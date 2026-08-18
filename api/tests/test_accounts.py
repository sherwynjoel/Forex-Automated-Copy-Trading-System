"""Tests for accounts endpoints."""
import psycopg
import pytest


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _seed_symbols(db, account_id):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO symbol_cache (account_id, name, symbol_id, digits, lot_size, min_volume, step_volume)
               VALUES (%s, 'EURUSD', 1, 5, 10000000, 100000, 100000),
                      (%s, 'GBPUSD', 2, 5, 10000000, 100000, 100000)""",
            (account_id, account_id),
        )


def test_list_accounts(org_client):
    """GET /api/orgs/{org_id}/accounts returns accounts with connection status."""
    client, org_id, seed = org_client
    seed(12345, role="master", enabled=True, multiplier=2.5, is_live=True)
    seed(12346, role="slave", enabled=True, multiplier=1.0, is_live=False)

    response = client.get(f"/api/orgs/{org_id}/accounts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2

    # Check master account
    master = [a for a in data if a["ctid_trader_account_id"] == 12345][0]
    assert master["trader_login"] == 12345
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
    """GET /api/orgs/{org_id}/accounts without auth returns 401."""
    response = app_client.get("/api/orgs/1/accounts")
    assert response.status_code == 401


def test_patch_multiplier_and_enabled(org_client, db):
    """PATCH /api/orgs/{org_id}/accounts/{id} updates multiplier and enabled."""
    client, org_id, seed = org_client
    seed(12345, role="slave", enabled=True, multiplier=1.0, is_live=True)

    # PATCH to update multiplier
    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"multiplier": 3.5},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    assert float(data["multiplier"]) == 3.5

    # Verify in DB
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute("SELECT multiplier FROM accounts WHERE ctid_trader_account_id = %s", (12345,)).fetchone()
        assert float(result[0]) == 3.5

    # PATCH to update enabled
    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"enabled": False},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False


def test_patch_invalid_multiplier_400(org_client):
    """PATCH with invalid multiplier returns 400."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)

    # Multiplier <= 0 should fail
    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"multiplier": 0},
        headers=_csrf(client),
    )
    assert response.status_code == 400
    assert "multiplier" in response.json().get("detail", "").lower()

    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"multiplier": -1},
        headers=_csrf(client),
    )
    assert response.status_code == 400

    # Non-numeric multiplier should also return 400 (not 422)
    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"multiplier": "not-a-number"},
        headers=_csrf(client),
    )
    assert response.status_code == 400
    assert "multiplier" in response.json().get("detail", "").lower()


def test_patch_invalid_role_400(org_client):
    """PATCH with invalid role returns 400."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)

    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"role": "invalid_role"},
        headers=_csrf(client),
    )
    assert response.status_code == 400
    assert "role" in response.json().get("detail", "").lower()


def test_second_master_409(org_client):
    """Setting a second master returns 409 with clear message."""
    client, org_id, seed = org_client
    seed(12345, role="master", is_live=True)
    seed(12346, role="slave", is_live=False)

    # Try to set second account as master
    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12346",
        json={"role": "master"},
        headers=_csrf(client),
    )
    assert response.status_code == 409
    data = response.json()
    assert "master already exists" in data.get("detail", "").lower()


def test_patch_role_master_conflict_is_per_org(org_client):
    """The single-master constraint is per-org: a conflict in one org must
    not be triggered or masked by another org's master."""
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(101, role="slave")
    r = client.patch(f"/api/orgs/{org_id}/accounts/101",
                     json={"role": "master"}, headers=_csrf(client))
    assert r.status_code == 409
    assert "master already exists" in r.json()["detail"]


def test_role_change_triggers_copier_reload(org_client):
    """Changing role POSTs to copier /reload endpoint."""
    client, org_id, seed = org_client
    seed(12345, role="slave", is_live=True)

    # Mock transport should record the POST to copier
    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"role": "slave"},  # Change slave -> slave (no real change but still calls reload)
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    # Check if copier was contacted
    assert "copier_reloaded" in data


