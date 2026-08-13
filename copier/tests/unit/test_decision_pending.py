from decimal import Decimal

from copier.domain import models as m
from copier.domain.decision import decide

EURUSD = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                      lot_size=10_000_000, min_volume=100_000, step_volume=100_000)


def slave(account_id=101, enabled=True, mult="1.0"):
    return m.SlaveConfig(account_id=account_id, enabled=enabled,
                         multiplier=Decimal(mult), symbols={"EURUSD": EURUSD})


class MapState:
    def __init__(self, orders=None):
        self._o = orders or {}
    def position_entries(self, master_position_id):
        return []
    def order_entries(self, master_order_id):
        return self._o.get(master_order_id, [])


PLACED = m.MasterPendingPlaced(order_id=42, symbol_name="EURUSD", side=m.Side.SELL,
                               order_type=m.PendingType.LIMIT, volume=1_000_000,
                               lot_size=10_000_000, price=1.1150,
                               stop_loss=1.1200, take_profit=1.1000, expiry_ts_ms=None)


def test_pending_place_mirrors_type_price_sltp_with_label():
    out = decide(PLACED, MapState(), [slave(101)])
    assert out == [m.PlacePending(slave_account_id=101, master_order_id=42, symbol_id=1,
                                  side=m.Side.SELL, order_type=m.PendingType.LIMIT,
                                  volume=1_000_000, price=1.1150, stop_loss=1.1200,
                                  take_profit=1.1000, expiry_ts_ms=None, label="copy:o42")]


def test_pending_place_applies_multiplier():
    out = decide(PLACED, MapState(), [slave(101, mult="2.0")])
    assert out[0].volume == 2_000_000


def test_pending_replace_amends_mapped_order_with_remirrored_volume():
    st = MapState(orders={42: [m.OrderMappingEntry(101, 900)]})
    ev = m.MasterPendingReplaced(order_id=42, symbol_name="EURUSD", lot_size=10_000_000,
                                 order_type=m.PendingType.LIMIT, volume=3_000_000,
                                 price=1.1100, stop_loss=None, take_profit=None)
    out = decide(ev, st, [slave(101)])
    assert out == [m.AmendPending(slave_account_id=101, order_id=900,
                                  order_type=m.PendingType.LIMIT, volume=3_000_000,
                                  price=1.1100, stop_loss=None, take_profit=None)]


def test_pending_replace_without_mapping_alerts():
    ev = m.MasterPendingReplaced(order_id=42, symbol_name="EURUSD", lot_size=10_000_000,
                                 order_type=m.PendingType.STOP, volume=1, price=1.0,
                                 stop_loss=None, take_profit=None)
    out = decide(ev, MapState(), [slave(101)])
    assert isinstance(out[0], m.Alert)


def test_pending_cancel_cancels_each_mapped_order():
    st = MapState(orders={42: [m.OrderMappingEntry(101, 900), m.OrderMappingEntry(102, 901)]})
    out = decide(m.MasterPendingCancelled(order_id=42), st, [slave(101), slave(102)])
    assert out == [m.CancelPending(101, 900), m.CancelPending(102, 901)]


def test_pending_fill_links_and_never_opens():
    st = MapState(orders={42: [m.OrderMappingEntry(101, 900)]})
    out = decide(m.MasterPendingFilled(order_id=42, position_id=77), st,
                 [slave(101), slave(102)])
    assert m.LinkPendingFill(101, 42, 77) in out
    assert not any(isinstance(i, m.OpenMarket) for i in out)
    # slave 102 has no mapped order -> alert (spec: slave order not yet placed/filled -> alert)
    assert any(isinstance(i, m.Alert) and i.slave_account_id == 102 for i in out)


def test_master_rejection_is_a_noop():
    assert decide(m.MasterRejected(reason="NOT_ENOUGH_MONEY"), MapState(), [slave(101)]) == []
