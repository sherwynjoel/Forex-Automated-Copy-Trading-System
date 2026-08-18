"""Integration tests for engine/queries.py against the fake cTrader server.

Drives the real CTraderClient (tagged send() round trips over TLS) against
FakeCTraderServer's trader/asset/deal-list/order-list handlers, proving the
on-demand read models map broker payloads into the JSON-ready dicts the
dashboard consumes: scaled money (moneyDigits), symbol names from the
account's symbol map, volumes in lots, and enum names instead of ints.
"""

import pytest
import pytest_twisted
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAAccessRights,
    ProtoOAAccountType,
    ProtoOADealStatus,
    ProtoOAOrderStatus,
    ProtoOAOrderType,
    ProtoOATradeSide,
)

from copier.domain.models import SymbolInfo
from copier.engine import queries

ACCOUNT_ID = 1001
ACCESS_TOKEN = "tok-1001"

SYMBOLS = {
    1: SymbolInfo(
        symbol_id=1, name="EURUSD", digits=5,
        lot_size=10_000_000, min_volume=100_000, step_volume=100_000,
    )
}


@pytest_twisted.inlineCallbacks
def test_account_details_reports_full_trader_profile(server_and_client):
    server, client = server_and_client
    server.balances[ACCOUNT_ID] = 1_234_567  # broker cents (moneyDigits=2)
    server.trader_details[ACCOUNT_ID] = {
        "leverage_in_cents": 50_00,
        "max_leverage": 500_00,
        "broker_name": "FP Markets",
        "registration_timestamp": 1_700_000_000_000,
        "account_type": ProtoOAAccountType.HEDGED,
        "trader_login": 987654,
        "money_digits": 2,
        "swap_free": True,
        "access_rights": ProtoOAAccessRights.FULL_ACCESS,
        "deposit_asset_id": 1,
    }
    server.open_positions[ACCOUNT_ID] = [{
        "position_id": 7001, "symbol_id": 1, "volume": 200_000,
        "trade_side": ProtoOATradeSide.BUY, "label": "", "price": 1.105,
    }]

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    details = yield queries.account_details(client, ACCOUNT_ID, SYMBOLS)

    assert details["account_id"] == ACCOUNT_ID
    assert details["balance"] == 12345.67
    assert details["leverage"] == 50.0
    assert details["max_leverage"] == 500.0
    assert details["broker_name"] == "FP Markets"
    assert details["registration_timestamp"] == 1_700_000_000_000
    assert details["account_type"] == "HEDGED"
    assert details["access_rights"] == "FULL_ACCESS"
    assert details["swap_free"] is True
    assert details["trader_login"] == 987654
    assert details["deposit_currency"] == "USD"

    pos = details["open_positions"][0]
    assert pos["position_id"] == 7001
    assert pos["symbol"] == "EURUSD"
    assert pos["side"] == "BUY"
    assert pos["volume"] == 200_000
    assert pos["volume_lots"] == "0.02"
    assert pos["price"] == 1.105


@pytest_twisted.inlineCallbacks
def test_deal_history_maps_deals_and_close_detail(server_and_client):
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
            "deal_id": 2, "order_id": 12, "position_id": 21,
            "volume": 100_000, "filled_volume": 100_000, "symbol_id": 1,
            "execution_price": 1.12, "trade_side": ProtoOATradeSide.SELL,
            "status": ProtoOADealStatus.FILLED,
            "create_timestamp": 2_000, "execution_timestamp": 2_000,
            "commission": -70, "money_digits": 2,
            "close_position_detail": {
                "entry_price": 1.10, "gross_profit": 2_000, "swap": -12,
                "commission": -70, "balance": 1_002_000,
                "closed_volume": 100_000, "money_digits": 2,
            },
        },
        {
            # Outside the requested window -- must be filtered out.
            "deal_id": 3, "order_id": 13, "position_id": 22,
            "volume": 100_000, "filled_volume": 100_000, "symbol_id": 1,
            "execution_price": 1.15, "trade_side": ProtoOATradeSide.BUY,
            "status": ProtoOADealStatus.FILLED,
            "create_timestamp": 99_000, "execution_timestamp": 99_000,
            "commission": -70, "money_digits": 2,
        },
    ]

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    result = yield queries.deal_history(
        client, ACCOUNT_ID, SYMBOLS, from_ms=0, to_ms=50_000)

    assert [d["deal_id"] for d in result["deals"]] == [1, 2]
    assert result["has_more"] is False

    opening = result["deals"][0]
    assert opening["symbol"] == "EURUSD"
    assert opening["side"] == "BUY"
    assert opening["volume_lots"] == "0.01"
    assert opening["execution_price"] == 1.10
    assert opening["commission"] == -0.7
    assert opening["status"] == "FILLED"
    assert opening["close"] is None

    closing = result["deals"][1]
    assert closing["side"] == "SELL"
    assert closing["close"]["entry_price"] == 1.10
    assert closing["close"]["gross_profit"] == 20.0
    assert closing["close"]["swap"] == -0.12
    assert closing["close"]["commission"] == -0.7
    assert closing["close"]["balance"] == 10020.0
    assert closing["close"]["closed_volume"] == 100_000


@pytest_twisted.inlineCallbacks
def test_order_history_maps_orders(server_and_client):
    server, client = server_and_client
    server.historical_orders[ACCOUNT_ID] = [
        {
            "order_id": 11, "symbol_id": 1, "volume": 100_000,
            "trade_side": ProtoOATradeSide.BUY,
            "order_type": ProtoOAOrderType.LIMIT,
            "order_status": ProtoOAOrderStatus.ORDER_STATUS_FILLED,
            "limit_price": 1.0950, "execution_price": 1.0950,
            "executed_volume": 100_000, "position_id": 21,
            "open_timestamp": 1_200, "utc_last_update_timestamp": 1_500,
            "label": "manual",
        },
        {
            # Outside the requested window -- must be filtered out.
            "order_id": 12, "symbol_id": 1, "volume": 100_000,
            "trade_side": ProtoOATradeSide.SELL,
            "order_type": ProtoOAOrderType.MARKET,
            "order_status": ProtoOAOrderStatus.ORDER_STATUS_FILLED,
            "execution_price": 1.20, "executed_volume": 100_000,
            "position_id": 22,
            "open_timestamp": 99_000, "utc_last_update_timestamp": 99_500,
            "label": "",
        },
    ]

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    result = yield queries.order_history(
        client, ACCOUNT_ID, SYMBOLS, from_ms=0, to_ms=50_000)

    assert [o["order_id"] for o in result["orders"]] == [11]
    assert result["has_more"] is False

    order = result["orders"][0]
    assert order["symbol"] == "EURUSD"
    assert order["side"] == "BUY"
    assert order["order_type"] == "LIMIT"
    assert order["status"] == "FILLED"
    assert order["volume_lots"] == "0.01"
    assert order["limit_price"] == 1.0950
    assert order["execution_price"] == 1.0950
    assert order["position_id"] == 21
    assert order["label"] == "manual"


@pytest_twisted.inlineCallbacks
def test_query_error_response_raises(server_and_client):
    server, client = server_and_client
    server.fail_next_query_error_code = "CH_ACCESS_TOKEN_INVALID"

    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    with pytest.raises(queries.QueryFailed) as exc_info:
        yield queries.deal_history(
            client, ACCOUNT_ID, SYMBOLS, from_ms=0, to_ms=50_000)

    assert "CH_ACCESS_TOKEN_INVALID" in str(exc_info.value)
