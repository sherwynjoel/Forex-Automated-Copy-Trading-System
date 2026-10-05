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


# ------------------------------------------------------------ investor side


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    """org_client's org with subjects Deposits (enabled) and Old (disabled)
    and an investor member logged in."""
    client, org_id, _seed = org_client
    deposits = _subject(client, org_id, "Deposits").json()
    old = _subject(client, org_id, "Old", enabled=False).json()
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    client.cookies.clear()
    login_as(client, investor)
    return client, org_id, investor, {"deposits": deposits["id"], "old": old["id"]}


def _open(client, org_id, subject_id, body="My deposit is missing", file_ids=None):
    return client.post(f"/api/orgs/{org_id}/investor/tickets",
                       json={"subject_id": subject_id, "body": body, "file_ids": file_ids or []},
                       headers=csrf(client))


def _reply(client, org_id, ticket_id, body="Any news?", file_ids=None, desk=False):
    base = "tickets" if desk else "investor/tickets"
    payload = {"body": body} if desk else {"body": body, "file_ids": file_ids or []}
    return client.post(f"/api/orgs/{org_id}/{base}/{ticket_id}/messages", json=payload,
                       headers=csrf(client))


def test_an_investor_opens_a_ticket_with_images(portal, db):
    client, org_id, investor, subjects = portal
    images = [seed_file(db, org_id, investor["id"], purpose="ticket_attachment") for _ in range(2)]
    r = _open(client, org_id, subjects["deposits"], file_ids=images)
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["status"], t["subject_label"], t["user_id"]) == ("new", "Deposits", investor["id"])
    assert t["last_from_desk"] is False and t["closed_at"] is None
    assert t["waiting_on_desk"] is True
    (m,) = t["messages"]
    assert (m["body"], m["file_ids"], m["from_desk"], m["author_name"]) == (
        "My deposit is missing", images, False, "Inv One")
    severity, payload, actor = _events(db, org_id, "ticket_opened")[-1]
    assert (severity, actor) == ("info", "inv@example.com")
    assert payload == {"action": "ticket_opened", "user_id": investor["id"], "ticket_id": t["id"],
                       "subject": "Deposits", "images": 2,
                       "summary": f"Ticket #{t['id']} opened by inv@example.com: Deposits"}
    with psycopg.connect(db, autocommit=True) as conn:
        notes = conn.execute("SELECT user_id, topic, title, link FROM notifications "
                             "ORDER BY id").fetchall()
    assert notes == [(_user_id(db, "admin@example.com"), "support",
                      f"New ticket #{t['id']}: Deposits",
                      f"/org/{org_id}/requests?tab=support&ticket={t['id']}")]


def test_a_ticket_is_refused_for_a_bad_subject_body_or_images(portal, db, make_user):
    client, org_id, investor, subjects = portal
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    mine = seed_file(db, org_id, investor["id"], purpose="ticket_attachment")
    theirs = seed_file(db, org_id, other["id"], purpose="ticket_attachment")
    receipt = seed_file(db, org_id, investor["id"], purpose="deposit_receipt")
    four = [seed_file(db, org_id, investor["id"], purpose="ticket_attachment") for _ in range(4)]
    dep = subjects["deposits"]
    cases = [
        ({"subject_id": subjects["old"], "body": "x"}, 404, "Subject not found"),
        ({"subject_id": 999, "body": "x"}, 404, "Subject not found"),
        ({"subject_id": None, "body": "x"}, 400, "subject_id is required"),
        ({"subject_id": dep, "body": "  "}, 400, "body is required"),
        ({"subject_id": dep, "body": "x" * 4001}, 400, "body must be at most 4000 characters"),
        ({"subject_id": dep, "body": "x", "file_ids": four}, 400, "at most 3 images per message"),
        ({"subject_id": dep, "body": "x", "file_ids": [mine, mine]}, 400,
         "each image may be attached once"),
        ({"subject_id": dep, "body": "x", "file_ids": [theirs]}, 400, "image not found"),
        ({"subject_id": dep, "body": "x", "file_ids": [receipt]}, 400, "image not found"),
        ({"subject_id": dep, "body": "x", "file_ids": "1"}, 400, "file_ids must be a list of file ids"),
    ]
    for body, status, detail in cases:
        r = client.post(f"/api/orgs/{org_id}/investor/tickets", json=body, headers=csrf(client))
        assert (r.status_code, r.json()["detail"]) == (status, detail), body
    assert _open(client, org_id, dep, file_ids=[mine]).status_code == 201
    r = _open(client, org_id, dep, file_ids=[mine])
    assert r.status_code == 400 and r.json()["detail"] == "image not found"   # already attached


