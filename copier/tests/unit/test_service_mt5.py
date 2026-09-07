"""The platform-neutral seams of CopierService (engine/service.py) that the
MT5 lane drives directly: act_on_master_event, handle_slave_fill,
handle_slave_rejection, handle_slave_order_accepted/cancelled. The cTrader
protobuf path is exercised by test_service.py, whose fixtures this reuses."""

import psycopg
import pytest

from copier.domain.models import (
    MANUAL_ORDER_LABEL, AmendPositionSLTP, ClosePosition, MasterPositionClosed,
    MasterPositionOpened, MasterPositionSLTPAmended, OpenMarket, Side)
from copier.engine.service import SlaveFill

from test_service import (  # noqa: F401  (fixtures are used by name)
    ORG_ID, clock, db_seeded, recording_dispatcher, repo, routing_box, service)


def _events(repo, action=None, category=None):
    query = "SELECT category, severity, payload, latency_ms, account_id FROM events"
    clauses, params = [], []
    if action is not None:
        clauses.append("payload->>'action' = %s")
        params.append(action)
    if category is not None:
        clauses.append("category = %s")
        params.append(category)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


def _fill(account_id=100, coid="cm11.100", position_id=55, volume=10_000_000, price=1.1,
          label="", **extra):
    return SlaveFill(account_id=account_id, client_order_id=coid, position_id=position_id,
                     filled_volume=volume, fill_price=price, closed_volume=None, label=label,
                     **extra)


OPENED = MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=Side.BUY,
                              volume=10_000_000, lot_size=10_000_000, stop_loss=1.09,
                              take_profit=1.12, entry_price=1.1)


def _amends(dispatcher):
    return [i for i in dispatcher.intents if isinstance(i, AmendPositionSLTP)]


class TestActOnMasterEvent:
    def test_decides_dispatches_and_audits_under_the_org(self, service, recording_dispatcher, repo):
        calls = []
        service.on_positions_changed = lambda org_id=None: calls.append(org_id)

        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")

        opens = [i for i in recording_dispatcher.intents if isinstance(i, OpenMarket)]
        assert {i.slave_account_id for i in opens} == {100, 101}
        assert all(i.stop_loss == 1.09 and i.take_profit == 1.12 and i.entry_price == 1.1
                   for i in opens)
        assert [org for _i, org in recording_dispatcher.calls] == [ORG_ID]
        (event,) = _events(repo, category="master_event")
        assert event[2] == {"source": "mt5", "normalized": "MasterPositionOpened"}
        assert event[3] is not None and event[3] >= 0 and event[4] == 999
        assert calls == [ORG_ID]

    def test_a_close_reaches_the_mapped_copies(self, service, recording_dispatcher, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 10_000_000)
        service.act_on_master_event(
            ORG_ID, 999, MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                              closed_volume=10_000_000, remaining_volume=0),
            source="mt5")
        closes = [i for i in recording_dispatcher.intents if isinstance(i, ClosePosition)]
        assert closes == [ClosePosition(100, 55, 10_000_000, master_position_id=11)]

    def test_the_audit_row_lands_even_when_dispatch_raises(self, service, recording_dispatcher, repo):
        recording_dispatcher.dispatch.side_effect = RuntimeError("wire fell over")
        with pytest.raises(RuntimeError):
            service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")
        assert len(_events(repo, category="master_event")) == 1


