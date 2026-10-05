# api/tests/test_portal_common.py
"""portal_common: the figures every portal router reads, the one settlement
writer, audit and email plumbing, the settings row and the serialisers.
Real Postgres; the copier's /state is faked through the app's mock
transport only where equity_for needs it."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from conftest import default_mock_callback, seed_mt5
from fastapi import HTTPException
from portal_helpers import (add_package, approved_destination, credit, link, member,
                            open_account_request)

from api import portal_common as pc
from api import ws as ws_module


@pytest.fixture
def org_user(db, make_user, make_org):
    user = make_user(email="inv@example.com")
    org_id = make_org(name="Desk", members=[(user, "investor")])
    return org_id, user["id"]


def _fake_state(client, accounts=None, down=False):
    """Fake the copier's /state. `accounts` is {account_id: {equity, ...}}
    exactly as the copier serialises it (string keys)."""
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


# ------------------------------------------------------------ re-exports


def test_the_ledger_rules_are_reachable_through_portal_common():
    assert pc.parse_amount("1.50") == Decimal("1.50")
    assert pc.WALLETS == ("main", "credit", "pamm", "social")
    assert pc.can_transition("deposits", "pending", "cancelled") is True
    assert pc.money(Decimal("2.005")) == 2.01
    assert pc.CURRENCY == "USD"


# ------------------------------------------------------------ settlement


def test_settle_writes_once_per_reference(db, org_user):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        first = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="main",
                          amount=Decimal("100.00"), kind="deposit",
                          ref_table="deposits", ref_id=7)
        replay = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="main",
                           amount=Decimal("100.00"), kind="deposit",
                           ref_table="deposits", ref_id=7)
        # An adjustment has no reference, so two of them are two rows.
        adj1 = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="credit",
                         amount=Decimal("-5.00"), kind="adjustment",
                         ref_table=None, ref_id=None, note="fix", created_by=user_id)
        adj2 = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="credit",
                         amount=Decimal("-5.00"), kind="adjustment",
                         ref_table=None, ref_id=None, note="fix", created_by=user_id)
        rows = conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id, note, created_by "
            "FROM wallet_entries WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()
    assert (first, replay, adj1, adj2) == (True, False, True, True)
    assert rows == [("main", Decimal("100.00"), "deposit", "deposits", 7, None, None),
                    ("credit", Decimal("-5.00"), "adjustment", None, None, "fix", user_id),
                    ("credit", Decimal("-5.00"), "adjustment", None, None, "fix", user_id)]
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.wallet_balances(conn, org_id, user_id) == {
            "main": Decimal("100.00"), "credit": Decimal("-10.00"),
            "pamm": Decimal("0"), "social": Decimal("0")}


# ------------------------------------------------------------ locking


def test_lock_investor_ledger_refuses_outside_a_transaction(db, org_user):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(RuntimeError, match="must run inside a transaction"):
            pc.lock_investor_ledger(conn, org_id, user_id)


def test_lock_investor_ledger_serialises_per_investor_not_across_investors(db, org_user):
    """A second, independent connection to the same test database (the `db`
    fixture's DSN): while a first connection holds the transaction-scoped
    lock for (org_id, user_id), a second connection with a short
    lock_timeout cannot take the SAME (org, user) lock -- it waits out the
    timeout and raises LockNotAvailable -- but takes a DIFFERENT investor's
    lock immediately, because the two never share a key. Ending the first
    connection's transaction releases the lock."""
    org_id, user_id = org_user
    other_user_id = user_id + 1
    with psycopg.connect(db, autocommit=True) as first:
        with first.transaction():
            pc.lock_investor_ledger(first, org_id, user_id)
            with psycopg.connect(db, autocommit=True) as second:
                second.execute("SET lock_timeout = '200ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with second.transaction():
                        pc.lock_investor_ledger(second, org_id, user_id)
                # A different investor is a different (namespace, hashtext)
                # key -- no contention, so this must not block or raise.
                with second.transaction():
                    pc.lock_investor_ledger(second, org_id, other_user_id)
        # first's `with` block has now committed, releasing its xact lock.
        with first.transaction():
            pc.lock_investor_ledger(first, org_id, user_id)


# ------------------------------------------------------------ figures


def test_wallet_figures_hold_open_requests_and_floor_available(db, org_user):
    org_id, user_id = org_user
    credit(db, org_id, user_id, "5120.50")
    credit(db, org_id, user_id, "10", wallet="pamm")
    dest = approved_destination(db, org_id, user_id)
    with psycopg.connect(db, autocommit=True) as conn:
        for amount, status in (("100", "requested"), ("30", "approved"),
                               ("999", "rejected"), ("999", "paid"), ("999", "cancelled")):
            conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount, status) "
                "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…23', %s, 0, %s, %s)",
                (org_id, user_id, dest, amount, amount, status))
        for src, tgt, amount, status in (("main", "pamm", "20.25", "requested"),
                                         ("pamm", "main", "4", "approved"),
                                         ("main", "pamm", "999", "done"),
                                         ("main", "pamm", "999", "rejected")):
            conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                "target_kind, target_wallet, amount, status) "
                "VALUES (%s, %s, 'wallet', %s, 'wallet', %s, %s, %s)",
                (org_id, user_id, src, tgt, amount, status))
        holds = pc.wallet_holds(conn, org_id, user_id)
        figures = pc.wallet_figures(conn, org_id, user_id)
    assert holds == {"main": Decimal("150.25"), "credit": Decimal("0"),
                     "pamm": Decimal("4"), "social": Decimal("0")}
    assert figures["main"] == {"balance": Decimal("5120.50"), "on_hold": Decimal("150.25"),
                               "available": Decimal("4970.25")}
    assert figures["pamm"] == {"balance": Decimal("10.00"), "on_hold": Decimal("4"),
                               "available": Decimal("6.00")}
    assert figures["credit"]["available"] == Decimal("0.00")
    assert set(figures) == set(pc.WALLETS)
    # available is floored, never rounded up: the pure rule the figures use.
    assert pc.available(Decimal("5120.506"), Decimal("0")) == Decimal("5120.50")


