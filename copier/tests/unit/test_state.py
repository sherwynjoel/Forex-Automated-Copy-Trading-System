"""Tests for account state tracking: balance, equity, open P&L."""

import pytest
import pytest_twisted
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASpotEvent, ProtoOASubscribeSpotsReq, ProtoOATraderReq, ProtoOATraderRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from dataclasses import dataclass
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.ctrader.client import CTraderClient
from copier.domain.models import Side, SymbolInfo
from copier.engine.state import (
    SPOT_PRICE_SCALE, unrealized_pnl_quote, AccountStateTracker, PositionSnapshot)
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


def test_partial_spot_event_retains_the_other_side():
    """RED before the fix: live ticks routinely carry only one side (bid
    and ask are optional fields), and reading the absent side yields
    protobuf's default 0 -- so an ask-only tick clobbered the retained bid
    with 0 and a BUY position's P&L displayed -(entry x volume) as a huge
    bogus loss (seen live on Stage 1: -5797.05 on a 0.05-lot EURUSD BUY)."""
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
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=1, side=Side.BUY,
                        volume=500_000, price=1.15941, label="")
    ]

    # Full tick first: both sides known
    full = ProtoOASpotEvent()
    full.ctidTraderAccountId = 1001
    full.symbolId = 1
    full.bid = 115964
    full.ask = 115966
    tracker.on_spot(full)

    # Ask-only tick: bid field absent on the wire
    partial = ProtoOASpotEvent()
    partial.ctidTraderAccountId = 1001
    partial.symbolId = 1
    partial.ask = 115968
    tracker.on_spot(partial)

    bid, ask = tracker._spots[1]
    assert bid == pytest.approx(1.15964)   # retained, not zeroed
    assert ask == pytest.approx(1.15968)
    pnl = tracker.snapshot()[1001]["positions"][0]["pnl_quote"]
    assert pnl == pytest.approx((1.15964 - 1.15941) * 5000)


def test_spot_side_never_seen_falls_back_to_entry_price():
    """A side that has never arrived stays None internally; P&L must fall
    back to the entry price for that side (P&L 0), matching the behavior
    when no spot has arrived at all."""
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
    tracker._positions[1001] = [
        PositionSnapshot(position_id=1, symbol_id=1, side=Side.BUY,
                        volume=500_000, price=1.15941, label="")
    ]

    # Only an ask-side tick ever arrives; a BUY closes at bid, which is unknown
    partial = ProtoOASpotEvent()
    partial.ctidTraderAccountId = 1001
    partial.symbolId = 1
    partial.ask = 115968
    tracker.on_spot(partial)

    pnl = tracker.snapshot()[1001]["positions"][0]["pnl_quote"]
    assert pnl == pytest.approx(0.0)


def test_spot_event_scaling_for_3_digit_symbol():
    """N5: spot prices are FIXED 1/100_000 units, even for a 3-digit symbol.

    This test previously PINNED THE BUG: it encoded USDJPY's 110.50 as
    `int(110.50 * 1000)` -- i.e. it assumed the wire scaled by the symbol's
    `digits` -- and asserted the tracker divided by the same 1000. cTrader
    does not: `ProtoOASpotEvent.bid`/`.ask` are 1/100_000 units for every
    symbol regardless of `digits` (which is display precision only), which
    is why the official OpenApiPy samples divide by 100000 unconditionally.

    So a real USDJPY quote of 110.50 arrives as 11_050_000, and `/10**3`
    turned that into 11_050.0 -- a price 100x too large, and therefore an
    open P&L and equity 100x too large, on exactly the Overview numbers the
    rollout stages tell the operator to watch.
    """
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    # USDJPY: digits=3 (display precision), lot 10_000_000.
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

    # How the wire really carries 110.50 / 110.51 for a JPY pair.
    spot_evt = ProtoOASpotEvent()
    spot_evt.symbolId = 2
    spot_evt.bid = 11_050_000
    spot_evt.ask = 11_051_000
    assert spot_evt.bid == int(110.50 * SPOT_PRICE_SCALE)

    tracker.on_spot(spot_evt)

    bid, ask = tracker._spots[2]
    assert bid == pytest.approx(110.50, rel=1e-9)
    assert ask == pytest.approx(110.51, rel=1e-9)
    # The specific defect: dividing by 10**digits would give 11_050.0 here.
    assert bid != pytest.approx(11_050.0)

    # P&L: units = 10_000_000 / 100 = 100_000
    # pnl = (110.50 - 110.00) * 100_000 = 0.50 * 100_000 = 50_000
    snapshot = tracker.snapshot()
    assert snapshot[1001]["open_pnl"] == pytest.approx(50000.0)
    assert snapshot[1001]["equity"] == pytest.approx(51000.0)


