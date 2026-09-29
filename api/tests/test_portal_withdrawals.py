"""Payout destinations and withdrawals: the investor saves where money goes,
an admin approves it, the investor asks (with the MPIN) for money out of
`main`, the admin approves, pays outside the app and marks it paid, which
is the one moment the ledger moves."""
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import approved_destination, credit, csrf, seed_file

from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
BANK = {"bank_name": "ICICI Bank", "holder": "Sherwyn Joel", "account_number": "000401234543",
        "code": "ICIC0000004", "bank_address": "Mumbai", "country": "IN"}
CRYPTO = {"coin": "USDT", "network": "TRC20", "address": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _investor(org_client, make_user, login_as, db, email="inv@example.com"):
    client, org_id, seed = org_client
    investor = make_user(email=email)
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    return client, org_id, investor


def _add_destination(client, org_id, kind="bank", details=None, nickname="Salary account",
                     mpin="123456", proof=None):
    body = {"kind": kind, "nickname": nickname,
            "details": details if details is not None else (BANK if kind == "bank" else CRYPTO),
            "mpin": mpin}
    if proof is not None:
        body["proof_file_id"] = proof
    return client.post(f"/api/orgs/{org_id}/investor/payout-destinations", json=body,
                       headers=csrf(client))


def _decide_destination(client, org_id, dest_id, status, note=None):
    return client.post(f"/api/orgs/{org_id}/payout-destinations/{dest_id}/decision",
                       json={"status": status, "note": note}, headers=csrf(client))


def _withdraw(client, org_id, destination_id, amount, mpin="123456"):
    return client.post(f"/api/orgs/{org_id}/investor/withdrawals",
                       json={"destination_id": destination_id, "amount": amount, "mpin": mpin},
                       headers=csrf(client))


def _decide_withdrawal(client, org_id, wd_id, status, note=None):
    return client.post(f"/api/orgs/{org_id}/withdrawals/{wd_id}/decision",
                       json={"status": status, "note": note}, headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()


def _entries(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return [(r[0], float(r[1]), r[2], r[3], r[4]) for r in conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id FROM wallet_entries "
            "WHERE org_id = %s AND user_id = %s ORDER BY id", (org_id, user_id)).fetchall()]


def _figures(db, org_id, user_id, wallet="main"):
    from api.portal_common import wallet_figures
    with psycopg.connect(db, autocommit=True) as conn:
        figures = wallet_figures(conn, org_id, user_id)[wallet]
    return {k: float(v) for k, v in figures.items()}


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject))
        return True


# ------------------------------------------------------ payout destinations


