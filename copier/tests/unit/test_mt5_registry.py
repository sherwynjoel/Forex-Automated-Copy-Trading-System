"""MT5Registry (copier/src/copier/mt5/registry.py): the in-memory book of
every MT5 terminal, fed by its reports and read by reconcile, get_state,
flatten and the queries."""

import psycopg
import pytest

from copier.db.repo import Repo
from copier.domain.models import Side
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.protocol import HelloReport, HelloSymbol
from copier.mt5.registry import OFFLINE_AFTER_S, MT5Registry
from copier.testing.mt5_fixtures import order, position, report

EURUSD = HelloSymbol("EURUSD.r", 5, 100000.0, 0.01, 0.01, 100.0, 4)
XAUUSD = HelloSymbol("XAUUSD.r", 2, 100.0, 0.01, 0.01, 50.0, 4)


def _hello(symbols, hedging=True, chunk=1, chunks=1):
    return HelloReport(
        ea_version="1.0.0", ea_build=4400, login=12345678, broker="B", server="S",
        currency="USD", hedging=hedging, trade_mode="demo", leverage=100,
        symbols=list(symbols), chunk=chunk, chunks=chunks)


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    mt5_id = seed_mt5_account(org_id)
    repo = Repo(db)
    return repo, org_id, mt5_id, MT5Registry(repo)


class TestHello:
    def test_stores_symbols_and_persists_the_cache(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD, XAUUSD]), now=100.0)
        info = registry.symbol_by_name(mt5_id, "XAUUSD.r")
        assert (info.name, info.digits, info.lot_size, info.min_volume, info.step_volume) == (
            "XAUUSD.r", 2, 100, 1, 1)
        assert registry.symbol_by_name(mt5_id, "GBPJPY.r") is None
        cached = repo.load_symbol_cache(mt5_id)
        assert set(cached) == {"EURUSD.r", "XAUUSD.r"} and cached["XAUUSD.r"] == info
        assert registry.hedging(mt5_id) is True and registry.last_seen(mt5_id) == 100.0
        assert registry.margin_mode(mt5_id) == "hedging"

    def test_chunks_accumulate_and_persist_on_the_last_one(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD], chunk=1, chunks=2), now=1.0)
        assert repo.load_symbol_cache(mt5_id) == {}
        registry.update_from_hello(mt5_id, org_id, _hello([XAUUSD], chunk=2, chunks=2), now=2.0)
        assert set(repo.load_symbol_cache(mt5_id)) == {"EURUSD.r", "XAUUSD.r"}
        ids = {registry.symbol_by_name(mt5_id, n).symbol_id for n in ("EURUSD.r", "XAUUSD.r")}
        assert len(ids) == 2

    def test_a_new_first_chunk_replaces_the_old_list(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD, XAUUSD]), now=1.0)
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=2.0)
        assert set(repo.load_symbol_cache(mt5_id)) == {"EURUSD.r"}


class TestRestart:
    def test_symbols_and_hedging_come_back_from_postgres(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD], hedging=False), now=1.0)
        repo.upsert_mt5_link_hello(
            mt5_id, login=1, broker="B", server="S", currency="USD", hedging=False,
            trade_mode="demo", leverage=100, ea_version="1.0.0", ea_build=4400)
        fresh = MT5Registry(repo)   # the copier restarted: memory is gone
        assert fresh.symbol_by_name(mt5_id, "EURUSD.r").lot_size == 100
        assert fresh.hedging(mt5_id) is False and fresh.margin_mode(mt5_id) == "netting"
        assert fresh.report(mt5_id) is None and fresh.snapshot(mt5_id) is None
        assert fresh.account_block(mt5_id) is None and fresh.position(mt5_id, 1) is None

    def test_unknown_account_answers_nothing(self, world):
        _repo, _org_id, _mt5_id, registry = world
        assert registry.hedging(424242) is None and registry.last_seen(424242) is None
        assert registry.margin_mode(424242) is None
        assert registry.is_online(424242, now=5.0) is False


class TestSync:
    def test_persists_positions_and_closes_vanished_ones(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        registry.update_from_sync(mt5_id, org_id, report([
            position(7001, volume=100, comment="copy:m42"), position(7002, volume=50)]), now=2.0)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, symbol, side, volume, label, status, org_id FROM positions"
                " WHERE account_id = %s ORDER BY position_id", (mt5_id,)).fetchall()
        assert rows == [(7001, "EURUSD.r", "BUY", 100, "copy:m42", "open", org_id),
                        (7002, "EURUSD.r", "BUY", 50, None, "open", org_id)]

        registry.update_from_sync(mt5_id, org_id, report(
            [position(7001, volume=60, comment="copy:m42")], seq=2), now=3.0)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions"
                " WHERE account_id = %s ORDER BY position_id", (mt5_id,)).fetchall()
        assert rows == [(7001, 60, "open"), (7002, 50, "closed")]
        assert registry.report(mt5_id).seq == 2
        assert registry.position(mt5_id, 7001).volume == 60
        assert registry.position(mt5_id, 7002) is None

    def test_an_unchanged_book_is_not_rewritten(self, world, monkeypatch):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        calls = []
        original = repo.upsert_positions

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(repo, "upsert_positions", counting)
        registry.update_from_sync(mt5_id, org_id, report([position(7001)]), now=2.0)
        # A moving P&L is not a changed book.
        registry.update_from_sync(mt5_id, org_id, report([position(7001, pnl=2.5)], seq=2), now=2.25)
        assert len(calls) == 1
        registry.update_from_sync(mt5_id, org_id, report([position(7001, sl=1.05)], seq=3), now=2.5)
        assert len(calls) == 2


