# api/tests/test_portal_tickets.py
"""Support tickets: subjects (admin), the investor's tickets and the desk's
queue. Tasks 5-7 of the phase 4 plan grow this file."""
import psycopg
import pytest

from portal_helpers import csrf, member, seed_file

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


def _user_id(db, email):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


def _subject(client, org_id, label, **extra):
    return client.post(f"/api/orgs/{org_id}/ticket-subjects", json={"label": label, **extra},
                       headers=csrf(client))


# ------------------------------------------------------------ subjects


def test_admin_manages_subjects_and_investors_see_the_enabled_ones(org_client, make_user,
                                                                   login_as, db):
    client, org_id, _seed = org_client
    r = _subject(client, org_id, "  Deposits ", sort=2)
    assert r.status_code == 201, r.text
    dep = r.json()
    assert (dep["label"], dep["enabled"], dep["sort"]) == ("Deposits", True, 2)
    wd = _subject(client, org_id, "Withdrawals", sort=1).json()
    other = _subject(client, org_id, "Other", sort=3).json()
    r = _subject(client, org_id, "deposits")
    assert r.status_code == 409 and r.json()["detail"] == "a subject with this label already exists"
    assert _subject(client, org_id, " ").json()["detail"] == "label is required"
    assert _subject(client, org_id, "x" * 81).json()["detail"] == "label must be at most 80 characters"
    base = f"/api/orgs/{org_id}/ticket-subjects"
    r = client.patch(f"{base}/{other['id']}", json={"enabled": False}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = client.patch(f"{base}/{wd['id']}", json={"label": "DEPOSITS"}, headers=csrf(client))
    assert r.status_code == 409
    r = client.patch(f"{base}/999", json={"sort": 1}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Subject not found"
    assert [s["label"] for s in client.get(base).json()] == ["Withdrawals", "Deposits", "Other"]
    assert [e[1]["change"] for e in _events(db, org_id, "ticket_subject_changed")] == [
        "created", "created", "created", "updated"]
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    client.cookies.clear()
    login_as(client, investor)
    assert [s["label"] for s in client.get(
        f"/api/orgs/{org_id}/investor/ticket-subjects").json()] == ["Withdrawals", "Deposits"]


def test_deleting_a_used_subject_keeps_the_ticket_label(org_client, make_user, db):
    client, org_id, _seed = org_client
    subject = _subject(client, org_id, "Deposits").json()
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (ticket_id,) = conn.execute(
            "INSERT INTO tickets (org_id, user_id, subject_id, subject_label) "
            "VALUES (%s, %s, %s, 'Deposits') RETURNING id",
            (org_id, investor["id"], subject["id"])).fetchone()
    url = f"/api/orgs/{org_id}/ticket-subjects/{subject['id']}"
    assert client.delete(url, headers=csrf(client)).status_code == 204
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT subject_id, subject_label FROM tickets WHERE id = %s",
                            (ticket_id,)).fetchone() == (None, "Deposits")
    assert client.delete(url, headers=csrf(client)).status_code == 404
    assert _events(db, org_id, "ticket_subject_changed")[-1][1]["change"] == "deleted"


def test_a_file_in_a_ticket_message_is_not_free_any_more(org_client, make_user, db):
    from api.routes.portal_files import file_belongs
    _client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    image = seed_file(db, org_id, investor["id"], purpose="ticket_attachment")
    with psycopg.connect(db, autocommit=True) as conn:
        assert file_belongs(conn, org_id, investor["id"], image, "ticket_attachment")
        (ticket_id,) = conn.execute(
            "INSERT INTO tickets (org_id, user_id, subject_label) VALUES (%s, %s, 'X') "
            "RETURNING id", (org_id, investor["id"])).fetchone()
        conn.execute(
            "INSERT INTO ticket_messages (ticket_id, org_id, author_id, from_desk, body, file_ids) "
            "VALUES (%s, %s, %s, false, 'Hi', %s::bigint[])",
            (ticket_id, org_id, investor["id"], [image]))
        assert not file_belongs(conn, org_id, investor["id"], image, "ticket_attachment")
