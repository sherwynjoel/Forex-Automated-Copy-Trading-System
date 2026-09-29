# api/tests/test_mpin_core.py
"""mpin_core: the MPIN check shared by the login step and the money
step-up routes. Bodies and the lock are the login route's, unchanged;
require_mpin adds the shape gate in front of it."""
import json

import psycopg
import pytest

from api.mpin_core import (MPIN_LOCK_MINUTES, MPIN_MAX_ATTEMPTS, MPIN_RE, audit_auth,
                           check_mpin, require_mpin)


def _body(resp):
    return json.loads(resp.body)


def _attempts(db, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        (attempts,) = conn.execute(
            "SELECT mpin_failed_attempts FROM users WHERE id = %s", (user_id,)).fetchone()
    return attempts


def test_constants_are_the_login_ones():
    assert MPIN_MAX_ATTEMPTS == 5 and MPIN_LOCK_MINUTES == 15
    assert MPIN_RE.fullmatch("123456")
    assert not MPIN_RE.fullmatch("12345") and not MPIN_RE.fullmatch("12345a")


@pytest.mark.parametrize("bad", [None, "", "12345", "1234567", "12345a", 123456, True, ["123456"]])
def test_require_mpin_refuses_anything_but_six_digits_without_spending_a_try(db, make_user, bad):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        resp = require_mpin(conn, user["id"], bad)
    assert resp is not None and resp.status_code == 400
    assert _body(resp) == {"detail": "MPIN must be exactly 6 digits"}
    assert _attempts(db, user["id"]) == 0


def test_require_mpin_accepts_the_right_mpin_and_clears_the_counter(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_failed_attempts = 2 WHERE id = %s", (user["id"],))
        assert require_mpin(conn, user["id"], "123456") is None
    assert _attempts(db, user["id"]) == 0


def test_require_mpin_counts_wrong_tries_then_locks(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        for left in (4, 3, 2, 1):
            resp = require_mpin(conn, user["id"], "000000")
            assert resp.status_code == 401
            assert _body(resp) == {"detail": "Invalid MPIN", "attempts_left": left}
        resp = require_mpin(conn, user["id"], "000000")
        assert resp.status_code == 423
        body = _body(resp)
        assert body["detail"] == "MPIN locked" and body["locked_until"]
        # Even the right MPIN is refused while locked.
        resp = require_mpin(conn, user["id"], "123456")
        assert resp.status_code == 423
        (mins,) = conn.execute(
            "SELECT EXTRACT(EPOCH FROM (mpin_locked_until - now())) / 60 FROM users WHERE id = %s",
            (user["id"],)).fetchone()
    assert 14 < float(mins) <= 15


def test_require_mpin_without_an_mpin_is_409(db, make_user):
    user = make_user(mpin=None)
    with psycopg.connect(db, autocommit=True) as conn:
        resp = require_mpin(conn, user["id"], "123456")
    assert resp.status_code == 409 and _body(resp) == {"detail": "MPIN not set"}


def test_the_login_route_uses_the_same_check():
    from api.routes import mpin as routes
    assert routes.check_mpin is check_mpin


def test_audit_auth_writes_an_org_less_auth_event(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        audit_auth(conn, user["id"], "mpin_locked")
        rows = conn.execute(
            "SELECT org_id, account_id, category, severity, payload FROM events").fetchall()
    assert rows == [(None, None, "auth", "info", {"action": "mpin_locked", "user_id": user["id"]})]
