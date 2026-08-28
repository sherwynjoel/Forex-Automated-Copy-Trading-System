"""Deal backfill: the week-by-week walk and its watermark.

next_window is pure, so the walk logic is tested without a broker."""

import pytest

from copier.engine.backfill import (
    WEEK_MS, next_window, reached_history_bound)

NOW = 1_700_000_000_000


def test_first_window_is_the_most_recent_week():
    lo, hi = next_window(None, NOW, max_years=2)
    assert hi == NOW
    assert lo == NOW - WEEK_MS


def test_second_window_steps_back_another_week():
    state = {"backfilled_from_ms": NOW - WEEK_MS, "backfilled_to_ms": NOW,
             "exhausted": False}
    lo, hi = next_window(state, NOW, max_years=2)
    assert hi == NOW - WEEK_MS
    assert lo == NOW - 2 * WEEK_MS


def test_walk_stops_at_the_max_years_bound():
    """An account with a decade of history would otherwise spend days
    walking week-by-week against a 10 msg/s queue."""
    two_years_ago = NOW - 2 * 365 * 24 * 3600 * 1000
    state = {"backfilled_from_ms": two_years_ago, "backfilled_to_ms": NOW,
             "exhausted": False}
    assert next_window(state, NOW, max_years=2) is None


def test_exhausted_accounts_only_track_forward():
    state = {"backfilled_from_ms": NOW - WEEK_MS, "backfilled_to_ms": NOW - 100,
             "exhausted": True}
    lo, hi = next_window(state, NOW, max_years=2)
    assert hi == NOW
    assert lo == NOW - 100


def test_an_exhausted_account_with_nothing_new_gets_no_window():
    state = {"backfilled_from_ms": NOW - WEEK_MS, "backfilled_to_ms": NOW,
             "exhausted": True}
    assert next_window(state, NOW, max_years=2) is None


def test_a_forward_catch_up_window_is_clamped_to_one_week():
    """I4: cTrader REJECTS DealList windows longer than a week. A copier
    down for ten days would ask for a ten-day window, the broker would
    error, deal_history would raise, set_backfill_state would never run --
    and every subsequent tick would repeat identically, forever, killing
    the whole fleet's backfill until someone edited the table by hand."""
    ten_days_ago = NOW - 10 * 24 * 3600 * 1000
    state = {"backfilled_from_ms": NOW - 100 * WEEK_MS,
             "backfilled_to_ms": ten_days_ago, "exhausted": True}

    lo, hi = next_window(state, NOW, max_years=2)

    assert lo == ten_days_ago
    assert hi - lo <= WEEK_MS, "the broker rejects windows longer than a week"


def test_a_short_forward_catch_up_window_is_left_alone():
    state = {"backfilled_from_ms": NOW - WEEK_MS,
             "backfilled_to_ms": NOW - 30_000, "exhausted": True}
    lo, hi = next_window(state, NOW, max_years=2)
    assert (lo, hi) == (NOW - 30_000, NOW)


# ---------- what `exhausted` means (C2) ----------

def test_reached_history_bound_is_false_until_the_walk_covers_max_years():
    """`exhausted` means "we reached the account's first deal", not "the
    last window was empty". A quiet week is the common case for a slave
    that has not been copied to in seven days."""
    assert reached_history_bound(NOW - WEEK_MS, NOW, max_years=2) is False
    assert reached_history_bound(NOW - 103 * WEEK_MS, NOW, max_years=2) is False


def test_reached_history_bound_is_true_at_the_max_years_bound():
    two_years = 2 * 365 * 24 * 3600 * 1000
    assert reached_history_bound(NOW - two_years, NOW, max_years=2) is True


def test_the_bound_next_window_stops_at_is_the_bound_exhaustion_uses():
    """The two must not drift apart: if next_window stops walking back at a
    point reached_history_bound calls False, the account can never be
    marked exhausted and the Performance page reports truncated=True
    forever even though backfill has stopped for good."""
    two_years = 2 * 365 * 24 * 3600 * 1000
    for from_ms in (NOW - two_years + WEEK_MS, NOW - two_years,
                    NOW - two_years - WEEK_MS):
        state = {"backfilled_from_ms": from_ms, "backfilled_to_ms": NOW,
                 "exhausted": False}
        stopped = next_window(state, NOW, max_years=2) is None
        assert stopped is reached_history_bound(from_ms, NOW, max_years=2)
