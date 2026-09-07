"""CopierApp x MT5 (copier/src/copier/main.py): hello/sync/status, the
outbox round trip, deal ingest, an MT5 master, get_state, offline
detection, the auth loops, operator actions and the kill switch. Drives
the REAL app from build_app with StubSdk-backed cTrader clients, exactly
like test_main.py, whose fixtures this reuses. A Twisted Clock stands in
for the reactor so timestamps are deterministic and nothing scheduled
leaks into the next test."""

import zlib

import psycopg
import pytest
import pytest_twisted
from twisted.internet.task import Clock

import copier.main as main
from copier.ctrader.symbols import by_id as symbols_by_id
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.domain.models import AmendPositionSLTP, ClosePosition, OpenMarket, Side
from copier.engine.reconcile import PositionSnapshot
from copier.mt5 import protocol as p

from test_main import (  # noqa: F401  (fixtures are used by name)
    MASTER_A, ORG_A, SLAVE_A1, SLAVE_B1, _events, _seed_symbol_cache, db_seeded, fernet_key,
    make_stub_client_factory, repo, seed_org_b, token_store)

EURUSD_R = {"n": "EURUSD.r", "d": 5, "cs": 100000, "vmin": 0.01, "vstep": 0.01, "vmax": 100,
            "tm": 4}
XAUUSD_R = {"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01, "vmax": 50, "tm": 4}
EURUSD_R_ID = zlib.crc32(b"EURUSD.r") & 0x7FFFFFFF


def _hello(symbols=(EURUSD_R, XAUUSD_R), hedging=True):
    return {"v": 1, "ea": "1.0.0", "build": 4400, "login": 12345678, "broker": "XYZ Ltd",
            "server": "XYZ-Demo", "currency": "USD", "hedging": hedging, "trade_mode": "demo",
            "leverage": 500, "symbols": list(symbols), "chunk": 1, "chunks": 1}


def _sync(seq=1, positions=(), orders=(), deals=(), acks=(), balance=10000.0, equity=None):
    return {"v": 1, "seq": seq, "ts": 1_757_203_200_000 + seq, "balance": balance,
            "equity": balance if equity is None else equity, "margin": 0,
            "margin_free": balance, "positions": list(positions), "orders": list(orders),
            "deals": list(deals), "acks": list(acks)}


def _pos(ticket, symbol="EURUSD.r", side="BUY", lots=1.0, open_price=1.1, sl=0, tp=0,
         price=1.101, pnl=1.0, comment=""):
    return {"t": ticket, "s": symbol, "side": side, "lots": lots, "open": open_price, "sl": sl,
            "tp": tp, "price": price, "pnl": pnl, "swap": 0, "comment": comment,
            "magic": 20260907, "time": 1757203100}


def _deal(ticket, position, entry="IN", side="BUY", lots=1.0, price=1.1, profit=0.0,
          commission=0.0, time_ms=1_757_203_100_456, order=0, comment=""):
    return {"t": ticket, "pos": position, "order": order, "s": "EURUSD.r", "type": side,
            "entry": entry, "lots": lots, "price": price, "profit": profit, "swap": 0,
            "commission": commission, "time": time_ms, "comment": comment, "magic": 20260907}


def _ack(command_id, ok=True, pos=None, deal=None, order=None, price=None, lots=None,
         retcode=10009, msg="done"):
    return {"id": command_id, "ok": ok, "retcode": retcode, "msg": msg, "pos": pos,
            "deal": deal, "order": order, "price": price, "lots": lots}


def _account(repo, account_id):
    return next(a for a in repo.load_accounts() if a.account_id == account_id)


def _commands(repo, mt5_id):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM mt5_commands WHERE account_id = %s ORDER BY id", (mt5_id,)
            ).fetchall()


def _open_intent(mt5_id, master_position_id=42, symbol_id=EURUSD_R_ID, side=Side.BUY):
    return OpenMarket(slave_account_id=mt5_id, master_position_id=master_position_id,
                      symbol_id=symbol_id, side=side, volume=100, stop_loss=1.09,
                      take_profit=1.12, label=f"copy:m{master_position_id}",
                      symbol_name="EURUSD", entry_price=1.1)


@pytest.fixture
def mt5_world(repo, token_store, seed_mt5_account):
    """Org A (cTrader master 999, slaves 100/101) plus one MT5 slave; the
    master's symbol cache holds EURUSD so the hello can auto-match."""
    _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1])
    mt5_id = seed_mt5_account(ORG_A)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1, clock=Clock())
    return repo, mt5_id, app


@pytest.fixture
def netting_master_world(db_seeded, fernet_key, seed_mt5_account):
    """Org B: a NETTING MT5 master and the cTrader slave 200, plus a way to
    build a second app on the same database (a copier restart)."""
    org_b = seed_org_b(db_seeded, fernet_key, with_master=False)
    master_id = seed_mt5_account(org_b, role="master")
    repo = Repo(db_seeded)
    _seed_symbol_cache(repo, [SLAVE_B1])

    def build():
        return main.build_app(repo, TokenStore(db_seeded, fernet_key), make_stub_client_factory(),
                              shards=1, clock=Clock())

    return repo, org_b, master_id, build


