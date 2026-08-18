"""FastAPI application factory and main routes."""
import asyncio
import httpx
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import ApiConfig
from .auth import (
    ensure_bootstrap_user,
    LoginRateLimiter,
    CSRFMiddleware,
    create_auth_router,
)
from .oauth import create_oauth_router, create_oauth_callback_router
from .routes.orgs import create_orgs_router
from .routes.accounts import create_accounts_router
from .routes.events import create_events_router
from .routes.settings_control import create_settings_control_router, create_state_router
from .routes.trading import create_trading_router
from .ws import create_ws_router, broadcaster


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
        if cfg.bootstrap_admin_email and cfg.bootstrap_admin_password:
            ensure_bootstrap_user(
                cfg.postgres_dsn, cfg.bootstrap_admin_email, cfg.bootstrap_admin_password)

        # Set up async HTTP client with optional transport injection
        if http_transport:
            app.state.http = httpx.AsyncClient(transport=http_transport)
        else:
            app.state.http = httpx.AsyncClient()

        # Set DSN and start listener task immediately
        broadcaster.dsn = cfg.postgres_dsn
        broadcaster.listener_task = asyncio.create_task(broadcaster.start_listener(cfg.postgres_dsn))

        yield

        # Shutdown
        await broadcaster.stop_listener()
        await app.state.http.aclose()

    app = FastAPI(lifespan=lifespan)

    # Store rate limiter in app state
    app.state.rate_limiter = rate_limiter

    # Add CSRF middleware
    app.add_middleware(CSRFMiddleware)

    # Include auth router
    auth_router = create_auth_router(rate_limiter)
    app.include_router(auth_router)

    # Include orgs router (lifecycle, members, invites, join)
    app.include_router(create_orgs_router())

    # Include OAuth routers: org-scoped connect + global callback
    oauth_router = create_oauth_router()
    app.include_router(oauth_router)
    oauth_callback_router = create_oauth_callback_router()
    app.include_router(oauth_callback_router)

    # Include accounts router
    accounts_router = create_accounts_router()
    app.include_router(accounts_router)

    # Include settings and control routers
    settings_control_router = create_settings_control_router()
    app.include_router(settings_control_router)

    state_router = create_state_router()
    app.include_router(state_router)

    # Include trading actions router (manual orders, closes, kill switch)
    trading_router = create_trading_router()
    app.include_router(trading_router)

    # Include events router
    events_router = create_events_router()
    app.include_router(events_router)

    # Include WebSocket router
    ws_router = create_ws_router()
    app.include_router(ws_router)

    # Mount static files with SPA fallback if STATIC_DIR is set
    static_dir = os.environ.get("STATIC_DIR")
    if static_dir and os.path.isdir(static_dir):
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            """Serve static files with SPA fallback."""
            # Don't serve /api routes here
            if full_path.startswith("api/") or full_path == "api":
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not found")

            file_path = os.path.join(static_dir, full_path)

            # Make sure the path is within static_dir (security check)
            file_path = os.path.normpath(file_path)
            static_dir_norm = os.path.normpath(static_dir)
            if not file_path.startswith(static_dir_norm):
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not found")

            # If file exists, serve it
            if os.path.isfile(file_path):
                return FileResponse(file_path)

            # Otherwise, return index.html for SPA routing
            index_file = os.path.join(static_dir, "index.html")
            if os.path.exists(index_file):
                return FileResponse(index_file)

            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")

    return app


# Module-level app instance for uvicorn
app = create_app()
