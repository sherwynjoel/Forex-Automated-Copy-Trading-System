"""End-to-end tests: the REAL CopierApp against a REAL FakeCTraderServer.

Drives `copier.main.build_app()` + `CopierApp.startup()` -- the exact
composition `boot()` wires into a live reactor -- against a scripted
FakeCTraderServer over TLS. This is the executable proof of spec Sec.5: a
master execution event flows all the way through normalize -> decide ->
dispatch (throttled) -> real slave ProtoOA*Req messages on the wire -> slave
execution events -> mapping activation in Postgres, plus the operational
controls (kill switch, dry-run) and reconciliation/drift reporting layered on
top.

Every test seeds its own accounts/connection into the shared `copytrader_test`
database (via the function-scoped `db` fixture in tests/conftest.py, which
truncates all relevant tables beforehand) and builds a fresh CopierApp +
FakeCTraderServer pair; nothing is shared across tests. All three accounts
are demo (is_live=False) with shards=1, so master (100) and both slaves (101,
102) share a single CTraderClient/TCP connection to a single
FakeCTraderServer -- exactly like a real single-connection deployment. The
master's own execution events (open/close/amend/cancel) are simulated by
sending the corresponding request over that SAME shared connection, fire-
and-forget: it is the only way to inject a "pushed" master event into a
black-box wire-level composition without a live upstream broker feed, and it
is a value-neutral stand-in since FakeCTraderServer only reacts to messages
it receives.

Trade/amend/cancel requests are fire-and-forget (the real cTrader server
never sends a synchronous, clientMsgId-tagged reply to them -- see
test_fake_server.py); outcomes are awaited via real DB state and the
recorded wire traffic, never via internal mocks.
"""

from datetime import datetime, timedelta

import psycopg
import pytest
import pytest_twisted
from cryptography.fernet import Fernet
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAmendOrderReq,
    ProtoOAAmendPositionSLTPReq,
    ProtoOACancelOrderReq,
    ProtoOAClosePositionReq,
    ProtoOANewOrderReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAOrderType, ProtoOATradeSide
from twisted.internet import defer, reactor, task

from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.domain.models import OpenMarket, Side
from copier.main import build_app
from copier.testing.fake_server import FakeCTraderServer

MASTER_ID = 100
SLAVE1_ID = 101   # multiplier 1.0
SLAVE2_ID = 102   # multiplier 0.5
SYMBOL_ID = 1     # EURUSD, the fake server's only symbol
ONE_LOT = 10_000_000   # ProtoOASymbol.lotSize for EURUSD in FakeCTraderServer
ACCESS_TOKEN = "e2e-access-token"

# Not a secret; regenerated per test process only so TokenStore has a real
# Fernet key to encrypt/decrypt the seeded connection's token pair with.
FERNET_KEY = Fernet.generate_key().decode()


# ---------- setup / teardown ----------

