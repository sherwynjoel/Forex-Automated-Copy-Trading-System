"""Tests for the control endpoint and boot sequence (control.py, main.py).

Every test here drives the REAL CopierApp / control site produced by
copier.main / copier.engine.control -- there is no hand-duplicated stub app.
Clients are either a lightweight StubSdk-backed CTraderClient (synchronous,
no real socket) or a real CTraderClient talking to FakeCTraderServer over a
real TLS socket on the real reactor, matching the patterns already used by
tests/unit/test_client.py, tests/unit/test_state.py and
tests/unit/test_reconcile.py.
"""

import json
from datetime import timedelta
from io import BytesIO

import psycopg
import pytest
import pytest_twisted
from cryptography.fernet import Fernet
from ctrader_open_api import Client, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAccountsTokenInvalidatedEvent
from twisted.internet import defer, reactor as real_reactor, task
from twisted.internet.task import Clock
from twisted.python.failure import Failure
from twisted.web.client import Agent, readBody
from twisted.web.test.requesthelper import DummyRequest

import copier.main as main
from copier.ctrader.client import CTraderClient
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.engine.control import (
    make_control_site, HealthResource, StateResource, PauseResource, ResumeResource,
    DryRunResource, DriftCloseOrphanResource,
)
from copier.testing.fake_server import FakeCTraderServer
from test_client import StubSdk

# ---------- fixtures ----------


def seed_db(db, fernet_key, expires_in_days=30):
    """Seed test database with one connection and three accounts: 999 master, 100/101 slaves."""
    fernet = Fernet(fernet_key.encode())
    access_enc = fernet.encrypt(b"token_access").decode()
    refresh_enc = fernet.encrypt(b"token_refresh").decode()

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, now(), now() + %s * interval '1 day')
            """,
            (access_enc, refresh_enc, expires_in_days),
        )
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (999, 1, 99900, false, 'master', true, 1.0),
                (100, 1, 10000, false, 'slave', true, 1.0),
                (101, 1, 10001, false, 'slave', true, 1.5)
            """
        )


@pytest.fixture
def fernet_key():
    return Fernet.generate_key().decode()


@pytest.fixture
def db_seeded(db, fernet_key):
    seed_db(db, fernet_key)
    return db


@pytest.fixture
def repo(db_seeded):
    return Repo(db_seeded)


@pytest.fixture
def token_store(db_seeded, fernet_key):
    return TokenStore(db_seeded, fernet_key)


def make_stub_client_factory():
    """Client factory producing StubSdk-backed CTraderClients that are
    app-authed (ready) synchronously the moment they're built -- lets
    startup()/reload() run to completion without needing a real reactor."""
    built = []

    def factory(is_live):
        sdk = StubSdk()
        client = CTraderClient(sdk, "test-cid", "test-secret")
        sdk.connect()  # synchronously fires app auth -> ready, via StubSdk's synchronous send()
        built.append(client)
        return client

    factory.built = built
    return factory


class _FailingClient:
    """Minimal stub CTraderClient whose send() always fails -- for exercising
    the refresh-failure path without any real I/O."""

    def __init__(self):
        self.ready = defer.succeed(self)
        self._accounts = {}

    def start(self):
        pass

    def on_execution(self, cb):
        pass

    def on_tokens_invalidated(self, cb):
        pass

    def on_account_disconnect(self, cb):
        pass

    def on_spot(self, cb):
        pass

    def on_trader_updated(self, cb):
        pass

    def on_margin_call(self, cb):
        pass

    def authorize_account(self, account_id, token):
        self._accounts[account_id] = token
        return defer.succeed(None)

    def deauthorize_account(self, account_id):
        self._accounts.pop(account_id, None)

    def send(self, msg):
        return defer.fail(RuntimeError("boom"))


class _ConnFailingClient:
    """Minimal stub CTraderClient that is ready/account-authed but whose
    send_no_reply() always fails with a bare connection-level error --
    e.g. what CTraderClient.send_no_reply() now produces itself when no
    connected transport is available within its bounded wait (see
    ctrader/client.py:SEND_HANDOFF_TIMEOUT_S), or when whenConnected()
    fails outright. For exercising send_for_account's reclassification of
    that failure into SendNotAttempted (finding I1) without any real I/O."""

    def __init__(self, account_id):
        self.ready = defer.succeed(self)
        self._accounts = {account_id: "tok"}

    def send_no_reply(self, msg):
        return defer.fail(RuntimeError("no connected transport (mid-backoff)"))


