# api/tests/test_portal_account_requests.py
"""Live account requests: a verified investor with no trading account asks
for one from a package, with two MT5 passwords sealed until an admin acts;
the admin reveals them, then fulfils (login, server, optional link) or
rejects (Task 9). Decisions wipe the passwords and email the investor."""
import os

import psycopg
import pytest

from portal_helpers import (add_package, csrf, kyc_profile, link, member,
                            open_account_request)

from api import portal_identity as pid
from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    """A verified investor logged in and one 1:100/200/500 package.
    Returns (client, org_id, investor, package_id, seed)."""
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    kyc_profile(db, org_id, investor["id"], status="approved")
    package_id = add_package(db, org_id)
    login_as(client, investor)
    return client, org_id, investor, package_id, seed


def _request(client, org_id, default_package, **over):
    # Not named package_id: an over["package_id"] (the 404 case) would
    # collide with it ("got multiple values for argument").
    body = {"package_id": default_package, "leverage": 200, "main_password": "Main1234",
            "investor_password": "Look1234", "mpin": "123456", **over}
    return client.post(f"/api/orgs/{org_id}/investor/account-requests", json=body,
                       headers=csrf(client))


def _sealed(db, req_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT main_password_enc, investor_password_enc FROM account_requests WHERE id = %s",
            (req_id,)).fetchone()


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


# ------------------------------------------------------------ investor


def test_a_verified_investor_requests_an_account(portal, db):
    client, org_id, investor, package_id, _ = portal
    r = _request(client, org_id, package_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "requested" and body["package_name"] == "Standard"
    assert body["leverage"] == 200 and body["user_id"] == investor["id"]
    assert "Main1234" not in r.text and "password" not in r.text
    main_enc, inv_enc = _sealed(db, body["id"])
    assert main_enc != "Main1234"
    assert pid.unseal(os.environ["FERNET_KEY"], main_enc) == "Main1234"
    assert pid.unseal(os.environ["FERNET_KEY"], inv_enc) == "Look1234"
    severity, payload = _events(db, org_id, "investor_account_requested")[-1]
    assert severity == "warning" and payload["user_id"] == investor["id"]
    assert payload["request_id"] == body["id"] and "Main1234" not in str(payload)
    listed = client.get(f"/api/orgs/{org_id}/investor/account-requests").json()
    assert [r["id"] for r in listed] == [body["id"]] and "password" not in str(listed)


def test_the_mpin_comes_first(portal):
    client, org_id, _, package_id, _ = portal
    r = _request(client, org_id, package_id, mpin=None, leverage=7)
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"


def test_a_bad_package_id_is_refused_after_the_mpin_and_echoes_no_password(portal):
    client, org_id, _, _, _ = portal
    r = _request(client, org_id, None, package_id="nope", mpin=None)
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = _request(client, org_id, None, package_id="nope")
    assert r.status_code == 404 and "Main1234" not in r.text


def test_a_422_never_echoes_the_request_body(portal):
    client, org_id, _, _, _ = portal
    r = client.post("/api/me/password", json=["Main1234"], headers=csrf(client))
    assert r.status_code == 422 and "Main1234" not in r.text
    assert r.json()["detail"][0]["loc"] == ["body"]


def test_an_unverified_investor_is_sent_to_verify(portal, db):
    client, org_id, investor, package_id, _ = portal
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE kyc_profiles SET status = 'submitted' WHERE user_id = %s",
                     (investor["id"],))
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "verify your identity first"


def test_an_investor_with_an_account_or_an_open_request_is_refused(portal, db):
    client, org_id, investor, package_id, seed = portal
    assert _request(client, org_id, package_id).status_code == 201
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "a request is already open"
    seed(1001, role="slave")
    link(db, org_id, investor["id"], 1001)
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "you already have a trading account"


@pytest.mark.parametrize("over,status,detail", [
    ({"package_id": 999}, 404, "Package not found"),
    ({"leverage": 300}, 400, "leverage must be one of 100, 200, 500"),
    ({"leverage": "200"}, 400, "leverage must be one of 100, 200, 500"),
    ({"main_password": "short1A"}, 400,
     "main_password must be 8-32 characters without spaces, with an upper-case letter, "
     "a lower-case letter and a digit"),
    ({"investor_password": "nouppercase1"}, 400,
     "investor_password must be 8-32 characters without spaces, with an upper-case letter, "
     "a lower-case letter and a digit"),
    ({"investor_password": "Main1234"}, 400,
     "the investor password must differ from the main password"),
])
def test_a_bad_request_is_refused(portal, over, status, detail):
    client, org_id, _, package_id, _ = portal
    r = _request(client, org_id, package_id, **over)
    assert r.status_code == status and r.json()["detail"] == detail