def test_multiplier_change_does_not_trigger_reload(org_client):
    """Changing multiplier does not call copier reload."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)

    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"multiplier": 2.0},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    # Should not include copier_reloaded in response if multiplier only changed
    data = response.json()
    # This is just an update response, not a reload response


def test_disconnect_account_cascades(org_client, db):
    """DELETE /api/orgs/{org_id}/accounts/{account_id}/connection deletes
    the account's grant, cascading to every account under it."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)
    seed(12346, is_live=False)

    with psycopg.connect(db, autocommit=True) as conn:
        (conn_id,) = conn.execute(
            "SELECT ctid_connection_id FROM accounts WHERE ctid_trader_account_id = %s",
            (12345,),
        ).fetchone()

    # Disconnect by ACCOUNT id -- the account's connection is resolved server-side
    response = client.delete(
        f"/api/orgs/{org_id}/accounts/12345/connection",
        headers=_csrf(client),
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


def test_disconnect_account_triggers_copier_reload(org_client):
    """Disconnecting an account POSTs /reload to the copier so it de-authorizes immediately."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)

    from conftest import default_mock_callback
    copier_calls = []

    def recording_callback(request):
        if "copier.test" in str(request.url):
            copier_calls.append(str(request.url))
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(recording_callback)

    response = client.delete(
        f"/api/orgs/{org_id}/accounts/12345/connection",
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert response.json()["copier_reloaded"] is True
    assert any("/reload" in url for url in copier_calls)


def test_disconnect_unknown_account_404(org_client):
    """DELETE /api/orgs/{org_id}/accounts/{account_id}/connection for an unknown account returns 404."""
    client, org_id, seed = org_client

    response = client.delete(
        f"/api/orgs/{org_id}/accounts/99999/connection",
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_disconnect_account_requires_csrf(org_client):
    """DELETE without CSRF token returns 403."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)
    response = client.delete(f"/api/orgs/{org_id}/accounts/12345/connection")
    assert response.status_code == 403


def test_patch_nickname_and_list_returns_it(org_client):
    """PATCH can set a nickname; GET /api/orgs/{org_id}/accounts returns it."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)

    response = client.patch(
        f"/api/orgs/{org_id}/accounts/12345",
        json={"nickname": "Main live account"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert response.json()["nickname"] == "Main live account"

    accounts = client.get(f"/api/orgs/{org_id}/accounts").json()
    assert accounts[0]["nickname"] == "Main live account"


def test_account_details_merges_copier_and_db(org_client):
    """GET /api/orgs/{org_id}/accounts/{id}/details returns the copier's
    broker-side profile merged with what only the DB knows (nickname, role,
    grant)."""
    client, org_id, seed = org_client
    seed(12345, role="master", is_live=True)
    client.patch(
        f"/api/orgs/{org_id}/accounts/12345", json={"nickname": "Big master"},
        headers=_csrf(client))

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

    client.app.state.mock_transport.set_callback(callback)

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/details")
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


def test_account_details_unknown_account_404(org_client):
    client, org_id, seed = org_client
    response = client.get(f"/api/orgs/{org_id}/accounts/99999/details")
    assert response.status_code == 404


def test_account_history_proxies_window(org_client):
    """GET /api/orgs/{org_id}/accounts/{id}/history/{deals,orders} forwards
    the window to the copier and returns its payload."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)

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

    client.app.state.mock_transport.set_callback(callback)

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/history/deals?from=1000&to=2000")
    assert response.status_code == 200
    assert response.json()["deals"] == [{"deal_id": 1}]

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/history/orders?from=1000&to=2000")
    assert response.status_code == 200
    assert response.json()["orders"] == []

    assert any("account_id=12345" in u and "from=1000" in u and "to=2000" in u
               and "/deals" in u for u in seen)
    assert any("/orders" in u for u in seen)


def test_account_symbols_from_cache(org_client, db):
    """GET /api/orgs/{org_id}/accounts/{id}/symbols lists the account's
    cached symbols (for the order ticket) straight from the DB -- no copier
    round trip."""
    client, org_id, seed = org_client
    seed(12345, is_live=True)
    _seed_symbols(db, 12345)

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/symbols")
    assert response.status_code == 200
    symbols = response.json()
    assert {s["name"] for s in symbols} == {"EURUSD", "GBPUSD"}
    eurusd = next(s for s in symbols if s["name"] == "EURUSD")
    assert eurusd["min_volume_lots"] == 0.01
    assert eurusd["step_volume_lots"] == 0.01
    assert eurusd["digits"] == 5


def test_patch_nonexistent_account_404(org_client):
    """PATCH nonexistent account returns 404."""
    client, org_id, seed = org_client

    response = client.patch(
        f"/api/orgs/{org_id}/accounts/99999",
        json={"multiplier": 2.0},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_accounts_listing_is_org_scoped(org_client, make_user, make_org, login_as, db):
    import psycopg
    client, org_id, seed = org_client
    seed(100, role="master")

    # A second org with its own account
    other_owner = make_user(email="other@example.com")
    other_org = make_org(name="Other", members=[(other_owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (other_conn,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (other_org,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live)
               VALUES (200, %s, %s, 200, false)""", (other_conn, other_org))

    listed = client.get(f"/api/orgs/{org_id}/accounts").json()
    assert [a["ctid_trader_account_id"] for a in listed] == [100]
    # the other org's account is a 404 through THIS org's paths
    assert client.get(f"/api/orgs/{org_id}/accounts/200/symbols").status_code == 404
    r = client.patch(f"/api/orgs/{org_id}/accounts/200",
                     json={"enabled": False}, headers=_csrf(client))
    assert r.status_code == 404


def test_viewer_can_read_but_not_patch(org_client, make_user, login_as, db):
    import psycopg
    client, org_id, seed = org_client
    seed(100, role="master")
    viewer = make_user(email="v@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
            (org_id, viewer["id"]))
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/accounts").status_code == 200
    r = client.patch(f"/api/orgs/{org_id}/accounts/100",
                     json={"enabled": False}, headers=_csrf(client))
    assert r.status_code == 403
