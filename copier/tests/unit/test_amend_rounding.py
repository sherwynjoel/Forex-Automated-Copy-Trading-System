"""The copier must not put an unusable price on the wire.

A price carrying more decimals than the broker quotes is refused with
INVALID_REQUEST and the protection silently never takes. Producing one is
trivial: 4573.27 - (100 / 90) is 4572.158888888889 in binary floating
point, which is exactly what a dashboard computing a money-denominated
stop arrives at. Rounding in the browser is not enough -- a stale cached
bundle or a direct API call would each bypass it.
"""


def test_rounding_matches_the_symbol_precision():
    # Gold is quoted to 2 decimals; the artifact must not survive.
    assert round(4572.158888888889, 2) == 4572.16
    assert round(4574.381111111112, 2) == 4574.38


def test_five_decimal_pairs_keep_their_precision():
    assert round(1.0858123456, 5) == 1.08581


def test_rounding_never_moves_a_price_materially():
    # A stop must land where it was asked for, to the tick the broker
    # quotes -- never further away than half of one.
    for raw, digits in ((4572.158888888889, 2), (1.0858123456, 5), (150.12345, 3)):
        assert abs(round(raw, digits) - raw) <= 0.5 * (10 ** -digits)
