"""Tests for the insight proxies (margin estimate, trendbars, cash flow,
position deals, analytics) and the DB-computed per-org overview stats.

Ported from the single-tenant batch: every route moved under
/api/orgs/{org_id}/... and every DB assertion now has to hold with a SECOND
org's data present in the same tables, which is what the cross-org tests at
the bottom pin.
"""
import httpx
import psycopg
import pytest

from api.routes.settings_control import COPIER_SLOW_COMMAND_TIMEOUT_S
from conftest import default_mock_callback


@pytest.fixture
def seeded(org_client):
    """The org from org_client with a master and two slaves (one disabled)."""
    client, org_id, seed = org_client
    seed(12345, role="master", enabled=True)
    seed(12346, role="slave", enabled=True)
    seed(12347, role="slave", enabled=False)
    return client, org_id


@pytest.fixture
def other_org(make_user, make_org, db):
    """A second org with its own connection and account 999; returns
    (org_id, account_id)."""
    owner = make_user(email="other-owner@example.com")
    org_id = make_org(name="Other Desk", members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (org_id,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role, enabled)
               VALUES (999, %s, %s, 999, false, 'master', true)""",
            (connection_id, org_id))
    return org_id, 999


def _record_copier(client, responses=None):
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

    client.app.state.mock_transport.set_callback(callback)
    return calls


# ---------- proxies ----------

def test_margin_estimate_proxies(seeded):
    client, org_id = seeded
    calls = _record_copier(client, {
        "/margin-estimate": httpx.Response(200, json={
            "buy_margin": 27.5, "sell_margin": 27.5, "volume_lots": "0.10"}),
    })

    response = client.get(
        f"/api/orgs/{org_id}/accounts/12345/margin-estimate"
        "?symbol=EURUSD&volume_lots=0.1")

    assert response.status_code == 200
    assert response.json()["buy_margin"] == 27.5
    url = next(u for u in calls if "/margin-estimate" in u)
    # The copier's query routes are account-scoped, with no org param: the
    # tenancy check happened here, before the proxy.
    assert "account_id=12345" in url and "symbol=EURUSD" in url and "volume_lots=0.1" in url
    assert "org_id" not in url


def test_trendbars_proxies(seeded):
    client, org_id = seeded
    calls = _record_copier(client, {
        "/trendbars": httpx.Response(200, json={"period": "H1", "bars": []}),
    })

    response = client.get(
        f"/api/orgs/{org_id}/accounts/12345/trendbars"
        "?symbol=EURUSD&period=H1&from=1000&to=2000")

    assert response.status_code == 200
    assert response.json()["period"] == "H1"
    url = next(u for u in calls if "/trendbars" in u)
    assert "period=H1" in url and "from=1000" in url


def test_cashflow_history_proxies(seeded):
    client, org_id = seeded
    calls = _record_copier(client, {
        "/history/cashflow": httpx.Response(200, json={"entries": [{"id": 1}]}),
    })

    response = client.get(
        f"/api/orgs/{org_id}/accounts/12345/history/cashflow?from=1000&to=2000")

    assert response.status_code == 200
    assert response.json()["entries"] == [{"id": 1}]
    assert any("/history/cashflow" in u for u in calls)


def test_position_deals_proxies(seeded):
    client, org_id = seeded
    calls = _record_copier(client, {
        "/position-deals": httpx.Response(200, json={"deals": [], "has_more": False}),
    })

    response = client.get(
        f"/api/orgs/{org_id}/accounts/12345/positions/7001/deals?from=0&to=9000")

    assert response.status_code == 200
    url = next(u for u in calls if "/position-deals" in u)
    assert "position_id=7001" in url


def test_analytics_proxies_with_weeks(seeded):
    client, org_id = seeded
    calls = _record_copier(client, {
        "/analytics": httpx.Response(200, json={"closed_trades": 3, "weeks": 8}),
    })

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/analytics?weeks=8")

    assert response.status_code == 200
    assert response.json()["closed_trades"] == 3
    url = next(u for u in calls if "/analytics" in u)
    assert "weeks=8" in url


@pytest.mark.parametrize("requested,forwarded", [
    (26, 12),    # the old dashboard ceiling: clamped, not rejected
    (100, 12),
    (0, 1),
    (-5, 1),
    (12, 12),    # the boundary itself is allowed through untouched
    (1, 1),
])
def test_analytics_weeks_is_clamped_not_rejected(seeded, requested, forwarded):
    """`weeks` is a range control, not a validation surface: out-of-range
    values are clamped the way /events clamps `limit`.

    The ceiling is a shared-resource budget -- each week is one sequential
    broker request on the CTraderClient EVERY org's orders go through -- and
    it is reachable by anyone with `viewer` from a dropdown, so it cannot be
    left to the caller.
    """
    client, org_id = seeded
    calls = _record_copier(client)

    response = client.get(
        f"/api/orgs/{org_id}/accounts/12345/analytics?weeks={requested}")

    assert response.status_code == 200
    url = next(u for u in calls if "/analytics" in u)
    assert f"weeks={forwarded}" in url


def test_analytics_gets_the_slow_command_timeout(seeded):
    """The analytics proxy is bounded by broker round trips, not local work,
    so it must not inherit the shared client's fail-fast 5s default -- that
    would 502 a legitimate multi-week read while the copier kept spending
    broker budget on a result nobody would receive."""
    seen = {}

    def callback(request):
        url = str(request.url)
        if "/analytics" in url:
            seen["timeout"] = request.extensions.get("timeout")
            return httpx.Response(200, json={"closed_trades": 0})
        return default_mock_callback(request)

    client, org_id = seeded
    client.app.state.mock_transport.set_callback(callback)

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/analytics")

    assert response.status_code == 200
    # httpx records the per-request override in the transport extensions.
    assert seen["timeout"]["read"] == COPIER_SLOW_COMMAND_TIMEOUT_S


@pytest.mark.parametrize("tail", [
    "margin-estimate?symbol=EURUSD&volume_lots=0.1",
    "trendbars?symbol=EURUSD&period=H1&from=0&to=1",
    "positions/7001/deals?from=0&to=1",
    "analytics",
    "history/cashflow?from=0&to=1",
])
def test_proxies_refuse_another_orgs_account(seeded, other_org, tail):
    """A foreign account id is a 404 through THIS org's paths -- and the
    copier is never even asked."""
    client, org_id = seeded
    _, foreign_account = other_org
    calls = _record_copier(client)

    response = client.get(
        f"/api/orgs/{org_id}/accounts/{foreign_account}/{tail}")

    assert response.status_code == 404
    assert calls == []


def test_proxies_refuse_non_members(seeded, other_org, make_user, login_as):
    """A user with no membership in the org gets 404, not data."""
    client, org_id = seeded
    outsider = make_user(email="outsider@example.com")
    login_as(client, outsider)

    response = client.get(f"/api/orgs/{org_id}/accounts/12345/analytics")
    assert response.status_code == 404


# ---------- overview ----------

def test_overview_stats_computed_from_db(seeded, db):
    """/api/orgs/{org_id}/overview aggregates what only the DB knows:
    account counts, yesterday's portfolio snapshot, copies today, and the
    recent-copy feed."""
    client, org_id = seeded

    with psycopg.connect(db, autocommit=True) as conn:
        # Yesterday's snapshot for the vs-yesterday comparison
        conn.execute(
            """INSERT INTO portfolio_snapshots
                   (snapshot_date, account_id, balance, equity, org_id)
               VALUES (CURRENT_DATE - 1, 12345, 10000, 10100, %s),
                      (CURRENT_DATE - 1, 12346, 5000, 5050, %s)""",
            (org_id, org_id))
        # One copy fill logged today + one yesterday (only today's counts)
        conn.execute(
            """INSERT INTO events (ts, account_id, org_id, category, severity, payload)
               VALUES (now(), 12346, %s, 'slave_action', 'info',
                       '{"action": "position_filled"}'),
                      (now() - interval '1 day', 12346, %s, 'slave_action', 'info',
                       '{"action": "position_filled"}')""",
            (org_id, org_id))
        # Recent copies
        conn.execute(
            """INSERT INTO mappings (master_position_id, slave_account_id, client_order_id,
                                     org_id, status, symbol, slave_volume, fill_price)
               VALUES (42, 12346, 'cm42.12346', %s, 'active', 'EURUSD', 100000, 1.1050),
                      (43, 12346, 'cm43.12346', %s, 'failed', 'GBPUSD', NULL, NULL)""",
            (org_id, org_id))

    response = client.get(f"/api/orgs/{org_id}/overview")
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
    assert copies[1]["slave_login"] == 12346
    assert copies[1]["fill_price"] == 1.105


def test_overview_without_snapshots_has_null_yesterday(seeded):
    client, org_id = seeded
    response = client.get(f"/api/orgs/{org_id}/overview")
    assert response.status_code == 200
    assert response.json()["yesterday"] is None


def test_overview_counts_only_this_orgs_rows(seeded, other_org, db):
    """Every overview query is org-scoped: another desk's accounts, copy
    fills, mappings and snapshots must not appear in these numbers."""
    client, org_id = seeded
    foreign_org, foreign_account = other_org

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO portfolio_snapshots
                   (snapshot_date, account_id, balance, equity, org_id)
               VALUES (CURRENT_DATE - 1, 12345, 10000, 10100, %s),
                      (CURRENT_DATE - 1, %s, 777777, 777777, %s)""",
            (org_id, foreign_account, foreign_org))
        conn.execute(
            """INSERT INTO events (ts, account_id, org_id, category, severity, payload)
               VALUES (now(), 12346, %s, 'slave_action', 'info',
                       '{"action": "position_filled"}'),
                      (now(), %s, %s, 'slave_action', 'info',
                       '{"action": "position_filled"}')""",
            (org_id, foreign_account, foreign_org))
        conn.execute(
            """INSERT INTO mappings (master_position_id, slave_account_id, client_order_id,
                                     org_id, status, symbol)
               VALUES (42, 12346, 'cm42.12346', %s, 'active', 'EURUSD'),
                      (77, %s, 'cm77.999', %s, 'active', 'XAUUSD')""",
            (org_id, foreign_account, foreign_org))

    data = client.get(f"/api/orgs/{org_id}/overview").json()

    assert data["accounts_connected"] == 3          # not 4
    assert data["copied_today"] == 1                # not 2
    assert data["yesterday"]["total_balance"] == 10000.0   # foreign 777777 excluded
    assert [c["symbol"] for c in data["recent_copies"]] == ["EURUSD"]


def test_overview_ignores_snapshots_with_no_org(seeded, db):
    """Pre-006 rows (org_id NULL) belong to no desk and are simply not
    counted -- they must never fall through into an org's totals."""
    client, org_id = seeded
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO portfolio_snapshots
                   (snapshot_date, account_id, balance, equity, org_id)
               VALUES (CURRENT_DATE - 1, 12345, 4242, 4242, NULL)""")

    assert client.get(f"/api/orgs/{org_id}/overview").json()["yesterday"] is None


def test_overview_requires_membership(seeded, make_user, login_as):
    client, org_id = seeded
    outsider = make_user(email="nobody@example.com")
    login_as(client, outsider)
    assert client.get(f"/api/orgs/{org_id}/overview").status_code == 404
