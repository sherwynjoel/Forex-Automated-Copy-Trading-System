"""The investor's summary and ledger pages, the admin's investor list,
ledger view, adjustments and requests summary, the read-throughs that
moved from the old router, the alert rules, and the proof that the old
router and ledger are gone."""
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import add_method, approved_destination, credit, csrf, link

from api import ws as ws_module
from api.alerts import ALERT_RULES
from api.telegram import TELEGRAM_RULES

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
WARNING_ACTIONS = ("investor_deposit_noticed", "investor_withdrawal_requested",
                   "investor_transfer_requested", "investor_destination_added",
                   "investor_ledger_adjusted", "payment_method_changed")


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _state(client, accounts=None, down=False):
    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            if down:
                return httpx.Response(502, json={"detail": "down"})
            return httpx.Response(200, json={
                "status": "ok",
                "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)


def _investor(org_client, make_user, login_as, db, email="inv@example.com", display_name="User"):
    client, org_id, seed = org_client
    investor = make_user(email=email, display_name=display_name)
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    return client, org_id, investor


def _insert_transfer(db, org_id, user_id, *, source, target, amount, status, account_id=None):
    """source/target are wallet names or 'account'."""
    with psycopg.connect(db, autocommit=True) as conn:
        (tr_id,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
            "source_account_id, target_kind, target_wallet, target_account_id, amount, status, "
            "done_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CASE WHEN %s = 'done' THEN now() END) RETURNING id",
            (org_id, user_id,
             "account" if source == "account" else "wallet",
             None if source == "account" else source,
             account_id if source == "account" else None,
             "account" if target == "account" else "wallet",
             None if target == "account" else target,
             account_id if target == "account" else None,
             Decimal(amount), status, status)).fetchone()
    return tr_id


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject))
        return True


ZERO = {"balance": 0.0, "on_hold": 0.0, "available": 0.0}


# ----------------------------------------------------------------- summary


def test_summary_before_an_account_is_linked(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db,
                                         display_name="Sherwyn Joel")
    r = client.get(f"/api/orgs/{org_id}/investor/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org"] == {"id": org_id, "name": "Desk"} and body["currency"] == "USD"
    assert body["investor"]["display_name"] == "Sherwyn Joel"
    assert body["investor"]["first_name"] == "Sherwyn"
    assert body["investor"]["member_since"] is not None
    assert body["wallets"] == {"main": ZERO, "credit": ZERO, "pamm": ZERO, "social": ZERO}
    assert body["totals"] == {"deposited": 0.0, "withdrawn": 0.0, "transferred_in": 0.0,
                              "transferred_out": 0.0}
    assert body["cash_flow"] == []
    assert body["pending"] == {"deposits": 0, "withdrawals": 0, "transfers": 0,
                               "payout_destinations": 0}
    assert body["deposits_open"] is False
    assert body["withdrawal_rules"] == {"min": 0.0, "fee_pct": 0.0}
    assert body["link_state"] == "unlinked" and body["account"] is None
    assert body["equity_source"] == "unknown" and body["equity"] is None
    assert body["net_funded"] == 0.0 and body["profit"] is None
    assert body["account_available"] is None and body["open_positions"] == 0


