"""On-demand broker read models: full account details and trade history.

Every function here is a pull-style query -- a tagged request/response round
trip over ``client.send()`` -- never a trade send: nothing in this module
goes through send_no_reply or the Dispatcher, so nothing here can ever place,
amend, or close anything.

Callers hand in the account's already-authed CTraderClient and that
account's ``symbols_by_id`` map (from its symbol cache); the functions
return JSON-ready dicts: money scaled by moneyDigits, volumes also expressed
in lots, enum ints replaced by their names, and symbol ids resolved to
names.  A broker error response raises QueryFailed rather than being
returned as data.
"""

import logging

from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoErrorRes
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAssetListReq, ProtoOADealListReq, ProtoOAErrorRes,
    ProtoOAOrderListReq, ProtoOAReconcileReq, ProtoOATraderReq)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAAccessRights, ProtoOAAccountType, ProtoOADealStatus,
    ProtoOAOrderStatus, ProtoOAOrderType, ProtoOATradeSide)
from twisted.internet import defer

log = logging.getLogger(__name__)

# cTrader rejects DealList windows longer than a week; the dashboard pages
# by week, and maxRows keeps a very busy week from producing an unbounded
# payload.
DEAL_LIST_MAX_ROWS = 500


class QueryFailed(Exception):
    """The broker answered a read request with an error response."""


def extract_or_raise(response, what: str):
    payload = Protobuf.extract(response)
    if isinstance(payload, (ProtoOAErrorRes, ProtoErrorRes)):
        raise QueryFailed(f"{what} failed: {payload.errorCode}")
    return payload


def _lots(volume, lot_size) -> str | None:
    if volume is None or not lot_size:
        return None
    return f"{volume / lot_size:.2f}"


def _money(value, money_digits) -> float:
    return value / (10 ** money_digits)


@defer.inlineCallbacks
def account_details(client, account_id: int, symbols_by_id: dict):
    """Everything the broker exposes about one account, in one dict.

    Three round trips: ProtoOATraderReq (balance, leverage, broker, account
    type, registration...), ProtoOAAssetListReq (to resolve depositAssetId
    to a currency name), and ProtoOAReconcileReq (the account's open
    positions and working orders, fresh from the broker rather than from
    this process's mappings).

    Note the deliberate omission: the account holder's name/email are NOT
    available -- Spotware's Open API exposes no personal profile data
    beyond a numeric cTID user id.
    """
    trader_req = ProtoOATraderReq()
    trader_req.ctidTraderAccountId = account_id
    trader = extract_or_raise(
        (yield client.send(trader_req)), "trader details").trader
    money_digits = trader.moneyDigits if trader.HasField("moneyDigits") else 2

    asset_req = ProtoOAAssetListReq()
    asset_req.ctidTraderAccountId = account_id
    assets = extract_or_raise(
        (yield client.send(asset_req)), "asset list").asset
    deposit_currency = next(
        (a.name for a in assets if a.assetId == trader.depositAssetId), None)

    rec_req = ProtoOAReconcileReq()
    rec_req.ctidTraderAccountId = account_id
    rec = extract_or_raise((yield client.send(rec_req)), "open positions")

    open_positions = []
    for p in rec.position:
        sym = symbols_by_id.get(p.tradeData.symbolId)
        pos_digits = p.moneyDigits if p.HasField("moneyDigits") else money_digits
        open_positions.append({
            "position_id": p.positionId,
            "symbol_id": p.tradeData.symbolId,
            "symbol": sym.name if sym else None,
            "side": ProtoOATradeSide.Name(p.tradeData.tradeSide),
            "volume": p.tradeData.volume,
            "volume_lots": _lots(p.tradeData.volume, sym.lot_size if sym else None),
            "price": p.price,
            "label": p.tradeData.label,
            "stop_loss": p.stopLoss if p.HasField("stopLoss") else None,
            "take_profit": p.takeProfit if p.HasField("takeProfit") else None,
            "swap": _money(p.swap, pos_digits),
            "open_timestamp": p.tradeData.openTimestamp or None,
        })

    pending_orders = []
    for o in rec.order:
        sym = symbols_by_id.get(o.tradeData.symbolId)
        pending_orders.append({
            "order_id": o.orderId,
            "symbol_id": o.tradeData.symbolId,
            "symbol": sym.name if sym else None,
            "side": ProtoOATradeSide.Name(o.tradeData.tradeSide),
            "volume": o.tradeData.volume,
            "volume_lots": _lots(o.tradeData.volume, sym.lot_size if sym else None),
            "order_type": ProtoOAOrderType.Name(o.orderType),
            "limit_price": o.limitPrice if o.HasField("limitPrice") else None,
            "stop_price": o.stopPrice if o.HasField("stopPrice") else None,
            "label": o.tradeData.label,
        })

    return {
        "account_id": account_id,
        "trader_login": trader.traderLogin if trader.HasField("traderLogin") else None,
        "balance": _money(trader.balance, money_digits),
        "money_digits": money_digits,
        "deposit_currency": deposit_currency,
        "leverage": (trader.leverageInCents / 100
                     if trader.HasField("leverageInCents") else None),
        "max_leverage": (trader.maxLeverage / 100
                         if trader.HasField("maxLeverage") else None),
        "broker_name": trader.brokerName if trader.HasField("brokerName") else None,
        "registration_timestamp": (trader.registrationTimestamp
                                   if trader.HasField("registrationTimestamp") else None),
        "account_type": ProtoOAAccountType.Name(trader.accountType),
        "access_rights": ProtoOAAccessRights.Name(trader.accessRights),
        "swap_free": trader.swapFree if trader.HasField("swapFree") else None,
        "is_limited_risk": (trader.isLimitedRisk
                            if trader.HasField("isLimitedRisk") else False),
        "open_positions": open_positions,
        "pending_orders": pending_orders,
    }


