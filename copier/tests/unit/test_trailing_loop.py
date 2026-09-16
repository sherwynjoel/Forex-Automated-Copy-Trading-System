"""CopierApp.check_trailing_stops: the periodic sweep that ratchets every
trailing-enabled position's stop. Mirrors the style of the other
LoopingCall-body tests in this file (test_main.py's check_mt5_offline
coverage) -- a real CopierApp with its repo/registries stubbed, asserting
on what amend_position_sltp was called with.

Accessor names: mt5_registry.position_price(account_id, position_id) and
tracker.position_current_price(account_id, position_id) are the REAL
methods added to MT5Registry (copier/src/copier/mt5/registry.py) and
AccountStateTracker (copier/src/copier/engine/state.py) for this task --
neither class had a direct by-position price lookup before. Both are
scoped by account_id: cTrader position ids are broker-assigned per
account, not globally unique, and one org's tracker holds both the
master's and every slave's positions in the same dict, so an unscoped
lookup could return another account's price for a colliding id. See
_current_position_price below.

A missing live price does NOT mean the position is closed -- see
TRAILING_STATE_STALE_AFTER_S's docstring in main.py for the legitimate
gaps (fresh seed, restart, reconnect) where a still-open position is
briefly unpriceable. The staleness tests below construct TrailingState
rows with an explicit `updated_at` to exercise both sides of that grace
period.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from copier.main import CopierApp, TRAILING_STATE_STALE_AFTER_S
from copier.db.repo import TrailingState


def _app_with_price(current_price, is_mt5=False):
    app = CopierApp.__new__(CopierApp)  # bypass __init__'s broker wiring
    app.repo = MagicMock()
    app.amend_position_sltp = MagicMock(return_value={"status": "submitted"})
    app._is_mt5 = MagicMock(return_value=is_mt5)
    if is_mt5:
        app.mt5_registry = MagicMock()
        app.mt5_registry.position_price = MagicMock(return_value=current_price)
    else:
        tracker = MagicMock()
        tracker.position_current_price = MagicMock(return_value=current_price)
        app.state_trackers = {1: tracker}
        app.repo.get_org_for_account = MagicMock(return_value=1)
    return app


def test_a_position_past_trail_start_gets_amended():
    app = _app_with_price(current_price=4212.0)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0)]
    app.repo.get_position_side_and_entry = MagicMock(return_value=("BUY", 4200.0))
    app.repo.load_risk_rule_for_position = MagicMock(
        return_value=MagicMock(trail_start_points=10.0, trail_step_points=5.0))
    app.repo.load_position_protection = MagicMock(return_value=(4150.0, 4400.0))

    app.check_trailing_stops()

    # compute_trailed_stop(side=BUY, entry=4200, best=max(4200, 4212)=4212,
    # trail_start=10, trail_step=5, current_stop=4150):
    #   favourable = 12 >= trail_start(10)
    #   steps = floor((12 - 10) / 5) = 0
    #   distance = 10 - 5 + 0*5 = 5
    #   candidate = 4200 + 5 = 4205 -> max(4205, 4150) = 4205
    app.amend_position_sltp.assert_called_once_with(10, 1, 4205.0, 4400.0, actor="risk-engine")
    app.repo.upsert_trailing_state.assert_called_once_with(
        account_id=10, position_id=1, best_price=4212.0, current_stop=4205.0)
    # The price lookup must be scoped to the position's OWN account, not a
    # global by-id scan (see test_state.py's dedicated collision test).
    app.state_trackers[1].position_current_price.assert_called_once_with(10, 1)


def test_a_stale_position_not_found_live_is_cleaned_up():
    """A row that has gone unpriceable for well over
    TRAILING_STATE_STALE_AFTER_S is genuinely gone -- closed, stopped out,
    anything -- and its trailing state is cleaned up."""
    app = _app_with_price(current_price=None)
    old = datetime.now(timezone.utc) - timedelta(seconds=TRAILING_STATE_STALE_AFTER_S + 30)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0,
                     updated_at=old)]

    app.check_trailing_stops()

    app.repo.delete_trailing_state.assert_called_once_with(account_id=10, position_id=1)
    app.amend_position_sltp.assert_not_called()


def test_a_freshly_seeded_position_not_found_live_is_skipped_not_deleted():
    """The headline bug this fix closes: a tick landing in the resync gap
    right after a fresh seed (tracker/registry lags the synchronous seed by
    ~0.2-0.5s), or during a restart/reconnect, must NOT permanently delete
    trailing state for a still-open position -- nothing ever re-seeds this
    row for an already-open position, so a wrongful delete here silently
    ends trailing for that position's entire remaining life."""
    app = _app_with_price(current_price=None)
    recent = datetime.now(timezone.utc) - timedelta(seconds=1)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0,
                     updated_at=recent)]

    app.check_trailing_stops()

    app.repo.delete_trailing_state.assert_not_called()
    app.amend_position_sltp.assert_not_called()
    app.repo.upsert_trailing_state.assert_not_called()


