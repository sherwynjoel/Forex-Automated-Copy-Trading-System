from decimal import Decimal

from copier.domain import models as m


def test_master_events_are_frozen_and_hashable():
    e = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                               volume=10_000_000, lot_size=10_000_000,
                               stop_loss=1.09, take_profit=None)
    assert hash(e)  # frozen dataclass


def test_slave_config_holds_symbol_map():
    sym = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                       lot_size=10_000_000, min_volume=100_000, step_volume=100_000)
    cfg = m.SlaveConfig(account_id=101, enabled=True,
                        multiplier=Decimal("1.0"), symbols={"EURUSD": sym})
    assert cfg.symbols["EURUSD"].lot_size == 10_000_000


def test_intent_union_members_exist():
    for name in ["OpenMarket", "ClosePosition", "AmendPositionSLTP", "PlacePending",
                 "AmendPending", "CancelPending", "LinkPendingFill", "Alert"]:
        assert hasattr(m, name)


def test_mapping_state_is_a_protocol():
    class Fake:
        def position_entries(self, master_position_id):
            return [m.PositionMappingEntry(101, 555, 10_000_000)]
        def order_entries(self, master_order_id):
            return []
    state: m.MappingState = Fake()
    assert state.position_entries(11)[0].slave_position_id == 555
