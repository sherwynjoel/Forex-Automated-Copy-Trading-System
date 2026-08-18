"""Tests for the trading action proxies: manual orders, position close,
order cancel, and the kill switch (/api/control/close-all)."""
import httpx
import psycopg
import pytest

from conftest import default_mock_callback
from test_accounts import _login, _seed_account, _seed_connection


@pytest.fixture
def seeded(db):
    conn_id = _seed_connection(db)
    _seed_account(db, 12345, conn_id, 111111, True, role="master")
    _seed_account(db, 12346, conn_id, 222222, False, role="slave")
    return db


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


def test_place_order_proxies_body(app_client, seeded):
    csrf = _login(app_client)
    calls = _record_copier(app_client, {
        "/order": httpx.Response(200, json={"status": "submitted", "volume": 5000000}),
    })

    body = {"account_id": 12346, "symbol": "EURUSD", "side": "BUY",
            "order_type": "MARKET", "volume_lots": 0.5}
    response = app_client.post(
        "/api/orders", json=body, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    url, sent_body = next(c for c in calls if "/order" in c[0])
    import json
    sent = json.loads(sent_body)
    assert sent["account_id"] == 12346
    assert sent["volume_lots"] == 0.5


def test_place_order_forwards_copier_validation_error(app_client, seeded):
    csrf = _login(app_client)
    _record_copier(app_client, {
        "/order": httpx.Response(400, json={"error": "side must be BUY or SELL"}),
    })

    response = app_client.post(
        "/api/orders",
        json={"account_id": 12346, "side": "HOLD"},
        headers={"X-CSRF-Token": csrf})

    assert response.status_code == 400
    assert response.json()["detail"] == "side must be BUY or SELL"


def test_place_order_requires_auth(app_client, seeded):
    response = app_client.post("/api/orders", json={})
    # CSRF middleware rejects before auth can 401
    assert response.status_code in (401, 403)


def test_close_position_proxies(app_client, seeded):
    csrf = _login(app_client)
    calls = _record_copier(app_client, {
        "/positions/close": httpx.Response(200, json={"status": "submitted"}),
    })

    response = app_client.post(
        "/api/positions/close",
        json={"account_id": 12346, "position_id": 7001, "volume_lots": 0.1},
        headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    url, sent = next(c for c in calls if "/positions/close" in c[0])
    import json
    assert json.loads(sent)["position_id"] == 7001


def test_cancel_order_proxies(app_client, seeded):
    csrf = _login(app_client)
    calls = _record_copier(app_client, {
        "/orders/cancel": httpx.Response(200, json={"status": "submitted"}),
    })

    response = app_client.post(
        "/api/orders/cancel",
        json={"account_id": 12346, "order_id": 9001},
        headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    url, sent = next(c for c in calls if "/orders/cancel" in c[0])
    import json
    assert json.loads(sent)["order_id"] == 9001


def test_close_all_global_proxies(app_client, seeded):
    csrf = _login(app_client)
    calls = _record_copier(app_client, {
        "/close-all": httpx.Response(200, json={
            "status": "flattened", "paused": True,
            "accounts": [{"account_id": 12345, "positions_closed": 2,
                          "orders_cancelled": 0, "error": None}],
        }),
    })

    response = app_client.post(
        "/api/control/close-all", json={}, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    data = response.json()
    assert data["paused"] is True
    assert data["accounts"][0]["positions_closed"] == 2
    url, sent = next(c for c in calls if "/close-all" in c[0])
    assert sent in ("{}", "")


def test_close_all_single_account_proxies(app_client, seeded):
    csrf = _login(app_client)
    calls = _record_copier(app_client, {
        "/close-all": httpx.Response(200, json={
            "status": "flattened", "paused": False,
            "accounts": [{"account_id": 12346, "positions_closed": 1,
                          "orders_cancelled": 1, "error": None}],
        }),
    })

    response = app_client.post(
        "/api/control/close-all", json={"account_id": 12346},
        headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert response.json()["paused"] is False
    url, sent = next(c for c in calls if "/close-all" in c[0])
    import json
    assert json.loads(sent)["account_id"] == 12346


def test_close_all_copier_down_is_502(app_client, seeded):
    csrf = _login(app_client)

    def callback(request):
        url = str(request.url)
        if "copier.test" in url:
            raise httpx.ConnectError("Connection refused")
        return default_mock_callback(request)

    app_client.app.state.mock_transport.set_callback(callback)

    response = app_client.post(
        "/api/control/close-all", json={}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 502
