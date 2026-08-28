"""Tests for the copier service (service.py) — master/slave event wiring orchestration."""

from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch
import time

import psycopg
import pytest
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType, ProtoOATradeSide
)
from twisted.internet.task import Clock

from copier.db.repo import Repo, MappingNotFound
from copier.domain.models import SymbolInfo, SlaveConfig, Side, PendingType, OpenMarket, Alert
from copier.engine.routing import OrgRouting
from copier.engine.service import CopierService, PENDING_FILL_ALERT_S


# Test fixtures and helpers

ORG_ID = 1

EURUSD = SymbolInfo(
    symbol_id=1, name="EURUSD", digits=5,
    lot_size=10_000_000, min_volume=100_000, step_volume=100_000
)

GBPUSD = SymbolInfo(
    symbol_id=2, name="GBPUSD", digits=5,
    lot_size=10_000_000, min_volume=100_000, step_volume=100_000
)


def base_event(
    account_id=999,  # master by default
    execution_type=ProtoOAExecutionType.ORDER_FILLED,
    order_type=ProtoOAOrderType.MARKET,
    order_id=5,
    position_id=11,
    volume=10_000_000,
    symbol_id=1,
):
    """Build a base ProtoOAExecutionEvent for testing."""
    e = ProtoOAExecutionEvent()
    e.ctidTraderAccountId = account_id
    e.executionType = execution_type
    e.order.orderId = order_id
    e.order.orderType = order_type
    e.order.tradeData.symbolId = symbol_id
    e.order.tradeData.tradeSide = ProtoOATradeSide.BUY
    e.order.tradeData.volume = volume
    e.position.positionId = position_id
    e.position.tradeData.volume = volume
    return e


def seed_db(db):
    """Seed test database with one org, its connection, and its accounts."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, 'Org A')", (ORG_ID,))
        # Create connection
        conn.execute(
            """
            INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, %s, now(), now() + interval '1 hour')
            """,
            (ORG_ID, "token_access", "token_refresh"),
        )
        # Create master account (999) and slave accounts (100, 101)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (999, %(org)s, 1, 99900, false, 'master', true, 1.0),
                (100, %(org)s, 1, 10000, false, 'slave', true, 1.0),
                (101, %(org)s, 1, 10001, false, 'slave', true, 1.5)
            """,
            {"org": ORG_ID},
        )


@pytest.fixture
def db_seeded(db):
    """Database fixture with seeded accounts."""
    seed_db(db)
    return db


@pytest.fixture
def repo(db_seeded):
    """Create a Repo instance connected to test database."""
    return Repo(db_seeded)


@pytest.fixture
def clock():
    """Twisted Clock for testing time-based behavior."""
    return Clock()


@pytest.fixture
def recording_dispatcher():
    """Mock dispatcher that records every dispatch call's intents AND org."""
    dispatcher = Mock()
    dispatcher.dispatch = Mock()
    dispatcher.intents = []
    dispatcher.calls = []  # [(intents, org_id)] -- one entry per dispatch call

    def record_dispatch(intents, org_id):
        dispatcher.intents.extend(intents)
        dispatcher.calls.append((list(intents), org_id))

    dispatcher.dispatch.side_effect = record_dispatch
    return dispatcher


