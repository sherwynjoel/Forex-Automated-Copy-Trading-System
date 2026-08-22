"""Tests for the trading action proxies: manual orders, position close,
order cancel, and the kill switch (/api/orgs/{org_id}/control/close-all)."""
import json

import httpx
import psycopg

from conftest import default_mock_callback


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _record_copier(app_client, responses=None):
    """Route copier calls through a recorder; `responses` maps a URL
    substring to an httpx.Response."""
    calls = []

    def callback(request):
        url = str(request.url)
        if "copier.test" in url:
            calls.append((url, request.content.decode() if request.content else ""))
            for fragment, response in (responses or {}).items():
                if fragment in url:
                    return response
            return httpx.Response(200, json={"status": "ok"})
        return default_mock_callback(request)

    app_client.app.state.mock_transport.set_callback(callback)
    return calls


def test_place_order_proxies_body(org_client):
    client, org_id, seed = org_client
    seed(12346, role="slave")
    calls = _record_copier(client, {
        "/order": httpx.Response(200, json={"status": "submitted", "volume": 5000000}),
    })

    body = {"account_id": 12346, "symbol": "EURUSD", "side": "BUY",
            "order_type": "MARKET", "volume_lots": 0.5}
    response = client.post(
        f"/api/orgs/{org_id}/orders", json=body, headers=_csrf(client))

    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    url, sent_body = next(c for c in calls if "/order" in c[0])
    sent = json.loads(sent_body)
    assert sent["account_id"] == 12346
    assert sent["volume_lots"] == 0.5


def test_place_order_forwards_copier_validation_error(org_client):
    client, org_id, seed = org_client
    seed(12346, role="slave")
    _record_copier(client, {
        "/order": httpx.Response(400, json={"error": "side must be BUY or SELL"}),
    })

    response = client.post(
        f"/api/orgs/{org_id}/orders",
        json={"account_id": 12346, "side": "HOLD"},
        headers=_csrf(client))

    assert response.status_code == 400
    assert response.json()["detail"] == "side must be BUY or SELL"


def test_place_order_requires_auth(app_client):
    response = app_client.post("/api/orgs/1/orders", json={})
    # CSRF middleware rejects before auth can 401
    assert response.status_code in (401, 403)


def test_close_position_proxies(org_client):
    client, org_id, seed = org_client
    seed(12346, role="slave")
    calls = _record_copier(client, {
        "/positions/close": httpx.Response(200, json={"status": "submitted"}),
    })

    response = client.post(
        f"/api/orgs/{org_id}/positions/close",
        json={"account_id": 12346, "position_id": 7001, "volume_lots": 0.1},
        headers=_csrf(client))

    assert response.status_code == 200
    url, sent = next(c for c in calls if "/positions/close" in c[0])
    assert json.loads(sent)["position_id"] == 7001


def test_cancel_order_proxies(org_client):
    client, org_id, seed = org_client
    seed(12346, role="slave")
    calls = _record_copier(client, {
        "/orders/cancel": httpx.Response(200, json={"status": "submitted"}),
    })

    response = client.post(
        f"/api/orgs/{org_id}/orders/cancel",
        json={"account_id": 12346, "order_id": 9001},
        headers=_csrf(client))

    assert response.status_code == 200
    url, sent = next(c for c in calls if "/orders/cancel" in c[0])
    assert json.loads(sent)["order_id"] == 9001


def test_close_all_global_proxies(org_client):
    client, org_id, seed = org_client
    seed(12345, role="master", is_live=True)
    calls = _record_copier(client, {
        "/close-all": httpx.Response(200, json={
            "status": "flattened", "paused": True,
            "accounts": [{"account_id": 12345, "positions_closed": 2,
                          "orders_cancelled": 0, "error": None}],
        }),
    })

    response = client.post(
        f"/api/orgs/{org_id}/control/close-all", json={}, headers=_csrf(client))

    assert response.status_code == 200
    data = response.json()
    assert data["paused"] is True
    assert data["accounts"][0]["positions_closed"] == 2
    url, sent = next(c for c in calls if "/close-all" in c[0])
    # The org bound AND the human who fired the kill switch: audit
    # attribution travels with every money-moving command.
    assert json.loads(sent) == {"org_id": org_id, "actor_email": "admin@example.com"}


def test_close_all_single_account_proxies(org_client):
    client, org_id, seed = org_client
    seed(12346, role="slave")
    calls = _record_copier(client, {
        "/close-all": httpx.Response(200, json={
            "status": "flattened", "paused": False,
            "accounts": [{"account_id": 12346, "positions_closed": 1,
                          "orders_cancelled": 1, "error": None}],
        }),
    })

    response = client.post(
        f"/api/orgs/{org_id}/control/close-all", json={"account_id": 12346},
        headers=_csrf(client))

    assert response.status_code == 200
    assert response.json()["paused"] is False
    url, sent = next(c for c in calls if "/close-all" in c[0])
    body = json.loads(sent)
    assert body["account_id"] == 12346
    assert body["org_id"] == org_id


def test_close_all_copier_down_is_502(org_client):
    client, org_id, seed = org_client

    def callback(request):
        url = str(request.url)
        if "copier.test" in url:
            raise httpx.ConnectError("Connection refused")
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(callback)

    response = client.post(
        f"/api/orgs/{org_id}/control/close-all", json={}, headers=_csrf(client))
    assert response.status_code == 502


def test_order_for_foreign_account_is_404_and_never_proxied(
        org_client, make_user, make_org, login_as, db):
    """The core cross-tenant money test: org B's account is unreachable
    through org A's trading routes, and the copier is never contacted."""
    client, org_id, seed = org_client
    seed(100, role="master")
    other = make_user(email="b@example.com")
    other_org = make_org(name="B", members=[(other, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (other_conn,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (other_org,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live)
               VALUES (999, %s, %s, 999, false)""", (other_conn, other_org))

    proxied = []

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            proxied.append(str(request.url))
            return httpx.Response(200, json={"status": "submitted"})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    for path, body in [
        ("orders", {"account_id": 999, "symbol": "EURUSD", "side": "BUY",
                    "order_type": "MARKET", "volume_lots": 0.01}),
        ("positions/close", {"account_id": 999, "position_id": 1}),
        ("orders/cancel", {"account_id": 999, "order_id": 1}),
        ("control/close-all", {"account_id": 999}),
    ]:
        r = client.post(f"/api/orgs/{org_id}/{path}", json=body,
                        headers=_csrf(client))
        assert r.status_code == 404, path
    assert proxied == []


def test_close_all_forwards_org_id(org_client):
    client, org_id, seed = org_client
    captured = {}

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            captured["body"] = json.loads(request.content or b"{}")
            return httpx.Response(200, json={"status": "flattened", "paused": True,
                                             "accounts": []})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    r = client.post(f"/api/orgs/{org_id}/control/close-all", json={},
                    headers=_csrf(client))
    assert r.status_code == 200
    assert captured["body"] == {"org_id": org_id, "actor_email": "admin@example.com"}


def test_trader_can_order_but_not_close_all(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    trader = make_user(email="t@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'trader')",
            (org_id, trader["id"]))
    login_as(client, trader)
    r = client.post(f"/api/orgs/{org_id}/orders",
                    json={"account_id": 100, "symbol": "EURUSD", "side": "BUY",
                          "order_type": "MARKET", "volume_lots": 0.01},
                    headers=_csrf(client))
    assert r.status_code == 200  # mock copier answers 200
    r = client.post(f"/api/orgs/{org_id}/control/close-all", json={},
                    headers=_csrf(client))
    assert r.status_code == 403
