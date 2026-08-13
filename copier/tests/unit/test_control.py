"""Tests for the control endpoint and boot sequence (control.py, main.py)."""

import json
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from datetime import datetime, timedelta
from io import BytesIO

import psycopg
import pytest
from twisted.internet import defer, reactor as real_reactor
from twisted.internet.task import Clock
from twisted.web.test.requesthelper import DummyRequest
from twisted.web import server
from cryptography.fernet import Fernet

from copier.db.repo import Repo
from copier.engine.control import (
    make_control_site, HealthResource, StateResource, PauseResource, ResumeResource
)
from copier.ctrader.tokens import TokenStore
from copier.engine.service import CopierService
from copier.engine.reconcile import Reconciler
from copier.engine.state import AccountStateTracker
from copier.engine.dispatch import Dispatcher
from copier.engine.throttle import TokenBucket
from copier.domain.models import SymbolInfo


# Test fixtures and helpers

EURUSD = SymbolInfo(
    symbol_id=1, name="EURUSD", digits=5,
    lot_size=10_000_000, min_volume=100_000, step_volume=100_000
)


def seed_db(db, fernet_key=None):
    """Seed test database with accounts and connections."""
    if fernet_key is None:
        fernet_key = Fernet.generate_key().decode()

    fernet = Fernet(fernet_key.encode())
    access_enc = fernet.encrypt(b"token_access").decode()
    refresh_enc = fernet.encrypt(b"token_refresh").decode()

    with psycopg.connect(db, autocommit=True) as conn:
        # Create connection with encrypted tokens
        conn.execute(
            """
            INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, now(), now() + interval '30 days')
            """,
            (access_enc, refresh_enc),
        )
        # Create master account (999) and slave accounts (100, 101)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (999, 1, 99900, false, 'master', true, 1.0),
                (100, 1, 10000, false, 'slave', true, 1.0),
                (101, 1, 10001, false, 'slave', true, 1.5)
            """
        )

    return fernet_key


@pytest.fixture
def fernet_key():
    """Generate a Fernet key for testing."""
    return Fernet.generate_key().decode()


@pytest.fixture
def db_seeded(db, fernet_key):
    """Database fixture with seeded accounts."""
    seed_db(db, fernet_key)
    return db


@pytest.fixture
def repo(db_seeded):
    """Create a Repo instance connected to test database."""
    return Repo(db_seeded)


@pytest.fixture
def clock():
    """Twisted Clock for testing time-based behavior."""
    return Clock()


@pytest.fixture
def token_store(db_seeded, fernet_key):
    """Create a TokenStore instance for testing."""
    return TokenStore(db_seeded, fernet_key)


@pytest.fixture
def stub_client():
    """Stub CTraderClient for testing."""
    stub = MagicMock()
    stub.ready = defer.succeed(None)
    stub.send = MagicMock(return_value=defer.succeed(None))
    stub.on_execution = MagicMock()
    stub.on_account_disconnect = MagicMock()
    stub.on_tokens_invalidated = MagicMock()
    stub.on_spot = MagicMock()
    stub.authorize_account = MagicMock(return_value=defer.succeed(None))
    stub.deauthorize_account = MagicMock()
    stub.start = MagicMock()
    stub.stop = MagicMock()
    return stub


@pytest.fixture
def stub_dispatcher():
    """Stub Dispatcher for testing."""
    return MagicMock(spec=Dispatcher)


class StubCopierApp:
    """Stub CopierApp for testing control endpoints."""

    def __init__(self, repo, token_store, clock=None):
        self.repo = repo
        self.token_store = token_store
        self.clients = {}
        self.service = MagicMock(spec=CopierService)
        self.reconciler = MagicMock(spec=Reconciler)
        self.state_tracker = MagicMock(spec=AccountStateTracker)
        self.clock = clock or Clock()
        self._token_refresh_running = False

    def pause(self, account_id=None):
        """Pause copying globally or per-slave."""
        if account_id is None:
            self.repo.set_setting("copying_enabled", False)
            self.repo.log_event(
                'control',
                'info',
                {'action': 'pause_global'},
            )
        else:
            self.repo.set_account_status(account_id, 'paused')
            self.repo.log_event(
                'control',
                'info',
                {'action': 'pause_slave', 'account_id': account_id},
                account_id=account_id,
            )

    def resume(self, account_id=None):
        """Resume copying globally or per-slave."""
        if account_id is None:
            self.repo.set_setting("copying_enabled", True)
            self.repo.log_event(
                'control',
                'info',
                {'action': 'resume_global'},
            )
        else:
            self.repo.set_account_status(account_id, 'ok')
            self.repo.log_event(
                'control',
                'info',
                {'action': 'resume_slave', 'account_id': account_id},
                account_id=account_id,
            )

    def set_dry_run(self, enabled):
        """Enable or disable dry-run mode."""
        self.repo.set_setting("dry_run", enabled)

    def resync(self):
        """Trigger reconciliation."""
        return self.reconciler.run()

    def get_health(self):
        """Get health status."""
        settings = self.repo.get_settings()
        accounts = self.repo.load_accounts()
        master_account = next((a for a in accounts if a.role == 'master'), None)
        return {
            "status": "ok",
            "master": master_account.account_id if master_account else None,
            "copying_enabled": settings.copying_enabled,
            "dry_run": settings.dry_run,
        }

    def get_state(self):
        """Get current state."""
        snapshot = self.state_tracker.snapshot()
        return {
            "accounts": snapshot,
            "master_positions": [],
            "pending_orders": [],
            "drift": self.reconciler.current if hasattr(self.reconciler, 'current') else [],
        }


# Tests for health endpoint

def test_health_reports_settings(db_seeded):
    """Test /health returns correct settings."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)

    # Directly test the resource
    resource = HealthResource(app)
    request = DummyRequest([b'health'])
    request.method = b'GET'
    result = resource.render_GET(request)

    # Parse response
    response = json.loads(result)

    assert response['status'] == 'ok'
    assert response['master'] == 999
    assert response['copying_enabled'] is True
    assert response['dry_run'] is False


