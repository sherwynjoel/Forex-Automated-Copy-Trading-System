"""Tests for the composition root and every CopierApp operation (main.py).

Every test here drives the REAL CopierApp produced by `copier.main` -- there
is no hand-duplicated stub app. Clients are either a lightweight StubSdk-backed
CTraderClient (synchronous, no real socket) or a real CTraderClient talking to
FakeCTraderServer over a real socket on the real reactor, matching the patterns
already used by tests/unit/test_client.py, tests/unit/test_state.py and
tests/unit/test_reconcile.py.

Multi-org: CopierApp runs one engine (reconciler + state tracker + master
symbol map) PER ORG, and every control operation takes the org it acts on.
The isolation tests here are the executable proof that one tenant's pause,
dry-run, kill switch, state or discovery can never reach another tenant's
accounts -- `close_all` in particular is the kill switch, so its org boundary
is asserted against real broker traffic, not just return values.

The HTTP control-endpoint tests live in test_control.py, which imports this
module's fixtures and harness.
"""

import json

import psycopg
import pytest
import pytest_twisted
from cryptography.fernet import Fernet
from ctrader_open_api import Client, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountsTokenInvalidatedEvent, ProtoOAClosePositionReq)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from twisted.internet import defer, reactor as real_reactor, task
from twisted.internet.task import Clock
from twisted.python.failure import Failure

import copier.main as main
from copier.ctrader.client import CTraderClient
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.domain.models import Side, SymbolInfo
from copier.engine.backfill import WEEK_MS
from copier.engine.reconcile import DriftItem, OrderSnapshot, PositionSnapshot
from copier.testing.fake_server import FakeCTraderServer
from test_client import StubSdk

# ---------- the two-tenant test world ----------

# Both orgs are seeded into a freshly TRUNCATE ... RESTART IDENTITY'd database
# (the `db` fixture), so their ids are deterministic.
ORG_A = 1
ORG_B = 2
MASTER_A, SLAVE_A1, SLAVE_A2 = 999, 100, 101
MASTER_B, SLAVE_B1 = 888, 200
TOKEN_A = "token_access"
TOKEN_B = "token_access_b"


def _insert_org(conn, name: str) -> int:
    (org_id,) = conn.execute(
        "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (name,)).fetchone()
    return org_id


def _insert_connection(conn, org_id: int, fernet, access_token: str,
                       expires_in_days: int = 30) -> int:
    (connection_id,) = conn.execute(
        """
        INSERT INTO ctid_connections
            (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
        VALUES (%s, %s, %s, now(), now() + %s * interval '1 day')
        RETURNING id
        """,
        (org_id, fernet.encrypt(access_token.encode()).decode(),
         fernet.encrypt(b"token_refresh").decode(), expires_in_days),
    ).fetchone()
    return connection_id


def seed_db(db, fernet_key, expires_in_days=30):
    """Seed org A: one connection and three accounts (999 master, 100/101 slaves).

    Returns the org id (ORG_A in a truncated database).
    """
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        org_id = _insert_org(conn, "Org A")
        connection_id = _insert_connection(conn, org_id, fernet, TOKEN_A, expires_in_days)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                  trader_login, is_live, role, enabled, multiplier)
            VALUES
                (%(master)s, %(org)s, %(conn)s, 99900, false, 'master', true, 1.0),
                (%(slave1)s, %(org)s, %(conn)s, 10000, false, 'slave', true, 1.0),
                (%(slave2)s, %(org)s, %(conn)s, 10001, false, 'slave', true, 1.5)
            """,
            {"master": MASTER_A, "slave1": SLAVE_A1, "slave2": SLAVE_A2,
             "org": org_id, "conn": connection_id},
        )
    return org_id


def seed_org_b(db, fernet_key, with_master=True):
    """Add org B (888 master, 200 slave) on its OWN connection and token."""
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        org_id = _insert_org(conn, "Org B")
        connection_id = _insert_connection(conn, org_id, fernet, TOKEN_B)
        if with_master:
            conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                      trader_login, is_live, role, enabled, multiplier)
                VALUES (%s, %s, %s, 88800, false, 'master', true, 1.0)
                """,
                (MASTER_B, org_id, connection_id),
            )
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                  trader_login, is_live, role, enabled, multiplier)
            VALUES (%s, %s, %s, 20000, false, 'slave', true, 1.0)
            """,
            (SLAVE_B1, org_id, connection_id),
        )
    return org_id


def seed_two_orgs(db, fernet_key):
    """The full two-tenant world: (org_a, org_b), each with its own master,
    slaves, connection and token."""
    return seed_db(db, fernet_key), seed_org_b(db, fernet_key)


# ---------- fixtures ----------

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


@pytest.fixture(autouse=True)
def _drain_boot_writers(monkeypatch):
    """Stop every AsyncWriter boot() starts, when the test that booted ends.

    boot() now attaches a real writer running on a daemon thread with its
    own connection. Left alive past the test, it can flush rows queued by
    THIS test into the next test's freshly truncated database -- a
    cross-test leak that shows up as an unexplained extra row somewhere
    unrelated. Production never needs this: the shutdown trigger boot()
    registers does the same job.
    """
    created = []
    real = main.AsyncWriter

    def factory(*args, **kwargs):
        writer = real(*args, **kwargs)
        created.append(writer)
        return writer

    monkeypatch.setattr(main, "AsyncWriter", factory)
    yield
    for writer in created:
        try:
            writer.flush_and_stop(timeout_s=2.0)
        except Exception:
            pass


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


def make_real_client_factory(port):
    """Client factory producing real CTraderClients wired to a
    FakeCTraderServer on `port`. Returns (factory, created) -- the caller must
    stop every client in `created` (heartbeat LoopingCall) at teardown."""
    created = []

    def factory(is_live):
        client = CTraderClient(Client("127.0.0.1", port, TcpProtocol),
                               "test-cid", "test-secret")
        created.append(client)
        return client

    return factory, created


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

    def on_disconnected(self, cb):
        pass

    def on_trader_updated(self, cb):
        pass

    def on_order_error(self, cb):
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


class _RecordingStateTracker:
    """Stands in for AccountStateTracker, recording refresh_balances calls."""

    def __init__(self):
        self.refresh_calls = []
        self.client_maps = []
        self.positions = {}

    def refresh_balances(self, account_ids, clients_by_account=None):
        self.refresh_calls.append(list(account_ids))
        self.client_maps.append(dict(clients_by_account or {}))
        return defer.succeed(None)

    def snapshot(self):
        return {}

    def set_positions(self, account_id, positions):
        self.positions[account_id] = list(positions)

    def ensure_spot_subscriptions(self):
        return defer.succeed(None)


class _FakeReactor:
    """Records callWhenRunning/listenTCP/addSystemEventTrigger calls without
    ever invoking them -- proves boot() composes and wires the reactor
    without requiring a real event loop or accidentally calling
    reactor.run()."""

    def __init__(self):
        self.callWhenRunning_calls = []
        self.listenTCP_calls = []
        self.addSystemEventTrigger_calls = []

    def callWhenRunning(self, f):
        self.callWhenRunning_calls.append(f)

    def listenTCP(self, port, site, interface=""):
        self.listenTCP_calls.append({"port": port, "site": site, "interface": interface})
        return object()

    def addSystemEventTrigger(self, phase, event_type, f, *args, **kwargs):
        self.addSystemEventTrigger_calls.append(
            {"phase": phase, "event_type": event_type, "f": f,
             "args": args, "kwargs": kwargs})
        return object()

    def seconds(self):
        import time
        return time.time()


# ---------- helpers ----------

def _last_event(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT category, severity, payload FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    category, severity, payload = row
    return category, severity, (payload if isinstance(payload, dict) else json.loads(payload))


def _events(dsn, action=None):
    """Every event row (optionally one action) as dicts, oldest first."""
    query = "SELECT category, severity, payload, org_id, account_id FROM events"
    params: tuple = ()
    if action is not None:
        query += " WHERE payload->>'action' = %s"
        params = (action,)
    query += " ORDER BY id"
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {"category": r[0], "severity": r[1],
         "payload": r[2] if isinstance(r[2], dict) else json.loads(r[2]),
         "org_id": r[3], "account_id": r[4]}
        for r in rows
    ]


def _actions_by_org(dsn) -> set:
    return {(e["payload"].get("action"), e["org_id"]) for e in _events(dsn)}


def _seed_symbol_cache(repo, account_ids, name="EURUSD", symbol_id=1, lot_size=10_000_000):
    info = SymbolInfo(symbol_id=symbol_id, name=name, digits=5, lot_size=lot_size,
                      min_volume=100_000, step_volume=100_000)
    for account_id in account_ids:
        repo.save_symbol_cache(account_id, {name: info})
    return info


def _seed_broker_position(server, account_id, position_id, volume=100_000, label=""):
    server.open_positions.setdefault(account_id, []).append({
        "position_id": position_id, "symbol_id": 1, "volume": volume,
        "trade_side": ProtoOATradeSide.BUY, "price": 1.10, "label": label,
    })
    server._position_volumes[position_id] = volume


def _closes_for(server, account_id):
    return [r for r in server.requests
            if isinstance(r, ProtoOAClosePositionReq)
            and r.ctidTraderAccountId == account_id]


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


# ---------- composition: main.py must not crash before reactor.run() ----------

def test_boot_composes_without_crashing_and_binds_all_interfaces(db, fernet_key):
    """Regression test for the exact crash the prior implementation shipped:
    _build_send_for_account(None) -> AttributeError before reactor.run(). Also
    covers finding #5: the control port must NOT be bound to 127.0.0.1 only,
    since inside Docker's bridge network other containers reach copier via
    the bridge interface, not loopback."""
    seed_db(db, fernet_key)
    config = main.BootConfig(
        postgres_dsn=db, fernet_key=fernet_key, client_id="cid", client_secret="secret",
        demo_host="demo.example.invalid", live_host="live.example.invalid",
        ctrader_port=5035, shards=1,
    )
    fake_reactor = _FakeReactor()

    app = main.boot(config, fake_reactor)  # must not raise

    assert app is not None
    assert app.dispatcher is not None
    assert app.reconcilers[ORG_A].dispatcher is app.dispatcher
    assert len(fake_reactor.listenTCP_calls) == 1
    call = fake_reactor.listenTCP_calls[0]
    assert call["port"] == main.CONTROL_PORT
    assert call["interface"] == "0.0.0.0"
    assert call["interface"] != "127.0.0.1"
    # startup + token-refresh loop + balance-refresh loop (N9) + resync
    # loop + cutoff-reminder loop + commission-refresh loop +
    # partition-maintenance loop + deal-backfill loop were scheduled, not
    # run inline (no reactor loop here).
    assert len(fake_reactor.callWhenRunning_calls) == 8
    assert app.balance_refresh_call.interval is None   # not started until the reactor runs
    assert app.resync_call.interval is None            # not started until the reactor runs
    assert app.cutoff_reminder_call.interval is None   # not started until the reactor runs
    # Learned commission is read on every amend, so the loop has to exist
    # from boot -- but it must not fire before the reactor is up either.
    assert app.commission_refresh_call.interval is None
    assert app.partition_call.interval is None
    assert app.deal_backfill_call.interval is None
    # The writer's drain is registered as a shutdown trigger, so a clean
    # restart loses nothing that was still queued.
    assert len(fake_reactor.addSystemEventTrigger_calls) == 1
    trigger = fake_reactor.addSystemEventTrigger_calls[0]
    assert trigger["phase"] == "before"
    assert trigger["event_type"] == "shutdown"
    assert trigger["f"] == app.writer.flush_and_stop


def test_build_app_tolerates_zero_accounts_and_wires_dispatcher_before_app_exists(db, fernet_key):
    """build_app() must not require an already-constructed CopierApp to build
    send_for_account -- clients_by_account closes over repo/clients directly."""
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    factory = make_stub_client_factory()

    app = main.build_app(repo, token_store, factory, shards=1)

    assert app.clients == {}
    assert app.state_trackers == {}
    assert app.reconcilers == {}
    assert app.master_symbols_by_org == {}
    # dispatcher's send_for_account raises SendNotAttempted (not AttributeError!) for any account
    with pytest.raises(main.SendNotAttempted):
        app.dispatcher._send_for_account(999, object())


def test_build_app_builds_one_engine_per_org_with_a_master(db, fernet_key):
    """Two orgs with masters -> two reconcilers, two state trackers, two
    symbol maps, each pinned to its own org and master."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)

    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    assert set(app.reconcilers) == {org_a, org_b}
    assert app.reconcilers[org_a].master_account_id == MASTER_A
    assert app.reconcilers[org_a].org_id == org_a
    assert app.reconcilers[org_b].master_account_id == MASTER_B
    assert app.reconcilers[org_b].org_id == org_b
    assert set(app.state_trackers) == {org_a, org_b}
    assert app.state_trackers[org_a]._master_account_id == MASTER_A
    assert app.state_trackers[org_b]._master_account_id == MASTER_B
    # The per-org symbol dict handed to the state tracker is the SAME object
    # the app refreshes on reload, so a reload is visible to the tracker.
    assert app.state_trackers[org_a]._symbols_by_id is app.master_symbols_by_org[org_a]
    # ... and the service sees the same outer dict.
    assert app.service._master_symbols_by_org is app.master_symbols_by_org