def test_summary_figures_from_the_ledger_and_live_equity(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    uid = investor["id"]
    credit(db, org_id, uid, Decimal("5120.50"), kind="deposit")
    credit(db, org_id, uid, Decimal("-100"), kind="withdrawal")
    credit(db, org_id, uid, Decimal("30"), wallet="pamm", kind="bonus")
    seed(1001, role="slave")
    link(db, org_id, uid, 1001)
    _insert_transfer(db, org_id, uid, source="main", target="account", amount="2000",
                     status="done", account_id=1001)
    _insert_transfer(db, org_id, uid, source="account", target="main", amount="20.50",
                     status="requested", account_id=1001)
    _insert_transfer(db, org_id, uid, source="main", target="account", amount="100",
                     status="requested", account_id=1001)
    dest_id = approved_destination(db, org_id, uid)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount, status) "
            "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…st', 50, 0, 50, 'approved')",
            (org_id, uid, dest_id))
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'Pending', '{\"coin\": \"USDT\", \"network\": \"TRC20\", "
            "\"address\": \"TPending\"}')", (org_id, uid))
    _state(client, {1001: {"balance": 2000.0, "equity": 2120.5, "open_pnl": 120.5,
                           "positions": [{"position_id": 7, "symbol": "XAUUSD", "side": "BUY",
                                          "volume": 1, "entry_price": 4350.0,
                                          "pnl_quote": 120.5}]}})
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    # main: 5120.50 - 100 - 2000 (done transfer is settled by the caller in
    # real life; here the ledger row is what counts) -> the ledger only
    # holds what was written: 5020.50, with 150 on hold (100 transfer + 50
    # withdrawal).
    assert body["wallets"]["main"] == {"balance": 5020.5, "on_hold": 150.0, "available": 4870.5}
    assert body["wallets"]["pamm"] == {"balance": 30.0, "on_hold": 0.0, "available": 30.0}
    assert body["totals"] == {"deposited": 5120.5, "withdrawn": 100.0,
                              "transferred_in": 0.0, "transferred_out": 2000.0}
    today = datetime.now(timezone.utc).date().isoformat()
    assert body["cash_flow"] == [{"date": today, "deposits": 5120.5, "withdrawals": 100.0}]
    assert body["pending"] == {"deposits": 0, "withdrawals": 1, "transfers": 2,
                               "payout_destinations": 1}
    assert body["link_state"] == "linked" and body["account"]["account_id"] == 1001
    assert body["equity"] == 2120.5 and body["equity_source"] == "live"
    assert body["net_funded"] == 2000.0 and body["profit"] == 120.5
    assert body["account_available"] == 2100.0, "equity minus the open account->wallet transfer"
    assert body["open_positions"] == 1


def test_summary_falls_back_to_last_known_equity_when_the_copier_is_down(
        org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (aid,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled, nickname, investor_user_id) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', "
            "true, 'Inv', %s) RETURNING ctid_trader_account_id",
            (org_id, investor["id"])).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (%s, 'h', 4990.25, 4990.25)", (aid,))
    _state(client, down=True)
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["equity"] == 4990.25 and body["equity_source"] == "last known"
    assert body["account"]["platform"] == "mt5" and body["account"]["connected"] is False
    assert body["account_available"] == 4990.25 and body["profit"] == 4990.25


