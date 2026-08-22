"""
Normalizer: converts raw cTrader ProtoOAExecutionEvent protobuf messages
into typed MasterEvent domain models.
"""
from typing import Mapping

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType, ProtoOATradeSide)

from copier.domain import models as m


def normalize(evt: ProtoOAExecutionEvent,
              symbols_by_id: Mapping[int, m.SymbolInfo]) -> m.MasterEvent | None:
    """
    Normalize a ProtoOAExecutionEvent to a MasterEvent.

    Returns None for non-replication-relevant events (e.g., MARKET ORDER_ACCEPTED,
    unknown symbols).
    """

    execution_type = evt.executionType

    # Handle ORDER_REJECTED BEFORE symbol gate (rejections don't require mapped symbol)
    if execution_type == ProtoOAExecutionType.ORDER_REJECTED:
        reason = evt.errorCode if evt.errorCode else ProtoOAExecutionType.Name(execution_type)
        return m.MasterRejected(reason=reason)

    # Get symbol info; return None if symbol is unknown
    symbol_id = evt.order.tradeData.symbolId
    symbol_info = symbols_by_id.get(symbol_id)
    if symbol_info is None:
        return None

    order_type = evt.order.orderType

    # Handle protection orders (STOP_LOSS_TAKE_PROFIT)
    if order_type == ProtoOAOrderType.STOP_LOSS_TAKE_PROFIT:
        if execution_type in (ProtoOAExecutionType.ORDER_ACCEPTED,
                             ProtoOAExecutionType.ORDER_REPLACED,
                             ProtoOAExecutionType.ORDER_CANCELLED):
            stop_loss = evt.position.stopLoss if evt.position.HasField('stopLoss') else None
            take_profit = evt.position.takeProfit if evt.position.HasField('takeProfit') else None
            return m.MasterPositionSLTPAmended(
                position_id=evt.position.positionId,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        # STOP_LOSS_TAKE_PROFIT ORDER_FILLED or other execution types -> ignore
        return None

    # Handle ORDER_FILLED and ORDER_PARTIAL_FILL
    if execution_type in (ProtoOAExecutionType.ORDER_FILLED,
                         ProtoOAExecutionType.ORDER_PARTIAL_FILL):

        # Check if this is a close (has closePositionDetail)
        if evt.deal.HasField('closePositionDetail'):
            return m.MasterPositionClosed(
                position_id=evt.deal.positionId,
                symbol_name=symbol_info.name,
                closed_volume=evt.deal.closePositionDetail.closedVolume,
                remaining_volume=evt.position.tradeData.volume
            )

        # For LIMIT and STOP orders, it's a pending fill
        if order_type in (ProtoOAOrderType.LIMIT, ProtoOAOrderType.STOP):
            return m.MasterPendingFilled(
                order_id=evt.order.orderId,
                position_id=evt.deal.positionId
            )

        # For MARKET orders, it's a position open. MARKET_RANGE is cTrader's
        # market-with-slippage-protection variant -- the platform UI places
        # those instead of plain MARKET in several default configurations --
        # and its fills carry the same shape.
        if order_type in (ProtoOAOrderType.MARKET, ProtoOAOrderType.MARKET_RANGE):
            side = _proto_side_to_side(evt.order.tradeData.tradeSide)
            stop_loss = evt.position.stopLoss if evt.position.HasField('stopLoss') else None
            take_profit = evt.position.takeProfit if evt.position.HasField('takeProfit') else None

            return m.MasterPositionOpened(
                position_id=evt.deal.positionId,
                symbol_name=symbol_info.name,
                side=side,
                volume=evt.deal.filledVolume,
                lot_size=symbol_info.lot_size,
                stop_loss=stop_loss,
                take_profit=take_profit,
                # What the copy's SL/TP distance is measured from. The
                # POSITION's price, not this deal's: on a partial fill the
                # master's levels were set against the position's entry, and
                # measuring from one deal would shift the copy's stop by the
                # difference. Falls back to the deal when absent.
                entry_price=(
                    evt.position.price if evt.position.HasField('price')
                    else (evt.deal.executionPrice
                          if evt.deal.HasField('executionPrice') else None)),
            )

        # Unknown order type with fill -> ignore
        return None

    # Handle ORDER_ACCEPTED for LIMIT/STOP pending orders
    if execution_type == ProtoOAExecutionType.ORDER_ACCEPTED:
        if order_type in (ProtoOAOrderType.LIMIT, ProtoOAOrderType.STOP):
            side = _proto_side_to_side(evt.order.tradeData.tradeSide)
            pending_type = m.PendingType.LIMIT if order_type == ProtoOAOrderType.LIMIT else m.PendingType.STOP

            # Get price from limitPrice or stopPrice
            if order_type == ProtoOAOrderType.LIMIT:
                price = evt.order.limitPrice
            else:
                price = evt.order.stopPrice

            stop_loss = _extract_float_if_nonzero(evt.order.stopLoss)
            take_profit = _extract_float_if_nonzero(evt.order.takeProfit)
            expiry_ts_ms = evt.order.expirationTimestamp if evt.order.HasField('expirationTimestamp') else None

            return m.MasterPendingPlaced(
                order_id=evt.order.orderId,
                symbol_name=symbol_info.name,
                side=side,
                order_type=pending_type,
                volume=evt.order.tradeData.volume,
                lot_size=symbol_info.lot_size,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                expiry_ts_ms=expiry_ts_ms
            )

        # MARKET ORDER_ACCEPTED -> not replication-relevant
        return None

    # Handle ORDER_REPLACED for pending orders
    if execution_type == ProtoOAExecutionType.ORDER_REPLACED:
        if order_type in (ProtoOAOrderType.LIMIT, ProtoOAOrderType.STOP):
            pending_type = m.PendingType.LIMIT if order_type == ProtoOAOrderType.LIMIT else m.PendingType.STOP

            # Get price from limitPrice or stopPrice
            if order_type == ProtoOAOrderType.LIMIT:
                price = evt.order.limitPrice
            else:
                price = evt.order.stopPrice

            stop_loss = _extract_float_if_nonzero(evt.order.stopLoss)
            take_profit = _extract_float_if_nonzero(evt.order.takeProfit)

            return m.MasterPendingReplaced(
                order_id=evt.order.orderId,
                symbol_name=symbol_info.name,
                lot_size=symbol_info.lot_size,
                order_type=pending_type,
                volume=evt.order.tradeData.volume,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )

        # ORDER_REPLACED for other types -> ignore
        return None

    # Handle ORDER_CANCELLED and ORDER_EXPIRED
    if execution_type in (ProtoOAExecutionType.ORDER_CANCELLED,
                         ProtoOAExecutionType.ORDER_EXPIRED):
        if order_type in (ProtoOAOrderType.LIMIT, ProtoOAOrderType.STOP):
            return m.MasterPendingCancelled(order_id=evt.order.orderId)

        # CANCELLED/EXPIRED for other types -> ignore
        return None

    # Unknown execution type -> not replication-relevant
    return None


def _proto_side_to_side(proto_side: int) -> m.Side:
    """Convert ProtoOATradeSide to Side enum."""
    if proto_side == ProtoOATradeSide.BUY:
        return m.Side.BUY
    elif proto_side == ProtoOATradeSide.SELL:
        return m.Side.SELL
    else:
        raise ValueError(f"Unknown trade side: {proto_side}")


def _extract_float_if_nonzero(value: float) -> float | None:
    """Return float if nonzero (protobuf default is 0.0); else None."""
    return value if value != 0.0 else None