def test_routing_provider_caches_until_invalidated(db, fernet_key):
    """Routing is briefly cached for the hot path: repeat calls inside the
    TTL serve the SAME snapshot (zero database work per event), and the
    freshness contract is now reload()-shaped -- an enabled/status edit
    applies after invalidate() (which reload() calls), or on TTL expiry.
    The kill switch is NOT behind this cache; the dispatcher reads
    get_org() fresh on every dispatch."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    routing = app.routing_provider()
    assert routing.master_by_org == {org_a: MASTER_A, org_b: MASTER_B}
    assert {s.account_id for s in routing.slaves_by_org[org_a]} == {SLAVE_A1, SLAVE_A2}
    assert {s.account_id for s in routing.slaves_by_org[org_b]} == {SLAVE_B1}
    assert all(s.enabled for s in routing.slaves_by_org[org_a])

    # Inside the TTL the same snapshot object comes back -- the hot path
    # pays no database round trips.
    assert app.routing_provider() is routing

    repo.set_account_status(SLAVE_A1, 'paused')

    app.routing_provider.invalidate()  # exactly what reload() does
    refreshed = app.routing_provider()
    assert refreshed is not routing
    flags = {s.account_id: s.enabled for s in refreshed.slaves_by_org[org_a]}
    assert flags[SLAVE_A1] is False and flags[SLAVE_A2] is True


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
    account_id = SLAVE_A1  # seeded by db_seeded/seed_db, is_live=False, shard 0

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
    account_id = SLAVE_A1  # seeded by db_seeded/seed_db, is_live=False, shard 0

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

    assert app.state_trackers == {}


def test_build_app_wires_every_push_consumer_to_every_client_all_shards(db, fernet_key):
    """Finding #6, extended to the pushed-event consumers: EVERY consumer
    must be wired to EVERY client (all shards, both environments).

    Slave shards must deliver execution events too, not just the master's
    client -- and the same is true of the two push consumers added with the
    analytics batch. A client missing on_trader_updated shows stale balances
    for whatever accounts it carries; one missing on_margin_call silently
    never raises the dashboard's margin-call banner for them. Both failures
    are invisible until the moment they matter, which is why this asserts
    per client rather than "at least one was wired"."""
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        org_id = _insert_org(conn, "Org A")
        connection_id = _insert_connection(conn, org_id, fernet, TOKEN_A)
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, enabled, multiplier)"
            " VALUES (10, %(org)s, %(conn)s, 1, false, 'slave', true, 1.0),"
            "        (11, %(org)s, %(conn)s, 2, true, 'slave', true, 1.0)",
            {"org": org_id, "conn": connection_id},
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
        assert client.on_trader_updated.call_count == 1
        assert client.on_margin_call.call_count == 1


@pytest_twisted.inlineCallbacks
def test_reload_wires_every_push_consumer_to_a_new_environment_client(db, fernet_key):
    """The second client-creation path: reload() builds a client when an org
    acquires an account in an environment nothing was connected to yet.

    It must go through wire_client() like build_app does -- a client built
    here that missed on_trader_updated/on_margin_call would serve stale
    balances and swallow margin calls for every account on that environment,
    with nothing at boot to reveal it."""
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        org_id = _insert_org(conn, "Org A")
        connection_id = _insert_connection(conn, org_id, fernet, TOKEN_A)
        # Demo only at boot, so the live environment has no client yet.
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, enabled, multiplier)"
            " VALUES (10, %(org)s, %(conn)s, 1, false, 'master', true, 1.0)",
            {"org": org_id, "conn": connection_id},
        )

    from unittest.mock import MagicMock
    built = []

    def factory(is_live):
        client = MagicMock()
        client.ready = defer.succeed(client)
        client.authorize_account.return_value = defer.succeed(None)
        client.send.return_value = defer.succeed(None)
        built.append((is_live, client))
        return client

    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, factory, shards=1)
    assert [is_live for is_live, _ in built] == [False]  # demo only so far

    # A LIVE account appears; reload() must build and fully wire its client.
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, enabled, multiplier)"
            " VALUES (11, %(org)s, %(conn)s, 2, true, 'slave', true, 1.0)",
            {"org": org_id, "conn": connection_id},
        )

    yield app.reload()

    live_clients = [c for is_live, c in built if is_live]
    assert live_clients, "reload() did not build a client for the live environment"
    for client in live_clients:
        assert client.on_execution.call_count == 1
        assert client.on_tokens_invalidated.call_count == 1
        assert client.on_trader_updated.call_count == 1
        assert client.on_margin_call.call_count == 1


def test_build_app_wires_the_position_change_hook(db, fernet_key):
    """The service's on_positions_changed must be attached to
    app.request_resync (service is built before the app, so the wiring
    happens post-construction and is easy to lose)."""
    seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    assert app.service.on_positions_changed == app.request_resync


# ---------- /health ----------

def test_get_health_lists_all_orgs(db, fernet_key):
    """Health is a per-org table now: every org, its master (or None), and
    its own copying_enabled/dry_run -- never one global pair."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        org_c = _insert_org(conn, "Org C (no accounts)")
    repo.set_org_setting(org_b, "copying_enabled", False)
    repo.set_org_setting(org_b, "dry_run", True)

    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    health = app.get_health()

    assert health["status"] == "ok"
    by_org = {o["org_id"]: o for o in health["orgs"]}
    assert by_org[org_a] == {"org_id": org_a, "master": MASTER_A,
                             "copying_enabled": True, "dry_run": False}
    assert by_org[org_b] == {"org_id": org_b, "master": MASTER_B,
                             "copying_enabled": False, "dry_run": True}
    # An org with no accounts is still listed, with no master.
    assert by_org[org_c]["master"] is None
    assert set(by_org) == {org_a, org_b, org_c}


# ---------- pause / resume / dry-run ----------

