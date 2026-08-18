"""Auth: register, login, sessions, /api/me, bootstrap."""


def test_register_sets_session_and_csrf_cookies(app_client):
    r = app_client.post("/api/register", json={
        "email": "ada@example.com", "password": "correct-horse", "display_name": "Ada"})
    assert r.status_code == 204
    assert "session" in app_client.cookies
    assert "csrf" in app_client.cookies
    me = app_client.get("/api/me").json()
    assert me["user"]["email"] == "ada@example.com"
    assert me["user"]["display_name"] == "Ada"
    assert me["orgs"] == []


def test_register_rejects_short_password(app_client):
    r = app_client.post("/api/register", json={
        "email": "b@example.com", "password": "short", "display_name": "B"})
    assert r.status_code == 400
    assert "10" in r.json()["detail"]


def test_register_duplicate_email_case_insensitive(app_client):
    body = {"email": "dup@example.com", "password": "long-enough-pw", "display_name": "D"}
    assert app_client.post("/api/register", json=body).status_code == 204
    body["email"] = "DUP@example.com"
    r = app_client.post("/api/register", json=body)
    assert r.status_code == 409


def test_login_with_email_and_password(app_client, make_user):
    user = make_user(email="carl@example.com", password="a-solid-password")
    r = app_client.post("/api/login", json={
        "email": "carl@example.com", "password": "a-solid-password"})
    assert r.status_code == 204
    assert app_client.get("/api/me").json()["user"]["id"] == user["id"]


def test_login_wrong_password_is_401(app_client, make_user):
    make_user(email="carl@example.com", password="a-solid-password")
    r = app_client.post("/api/login", json={
        "email": "carl@example.com", "password": "wrong-password!"})
    assert r.status_code == 401


def test_login_unknown_email_is_401_not_distinguishable(app_client):
    r = app_client.post("/api/login", json={
        "email": "ghost@example.com", "password": "whatever-long"})
    assert r.status_code == 401


def test_me_unauthenticated_is_401(app_client):
    assert app_client.get("/api/me").status_code == 401


def test_logout_clears_session(app_client, make_user):
    make_user(email="e@example.com", password="a-solid-password")
    app_client.post("/api/login", json={"email": "e@example.com", "password": "a-solid-password"})
    csrf = app_client.cookies.get("csrf")
    r = app_client.post("/api/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert app_client.get("/api/me").status_code == 401


def test_login_rate_limited_per_email_ip(app_client, make_user):
    make_user(email="rl@example.com", password="a-solid-password")
    for _ in range(5):
        app_client.post("/api/login", json={
            "email": "rl@example.com", "password": "wrong-password!"})
    r = app_client.post("/api/login", json={
        "email": "rl@example.com", "password": "a-solid-password"})
    assert r.status_code == 429


def test_bootstrap_user_claims_default_org(db):
    import psycopg
    from api.auth import ensure_bootstrap_user

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (name) VALUES ('Default')")
    ensure_bootstrap_user(db, "root@example.com", "bootstrap-password")
    ensure_bootstrap_user(db, "root@example.com", "bootstrap-password")  # idempotent
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            """SELECT u.email, m.role, o.name FROM org_memberships m
               JOIN users u ON u.id = m.user_id JOIN orgs o ON o.id = m.org_id"""
        ).fetchall()
    assert rows == [("root@example.com", "owner", "Default")]


def test_bootstrap_user_without_default_org_creates_only_user(db):
    import psycopg
    from api.auth import ensure_bootstrap_user

    ensure_bootstrap_user(db, "root@example.com", "bootstrap-password")
    with psycopg.connect(db, autocommit=True) as conn:
        (users,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        (memberships,) = conn.execute("SELECT COUNT(*) FROM org_memberships").fetchone()
    assert users == 1 and memberships == 0
