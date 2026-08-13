"""Authentication and authorization utilities."""
import time
from collections import defaultdict
from typing import Optional

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

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
        # Check if admin row exists
        result = conn.execute("SELECT password_hash FROM admin WHERE id = TRUE").fetchone()
        if result is None:
            # Insert admin with hashed password
            hashed = hash_password(bootstrap_password)
            conn.execute(
                "INSERT INTO admin (id, password_hash) VALUES (TRUE, %s)",
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


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    # Check X-Forwarded-For first (for proxies)
    if forwarded := request.headers.get("x-forwarded-for"):
        return forwarded.split(",")[0].strip()
    # Fall back to client address
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
                    return Response(status_code=403, detail="Missing CSRF token")

                # Get CSRF token from header
                csrf_header = request.headers.get("x-csrf-token")
                if not csrf_header or csrf_header != csrf_cookie:
                    return Response(status_code=403)

        response = await call_next(request)
        return response