def test_a_disabled_package_cannot_be_requested(portal, db):
    client, org_id, _, _, _ = portal
    hidden = add_package(db, org_id, name="Hidden", enabled=False)
    r = _request(client, org_id, hidden)
    assert r.status_code == 404


def test_the_investor_cancels_and_the_passwords_go(portal, db, make_user):
    client, org_id, investor, package_id, _ = portal
    req_id = _request(client, org_id, package_id).json()["id"]
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests/{req_id}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _sealed(db, req_id) == (None, None)
    assert _events(db, org_id, "investor_account_request_cancelled")[-1][1]["request_id"] == req_id
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests/{req_id}/cancel",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "request is already cancelled"
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    theirs = open_account_request(db, org_id, other["id"], package_id)
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests/{theirs}/cancel",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Request not found"
    # A new request after the cancel is fine.
    assert _request(client, org_id, package_id).status_code == 201


def test_desk_members_cannot_use_the_investor_routes(portal, login_as):
    client, org_id, _, package_id, _ = portal
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _request(client, org_id, package_id).status_code == 403
    assert client.get(f"/api/orgs/{org_id}/investor/account-requests").status_code == 403


# ------------------------------------------------------------ admin

from cryptography.fernet import Fernet

from api.alerts import ALERT_RULES
from api.telegram import TELEGRAM_RULES


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject, text))
        return True


@pytest.fixture
def desk(portal, login_as):
    """Task 8's portal with one open request filed through the API and the
    admin logged in. Returns (client, org_id, investor, package_id, seed, req_id)."""
    client, org_id, investor, package_id, seed = portal
    req_id = _request(client, org_id, package_id).json()["id"]
    client.cookies.clear()
    login_as(client, ADMIN)
    return client, org_id, investor, package_id, seed, req_id


def _act(client, org_id, req_id, verb, **body):
    return client.post(f"/api/orgs/{org_id}/account-requests/{req_id}/{verb}", json=body,
                       headers=csrf(client))


def test_the_admin_queue_lists_open_requests_first_without_passwords(desk, db, make_user):
    client, org_id, investor, package_id, _, req_id = desk
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    older = open_account_request(db, org_id, other["id"], package_id)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (older,))
    rows = client.get(f"/api/orgs/{org_id}/account-requests").json()
    assert [r["id"] for r in rows] == [req_id, older]
    assert rows[0]["email"] == "inv@example.com" and rows[0]["display_name"] == "Inv One"
    assert "password" not in str(rows)
    assert [r["id"] for r in client.get(
        f"/api/orgs/{org_id}/account-requests?status=cancelled").json()] == [older]


def test_reveal_needs_the_admins_mpin_and_is_audited_every_time(desk, db):
    client, org_id, investor, _, _, req_id = desk
    r = _act(client, org_id, req_id, "reveal", mpin="000000")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid MPIN"
    for _ in range(2):
        r = _act(client, org_id, req_id, "reveal", mpin="123456")
        assert r.status_code == 200
        assert r.json() == {"main_password": "Main1234", "investor_password": "Look1234"}
        assert r.headers["cache-control"] == "no-store"
    rows = _events(db, org_id, "account_request_passwords_revealed")
    assert len(rows) == 2
    severity, payload = rows[-1]
    assert severity == "warning" and payload["user_id"] == investor["id"]
    assert payload["request_id"] == req_id and "Main1234" not in str(payload)
    r = _act(client, org_id, 999, "reveal", mpin="123456")
    assert r.status_code == 404 and r.json()["detail"] == "Request not found"


def test_reveal_after_a_key_change_says_what_to_do(desk, db):
    client, org_id, _, _, _, req_id = desk
    foreign = Fernet(Fernet.generate_key())
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET main_password_enc = %s WHERE id = %s",
                     (foreign.encrypt(b"Main1234").decode(), req_id))
    r = _act(client, org_id, req_id, "reveal", mpin="123456")
    assert r.status_code == 409
    assert r.json()["detail"] == ("the passwords can no longer be read; "
                                  "reject this request and ask for a new one")


