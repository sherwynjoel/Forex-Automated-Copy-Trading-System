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
