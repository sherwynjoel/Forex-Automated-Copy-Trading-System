# api/tests/test_portal_kyc.py
"""KYC: the investor saves a profile in parts, submits it with the MPIN,
and an admin approves or rejects it (Task 6 adds the admin half). An
approved profile stays approved through contact edits and returns to
draft on identity edits."""
import psycopg
import pytest

from portal_helpers import COMPLETE_PROFILE, csrf, kyc_profile, member, seed_file

from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    """org_client's org with an investor member logged in."""
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    return client, org_id, investor


def _put(client, org_id, body):
    return client.put(f"/api/orgs/{org_id}/investor/profile", json=body, headers=csrf(client))


def _submit(client, org_id, mpin="123456"):
    return client.post(f"/api/orgs/{org_id}/investor/profile/submit", json={"mpin": mpin},
                       headers=csrf(client))


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


def _status(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT status FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
                            (org_id, user_id)).fetchone()[0]


def _complete(client, org_id, db, investor):
    """Save every required field and the four documents through the API."""
    files = {slot: seed_file(db, org_id, investor["id"], purpose=purpose) for slot, purpose in (
        ("id_front_file_id", "kyc_document"), ("id_back_file_id", "kyc_document"),
        ("address_proof_file_id", "kyc_document"), ("photo_file_id", "kyc_photo"))}
    r = _put(client, org_id, {**COMPLETE_PROFILE, **files})
    assert r.status_code == 200, r.text
    return files


# ------------------------------------------------------------ read + save


def test_a_new_investor_reads_an_empty_draft(portal):
    client, org_id, investor = portal
    body = client.get(f"/api/orgs/{org_id}/investor/profile").json()
    assert body["user_id"] == investor["id"] and body["status"] == "draft"
    assert body["full_name"] is None and body["photo_file_id"] is None
    assert body["missing"][0] == "full_name" and "photo_file_id" in body["missing"]


def test_saving_in_parts_keeps_the_earlier_parts(portal, db):
    client, org_id, investor = portal
    r = _put(client, org_id, {"full_name": " Ada Lovelace ", "gender": "Female",
                              "date_of_birth": "1990-04-02", "phone": "+44 20 1234"})
    assert r.status_code == 200
    r = _put(client, org_id, {"city": "London", "country_residence": "gb"})
    body = r.json()
    assert body["full_name"] == "Ada Lovelace" and body["gender"] == "female"
    assert body["date_of_birth"] == "1990-04-02" and body["city"] == "London"
    assert body["country_residence"] == "GB" and body["status"] == "draft"
    assert client.get(f"/api/orgs/{org_id}/investor/profile").json() == body
    severity, payload, actor = _events(db, org_id, "investor_profile_saved")[-1]
    assert severity == "info" and actor == "inv@example.com"
    assert payload["user_id"] == investor["id"]
    assert payload["fields"] == ["city", "country_residence"] and payload["reverify"] is False
    assert "London" not in str(payload)


def test_blank_clears_a_field_and_an_empty_body_changes_nothing(portal):
    client, org_id, _ = portal
    _put(client, org_id, {"landmark": "Near the park"})
    assert _put(client, org_id, {"landmark": ""}).json()["landmark"] is None
    assert _put(client, org_id, {}).status_code == 200


@pytest.mark.parametrize("body,detail", [
    ({"nickname": "x"}, "unknown field: nickname"),
    ({"gender": "x"}, "gender must be one of male, female, other"),
    ({"country_citizenship": "IND"}, "country_citizenship must be a two-letter country code"),
    ({"date_of_birth": "2/4/1990"}, "date_of_birth must be a date (YYYY-MM-DD)"),
    ({"photo_file_id": "12"}, "photo_file_id must be a file id"),
])
def test_a_bad_field_is_refused(portal, body, detail):
    client, org_id, _ = portal
    r = _put(client, org_id, body)
    assert r.status_code == 400 and r.json()["detail"] == detail