class TestHello:
    def test_records_the_terminal_symbols_and_aliases(self, mt5_world):
        repo, mt5_id, app = mt5_world
        assert app.mt5_hello(mt5_id, _hello()) == {"last_deal_ticket": 0}
        link = repo.load_mt5_link(mt5_id)
        assert (link["login"], link["broker"], link["hedging"], link["ea_version"]) == (
            12345678, "XYZ Ltd", True, "1.0.0")
        cache = repo.load_symbol_cache(mt5_id)
        assert cache["EURUSD.r"].lot_size == 100 and cache["EURUSD.r"].symbol_id == EURUSD_R_ID
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r"}
        account = _account(repo, mt5_id)
        assert account.trader_login == 12345678 and account.status == "ok"
        (event,) = _events(repo.dsn, "mt5_hello")
        assert event["account_id"] == mt5_id and event["org_id"] == ORG_A
        # Routing now keys the MT5 slave by the canonical name the master's events carry.
        (slave,) = [s for s in app.routing_provider().slaves_by_org[ORG_A]
                    if s.account_id == mt5_id]
        assert slave.symbols["EURUSD"].name == "EURUSD.r"

    def test_the_watermark_is_returned_so_a_restarted_ea_resumes(self, mt5_world):
        repo, mt5_id, app = mt5_world
        repo.set_mt5_watermark(mt5_id, 700005, 1)
        assert app.mt5_hello(mt5_id, _hello()) == {"last_deal_ticket": 700005}

    def test_a_netting_account_is_accepted_and_never_degraded_for_its_mode(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello(hedging=False))
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == ("ok", None)
        assert repo.load_mt5_link(mt5_id)["hedging"] is False
        assert app.mt5_registry.margin_mode(mt5_id) == "netting"
        text = app.mt5_sync(mt5_id, _sync())
        assert text.startswith("OK\t")
        assert app.mt5_status(mt5_id)["hedging"] is False
        (event,) = _events(repo.dsn, "mt5_hello")
        assert event["payload"]["hedging"] is False

    def test_hello_for_a_ctrader_account_is_refused(self, mt5_world):
        _repo, _mt5_id, app = mt5_world
        with pytest.raises(ValueError):
            app.mt5_hello(SLAVE_A1, _hello())


class TestSyncCommandsAndAcks:
    def test_a_queued_copy_is_delivered_and_its_ack_activates_the_mapping(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)

        text = app.mt5_sync(mt5_id, _sync(seq=1))
        status, commands = p.parse_response(text)
        assert status[0] == "OK" and status[2] == str(p.DEFAULT_POLL_MS) and len(status) == 3
        (cmd,) = commands
        assert cmd.kind == "open" and cmd.payload["symbol"] == "EURUSD.r"
        assert cmd.payload["lots"] == 1.0 and cmd.client_order_id == f"cm42.{mt5_id}"

        text = app.mt5_sync(mt5_id, _sync(
            seq=2, positions=[_pos(7001, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 7001, order=700000, comment="copy:m42")],
            acks=[_ack(cmd.id, pos=7001, deal=700001, order=700000, price=1.1001, lots=1.0)]))
        assert p.parse_response(text)[1] == []
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert (mapping["status"], mapping["slave_position_id"], mapping["slave_volume"],
                mapping["fill_price"]) == ("active", 7001, 100, 1.1001)
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)] == [("open", "done")]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id)] == [700001]
        assert repo.mt5_watermark(mt5_id) == (700001, 1_757_203_100_456)

        # The same report again (a re-send): nothing double-counts.
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[_pos(7001, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 7001, order=700000)],
            acks=[_ack(cmd.id, pos=7001, price=1.1001, lots=1.0)]))
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert mapping["slave_volume"] == 100 and len(repo.load_deals(mt5_id)) == 1

    def test_a_rejected_command_fails_the_mapping_and_degrades_the_account(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=1)))[1]
        app.mt5_sync(mt5_id, _sync(seq=2, acks=[_ack(cmd.id, ok=False, retcode=10019,
                                                     msg="No money")]))
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert (mapping["status"], mapping["error"]) == (
            "failed", "terminal rejected open: No money")
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == (
            "degraded", "terminal rejected open: No money")
        # A later successful command clears it, as a successful cTrader send would.
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=43)], org_id=ORG_A)
        (cmd2,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=3)))[1]
        app.mt5_sync(mt5_id, _sync(seq=4, positions=[_pos(7002)],
                                   acks=[_ack(cmd2.id, pos=7002, price=1.1, lots=1.0)]))
        assert _account(repo, mt5_id).status == "ok"

    def test_a_terminal_side_close_reduces_the_mapping_through_its_deal(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=1)))[1]
        app.mt5_sync(mt5_id, _sync(seq=2, positions=[_pos(7001)], deals=[_deal(700001, 7001)],
                                   acks=[_ack(cmd.id, pos=7001, price=1.1, lots=1.0)]))
        # The stop is hit on the terminal: an OUT deal, no ack.
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[],
            deals=[_deal(700002, 7001, entry="OUT", side="SELL", price=1.09, profit=-100.0,
                         time_ms=1_757_203_200_000)],
            balance=9900.0))
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert (mapping["status"], mapping["slave_volume"]) == ("closed", 0)
        closes = [d for d in repo.load_deals(mt5_id) if d["close"]]
        assert len(closes) == 1
        assert closes[0]["close"]["balance"] == 9900.0 and closes[0]["close"]["entry_price"] == 1.1

    def test_an_unknown_account_is_told_to_stop(self, mt5_world):
        _repo, _mt5_id, app = mt5_world
        assert app.mt5_sync(424242, _sync()).startswith("STOP\t")

    def test_status(self, mt5_world):
        repo, mt5_id, app = mt5_world
        assert app.mt5_status(mt5_id) == {"online": False, "last_seen_at": None,
                                          "pending_commands": 0, "hedging": None}
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)
        status = app.mt5_status(mt5_id)
        assert status["online"] is True and status["pending_commands"] == 1
        assert status["hedging"] is True and status["last_seen_at"] is not None

    def test_the_link_write_from_a_sync_is_throttled(self, mt5_world):
        """Four polls a second must not be four UPDATEs a second: balance,
        equity and last_seen_at reach mt5_links at most once per
        MT5_LINK_TOUCH_INTERVAL_S per account (the api throttles its own
        write of those columns the same way)."""
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1, balance=100.0))
        assert repo.load_mt5_link(mt5_id)["balance"] == 100.0
        app.clock.advance(main.MT5_LINK_TOUCH_INTERVAL_S - 0.25)
        app.mt5_sync(mt5_id, _sync(seq=2, balance=200.0))
        assert repo.load_mt5_link(mt5_id)["balance"] == 100.0     # inside the window: not written
        app.clock.advance(0.25)
        app.mt5_sync(mt5_id, _sync(seq=3, balance=300.0))
        link = repo.load_mt5_link(mt5_id)
        assert link["balance"] == 300.0
        assert link["last_seen_at"].timestamp() == app.clock.seconds()


