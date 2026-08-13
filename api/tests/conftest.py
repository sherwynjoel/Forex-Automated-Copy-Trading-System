import os
import pathlib
import sys
from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient
import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # makes the top-level `db` package importable

ADMIN_DSN = os.environ.get(
    "TEST_POSTGRES_ADMIN_DSN",
    "postgresql://copytrader:copytrader@localhost:5433/copytrader",
)
TEST_DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql://copytrader:copytrader@localhost:5433/copytrader_test",
)


@pytest.fixture(scope="session")
def database():
    from db.migrate import apply_migrations

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS copytrader_test WITH (FORCE)")
        conn.execute("CREATE DATABASE copytrader_test")
    apply_migrations(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, mappings, symbol_cache, accounts, ctid_connections, oauth_states, admin "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET copying_enabled = true, dry_run = false, shards = 1")
    return database


def default_mock_callback(request: httpx.Request) -> httpx.Response:
    """Default mock transport that handles OAuth token exchange and copier control endpoints."""
    url = str(request.url)

    if "openapi.ctrader.com/apps/token" in url:
        # Mock token exchange response
        return httpx.Response(
            200,
            json={
                "accessToken": "at",
                "refreshToken": "rt",
                "expiresIn": 2592000,  # 30 days
            }
        )
    elif "copier.test" in url:
        # Mock copier endpoints (discover, reload, pause, resume, resync, state, drift, etc.)
        if "/reload" in url or "/pause" in url or "/resume" in url or "/resync" in url:
            return httpx.Response(200, json={"status": "ok"})
        elif "/state" in url:
            return httpx.Response(200, json={"status": "ok", "accounts": []})
        elif "/drift/" in url:
            return httpx.Response(200, json={"action": "completed"})
        elif "/dry-run" in url:
            return httpx.Response(200, json={"status": "ok"})
        else:
            # Default copier response
            return httpx.Response(200, json={"status": "ok"})
    else:
        # Default response
        return httpx.Response(200)


@pytest.fixture
def app_client(db):
    """Provide a TestClient with test environment variables and injectable transport."""
    # Generate a test Fernet key first (before any config imports)
    from cryptography.fernet import Fernet
    fernet_key = Fernet.generate_key().decode()

    # Set up test environment variables
    os.environ["POSTGRES_DSN"] = db
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "hunter2!"
    os.environ["COPIER_CONTROL_URL"] = "http://copier.test"
    os.environ["COOKIE_SECURE"] = "false"  # Disable Secure flag in tests
    os.environ["CTRADER_CLIENT_ID"] = "test-client-id"
    os.environ["CTRADER_CLIENT_SECRET"] = "test-client-secret"
    os.environ["CTRADER_REDIRECT_URI"] = "http://localhost:8000/api/oauth/callback"
    os.environ["CTRADER_AUTH_URL"] = "https://openapi.ctrader.com/apps/auth"
    os.environ["CTRADER_TOKEN_URL"] = "https://openapi.ctrader.com/apps/token"
    os.environ["FERNET_KEY"] = fernet_key

    # Import after setting env vars
    from api.main import create_app
    from api.auth import ensure_admin

    # Explicitly bootstrap admin BEFORE creating the app
    ensure_admin(db, "hunter2!")

    # Create injectable mock transport with configurable callback
    mock_transport = MockTransportWrapper(default_mock_callback)

    # Create app with injectable transport (don't use lifespan context manager)
    app = create_app(http_transport=mock_transport)

    # Manually set up the http client since TestClient doesn't run async lifespan properly
    if not hasattr(app.state, "http"):
        app.state.http = httpx.AsyncClient(transport=mock_transport)

    # Store the transport so tests can replace its callback
    app.state.mock_transport = mock_transport

    client = TestClient(app)

    yield client

    # Clean up
    import asyncio
    if hasattr(app.state, "http"):
        try:
            asyncio.run(app.state.http.aclose())
        except:
            pass


class MockTransportWrapper:
    """Wrapper that allows dynamic callback replacement."""
    def __init__(self, initial_callback):
        self.callback = initial_callback
        self.delegate = httpx.MockTransport(initial_callback)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Handle request with current callback."""
        return self.callback(request)

    def set_callback(self, callback):
        """Replace the callback function."""
        self.callback = callback
        self.delegate = httpx.MockTransport(callback)

    def __getattr__(self, name):
        """Delegate unknown attributes to the underlying MockTransport."""
        return getattr(self.delegate, name)


@pytest.fixture
def copier_error_response(app_client):
    """Helper to configure copier mock to return a specific response."""
    # Get the current transport from the app
    current_transport = app_client.app.state.http._transport

    # If it's already a wrapper, use it; otherwise, wrap it
    if isinstance(current_transport, MockTransportWrapper):
        wrapper = current_transport
    else:
        # Replace with a wrapper
        wrapper = MockTransportWrapper(default_mock_callback)
        app_client.app.state.http = httpx.AsyncClient(transport=wrapper)

    class CopierResponseConfig:
        def __init__(self, wrapper):
            self.wrapper = wrapper

        def return_status_json(self, status_code: int, json_data: dict):
            """Configure copier to return a specific status and JSON."""
            def custom_callback(request: httpx.Request) -> httpx.Response:
                url = str(request.url)
                if "copier.test" in url:
                    return httpx.Response(status_code, json=json_data)
                return default_mock_callback(request)

            self.wrapper.set_callback(custom_callback)

        def return_non_json(self, status_code: int, body: bytes):
            """Configure copier to return a non-JSON response."""
            def custom_callback(request: httpx.Request) -> httpx.Response:
                url = str(request.url)
                if "copier.test" in url:
                    return httpx.Response(status_code, content=body)
                return default_mock_callback(request)

            self.wrapper.set_callback(custom_callback)

        def raise_connect_error(self):
            """Configure copier to raise ConnectError."""
            def custom_callback(request: httpx.Request) -> httpx.Response:
                url = str(request.url)
                if "copier.test" in url:
                    raise httpx.ConnectError("Connection refused")
                return default_mock_callback(request)

            self.wrapper.set_callback(custom_callback)

        def reset(self):
            """Reset to default behavior."""
            self.wrapper.set_callback(default_mock_callback)

    return CopierResponseConfig(wrapper)