@pytest_twisted.inlineCallbacks
def test_pause_and_dry_run_are_org_scoped(db, fernet_key):
    """pause(org_a) flips orgs.copying_enabled only for org A;
    set_dry_run(org_b, True) only for org B."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    yield app.pause(org_a)

    assert repo.get_org(org_a).copying_enabled is False
    assert repo.get_org(org_b).copying_enabled is True      # untouched

    app.set_dry_run(org_b, True)

    assert repo.get_org(org_b).dry_run is True
    assert repo.get_org(org_a).dry_run is False             # untouched

    app.set_dry_run(org_b, False)                           # ... and back off again

    assert repo.get_org(org_b).dry_run is False
    assert repo.get_org(org_a).dry_run is False             # still untouched

    yield app.resume(org_a)

    assert repo.get_org(org_a).copying_enabled is True
    assert repo.get_org(org_b).copying_enabled is True

    actions = _actions_by_org(repo.dsn)
    assert ("pause_org", org_a) in actions
    assert ("resume_org", org_a) in actions
    assert ("set_dry_run", org_b) in actions
    assert ("pause_org", org_b) not in actions


@pytest_twisted.inlineCallbacks
def test_pause_org_logs_a_control_event_and_reloads(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    yield app.pause(ORG_A)

    assert repo.get_org(ORG_A).copying_enabled is False
    # both events must be present: the pause itself, and the reload it triggers
    actions = {e["payload"]["action"] for e in _events(repo.dsn) if e["category"] == "control"}
    assert "pause_org" in actions
    assert "reload" in actions


@pytest_twisted.inlineCallbacks
def test_pause_single_slave_sets_paused_status(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    yield app.pause(ORG_A, account_id=SLAVE_A1)

    account = next(a for a in repo.load_accounts() if a.account_id == SLAVE_A1)
    assert account.status == "paused"
    events = [e for e in _events(repo.dsn, action="pause_slave")]
    assert len(events) == 1
    assert events[0]["payload"]["account_id"] == SLAVE_A1
    assert events[0]["org_id"] == ORG_A
    assert events[0]["account_id"] == SLAVE_A1


@pytest_twisted.inlineCallbacks
def test_pause_single_slave_suppresses_it_and_resume_reenables_it(repo, token_store):
    """Pause/resume must have REAL effect on copying (finding #3/#7), not just
    flip a cosmetic status column: the paused slave must drop out of its org's
    enabled slave fleet, and come back on resume."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    def enabled_flags():
        return {s.account_id: s.enabled
                for s in app.routing_provider().slaves_by_org[ORG_A]}

    before = enabled_flags()
    assert before[SLAVE_A1] is True and before[SLAVE_A2] is True

    yield app.pause(ORG_A, account_id=SLAVE_A1)
    after_pause = enabled_flags()
    assert after_pause[SLAVE_A1] is False
    assert after_pause[SLAVE_A2] is True  # untouched

    yield app.resume(ORG_A, account_id=SLAVE_A1)
    assert enabled_flags()[SLAVE_A1] is True


def test_pause_and_resume_reject_an_account_from_another_org(db, fernet_key):
    """A per-slave pause/resume is a tenant-scoped action: naming another
    org's account must fail loudly, not quietly pause it."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    with pytest.raises(ValueError):
        app.pause(org_b, account_id=SLAVE_A1)
    with pytest.raises(ValueError):
        app.resume(org_b, account_id=SLAVE_A1)
    with pytest.raises(ValueError):
        app.pause(org_a, account_id=4242)  # no such account anywhere

    assert next(a for a in repo.load_accounts()
                if a.account_id == SLAVE_A1).status == "ok"


def test_pause_resume_set_dry_run_reject_unknown_org(repo, token_store):
    """F3: same class of bug close_all was fixed for -- pause/resume/
    set_dry_run must fail loudly for an org that doesn't exist rather than
    silently no-op via a 0-row UPDATE."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    with pytest.raises(RuntimeError):
        app.pause(999999)
    with pytest.raises(RuntimeError):
        app.resume(999999)
    with pytest.raises(RuntimeError):
        app.set_dry_run(999999, True)

    # Nothing was written anywhere: the real org is untouched and no event
    # was logged for the unknown org.
    assert repo.get_org(ORG_A).copying_enabled is True
    assert repo.get_org(ORG_A).dry_run is False
    assert not any(org_id == 999999 for _, org_id in _actions_by_org(repo.dsn))


# ---------- kill switch ----------

@pytest_twisted.inlineCallbacks
def test_close_all_flattens_only_the_org(db, fernet_key, monkeypatch):
    """Two orgs, both with open positions on the fake broker. close_all(org_a)
    sends ProtoOAClosePositionReq only for org A's accounts. Copying is
    paused only INSIDE the call (so the master's closes cannot fan out as
    copy-closes mid-flatten) and restored before it returns: the button
    closes contracts, it does not stop the copier."""
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    org_a, org_b = seed_two_orgs(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A,
                       MASTER_B: TOKEN_B, SLAVE_B1: TOKEN_B}
    _seed_broker_position(server, MASTER_A, 7001)
    _seed_broker_position(server, SLAVE_A1, 7002)
    _seed_broker_position(server, MASTER_B, 7003)
    _seed_broker_position(server, SLAVE_B1, 7004)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        # The race guard itself: copying must be OFF while each account is
        # being flattened (delete the pause write and this fails).
        orig_flatten = app._flatten_account
        copying_during_flatten = []
        def spying_flatten(acct_id):
            copying_during_flatten.append(repo.get_org(org_a).copying_enabled)
            return orig_flatten(acct_id)
        monkeypatch.setattr(app, "_flatten_account", spying_flatten)

        result = yield app.close_all(org_a)

        assert result["paused"] is False
        assert {s["account_id"] for s in result["accounts"]} == {MASTER_A, SLAVE_A1, SLAVE_A2}
        assert copying_during_flatten and all(
            v is False for v in copying_during_flatten)

        # Copying survived the flatten on both orgs: the transient pause
        # that guards the fan-out race was restored before returning.
        assert repo.get_org(org_a).copying_enabled is True
        assert repo.get_org(org_b).copying_enabled is True

        yield _wait_until(lambda: len(_closes_for(server, MASTER_A)) == 1
                          and len(_closes_for(server, SLAVE_A1)) == 1)

        # Org A really was flattened on the broker (so the "still open"
        # assertions below are not vacuously true) ...
        yield _wait_until(lambda: server.open_positions[MASTER_A] == []
                          and server.open_positions[SLAVE_A1] == [])

        # ... while nothing was ever sent for org B, whose positions are
        # still open.
        assert _closes_for(server, MASTER_B) == []
        assert _closes_for(server, SLAVE_B1) == []
        assert [p["position_id"] for p in server.open_positions[MASTER_B]] == [7003]
        assert [p["position_id"] for p in server.open_positions[SLAVE_B1]] == [7004]

        kill = [e for e in _events(repo.dsn, action="kill_switch")]
        assert len(kill) == 1
        assert kill[0]["org_id"] == org_a
        assert kill[0]["payload"]["org_wide"] is True
    finally:
        for client in created:
            client.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_close_all_honors_a_concurrent_stop_during_the_flatten(db, fernet_key, monkeypatch):
    """An operator's explicit stop issued WHILE the flatten runs must
    survive the automatic restore. The api writes orgs directly in SQL, so
    the guard is DB-side: migration 009's trigger bumps settings_version on
    every org write and the restore is a compare-and-set that loses to any
    interim write -- even one re-asserting the same False value."""
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    org_a, org_b = seed_two_orgs(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A,
                       MASTER_B: TOKEN_B, SLAVE_B1: TOKEN_B}
    _seed_broker_position(server, MASTER_A, 7201)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        orig_flatten = app._flatten_account
        def stop_then_flatten(acct_id):
            # Mid-flatten STOP COPYING, exactly as the api issues it: a
            # direct settings write (same value the transient pause already
            # holds -- the version bump alone must defeat the restore).
            repo.set_org_setting(org_a, "copying_enabled", False)
            return orig_flatten(acct_id)
        monkeypatch.setattr(app, "_flatten_account", stop_then_flatten)

        result = yield app.close_all(org_a)

        assert result["paused"] is True
        assert repo.get_org(org_a).copying_enabled is False
        skipped = [e for e in _events(repo.dsn, action="close_all_restore_skipped")]
        assert len(skipped) == 1 and skipped[0]["org_id"] == org_a
    finally:
        for client in created:
            client.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_close_all_keeps_an_already_stopped_org_stopped(db, fernet_key, monkeypatch):
    """If the org's copying was already off before the button, close_all
    must not sneakily resume it: the restore puts back the PRIOR state, and
    the response says paused so the UI can tell the truth."""
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    org_a, org_b = seed_two_orgs(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A,
                       MASTER_B: TOKEN_B, SLAVE_B1: TOKEN_B}
    _seed_broker_position(server, MASTER_A, 7101)
    port = server.listen(real_reactor)

    repo = Repo(db)
    repo.set_org_setting(org_a, "copying_enabled", False)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        result = yield app.close_all(org_a)

        assert result["paused"] is True
        assert repo.get_org(org_a).copying_enabled is False
        yield _wait_until(lambda: server.open_positions[MASTER_A] == [])
    finally:
        for client in created:
            client.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_close_all_rejects_a_missing_or_null_org_before_touching_anything(db, fernet_key):
    """A kill switch must never report success having closed nothing.

    An unknown -- or None, which is what a mis-bound positional caller
    produces -- org would otherwise write no setting, match no target, and
    still answer {"status": "flattened", "paused": true, "accounts": []}.
    The org is resolved first, so any such call raises and NOTHING is
    written or sent.
    """
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    def _never(account_id):
        raise AssertionError(
            "close_all must validate the org before reaching any account")

    app._flatten_account = _never

    for bad_org in (None, 999999):
        with pytest.raises(RuntimeError):
            yield app.close_all(bad_org)
        with pytest.raises(RuntimeError):
            yield app.close_all(bad_org, account_id=SLAVE_A1)

    # No setting written, no kill-switch event, no broker traffic attempted.
    assert repo.get_org(org_a).copying_enabled is True
    assert _events(repo.dsn, action="kill_switch") == []
    assert _events(repo.dsn, action="kill_switch_flatten") == []


@pytest_twisted.inlineCallbacks
def test_close_all_for_one_account_rejects_another_orgs_account(db, fernet_key):
    """The single-account kill switch validates membership BEFORE flattening
    anything, and never touches any org's copying flag."""
    org_a, org_b = seed_two_orgs(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A,
                       MASTER_B: TOKEN_B, SLAVE_B1: TOKEN_B}
    _seed_broker_position(server, SLAVE_A1, 7001)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        with pytest.raises(ValueError):
            yield app.close_all(org_b, account_id=SLAVE_A1)

        assert _closes_for(server, SLAVE_A1) == []
        assert repo.get_org(org_a).copying_enabled is True
        assert repo.get_org(org_b).copying_enabled is True

        # ... and the account's own org can still flatten it, without pausing.
        result = yield app.close_all(org_a, account_id=SLAVE_A1)
        assert result["paused"] is False
        assert result["accounts"][0]["positions_closed"] == 1
        assert repo.get_org(org_a).copying_enabled is True
    finally:
        for client in created:
            client.stop()
        server.shutdown()


# ---------- /state ----------

def test_get_state_is_org_scoped(db, fernet_key):
    """get_state(org_a) surfaces only org A's mappings/positions/drift --
    proven with the SAME master position id live in both orgs, so an
    unfiltered mapping read would hand org A org B's copy."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    repo.create_position_mapping(master_position_id=42, slave_account_id=SLAVE_A1,
                                 client_order_id="cm42.100", org_id=org_a)
    repo.activate_position_mapping(SLAVE_A1, "cm42.100", slave_position_id=5001,
                                   slave_volume=100_000)
    repo.create_position_mapping(master_position_id=42, slave_account_id=SLAVE_B1,
                                 client_order_id="cm42.200", org_id=org_b)
    repo.activate_position_mapping(SLAVE_B1, "cm42.200", slave_position_id=6001,
                                   slave_volume=200_000)

    app.reconcilers[org_a].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=100_000, price=1.1, label="copy:m42")]
    app.reconcilers[org_b].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=200_000, price=1.2, label="copy:m42")]
    app.reconcilers[org_a].master_orders = [
        OrderSnapshot(order_id=7, symbol_id=1, volume=50_000, label="a-pending")]
    app.reconcilers[org_b].master_orders = [
        OrderSnapshot(order_id=8, symbol_id=1, volume=60_000, label="b-pending")]
    app.reconcilers[org_a].current = [
        DriftItem(id="drift-a", kind="unmapped_master_position", account_id=None,
                  position_id=99, order_id=None, detail="org a")]
    app.reconcilers[org_b].current = [
        DriftItem(id="drift-b", kind="unmapped_master_position", account_id=None,
                  position_id=98, order_id=None, detail="org b")]

    state_a = app.get_state(org_a)
    state_b = app.get_state(org_b)

    assert [p["volume"] for p in state_a["master_positions"]] == [100_000]
    assert [c["slave_account_id"] for c in state_a["master_positions"][0]["copies"]] == [SLAVE_A1]
    assert [o["order_id"] for o in state_a["pending_orders"]] == [7]
    assert [d["id"] for d in state_a["drift"]] == ["drift-a"]

    assert [p["volume"] for p in state_b["master_positions"]] == [200_000]
    assert [c["slave_account_id"] for c in state_b["master_positions"][0]["copies"]] == [SLAVE_B1]
    assert [o["order_id"] for o in state_b["pending_orders"]] == [8]
    assert [d["id"] for d in state_b["drift"]] == ["drift-b"]


def test_get_state_for_an_org_without_an_engine_is_empty_not_an_error(db, fernet_key):
    """An org with no master has no engine; /state for it must answer with
    empty lists rather than blowing up (or leaking another org's state)."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        org_c = _insert_org(conn, "Org C")
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    state = app.get_state(org_c)

    assert state == {"accounts": {}, "master_positions": [], "pending_orders": [], "drift": []}
    assert org_a in app.reconcilers and org_c not in app.reconcilers


def test_get_state_includes_master_positions_with_copies_pending_orders_and_drift(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    assert app.state_trackers[ORG_A] is not None  # master (999) exists

    repo.create_position_mapping(master_position_id=42, slave_account_id=SLAVE_A1,
                                 client_order_id="cm42.100", org_id=ORG_A)
    repo.activate_position_mapping(SLAVE_A1, "cm42.100", slave_position_id=5001,
                                   slave_volume=100_000)

    app.reconcilers[ORG_A].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY, volume=100_000,
                         price=1.1, label="copy:m42")
    ]
    app.reconcilers[ORG_A].master_orders = [
        OrderSnapshot(order_id=7, symbol_id=1, volume=50_000, label="pending")
    ]
    app.reconcilers[ORG_A].current = [
        DriftItem(id="abc123", kind="unmapped_master_position", account_id=None,
                  position_id=99, order_id=None, detail="oops")
    ]

    state = app.get_state(ORG_A)

    assert "accounts" in state
    assert len(state["master_positions"]) == 1
    mp = state["master_positions"][0]
    assert mp["position_id"] == 42
    assert len(mp["copies"]) == 1
    assert mp["copies"][0]["slave_account_id"] == SLAVE_A1
    assert mp["copies"][0]["slave_position_id"] == 5001
    assert mp["copies"][0]["status"] == "active"

    assert len(state["pending_orders"]) == 1
    assert state["pending_orders"][0]["order_id"] == 7

    assert state["drift"] == [
        {"id": "abc123", "kind": "unmapped_master_position", "account_id": None,
         "position_id": 99, "order_id": None, "detail": "oops"}
    ]


def test_get_state_enriches_copies_and_master_rows_for_the_positions_screen(repo, token_store):
    """T9c: GET /state used to carry only ids/volume/status/error per copy
    and no symbol/lots/P&L on master rows, so the dashboard's Positions
    screen rendered a literal "-" for every Fill Price and Slippage,
    `ID:<n>` instead of a symbol name, and raw protocol volume instead of
    lots -- while spec §7 names fill price and slippage as Positions-screen
    content and Stage 2's "verify multipliers produce expected volumes"
    depends on the lots being readable.
    """
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol

    repo.create_position_mapping(master_position_id=42, slave_account_id=SLAVE_A1,
                                 client_order_id="cm42.100", org_id=ORG_A)
    repo.activate_position_mapping(SLAVE_A1, "cm42.100", slave_position_id=5001,
                                   slave_volume=5_000_000, fill_price=1.10537)

    app.reconcilers[ORG_A].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=10_000_000, price=1.10500, label="copy:m42")
    ]
    app.reconcilers[ORG_A].master_orders = [
        OrderSnapshot(order_id=7, symbol_id=1, volume=2_500_000, label="copy:o7")
    ]
    # A live per-position P&L, exactly as the state tracker reports it.
    app.state_trackers[ORG_A].set_positions(MASTER_A, [
        main.StatePositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                                   volume=10_000_000, price=1.10500, label="copy:m42")
    ])
    app.state_trackers[ORG_A]._spots[1] = (1.10600, 1.10620)

    state = app.get_state(ORG_A)

    master_position = state["master_positions"][0]
    assert master_position["symbol"] == "EURUSD"          # not "ID:1"
    assert master_position["volume_lots"] == "1.00"       # not 10000000
    assert master_position["pnl_quote"] == pytest.approx(100.0)
    assert master_position["current_price"] == pytest.approx(1.10600)  # BUY marks at bid

    copy = master_position["copies"][0]
    assert copy["fill_price"] == pytest.approx(1.10537)   # not None -> not "-"
    assert copy["volume_lots"] == "0.50"                  # the slave's own lots
    assert copy["slave_position_id"] == 5001
    assert copy["status"] == "active"

    pending_order = state["pending_orders"][0]
    assert pending_order["symbol"] == "EURUSD"
    assert pending_order["volume_lots"] == "0.25"


def test_get_state_reports_null_enrichment_rather_than_inventing_values(repo, token_store):
    """A still-pending copy has no fill price and no volume; an unknown
    symbol has no name. Those must come back as null, so the dashboard shows
    "-" honestly, rather than as a fabricated number."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    repo.create_position_mapping(master_position_id=42, slave_account_id=SLAVE_A1,
                                 client_order_id="cm42.100", org_id=ORG_A)
    app.reconcilers[ORG_A].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=10_000_000, price=1.105, label="copy:m42")
    ]

    master_position = app.get_state(ORG_A)["master_positions"][0]

    assert master_position["symbol"] is None          # symbol map is empty
    assert master_position["volume_lots"] is None
    assert master_position["pnl_quote"] is None
    assert master_position["current_price"] is None
    assert master_position["copies"][0]["fill_price"] is None
    assert master_position["copies"][0]["volume_lots"] is None


# ---------- order rejections ----------

def test_order_rejections_become_visible_events(db, fernet_key):
    """A broker order rejection must land in the events log, org-resolved:
    the ws pushes it to the dashboard and the Logs page keeps it. A weekend
    MARKET_CLOSED order used to look exactly like success."""
    from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAOrderErrorEvent

    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    evt = ProtoOAOrderErrorEvent()
    evt.ctidTraderAccountId = MASTER_A
    evt.errorCode = 'MARKET_CLOSED'
    app._on_order_rejected(evt)

    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            """SELECT payload, account_id, org_id FROM events
               WHERE category = 'control' AND payload->>'action' = 'order_rejected'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    assert row is not None
    assert row[0]['error_code'] == 'MARKET_CLOSED'
    assert row[1] == MASTER_A
    assert row[2] == org_a


# ---------- get_quote ----------

