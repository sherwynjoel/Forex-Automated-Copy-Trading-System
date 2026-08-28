"""Protobuf execution events -> `executions` rows.

Pure functions: no I/O, no broker, no clock, so they are trivially testable
and the service layer just submits what they return.

This is where the master trade's own economics finally get recorded.
_handle_master_event used to log {execution_type, normalized} and nothing
else, so entry price, volume, side, symbol and the broker's execution
timestamp never reached Postgres -- which is why realized copy slippage was
not computable from production at all.

Money is scaled by moneyDigits exactly as queries._map_deal does, so the
two paths report the same numbers for the same deal.
"""

from typing import Mapping

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderStatus, ProtoOAOrderType, ProtoOATradeSide)
from google.protobuf.json_format import MessageToDict
from psycopg.types.json import Jsonb

from copier.domain.models import SymbolInfo


def _money(raw: int | None, digits: int) -> float | None:
    """Broker money is an integer scaled by moneyDigits."""
    if raw is None:
        return None
    return raw / (10 ** digits)


def _opt(msg, field: str):
    """Optional protobuf scalar, or None. Protobuf reports an unset price as
    0, which is not a price -- HasField is the only honest test."""
    return getattr(msg, field) if msg.HasField(field) else None


def _enum_name(enum, value):
    try:
        return enum.Name(value)
    except ValueError:
        return None


def execution_row(
    evt,
    *,
    account_id: int,
    org_id: int | None,
    is_master: bool,
    symbols_by_id: Mapping[int, SymbolInfo],
    quote: tuple[float, float] | None,
) -> dict:
    """Decode one ProtoOAExecutionEvent into an `executions` row.

    Args:
        evt: The raw ProtoOAExecutionEvent.
        account_id: Account the event arrived on.
        org_id: Org that owns the account, or None if unresolved.
        is_master: Whether this account is its org's master.
        symbols_by_id: That account's symbol map; an unknown symbol yields a
            null name rather than dropping the row.
        quote: (bid, ask) live at execution time, or None if unsubscribed.

    Returns:
        A dict whose keys are `executions` column names.
    """
    symbol_id = evt.order.tradeData.symbolId or None
    symbol_info = symbols_by_id.get(symbol_id) if symbol_id else None

    close = evt.deal.closePositionDetail if evt.deal.HasField(
        'closePositionDetail') else None
    # Deal-level scale, then the close block's own scale falling back to it --
    # the same two-level rule queries._map_deal applies, so the stored row and
    # the broker-fetched deal report identical numbers.
    deal_digits = (evt.deal.moneyDigits
                   if evt.deal.HasField('moneyDigits') else 2)
    close_digits = (close.moneyDigits
                    if close is not None and close.HasField('moneyDigits')
                    else deal_digits)

    bid, ask = (quote if quote is not None else (None, None))

    return {
        'org_id': org_id,
        'account_id': account_id,
        'is_master': is_master,
        'execution_type': ProtoOAExecutionType.Name(evt.executionType),
        'order_id': evt.order.orderId or None,
        'position_id': evt.deal.positionId or evt.position.positionId or None,
        'deal_id': evt.deal.dealId or None,
        'client_order_id': evt.order.clientOrderId or None,
        'symbol_id': symbol_id,
        'symbol': symbol_info.name if symbol_info else None,
        'side': _enum_name(ProtoOATradeSide, evt.order.tradeData.tradeSide),
        'order_type': _enum_name(ProtoOAOrderType, evt.order.orderType),
        'order_status': _enum_name(ProtoOAOrderStatus, evt.order.orderStatus),
        'volume': evt.order.tradeData.volume or None,
        'filled_volume': evt.deal.filledVolume or None,
        'closed_volume': close.closedVolume if close is not None else None,
        'execution_price': _opt(evt.deal, 'executionPrice'),
        'limit_price': _opt(evt.order, 'limitPrice'),
        'stop_price': _opt(evt.order, 'stopPrice'),
        'stop_loss': _opt(evt.position, 'stopLoss'),
        'take_profit': _opt(evt.position, 'takeProfit'),
        'gross_profit': (_money(close.grossProfit, close_digits)
                         if close is not None else None),
        'swap': _money(close.swap, close_digits) if close is not None else None,
        'commission': (_money(evt.deal.commission, deal_digits)
                       if evt.deal.HasField('commission') else None),
        'balance_after': (_money(close.balance, close_digits)
                          if close is not None else None),
        'execution_timestamp': evt.deal.executionTimestamp or None,
        'bid_at_exec': bid,
        'ask_at_exec': ask,
        'error_code': evt.errorCode or None,
        # The decoded protobuf, not str(evt). Spec 3 requires "nothing lost
        # to an unanticipated field"; protobuf TEXT format inside a JSON
        # string is neither queryable by field nor indexable, and is larger
        # than the structured form it replaces.
        'raw': Jsonb(MessageToDict(evt, preserving_proto_field_name=True)),
    }
