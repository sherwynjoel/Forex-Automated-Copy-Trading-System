# api/tests/test_portal_deposits.py
"""Deposits end to end: the investor files a notice against a payment
method, may cancel it while pending, and an admin confirms (crediting the
wallet exactly once, and parking the money in an approved transfer when the
notice targets the trading account) or rejects with a note."""
from decimal import Decimal

import psycopg
import pytest
from portal_helpers import add_method, csrf, link, seed_file

from api import portal_common as pc
from api import ws as ws_module

CRYPTO = {"coin": "USDT", "network": "TRC20", "address": "TAddr123"}
BANK = {"bank_name": "ICICI Bank", "holder": "Desk Ltd", "account_number": "000401234543",
        "code": "ICIC0000004"}
ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _investor(org_client, make_user, login_as, db, *, link_to=None, **method):
    """An investor member logged in, with one enabled crypto method charging
    1 % unless overridden. Returns (client, org_id, investor, method_id)."""
    client, org_id, seed = org_client
    opts = {"kind": "crypto", "label": "USDT on TRC20", "details": CRYPTO, "fee_pct": "1",
            **method}
    method_id = add_method(db, org_id, **opts)
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    if link_to is not None:
        seed(link_to, role="slave")
        link(db, org_id, investor["id"], link_to)
    login_as(client, investor)
    return client, org_id, investor, method_id


def _notice(client, org_id, default_method_id, **over):
    # A plain positional `method_id` parameter would collide with an
    # `over["method_id"]` override (Python: "got multiple values for
    # argument"), which the 404 case of test_a_bad_notice_is_refused_...
    # relies on. The rename keeps every call site (all positional) working
    # unchanged while letting **over's own "method_id" win the merge below.
    body = {"method_id": default_method_id, "amount": "5000", "reference": "chain-tx-1", **over}
    return client.post(f"/api/orgs/{org_id}/investor/deposits", json=body, headers=csrf(client))


def _decide(client, org_id, deposit_id, **body):
    return client.post(f"/api/orgs/{org_id}/deposits/{deposit_id}/decision", json=body,
                       headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email, account_id FROM events WHERE org_id = %s "
            "ORDER BY id", (org_id,)).fetchall()


def _entries(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id, created_by FROM wallet_entries "
            "WHERE org_id = %s AND user_id = %s ORDER BY id", (org_id, user_id)).fetchall()


def _figures(db, org_id, user_id, wallet="main"):
    with psycopg.connect(db, autocommit=True) as conn:
        return pc.wallet_figures(conn, org_id, user_id)[wallet]


def _admin_id(db):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()[0]


# ------------------------------------------------------------ filing


