"""copier/src/copier/mt5/deals.py: MT5 deals -> the deals table's row shape,
with the spec's backward balance_after estimate."""

from copier.domain.models import SymbolInfo
from copier.mt5.deals import balance_after_estimates, deal_rows
from copier.testing.mt5_fixtures import deal

EURUSD = SymbolInfo(symbol_id=77, name="EURUSD.r", digits=5, lot_size=100, min_volume=1, step_volume=1)
SYMBOLS = {"EURUSD.r": EURUSD}


def test_balance_after_walks_backwards_from_the_current_balance():
    deals = [
        deal(1, position=7001, entry="IN", volume=100, price=1.1, commission=-0.03, time_ms=1000),
        deal(2, position=7001, deal_type="SELL", entry="OUT", volume=100, price=1.105, profit=5.0,
             swap=-0.1, commission=-0.03, time_ms=2000),
        deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0, time_ms=3000),
    ]
    assert balance_after_estimates(deals, current_balance=10500.0) == {
        3: 10500.0, 2: 10000.0, 1: 9995.13}


def test_estimates_follow_time_order_regardless_of_input_order():
    deals = [deal(2, entry="OUT", deal_type="SELL", profit=5.0, time_ms=2000),
             deal(1, entry="IN", commission=-0.03, time_ms=1000)]
    assert balance_after_estimates(deals, 100.0) == {2: 100.0, 1: 95.0}


def test_deal_rows_have_map_deals_shape():
    deals = [
        deal(1, position=7001, order=10, entry="IN", volume=100, price=1.1, commission=-0.03,
             time_ms=1000),
        deal(2, position=7001, order=11, deal_type="SELL", entry="OUT", volume=40, price=1.105,
             profit=2.0, commission=-0.01, time_ms=2000),
    ]
    rows = deal_rows(deals, 10000.0, SYMBOLS,
                     entry_price_for=lambda position_id: 1.1 if position_id == 7001 else None)
    assert rows[0] == {
        "deal_id": 1, "order_id": 10, "position_id": 7001, "symbol_id": 77, "symbol": "EURUSD.r",
        "side": "BUY", "volume": 100, "filled_volume": 100, "volume_lots": "1.00",
        "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
        "create_timestamp": 1000, "execution_timestamp": 1000, "close": None,
        "balance_after": 9998.01, "gross_profit": None}
    assert rows[1]["close"] == {
        "entry_price": 1.1, "gross_profit": 2.0, "swap": 0.0, "commission": -0.01,
        "balance": 10000.0, "closed_volume": 40, "closed_volume_lots": "0.40"}
    assert rows[1]["side"] == "SELL" and rows[1]["balance_after"] == 10000.0


def test_balance_operations_carry_their_amount_and_no_symbol():
    (row,) = deal_rows(
        [deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0, symbol="", time_ms=3000)],
        10500.0, SYMBOLS, entry_price_for=lambda _p: None)
    assert (row["side"], row["symbol"], row["symbol_id"], row["execution_price"],
            row["commission"], row["close"]) == ("BALANCE", None, None, None, None, None)
    assert (row["gross_profit"], row["balance_after"], row["volume"], row["position_id"]) == (
        500.0, 10500.0, 0, None)


def test_an_unknown_symbol_has_no_id_but_keeps_its_name():
    (row,) = deal_rows([deal(1, position=5, symbol="GBPJPY.r", entry="IN")], 100.0, SYMBOLS,
                       entry_price_for=lambda _p: None)
    assert (row["symbol"], row["symbol_id"]) == ("GBPJPY.r", None)


def test_other_closing_entries_are_closes():
    rows = deal_rows(
        [deal(1, position=5, deal_type="SELL", entry="OUT_BY", volume=10, profit=1.0),
         deal(2, position=6, deal_type="BUY", entry="INOUT", volume=10, profit=-1.0, time_ms=2000)],
        100.0, SYMBOLS, entry_price_for=lambda _p: 1.0)
    assert all(r["close"] is not None for r in rows)