def _setup(dsn: str, auto_fill: bool = True):
    """Build a fresh FakeCTraderServer + real CopierApp wired via build_app().

    Seeds one ctid_connections row and three accounts (100 master, 101 slave
    @1.0x, 102 slave @0.5x) sharing that connection's token pair -- exactly
    what a single cTrader OAuth grant covering multiple accounts looks like.
    All accounts are demo (is_live=False), so build_app's shards=1 wiring
    gives them a single shared CTraderClient/TCP connection to this one
    server. Caller must yield app.startup() and eventually call _teardown().
    """
    server = FakeCTraderServer(auto_fill=auto_fill)
    port = server.listen(reactor)

    token_store = TokenStore(dsn, FERNET_KEY)
    expires_at = datetime.utcnow() + timedelta(days=60)
    connection_id = token_store.save_grant(ACCESS_TOKEN, "e2e-refresh-token", expires_at)

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO accounts
                (ctid_trader_account_id, ctid_connection_id, trader_login, is_live,
                 role, enabled, multiplier)
            VALUES
                (%(master)s, %(conn)s, 90100, false, 'master', true, 1.0),
                (%(slave1)s, %(conn)s, 90101, false, 'slave',  true, 1.0),
                (%(slave2)s, %(conn)s, 90102, false, 'slave',  true, 0.5)
            """,
            {"master": MASTER_ID, "slave1": SLAVE1_ID, "slave2": SLAVE2_ID, "conn": connection_id},
        )

    server.accounts = {MASTER_ID: ACCESS_TOKEN, SLAVE1_ID: ACCESS_TOKEN, SLAVE2_ID: ACCESS_TOKEN}

    repo = Repo(dsn)

    def client_factory(is_live: bool) -> CTraderClient:
        sdk = make_sdk_client("127.0.0.1", port)
        return CTraderClient(sdk, "e2e-client-id", "e2e-client-secret")

    app = build_app(repo, token_store, client_factory, shards=1)
    return server, repo, app


def _setup_empty(dsn: str, auto_fill: bool = True):
    """Like _setup(), but builds the CopierApp with ZERO accounts in the
    database -- exactly how the real process boots when accounts are added
    AFTER startup (discover(), the dashboard, or a direct insert), which is
    the scenario task-30 review finding I1(a) exists to pin: build_app()
    with no accounts builds no clients, and CopierApp.startup() with no
    accounts returns before doing anything else (main.py:100-103). Returns
    (server, repo, app, connection_id) -- connection_id is a ready-to-use
    OAuth grant row the caller can attach accounts to later with
    _insert_accounts(). Caller must yield app.startup() (with zero
    accounts, a no-op) before inserting accounts and calling app.reload(),
    and eventually call _teardown().
    """
    server = FakeCTraderServer(auto_fill=auto_fill)
    port = server.listen(reactor)

    token_store = TokenStore(dsn, FERNET_KEY)
    expires_at = datetime.utcnow() + timedelta(days=60)
    connection_id = token_store.save_grant(ACCESS_TOKEN, "e2e-refresh-token", expires_at)

    server.accounts = {MASTER_ID: ACCESS_TOKEN, SLAVE1_ID: ACCESS_TOKEN, SLAVE2_ID: ACCESS_TOKEN}

    repo = Repo(dsn)

    def client_factory(is_live: bool) -> CTraderClient:
        sdk = make_sdk_client("127.0.0.1", port)
        return CTraderClient(sdk, "e2e-client-id", "e2e-client-secret")

    app = build_app(repo, token_store, client_factory, shards=1)  # zero accounts at this point
    return server, repo, app, connection_id


def _insert_accounts(dsn: str, connection_id: int) -> None:
    """Insert the same three accounts (100 master, 101 slave @1.0x, 102
    slave @0.5x) _setup() seeds up front, but as a standalone step -- used
    by tests that need them to arrive AFTER the app already exists (see
    _setup_empty)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO accounts
                (ctid_trader_account_id, ctid_connection_id, trader_login, is_live,
                 role, enabled, multiplier)
            VALUES
                (%(master)s, %(conn)s, 90100, false, 'master', true, 1.0),
                (%(slave1)s, %(conn)s, 90101, false, 'slave',  true, 1.0),
                (%(slave2)s, %(conn)s, 90102, false, 'slave',  true, 0.5)
            """,
            {"master": MASTER_ID, "slave1": SLAVE1_ID, "slave2": SLAVE2_ID, "conn": connection_id},
        )


def _teardown(app, server) -> None:
    """Stop every client (kills the heartbeat LoopingCall) then the server."""
    for env_clients in app.clients.values():
        for client in env_clients.values():
            client.stop()
    server.shutdown()


# ---------- polling / request helpers ----------

def _wait_until(predicate, timeout=10.0):
    """Poll a zero-arg predicate every 50ms on the reactor until it's true."""
    def check():
        if predicate():
            return defer.succeed(None)
        return task.deferLater(reactor, 0.05, check)

    d = check()
    d.addTimeout(timeout, reactor)
    return d


def _fire_and_forget(client, msg):
    """Send a trade/amend/cancel request without waiting on its Deferred.

    The real cTrader server (and FakeCTraderServer) never sends a
    synchronous, clientMsgId-tagged reply to these; the SDK's response
    Deferred would otherwise time out after 5s. Swallow that expected
    timeout so it never surfaces as an unhandled error.
    """
    d = client.send(msg)
    d.addErrback(lambda failure: None)
    return d


def _market_order(account_id: int, side, volume: int, label: str = ""):
    req = ProtoOANewOrderReq()
    req.ctidTraderAccountId = account_id
    req.symbolId = SYMBOL_ID
    req.orderType = ProtoOAOrderType.MARKET
    req.tradeSide = side
    req.volume = volume
    req.label = label
    return req


def _limit_order(account_id: int, side, volume: int, price: float, label: str = ""):
    req = ProtoOANewOrderReq()
    req.ctidTraderAccountId = account_id
    req.symbolId = SYMBOL_ID
    req.orderType = ProtoOAOrderType.LIMIT
    req.tradeSide = side
    req.volume = volume
    req.limitPrice = price
    req.label = label
    return req


