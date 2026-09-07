"""End-to-end tests for the MT5 lane: the REAL CopierApp (build_app) with
a FakeCTraderServer on one side and the FakeEA on the other.

The fake EA talks to the app the way the api would proxy it (hello_fn /
sync_fn call app.mt5_hello / app.mt5_sync directly -- the control route
and the api door are unit-tested elsewhere), executes every command in
the response against its in-memory hedging or netting book, and reports
back on the next tick. Outcomes are asserted from real Postgres state, the recorded
cTrader wire traffic and the EA's book -- never from internal mocks.
"""

from datetime import datetime, timedelta

import psycopg
import pytest
import pytest_twisted
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAClosePositionReq, ProtoOANewOrderReq)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from twisted.internet import reactor, task

import copier.main as main_module
import copier.mt5.outbox as outbox_module
from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.main import build_app
from copier.testing.fake_ea import FakeEA
from copier.testing.fake_server import FakeCTraderServer

from integration.test_copier_e2e import (
    ACCESS_TOKEN, FERNET_KEY, MASTER_ID, ONE_LOT, ORG_ID, SLAVE1_ID, SLAVE2_ID, SYMBOL_ID,
    _amend_sltp, _close_position, _events, _fire_and_forget, _insert_accounts, _market_order,
    _seed_org, _teardown, _wait_until)

XAUUSD_ONLY = [{"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01, "vmax": 50,
                "tm": 4}]


def _insert_ctrader_slaves(dsn, connection_id, org_id):
    """Slaves 101 (1.0x) and 102 (0.5x) only -- for the MT5-master scenario."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                  trader_login, is_live, role, enabled, multiplier)
            VALUES (%(s1)s, %(org)s, %(conn)s, 90101, false, 'slave', true, 1.0),
                   (%(s2)s, %(org)s, %(conn)s, 90102, false, 'slave', true, 0.5)
            """,
            {"s1": SLAVE1_ID, "s2": SLAVE2_ID, "org": org_id, "conn": connection_id})