def test_deposits_open_and_withdrawal_rules_reflect_admin_settings(
        org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    add_method(db, org_id)
    r = client.put(f"/api/orgs/{org_id}/portal-settings",
                   json={"withdrawal_min": "50", "withdrawal_fee_pct": "2.5"},
                   headers=csrf(client))
    assert r.status_code == 200
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["deposits_open"] is True
    assert body["withdrawal_rules"] == {"min": 50.0, "fee_pct": 2.5}


# ---------------------------------------------------------- wallet entries


def test_wallet_entries_page_filters_and_cursor(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    uid = investor["id"]
    ids = [credit(db, org_id, uid, Decimal("10")), credit(db, org_id, uid, Decimal("20")),
           credit(db, org_id, uid, Decimal("30")),
           credit(db, org_id, uid, Decimal("5"), wallet="pamm", kind="deposit")]
    base = f"/api/orgs/{org_id}/investor/wallet-entries"
    page = client.get(base).json()
    assert [e["id"] for e in page["entries"]] == list(reversed(ids))
    assert page["has_more"] is False and page["next_before"] is None
    first = page["entries"][0]
    assert first["wallet"] == "pamm" and first["amount"] == 5.0 and first["kind"] == "deposit"
    assert first["currency"] == "USD" and first["ref_table"] is None and first["note"] is None

    page = client.get(f"{base}?limit=2").json()
    assert [e["id"] for e in page["entries"]] == [ids[3], ids[2]]
    assert page["has_more"] is True and page["next_before"] == ids[2]
    page = client.get(f"{base}?limit=2&before={page['next_before']}").json()
    assert [e["id"] for e in page["entries"]] == [ids[1], ids[0]] and page["has_more"] is False

    assert [e["id"] for e in client.get(f"{base}?wallet=pamm").json()["entries"]] == [ids[3]]
    assert [e["id"] for e in client.get(f"{base}?kind=adjustment").json()["entries"]] == \
        [ids[2], ids[1], ids[0]]
    today = datetime.now(timezone.utc).date()
    assert len(client.get(f"{base}?from={today}&to={today}").json()["entries"]) == 4
    assert client.get(f"{base}?to={today - timedelta(days=1)}").json()["entries"] == []
    assert client.get(f"{base}?from={today + timedelta(days=1)}").json()["entries"] == []
    assert len(client.get(f"{base}?limit=999").json()["entries"]) == 4

    r = client.get(f"{base}?wallet=gold")
    assert r.status_code == 400
    assert r.json()["detail"] == "wallet must be one of main, credit, pamm, social"
    r = client.get(f"{base}?kind=refund")
    assert r.status_code == 400 and r.json()["detail"].startswith("kind must be one of")
    r = client.get(f"{base}?from=2026-13-01")
    assert r.status_code == 400 and r.json()["detail"] == "from must be a date (YYYY-MM-DD)"


# ----------------------------------------------------------- admin: list


def test_the_investor_list_has_figures_and_asks_the_copier_once(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    seed(1002, role="slave")
    inv1 = make_user(email="inv1@example.com", display_name="Ann")
    inv2 = make_user(email="inv2@example.com", display_name="Bob")
    viewer = make_user(email="v@example.com")
    _member(db, org_id, inv1, "investor")
    _member(db, org_id, inv2, "investor")
    _member(db, org_id, viewer, "viewer")
    link(db, org_id, inv1["id"], 1001)
    credit(db, org_id, inv1["id"], Decimal("500"))
    credit(db, org_id, inv1["id"], Decimal("25"), wallet="credit", kind="bonus")
    _insert_transfer(db, org_id, inv1["id"], source="main", target="account", amount="100",
                     status="requested", account_id=1001)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'P', '{\"coin\": \"USDT\", \"network\": \"TRC20\", "
            "\"address\": \"T1\"}')", (org_id, inv2["id"]))
    calls = {"state": 0}

    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            calls["state"] += 1
            return httpx.Response(200, json={
                "status": "ok",
                "accounts": {"1001": {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0,
                                      "positions": []}},
                "master_positions": [], "pending_orders": [], "drift": []})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)

    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert calls["state"] == 1
    assert [r["email"] for r in rows] == ["inv1@example.com", "inv2@example.com"]
    ann, bob = rows
    assert ann["display_name"] == "Ann" and ann["joined_at"] is not None
    assert ann["account_id"] == 1001 and ann["nickname"] is None
    assert ann["equity"] == 100.0 and ann["equity_source"] == "live"
    assert ann["balances"] == {"main": 500.0, "credit": 25.0, "pamm": 0.0, "social": 0.0}
    assert ann["on_hold"] == 100.0 and ann["available"] == 400.0
    assert ann["pending"] == {"deposits": 0, "withdrawals": 0, "transfers": 1,
                              "payout_destinations": 0}
    assert bob["account_id"] is None and bob["equity"] is None
    assert bob["equity_source"] == "unknown"
    assert bob["balances"] == {"main": 0.0, "credit": 0.0, "pamm": 0.0, "social": 0.0}
    assert bob["pending"]["payout_destinations"] == 1


def test_admin_links_and_unlinks_an_account(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    _state(client, {1001: {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0, "positions": []}})
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 1001}, headers=csrf(client))
    assert r.status_code == 200 and r.json() == {"user_id": investor["id"], "account_id": 1001}
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] == 1001
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 100}, headers=csrf(client))
    assert r.status_code == 400 and "master" in r.json()["detail"]
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] == 1001
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": None}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["account_id"] is None
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] is None
    viewer = make_user(email="v@example.com")
    _member(db, org_id, viewer, "viewer")
    r = client.put(f"/api/orgs/{org_id}/investors/{viewer['id']}/account",
                   json={"account_id": 1001}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    actions = [p["action"] for _, p in _events(db, org_id)]
    assert actions == ["investor_account_linked", "investor_account_linked"]


def test_admin_reads_an_investors_ledger(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    viewer = make_user(email="v@example.com")
    _member(db, org_id, investor, "investor")
    _member(db, org_id, viewer, "viewer")
    entry_id = credit(db, org_id, investor["id"], Decimal("10"))
    page = client.get(f"/api/orgs/{org_id}/investors/{investor['id']}/wallet-entries").json()
    assert [e["id"] for e in page["entries"]] == [entry_id] and page["has_more"] is False
    assert client.get(f"/api/orgs/{org_id}/investors/{investor['id']}/wallet-entries"
                      "?wallet=pamm").json()["entries"] == []
    r = client.get(f"/api/orgs/{org_id}/investors/{viewer['id']}/wallet-entries")
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    login_as(client, investor)
    r = client.get(f"/api/orgs/{org_id}/investors/{investor['id']}/wallet-entries")
    assert r.status_code == 403


# ------------------------------------------------------ admin: adjustments


def test_admin_posts_an_adjustment_with_their_own_mpin(org_client, make_user, login_as, db,
                                                        monkeypatch):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    credit(db, org_id, investor["id"], Decimal("1000"))
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    url = f"/api/orgs/{org_id}/investors/{investor['id']}/adjustments"

    def post(**body):
        return client.post(url, json={"wallet": "main", "amount": "-250.00",
                                      "note": "correction", "mpin": "123456", **body},
                           headers=csrf(client))

    r = post(mpin="000000")
    assert r.status_code == 401 and r.json()["attempts_left"] == 4
    r = post(amount="0")
    assert r.status_code == 400 and r.json()["detail"] == "amount must not be zero"
    r = post(amount="abc")
    assert r.status_code == 400 and "amount" in r.json()["detail"]
    r = post(wallet="gold")
    assert r.status_code == 400
    assert r.json()["detail"] == "wallet must be one of main, credit, pamm, social"
    r = post(note="  ")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = post()
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["wallet"] == "main" and body["amount"] == -250.0
    assert body["kind"] == "adjustment" and body["note"] == "correction"
    assert body["ref_table"] is None and body["currency"] == "USD"
    r = post(wallet="credit", amount="+100", note="welcome bonus")
    assert r.status_code == 201 and r.json()["amount"] == 100.0 and r.json()["wallet"] == "credit"
    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert rows[0]["balances"] == {"main": 750.0, "credit": 100.0, "pamm": 0.0, "social": 0.0}
    severity, payload = _events(db, org_id)[-2]
    assert severity == "warning" and payload["action"] == "investor_ledger_adjusted"
    assert payload["user_id"] == investor["id"] and payload["amount"] == -250.0
    assert payload["wallet"] == "main" and payload["note"] == "correction"
    assert fake.sent == [("inv@example.com", "Your My wallet was adjusted by -250.00 USD"),
                         ("inv@example.com", "Your Credit wallet was adjusted by +100.00 USD")]
    viewer = make_user(email="v@example.com")
    _member(db, org_id, viewer, "viewer")
    r = client.post(f"/api/orgs/{org_id}/investors/{viewer['id']}/adjustments",
                    json={"wallet": "main", "amount": "1", "note": "n", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"


def test_adjustment_takes_the_ledger_lock_for_the_investor_not_the_admin(
        org_client, make_user, db, monkeypatch):
    """Controller ruling: the INSERT runs inside `with conn.transaction():`
    whose first statement is lock_investor_ledger, keyed on the investor
    being adjusted -- never the admin posting it. A call-through spy on the
    helper in the module the route resolves it from (portal_common,
    imported as `pc` in routes/portal_admin.py) proves both that it is
    called and with which (org_id, user_id), the same approach used for the
    withdrawal/transfer/deposit lock-wiring tests."""
    from api import portal_common as pc

    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    credit(db, org_id, investor["id"], Decimal("1000"))

    calls = []
    real_lock = pc.lock_investor_ledger

    def spy(conn, org_id_, user_id_):
        calls.append((org_id_, user_id_))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "lock_investor_ledger", spy)
    r = client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/adjustments",
                    json={"wallet": "main", "amount": "50", "note": "bonus", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 201, r.text
    assert calls == [(org_id, investor["id"])], "locked on the investor, not the admin"


# -------------------------------------------------- admin: requests summary


def test_requests_summary_counts_open_rows(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    uid = investor["id"]
    link(db, org_id, uid, 1001)
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 0, "withdrawals": 0, "transfers": 0, "payout_destinations": 0, "total": 0}
    dest_id = approved_destination(db, org_id, uid)
    with psycopg.connect(db, autocommit=True) as conn:
        for reference, status in (("r1", "pending"), ("r2", "confirmed"), ("r3", "rejected")):
            conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, "
                "reference, status) VALUES (%s, %s, 'crypto', 'USDT on TRC20', 10, %s, %s)",
                (org_id, uid, reference, status))
        for status in ("requested", "approved", "paid", "cancelled"):
            conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount, status) "
                "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…st', 5, 0, 5, %s)",
                (org_id, uid, dest_id, status))
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'P', '{\"coin\": \"USDT\", \"network\": \"TRC20\", "
            "\"address\": \"T1\"}')", (org_id, uid))
    for status in ("requested", "approved", "done", "rejected"):
        _insert_transfer(db, org_id, uid, source="main", target="account", amount="1",
                         status=status, account_id=1001)
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 1, "withdrawals": 2, "transfers": 2, "payout_destinations": 1, "total": 6}