def _close_position(account_id: int, position_id: int, volume: int):
    req = ProtoOAClosePositionReq()
    req.ctidTraderAccountId = account_id
    req.positionId = position_id
    req.volume = volume
    return req


def _amend_sltp(account_id: int, position_id: int, stop_loss=None, take_profit=None):
    req = ProtoOAAmendPositionSLTPReq()
    req.ctidTraderAccountId = account_id
    req.positionId = position_id
    if stop_loss is not None:
        req.stopLoss = stop_loss
    if take_profit is not None:
        req.takeProfit = take_profit
    return req


def _amend_order(account_id: int, order_id: int, volume=None, limit_price=None):
    req = ProtoOAAmendOrderReq()
    req.ctidTraderAccountId = account_id
    req.orderId = order_id
    if volume is not None:
        req.volume = volume
    if limit_price is not None:
        req.limitPrice = limit_price
    return req


def _cancel_order(account_id: int, order_id: int):
    req = ProtoOACancelOrderReq()
    req.ctidTraderAccountId = account_id
    req.orderId = order_id
    return req


def _events(dsn: str, category: str | None = None) -> list[dict]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.row_factory = psycopg.rows.dict_row
        if category is not None:
            rows = conn.execute(
                "SELECT * FROM events WHERE category = %s ORDER BY id", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    return rows


def _active_rows(repo) -> list[dict]:
    """Mapping rows with status='active' (row exists is not enough -- a
    mapping is created 'pending' the instant an intent is dispatched, and
    only flips to 'active' once the slave's own execution event confirms
    it, asynchronously, over the wire)."""
    return [r for r in repo.mapping_rows() if r["status"] == "active"]


def _new_order_reqs_to_slaves(server) -> list:
    """ProtoOANewOrderReq the fake server received, addressed to a slave.

    Excludes the master's own order (sent by the test to simulate a pushed
    master execution event) so counts reflect fan-out only.
    """
    return [
        r for r in server.requests
        if isinstance(r, ProtoOANewOrderReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
    ]


# ---------- tests ----------

@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_master_fill_fans_out_and_maps(db):
    """Master MARKET fill fans out to both slaves and mappings go active.

    Proves: normalize -> decide -> dispatch(throttled) -> real
    ProtoOANewOrderReq on the wire, sized by each slave's multiplier, and
    the resulting fills activate both position mappings in Postgres.
    """
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))

        yield _wait_until(lambda: len(_active_rows(repo)) >= 2)

        rows = repo.mapping_rows()
        assert len(rows) == 2
        master_position_id = rows[0]["master_position_id"]
        assert all(r["master_position_id"] == master_position_id for r in rows)
        assert all(r["status"] == "active" for r in rows)
        assert all(r["slave_position_id"] is not None for r in rows)

        by_slave = {r["slave_account_id"]: r for r in rows}
        assert by_slave[SLAVE1_ID]["slave_volume"] == ONE_LOT           # multiplier 1.0
        assert by_slave[SLAVE2_ID]["slave_volume"] == ONE_LOT // 2      # multiplier 0.5

        copy_reqs = _new_order_reqs_to_slaves(server)
        assert len(copy_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in copy_reqs}
        assert by_account[SLAVE1_ID].volume == ONE_LOT
        assert by_account[SLAVE2_ID].volume == ONE_LOT // 2
        for r in copy_reqs:
            assert r.orderType == ProtoOAOrderType.MARKET
            assert r.tradeSide == ProtoOATradeSide.BUY
            assert r.label == f"copy:m{master_position_id}"
            assert r.clientOrderId == f"cm{master_position_id}.{r.ctidTraderAccountId}"

        # A market fill arrives as two execution events (ORDER_ACCEPTED, then
        # ORDER_FILLED); master_event is logged for both -- ORDER_ACCEPTED on
        # a MARKET order normalizes to nothing, ORDER_FILLED to MasterPositionOpened.
        master_events = _events(db, "master_event")
        assert len(master_events) == 2
        opened = [e for e in master_events if e["payload"]["normalized"] == "MasterPositionOpened"]
        assert len(opened) == 1

        filled = [
            e for e in _events(db, "slave_action")
            if e["payload"].get("action") == "position_filled"
        ]
        assert {e["account_id"] for e in filled} == {SLAVE1_ID, SLAVE2_ID}

        # Regression guard for the exact bug this task exists to catch:
        # send_for_account used to call client.send() (not send_no_reply()),
        # whose response Deferred timed out ~5s after every successful send
        # (the real cTrader server never tags a reply to a trade request),
        # which Dispatcher misread as an ambiguous failure and degraded the
        # slave account -- on EVERY successful copy. Wait past that old
        # window and prove neither slave was degraded and no send_failed_*
        # event was logged.
        yield task.deferLater(reactor, 5.5, lambda: None)

        accounts_after = {a.account_id: a.status for a in repo.load_accounts()}
        assert accounts_after[SLAVE1_ID] == 'ok'
        assert accounts_after[SLAVE2_ID] == 'ok'

        failed_sends = [
            e for e in _events(db, "slave_action")
            if str(e["payload"].get("action", "")).startswith("send_failed")
        ]
        assert failed_sends == []
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_master_close_closes_slave_copies(db):
    """Master closes its position in full; both slave copies close too.

    See test_partial_close_closes_same_fraction for a close of less than
    the full position (the proportional-volume path).
    """
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(_active_rows(repo)) >= 2)

        opened = repo.mapping_rows()
        master_position_id = opened[0]["master_position_id"]
        slave_position_id = {r["slave_account_id"]: r["slave_position_id"] for r in opened}
        slave_volume = {r["slave_account_id"]: r["slave_volume"] for r in opened}

        _fire_and_forget(client, _close_position(MASTER_ID, master_position_id, ONE_LOT))

        yield _wait_until(
            lambda: sum(1 for r in repo.mapping_rows() if r["status"] == "closed") >= 2
        )

        close_reqs = [
            r for r in server.requests
            if isinstance(r, ProtoOAClosePositionReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
        ]
        assert len(close_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in close_reqs}
        for slave_id in (SLAVE1_ID, SLAVE2_ID):
            assert by_account[slave_id].positionId == slave_position_id[slave_id]
            assert by_account[slave_id].volume == slave_volume[slave_id]

        final_rows = repo.mapping_rows()
        assert len(final_rows) == 2
        assert all(r["status"] == "closed" for r in final_rows)
        assert all(r["slave_volume"] == 0 for r in final_rows)
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_partial_close_closes_same_fraction(db):
    """Master closes HALF its position; each slave receives a
    ProtoOAClosePositionReq for the SAME fraction of ITS OWN position
    (scaled by its multiplier, not by the master's raw volume), and its
    mapping stays 'active' with slave_volume reduced accordingly -- not
    closed outright.

    Exercises domain/sizing.py:partial_close_volume via
    engine/normalize.py's MasterPositionClosed.remaining_volume, which
    FakeCTraderServer now tracks per position (see
    testing/fake_server.py:_handle_close_position_req).
    """
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(_active_rows(repo)) >= 2)

        opened = repo.mapping_rows()
        master_position_id = opened[0]["master_position_id"]
        slave_position_id = {r["slave_account_id"]: r["slave_position_id"] for r in opened}

        half_of_master = ONE_LOT // 2   # 5_000_000
        expected_slave_close = {
            SLAVE1_ID: 5_000_000,   # multiplier 1.0: half of slave's 10_000_000
            SLAVE2_ID: 2_500_000,   # multiplier 0.5: half of slave's 5_000_000
        }
        expected_remaining = {
            SLAVE1_ID: 5_000_000,
            SLAVE2_ID: 2_500_000,
        }

        _fire_and_forget(client, _close_position(MASTER_ID, master_position_id, half_of_master))

        yield _wait_until(
            lambda: sum(
                1 for r in server.requests
                if isinstance(r, ProtoOAClosePositionReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
            ) >= 2
        )

        close_reqs = [
            r for r in server.requests
            if isinstance(r, ProtoOAClosePositionReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
        ]
        assert len(close_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in close_reqs}
        for slave_id in (SLAVE1_ID, SLAVE2_ID):
            assert by_account[slave_id].positionId == slave_position_id[slave_id]
            assert by_account[slave_id].volume == expected_slave_close[slave_id]

        yield _wait_until(
            lambda: all(
                r["slave_volume"] == expected_remaining[r["slave_account_id"]]
                for r in repo.mapping_rows()
            )
        )

        final_rows = repo.mapping_rows()
        assert len(final_rows) == 2
        # Partial close: mapping stays active (not closed), volume reduced
        # by the proportional amount -- not zeroed out.
        assert all(r["status"] == "active" for r in final_rows)
        by_slave = {r["slave_account_id"]: r for r in final_rows}
        assert by_slave[SLAVE1_ID]["slave_volume"] == expected_remaining[SLAVE1_ID]
        assert by_slave[SLAVE2_ID]["slave_volume"] == expected_remaining[SLAVE2_ID]
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_sltp_amend_propagates(db):
    """Master SL/TP change on an open position propagates to both copies."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(_active_rows(repo)) >= 2)

        opened = repo.mapping_rows()
        master_position_id = opened[0]["master_position_id"]
        slave_position_id = {r["slave_account_id"]: r["slave_position_id"] for r in opened}

        new_sl, new_tp = 1.0500, 1.1200
        _fire_and_forget(
            client, _amend_sltp(MASTER_ID, master_position_id, stop_loss=new_sl, take_profit=new_tp)
        )

        yield _wait_until(
            lambda: sum(
                1 for r in server.requests
                if isinstance(r, ProtoOAAmendPositionSLTPReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
            ) >= 2
        )

        amend_reqs = [
            r for r in server.requests
            if isinstance(r, ProtoOAAmendPositionSLTPReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
        ]
        assert len(amend_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in amend_reqs}
        for slave_id in (SLAVE1_ID, SLAVE2_ID):
            req = by_account[slave_id]
            assert req.positionId == slave_position_id[slave_id]
            assert req.stopLoss == pytest.approx(new_sl)
            assert req.takeProfit == pytest.approx(new_tp)
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_pending_order_lifecycle(db):
    """LIMIT placed -> both slaves get a mirrored LIMIT; replace and cancel
    on the master propagate the same way; order mappings are tracked
    throughout (active with slave_order_id, then closed on cancel)."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        entry_price = 1.05000
        _fire_and_forget(client, _limit_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT, entry_price))

        yield _wait_until(lambda: len(_active_rows(repo)) >= 2)

        placed = repo.mapping_rows()
        assert len(placed) == 2
        master_order_id = placed[0]["master_order_id"]
        assert all(r["master_order_id"] == master_order_id for r in placed)
        assert all(r["status"] == "active" for r in placed)
        assert all(r["slave_position_id"] is None for r in placed)   # unfilled, still pending
        slave_order_id = {r["slave_account_id"]: r["slave_order_id"] for r in placed}
        assert all(v is not None for v in slave_order_id.values())

        place_reqs = [
            r for r in _new_order_reqs_to_slaves(server) if r.orderType == ProtoOAOrderType.LIMIT
        ]
        assert len(place_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in place_reqs}
        assert by_account[SLAVE1_ID].volume == ONE_LOT
        assert by_account[SLAVE2_ID].volume == ONE_LOT // 2
        for r in place_reqs:
            assert r.limitPrice == pytest.approx(entry_price)
            assert r.label == f"copy:o{master_order_id}"
            assert r.clientOrderId == f"co{master_order_id}.{r.ctidTraderAccountId}"

        # --- replace: bigger size, new price ---
        replaced_volume = 4_000_000
        replaced_price = 1.04000
        _fire_and_forget(
            client, _amend_order(MASTER_ID, master_order_id, volume=replaced_volume, limit_price=replaced_price)
        )

        yield _wait_until(
            lambda: sum(
                1 for r in server.requests
                if isinstance(r, ProtoOAAmendOrderReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
            ) >= 2
        )

        amend_reqs = [
            r for r in server.requests
            if isinstance(r, ProtoOAAmendOrderReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
        ]
        assert len(amend_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in amend_reqs}
        assert by_account[SLAVE1_ID].orderId == slave_order_id[SLAVE1_ID]
        assert by_account[SLAVE1_ID].volume == replaced_volume
        assert by_account[SLAVE2_ID].orderId == slave_order_id[SLAVE2_ID]
        assert by_account[SLAVE2_ID].volume == replaced_volume // 2
        for r in amend_reqs:
            assert r.limitPrice == pytest.approx(replaced_price)

        # --- cancel: both slave copies cancelled, mappings close ---
        _fire_and_forget(client, _cancel_order(MASTER_ID, master_order_id))

        yield _wait_until(
            lambda: sum(1 for r in repo.mapping_rows() if r["status"] == "closed") >= 2
        )

        cancel_reqs = [
            r for r in server.requests
            if isinstance(r, ProtoOACancelOrderReq) and r.ctidTraderAccountId in (SLAVE1_ID, SLAVE2_ID)
        ]
        assert len(cancel_reqs) == 2
        by_account = {r.ctidTraderAccountId: r for r in cancel_reqs}
        assert by_account[SLAVE1_ID].orderId == slave_order_id[SLAVE1_ID]
        assert by_account[SLAVE2_ID].orderId == slave_order_id[SLAVE2_ID]

        final_rows = repo.mapping_rows()
        assert len(final_rows) == 2
        assert all(r["status"] == "closed" for r in final_rows)
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_master_rejection_is_noop(db):
    """A rejected master order replicates as a no-op: no fan-out, no mappings,
    exactly one master_event log row."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        server.reject_next_order = True
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))

        yield _wait_until(lambda: len(_events(db, "master_event")) >= 1)

        master_events = _events(db, "master_event")
        assert len(master_events) == 1
        assert master_events[0]["payload"]["normalized"] == "MasterRejected"

        assert _new_order_reqs_to_slaves(server) == []
        assert repo.mapping_rows() == []
        assert _events(db, "slave_action") == []
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_dry_run_sends_nothing_but_logs(db):
    """Dry-run mode creates (pending) mappings and logs would-be requests, but
    never puts a slave order on the wire."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        app.set_dry_run(True)
        assert repo.get_settings().dry_run is True

        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))

        yield _wait_until(
            lambda: sum(1 for r in repo.mapping_rows() if r["status"] == "pending") >= 2
        )

        assert _new_order_reqs_to_slaves(server) == []   # dry-run never reaches the wire

        rows = repo.mapping_rows()
        assert len(rows) == 2
        assert all(r["status"] == "pending" for r in rows)
        assert all(r["slave_position_id"] is None for r in rows)

        dry_run_events = [e for e in _events(db, "slave_action") if e["payload"].get("dry_run") is True]
        assert {e["account_id"] for e in dry_run_events} == {SLAVE1_ID, SLAVE2_ID}
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_kill_switch_blocks_fanout(db):
    """copying_enabled=false (via app.pause()) suppresses all fan-out: the
    master event is still logged and decided, but nothing is sent and no
    mapping rows are created."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        client = app.clients[False][0]

        yield app.pause()
        assert repo.get_settings().copying_enabled is False

        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))

        yield _wait_until(
            lambda: sum(
                1 for e in _events(db, "slave_action")
                if e["payload"].get("skipped") == "kill_switch"
            ) >= 2
        )

        assert _new_order_reqs_to_slaves(server) == []
        assert repo.mapping_rows() == []

        # A market fill is two execution events (ACCEPTED then FILLED);
        # master_event is logged for both regardless of the kill switch.
        master_events = _events(db, "master_event")
        assert len(master_events) == 2
        opened = [e for e in master_events if e["payload"]["normalized"] == "MasterPositionOpened"]
        assert len(opened) == 1
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_reconnect_reauths_and_resyncs(db):
    """A dropped connection re-authenticates every account automatically
    (CTraderClient's own reconnect handling); an explicit resync() afterwards
    proves the connection is live again by surfacing a scripted broker/mapping
    mismatch (an orphan slave position) as drift."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()
        assert app.reconciler.current == []

        auths_before = {
            acc: server.account_auths.count(acc) for acc in (MASTER_ID, SLAVE1_ID, SLAVE2_ID)
        }

        # Script an orphan: a slave position labeled as a copy but with no
        # corresponding mapping row (e.g. it was opened while the copier was
        # down and never replayed).
        server.open_positions[SLAVE1_ID] = [{
            "position_id": 424242,
            "symbol_id": SYMBOL_ID,
            "volume": 100_000,
            "trade_side": ProtoOATradeSide.BUY,
            "label": "copy:m999999",
        }]

        server.drop_all_connections()

        yield _wait_until(
            lambda: all(
                server.account_auths.count(acc) > auths_before[acc]
                for acc in (MASTER_ID, SLAVE1_ID, SLAVE2_ID)
            ),
            timeout=20.0,
        )

        items = yield app.resync()

        orphan_items = [i for i in items if i.kind == "orphan_slave_position"]
        assert len(orphan_items) == 1
        assert orphan_items[0].account_id == SLAVE1_ID
        assert orphan_items[0].position_id == 424242
        assert app.reconciler.current == items

        matching_drift_events = [
            e for e in _events(db, "drift")
            if e["payload"].get("drift_kind") == "orphan_slave_position"
            and e["payload"].get("position_id") == 424242
        ]
        assert len(matching_drift_events) == 1
        assert matching_drift_events[0]["account_id"] == SLAVE1_ID
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_trade_dispatched_during_reconnect_reauth_window_retries_and_lands(db):
    """NEW-1 regression: send_no_reply's instant=True write (Fix Round 2)
    can reach the wire in the same reactor turn a connection is confirmed
    -- before THAT connection's own account-auth round trip for the target
    account has completed. Before instant=True, the single FIFO drain queue
    incidentally serialized every trade send behind the account-auth
    request queued moments earlier on the same reconnect; instant=True
    removed that accidental protection.

    FakeCTraderServer.enforce_auth=True makes this reachable in CI: it
    rejects a trade request for an account not yet account-authed on the
    connection it arrived on, faithful to real cTrader (which the
    unenforced fake -- the default everywhere else -- would otherwise
    silently accept regardless of auth state, hiding the race entirely).

    Dispatches a SlaveIntent directly via app.dispatcher.dispatch(),
    deterministically inside the window: waits for CTraderClient to have
    actually detected the disconnect (is_account_authed() false --
    _on_disconnected clears it) before dispatching, so the send is
    guaranteed to land on a genuinely not-yet-re-authed connection rather
    than a stale, already-dying one whose broadcast responses would go
    nowhere once the server finishes tearing it down (a test-harness race
    to avoid, distinct from the production race under test). From that
    point, CTraderClient._authed_accounts stays empty until the automatic
    reconnect + re-auth (real reactor time) completes.

    Without main.py's is_account_authed() gate (Fix Round 3),
    send_for_account would let send_no_reply through regardless,
    FakeCTraderServer would reject it, and -- since send_no_reply's own
    Deferred still reports success once the write is handed to a connected
    transport -- Dispatcher would never see a failure to retry: the mapping
    would stay 'pending' forever, no event, no alert, no degraded status.
    With the gate, the premature attempt raises SendNotAttempted (nothing
    reached the wire), Dispatcher retries at 1s/2s/4s, and the retry lands
    once real reconnect+re-auth (typically ~1-2s, well inside the ~7s retry
    budget) actually completes.
    """
    server, repo, app = _setup(db)
    server.enforce_auth = True
    try:
        yield app.startup()
        client = app.clients[False][0]

        server.drop_all_connections()

        # Wait for the disconnect to actually register client-side (see
        # the docstring) before firing, so the send is deterministically
        # inside the not-yet-re-authed window rather than racing detection
        # of the disconnect itself.
        yield _wait_until(lambda: not client.is_account_authed(SLAVE1_ID), timeout=10.0)

        intent = OpenMarket(
            slave_account_id=SLAVE1_ID,
            master_position_id=999001,
            symbol_id=SYMBOL_ID,
            side=Side.BUY,
            volume=ONE_LOT,
            stop_loss=None,
            take_profit=None,
            label="copy:m999001",
        )
        app.dispatcher.dispatch([intent])

        yield _wait_until(
            lambda: any(
                r["status"] == "active"
                for r in repo.mapping_rows()
                if r["master_position_id"] == 999001
            ),
            timeout=20.0,
        )

        rows = [r for r in repo.mapping_rows() if r["master_position_id"] == 999001]
        assert len(rows) == 1
        assert rows[0]["status"] == "active"
        assert rows[0]["slave_position_id"] is not None

        landed = [
            r for r in server.requests
            if isinstance(r, ProtoOANewOrderReq) and r.ctidTraderAccountId == SLAVE1_ID
            and r.label == "copy:m999001"
        ]
        assert len(landed) >= 1
        assert landed[-1].volume == ONE_LOT

        # Never degraded: the retry ladder carried it past the window
        # instead of exhausting into a permanent failure.
        accounts_after = {a.account_id: a.status for a in repo.load_accounts()}
        assert accounts_after[SLAVE1_ID] == 'ok'
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_reload_fetches_symbols_for_accounts_added_after_boot(db):
    """Regression test for task-30 review finding I1(a).

    CopierApp.startup() with ZERO accounts (the real boot sequence when
    accounts only arrive later -- via discover(), the dashboard, or a
    direct insert, exactly like task-30's compose e2e) returns before ever
    calling _fetch_and_cache_symbols (main.py:100-103). Accounts added
    after that only ever traverse reload() -- which, before this fix,
    never called _fetch_and_cache_symbols either. Consequence:
    master_symbols_by_id stayed empty forever, so normalize()'s
    unknown-symbol gate silently dropped EVERY master event
    (engine/normalize.py:30-34), and every slave's symbol_cache stayed
    empty, so mirror_volume had no lot_size/step_volume to size a copy
    with. Proves both the direct symptom (symbol_cache / master_symbols_by_id
    populated after reload()) and the actual consequence the brief cares
    about: a real master fill dispatched AFTER reload() fans out to both
    slaves at the correct volumes -- the exact thing that was silently
    broken end-to-end before this fix, and the exact thing task-30's
    compose-level e2e (e2e/test_full_stack.py) exercises against the real
    stack. Confirmed RED against the pre-fix reload() (see
    task-30-report.md's "Fix Round 1" section for the revert-and-rerun
    evidence: this test fails with an empty master_symbols_by_id, an empty
    symbol_cache, and zero mapping rows when the
    `_fetch_and_cache_symbols` call is removed from reload()).
    """
    server, repo, app, connection_id = _setup_empty(db)
    try:
        yield app.startup()  # zero accounts: hits the early-return branch, exactly like a real boot
        assert app.master_symbols_by_id == {}
        assert repo.load_symbol_cache(SLAVE1_ID) == {}

        _insert_accounts(db, connection_id)
        yield app.reload()

        # Direct symptom: the bug's own diagnosis.
        assert app.master_symbols_by_id, (
            "master_symbols_by_id still empty after reload() -- normalize() "
            "would silently drop every master event"
        )
        slave1_symbols = repo.load_symbol_cache(SLAVE1_ID)
        assert slave1_symbols, (
            "slave 101's symbol_cache still empty after reload() -- mirror_volume "
            "has no lot_size/step_volume to size a copy with"
        )
        assert slave1_symbols["EURUSD"].lot_size == 10_000_000
        slave2_symbols = repo.load_symbol_cache(SLAVE2_ID)
        assert slave2_symbols and slave2_symbols["EURUSD"].lot_size == 10_000_000

        # The actual consequence: a real trade dispatched after reload()
        # now fans out to both slaves at the correct volumes, exactly like
        # test_master_fill_fans_out_and_maps proves for the ordinary
        # (accounts-seeded-before-startup) path.
        client = app.clients[False][0]
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(_active_rows(repo)) >= 2)

        rows = repo.mapping_rows()
        by_slave = {r["slave_account_id"]: r for r in rows}
        assert by_slave[SLAVE1_ID]["slave_volume"] == ONE_LOT           # multiplier 1.0
        assert by_slave[SLAVE2_ID]["slave_volume"] == ONE_LOT // 2      # multiplier 0.5
    finally:
        _teardown(app, server)


@pytest.mark.timeout(45)
@pytest_twisted.inlineCallbacks
def test_reload_skips_symbol_fetch_for_already_cached_accounts(db):
    """Regression test for task-30 review finding I2.

    reload() sits on the hot path of every PUT /api/settings and every
    pause()/resume() (settings_control.py / main.py:191,206) -- so once an
    account's symbol_cache has been populated at least once, a SECOND
    reload() with no new accounts must do ZERO broker symbol round trips
    for it, not silently re-fetch the full map on every single operator
    action. Proves this by monkeypatching fetch_symbol_map (imported into
    copier.main) to count calls: the first reload() (fresh accounts, empty
    cache) must fetch; a second reload() right after (same accounts,
    now-populated cache, nothing changed) must fetch nothing at all.
    """
    import copier.main as main_module

    server, repo, app = _setup(db)
    try:
        yield app.startup()

        fetch_calls = []
        real_fetch_symbol_map = main_module.fetch_symbol_map

        def _counting_fetch_symbol_map(client, account_id):
            fetch_calls.append(account_id)
            return real_fetch_symbol_map(client, account_id)

        main_module.fetch_symbol_map = _counting_fetch_symbol_map
        try:
            # First reload after startup: startup() already force-fetched
            # everything, so this reload should ALSO fetch nothing (every
            # account is already cached) -- confirms cache-miss-only, not
            # just "skips a literal no-op second call".
            yield app.reload()
            assert fetch_calls == [], (
                f"reload() re-fetched symbols for already-cached accounts: {fetch_calls}"
            )

            # A second reload() with still nothing new must also be a
            # zero-fetch no-op.
            yield app.reload()
            assert fetch_calls == [], (
                f"a second reload() with no new accounts fetched symbols: {fetch_calls}"
            )
        finally:
            main_module.fetch_symbol_map = real_fetch_symbol_map
    finally:
        _teardown(app, server)