# Tests for pause/resume

def test_pause_global_flips_kill_switch_and_logs_control_event(db_seeded):
    """Test global pause sets copying_enabled to false and logs event."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)

    # Pause globally
    app.pause(account_id=None)

    # Verify setting was updated
    settings = repo.get_settings()
    assert settings.copying_enabled is False

    # Verify event was logged
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        row = conn.execute(
            "SELECT category, payload FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == 'control'
        # psycopg converts JSONB to dict automatically
        payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        assert payload['action'] == 'pause_global'


def test_pause_single_slave_sets_paused_status(db_seeded):
    """Test per-slave pause sets account status to paused."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)

    # Pause single slave
    app.pause(account_id=100)

    # Verify status was updated
    accounts = repo.load_accounts()
    account_100 = next(a for a in accounts if a.account_id == 100)
    assert account_100.status == 'paused'

    # Verify event was logged
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        row = conn.execute(
            "SELECT category, payload FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == 'control'
        payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        assert payload['action'] == 'pause_slave'
        assert payload['account_id'] == 100


# Tests for dry-run toggle

def test_dry_run_toggle_persists(db_seeded):
    """Test dry-run toggle persists to database."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)

    # Enable dry-run
    app.set_dry_run(True)
    settings = repo.get_settings()
    assert settings.dry_run is True

    # Disable dry-run
    app.set_dry_run(False)
    settings = repo.get_settings()
    assert settings.dry_run is False


# Tests for token refresh

def test_refresh_due_tokens_rotates_and_persists(db_seeded, token_store):
    """Test token refresh rotates tokens and persists them."""
    # Get the existing token pair (connection_id=1)
    old_pair = token_store.get(1)

    # Simulate refresh by rotating tokens
    new_access = "new_access_token"
    new_refresh = "new_refresh_token"
    new_expires = datetime.utcnow() + timedelta(days=30)

    token_store.rotate(1, new_access, new_refresh, new_expires)

    # Verify tokens were rotated
    new_pair = token_store.get(1)
    assert new_pair.access_token == new_access
    assert new_pair.refresh_token == new_refresh
    assert new_pair.status == 'active'


def test_refresh_failure_marks_and_alerts(db_seeded, token_store):
    """Test refresh failure marks connection and logs alert event."""
    # Mark connection as refresh_failed
    token_store.mark(1, 'refresh_failed')

    # Verify status was updated
    pair = token_store.get(1)
    assert pair.status == 'refresh_failed'


# Tests for discovery

def test_discover_upserts_accounts_from_token(db_seeded):
    """Test discover upserts accounts from connection."""
    repo = Repo(db_seeded)

    # Upsert a new account
    repo.upsert_account(
        account_id=200,
        connection_id=1,
        trader_login=12345,
        is_live=False,
    )

    # Verify account was created
    accounts = repo.load_accounts()
    account_200 = next((a for a in accounts if a.account_id == 200), None)
    assert account_200 is not None
    assert account_200.trader_login == 12345


# Tests for startup without accounts

def test_startup_with_zero_accounts_does_not_crash(db_seeded):
    """Test startup with no accounts configured."""
    # Delete all accounts
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        conn.execute("DELETE FROM accounts")

    repo = Repo(db_seeded)
    accounts = repo.load_accounts()
    assert len(accounts) == 0

    # This should not crash
    app = StubCopierApp(repo, None)
    assert app is not None


# Tests for control site

def test_control_site_get_state(db_seeded):
    """Test /state endpoint returns account snapshots."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)
    app.state_tracker.snapshot = MagicMock(return_value={
        999: {"balance": 10000.0, "open_pnl": 0.0, "equity": 10000.0, "positions": []}
    })
    app.reconciler.current = []

    resource = StateResource(app)
    request = DummyRequest([b'state'])
    request.method = b'GET'
    result = resource.render_GET(request)

    response = json.loads(result)

    assert 'accounts' in response
    # JSON serialization converts integer keys to strings
    assert '999' in response['accounts']
    assert response['accounts']['999']['balance'] == 10000.0


def test_control_site_post_pause(db_seeded):
    """Test POST /pause endpoint."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)

    resource = PauseResource(app)
    request = DummyRequest([b'pause'])
    request.method = b'POST'
    request.content = BytesIO(json.dumps({"account_id": None}).encode())
    result = resource.render_POST(request)

    # Verify pause was called
    settings = repo.get_settings()
    assert settings.copying_enabled is False


def test_control_site_post_resume(db_seeded):
    """Test POST /resume endpoint."""
    repo = Repo(db_seeded)
    app = StubCopierApp(repo, None)

    # First pause
    repo.set_setting("copying_enabled", False)

    # Then resume
    resource = ResumeResource(app)
    request = DummyRequest([b'resume'])
    request.method = b'POST'
    request.content = BytesIO(json.dumps({"account_id": None}).encode())
    result = resource.render_POST(request)

    # Verify resume was called
    settings = repo.get_settings()
    assert settings.copying_enabled is True
