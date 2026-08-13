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
from copier.engine.state import unrealized_pnl_quote, AccountStateTracker, PositionSnapshot
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


def test_negative_pnl_when_position_moved_against_you():
    """P&L is negative when position moves against you, equity < balance."""
    # BUY at 1.1050, bid drops to 1.1000
    # units = 10_000_000 / 100 = 100_000
    # pnl = (1.1000 - 1.1050) * 100_000 = -0.0050 * 100_000 = -500
    pnl = unrealized_pnl_quote(Side.BUY, 1.1050, 10_000_000, 1.1000, 1.1001)
    assert pnl == pytest.approx(-500.0)


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

    # Create tracker with 5-digit symbol (EURUSD)
    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Manually set balance and positions
    # Account 1001 with balance of 1000.0
    tracker._balances[1001] = 1000.0
    tracker._balance_known[1001] = True

    # Position: BUY 10_000_000 vol at 1.1000, symbol_id=1
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=1, side=Side.BUY,
                        volume=10_000_000, price=1.1000, label="")
    ]

    # Set spot (bid 1.1050, ask 1.1052)
    tracker._spots = {1: (1.1050, 1.1052)}

    snapshot = tracker.snapshot()
    assert 1001 in snapshot
    account_state = snapshot[1001]
    assert account_state["balance"] == 1000.0
    assert account_state["open_pnl"] == pytest.approx(500.0)
    assert account_state["equity"] == pytest.approx(1500.0)


def test_spot_event_updates_pnl_with_5_digit_symbol():
    """on_spot callback updates open P&L calculations with 5-digit scaling."""
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
    tracker._balance_known[1001] = True

    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=1, side=Side.BUY,
                        volume=10_000_000, price=1.1000, label="")
    ]

    # Push spot event with bid 1.1050, ask 1.1052 (scaled by 10**5 = 100000)
    spot_evt = ProtoOASpotEvent()
    spot_evt.ctidTraderAccountId = 1001
    spot_evt.symbolId = 1
    spot_evt.bid = int(1.1050 * 100000)  # 110500
    spot_evt.ask = int(1.1052 * 100000)  # 110520

    # This should trigger on_spot
    tracker.on_spot(spot_evt)

    # Check that spot was updated
    assert 1 in tracker._spots
    bid, ask = tracker._spots[1]
    assert bid == pytest.approx(1.1050)
    assert ask == pytest.approx(1.1052)

    # Verify P&L is calculated
    snapshot = tracker.snapshot()
    assert snapshot[1001]["equity"] == pytest.approx(1500.0)


def test_spot_event_scaling_for_3_digit_symbol():
    """Spot event scaling respects per-symbol digits (USDJPY with digits=3)."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    # USDJPY has digits=3 (e.g., 110.50 is represented as 110500 in protocol)
    symbols = {2: SymbolInfo(symbol_id=2, name="USDJPY", digits=3,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    tracker._balances[1001] = 1000.0
    tracker._balance_known[1001] = True

    # BUY USDJPY at 110.00
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=2, side=Side.BUY,
                        volume=10_000_000, price=110.00, label="")
    ]

    # Push spot event with bid 110.50 (scaled by 10**3 = 1000)
    spot_evt = ProtoOASpotEvent()
    spot_evt.symbolId = 2
    spot_evt.bid = int(110.50 * 1000)  # 110500
    spot_evt.ask = int(110.51 * 1000)  # 110510

    tracker.on_spot(spot_evt)

    # Verify spot was correctly scaled
    bid, ask = tracker._spots[2]
    assert bid == pytest.approx(110.50, rel=1e-4)
    assert ask == pytest.approx(110.51, rel=1e-4)

    # P&L: units = 10_000_000 / 100 = 100_000
    # pnl = (110.50 - 110.00) * 100_000 = 0.50 * 100_000 = 50_000
    snapshot = tracker.snapshot()
    assert snapshot[1001]["open_pnl"] == pytest.approx(50000.0)


def test_balance_scaling_with_non_2_money_digits():
    """Balance is scaled by 10**moneyDigits (not hardcoded /100)."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Simulate a trader response with moneyDigits=3 (balance in thousandths)
    class MockTrader:
        balance = 5000  # 5000 in thousandths = 5.000
        moneyDigits = 3

    class MockResponse:
        trader = MockTrader()

    # Process the response
    tracker._process_trader_response(MockResponse(), 1001)

    # Verify balance was scaled by 10**3
    assert tracker._balances[1001] == pytest.approx(5.0)
    assert tracker._balance_known[1001] is True


def test_balance_unknown_when_trader_response_missing_trader():
    """Balance is None and balance_known=False when trader field is missing."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Response with no trader field
    class MockResponse:
        pass

    tracker._process_trader_response(MockResponse(), 1001)

    # Verify balance is unknown
    assert tracker._balances[1001] is None
    assert tracker._balance_known[1001] is False


def test_equity_is_none_when_balance_unknown():
    """Equity is None (not $0) when balance hasn't been refreshed."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Only set position, don't set balance
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=1, side=Side.BUY,
                        volume=10_000_000, price=1.1000, label="")
    ]
    tracker._spots = {1: (1.1050, 1.1052)}

    snapshot = tracker.snapshot()
    account_state = snapshot[1001]
    assert account_state["balance"] is None
    assert account_state["equity"] is None
    assert account_state["open_pnl"] == pytest.approx(500.0)


def test_position_with_unmatched_symbol_is_surfaced_with_none_pnl():
    """Unmatched symbol is logged and position is surfaced with pnl_quote=None."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    tracker._balances[1001] = 1000.0
    tracker._balance_known[1001] = True

    # Position with symbol_id=999 that doesn't exist in symbols_by_id
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=999, side=Side.BUY,
                        volume=10_000_000, price=1.1000, label="")
    ]

    snapshot = tracker.snapshot()
    account_state = snapshot[1001]

    # Position is still in the list
    assert len(account_state["positions"]) == 1
    pos = account_state["positions"][0]
    assert pos["position_id"] == 1
    assert pos["symbol_id"] == 999
    assert pos["pnl_quote"] is None  # Unknown due to missing symbol


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

    # Only symbol_id=1 (EURUSD) has an open position
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=1, side=Side.BUY,
                        volume=10_000_000, price=1.1000, label="")
    ]

    # Call ensure_spot_subscriptions - should subscribe only to symbol_id=1
    d = tracker.ensure_spot_subscriptions()

    # Check that a ProtoOASubscribeSpotsReq was sent
    reqs = of_type(sdk.sent, ProtoOASubscribeSpotsReq)
    assert len(reqs) == 1
    # The request should only have symbol_id 1
    assert list(reqs[0].symbolId) == [1]


def test_fallback_spot_scaling_when_symbol_digits_unavailable():
    """Spot event scaling falls back to 10**5 if symbol_id unavailable."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {}  # Empty: no symbol_id mappings
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    # Push spot for unknown symbol_id 999
    spot_evt = ProtoOASpotEvent()
    spot_evt.symbolId = 999
    spot_evt.bid = int(1.1050 * 100000)  # Assume 5-digit encoding
    spot_evt.ask = int(1.1052 * 100000)

    tracker.on_spot(spot_evt)

    # Should fallback to 10**5 scaling
    bid, ask = tracker._spots[999]
    assert bid == pytest.approx(1.1050)
    assert ask == pytest.approx(1.1052)
