"""FastAPI application factory and main routes."""
import httpx
import psycopg
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request, Cookie
from pydantic import BaseModel

from .config import ApiConfig
from .auth import (
    ensure_admin,
    hash_password,
    verify_password,
    require_admin,
    get_session_serializer,
    get_client_ip,
    LoginRateLimiter,
    CSRFMiddleware,
)
from .db import get_conn


class LoginRequest(BaseModel):
    """Login request body."""

    password: str


class MeResponse(BaseModel):
    """Response for /api/me endpoint."""

    authenticated: bool


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    # Create rate limiter for this app instance
    rate_limiter = LoginRateLimiter(max_attempts=5, window_s=60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage app startup and shutdown."""
        # Startup
        cfg = ApiConfig.from_env()
        ensure_admin(cfg.postgres_dsn, cfg.admin_bootstrap_password)

        # Set up async HTTP client
        app.state.http = httpx.AsyncClient()

        yield

        # Shutdown
        await app.state.http.aclose()

    app = FastAPI(lifespan=lifespan)

    # Store rate limiter in app state
    app.state.rate_limiter = rate_limiter

    # Add CSRF middleware
    app.add_middleware(CSRFMiddleware)

    # Routes

    @app.post("/api/login")
    async def login(request_data: LoginRequest, request: Request, cfg: ApiConfig = Depends(ApiConfig.from_env)):
        """Login with password."""
        # Check rate limit
        client_ip = get_client_ip(request)
        if rate_limiter.is_limited(client_ip):
            raise HTTPException(status_code=429, detail="Too many requests")

        # Get admin password from database
        conn = psycopg.connect(cfg.postgres_dsn, autocommit=True)
        try:
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

            # Create response
            from fastapi.responses import Response

            response = Response(status_code=204)
            response.set_cookie(
                "session",
                session_cookie,
                httponly=True,
                samesite="lax",
                max_age=43200,  # 12 hours
            )
            # CSRF token as readable cookie
            response.set_cookie(
                "csrf",
                session_cookie,  # Use same value for simplicity
                httponly=False,
                samesite="lax",
                max_age=43200,
            )

            return response
        finally:
            conn.close()

    @app.post("/api/logout")
    async def logout():
        """Logout and clear session."""
        from fastapi.responses import Response

        response = Response(status_code=204)
        response.delete_cookie("session")
        response.delete_cookie("csrf")
        return response

    @app.get("/api/me")
    async def me(_: bool = Depends(require_admin)):
        """Get current authenticated user info."""
        return MeResponse(authenticated=True)

    return app


# Module-level app instance for uvicorn
app = create_app()