class TestMt5Master:
    @pytest.fixture
    def master_world(self, db_seeded, fernet_key, seed_mt5_account):
        """Org B: an MT5 master and the cTrader slave 200."""
        org_b = seed_org_b(db_seeded, fernet_key, with_master=False)
        master_id = seed_mt5_account(org_b, role="master")
        repo = Repo(db_seeded)
        _seed_symbol_cache(repo, [SLAVE_B1])
        app = main.build_app(repo, TokenStore(db_seeded, fernet_key), make_stub_client_factory(),
                             shards=1, clock=Clock())
        return repo, org_b, master_id, app

    def test_an_mt5_master_gets_an_engine_and_its_deals_fan_out(self, master_world):
        repo, org_b, master_id, app = master_world
        assert app.reconcilers[org_b].master_account_id == master_id
        assert app.state_trackers[org_b] is not None          # quotes ride the slave's connection

        app.mt5_hello(master_id, _hello())
        assert repo.load_symbol_aliases(master_id) == {"EURUSD": "EURUSD.r"}   # from the followers
        assert app.master_symbols_by_org[org_b][EURUSD_R_ID].name == "EURUSD.r"

        app.mt5_sync(master_id, _sync(seq=1))
        app.mt5_sync(master_id, _sync(
            seq=2, positions=[_pos(7001, lots=0.5, sl=1.09)],
            deals=[_deal(700001, 7001, lots=0.5, price=1.1, order=700000)]))
        (mapping,) = repo.mapping_rows(org_id=org_b)
        assert (mapping["master_position_id"], mapping["slave_account_id"],
                mapping["client_order_id"], mapping["symbol"], mapping["status"]) == (
            7001, SLAVE_B1, f"cm7001.{SLAVE_B1}", "EURUSD", "pending")
        # mapping_rows does not select master_fill_price; read it as test_repo.py does.
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (master_fill_price,) = conn.execute(
                "SELECT master_fill_price FROM mappings WHERE client_order_id = %s",
                (mapping["client_order_id"],)).fetchone()
        assert master_fill_price == 1.1

        app.mt5_sync(master_id, _sync(seq=3, positions=[_pos(7001, lots=0.5, sl=1.095)]))
        app.mt5_sync(master_id, _sync(
            seq=4, positions=[],
            deals=[_deal(700002, 7001, entry="OUT", side="SELL", lots=0.5, price=1.105,
                         profit=25.0, time_ms=1_757_203_200_000)]))
        master_events = [e["payload"] for e in _events(repo.dsn)
                         if e["category"] == "master_event"]
        assert master_events == [
            {"source": "mt5", "normalized": "MasterPositionOpened"},
            {"source": "mt5", "normalized": "MasterPositionSLTPAmended"},
            {"source": "mt5", "normalized": "MasterPositionClosed"},
        ]

    def test_get_state_serves_an_mt5_masters_book_from_the_registry(self, master_world):
        repo, org_b, master_id, app = master_world
        app.mt5_hello(master_id, _hello())
        app.mt5_sync(master_id, _sync(
            seq=1, positions=[_pos(7001, lots=0.5, sl=1.09, price=1.102, pnl=3.0)],
            deals=[_deal(700001, 7001, lots=0.5, order=700000)], balance=5000.0, equity=5003.0))
        reconciler = app.reconcilers[org_b]
        reconciler.master_positions, reconciler.master_orders = app.mt5_registry.snapshot(master_id)

        state = app.get_state(org_b)

        (position,) = state["master_positions"]
        assert (position["account_id"], position["symbol"], position["volume"],
                position["volume_lots"], position["pnl_quote"], position["current_price"],
                position["stop_loss"], position["digits"]) == (
            master_id, "EURUSD.r", 50, "0.50", 3.0, 1.102, 1.09, 5)
        assert [c["slave_account_id"] for c in position["copies"]] == [SLAVE_B1]
        assert state["accounts"][master_id]["equity"] == 5003.0