def test_a_position_with_unknown_row_age_is_skipped_not_deleted():
    """updated_at=None (a test double, or any row somehow missing it) is
    treated as NOT proven stale -- unproven staleness must never delete."""
    app = _app_with_price(current_price=None)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0,
                     updated_at=None)]

    app.check_trailing_stops()

    app.repo.delete_trailing_state.assert_not_called()


def test_a_position_missing_from_positions_table_is_skipped_not_raised():
    """get_position_side_and_entry can return None -- the positions table
    row lags the synchronous trailing-state seed by the same resync gap as
    the price lookup. Unpacking that unconditionally used to raise
    TypeError (caught by the outer per-position guard, but noisily, and it
    skipped the graceful rule-lookup branch). Must instead skip this tick
    cleanly."""
    app = _app_with_price(current_price=4212.0)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0,
                     updated_at=datetime.now(timezone.utc))]
    app.repo.get_position_side_and_entry = MagicMock(return_value=None)

    app.check_trailing_stops()  # must not raise

    app.amend_position_sltp.assert_not_called()
    app.repo.load_risk_rule_for_position.assert_not_called()
    app.repo.upsert_trailing_state.assert_not_called()
    app.repo.delete_trailing_state.assert_not_called()


def test_a_failing_amend_does_not_stop_the_rest_of_the_tick():
    app = _app_with_price(current_price=4212.0)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0),
        TrailingState(account_id=10, position_id=2, best_price=4200.0, current_stop=4150.0)]
    app.repo.get_position_side_and_entry = MagicMock(return_value=("BUY", 4200.0))
    app.repo.load_risk_rule_for_position = MagicMock(
        return_value=MagicMock(trail_start_points=10.0, trail_step_points=5.0))
    app.repo.load_position_protection = MagicMock(return_value=(4150.0, 4400.0))
    app.amend_position_sltp = MagicMock(side_effect=[Exception("boom"), {"status": "submitted"}])

    app.check_trailing_stops()  # must not raise

    assert app.amend_position_sltp.call_count == 2


def test_a_failure_loading_the_trailing_rows_themselves_does_not_crash_the_loop():
    """load_all_trailing_state_rows() sits OUTSIDE _check_one_trailing_position's
    per-position guard -- it must be guarded on its own. Repo._connect()
    names a killed idle connection as an expected failure mode; if that
    propagated out of check_trailing_stops, it would fail the LoopingCall's
    Deferred and permanently stop trailing for every account, which is
    worse than the failure mode the per-position guard protects against."""
    app = CopierApp.__new__(CopierApp)
    app.repo = MagicMock()
    app.repo.load_all_trailing_state_rows = MagicMock(
        side_effect=Exception("connection killed"))
    app.amend_position_sltp = MagicMock()

    app.check_trailing_stops()  # must not raise

    app.amend_position_sltp.assert_not_called()