def test_net_funded_and_open_account_transfers_out(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'investor')",
                     (org_id, investor["id"]))
        conn.execute("UPDATE accounts SET investor_user_id = %s WHERE ctid_trader_account_id = 1001",
                     (investor["id"],))
        rows = [("wallet", "main", None, "account", None, 1001, "500", "done"),
                ("account", None, 1001, "wallet", "main", None, "120", "done"),
                ("account", None, 1001, "wallet", "main", None, "30", "requested"),
                ("account", None, 1001, "wallet", "main", None, "12.50", "approved"),
                ("account", None, 1001, "wallet", "main", None, "999", "rejected"),
                ("wallet", "main", None, "account", None, 1001, "999", "cancelled")]
        for sk, sw, sa, tk, tw, ta, amount, status in rows:
            conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                "source_account_id, target_kind, target_wallet, target_account_id, amount, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (org_id, investor["id"], sk, sw, sa, tk, tw, ta, amount, status))
        assert pc.linked_accounts(conn, org_id, investor["id"]) == [1001]
        assert pc.linked_accounts(conn, org_id, 999999) == []
        assert pc.net_funded(conn, org_id, investor["id"], 1001) == Decimal("380.00")
        assert pc.open_account_transfers_out(conn, org_id, investor["id"], 1001) == Decimal("42.50")
        card = pc.account_card(conn, org_id, 1001)
    assert card == {"account_id": 1001, "nickname": None, "platform": "ctrader",
                    "status": "ok", "last_error": None, "connected": True}


def test_equity_for_prefers_live_then_last_known_then_unknown(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    request = SimpleNamespace(app=client.app)
    with psycopg.connect(db, autocommit=True) as conn:
        (mt5_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled, nickname) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', "
            "true, 'Inv') RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (%s, 'h', 4990.25, 4990.25)", (mt5_id,))
        _fake_state(client, {1001: {"balance": 5000.0, "equity": 5120.5, "open_pnl": 120.5,
                                    "positions": [{"position_id": 7}]}})
        live = asyncio.run(pc.equity_for(request, conn, org_id, 1001))
        _fake_state(client, down=True)
        last_known = asyncio.run(pc.equity_for(request, conn, org_id, int(mt5_id)))
        unknown = asyncio.run(pc.equity_for(request, conn, org_id, 1001))
        none = asyncio.run(pc.equity_for(request, conn, org_id, None))
    assert live == (Decimal("5120.5"), "live", [{"position_id": 7}])
    assert last_known == (Decimal("4990.25"), "last known", [])
    assert unknown == (None, "unknown", [])
    assert none == (None, "unknown", [])