def test_documents_must_be_the_investors_own_unused_uploads(portal, db, make_user):
    client, org_id, investor = portal
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    theirs = seed_file(db, org_id, other["id"], purpose="kyc_document")
    photo = seed_file(db, org_id, investor["id"], purpose="kyc_photo")
    receipt = seed_file(db, org_id, investor["id"], purpose="deposit_receipt")
    doc = seed_file(db, org_id, investor["id"], purpose="kyc_document")
    for body, detail in (
            ({"id_front_file_id": theirs}, "ID front file not found"),
            ({"id_front_file_id": photo}, "ID front file not found"),
            ({"photo_file_id": receipt}, "photo file not found"),
            ({"id_front_file_id": doc, "id_back_file_id": doc}, "each document needs its own file")):
        r = _put(client, org_id, body)
        assert r.status_code == 400 and r.json()["detail"] == detail, body
    assert _put(client, org_id, {"id_front_file_id": doc}).status_code == 200
    # The same file in the same slot again is not a reuse.
    assert _put(client, org_id, {"id_front_file_id": doc, "phone": "1"}).status_code == 200
    # ...but moving it into another slot is.
    r = _put(client, org_id, {"id_back_file_id": doc})
    assert r.status_code == 400 and r.json()["detail"] == "each document needs its own file"


# ------------------------------------------------------------ submit


def test_submit_needs_the_mpin_and_a_complete_profile(portal, db):
    client, org_id, investor = portal
    _put(client, org_id, {"full_name": "Ada"})
    r = _submit(client, org_id, mpin="12")
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = _submit(client, org_id, mpin="000000")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid MPIN"
    r = _submit(client, org_id)
    assert r.status_code == 400
    body = r.json()
    assert body["missing"][0] == "gender" and "photo_file_id" in body["missing"]
    assert body["detail"] == "complete your profile first: " + ", ".join(body["missing"])
    assert _status(db, org_id, investor["id"]) == "draft"


def test_a_complete_profile_submits_and_locks(portal, db):
    client, org_id, investor = portal
    _complete(client, org_id, db, investor)
    r = _submit(client, org_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "submitted" and body["submitted_at"] and body["missing"] == []
    severity, payload, _ = _events(db, org_id, "investor_kyc_submitted")[-1]
    assert severity == "warning" and payload["user_id"] == investor["id"]
    r = _put(client, org_id, {"phone": "2"})
    assert r.status_code == 409 and r.json()["detail"] == "your profile is under review"
    r = _submit(client, org_id)
    assert r.status_code == 409 and r.json()["detail"] == "your profile is already submitted"


def test_a_rejected_profile_is_edited_then_resubmitted(portal, db):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="rejected")
    r = _put(client, org_id, {"id_number": "P7654321"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    r = _submit(client, org_id)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    assert r.json()["decision_note"] is None


def test_an_approved_profile_survives_contact_edits_but_not_identity_edits(portal, db):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="approved")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE kyc_profiles SET decision_note = 'ok', decided_at = now() "
                     "WHERE user_id = %s", (investor["id"],))
    r = _put(client, org_id, {"phone": "+91 1", "city": "Chennai", "postal_code": "600001"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decision_note"] == "ok"
    r = _put(client, org_id, {"full_name": "Investor Renamed"})
    body = r.json()
    assert body["status"] == "draft" and body["decision_note"] is None and body["decided_at"] is None
    payload = _events(db, org_id, "investor_profile_saved")[-1][1]
    assert payload["reverify"] is True
    r = _submit(client, org_id)
    assert r.status_code == 200 and r.json()["status"] == "submitted"


def test_the_profile_routes_are_for_investors_only(portal, login_as):
    client, org_id, _ = portal
    client.cookies.clear()
    login_as(client, ADMIN)
    assert client.get(f"/api/orgs/{org_id}/investor/profile").status_code == 403
    assert _put(client, org_id, {"phone": "1"}).status_code == 403
    assert _submit(client, org_id).status_code == 403


# ------------------------------------------------------------ admin


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject, text))
        return True


