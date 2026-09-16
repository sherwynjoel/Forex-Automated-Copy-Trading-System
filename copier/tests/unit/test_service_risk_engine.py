"""The risk-engine fill-in step: when a master position opens with no
stop/target (a bare alert from an indicator that doesn't compute its
own), and the org has a configured org_risk_rules row for that symbol,
CopierService amends the position to the rule's defaults and seeds
position_trailing_state if trailing is on. An alert that already carries
its own stop/target is left exactly alone -- MirrorFleet never overrides
what an indicator explicitly sent.
"""
from unittest.mock import MagicMock
from copier.engine.service import CopierService
from copier.domain.models import MasterPositionOpened, MasterPositionClosed, Side
from copier.domain.models import AmendPositionSLTP
from copier.db.repo import RiskRule


def _service(repo):
    return CopierService(
        repo=repo, dispatcher=MagicMock(),
        routing_provider=lambda: MagicMock(slaves_by_org={}),
        master_symbols_by_org={},
    )


def test_bare_open_with_no_rule_does_nothing():
    repo = MagicMock()
    repo.load_risk_rule.return_value = None
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service._dispatcher.dispatch.assert_not_called()
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

    (intents,), kwargs = service._dispatcher.dispatch.call_args
    assert kwargs == {"org_id": 1}
    assert intents == [AmendPositionSLTP(10, 1, 4150.0, 4350.0)]
    repo.upsert_trailing_state.assert_not_called()  # trailing_enabled is False


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
