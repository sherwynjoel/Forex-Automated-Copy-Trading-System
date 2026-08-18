"""Unit tests for the pure performance-analytics aggregation
(engine/analytics.py). Input is the mapped deal-dict shape queries.deal_history
returns; everything here is arithmetic over that -- no I/O."""

import pytest

from copier.engine.analytics import compute_analytics

WEEK_MS = 7 * 24 * 3600 * 1000


def _open_deal(deal_id, ts, commission=-0.5, symbol="EURUSD"):
    return {
        "deal_id": deal_id, "position_id": deal_id, "symbol": symbol,
        "side": "BUY", "filled_volume": 100_000, "volume_lots": "0.01",
        "execution_price": 1.10, "status": "FILLED", "commission": commission,
        "execution_timestamp": ts, "close": None,
    }


def _close_deal(deal_id, ts, gross, balance, swap=-0.1, commission=-0.5,
                symbol="EURUSD"):
    return {
        "deal_id": deal_id, "position_id": deal_id, "symbol": symbol,
        "side": "SELL", "filled_volume": 100_000, "volume_lots": "0.01",
        "execution_price": 1.12, "status": "FILLED", "commission": commission,
        "execution_timestamp": ts,
        "close": {
            "entry_price": 1.10, "gross_profit": gross, "swap": swap,
            "commission": commission, "balance": balance,
            "closed_volume": 100_000, "closed_volume_lots": "0.01",
        },
    }


def test_empty_deals_yield_zeroed_analytics():
    result = compute_analytics([])
    assert result["closed_trades"] == 0
    assert result["win_rate"] is None
    assert result["profit_factor"] is None
    assert result["best_trade"] is None
    assert result["worst_trade"] is None
    assert result["net_pnl"] == 0.0
    assert result["equity_curve"] == []


def test_win_loss_and_profit_metrics():
    deals = [
        _open_deal(1, 1_000),
        _close_deal(2, 2_000, gross=100.0, balance=10_100),
        _open_deal(3, 3_000),
        _close_deal(4, 4_000, gross=-40.0, balance=10_060),
        _open_deal(5, 5_000),
        _close_deal(6, 6_000, gross=60.0, balance=10_120),
    ]
    result = compute_analytics(deals)

    assert result["closed_trades"] == 3
    assert result["wins"] == 2
    assert result["losses"] == 1
    assert result["win_rate"] == pytest.approx(2 / 3)
    # profit factor = gross wins / |gross losses| = 160 / 40
    assert result["profit_factor"] == pytest.approx(4.0)
    assert result["best_trade"] == 100.0
    assert result["worst_trade"] == -40.0
    # net = sum(gross) + sum(swap over closes) + sum(commission over ALL deals)
    # gross = 120, swaps = -0.3, commissions = 6 deals * -0.5 = -3.0
    assert result["net_pnl"] == pytest.approx(120 - 0.3 - 3.0)
    assert result["avg_win"] == pytest.approx(80.0)
    assert result["avg_loss"] == pytest.approx(-40.0)


def test_equity_curve_and_max_drawdown():
    deals = [
        _close_deal(1, 1_000, gross=100.0, balance=10_100),
        _close_deal(2, 2_000, gross=-300.0, balance=9_800),
        _close_deal(3, 3_000, gross=-100.0, balance=9_700),
        _close_deal(4, 4_000, gross=500.0, balance=10_200),
    ]
    result = compute_analytics(deals)

    assert result["equity_curve"] == [
        {"timestamp": 1_000, "balance": 10_100},
        {"timestamp": 2_000, "balance": 9_800},
        {"timestamp": 3_000, "balance": 9_700},
        {"timestamp": 4_000, "balance": 10_200},
    ]
    # Peak 10_100 -> trough 9_700: drawdown 400 absolute
    assert result["max_drawdown"] == pytest.approx(400.0)
    assert result["max_drawdown_pct"] == pytest.approx(400 / 10_100)


def test_per_symbol_and_weekly_buckets():
    deals = [
        _close_deal(1, 1_000, gross=100.0, balance=10_100, symbol="EURUSD"),
        _close_deal(2, 2_000, gross=-40.0, balance=10_060, symbol="GBPUSD"),
        _close_deal(3, WEEK_MS + 5_000, gross=60.0, balance=10_120, symbol="EURUSD"),
    ]
    result = compute_analytics(deals)

    by_symbol = {row["symbol"]: row for row in result["per_symbol"]}
    assert by_symbol["EURUSD"]["trades"] == 2
    assert by_symbol["EURUSD"]["gross_pnl"] == pytest.approx(160.0)
    assert by_symbol["GBPUSD"]["gross_pnl"] == pytest.approx(-40.0)

    assert len(result["weekly"]) == 2
    assert result["weekly"][0]["trades"] == 2
    assert result["weekly"][0]["gross_pnl"] == pytest.approx(60.0)
    assert result["weekly"][1]["trades"] == 1
    assert result["weekly"][1]["gross_pnl"] == pytest.approx(60.0)