def test_the_list_filters_by_status_and_searches_subject_and_messages(portal, db):
    client, org_id, _investor, subjects = portal
    a = _open(client, org_id, subjects["deposits"], body="USDT never arrived").json()
    b = _open(client, org_id, subjects["deposits"], body="Bank wire 50% fee?").json()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE tickets SET status = 'closed', closed_at = now() WHERE id = %s",
                     (b["id"],))
    url = f"/api/orgs/{org_id}/investor/tickets"
    assert [t["id"] for t in client.get(url).json()] == [b["id"], a["id"]]
    assert [t["id"] for t in client.get(url + "?status=new").json()] == [a["id"]]
    assert [t["id"] for t in client.get(url + "?status=closed").json()] == [b["id"]]
    assert [t["id"] for t in client.get(url + "?q=usdt").json()] == [a["id"]]
    assert [t["id"] for t in client.get(url + "?q=50%25").json()] == [b["id"]]   # % is literal
    assert [t["id"] for t in client.get(url + "?q=deposits").json()] == [b["id"], a["id"]]
    r = client.get(url + "?status=pending")
    assert r.status_code == 400 and r.json()["detail"] == "status must be new, open or closed"


def test_reply_close_and_reopen(portal, db):
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    r = _reply(client, org_id, t["id"], "Any news?")
    assert r.status_code == 201 and r.json()["status"] == "new" and len(r.json()["messages"]) == 2
    close = f"/api/orgs/{org_id}/investor/tickets/{t['id']}/close"
    r = client.post(close, headers=csrf(client))
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["closed_by"]) == ("closed", investor["id"])
    r = client.post(close, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "ticket is already closed"
    body = _reply(client, org_id, t["id"], "Still missing").json()
    assert (body["status"], body["closed_at"], body["closed_by"]) == ("open", None, None)
    thread = client.get(f"/api/orgs/{org_id}/investor/tickets/{t['id']}").json()
    assert [m["body"] for m in thread["messages"]] == [
        "My deposit is missing", "Any news?", "Still missing"]
    assert [e[1]["from_desk"] for e in _events(db, org_id, "ticket_replied")] == [False, False]
    (closed,) = _events(db, org_id, "ticket_closed")
    assert closed[1]["from_desk"] is False and "missing" not in str(closed[1])


def test_another_investors_ticket_is_a_404(portal, db, make_user, login_as):
    client, org_id, _investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    client.cookies.clear()
    login_as(client, other)
    base = f"/api/orgs/{org_id}/investor/tickets"
    for r in (client.get(f"{base}/{t['id']}"), _reply(client, org_id, t["id"]),
              client.post(f"{base}/{t['id']}/close", headers=csrf(client))):
        assert r.status_code == 404 and r.json()["detail"] == "Ticket not found"
    assert client.get(base).json() == []


def test_ten_tickets_an_hour(portal):
    client, org_id, _investor, subjects = portal
    for _ in range(10):
        assert _open(client, org_id, subjects["deposits"]).status_code == 201
    r = _open(client, org_id, subjects["deposits"])
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


async def _no_notify(*_args, **_kwargs):
    """Skips the admin emails the reply rate-limit test would otherwise send."""
    return None


def test_sixty_replies_an_hour(portal, monkeypatch):
    """Each reply emails every admin, so replies are limited too: 60 per
    investor per hour, counted apart from new tickets."""
    from api import portal_common
    monkeypatch.setattr(portal_common, "notify_admins", _no_notify)
    client, org_id, _investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    for _ in range(60):
        assert _reply(client, org_id, t["id"]).status_code == 201
    r = _reply(client, org_id, t["id"])
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"
    thread = client.get(f"/api/orgs/{org_id}/investor/tickets/{t['id']}").json()
    assert len(thread["messages"]) == 61
    assert _open(client, org_id, subjects["deposits"]).status_code == 201, "tickets count apart"


# ------------------------------------------------------------ desk side


def test_the_desk_answers_and_the_status_and_summary_follow(portal, db, login_as):
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    client.cookies.clear()
    login_as(client, ADMIN)

    def summary():
        return client.get(f"/api/orgs/{org_id}/requests/summary").json()

    assert summary()["tickets"] == 1 and summary()["total"] == 1
    queue = client.get(f"/api/orgs/{org_id}/tickets").json()
    assert [(q["id"], q["status"], q["email"], q["last_from_desk"], q["waiting_on_desk"])
            for q in queue] == [(t["id"], "new", "inv@example.com", False, True)]
    r = _reply(client, org_id, t["id"], "We are checking", desk=True)
    assert r.status_code == 201, r.text
    thread = r.json()
    assert thread["status"] == "open" and thread["last_from_desk"] is True
    assert thread["waiting_on_desk"] is False
    last = thread["messages"][-1]
    assert (last["from_desk"], last["author_name"], last["file_ids"]) == (True, "User", [])
    assert last["author_id"] == _user_id(db, "admin@example.com"), "the desk sees who answered"
    assert summary()["tickets"] == 0           # answered: waiting on the investor
    client.cookies.clear()
    login_as(client, investor)
    r = _reply(client, org_id, t["id"], "Thanks, any update?")
    assert r.status_code == 201 and r.json()["waiting_on_desk"] is True
    client.cookies.clear()
    login_as(client, ADMIN)
    assert summary()["tickets"] == 1           # the investor spoke last
    r = client.post(f"/api/orgs/{org_id}/tickets/{t['id']}/close", headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert r.json()["waiting_on_desk"] is False
    assert r.json()["closed_by"] == _user_id(db, "admin@example.com")
    assert summary()["tickets"] == 0
    r = _reply(client, org_id, t["id"], "late", desk=True)
    assert r.status_code == 409 and r.json()["detail"] == "ticket is closed"
    assert _reply(client, org_id, 999999, "x", desk=True).status_code == 404
    link = f"/org/{org_id}/invest/support?ticket={t['id']}"
    with psycopg.connect(db, autocommit=True) as conn:
        to_investor = conn.execute("SELECT topic, title, link FROM notifications WHERE user_id = %s "
                                   "ORDER BY id", (investor["id"],)).fetchall()
        to_desk = [r[0] for r in conn.execute(
            "SELECT title FROM notifications WHERE user_id = %s ORDER BY id",
            (_user_id(db, "admin@example.com"),)).fetchall()]
    assert to_investor == [("support", f"New reply on ticket #{t['id']}: Deposits", link),
                           ("support", f"Ticket #{t['id']} was closed", link)]
    assert to_desk == [f"New ticket #{t['id']}: Deposits", f"Reply on ticket #{t['id']}: Deposits"]
    replies = [(e[1]["from_desk"], e[1]["user_id"]) for e in _events(db, org_id, "ticket_replied")]
    assert replies == [(True, investor["id"]), (False, investor["id"])]


def test_the_investor_thread_never_names_desk_staff(portal, db, login_as):
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _reply(client, org_id, t["id"], "We are checking", desk=True).status_code == 201
    assert client.post(f"/api/orgs/{org_id}/tickets/{t['id']}/close",
                       headers=csrf(client)).status_code == 200
    client.cookies.clear()
    login_as(client, investor)
    thread = client.get(f"/api/orgs/{org_id}/investor/tickets/{t['id']}").json()
    assert thread["closed_by"] is None and thread["closed_at"] is not None
    mine, desk = thread["messages"]
    assert (mine["author_id"], mine["author_name"]) == (investor["id"], "Inv One")
    assert (desk["from_desk"], desk["author_id"], desk["author_name"]) == (True, None, None)
    reopened = _reply(client, org_id, t["id"], "Still missing").json()
    assert reopened["messages"][1]["author_name"] is None
    # The investor's own close keeps closed_by: it names nobody else.
    r = client.post(f"/api/orgs/{org_id}/investor/tickets/{t['id']}/close", headers=csrf(client))
    assert r.json()["closed_by"] == investor["id"]


def test_the_desk_queue_filters_and_shows_the_thread(portal, db, login_as):
    client, org_id, investor, subjects = portal
    image = seed_file(db, org_id, investor["id"], purpose="ticket_attachment")
    a = _open(client, org_id, subjects["deposits"], body="Card payment", file_ids=[image]).json()
    b = _open(client, org_id, subjects["deposits"], body="Wire transfer").json()
    client.cookies.clear()
    login_as(client, ADMIN)
    url = f"/api/orgs/{org_id}/tickets"
    assert [t["id"] for t in client.get(url).json()] == [b["id"], a["id"]]
    assert [t["id"] for t in client.get(url + "?q=wire").json()] == [b["id"]]
    assert client.get(url + "?status=open").json() == []
    thread = client.get(f"{url}/{a['id']}").json()
    assert thread["messages"][0]["file_ids"] == [image] and thread["email"] == "inv@example.com"
    assert client.get(f"{url}/999999").status_code == 404


def test_the_investor_list_masks_a_desk_close_too(portal, db, login_as):
    """Controller ruling: load_thread already masks a desk close's identity;
    the investor's ticket LIST must do the same, or a desk close leaks the
    desk user's id through the one route that forgot to mask it. The desk's
    own list keeps the real id."""
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    client.cookies.clear()
    login_as(client, ADMIN)
    r = client.post(f"/api/orgs/{org_id}/tickets/{t['id']}/close", headers=csrf(client))
    assert r.status_code == 200
    admin_id = _user_id(db, "admin@example.com")
    desk_queue = client.get(f"/api/orgs/{org_id}/tickets").json()
    assert desk_queue[0]["closed_by"] == admin_id
    client.cookies.clear()
    login_as(client, investor)
    mine = client.get(f"/api/orgs/{org_id}/investor/tickets").json()
    assert mine[0]["id"] == t["id"] and mine[0]["closed_by"] is None