def _decide(client, org_id, user_id, **body):
    return client.post(f"/api/orgs/{org_id}/kyc/{user_id}/decision", json=body,
                       headers=csrf(client))


def test_the_queue_lists_submitted_first_with_who(portal, db, make_user, login_as):
    client, org_id, investor = portal
    other = make_user(email="other@example.com", display_name="Other")
    member(db, org_id, other["id"], "investor")
    kyc_profile(db, org_id, other["id"], status="approved")
    kyc_profile(db, org_id, investor["id"], status="submitted")
    client.cookies.clear()
    login_as(client, ADMIN)
    rows = client.get(f"/api/orgs/{org_id}/kyc").json()
    assert [r["user_id"] for r in rows] == [investor["id"], other["id"]]
    assert rows[0]["email"] == "inv@example.com" and rows[0]["display_name"] == "Inv One"
    assert rows[0]["full_name"] == COMPLETE_PROFILE["full_name"]
    assert [r["user_id"] for r in client.get(f"/api/orgs/{org_id}/kyc?status=approved").json()] \
        == [other["id"]]


def test_an_ex_investors_profile_leaves_the_queue_and_cannot_be_decided(portal, db, login_as):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="submitted")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("DELETE FROM org_memberships WHERE org_id = %s AND user_id = %s",
                     (org_id, investor["id"]))
    client.cookies.clear()
    login_as(client, ADMIN)
    assert client.get(f"/api/orgs/{org_id}/kyc").json() == []
    r = _decide(client, org_id, investor["id"], status="approved")
    assert r.status_code == 404 and r.json()["detail"] == "Profile not found"


def test_an_admin_approves_and_the_investor_is_emailed(portal, db, login_as, monkeypatch):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="submitted")
    client.cookies.clear()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = _decide(client, org_id, investor["id"], status="approved")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and body["decided_by"] is not None and body["decided_at"]
    assert fake.sent[0][:2] == ("inv@example.com", "Your identity verification was approved")
    severity, payload, actor = _events(db, org_id, "investor_kyc_decided")[-1]
    assert severity == "info" and payload["user_id"] == investor["id"]
    assert payload["status"] == "approved" and actor == "admin@example.com"
    r = _decide(client, org_id, investor["id"], status="rejected", note="late")
    assert r.status_code == 409 and r.json()["detail"] == "profile is approved, not submitted"


def test_a_rejection_needs_a_note_and_bad_input_is_refused(portal, db, login_as):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="submitted")
    client.cookies.clear()
    login_as(client, ADMIN)
    r = _decide(client, org_id, investor["id"], status="rejected")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = _decide(client, org_id, investor["id"], status="maybe")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved or rejected"
    r = _decide(client, org_id, 9999, status="approved")
    assert r.status_code == 404 and r.json()["detail"] == "Profile not found"
    r = _decide(client, org_id, investor["id"], status="rejected", note="ID photo is blurred")
    assert r.status_code == 200 and r.json()["decision_note"] == "ID photo is blurred"
    assert _status(db, org_id, investor["id"]) == "rejected"


def test_kyc_status_rides_on_the_summary_and_the_investors_list(portal, db, login_as):
    client, org_id, investor = portal
    assert client.get(f"/api/orgs/{org_id}/investor/summary").json()["kyc_status"] == "draft"
    kyc_profile(db, org_id, investor["id"], status="approved")
    assert client.get(f"/api/orgs/{org_id}/investor/summary").json()["kyc_status"] == "approved"
    client.cookies.clear()
    login_as(client, ADMIN)
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert row["kyc_status"] == "approved"


def test_the_admin_kyc_routes_refuse_investors_and_viewers(portal, db, make_user, login_as):
    client, org_id, investor = portal
    assert client.get(f"/api/orgs/{org_id}/kyc").status_code == 403
    assert _decide(client, org_id, investor["id"], status="approved").status_code == 403
    viewer = make_user(email="v@example.com")
    member(db, org_id, viewer["id"], "viewer")
    client.cookies.clear()
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/kyc").status_code == 403