def test_get_quote_returns_the_tracked_spot(repo, token_store):
    """The trade ticket polls get_quote for its selected symbol: the answer
    is the tracker's live (bid, ask) for that symbol."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    _seed_symbol_cache(repo, [MASTER_A])
    app.state_trackers[ORG_A]._spots[1] = (1.10600, 1.10620)

    quote = app.get_quote(MASTER_A, "EURUSD")

    assert quote == {"symbol": "EURUSD", "bid": 1.10600, "ask": 1.10620}


def test_get_quote_before_any_tick_returns_nulls_not_an_error(repo, token_store):
    """No tick yet (fresh subscription) answers null bid/ask — the dashboard
    shows an honest placeholder and polls again, rather than erroring."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    _seed_symbol_cache(repo, [MASTER_A])

    quote = app.get_quote(MASTER_A, "EURUSD")

    assert quote == {"symbol": "EURUSD", "bid": None, "ask": None}


def test_get_quote_rejects_unknown_symbols_and_accounts(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    _seed_symbol_cache(repo, [MASTER_A])

    with pytest.raises(ValueError):
        app.get_quote(MASTER_A, "NOPEUSD")
    with pytest.raises(ValueError):
        app.get_quote(424242, "EURUSD")


# ---------- reconciler_for / resync ----------

def test_reconciler_for_returns_the_orgs_engine_and_raises_without_one(db, fernet_key):
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        org_c = _insert_org(conn, "Org C")
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    assert app.reconciler_for(org_a) is app.reconcilers[org_a]
    with pytest.raises(ValueError):
        app.reconciler_for(org_c)
    with pytest.raises(ValueError):
        app.reconciler_for(4242)


@pytest_twisted.inlineCallbacks
def test_resync_runs_one_org_or_every_org(db, fernet_key):
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    ran = []

    def recorder(org_id, items):
        def _run():
            ran.append(org_id)
            return defer.succeed(items)
        return _run

    app.reconcilers[org_a].run = recorder(org_a, ["drift-a"])
    app.reconcilers[org_b].run = recorder(org_b, ["drift-b"])
    trackers = {org_a: _RecordingStateTracker(), org_b: _RecordingStateTracker()}
    app.state_trackers = trackers

    items = yield app.resync(org_a)

    assert ran == [org_a]
    assert items == ["drift-a"]
    assert trackers[org_b].positions == {}          # org B's engine untouched

    ran.clear()
    items = yield app.resync()

    assert sorted(ran) == sorted([org_a, org_b])
    assert sorted(items) == ["drift-a", "drift-b"]
    # Each org's master positions went into ITS OWN tracker.
    assert set(trackers[org_a].positions) == {MASTER_A}
    assert set(trackers[org_b].positions) == {MASTER_B}


@pytest_twisted.inlineCallbacks
def test_one_orgs_failing_resync_does_not_blind_the_others(db, fernet_key):
    """A sweep must isolate per-org failures.

    In production one org's accounts were disabled by its broker, so its
    reconcile answered with an error object. That raised inside the sweep
    and, because the loop awaited each org in turn with no guard, aborted
    the whole pass -- every org queued behind it stopped refreshing. The
    visible symptom was a Positions page that kept showing a trade the
    broker had already closed, because the only thing that clears it is a
    resync, and the periodic one had been dying every minute for hours.
    """
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    def exploding():
        raise AttributeError("'ProtoOAErrorRes' object has no attribute 'position'")

    healthy = []

    def healthy_run():
        healthy.append(org_b)
        return defer.succeed(["drift-b"])

    app.reconcilers[org_a].run = exploding
    app.reconcilers[org_b].run = healthy_run
    trackers = {org_a: _RecordingStateTracker(), org_b: _RecordingStateTracker()}
    app.state_trackers = trackers

    items = yield app.resync()          # must NOT raise

    assert healthy == [org_b], "the healthy org was skipped by its neighbour's failure"
    assert items == ["drift-b"]
    assert set(trackers[org_b].positions) == {MASTER_B}


@pytest_twisted.inlineCallbacks
def test_org_scoped_resync_still_surfaces_its_own_failure(db, fernet_key):
    """Isolation is for the SWEEP. When an operator resyncs one org by
    hand, a failure is theirs to see -- swallowing it would report success
    on a resync that did nothing."""
    org_a, _ = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    def exploding():
        raise AttributeError("boom")

    app.reconcilers[org_a].run = exploding

    try:
        yield app.resync(org_a)
    except AttributeError:
        pass
    else:
        raise AssertionError("an explicitly requested resync must not hide its failure")


@pytest_twisted.inlineCallbacks
def test_resync_feeds_slave_positions_into_the_state_tracker(db, fernet_key):
    """The slave tiles and /state read per-account positions from the state
    tracker -- but resync used to push ONLY the master's snapshot, so every
    slave reported positions: 0 / open_pnl: 0 forever, even while the same
    payload listed active copies on those very slaves (the broker held six
    open positions on a slave that the dashboard showed as flat)."""
    org_a, _org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    app.reconcilers[org_a].run = lambda: defer.succeed([])
    app.reconcilers[org_a].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                         volume=10_000_000, price=1.10500, label="copy:m42")
    ]
    app.reconcilers[org_a].slave_positions = {
        SLAVE_A1: [
            PositionSnapshot(position_id=5001, symbol_id=1, side=Side.BUY,
                             volume=5_000_000, price=1.10537, label="copy:m42")
        ],
        SLAVE_A2: [],
    }
    tracker = _RecordingStateTracker()
    app.state_trackers = {org_a: tracker}

    yield app.resync(org_a)

    assert set(tracker.positions) == {MASTER_A, SLAVE_A1, SLAVE_A2}
    assert [p.position_id for p in tracker.positions[SLAVE_A1]] == [5001]
    assert tracker.positions[SLAVE_A2] == []  # explicitly flat, not unknown


@pytest_twisted.inlineCallbacks
def test_org_scoped_resync_refreshes_only_that_orgs_balances(db, fernet_key):
    """resync() ends with a balance refresh so the operator sees current
    numbers -- but a resync in org A must not put a ProtoOATraderReq on org
    B's accounts. Beyond the tenancy leak, the unscoped sweep made every
    single-org resync cost broker round trips proportional to the WHOLE
    deployment (the SDK paces the wire at 5 msg/s), which is what pushed a
    two-org resync past the api proxy's own timeout."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    for org_id in (org_a, org_b):
        app.reconcilers[org_id].run = lambda: defer.succeed([])
    tracker_a, tracker_b = _RecordingStateTracker(), _RecordingStateTracker()
    app.state_trackers = {org_a: tracker_a, org_b: tracker_b}

    yield app.resync(org_a)

    assert sorted(tracker_a.refresh_calls[0]) == [SLAVE_A1, SLAVE_A2, MASTER_A]
    assert tracker_b.refresh_calls == [], "org B's accounts were queried by org A's resync"

    # The process-wide sweep (startup, the periodic loop) still covers both.
    yield app.resync()

    assert len(tracker_a.refresh_calls) == 2
    assert sorted(tracker_b.refresh_calls[0]) == [SLAVE_B1, MASTER_B]


# ---------- reload ----------

@pytest_twisted.inlineCallbacks
def test_reload_rebuilds_per_org_engines(db, fernet_key):
    """After giving org B a master via SQL and calling reload(), the app has
    reconcilers/state_trackers/master_symbols_by_org entries for both orgs;
    after deleting org B, reload() drops them."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1, SLAVE_A2])
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    assert set(app.reconcilers) == {org_a}
    assert set(app.state_trackers) == {org_a}

    org_b = seed_org_b(db, fernet_key)
    _seed_symbol_cache(repo, [MASTER_B, SLAVE_B1])

    yield app.reload()

    assert set(app.reconcilers) == {org_a, org_b}
    assert app.reconcilers[org_b].master_account_id == MASTER_B
    assert app.reconcilers[org_b].org_id == org_b
    assert set(app.state_trackers) == {org_a, org_b}
    assert app.state_trackers[org_b]._master_account_id == MASTER_B
    assert app.master_symbols_by_org[org_b] != {}
    assert app.reconcilers[org_a] is not app.reconcilers[org_b]

    reconciler_a = app.reconcilers[org_a]

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (org_b,))  # cascades to accounts

    yield app.reload()

    assert set(app.reconcilers) == {org_a}
    assert set(app.state_trackers) == {org_a}
    assert org_b not in app.master_symbols_by_org
    assert app.reconcilers[org_a] is reconciler_a       # org A's engine survives untouched


@pytest_twisted.inlineCallbacks
def test_reload_drops_the_engine_of_an_org_that_lost_its_master(db, fernet_key):
    """A master demoted to slave leaves the org without an engine: no
    reconciler, no tracker, no stale symbol map."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1, SLAVE_A2, MASTER_B, SLAVE_B1])
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)
    assert set(app.reconcilers) == {org_a, org_b}

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET role = 'slave' WHERE ctid_trader_account_id = %s",
                     (MASTER_B,))

    yield app.reload()

    assert set(app.reconcilers) == {org_a}
    assert set(app.state_trackers) == {org_a}
    assert org_b not in app.master_symbols_by_org


@pytest_twisted.inlineCallbacks
def test_reload_repoints_an_orgs_engine_at_a_new_master(db, fernet_key):
    """Promoting a different account keeps ONE engine for the org, repointed."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1, SLAVE_A2])
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET role = 'slave' WHERE ctid_trader_account_id = %s",
                     (MASTER_A,))
        conn.execute("UPDATE accounts SET role = 'master' WHERE ctid_trader_account_id = %s",
                     (SLAVE_A1,))

    yield app.reload()

    assert set(app.reconcilers) == {org_a}
    assert app.reconcilers[org_a].master_account_id == SLAVE_A1
    assert app.state_trackers[org_a]._master_account_id == SLAVE_A1
    # The promoted former slave's cached symbols become the org's master map,
    # even though nothing was re-fetched from the broker.
    assert app.master_symbols_by_org[org_a] != {}


# ---------- discovery ----------

@pytest_twisted.inlineCallbacks
def test_discover_upserts_accounts_into_the_connections_org(db, fernet_key):
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        org_id = _insert_org(conn, "Org A")
        connection_id = _insert_connection(conn, org_id, fernet, "discover-token")

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {5001: "discover-token"}
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)

    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    assert app.clients == {}  # zero accounts yet -> no clients built
    try:
        discovered = yield app.discover(connection_id=connection_id)

        assert [a.ctidTraderAccountId for a in discovered] == [5001]
        account = next(a for a in repo.load_accounts() if a.account_id == 5001)
        assert account.connection_id == connection_id
        assert account.org_id == org_id
        assert account.is_live is False

        events = _events(repo.dsn, action="discover")
        assert len(events) == 1
        assert events[0]["org_id"] == org_id
        assert events[0]["payload"]["conflicts"] == []
    finally:
        for client in created:
            client.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_discover_conflict_logs_and_skips(db, fernet_key):
    """Account already owned by org A; discovery on org B's connection
    upserts nothing (the row keeps org A) and logs an 'error' event with
    org B's org_id and action 'discover_conflict'."""
    fernet = Fernet(fernet_key.encode())
    with psycopg.connect(db, autocommit=True) as conn:
        org_a = _insert_org(conn, "Org A")
        conn_a = _insert_connection(conn, org_a, fernet, TOKEN_A)
        org_b = _insert_org(conn, "Org B")
        conn_b = _insert_connection(conn, org_b, fernet, TOKEN_B)
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live) VALUES (5001, %s, %s, 50010, false)",
            (org_a, conn_a),
        )

    # The same broker account is reachable with org B's grant too -- exactly
    # the situation the cross-org ownership guard exists for.
    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {5001: TOKEN_B}
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        discovered = yield app.discover(connection_id=conn_b)

        assert [a.ctidTraderAccountId for a in discovered] == [5001]

        # The row still belongs to org A, on org A's connection.
        account = next(a for a in repo.load_accounts() if a.account_id == 5001)
        assert account.org_id == org_a
        assert account.connection_id == conn_a

        conflicts = _events(repo.dsn, action="discover_conflict")
        assert len(conflicts) == 1
        assert conflicts[0]["severity"] == "error"
        assert conflicts[0]["category"] == "control"
        assert conflicts[0]["org_id"] == org_b
        assert conflicts[0]["account_id"] == 5001
        assert conflicts[0]["payload"]["account_id"] == 5001

        summary = _events(repo.dsn, action="discover")
        assert len(summary) == 1
        assert summary[0]["org_id"] == org_b
        assert summary[0]["payload"]["conflicts"] == [5001]
    finally:
        for client in created:
            client.stop()
        server.shutdown()


# ---------- token refresh ----------