def test_an_investor_adds_a_bank_payout_account_with_the_mpin(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "bank" and body["nickname"] == "Salary account"
    assert body["status"] == "pending" and body["summary"] == "ICICI Bank ••4543"
    assert body["details"]["account_number"] == "000401234543", "the owner sees the full number"
    assert body["proof_file_id"] is None and body["decided_by"] is None
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == [body]
    severity, payload, actor = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_destination_added"
    assert payload["user_id"] == investor["id"] and payload["destination_id"] == body["id"]
    assert "ICICI Bank ••4543" in payload["summary"] and actor == "inv@example.com"


def test_a_wrong_mpin_is_refused_and_counted(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id, mpin="000000")
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid MPIN", "attempts_left": 4}
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []


def test_five_wrong_mpins_lock_money_actions(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    for left in (4, 3, 2, 1):
        r = _add_destination(client, org_id, mpin="000000")
        assert r.status_code == 401 and r.json()["attempts_left"] == left
    r = _add_destination(client, org_id, mpin="000000")
    assert r.status_code == 423 and r.json()["detail"] == "MPIN locked"
    assert r.json()["locked_until"]
    r = _add_destination(client, org_id, mpin="123456")
    assert r.status_code == 423, "the right MPIN does not open a locked step-up"
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []


def test_a_malformed_mpin_is_400_and_an_unset_mpin_is_409(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id, mpin="12")
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = _add_destination(client, org_id, mpin=None)
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_hash = NULL WHERE id = %s", (investor["id"],))
    r = _add_destination(client, org_id)
    assert r.status_code == 409 and r.json()["detail"] == "MPIN not set"


def test_missing_details_are_named(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    bank = {k: v for k, v in BANK.items() if k != "account_number"}
    r = _add_destination(client, org_id, details=bank)
    assert r.status_code == 400 and r.json()["detail"] == "account_number is required"
    crypto = {k: v for k, v in CRYPTO.items() if k != "address"}
    r = _add_destination(client, org_id, kind="crypto", details=crypto)
    assert r.status_code == 400 and r.json()["detail"] == "address is required"
    r = _add_destination(client, org_id, kind="paypal")
    assert r.status_code == 400 and r.json()["detail"] == "kind must be bank or crypto"
    r = _add_destination(client, org_id, nickname="   ")
    assert r.status_code == 400 and r.json()["detail"] == "nickname is required"


def test_a_crypto_destination_summarises_network_and_address(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id, kind="crypto", nickname="Binance")
    assert r.status_code == 201
    assert r.json()["summary"] == "TRC20 T…SE"
    assert r.json()["details"] == CRYPTO


def test_a_proof_must_be_the_callers_payout_proof(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    theirs = seed_file(db, org_id, other["id"], purpose="payout_proof")
    r = _add_destination(client, org_id, proof=theirs)
    assert r.status_code == 400 and r.json()["detail"] == "proof file not found"
    receipt = seed_file(db, org_id, investor["id"], purpose="deposit_receipt")
    r = _add_destination(client, org_id, proof=receipt)
    assert r.status_code == 400 and r.json()["detail"] == "proof file not found"
    proof = seed_file(db, org_id, investor["id"], purpose="payout_proof")
    r = _add_destination(client, org_id, proof=proof)
    assert r.status_code == 201 and r.json()["proof_file_id"] == proof


def test_admin_approves_or_rejects_with_a_note(org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest = _add_destination(client, org_id).json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    queue = client.get(f"/api/orgs/{org_id}/payout-destinations?status=pending").json()
    assert [d["id"] for d in queue] == [dest["id"]] and queue[0]["email"] == "inv@example.com"
    assert queue[0]["display_name"] == "User"
    r = _decide_destination(client, org_id, dest["id"], "rejected")
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = _decide_destination(client, org_id, dest["id"], "shredded")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved or rejected"
    r = _decide_destination(client, org_id, dest["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decided_at"] is not None
    r = _decide_destination(client, org_id, dest["id"], "approved")
    assert r.status_code == 409 and r.json()["detail"] == "payout account is already approved"
    assert _decide_destination(client, org_id, 999, "approved").status_code == 404
    assert _events(db, org_id)[-1][1]["action"] == "investor_destination_decided"
    assert fake.sent == [("inv@example.com", "Your payout account ICICI Bank ••4543 was approved")]


def test_removal_keeps_the_row_but_hides_it(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest = _add_destination(client, org_id).json()
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest['id']}/remove",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "removed"
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest['id']}/remove",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "payout account is already removed"
    assert _events(db, org_id)[-1][1]["action"] == "investor_destination_removed"
    with psycopg.connect(db, autocommit=True) as conn:
        (status,) = conn.execute("SELECT status FROM payout_destinations WHERE id = %s",
                                 (dest["id"],)).fetchone()
    assert status == "removed"


def test_removal_is_refused_while_a_withdrawal_uses_it(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    wd = _withdraw(client, org_id, dest_id, "100").json()
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest_id}/remove",
                    headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["detail"] == "a withdrawal is still using this payout account"
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest_id}/remove",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "removed"


def test_other_investors_cannot_see_or_remove_it(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest = _add_destination(client, org_id).json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest['id']}/remove",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Payout account not found"


# --------------------------------------------------------------- withdrawals


def test_a_withdrawal_needs_an_approved_destination(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    credit(db, org_id, investor["id"], Decimal("1000"))
    pending = _add_destination(client, org_id).json()
    r = _withdraw(client, org_id, pending["id"], "100")
    assert r.status_code == 404 and r.json()["detail"] == "Payout account not found"
    dest_id = approved_destination(db, org_id, investor["id"])
    r = _withdraw(client, org_id, dest_id, "100")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "requested" and body["destination_id"] == dest_id
    assert body["amount"] == 100.0 and body["fee"] == 0.0 and body["net_amount"] == 100.0
    assert body["currency"] == "USD" and body["destination_kind"] == "crypto"
    assert body["destination_summary"] and body["txid"] is None
    assert client.get(f"/api/orgs/{org_id}/investor/withdrawals").json() == [body]
    severity, payload, actor = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_withdrawal_requested"
    assert payload["user_id"] == investor["id"] and payload["withdrawal_id"] == body["id"]
    assert payload["summary"] == (
        f"Withdrawal request: 100.00 USD to {body['destination_summary']} from inv@example.com")


def test_a_wrong_mpin_on_a_withdrawal_is_refused_before_anything_else(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    r = _withdraw(client, org_id, dest_id, "-5", mpin="000000")
    assert r.status_code == 401 and r.json()["attempts_left"] == 4
    r = _withdraw(client, org_id, dest_id, "-5")
    assert r.status_code == 400 and "amount" in r.json()["detail"]


def test_the_cap_is_the_floored_available_and_use_max_is_never_refused(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("5120.50"))
    assert _withdraw(client, org_id, dest_id, "100").status_code == 201
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 5120.5, "on_hold": 100.0, "available": 5020.5}
    r = _withdraw(client, org_id, dest_id, "5020.51")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (5020.50)"
    assert _withdraw(client, org_id, dest_id, "5020.50").status_code == 201, "Use max"
    r = _withdraw(client, org_id, dest_id, "0.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (0.00)"


def test_fee_and_minimum_come_from_portal_settings(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    login_as(client, ADMIN)
    r = client.put(f"/api/orgs/{org_id}/portal-settings",
                   json={"withdrawal_min": "50", "withdrawal_fee_pct": "2.5"},
                   headers=csrf(client))
    assert r.status_code == 200, r.text
    login_as(client, investor)
    r = _withdraw(client, org_id, dest_id, "49.99")
    assert r.status_code == 400 and r.json()["detail"] == "minimum withdrawal is 50.00"
    r = _withdraw(client, org_id, dest_id, "200")
    assert r.status_code == 201
    assert r.json()["amount"] == 200.0 and r.json()["fee"] == 5.0
    assert r.json()["net_amount"] == 195.0
    assert _figures(db, org_id, investor["id"])["on_hold"] == 200.0, "the hold is the gross amount"


def test_cancel_releases_the_hold(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    wd = _withdraw(client, org_id, dest_id, "300").json()
    assert _figures(db, org_id, investor["id"])["available"] == 700.0
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["decided_at"] is not None
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 1000.0, "on_hold": 0.0, "available": 1000.0}
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is already cancelled"
    assert client.post(f"/api/orgs/{org_id}/investor/withdrawals/999/cancel",
                       headers=csrf(client)).status_code == 404
    assert _events(db, org_id)[-1][1]["action"] == "investor_withdrawal_cancelled"
    login_as(client, ADMIN)
    r = _decide_withdrawal(client, org_id, wd["id"], "approved")
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is already cancelled"


def test_approve_then_paid_debits_main_exactly_once(org_client, make_user, login_as, db,
                                                     monkeypatch):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    wd = _withdraw(client, org_id, dest_id, "250").json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    queue = client.get(f"/api/orgs/{org_id}/withdrawals?status=requested").json()
    assert [w["id"] for w in queue] == [wd["id"]] and queue[0]["email"] == "inv@example.com"

    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid", json={"txid": "x"},
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is requested, not approved"
    r = _decide_withdrawal(client, org_id, wd["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 250.0, "approved still holds"
    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid", json={"txid": "  "},
                    headers=csrf(client))
    assert r.status_code == 400 and "txid" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid",
                    json={"txid": "chain-tx-1"}, headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "paid" and r.json()["txid"] == "chain-tx-1"
    assert r.json()["paid_at"] is not None
    assert _entries(db, org_id, investor["id"])[-1] == (
        "main", -250.0, "withdrawal", "withdrawals", wd["id"])
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 750.0, "on_hold": 0.0, "available": 750.0}

    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid",
                    json={"txid": "chain-tx-1"}, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is paid, not approved"
    r = _decide_withdrawal(client, org_id, wd["id"], "rejected", "too late")
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is already paid"
    assert len([e for e in _entries(db, org_id, investor["id"]) if e[2] == "withdrawal"]) == 1
    actions = [e[1]["action"] for e in _events(db, org_id)]
    assert actions[-3:] == ["investor_withdrawal_requested", "investor_withdrawal_decided",
                            "investor_withdrawal_paid"]
    assert fake.sent == [("inv@example.com", "Your withdrawal of 250.00 USD was approved"),
                         ("inv@example.com", "Your withdrawal of 250.00 USD was paid")]


def test_an_approved_request_can_still_be_rejected_with_a_note(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    wd = _withdraw(client, org_id, dest_id, "10").json()
    login_as(client, ADMIN)
    assert _decide_withdrawal(client, org_id, wd["id"], "approved").status_code == 200
    r = _decide_withdrawal(client, org_id, wd["id"], "rejected")
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = _decide_withdrawal(client, org_id, wd["id"], "paid")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved or rejected"
    r = _decide_withdrawal(client, org_id, wd["id"], "rejected", "address did not match")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["decision_note"] == "address did not match"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0
    assert _decide_withdrawal(client, org_id, 999, "approved").status_code == 404


def test_a_failed_email_never_fails_the_decision(org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    wd = _withdraw(client, org_id, dest_id, "10").json()
    login_as(client, ADMIN)
    monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
    r = _decide_withdrawal(client, org_id, wd["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_ten_requests_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    for _ in range(10):
        assert _withdraw(client, org_id, dest_id, "1").status_code == 201
    r = _withdraw(client, org_id, dest_id, "1")
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


def test_investors_only_see_their_own_withdrawals_and_cannot_work_the_queue(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    wd = _withdraw(client, org_id, dest_id, "10").json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/withdrawals").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Withdrawal not found"
    assert _decide_withdrawal(client, org_id, wd["id"], "approved").status_code == 403
    assert client.get(f"/api/orgs/{org_id}/withdrawals").status_code == 403
    assert client.get(f"/api/orgs/{org_id}/payout-destinations").status_code == 403
    assert _decide_destination(client, org_id, dest_id, "approved").status_code == 403


# ------------------------------------------------------ ledger lock wiring


def test_request_withdrawal_takes_the_per_investor_ledger_lock(
        org_client, make_user, login_as, db, monkeypatch):
    """A focused unit check (not a true concurrency test -- see
    test_portal_common.py for that): the route resolves the lock helper
    through the `portal_common` module (imported as `pc` in
    routes/portal_investor.py), so patching it there and recording calls
    proves the route path calls it, with the right (org_id, user_id) and
    before the cap is read."""
    from api import portal_common as pc

    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))

    calls = []
    real_lock = pc.lock_investor_ledger

    def spy(conn, org_id_, user_id_):
        calls.append((org_id_, user_id_))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "lock_investor_ledger", spy)
    r = _withdraw(client, org_id, dest_id, "100")
    assert r.status_code == 201, r.text
    assert calls == [(org_id, investor["id"])]
