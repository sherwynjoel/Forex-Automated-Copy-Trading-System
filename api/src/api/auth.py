"""Authentication and authorization utilities."""
import os
import secrets
import time
from collections import defaultdict
from typing import Optional

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from .config import ApiConfig
from .db import get_conn

# Password hashing
_hasher = PasswordHasher()

# Verified against for unknown emails so login timing does not reveal
# whether an email exists (one argon2 verify on both paths).
_DUMMY_HASH = _hasher.hash("timing-equalizer")


def hash_password(password: str) -> str:
    """Hash a password using argon2."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against its hash."""
    try:
        _hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False


def get_session_serializer(cfg: ApiConfig = Depends(ApiConfig.from_env)):
    """Get a session serializer."""
    return URLSafeTimedSerializer(cfg.session_secret, salt="session")


SESSION_MAX_AGE_S = 43200  # 12 hours
MIN_PASSWORD_LEN = 10


def ensure_bootstrap_user(dsn: str, email: str, password: str) -> None:
    """Idempotently create the bootstrap user; if a 'Default' org exists with
    zero memberships (the legacy-migration case), make them its Owner. This is
    the only way to claim a migrated legacy org (spec §3)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()
        if row:
            user_id = row[0]
        else:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (email, hash_password(password), email.split("@")[0]),
            ).fetchone()
        org = conn.execute(
            """SELECT o.id FROM orgs o
               WHERE o.name = 'Default'
                 AND NOT EXISTS (SELECT 1 FROM org_memberships m WHERE m.org_id = o.id)"""
        ).fetchone()
        if org:
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner') ON CONFLICT DO NOTHING",
                (org[0], user_id),
            )


def require_user(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
) -> int:
    """Dependency: the authenticated user's id from the session cookie."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    serializer = get_session_serializer(cfg)
    try:
        data = serializer.loads(session, max_age=SESSION_MAX_AGE_S)
        user_id = data.get("user_id")
        if isinstance(user_id, int):
            return user_id
    except (SignatureExpired, BadSignature):
        pass
    raise HTTPException(status_code=401, detail="Not authenticated")


def user_id_from_session_cookie(session: str, cfg: ApiConfig) -> Optional[int]:
    """Same check as require_user, usable outside Depends (the WebSocket)."""
    serializer = get_session_serializer(cfg)
    try:
        data = serializer.loads(session, max_age=SESSION_MAX_AGE_S)
        user_id = data.get("user_id")
        if isinstance(user_id, int):
            return user_id
    except (SignatureExpired, BadSignature):
        pass
    return None


def require_admin(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
) -> bool:
    """Deprecated: the single-admin auth model is gone (see require_user).

    Kept only so route modules not yet converted to per-user auth (Tasks
    3-8) still import successfully; always rejects.
    """
    raise HTTPException(status_code=401, detail="Not authenticated")


class LoginRateLimiter:
    """Rate limiter for login attempts."""

    def __init__(self, max_attempts: int = 5, window_s: int = 60):
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.attempts = defaultdict(list)  # key -> [timestamps]

    def is_limited(self, key: str) -> bool:
        """Check if a key is rate limited."""
        now = time.time()
        # Clean old attempts
        cutoff = now - self.window_s
        self.attempts[key] = [t for t in self.attempts[key] if t > cutoff]

        # Check if limited
        if len(self.attempts[key]) >= self.max_attempts:
            return True

        # Record this attempt
        self.attempts[key].append(now)
        return False