def _insert_mt5_account(dsn, org_id, role):
    with psycopg.connect(dsn, autocommit=True) as conn:
        (account_id,) = conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id,
                                  trader_login, is_live, role, enabled, multiplier, platform)
            VALUES (nextval('mt5_account_id_seq'), NULL, %s, 0, false, %s, true, 1.0, 'mt5')
            RETURNING ctid_trader_account_id
            """, (org_id, role)).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, %s)",
                     (account_id, f"hash-{account_id}"))
    return account_id


def _setup_bridge(dsn, *, mt5_role="slave", ea_symbols=None, ea_mode="hedging"):
    """FakeCTraderServer + real CopierApp + FakeEA.

    mt5_role='slave': cTrader master 100 with slaves 101/102 and the MT5
    slave. mt5_role='master': the MT5 master with cTrader slaves 101/102.
    ea_mode: the fake terminal's book, 'hedging' or 'netting'.
    Returns (server, repo, app, ea, mt5_id); the caller yields
    app.startup(), then ea.tick() once for the hello (the hello auto-matches
    against symbol caches startup fills), and eventually calls _teardown.
    """
    server = FakeCTraderServer(auto_fill=True)
    port = server.listen(reactor)
    org_id = _seed_org(dsn)
    token_store = TokenStore(dsn, FERNET_KEY)
    connection_id = token_store.save_grant(
        ACCESS_TOKEN, "e2e-refresh-token", datetime.utcnow() + timedelta(days=60), org_id)
    if mt5_role == "slave":
        _insert_accounts(dsn, connection_id, org_id)
    else:
        _insert_ctrader_slaves(dsn, connection_id, org_id)
    mt5_id = _insert_mt5_account(dsn, org_id, mt5_role)
    server.accounts = {MASTER_ID: ACCESS_TOKEN, SLAVE1_ID: ACCESS_TOKEN, SLAVE2_ID: ACCESS_TOKEN}
    repo = Repo(dsn)

    def client_factory(is_live: bool) -> CTraderClient:
        return CTraderClient(make_sdk_client("127.0.0.1", port), "e2e-client-id",
                             "e2e-client-secret")

    app = build_app(repo, token_store, client_factory, shards=1)
    ea = FakeEA(account_id=mt5_id,
                hello_fn=lambda body: app.mt5_hello(mt5_id, body),
                sync_fn=lambda body: app.mt5_sync(mt5_id, body),
                symbols=ea_symbols, mode=ea_mode)
    return server, repo, app, ea, mt5_id


class _Ticker:
    """Polls the fake EA on the reactor like its timer would (faster, so
    tests finish); a tick that raises is recorded, not swallowed."""

    def __init__(self, ea, interval=0.1):
        self.ea, self.errors = ea, []
        self._call = task.LoopingCall(self._tick)
        self._call.clock = reactor
        self._call.start(interval, now=False)

    def _tick(self):
        try:
            self.ea.tick()
        except Exception as e:      # pragma: no cover - surfaced by the assertions
            self.errors.append(e)

    def stop(self):
        if self._call.running:
            self._call.stop()


def _mt5_rows(repo, mt5_id, status=None):
    return [r for r in repo.mapping_rows()
            if r["slave_account_id"] == mt5_id and (status is None or r["status"] == status)]


def _all_commands(repo, mt5_id):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM mt5_commands WHERE account_id = %s ORDER BY id", (mt5_id,)
            ).fetchall()


def _reqs(server, req_type, account_id):
    return [r for r in server.requests
            if isinstance(r, req_type) and r.ctidTraderAccountId == account_id]


def _sleep(seconds):
    return task.deferLater(reactor, seconds, lambda: None)


@pytest_twisted.inlineCallbacks
def _open_one_copy(app, repo, ea, mt5_id):
    """Master buys ONE_LOT; wait for the MT5 copy to go active. Returns
    (master_position_id, slave ticket)."""
    client = app.clients[False][0]
    _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
    yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 1)
    (row,) = _mt5_rows(repo, mt5_id, "active")
    return row["master_position_id"], row["slave_position_id"]


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_ctrader_master_fill_opens_an_mt5_copy_that_inherits_protection(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()                                            # hello + first sync
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r"}
        ticker = _Ticker(ea)

        master_position_id, ticket = yield _open_one_copy(app, repo, ea, mt5_id)

        (row,) = _mt5_rows(repo, mt5_id, "active")
        assert row["slave_volume"] == 100                          # 1.00 lot in centilots
        assert row["fill_price"] == pytest.approx(1.10010)         # the fake EA's ask
        pos = ea.positions[ticket]
        assert (pos["s"], pos["side"], pos["lots"], pos["comment"]) == (
            "EURUSD.r", "BUY", 1.0, f"copy:m{master_position_id}")
        assert len(ea.executed) == 1

        # cTrader protects a market position with a SECOND event, after the
        # fill; the copy inherits it through an amend command.
        _fire_and_forget(app.clients[False][0],
                         _amend_sltp(MASTER_ID, master_position_id, stop_loss=1.0900,
                                     take_profit=1.1200))
        yield _wait_until(lambda: (ea.positions[ticket]["sl"], ea.positions[ticket]["tp"]) == (1.09, 1.12))
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_master_partial_close_and_amend_propagate_to_the_mt5_copy(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        master_position_id, ticket = yield _open_one_copy(app, repo, ea, mt5_id)
        client = app.clients[False][0]

        _fire_and_forget(client, _close_position(MASTER_ID, master_position_id, ONE_LOT // 2))
        yield _wait_until(lambda: ea.positions.get(ticket, {}).get("lots") == 0.5)
        yield _wait_until(lambda: _mt5_rows(repo, mt5_id)[0]["slave_volume"] == 50)
        assert _mt5_rows(repo, mt5_id)[0]["status"] == "active"
        (close,) = [c for c in ea.executed if c.kind == "close"]
        assert close.payload == {"position": ticket, "lots": 0.5}

        _fire_and_forget(client, _amend_sltp(MASTER_ID, master_position_id, stop_loss=1.0950))
        yield _wait_until(lambda: ea.positions[ticket]["sl"] == 1.095)
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_mt5_master_deals_copy_to_ctrader_followers(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, mt5_role="master")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r"}
        ticker = _Ticker(ea)

        ticket = ea.market_order("EURUSD.r", "BUY", 0.5)     # the owner trades in the terminal
        yield _wait_until(lambda: len(_reqs(server, ProtoOANewOrderReq, SLAVE1_ID)) == 1
                          and len(_reqs(server, ProtoOANewOrderReq, SLAVE2_ID)) == 1)
        s1 = _reqs(server, ProtoOANewOrderReq, SLAVE1_ID)[0]
        s2 = _reqs(server, ProtoOANewOrderReq, SLAVE2_ID)[0]
        assert (s1.volume, s2.volume) == (ONE_LOT // 2, ONE_LOT // 4)   # 0.5 lot x 1.0 and x 0.5
        assert s1.symbolId == SYMBOL_ID and s1.clientOrderId == f"cm{ticket}.{SLAVE1_ID}"
        yield _wait_until(lambda: len([r for r in repo.mapping_rows() if r["status"] == "active"]) == 2)
        master_events = [e for e in _events(db, "master_event")
                         if e["payload"].get("source") == "mt5"]
        assert [e["payload"]["normalized"] for e in master_events] == ["MasterPositionOpened"]

        ea.close_by_terminal(ticket)
        yield _wait_until(lambda: len(_reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 1
                          and len(_reqs(server, ProtoOAClosePositionReq, SLAVE2_ID)) == 1)
        yield _wait_until(lambda: all(r["status"] == "closed" for r in repo.mapping_rows()))
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_trade_page_order_on_mt5_executes_in_the_terminal(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)

        result = app.place_order({"account_id": mt5_id, "symbol": "EURUSD.r", "side": "SELL",
                                  "order_type": "MARKET", "volume_lots": 0.2,
                                  "actor_email": "ada@example.com"})
        assert result["status"] == "submitted" and result["volume"] == 20
        yield _wait_until(lambda: len(ea.positions) == 1)
        (pos,) = ea.positions.values()
        assert (pos["s"], pos["side"], pos["lots"], pos["comment"]) == ("EURUSD.r", "SELL", 0.2, "manual")
        yield _wait_until(lambda: any(e["payload"].get("action") == "manual_fill"
                                      for e in _events(db, "slave_action")))
        assert _mt5_rows(repo, mt5_id) == []                     # a manual order maps to nothing

        app.place_order({"account_id": mt5_id, "symbol": "EURUSD.r", "side": "BUY",
                         "order_type": "LIMIT", "volume_lots": 0.1, "limit_price": 1.0950})
        yield _wait_until(lambda: len(ea.orders) == 1)
        (order,) = ea.orders.values()
        assert (order["type"], order["lots"], order["price"]) == ("BUY_LIMIT", 0.1, 1.095)

        # The Positions screen sees the terminal's book through the registry.
        state = app.get_state(ORG_ID)
        assert [p["side"] for p in state["accounts"][mt5_id]["positions"]] == ["SELL"]
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_close_all_flattens_ctrader_and_mt5_with_verified_counts(db, monkeypatch):
    monkeypatch.setattr(main_module, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    monkeypatch.setattr(main_module, "FLATTEN_SETTLE_S", 0.5)
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    for account_id, position_id in ((MASTER_ID, 7001), (SLAVE1_ID, 7002)):
        server.open_positions.setdefault(account_id, []).append({
            "position_id": position_id, "symbol_id": SYMBOL_ID, "volume": 100_000,
            "trade_side": ProtoOATradeSide.BUY, "label": ""})
        server._position_volumes[position_id] = 100_000
    ea.market_order("EURUSD.r", "BUY", 0.3)
    ea.market_order("XAUUSD.r", "SELL", 0.1)
    ea.place_pending_by_terminal("EURUSD.r", "BUY_LIMIT", 0.1, 1.09)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)

        result = yield app.close_all(ORG_ID)

        assert result["paused"] is False
        by_account = {s["account_id"]: s for s in result["accounts"]}
        assert by_account[MASTER_ID]["positions_closed"] == 1
        assert by_account[SLAVE1_ID]["positions_closed"] == 1
        mt5 = by_account[mt5_id]
        assert (mt5["positions_closed"], mt5["orders_cancelled"], mt5["positions_remaining"],
                mt5["orders_remaining"], mt5["error"]) == (2, 1, [], [], None)
        assert ea.positions == {} and ea.orders == {}
        assert repo.get_org(ORG_ID).copying_enabled is True
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_terminal_offline_expires_the_open_but_the_close_survives(db, monkeypatch):
    monkeypatch.setattr(outbox_module, "OPEN_COMMAND_TTL_S", 0.5)
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        master_position_id, ticket = yield _open_one_copy(app, repo, ea, mt5_id)
        client = app.clients[False][0]

        ea.disconnect()
        _fire_and_forget(client, _close_position(MASTER_ID, master_position_id, ONE_LOT))
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.SELL, ONE_LOT))
        yield _wait_until(lambda: len(repo.mt5_commands_open(mt5_id)) == 2)
        yield _sleep(0.7)                                        # past the open's TTL
        ea.reconnect()

        yield _wait_until(lambda: ticket not in ea.positions)
        yield _wait_until(lambda: {r["status"] for r in _mt5_rows(repo, mt5_id)} == {"closed", "failed"})
        failed = next(r for r in _mt5_rows(repo, mt5_id) if r["status"] == "failed")
        assert failed["error"] == "terminal offline"
        assert ea.positions == {} and [c.kind for c in ea.executed] == ["open", "close"]
        rows = _all_commands(repo, mt5_id)
        assert sorted((r["kind"], r["status"]) for r in rows) == [
            ("close", "done"), ("open", "done"), ("open", "failed")]
        (expired,) = [r for r in rows if r["status"] == "failed"]
        assert expired["result"]["message"] == "terminal offline"
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_a_redelivered_command_is_reacked_not_reexecuted_after_a_restart(db, monkeypatch):
    monkeypatch.setattr(outbox_module, "REDELIVER_AFTER_S", 0.2)
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    try:
        yield app.startup()
        ea.tick()
        client = app.clients[False][0]
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(repo.mt5_commands_open(mt5_id)) == 1)

        ea.tick()                     # the open is delivered and executed; its ack waits for the next sync
        assert len(ea.positions) == 1 and len(ea.executed) == 1
        ea.restart()                  # ...and the terminal restarts before that sync
        ea.tick()                     # hello again, then a sync with no ack
        assert _mt5_rows(repo, mt5_id, "active") == []
        yield _sleep(0.3)
        ea.tick()                     # the copier re-delivers; the EA re-acks from its file
        assert len(ea.executed) == 1 and len(ea.positions) == 1
        ea.tick()                     # the re-ack arrives

        (row,) = _mt5_rows(repo, mt5_id, "active")
        assert row["slave_position_id"] in ea.positions
        (command,) = _all_commands(repo, mt5_id)
        assert (command["status"], command["attempts"]) == ("done", 2)
    finally:
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_an_unmatched_symbol_alerts_and_queues_nothing(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_symbols=XAUUSD_ONLY)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert repo.load_symbol_aliases(mt5_id) == {}
        ticker = _Ticker(ea)

        _fire_and_forget(app.clients[False][0],
                         _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(_reqs(server, ProtoOANewOrderReq, SLAVE1_ID)) == 1)   # cTrader slaves still copy
        yield _wait_until(lambda: any(
            e["account_id"] == mt5_id and e["severity"] == "warning" and "EURUSD" in str(e["payload"])
            for e in _events(db, "slave_action")))
        yield _sleep(0.5)
        assert _all_commands(repo, mt5_id) == [] and _mt5_rows(repo, mt5_id) == []
        assert ea.positions == {} and ea.executed == []
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


# ---------- netting accounts ----------

def _oldest_first(rows):
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def _active_mappings(repo):
    return [r for r in repo.mapping_rows() if r["status"] == "active"]


@pytest_twisted.inlineCallbacks
def _two_master_positions(server, app, repo, ea, mt5_id):
    """Two SEPARATE same-side master positions (1.00 and 0.50 lots), each
    copied to the MT5 follower. cTrader merges a second same-side market
    fill into the existing position (N2) and the fake server does too, so
    the first position is hidden from the fake's merge lookup while the
    second fills and put back afterwards -- the copier sees two distinct
    master fills, as it would for positions opened through different
    routes. Returns (first_master_id, second_master_id), oldest first."""
    client = app.clients[False][0]
    _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
    yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 1)
    hidden = server.open_positions[MASTER_ID].pop()
    _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT // 2))
    yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 2)
    server.open_positions[MASTER_ID].append(hidden)
    rows = _oldest_first(_mt5_rows(repo, mt5_id, "active"))
    return rows[0]["master_position_id"], rows[1]["master_position_id"]


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_follower_nets_two_copies_and_a_master_close_reduces_its_share(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert ea.hello_body()["hedging"] is False
        assert app.mt5_registry.margin_mode(mt5_id) == "netting"
        assert repo.load_mt5_link(mt5_id)["hedging"] is False
        ticker = _Ticker(ea)

        first_master, second_master = yield _two_master_positions(server, app, repo, ea, mt5_id)

        (net,) = ea.positions.values()                        # ONE net position...
        net_ticket = net["t"]
        assert (net["s"], net["side"], net["lots"]) == ("EURUSD.r", "BUY", 1.5)   # ...its volume the sum
        rows = {r["master_position_id"]: r for r in _mt5_rows(repo, mt5_id, "active")}
        assert rows[first_master]["slave_position_id"] == net_ticket
        assert rows[second_master]["slave_position_id"] == net_ticket
        assert (rows[first_master]["slave_volume"], rows[second_master]["slave_volume"]) == (100, 50)
        assert [c.kind for c in ea.executed] == ["open", "open"]

        # The master closes its FIRST position: the follower's copy of it is
        # closed with an opposite-side open for exactly that copy's volume.
        _fire_and_forget(app.clients[False][0], _close_position(MASTER_ID, first_master, ONE_LOT))
        yield _wait_until(lambda: ea.positions.get(net_ticket, {}).get("lots") == 0.5)
        yield _wait_until(lambda: {r["master_position_id"] for r in _mt5_rows(repo, mt5_id, "active")}
                          == {second_master})

        (close,) = [c for c in ea.executed if c.payload.get("comment", "").startswith("close:")]
        assert (close.kind, close.payload["side"], close.payload["lots"], close.client_order_id) == (
            "open", "SELL", 1.0, f"cm{first_master}.{mt5_id}:close")
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "IN", "OUT"]
        (closed,) = _mt5_rows(repo, mt5_id, "closed")
        assert (closed["master_position_id"], closed["slave_volume"]) == (first_master, 0)
        (active,) = _mt5_rows(repo, mt5_id, "active")
        assert (active["master_position_id"], active["slave_volume"]) == (second_master, 50)
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{first_master}.{mt5_id}", 100)]

        # The master closes its SECOND position too: the follower's last
        # 0.50 goes and the net position is gone, so the EA acks pos 0 (the
        # ticket the terminal shows after the fill: none). The mapping is
        # named by the ack's coid, so it closes all the same.
        _fire_and_forget(app.clients[False][0],
                         _close_position(MASTER_ID, second_master, ONE_LOT // 2))
        yield _wait_until(lambda: ea.positions == {})
        yield _wait_until(lambda: {r["status"] for r in _mt5_rows(repo, mt5_id)} == {"closed"})
        (last_close,) = [c for c in ea.executed
                         if c.client_order_id == f"cm{second_master}.{mt5_id}:close"]
        assert ea._file_acks[last_close.id]["pos"] == 0           # what the EA acked
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "IN", "OUT", "OUT"]
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{first_master}.{mt5_id}", 100), (f"cm{second_master}.{mt5_id}", 50)]
        assert _mt5_rows(repo, mt5_id, "active") == []
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_follower_copies_an_opposite_side_master_position_without_closing_the_older_copy(db):
    """Spec "Opposite positions": the master holds BUY and opens SELL on
    the same symbol. On a netting follower the SELL copy nets against the
    BUY copy inside the one net position -- its fill is an OUT deal on it
    -- yet it is a COPY: its mapping activates on the net ticket and the
    BUY copy, whose master is still open, is untouched. When the master
    then closes the BUY, its ':close' SELL flips the net position (INOUT)
    and only the BUY mapping closes."""
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        client = app.clients[False][0]

        buy_master, net_ticket = yield _open_one_copy(app, repo, ea, mt5_id)
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.SELL, ONE_LOT // 2))
        yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 2)

        (net,) = ea.positions.values()
        assert (net["t"], net["side"], net["lots"]) == (net_ticket, "BUY", 0.5)   # 1.00 BUY - 0.50 SELL
        rows = {r["master_position_id"]: r for r in _mt5_rows(repo, mt5_id, "active")}
        sell_master = next(m for m in rows if m != buy_master)
        assert (rows[buy_master]["slave_position_id"], rows[buy_master]["slave_volume"]) == (
            net_ticket, 100)
        assert (rows[sell_master]["slave_position_id"], rows[sell_master]["slave_volume"]) == (
            net_ticket, 50)
        assert [(c.kind, c.payload["side"], c.payload["comment"]) for c in ea.executed] == [
            ("open", "BUY", f"copy:m{buy_master}"), ("open", "SELL", f"copy:m{sell_master}")]
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "OUT"]
        assert [(c["kind"], c["status"]) for c in _all_commands(repo, mt5_id)] == [
            ("open", "done"), ("open", "done")]
        assert [e for e in _events(db, "slave_action")
                if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id] == []
        (netted,) = [e for e in _events(db, "slave_action")
                     if e["payload"].get("action") == "mt5_netting_opposite_copy"]
        assert (netted["payload"]["client_order_id"], netted["payload"]["entry"],
                netted["payload"]["nets_against"]) == (f"cm{sell_master}.{mt5_id}", "OUT", [buy_master])

        # The master closes the BUY: a ':close' SELL 1.00 against BUY 0.50
        # flips the net position to SELL 0.50 -- the SELL copy's own volume.
        _fire_and_forget(client, _close_position(MASTER_ID, buy_master, ONE_LOT))
        yield _wait_until(lambda: {r["master_position_id"] for r in _mt5_rows(repo, mt5_id, "active")}
                          == {sell_master})
        (net,) = ea.positions.values()
        assert (net["side"], net["lots"]) == ("SELL", 0.5)
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "OUT", "INOUT"]
        (active,) = _mt5_rows(repo, mt5_id, "active")
        assert (active["master_position_id"], active["slave_position_id"],
                active["slave_volume"]) == (sell_master, net_ticket, 50)
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{buy_master}.{mt5_id}", 100)]
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_follower_terminal_close_reduces_the_copies_oldest_first(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        first_master, second_master = yield _two_master_positions(server, app, repo, ea, mt5_id)
        (net,) = ea.positions.values()
        net_ticket = net["t"]

        # The owner's terminal trims 1.00 lot of the 1.50 (a stop on part of
        # it, a manual reduce): the OLDER copy is what goes.
        ea.close_by_terminal(net_ticket, lots=1.0)
        yield _wait_until(lambda: [r["status"] for r in _oldest_first(_mt5_rows(repo, mt5_id))]
                          == ["closed", "active"])
        rows = _oldest_first(_mt5_rows(repo, mt5_id))
        assert (rows[0]["master_position_id"], rows[0]["slave_volume"]) == (first_master, 0)
        assert (rows[1]["master_position_id"], rows[1]["slave_volume"]) == (second_master, 50)

        # Then the net stop takes the rest.
        ea.close_by_terminal(net_ticket)
        yield _wait_until(lambda: {r["status"] for r in _mt5_rows(repo, mt5_id)} == {"closed"})
        assert ea.positions == {}
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{first_master}.{mt5_id}", 100), (f"cm{second_master}.{mt5_id}", 50)]
        assert [c.kind for c in ea.executed] == ["open", "open"]   # nothing queued for the terminal's own closes
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_mt5_master_add_reduce_and_reverse_fan_out_to_ctrader_followers(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, mt5_role="master", ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert app.mt5_registry.margin_mode(mt5_id) == "netting"
        ticker = _Ticker(ea)

        # add, add: one net position on the terminal, TWO virtual master
        # positions in the ledger, two opens per follower
        ea.market_order("EURUSD.r", "BUY", 0.5)
        yield _wait_until(lambda: len(_active_mappings(repo)) == 2)
        # cTrader merges a follower's second same-side fill into its first
        # position (N2) and the fake server does too, which would leave both
        # mappings on ONE slave position (and a close of one would reduce
        # both). Hide the first copies from the fake's merge lookup while the
        # second fills, as _two_master_positions does for the master, so each
        # copy is a position of its own.
        hidden = {a: server.open_positions[a].pop() for a in (SLAVE1_ID, SLAVE2_ID)}
        ea.market_order("EURUSD.r", "BUY", 0.3)
        yield _wait_until(lambda: len(_active_mappings(repo)) == 4)
        for account_id, entry in hidden.items():
            server.open_positions[account_id].append(entry)
        (net,) = ea.positions.values()
        assert (net["side"], net["lots"]) == ("BUY", 0.8)
        ledger = repo.load_net_ledger(mt5_id)
        first_id, second_id = [r["virtual_id"] for r in ledger]
        assert [(r["side"], r["volume_left"]) for r in ledger] == [("BUY", 50), ("BUY", 30)]
        opens = _reqs(server, ProtoOANewOrderReq, SLAVE1_ID)
        assert [o.clientOrderId for o in opens] == [f"cm{first_id}.{SLAVE1_ID}", f"cm{second_id}.{SLAVE1_ID}"]
        assert [o.volume for o in opens] == [ONE_LOT // 2, ONE_LOT * 3 // 10]
        assert [o.volume for o in _reqs(server, ProtoOANewOrderReq, SLAVE2_ID)] == [
            ONE_LOT // 4, ONE_LOT * 3 // 20]                    # the 0.5x follower

        # partial reduce: 0.6 lots out of 0.8 -> the older virtual position
        # closes, the younger loses 0.1: two closes per follower, oldest first
        ea.market_order("EURUSD.r", "SELL", 0.6)
        yield _wait_until(lambda: len(_reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 2)
        closes = _reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)
        assert [c.volume for c in closes] == [ONE_LOT // 2, ONE_LOT // 10]
        assert [(r["virtual_id"], r["volume_left"]) for r in repo.load_net_ledger(mt5_id)] == [
            (second_id, 20)]

        # reversal: SELL 0.5 against 0.2 BUY -> the last virtual position
        # closes and a NEW one opens the other way
        ea.market_order("EURUSD.r", "SELL", 0.5)
        yield _wait_until(lambda: len(_reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 3
                          and len(_reqs(server, ProtoOANewOrderReq, SLAVE1_ID)) == 3)
        assert _reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)[2].volume == ONE_LOT // 5
        (row,) = repo.load_net_ledger(mt5_id)
        assert (row["side"], row["volume_left"]) == ("SELL", 30)
        reopened = _reqs(server, ProtoOANewOrderReq, SLAVE1_ID)[2]
        assert (reopened.tradeSide, reopened.volume, reopened.clientOrderId) == (
            ProtoOATradeSide.SELL, ONE_LOT * 3 // 10, f"cm{row['virtual_id']}.{SLAVE1_ID}")
        (net,) = ea.positions.values()
        assert (net["side"], net["lots"]) == ("SELL", 0.3)
        assert [d["entry"] for d in ea.deals] == ["IN", "IN", "OUT", "INOUT"]
        master_events = [e["payload"]["normalized"] for e in _events(db, "master_event")
                         if e["payload"].get("source") == "mt5"]
        assert master_events == [
            "MasterPositionOpened", "MasterPositionOpened",
            "MasterPositionClosed", "MasterPositionClosed",
            "MasterPositionClosed", "MasterPositionOpened"]
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)