def test_fulfil_hands_over_the_login_wipes_the_passwords_and_emails(desk, db, monkeypatch):
    client, org_id, investor, _, _, req_id = desk
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=0, mt5_server="Broker-Live")
    assert r.status_code == 400 and r.json()["detail"] == "mt5_login must be a whole number above zero"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=2**63, mt5_server="Broker-Live")
    assert r.status_code == 400 and r.json()["detail"] == "mt5_login must be a whole number above zero"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001)
    assert r.status_code == 400 and r.json()["detail"] == "mt5_server is required"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server=" Broker-Live ",
             note="opened")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "fulfilled" and body["mt5_login"] == 5001
    assert body["mt5_server"] == "Broker-Live" and body["account_id"] is None
    assert body["decision_note"] == "opened" and body["decided_by"] is not None
    assert _sealed(db, req_id) == (None, None)
    to, subject, text = fake.sent[-1]
    assert (to, subject) == ("inv@example.com", "Your trading account is ready")
    assert "Login: 5001" in text and "Server: Broker-Live" in text and "Main1234" not in text
    payload = _events(db, org_id, "investor_account_request_decided")[-1][1]
    assert payload["status"] == "fulfilled" and payload["user_id"] == investor["id"]
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="Broker-Live")
    assert r.status_code == 409 and r.json()["detail"] == "request is already fulfilled"
    r = _act(client, org_id, req_id, "reveal", mpin="123456")
    assert r.status_code == 409


def test_fulfil_can_link_an_account_under_the_phase_1_rules(desk, db, make_user):
    client, org_id, investor, package_id, seed, req_id = desk
    seed(100, role="master")
    seed(1001, role="slave")
    seed(1002, role="slave")
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    link(db, org_id, other["id"], 1002)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=100)
    assert r.status_code == 400
    assert r.json()["detail"] == "The master account cannot be linked to an investor"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=1002)
    assert r.status_code == 404
    assert r.json()["detail"] == "Account not found in this workspace, or already linked"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=1001)
    assert r.status_code == 200 and r.json()["account_id"] == 1001
    with psycopg.connect(db, autocommit=True) as conn:
        (owner,) = conn.execute("SELECT investor_user_id FROM accounts "
                                "WHERE ctid_trader_account_id = 1001").fetchone()
    assert owner == investor["id"]
    with psycopg.connect(db, autocommit=True) as conn:
        # audit_control files account_id in the events column, not the payload.
        (linked,) = conn.execute("SELECT account_id FROM events WHERE org_id = %s AND "
                                 "payload->>'action' = 'investor_account_linked' "
                                 "ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    assert linked == 1001


def test_fulfil_refuses_a_second_account_for_a_linked_investor(desk, db):
    client, org_id, investor, _, seed, req_id = desk
    seed(1001, role="slave")
    seed(1003, role="slave")
    link(db, org_id, investor["id"], 1003)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=1001)
    assert r.status_code == 409 and r.json()["detail"] == "the investor already has a linked account"
    with psycopg.connect(db, autocommit=True) as conn:
        (status,) = conn.execute("SELECT status FROM account_requests WHERE id = %s",
                                 (req_id,)).fetchone()
    assert status == "requested"


def test_reject_needs_a_note_wipes_and_lets_the_investor_try_again(desk, db, login_as,
                                                                   monkeypatch):
    client, org_id, investor, package_id, _, req_id = desk
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = _act(client, org_id, req_id, "reject")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = _act(client, org_id, req_id, "reject", note="Broker paused new accounts")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _sealed(db, req_id) == (None, None)
    assert fake.sent[-1][1] == "Your trading account request was rejected"
    assert "Broker paused new accounts" in fake.sent[-1][2]
    client.cookies.clear()
    login_as(client, investor)
    assert _request(client, org_id, package_id).status_code == 201


def test_the_requests_summary_counts_verifications_and_account_requests(desk, db, make_user):
    client, org_id, _, _, _, _ = desk
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    kyc_profile(db, org_id, other["id"], status="submitted")
    summary = client.get(f"/api/orgs/{org_id}/requests/summary").json()
    assert summary["kyc"] == 1 and summary["account_requests"] == 1
    assert summary["total"] == 2


def test_the_three_phase_2_warnings_reach_both_alerters():
    for action in ("investor_kyc_submitted", "investor_account_requested",
                   "account_request_passwords_revealed"):
        assert ("control", "warning", action) in ALERT_RULES, action
        assert ("control", "warning", action) in TELEGRAM_RULES, action
