"""FastAPI application factory and main routes."""
import httpx
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from .config import ApiConfig
from .auth import (
    ensure_admin,
    LoginRateLimiter,
    CSRFMiddleware,
    create_auth_router,
)
from .oauth import create_oauth_router
from .routes.accounts import create_accounts_router
from .routes.settings_control import create_settings_control_router, create_state_router


def create_app(http_transport: Optional[httpx.BaseTransport] = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        http_transport: Optional httpx transport (for testing with MockTransport).
    """

    # Create rate limiter for this app instance
    rate_limiter = LoginRateLimiter(max_attempts=5, window_s=60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage app startup and shutdown."""
        # Startup
        cfg = ApiConfig.from_env()
        ensure_admin(cfg.postgres_dsn, cfg.admin_bootstrap_password)

        # Set up async HTTP client with optional transport injection
        if http_transport:
            app.state.http = httpx.AsyncClient(transport=http_transport)
        else:
            app.state.http = httpx.AsyncClient()

        yield

        # Shutdown
        await app.state.http.aclose()

    app = FastAPI(lifespan=lifespan)

    # Store rate limiter in app state
    app.state.rate_limiter = rate_limiter

    # Add CSRF middleware
    app.add_middleware(CSRFMiddleware)

    # Include auth router
    auth_router = create_auth_router(rate_limiter)
    app.include_router(auth_router)

    # Include OAuth router
    oauth_router = create_oauth_router()
    app.include_router(oauth_router)

    # Include accounts router
    accounts_router = create_accounts_router()
    app.include_router(accounts_router)

    # Include settings and control routers
    settings_control_router = create_settings_control_router()
    app.include_router(settings_control_router)

    state_router = create_state_router()
    app.include_router(state_router)

    return app


# Module-level app instance for uvicorn
app = create_app()
