"""Tests for account state tracking: balance, equity, open P&L."""

import pytest
import pytest_twisted
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASpotEvent, ProtoOASubscribeSpotsReq, ProtoOATraderReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from dataclasses import dataclass
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.ctrader.client import CTraderClient
from copier.domain.models import Side, SymbolInfo
from copier.engine.state import unrealized_pnl_quote, AccountStateTracker
from test_client import StubSdk, of_type


def test_buy_pnl_uses_bid():
    """BUY 1 lot EURUSD (vol 10_000_000 -> 100_000 units) entry 1.1000, bid 1.1050."""
    # units = 10_000_000 / 100 = 100_000
    # pnl = (1.1050 - 1.1000) * 100_000 = 0.0050 * 100_000 = 500.0
    assert unrealized_pnl_quote(Side.BUY, 1.1000, 10_000_000, 1.1050, 1.1052) == pytest.approx(500.0)


def test_sell_pnl_uses_ask():
    """SELL 1 lot EURUSD (vol 10_000_000 -> 100_000 units) entry 1.1000, ask 1.0950."""
    # units = 10_000_000 / 100 = 100_000
    # pnl = (1.1000 - 1.0950) * 100_000 = 0.0050 * 100_000 = 500.0
    assert unrealized_pnl_quote(Side.SELL, 1.1000, 10_000_000, 1.0948, 1.0950) == pytest.approx(500.0)


def test_snapshot_equity_is_balance_plus_open_pnl():
    """Equity = balance + unrealized P&L across open positions."""
    # Create stub SDK and client
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    # Create mock repo (minimal)
    class MockRepo:
        pass

    # Create tracker
    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Manually set balance and positions
    # Account 1001 with balance of 1000.0
    tracker._balances[1001] = 1000.0

    # Position: BUY 10_000_000 vol at 1.1000, current bid 1.1050
    from copier.engine.state import PositionSnapshot

    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol="EURUSD", side=Side.BUY,
                        volume=10_000_000, entry_price=1.1000)
    ]

    # Set spot (bid 1.1050, ask 1.1052) - scaled as ints (multiply by 100000)
    tracker._spots = {1: (1.1050, 1.1052)}

    snapshot = tracker.snapshot()
    assert 1001 in snapshot
    account_state = snapshot[1001]
    assert account_state["balance"] == 1000.0
    assert account_state["open_pnl"] == pytest.approx(500.0)
    assert account_state["equity"] == pytest.approx(1500.0)


def test_spot_event_updates_pnl():
    """on_spot callback updates open P&L calculations."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Set up balance and position
    tracker._balances[1001] = 1000.0

    from copier.engine.state import PositionSnapshot

    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol="EURUSD", side=Side.BUY,
                        volume=10_000_000, entry_price=1.1000)
    ]

    # Push spot event with bid 1.1050, ask 1.1052 (scaled by 100000)
    spot_evt = ProtoOASpotEvent()
    spot_evt.ctidTraderAccountId = 1001
    spot_evt.symbolId = 1
    spot_evt.bid = int(1.1050 * 100000)  # 110500
    spot_evt.ask = int(1.1052 * 100000)  # 110520

    # This should trigger on_spot through the client callback
    tracker.on_spot(spot_evt)

    # Check that spot was updated
    assert 1 in tracker._spots
    bid, ask = tracker._spots[1]
    assert bid == pytest.approx(1.1050)
    assert ask == pytest.approx(1.1052)

    # Verify P&L is calculated
    snapshot = tracker.snapshot()
    assert snapshot[1001]["equity"] == pytest.approx(1500.0)


def test_subscribes_only_open_position_symbols():
    """ensure_spot_subscriptions sends subscription for open position symbols only."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {
        1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                     lot_size=10_000_000, min_volume=100_000, step_volume=100_000),
        2: SymbolInfo(symbol_id=2, name="GBPUSD", digits=5,
                     lot_size=10_000_000, min_volume=100_000, step_volume=100_000),
    }
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    from copier.engine.state import PositionSnapshot

    # Only EURUSD has an open position
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol="EURUSD", side=Side.BUY,
                        volume=10_000_000, entry_price=1.1000)
    ]

    # Call ensure_spot_subscriptions - should subscribe only to symbol_id=1 (EURUSD)
    d = tracker.ensure_spot_subscriptions()

    # Check that a ProtoOASubscribeSpotsReq was sent
    reqs = of_type(sdk.sent, ProtoOASubscribeSpotsReq)
    assert len(reqs) == 1
    # The request should only have symbol_id 1
    assert list(reqs[0].symbolId) == [1]
