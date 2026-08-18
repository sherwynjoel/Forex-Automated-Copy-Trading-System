"""Integration tests for the second wave of broker read models: expected
margin, trendbars (candles), cash-flow history, and deals-by-position.
Same harness as test_queries: real CTraderClient against FakeCTraderServer."""

import pytest_twisted
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAChangeBalanceType, ProtoOADealStatus, ProtoOATradeSide,
    ProtoOATrendbarPeriod,
)

from copier.domain.models import SymbolInfo
from copier.engine import queries

ACCOUNT_ID = 1001
ACCESS_TOKEN = "tok-1001"

EURUSD = SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                    lot_size=10_000_000, min_volume=100_000, step_volume=100_000)
SYMBOLS = {1: EURUSD}


@pytest_twisted.inlineCallbacks
def test_expected_margin_scales_money(server_and_client):
    server, client = server_and_client
    # Fake computes margin as volume * factor; moneyDigits=2.
    server.margin_factors = (0.002, 0.0025)

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    result = yield queries.expected_margin(client, ACCOUNT_ID, EURUSD, 1_000_000)

    assert result["volume"] == 1_000_000
    # 1_000_000 * 0.002 = 2000 broker-cents -> 20.00
    assert result["buy_margin"] == 20.0
    assert result["sell_margin"] == 25.0


@pytest_twisted.inlineCallbacks
def test_trendbars_decode_relative_prices(server_and_client):
    server, client = server_and_client
    server.trendbars[(ACCOUNT_ID, 1, ProtoOATrendbarPeriod.H1)] = [
        {
            "utc_ts_minutes": 1_000_000, "volume": 42,
            "low": 110_000, "delta_open": 500, "delta_high": 900, "delta_close": 700,
        },
        {
            "utc_ts_minutes": 1_000_060, "volume": 10,
            "low": 110_400, "delta_open": 300, "delta_high": 600, "delta_close": 100,
        },
    ]

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    result = yield queries.trendbars(
        client, ACCOUNT_ID, EURUSD, "H1",
        from_ms=0, to_ms=1_000_100 * 60_000)

    assert result["period"] == "H1"
    assert len(result["bars"]) == 2
    bar = result["bars"][0]
    assert bar["timestamp"] == 1_000_000 * 60_000
    assert bar["low"] == 1.10
    assert bar["open"] == 1.105
    assert bar["high"] == 1.109
    assert bar["close"] == 1.107
    assert bar["volume"] == 42


@pytest_twisted.inlineCallbacks
def test_cash_flow_history_maps_operations(server_and_client):
    server, client = server_and_client
    server.cash_flows[ACCOUNT_ID] = [
        {
            "id": 1, "operation_type": ProtoOAChangeBalanceType.BALANCE_DEPOSIT,
            "balance": 1_000_000, "delta": 1_000_000,
            "timestamp": 5_000, "money_digits": 2, "note": "initial funding",
        },
        {
            "id": 2, "operation_type": ProtoOAChangeBalanceType.BALANCE_WITHDRAW,
            "balance": 900_000, "delta": 100_000,
            "timestamp": 9_000, "money_digits": 2,
        },
        {
            # outside window
            "id": 3, "operation_type": ProtoOAChangeBalanceType.BALANCE_DEPOSIT,
            "balance": 950_000, "delta": 50_000,
            "timestamp": 99_000, "money_digits": 2,
        },
    ]

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    result = yield queries.cash_flow_history(client, ACCOUNT_ID, from_ms=0, to_ms=50_000)

    assert [e["id"] for e in result["entries"]] == [1, 2]
    deposit = result["entries"][0]
    assert deposit["type"] == "DEPOSIT"
    assert deposit["amount"] == 10_000.0
    assert deposit["balance_after"] == 10_000.0
    assert deposit["note"] == "initial funding"
    withdraw = result["entries"][1]
    assert withdraw["type"] == "WITHDRAW"
    assert withdraw["amount"] == 1_000.0
    assert withdraw["balance_after"] == 9_000.0


@pytest_twisted.inlineCallbacks
def test_position_deals_filters_by_position(server_and_client):
    server, client = server_and_client
    server.deals[ACCOUNT_ID] = [
        {
            "deal_id": 1, "order_id": 11, "position_id": 21,
            "volume": 100_000, "filled_volume": 100_000, "symbol_id": 1,
            "execution_price": 1.10, "trade_side": ProtoOATradeSide.BUY,
            "status": ProtoOADealStatus.FILLED,
            "create_timestamp": 1_000, "execution_timestamp": 1_000,
            "commission": -70, "money_digits": 2,
        },
        {
            "deal_id": 2, "order_id": 12, "position_id": 99,  # different position
            "volume": 100_000, "filled_volume": 100_000, "symbol_id": 1,
            "execution_price": 1.20, "trade_side": ProtoOATradeSide.SELL,
            "status": ProtoOADealStatus.FILLED,
            "create_timestamp": 2_000, "execution_timestamp": 2_000,
            "commission": -70, "money_digits": 2,
        },
    ]

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    result = yield queries.position_deals(
        client, ACCOUNT_ID, 21, SYMBOLS, from_ms=0, to_ms=50_000)

    assert [d["deal_id"] for d in result["deals"]] == [1]
    assert result["deals"][0]["symbol"] == "EURUSD"