def test_spot_scaling_is_identical_for_3_and_5_digit_symbols():
    """N5 regression guard: `digits` must play no part in spot scaling.

    The same raw wire value must decode to the same price whether the
    symbol is a 5-digit major or a 3-digit JPY pair -- which is precisely
    what a `10 ** digits` divisor cannot do.
    """
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    symbols = {
        1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5, lot_size=10_000_000,
                      min_volume=100_000, step_volume=100_000),
        2: SymbolInfo(symbol_id=2, name="USDJPY", digits=3, lot_size=10_000_000,
                      min_volume=100_000, step_volume=100_000),
    }
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    for symbol_id in (1, 2):
        evt = ProtoOASpotEvent()
        evt.symbolId = symbol_id
        evt.bid = 12_345_600
        evt.ask = 12_345_700
        tracker.on_spot(evt)

    assert tracker._spots[1] == tracker._spots[2]
    assert tracker._spots[2][0] == pytest.approx(123.456)


def test_spot_scaling_for_an_unknown_symbol_matches_a_known_one():
    """A symbol absent from the symbol map must decode identically: the
    scale is a protocol constant, not something looked up per symbol."""
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    tracker = AccountStateTracker(client, MockRepo(), 1001, {})

    evt = ProtoOASpotEvent()
    evt.symbolId = 4242            # not in the (empty) symbol map
    evt.bid = 110_500
    evt.ask = 110_520
    tracker.on_spot(evt)

    assert tracker._spots[4242][0] == pytest.approx(1.105)


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
    """Unmatched symbol is logged and position is surfaced with pnl_quote=None.

    Verifies dict schema consistency: both matched and unmatched branches
    must emit identical keys (position_id, symbol_id, symbol, side, volume,
    entry_price, pnl_quote) so downstream consumers don't KeyError.
    """
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

    # Verify all expected keys are present (schema consistency)
    expected_keys = {"position_id", "symbol_id", "symbol", "side", "volume", "entry_price", "pnl_quote"}
    assert set(pos.keys()) == expected_keys, f"Missing or extra keys: {set(pos.keys()) ^ expected_keys}"

    # Verify values
    assert pos["position_id"] == 1
    assert pos["symbol_id"] == 999
    assert pos["symbol"] is None  # Placeholder for unknown symbol
    assert pos["pnl_quote"] is None  # Unknown due to missing symbol
    assert pos["side"] == "BUY"
    assert pos["volume"] == 10_000_000
    assert pos["entry_price"] == 1.1000


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


def _spot_tracker():
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
    return tracker, sdk


def _open_position(position_id, symbol_id):
    return PositionSnapshot(position_id=position_id, symbol_id=symbol_id,
                            side=Side.BUY, volume=10_000_000, price=1.1, label="")