# ------------------------------------------------------------ read-throughs


def _recording_copier(client, accounts=None):
    seen = []

    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        seen.append(url)
        if "/state" in url:
            return httpx.Response(200, json={
                "status": "ok", "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        if "/analytics" in url:
            return httpx.Response(200, json={"closed_trades": 3, "wins": 2, "losses": 1,
                                             "net_pnl": 120.5, "weeks": 4})
        if "/history/" in url:
            return httpx.Response(200, json={"deals": [], "has_more": False})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)
    return seen


def test_read_throughs_need_a_linked_account(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    for tail in ("investor/positions", "investor/analytics",
                 "investor/history/deals?from=0&to=1"):
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 409 and "no account linked" in r.json()["detail"], tail


def test_positions_come_from_the_linked_accounts_state(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    link(db, org_id, investor["id"], 1001)
    _recording_copier(client, {1001: {"balance": 5000.0, "equity": 5010.0, "open_pnl": 10.0,
        "positions": [{"position_id": 7, "symbol_id": 41, "symbol": "XAUUSD", "side": "BUY",
                       "volume": 1, "stop_loss": 4300.0, "take_profit": 4400.0,
                       "entry_price": 4350.0, "pnl_quote": 10.0, "current_price": 4360.0}]},
        1002: {"balance": 1.0, "equity": 1.0, "open_pnl": 0.0,
               "positions": [{"position_id": 8, "symbol_id": 41, "symbol": "XAUUSD",
                              "side": "SELL", "volume": 1, "entry_price": 1.0}]}})
    body = client.get(f"/api/orgs/{org_id}/investor/positions").json()
    assert body["equity_source"] == "live"
    assert body["positions"] == [{"position_id": 7, "symbol": "XAUUSD", "side": "BUY",
                                  "volume": 1, "entry_price": 4350.0, "current_price": 4360.0,
                                  "stop_loss": 4300.0, "take_profit": 4400.0, "pnl_quote": 10.0}]


def test_analytics_and_history_are_asked_for_the_linked_account_only(
        org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    link(db, org_id, investor["id"], 1001)
    seen = _recording_copier(client)
    r = client.get(f"/api/orgs/{org_id}/investor/analytics?weeks=99")
    assert r.status_code == 200 and r.json()["net_pnl"] == 120.5
    assert any(u.endswith("/analytics?account_id=1001&weeks=12") for u in seen)
    r = client.get(f"/api/orgs/{org_id}/investor/history/deals?from=5&to=9")
    assert r.status_code == 200 and r.json() == {"deals": [], "has_more": False}
    assert any(u.endswith("/history/deals?account_id=1001&from=5&to=9") for u in seen)
    assert client.get(f"/api/orgs/{org_id}/investor/history/trades?from=0&to=1").status_code == 400


# ------------------------------------------------------- rules and retirement


def test_the_six_warning_actions_reach_both_alerters():
    for action in WARNING_ACTIONS:
        assert ("control", "warning", action) in ALERT_RULES, action
        assert ("control", "warning", action) in TELEGRAM_RULES, action


def test_the_old_router_and_ledger_are_gone(org_client):
    assert importlib.util.find_spec("api.routes.investor") is None
    assert importlib.util.find_spec("api.investor_ledger") is None
    client, org_id, seed = org_client
    for tail in ("investor-wallet", "investor-deposits", "investor-withdrawals"):
        assert client.get(f"/api/orgs/{org_id}/{tail}").status_code == 404, tail