def test_linked_accounts_owns_account_and_pick_account(org_client, make_user, db):
    client, org_id, seed = org_client
    for aid in (1001, 1002, 1003):
        seed(aid, role="slave")
    investor = make_user(email="inv@example.com")
    other = make_user(email="other@example.com")
    member(db, org_id, investor["id"], "investor")
    member(db, org_id, other["id"], "investor")
    uid = investor["id"]

    def refused(account_id):
        with pytest.raises(HTTPException) as exc:
            pc.pick_account(conn, org_id, uid, account_id)
        return exc.value.status_code, exc.value.detail

    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.linked_accounts(conn, org_id, uid) == []
        assert refused(None) == (409, "no account linked yet")
        assert refused(1001) == (404, "Account not found")
    link(db, org_id, uid, 1002)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.pick_account(conn, org_id, uid, None) == 1002
    link(db, org_id, uid, 1001)
    link(db, org_id, other["id"], 1003)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.linked_accounts(conn, org_id, uid) == [1001, 1002]
        assert pc.owns_account(conn, org_id, uid, 1001) is True
        assert pc.owns_account(conn, org_id, uid, 1003) is False
        assert pc.owns_account(conn, org_id, uid, 999) is False
        assert pc.owns_account(conn, org_id, uid, None) is False
        assert refused(None) == (400, "account_id is required")
        assert pc.pick_account(conn, org_id, uid, 1001) == 1001
        assert refused(1003) == (404, "Account not found")
        assert refused(999) == (404, "Account not found")


def test_accounts_used_counts_owned_accounts_and_open_requests(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.accounts_used(conn, org_id, investor["id"]) == 0
    link(db, org_id, investor["id"], 1001)
    req_id = open_account_request(db, org_id, investor["id"], package_id)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.accounts_used(conn, org_id, investor["id"]) == 2
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (req_id,))
        assert pc.accounts_used(conn, org_id, investor["id"]) == 1
    assert pc.account_limit_text(3) == "you have reached the limit of 3 live accounts"


