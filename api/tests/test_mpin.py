"""MPIN routes: set, verify (with lock), reset via password, change."""
import psycopg


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _half_login(client, user):
    r = client.post("/api/login", json={"email": user["email"], "password": user["password"]})
    assert r.status_code == 204


def _payload(client):
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer("test-secret", salt="session").loads(client.cookies["session"])


def _events(db, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT payload FROM events WHERE category = 'auth' AND payload->>'action' = %s",
            (action,)).fetchall()


# ---------- set ----------

def test_set_needs_six_digits_that_match(app_client, make_user):
    user = make_user(mpin=None)
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/set", json={"mpin": "12345", "mpin_confirm": "12345"},
                        headers=_csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = app_client.post("/api/mpin/set", json={"mpin": "12345a", "mpin_confirm": "12345a"},
                        headers=_csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = app_client.post("/api/mpin/set", json={"mpin": "123456", "mpin_confirm": "654321"},
                        headers=_csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPINs do not match"


def test_set_stores_a_hash_issues_a_full_session_and_audits(app_client, make_user, db):
    user = make_user(mpin=None)
    _half_login(app_client, user)
    assert _payload(app_client)["pin"] is False
    r = app_client.post("/api/mpin/set", json={"mpin": "246810", "mpin_confirm": "246810"},
                        headers=_csrf(app_client))
    assert r.status_code == 204
    assert _payload(app_client)["pin"] is True
    with psycopg.connect(db, autocommit=True) as conn:
        h, set_at = conn.execute(
            "SELECT mpin_hash, mpin_set_at FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert h and h != "246810" and set_at is not None
    assert len(_events(db, "mpin_set")) == 1


def test_set_is_refused_once_an_mpin_exists(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/set", json={"mpin": "111111", "mpin_confirm": "111111"},
                        headers=_csrf(app_client))
    assert r.status_code == 409 and r.json()["detail"] == "MPIN already set"


# ---------- verify ----------

def test_verify_right_mpin_issues_a_full_session(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 204
    assert _payload(app_client)["pin"] is True


def test_verify_counts_wrong_tries_then_locks_for_fifteen_minutes(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    for left in (4, 3, 2, 1):
        r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=_csrf(app_client))
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid MPIN", "attempts_left": left}
    r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=_csrf(app_client))
    assert r.status_code == 423
    body = r.json()
    assert body["detail"] == "MPIN locked" and body["locked_until"]
    # Even the right MPIN is refused while locked.
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 423
    assert _payload(app_client)["pin"] is False
    with psycopg.connect(db, autocommit=True) as conn:
        attempts, until = conn.execute(
            "SELECT mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
            (user["id"],)).fetchone()
        assert attempts == 0 and until is not None
        (mins,) = conn.execute(
            "SELECT EXTRACT(EPOCH FROM (mpin_locked_until - now())) / 60 FROM users WHERE id = %s",
            (user["id"],)).fetchone()
    assert 14 < float(mins) <= 15
    assert len(_events(db, "mpin_locked")) == 1


def test_verify_works_again_once_the_lock_has_passed(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_locked_until = now() - interval '1 second' WHERE id = %s",
                     (user["id"],))
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 204


def test_a_right_mpin_resets_the_counter(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=_csrf(app_client))
    assert r.status_code == 401
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 204
    with psycopg.connect(db, autocommit=True) as conn:
        (attempts,) = conn.execute(
            "SELECT mpin_failed_attempts FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert attempts == 0


def test_verify_without_an_mpin_is_409(app_client, make_user):
    user = make_user(mpin=None)
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 409 and r.json()["detail"] == "MPIN not set"


# ---------- reset (forgot) ----------

def test_reset_needs_the_password_and_clears_the_lock(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_locked_until = now() + interval '10 minutes' WHERE id = %s",
                     (user["id"],))
    r = app_client.post("/api/mpin/reset", json={
        "password": "wrong-password!", "mpin": "999999", "mpin_confirm": "999999"},
        headers=_csrf(app_client))
    assert r.status_code == 401 and r.json()["detail"] == "Invalid password"
    r = app_client.post("/api/mpin/reset", json={
        "password": user["password"], "mpin": "999999", "mpin_confirm": "999999"},
        headers=_csrf(app_client))
    assert r.status_code == 204
    assert _payload(app_client)["pin"] is True
    with psycopg.connect(db, autocommit=True) as conn:
        attempts, until = conn.execute(
            "SELECT mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
            (user["id"],)).fetchone()
    assert attempts == 0 and until is None
    assert len(_events(db, "mpin_reset")) == 1
    # The new MPIN is the one that works now.
    app_client.cookies.clear()
    _half_login(app_client, user)
    assert app_client.post("/api/mpin/verify", json={"mpin": "123456"},
                           headers=_csrf(app_client)).status_code == 401
    assert app_client.post("/api/mpin/verify", json={"mpin": "999999"},
                           headers=_csrf(app_client)).status_code == 204


def test_reset_is_rate_limited_like_login(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    for _ in range(5):
        app_client.post("/api/mpin/reset", json={
            "password": "wrong-password!", "mpin": "999999", "mpin_confirm": "999999"},
            headers=_csrf(app_client))
    r = app_client.post("/api/mpin/reset", json={
        "password": user["password"], "mpin": "999999", "mpin_confirm": "999999"},
        headers=_csrf(app_client))
    assert r.status_code == 429


# ---------- change (signed in) ----------

def test_change_refuses_a_half_session(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "123456", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 401  # half session cannot reach it


def test_change_needs_the_current_mpin_and_a_full_session(app_client, make_user, login_as, db):
    user = make_user()
    login_as(app_client, user)
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "000000", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 401 and r.json()["detail"] == "Invalid MPIN"
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "123456", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 204
    assert len(_events(db, "mpin_changed")) == 1
    app_client.cookies.clear()
    _half_login(app_client, user)
    assert app_client.post("/api/mpin/verify", json={"mpin": "222222"},
                           headers=_csrf(app_client)).status_code == 204


def test_change_is_refused_while_locked(app_client, make_user, login_as, db):
    user = make_user()
    login_as(app_client, user)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_locked_until = now() + interval '10 minutes' WHERE id = %s",
                     (user["id"],))
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "123456", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 423