class TestNettingFollower:
    """Every copy on a symbol lives inside the symbol's single net position
    (ticket 9001 here): two master positions, two mappings, one net ticket."""

    def _two_copies(self, repo, mt5_id, app):
        app.mt5_hello(mt5_id, _hello(hedging=False))
        app.mt5_sync(mt5_id, _sync(seq=1))
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=42)], org_id=ORG_A)
        (first,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=2)))[1]
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 9001, order=700000)],
            acks=[_ack(first.id, pos=9001, deal=700001, order=700000, price=1.1, lots=1.0)]))
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=43)], org_id=ORG_A)
        (second,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=4, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)])))[1]
        # The second copy added to the SAME net position: same ticket, 2.00 lots.
        app.mt5_sync(mt5_id, _sync(
            seq=5, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)],
            deals=[_deal(700003, 9001, order=700002, time_ms=1_757_203_100_500)],
            acks=[_ack(second.id, pos=9001, deal=700003, order=700002, price=1.1, lots=1.0)]))
        by_master = self._by_master(repo)
        assert by_master == {42: ("active", 9001, 100), 43: ("active", 9001, 100)}
        return first, second

    @staticmethod
    def _by_master(repo):
        return {m["master_position_id"]: (m["status"], m["slave_position_id"], m["slave_volume"])
                for m in repo.mapping_rows(org_id=ORG_A)}

    def test_a_close_is_an_opposite_open_whose_ack_reduces_exactly_that_mapping(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)

        app.dispatcher.dispatch([ClosePosition(mt5_id, 9001, 100, master_position_id=42)],
                                org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=6, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)])))[1]
        assert cmd.kind == "open" and cmd.client_order_id == f"cm42.{mt5_id}:close"
        assert cmd.payload == {"symbol": "EURUSD.r", "side": "SELL", "lots": 1.0, "sl": 0.0,
                               "tp": 0.0, "comment": "close:m42"}

        # The terminal reduced the net position; its OUT deal rides with the ack.
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)],
            deals=[_deal(700005, 9001, entry="OUT", side="SELL", price=1.101, profit=10.0,
                         time_ms=1_757_203_101_000, order=700004)],
            acks=[_ack(cmd.id, pos=9001, deal=700005, order=700004, price=1.101, lots=1.0)]))

        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("active", 9001, 100)}
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)][-1] == ("open", "done")
        (event,) = _events(repo.dsn, "position_closed")
        assert (event["payload"]["client_order_id"], event["payload"]["closed_volume"]) == (
            f"cm42.{mt5_id}", 100)
        assert [d["deal_id"] for d in repo.load_deals(mt5_id) if d["close"]] == [700005]

    def test_a_close_that_empties_the_net_position_is_acked_with_ticket_0_and_still_closes_its_mapping(
            self, mt5_world):
        """Plan 04's EA acks the net position the terminal shows AFTER the
        fill -- 0 once the last lot is gone (contract §2). The mapping is
        named by the ack's coid, never by that ticket."""
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)

        for seq, master, lots_left, pos_after in ((6, 42, 1.0, 9001), (8, 43, 0.0, 0)):
            app.dispatcher.dispatch([ClosePosition(mt5_id, 9001, 100, master_position_id=master)],
                                    org_id=ORG_A)
            (cmd,) = p.parse_response(app.mt5_sync(mt5_id, _sync(
                seq=seq, positions=[_pos(9001, lots=lots_left + 1.0, sl=1.09, tp=1.12)])))[1]
            assert cmd.client_order_id == f"cm{master}.{mt5_id}:close"
            app.mt5_sync(mt5_id, _sync(
                seq=seq + 1,
                positions=[_pos(9001, lots=lots_left, sl=1.09, tp=1.12)] if lots_left else [],
                deals=[_deal(700000 + seq, 9001, entry="OUT", side="SELL", price=1.101,
                             profit=10.0, time_ms=1_757_203_101_000 + seq, order=700000 + seq - 1)],
                acks=[_ack(cmd.id, pos=pos_after, deal=700000 + seq, order=700000 + seq - 1,
                           price=1.101, lots=1.0)]))

        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("closed", 9001, 0)}
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"])
                for e in _events(repo.dsn, "position_closed")] == [
            (f"cm42.{mt5_id}", 100), (f"cm43.{mt5_id}", 100)]
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)][-2:] == [
            ("open", "done"), ("open", "done")]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id) if d["close"]] == [700006, 700008]

    def test_an_opposite_side_copy_nets_against_the_older_copy_without_closing_it(self, mt5_world):
        """Spec "Opposite positions": the master holds BUY 42 and opens SELL
        45 on the same symbol. On the follower the SELL copy is a plain
        open whose fill REDUCES the net position -- here it empties it, so
        plan 04's EA acks pos 0 and the deal names the position. Both
        masters are open, so both copies stay active with their own
        volumes: the OUT deal is the SELL copy's own fill, not a close of
        the BUY copy."""
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello(hedging=False))
        app.mt5_sync(mt5_id, _sync(seq=1))
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=42)], org_id=ORG_A)
        (first,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=2)))[1]
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 9001, order=700000)],
            acks=[_ack(first.id, pos=9001, deal=700001, order=700000, price=1.1, lots=1.0)]))

        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=45, side=Side.SELL)],
                                org_id=ORG_A)
        (second,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=4, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)])))[1]
        assert (second.kind, second.payload["side"], second.client_order_id) == (
            "open", "SELL", f"cm45.{mt5_id}")
        # The terminal netted the SELL against the BUY: one OUT deal for the
        # whole lot, no position left, an ack whose pos is 0.
        app.mt5_sync(mt5_id, _sync(
            seq=5, positions=[],
            deals=[_deal(700003, 9001, entry="OUT", side="SELL", price=1.1, order=700002,
                         time_ms=1_757_203_100_500)],
            acks=[_ack(second.id, pos=0, deal=700003, order=700002, price=1.1, lots=1.0)]))

        assert self._by_master(repo) == {42: ("active", 9001, 100), 45: ("active", 9001, 100)}
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)] == [
            ("open", "done"), ("open", "done")]
        assert _events(repo.dsn, "position_closed") == []
        assert _events(repo.dsn, "mt5_open_ack_without_position") == []
        (netted,) = _events(repo.dsn, "mt5_netting_opposite_copy")
        assert (netted["payload"]["client_order_id"], netted["payload"]["position"],
                netted["payload"]["entry"], netted["payload"]["volume"],
                netted["payload"]["nets_against"]) == (f"cm45.{mt5_id}", 9001, "OUT", 100, [42])

    def test_a_terminal_side_close_reduces_the_copies_oldest_first(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)

        # The owner trims 1.50 lots by hand: the older copy goes first, then
        # a third of the younger one.
        app.mt5_sync(mt5_id, _sync(
            seq=6, positions=[_pos(9001, lots=0.5, sl=1.09, tp=1.12)],
            deals=[_deal(700005, 9001, entry="OUT", side="SELL", lots=1.5, price=1.09,
                         profit=-150.0, time_ms=1_757_203_101_000)],
            balance=9850.0))
        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("active", 9001, 50)}
        events = _events(repo.dsn, "position_closed")
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"])
                for e in events] == [(f"cm42.{mt5_id}", 100), (f"cm43.{mt5_id}", 50)]

        # Then the net stop takes the rest.
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[],
            deals=[_deal(700007, 9001, entry="OUT", side="SELL", lots=0.5, price=1.09,
                         profit=-50.0, time_ms=1_757_203_102_000)],
            balance=9800.0))
        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("closed", 9001, 0)}
        assert _commands(repo, mt5_id)[-1]["kind"] == "open"      # nothing was queued for the terminal's own closes

    def test_the_shared_protection_override_is_recorded(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)
        (event,) = _events(repo.dsn, "mt5_netting_protection_override")
        assert event["account_id"] == mt5_id
        assert (event["payload"]["position"], event["payload"]["set_by"],
                event["payload"]["overrides"], event["payload"]["stop_loss"],
                event["payload"]["take_profit"]) == (9001, f"cm43.{mt5_id}", [42], 1.09, 1.12)

        # An amend for one copy moves the stop every copy on the symbol shares.
        app.dispatcher.dispatch([AmendPositionSLTP(mt5_id, 9001, 1.095, 1.12)], org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=6, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)])))[1]
        assert cmd.kind == "amend" and cmd.payload == {"position": 9001, "sl": 1.095, "tp": 1.12}
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[_pos(9001, lots=2.0, sl=1.095, tp=1.12)], acks=[_ack(cmd.id, pos=9001)]))
        events = _events(repo.dsn, "mt5_netting_protection_override")
        assert len(events) == 2
        assert (events[1]["payload"]["set_by"], sorted(events[1]["payload"]["overrides"]),
                events[1]["payload"]["stop_loss"]) == (None, [42, 43], 1.095)
        assert _account(repo, mt5_id).status == "ok"

    def test_a_refused_close_leaves_the_mapping_open(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)
        app.dispatcher.dispatch([ClosePosition(mt5_id, 9001, 100, master_position_id=42)],
                                org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=6, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)])))[1]
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)],
            acks=[_ack(cmd.id, ok=False, retcode=10018, msg="Market is closed")]))
        assert self._by_master(repo) == {42: ("active", 9001, 100), 43: ("active", 9001, 100)}
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == (
            "degraded", "terminal rejected close: Market is closed")


