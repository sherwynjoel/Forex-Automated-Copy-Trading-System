"""The MPIN check, shared by the login step (routes/mpin.py) and every
money action that re-confirms with the MPIN (the client portal's step-up
routes: withdrawal request, transfer request, payout destination add,
admin adjustment).

One lock for both: five wrong guesses anywhere lock the MPIN everywhere
for fifteen minutes. The lock lives on the users row so it survives
restarts and is shared across workers; every path does exactly one argon2
verification whatever the outcome, so timing does not leak state.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb

from .auth import _DUMMY_HASH, verify_password

logger = logging.getLogger(__name__)

MPIN_RE = re.compile(r"^[0-9]{6}$")
MPIN_MAX_ATTEMPTS = 5
MPIN_LOCK_MINUTES = 15


def audit_auth(conn: psycopg.Connection, user_id: int, action: str) -> None:
    """Account-level security events carry no org; they reach the operator
    log, not an org's feed. Best-effort like the other audit writers."""
    if action in ("mpin_locked", "mpin_reset"):
        logger.warning("mpin %s user_id=%s", action, user_id)
    else:
        logger.info("mpin %s user_id=%s", action, user_id)
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, payload) "
            "VALUES (NULL, NULL, 'auth', 'info', %s)",
            (Jsonb({"action": action, "user_id": user_id}),))
    except Exception:
        logger.exception("failed to write mpin audit event %s", action)


def _locked_response(until: datetime) -> JSONResponse:
    return JSONResponse(status_code=423,
                        content={"detail": "MPIN locked", "locked_until": until.isoformat()})


def lock_state(conn: psycopg.Connection, user_id: int) -> tuple[Optional[str], int, Optional[datetime]]:
    """(mpin_hash, failed_attempts, locked_until) for a user; 401 when the
    session names a user that no longer exists."""
    row = conn.execute(
        "SELECT mpin_hash, mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
        (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return row[0], row[1], row[2]


def _lock(conn: psycopg.Connection, user_id: int, now: datetime) -> datetime:
    until = now + timedelta(minutes=MPIN_LOCK_MINUTES)
    conn.execute("UPDATE users SET mpin_failed_attempts = 0, mpin_locked_until = %s "
                 "WHERE id = %s", (until, user_id))
    audit_auth(conn, user_id, "mpin_locked")
    return until


def check_mpin(conn: psycopg.Connection, user_id: int, mpin: str) -> Optional[Response]:
    """Reserve a try, then verify. One argon2 verify on every path. The
    reservation is a single UPDATE guarded by the lock and the cap, so a
    burst of concurrent guesses gets at most MPIN_MAX_ATTEMPTS verifies per
    window. Returns None when the MPIN is right (counter cleared), otherwise
    the error response: 409 no MPIN, 423 locked, 401 with attempts_left."""
    now = datetime.now(timezone.utc)
    reserved = conn.execute(
        "UPDATE users SET mpin_failed_attempts = mpin_failed_attempts + 1 "
        "WHERE id = %s AND mpin_hash IS NOT NULL "
        "  AND (mpin_locked_until IS NULL OR mpin_locked_until <= now()) "
        "  AND mpin_failed_attempts < %s "
        "RETURNING mpin_failed_attempts, mpin_hash",
        (user_id, MPIN_MAX_ATTEMPTS)).fetchone()
    if reserved is None:
        mpin_hash, _, locked_until = lock_state(conn, user_id)
        verify_password(_DUMMY_HASH, mpin)
        if mpin_hash is None:
            return JSONResponse(status_code=409, content={"detail": "MPIN not set"})
        # Judge the lock by the database clock, the one the reservation used;
        # a skewed api clock must not extend a lock the database still holds.
        (db_now,) = conn.execute("SELECT now()").fetchone()
        if locked_until is None or locked_until <= db_now:
            # The cap was reached before a lock was written (a burst of
            # concurrent tries); start the window now.
            locked_until = _lock(conn, user_id, db_now)
        return _locked_response(locked_until)
    attempts, mpin_hash = reserved
    if verify_password(mpin_hash, mpin):
        conn.execute("UPDATE users SET mpin_failed_attempts = 0, mpin_locked_until = NULL "
                     "WHERE id = %s", (user_id,))
        return None
    if attempts >= MPIN_MAX_ATTEMPTS:
        return _locked_response(_lock(conn, user_id, now))
    return JSONResponse(status_code=401,
                        content={"detail": "Invalid MPIN",
                                 "attempts_left": MPIN_MAX_ATTEMPTS - attempts})


def require_mpin(conn: psycopg.Connection, user_id: int, mpin: object) -> Optional[Response]:
    """The step-up gate for a money route: the body's `mpin` field must be
    a six-digit string (400 otherwise, and no try is spent), then it goes
    through check_mpin. Routes call this first and return the Response
    when there is one."""
    if not isinstance(mpin, str) or not MPIN_RE.fullmatch(mpin):
        return JSONResponse(status_code=400, content={"detail": "MPIN must be exactly 6 digits"})
    return check_mpin(conn, user_id, mpin)
