"""Authentication and authorization utilities."""
import hashlib
import logging
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
logger = logging.getLogger(__name__)

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
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"BOOTSTRAP_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LEN} "
            f"characters")
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()
        if row:
            user_id = row[0]
            # The credential is plaintext in .env and visible to `docker
            # inspect`; once the account exists it has no further purpose.
            logger.warning(
                "BOOTSTRAP_ADMIN_* is still set but the bootstrap user "
                "already exists - clear both variables from .env and "
                "restart to stop shipping an admin password in the "
                "environment")
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


def _unpack_session(session: str, cfg: ApiConfig) -> Optional[tuple[int, int]]:
    """(user_id, session_version) from a signed cookie, or None if the
    signature is bad or it has aged out. Cookies issued before the version
    existed read as version 0, which matches the column default."""
    serializer = get_session_serializer(cfg)
    try:
        data = serializer.loads(session, max_age=SESSION_MAX_AGE_S)
    except (SignatureExpired, BadSignature):
        return None
    user_id = data.get("user_id")
    if not isinstance(user_id, int):
        return None
    version = data.get("sv")
    return user_id, version if isinstance(version, int) else 0


def session_version_matches(conn, user_id: int, version: int) -> bool:
    """Whether the cookie's version still matches the user's current one.

    A missing user (deleted account) fails closed. This is the one extra
    primary-key read that makes stateless sessions revocable.
    """
    row = conn.execute(
        "SELECT session_version FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    return row is not None and row[0] == version


def require_user(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
    conn: psycopg.Connection = Depends(get_conn),
) -> int:
    """Dependency: the authenticated user's id from the session cookie."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    unpacked = _unpack_session(session, cfg)
    if unpacked is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id, version = unpacked
    if not session_version_matches(conn, user_id, version):
        # Password changed, signed out everywhere, or the account is gone.
        raise HTTPException(status_code=401, detail="Session expired")
    return user_id


def session_identity(session: str, cfg: ApiConfig) -> Optional[tuple[int, int]]:
    """(user_id, session_version) for callers outside Depends (the
    WebSocket), which must check the version against the database
    themselves -- they already hold a connection for the membership read."""
    return _unpack_session(session, cfg)


# `user_id_from_session_cookie` used to live here. It validated only the
# signature, so it could not see a session disowned by a password change --
# a second, weaker door beside the checked one. Use session_identity() plus
# session_version_matches() instead.


class LoginRateLimiter:
    """Rate limiter for login attempts."""

    def __init__(self, max_attempts: int = 5, window_s: int = 60):
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.attempts = defaultdict(list)  # key -> [timestamps]
        self._sweep_at = 1024  # dict size that triggers the next sweep

    def is_limited(self, key: str, max_attempts: int | None = None) -> bool:
        """Check if a key is rate limited.

        `max_attempts` overrides the instance default for callers that keep a
        wider bucket (e.g. per-source login attempts across many emails).
        """
        limit = self.max_attempts if max_attempts is None else max_attempts
        now = time.time()
        # Clean old attempts
        cutoff = now - self.window_s
        self.attempts[key] = [t for t in self.attempts[key] if t > cutoff]
        # Keys embed an attacker-supplied email, so an unbounded dict is a
        # slow memory leak driven by request volume. Drop keys that have
        # aged out entirely; the sweep is bounded and amortized.
        if len(self.attempts) > self._sweep_at:
            self.attempts = defaultdict(list, {
                k: v for k, v in self.attempts.items()
                if v and max(v) > cutoff})
            self._sweep_at = max(1024, len(self.attempts) * 2)

        # Check if limited
        if len(self.attempts[key]) >= limit:
            return True

        # Record this attempt
        self.attempts[key].append(now)
        return False


def get_client_ip(request: Request, trust_proxy: bool = False) -> str:
    """Extract client IP from request.

    By default, only uses the real client IP from request.client.host.

    In the production topology (Caddy -> 127.0.0.1:8000) request.client.host
    is the PROXY for every request, which collapses per-IP rate limiting
    into one global bucket -- so TRUST_PROXY=true makes us read the hop
    Caddy appends. Take the LAST entry: earlier ones are client-supplied and
    forgeable, the last is the address our own proxy observed.
    """
    if trust_proxy and (forwarded := request.headers.get("x-forwarded-for")):
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    # Use the real client address
    return request.client.host if request.client else "unknown"


def _is_proxy_address(ip: str) -> bool:
    """Loopback (or unknown) means we are seeing our own reverse proxy, not
    a client -- per-source limits keyed on it would punish everyone."""
    return ip in ("127.0.0.1", "::1", "localhost", "unknown")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware for CSRF protection on mutating requests."""

    async def dispatch(self, request: StarletteRequest, call_next):
        """Process request and enforce CSRF for mutations."""
        # Only check mutations (POST, PUT, DELETE, PATCH)
        if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
            # Skip CSRF check for the public endpoints: login, register,
            # and inbound webhooks. A webhook has no session and no CSRF
            # cookie by definition -- it authenticates with a per-org
            # secret in its body (routes/webhooks.py), and the prefix is
            # its own namespace so nothing else is ever exempted with it.
            path = request.url.path
            if path not in ("/api/login", "/api/register") and not path.startswith("/api/webhooks/"):
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


def _invite_is_open(conn, token: Optional[str]) -> bool:
    """Whether this invite token is real, unused and unexpired.

    Read-only on purpose: it authorizes creating the ACCOUNT. The token is
    consumed later by POST /api/orgs/join, which is what actually grants
    membership -- so a token stays valid if signup fails halfway.
    """
    if not token:
        return False
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = conn.execute(
        "SELECT 1 FROM org_invites WHERE token_hash = %s "
        "AND consumed_at IS NULL AND expires_at > now()",
        (token_hash,),
    ).fetchone()
    return row is not None


def _issue_session(response, cfg: ApiConfig, user_id: int, session_version: int = 0):
    """Set a fresh session + CSRF cookie pair (login-time re-issue prevents
    session fixation).

    The user's session_version is baked into the cookie so the server can
    disown it later: bumping the column invalidates every cookie already
    issued for that user (see require_user).
    """
    serializer = URLSafeTimedSerializer(cfg.session_secret, salt="session")
    session_cookie = serializer.dumps({"user_id": user_id, "sv": session_version})
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
        # An invite token authorizes signup even when open registration is
        # off: the whole point of an invite is to let a specific new person
        # in. Without this the gate does not restrict onboarding, it ends it.
        invite_token: Optional[str] = None

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
        if not cfg.registration_enabled and not _invite_is_open(
                conn, request_data.invite_token):
            raise HTTPException(
                status_code=403,
                detail="Self-service registration is disabled; ask an "
                       "administrator for an invite link")
        client_ip = get_client_ip(request, trust_proxy=cfg.trust_proxy)
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
        client_ip = get_client_ip(request, trust_proxy=cfg.trust_proxy)
        email = request_data.email.strip().lower()
        # Two buckets: per-credential (stops guessing ONE password) and
        # per-source (stops spraying MANY accounts from one host).
        # Per-credential bucket always applies. The per-SOURCE bucket only
        # makes sense when client_ip is a real client: behind a proxy whose
        # header we do not trust, every request looks like the proxy itself,
        # and a shared bucket would let one attacker lock out the whole
        # deployment. Falling back to the per-email limit is the safe side.
        if rate_limiter.is_limited(f"login:{email}:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")
        if not _is_proxy_address(client_ip) and rate_limiter.is_limited(
                f"login-ip:{client_ip}", max_attempts=20):
            raise HTTPException(status_code=429, detail="Too many requests")

        row = conn.execute(
            "SELECT id, password_hash, session_version FROM users "
            "WHERE lower(email) = %s", (email,)
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
        _issue_session(response, cfg, row[0], row[2])
        return response

    @router.post("/logout")
    async def logout():
        response = Response(status_code=204)
        response.delete_cookie("session")
        response.delete_cookie("csrf")
        return response

    class PasswordChangeRequest(BaseModel):
        current_password: str
        new_password: str

    @router.post("/me/password")
    async def change_password(
        request_data: PasswordChangeRequest,
        user_id: int = Depends(require_user),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        """Rotate the caller's password and disown every other session.

        Requires the current password (a stolen session alone must not be
        enough to take an account over). Bumping session_version kills all
        outstanding cookies -- including any the attacker holds -- and this
        browser is handed a freshly versioned one so the user stays signed
        in where they are.
        """
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not verify_password(row[0], request_data.current_password):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
        if len(request_data.new_password) < MIN_PASSWORD_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LEN} characters")
        if request_data.new_password == request_data.current_password:
            raise HTTPException(
                status_code=400, detail="The new password must be different")

        (new_version,) = conn.execute(
            "UPDATE users SET password_hash = %s, session_version = session_version + 1 "
            "WHERE id = %s RETURNING session_version",
            (hash_password(request_data.new_password), user_id),
        ).fetchone()

        # The cookie is dead everywhere else; the sockets it already opened
        # must go with it (they are only checked at handshake).
        from .ws import broadcaster
        await broadcaster.close_for_user(user_id)

        response = Response(status_code=204)
        _issue_session(response, cfg, user_id, new_version)
        return response

    @router.post("/me/logout-all")
    async def logout_everywhere(
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        """Invalidate every session this user has anywhere, this one included
        -- the break-glass control for a cookie you think was stolen."""
        conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = %s",
            (user_id,),
        )
        from .ws import broadcaster
        await broadcaster.close_for_user(user_id)

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
