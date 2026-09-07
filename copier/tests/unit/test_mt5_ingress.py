"""master_events_from_report (copier/src/copier/mt5/ingress.py): an MT5
master's deals and book diffs become the MasterEvents decide() consumes."""

import pytest

from copier.domain.models import (
    MasterPendingCancelled, MasterPendingFilled, MasterPendingPlaced, MasterPendingReplaced,
    MasterPositionClosed, MasterPositionOpened, MasterPositionSLTPAmended, PendingType, Side,
    SymbolInfo)
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.ingress import NetLedger, VirtualPosition, master_events_from_report
from copier.testing.mt5_fixtures import deal, order, position, report

XAU = SymbolInfo(symbol_id=7, name="XAUUSD.r", digits=2, lot_size=100, min_volume=1, step_volume=1)
SYMBOLS = {"XAUUSD.r": XAU}
REVERSE = {"XAUUSD.r": "XAUUSD"}          # broker -> canonical


def _prev(positions=(), orders=()):
    return (list(positions), list(orders))


def _pos_snap(ticket, sl=None, tp=None):
    return PositionSnapshot(position_id=ticket, symbol_id=7, side=Side.BUY, volume=100,
                            price=2400.0, label="", stop_loss=sl, take_profit=tp)


def _order_snap(ticket, volume=100, price=2390.0, sl=None, tp=None):
    return OrderSnapshot(order_id=ticket, symbol_id=7, volume=volume, label="", side=Side.BUY,
                         order_type="LIMIT", price=price, stop_loss=sl, take_profit=tp)


class TestDeals:
    def test_in_deal_opens_a_position_in_canonical_terms(self):
        rep = report(
            positions=[position(7001, symbol="XAUUSD.r", volume=100, open_price=2400.5,
                                sl=2390.0, tp=2420.0)],
            deals=[deal(90001, position=7001, order=90000, symbol="XAUUSD.r", deal_type="BUY",
                        entry="IN", volume=100, price=2400.5)])
        assert master_events_from_report(rep, _prev(), REVERSE, SYMBOLS) == [
            MasterPositionOpened(position_id=7001, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100, lot_size=100, stop_loss=2390.0, take_profit=2420.0,
                                 entry_price=2400.5)]

    def test_an_unaliased_symbol_keeps_its_broker_name_and_default_lot_size(self):
        rep = report(deals=[deal(1, position=5, symbol="GBPJPY.r", deal_type="SELL", entry="IN",
                                 volume=30, price=190.1)])
        (event,) = master_events_from_report(rep, _prev(), REVERSE, SYMBOLS)
        assert (event.symbol_name, event.side, event.lot_size, event.stop_loss) == (
            "GBPJPY.r", Side.SELL, 100, None)

    def test_full_close(self):
        rep = report(positions=[], deals=[deal(2, position=7001, symbol="XAUUSD.r",
                                               deal_type="SELL", entry="OUT", volume=100,
                                               price=2410.0, profit=100.0)])
        assert master_events_from_report(rep, _prev([_pos_snap(7001)]), REVERSE, SYMBOLS) == [
            MasterPositionClosed(position_id=7001, symbol_name="XAUUSD", closed_volume=100,
                                 remaining_volume=0)]

    def test_partial_close_reports_what_is_still_open(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r", volume=60)],
                     deals=[deal(2, position=7001, symbol="XAUUSD.r", deal_type="SELL",
                                 entry="OUT", volume=40, price=2410.0)])
        (event,) = master_events_from_report(rep, _prev([_pos_snap(7001)]), REVERSE, SYMBOLS)
        assert (event.closed_volume, event.remaining_volume) == (40, 60)

    @pytest.mark.parametrize("entry", ["OUT_BY", "INOUT"])
    def test_other_closing_entries_close_too(self, entry):
        rep = report(deals=[deal(2, position=7001, symbol="XAUUSD.r", deal_type="SELL",
                                 entry=entry, volume=100)])
        (event,) = master_events_from_report(rep, _prev(), REVERSE, SYMBOLS)
        assert isinstance(event, MasterPositionClosed) and event.remaining_volume == 0

    def test_balance_operations_are_not_trades(self):
        rep = report(deals=[deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0)])
        assert master_events_from_report(rep, _prev(), REVERSE, SYMBOLS) == []

    def test_deals_are_handled_in_time_order(self):
        rep = report(deals=[
            deal(2, position=7001, symbol="XAUUSD.r", deal_type="SELL", entry="OUT", volume=100,
                 time_ms=2000),
            deal(1, position=7001, symbol="XAUUSD.r", deal_type="BUY", entry="IN", volume=100,
                 time_ms=1000)])
        kinds = [type(e).__name__ for e in master_events_from_report(rep, _prev(), REVERSE, SYMBOLS)]
        assert kinds == ["MasterPositionOpened", "MasterPositionClosed"]


