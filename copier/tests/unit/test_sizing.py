from decimal import Decimal

from copier.domain.sizing import (floor_to_step, lots_to_protocol_volume, mirror_volume,
                                  partial_close_volume, protocol_volume_to_lots)

LOT = 10_000_000     # ProtoOASymbol.lotSize for EURUSD ("in cents")
STEP = 100_000       # 0.01 lot


def test_spec_example_one_lot_eurusd_is_ten_million():
    # spec §3: 1.00 lot EURUSD = protocol volume 10,000,000
    assert lots_to_protocol_volume(Decimal("1.00"), LOT) == 10_000_000
    assert protocol_volume_to_lots(10_000_000, LOT) == Decimal("1")


def test_hundredth_lot():
    assert lots_to_protocol_volume(Decimal("0.01"), LOT) == 100_000
    assert protocol_volume_to_lots(100_000, LOT) == Decimal("0.01")


def test_floor_to_step():
    assert floor_to_step(3_333_333, STEP) == 3_300_000
    assert floor_to_step(3_300_000, STEP) == 3_300_000
    assert floor_to_step(99_999, STEP) == 0


def test_mirror_default_multiplier_is_exact():
    assert mirror_volume(10_000_000, LOT, Decimal("1.0"), LOT, STEP) == 10_000_000


def test_mirror_half_multiplier():
    assert mirror_volume(10_000_000, LOT, Decimal("0.5"), LOT, STEP) == 5_000_000


def test_mirror_never_returns_lots_or_centilots():
    # order-of-magnitude guard (spec §5): 1 lot must be 10_000_000, never 1, 100, or 100_000
    v = mirror_volume(10_000_000, LOT, Decimal("1.0"), LOT, STEP)
    assert v not in (1, 100, 100_000)
    assert v == 10_000_000


def test_mirror_rounds_down_to_step():
    # 0.10 lot * 0.333 = 0.0333 lot -> floors to 0.03 lot
    assert mirror_volume(1_000_000, LOT, Decimal("0.333"), LOT, STEP) == 300_000


def test_mirror_below_one_step_rounds_to_zero():
    # caller must alert, never send 0 (Task 5)
    assert mirror_volume(100_000, LOT, Decimal("0.5"), LOT, STEP) == 0


def test_partial_close_half():
    assert partial_close_volume(10_000_000, 5_000_000, 5_000_000, STEP) == 5_000_000


def test_partial_close_uneven_fraction_floors_to_step():
    # master closes 1/3 -> slave closes floor(10M/3) floored to step
    assert partial_close_volume(10_000_000, 1_000_000, 2_000_000, STEP) == 3_300_000


def test_full_close_returns_entire_slave_volume():
    # remaining 0 = full close: return everything regardless of step rounding
    assert partial_close_volume(9_999_999, 3_000_000, 0, STEP) == 9_999_999


def test_partial_close_never_exceeds_slave_volume():
    assert partial_close_volume(200_000, 999_999_999, 1, STEP) <= 200_000
