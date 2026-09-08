"""MT5 deals -> the `deals` table's row shape (queries._map_deal), plus the
balance_after estimate the spec asks for.

MT5 stores no per-deal balance. Walking the batch backwards from the
terminal's CURRENT balance -- subtracting each deal's profit + swap +
commission in reverse time order -- gives each deal the balance that stood
right after it, as long as the batch holds every deal since the previous
one. The api marks the field estimated; the History page shows it unchanged.
"""

from typing import Callable, Iterable, Mapping

from copier.domain.models import SymbolInfo
from copier.engine.queries import _lots
from copier.mt5.protocol import CENTILOTS, ReportDeal

CLOSE_ENTRIES = ("OUT", "OUT_BY", "INOUT")
TRADE_TYPES = ("BUY", "SELL")


def sort_deals(deals: Iterable[ReportDeal]) -> list[ReportDeal]:
    return sorted(deals, key=lambda d: (d.time_ms, d.ticket))


def balance_after_estimates(deals: Iterable[ReportDeal], current_balance: float) -> dict[int, float]:
    """ticket -> the balance right after that deal, newest first from the
    current balance."""
    running = float(current_balance)
    out: dict[int, float] = {}
    for d in reversed(sort_deals(deals)):
        out[d.ticket] = round(running, 2)
        running -= d.profit + d.swap + d.commission
    return out


def deal_rows(
    deals: Iterable[ReportDeal],
    current_balance: float,
    symbols: Mapping[str, SymbolInfo],
    entry_price_for: Callable[[int], float | None],
) -> list[dict]:
    """Rows in queries._map_deal's shape. A closing deal gets the `close`
    sub-dict compute_analytics reads (entry price from `entry_price_for`,
    the position's open price as last reported); a BALANCE/CREDIT operation
    carries its amount as top-level `gross_profit` with no `close`, so
    analytics never counts a deposit as a trade."""
    estimates = balance_after_estimates(deals, current_balance)
    rows: list[dict] = []
    for d in sort_deals(deals):
        sym = symbols.get(d.symbol)
        is_trade = d.deal_type in TRADE_TYPES
        close = None
        if is_trade and d.entry in CLOSE_ENTRIES:
            close = {
                "entry_price": entry_price_for(d.position),
                "gross_profit": d.profit,
                "swap": d.swap,
                "commission": d.commission,
                "balance": estimates[d.ticket],
                "closed_volume": d.volume,
                "closed_volume_lots": _lots(d.volume, CENTILOTS),
            }
        rows.append({
            "deal_id": d.ticket,
            "order_id": d.order or None,
            "position_id": d.position or None,
            "symbol_id": sym.symbol_id if sym is not None else None,
            "symbol": d.symbol or None,
            "side": d.deal_type,
            "volume": d.volume,
            "filled_volume": d.volume,
            "volume_lots": _lots(d.volume, CENTILOTS),
            "execution_price": d.price if is_trade else None,
            "status": "FILLED",
            "commission": d.commission if is_trade else None,
            "create_timestamp": d.time_ms,
            "execution_timestamp": d.time_ms,
            "label": d.comment or None,
            "close": close,
            "balance_after": estimates[d.ticket],
            "gross_profit": None if is_trade else d.profit,
        })
    return rows