class TestProtection:
    def test_a_changed_stop_or_target_is_an_amend(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r", sl=2395.0, tp=2420.0)])
        assert master_events_from_report(
            rep, _prev([_pos_snap(7001, sl=2390.0, tp=2420.0)]), REVERSE, SYMBOLS) == [
            MasterPositionSLTPAmended(position_id=7001, stop_loss=2395.0, take_profit=2420.0)]

    def test_clearing_protection_is_an_amend_with_nones(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r")])
        (event,) = master_events_from_report(
            rep, _prev([_pos_snap(7001, sl=2390.0)]), REVERSE, SYMBOLS)
        assert (event.stop_loss, event.take_profit) == (None, None)

    def test_unchanged_protection_is_silent(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r", sl=2390.0)])
        assert master_events_from_report(
            rep, _prev([_pos_snap(7001, sl=2390.0)]), REVERSE, SYMBOLS) == []


class TestPendingOrders:
    def test_a_new_order_is_placed(self):
        rep = report(orders=[order(5551, symbol="XAUUSD.r", order_type="SELL_STOP", volume=50,
                                   price=2380.0, sl=2390.0)])
        assert master_events_from_report(rep, _prev(), REVERSE, SYMBOLS) == [
            MasterPendingPlaced(order_id=5551, symbol_name="XAUUSD", side=Side.SELL,
                                order_type=PendingType.STOP, volume=50, lot_size=100,
                                price=2380.0, stop_loss=2390.0, take_profit=None,
                                expiry_ts_ms=None)]

    def test_a_changed_order_is_replaced(self):
        rep = report(orders=[order(5551, symbol="XAUUSD.r", order_type="BUY_LIMIT", volume=100,
                                   price=2385.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551, price=2390.0)]), REVERSE, SYMBOLS) == [
            MasterPendingReplaced(order_id=5551, symbol_name="XAUUSD", lot_size=100,
                                  order_type=PendingType.LIMIT, volume=100, price=2385.0,
                                  stop_loss=None, take_profit=None)]

    def test_an_unchanged_order_is_silent(self):
        rep = report(orders=[order(5551, symbol="XAUUSD.r", order_type="BUY_LIMIT", volume=100,
                                   price=2390.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS) == []

    def test_a_vanished_order_without_a_deal_is_cancelled(self):
        assert master_events_from_report(
            report(), _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS) == [
            MasterPendingCancelled(order_id=5551)]

    def test_a_vanished_order_with_its_deal_is_filled_not_opened(self):
        rep = report(positions=[position(5551, symbol="XAUUSD.r")],
                     deals=[deal(90001, position=5551, order=5551, symbol="XAUUSD.r",
                                 deal_type="BUY", entry="IN", volume=100, price=2390.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS) == [
            MasterPendingFilled(order_id=5551, position_id=5551)]


class TestFirstReport:
    def test_without_a_previous_snapshot_only_deals_speak(self):
        """After a copier restart every pending order and every stop would
        otherwise look new -- and be copied a second time."""
        rep = report(positions=[position(7001, symbol="XAUUSD.r", sl=2390.0)],
                     orders=[order(5551, symbol="XAUUSD.r")],
                     deals=[deal(1, position=7002, symbol="XAUUSD.r", deal_type="BUY",
                                 entry="IN", volume=10)])
        events = master_events_from_report(rep, None, REVERSE, SYMBOLS)
        assert [type(e).__name__ for e in events] == ["MasterPositionOpened"]


def _vp(virtual_id, volume, side="BUY", symbol="XAUUSD.r", sl=None, tp=None, opened_at_ms=None):
    return VirtualPosition(virtual_id=virtual_id, symbol=symbol, side=side, volume_open=volume,
                           volume_left=volume, stop_loss=sl, take_profit=tp,
                           opened_at_ms=opened_at_ms if opened_at_ms is not None else virtual_id * 1000)


def _xau(ticket, entry, volume, side="BUY", price=2400.0, time_ms=None):
    return deal(ticket, position=5, symbol="XAUUSD.r", deal_type=side, entry=entry, volume=volume,
                price=price, time_ms=time_ms if time_ms is not None else ticket * 1000)


class TestNetLedger:
    """A netting master's single net position per symbol, expanded into the
    virtual positions its followers copy."""

    def test_add_add_reduce_consumes_the_oldest_first_and_partially(self):
        ledger = NetLedger([])
        first = ledger.apply_deal(_xau(1, "IN", 50), "XAUUSD", stop_loss=2390.0, take_profit=None)
        second = ledger.apply_deal(_xau(2, "IN", 30, price=2401.0), "XAUUSD")
        assert first == [MasterPositionOpened(
            position_id=1, symbol_name="XAUUSD", side=Side.BUY, volume=50, lot_size=100,
            stop_loss=2390.0, take_profit=None, entry_price=2400.0)]
        assert (second[0].position_id, second[0].volume, second[0].entry_price) == (2, 30, 2401.0)

        reduced = ledger.apply_deal(_xau(3, "OUT", 60, side="SELL"), "XAUUSD")

        assert reduced == [
            MasterPositionClosed(position_id=1, symbol_name="XAUUSD", closed_volume=50,
                                 remaining_volume=0),
            MasterPositionClosed(position_id=2, symbol_name="XAUUSD", closed_volume=10,
                                 remaining_volume=20)]
        (left,) = ledger.positions()
        assert (left.virtual_id, left.volume_open, left.volume_left) == (2, 30, 20)
        upserts, deleted = ledger.dirty_rows()
        assert [(v.virtual_id, v.volume_left) for v in upserts] == [(2, 20)] and deleted == [1]
        assert ledger.dirty_rows() == ([], [])

    def test_full_close_empties_the_symbol(self):
        ledger = NetLedger([_vp(1, 50), _vp(2, 30)])
        events = ledger.apply_deal(_xau(3, "OUT", 80, side="SELL"), "XAUUSD")
        assert [(e.position_id, e.closed_volume, e.remaining_volume) for e in events] == [
            (1, 50, 0), (2, 30, 0)]
        assert ledger.positions() == []
        assert ledger.dirty_rows() == ([], [1, 2])

    def test_a_close_beyond_what_the_ledger_holds_closes_what_it_has(self):
        ledger = NetLedger([_vp(1, 50)])
        events = ledger.apply_deal(_xau(3, "OUT", 80, side="SELL"), "XAUUSD")
        assert [(e.position_id, e.closed_volume) for e in events] == [(1, 50)]
        assert ledger.positions() == []

    def test_reversal_closes_everything_and_opens_the_remainder_the_other_way(self):
        ledger = NetLedger([_vp(1, 50)])
        events = ledger.apply_deal(_xau(9, "INOUT", 80, side="SELL", price=2410.0), "XAUUSD",
                                   stop_loss=2420.0)
        assert events == [
            MasterPositionClosed(position_id=1, symbol_name="XAUUSD", closed_volume=50,
                                 remaining_volume=0),
            MasterPositionOpened(position_id=9, symbol_name="XAUUSD", side=Side.SELL, volume=30,
                                 lot_size=100, stop_loss=2420.0, take_profit=None,
                                 entry_price=2410.0)]
        (left,) = ledger.positions()
        assert (left.virtual_id, left.side, left.volume_left, left.opened_at_ms) == (9, "SELL", 30, 9000)
        upserts, deleted = ledger.dirty_rows()
        assert [v.virtual_id for v in upserts] == [9] and deleted == [1]

    def test_other_symbols_are_untouched(self):
        ledger = NetLedger([_vp(1, 50), _vp(2, 100, symbol="EURUSD.r")])
        ledger.apply_deal(_xau(3, "OUT", 50, side="SELL"), "XAUUSD")
        assert [v.virtual_id for v in ledger.positions()] == [2]

    def test_a_protection_change_fans_out_to_every_virtual_position_of_the_symbol(self):
        ledger = NetLedger([_vp(1, 50, sl=2390.0), _vp(2, 30, sl=2390.0), _vp(3, 30, symbol="EURUSD.r")])
        events = ledger.apply_protection("XAUUSD.r", 2395.0, 2420.0)
        assert events == [
            MasterPositionSLTPAmended(position_id=1, stop_loss=2395.0, take_profit=2420.0),
            MasterPositionSLTPAmended(position_id=2, stop_loss=2395.0, take_profit=2420.0)]
        assert ledger.apply_protection("XAUUSD.r", 2395.0, 2420.0) == []      # nothing changed
        upserts, _deleted = ledger.dirty_rows()
        assert sorted((v.virtual_id, v.stop_loss) for v in upserts) == [(1, 2395.0), (2, 2395.0)]

    def test_balance_operations_and_zero_volume_deals_are_ignored(self):
        ledger = NetLedger([])
        assert ledger.apply_deal(deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0), "") == []
        assert ledger.apply_deal(_xau(4, "IN", 0), "XAUUSD") == []
        assert ledger.positions() == [] and ledger.dirty_rows() == ([], [])

    def test_restart_rebuilds_the_ledger_from_its_rows(self):
        before = NetLedger([])
        before.apply_deal(_xau(1, "IN", 50), "XAUUSD")
        before.apply_deal(_xau(2, "IN", 30), "XAUUSD")
        before.apply_deal(_xau(3, "OUT", 20, side="SELL"), "XAUUSD")
        upserts, deleted = before.dirty_rows()
        rows = [v.row() for v in upserts]                       # what repo.upsert_net_ledger stored
        assert deleted == [] and [(r["virtual_id"], r["volume_left"]) for r in rows] == [(1, 30), (2, 30)]
        assert set(rows[0]) == {"virtual_id", "symbol", "side", "volume_open", "volume_left",
                                "stop_loss", "take_profit", "opened_at_ms"}

        after = NetLedger(rows)                                 # the copier restarted

        assert [(v.virtual_id, v.volume_left) for v in after.positions()] == [(1, 30), (2, 30)]
        events = after.apply_deal(_xau(4, "OUT", 40, side="SELL"), "XAUUSD")
        assert [(e.position_id, e.closed_volume, e.remaining_volume) for e in events] == [
            (1, 30, 0), (2, 10, 20)]


class TestNettingMaster:
    """master_events_from_report with a ledger: deals expand through it and
    the net position's protection fans out to every virtual position."""

    def test_deals_go_through_the_ledger_with_the_net_positions_levels(self):
        ledger = NetLedger([])
        rep = report(positions=[position(5, symbol="XAUUSD.r", volume=80, sl=2390.0)],
                     deals=[_xau(1, "IN", 50), _xau(2, "IN", 30, price=2401.0)])
        events = master_events_from_report(rep, _prev(), REVERSE, SYMBOLS, ledger=ledger)
        assert [(type(e).__name__, e.position_id, e.stop_loss) for e in events] == [
            ("MasterPositionOpened", 1, 2390.0), ("MasterPositionOpened", 2, 2390.0)]
        assert [v.virtual_id for v in ledger.positions()] == [1, 2]

    def test_a_net_stop_change_amends_every_virtual_position(self):
        ledger = NetLedger([_vp(1, 50, sl=2390.0), _vp(2, 30, sl=2390.0)])
        rep = report(positions=[position(5, symbol="XAUUSD.r", volume=80, sl=2395.0)])
        prev = _prev([PositionSnapshot(position_id=5, symbol_id=7, side=Side.BUY, volume=80,
                                       price=2400.0, label="", stop_loss=2390.0)])
        assert master_events_from_report(rep, prev, REVERSE, SYMBOLS, ledger=ledger) == [
            MasterPositionSLTPAmended(position_id=1, stop_loss=2395.0, take_profit=None),
            MasterPositionSLTPAmended(position_id=2, stop_loss=2395.0, take_profit=None)]

    def test_deals_are_absorbed_before_the_protection_diff(self):
        """A reversal in the same report as a stop change: the old virtual
        positions are closed first, so the amend reaches only the new one."""
        ledger = NetLedger([_vp(1, 50, sl=2390.0)])
        rep = report(positions=[position(5, symbol="XAUUSD.r", side="SELL", volume=30, sl=2420.0)],
                     deals=[_xau(9, "INOUT", 80, side="SELL", price=2410.0)])
        prev = _prev([PositionSnapshot(position_id=5, symbol_id=7, side=Side.BUY, volume=50,
                                       price=2400.0, label="", stop_loss=2390.0)])
        events = master_events_from_report(rep, prev, REVERSE, SYMBOLS, ledger=ledger)
        assert [type(e).__name__ for e in events] == ["MasterPositionClosed", "MasterPositionOpened"]
        assert events[1].stop_loss == 2420.0                     # carried by the open, not re-amended

    def test_a_pending_fill_links_to_the_virtual_id(self):
        ledger = NetLedger([])
        rep = report(positions=[position(5, symbol="XAUUSD.r")],
                     deals=[deal(90001, position=5, order=5551, symbol="XAUUSD.r", deal_type="BUY",
                                 entry="IN", volume=100, price=2390.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS, ledger=ledger) == [
            MasterPendingFilled(order_id=5551, position_id=90001)]
        assert [v.virtual_id for v in ledger.positions()] == [90001]

    def test_the_first_report_after_a_restart_still_diffs_nothing(self):
        ledger = NetLedger([_vp(1, 50, sl=2390.0)])
        rep = report(positions=[position(5, symbol="XAUUSD.r", sl=2395.0)],
                     orders=[order(5551, symbol="XAUUSD.r")])
        assert master_events_from_report(rep, None, REVERSE, SYMBOLS, ledger=ledger) == []
        assert ledger.positions()[0].stop_loss == 2390.0
