"""Builders for MT5 reports, shared by the unit tests of the MT5 lane.

Every argument has the value a plain 1.00-lot EURUSD.r position/deal would
carry, so a test names only what it is about.
"""

from copier.mt5.protocol import Ack, ReportDeal, ReportOrder, ReportPosition, SyncReport

TS_MS = 1_757_203_200_123


def position(ticket, symbol="EURUSD.r", side="BUY", volume=100, open_price=1.1, sl=None, tp=None,
             price=1.101, pnl=1.0, swap=0.0, comment="", magic=20260907,
             opened_at_ms=1_757_203_100_000) -> ReportPosition:
    return ReportPosition(
        ticket=ticket, symbol=symbol, side=side, volume=volume, open_price=open_price,
        stop_loss=sl, take_profit=tp, current_price=price, pnl=pnl, swap=swap,
        comment=comment, magic=magic, opened_at_ms=opened_at_ms)


def order(ticket, symbol="EURUSD.r", order_type="BUY_LIMIT", volume=100, price=1.09, sl=None,
          tp=None, comment="", magic=20260907) -> ReportOrder:
    return ReportOrder(
        ticket=ticket, symbol=symbol, order_type=order_type, volume=volume, price=price,
        stop_loss=sl, take_profit=tp, comment=comment, magic=magic)


def deal(ticket, position=0, order=0, symbol="EURUSD.r", deal_type="BUY", entry="IN", volume=100,
         price=1.1, profit=0.0, swap=0.0, commission=0.0, time_ms=TS_MS, comment="",
         magic=20260907) -> ReportDeal:
    return ReportDeal(
        ticket=ticket, position=position, order=order, symbol=symbol, deal_type=deal_type,
        entry=entry, volume=volume, price=price, profit=profit, swap=swap,
        commission=commission, time_ms=time_ms, comment=comment, magic=magic)


def ack(command_id, ok=True, retcode=10009, message="done", position=None, deal=None,
        order=None, price=None, volume=None) -> Ack:
    return Ack(command_id=command_id, ok=ok, retcode=retcode, message=message,
               position=position, deal=deal, order=order, price=price, volume=volume)


def report(positions=(), orders=(), deals=(), acks=(), seq=1, ts_ms=TS_MS, balance=10000.0,
           equity=None, margin=0.0, margin_free=None) -> SyncReport:
    return SyncReport(
        seq=seq, ts_ms=ts_ms, balance=balance,
        equity=balance if equity is None else equity, margin=margin,
        margin_free=balance if margin_free is None else margin_free,
        positions=list(positions), orders=list(orders), deals=list(deals), acks=list(acks))
