"""MPIN: the six-digit second factor every user passes after email+password.

Set once (first login), verified on every later login, reset by proving the
password, changed from Account security. The check itself (reservation,
5-try / 15-minute lock, constant-time verify) lives in mpin_core so the
client portal's money routes can re-confirm with the same MPIN and share
the same lock.
"""
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..auth import (LoginRateLimiter, SessionInfo, _is_proxy_address, _issue_session,
                    get_client_ip, hash_password, require_half_session, require_user,
                    verify_password)
from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import MPIN_RE, audit_auth, check_mpin, lock_state


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
        mpin_hash, _, _ = lock_state(conn, info.user_id)
        if mpin_hash is not None:
            raise HTTPException(status_code=409, detail="MPIN already set")
        _store(conn, info.user_id, body.mpin)
        audit_auth(conn, info.user_id, "mpin_set")
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
        failure = check_mpin(conn, info.user_id, body.mpin)
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
        audit_auth(conn, info.user_id, "mpin_reset")
        return response

    @router.post("/api/me/mpin", status_code=204)
    async def change_mpin(body: ChangeRequest,
                          user_id: int = Depends(require_user),
                          cfg: ApiConfig = Depends(ApiConfig.from_env),
                          conn: psycopg.Connection = Depends(get_conn)):
        _validate_pair(body.mpin, body.mpin_confirm)
        if not MPIN_RE.fullmatch(body.current_mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = check_mpin(conn, user_id, body.current_mpin)
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
        audit_auth(conn, user_id, "mpin_changed")
        return response

    return router
