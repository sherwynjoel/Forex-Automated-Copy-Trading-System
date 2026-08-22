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


# ---------- session revocation (migration 010) ----------

def _csrf_headers(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_password_change_requires_the_current_password(app_client, make_user):
    make_user(email="rot@example.com", password="a-solid-password")
    app_client.post("/api/login", json={
        "email": "rot@example.com", "password": "a-solid-password"})

    r = app_client.post("/api/me/password", json={
        "current_password": "not-the-password", "new_password": "another-good-one"},
        headers=_csrf_headers(app_client))
    assert r.status_code == 403
    # The old password still works: nothing was rotated.
    assert app_client.post("/api/login", json={
        "email": "rot@example.com", "password": "a-solid-password"}).status_code == 204


def test_password_change_rotates_and_disowns_other_sessions(app_client, make_user):
    make_user(email="rot2@example.com", password="a-solid-password")
    app_client.post("/api/login", json={
        "email": "rot2@example.com", "password": "a-solid-password"})
    # A second device holding its own cookie for the same account.
    stolen = app_client.cookies.get("session")

    r = app_client.post("/api/me/password", json={
        "current_password": "a-solid-password", "new_password": "brand-new-secret"},
        headers=_csrf_headers(app_client))
    assert r.status_code == 204

    # This browser keeps working on its re-issued cookie...
    assert app_client.get("/api/me").status_code == 200
    # ...while the cookie captured before the change is dead.
    app_client.cookies.set("session", stolen)
    assert app_client.get("/api/me").status_code == 401

    # The new password is what logs in now.
    app_client.cookies.clear()
    assert app_client.post("/api/login", json={
        "email": "rot2@example.com", "password": "a-solid-password"}).status_code == 401
    assert app_client.post("/api/login", json={
        "email": "rot2@example.com", "password": "brand-new-secret"}).status_code == 204


def test_password_change_rejects_short_or_unchanged(app_client, make_user):
    make_user(email="rot3@example.com", password="a-solid-password")
    app_client.post("/api/login", json={
        "email": "rot3@example.com", "password": "a-solid-password"})

    assert app_client.post("/api/me/password", json={
        "current_password": "a-solid-password", "new_password": "short"},
        headers=_csrf_headers(app_client)).status_code == 400
    assert app_client.post("/api/me/password", json={
        "current_password": "a-solid-password", "new_password": "a-solid-password"},
        headers=_csrf_headers(app_client)).status_code == 400


def test_logout_everywhere_kills_the_calling_session_too(app_client, make_user):
    make_user(email="rot4@example.com", password="a-solid-password")
    app_client.post("/api/login", json={
        "email": "rot4@example.com", "password": "a-solid-password"})
    held = app_client.cookies.get("session")

    assert app_client.post(
        "/api/me/logout-all", headers=_csrf_headers(app_client)).status_code == 204

    app_client.cookies.set("session", held)
    assert app_client.get("/api/me").status_code == 401


def test_registration_can_be_disabled(app_client, monkeypatch):
    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    r = app_client.post("/api/register", json={
        "email": "nope@example.com", "password": "long-enough-pw", "display_name": "N"})
    assert r.status_code == 403
    assert "invite" in r.json()["detail"].lower()


def test_an_invite_token_authorizes_signup_while_registration_is_closed(
        app_client, db, monkeypatch):
    """Closing self-service signup must not close ONBOARDING: redeeming an
    invite requires an account, so the invite itself has to authorize
    creating one."""
    import hashlib
    import psycopg

    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    token = "invite-token-for-a-new-colleague"
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Acme') RETURNING id").fetchone()
        (inviter,) = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('inviter@example.com', 'x', 'Inviter') RETURNING id").fetchone()
        conn.execute(
            "INSERT INTO org_invites (org_id, role, token_hash, expires_at, created_by) "
            "VALUES (%s, 'viewer', %s, now() + interval '7 days', %s)",
            (org_id, hashlib.sha256(token.encode()).hexdigest(), inviter),
        )

    # Without a token: closed.
    assert app_client.post("/api/register", json={
        "email": "stranger@example.com", "password": "long-enough-pw",
        "display_name": "S"}).status_code == 403

    # With the invite: allowed, and signed in.
    r = app_client.post("/api/register", json={
        "email": "colleague@example.com", "password": "long-enough-pw",
        "display_name": "C", "invite_token": token})
    assert r.status_code == 204, r.text
    assert app_client.get("/api/me").json()["user"]["email"] == "colleague@example.com"


def test_a_bogus_or_expired_invite_does_not_open_registration(
        app_client, db, monkeypatch):
    import hashlib
    import psycopg

    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Acme2') RETURNING id").fetchone()
        (inviter,) = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('inviter2@example.com', 'x', 'Inviter') RETURNING id").fetchone()
        conn.execute(
            "INSERT INTO org_invites (org_id, role, token_hash, expires_at, created_by) "
            "VALUES (%s, 'viewer', %s, now() - interval '1 day', %s)",
            (org_id, hashlib.sha256(b"expired-token").hexdigest(), inviter),
        )

    for token in ("never-issued", "expired-token"):
        assert app_client.post("/api/register", json={
            "email": f"x-{token}@example.com", "password": "long-enough-pw",
            "display_name": "X", "invite_token": token}).status_code == 403


def test_the_per_source_login_limit_is_skipped_for_the_proxy_address():
    """Behind Caddy every request appears to come from 127.0.0.1. Applying
    a shared per-source bucket there would let one attacker lock out every
    user, so the proxy's own address must be exempt -- while a real client
    address is still bucketed."""
    from api.auth import _is_proxy_address

    assert _is_proxy_address("127.0.0.1")
    assert _is_proxy_address("::1")
    assert _is_proxy_address("unknown")  # request.client absent
    assert not _is_proxy_address("203.0.113.7")


def test_a_real_source_address_is_still_rate_limited(app_client, make_user):
    """The exemption is narrow: a genuine client IP (which is what
    TRUST_PROXY=true yields in production) still gets a per-source cap."""
    from api.auth import LoginRateLimiter

    limiter = LoginRateLimiter()
    ip = "203.0.113.7"
    assert not any(
        limiter.is_limited(f"login-ip:{ip}", max_attempts=20) for _ in range(20))
    assert limiter.is_limited(f"login-ip:{ip}", max_attempts=20)