class TestSnapshot:
    def test_labels_come_from_mappings_else_the_comment(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        repo.activate_position_mapping(mt5_id, f"cm42.{mt5_id}", 7001, 100)
        repo.create_order_mapping(9, mt5_id, f"co9.{mt5_id}", org_id=org_id)
        repo.activate_order_mapping(mt5_id, f"co9.{mt5_id}", 5551)
        registry.update_from_sync(mt5_id, org_id, report(
            positions=[position(7001, comment="wiped by broker", sl=1.05, tp=1.15),
                       position(7002, comment="manual")],
            orders=[order(5551, order_type="SELL_STOP", price=1.08, sl=1.09),
                    order(5552, comment="mine")]), now=2.0)

        positions, orders = registry.snapshot(mt5_id)

        eurusd_id = registry.symbol_by_name(mt5_id, "EURUSD.r").symbol_id
        assert positions == [
            PositionSnapshot(position_id=7001, symbol_id=eurusd_id, side=Side.BUY, volume=100,
                             price=1.1, label="copy:m42", stop_loss=1.05, take_profit=1.15),
            PositionSnapshot(position_id=7002, symbol_id=eurusd_id, side=Side.BUY, volume=100,
                             price=1.1, label="manual"),
        ]
        assert orders == [
            OrderSnapshot(order_id=5551, symbol_id=eurusd_id, volume=100, label="copy:o9",
                          side=Side.SELL, order_type="STOP", price=1.08, stop_loss=1.09,
                          take_profit=None),
            OrderSnapshot(order_id=5552, symbol_id=eurusd_id, volume=100, label="mine",
                          side=Side.BUY, order_type="LIMIT", price=1.09),
        ]
        assert registry.snapshot(mt5_id, with_labels=False)[0][0].label == "wiped by broker"

    def test_a_filled_pending_copy_keeps_its_order_label_on_the_position(self, world):
        """compute_drift's unfilled-order check looks for copy:o<order> on
        the slave position a linked pending copy became."""
        repo, org_id, mt5_id, registry = world
        repo.create_order_mapping(9, mt5_id, f"co9.{mt5_id}", org_id=org_id)
        repo.activate_order_mapping(mt5_id, f"co9.{mt5_id}", 5551)
        repo.link_pending_fill(9, mt5_id, 77)
        repo.activate_pending_fill(mt5_id, 5551, 7003, 100)
        registry.update_from_sync(mt5_id, org_id, report([position(7003)]), now=2.0)
        (positions, _orders) = registry.snapshot(mt5_id)
        assert positions[0].label == "copy:o9"

    def test_an_unknown_symbol_gets_id_zero(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_sync(mt5_id, org_id, report([position(7001, symbol="GBPJPY.r")]), now=2.0)
        (positions, _orders) = registry.snapshot(mt5_id)
        assert positions[0].symbol_id == 0


class TestAccountBlock:
    def test_get_state_shape(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        registry.update_from_sync(mt5_id, org_id, report(
            [position(7001, sl=1.05, tp=1.15, price=1.102, pnl=2.0),
             position(7002, side="SELL", pnl=-0.5, price=1.099)],
            balance=9784.04, equity=9785.54), now=2.0)
        eurusd_id = registry.symbol_by_name(mt5_id, "EURUSD.r").symbol_id
        assert registry.account_block(mt5_id) == {
            "balance": 9784.04, "equity": 9785.54, "open_pnl": 1.5,
            "positions": [
                {"position_id": 7001, "symbol_id": eurusd_id, "symbol": "EURUSD.r", "side": "BUY",
                 "volume": 100, "entry_price": 1.1, "stop_loss": 1.05, "take_profit": 1.15,
                 "pnl_quote": 2.0, "current_price": 1.102},
                {"position_id": 7002, "symbol_id": eurusd_id, "symbol": "EURUSD.r", "side": "SELL",
                 "volume": 100, "entry_price": 1.1, "stop_loss": None, "take_profit": None,
                 "pnl_quote": -0.5, "current_price": 1.099},
            ],
        }


class TestOnline:
    def test_online_within_the_window_offline_after(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_sync(mt5_id, org_id, report(), now=100.0)
        assert registry.last_seen(mt5_id) == 100.0
        assert registry.is_online(mt5_id, now=100.0 + OFFLINE_AFTER_S - 0.01) is True
        assert registry.is_online(mt5_id, now=100.0 + OFFLINE_AFTER_S) is False
        registry.update_from_sync(mt5_id, org_id, report(seq=2), now=200.0)
        assert registry.is_online(mt5_id, now=201.0) is True
