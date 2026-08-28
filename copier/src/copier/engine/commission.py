"""Learning what the broker charges from what the broker charged.

A money-denominated stop inverts the P&L formula -- price_move =
amount / units -- which yields GROSS profit. The broker's commission comes
out of that, so a "$1.50" target paid $1.26 and a "$1.50" stop lost $1.78.
To correct it we need the commission, and the honest source for it is not
the symbol record but the account's own closed trades.

WHY NOT ProtoOASymbol. It carries commission, commissionType,
minCommission, preciseTradingCommissionRate and preciseMinCommission, and
any of them would be cheaper to read than a deal history. But the Python
SDK ships only generated descriptors with source_code_info stripped: no
comment, docstring or constant in the wheel states the scale of a single
one of those fields, and nothing in this repo has ever read them, so there
is no in-house precedent either. `commission = 30` could be thirty
dollars, thirty cents, or thirty per million traded. Choosing the wrong
exponent misplaces a live stop by a factor of a hundred, in a direction
nobody would notice until the money was gone.

WHAT A DEAL GIVES INSTEAD. Every deal reports the money actually taken,
already scaled by its own moneyDigits (queries.py:_money). Summing the
deals of one position gives that position's round trip as a fact. This
module does only that, and deliberately does not care whether the broker
splits the charge across both legs or bills it all on the close -- summing
every deal on the position is correct under either, which is the point.
"""

from statistics import median


def round_trip_rates(deals) -> dict[int, tuple[float, int]]:
    """symbol_id -> (round-trip commission per unit, positions observed).

    `deals` is a sequence of the dicts queries.py:_map_deal produces. Only
    positions seen to open AND close within the batch are used: a position
    whose opening deal predates the window would otherwise have its
    commission divided by a volume of zero, and one still open has not
    been charged its closing leg yet. Both are skipped rather than
    approximated -- a rate that is absent leaves protection exactly as it
    is today, while a rate that is wrong moves a stop.

    "Per unit" means per protocol-volume/100, the divisor the P&L engine
    uses, so a caller holding `units` multiplies and is done.
    """
    by_position: dict[int, list[dict]] = {}
    for deal in deals:
        position_id = deal.get("position_id")
        if position_id is None:
            continue
        by_position.setdefault(position_id, []).append(deal)

    observations: dict[int, list[float]] = {}
    for position_deals in by_position.values():
        rate = _round_trip_rate(position_deals)
        if rate is None:
            continue
        symbol_id, per_unit = rate
        observations.setdefault(symbol_id, []).append(per_unit)

    # The median, not the mean: a single partial close billed oddly, or one
    # trade that tripped a minimum-commission floor, should not drag the
    # rate every later trade is priced from.
    return {symbol_id: (median(rates), len(rates))
            for symbol_id, rates in observations.items()}


def _round_trip_rate(position_deals: list[dict]) -> tuple[int, float] | None:
    """One position's (symbol_id, commission per unit), or None to skip."""
    opens = [d for d in position_deals if not d.get("close")]
    closes = [d for d in position_deals if d.get("close")]
    if not opens or not closes:
        return None

    # The position's size is what it OPENED at. Charging is round trip, so
    # dividing the total by open+close volume would halve every rate.
    volume = sum(d.get("filled_volume") or 0 for d in opens)
    if volume <= 0:
        return None

    # A deal with no commission field is not evidence of a free trade, it
    # is an absence. Requiring at least one deal to report the field keeps
    # a broker that omits it entirely from teaching us a confident zero;
    # once one deal reports it, a sibling reporting nothing really is nil.
    reported = [d.get("commission") for d in position_deals
                if d.get("commission") is not None]
    if not reported:
        return None
    total = sum(abs(c) for c in reported)

    symbol_id = opens[0].get("symbol_id")
    if symbol_id is None:
        return None
    return symbol_id, total / (volume / 100)


def fee_for(units: float | None, per_unit: float | None) -> float:
    """Round-trip commission on `units`, or 0.0 when it is not known.

    Zero is the safe unknown: it leaves an amount-denominated stop exactly
    where today's arithmetic puts it, so a symbol we have never traded
    behaves as it always has instead of moving on a guess.
    """
    if units is None or per_unit is None:
        return 0.0
    if units <= 0 or per_unit < 0:
        return 0.0
    return per_unit * units
