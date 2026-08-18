import os
import pathlib
import sys
import threading
import time
from contextlib import contextmanager

import psycopg
import pytest
import uvicorn
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
TEST_DB_NAME = TEST_DSN.rsplit("/", 1)[1]


@pytest.fixture(scope="session")
def database():
    from db.migrate import apply_migrations

    # The scratch database NAME comes from TEST_POSTGRES_DSN (TEST_DB_NAME)
    # rather than being hardcoded: parallel sessions (e.g. two agents on
    # different branches) can then isolate by exporting DSNs with distinct
    # names instead of silently dropping each other's scratch DB mid-run.
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    apply_migrations(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, portfolio_snapshots, mappings, symbol_cache, "
            "accounts, ctid_connections, "
            "oauth_states, org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET shards = 1")
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


@pytest.fixture
def app_client_with_lifespan(db):
    """Provide a TestClient that runs ASGI lifespan for async features like WebSocket listeners."""
    # Generate a test Fernet key first (before any config imports)
    from cryptography.fernet import Fernet
    fernet_key = Fernet.generate_key().decode()

    # Set up test environment variables
    os.environ["POSTGRES_DSN"] = db
    os.environ["SESSION_SECRET"] = "test-secret"
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
    from api.ws import broadcaster

    # Reset broadcaster for this test (clean slate)
    broadcaster.connections.clear()
    broadcaster.listener_task = None
    broadcaster.listener_connection = None
    broadcaster.query_connection = None
    broadcaster.dsn = None

    # Create injectable mock transport with configurable callback
    mock_transport = MockTransportWrapper(default_mock_callback)

    # Create app with injectable transport
    app = create_app(http_transport=mock_transport)

    # Store the transport so tests can replace its callback
    app.state.mock_transport = mock_transport

    # Use context manager so ASGI lifespan runs (required for broadcaster listener)
    with TestClient(app) as client:
        yield client


def _set_live_test_env(db: str) -> None:
    """Set the process env vars ApiConfig.from_env() needs, pointed at the test DB.

    Mirrors app_client/app_client_with_lifespan. Shared here because live_server
    also needs it, and os.environ is process-global (shared with the uvicorn
    background thread since it's the same process, just a different thread).
    """
    from cryptography.fernet import Fernet

    fernet_key = Fernet.generate_key().decode()
    os.environ["POSTGRES_DSN"] = db
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["COPIER_CONTROL_URL"] = "http://copier.test"
    os.environ["COOKIE_SECURE"] = "false"
    os.environ["CTRADER_CLIENT_ID"] = "test-client-id"
    os.environ["CTRADER_CLIENT_SECRET"] = "test-client-secret"
    os.environ["CTRADER_REDIRECT_URI"] = "http://localhost:8000/api/oauth/callback"
    os.environ["CTRADER_AUTH_URL"] = "https://openapi.ctrader.com/apps/auth"
    os.environ["CTRADER_TOKEN_URL"] = "https://openapi.ctrader.com/apps/token"
    os.environ["FERNET_KEY"] = fernet_key


@pytest.fixture
def live_server(db):
    """Run the real app under a real uvicorn.Server in a background thread.

    This is a genuine TCP server on an ephemeral port with ASGI lifespan enabled,
    so EventBroadcaster's LISTEN/NOTIFY task actually runs (unlike TestClient's
    ASGI-transport shortcut). A `websockets` client connecting to it talks over a
    real socket -- there is no shared/cross-loop asyncio object between the test
    and the server; the only channel between them is the TCP connection itself
    (for the WS) and Postgres (for the INSERT -> pg_notify).

    Yields (base_url, ws_url) for the bound ephemeral port. The test DB DSN is
    the same `db` string the app was configured with (POSTGRES_DSN), so tests
    that want to insert rows the app's listener will see should connect with `db`.
    """
    _set_live_test_env(db)

    from api.main import create_app
    from api.ws import broadcaster

    # broadcaster is a module-level singleton reused across tests/fixtures in this
    # process -- reset it to a clean slate before this test's server starts.
    broadcaster.connections.clear()
    broadcaster.listener_task = None
    broadcaster.listener_connection = None
    broadcaster.query_connection = None
    broadcaster.dsn = None
    broadcaster.listening = False

    mock_transport = MockTransportWrapper(default_mock_callback)
    app = create_app(http_transport=mock_transport)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        deadline = time.time() + 10
        while not server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn live_server did not start within 10s")
            time.sleep(0.01)

        # Wait for the EventBroadcaster to actually issue LISTEN events before
        # handing control to the test -- otherwise an INSERT could fire pg_notify
        # before any listener is registered on that channel, and the notification
        # would simply be lost (not queued for a not-yet-listening connection).
        deadline = time.time() + 10
        while not broadcaster.listening:
            if time.time() > deadline:
                raise RuntimeError("EventBroadcaster did not start LISTENing within 10s")
            time.sleep(0.01)

        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/api/ws"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("uvicorn live_server thread did not shut down within 10s")


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


@pytest.fixture
def make_user(db):
    """Create a user directly in the DB; returns {id, email, password, display_name}."""
    from api.auth import hash_password

    def _make(email="user@example.com", password="a-solid-password", display_name="User"):
        with psycopg.connect(db, autocommit=True) as conn:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (email, hash_password(password), display_name),
            ).fetchone()
        return {"id": user_id, "email": email, "password": password,
                "display_name": display_name}

    return _make


@pytest.fixture
def make_org(db):
    """Create an org with memberships: make_org(name, members=[(user, role), ...])."""

    def _make(name="Desk", members=()):
        with psycopg.connect(db, autocommit=True) as conn:
            (org_id,) = conn.execute(
                "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (name,)
            ).fetchone()
            for user, role in members:
                conn.execute(
                    "INSERT INTO org_memberships (org_id, user_id, role) "
                    "VALUES (%s, %s, %s)",
                    (org_id, user["id"], role),
                )
        return org_id

    return _make


@pytest.fixture
def login_as():
    """Log a TestClient in as a make_user() user (sets session+csrf cookies)."""

    def _login(client, user):
        r = client.post("/api/login", json={
            "email": user["email"], "password": user["password"]})
        assert r.status_code == 204, f"login failed: {r.status_code} {r.text}"

    return _login


@pytest.fixture
def org_client(app_client, make_user, make_org, login_as, db):
    """app_client logged in as the admin of a fresh org; returns
    (client, org_id, seed) where seed(account_id, role, ...) inserts an
    account owned by that org."""

    user = make_user(email="admin@example.com")
    org_id = make_org(name="Desk", members=[(user, "admin")])
    login_as(app_client, user)

    with psycopg.connect(db, autocommit=True) as conn:
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""",
            (org_id,),
        ).fetchone()

    def seed(account_id, role="slave", enabled=True, multiplier=1.0, is_live=False):
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                       org_id, trader_login, is_live, role, enabled, multiplier)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (account_id, connection_id, org_id, account_id, is_live,
                 role, enabled, multiplier),
            )
        return account_id

    return app_client, org_id, seed