@pytest_twisted.inlineCallbacks
def test_refresh_due_tokens_rotates_and_persists(db_seeded, fernet_key):
    """Drives the real _refresh_token loop end to end against FakeCTraderServer
    (not token_store.rotate() called directly): a due connection gets
    ProtoOARefreshTokenReq'd, and the scripted ("a2", "r2") response is what
    ends up persisted."""
    with psycopg.connect(db_seeded, autocommit=True) as conn:
        conn.execute("UPDATE ctid_connections SET expires_at = now() + interval '10 days' WHERE id = 1")

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    server.next_tokens = ("a2", "r2")
    port = server.listen(real_reactor)

    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    factory, created = make_real_client_factory(port)

    app = main.build_app(repo, token_store, factory, shards=1)
    try:
        yield app.startup()  # connects + authorizes, so a ready client exists

        yield app.refresh_due_tokens()

        new_pair = token_store.get(1)
        assert new_pair.access_token == "a2"
        assert new_pair.refresh_token == "r2"
        assert new_pair.status == "active"
    finally:
        for client in created:
            client.stop()
        server.shutdown()


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

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    server.next_tokens = ("invalidated-a2", "invalidated-r2")
    port = server.listen(real_reactor)

    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    factory, created = make_real_client_factory(port)

    app = main.build_app(repo, token_store, factory, shards=1)
    try:
        yield app.startup()

        assert token_store.get(1).refresh_token != "invalidated-r2"  # not refreshed yet

        evt = ProtoOAAccountsTokenInvalidatedEvent()
        evt.ctidTraderAccountIds.extend([MASTER_A])
        server.broadcast(evt)  # real wire delivery -> on_tokens_invalidated -> refresh_due_tokens()

        yield _wait_until(lambda: token_store.get(1).refresh_token == "invalidated-r2")
    finally:
        for client in created:
            client.stop()
        server.shutdown()


# ---------- N9: balances are refreshed after boot ----------

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
    app.state_trackers = {ORG_A: tracker}

    clock = Clock()
    app.balance_refresh_call.clock = clock
    app.balance_refresh_call.start(main.BALANCE_REFRESH_INTERVAL_S, now=False)
    try:
        assert tracker.refresh_calls == []          # nothing before the first tick

        clock.advance(main.BALANCE_REFRESH_INTERVAL_S)
        assert len(tracker.refresh_calls) == 1
        # Every ENABLED account (master 999 + slaves 100/101), and nothing else.
        assert sorted(tracker.refresh_calls[0]) == [SLAVE_A1, SLAVE_A2, MASTER_A]

        clock.advance(main.BALANCE_REFRESH_INTERVAL_S)
        assert len(tracker.refresh_calls) == 2
    finally:
        app.balance_refresh_call.stop()


def test_balance_refresh_routes_live_accounts_to_the_live_client(db, fernet_key):
    """Prod org 3: one LIVE account among demo accounts (48376002). The
    tracker used to push every ProtoOATraderReq down its master's (demo)
    client, so the demo server rejected the live account's request with
    INVALID_REQUEST twice a minute and its balance stayed unknown forever.
    refresh_balances must hand the tracker a per-account client map routed
    by (is_live, shard)."""
    seed_db(db, fernet_key)
    live_account = 555
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                   trader_login, is_live, role, enabled, multiplier)
               VALUES (%s, %s, 1, 55500, true, 'ignored', true, 1.0)""",
            (live_account, ORG_A))
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    tracker = _RecordingStateTracker()
    app.state_trackers = {ORG_A: tracker}

    app.refresh_balances()

    assert sorted(tracker.refresh_calls[0]) == [SLAVE_A1, SLAVE_A2, live_account, MASTER_A]
    clients_map = tracker.client_maps[0]
    assert clients_map[live_account] is app.clients[True][0]
    assert clients_map[MASTER_A] is app.clients[False][0]


def test_balance_refresh_is_per_org(db, fernet_key):
    """Each org's tracker refreshes ITS OWN accounts -- an account never
    reaches another org's broker session."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)

    tracker_a, tracker_b = _RecordingStateTracker(), _RecordingStateTracker()
    app.state_trackers = {org_a: tracker_a, org_b: tracker_b}

    app.refresh_balances()

    assert sorted(tracker_a.refresh_calls[0]) == [SLAVE_A1, SLAVE_A2, MASTER_A]
    assert sorted(tracker_b.refresh_calls[0]) == [SLAVE_B1, MASTER_B]


def test_balance_refresh_survives_a_broker_failure_and_keeps_looping(db, fernet_key):
    """A LoopingCall whose Deferred fails stops looping forever, so one
    transient broker hiccup would otherwise end balance refreshing for the
    life of the process."""
    seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    class _ExplodingTracker(_RecordingStateTracker):
        def refresh_balances(self, account_ids, clients_by_account=None):
            self.refresh_calls.append(list(account_ids))
            return defer.fail(RuntimeError("broker said no"))

    tracker = _ExplodingTracker()
    app.state_trackers = {ORG_A: tracker}

    d = app.refresh_balances()
    results = []
    d.addBoth(results.append)

    assert len(results) == 1
    assert not isinstance(results[0], Failure), "refresh_balances must never errback"
    assert len(tracker.refresh_calls) == 1

    # And it can be called again afterwards.
    app.refresh_balances()
    assert len(tracker.refresh_calls) == 2


def test_balance_refresh_skips_disabled_accounts(db, fernet_key):
    seed_db(db, fernet_key)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET enabled = false WHERE ctid_trader_account_id = %s",
                     (SLAVE_A2,))

    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key), make_stub_client_factory(), shards=1)
    tracker = _RecordingStateTracker()
    app.state_trackers = {ORG_A: tracker}

    app.refresh_balances()

    assert sorted(tracker.refresh_calls[0]) == [SLAVE_A1, MASTER_A]


# ---------- periodic resync ----------

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


def test_periodic_resync_skips_overlap_no_engine_and_survives_failure(db, fernet_key):
    """periodic_resync must (a) no-op when no org has an engine, (b) skip a
    tick while a previous resync's broker fan-out is still in flight rather
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
    engines = dict(app.reconcilers)

    # (a) no engine anywhere -> no-op
    app.reconcilers = {}
    app.periodic_resync()
    assert calls == []

    # normal tick runs
    app.reconcilers = engines
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


def test_request_resync_scopes_to_the_events_org(db, fernet_key):
    """An execution event names its org; the near-immediate resync must fan
    ProtoOAReconcileReq out over THAT org's accounts only -- a fleet-wide
    sweep on every fill is what made fills take seconds to appear."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    clock = Clock()
    app.clock = clock
    calls = []
    app.resync = lambda org_id=None: (calls.append(org_id), defer.succeed(None))[1]

    app.request_resync(org_a)
    clock.advance(main.RESYNC_DEBOUNCE_S)
    assert calls == [org_a]


def test_request_resync_debounces_per_org_not_across_kinds(db, fernet_key):
    """A burst for one org collapses; an org-scoped request and a fleet-wide
    one are different keys and both fire."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    clock = Clock()
    app.clock = clock
    org_calls = []
    sweep_calls = []
    app.resync = lambda org_id=None: (org_calls.append(org_id), defer.succeed(None))[1]
    app.periodic_resync = lambda: sweep_calls.append(1)

    app.request_resync(org_a)
    app.request_resync(org_a)   # same org: coalesced
    app.request_resync()        # fleet-wide: its own key
    clock.advance(main.RESYNC_DEBOUNCE_S)
    assert org_calls == [org_a]
    assert sweep_calls == [1]


def test_service_notify_passes_the_org_through(db, fernet_key):
    """The service tells the app WHICH org's positions changed."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    token_store = TokenStore(db, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    seen = []
    app.service.on_positions_changed = lambda org_id=None: seen.append(org_id)
    app.service._notify_positions_changed(org_a)
    assert seen == [org_a]


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


def test_get_ticks_serves_quotes_and_marks_from_memory(repo, token_store):
    """get_ticks is the live feed the api polls several times a second: the
    org's spot quotes by symbol name plus per-account equity/P&L/position
    marks -- all from the in-memory tracker, and an unknown or engine-less
    org answers empty rather than raising (the api only polls orgs whose
    sockets were already authorized)."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    app.state_trackers[ORG_A].set_positions(MASTER_A, [
        main.StatePositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY,
                                   volume=10_000_000, price=1.10500, label="copy:m42")
    ])
    app.state_trackers[ORG_A]._spots[1] = (1.10600, 1.10620)

    ticks = app.get_ticks(ORG_A)

    assert ticks["quotes"]["EURUSD"] == {"bid": 1.10600, "ask": 1.10620}
    acct = ticks["accounts"][str(MASTER_A)]
    assert acct["open_pnl"] == pytest.approx(100.0)
    pos = acct["positions"][0]
    assert pos["position_id"] == 42
    assert pos["symbol"] == "EURUSD"
    assert pos["current_price"] == pytest.approx(1.10600)
    assert pos["pnl_quote"] == pytest.approx(100.0)

    assert app.get_ticks(999999) == {"quotes": {}, "accounts": {}}


# ---------- trading safety guards (security review, 2026-08-22) ----------

def test_manual_order_rejects_a_fat_finger_volume(repo, token_store, monkeypatch):
    """A mistyped volume on the MASTER fans out to every slave, so the
    manual path carries a hard ceiling that copied trades never see."""
    monkeypatch.setattr(main, "MAX_MANUAL_ORDER_LOTS", 5.0)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol

    with pytest.raises(ValueError, match="exceeds the manual-order limit"):
        app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                         "side": "BUY", "order_type": "MARKET",
                         "volume_lots": 100})

    # The ceiling is the only thing rejecting it: one lot goes through.
    result = app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                              "side": "BUY", "order_type": "MARKET",
                              "volume_lots": 1})
    assert result["status"] == "submitted"


def test_manual_order_rejects_non_finite_numbers(repo, token_store):
    """NaN and inf pass every > 0 comparison and reach the broker as
    garbage; they must die at the boundary."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol

    base = {"account_id": MASTER_A, "symbol": symbol.name, "side": "BUY",
            "order_type": "MARKET"}
    with pytest.raises(ValueError, match="finite"):
        app.place_order({**base, "volume_lots": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        app.place_order({**base, "volume_lots": float("inf")})
    with pytest.raises(ValueError, match="finite"):
        app.place_order({**base, "volume_lots": 1, "stop_loss": float("inf")})


def test_manual_order_is_blocked_while_the_org_is_in_dry_run(repo, token_store):
    """Dry-run is the safe-mode gate: copied trades are simulated, so a
    manual order slipping through would put real money on the wire the
    operator believes is idle -- and desync master from slaves."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    repo.set_org_setting(ORG_A, "dry_run", True)

    with pytest.raises(ValueError, match="dry-run"):
        app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                         "side": "BUY", "order_type": "MARKET",
                         "volume_lots": 1})

    repo.set_org_setting(ORG_A, "dry_run", False)
    assert app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                            "side": "BUY", "order_type": "MARKET",
                            "volume_lots": 1})["status"] == "submitted"


def test_manual_order_records_who_placed_it(repo, token_store):
    """Audit attribution: the api forwards the acting user's email and the
    event carries it, so a fleet-wide order is traceable to a person."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol

    app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                     "side": "BUY", "order_type": "MARKET", "volume_lots": 1,
                     "actor_email": "ada@example.com"})

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT actor_email, org_id FROM events "
            "WHERE payload->>'action' = 'manual_order' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == ("ada@example.com", ORG_A)


def test_get_state_describes_pending_orders_fully(repo, token_store):
    """The Positions screen shows working orders next to positions, so a
    pending order has to say what it IS -- side, type and the price it is
    waiting for -- not just its symbol and size."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    app.reconcilers[ORG_A].master_orders = [
        OrderSnapshot(order_id=7, symbol_id=symbol.symbol_id, volume=2_500_000,
                      label="", side=Side.SELL, order_type="LIMIT",
                      price=1.10950),
    ]

    order = app.get_state(ORG_A)["pending_orders"][0]

    assert order["side"] == "SELL"
    assert order["order_type"] == "LIMIT"
    assert order["price"] == pytest.approx(1.10950)
    assert order["volume_lots"] == "0.25"


def test_pending_order_fields_are_null_when_the_broker_omits_them(repo, token_store):
    """An order snapshot without side/type/price reports nulls rather than
    inventing a side -- the dashboard shows a dash."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    app.reconcilers[ORG_A].master_orders = [
        OrderSnapshot(order_id=8, symbol_id=symbol.symbol_id, volume=100_000, label=""),
    ]

    order = app.get_state(ORG_A)["pending_orders"][0]
    assert order["side"] is None
    assert order["order_type"] is None
    assert order["price"] is None


def test_amend_position_sltp_sends_both_protections_and_attributes_it(repo, token_store):
    """Setting SL/TP on an open position: one amend carrying both values,
    logged against the account, the org, and the person who asked."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    sent = []
    app.dispatcher.send_direct = lambda acct, req: sent.append((acct, req))

    result = app.amend_position_sltp(
        MASTER_A, 4242, stop_loss=1.0950, take_profit=1.1150,
        actor="ada@example.com")

    assert result == {"status": "submitted", "account_id": MASTER_A,
                      "position_id": 4242, "stop_loss": 1.0950,
                      "take_profit": 1.1150}
    (acct, req) = sent[0]
    assert acct == MASTER_A
    assert req.positionId == 4242
    assert req.stopLoss == pytest.approx(1.0950)
    assert req.takeProfit == pytest.approx(1.1150)

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT actor_email, org_id, payload->>'action' FROM events "
            "ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("ada@example.com", ORG_A, "amend_sltp")


def test_amend_position_sltp_treats_an_empty_value_as_removal(repo, token_store):
    """An omitted protection is cleared, not left alone -- that is what the
    broker's amend does, and the response says so plainly."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    sent = []
    app.dispatcher.send_direct = lambda acct, req: sent.append((acct, req))

    result = app.amend_position_sltp(MASTER_A, 99, stop_loss=1.10, take_profit="")

    assert result["take_profit"] is None
    assert result["stop_loss"] == pytest.approx(1.10)
    assert sent[0][1].takeProfit == 0  # unset on the wire


