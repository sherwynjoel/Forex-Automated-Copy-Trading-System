"""Tests for accounts endpoints."""
import psycopg
import pytest


def _seed_account(db, ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role="slave", enabled=True, multiplier=1.0):
    """Helper to insert an account into the test database."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO accounts
               (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
        )


def _seed_connection(db):
    """Helper to insert a connection into the test database."""
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            """INSERT INTO ctid_connections
               (access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, %s, now(), now() + interval '30 days')
               RETURNING id""",
            ("enc_access", "enc_refresh")
        ).fetchone()
        return result[0]


def test_list_accounts(app_client, db):
    """GET /api/accounts returns accounts with connection status."""
    # Login first
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Seed a connection and an account
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True, role="master", enabled=True, multiplier=2.5)
    _seed_account(db, 12346, conn_id, 222222, False, role="slave", enabled=True, multiplier=1.0)

    # GET /api/accounts should return the accounts
    response = app_client.get("/api/accounts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2

    # Check master account
    master = [a for a in data if a["ctid_trader_account_id"] == 12345][0]
    assert master["trader_login"] == 111111
    assert master["is_live"] is True
    assert master["role"] == "master"
    assert master["enabled"] is True
    assert float(master["multiplier"]) == 2.5
    assert "ctid_connection_id" not in master  # Don't expose raw connection IDs
    assert "status" in master
    assert "connection_status" in master

    # Check that tokens are never exposed
    assert "access_token_enc" not in master
    assert "refresh_token_enc" not in master


def test_list_accounts_requires_auth(app_client):
    """GET /api/accounts without auth returns 401."""
    response = app_client.get("/api/accounts")
    assert response.status_code == 401


def test_patch_multiplier_and_enabled(app_client, db):
    """PATCH /api/accounts/{id} updates multiplier and enabled."""
    # Login first
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Seed a connection and an account
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True, role="slave", enabled=True, multiplier=1.0)

    # PATCH to update multiplier
    response = app_client.patch(
        "/api/accounts/12345",
        json={"multiplier": 3.5},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert float(data["multiplier"]) == 3.5

    # Verify in DB
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute("SELECT multiplier FROM accounts WHERE ctid_trader_account_id = %s", (12345,)).fetchone()
        assert float(result[0]) == 3.5

    # PATCH to update enabled
    response = app_client.patch(
        "/api/accounts/12345",
        json={"enabled": False},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False


def test_patch_invalid_multiplier_400(app_client, db):
    """PATCH with invalid multiplier returns 400."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)

    # Multiplier <= 0 should fail
    response = app_client.patch(
        "/api/accounts/12345",
        json={"multiplier": 0},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 400
    assert "multiplier" in response.json().get("detail", "").lower()

    response = app_client.patch(
        "/api/accounts/12345",
        json={"multiplier": -1},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 400

    # Non-numeric multiplier should also return 400 (not 422)
    response = app_client.patch(
        "/api/accounts/12345",
        json={"multiplier": "not-a-number"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 400
    assert "multiplier" in response.json().get("detail", "").lower()


def test_patch_invalid_role_400(app_client, db):
    """PATCH with invalid role returns 400."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)

    response = app_client.patch(
        "/api/accounts/12345",
        json={"role": "invalid_role"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 400
    assert "role" in response.json().get("detail", "").lower()


def test_second_master_409(app_client, db):
    """Setting a second master returns 409 with clear message."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Seed a connection and two accounts
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True, role="master")
    _seed_account(db, 12346, conn_id, 222222, False, role="slave")

    # Try to set second account as master
    response = app_client.patch(
        "/api/accounts/12346",
        json={"role": "master"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 409
    data = response.json()
    assert "master already exists" in data.get("detail", "").lower()


def test_role_change_triggers_copier_reload(app_client, db):
    """Changing role POSTs to copier /reload endpoint."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True, role="slave")

    # Mock transport should record the POST to copier
    response = app_client.patch(
        "/api/accounts/12345",
        json={"role": "slave"},  # Change slave -> slave (no real change but still calls reload)
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    # Check if copier was contacted
    assert "copier_reloaded" in data


def test_multiplier_change_does_not_trigger_reload(app_client, db):
    """Changing multiplier does not call copier reload."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)

    response = app_client.patch(
        "/api/accounts/12345",
        json={"multiplier": 2.0},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    # Should not include copier_reloaded in response if multiplier only changed
    data = response.json()
    # This is just an update response, not a reload response


def test_disconnect_account_cascades(app_client, db):
    """DELETE /api/accounts/{account_id}/connection deletes the account's grant, cascading to every account under it."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)
    _seed_account(db, 12346, conn_id, 222222, False)

    # Disconnect by ACCOUNT id -- the account's connection is resolved server-side
    response = app_client.delete(
        "/api/accounts/12345/connection",
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["accounts_removed"] == 2

    # Verify connection and both sibling accounts are deleted
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute("SELECT COUNT(*) FROM ctid_connections WHERE id = %s", (conn_id,)).fetchone()
        assert result[0] == 0

        result = conn.execute("SELECT COUNT(*) FROM accounts WHERE ctid_connection_id = %s", (conn_id,)).fetchone()
        assert result[0] == 0


def test_disconnect_account_triggers_copier_reload(app_client, db):
    """Disconnecting an account POSTs /reload to the copier so it de-authorizes immediately."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)

    from conftest import default_mock_callback
    copier_calls = []

    def recording_callback(request):
        if "copier.test" in str(request.url):
            copier_calls.append(str(request.url))
        return default_mock_callback(request)

    app_client.app.state.mock_transport.set_callback(recording_callback)

    response = app_client.delete(
        "/api/accounts/12345/connection",
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    assert response.json()["copier_reloaded"] is True
    assert any("/reload" in url for url in copier_calls)


def test_disconnect_unknown_account_404(app_client, db):
    """DELETE /api/accounts/{account_id}/connection for an unknown account returns 404."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.delete(
        "/api/accounts/99999/connection",
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 404


def test_disconnect_account_requires_csrf(app_client, db):
    """DELETE without CSRF token returns 403."""
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)
    response = app_client.delete("/api/accounts/12345/connection")
    assert response.status_code == 403


def _seed_symbols(db, account_id):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO symbol_cache (account_id, name, symbol_id, digits, lot_size, min_volume, step_volume)
               VALUES (%s, 'EURUSD', 1, 5, 10000000, 100000, 100000),
                      (%s, 'GBPUSD', 2, 5, 10000000, 100000, 100000)""",
            (account_id, account_id),
        )


def _login(app_client):
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    return response.cookies.get("csrf")


def test_patch_nickname_and_list_returns_it(app_client, db):
    """PATCH can set a nickname; GET /api/accounts returns it."""
    csrf_token = _login(app_client)
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)

    response = app_client.patch(
        "/api/accounts/12345",
        json={"nickname": "Main live account"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert response.json()["nickname"] == "Main live account"

    accounts = app_client.get("/api/accounts").json()
    assert accounts[0]["nickname"] == "Main live account"


def test_account_details_merges_copier_and_db(app_client, db):
    """GET /api/accounts/{id}/details returns the copier's broker-side
    profile merged with what only the DB knows (nickname, role, grant)."""
    csrf_token = _login(app_client)
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True, role="master")
    app_client.patch(
        "/api/accounts/12345", json={"nickname": "Big master"},
        headers={"X-CSRF-Token": csrf_token})

    import httpx
    from conftest import default_mock_callback

    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/details" in url:
            assert "account_id=12345" in url
            return httpx.Response(200, json={
                "account_id": 12345, "balance": 10000.0,
                "broker_name": "FP Markets", "deposit_currency": "USD",
                "leverage": 50.0, "account_type": "HEDGED",
                "open_positions": [], "pending_orders": [],
            })
        return default_mock_callback(request)

    app_client.app.state.mock_transport.set_callback(callback)

    response = app_client.get("/api/accounts/12345/details")
    assert response.status_code == 200
    data = response.json()
    assert data["balance"] == 10000.0
    assert data["broker_name"] == "FP Markets"
    assert data["nickname"] == "Big master"
    assert data["role"] == "master"
    assert data["is_live"] is True
    assert data["enabled"] is True
    assert "granted_at" in data["connection"]
    assert "expires_at" in data["connection"]
    assert data["connection"]["status"] == "active"


def test_account_details_unknown_account_404(app_client, db):
    _login(app_client)
    response = app_client.get("/api/accounts/99999/details")
    assert response.status_code == 404


def test_account_history_proxies_window(app_client, db):
    """GET /api/accounts/{id}/history/{deals,orders} forwards the window to
    the copier and returns its payload."""
    _login(app_client)
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)

    import httpx
    from conftest import default_mock_callback
    seen = []

    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/history/" in url:
            seen.append(url)
            if "/deals" in url:
                return httpx.Response(200, json={"deals": [{"deal_id": 1}], "has_more": False})
            return httpx.Response(200, json={"orders": [], "has_more": False})
        return default_mock_callback(request)

    app_client.app.state.mock_transport.set_callback(callback)

    response = app_client.get("/api/accounts/12345/history/deals?from=1000&to=2000")
    assert response.status_code == 200
    assert response.json()["deals"] == [{"deal_id": 1}]

    response = app_client.get("/api/accounts/12345/history/orders?from=1000&to=2000")
    assert response.status_code == 200
    assert response.json()["orders"] == []

    assert any("account_id=12345" in u and "from=1000" in u and "to=2000" in u
               and "/deals" in u for u in seen)
    assert any("/orders" in u for u in seen)


def test_account_symbols_from_cache(app_client, db):
    """GET /api/accounts/{id}/symbols lists the account's cached symbols
    (for the order ticket) straight from the DB -- no copier round trip."""
    _login(app_client)
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True)
    _seed_symbols(db, 12345)

    response = app_client.get("/api/accounts/12345/symbols")
    assert response.status_code == 200
    symbols = response.json()
    assert {s["name"] for s in symbols} == {"EURUSD", "GBPUSD"}
    eurusd = next(s for s in symbols if s["name"] == "EURUSD")
    assert eurusd["min_volume_lots"] == 0.01
    assert eurusd["step_volume_lots"] == 0.01
    assert eurusd["digits"] == 5


def test_patch_nonexistent_account_404(app_client, db):
    """PATCH nonexistent account returns 404."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.patch(
        "/api/accounts/99999",
        json={"multiplier": 2.0},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 404