class TestNettingMaster:
    """A netting MT5 master: its deals expand into virtual positions the
    cTrader follower copies one by one."""

    def test_add_add_reduce_and_reverse_fan_out_by_virtual_id(self, netting_master_world):
        repo, org_b, master_id, build = netting_master_world
        app = build()
        app.mt5_hello(master_id, _hello(hedging=False))
        app.mt5_sync(master_id, _sync(seq=1))

        # add, add
        app.mt5_sync(master_id, _sync(
            seq=2, positions=[_pos(5, lots=0.5, sl=1.09)],
            deals=[_deal(700001, 5, lots=0.5, price=1.1, order=700000, time_ms=1_000)]))
        app.mt5_sync(master_id, _sync(
            seq=3, positions=[_pos(5, lots=0.8, sl=1.09)],
            deals=[_deal(700003, 5, lots=0.3, price=1.101, order=700002, time_ms=2_000)]))
        by_master = {m["master_position_id"]: m for m in repo.mapping_rows(org_id=org_b)}
        assert set(by_master) == {700001, 700003}
        assert by_master[700001]["client_order_id"] == f"cm700001.{SLAVE_B1}"
        assert [(r["virtual_id"], r["side"], r["volume_left"], r["stop_loss"])
                for r in repo.load_net_ledger(master_id)] == [
            (700001, "BUY", 50, 1.09), (700003, "BUY", 30, 1.09)]
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (master_fill_price,) = conn.execute(
                "SELECT master_fill_price FROM mappings WHERE client_order_id = %s",
                (f"cm700003.{SLAVE_B1}",)).fetchone()
        assert master_fill_price == 1.101

        # partial reduce: 0.6 lots out -> the older virtual position closes,
        # the younger loses 0.1
        app.mt5_sync(master_id, _sync(
            seq=4, positions=[_pos(5, lots=0.2, sl=1.09)],
            deals=[_deal(700005, 5, entry="OUT", side="SELL", lots=0.6, price=1.105, profit=30.0,
                         time_ms=3_000)]))
        assert [(r["virtual_id"], r["volume_left"]) for r in repo.load_net_ledger(master_id)] == [
            (700003, 20)]
        closes = [d for d in repo.load_deals(master_id) if d["close"]]
        assert closes[0]["close"]["entry_price"] == 1.1 and closes[0]["close"]["closed_volume"] == 60

        # the net stop moves: every virtual position is amended
        app.mt5_sync(master_id, _sync(seq=5, positions=[_pos(5, lots=0.2, sl=1.095)]))

        # reversal: SELL 0.5 against 0.2 BUY -> INOUT: close all, open 0.3 SELL
        app.mt5_sync(master_id, _sync(
            seq=6, positions=[_pos(5, side="SELL", lots=0.3)],
            deals=[_deal(700007, 5, entry="INOUT", side="SELL", lots=0.5, price=1.104, profit=6.0,
                         time_ms=4_000)]))
        assert [(r["virtual_id"], r["side"], r["volume_left"])
                for r in repo.load_net_ledger(master_id)] == [(700007, "SELL", 30)]
        assert 700007 in {m["master_position_id"] for m in repo.mapping_rows(org_id=org_b)}
        master_events = [e["payload"]["normalized"] for e in _events(repo.dsn)
                         if e["category"] == "master_event"]
        assert master_events == [
            "MasterPositionOpened", "MasterPositionOpened",
            "MasterPositionClosed", "MasterPositionClosed",
            "MasterPositionSLTPAmended",
            "MasterPositionClosed", "MasterPositionOpened"]
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions WHERE account_id = %s"
                " ORDER BY position_id", (master_id,)).fetchall()
        assert [r for r in rows if r[2] == "open"] == [(700007, 30, "open")]

    def test_get_state_and_a_restart_see_the_virtual_positions(self, netting_master_world):
        repo, org_b, master_id, build = netting_master_world
        app = build()
        app.mt5_hello(master_id, _hello(hedging=False))
        app.mt5_sync(master_id, _sync(
            seq=1, positions=[_pos(5, lots=0.8, price=1.102, pnl=8.0)],
            deals=[_deal(700001, 5, lots=0.5, time_ms=1_000),
                   _deal(700003, 5, lots=0.3, time_ms=2_000)],
            balance=5000.0, equity=5008.0))
        reconciler = app.reconcilers[org_b]
        reconciler.master_positions, reconciler.master_orders = app._mt5_snapshot_provider(master_id)
        assert [p_.position_id for p_ in reconciler.master_positions] == [700001, 700003]

        state = app.get_state(org_b)

        assert [(pos["position_id"], pos["volume"], pos["pnl_quote"], pos["current_price"])
                for pos in state["master_positions"]] == [
            (700001, 50, 5.0, 1.102), (700003, 30, 3.0, 1.102)]
        assert [(pos["position_id"], pos["pnl_quote"])
                for pos in state["accounts"][master_id]["positions"]] == [
            (700001, 5.0), (700003, 3.0)]
        assert state["accounts"][master_id]["equity"] == 5008.0
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions WHERE account_id = %s"
                " ORDER BY position_id", (master_id,)).fetchall()
        assert rows == [(700001, 50, "open"), (700003, 30, "open")]

        # The copier restarts: the ledger comes back from Postgres, the first
        # report diffs nothing, and the next OUT still consumes oldest-first.
        fresh = build()
        fresh.mt5_hello(master_id, _hello(hedging=False))
        fresh.mt5_sync(master_id, _sync(seq=1, positions=[_pos(5, lots=0.8)]))
        fresh.mt5_sync(master_id, _sync(
            seq=2, positions=[_pos(5, lots=0.2)],
            deals=[_deal(700005, 5, entry="OUT", side="SELL", lots=0.6, price=1.105, profit=30.0,
                         time_ms=3_000)]))
        assert [(r["virtual_id"], r["volume_left"]) for r in repo.load_net_ledger(master_id)] == [
            (700003, 20)]
        master_events = [e["payload"]["normalized"] for e in _events(repo.dsn)
                         if e["category"] == "master_event"]
        assert master_events[-2:] == ["MasterPositionClosed", "MasterPositionClosed"]