def get_client_ip(request: Request, trust_proxy: bool = False) -> str:
    """Extract client IP from request.

    By default, only uses the real client IP from request.client.host.
    Set trust_proxy=True only if behind a trusted reverse proxy that sets X-Forwarded-For.
    We serve directly or behind our own compose network, so don't trust XFF by default.
    """
    if trust_proxy and (forwarded := request.headers.get("x-forwarded-for")):
        return forwarded.split(",")[0].strip()
    # Use the real client address
    return request.client.host if request.client else "unknown"


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware for CSRF protection on mutating requests."""

    async def dispatch(self, request: StarletteRequest, call_next):
        """Process request and enforce CSRF for mutations."""
        # Only check mutations (POST, PUT, DELETE, PATCH)
        if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
            # Skip CSRF check for /api/login and /api/register (public endpoints)
            if request.url.path not in ("/api/login", "/api/register"):
                # Get CSRF token from cookie
                csrf_cookie = request.cookies.get("csrf")
                if not csrf_cookie:
                    return JSONResponse(status_code=403, content={"detail": "Missing CSRF token"})

                # Get CSRF token from header
                csrf_header = request.headers.get("x-csrf-token")
                if not csrf_header or csrf_header != csrf_cookie:
                    return JSONResponse(status_code=403, content={"detail": "Invalid CSRF token"})

        response = await call_next(request)
        return response


def _issue_session(response, cfg: ApiConfig, user_id: int):
    """Set a fresh session + CSRF cookie pair (login-time re-issue prevents
    session fixation)."""
    serializer = URLSafeTimedSerializer(cfg.session_secret, salt="session")
    session_cookie = serializer.dumps({"user_id": user_id})
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie("session", session_cookie, httponly=True, samesite="lax",
                        secure=cfg.cookie_secure, max_age=SESSION_MAX_AGE_S)
    response.set_cookie("csrf", csrf_token, httponly=False, samesite="lax",
                        secure=cfg.cookie_secure, max_age=SESSION_MAX_AGE_S)


def create_auth_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    """Router with register/login/logout/me."""
    from fastapi.responses import Response
    from pydantic import BaseModel

    class RegisterRequest(BaseModel):
        email: str
        password: str
        display_name: str

    class LoginRequest(BaseModel):
        email: str
        password: str

    router = APIRouter(prefix="/api", tags=["auth"])

    @router.post("/register")
    async def register(
        request_data: RegisterRequest,
        request: Request,
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        client_ip = get_client_ip(request, trust_proxy=False)
        if rate_limiter.is_limited(f"register:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")

        email = request_data.email.strip().lower()
        if "@" not in email or len(email) < 3:
            raise HTTPException(status_code=400, detail="A valid email is required")
        if len(request_data.password) < MIN_PASSWORD_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LEN} characters")
        display_name = request_data.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=400, detail="display_name is required")

        try:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (email, hash_password(request_data.password), display_name),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Email already registered")

        response = Response(status_code=204)
        _issue_session(response, cfg, user_id)
        return response

    @router.post("/login")
    async def login(
        request_data: LoginRequest,
        request: Request,
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        client_ip = get_client_ip(request, trust_proxy=False)
        email = request_data.email.strip().lower()
        if rate_limiter.is_limited(f"login:{email}:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")

        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE lower(email) = %s", (email,)
        ).fetchone()
        # Verify against a dummy hash for unknown emails so login timing
        # does not reveal whether an email exists (one argon2 verify on
        # both paths).
        if not row:
            verify_password(_DUMMY_HASH, request_data.password)
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if not verify_password(row[1], request_data.password):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        response = Response(status_code=204)
        _issue_session(response, cfg, row[0])
        return response

    @router.post("/logout")
    async def logout():
        response = Response(status_code=204)
        response.delete_cookie("session")
        response.delete_cookie("csrf")
        return response

    @router.get("/me")
    async def me(
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        row = conn.execute(
            "SELECT id, email, display_name FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Not authenticated")
        orgs = conn.execute(
            """SELECT o.id, o.name, m.role FROM org_memberships m
               JOIN orgs o ON o.id = m.org_id WHERE m.user_id = %s ORDER BY o.id""",
            (user_id,),
        ).fetchall()
        return {
            "user": {"id": row[0], "email": row[1], "display_name": row[2]},
            "orgs": [{"id": o[0], "name": o[1], "role": o[2]} for o in orgs],
        }

    return router
