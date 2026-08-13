from decimal import Decimal

from copier.domain import models as m
from copier.domain.decision import decide

EURUSD = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                      lot_size=10_000_000, min_volume=100_000, step_volume=100_000)


def slave(account_id=101, enabled=True, mult="1.0", symbols=None):
    return m.SlaveConfig(account_id=account_id, enabled=enabled,
                         multiplier=Decimal(mult),
                         symbols={"EURUSD": EURUSD} if symbols is None else symbols)


class MapState:
    def __init__(self, positions=None, orders=None):
        self._p = positions or {}
        self._o = orders or {}
    def position_entries(self, master_position_id):
        return self._p.get(master_position_id, [])
    def order_entries(self, master_order_id):
        return self._o.get(master_order_id, [])


OPEN = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                              volume=10_000_000, lot_size=10_000_000,
                              stop_loss=1.09, take_profit=1.12)


def test_open_fans_out_to_enabled_slaves_with_label():
    out = decide(OPEN, MapState(), [slave(101), slave(102)])
    assert [type(i) for i in out] == [m.OpenMarket, m.OpenMarket]
    assert out[0] == m.OpenMarket(slave_account_id=101, master_position_id=11, symbol_id=1,
                                  side=m.Side.BUY, volume=10_000_000,
                                  stop_loss=1.09, take_profit=1.12, label="copy:m11")


def test_open_applies_multiplier():
    out = decide(OPEN, MapState(), [slave(101, mult="0.5")])
    assert out[0].volume == 5_000_000


def test_open_skips_disabled_slaves():
    assert decide(OPEN, MapState(), [slave(101, enabled=False)]) == []


def test_open_missing_symbol_alerts():
    out = decide(OPEN, MapState(), [slave(101, symbols={})])
    assert isinstance(out[0], m.Alert) and out[0].slave_account_id == 101
    assert "EURUSD" in out[0].message


def test_open_zero_rounded_volume_alerts_never_sends_zero():
    tiny = m.MasterPositionOpened(position_id=12, symbol_name="EURUSD", side=m.Side.SELL,
                                  volume=100_000, lot_size=10_000_000,
                                  stop_loss=None, take_profit=None)
    out = decide(tiny, MapState(), [slave(101, mult="0.5")])
    assert isinstance(out[0], m.Alert)


def test_full_close_closes_entire_mapped_volume():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 10_000_000)]})
    ev = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                closed_volume=10_000_000, remaining_volume=0)
    out = decide(ev, st, [slave(101)])
    assert out == [m.ClosePosition(slave_account_id=101, position_id=555, volume=10_000_000)]


def test_partial_close_closes_same_fraction_of_slave_volume():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 6_000_000)]})
    ev = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                closed_volume=5_000_000, remaining_volume=5_000_000)
    out = decide(ev, st, [slave(101)])
    assert out == [m.ClosePosition(slave_account_id=101, position_id=555, volume=3_000_000)]


def test_close_with_no_mapping_alerts():
    ev = m.MasterPositionClosed(position_id=99, symbol_name="EURUSD",
                                closed_volume=1, remaining_volume=0)
    out = decide(ev, MapState(), [slave(101)])
    assert isinstance(out[0], m.Alert)


def test_close_skips_disabled_slave_entry():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 10_000_000)]})
    ev = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                closed_volume=10_000_000, remaining_volume=0)
    assert decide(ev, st, [slave(101, enabled=False)]) == []


def test_increase_emits_delta_open_for_mapped_position():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 10_000_000)]})
    inc = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                                 volume=2_000_000, lot_size=10_000_000,
                                 stop_loss=None, take_profit=None)
    out = decide(inc, st, [slave(101)])
    assert out == [m.OpenMarket(101, 11, 1, m.Side.BUY, 2_000_000, None, None, "copy:m11")]


def test_sltp_amend_maps_to_each_entry():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 1),
                                  m.PositionMappingEntry(102, 777, 1)]})
    ev = m.MasterPositionSLTPAmended(position_id=11, stop_loss=1.05, take_profit=None)
    out = decide(ev, st, [slave(101), slave(102)])
    assert out == [m.AmendPositionSLTP(101, 555, 1.05, None),
                   m.AmendPositionSLTP(102, 777, 1.05, None)]


def test_sequential_partial_closes_track_slave_volume():
    """Verify that sequential partial closes compute fractions against current slave volume.

    Master position: 10M total, slave: 6M (60% multiplier)
    1. Close 50% of master (5M out of 10M) -> close 50% of slave (3M out of 6M)
    2. Close 50% of remaining master (2.5M out of 5M) -> close 50% of remaining slave (1.5M out of 3M)
    """
    # First close: 50% of master position
    st1 = MapState(positions={11: [m.PositionMappingEntry(101, 555, 6_000_000)]})
    ev1 = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                 closed_volume=5_000_000, remaining_volume=5_000_000)
    out1 = decide(ev1, st1, [slave(101)])
    assert out1 == [m.ClosePosition(slave_account_id=101, position_id=555, volume=3_000_000)]

    # Second close: 50% of remaining master (2.5M out of 5M)
    # The mapping entry should now reflect slave has 3M remaining
    st2 = MapState(positions={11: [m.PositionMappingEntry(101, 555, 3_000_000)]})
    ev2 = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                 closed_volume=2_500_000, remaining_volume=2_500_000)
    out2 = decide(ev2, st2, [slave(101)])
    # Should close 1.5M (50% of remaining 3M)
    assert out2 == [m.ClosePosition(slave_account_id=101, position_id=555, volume=1_500_000)]