def test_amend_position_sltp_rejects_nonsense_prices(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    for bad in (0, -1, float("nan"), float("inf"), "abc"):
        with pytest.raises(ValueError):
            app.amend_position_sltp(MASTER_A, 1, stop_loss=bad)


def test_get_state_names_the_master_account_and_its_protection(repo, token_store):
    """The desk amends against the position's OWN account; inferring it
    from a copy row would name a slave."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    app.reconcilers[ORG_A].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=symbol.symbol_id, side=Side.BUY,
                         volume=10_000_000, price=1.105, label="",
                         stop_loss=1.0950, take_profit=1.1150),
    ]

    pos = app.get_state(ORG_A)["master_positions"][0]
    assert pos["account_id"] == MASTER_A
    assert pos["stop_loss"] == pytest.approx(1.0950)
    assert pos["take_profit"] == pytest.approx(1.1150)


# ---- market orders express protection as a distance, not a price ----

def _sent_order(app):
    sent = []
    app.dispatcher.send_direct = lambda acct, req: sent.append(req)
    return sent


def test_manual_market_order_sends_relative_protection(repo, token_store):
    """The broker refuses an absolute SL/TP on a MARKET order, so the ticket
    has to send the distance from the price the order will cross."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    app.state_trackers[ORG_A]._spots[symbol.symbol_id] = (1.0998, 1.1000)
    sent = _sent_order(app)

    app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                     "side": "BUY", "order_type": "MARKET", "volume_lots": 1,
                     "stop_loss": 1.0900, "take_profit": 1.1100})

    req = sent[0]
    # BUY crosses the ask (1.1000). The distance is 0.0100, and the wire
    # unit is 1/100000 of a price: 0.0100 * 100000 = 1000.
    assert req.relativeStopLoss == 1000
    assert req.relativeTakeProfit == 1000
    assert req.stopLoss == 0.0 and req.takeProfit == 0.0


def test_pending_orders_keep_absolute_protection(repo, token_store):
    """LIMIT and STOP orders DO take absolute prices -- the fix must not
    convert those."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    sent = _sent_order(app)

    app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                     "side": "BUY", "order_type": "LIMIT", "volume_lots": 1,
                     "limit_price": 1.0950,
                     "stop_loss": 1.0900, "take_profit": 1.1100})

    req = sent[0]
    assert req.stopLoss == pytest.approx(1.0900)
    assert req.takeProfit == pytest.approx(1.1100)
    assert req.relativeStopLoss == 0


def test_market_order_protection_needs_a_live_price(repo, token_store):
    """Without a quote there is no distance to compute. Say so plainly
    rather than sending something the broker will reject."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    _sent_order(app)

    with pytest.raises(ValueError, match="no live price"):
        app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                         "side": "BUY", "order_type": "MARKET",
                         "volume_lots": 1, "stop_loss": 1.09})

    # ...but an unprotected market order still goes through.
    assert app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                            "side": "BUY", "order_type": "MARKET",
                            "volume_lots": 1})["status"] == "submitted"


def test_market_order_rejects_protection_on_the_wrong_side(repo, token_store):
    """A stop above a BUY is not protection; catch it here with a readable
    message instead of letting the broker answer TRADING_BAD_STOPS."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol
    app.state_trackers[ORG_A]._spots[symbol.symbol_id] = (1.0998, 1.1000)
    _sent_order(app)

    with pytest.raises(ValueError, match="losing side"):
        app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                         "side": "BUY", "order_type": "MARKET",
                         "volume_lots": 1, "stop_loss": 1.2000})
    with pytest.raises(ValueError, match="winning side"):
        app.place_order({"account_id": MASTER_A, "symbol": symbol.name,
                         "side": "BUY", "order_type": "MARKET",
                         "volume_lots": 1, "take_profit": 1.0000})


def test_copies_report_the_protection_actually_on_the_slave(repo, token_store):
    """A copy can be live WITHOUT the protection its master carries -- the
    amend can lose the race with the copy's own fill. The screen has to
    report the slave's real state, not the master's intent."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    symbol = _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1, SLAVE_A2])
    app.master_symbols_by_org[ORG_A][symbol.symbol_id] = symbol

    for slave, pos_id, coid in ((SLAVE_A1, 5001, "cm42.1"), (SLAVE_A2, 5002, "cm42.2")):
        repo.create_position_mapping(master_position_id=42, slave_account_id=slave,
                                     client_order_id=coid, org_id=ORG_A)
        repo.activate_position_mapping(slave, coid, slave_position_id=pos_id,
                                       slave_volume=100_000, fill_price=1.1050)

    app.reconcilers[ORG_A].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=symbol.symbol_id, side=Side.BUY,
                         volume=100_000, price=1.1050, label="copy:m42",
                         stop_loss=1.1000, take_profit=1.1100),
    ]
    # One copy took the protection; the other is running naked.
    app.reconcilers[ORG_A].slave_positions = {
        SLAVE_A1: [PositionSnapshot(position_id=5001, symbol_id=symbol.symbol_id,
                                    side=Side.BUY, volume=100_000, price=1.1051,
                                    label="copy:m42", stop_loss=1.1001,
                                    take_profit=1.1101)],
        SLAVE_A2: [PositionSnapshot(position_id=5002, symbol_id=symbol.symbol_id,
                                    side=Side.BUY, volume=100_000, price=1.1051,
                                    label="copy:m42")],
    }

    copies = {c["slave_account_id"]: c
              for c in app.get_state(ORG_A)["master_positions"][0]["copies"]}
    assert copies[SLAVE_A1]["stop_loss"] == pytest.approx(1.1001)
    assert copies[SLAVE_A1]["take_profit"] == pytest.approx(1.1101)
    # The unprotected one says so plainly rather than echoing the master.
    assert copies[SLAVE_A2]["stop_loss"] is None
    assert copies[SLAVE_A2]["take_profit"] is None


@pytest_twisted.inlineCallbacks
def test_startup_learns_commission_only_after_authorizing(db_seeded, fernet_key):
    """The first commission read must FOLLOW authorization, not race it.

    Wired as its own now=True LoopingCall, this asked the broker for deal
    history 0.3s before the account was authorized -- every request came
    back INVALID_REQUEST, so the copier booted with no rate at all and
    every money-denominated stop stayed uncorrected until the next
    six-hourly tick. Ordering is the whole fix, so it is what is pinned.
    """
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    order = []
    original_authorize = app._connect_and_authorize

    def spy_authorize(accounts):
        d = defer.maybeDeferred(original_authorize, accounts)
        return d.addCallback(lambda result: (order.append("authorized"), result)[1])

    def spy_commission():
        order.append("commission")
        return defer.succeed(None)

    app._connect_and_authorize = spy_authorize
    app.refresh_commission_rates = spy_commission

    yield app.startup()

    assert "commission" in order, "startup() must seed the commission rates"
    assert order.index("authorized") < order.index("commission")


class _RefusingClient:
    """A client whose broker refuses one account without erroring the send.

    That is what a real refusal looks like: ProtoOAAccountAuthReq is
    answered with an error MESSAGE, so the Deferred fires successfully and
    only account_auth_error() distinguishes it from an authorization.
    """

    def __init__(self, reason="RET_ACCOUNT_DISABLED"):
        self.reason = reason

    def authorize_account(self, account_id, access_token):
        return defer.succeed(None)

    def account_auth_error(self, account_id):
        return self.reason


@pytest_twisted.inlineCallbacks
def test_a_refused_account_is_degraded_and_names_the_brokers_reason(db_seeded, fernet_key):
    """RED before the fix: the copier logged "authorized account 48434167"
    one line after the client logged "rejected: RET_ACCOUNT_DISABLED",
    left the account 'ok', and showed it as healthy. Every later request
    then failed as a bare INVALID_REQUEST naming neither account nor cause.
    """
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    account = next(a for a in repo.load_accounts() if a.account_id == MASTER_A)

    yield app._authorize_one(_RefusingClient(), account)

    refreshed = next(a for a in repo.load_accounts() if a.account_id == MASTER_A)
    assert refreshed.status == "degraded"
    assert "RET_ACCOUNT_DISABLED" in refreshed.last_error


@pytest_twisted.inlineCallbacks
def test_authorizing_again_clears_a_previously_refused_account(db_seeded, fernet_key):
    """A broker that re-enables an account must not leave it degraded."""
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    account = next(a for a in repo.load_accounts() if a.account_id == MASTER_A)

    yield app._authorize_one(_RefusingClient(), account)
    yield app._authorize_one(_RefusingClient(reason=None), account)

    refreshed = next(a for a in repo.load_accounts() if a.account_id == MASTER_A)
    assert refreshed.status == "ok"
    assert refreshed.last_error is None


def test_querying_a_refused_account_names_the_reason(db_seeded, fernet_key):
    """"400: trader details failed: INVALID_REQUEST" was true and useless.

    Every request type on an account the broker has refused comes back
    INVALID_REQUEST, indistinguishable from a malformed request of our own.
    """
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    app._client_for_account = lambda account: _RefusingClient()

    with pytest.raises(ValueError) as excinfo:
        app._query_context(MASTER_A)

    assert "RET_ACCOUNT_DISABLED" in str(excinfo.value)
    assert str(MASTER_A) in str(excinfo.value)


def test_a_query_is_not_blocked_while_auth_is_merely_in_flight(db_seeded, fernet_key):
    """In flight is not refused. Only an explicit refusal short circuits --
    a request during re-auth must take its chances as it always has."""
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    app._client_for_account = lambda account: _RefusingClient(reason=None)

    client, symbols = app._query_context(MASTER_A)   # must not raise

    assert client is not None
    assert symbols == {}


def _commission_spy(app):
    """Replace the refresh with a counter, keeping the debounce stamp."""
    calls = []

    def spy():
        app._last_commission_learn = app.clock.seconds()
        calls.append(1)
        return defer.succeed(None)

    app.refresh_commission_rates = spy
    return calls


def test_a_position_change_looks_for_a_rate_we_do_not_have_yet(db_seeded, fernet_key):
    """Six hours is right for keeping a rate current and wrong for getting
    the first one.

    An account connected minutes ago has no rate, so every amount stop on
    it is uncorrected until the periodic loop comes round -- and this fleet
    is reconnected often enough that in practice it never did. Observed
    live: a fresh account took a $1.50 target and paid $1.29.
    """
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    clock = Clock()
    app = main.build_app(repo, token_store, make_stub_client_factory(),
                         shards=1, clock=clock)
    calls = _commission_spy(app)

    app.request_resync(ORG_A)

    assert len(calls) == 1


def test_the_burst_from_one_trade_reads_history_once(db_seeded, fernet_key):
    """A single trade fires a position change per account. Deal history is
    far too heavyweight to ride every one."""
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    clock = Clock()
    app = main.build_app(repo, token_store, make_stub_client_factory(),
                         shards=1, clock=clock)
    calls = _commission_spy(app)

    for _ in range(12):
        app.request_resync(ORG_A)
        clock.advance(0.2)

    assert len(calls) == 1


def test_a_later_trade_may_look_again(db_seeded, fernet_key):
    """The debounce is a rate limit, not a one-shot: a symbol traded for
    the first time tomorrow still needs its rate."""
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    clock = Clock()
    app = main.build_app(repo, token_store, make_stub_client_factory(),
                         shards=1, clock=clock)
    calls = _commission_spy(app)

    app.request_resync(ORG_A)
    clock.advance(main.COMMISSION_LEARN_DEBOUNCE_S + 1)
    app.request_resync(ORG_A)

    assert len(calls) == 2


def test_the_startup_read_counts_towards_the_debounce(db_seeded, fernet_key):
    """Otherwise the first fill after boot fires a second sweep seconds
    after startup() has already done one."""
    repo = Repo(db_seeded)
    token_store = TokenStore(db_seeded, fernet_key)
    clock = Clock()
    app = main.build_app(repo, token_store, make_stub_client_factory(),
                         shards=1, clock=clock)

    # A real refresh stamps the debounce even when it finds nothing.
    app.refresh_commission_rates()
    calls = _commission_spy(app)

    app.request_resync(ORG_A)

    assert calls == []


def _make_broker_refuse_closes(server, accept_after=None):
    """Model a broker that takes the close request and does nothing.

    This is what production did: 66 ProtoOAClosePositionReq were sent, all
    answered BLOCKED_PAYLOAD_TYPE, and every position stayed open -- while
    the dashboard reported all 66 closed. The rejection carries no position
    id, so the copier cannot see WHICH close failed, only that the position
    is still there when it asks again.

    accept_after=N refuses the first N closes and lets the rest through, so
    a test can prove the retry recovers.
    """
    key = ProtoOAClosePositionReq().payloadType
    original = server._handlers[key]
    seen = []

    def refusing(proto, msg):
        req = ProtoOAClosePositionReq()
        req.ParseFromString(msg.payload)
        seen.append(req)
        server.requests.append(req)      # it reached the broker...
        if accept_after is not None and len(seen) > accept_after:
            original(proto, msg)         # ...and this time it worked

    server._handlers[key] = refusing
    return seen