@defer.inlineCallbacks
def deal_history(client, account_id: int, symbols_by_id: dict,
                 from_ms: int, to_ms: int, max_rows: int = DEAL_LIST_MAX_ROWS):
    """Deals (fills) in [from_ms, to_ms].  A deal carrying a
    closePositionDetail is a position (partial) close and gets a ``close``
    sub-dict with the realized P&L breakdown; opens have ``close: None``."""
    req = ProtoOADealListReq()
    req.ctidTraderAccountId = account_id
    req.fromTimestamp = from_ms
    req.toTimestamp = to_ms
    req.maxRows = max_rows
    res = extract_or_raise((yield client.send(req)), "deal history")

    deals = []
    for d in res.deal:
        sym = symbols_by_id.get(d.symbolId)
        money_digits = d.moneyDigits if d.HasField("moneyDigits") else 2
        close = None
        if d.HasField("closePositionDetail"):
            cpd = d.closePositionDetail
            cpd_digits = cpd.moneyDigits if cpd.HasField("moneyDigits") else money_digits
            close = {
                "entry_price": cpd.entryPrice,
                "gross_profit": _money(cpd.grossProfit, cpd_digits),
                "swap": _money(cpd.swap, cpd_digits),
                "commission": _money(cpd.commission, cpd_digits),
                "balance": _money(cpd.balance, cpd_digits),
                "closed_volume": cpd.closedVolume,
                "closed_volume_lots": _lots(
                    cpd.closedVolume, sym.lot_size if sym else None),
            }
        deals.append({
            "deal_id": d.dealId,
            "order_id": d.orderId,
            "position_id": d.positionId,
            "symbol_id": d.symbolId,
            "symbol": sym.name if sym else None,
            "side": ProtoOATradeSide.Name(d.tradeSide),
            "volume": d.volume,
            "filled_volume": d.filledVolume,
            "volume_lots": _lots(d.filledVolume, sym.lot_size if sym else None),
            "execution_price": (d.executionPrice
                                if d.HasField("executionPrice") else None),
            "status": ProtoOADealStatus.Name(d.dealStatus),
            "commission": (_money(d.commission, money_digits)
                           if d.HasField("commission") else None),
            "create_timestamp": d.createTimestamp,
            "execution_timestamp": d.executionTimestamp,
            "close": close,
        })
    return {"deals": deals, "has_more": res.hasMore}


@defer.inlineCallbacks
def order_history(client, account_id: int, symbols_by_id: dict,
                  from_ms: int, to_ms: int):
    """Historical orders in [from_ms, to_ms], newest state per order."""
    req = ProtoOAOrderListReq()
    req.ctidTraderAccountId = account_id
    req.fromTimestamp = from_ms
    req.toTimestamp = to_ms
    res = extract_or_raise((yield client.send(req)), "order history")

    orders = []
    for o in res.order:
        sym = symbols_by_id.get(o.tradeData.symbolId)
        orders.append({
            "order_id": o.orderId,
            "symbol_id": o.tradeData.symbolId,
            "symbol": sym.name if sym else None,
            "side": ProtoOATradeSide.Name(o.tradeData.tradeSide),
            "volume": o.tradeData.volume,
            "volume_lots": _lots(o.tradeData.volume, sym.lot_size if sym else None),
            "order_type": ProtoOAOrderType.Name(o.orderType),
            "status": ProtoOAOrderStatus.Name(o.orderStatus).removeprefix(
                "ORDER_STATUS_"),
            "limit_price": o.limitPrice if o.HasField("limitPrice") else None,
            "stop_price": o.stopPrice if o.HasField("stopPrice") else None,
            "execution_price": (o.executionPrice
                                if o.HasField("executionPrice") else None),
            "executed_volume": (o.executedVolume
                                if o.HasField("executedVolume") else None),
            "position_id": o.positionId if o.HasField("positionId") else None,
            "label": o.tradeData.label,
            "open_timestamp": (o.tradeData.openTimestamp
                               if o.tradeData.HasField("openTimestamp") else None),
            "update_timestamp": (o.utcLastUpdateTimestamp
                                 if o.HasField("utcLastUpdateTimestamp") else None),
            "stop_loss": o.stopLoss if o.HasField("stopLoss") else None,
            "take_profit": o.takeProfit if o.HasField("takeProfit") else None,
        })
    return {"orders": orders, "has_more": res.hasMore}