class TestGetState:
    def test_mt5_accounts_are_served_from_the_registry(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.master_symbols_by_org[ORG_A].update(symbols_by_id(repo.load_symbol_cache(MASTER_A)))
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=ORG_A, symbol="EURUSD")
        repo.activate_position_mapping(mt5_id, f"cm42.{mt5_id}", 7001, 100, fill_price=1.1001)
        app.mt5_sync(mt5_id, _sync(positions=[_pos(7001, sl=1.09, tp=1.12, price=1.102, pnl=2.0)],
                                   balance=9784.04, equity=9786.04))
        reconciler = app.reconcilers[ORG_A]
        reconciler.master_positions = [PositionSnapshot(
            position_id=42, symbol_id=1, side=Side.BUY, volume=10_000_000, price=1.1, label="")]
        reconciler.slave_positions = {mt5_id: app.mt5_registry.snapshot(mt5_id)[0]}

        state = app.get_state(ORG_A)

        block = state["accounts"][mt5_id]
        assert (block["balance"], block["equity"], block["open_pnl"]) == (9784.04, 9786.04, 2.0)
        assert block["positions"][0] == {
            "position_id": 7001, "symbol_id": EURUSD_R_ID, "symbol": "EURUSD.r", "side": "BUY",
            "volume": 100, "entry_price": 1.1, "stop_loss": 1.09, "take_profit": 1.12,
            "pnl_quote": 2.0, "current_price": 1.102}
        (copy,) = state["master_positions"][0]["copies"]
        assert (copy["slave_account_id"], copy["slave_position_id"], copy["volume_lots"],
                copy["fill_price"], copy["stop_loss"], copy["take_profit"]) == (
            mt5_id, 7001, "1.00", 1.1001, 1.09, 1.12)


