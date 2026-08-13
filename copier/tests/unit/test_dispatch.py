"""Tests for the intent dispatcher (dispatch.py)."""

from unittest.mock import Mock, MagicMock
from decimal import Decimal

import pytest
import psycopg
from twisted.internet.task import Clock
from twisted.internet import defer

from copier.domain.models import (
    Side, PendingType, OpenMarket, ClosePosition, AmendPositionSLTP,
    PlacePending, AmendPending, CancelPending, LinkPendingFill, Alert
)
from copier.engine.dispatch import (
    RETRY_DELAYS, client_order_id_for, build_request, Dispatcher, SendNotAttempted
)
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOANewOrderReq, ProtoOAClosePositionReq, ProtoOAAmendPositionSLTPReq,
    ProtoOAAmendOrderReq, ProtoOACancelOrderReq
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType, ProtoOATradeSide
)


class TestRetryDelays:
    """Test RETRY_DELAYS constant."""
    def test_retry_delays_correct(self):
        assert RETRY_DELAYS == (1.0, 2.0, 4.0)


class TestClientOrderIdFor:
    """Test client_order_id_for function."""

    def test_open_market_generates_correct_id(self):
        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=1,
            side=Side.BUY,
            volume=1000,
            stop_loss=None,
            take_profit=None,
            label="test"
        )
        coid = client_order_id_for(intent)
        assert coid == "cm42.101"
        assert len(coid) <= 50

    def test_place_pending_generates_correct_id(self):
        intent = PlacePending(
            slave_account_id=101,
            master_order_id=99,
            symbol_id=1,
            side=Side.SELL,
            order_type=PendingType.LIMIT,
            volume=2000,
            price=1.1,
            stop_loss=None,
            take_profit=None,
            expiry_ts_ms=None,
            label="test"
        )
        coid = client_order_id_for(intent)
        assert coid == "co99.101"
        assert len(coid) <= 50

    def test_non_mapping_intent_returns_none(self):
        intent = Alert(slave_account_id=None, message="test")
        assert client_order_id_for(intent) is None

        intent = ClosePosition(slave_account_id=101, position_id=5, volume=1000)
        assert client_order_id_for(intent) is None


class TestBuildRequest:
    """Test build_request function with protobuf assertions."""

    def test_build_open_market_request(self):
        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=1.05,
            take_profit=1.15,
            label="copy:m42"
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOANewOrderReq)
        assert req.ctidTraderAccountId == 101
        assert req.symbolId == 100
        assert req.orderType == ProtoOAOrderType.MARKET
        assert req.tradeSide == ProtoOATradeSide.BUY
        assert req.volume == 50000
        assert req.stopLoss == 1.05
        assert req.takeProfit == 1.15
        assert req.label == "copy:m42"
        assert req.clientOrderId == "cm42.101"
        # Verify message serializes
        req.SerializeToString()

    def test_build_open_market_sell_without_sl_tp(self):
        intent = OpenMarket(
            slave_account_id=102,
            master_position_id=43,
            symbol_id=200,
            side=Side.SELL,
            volume=25000,
            stop_loss=None,
            take_profit=None,
            label="copy:m43"
        )
        account_id, req = build_request(intent)

        assert account_id == 102
        assert req.orderType == ProtoOAOrderType.MARKET
        assert req.tradeSide == ProtoOATradeSide.SELL
        assert req.volume == 25000
        assert req.stopLoss == 0.0
        assert req.takeProfit == 0.0
        # Verify message serializes
        req.SerializeToString()

    def test_build_close_position_request(self):
        intent = ClosePosition(
            slave_account_id=101,
            position_id=500,
            volume=40000
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOAClosePositionReq)
        assert req.ctidTraderAccountId == 101
        assert req.positionId == 500
        assert req.volume == 40000
        # Verify message serializes
        req.SerializeToString()

    def test_build_amend_position_sltp_request(self):
        intent = AmendPositionSLTP(
            slave_account_id=101,
            position_id=501,
            stop_loss=1.10,
            take_profit=1.20
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOAAmendPositionSLTPReq)
        assert req.ctidTraderAccountId == 101
        assert req.positionId == 501
        assert req.stopLoss == 1.10
        assert req.takeProfit == 1.20
        # Verify message serializes
        req.SerializeToString()

    def test_build_amend_position_sltp_partial_fields(self):
        # Only stopLoss set
        intent = AmendPositionSLTP(
            slave_account_id=101,
            position_id=502,
            stop_loss=1.08,
            take_profit=None
        )
        account_id, req = build_request(intent)

        assert req.stopLoss == 1.08
        assert req.takeProfit == 0.0  # default for unset float

    def test_build_place_pending_limit(self):
        intent = PlacePending(
            slave_account_id=101,
            master_order_id=99,
            symbol_id=100,
            side=Side.BUY,
            order_type=PendingType.LIMIT,
            volume=10000,
            price=1.12,
            stop_loss=1.10,
            take_profit=1.20,
            expiry_ts_ms=1692460800000,
            label="copy:o99"
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOANewOrderReq)
        assert req.orderType == ProtoOAOrderType.LIMIT
        assert req.limitPrice == 1.12
        assert req.volume == 10000
        assert req.stopLoss == 1.10
        assert req.takeProfit == 1.20
        assert req.expirationTimestamp == 1692460800000
        assert req.clientOrderId == "co99.101"
        # Verify message serializes
        req.SerializeToString()

    def test_build_place_pending_stop(self):
        intent = PlacePending(
            slave_account_id=101,
            master_order_id=100,
            symbol_id=100,
            side=Side.SELL,
            order_type=PendingType.STOP,
            volume=5000,
            price=1.08,
            stop_loss=None,
            take_profit=None,
            expiry_ts_ms=None,
            label="copy:o100"
        )
        account_id, req = build_request(intent)

        assert req.orderType == ProtoOAOrderType.STOP
        assert req.stopPrice == 1.08
        assert req.volume == 5000

    def test_build_amend_pending_request(self):
        intent = AmendPending(
            slave_account_id=101,
            order_id=10,
            order_type=PendingType.LIMIT,
            volume=8000,
            price=1.11,
            stop_loss=1.09,
            take_profit=1.19
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOAAmendOrderReq)
        assert req.ctidTraderAccountId == 101
        assert req.orderId == 10
        assert req.volume == 8000
        assert req.limitPrice == 1.11
        assert req.stopLoss == 1.09
        assert req.takeProfit == 1.19
        # Verify message serializes
        req.SerializeToString()

    def test_build_amend_pending_stop(self):
        intent = AmendPending(
            slave_account_id=101,
            order_id=11,
            order_type=PendingType.STOP,
            volume=6000,
            price=1.07,
            stop_loss=None,
            take_profit=None
        )
        account_id, req = build_request(intent)

        assert req.stopPrice == 1.07
        assert req.volume == 6000

    def test_build_cancel_pending_request(self):
        intent = CancelPending(
            slave_account_id=101,
            order_id=20
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOACancelOrderReq)
        assert req.ctidTraderAccountId == 101
        assert req.orderId == 20
        # Verify message serializes
        req.SerializeToString()


