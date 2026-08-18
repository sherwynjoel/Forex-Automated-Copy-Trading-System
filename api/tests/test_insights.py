"""Tests for the insight proxies (margin estimate, trendbars, cash flow,
position deals, analytics) and the DB-computed /api/overview stats."""
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
    _seed_account(db, 12347, conn_id, 333333, False, role="slave", enabled=False)
    return db


def _record_copier(app_client, responses=None):
    calls = []

    def callback(request):
        url = str(request.url)
        if "copier.test" in url:
            calls.append(url)
            for fragment, response in (responses or {}).items():
                if fragment in url:
                    return response
            return httpx.Response(200, json={"status": "ok"})
        return default_mock_callback(request)

    app_client.app.state.mock_transport.set_callback(callback)
    return calls


def test_margin_estimate_proxies(app_client, seeded):
    _login(app_client)
    calls = _record_copier(app_client, {
        "/margin-estimate": httpx.Response(200, json={
            "buy_margin": 27.5, "sell_margin": 27.5, "volume_lots": "0.10"}),
    })

    response = app_client.get(
        "/api/accounts/12345/margin-estimate?symbol=EURUSD&volume_lots=0.1")

    assert response.status_code == 200
    assert response.json()["buy_margin"] == 27.5
    url = next(u for u in calls if "/margin-estimate" in u)
    assert "account_id=12345" in url and "symbol=EURUSD" in url and "volume_lots=0.1" in url


def test_trendbars_proxies(app_client, seeded):
    _login(app_client)
    calls = _record_copier(app_client, {
        "/trendbars": httpx.Response(200, json={"period": "H1", "bars": []}),
    })

    response = app_client.get(
        "/api/accounts/12345/trendbars?symbol=EURUSD&period=H1&from=1000&to=2000")

    assert response.status_code == 200
    assert response.json()["period"] == "H1"
    url = next(u for u in calls if "/trendbars" in u)
    assert "period=H1" in url and "from=1000" in url


def test_cashflow_history_proxies(app_client, seeded):
    _login(app_client)
    calls = _record_copier(app_client, {
        "/history/cashflow": httpx.Response(200, json={"entries": [{"id": 1}]}),
    })

    response = app_client.get(
        "/api/accounts/12345/history/cashflow?from=1000&to=2000")

    assert response.status_code == 200
    assert response.json()["entries"] == [{"id": 1}]
    assert any("/history/cashflow" in u for u in calls)


def test_position_deals_proxies(app_client, seeded):
    _login(app_client)
    calls = _record_copier(app_client, {
        "/position-deals": httpx.Response(200, json={"deals": [], "has_more": False}),
    })

    response = app_client.get(
        "/api/accounts/12345/positions/7001/deals?from=0&to=9000")

    assert response.status_code == 200
    url = next(u for u in calls if "/position-deals" in u)
    assert "position_id=7001" in url


def test_analytics_proxies_with_weeks(app_client, seeded):
    _login(app_client)
    calls = _record_copier(app_client, {
        "/analytics": httpx.Response(200, json={"closed_trades": 3, "weeks": 8}),
    })

    response = app_client.get("/api/accounts/12345/analytics?weeks=8")

    assert response.status_code == 200
    assert response.json()["closed_trades"] == 3
    url = next(u for u in calls if "/analytics" in u)
    assert "weeks=8" in url


def test_overview_stats_computed_from_db(app_client, seeded, db):
    """/api/overview aggregates what only the DB knows: account counts,
    yesterday's portfolio snapshot, copies today, and the recent-copy feed."""
    _login(app_client)

    with psycopg.connect(db, autocommit=True) as conn:
        # Yesterday's snapshot for the vs-yesterday comparison
        conn.execute(
            """INSERT INTO portfolio_snapshots (snapshot_date, account_id, balance, equity)
               VALUES (CURRENT_DATE - 1, 12345, 10000, 10100),
                      (CURRENT_DATE - 1, 12346, 5000, 5050)""")
        # One copy fill logged today + one yesterday (only today's counts)
        conn.execute(
            """INSERT INTO events (ts, account_id, category, severity, payload)
               VALUES (now(), 12346, 'slave_action', 'info', '{"action": "position_filled"}'),
                      (now() - interval '1 day', 12346, 'slave_action', 'info', '{"action": "position_filled"}')""")
        # Recent copies
        conn.execute(
            """INSERT INTO mappings (master_position_id, slave_account_id, client_order_id,
                                     status, symbol, slave_volume, fill_price)
               VALUES (42, 12346, 'cm42.12346', 'active', 'EURUSD', 100000, 1.1050),
                      (43, 12346, 'cm43.12346', 'failed', 'GBPUSD', NULL, NULL)""")

    response = app_client.get("/api/overview")
    assert response.status_code == 200
    data = response.json()

    assert data["accounts_connected"] == 3
    assert data["masters"] == 1
    assert data["active_slaves"] == 1        # 12346; 12347 is disabled
    assert data["disabled_or_paused"] == 1
    assert data["copied_today"] == 1
    assert data["yesterday"]["total_equity"] == 15150.0
    assert data["yesterday"]["total_balance"] == 15000.0

    copies = data["recent_copies"]
    assert len(copies) == 2
    # Newest first
    assert copies[0]["symbol"] == "GBPUSD"
    assert copies[0]["status"] == "failed"
    assert copies[1]["master_position_id"] == 42
    assert copies[1]["slave_login"] == 222222
    assert copies[1]["fill_price"] == 1.105


def test_overview_without_snapshots_has_null_yesterday(app_client, seeded):
    _login(app_client)
    response = app_client.get("/api/overview")
    assert response.status_code == 200
    assert response.json()["yesterday"] is None
