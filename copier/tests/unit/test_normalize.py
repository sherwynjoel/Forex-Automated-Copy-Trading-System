from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType, ProtoOATradeSide)

from copier.domain import models as m
from copier.engine.normalize import normalize

SYMS = {1: m.SymbolInfo(1, "EURUSD", 5, 10_000_000, 100_000, 100_000)}


def base_event(execution_type, order_type=ProtoOAOrderType.MARKET, order_id=5,
               position_id=11, volume=10_000_000):
    e = ProtoOAExecutionEvent()
    e.ctidTraderAccountId = 100
    e.executionType = execution_type
    e.order.orderId = order_id
    e.order.orderType = order_type
    e.order.tradeData.symbolId = 1
    e.order.tradeData.tradeSide = ProtoOATradeSide.BUY
    e.order.tradeData.volume = volume
    e.position.positionId = position_id
    e.position.tradeData.volume = volume
    return e


def test_market_fill_opens_position():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED)
    e.deal.positionId = 11
    e.deal.filledVolume = 10_000_000
    out = normalize(e, SYMS)
    assert out == m.MasterPositionOpened(11, "EURUSD", m.Side.BUY, 10_000_000,
                                         10_000_000, None, None)


def test_fill_with_close_detail_is_a_close():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED)
    e.deal.positionId = 11
    e.deal.filledVolume = 4_000_000
    e.deal.closePositionDetail.closedVolume = 4_000_000
    e.position.tradeData.volume = 6_000_000     # remaining
    out = normalize(e, SYMS)
    assert out == m.MasterPositionClosed(11, "EURUSD", 4_000_000, 6_000_000)


def test_limit_accept_is_pending_placed():
    e = base_event(ProtoOAExecutionType.ORDER_ACCEPTED, ProtoOAOrderType.LIMIT, order_id=42)
    e.order.limitPrice = 1.115
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterPendingPlaced)
    assert (out.order_id, out.order_type, out.price) == (42, m.PendingType.LIMIT, 1.115)


def test_market_accept_is_ignored():
    assert normalize(base_event(ProtoOAExecutionType.ORDER_ACCEPTED), SYMS) is None


def test_limit_fill_is_pending_filled_not_open():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED, ProtoOAOrderType.LIMIT, order_id=42)
    e.deal.positionId = 77
    e.deal.filledVolume = 10_000_000
    assert normalize(e, SYMS) == m.MasterPendingFilled(42, 77)


def test_replace_cancel_expire_pending():
    rep = base_event(ProtoOAExecutionType.ORDER_REPLACED, ProtoOAOrderType.STOP, order_id=42)
    rep.order.stopPrice = 1.2
    out = normalize(rep, SYMS)
    assert isinstance(out, m.MasterPendingReplaced) and out.price == 1.2
    can = base_event(ProtoOAExecutionType.ORDER_CANCELLED, ProtoOAOrderType.LIMIT, order_id=42)
    assert normalize(can, SYMS) == m.MasterPendingCancelled(42)
    exp = base_event(ProtoOAExecutionType.ORDER_EXPIRED, ProtoOAOrderType.STOP, order_id=42)
    assert normalize(exp, SYMS) == m.MasterPendingCancelled(42)


def test_protection_order_is_sltp_amend():
    e = base_event(ProtoOAExecutionType.ORDER_ACCEPTED,
                   ProtoOAOrderType.STOP_LOSS_TAKE_PROFIT)
    e.position.stopLoss = 1.05
    out = normalize(e, SYMS)
    assert out == m.MasterPositionSLTPAmended(11, 1.05, None)


def test_rejection_normalizes_to_rejected():
    e = base_event(ProtoOAExecutionType.ORDER_REJECTED)
    assert isinstance(normalize(e, SYMS), m.MasterRejected)


def test_unknown_symbol_returns_none():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED)
    e.order.tradeData.symbolId = 999
    e.deal.positionId = 11
    e.deal.filledVolume = 1
    assert normalize(e, {}) is None


# Fix Round 1: Critical & Important defects

def test_pending_placed_extracts_sl_tp_from_order_not_position():
    """CRITICAL: pending order SL/TP comes from order field, not position."""
    e = base_event(ProtoOAExecutionType.ORDER_ACCEPTED, ProtoOAOrderType.LIMIT, order_id=42)
    e.order.limitPrice = 1.115
    e.order.stopLoss = 1.10
    e.order.takeProfit = 1.13
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterPendingPlaced)
    assert out.stop_loss == 1.10
    assert out.take_profit == 1.13


def test_pending_placed_handles_absent_sl_tp():
    """Test explicit absence of SL/TP (0.0 is set, not absent)."""
    e = base_event(ProtoOAExecutionType.ORDER_ACCEPTED, ProtoOAOrderType.LIMIT, order_id=42)
    e.order.limitPrice = 1.115
    # Don't set stopLoss/takeProfit on order; they default to 0.0
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterPendingPlaced)
    assert out.stop_loss is None
    assert out.take_profit is None


def test_pending_replaced_extracts_sl_tp_from_order_not_position():
    """CRITICAL: pending order SL/TP comes from order field, not position."""
    e = base_event(ProtoOAExecutionType.ORDER_REPLACED, ProtoOAOrderType.STOP, order_id=42)
    e.order.stopPrice = 1.2
    e.order.stopLoss = 1.15
    e.order.takeProfit = 1.25
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterPendingReplaced)
    assert out.stop_loss == 1.15
    assert out.take_profit == 1.25


def test_rejection_with_error_code_extracts_reason():
    """IMPORTANT: rejection reason from errorCode string, or executionType if empty."""
    e = base_event(ProtoOAExecutionType.ORDER_REJECTED)
    e.errorCode = "INSUFFICIENT_BALANCE"
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterRejected)
    assert out.reason == "INSUFFICIENT_BALANCE"


def test_rejection_without_error_code_uses_execution_type_name():
    """IMPORTANT: empty errorCode falls back to executionType name."""
    e = base_event(ProtoOAExecutionType.ORDER_REJECTED)
    # errorCode is not set (defaults to empty string)
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterRejected)
    assert out.reason == "ORDER_REJECTED"


def test_rejection_with_unmapped_symbol_still_rejected():
    """IMPORTANT: ORDER_REJECTED produced even if symbol unmapped."""
    e = base_event(ProtoOAExecutionType.ORDER_REJECTED)
    e.order.tradeData.symbolId = 999  # unknown symbol
    e.errorCode = "INVALID_SYMBOL"
    out = normalize(e, {})
    assert isinstance(out, m.MasterRejected)
    assert out.reason == "INVALID_SYMBOL"
