"""Pure performance-analytics aggregation over mapped deal dicts.

Input is the shape queries.deal_history produces; a closing deal (one with a
``close`` sub-dict) is one completed trade.  Everything here is arithmetic --
no I/O, no broker, no clock -- so it is trivially testable and the copier's
/analytics endpoint just feeds it deals fetched week by week.

Money semantics: ``net_pnl`` counts every closing deal's gross profit and
swap plus EVERY deal's commission (open and close legs are charged
separately by the broker; a close's own commission is already the deal-level
commission on that closing deal, so summing deal-level commissions across
all deals counts each leg exactly once).
"""

WEEK_MS = 7 * 24 * 3600 * 1000
# 1970-01-01 was a Thursday; shift so weekly buckets start on Monday UTC.
_MONDAY_SHIFT_MS = 3 * 24 * 3600 * 1000


def compute_analytics(deals: list[dict]) -> dict:
    """Aggregate mapped deals into the performance-dashboard read model."""
    closes = [d for d in deals if d.get("close")]
    closes.sort(key=lambda d: d["execution_timestamp"])

    wins = [d for d in closes if d["close"]["gross_profit"] > 0]
    losses = [d for d in closes if d["close"]["gross_profit"] < 0]
    gross_wins = sum(d["close"]["gross_profit"] for d in wins)
    gross_losses = sum(d["close"]["gross_profit"] for d in losses)

    net_pnl = (
        sum(d["close"]["gross_profit"] + d["close"]["swap"] for d in closes)
        + sum(d.get("commission") or 0.0 for d in deals)
    )

    equity_curve = [
        {"timestamp": d["execution_timestamp"], "balance": d["close"]["balance"]}
        for d in closes
    ]

    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    peak = None
    for point in equity_curve:
        balance = point["balance"]
        if peak is None or balance > peak:
            peak = balance
        elif peak:
            drawdown = peak - balance
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = drawdown / peak

    per_symbol: dict[str, dict] = {}
    for d in closes:
        row = per_symbol.setdefault(
            d.get("symbol") or f"#{d.get('symbol_id')}",
            {"trades": 0, "gross_pnl": 0.0})
        row["trades"] += 1
        row["gross_pnl"] += d["close"]["gross_profit"]
    per_symbol_rows = [
        {"symbol": symbol, **row}
        for symbol, row in sorted(per_symbol.items(),
                                  key=lambda kv: kv[1]["gross_pnl"], reverse=True)
    ]

    weekly: dict[int, dict] = {}
    for d in closes:
        bucket = (d["execution_timestamp"] + _MONDAY_SHIFT_MS) // WEEK_MS
        row = weekly.setdefault(bucket, {"trades": 0, "gross_pnl": 0.0})
        row["trades"] += 1
        row["gross_pnl"] += d["close"]["gross_profit"]
    weekly_rows = [
        {"week_start": bucket * WEEK_MS - _MONDAY_SHIFT_MS, **row}
        for bucket, row in sorted(weekly.items())
    ]

    return {
        "closed_trades": len(closes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closes)) if closes else None,
        "profit_factor": (gross_wins / abs(gross_losses)) if gross_losses else None,
        "best_trade": max((d["close"]["gross_profit"] for d in closes), default=None),
        "worst_trade": min((d["close"]["gross_profit"] for d in closes), default=None),
        "avg_win": (gross_wins / len(wins)) if wins else None,
        "avg_loss": (gross_losses / len(losses)) if losses else None,
        "net_pnl": net_pnl,
        "gross_wins": gross_wins,
        "gross_losses": gross_losses,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "equity_curve": equity_curve,
        "per_symbol": per_symbol_rows,
        "weekly": weekly_rows,
    }
