# api/tests/test_login_events.py
"""Sign-in history: /api/login writes password_ok (or failed for a known
email -- never anything for an unknown one), /api/mpin/verify writes mpin_ok,
rows older than 180 days go on the next write, and each user reads only
their own. Plus the password change's MPIN step-up."""
import psycopg

from portal_helpers import csrf, member


def _events(db):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT user_id, ip, user_agent, outcome FROM login_events ORDER BY id").fetchall()


def test_a_full_sign_in_writes_password_ok_then_mpin_ok(app_client, make_user, login_as, db):
    user = make_user(email="a@example.com")
    login_as(app_client, user)
    assert _events(db) == [(user["id"], "testclient", "testclient", "password_ok"),
                           (user["id"], "testclient", "testclient", "mpin_ok")]


def test_a_wrong_password_is_recorded_but_an_unknown_email_is_not(app_client, make_user, db):
    user = make_user(email="a@example.com")
    r = app_client.post("/api/login", json={"email": "a@example.com", "password": "wrong-one!"})
    assert r.status_code == 401
    r = app_client.post("/api/login", json={"email": "nobody@example.com", "password": "x-y-z-1"})
    assert r.status_code == 401
    assert [(e[0], e[3]) for e in _events(db)] == [(user["id"], "failed")]


def test_a_wrong_mpin_writes_nothing(app_client, make_user, db):
    make_user(email="a@example.com")
    assert app_client.post("/api/login", json={
        "email": "a@example.com", "password": "a-solid-password"}).status_code == 204
    r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=csrf(app_client))
    assert r.status_code == 401
    assert [e[3] for e in _events(db)] == ["password_ok"]


def test_rows_older_than_180_days_go_on_the_next_write(app_client, make_user, login_as, db):
    user = make_user(email="a@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        for days, ip in ((181, "10.0.0.181"), (179, "10.0.0.179")):
            conn.execute(
                "INSERT INTO login_events (user_id, ip, outcome, created_at) "
                "VALUES (%s, %s, 'password_ok', now() - make_interval(days => %s))",
                (user["id"], ip, days))
    login_as(app_client, user)
    ips = [e[1] for e in _events(db)]
    assert "10.0.0.181" not in ips and "10.0.0.179" in ips


def test_me_sign_ins_lists_only_your_own_newest_first(app_client, make_user, login_as):
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    login_as(app_client, b)
    app_client.cookies.clear()
    login_as(app_client, a)
    rows = app_client.get("/api/me/sign-ins").json()
    assert [r["outcome"] for r in rows] == ["mpin_ok", "password_ok"]
    assert set(rows[0]) == {"id", "ip", "user_agent", "outcome", "created_at"}
    assert rows[0]["ip"] == "testclient" and rows[0]["user_agent"] == "testclient"
    limited = app_client.get("/api/me/sign-ins?limit=1").json()
    assert [r["outcome"] for r in limited] == ["mpin_ok"]


def test_me_sign_ins_needs_the_mpin(app_client, make_user):
    make_user(email="a@example.com")
    app_client.post("/api/login", json={"email": "a@example.com", "password": "a-solid-password"})
    r = app_client.get("/api/me/sign-ins")
    assert r.status_code == 401 and r.json()["detail"] == "MPIN required"


def test_a_password_change_needs_the_mpin(app_client, make_user, login_as):
    user = make_user(email="p@example.com")
    login_as(app_client, user)
    body = {"current_password": "a-solid-password", "new_password": "brand-new-secret"}
    r = app_client.post("/api/me/password", json=body, headers=csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = app_client.post("/api/me/password", json={**body, "mpin": "000000"},
                        headers=csrf(app_client))
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid MPIN", "attempts_left": 4}
    r = app_client.post("/api/me/password", json={**body, "mpin": "123456"},
                        headers=csrf(app_client))
    assert r.status_code == 204
    app_client.cookies.clear()
    assert app_client.post("/api/login", json={
        "email": "p@example.com", "password": "brand-new-secret"}).status_code == 204
