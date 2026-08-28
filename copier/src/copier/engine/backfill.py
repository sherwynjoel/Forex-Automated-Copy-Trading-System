"""Deal-history backfill: pick the next week-long window to fetch.

The broker caps ProtoOADealListReq at one week and 500 rows per call
(queries.DEAL_LIST_MAX_ROWS), and the copier's queued send path runs at
10 msg/s, so this walks backwards one week at a time in the background
rather than trying to pull years at once.

next_window is pure so the walk is testable without a broker.
"""

WEEK_MS = 7 * 24 * 3600 * 1000
YEAR_MS = 365 * 24 * 3600 * 1000


def reached_history_bound(from_ms: int, now_ms: int, max_years: int) -> bool:
    """True once the backward walk has covered `max_years` -- the point at
    which next_window() stops walking back.

    This is what `deal_backfill_state.exhausted` means: "we reached the
    account's first deal", or at least the bound we are willing to walk to.
    It is deliberately NOT "the last window came back empty": a single quiet
    week (a holiday, a paused slave, any account not copied to in seven
    days) is the common case, and treating it as exhaustion threw away the
    account's entire history while telling the Performance page
    truncated=False -- the flag claiming "complete" exactly when the data
    was worst.
    """
    return now_ms - from_ms >= max_years * YEAR_MS


def next_window(
    state: dict | None, now_ms: int, max_years: int
) -> tuple[int, int] | None:
    """The next [from_ms, to_ms] to fetch, or None when there is nothing to do.

    Args:
        state: Row from deal_backfill_state, or None if never started.
        now_ms: Current time in epoch milliseconds.
        max_years: How far back the first run walks before giving up. Not a
            one-way door -- raising it and restarting resumes from the
            watermark.

    Returns:
        (from_ms, to_ms), or None.
    """
    if state is None:
        return (now_ms - WEEK_MS, now_ms)

    if state.get("exhausted"):
        # Nothing older to fetch; just catch up on anything since the last
        # window closed.
        to_ms = state.get("backfilled_to_ms") or now_ms
        if to_ms >= now_ms:
            return None
        # Clamped to a week: cTrader REJECTS DealList windows longer than
        # one week (see queries.py). A copier that was down for ten days
        # would otherwise request a ten-day window on the first tick after
        # restart, the broker would error, queries.deal_history would raise,
        # and set_backfill_state would never run -- so backfilled_to_ms
        # never advances and EVERY subsequent tick repeats identically,
        # forever, with no way out but editing deal_backfill_state by hand.
        return (to_ms, min(now_ms, to_ms + WEEK_MS))

    from_ms = state.get("backfilled_from_ms")
    if from_ms is None:
        return (now_ms - WEEK_MS, now_ms)

    if reached_history_bound(from_ms, now_ms, max_years):
        return None

    return (from_ms - WEEK_MS, from_ms)
