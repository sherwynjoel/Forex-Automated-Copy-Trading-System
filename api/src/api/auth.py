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


def ensure_admin(dsn: str, bootstrap_password: str) -> None:
    """Ensure admin user exists with bootstrapped password, idempotently."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        # Use INSERT ... ON CONFLICT to preserve idempotency
        hashed = hash_password(bootstrap_password)
        conn.execute(
            "INSERT INTO admin (id, password_hash) VALUES (TRUE, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (hashed,),
        )


def get_session_serializer(cfg: ApiConfig = Depends(ApiConfig.from_env)):
    """Get a session serializer."""
    return URLSafeTimedSerializer(cfg.session_secret, salt="session")


def require_admin(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
) -> bool:
    """Dependency that ensures user is authenticated via session cookie."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    serializer = get_session_serializer(cfg)
    try:
        # max_age=12h = 43200 seconds
        data = serializer.loads(session, max_age=43200)
        # Verify the session data contains authenticated flag
        if data.get("authenticated"):
            return True
    except (SignatureExpired, BadSignature):
        pass

    raise HTTPException(status_code=401, detail="Not authenticated")


class LoginRateLimiter:
    """Rate limiter for login attempts."""

    def __init__(self, max_attempts: int = 5, window_s: int = 60):
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.attempts = defaultdict(list)  # ip -> [timestamps]

    def is_limited(self, ip: str) -> bool:
        """Check if an IP is rate limited."""
        now = time.time()
        # Clean old attempts
        cutoff = now - self.window_s
        self.attempts[ip] = [t for t in self.attempts[ip] if t > cutoff]

        # Check if limited
        if len(self.attempts[ip]) >= self.max_attempts:
            return True

        # Record this attempt
        self.attempts[ip].append(now)
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
            # Skip CSRF check for /api/login (public endpoint)
            if request.url.path != "/api/login":
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


def create_auth_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    """Create router with authentication endpoints."""
    from pydantic import BaseModel

    class LoginRequest(BaseModel):
        """Login request body."""

        password: str

    router = APIRouter(prefix="/api", tags=["auth"])

    @router.post("/login")
    async def login(
        request_data: LoginRequest,
        request: Request,
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        """Login with password."""
        # Check rate limit using real client IP
        client_ip = get_client_ip(request, trust_proxy=False)
        if rate_limiter.is_limited(client_ip):
            raise HTTPException(status_code=429, detail="Too many requests")

        # Get admin password from database
        result = conn.execute("SELECT password_hash FROM admin WHERE id = TRUE").fetchone()
        if not result:
            raise HTTPException(status_code=401, detail="Unauthorized")

        password_hash = result[0]

        # Verify password
        if not verify_password(password_hash, request_data.password):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Create session
        serializer = get_session_serializer(cfg)
        session_data = {"authenticated": True}
        session_cookie = serializer.dumps(session_data)

        # Generate independent CSRF token (not derived from session)
        csrf_token = secrets.token_urlsafe(32)

        # Create response
        from fastapi.responses import Response

        response = Response(status_code=204)
        response.set_cookie(
            "session",
            session_cookie,
            httponly=True,
            samesite="lax",
            secure=cfg.cookie_secure,
            max_age=43200,  # 12 hours
        )
        # CSRF token as readable cookie (independent from session)
        response.set_cookie(
            "csrf",
            csrf_token,
            httponly=False,
            samesite="lax",
            secure=cfg.cookie_secure,
            max_age=43200,
        )

        return response

    @router.post("/logout")
    async def logout():
        """Logout and clear session."""
        from fastapi.responses import Response

        response = Response(status_code=204)
        response.delete_cookie("session")
        response.delete_cookie("csrf")
        return response

    @router.get("/me")
    async def me(_: bool = Depends(require_admin)):
        """Get current authenticated user info."""
        from pydantic import BaseModel

        class MeResponse(BaseModel):
            """Response for /api/me endpoint."""

            authenticated: bool

        return MeResponse(authenticated=True)

    return router