class TestOffline:
    def test_a_silent_terminal_is_degraded_and_a_report_clears_it(self, mt5_world):
        repo, mt5_id, app = mt5_world
        clock = app.clock
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1))
        clock.advance(10.0)
        app.check_mt5_offline()
        assert _account(repo, mt5_id).status == "ok"
        clock.advance(6.0)
        app.check_mt5_offline()
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == (
            "degraded", "terminal offline since 1970-01-01T00:00:00+00:00")
        assert _events(repo.dsn, "mt5_offline")[0]["payload"]["since"] == "1970-01-01T00:00:00+00:00"
        app.check_mt5_offline()                                   # idempotent: no second event
        assert len(_events(repo.dsn, "mt5_offline")) == 1
        assert app.mt5_status(mt5_id)["online"] is False

        app.mt5_sync(mt5_id, _sync(seq=2))
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == ("ok", None)
        assert _events(repo.dsn, "mt5_online")

    def test_a_paused_account_is_left_alone(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1))
        repo.set_account_status(mt5_id, "paused")
        app.clock.advance(16.0)
        app.check_mt5_offline()
        assert _account(repo, mt5_id).status == "paused"


class TestCtraderLoopsSkipMt5:
    @pytest_twisted.inlineCallbacks
    def test_startup_never_authorizes_or_fetches_symbols_for_an_mt5_account(self, mt5_world):
        repo, mt5_id, app = mt5_world
        yield app.startup()
        for env_clients in app.clients.values():
            for client in env_clients.values():
                assert mt5_id not in client._accounts
        assert _account(repo, mt5_id).status == "ok"
        assert not [e for e in _events(repo.dsn, "symbol_fetch_failed")
                    if e["account_id"] == mt5_id]
        assert app._client_for_account(_account(repo, mt5_id)) is None
        assert app._query_context(mt5_id)[0] is app.mt5_lane

    @pytest_twisted.inlineCallbacks
    def test_reload_keeps_the_mt5_account_out_of_the_auth_loop(self, mt5_world):
        repo, mt5_id, app = mt5_world
        yield app.reload()
        for env_clients in app.clients.values():
            for client in env_clients.values():
                assert mt5_id not in client._accounts
        assert _account(repo, mt5_id).status == "ok"
        assert app.reconcilers[ORG_A].snapshot_provider is app._mt5_snapshot_provider
        assert app.reconcilers[ORG_A].netting_slaves is app._mt5_netting_slaves


