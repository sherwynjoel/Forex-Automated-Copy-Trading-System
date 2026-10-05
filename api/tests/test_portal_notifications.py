# api/tests/test_portal_notifications.py
"""Notification routes: any member reads and marks only their OWN rows,
newest first with an id cursor; another user's id is a 404."""
import psycopg
import pytest

from portal_helpers import csrf, member


def _user_id(db, email):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


def _seed(db, org_id, user_id, n, *, read=False):
    """n notifications titled N0..N<n-1>, oldest first; returns their ids."""
    ids = []
    with psycopg.connect(db, autocommit=True) as conn:
        for i in range(n):
            (note_id,) = conn.execute(
                "INSERT INTO notifications (org_id, user_id, topic, title, body, link, read_at) "
                "VALUES (%s, %s, 'money', %s, 'Body', '/x', CASE WHEN %s THEN now() END) "
                "RETURNING id", (org_id, user_id, f"N{i}", read)).fetchone()
            ids.append(int(note_id))
    return ids


def _url(org_id, tail=""):
    return f"/api/orgs/{org_id}/notifications{tail}"


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    client, org_id, _seed_account = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    return client, org_id, investor


def test_the_list_is_own_rows_newest_first_with_a_cursor(portal, db):
    client, org_id, investor = portal
    mine = _seed(db, org_id, investor["id"], 3)
    _seed(db, org_id, _user_id(db, "admin@example.com"), 2)
    body = client.get(_url(org_id, "?limit=2")).json()
    assert [n["title"] for n in body["notifications"]] == ["N2", "N1"]
    assert body["has_more"] is True and body["next_before"] == mine[1]
    first = body["notifications"][0]
    assert first == {"id": mine[2], "topic": "money", "title": "N2", "body": "Body", "link": "/x",
                     "read_at": None, "created_at": first["created_at"]}
    rest = client.get(_url(org_id, f"?limit=2&before={mine[1]}")).json()
    assert [n["title"] for n in rest["notifications"]] == ["N0"]
    assert rest["has_more"] is False and rest["next_before"] is None
    assert len(client.get(_url(org_id)).json()["notifications"]) == 3
    assert len(client.get(_url(org_id, "?limit=0")).json()["notifications"]) == 1


def test_unread_count_mark_one_and_mark_all(portal, db):
    client, org_id, investor = portal
    ids = _seed(db, org_id, investor["id"], 3)
    _seed(db, org_id, investor["id"], 1, read=True)
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 3}
    r = client.post(_url(org_id, f"/{ids[0]}/read"), headers=csrf(client))
    assert r.status_code == 200 and r.json()["read_at"] is not None
    again = client.post(_url(org_id, f"/{ids[0]}/read"), headers=csrf(client)).json()
    assert again["read_at"] == r.json()["read_at"]   # a second mark keeps the first time
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 2}
    assert client.post(_url(org_id, "/read-all"), headers=csrf(client)).json() == {"updated": 2}
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 0}


def test_another_users_notification_is_a_404(portal, db):
    client, org_id, _investor = portal
    (theirs,) = _seed(db, org_id, _user_id(db, "admin@example.com"), 1)
    for note_id in (theirs, 999999):
        r = client.post(_url(org_id, f"/{note_id}/read"), headers=csrf(client))
        assert r.status_code == 404 and r.json()["detail"] == "Notification not found"
    client.post(_url(org_id, "/read-all"), headers=csrf(client))
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT read_at FROM notifications WHERE id = %s",
                            (theirs,)).fetchone() == (None,)


def test_rows_of_another_org_stay_in_that_org(portal, db, make_org):
    client, org_id, investor = portal
    other_org = make_org(name="Other", members=[(investor, "investor")])
    _seed(db, other_org, investor["id"], 2)
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 0}
    assert client.get(_url(other_org, "/unread-count")).json() == {"count": 2}


def test_a_desk_member_reads_their_own_too(org_client, db):
    client, org_id, _seed_account = org_client
    _seed(db, org_id, _user_id(db, "admin@example.com"), 1)
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 1}