@pytest.fixture
def repo(db):
    """Create a Repo instance."""
    from copier.db.repo import Repo
    return Repo(db)


@pytest.fixture
def seed_accounts(db):
    """Seed test accounts."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, now(), now() + interval '1 hour')
            """,
            ("token_access", "token_refresh"),
        )
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (101, 1, 10001, false, 'slave', true, 1.5),
                (102, 1, 10002, false, 'slave', true, 2.0)
            """
        )
    return db


class TestDispatcher:
    """Tests for Dispatcher class."""

    def test_alert_logs_and_sends_nothing(self, seed_accounts, repo):
        """Alert intents should log an event and not send."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        clock = Clock()
        bucket = Mock()
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = Alert(slave_account_id=101, message="Test alert message")
        dispatcher.dispatch([intent])

        # Verify no messages sent
        assert len(sent_messages) == 0

        # Verify event was logged
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE category = 'slave_action' AND severity = 'warning'"
            ).fetchall()
        assert len(rows) > 0
        assert rows[0][0]['message'] == "Test alert message"

    def test_link_pending_fill_updates_repo_and_logs(self, seed_accounts, repo):
        """LinkPendingFill should call repo.link_pending_fill and log."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        clock = Clock()
        bucket = Mock()
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        # Create an order mapping and activate it
        repo.create_order_mapping(50, 101, "co50.101")
        repo.activate_order_mapping("co50.101", 999)  # slave_order_id=999

        intent = LinkPendingFill(
            slave_account_id=101,
            master_order_id=50,
            master_position_id=100
        )
        dispatcher.dispatch([intent])

        # Verify no messages sent
        assert len(sent_messages) == 0

        # Verify mapping was updated with master_position_id
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT master_position_id FROM mappings WHERE master_order_id = %s",
                (50,)
            ).fetchone()
        assert row is not None
        assert row[0] == 100

    def test_kill_switch_blocks_sends(self, seed_accounts, repo):
        """When copying_enabled=False, nothing should be sent."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        repo.set_setting("copying_enabled", False)

        clock = Clock()
        bucket = Mock()
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        # Verify nothing was sent
        assert len(sent_messages) == 0

        # Verify mapping was not created
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT * FROM mappings WHERE master_position_id = %s",
                (42,)
            ).fetchall()
        assert len(rows) == 0

    def test_dry_run_logs_and_sends_nothing(self, seed_accounts, repo):
        """Dry run should log the exact would-be request without sending."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        repo.set_setting("dry_run", True)

        clock = Clock()
        bucket = Mock()
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=1.05,
            take_profit=1.15,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        # Verify nothing was sent
        assert len(sent_messages) == 0

        # Verify dry_run event was logged with exact field details
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE category = 'slave_action' AND payload @> '{\"dry_run\": true}'"
            ).fetchall()
        assert len(rows) > 0
        payload = rows[0][0]
        assert payload['dry_run'] is True
        assert 'would_send' in payload

        # Verify operators can see exact fields: SL, TP, label, volume, etc.
        would_send = payload['would_send']
        assert would_send['stopLoss'] == 1.05
        assert would_send['takeProfit'] == 1.15
        assert would_send['label'] == "copy:m42"
        assert would_send['volume'] == 50000

        # Verify mapping was created (but stays pending)
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM mappings WHERE master_position_id = %s",
                (42,)
            ).fetchone()
        assert row[0] == "pending"

    def test_open_market_creates_pending_mapping_then_sends(self, seed_accounts, repo):
        """OpenMarket should create a pending mapping, then send after throttle."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        clock = Clock()
        bucket = Mock()
        # First acquire succeeds immediately
        bucket.acquire.return_value = defer.succeed(None)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        # Verify message was sent
        assert len(sent_messages) == 1
        account_id, msg = sent_messages[0]
        assert account_id == 101
        assert isinstance(msg, ProtoOANewOrderReq)

        # Verify mapping was created and is pending
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status, client_order_id FROM mappings WHERE master_position_id = %s",
                (42,)
            ).fetchone()
        assert row[0] == "pending"
        assert row[1] == "cm42.101"

    def test_place_pending_creates_order_mapping_then_sends(self, seed_accounts, repo):
        """PlacePending should create an order mapping, then send."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        clock = Clock()
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = PlacePending(
            slave_account_id=101,
            master_order_id=99,
            symbol_id=100,
            side=Side.BUY,
            order_type=PendingType.LIMIT,
            volume=10000,
            price=1.12,
            stop_loss=None,
            take_profit=None,
            expiry_ts_ms=None,
            label="copy:o99"
        )
        dispatcher.dispatch([intent])

        # Verify message was sent
        assert len(sent_messages) == 1
        account_id, msg = sent_messages[0]
        assert account_id == 101
        assert isinstance(msg, ProtoOANewOrderReq)

        # Verify order mapping was created
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status, client_order_id FROM mappings WHERE master_order_id = %s",
                (99,)
            ).fetchone()
        assert row[0] == "pending"
        assert row[1] == "co99.101"

    def test_transient_failure_retries_then_degraded(self, seed_accounts, repo):
        """SendNotAttempted failures retry (1s/2s/4s), then degraded after 4th attempt."""
        attempt_count = [0]
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            attempt_count[0] += 1
            return defer.fail(SendNotAttempted("Connection lost"))

        clock = Clock()
        from copier.engine.throttle import TokenBucket
        bucket = TokenBucket(clock=clock)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        # Verify first attempt was made
        assert attempt_count[0] == 1

        # Advance time for first retry (1s)
        clock.advance(1.0)
        assert attempt_count[0] == 2

        # Advance time for second retry (2s)
        clock.advance(2.0)
        assert attempt_count[0] == 3

        # Advance time for third retry (4s)
        clock.advance(4.0)
        assert attempt_count[0] == 4

        # Verify account is now degraded
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM accounts WHERE ctid_trader_account_id = %s",
                (101,)
            ).fetchone()
        assert row[0] == "degraded"

        # Verify error event was logged with request summary
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE account_id = %s AND severity = 'error'",
                (101,)
            ).fetchall()
        assert len(rows) > 0
        assert 'send_failed_degraded' in rows[0][0]['action']
        assert 'request_summary' in rows[0][0]

    def test_one_slave_failure_does_not_block_others(self, seed_accounts, repo):
        """One slave's failure should not block other slaves in the same batch."""
        attempt_count = {'slave101': 0, 'slave102': 0}
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            key = f'slave{account_id}'
            attempt_count[key] += 1
            if account_id == 101:
                return defer.fail(Exception("Slave 101 error"))
            return defer.succeed(None)

        clock = Clock()
        from copier.engine.throttle import TokenBucket
        bucket = TokenBucket(clock=clock)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        # Two intents from different slaves
        intent1 = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        intent2 = ClosePosition(
            slave_account_id=102,
            position_id=500,
            volume=40000
        )
        dispatcher.dispatch([intent1, intent2])

        # Both should have been attempted
        assert attempt_count['slave101'] >= 1
        assert attempt_count['slave102'] == 1

        # Slave 102's message should be sent
        assert any(msg[0] == 102 for msg in sent_messages)

    def test_exception_isolation_in_dispatch_loop(self, seed_accounts, repo):
        """One intent's exception should not block remaining intents in batch."""
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        clock = Clock()
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        # Batch: intent #1 succeeds, #2 fails (duplicate mapping), #3 succeeds
        intent1 = ClosePosition(slave_account_id=101, position_id=500, volume=10000)
        # intent2 will fail: create mapping first, then duplicate it
        repo.create_position_mapping(42, 102, "cm42.102")
        intent2 = OpenMarket(
            slave_account_id=102,
            master_position_id=42,  # duplicate client_order_id will cause DB constraint
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        intent3 = ClosePosition(slave_account_id=101, position_id=501, volume=20000)

        dispatcher.dispatch([intent1, intent2, intent3])

        # Verify #1 and #3 were sent (slave 101), #2 failed but not blocking
        close_messages = [msg for msg in sent_messages if msg[0] == 101]
        assert len(close_messages) == 2

        # Verify #2's failure was logged as degraded (intent processing failed)
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM accounts WHERE ctid_trader_account_id = %s",
                (102,)
            ).fetchone()
        assert row[0] == "degraded"

        # Verify error event for intent #2 processing failure
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE account_id = %s AND severity = 'error'",
                (102,)
            ).fetchall()
        assert len(rows) > 0
        assert 'intent_processing_failed' in rows[0][0]['action']

    def test_send_not_attempted_retries_then_succeeds(self, seed_accounts, repo):
        """SendNotAttempted should trigger retries and eventually succeed."""
        attempt_count = [0]
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                # First two attempts fail with SendNotAttempted (pre-wire)
                return defer.fail(SendNotAttempted("Connection dropped before send"))
            else:
                # Third attempt succeeds
                return defer.succeed(None)

        clock = Clock()
        from copier.engine.throttle import TokenBucket
        bucket = TokenBucket(clock=clock)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        # First attempt made immediately
        assert attempt_count[0] == 1

        # Retry after 1s
        clock.advance(1.0)
        assert attempt_count[0] == 2

        # Retry after 2s
        clock.advance(2.0)
        assert attempt_count[0] == 3

        # Account should NOT be degraded (success after retry)
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM accounts WHERE ctid_trader_account_id = %s",
                (101,)
            ).fetchone()
        assert row[0] != "degraded"

    def test_send_not_attempted_4_times_then_degraded(self, seed_accounts, repo):
        """SendNotAttempted ×4 should retry 3 times, then mark degraded."""
        attempt_count = [0]
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            attempt_count[0] += 1
            return defer.fail(SendNotAttempted("Connection dropped"))

        clock = Clock()
        from copier.engine.throttle import TokenBucket
        bucket = TokenBucket(clock=clock)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        assert attempt_count[0] == 1
        clock.advance(1.0)
        assert attempt_count[0] == 2
        clock.advance(2.0)
        assert attempt_count[0] == 3
        clock.advance(4.0)
        assert attempt_count[0] == 4  # Exactly 4 attempts

        # Account should be degraded after 4th SendNotAttempted
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM accounts WHERE ctid_trader_account_id = %s",
                (101,)
            ).fetchone()
        assert row[0] == "degraded"

    def test_generic_exception_no_retry_immediate_degraded(self, seed_accounts, repo):
        """Generic Exception → NO retry, immediately mark degraded."""
        attempt_count = [0]
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            attempt_count[0] += 1
            # Return a generic Exception (not SendNotAttempted)
            return defer.fail(RuntimeError("Ambiguous broker error"))

        clock = Clock()
        from copier.engine.throttle import TokenBucket
        bucket = TokenBucket(clock=clock)

        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        intent = OpenMarket(
            slave_account_id=101,
            master_position_id=42,
            symbol_id=100,
            side=Side.BUY,
            volume=50000,
            stop_loss=None,
            take_profit=None,
            label="copy:m42"
        )
        dispatcher.dispatch([intent])

        # Exactly ONE attempt (no retry for ambiguous failures)
        assert attempt_count[0] == 1

        # Account should be degraded immediately
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status FROM accounts WHERE ctid_trader_account_id = %s",
                (101,)
            ).fetchone()
        assert row[0] == "degraded"

        # Verify error event mentions ambiguous/no retry
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE account_id = %s AND severity = 'error'",
                (101,)
            ).fetchall()
        assert len(rows) > 0
        assert 'send_failed_ambiguous_no_retry' in rows[0][0]['action']

        # Advance time to verify no retries were scheduled
        clock.advance(10.0)
        assert attempt_count[0] == 1  # Still exactly 1, no retries scheduled
