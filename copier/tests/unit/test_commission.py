"""Deriving the broker's real commission from the account's own deals."""

import pytest

from copier.engine.commission import round_trip_rates, fee_for


def deal(position_id, *, symbol_id=41, volume=100, commission=None,
         closing=False):
    """A deal shaped like queries.py:_map_deal builds them."""
    return {
        "position_id": position_id,
        "symbol_id": symbol_id,
        "filled_volume": volume,
        "commission": commission,
        "close": {"gross_profit": 1.54} if closing else None,
    }


def test_learns_the_rate_from_a_real_closed_position():
    """The trade that started this: XAUUSD 0.01 lots, $0.28 round trip.

    Protocol volume 100 is 0.01 lots, which is one ounce of gold, so a
    round trip costing $0.28 is $0.28 per unit. Anchored to a real trade
    (position 313386385, 27 Aug 2026) rather than an invented one, because
    the whole point of this module is to stop inventing the number.
    """
    rates = round_trip_rates([
        deal(313386385, commission=-0.14),
        deal(313386385, commission=-0.14, closing=True),
    ])
    assert rates == {41: (0.28, 1)}


def test_a_full_lot_costs_a_hundred_times_a_hundredth():
    """The rate is per unit, so it scales with size and nothing else."""
    rates = round_trip_rates([
        deal(1, commission=-0.14),
        deal(1, commission=-0.14, closing=True),
    ])
    per_unit, _ = rates[41]
    # 0.01 lots is 1 unit here; 1.00 lot is 100.
    assert fee_for(100, per_unit) == pytest.approx(28.0)


def test_charging_only_the_closing_leg_gives_the_same_answer():
    """Summing the position is right whichever leg the broker bills.

    Whether cTrader splits the charge across open and close or puts the
    whole round trip on the closing deal is not documented anywhere we
    ship. Summing every deal on the position makes the question moot,
    which is the reason it is done that way -- so pin it.
    """
    split = round_trip_rates([
        deal(1, commission=-0.14),
        deal(1, commission=-0.14, closing=True),
    ])
    on_close = round_trip_rates([
        deal(2, commission=None),
        deal(2, commission=-0.28, closing=True),
    ])
    assert split[41] == on_close[41] == (0.28, 1)


def test_open_position_is_not_a_round_trip():
    """It has not been charged its closing leg, so it teaches nothing."""
    assert round_trip_rates([deal(1, commission=-0.14)]) == {}


def test_close_without_its_open_is_skipped():
    """A position opened before the window has no volume to divide by.

    Dividing $0.28 by a volume of zero is not a rate, and quietly using
    the closing volume instead would halve it.
    """
    assert round_trip_rates([
        deal(1, commission=-0.28, closing=True),
    ]) == {}


def test_a_broker_reporting_no_commission_teaches_nothing():
    """Absent is not zero. An unknown rate leaves protection untouched."""
    assert round_trip_rates([
        deal(1, commission=None),
        deal(1, commission=None, closing=True),
    ]) == {}


def test_partial_closes_divide_by_the_opened_volume():
    """Two half closes are one round trip, not two."""
    rates = round_trip_rates([
        deal(1, volume=200, commission=-0.28),
        deal(1, volume=100, commission=-0.14, closing=True),
        deal(1, volume=100, commission=-0.14, closing=True),
    ])
    # 0.56 total over 200 protocol volume = 2 units, and ONE round trip.
    per_unit, samples = rates[41]
    assert per_unit == pytest.approx(0.28)
    assert samples == 1


def test_median_ignores_one_odd_trade():
    """A minimum-commission floor on one small trade must not set the rate."""
    deals = []
    for position_id in (1, 2, 3):
        deals += [deal(position_id, commission=-0.14),
                  deal(position_id, commission=-0.14, closing=True)]
    # A fourth position billed a flat floor, wildly off the per-unit rate.
    deals += [deal(4, volume=10, commission=-1.00),
              deal(4, volume=10, commission=0.0, closing=True)]
    per_unit, samples = round_trip_rates(deals)[41]
    assert per_unit == 0.28
    assert samples == 4


def test_symbols_are_kept_apart():
    rates = round_trip_rates([
        deal(1, symbol_id=41, commission=-0.14),
        deal(1, symbol_id=41, commission=-0.14, closing=True),
        deal(2, symbol_id=1, volume=100000, commission=-3.0),
        deal(2, symbol_id=1, volume=100000, commission=-3.0, closing=True),
    ])
    assert rates[41] == (0.28, 1)
    # 6.00 over 1000 units of EURUSD.
    assert rates[1] == (0.006, 1)


def test_unknown_rate_charges_nothing():
    """The fallback has to be a no-op, not a guess."""
    assert fee_for(1.0, None) == 0.0
    assert fee_for(None, 0.28) == 0.0
    assert fee_for(0, 0.28) == 0.0


def test_fee_scales_with_size():
    assert fee_for(1, 0.28) == pytest.approx(0.28)
    assert fee_for(10, 0.28) == pytest.approx(2.8)