@pytest_twisted.inlineCallbacks
def test_a_flatten_the_broker_refuses_is_reported_as_a_failure(db, fernet_key, monkeypatch):
    """The bug, exactly: sends were counted as closes.

    On 28 Aug the operator was told 66 positions across four accounts had
    been closed. The broker had refused all 66 and every one was still open.
    The kill switch must never again report a position closed when it is not.
    """
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    monkeypatch.setattr(main, "FLATTEN_SETTLE_S", 0.05)
    org_a = seed_db(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    _seed_broker_position(server, SLAVE_A1, 7001)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()
        _make_broker_refuse_closes(server)

        result = yield app.close_all(org_a, account_id=SLAVE_A1)

        summary = result["accounts"][0]
        assert summary["positions_closed"] == 0, (
            "reported a position closed that the broker refused to close")
        assert summary["positions_remaining"] == [7001]
        assert summary["error"], "a flatten that closed nothing must say so"
        # It kept trying rather than giving up after one wave.
        assert len(_closes_for(server, SLAVE_A1)) >= main.FLATTEN_ROUNDS
    finally:
        for c in created:
            c.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_a_flatten_retries_what_the_first_wave_could_not_close(db, fernet_key, monkeypatch):
    """Pressing the button again is what the operator had to do by hand, and
    it worked. The loop does it for them."""
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    monkeypatch.setattr(main, "FLATTEN_SETTLE_S", 0.05)
    org_a = seed_db(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    _seed_broker_position(server, SLAVE_A1, 7001)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()
        _make_broker_refuse_closes(server, accept_after=1)   # first wave refused

        result = yield app.close_all(org_a, account_id=SLAVE_A1)

        summary = result["accounts"][0]
        assert summary["positions_closed"] == 1
        assert summary["positions_remaining"] == []
        assert summary["error"] is None
        assert summary["rounds"] >= 2, "the recovery came from a second wave"
    finally:
        for c in created:
            c.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_a_clean_flatten_reports_verified_flat(db, fernet_key, monkeypatch):
    """The happy path still reports the truth -- measured, not assumed."""
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    monkeypatch.setattr(main, "FLATTEN_SETTLE_S", 0.05)
    org_a = seed_db(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    _seed_broker_position(server, SLAVE_A1, 7001)
    _seed_broker_position(server, SLAVE_A1, 7002)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        result = yield app.close_all(org_a, account_id=SLAVE_A1)

        summary = result["accounts"][0]
        assert summary["positions_closed"] == 2
        assert summary["positions_remaining"] == []
        assert summary["error"] is None
    finally:
        for c in created:
            c.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_an_unreachable_account_is_reported_open_not_flat(db, fernet_key, monkeypatch):
    """If the copier cannot even ask, it must not claim the account is flat.

    Not knowing and being flat are different, and only one of them is safe
    to walk away from.
    """
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    org_a = seed_db(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        def _no_answer(client, account_id):
            return defer.fail(RuntimeError("reconcile failed: TIMEOUT_ERROR"))
        monkeypatch.setattr(app, "_reconcile_book", _no_answer)

        result = yield app.close_all(org_a, account_id=SLAVE_A1)

        summary = result["accounts"][0]
        assert summary["positions_closed"] == 0
        assert summary["error"] and "TIMEOUT_ERROR" in summary["error"]
        # Null, not [] -- an empty list would read as "verified flat".
        assert summary["positions_remaining"] is None
    finally:
        for c in created:
            c.stop()
        server.shutdown()


@pytest_twisted.inlineCallbacks
def test_accounts_are_flattened_concurrently(db, fernet_key, monkeypatch):
    """Serially, the last account's closes did not reach the wire until well
    over a second after the button was pressed -- measured at 1.4s across 11
    accounts in production, with every position live throughout."""
    monkeypatch.setattr(main, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    monkeypatch.setattr(main, "FLATTEN_SETTLE_S", 0.05)
    org_a = seed_db(db, fernet_key)

    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {MASTER_A: TOKEN_A, SLAVE_A1: TOKEN_A, SLAVE_A2: TOKEN_A}
    for account_id, position_id in ((MASTER_A, 7001), (SLAVE_A1, 7002), (SLAVE_A2, 7003)):
        _seed_broker_position(server, account_id, position_id)
    port = server.listen(real_reactor)

    repo = Repo(db)
    factory, created = make_real_client_factory(port)
    app = main.build_app(repo, TokenStore(db, fernet_key), factory, shards=1)
    try:
        yield app.startup()

        timeline = []
        original = app._flatten_account

        def traced(account_id):
            timeline.append(("start", account_id))
            d = defer.maybeDeferred(original, account_id)
            return d.addCallback(
                lambda r: (timeline.append(("end", account_id)), r)[1])

        monkeypatch.setattr(app, "_flatten_account", traced)

        yield app.close_all(org_a)

        starts = [i for i, (kind, _) in enumerate(timeline) if kind == "start"]
        first_end = next(i for i, (kind, _) in enumerate(timeline) if kind == "end")
        assert max(starts) < first_end, (
            "accounts were flattened one after another; every account must be "
            "asked before any of them has finished")
    finally:
        for c in created:
            c.stop()
        server.shutdown()


# ---------- get_analytics: read from `deals`, not the broker ----------

def test_get_analytics_with_no_backfill_state_is_truncated(repo, token_store):
    """A freshly deployed system has no deal_backfill_state row yet. The
    Performance page must be told backfill has not started, rather than
    reading a near-empty result as "there is no trading history"."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    result = app.get_analytics(MASTER_A, weeks=4)

    assert result["truncated"] is True
    assert result["weeks"] == 4


def test_get_analytics_not_truncated_once_backfill_is_exhausted(repo, token_store):
    """exhausted=True means the backfill walked all the way back to the
    history bound: nothing more is missing."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    repo.set_backfill_state(MASTER_A, 0, 1700000000000, exhausted=True)

    assert app.get_analytics(MASTER_A, weeks=4)["truncated"] is False


def test_get_analytics_truncated_when_backfill_has_not_reached_the_window(
        repo, token_store):
    """Backfill in progress, but its oldest covered point is still newer
    than the requested window's start: some of the window is uncovered."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    now_ms = app._now_ms()
    window_start = now_ms - 4 * WEEK_MS
    repo.set_backfill_state(MASTER_A, window_start + WEEK_MS, now_ms,
                            exhausted=False)

    assert app.get_analytics(MASTER_A, weeks=4)["truncated"] is True


def test_get_analytics_not_truncated_when_backfill_covers_the_window(
        repo, token_store):
    """Backfill unfinished but already walked back past the window start:
    that window is fully covered."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    now_ms = app._now_ms()
    window_start = now_ms - 4 * WEEK_MS
    repo.set_backfill_state(MASTER_A, window_start - WEEK_MS, now_ms,
                            exhausted=False)

    assert app.get_analytics(MASTER_A, weeks=4)["truncated"] is False


def test_get_analytics_weeks_zero_means_all_stored_history(repo, token_store):
    """weeks == 0 asks for the whole `deals` table (since_ms=None), not the
    max(1, ...) clamp applied to every other value."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    repo.upsert_deals(MASTER_A, ORG_A, [{
        "deal_id": 1, "execution_timestamp": 1, "commission": 0.0,
        "close": {"entry_price": 1.0, "gross_profit": 5.0, "swap": 0.0,
                  "commission": 0.0, "balance": 10005.0, "closed_volume": 1},
    }])

    result = app.get_analytics(MASTER_A, weeks=0)

    assert result["weeks"] == 0
    assert result["net_pnl"] == pytest.approx(5.0)
    # No backfill state at all and weeks == 0 (window start = beginning of
    # time): truncated only turns False once exhausted is True.
    assert result["truncated"] is True


def test_get_analytics_reads_stored_deals_and_never_the_broker(repo, token_store):
    """The whole point: one indexed DB read, no DealListReq fan-out."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    repo.upsert_deals(MASTER_A, ORG_A, [{
        "deal_id": 2, "execution_timestamp": app._now_ms(), "commission": -0.07,
        "close": {"entry_price": 1.08, "gross_profit": 2.75, "swap": -0.12,
                  "commission": -0.07, "balance": 10002.75, "closed_volume": 1},
    }])

    called = []
    original = main.queries.deal_history
    main.queries.deal_history = lambda *a, **k: called.append(a)
    try:
        result = app.get_analytics(MASTER_A, weeks=4)
    finally:
        main.queries.deal_history = original

    assert called == [], "get_analytics went to the broker"
    assert result["net_pnl"] == pytest.approx(2.75 - 0.12 - 0.07)


def test_get_analytics_bounds_the_row_fetch(repo, token_store):
    """D3: analytics runs synchronously on the reactor connection, so an
    account with years of history must not be able to stall every org on
    one unbounded fetch. load_deals is handed an explicit LIMIT."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    seen = {}
    real = repo.load_deals

    def spy(account_id, since_ms=None, limit=None):
        seen["limit"] = limit
        return real(account_id, since_ms, limit=limit)

    repo.load_deals = spy
    try:
        app.get_analytics(MASTER_A, weeks=4)
    finally:
        repo.load_deals = real

    assert seen["limit"] == main.ANALYTICS_ROW_LIMIT


# ---------- /health reports the async writer ----------

def test_get_health_reports_the_db_writer(repo, token_store):
    """The writer DROPS rather than blocks under overflow, so its health is
    the only place a silent loss of executions, position upserts and event
    logs becomes visible. The rest of the payload must not change shape."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    without = app.get_health()
    assert without["db_writer"] == {"healthy": None, "dropped": 0}
    assert without["status"] == "ok"
    assert [o["org_id"] for o in without["orgs"]] == [ORG_A]

    writer = main.AsyncWriter(repo.dsn, batch_interval_s=10.0)
    repo.writer = writer
    try:
        health = app.get_health()
        assert health["db_writer"] == {"healthy": True, "dropped": 0}
        assert [o["org_id"] for o in health["orgs"]] == [ORG_A]
    finally:
        writer.flush_and_stop(timeout_s=2.0)
        repo.writer = None


# ---------- deal-backfill orchestration ----------

class _FakeDealBroker:
    """Answers deal_history and records every window it was asked for."""

    def __init__(self, has_more_over_ms=None, deals_per_window=1):
        self.calls = []
        self._has_more_over_ms = has_more_over_ms
        self._deals_per_window = deals_per_window

    def deal_history(self, client, account_id, symbols, from_ms, to_ms, **kw):
        self.calls.append((account_id, from_ms, to_ms))
        has_more = (self._has_more_over_ms is not None
                    and (to_ms - from_ms) > self._has_more_over_ms)
        deals = [{"deal_id": from_ms + to_ms + i,
                  "execution_timestamp": from_ms, "close": None}
                 for i in range(self._deals_per_window)]
        return defer.succeed({"deals": deals, "has_more": has_more})


@pytest.fixture
def backfill_app(repo, token_store):
    """The real CopierApp over the seeded three-account org A."""
    return main.build_app(repo, token_store, make_stub_client_factory(),
                          shards=1)


@pytest_twisted.inlineCallbacks
def test_backfill_rotates_across_accounts_instead_of_starving_all_but_one(
        backfill_app, repo, monkeypatch):
    """The loop used to return after the FIRST account with a window, and
    next_window() hands an already-exhausted account a forward catch-up
    window on essentially every tick (backfilled_to_ms carries the previous
    tick's now_ms). So one account consumed every 30s tick forever and the
    others never had a single deal fetched -- `deals` empty,
    get_backfill_state None, get_analytics zeros and truncated=True for the
    life of the deployment AND across restarts, because the watermark is
    durable."""
    broker = _FakeDealBroker()
    monkeypatch.setattr(main.queries, "deal_history", broker.deal_history)
    # SLAVE_A1 is exhausted with a stale watermark: a forward window every tick.
    repo.set_backfill_state(SLAVE_A1, 1, backfill_app._now_ms() - 60_000,
                            exhausted=True)

    for _ in range(3):
        yield backfill_app.backfill_deals_once()

    serviced = {account_id for account_id, _, _ in broker.calls}
    assert serviced == {MASTER_A, SLAVE_A1, SLAVE_A2}, (
        f"3 ticks backfilled only {sorted(serviced)}; the rest are starved "
        "permanently and their Performance page stays blank")


@pytest_twisted.inlineCallbacks
def test_backfill_does_one_window_per_tick(backfill_app, monkeypatch):
    """The rate discipline the rotation must not trade away: the broker
    caps DealList at a week and 500 rows and the send path runs at
    10 msg/s, so a tick services exactly one account."""
    broker = _FakeDealBroker()
    monkeypatch.setattr(main.queries, "deal_history", broker.deal_history)

    yield backfill_app.backfill_deals_once()

    assert len(broker.calls) == 1


@pytest_twisted.inlineCallbacks
def test_a_single_empty_week_does_not_mark_an_account_exhausted(
        backfill_app, repo, monkeypatch):
    """`exhausted` means "reached the account's first deal", but it was set
    by the first window that happened to be empty. An account with two
    years of history that simply did not trade last week -- a holiday, a
    paused slave, i.e. most of the fleet -- lost its ENTIRE history on tick
    1, and get_analytics then reported truncated=False, "we hold everything
    the broker has", over an empty table."""
    def empty(client, account_id, symbols, from_ms, to_ms, **kw):
        return defer.succeed({"deals": [], "has_more": False})

    monkeypatch.setattr(main.queries, "deal_history", empty)

    for _ in range(3):
        yield backfill_app.backfill_deals_once()

    for account_id in (MASTER_A, SLAVE_A1, SLAVE_A2):
        state = repo.get_backfill_state(account_id)
        assert state is not None, f"account {account_id} was never backfilled"
        assert state["exhausted"] is False, (
            f"account {account_id} was declared complete after one quiet "
            "week; its entire history is now unreachable")


@pytest_twisted.inlineCallbacks
def test_the_walk_marks_exhausted_once_it_reaches_the_history_bound(
        backfill_app, repo, monkeypatch):
    """The other half: `exhausted` must still be reachable, or truncated
    stays True forever even after backfill has stopped for good."""
    broker = _FakeDealBroker()
    monkeypatch.setattr(main.queries, "deal_history", broker.deal_history)
    year_ms = 365 * 24 * 3600 * 1000
    bound = main.DEAL_BACKFILL_MAX_YEARS * year_ms
    # One week short of the bound: the next backward window lands on it.
    repo.set_backfill_state(
        SLAVE_A1, backfill_app._now_ms() - (bound - WEEK_MS),
        backfill_app._now_ms(), exhausted=False)

    yield backfill_app.backfill_deals_once()

    assert repo.get_backfill_state(SLAVE_A1)["exhausted"] is True


@pytest_twisted.inlineCallbacks
def test_forward_catch_up_never_un_exhausts_an_account(
        backfill_app, repo, monkeypatch):
    """Once the walk is done it stays done: the forward catch-up branch
    re-reads recent time, whose from_ms is nowhere near the history bound."""
    broker = _FakeDealBroker()
    monkeypatch.setattr(main.queries, "deal_history", broker.deal_history)
    repo.set_backfill_state(SLAVE_A1, 1, backfill_app._now_ms() - 60_000,
                            exhausted=True)

    yield backfill_app.backfill_deals_once()

    assert repo.get_backfill_state(SLAVE_A1)["exhausted"] is True


@pytest_twisted.inlineCallbacks
def test_a_window_over_the_row_cap_is_bisected_not_silently_truncated(
        backfill_app, repo, monkeypatch):
    """deal_history caps a response at 500 rows and reports the overflow as
    has_more. The watermark advanced past the window regardless and the
    walk never revisits it, so an active week of 700 deals lost 200 of them
    forever -- with `exhausted` eventually True and `truncated` False, i.e.
    no signal at all that closed_trades, net_pnl and the equity curve are
    wrong."""
    broker = _FakeDealBroker(has_more_over_ms=WEEK_MS // 2)
    monkeypatch.setattr(main.queries, "deal_history", broker.deal_history)

    yield backfill_app.backfill_deals_once()

    assert len(broker.calls) == 3, (
        f"a has_more window produced {len(broker.calls)} requests; it must "
        "be bisected and re-fetched, not skipped")
    windows = sorted((lo, hi) for _, lo, hi in broker.calls)
    halves = [w for w in windows if w[1] - w[0] <= WEEK_MS // 2]
    full = [w for w in windows if w[1] - w[0] > WEEK_MS // 2]
    assert len(full) == 1 and len(halves) == 2
    assert halves[0][0] == full[0][0], "the older half must start at the window start"
    assert halves[1][1] == full[0][1], "the newer half must end at the window end"
    assert halves[0][1] == halves[1][0], "the halves must tile the window"
    # Every deal the broker handed back is stored; nothing was dropped.
    assert len(repo.load_deals(SLAVE_A1)) == 3


@pytest_twisted.inlineCallbacks
def test_backfill_tick_never_errbacks(backfill_app, monkeypatch):
    """It is a LoopingCall body: a Deferred that fails stops the loop
    permanently, and deal history would then never be fetched again."""
    def boom(*args, **kwargs):
        raise RuntimeError("broker exploded")

    monkeypatch.setattr(main.queries, "deal_history", boom)

    yield backfill_app.backfill_deals_once()   # must not raise


# ---------- the intraday balance-sample clock is PER ORG ----------

class _BalanceStateTracker(_RecordingStateTracker):
    """A tracker whose snapshot() carries real balances, so the sample
    write actually has rows to persist."""

    def __init__(self, account_ids):
        super().__init__()
        self._account_ids = list(account_ids)

    def snapshot(self):
        return {a: {"balance": 10_000.0, "equity": 10_050.0, "open_pnl": 50.0}
                for a in self._account_ids}


def _sampled_accounts(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT account_id FROM balance_samples").fetchall()
    return {r[0] for r in rows}


def test_an_org_scoped_refresh_does_not_consume_every_orgs_sample_slot(
        db, fernet_key):
    """A single process-wide clock would starve every org but one, because
    this body is also called org-scoped: resync(org_id) ends with
    refresh_balances(org_id), and request_resync fires on every position
    change. An actively-trading org A lands one of those A-only invocations
    on the 5-minute gate inside every window: the clock is stamped, the
    loop `continue`s past orgs B..N, and B..N never get an intraday balance
    sample at all."""
    org_a, org_b = seed_two_orgs(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key),
                         make_stub_client_factory(), shards=1)
    app.state_trackers = {
        org_a: _BalanceStateTracker([MASTER_A, SLAVE_A1, SLAVE_A2]),
        org_b: _BalanceStateTracker([MASTER_B, SLAVE_B1]),
    }

    app.refresh_balances(org_a)      # org A's fill-driven resync
    assert _sampled_accounts(db) >= {MASTER_A}

    app.refresh_balances()           # the fleet-wide 60s poll, right after

    assert MASTER_B in _sampled_accounts(db), (
        "org B never got an intraday balance sample: org A's org-scoped "
        "refresh consumed the process-wide sampling slot")


def test_a_second_refresh_inside_the_interval_does_not_resample_an_org(
        db, fernet_key):
    """The gate must still throttle: BALANCE_SAMPLE_INTERVAL_S is 5
    minutes and the poll runs every 60 s."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key),
                         make_stub_client_factory(), shards=1)
    app.state_trackers = {org_a: _BalanceStateTracker([MASTER_A])}

    app.refresh_balances()
    app.refresh_balances()

    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM balance_samples WHERE account_id = %s",
            (MASTER_A,)).fetchone()
    assert n == 1, f"{n} samples in one interval; the throttle is gone"


def test_balance_samples_carry_equity_and_unrealized_pnl_and_null_margin(
        db, fernet_key):
    """margin_used is not tracked anywhere in this codebase, so it must be
    NULL rather than a fabricated zero; open_pnl is the unrealized half."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    app = main.build_app(repo, TokenStore(db, fernet_key),
                         make_stub_client_factory(), shards=1)
    app.state_trackers = {org_a: _BalanceStateTracker([MASTER_A])}

    app.refresh_balances()

    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT org_id, balance, equity, margin_used, unrealized_pnl "
            "FROM balance_samples WHERE account_id = %s", (MASTER_A,)).fetchone()
    org_id, balance, equity, margin_used, unrealized = row
    assert org_id == org_a
    assert float(balance) == pytest.approx(10_000.0)
    assert float(equity) == pytest.approx(10_050.0)
    assert margin_used is None
    assert float(unrealized) == pytest.approx(50.0)


# ---------- resync persists positions without extra blocking reads ----------

@pytest_twisted.inlineCallbacks
def test_resync_does_not_re_read_each_slaves_symbol_cache(db, fernet_key):
    """resync must not call load_symbol_cache(slave_id) per slave to build
    the position upsert's symbol map -- a full 500-2000 row SELECT each,
    synchronously on the reactor -- when routing_provider(), resolved for
    the same pass, ALREADY holds exactly that map for every slave.

    request_resync fires ~0.2s after a fill, while the 10 msg/s send queue
    is still draining slaves 3-5 of the fan-out, so those extra round trips
    stall the queue mid-copy.
    """
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1, SLAVE_A2])
    app = main.build_app(repo, TokenStore(db, fernet_key),
                         make_stub_client_factory(), shards=1)

    app.reconcilers[org_a].run = lambda: defer.succeed([])
    app.reconcilers[org_a].master_positions = []
    app.reconcilers[org_a].slave_positions = {
        SLAVE_A1: [PositionSnapshot(position_id=5001, symbol_id=1,
                                    side=Side.BUY, volume=5_000_000,
                                    price=1.10537, label="")],
        SLAVE_A2: [],
    }
    app.state_trackers = {org_a: _RecordingStateTracker()}

    # Loads made while building an OrgRouting are the ones resync is
    # entitled to; the finding is the SECOND set it would make on top.
    direct_loads: list[int] = []
    depth = {"routing": 0}
    real_load = repo.load_symbol_cache
    real_routing = app.routing_provider

    def counting_load(account_id):
        if depth["routing"] == 0:
            direct_loads.append(account_id)
        return real_load(account_id)

    def tracked_routing():
        depth["routing"] += 1
        try:
            return real_routing()
        finally:
            depth["routing"] -= 1

    repo.load_symbol_cache = counting_load
    app.routing_provider = tracked_routing
    try:
        yield app.resync(org_a)
    finally:
        repo.load_symbol_cache = real_load

    assert direct_loads == [], (
        f"resync read symbol caches for {direct_loads} on top of the ones "
        "the routing snapshot it just built already holds")


@pytest_twisted.inlineCallbacks
def test_resync_persists_open_positions_and_closes_the_vanished_ones(
        db, fernet_key):
    """Positions existed only in Reconciler.master_positions and the
    in-memory tracker, so a restart showed an empty Positions screen until
    the broker answered. Each resync now records the book -- and the
    broker's answer is the truth about what is still open, so anything it
    stops reporting is closed out."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    info = _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1, SLAVE_A2])
    app = main.build_app(repo, TokenStore(db, fernet_key),
                         make_stub_client_factory(), shards=1)
    # startup()'s symbol fetch fills this in production; the master's map
    # is the one cache routing does NOT carry (routing holds slaves only).
    app.master_symbols_by_org[org_a][info.symbol_id] = info

    reconciler = app.reconcilers[org_a]
    reconciler.run = lambda: defer.succeed([])
    reconciler.master_positions = [
        PositionSnapshot(position_id=7001, symbol_id=1, side=Side.BUY,
                         volume=1_000_000, price=1.1050, label="")]
    reconciler.slave_positions = {
        SLAVE_A1: [PositionSnapshot(position_id=5001, symbol_id=1,
                                    side=Side.SELL, volume=5_000_000,
                                    price=1.10537, label="")],
        SLAVE_A2: [],
    }
    app.state_trackers = {org_a: _RecordingStateTracker()}

    yield app.resync(org_a)

    stored = {(r["account_id"], r["position_id"]): r
              for r in repo.load_open_positions(org_a)}
    assert set(stored) == {(MASTER_A, 7001), (SLAVE_A1, 5001)}
    assert stored[(MASTER_A, 7001)]["symbol"] == "EURUSD"
    assert stored[(SLAVE_A1, 5001)]["side"] == "SELL"

    # The broker now reports the master flat and the slave unchanged.
    reconciler.master_positions = []
    yield app.resync(org_a)

    assert {(r["account_id"], r["position_id"])
            for r in repo.load_open_positions(org_a)} == {(SLAVE_A1, 5001)}


def test_get_state_serves_stored_positions_before_the_first_resync(
        db, fernet_key):
    """A fresh process has no reconciler snapshot until the first resync
    lands. Serve the last known book from the database rather than an
    empty screen, flagged `stale` so the caller knows the quote-derived
    fields are missing."""
    org_a = seed_db(db, fernet_key)
    repo = Repo(db)
    _seed_symbol_cache(repo, [MASTER_A])
    repo.upsert_positions(
        MASTER_A, org_a,
        [PositionSnapshot(position_id=7001, symbol_id=1, side=Side.BUY,
                          volume=1_000_000, price=1.1050, label="")],
        {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                       lot_size=10_000_000, min_volume=100_000,
                       step_volume=100_000)})
    app = main.build_app(repo, TokenStore(db, fernet_key),
                         make_stub_client_factory(), shards=1)

    state = app.get_state(org_a)

    assert [p["position_id"] for p in state["master_positions"]] == [7001]
    pos = state["master_positions"][0]
    assert pos["account_id"] == MASTER_A
    assert pos["symbol"] == "EURUSD"
    assert pos["stale"] is True
    # No live quote before the first resync: invent nothing.
    assert pos["pnl_quote"] is None
    assert pos["current_price"] is None
