from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from copier.domain import models as m


def test_master_events_are_frozen_and_hashable():
    e = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                               volume=10_000_000, lot_size=10_000_000,
                               stop_loss=1.09, take_profit=None)
    assert hash(e)  # frozen dataclass
    # Verify immutability: attempting to mutate raises FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        e.position_id = 999


def test_slave_config_holds_symbol_map():
    sym = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                       lot_size=10_000_000, min_volume=100_000, step_volume=100_000)
    cfg = m.SlaveConfig(account_id=101, enabled=True,
                        multiplier=Decimal("1.0"), symbols={"EURUSD": sym})
    assert cfg.symbols["EURUSD"].lot_size == 10_000_000


def test_intent_union_members_exist():
    # Check that all intent class names are exported
    for name in ["OpenMarket", "ClosePosition", "AmendPositionSLTP", "PlacePending",
                 "AmendPending", "CancelPending", "LinkPendingFill", "Alert"]:
        assert hasattr(m, name)

    # Verify instances of each SlaveIntent member are actually union members
    open_market = m.OpenMarket(slave_account_id=1, master_position_id=1, symbol_id=1,
                                side=m.Side.BUY, volume=100, stop_loss=None,
                                take_profit=None, label="test")
    assert isinstance(open_market, m.SlaveIntent.__args__)  # type: ignore

    close_pos = m.ClosePosition(slave_account_id=1, position_id=1, volume=100)
    assert isinstance(close_pos, m.SlaveIntent.__args__)  # type: ignore

    alert = m.Alert(slave_account_id=1, message="test")
    assert isinstance(alert, m.SlaveIntent.__args__)  # type: ignore

    # Verify instances of each MasterEvent member are actually union members
    master_opened = m.MasterPositionOpened(position_id=1, symbol_name="EURUSD",
                                            side=m.Side.BUY, volume=100,
                                            lot_size=100, stop_loss=None,
                                            take_profit=None)
    assert isinstance(master_opened, m.MasterEvent.__args__)  # type: ignore

    master_closed = m.MasterPositionClosed(position_id=1, symbol_name="EURUSD",
                                           closed_volume=100, remaining_volume=0)
    assert isinstance(master_closed, m.MasterEvent.__args__)  # type: ignore

    master_rejected = m.MasterRejected(reason="test")
    assert isinstance(master_rejected, m.MasterEvent.__args__)  # type: ignore


def test_mapping_state_is_a_protocol():
    class Fake:
        def position_entries(self, master_position_id):
            return [m.PositionMappingEntry(101, 555, 10_000_000)]
        def order_entries(self, master_order_id):
            return []
    state: m.MappingState = Fake()
    assert state.position_entries(11)[0].slave_position_id == 555