def test_ensure_spot_subscriptions_sends_only_new_symbols():
    """Prod logged ALREADY_SUBSCRIBED once per org per minute: every 60s
    resync tick re-sent the FULL spot subscription with no memory of what
    this connection already holds, and the broker rejected the repeat.
    ensure_spot_subscriptions must subscribe only the delta -- an unchanged
    position set puts nothing on the wire."""
    tracker, sdk = _spot_tracker()
    tracker._positions[1001] = [_open_position(1, symbol_id=1)]

    tracker.ensure_spot_subscriptions()
    tracker.ensure_spot_subscriptions()   # same positions: no wire traffic

    reqs = of_type(sdk.sent, ProtoOASubscribeSpotsReq)
    assert len(reqs) == 1

    tracker._positions[1001].append(_open_position(2, symbol_id=2))
    tracker.ensure_spot_subscriptions()

    reqs = of_type(sdk.sent, ProtoOASubscribeSpotsReq)
    assert len(reqs) == 2
    assert list(reqs[1].symbolId) == [2]  # only the NEW symbol


def test_quote_returns_the_last_spot_or_none():
    """quote() is the ticket's read: the last (bid, ask) seen for a symbol,
    or None when no tick has arrived on this connection yet."""
    tracker, _sdk = _spot_tracker()
    tracker._spots[1] = (1.1050, 1.1052)
    assert tracker.quote(1) == (1.1050, 1.1052)
    assert tracker.quote(2) is None


def test_ensure_spot_subscription_subscribes_a_single_symbol_once():
    """The trade ticket asks for quotes on symbols with no open position;
    ensure_spot_subscription subscribes exactly that symbol on the master
    connection, once — a repeat call puts nothing on the wire."""
    tracker, sdk = _spot_tracker()

    tracker.ensure_spot_subscription(2)
    tracker.ensure_spot_subscription(2)

    reqs = of_type(sdk.sent, ProtoOASubscribeSpotsReq)
    assert len(reqs) == 1
    assert list(reqs[0].symbolId) == [2]
    assert reqs[0].ctidTraderAccountId == 1001


def test_quotes_are_forgotten_when_the_connection_drops():
    """A disconnect makes every cached spot stale: quote() must answer None
    during the outage rather than serving the last pre-disconnect price as
    live to the trade ticket."""
    tracker, sdk = _spot_tracker()
    tracker._spots[1] = (1.1050, 1.1052)

    sdk.disconnect()

    assert tracker.quote(1) is None


def test_ensure_spot_subscription_does_not_stack_requests_while_one_is_in_flight():
    """The dashboard polls quotes every 2s. If the first subscribe is still
    unanswered (slow wire), repeat calls must not enqueue duplicates the
    broker would reject with ALREADY_SUBSCRIBED."""
    class SlowClient:
        """send() never resolves — the subscribe request stays in flight."""
        def __init__(self):
            self.sent = []
        def send(self, req):
            self.sent.append(req)
            return defer.Deferred()
        def on_spot(self, cb):
            pass
        def on_disconnected(self, cb):
            pass

    client = SlowClient()

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                             lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    tracker.ensure_spot_subscription(2)
    tracker.ensure_spot_subscription(2)   # first one still unanswered

    assert len(client.sent) == 1


def test_spot_subscriptions_reset_when_the_connection_drops():
    """Broker-side spot subscriptions die with the connection: after a
    reconnect the tracker must resubscribe from scratch, not assume the old
    connection's subscriptions carried over."""
    tracker, sdk = _spot_tracker()
    tracker._positions[1001] = [_open_position(1, symbol_id=1)]
    tracker.ensure_spot_subscriptions()

    sdk.disconnect()
    sdk.connect()
    tracker.ensure_spot_subscriptions()

    reqs = of_type(sdk.sent, ProtoOASubscribeSpotsReq)
    assert len(reqs) == 2
    assert list(reqs[1].symbolId) == [1]