def test_link_account_keeps_the_link_rules_and_the_cap(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(1001, role="slave")
    first, second, taken = (seed_mt5(db, org_id, f"key-{i}") for i in range(3))
    investor = make_user(email="inv@example.com")
    other = make_user(email="other@example.com")
    member(db, org_id, investor["id"], "investor")
    member(db, org_id, other["id"], "investor")
    link(db, org_id, other["id"], taken)
    uid = investor["id"]

    with psycopg.connect(db, autocommit=True) as conn:
        def refused(account_id):
            with pytest.raises(HTTPException) as exc:
                with conn.transaction():
                    pc.link_account(conn, org_id, uid, account_id, mt5_only=True)
            return exc.value.status_code, exc.value.detail

        assert refused(100) == (400, "The master account cannot be linked to an investor")
        assert refused(1001) == (400, "Only an MT5 account can be linked here")
        assert refused(taken) == (404, "Account not found in this workspace, or already linked")
        assert refused(999) == (404, "Account not found in this workspace, or already linked")
        with conn.transaction():
            pc.link_account(conn, org_id, uid, first)
            pc.link_account(conn, org_id, uid, first)   # already this investor's: a no-op
        conn.execute("UPDATE portal_settings SET max_live_accounts = 1 WHERE org_id = %s",
                     (org_id,))
        assert refused(second) == (409, "you have reached the limit of 1 live accounts")
        # Even at the cap, an unknown id is still 404, never the limit's 409.
        assert refused(999) == (404, "Account not found in this workspace, or already linked")
        conn.execute("UPDATE portal_settings SET max_live_accounts = 2 WHERE org_id = %s",
                     (org_id,))
        with conn.transaction():
            pc.link_account(conn, org_id, uid, second)
        assert pc.linked_accounts(conn, org_id, uid) == sorted([first, second])


# ------------------------------------------------------------ settings


def test_portal_settings_creates_the_default_row_once(db, org_user):
    org_id, _user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        first = pc.portal_settings(conn, org_id)
        conn.execute("UPDATE portal_settings SET withdrawal_min = 50, withdrawal_fee_pct = 1.5 "
                     "WHERE org_id = %s", (org_id,))
        second = pc.portal_settings(conn, org_id)
        (count,) = conn.execute("SELECT count(*) FROM portal_settings WHERE org_id = %s",
                                (org_id,)).fetchone()
    assert first == {"withdrawal_min": Decimal("0.00"), "withdrawal_fee_pct": Decimal("0.000"),
                     "max_live_accounts": 5}
    assert second == {"withdrawal_min": Decimal("50.00"), "withdrawal_fee_pct": Decimal("1.500"),
                      "max_live_accounts": 5}
    assert count == 1


# ------------------------------------------------------------ text helpers


def test_destination_summary_and_short_address():
    assert pc.short_address("TAddr123") == "TAddr123"
    assert pc.short_address("TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE") == "T…SE"
    assert pc.destination_summary("bank", {"bank_name": "ICICI Bank", "holder": "S",
                                           "account_number": "000401234543", "code": "X"}) \
        == "ICICI Bank ••4543"
    assert pc.destination_summary("crypto", {"coin": "USDT", "network": "TRC20",
                                             "address": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"}) \
        == "TRC20 T…SE"
    assert pc.qualify("id, user_id", "d") == "d.id, d.user_id"


def test_require_note_on_reject():
    assert pc.require_note_on_reject("confirmed", None) is None
    assert pc.require_note_on_reject("confirmed", " ok ") == "ok"
    assert pc.require_note_on_reject("rejected", "no such tx") == "no such tx"
    with pytest.raises(pc.LedgerError, match="note is required"):
        pc.require_note_on_reject("rejected", "  ")
    with pytest.raises(pc.LedgerError, match="at most 500"):
        pc.require_note_on_reject("rejected", "x" * 501)


# ------------------------------------------------------------ serialisers


_TS = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_deposit_json_rounds_to_cents_and_carries_the_currency():
    row = (12, 5, 3, "crypto", "USDT on TRC20", Decimal("5000.00"), Decimal("50.00"), None,
           "tx-1", None, "wallet", None, "sent", "pending", None, None, None, _TS)
    out = pc.deposit_json(row)
    assert out == {"id": 12, "user_id": 5, "method_id": 3, "method_kind": "crypto",
                   "method_label": "USDT on TRC20", "amount": 5000.0, "fee": 50.0,
                   "credited_amount": None, "reference": "tx-1", "receipt_file_id": None,
                   "target": "wallet", "target_account_id": None, "note": "sent",
                   "status": "pending", "decided_by": None, "decided_at": None,
                   "decision_note": None, "created_at": _TS.isoformat(), "currency": "USD"}
    admin = pc.deposit_json(row + ("inv@example.com", "Inv"))
    assert admin["email"] == "inv@example.com" and admin["display_name"] == "Inv"


def test_withdrawal_transfer_entry_and_method_json():
    wd = pc.withdrawal_json((4, 5, 9, "bank", "ICICI Bank ••4543", Decimal("250.00"),
                             Decimal("2.50"), Decimal("247.50"), "requested", None, None, None,
                             None, None, None, _TS))
    assert wd["net_amount"] == 247.5 and wd["destination_summary"] == "ICICI Bank ••4543"
    assert wd["currency"] == "USD" and wd["paid_at"] is None and wd["created_at"] == _TS.isoformat()
    tr = pc.transfer_json((9, 5, "wallet", "main", None, "account", None, 1001, Decimal("100.00"),
                           "requested", Decimal("5120.50"), True, None, None, None, None, None,
                           None, _TS, "inv@example.com", "Inv"))
    assert tr["source"] == {"kind": "wallet", "wallet": "main"}
    assert tr["target"] == {"kind": "account", "account_id": 1001}
    assert tr["equity_at_request"] == 5120.5 and tr["equity_verified"] is True
    assert tr["email"] == "inv@example.com" and tr["currency"] == "USD"
    entry = pc.entry_json((1, "main", Decimal("-100.00"), "withdrawal", "withdrawals", 4, None, _TS))
    assert entry == {"id": 1, "wallet": "main", "amount": -100.0, "kind": "withdrawal",
                     "ref_table": "withdrawals", "ref_id": 4, "note": None,
                     "created_at": _TS.isoformat(), "currency": "USD"}
    method = pc.method_json((3, "crypto", "USDT on TRC20", True, "USD",
                             {"coin": "USDT", "network": "TRC20", "address": "T1"},
                             Decimal("50.00"), Decimal("1.500"), None, 2), public=True)
    assert method == {"id": 3, "kind": "crypto", "label": "USDT on TRC20", "enabled": True,
                      "currency": "USD", "details": {"coin": "USDT", "network": "TRC20",
                                                     "address": "T1"},
                      "min_amount": 50.0, "fee_pct": 1.5, "instructions": None, "sort_order": 2}


def test_destination_json_masks_bank_account_numbers_unless_full():
    details = {"bank_name": "ICICI Bank", "holder": "S", "account_number": "000401234543",
               "code": "ICIC0000004"}
    row = (9, 5, "bank", "Salary account", details, None, "approved", 1, _TS, None, _TS)
    masked = pc.destination_json(row, full=False)
    assert masked["details"]["account_number"] == "••4543"
    assert masked["summary"] == "ICICI Bank ••4543" and masked["nickname"] == "Salary account"
    assert masked["decided_at"] == _TS.isoformat() and "email" not in masked
    full = pc.destination_json(row + ("inv@example.com", "Inv"), full=True)
    assert full["details"]["account_number"] == "000401234543"
    assert full["email"] == "inv@example.com"
    assert details["account_number"] == "000401234543", "the caller's dict is not mutated"


# ------------------------------------------------------------ audit + email


def test_audit_control_writes_one_control_event(db, org_user):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        asyncio.run(pc.audit_control(
            conn, org_id=org_id, action="investor_deposit_noticed", actor_email="inv@example.com",
            user_id=user_id, severity="warning", account_id=None, deposit_id=12,
            summary="Deposit notice: 5000.00 USD via USDT on TRC20 from inv@example.com"))
        asyncio.run(pc.audit_control(
            conn, org_id=org_id, action="investor_deposit_decided", actor_email="admin@example.com",
            user_id=user_id, account_id=1001, deposit_id=12, status="confirmed"))
        rows = conn.execute(
            "SELECT category, severity, account_id, actor_email, payload FROM events "
            "WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()
    assert rows[0][:4] == ("control", "warning", None, "inv@example.com")
    assert rows[0][4] == {"action": "investor_deposit_noticed", "user_id": user_id,
                          "deposit_id": 12,
                          "summary": "Deposit notice: 5000.00 USD via USDT on TRC20 from inv@example.com"}
    assert rows[1][:4] == ("control", "info", 1001, "admin@example.com")
    assert rows[1][4]["status"] == "confirmed" and rows[1][4]["user_id"] == user_id


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject, text))
        return True


def _notes(db, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT topic, title, body, link, read_at FROM notifications WHERE user_id = %s "
            "ORDER BY id", (user_id,)).fetchall()


def test_notify_writes_a_row_and_emails_unless_the_pref_is_off(db, org_user, monkeypatch):
    org_id, user_id = org_user
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    with psycopg.connect(db, autocommit=True) as conn:
        asyncio.run(pc.notify(conn, request, org_id, user_id, "money", "Subject", "Body",
                              "/org/1/invest"))
        conn.execute("INSERT INTO notification_prefs (org_id, user_id, money) "
                     "VALUES (%s, %s, false)", (org_id, user_id))
        asyncio.run(pc.notify(conn, request, org_id, user_id, "money", "Muted", "Body"))
        asyncio.run(pc.notify(conn, request, org_id, user_id, "bonus", "Still on", "x" * 600))
        asyncio.run(pc.notify(conn, request, org_id, 999999, "money", "Nobody", "Body"))
        monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True),
                            raising=False)
        asyncio.run(pc.notify(conn, request, org_id, user_id, "identity", "Fails quietly", "Body"))
        monkeypatch.setattr(ws_module.broadcaster, "alerter", None, raising=False)
        asyncio.run(pc.notify(conn, request, org_id, user_id, "support", "No alerter", "Body"))
        with pytest.raises(ValueError):
            pc.email_wanted(conn, org_id, user_id, "chat")
    assert [(to, subject) for to, subject, _ in fake.sent] == [
        ("inv@example.com", "Subject"), ("inv@example.com", "Still on")]
    assert fake.sent[1][2] == "x" * 499 + "…"
    rows = _notes(db, user_id)
    assert [r[:2] for r in rows] == [("money", "Subject"), ("money", "Muted"),
                                     ("bonus", "Still on"), ("identity", "Fails quietly"),
                                     ("support", "No alerter")]
    assert rows[0][2:] == ("Body", "/org/1/invest", None)
    assert len(rows[2][2]) == 500


