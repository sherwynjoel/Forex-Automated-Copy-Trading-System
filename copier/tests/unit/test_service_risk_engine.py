"""The risk-engine fill-in step: when a master position opens with no
stop/target (a bare alert from an indicator that doesn't compute its
own), and the org has a configured org_risk_rules row for that symbol,
CopierService amends the position to the rule's defaults and seeds
position_trailing_state if trailing is on. An alert that already carries
its own stop/target is left exactly alone -- MirrorFleet never overrides
what an indicator explicitly sent.

The fill-in amend protects the operator's OWN master position, not a
copy, so it goes through master_amend_sltp (wired by build_app() to
CopierApp.amend_position_sltp) rather than the copy-gated
self._dispatcher.dispatch() -- see test_bare_open_with_a_rule_amends_...
below. This mirrors how the trailing loop's amends and the manual
Trade-page amend button already bypass copying_enabled/dry_run: those
gates exist to stop COPYING, not to stop the operator protecting their
own account.
"""
from unittest.mock import MagicMock
from copier.engine.service import CopierService
from copier.domain.models import MasterPositionOpened, MasterPositionClosed, Side
from copier.db.repo import RiskRule


def _service(repo):
    service = CopierService(
        repo=repo, dispatcher=MagicMock(),
        routing_provider=lambda: MagicMock(slaves_by_org={}),
        master_symbols_by_org={},
    )
    service.master_amend_sltp = MagicMock(return_value={"status": "submitted"})
    return service


def test_bare_open_with_no_rule_does_nothing():
    repo = MagicMock()
    repo.load_risk_rule.return_value = None
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service._dispatcher.dispatch.assert_not_called()
    service.master_amend_sltp.assert_not_called()
    repo.upsert_trailing_state.assert_not_called()


def test_bare_open_with_a_rule_amends_to_the_defaults():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service.master_amend_sltp.assert_called_once_with(10, 1, 4150.0, 4350.0, actor="risk-engine")
    # Never through the copy-gated path -- this protects the master's OWN
    # position, not a copy.
    service._dispatcher.dispatch.assert_not_called()
    repo.upsert_trailing_state.assert_not_called()  # trailing_enabled is False


def test_bare_open_with_a_rule_amends_even_with_dry_run_and_kill_switch_semantics():
    """The fill-in amend must reach the broker regardless of the org's
    copying_enabled/dry_run gates -- those exist to stop COPYING, and this
    is the operator's own master position, exactly like the trailing
    loop's amends and the manual Trade-page amend button. Proven here by
    never even touching org gating: master_amend_sltp is called
    unconditionally and repo.get_org (what Dispatcher.dispatch reads the
    gates from) is never consulted."""
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service.master_amend_sltp.assert_called_once_with(10, 1, 4150.0, 4350.0, actor="risk-engine")
    repo.get_org.assert_not_called()
    service._dispatcher.dispatch.assert_not_called()


def test_a_failing_master_amend_is_swallowed_not_raised():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    service.master_amend_sltp = MagicMock(side_effect=Exception("broker unreachable"))
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)  # must not raise

    service.master_amend_sltp.assert_called_once()


def test_no_master_amend_sltp_wired_logs_but_does_not_raise():
    """Defensive: if build_app() somehow left master_amend_sltp unwired,
    the fill-in step must not crash the event pump -- it logs and moves
    on, same as every other best-effort step in this class."""
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    service.master_amend_sltp = None
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)  # must not raise

    service._dispatcher.dispatch.assert_not_called()


def test_open_that_already_carries_its_own_levels_is_left_alone():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=4100.0,
                                 take_profit=4400.0, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service._dispatcher.dispatch.assert_not_called()
    service.master_amend_sltp.assert_not_called()


def test_trailing_enabled_seeds_state_even_when_alert_supplied_its_own_stop():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=None, target_points=None,
        trailing_enabled=True, trail_start_points=10.0, trail_step_points=5.0)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=4150.0,
                                 take_profit=4400.0, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    repo.upsert_trailing_state.assert_called_once_with(
        account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0)


def test_full_close_clears_trailing_state():
    repo = MagicMock()
    service = _service(repo)
    event = MasterPositionClosed(position_id=1, symbol_name="XAUUSD",
                                 closed_volume=100000, remaining_volume=0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    repo.delete_trailing_state.assert_called_once_with(account_id=10, position_id=1)


def test_partial_close_does_not_clear_trailing_state():
    repo = MagicMock()
    service = _service(repo)
    event = MasterPositionClosed(position_id=1, symbol_name="XAUUSD",
                                 closed_volume=50000, remaining_volume=50000)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    repo.delete_trailing_state.assert_not_called()