class _RawEnvelopeClient:
    """Client stub whose send() resolves exactly the way the real
    CTraderClient.send() does: with the raw ProtoMessage envelope
    (payloadType + serialized payload bytes), NOT the unwrapped typed
    response. This is the shape refresh_balances() must Protobuf.extract()
    before .trader is reachable."""

    def __init__(self, raw_response):
        self._raw_response = raw_response
        self.sent = []

    def on_spot(self, cb):
        pass

    def on_disconnected(self, cb):
        pass

    def send(self, req):
        self.sent.append(req)
        return defer.succeed(self._raw_response)


def _trader_envelope(account_id):
    typed = ProtoOATraderRes()
    typed.ctidTraderAccountId = account_id
    typed.trader.ctidTraderAccountId = account_id
    typed.trader.balance = 100_00
    typed.trader.moneyDigits = 2
    typed.trader.depositAssetId = 1
    return ProtoMessage(payloadType=typed.payloadType,
                        payload=typed.SerializeToString())


def test_refresh_balances_routes_each_account_via_the_client_map():
    """Prod org 3 mixes one LIVE account into a demo fleet; the tracker's
    single master (demo) client can never serve the live account's
    ProtoOATraderReq -- the demo server rejected it with INVALID_REQUEST
    twice a minute and that balance stayed permanently unknown.
    refresh_balances takes a per-account client map, falling back to the
    master client only for accounts not in it."""
    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                             lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    master_client = _RawEnvelopeClient(_trader_envelope(1001))
    live_client = _RawEnvelopeClient(_trader_envelope(1002))
    tracker = AccountStateTracker(master_client, MockRepo(), 1001, symbols)

    tracker.refresh_balances([1001, 1002], clients_by_account={1002: live_client})

    assert [r.ctidTraderAccountId for r in master_client.sent] == [1001]
    assert [r.ctidTraderAccountId for r in live_client.sent] == [1002]
    # Both balances landed, whichever client served them.
    assert tracker._balance_known.get(1001) and tracker._balance_known.get(1002)


def test_refresh_balances_unwraps_raw_envelope_before_reading_trader():
    """Guards the regression where refresh_balances() read .trader straight
    off the raw ProtoMessage envelope CTraderClient.send() resolves with
    (which has no .trader field -- hasattr is False, so it silently marked
    every account's balance unknown) instead of first unwrapping it via
    Protobuf.extract into a typed ProtoOATraderRes."""
    typed = ProtoOATraderRes()
    typed.ctidTraderAccountId = 1001
    typed.trader.ctidTraderAccountId = 1001
    typed.trader.balance = 123456
    typed.trader.moneyDigits = 2
    typed.trader.depositAssetId = 1

    raw_envelope = ProtoMessage(
        payloadType=typed.payloadType, payload=typed.SerializeToString(),
    )
    assert not hasattr(raw_envelope, "trader")  # sanity: this is the raw envelope shape

    class MockRepo:
        pass

    symbols = {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                            lot_size=10_000_000, min_volume=100_000, step_volume=100_000)}
    client = _RawEnvelopeClient(raw_envelope)
    tracker = AccountStateTracker(client, MockRepo(), 1001, symbols)

    d = tracker.refresh_balances([1001])
    results = []
    d.addCallback(results.append)
    assert results  # the stub resolves synchronously

    assert tracker._balance_known[1001] is True
    assert tracker._balances[1001] == pytest.approx(1234.56)


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

def test_trader_updated_event_updates_balance_immediately():
    """A pushed ProtoOATraderUpdatedEvent updates the tracked balance without
    waiting for the next 60s poll."""
    from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOATraderUpdatedEvent

    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    sdk.connect()

    class MockRepo:
        pass

    tracker = AccountStateTracker(client, MockRepo(), 1001, {})

    evt = ProtoOATraderUpdatedEvent()
    evt.ctidTraderAccountId = 2002
    evt.trader.ctidTraderAccountId = 2002
    evt.trader.balance = 1_234_567
    evt.trader.moneyDigits = 2
    tracker.on_trader_updated(evt)

    assert tracker.snapshot()[2002]["balance"] == pytest.approx(12345.67)