@pytest.fixture
def routing_box(make_routing):
    """Mutable holder for the routing the service sees.

    The service reads its routing through a provider on every event, exactly
    as it does in production (CopierApp.reload() swaps the table), so a test
    can change the fleet mid-test by assigning to routing_box['routing'].
    """
    slaves = [
        SlaveConfig(account_id=100, enabled=True, multiplier=Decimal("1.0"),
                    symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
        SlaveConfig(account_id=101, enabled=True, multiplier=Decimal("1.5"),
                    symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
    ]
    return {"routing": make_routing(master=999, slaves=slaves, org_id=ORG_ID)}


@pytest.fixture
def service(repo, recording_dispatcher, clock, routing_box):
    """Create a CopierService instance for testing."""
    return CopierService(
        repo=repo,
        dispatcher=recording_dispatcher,
        routing_provider=lambda: routing_box["routing"],
        master_symbols_by_org={ORG_ID: {1: EURUSD, 2: GBPUSD}},
        clock=clock
    )


# Test cases from the brief

class TestMasterEventHandling:
    """Test master event handling: normalize -> decide -> dispatch."""

    def test_master_fill_dispatches_open_intents(self, service, recording_dispatcher):
        """Master MARKET fill should normalize to open, decide, and dispatch to 2 slaves."""
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(999, evt)

        # Should have dispatched 2 OpenMarket intents (one per enabled slave)
        assert len(recording_dispatcher.intents) >= 2
        open_intents = [i for i in recording_dispatcher.intents if isinstance(i, OpenMarket)]
        assert len(open_intents) == 2
        assert all(i.master_position_id == 11 for i in open_intents)
        # and dispatched on behalf of exactly the org that owns the master
        assert [org_id for _intents, org_id in recording_dispatcher.calls] == [ORG_ID]


class TestCrossOrgIsolation:
    """THE invariant of the multi-org engine: one org's master events can
    never produce trades on another org's accounts.

    Before partitioning, the service held a single master_account_id and a
    single flat slaves_provider(), so the moment a second tenant's accounts
    existed in the same process every master fill fanned out across ALL of
    them -- real orders, on other people's money.
    """

    @pytest.fixture
    def two_org_db(self, db):
        """Two orgs, each with its own connection, master, and slave."""
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute("INSERT INTO orgs (id, name) VALUES (1, 'Org A'), (2, 'Org B')")
            conn.execute(
                """
                INSERT INTO ctid_connections (id, org_id, access_token_enc, refresh_token_enc,
                                              granted_at, expires_at)
                VALUES (1, 1, 'a', 'a', now(), now() + interval '1 hour'),
                       (2, 2, 'b', 'b', now(), now() + interval '1 hour')
                """
            )
            conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                      trader_login, is_live, role, enabled, multiplier)
                VALUES (100, 1, 1, 1000, false, 'master', true, 1.0),
                       (101, 1, 1, 1001, false, 'slave',  true, 1.0),
                       (200, 2, 2, 2000, false, 'master', true, 1.0),
                       (201, 2, 2, 2001, false, 'slave',  true, 1.0)
                """
            )
        return db

    @pytest.fixture
    def two_org_service(self, two_org_db, recording_dispatcher, clock):
        symbols = {"EURUSD": EURUSD, "GBPUSD": GBPUSD}
        routing = OrgRouting(
            org_by_account={100: 1, 101: 1, 200: 2, 201: 2},
            master_by_org={1: 100, 2: 200},
            slaves_by_org={
                1: [SlaveConfig(account_id=101, enabled=True,
                                multiplier=Decimal("1.0"), symbols=symbols)],
                2: [SlaveConfig(account_id=201, enabled=True,
                                multiplier=Decimal("1.0"), symbols=symbols)],
            },
        )
        return CopierService(
            repo=Repo(two_org_db),
            dispatcher=recording_dispatcher,
            routing_provider=lambda: routing,
            master_symbols_by_org={1: {1: EURUSD, 2: GBPUSD}, 2: {1: EURUSD, 2: GBPUSD}},
            clock=clock,
        )

    def test_master_event_from_org_a_never_reaches_org_b_slaves(
        self, two_org_service, recording_dispatcher
    ):
        """Two orgs in one routing table; a fill on org A's master produces
        intents only for org A's slaves and dispatch is called with org A's id."""
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        two_org_service.handle_execution(100, evt)

        assert len(recording_dispatcher.calls) == 1, "org A's master fill must dispatch exactly once"
        intents, org_id = recording_dispatcher.calls[0]
        assert org_id == 1, f"dispatched under org {org_id}, not org A"
        assert intents, "org A's master fill produced no intents at all"
        assert {i.slave_account_id for i in intents} == {101}, (
            f"an org A master event targeted accounts outside org A: "
            f"{sorted({i.slave_account_id for i in intents})}"
        )

        # The mirror case: org B's master reaches only org B, under org B's id.
        evt_b = base_event(account_id=200, execution_type=ProtoOAExecutionType.ORDER_FILLED,
                           order_id=6, position_id=22)
        evt_b.deal.positionId = 22
        evt_b.deal.filledVolume = 10_000_000

        two_org_service.handle_execution(200, evt_b)

        assert len(recording_dispatcher.calls) == 2
        intents_b, org_id_b = recording_dispatcher.calls[1]
        assert org_id_b == 2
        assert {i.slave_account_id for i in intents_b} == {201}

    def test_pending_fill_check_never_reports_another_orgs_slaves(
        self, two_org_service, two_org_db, clock
    ):
        """Master order/position ids are PER-ACCOUNT broker sequences, so two
        orgs routinely hold mapping rows for the same master_order_id.
        order_entries()/position_entries() are keyed on that id alone (they
        are the MappingState protocol decide() consumes, and must stay that
        way), so the 30s pending-fill check has to filter by org itself.

        Without that filter org A's scheduled check walks org B's mapping
        rows and writes a pending_fill_alert naming org B's slave account --
        stamped with org A's org_id, i.e. another tenant's account number
        surfacing in this tenant's Logs screen.
        """
        repo = Repo(two_org_db)
        # Both orgs have a copy of "master order 42" -- colliding ids, different orgs.
        repo.create_order_mapping(42, 101, "co42.101", org_id=1)
        repo.activate_order_mapping(101, "co42.101", 9999)
        repo.create_order_mapping(42, 201, "co42.201", org_id=2)
        repo.activate_order_mapping(201, "co42.201", 8888)

        # Org A's master pending order fills; neither slave has filled yet.
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED,
                         order_type=ProtoOAOrderType.LIMIT, order_id=42, position_id=11)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        two_org_service.handle_execution(100, evt)
        clock.advance(PENDING_FILL_ALERT_S)

        with psycopg.connect(two_org_db, autocommit=True) as conn:
            alerts = conn.execute(
                "SELECT account_id, org_id FROM events "
                "WHERE payload->>'action' = 'pending_fill_alert' ORDER BY account_id"
            ).fetchall()
        assert alerts == [(101, 1)], (
            f"org A's pending-fill check reported outside org A: {alerts}"
        )

    def test_slave_event_is_handled_under_its_own_org(
        self, two_org_service, recording_dispatcher, two_org_db
    ):
        """A slave event never crosses orgs either: it is processed for the
        org that owns the account, and its audit trail is stamped with it."""
        evt = base_event(account_id=201, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm11.201"  # matches nothing -> warning, no mutation
        evt.deal.positionId = 55
        evt.deal.filledVolume = 10_000_000

        two_org_service.handle_execution(201, evt)

        assert recording_dispatcher.calls == [], "slave events must never dispatch"
        with psycopg.connect(two_org_db, autocommit=True) as conn:
            orgs = conn.execute(
                "SELECT DISTINCT org_id FROM events WHERE account_id = 201"
            ).fetchall()
        assert orgs == [(2,)], f"org B's slave event was stamped {orgs}"


class TestSlaveEventHandling:
    """Test slave event handling: mapping updates, no dispatch."""

    def test_slave_events_never_dispatch(self, service, recording_dispatcher, repo):
        """Slave fill should update mapping but never call decide/dispatch."""
        # Create pending position mapping (as if master OpenMarket was already sent)
        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)

        # Now process a slave fill with matching clientOrderId
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm11.100"
        evt.deal.positionId = 55  # slave position ID
        evt.deal.filledVolume = 10_000_000

        # Patch decide to verify it's never called for slave events
        with patch('copier.engine.service.decide') as mock_decide:
            service.handle_execution(100, evt)
            # Verify decide was never called
            mock_decide.assert_not_called()

        # Dispatcher should NOT have been called with any replication intents
        dispatched_intents = recording_dispatcher.intents
        assert not any(isinstance(i, OpenMarket) for i in dispatched_intents)

    def test_slave_fill_activates_mapping(self, service, repo):
        """Slave fill with clientOrderId 'cm' prefix should activate position mapping."""
        # Setup: pending mapping
        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)

        # Process slave fill
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm11.100"
        evt.deal.positionId = 55
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(100, evt)

        # Mapping should be activated with slave position ID and volume
        entries = repo.position_entries(11)
        assert len(entries) == 1
        assert entries[0].slave_position_id == 55
        assert entries[0].slave_volume == 10_000_000
        assert entries[0].slave_account_id == 100

    def test_slave_close_reduces_mapping(self, service, repo):
        """Slave close with closePositionDetail should reduce position mapping."""
        # Setup: active position mapping
        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 10_000_000)

        # Process slave close (partial close of 4M out of 10M)
        evt = base_event(
            account_id=100,
            execution_type=ProtoOAExecutionType.ORDER_FILLED,
            position_id=55,
            volume=10_000_000
        )
        evt.deal.positionId = 55
        evt.deal.filledVolume = 4_000_000
        evt.deal.closePositionDetail.closedVolume = 4_000_000
        evt.position.tradeData.volume = 6_000_000  # remaining

        service.handle_execution(100, evt)

        # Mapping volume should be reduced
        entries = repo.position_entries(11)
        assert len(entries) == 1
        assert entries[0].slave_volume == 6_000_000

    def test_slave_pending_accept_then_fill_links_position(self, service, repo):
        """Slave ORDER_ACCEPTED 'co' order creates mapping; later fill links position."""
        # Setup: pending order mapping for master order 42
        repo.create_order_mapping(master_order_id=42, slave_account_id=100,
                                  client_order_id="co42.100", org_id=ORG_ID)

        # Step 1: ORDER_ACCEPTED activates order mapping
        evt_accept = base_event(
            account_id=100,
            execution_type=ProtoOAExecutionType.ORDER_ACCEPTED,
            order_type=ProtoOAOrderType.LIMIT,
            order_id=9999  # slave order ID
        )
        evt_accept.order.clientOrderId = "co42.100"

        service.handle_execution(100, evt_accept)

        # Order mapping should be activated
        order_entries = repo.order_entries(42)
        assert len(order_entries) == 1
        assert order_entries[0].slave_order_id == 9999

        # Step 2: Slave fill of pending order (ORDER_FILLED)
        # The fill event carries the slave_order_id (9999) which matches the order mapping
        evt_fill = base_event(
            account_id=100,
            execution_type=ProtoOAExecutionType.ORDER_FILLED,
            order_type=ProtoOAOrderType.LIMIT,
            order_id=9999,  # Must match the accepted order_id
            position_id=77  # the resulting slave position
        )
        evt_fill.deal.positionId = 77
        evt_fill.deal.filledVolume = 10_000_000

        service.handle_execution(100, evt_fill)

        # The order mapping should now be converted to a position mapping
        # (activate_pending_fill converts from order to position mapping)
        # Query the mapping row to verify the conversion
        rows = repo.mapping_rows()
        # Find the mapping by slave_order_id=9999 (the original order mapping)
        order_mapping_rows = [r for r in rows if r['slave_order_id'] == 9999]
        assert len(order_mapping_rows) >= 1, "Order mapping should exist after fill"

        # The mapping should now have slave_position_id set to 77
        mapping_row = order_mapping_rows[0]
        assert mapping_row['slave_position_id'] == 77, f"Expected slave_position_id=77, got {mapping_row['slave_position_id']}"

        # Verify events were logged
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'slave_action' AND account_id = 100"
            ).fetchone()
        assert events[0] >= 2  # At least accept + fill events

    def test_slave_order_cancelled_closes_mapping(self, service, repo):
        """Slave ORDER_CANCELLED on mapped order closes order mapping."""
        # Setup: active order mapping with slave order ID
        repo.create_order_mapping(master_order_id=42, slave_account_id=100,
                                  client_order_id="co42.100", org_id=ORG_ID)
        repo.activate_order_mapping(100, "co42.100", 9999)

        # Process slave cancel
        evt = base_event(
            account_id=100,
            execution_type=ProtoOAExecutionType.ORDER_CANCELLED,
            order_type=ProtoOAOrderType.LIMIT,
            order_id=9999
        )

        service.handle_execution(100, evt)

        # Order mapping should be closed (not active anymore)
        # Verify by trying to update it should fail
        try:
            repo.close_order_mapping(100, 9999)
            # If this succeeds, the mapping wasn't closed yet
            assert False, "Mapping should have been closed"
        except MappingNotFound:
            # Expected: mapping already closed
            pass

    def test_slave_rejection_fails_mapping_and_alerts(self, service, repo):
        """Slave ORDER_REJECTED should fail mapping and log alert."""
        # Setup: pending order mapping
        repo.create_order_mapping(master_order_id=42, slave_account_id=100,
                                  client_order_id="co42.100", org_id=ORG_ID)

        # Process slave rejection
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_REJECTED)
        evt.order.clientOrderId = "co42.100"
        evt.errorCode = "123"  # Broker error code (string)

        service.handle_execution(100, evt)

        # Mapping should be marked as failed
        rows = repo.mapping_rows()
        co42_mapping = [r for r in rows if r['client_order_id'] == "co42.100"]
        assert len(co42_mapping) == 1
        assert co42_mapping[0]['status'] == 'failed'


class TestPendingFillAlert:
    """Test pending fill alert scheduling after 30 seconds."""

    def test_pending_fill_check_alerts_after_30s(self, service, repo, clock):
        """Master MasterPendingFilled should schedule check; after 30s alert if slave fills are unmapped.

        Negative case: slave order DID fill in time → NO alert
        Positive case: slave order not yet filled → alert after 30s

        This test verifies that the check_pending_fills logic correctly distinguishes
        between slaves that filled in time (have linked position) vs. those still waiting.
        """
        # Setup: master pending order with two slaves
        master_order_id = 42
        master_position_id = 11

        repo.create_order_mapping(master_order_id=master_order_id, slave_account_id=100,
                                  client_order_id="co42.100", org_id=ORG_ID)
        repo.activate_order_mapping(100, "co42.100", 9999)  # slave order accepted for account 100
        repo.create_order_mapping(master_order_id=master_order_id, slave_account_id=101,
                                  client_order_id="co42.101", org_id=ORG_ID)
        repo.activate_order_mapping(101, "co42.101", 8888)  # slave order accepted for account 101

        # Master pending order fills (triggers check_pending_fills scheduling)
        evt = base_event(
            account_id=999,
            execution_type=ProtoOAExecutionType.ORDER_FILLED,
            order_type=ProtoOAOrderType.LIMIT,
            order_id=master_order_id,
            position_id=master_position_id
        )
        evt.deal.positionId = master_position_id
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(999, evt)

        # Simulate slave 100 fills in time: link the pending fill to a position
        # This requires both link_pending_fill (stamp master_position_id on order mapping)
        # AND activate_pending_fill (convert order mapping to position mapping)
        repo.link_pending_fill(master_order_id, 100, master_position_id)
        repo.activate_pending_fill(100, 9999, 100, 10_000_000)  # slave 100 position 100

        # Slave 101 order is NOT filled (no link_pending_fill, no activate_pending_fill)
        # So when check_pending_fills runs, it will find no position_entries for 101

        # Advance time by 30 seconds (triggers check_pending_fills callback)
        clock.advance(PENDING_FILL_ALERT_S)

        # Verify POSITIVE case: warning logged for slave 101 (not filled)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            warnings_101 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'slave_action' AND severity = 'warning' "
                "AND account_id = 101"
            ).fetchone()
        assert warnings_101[0] >= 1, "Should alert for slave 101 which didn't fill in time"

        # Verify NEGATIVE case: NO warning for slave 100 (did fill in time)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            warnings_100 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'slave_action' AND severity = 'warning' "
                "AND account_id = 100 AND payload->>'action' = 'pending_fill_alert'"
            ).fetchone()
        assert warnings_100[0] == 0, "Should NOT alert for slave 100 which filled in time"


class TestMappingMutationsArePinnedToTheReportingAccount:
    """A fill/accept/rejection may only move the mapping row belonging to the
    account that reported it.

    client_order_id is derived and guessable
    (`cm{master_position_id}.{slave_account_id}`), and master position ids are
    per-account broker sequences, so the coid alone is not proof of ownership.
    These paths write real-money sizing state, so the service passes the
    reporting account and the repo pins the UPDATE to it.
    """

    def test_fill_reported_by_the_wrong_slave_does_not_activate_the_mapping(
        self, service, repo
    ):
        """Slave 101 reports a fill carrying slave 100's client_order_id: 100's
        mapping must stay pending, and 101 gets the unknown-fill warning."""
        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)

        evt = base_event(account_id=101, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm11.100"  # not 101's coid
        evt.deal.positionId = 55
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(101, evt)

        assert repo.position_entries(11) == [], (
            "slave 101's fill activated slave 100's mapping"
        )
        row = next(r for r in repo.mapping_rows() if r['client_order_id'] == "cm11.100")
        assert row['status'] == 'pending'
        assert row['slave_position_id'] is None
        assert row['slave_volume'] is None

        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            actions = {
                r[0] for r in conn.execute(
                    "SELECT payload->>'action' FROM events WHERE account_id = 101"
                ).fetchall()
            }
        assert 'unknown_fill' in actions

    def test_rejection_from_the_wrong_slave_does_not_fail_the_mapping(self, service, repo):
        """Slave 101's rejection must not mark slave 100's copy failed."""
        repo.create_order_mapping(master_order_id=42, slave_account_id=100,
                                  client_order_id="co42.100", org_id=ORG_ID)

        evt = base_event(account_id=101, execution_type=ProtoOAExecutionType.ORDER_REJECTED)
        evt.order.clientOrderId = "co42.100"
        evt.errorCode = "123"

        service.handle_execution(101, evt)   # must not raise

        row = next(r for r in repo.mapping_rows() if r['client_order_id'] == "co42.100")
        assert row['status'] == 'pending', "another slave's rejection failed this copy"

    def test_order_accept_from_the_wrong_slave_does_not_stamp_the_mapping(
        self, service, repo
    ):
        """Slave 101's ORDER_ACCEPTED must not write its slave_order_id onto
        slave 100's row -- (slave_account_id, slave_order_id) lookups later
        depend on that pairing being real."""
        repo.create_order_mapping(master_order_id=42, slave_account_id=100,
                                  client_order_id="co42.100", org_id=ORG_ID)

        evt = base_event(account_id=101, execution_type=ProtoOAExecutionType.ORDER_ACCEPTED,
                         order_type=ProtoOAOrderType.LIMIT, order_id=9999)
        evt.order.clientOrderId = "co42.100"

        service.handle_execution(101, evt)

        assert repo.order_entries(42) == [], "another slave's accept activated this copy"
        row = next(r for r in repo.mapping_rows() if r['client_order_id'] == "co42.100")
        assert row['slave_order_id'] is None


class TestMasterEventLogging:
    """Test that master events are always audit-logged with latency."""

    def test_master_event_is_always_audit_logged(self, service, repo):
        """Master events should always log with latency_ms."""
        # Create a simple master fill event
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(999, evt)

        # Query events table for master_event entries
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            events = conn.execute(
                "SELECT category, severity, payload, latency_ms FROM events WHERE category = 'master_event' ORDER BY id DESC LIMIT 1"
            ).fetchall()

        assert len(events) >= 1
        event = events[0]
        category, severity, payload, latency_ms = event
        assert category == 'master_event'
        assert severity == 'info'
        assert latency_ms is not None
        assert latency_ms >= 0


class TestDisabledAndIgnoredAccounts:
    """Test handling of disabled and non-master/non-slave accounts."""

    def test_disabled_slave_event_is_logged_and_ignored(self, service, repo,
                                                        routing_box, make_routing):
        """Event from disabled slave or unknown account should be logged but NOT mutate mappings."""
        # Swap in a routing table where slave 101 is disabled, to test gating
        routing_box["routing"] = make_routing(
            master=999,
            slaves=[
                SlaveConfig(account_id=100, enabled=True, multiplier=Decimal("1.0"),
                            symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
                SlaveConfig(account_id=101, enabled=False, multiplier=Decimal("1.5"),  # DISABLED
                            symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
            ],
            org_id=ORG_ID,
        )

        # Test 1: disabled slave 101 with a guessed clientOrderId
        evt = base_event(account_id=101, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm11.101"  # Attempt to activate mapping
        evt.deal.positionId = 55
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(101, evt)

        # Verify: event WAS logged as ignored (drift category)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            drift_events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'drift' AND account_id = 101"
            ).fetchone()
        assert drift_events[0] >= 1, "Disabled slave event should be logged as drift"

        # Verify: NO mapping was created (mapping mutation was prevented)
        rows = repo.mapping_rows()
        cm11_mappings = [r for r in rows if r['client_order_id'] == "cm11.101"]
        assert len(cm11_mappings) == 0, "Disabled slave should not create mappings"

        # Test 2: completely unknown account (not 999 which is master, not 100/101 which are configured)
        # Use account_id=777, which belongs to no org in the routing table
        evt_unknown = base_event(account_id=777, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt_unknown.order.clientOrderId = "cm22.777"  # Attempt to activate mapping
        evt_unknown.deal.positionId = 77
        evt_unknown.deal.filledVolume = 5_000_000

        service.handle_execution(777, evt_unknown)

        # Verify: event WAS logged as ignored (drift category)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            drift_events_777 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'drift' AND account_id = 777"
            ).fetchone()
        assert drift_events_777[0] >= 1, "Unknown account event should be logged as drift"

        # Verify: NO mapping was created for unknown account
        rows = repo.mapping_rows()
        cm22_mappings = [r for r in rows if r['client_order_id'] == "cm22.777"]
        assert len(cm22_mappings) == 0, "Unknown account should not create mappings"


class TestSlaveEventEdgeCases:
    """Test edge cases for slave event processing."""

    def test_unknown_client_order_id_logs_warning_no_crash(self, service, repo):
        """Slave fill with unknown clientOrderId should log warning and not crash."""
        # Process slave fill with unknown clientOrderId
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm9999.100"  # Unknown mapping
        evt.deal.positionId = 999
        evt.deal.filledVolume = 10_000_000

        # Should not raise exception
        service.handle_execution(100, evt)

        # Warning should be logged
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            warnings = conn.execute(
                "SELECT COUNT(*) FROM events WHERE severity = 'warning'"
            ).fetchone()
        assert warnings[0] >= 1


    def test_manual_slave_fill_logs_info_not_warning(self, service, repo):
        """A fill for an operator-placed manual order (label 'manual') on a
        slave is expected -- it logs an info 'manual_fill', never the
        unmatched_slave_fill warning that flags genuinely unexplained fills."""
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.tradeData.label = "manual"
        evt.deal.positionId = 888
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(100, evt)

        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT severity, payload->>'action' FROM events WHERE account_id = 100"
            ).fetchall()
        actions = {r[1] for r in rows}
        assert 'manual_fill' in actions
        assert 'unmatched_slave_fill' not in actions
        assert all(r[0] != 'warning' for r in rows)


class TestExceptionBoundary:
    """Test exception handling in handle_execution."""

    def test_log_event_failure_does_not_crash_pump(self, service):
        """If log_event raises, handle_execution catches it, logs if possible, and returns.

        The event pump never crashes; the next event is processed normally.
        """
        # Inject a repo that raises on log_event
        failing_repo = Mock()
        failing_repo.log_event.side_effect = RuntimeError("DB connection lost")

        service._repo = failing_repo

        # Create a master event
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        # Should NOT raise; exception caught by exception boundary
        service.handle_execution(999, evt)

        # Verify that the failing log_event was called (the error happened)
        assert failing_repo.log_event.called

    def test_normalize_failure_caught_and_logged(self, service, repo):
        """If normalize raises, handle_execution catches it and logs.

        Test by patching normalize to raise an exception.
        """
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        # Patch normalize to raise
        with patch('copier.engine.service.normalize') as mock_normalize:
            mock_normalize.side_effect = ValueError("Invalid symbol")

            # Should NOT raise
            service.handle_execution(999, evt)

            # Verify normalize was called
            assert mock_normalize.called

        # Verify error was logged to repo
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            errors = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'connection' AND severity = 'error'"
            ).fetchone()
        # The error should be logged
        assert errors[0] >= 1


class TestPositionsChangedNotification:
    """on_positions_changed fires for events that change positions, orders,
    or mappings, so /state refreshes within ~1s (via request_resync)
    instead of on the next periodic resync tick."""

    def test_master_fill_notifies(self, service, recording_dispatcher):
        calls = []
        service.on_positions_changed = lambda org_id=None: calls.append(org_id)
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(999, evt)

        # The notification names the org, so the resync stays org-scoped.
        assert calls == [ORG_ID]

    def test_non_replication_master_event_does_not_notify(self, service):
        """MARKET ORDER_ACCEPTED normalizes to None -- nothing changed,
        nothing to refresh."""
        calls = []
        service.on_positions_changed = lambda: calls.append(1)
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_ACCEPTED)

        service.handle_execution(999, evt)

        assert calls == []

    def test_slave_fill_notifies(self, service):
        calls = []
        service.on_positions_changed = lambda org_id=None: calls.append(org_id)
        evt = base_event(account_id=100, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 500
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(100, evt)

        assert calls == [ORG_ID]

    def test_callback_failure_does_not_break_event_processing(self, service, recording_dispatcher):
        def boom():
            raise RuntimeError("boom")

        service.on_positions_changed = boom
        evt = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(999, evt)   # must not raise

        open_intents = [i for i in recording_dispatcher.intents if isinstance(i, OpenMarket)]
        assert len(open_intents) == 2


class TestMasterEventLatencyStamp:
    """latency_ms on the master_event audit row must measure the whole
    internal copy path -- normalize, decide, and the dispatch handoff -- and
    the audit write itself must come AFTER the send handoff, never sit in
    front of it as a blocking database write."""

    def _service_with(self, dispatch_side_effect, repo, clock, routing_box):
        dispatcher = Mock()
        dispatcher.dispatch = Mock(side_effect=dispatch_side_effect)
        service = CopierService(
            repo=repo,
            dispatcher=dispatcher,
            routing_provider=lambda: routing_box["routing"],
            master_symbols_by_org={ORG_ID: {1: EURUSD, 2: GBPUSD}},
            clock=clock,
        )
        return service, dispatcher

    def test_audit_write_happens_after_the_dispatch_handoff(
        self, db_seeded, repo, clock, routing_box
    ):
        rows_at_dispatch = {}

        def probing_dispatch(intents, org_id):
            with psycopg.connect(db_seeded, autocommit=True) as conn:
                (n,) = conn.execute(
                    "SELECT count(*) FROM events WHERE category = 'master_event'"
                ).fetchone()
            rows_at_dispatch["n"] = n

        service, dispatcher = self._service_with(
            probing_dispatch, repo, clock, routing_box)
        service.handle_execution(999, base_event())

        assert dispatcher.dispatch.called
        assert rows_at_dispatch["n"] == 0, (
            "master_event was written before dispatch -- the audit INSERT "
            "is blocking the copy handoff")
        with psycopg.connect(db_seeded, autocommit=True) as conn:
            row = conn.execute(
                "SELECT latency_ms FROM events WHERE category = 'master_event'"
            ).fetchone()
        assert row is not None and row[0] is not None and row[0] >= 0

    def test_audit_row_still_lands_when_dispatch_raises(
        self, db_seeded, repo, clock, routing_box
    ):
        def exploding_dispatch(intents, org_id):
            raise RuntimeError("wire fell over")

        service, _ = self._service_with(
            exploding_dispatch, repo, clock, routing_box)
        service.handle_execution(999, base_event())  # must not raise

        with psycopg.connect(db_seeded, autocommit=True) as conn:
            (n,) = conn.execute(
                "SELECT count(*) FROM events WHERE category = 'master_event'"
            ).fetchone()
        assert n == 1


def sltp_event(position_id=11, stop_loss=None, take_profit=None,
               execution_type=ProtoOAExecutionType.ORDER_ACCEPTED):
    """The separate protection event cTrader sends AFTER a fill."""
    e = base_event(account_id=999, execution_type=execution_type,
                   order_type=ProtoOAOrderType.STOP_LOSS_TAKE_PROFIT,
                   position_id=position_id)
    if stop_loss is not None:
        e.position.stopLoss = stop_loss
    if take_profit is not None:
        e.position.takeProfit = take_profit
    return e


def amends(dispatcher):
    from copier.domain.models import AmendPositionSLTP
    return [i for i in dispatcher.intents if isinstance(i, AmendPositionSLTP)]


class TestCopiesInheritMasterProtection:
    """A copy must not fill naked when its master carries a stop.

    cTrader protects a market order in two steps: ORDER_FILLED arrives with
    no stopLoss on the position, then a STOP_LOSS_TAKE_PROFIT order is
    accepted ~25ms later. The copies take ~250ms to fill. So the fan-out
    for that second event runs while every copy is still an unfilled order
    -- position_entries() sees no slave_position_id, logs "slave has no
    mapped copy", and the copies then fill unprotected and stay that way.

    Seen in production: master holding 4582.79 / 4584.79 with all ten
    copies showing no stop and no target.
    """

    def test_a_copy_filling_after_the_master_was_protected_still_gets_it(
            self, service, recording_dispatcher, repo):
        # 1. Master fills. Its own event carries NO protection yet -- this
        #    is the detail that makes the race possible.
        opened = base_event(account_id=999, position_id=11)
        opened.deal.positionId = 11
        opened.deal.filledVolume = 10_000_000
        service.handle_execution(999, opened)

        # 2. The copy order is on the wire but unfilled: a mapping exists
        #    with no slave_position_id, which is what hides it from the
        #    SL/TP fan-out.
        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)

        # 3. The master is protected. Nothing to amend yet -- and before
        #    this fix, nothing ever would be.
        service.handle_execution(999, sltp_event(11, stop_loss=1.0900,
                                                 take_profit=1.1100))
        assert amends(recording_dispatcher) == []

        # 4. The copy fills, a quarter of a second later.
        fill = base_event(account_id=100, position_id=11)
        fill.order.clientOrderId = "cm11.100"
        fill.deal.positionId = 55
        fill.deal.filledVolume = 10_000_000
        service.handle_execution(100, fill)

        applied = amends(recording_dispatcher)
        assert len(applied) == 1, "the copy filled without its master's protection"
        assert applied[0].slave_account_id == 100
        assert applied[0].position_id == 55
        assert applied[0].stop_loss == pytest.approx(1.0900)
        assert applied[0].take_profit == pytest.approx(1.1100)

    def test_an_unprotected_master_costs_no_extra_request(
            self, service, recording_dispatcher, repo):
        """Most trades carry no protection. They must not pay for this."""
        opened = base_event(account_id=999, position_id=11)
        opened.deal.positionId = 11
        opened.deal.filledVolume = 10_000_000
        service.handle_execution(999, opened)

        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)
        fill = base_event(account_id=100, position_id=11)
        fill.order.clientOrderId = "cm11.100"
        fill.deal.positionId = 55
        fill.deal.filledVolume = 10_000_000
        service.handle_execution(100, fill)

        assert amends(recording_dispatcher) == []

    def test_clearing_the_masters_protection_is_remembered_too(
            self, service, recording_dispatcher, repo):
        """A copy filling later must not be handed a stop the master has
        since removed."""
        service.handle_execution(999, sltp_event(11, stop_loss=1.0900))
        service.handle_execution(999, sltp_event(
            11, execution_type=ProtoOAExecutionType.ORDER_REPLACED))

        repo.create_position_mapping(master_position_id=11, slave_account_id=100,
                                     client_order_id="cm11.100", org_id=ORG_ID)
        fill = base_event(account_id=100, position_id=11)
        fill.order.clientOrderId = "cm11.100"
        fill.deal.positionId = 55
        fill.deal.filledVolume = 10_000_000
        service.handle_execution(100, fill)

        assert amends(recording_dispatcher) == []

    def test_each_copy_of_the_same_master_is_protected(
            self, service, recording_dispatcher, repo):
        """The fleet is the point: every copy, not just the first back."""
        service.handle_execution(999, sltp_event(11, stop_loss=1.0900,
                                                 take_profit=1.1100))
        for account_id, slave_position_id in ((100, 55), (101, 56)):
            repo.create_position_mapping(
                master_position_id=11, slave_account_id=account_id,
                client_order_id=f"cm11.{account_id}", org_id=ORG_ID)
            fill = base_event(account_id=account_id, position_id=11)
            fill.order.clientOrderId = f"cm11.{account_id}"
            fill.deal.positionId = slave_position_id
            fill.deal.filledVolume = 10_000_000
            service.handle_execution(account_id, fill)

        applied = amends(recording_dispatcher)
        assert {(a.slave_account_id, a.position_id) for a in applied} == {
            (100, 55), (101, 56)}


class TestRememberedProtectionDoesNotLeak:
    def test_a_closed_master_position_is_forgotten(self, service):
        service.handle_execution(999, sltp_event(11, stop_loss=1.0900))
        assert 11 in service._master_protection

        closed = base_event(account_id=999, position_id=11)
        closed.deal.positionId = 11
        closed.deal.filledVolume = 10_000_000
        closed.deal.closePositionDetail.entryPrice = 1.1000
        closed.deal.closePositionDetail.closedVolume = 10_000_000
        service.handle_execution(999, closed)

        assert 11 not in service._master_protection

    def test_the_table_is_bounded(self, service):
        """A close we never see must not accumulate for the life of the
        process -- a reconnect mid-trade is enough to lose one."""
        from copier.engine.service import MAX_REMEMBERED_PROTECTION
        for position_id in range(1, MAX_REMEMBERED_PROTECTION + 51):
            service.handle_execution(
                999, sltp_event(position_id, stop_loss=1.0900))
        assert len(service._master_protection) <= MAX_REMEMBERED_PROTECTION


def test_master_position_of_reads_the_copy_marker():
    from copier.engine.service import master_position_of
    assert master_position_of("cm665938284.48434542") == 665938284
    assert master_position_of("co77.100") is None      # a pending-order copy
    assert master_position_of("manual") is None
    assert master_position_of(None) is None
    assert master_position_of("cmnotanumber.1") is None
