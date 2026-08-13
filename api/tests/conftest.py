import os
import pathlib
import sys

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

    # Create injectable mock transport for httpx that handles both token URL and copier discover
    def mock_callback(request: httpx.Request) -> httpx.Response:
        """Mock transport that handles OAuth token exchange and copier discover."""
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
        elif "copier.test" in url and request.method == "POST":
            # Mock copier discover endpoint
            return httpx.Response(200, json={"status": "ok"})
        else:
            # Default response
            return httpx.Response(200)

    mock_transport = httpx.MockTransport(mock_callback)

    # Create app with injectable transport (don't use lifespan context manager)
    app = create_app(http_transport=mock_transport)

    # Manually set up the http client since TestClient doesn't run async lifespan properly
    if not hasattr(app.state, "http"):
        app.state.http = httpx.AsyncClient(transport=mock_transport)

    client = TestClient(app)

    yield client

    # Clean up
    import asyncio
    if hasattr(app.state, "http"):
        try:
            asyncio.run(app.state.http.aclose())
        except:
            pass
