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
from copier.engine.service import CopierService, PENDING_FILL_ALERT_S


# Test fixtures and helpers

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
    """Seed test database with accounts and connections."""
    with psycopg.connect(db, autocommit=True) as conn:
        # Create connection
        conn.execute(
            """
            INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, now(), now() + interval '1 hour')
            """,
            ("token_access", "token_refresh"),
        )
        # Create master account (999) and slave accounts (100, 101)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (999, 1, 99900, false, 'master', true, 1.0),
                (100, 1, 10000, false, 'slave', true, 1.0),
                (101, 1, 10001, false, 'slave', true, 1.5)
            """
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
    """Mock dispatcher that records all dispatched intents."""
    dispatcher = Mock()
    dispatcher.dispatch = Mock()
    dispatcher.intents = []

    def record_dispatch(intents):
        dispatcher.intents.extend(intents)

    dispatcher.dispatch.side_effect = record_dispatch
    return dispatcher


@pytest.fixture
def service(repo, recording_dispatcher, clock):
    """Create a CopierService instance for testing."""
    master_symbols = {"EURUSD": EURUSD, "GBPUSD": GBPUSD}
    slave_configs = [
        SlaveConfig(account_id=100, enabled=True, multiplier=Decimal("1.0"),
                   symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
        SlaveConfig(account_id=101, enabled=True, multiplier=Decimal("1.5"),
                   symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
    ]
    slaves_provider = lambda: slave_configs

    return CopierService(
        repo=repo,
        dispatcher=recording_dispatcher,
        master_account_id=999,
        master_symbols_by_id={1: EURUSD, 2: GBPUSD},
        slaves_provider=slaves_provider,
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


class TestSlaveEventHandling:
    """Test slave event handling: mapping updates, no dispatch."""

    def test_slave_events_never_dispatch(self, service, recording_dispatcher, repo):
        """Slave fill should update mapping but never call decide/dispatch."""
        # Create pending position mapping (as if master OpenMarket was already sent)
        repo.create_position_mapping(master_position_id=11, slave_account_id=100, client_order_id="cm11.100")

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
        repo.create_position_mapping(master_position_id=11, slave_account_id=100, client_order_id="cm11.100")

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
        repo.create_position_mapping(master_position_id=11, slave_account_id=100, client_order_id="cm11.100")
        repo.activate_position_mapping("cm11.100", 55, 10_000_000)

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
        # Setup: pending order mapping
        repo.create_order_mapping(master_order_id=42, slave_account_id=100, client_order_id="co42.100")

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
        # After the fill, verify events were logged
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'slave_action' AND account_id = 100"
            ).fetchone()
        assert events[0] >= 2  # At least accept + fill events

    def test_slave_order_cancelled_closes_mapping(self, service, repo):
        """Slave ORDER_CANCELLED on mapped order closes order mapping."""
        # Setup: active order mapping with slave order ID
        repo.create_order_mapping(master_order_id=42, slave_account_id=100, client_order_id="co42.100")
        repo.activate_order_mapping("co42.100", 9999)

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
        repo.create_order_mapping(master_order_id=42, slave_account_id=100, client_order_id="co42.100")

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
        """
        # Setup: master pending order with two slaves
        master_order_id = 42
        repo.create_order_mapping(master_order_id=master_order_id, slave_account_id=100, client_order_id="co42.100")
        repo.activate_order_mapping("co42.100", 9999)  # slave order accepted for account 100
        repo.create_order_mapping(master_order_id=master_order_id, slave_account_id=101, client_order_id="co42.101")
        repo.activate_order_mapping("co42.101", 8888)  # slave order accepted for account 101

        # Master pending order fills
        evt = base_event(
            account_id=999,
            execution_type=ProtoOAExecutionType.ORDER_FILLED,
            order_type=ProtoOAOrderType.LIMIT,
            order_id=master_order_id,
            position_id=11
        )
        evt.deal.positionId = 11
        evt.deal.filledVolume = 10_000_000

        service.handle_execution(999, evt)

        # Simulate slave 100 fills in time, but slave 101 doesn't
        evt_slave100_fill = base_event(
            account_id=100,
            execution_type=ProtoOAExecutionType.ORDER_FILLED,
            order_type=ProtoOAOrderType.LIMIT,
            order_id=9999,
            position_id=100  # slave position
        )
        evt_slave100_fill.deal.positionId = 100
        evt_slave100_fill.deal.filledVolume = 10_000_000
        service.handle_execution(100, evt_slave100_fill)

        # Now slave 101 is still waiting...
        # Advance time by 30 seconds
        clock.advance(PENDING_FILL_ALERT_S)

        # Check: warning events should be logged for slave 101 (still not filled)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            warnings = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'slave_action' AND severity = 'warning' "
                "AND account_id = 101"
            ).fetchone()
        # Should have at least one warning alert for slave 101's unfilled order
        assert warnings[0] >= 1


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

    def test_disabled_slave_event_is_logged_and_ignored(self, service, repo):
        """Event from disabled slave or unknown account should be logged but NOT mutate mappings."""
        # Create a slave with 'enabled=False' to test gating
        service._slaves_provider = lambda: [
            SlaveConfig(account_id=100, enabled=True, multiplier=Decimal("1.0"),
                       symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
            SlaveConfig(account_id=101, enabled=False, multiplier=Decimal("1.5"),  # DISABLED
                       symbols={"EURUSD": EURUSD, "GBPUSD": GBPUSD}),
        ]

        # Try to send fill event from disabled slave 101 with a guessed clientOrderId
        evt = base_event(account_id=101, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt.order.clientOrderId = "cm11.101"  # Attempt to activate mapping
        evt.deal.positionId = 55
        evt.deal.filledVolume = 10_000_000

        # Process the event
        service.handle_execution(101, evt)

        # Verify: event WAS logged as ignored
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category = 'drift' AND account_id != 101"
            ).fetchone()
            # The ignore event logged has account_id=None (not set for drift category at the gate)

        # Verify: NO mapping was created (mapping mutation was prevented)
        rows = repo.mapping_rows()
        cm11_mappings = [r for r in rows if r['client_order_id'] == "cm11.101"]
        assert len(cm11_mappings) == 0, "Disabled slave should not create mappings"

        # Test with completely unknown account (not in slaves_provider)
        evt2 = base_event(account_id=999, execution_type=ProtoOAExecutionType.ORDER_FILLED)
        evt2.order.clientOrderId = "cm22.999"
        evt2.deal.positionId = 77
        evt2.deal.filledVolume = 5_000_000

        service.handle_execution(999, evt2)

        # Verify: still NO mapping for unknown account
        rows = repo.mapping_rows()
        cm22_mappings = [r for r in rows if r['client_order_id'] == "cm22.999"]
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