def test_notify_admins_reaches_every_admin_and_nobody_else(db, make_user, make_org, monkeypatch):
    a1, a2 = make_user(email="a1@example.com"), make_user(email="a2@example.com")
    viewer, inv = make_user(email="v@example.com"), make_user(email="i@example.com")
    org_id = make_org(members=[(a1, "admin"), (a2, "admin"), (viewer, "viewer"),
                               (inv, "investor")])
    monkeypatch.setattr(ws_module.broadcaster, "alerter", None, raising=False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with psycopg.connect(db, autocommit=True) as conn:
        asyncio.run(pc.notify_admins(conn, request, org_id, "support", "New ticket #1: Deposits",
                                     "Body", "/org/1/requests?tab=support&ticket=1"))
        rows = conn.execute("SELECT user_id, topic FROM notifications ORDER BY user_id").fetchall()
    assert rows == sorted([(a1["id"], "support"), (a2["id"], "support")])


def test_clip_and_investor_link():
    assert pc.clip("abc", 3) == "abc"
    assert pc.clip("abcd", 3) == "ab…"
    assert pc.investor_link(7, "deposit") == "/org/7/invest/deposit"
    assert pc.TOPICS == ("money", "identity", "support", "bonus")


def test_pay_bonus_pays_once_into_credit(org_user, db):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(RuntimeError):
            pc.pay_bonus(conn, org_id, user_id, "signup", Decimal("5"))
        with conn.transaction():
            pc.lock_investor_ledger(conn, org_id, user_id)
            first = pc.pay_bonus(conn, org_id, user_id, "signup", Decimal("50"))
            again = pc.pay_bonus(conn, org_id, user_id, "signup", Decimal("50"))
            nothing = pc.pay_bonus(conn, org_id, user_id, "kyc", Decimal("0"))
            dep = pc.pay_bonus(conn, org_id, user_id, "deposit", Decimal("10"), source_id=7,
                               note="deposit #7")
            dep_again = pc.pay_bonus(conn, org_id, user_id, "deposit", Decimal("10"), source_id=7)
        assert isinstance(first, int) and isinstance(dep, int)
        assert again is None and nothing is None and dep_again is None
        rows = conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id, note FROM wallet_entries "
            "WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()
        assert rows == [("credit", Decimal("50.00"), "bonus", "bonuses", first, None),
                        ("credit", Decimal("10.00"), "bonus", "bonuses", dep, "deposit #7")]
        assert pc.wallet_figures(conn, org_id, user_id)["credit"]["available"] == Decimal("60.00")


def test_rule_bonus_follows_the_rules(org_user, db):
    org_id, _user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.rule_bonus(conn, org_id, "signup") == 0          # creates the row, all off
        assert pc.bonus_rules(conn, org_id)["deposit_cap"] is None
        conn.execute(
            "UPDATE bonus_rules SET signup_enabled = true, signup_amount = 25, "
            "deposit_enabled = true, deposit_pct = 10, deposit_cap = 30 WHERE org_id = %s",
            (org_id,))
        assert pc.rule_bonus(conn, org_id, "signup") == Decimal("25.00")
        assert pc.rule_bonus(conn, org_id, "kyc") == 0
        assert pc.rule_bonus(conn, org_id, "deposit", Decimal("100")) == Decimal("10.00")
        assert pc.rule_bonus(conn, org_id, "deposit", Decimal("1000")) == Decimal("30.00")