class TestOperatorActions:
    @pytest_twisted.inlineCallbacks
    def test_trade_page_actions_branch_to_the_lane(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(
            positions=[_pos(7001)],
            orders=[{"t": 5551, "s": "EURUSD.r", "type": "BUY_LIMIT", "lots": 0.1,
                     "price": 1.09, "sl": 0, "tp": 0, "comment": "manual", "magic": 20260907}]))

        placed = app.place_order({"account_id": mt5_id, "symbol": "XAUUSD.r", "side": "SELL",
                                  "order_type": "MARKET", "volume_lots": 0.2,
                                  "stop_loss": 2450.129, "actor_email": "ada@example.com"})
        assert placed["status"] == "submitted" and placed["volume"] == 20
        closed = yield app.close_position(mt5_id, 7001, 0.4, actor="ada@example.com")
        assert closed["volume"] == 40
        amended = app.amend_position_sltp(mt5_id, 7001, stop_loss="1.08", take_profit=None,
                                          actor="ada@example.com")
        assert amended["stop_loss"] == 1.08
        cancelled = yield app.cancel_order(mt5_id, 5551, actor="ada@example.com")
        assert cancelled["status"] == "submitted"

        rows = _commands(repo, mt5_id)
        assert [(r["kind"], r["payload"]) for r in rows] == [
            ("open", {"symbol": "XAUUSD.r", "side": "SELL", "lots": 0.2, "sl": 2450.13,
                      "tp": 0.0, "comment": "manual"}),
            ("close", {"position": 7001, "lots": 0.4}),
            ("amend", {"position": 7001, "sl": 1.08, "tp": 0.0}),
            ("cancel_pending", {"order": 5551}),
        ]
        actions = [e for e in _events(repo.dsn)
                   if e["payload"].get("action") in ("manual_order", "manual_close",
                                                     "amend_sltp", "manual_cancel")]
        assert len(actions) == 4 and all(e["payload"]["command_id"] for e in actions)

    def test_dry_run_still_blocks_a_manual_open_on_mt5(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        repo.set_org_setting(ORG_A, "dry_run", True)
        with pytest.raises(ValueError, match="dry-run"):
            app.place_order({"account_id": mt5_id, "symbol": "XAUUSD.r", "side": "BUY",
                             "order_type": "MARKET", "volume_lots": 0.2})

    def test_broker_only_queries_say_so_for_mt5(self, mt5_world):
        _repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        failures = []
        app.get_expected_margin(mt5_id, "EURUSD.r", 1.0).addErrback(
            lambda f: failures.append(str(f.value)))
        app.get_trendbars(mt5_id, "EURUSD.r", "M1", 0, 1).addErrback(
            lambda f: failures.append(str(f.value)))
        assert len(failures) == 2 and all("MT5" in message for message in failures)
        assert app.get_quote(mt5_id, "EURUSD.r") == {"symbol": "EURUSD.r", "bid": None, "ask": None}

    def test_history_queries_read_the_lane(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1, deals=[_deal(700001, 7001, time_ms=1000)]))
        results = {}
        app.get_deal_history(mt5_id, 0, 5000).addCallback(lambda r: results.update(deals=r))
        app.get_order_history(mt5_id, 0, 5000).addCallback(lambda r: results.update(orders=r))
        app.get_cash_flow(mt5_id, 0, 5000).addCallback(lambda r: results.update(cash=r))
        app.get_position_deals(mt5_id, 7001, 0, 5000).addCallback(lambda r: results.update(pos=r))
        app.get_account_details(mt5_id).addCallback(lambda r: results.update(details=r))
        assert [d["deal_id"] for d in results["deals"]["deals"]] == [700001]
        assert results["deals"]["deals"][0]["balance_after_estimated"] is True
        assert [o["position_id"] for o in results["orders"]["orders"]] == [7001]
        assert results["cash"] == {"entries": []}
        assert [d["deal_id"] for d in results["pos"]["deals"]] == [700001]
        assert results["details"]["platform"] == "mt5" and results["details"]["mt5"]["login"] == 12345678
        assert app.get_analytics(mt5_id, 4)["truncated"] is False


class TestKillSwitch:
    def test_close_all_on_an_mt5_account_is_verified_from_its_reports(self, mt5_world, monkeypatch):
        monkeypatch.setattr(main, "FLATTEN_SETTLE_S", 0.5)
        repo, mt5_id, app = mt5_world
        clock = app.clock
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1, positions=[_pos(7001), _pos(7002)]))

        results = []
        app.close_all(ORG_A, mt5_id).addCallback(results.append)
        commands = p.parse_response(
            app.mt5_sync(mt5_id, _sync(seq=2, positions=[_pos(7001), _pos(7002)])))[1]
        assert [(c.kind, c.payload["position"]) for c in commands] == [("close", 7001), ("close", 7002)]
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[],
            deals=[_deal(700001, 7001, entry="OUT", side="SELL", time_ms=1),
                   _deal(700002, 7002, entry="OUT", side="SELL", time_ms=2)],
            acks=[_ack(c.id, pos=c.payload["position"], lots=1.0) for c in commands]))
        clock.advance(0.5)

        (result,) = results
        assert result["paused"] is False
        (summary,) = result["accounts"]
        assert (summary["positions_closed"], summary["orders_cancelled"],
                summary["positions_remaining"], summary["error"]) == (2, 0, [], None)