def test_an_investor_files_a_notice_with_the_method_snapshot_and_fee(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    r = _notice(client, org_id, method_id, reference=" chain-tx-1 ", note="sent from Binance")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount"] == 5000.0 and body["fee"] == 50.0 and body["credited_amount"] is None
    assert body["method_id"] == method_id and body["method_kind"] == "crypto"
    assert body["method_label"] == "USDT on TRC20" and body["currency"] == "USD"
    assert body["reference"] == "chain-tx-1" and body["status"] == "pending"
    assert body["target"] == "wallet" and body["target_account_id"] is None
    assert body["receipt_file_id"] is None and body["note"] == "sent from Binance"
    assert body["decided_by"] is None and body["decision_note"] is None
    assert "email" not in body
    assert client.get(f"/api/orgs/{org_id}/investor/deposits").json() == [body]
    severity, payload, actor, account_id = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_deposit_noticed"
    assert payload["user_id"] == investor["id"] and payload["deposit_id"] == body["id"]
    assert payload["amount"] == 5000.0 and payload["fee"] == 50.0
    assert payload["summary"] == "Deposit notice: 5000.00 USD via USDT on TRC20 from inv@example.com"
    assert actor == "inv@example.com" and account_id is None
    assert _entries(db, org_id, investor["id"]) == [], "a notice moves no money"


def test_notices_come_back_newest_first(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    first = _notice(client, org_id, method_id, reference="a").json()["id"]
    second = _notice(client, org_id, method_id, reference="b").json()["id"]
    assert [d["id"] for d in client.get(f"/api/orgs/{org_id}/investor/deposits").json()] \
        == [second, first]


@pytest.mark.parametrize("over, status, detail", [
    ({"amount": "-5"}, 400, "amount must be greater than 0"),
    ({"amount": "5.001"}, 400, "amount may have at most two decimals"),
    ({"reference": "  "}, 400, "reference is required"),
    ({"target": "bank"}, 400, "target must be wallet or account"),
    ({"method_id": 999}, 404, "Payment method not found"),
    ({"target": "account"}, 409, "no account linked yet"),
])
def test_a_bad_notice_is_refused_with_the_reason(org_client, make_user, login_as, db,
                                                 over, status, detail):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    r = _notice(client, org_id, method_id, **over)
    assert r.status_code == status and r.json()["detail"] == detail, r.text


def test_the_method_minimum_is_enforced_with_the_figure(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    min_amount="500")
    r = _notice(client, org_id, method_id, amount="499.99")
    assert r.status_code == 400
    assert r.json()["detail"] == "minimum deposit for this method is 500.00"
    assert _notice(client, org_id, method_id, amount="500").status_code == 201


def test_a_disabled_or_foreign_method_is_not_found(org_client, make_user, login_as, make_org, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    off = add_method(db, org_id, kind="crypto", label="Off", details=CRYPTO, enabled=False)
    foreign = add_method(db, make_org(name="Other"), kind="crypto", label="Foreign", details=CRYPTO)
    for bad in (off, foreign):
        r = _notice(client, org_id, bad)
        assert r.status_code == 404 and r.json()["detail"] == "Payment method not found"


def test_bank_deposits_need_a_receipt_that_is_the_investors_own(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    kind="bank", label="ICICI Bank",
                                                    details=BANK, fee_pct="0")
    r = _notice(client, org_id, method_id)
    assert r.status_code == 400 and r.json()["detail"] == "receipt is required for bank deposits"
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    theirs = seed_file(db, org_id, other["id"])
    wrong_purpose = seed_file(db, org_id, investor["id"], purpose="payout_proof")
    for bad in (theirs, wrong_purpose, 999):
        r = _notice(client, org_id, method_id, receipt_file_id=bad)
        assert r.status_code == 400 and r.json()["detail"] == "receipt file not found", bad
    mine = seed_file(db, org_id, investor["id"])
    r = _notice(client, org_id, method_id, receipt_file_id=mine)
    assert r.status_code == 201 and r.json()["receipt_file_id"] == mine
    assert r.json()["method_kind"] == "bank" and r.json()["fee"] == 0.0
    r = _notice(client, org_id, method_id, reference="chain-tx-2", receipt_file_id=mine)
    assert r.status_code == 400
    assert r.json()["detail"] == "receipt file is already attached to another notice"


def test_the_already_attached_check_never_leaks_another_investors_receipt(
        org_client, make_user, login_as, db):
    """The 'already attached' probe must not become a yes/no oracle over
    every other investor's (or org's) receipts: a caller who names a file
    id that IS already attached, but to someone else's notice, learns only
    'not found' -- exactly what a foreign or nonexistent id also returns."""
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    other = make_user(email="other2@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    other_file = seed_file(db, org_id, other["id"])
    r = _notice(client, org_id, method_id, reference="other-tx", receipt_file_id=other_file)
    assert r.status_code == 201 and r.json()["receipt_file_id"] == other_file

    login_as(client, investor)
    r = _notice(client, org_id, method_id, reference="mine-tx", receipt_file_id=other_file)
    assert r.status_code == 400 and r.json()["detail"] == "receipt file not found"


def test_a_notice_may_target_the_linked_trading_account(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    link_to=1001)
    r = _notice(client, org_id, method_id, target="account", target_account_id=1002)
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = _notice(client, org_id, method_id, target="account")
    assert r.status_code == 201
    assert r.json()["target"] == "account" and r.json()["target_account_id"] == 1001
    _severity, payload, _actor, account_id = _events(db, org_id)[-1]
    assert payload["target"] == "account" and account_id == 1001


def test_one_live_reference_per_workspace(org_client, make_user, login_as, db):
    """Two notices quoting one transaction are the same money twice. A
    cancelled or rejected row frees the reference; a confirmed one keeps it."""
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    first = _notice(client, org_id, method_id)
    assert first.status_code == 201
    r = _notice(client, org_id, method_id, amount="4000", reference=" chain-tx-1 ")
    assert r.status_code == 409 and r.json()["detail"] == "A notice with this reference already exists"

    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert _notice(client, org_id, method_id).status_code == 409

    login_as(client, investor)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits/{first.json()['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    second = _notice(client, org_id, method_id, amount="4000")
    assert second.status_code == 201 and second.json()["amount"] == 4000.0

    login_as(client, ADMIN)
    assert _decide(client, org_id, second.json()["id"], status="confirmed").status_code == 200
    login_as(client, investor)
    assert _notice(client, org_id, method_id).status_code == 409


def test_ten_notices_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    for i in range(10):
        assert _notice(client, org_id, method_id, amount="1", reference=f"t{i}").status_code == 201
    r = _notice(client, org_id, method_id, amount="1", reference="t10")
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


# ------------------------------------------------------------ cancel


def test_the_investor_cancels_a_pending_notice_only(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    url = f"/api/orgs/{org_id}/investor/deposits/{dep['id']}/cancel"

    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    r = client.post(url, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Deposit not found"

    login_as(client, investor)
    r = client.post(url, headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled" and r.json()["decided_at"] is not None
    assert r.json()["decided_by"] == investor["id"]
    r = client.post(url, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already cancelled"
    assert client.post(f"/api/orgs/{org_id}/investor/deposits/999/cancel",
                       headers=csrf(client)).status_code == 404

    severity, payload, actor, _ = _events(db, org_id)[-1]
    assert severity == "info" and payload["action"] == "investor_deposit_cancelled"
    assert payload["deposit_id"] == dep["id"] and payload["user_id"] == investor["id"]
    assert actor == "inv@example.com"

    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already cancelled"
    assert _entries(db, org_id, investor["id"]) == []


# ------------------------------------------------------------ admin decisions


def test_admin_confirms_once_and_the_wallet_is_credited_exactly_once(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    login_as(client, ADMIN)
    queue = client.get(f"/api/orgs/{org_id}/deposits?status=pending").json()
    assert [d["id"] for d in queue] == [dep["id"]]
    assert queue[0]["email"] == "inv@example.com" and queue[0]["display_name"] == "User"
    assert queue[0]["fee"] == 50.0 and queue[0]["currency"] == "USD"

    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed" and body["credited_amount"] == 4950.0
    assert body["decided_by"] == _admin_id(db) and body["decided_at"] is not None
    assert body["decision_note"] is None
    assert _entries(db, org_id, investor["id"]) == [
        ("main", Decimal("4950.00"), "deposit", "deposits", dep["id"], _admin_id(db))]
    assert _figures(db, org_id, investor["id"]) == {
        "balance": Decimal("4950.00"), "on_hold": Decimal("0"), "available": Decimal("4950.00")}

    # A replayed decision (double click, retried request) is refused and
    # the ledger still holds ONE entry.
    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already confirmed"
    r = _decide(client, org_id, dep["id"], status="rejected", note="changed my mind")
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already confirmed"
    assert len(_entries(db, org_id, investor["id"])) == 1

    events = _events(db, org_id)
    assert [e[1]["action"] for e in events[-2:]] == ["investor_deposit_noticed",
                                                     "investor_deposit_decided"]
    severity, payload, actor, _ = events[-1]
    assert severity == "info" and actor == "admin@example.com"
    assert payload["deposit_id"] == dep["id"] and payload["status"] == "confirmed"
    assert payload["credited_amount"] == 4950.0 and payload["user_id"] == investor["id"]
    assert payload["transfer_id"] is None
    assert _decide(client, org_id, 999, status="confirmed").status_code == 404


def test_admin_may_edit_the_credited_amount(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed", credited_amount="0")
    assert r.status_code == 400 and r.json()["detail"] == "credited_amount must be greater than 0"
    r = _decide(client, org_id, dep["id"], status="confirmed", credited_amount="4900.005")
    assert r.status_code == 400 and r.json()["detail"] == "credited_amount may have at most two decimals"
    r = _decide(client, org_id, dep["id"], status="confirmed", credited_amount="4900",
                note="fee was higher on chain")
    assert r.status_code == 200
    assert r.json()["credited_amount"] == 4900.0 and r.json()["amount"] == 5000.0
    assert r.json()["decision_note"] == "fee was higher on chain"
    assert _entries(db, org_id, investor["id"])[0][1] == Decimal("4900.00")


def test_a_rejection_needs_a_note_and_credits_nothing(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="rejected")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = _decide(client, org_id, dep["id"], status="maybe")
    assert r.status_code == 400 and r.json()["detail"] == "status must be confirmed or rejected"
    r = _decide(client, org_id, dep["id"], status="rejected", note="no such transaction",
                credited_amount="4950")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected" and r.json()["credited_amount"] is None
    assert r.json()["decision_note"] == "no such transaction"
    assert _entries(db, org_id, investor["id"]) == []
    assert _events(db, org_id)[-1][1]["status"] == "rejected"


def test_confirming_an_account_deposit_parks_the_money_in_an_approved_transfer(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    link_to=1001)
    dep = _notice(client, org_id, method_id, target="account").json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 200 and r.json()["credited_amount"] == 4950.0
    with psycopg.connect(db, autocommit=True) as conn:
        transfers = conn.execute(
            "SELECT source_kind, source_wallet, target_kind, target_account_id, amount, status, "
            "decided_by, decided_at IS NOT NULL, decision_note, user_id "
            "FROM transfers WHERE org_id = %s", (org_id,)).fetchall()
    assert transfers == [("wallet", "main", "account", 1001, Decimal("4950.00"), "approved",
                          _admin_id(db), True, f"funded from deposit #{dep['id']}",
                          investor["id"])]
    # Credited to main and held there until the admin funds the broker
    # account and marks the transfer done (Task 9).
    assert _figures(db, org_id, investor["id"]) == {
        "balance": Decimal("4950.00"), "on_hold": Decimal("4950.00"), "available": Decimal("0.00")}
    _severity, payload, _actor, account_id = _events(db, org_id)[-1]
    assert payload["action"] == "investor_deposit_decided" and account_id == 1001
    with psycopg.connect(db, autocommit=True) as conn:
        (transfer_id,) = conn.execute("SELECT id FROM transfers WHERE org_id = %s",
                                      (org_id,)).fetchone()
    assert payload["transfer_id"] == transfer_id
    # The replayed decision creates no second transfer either.
    assert _decide(client, org_id, dep["id"], status="confirmed").status_code == 409
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE org_id = %s",
                            (org_id,)).fetchone() == (1,)


def test_an_account_deposit_whose_link_is_gone_is_credited_to_the_wallet(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    link_to=1001)
    dep = _notice(client, org_id, method_id, target="account").json()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET investor_user_id = NULL WHERE ctid_trader_account_id = 1001")
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed", note="seen on chain")
    assert r.status_code == 200
    assert r.json()["decision_note"] == "seen on chain (no account linked; credited to wallet)"
    assert r.json()["target"] == "account" and r.json()["target_account_id"] == 1001
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE org_id = %s",
                            (org_id,)).fetchone() == (0,)
    assert _figures(db, org_id, investor["id"]) == {
        "balance": Decimal("4950.00"), "on_hold": Decimal("0"), "available": Decimal("4950.00")}


def test_the_queue_lists_open_notices_first_and_refuses_investors(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    older = _notice(client, org_id, method_id, reference="a").json()["id"]
    newer = _notice(client, org_id, method_id, reference="b").json()["id"]
    login_as(client, ADMIN)
    assert _decide(client, org_id, newer, status="confirmed").status_code == 200
    queue = client.get(f"/api/orgs/{org_id}/deposits").json()
    assert [(d["id"], d["status"]) for d in queue] == [(older, "pending"), (newer, "confirmed")]
    assert [d["id"] for d in client.get(f"/api/orgs/{org_id}/deposits?status=confirmed").json()] \
        == [newer]
    assert client.get(f"/api/orgs/{org_id}/deposits?status=rejected").json() == []

    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/deposits").status_code == 403
    assert _decide(client, org_id, older, status="confirmed").status_code == 403
    viewer = make_user(email="viewer@example.com")
    _member(db, org_id, viewer, "viewer")
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/deposits").status_code == 403
    assert _decide(client, org_id, older, status="confirmed").status_code == 403


# ------------------------------------------------------------ email


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject, text))
        return True


def test_a_decision_emails_the_investor_and_a_failed_email_never_fails_the_request(
        org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id, amount="250", reference="a").json()
    dep2 = _notice(client, org_id, method_id, amount="6", reference="b").json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    assert _decide(client, org_id, dep["id"], status="confirmed").status_code == 200
    assert [(to, subject) for to, subject, _ in fake.sent] == [
        ("inv@example.com", "Your deposit of 250.00 USD was confirmed")]
    assert "Credited: 247.50 USD" in fake.sent[0][2]
    monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
    r = _decide(client, org_id, dep2["id"], status="rejected", note="no")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
