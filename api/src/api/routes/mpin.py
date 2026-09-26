"""MPIN: the six-digit second factor every user passes after email+password.

Set once (first login), verified on every later login, reset by proving the
password, changed from Account security. The lock lives on the users row so
it survives restarts and is shared across workers; the verify and reset
paths do exactly one argon2 verification whatever the outcome, so timing
does not leak state.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import (LoginRateLimiter, SessionInfo, _DUMMY_HASH, _is_proxy_address,
                    _issue_session, get_client_ip, hash_password, require_half_session,
                    require_user, verify_password)
from ..config import ApiConfig
from ..db import get_conn

logger = logging.getLogger(__name__)

MPIN_RE = re.compile(r"^[0-9]{6}$")
MPIN_MAX_ATTEMPTS = 5
MPIN_LOCK_MINUTES = 15


class SetRequest(BaseModel):
    mpin: str
    mpin_confirm: str


class VerifyRequest(BaseModel):
    mpin: str


class ResetRequest(BaseModel):
    password: str
    mpin: str
    mpin_confirm: str


class ChangeRequest(BaseModel):
    current_mpin: str
    mpin: str
    mpin_confirm: str


def _validate_pair(mpin: str, confirm: str) -> None:
    if not MPIN_RE.fullmatch(mpin):
        raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
    if mpin != confirm:
        raise HTTPException(status_code=400, detail="MPINs do not match")


def _audit(conn: psycopg.Connection, user_id: int, action: str) -> None:
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


def _lock_state(conn: psycopg.Connection, user_id: int) -> tuple[Optional[str], int, Optional[datetime]]:
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
    _audit(conn, user_id, "mpin_locked")
    return until


def _check_mpin(conn: psycopg.Connection, user_id: int, mpin: str) -> Optional[Response]:
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
        mpin_hash, _, locked_until = _lock_state(conn, user_id)
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


def _store(conn: psycopg.Connection, user_id: int, mpin: str) -> None:
    conn.execute(
        "UPDATE users SET mpin_hash = %s, mpin_set_at = now(), mpin_failed_attempts = 0, "
        "mpin_locked_until = NULL WHERE id = %s",
        (hash_password(mpin), user_id))


def create_mpin_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    router = APIRouter(tags=["mpin"])

    @router.post("/api/mpin/set", status_code=204)
    async def set_mpin(body: SetRequest,
                       info: SessionInfo = Depends(require_half_session),
                       cfg: ApiConfig = Depends(ApiConfig.from_env),
                       conn: psycopg.Connection = Depends(get_conn)):
        _validate_pair(body.mpin, body.mpin_confirm)
        mpin_hash, _, _ = _lock_state(conn, info.user_id)
        if mpin_hash is not None:
            raise HTTPException(status_code=409, detail="MPIN already set")
        _store(conn, info.user_id, body.mpin)
        _audit(conn, info.user_id, "mpin_set")
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, info.session_version, pin=True)
        return response

    @router.post("/api/mpin/verify", status_code=204)
    async def verify_mpin(body: VerifyRequest,
                          info: SessionInfo = Depends(require_half_session),
                          cfg: ApiConfig = Depends(ApiConfig.from_env),
                          conn: psycopg.Connection = Depends(get_conn)):
        if not MPIN_RE.fullmatch(body.mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = _check_mpin(conn, info.user_id, body.mpin)
        if failure is not None:
            return failure
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, info.session_version, pin=True)
        return response

    @router.post("/api/mpin/reset", status_code=204)
    async def reset_mpin(body: ResetRequest, request: Request,
                         info: SessionInfo = Depends(require_half_session),
                         cfg: ApiConfig = Depends(ApiConfig.from_env),
                         conn: psycopg.Connection = Depends(get_conn)):
        """Forgot MPIN: prove the password, get a new MPIN. Works while
        locked -- that is its purpose -- and shares login's per-credential
        rate-limit bucket so it cannot be used to guess the password."""
        _validate_pair(body.mpin, body.mpin_confirm)
        row = conn.execute("SELECT email, password_hash FROM users WHERE id = %s",
                           (info.user_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        email, password_hash = row[0].lower(), row[1]
        client_ip = get_client_ip(request, trust_proxy=cfg.trust_proxy)
        if rate_limiter.is_limited(f"login:{email}:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")
        if not _is_proxy_address(client_ip) and rate_limiter.is_limited(
                f"login-ip:{client_ip}", max_attempts=20):
            raise HTTPException(status_code=429, detail="Too many requests")
        if not verify_password(password_hash, body.password):
            raise HTTPException(status_code=401, detail="Invalid password")
        _store(conn, info.user_id, body.mpin)
        # A reset signs out every other session (whoever forgot, or stole,
        # the MPIN); this browser is handed a freshly versioned cookie.
        (new_sv,) = conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = %s "
            "RETURNING session_version", (info.user_id,)).fetchone()
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, new_sv, pin=True)
        from ..ws import broadcaster
        await broadcaster.close_for_user(info.user_id)
        _audit(conn, info.user_id, "mpin_reset")
        return response

    @router.post("/api/me/mpin", status_code=204)
    async def change_mpin(body: ChangeRequest,
                          user_id: int = Depends(require_user),
                          cfg: ApiConfig = Depends(ApiConfig.from_env),
                          conn: psycopg.Connection = Depends(get_conn)):
        _validate_pair(body.mpin, body.mpin_confirm)
        if not MPIN_RE.fullmatch(body.current_mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = _check_mpin(conn, user_id, body.current_mpin)
        if failure is not None:
            return failure
        _store(conn, user_id, body.mpin)
        # Like a password change: every other session is signed out, this
        # one keeps working on a freshly versioned cookie.
        (new_sv,) = conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = %s "
            "RETURNING session_version", (user_id,)).fetchone()
        response = Response(status_code=204)
        _issue_session(response, cfg, user_id, new_sv, pin=True)
        from ..ws import broadcaster
        await broadcaster.close_for_user(user_id)
        _audit(conn, user_id, "mpin_changed")
        return response

    return router
