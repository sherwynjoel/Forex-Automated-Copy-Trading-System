"""CopierApp.check_trailing_stops: the periodic sweep that ratchets every
trailing-enabled position's stop. Mirrors the style of the other
LoopingCall-body tests in this file (test_main.py's check_mt5_offline
coverage) -- a real CopierApp with its repo/registries stubbed, asserting
on what amend_position_sltp was called with.

Accessor names: mt5_registry.position_price(account_id, position_id) and
tracker.position_current_price(position_id) are the REAL methods added to
MT5Registry (copier/src/copier/mt5/registry.py) and AccountStateTracker
(copier/src/copier/engine/state.py) for this task -- neither class had a
direct by-position price lookup before. See _current_position_price below.
"""
from unittest.mock import MagicMock, patch
from copier.main import CopierApp
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


def test_a_position_not_found_live_is_cleaned_up():
    app = _app_with_price(current_price=None)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0)]

    app.check_trailing_stops()

    app.repo.delete_trailing_state.assert_called_once_with(account_id=10, position_id=1)
    app.amend_position_sltp.assert_not_called()


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