class _UnauthedClient:
    """Stub CTraderClient that is ready and _accounts-registered but NOT
    yet account-authed on its CURRENT connection -- exactly the NEW-1
    window (right after a reconnect, before that connection's own
    ProtoOAAccountAuthReq round trip completes). send_no_reply raises if
    ever called, so a test using this proves the gate short-circuits
    BEFORE anything reaches the transport, not merely that the eventual
    send fails."""

    def __init__(self, account_id):
        self.ready = defer.succeed(self)
        self._accounts = {account_id: "tok"}

    def is_account_authed(self, account_id):
        return False

    def send_no_reply(self, msg):
        raise AssertionError(
            "send_no_reply must never be called for an account not yet "
            "authed on the current connection"
        )


def _last_event(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute("SELECT category, severity, payload FROM events ORDER BY id DESC LIMIT 1").fetchone()
    category, severity, payload = row
    return category, severity, (payload if isinstance(payload, dict) else json.loads(payload))


def _written_json(request):
    return json.loads(b"".join(request.written))


@pytest_twisted.inlineCallbacks
def _wait_until(predicate, timeout=5.0, interval=0.02):
    """Poll `predicate()` on the real reactor until true or timeout."""
    waited = 0.0
    while not predicate():
        if waited >= timeout:
            raise AssertionError(f"condition not met within {timeout}s")
        d = defer.Deferred()
        real_reactor.callLater(interval, d.callback, None)
        yield d
        waited += interval


# ---------- Finding #1: main.py composition must not crash before reactor.run() ----------

class _FakeReactor:
    """Records callWhenRunning/listenTCP calls without ever invoking them --
    proves boot() composes and wires the reactor without requiring a real
    event loop or accidentally calling reactor.run()."""

    def __init__(self):
        self.callWhenRunning_calls = []
        self.listenTCP_calls = []

    def callWhenRunning(self, f):
        self.callWhenRunning_calls.append(f)

    def listenTCP(self, port, site, interface=""):
        self.listenTCP_calls.append({"port": port, "site": site, "interface": interface})
        return object()

    def seconds(self):
        import time
        return time.time()


def test_boot_composes_without_crashing_and_binds_all_interfaces(db, fernet_key):
    """Regression test for the exact crash the prior implementation shipped:
    _build_send_for_account(None) -> AttributeError before reactor.run(). Also
    covers finding #5: the control port must NOT be bound to 127.0.0.1 only,
    since inside Docker's bridge network other containers reach copier via
    the bridge interface, not loopback."""
    config = main.BootConfig(
        postgres_dsn=db, fernet_key=fernet_key, client_id="cid", client_secret="secret",
        demo_host="demo.example.invalid", live_host="live.example.invalid",
        ctrader_port=5035, shards=1,
    )
    fake_reactor = _FakeReactor()

    app = main.boot(config, fake_reactor)  # must not raise

    assert app is not None
    assert app.dispatcher is not None
    assert app.reconciler.dispatcher is app.dispatcher
    assert len(fake_reactor.listenTCP_calls) == 1
    call = fake_reactor.listenTCP_calls[0]
    assert call["port"] == main.CONTROL_PORT
    assert call["interface"] == "0.0.0.0"
    assert call["interface"] != "127.0.0.1"
    # startup + token-refresh loop + balance-refresh loop (N9) + resync
    # loop were scheduled, not run inline (no reactor loop here).
    assert len(fake_reactor.callWhenRunning_calls) == 4
    assert app.balance_refresh_call.interval is None   # not started until the reactor runs
    assert app.resync_call.interval is None            # not started until the reactor runs


def test_build_app_tolerates_zero_accounts_and_wires_dispatcher_before_app_exists(db, fernet_key):
    """build_app() must not require an already-constructed CopierApp to build
    send_for_account -- clients_by_account closes over repo/clients directly."""
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    factory = make_stub_client_factory()

    app = main.build_app(repo, token_store, factory, shards=1)

    assert app.clients == {}
    assert app.state_tracker is None
    assert app.master_account_id is None
    # dispatcher's send_for_account raises SendNotAttempted (not AttributeError!) for any account
    with pytest.raises(main.SendNotAttempted):
        app.dispatcher._send_for_account(999, object())


def test_send_for_account_reclassifies_connection_failure_as_send_not_attempted(repo):
    """Finding I1: a connection-level failure out of client.send_no_reply()
    (e.g. no transport within its bounded wait, or whenConnected() failing
    outright) must be reclassified as SendNotAttempted by send_for_account.
    protocol.send() is only ever invoked inside send_no_reply()'s success
    callback, so ANY failure it produces means the message never reached a
    transport, let alone the wire -- exactly SendNotAttempted's contract.
    Left as a bare failure, it would instead hit Dispatcher's
    ambiguous-failure branch and degrade the account on the very first
    transient blip rather than retrying at 1s/2s/4s
    (engine/dispatch.py:_on_send_failure)."""
    account_id = 100  # slave seeded by db_seeded/seed_db, is_live=False, shard 0

    clients = {False: {0: _ConnFailingClient(account_id)}}
    clients_by_account = main._build_clients_by_account(repo, clients, shards=1)
    send_for_account = main._build_send_for_account(clients_by_account)

    d = send_for_account(account_id, object())

    failures = []
    d.addErrback(failures.append)
    assert len(failures) == 1
    assert isinstance(failures[0].value, main.SendNotAttempted)
    assert "no connected transport" in str(failures[0].value)


def test_send_for_account_blocks_account_not_yet_authed_on_current_connection(repo):
    """NEW-1 guard: send_no_reply's instant=True write reaches the wire in
    the same reactor turn a connection is confirmed, with no FIFO queue
    left to serialize it behind that connection's own (still in-flight)
    account-auth request the way there incidentally was before instant=True
    -- so _accounts registry membership alone (proves authorize_account()
    was ever called, not that auth completed on THIS connection) is not
    enough to gate on. send_for_account must also check
    is_account_authed(), and must raise SendNotAttempted -- safe to retry
    -- without ever calling send_no_reply at all: _UnauthedClient's
    send_no_reply raises AssertionError if invoked, so this proves nothing
    reached the transport, not merely that the eventual send failed."""
    account_id = 100  # slave seeded by db_seeded/seed_db, is_live=False, shard 0

    clients = {False: {0: _UnauthedClient(account_id)}}
    clients_by_account = main._build_clients_by_account(repo, clients, shards=1)
    send_for_account = main._build_send_for_account(clients_by_account)

    with pytest.raises(main.SendNotAttempted) as exc_info:
        send_for_account(account_id, object())
    assert "not yet confirmed" in str(exc_info.value)


@pytest_twisted.inlineCallbacks
def test_startup_with_zero_accounts_does_not_crash(db, fernet_key):
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    yield app.startup()  # must complete without raising

    assert app.state_tracker is None


def test_build_app_wires_execution_and_invalidated_to_every_client_all_shards(db, fernet_key):
    """Finding #6: on_execution/on_tokens_invalidated must be wired to EVERY
    client (all shards, both environments) -- slave shards must deliver
    execution events too, not just the master's client."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)"
            " VALUES ('a', 'b', now(), now() + interval '30 days')"
        )
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)"
            " VALUES (10, 1, 1, false, 'slave', true, 1.0), (11, 1, 2, true, 'slave', true, 1.0)"
        )

    from unittest.mock import MagicMock
    built = []

    def factory(is_live):
        m = MagicMock()
        built.append(m)
        return m

    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    main.build_app(repo, token_store, factory, shards=2)

    # 2 environments (demo + live) x 2 shards = 4 clients, each wired exactly once
    assert len(built) == 4
    for client in built:
        assert client.on_execution.call_count == 1
        assert client.on_tokens_invalidated.call_count == 1


# ---------- /health ----------

def test_health_reports_settings(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    status = app.get_health()

    assert status == {"status": "ok", "master": 999, "copying_enabled": True, "dry_run": False}

    request = DummyRequest([b"health"])
    request.method = b"GET"
    HealthResource(app).render_GET(request)
    assert _written_json(request) == status


@pytest_twisted.inlineCallbacks
def test_health_reachable_over_real_network_socket(repo, token_store):
    """Finding #5: prove /health actually answers over a real TCP socket when
    bound to an unspecified/all-interfaces address (as main.boot() does),
    not just via in-process resource calls."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    site = make_control_site(app)
    port = real_reactor.listenTCP(0, site, interface="0.0.0.0")
    try:
        agent = Agent(real_reactor)
        url = f"http://127.0.0.1:{port.getHost().port}/health".encode()
        response = yield agent.request(b"GET", url)
        body = yield readBody(response)
        assert response.code == 200
        assert json.loads(body)["status"] == "ok"
    finally:
        yield port.stopListening()


# ---------- pause / resume ----------

@pytest_twisted.inlineCallbacks
def test_pause_global_flips_kill_switch_and_logs_control_event(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    yield app.pause(account_id=None)

    assert repo.get_settings().copying_enabled is False
    category, severity, payload = _last_event(repo.dsn)
    assert (category, payload["action"]) in (("control", "reload"), ("control", "pause_global"))
    # both events must be present: the pause itself, and the reload it triggers
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute("SELECT payload->>'action' FROM events WHERE category = 'control'").fetchall()
    actions = {r[0] for r in rows}
    assert "pause_global" in actions
    assert "reload" in actions


@pytest_twisted.inlineCallbacks
def test_pause_single_slave_sets_paused_status(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    yield app.pause(account_id=100)

    account_100 = next(a for a in repo.load_accounts() if a.account_id == 100)
    assert account_100.status == "paused"
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT payload FROM events WHERE category='control' AND payload->>'action'='pause_slave'"
        ).fetchone()
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    assert payload["account_id"] == 100


@pytest_twisted.inlineCallbacks
def test_pause_single_slave_suppresses_it_and_resume_reenables_it(repo, token_store):
    """Pause/resume must have REAL effect on copying (finding #3/#7), not just
    flip a cosmetic status column: the paused slave must drop out of
    slaves_provider()'s enabled set, and come back on resume."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    before = {s.account_id: s.enabled for s in app.service._slaves_provider()}
    assert before[100] is True and before[101] is True

    yield app.pause(account_id=100)
    after_pause = {s.account_id: s.enabled for s in app.service._slaves_provider()}
    assert after_pause[100] is False
    assert after_pause[101] is True  # untouched

    yield app.resume(account_id=100)
    after_resume = {s.account_id: s.enabled for s in app.service._slaves_provider()}
    assert after_resume[100] is True


@pytest_twisted.inlineCallbacks
def test_control_site_post_pause_and_resume_over_dummy_request(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    request = DummyRequest([b"pause"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"account_id": None}).encode())
    PauseResource(app).render_POST(request)
    yield _wait_until(lambda: repo.get_settings().copying_enabled is False)
    assert _written_json(request)["status"] == "paused"

    request = DummyRequest([b"resume"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"account_id": None}).encode())
    ResumeResource(app).render_POST(request)
    yield _wait_until(lambda: repo.get_settings().copying_enabled is True)
    assert _written_json(request)["status"] == "resumed"


# ---------- dry-run ----------

def test_dry_run_toggle_persists(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    app.set_dry_run(True)
    assert repo.get_settings().dry_run is True

    request = DummyRequest([b"dry-run"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"enabled": False}).encode())
    DryRunResource(app).render_POST(request)
    assert repo.get_settings().dry_run is False
    assert _written_json(request) == {"status": "ok", "dry_run": False}


# ---------- /state ----------

def test_get_state_includes_master_positions_with_copies_pending_orders_and_drift(repo, token_store):
    from copier.domain.models import Side
    from copier.engine.reconcile import PositionSnapshot, OrderSnapshot, DriftItem

    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    assert app.state_tracker is not None  # master (999) exists

    repo.create_position_mapping(master_position_id=42, slave_account_id=100, client_order_id="cm42.100")
    repo.activate_position_mapping("cm42.100", slave_position_id=5001, slave_volume=100_000)

    app.reconciler.master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY, volume=100_000, price=1.1, label="copy:m42")
    ]
    app.reconciler.master_orders = [
        OrderSnapshot(order_id=7, symbol_id=1, volume=50_000, label="pending")
    ]
    app.reconciler.current = [
        DriftItem(id="abc123", kind="unmapped_master_position", account_id=None, position_id=99, order_id=None, detail="oops")
    ]

    state = app.get_state()

    assert "accounts" in state
    assert len(state["master_positions"]) == 1
    mp = state["master_positions"][0]
    assert mp["position_id"] == 42
    assert len(mp["copies"]) == 1
    assert mp["copies"][0]["slave_account_id"] == 100
    assert mp["copies"][0]["slave_position_id"] == 5001
    assert mp["copies"][0]["status"] == "active"

    assert len(state["pending_orders"]) == 1
    assert state["pending_orders"][0]["order_id"] == 7

    assert state["drift"] == [
        {"id": "abc123", "kind": "unmapped_master_position", "account_id": None,
         "position_id": 99, "order_id": None, "detail": "oops"}
    ]

    request = DummyRequest([b"state"])
    request.method = b"GET"
    StateResource(app).render_GET(request)
    assert _written_json(request)["master_positions"][0]["position_id"] == 42


# ---------- drift routes never fake success ----------

def test_drift_close_orphan_on_unknown_id_reports_error_not_fake_success(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    request = DummyRequest([b"drift", b"close-orphan"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"id": "does-not-exist"}).encode())
    DriftCloseOrphanResource(app).render_POST(request)

    # Unknown id is the caller's error: ValueError now maps to 400 (see
    # _JsonResource._on_error) so the api proxy can forward the detail
    # instead of collapsing it into an opaque 502.
    assert request.responseCode == 400
    body = _written_json(request)
    assert "error" in body
    assert body.get("status") != "closed"


# ---------- token refresh ----------

@pytest_twisted.inlineCallbacks
def test_refresh_due_tokens_rotates_and_persists(db_seeded, fernet_key):
    """Drives the real _refresh_token loop end to end against FakeCTraderServer
    (not token_store.rotate() called directly): a due connection gets
    ProtoOARefreshTokenReq'd, and the scripted ("a2", "r2") response is what
    ends up persisted."""
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        conn.execute("UPDATE ctid_connections SET expires_at = now() + interval '10 days' WHERE id = 1")

    srv = FakeCTraderServer(auto_fill=True)
    srv.accounts = {999: "token_access", 100: "token_access", 101: "token_access"}
    srv.next_tokens = ("a2", "r2")
    port = srv.listen(real_reactor)

    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    created = []

    def factory(is_live):
        client = CTraderClient(Client("127.0.0.1", port, TcpProtocol), "test-cid", "test-secret")
        created.append(client)
        return client

    app = main.build_app(repo, token_store, factory, shards=1)
    try:
        yield app.startup()  # connects + authorizes, so a ready client exists

        yield app.refresh_due_tokens()

        new_pair = token_store.get(1)
        assert new_pair.access_token == "a2"
        assert new_pair.refresh_token == "r2"
        assert new_pair.status == "active"
    finally:
        for c in created:
            c.stop()
        srv.shutdown()


@pytest_twisted.inlineCallbacks
def test_refresh_failure_marks_and_alerts(db_seeded, fernet_key):
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        conn.execute("UPDATE ctid_connections SET expires_at = now() + interval '10 days' WHERE id = 1")

    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, lambda is_live: _FailingClient(), shards=1)

    yield app.refresh_due_tokens()

    pair = token_store.get(1)
    assert pair.status == "refresh_failed"

    category, severity, payload = _last_event(db_seeded)
    assert category == "auth"
    assert severity == "error"
    assert payload["action"] == "token_refresh_failed"
    assert payload["connection_id"] == 1


@pytest_twisted.inlineCallbacks
def test_refresh_due_tokens_loop_body_survives_a_query_exception(db_seeded, fernet_key):
    """Finding #2: the LoopingCall body must be exception-guarded -- a raise
    inside must never kill the loop."""
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    def boom(_now):
        raise RuntimeError("DB is down")

    token_store.due_for_refresh = boom  # simulate a transient DB failure

    d = app.refresh_due_tokens()
    yield d  # must resolve (not errback) despite the exception inside

    clock = Clock()
    loop = task.LoopingCall(app.refresh_due_tokens)
    loop.clock = clock
    start_d = loop.start(main.TOKEN_REFRESH_INTERVAL_S, now=True)
    errors = []
    start_d.addErrback(errors.append)
    clock.advance(main.TOKEN_REFRESH_INTERVAL_S * 3)
    assert loop.running  # never stopped despite the guaranteed exception each tick
    assert errors == []


@pytest_twisted.inlineCallbacks
def test_tokens_invalidated_event_triggers_refresh(db_seeded, fernet_key):
    """Firing ProtoOAAccountsTokenInvalidatedEvent must trigger an immediate
    refresh_due_tokens() call, not wait for the daily LoopingCall."""
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        conn.execute("UPDATE ctid_connections SET expires_at = now() + interval '10 days' WHERE id = 1")

    srv = FakeCTraderServer(auto_fill=True)
    srv.accounts = {999: "token_access", 100: "token_access", 101: "token_access"}
    srv.next_tokens = ("invalidated-a2", "invalidated-r2")
    port = srv.listen(real_reactor)

    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    created = []

    def factory(is_live):
        client = CTraderClient(Client("127.0.0.1", port, TcpProtocol), "test-cid", "test-secret")
        created.append(client)
        return client

    app = main.build_app(repo, token_store, factory, shards=1)
    try:
        yield app.startup()

        assert token_store.get(1).refresh_token != "invalidated-r2"  # not refreshed yet

        evt = ProtoOAAccountsTokenInvalidatedEvent()
        evt.ctidTraderAccountIds.extend([999])
        srv.broadcast(evt)  # real wire delivery -> on_tokens_invalidated -> refresh_due_tokens()

        yield _wait_until(lambda: token_store.get(1).refresh_token == "invalidated-r2")
    finally:
        for c in created:
            c.stop()
        srv.shutdown()


# ---------- discovery ----------

@pytest_twisted.inlineCallbacks
def test_discover_upserts_accounts_from_token(db, fernet_key):
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)"
            " VALUES (%s, %s, now(), now() + interval '30 days')",
            (fernet.encrypt(b"discover-token").decode(), fernet.encrypt(b"refresh").decode()),
        )

    srv = FakeCTraderServer(auto_fill=True)
    srv.accounts = {5001: "discover-token"}
    port = srv.listen(real_reactor)

    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    created = []

    def factory(is_live):
        client = CTraderClient(Client("127.0.0.1", port, TcpProtocol), "test-cid", "test-secret")
        created.append(client)
        return client

    app = main.build_app(repo, token_store, factory, shards=1)  # zero accounts yet -> no clients built
    assert app.clients == {}
    try:
        discovered = yield app.discover(connection_id=1)

        assert [a.ctidTraderAccountId for a in discovered] == [5001]
        accounts = repo.load_accounts()
        account_5001 = next(a for a in accounts if a.account_id == 5001)
        assert account_5001.connection_id == 1
        assert account_5001.is_live is False
    finally:
        for c in created:
            c.stop()
        srv.shutdown()


# ---------- N9: balances are refreshed after boot ----------

def _seed_symbol_cache(repo, account_ids, name="EURUSD", symbol_id=1, lot_size=10_000_000):
    from copier.domain.models import SymbolInfo
    info = SymbolInfo(symbol_id=symbol_id, name=name, digits=5, lot_size=lot_size,
                      min_volume=100_000, step_volume=100_000)
    for account_id in account_ids:
        repo.save_symbol_cache(account_id, {name: info})
    return info


class _RecordingStateTracker:
    """Stands in for AccountStateTracker, recording refresh_balances calls."""

    def __init__(self):
        self.refresh_calls = []

    def refresh_balances(self, account_ids):
        self.refresh_calls.append(list(account_ids))
        return defer.succeed(None)

    def snapshot(self):
        return {}

    def set_positions(self, account_id, positions):
        pass

    def ensure_spot_subscriptions(self):
        return defer.succeed(None)


def test_boot_schedules_a_periodic_balance_refresh(db, fernet_key):
    """N9: refresh_balances was called ONLY from startup(), so Overview's
    balance/equity went stale within hours of live trading (balance changes
    on every realized close) while open P&L kept moving, which made the
    stale numbers look plausible rather than obviously frozen.

    Drives the real LoopingCall boot() wires, on a Clock: advancing past the
    interval must produce refreshes, and they must keep coming.
    """
    seed_db(db, fernet_key)
    config = main.BootConfig(
        postgres_dsn=db, fernet_key=fernet_key, client_id="cid", client_secret="secret",
        demo_host="demo.example.invalid", live_host="live.example.invalid",
        ctrader_port=5035, shards=1,
    )
    fake_reactor = _FakeReactor()
    app = main.boot(config, fake_reactor)

    tracker = _RecordingStateTracker()
    app.state_tracker = tracker

    clock = Clock()
    app.balance_refresh_call.clock = clock
    app.balance_refresh_call.start(main.BALANCE_REFRESH_INTERVAL_S, now=False)
    try:
        assert tracker.refresh_calls == []          # nothing before the first tick

        clock.advance(main.BALANCE_REFRESH_INTERVAL_S)
        assert len(tracker.refresh_calls) == 1
        # Every ENABLED account (master 999 + slaves 100/101), and nothing else.
        assert sorted(tracker.refresh_calls[0]) == [100, 101, 999]

        clock.advance(main.BALANCE_REFRESH_INTERVAL_S)
        assert len(tracker.refresh_calls) == 2
    finally:
        app.balance_refresh_call.stop()


def test_balance_refresh_survives_a_broker_failure_and_keeps_looping(db, fernet_key):
    """A LoopingCall whose Deferred fails stops looping forever, so one
    transient broker hiccup would otherwise end balance refreshing for the
    life of the process."""
    seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    class _ExplodingTracker(_RecordingStateTracker):
        def refresh_balances(self, account_ids):
            self.refresh_calls.append(list(account_ids))
            return defer.fail(RuntimeError("broker said no"))

    tracker = _ExplodingTracker()
    app.state_tracker = tracker

    d = app.refresh_balances()
    results = []
    d.addBoth(results.append)

    assert len(results) == 1
    assert not isinstance(results[0], Failure), "refresh_balances must never errback"
    assert len(tracker.refresh_calls) == 1

    # And it can be called again afterwards.
    app.refresh_balances()
    assert len(tracker.refresh_calls) == 2


def test_boot_schedules_a_periodic_resync(db, fernet_key):
    """Positions and drift read reconciler state, which only resync()
    refreshes -- so a position the master opened after boot stayed
    invisible on the Positions page until an operator clicked resync (a
    live Stage-1 trade sat exactly like that). boot() must wire a resync
    loop the same way it wires the balance loop.

    Drives the real LoopingCall boot() wires, on a Clock.
    """
    seed_db(db, fernet_key)
    config = main.BootConfig(
        postgres_dsn=db, fernet_key=fernet_key, client_id="cid", client_secret="secret",
        demo_host="demo.example.invalid", live_host="live.example.invalid",
        ctrader_port=5035, shards=1,
    )
    fake_reactor = _FakeReactor()
    app = main.boot(config, fake_reactor)

    calls = []

    def recording_resync():
        calls.append(1)
        return defer.succeed([])

    app.resync = recording_resync
    app.master_account_id = 999

    clock = Clock()
    app.resync_call.clock = clock
    app.resync_call.start(main.RESYNC_INTERVAL_S, now=False)
    try:
        assert calls == []                    # nothing before the first tick
        clock.advance(main.RESYNC_INTERVAL_S)
        assert len(calls) == 1
        clock.advance(main.RESYNC_INTERVAL_S)
        assert len(calls) == 2
    finally:
        app.resync_call.stop()


def test_periodic_resync_skips_overlap_no_master_and_survives_failure(db, fernet_key):
    """periodic_resync must (a) no-op without a master, (b) skip a tick
    while a previous resync's broker fan-out is still in flight rather
    than stacking a second one on top, (c) resume after completion, and
    (d) never errback on failure (a LoopingCall whose Deferred fails
    stops looping forever) while still clearing the in-flight flag."""
    seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    calls = []
    pending = defer.Deferred()

    def slow_resync():
        calls.append(1)
        return pending

    app.resync = slow_resync

    # (a) no master -> no-op
    app.master_account_id = None
    app.periodic_resync()
    assert calls == []

    # normal tick runs
    app.master_account_id = 999
    app.periodic_resync()
    assert len(calls) == 1

    # (b) still in flight -> skipped
    app.periodic_resync()
    assert len(calls) == 1

    # (c) completes -> next tick runs again
    pending.callback([])
    app.periodic_resync()
    assert len(calls) == 2

    # (d) failure clears the flag and never errbacks
    def exploding_resync():
        calls.append(1)
        return defer.fail(RuntimeError("broker said no"))

    app.resync = exploding_resync
    d = app.periodic_resync()
    results = []
    d.addBoth(results.append)
    assert len(results) == 1
    assert not isinstance(results[0], Failure), "periodic_resync must never errback"
    assert len(calls) == 3
    app.periodic_resync()                     # and it can run again afterwards
    assert len(calls) == 4


def test_request_resync_debounces_a_burst_into_one_resync(db, fernet_key):
    """request_resync collapses the burst a single trade produces (master
    fill + one fill per slave) into ONE near-immediate resync, and a later
    event schedules a fresh one."""
    seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    clock = Clock()
    app.clock = clock
    calls = []
    app.periodic_resync = lambda: calls.append(1)

    app.request_resync()
    app.request_resync()
    app.request_resync()
    assert calls == []                     # debounce window still open
    clock.advance(main.RESYNC_DEBOUNCE_S)
    assert calls == [1]                    # one resync for the whole burst

    app.request_resync()
    clock.advance(main.RESYNC_DEBOUNCE_S)
    assert calls == [1, 1]                 # a later event fires again


def test_build_app_wires_the_position_change_hook(db, fernet_key):
    """The service's on_positions_changed must be attached to
    app.request_resync (service is built before the app, so the wiring
    happens post-construction and is easy to lose)."""
    seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    assert app.service.on_positions_changed == app.request_resync


def test_balance_refresh_skips_disabled_accounts(db, fernet_key):
    seed_db(db, fernet_key)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET enabled = false WHERE ctid_trader_account_id = 101")

    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)
    tracker = _RecordingStateTracker()
    app.state_tracker = tracker

    app.refresh_balances()

    assert sorted(tracker.refresh_calls[0]) == [100, 999]


# ---------- T9c: /state carries the fields the Positions screen renders ----------

def test_get_state_enriches_copies_and_master_rows_for_the_positions_screen(repo, token_store):
    """T9c: GET /state used to carry only ids/volume/status/error per copy
    and no symbol/lots/P&L on master rows, so the dashboard's Positions
    screen rendered a literal "-" for every Fill Price and Slippage,
    `ID:<n>` instead of a symbol name, and raw protocol volume instead of
    lots -- while spec §7 names fill price and slippage as Positions-screen
    content and Stage 2's "verify multipliers produce expected volumes"
    depends on the lots being readable.
    """
    from copier.domain.models import Side
    from copier.engine.reconcile import PositionSnapshot, OrderSnapshot

    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [999, 100])
    app.master_symbols_by_id[symbol.symbol_id] = symbol

    repo.create_position_mapping(master_position_id=42, slave_account_id=100,
                                 client_order_id="cm42.100")
    repo.activate_position_mapping("cm42.100", slave_position_id=5001,
                                   slave_volume=5_000_000, fill_price=1.10537)

    app.reconciler.master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=10_000_000, price=1.10500, label="copy:m42")
    ]
    app.reconciler.master_orders = [
        OrderSnapshot(order_id=7, symbol_id=1, volume=2_500_000, label="copy:o7")
    ]
    # A live per-position P&L, exactly as the state tracker reports it.
    app.state_tracker.set_positions(999, [
        main.StatePositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                                   volume=10_000_000, price=1.10500, label="copy:m42")
    ])
    app.state_tracker._spots[1] = (1.10600, 1.10620)

    state = app.get_state()

    master_position = state["master_positions"][0]
    assert master_position["symbol"] == "EURUSD"          # not "ID:1"
    assert master_position["volume_lots"] == "1.00"       # not 10000000
    assert master_position["pnl_quote"] == pytest.approx(100.0)

    copy = master_position["copies"][0]
    assert copy["fill_price"] == pytest.approx(1.10537)   # not None -> not "-"
    assert copy["volume_lots"] == "0.50"                  # the slave's own lots
    assert copy["slave_position_id"] == 5001
    assert copy["status"] == "active"

    pending_order = state["pending_orders"][0]
    assert pending_order["symbol"] == "EURUSD"
    assert pending_order["volume_lots"] == "0.25"

    # ...and it survives the real control route, which is what the api proxies.
    request = DummyRequest([b"state"])
    request.method = b"GET"
    StateResource(app).render_GET(request)
    rendered = _written_json(request)["master_positions"][0]
    assert rendered["symbol"] == "EURUSD"
    assert rendered["copies"][0]["fill_price"] == pytest.approx(1.10537)


def test_get_state_reports_null_enrichment_rather_than_inventing_values(repo, token_store):
    """A still-pending copy has no fill price and no volume; an unknown
    symbol has no name. Those must come back as null, so the dashboard shows
    "-" honestly, rather than as a fabricated number."""
    from copier.domain.models import Side
    from copier.engine.reconcile import PositionSnapshot

    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    repo.create_position_mapping(master_position_id=42, slave_account_id=100,
                                 client_order_id="cm42.100")
    app.reconciler.master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=10_000_000, price=1.105, label="copy:m42")
    ]

    master_position = app.get_state()["master_positions"][0]

    assert master_position["symbol"] is None          # symbol map is empty
    assert master_position["volume_lots"] is None
    assert master_position["pnl_quote"] is None
    assert master_position["copies"][0]["fill_price"] is None
    assert master_position["copies"][0]["volume_lots"] is None
