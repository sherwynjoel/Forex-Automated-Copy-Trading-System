"""capture.execution_row: protobuf -> executions row.

Pure function, so these tests need no database and no reactor. This is the
piece that finally records the MASTER trade's own economics -- entry price,
volume, side, symbol and the broker's execution timestamp -- which
_handle_master_event previously threw away.
"""

import pytest
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType, ProtoOATradeSide)

from copier.domain.models import SymbolInfo
from copier.engine.capture import execution_row

SYMBOLS = {
    1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5, lot_size=100000,
                  min_volume=1000, step_volume=1000)
}


def _fill_event():
    evt = ProtoOAExecutionEvent()
    evt.executionType = ProtoOAExecutionType.ORDER_FILLED
    evt.order.orderId = 777
    evt.order.orderType = ProtoOAOrderType.MARKET
    evt.order.tradeData.symbolId = 1
    evt.order.tradeData.volume = 100000
    evt.order.tradeData.tradeSide = ProtoOATradeSide.BUY
    evt.deal.dealId = 999
    evt.deal.positionId = 555
    evt.deal.filledVolume = 100000
    evt.deal.executionPrice = 1.0855
    evt.deal.executionTimestamp = 1700000000000
    return evt


def test_master_fill_records_price_volume_side_symbol_and_broker_clock():
    row = execution_row(
        _fill_event(), account_id=100, org_id=7, is_master=True,
        symbols_by_id=SYMBOLS, quote=(1.0854, 1.0856))
    assert row["account_id"] == 100
    assert row["org_id"] == 7
    assert row["is_master"] is True
    assert row["execution_type"] == "ORDER_FILLED"
    assert row["symbol"] == "EURUSD"
    assert row["side"] == "BUY"
    assert row["volume"] == 100000
    assert row["execution_price"] == pytest.approx(1.0855)
    assert row["execution_timestamp"] == 1700000000000
    assert row["position_id"] == 555
    assert row["deal_id"] == 999
    assert row["order_id"] == 777


def test_quote_at_execution_is_recorded_when_available():
    row = execution_row(
        _fill_event(), account_id=100, org_id=7, is_master=True,
        symbols_by_id=SYMBOLS, quote=(1.0854, 1.0856))
    assert row["bid_at_exec"] == pytest.approx(1.0854)
    assert row["ask_at_exec"] == pytest.approx(1.0856)


def test_missing_quote_is_null_not_zero():
    """A missing quote must not masquerade as a price of 0."""
    row = execution_row(
        _fill_event(), account_id=100, org_id=7, is_master=True,
        symbols_by_id=SYMBOLS, quote=None)
    assert row["bid_at_exec"] is None
    assert row["ask_at_exec"] is None


def test_unknown_symbol_still_produces_a_row_with_a_null_name():
    """An unmapped symbol is exactly the case that used to vanish behind a
    bare {"normalized": null}. The row must still be written."""
    row = execution_row(
        _fill_event(), account_id=100, org_id=7, is_master=True,
        symbols_by_id={}, quote=None)
    assert row["symbol"] is None
    assert row["symbol_id"] == 1
    assert row["execution_type"] == "ORDER_FILLED"


def test_close_records_realized_economics():
    evt = _fill_event()
    evt.deal.moneyDigits = 2
    evt.deal.commission = -7
    evt.deal.closePositionDetail.closedVolume = 50000
    evt.deal.closePositionDetail.entryPrice = 1.0800
    evt.deal.closePositionDetail.grossProfit = 275
    evt.deal.closePositionDetail.swap = -12
    evt.deal.closePositionDetail.balance = 1000275
    evt.deal.closePositionDetail.moneyDigits = 2
    row = execution_row(evt, account_id=100, org_id=7, is_master=True,
                        symbols_by_id=SYMBOLS, quote=None)
    assert row["closed_volume"] == 50000
    assert row["gross_profit"] == pytest.approx(2.75)
    assert row["swap"] == pytest.approx(-0.12)
    assert row["commission"] == pytest.approx(-0.07)
    assert row["balance_after"] == pytest.approx(10002.75)


def test_opening_deal_records_its_own_commission():
    """The broker charges commission on the open leg too. Reading it only
    from closePositionDetail dropped it for every position we opened."""
    evt = _fill_event()
    evt.deal.commission = -7
    evt.deal.moneyDigits = 2
    row = execution_row(evt, account_id=100, org_id=7, is_master=True,
                        symbols_by_id=SYMBOLS, quote=None)
    assert row["commission"] == pytest.approx(-0.07)
    assert row["gross_profit"] is None   # still an open, no realized P&L


def test_close_economics_use_the_close_blocks_own_money_digits():
    evt = _fill_event()
    evt.deal.moneyDigits = 2
    evt.deal.closePositionDetail.closedVolume = 50000
    evt.deal.closePositionDetail.grossProfit = 27500
    evt.deal.closePositionDetail.moneyDigits = 4
    row = execution_row(evt, account_id=100, org_id=7, is_master=True,
                        symbols_by_id=SYMBOLS, quote=None)
    assert row["gross_profit"] == pytest.approx(2.75)


def test_rejection_records_the_error_code():
    evt = ProtoOAExecutionEvent()
    evt.executionType = ProtoOAExecutionType.ORDER_REJECTED
    evt.errorCode = "NOT_ENOUGH_MONEY"
    row = execution_row(evt, account_id=100, org_id=7, is_master=True,
                        symbols_by_id=SYMBOLS, quote=None)
    assert row["execution_type"] == "ORDER_REJECTED"
    assert row["error_code"] == "NOT_ENOUGH_MONEY"


def test_raw_payload_is_always_present():
    row = execution_row(_fill_event(), account_id=100, org_id=7,
                        is_master=True, symbols_by_id=SYMBOLS, quote=None)
    assert row["raw"] is not None