class TestHandleSlaveFill:
    def test_activates_the_mapping_with_the_fill_price(self, service, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill(price=1.10537))
        (mapping,) = repo.mapping_rows(org_id=ORG_ID)
        assert (mapping["status"], mapping["slave_position_id"], mapping["slave_volume"],
                mapping["fill_price"]) == ("active", 55, 10_000_000, 1.10537)
        (event,) = _events(repo, "position_filled")
        assert event[2]["fill_price"] == 1.10537 and event[4] == 100

    def test_a_copy_already_carrying_the_masters_protection_is_not_amended_again(
            self, service, recording_dispatcher, repo):
        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")   # remembers 1.09 / 1.12
        recording_dispatcher.intents.clear()
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill(stop_loss=1.09, take_profit=1.12))
        assert _amends(recording_dispatcher) == []

    def test_a_copy_whose_protection_differs_is_brought_up_to_date(
            self, service, recording_dispatcher, repo):
        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")
        service.act_on_master_event(
            ORG_ID, 999, MasterPositionSLTPAmended(position_id=11, stop_loss=1.095,
                                                   take_profit=1.12), source="mt5")
        recording_dispatcher.intents.clear()
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill(stop_loss=1.09, take_profit=1.12))   # the OLD stop
        assert _amends(recording_dispatcher) == [AmendPositionSLTP(100, 55, 1.095, 1.12)]

    def test_an_unknown_protection_is_restated_as_before(self, service, recording_dispatcher, repo):
        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")
        recording_dispatcher.intents.clear()
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill())        # cTrader-style: levels unknown
        assert _amends(recording_dispatcher) == [AmendPositionSLTP(100, 55, 1.09, 1.12)]

    def test_a_closing_fill_reduces_the_mapping(self, service, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 10_000_000)
        service.handle_slave_fill(ORG_ID, SlaveFill(
            account_id=100, client_order_id=None, position_id=55, filled_volume=4_000_000,
            fill_price=1.11, closed_volume=4_000_000, label=""))
        (entry,) = repo.position_entries(11)
        assert entry.slave_volume == 6_000_000
        (event,) = _events(repo, "position_closed")
        assert event[2] == {"action": "position_closed", "slave_position_id": 55,
                            "closed_volume": 4_000_000}

    def test_a_closing_fill_that_names_its_mapping_reduces_only_that_one(self, service, repo):
        """A netting MT5 follower's copies share one net position; the
        outbox's ':close' ack comes back under the mapping's own coid."""
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 6_000_000)
        repo.create_position_mapping(12, 100, "cm12.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm12.100", 55, 4_000_000)
        service.handle_slave_fill(ORG_ID, SlaveFill(
            account_id=100, client_order_id="cm12.100", position_id=55, filled_volume=4_000_000,
            fill_price=1.11, closed_volume=4_000_000, label=""))
        assert repo.position_entries(12) == []
        (entry,) = repo.position_entries(11)
        assert entry.slave_volume == 6_000_000
        (event,) = _events(repo, "position_closed")
        assert event[2] == {"action": "position_closed", "slave_position_id": 55,
                            "closed_volume": 4_000_000, "client_order_id": "cm12.100"}

    def test_a_pending_copy_fills_by_its_order_ticket(self, service, repo):
        repo.create_order_mapping(42, 100, "co42.100", org_id=ORG_ID)
        repo.activate_order_mapping(100, "co42.100", 9999)
        service.handle_slave_fill(ORG_ID, _fill(coid=None, position_id=77, order_id=9999))
        (mapping,) = repo.mapping_rows(org_id=ORG_ID)
        assert (mapping["slave_position_id"], mapping["slave_volume"],
                mapping["fill_price"]) == (77, 10_000_000, 1.1)
        assert _events(repo, "pending_fill")

    def test_a_manual_fill_is_expected_and_an_unmatched_one_is_a_warning(self, service, repo):
        service.handle_slave_fill(ORG_ID, _fill(coid=None, label=MANUAL_ORDER_LABEL))
        assert _events(repo, "manual_fill") and not _events(repo, "unmatched_slave_fill")
        service.handle_slave_fill(ORG_ID, _fill(coid=None, position_id=56))
        (event,) = _events(repo, "unmatched_slave_fill")
        assert event[1] == "warning" and event[2]["slave_position_id"] == 56

    def test_an_unknown_client_order_id_is_a_warning(self, service, repo):
        service.handle_slave_fill(ORG_ID, _fill(coid="cm999.100"))
        (event,) = _events(repo, "unknown_fill")
        assert event[1] == "warning" and event[2]["client_order_id"] == "cm999.100"


class TestRejectionAcceptedCancelled:
    def test_rejection_fails_the_mapping_and_logs(self, service, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_rejection(
            ORG_ID, 100, "cm11.100", "terminal rejected open: not enough money")
        (mapping,) = repo.mapping_rows(org_id=ORG_ID)
        assert (mapping["status"], mapping["error"]) == (
            "failed", "terminal rejected open: not enough money")
        (event,) = _events(repo, "order_rejected")
        assert event[1] == "error"
        assert event[2]["error"] == "terminal rejected open: not enough money"
        # No mapping to fail: still logged.
        service.handle_slave_rejection(ORG_ID, 100, None, "terminal rejected close: position closed")
        assert len(_events(repo, "order_rejected")) == 2

    def test_accepted_and_cancelled_pending_copies(self, service, repo):
        repo.create_order_mapping(42, 100, "co42.100", org_id=ORG_ID)
        service.handle_slave_order_accepted(ORG_ID, 100, "co42.100", 9999)
        assert [e.slave_order_id for e in repo.order_entries(42)] == [9999]
        service.handle_slave_order_accepted(ORG_ID, 100, None, 1)   # an operator's order: nothing to link
        service.handle_slave_order_cancelled(ORG_ID, 100, 9999)
        assert repo.order_entries(42) == []
        assert _events(repo, "order_accepted") and _events(repo, "order_cancelled")
