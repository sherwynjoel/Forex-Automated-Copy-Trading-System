"""Tests for the intent dispatcher (dispatch.py)."""

from dataclasses import dataclass
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
            label="copy:m42",
            entry_price=1.10,
        )
        account_id, req = build_request(intent)

        assert account_id == 101
        assert isinstance(req, ProtoOANewOrderReq)
        assert req.ctidTraderAccountId == 101
        assert req.symbolId == 100
        assert req.orderType == ProtoOAOrderType.MARKET
        assert req.tradeSide == ProtoOATradeSide.BUY
        assert req.volume == 50000
        # A MARKET order carries protection as a DISTANCE from the fill, in
        # 1/100000 of a price unit -- absolute prices are refused outright
        # (they were, silently, for every copy that carried SL/TP).
        assert req.relativeStopLoss == 5000     # 1.10 - 1.05
        assert req.relativeTakeProfit == 5000   # 1.15 - 1.10
        assert not req.HasField('stopLoss')
        assert not req.HasField('takeProfit')
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

    def test_build_open_market_sell_protection_is_mirrored(self):
        """A stop protects ABOVE a sell and a target BELOW it; the distance
        is the same arithmetic mirrored."""
        intent = OpenMarket(
            slave_account_id=102, master_position_id=43, symbol_id=200,
            side=Side.SELL, volume=25000, stop_loss=1.15, take_profit=1.05,
            label="copy:m43", entry_price=1.10,
        )
        _, req = build_request(intent)
        assert req.relativeStopLoss == 5000     # 1.15 - 1.10
        assert req.relativeTakeProfit == 5000   # 1.10 - 1.05

    def test_open_market_drops_protection_it_cannot_express(self):
        """Without the master's fill there is no distance to send, and a
        level on the wrong side would be refused. Better an unprotected
        position than an order the broker throws away entirely."""
        no_reference = OpenMarket(
            slave_account_id=102, master_position_id=44, symbol_id=200,
            side=Side.BUY, volume=25000, stop_loss=1.05, take_profit=1.15,
            label="copy:m44",
        )
        _, req = build_request(no_reference)
        assert not req.HasField('relativeStopLoss')
        assert not req.HasField('relativeTakeProfit')

        wrong_side = OpenMarket(
            slave_account_id=102, master_position_id=45, symbol_id=200,
            side=Side.BUY, volume=25000, stop_loss=1.20, take_profit=1.15,
            label="copy:m45", entry_price=1.10,
        )
        _, req = build_request(wrong_side)
        assert not req.HasField('relativeStopLoss')  # above a BUY: refused
        assert req.relativeTakeProfit == 5000        # the sound half survives

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


ORG_ID = 1
OTHER_ORG_ID = 2


@pytest.fixture
def repo(db):
    """Create a Repo instance."""
    from copier.db.repo import Repo
    return Repo(db)


@pytest.fixture
def seed_accounts(db):
    """Seed one org with its connection and two slave accounts.

    Every dispatch in this module is made on behalf of ORG_ID; the copy
    gates dispatch reads live on that org's row, not on global settings.
    """
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, 'Org A')", (ORG_ID,))
        conn.execute(
            """
            INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, %s, now(), now() + interval '1 hour')
            """,
            (ORG_ID, "token_access", "token_refresh"),
        )
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (101, %(org)s, 1, 10001, false, 'slave', true, 1.5),
                (102, %(org)s, 1, 10002, false, 'slave', true, 2.0)
            """,
            {"org": ORG_ID},
        )
    return db


@pytest.fixture
def seed_two_orgs(seed_accounts):
    """Add a SECOND org (OTHER_ORG_ID) with its own connection and slave 201."""
    with psycopg.connect(seed_accounts, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, 'Org B')", (OTHER_ORG_ID,))
        conn.execute(
            """
            INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, %s, now(), now() + interval '1 hour')
            """,
            (OTHER_ORG_ID, "token_access_b", "token_refresh_b"),
        )
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES (201, %s, 2, 20001, false, 'slave', true, 1.0)
            """,
            (OTHER_ORG_ID,),
        )
    return seed_accounts


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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        repo.create_order_mapping(50, 101, "co50.101", org_id=ORG_ID)
        repo.activate_order_mapping(101, "co50.101", 999)  # slave_order_id=999

        intent = LinkPendingFill(
            slave_account_id=101,
            master_order_id=50,
            master_position_id=100
        )
        dispatcher.dispatch([intent], org_id=ORG_ID)

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

        repo.set_org_setting(ORG_ID, "copying_enabled", False)

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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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

        repo.set_org_setting(ORG_ID, "dry_run", True)

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
            label="copy:m42",
            entry_price=1.10,
        )
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        # The logged request mirrors what actually goes on the wire, so a
        # market order shows the relative distance, not an absolute price.
        assert would_send['relativeStopLoss'] == 5000
        assert would_send['relativeTakeProfit'] == 5000
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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        dispatcher.dispatch([intent1, intent2], org_id=ORG_ID)

        # Both should have been attempted
        assert attempt_count['slave101'] >= 1
        assert attempt_count['slave102'] == 1

        # Slave 102's message should be sent
        assert any(msg[0] == 102 for msg in sent_messages)

    def test_exception_isolation_in_dispatch_loop(self, seed_accounts, repo):
        """One intent's exception should not block remaining intents in batch.

        The failing intent used to be a duplicate `client_order_id` (a
        second OpenMarket for an already-mapped master position), which
        raised UniqueViolation out of `_create_mapping`. That is exactly the
        case N2 fixed -- a duplicate coid is a legitimate position INCREASE
        and no longer raises anything -- so this test now provokes the same
        isolation behaviour with a genuinely unprocessable intent:
        `build_request` raises `ValueError: Unknown intent type` for a shape
        it does not recognize, from inside the same try block. The property
        under test (one bad intent degrades only its own account and never
        stops the batch) is unchanged.
        """
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        clock = Clock()
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=clock)

        @dataclass(frozen=True)
        class _UnprocessableIntent:
            slave_account_id: int

        # Batch: intent #1 succeeds, #2 raises inside processing, #3 succeeds
        intent1 = ClosePosition(slave_account_id=101, position_id=500, volume=10000)
        intent2 = _UnprocessableIntent(slave_account_id=102)
        intent3 = ClosePosition(slave_account_id=101, position_id=501, volume=20000)

        dispatcher.dispatch([intent1, intent2, intent3], org_id=ORG_ID)

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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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
        dispatcher.dispatch([intent], org_id=ORG_ID)

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


class TestPositionIncreaseSeam:
    """N2: a master POSITION INCREASE must actually reach the slave."""

    def _open_market(self, master_position_id: int, volume: int) -> OpenMarket:
        return OpenMarket(
            slave_account_id=101,
            master_position_id=master_position_id,
            symbol_id=100,
            side=Side.BUY,
            volume=volume,
            stop_loss=None,
            take_profit=None,
            label=f"copy:m{master_position_id}",
        )

    def test_increase_is_sent_and_does_not_degrade_the_slave(self, seed_accounts, repo):
        """The second OpenMarket for an already-mapped master position -- the
        delta the pure decision layer emits for an increase (see
        test_decision_positions.py:test_increase_emits_delta_open_for_mapped_position)
        -- must put a SECOND ProtoOANewOrderReq on the wire.

        RED before the fix: `_create_mapping` re-inserted the same
        deterministic `cm42.101`, hit `mappings.client_order_id TEXT UNIQUE`,
        and Dispatcher's per-intent exception handler swallowed the
        UniqueViolation into an `intent_processing_failed` event plus a
        degraded account -- with `_handle_live_send` abandoned BEFORE
        `_send_with_retries`, so the increase reached no slave at all. With
        50 slaves that is 50 degraded accounts and zero copies for a routine
        scale-in.
        """
        sent_messages = []

        def mock_send(account_id, msg):
            sent_messages.append((account_id, msg))
            return defer.succeed(None)

        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(mock_send, repo, bucket, clock=Clock())

        dispatcher.dispatch([self._open_market(42, 10_000_000)], org_id=ORG_ID)
        # The first copy fills, exactly as a slave execution event would report it.
        repo.activate_position_mapping(101, "cm42.101", 777, 10_000_000, fill_price=1.1050)

        dispatcher.dispatch([self._open_market(42, 5_000_000)], org_id=ORG_ID)   # the increase

        assert len(sent_messages) == 2, (
            "the increase never reached the wire: "
            f"{[type(m).__name__ for _a, m in sent_messages]}"
        )
        assert [msg.volume for _a, msg in sent_messages] == [10_000_000, 5_000_000]
        assert all(msg.clientOrderId == "cm42.101" for _a, msg in sent_messages)

        # No degradation, and no intent_processing_failed event.
        account = next(a for a in repo.load_accounts() if a.account_id == 101)
        assert account.status == 'ok'
        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            failures = conn.execute(
                "SELECT payload FROM events WHERE category = 'slave_action' AND severity = 'error'"
            ).fetchall()
        assert failures == [], failures

    def test_increase_lands_as_one_mapping_row_carrying_both_entries(self, seed_accounts, repo):
        """The mapping reflects BOTH fills: one row per (master position,
        slave), volume aggregated, pointing at the slave's single merged
        position (cTrader aggregates same-direction adds)."""
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(lambda a, m: defer.succeed(None), repo, bucket, clock=Clock())

        dispatcher.dispatch([self._open_market(42, 10_000_000)], org_id=ORG_ID)
        repo.activate_position_mapping(101, "cm42.101", 777, 10_000_000)
        dispatcher.dispatch([self._open_market(42, 5_000_000)], org_id=ORG_ID)
        repo.activate_position_mapping(101, "cm42.101", 777, 5_000_000)

        rows = [r for r in repo.mapping_rows() if r["master_position_id"] == 42]
        assert len(rows) == 1
        assert rows[0]["slave_volume"] == 15_000_000
        assert rows[0]["status"] == "active"


class TestDegradedAutoClear:
    """N6: README §5's promise that a degraded slave clears on its next
    successful send."""

    def test_successful_send_clears_a_degraded_account(self, seed_accounts, repo):
        """RED before the fix: `_on_send_success` was `pass`, so the only
        write of status 'ok' anywhere was the manual resume() path -- a slave
        degraded by one connectivity blip stayed degraded on the Overview
        screen until an operator pause/resumed it by hand."""
        repo.set_account_status(101, 'degraded', "no connected transport for send")

        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(lambda a, m: defer.succeed(None), repo, bucket, clock=Clock())

        dispatcher.dispatch([OpenMarket(
            slave_account_id=101, master_position_id=77, symbol_id=100, side=Side.BUY,
            volume=10_000, stop_loss=None, take_profit=None, label="copy:m77",
        )], org_id=ORG_ID)

        account = next(a for a in repo.load_accounts() if a.account_id == 101)
        assert account.status == 'ok'
        assert account.last_error is None

        with psycopg.connect(seed_accounts, autocommit=True) as conn:
            payloads = [
                r[0] for r in conn.execute(
                    "SELECT payload FROM events WHERE category = 'slave_action'"
                ).fetchall()
            ]
        assert any(p.get('action') == 'degraded_cleared' for p in payloads), payloads

    def test_successful_send_does_not_resume_a_paused_account(self, seed_accounts, repo):
        repo.set_account_status(101, 'paused', None)

        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(lambda a, m: defer.succeed(None), repo, bucket, clock=Clock())

        dispatcher.dispatch([OpenMarket(
            slave_account_id=101, master_position_id=78, symbol_id=100, side=Side.BUY,
            volume=10_000, stop_loss=None, take_profit=None, label="copy:m78",
        )], org_id=ORG_ID)

        account = next(a for a in repo.load_accounts() if a.account_id == 101)
        assert account.status == 'paused'

    def test_failed_send_still_degrades_and_is_not_cleared(self, seed_accounts, repo):
        """The auto-clear must not paper over a send that actually failed."""
        clock = Clock()
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)

        def failing_send(account_id, msg):
            return defer.fail(SendNotAttempted("no connected transport"))

        dispatcher = Dispatcher(failing_send, repo, bucket, clock=clock)
        dispatcher.dispatch([OpenMarket(
            slave_account_id=101, master_position_id=79, symbol_id=100, side=Side.BUY,
            volume=10_000, stop_loss=None, take_profit=None, label="copy:m79",
        )], org_id=ORG_ID)
        for delay in RETRY_DELAYS:
            clock.advance(delay)

        account = next(a for a in repo.load_accounts() if a.account_id == 101)
        assert account.status == 'degraded'
        assert account.last_error


class TestPerOrgGates:
    """The copy gates are per-org state, read from the dispatching org's row.

    Before multi-org they were a single global settings row, so ANY tenant
    pausing copying or switching to dry-run silently froze every other
    tenant's replication -- and one tenant flipping copying back on
    un-paused everybody.
    """

    def _open_market(self, slave_account_id: int, master_position_id: int) -> OpenMarket:
        return OpenMarket(
            slave_account_id=slave_account_id,
            master_position_id=master_position_id,
            symbol_id=100,
            side=Side.BUY,
            volume=50_000,
            stop_loss=None,
            take_profit=None,
            label=f"copy:m{master_position_id}",
        )

    def test_another_orgs_kill_switch_does_not_suppress_this_orgs_copying(
        self, seed_two_orgs, repo
    ):
        """Org B pausing copying must leave org A trading normally."""
        repo.set_org_setting(OTHER_ORG_ID, "copying_enabled", False)

        sent = []
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(
            lambda a, m: (sent.append((a, m)), defer.succeed(None))[1],
            repo, bucket, clock=Clock(),
        )

        dispatcher.dispatch([self._open_market(101, 42)], org_id=ORG_ID)

        assert [a for a, _m in sent] == [101]
        assert repo.get_org(ORG_ID).copying_enabled is True

    def test_this_orgs_kill_switch_suppresses_only_this_org(self, seed_two_orgs, repo):
        """Org A paused: org A's intent is suppressed, org B's still sends."""
        repo.set_org_setting(ORG_ID, "copying_enabled", False)

        sent = []
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(
            lambda a, m: (sent.append((a, m)), defer.succeed(None))[1],
            repo, bucket, clock=Clock(),
        )

        dispatcher.dispatch([self._open_market(101, 42)], org_id=ORG_ID)
        dispatcher.dispatch([self._open_market(201, 43)], org_id=OTHER_ORG_ID)

        assert [a for a, _m in sent] == [201], "org A was paused, org B was not"

    def test_mappings_and_events_are_stamped_with_the_dispatching_org(
        self, seed_two_orgs, repo
    ):
        """Every row this batch writes carries the org it was dispatched for,
        so /state and the audit log can never leak across tenants."""
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(lambda a, m: defer.succeed(None), repo, bucket, clock=Clock())

        dispatcher.dispatch([self._open_market(101, 42)], org_id=ORG_ID)
        dispatcher.dispatch([self._open_market(201, 43)], org_id=OTHER_ORG_ID)

        orgs_by_coid = {m["client_order_id"]: m["org_id"] for m in repo.mapping_rows()}
        assert orgs_by_coid == {"cm42.101": ORG_ID, "cm43.201": OTHER_ORG_ID}
        assert [m["client_order_id"] for m in repo.mapping_rows(org_id=ORG_ID)] == ["cm42.101"]

    def test_dry_run_is_per_org(self, seed_two_orgs, repo):
        """Org B in dry-run must not stop org A from putting orders on the wire."""
        repo.set_org_setting(OTHER_ORG_ID, "dry_run", True)

        sent = []
        bucket = Mock()
        bucket.acquire.return_value = defer.succeed(None)
        dispatcher = Dispatcher(
            lambda a, m: (sent.append((a, m)), defer.succeed(None))[1],
            repo, bucket, clock=Clock(),
        )

        dispatcher.dispatch([self._open_market(101, 42)], org_id=ORG_ID)
        dispatcher.dispatch([self._open_market(201, 43)], org_id=OTHER_ORG_ID)

        assert [a for a, _m in sent] == [101], "only org B was in dry-run"


class TestRelativeProtectionEdges:
    """The arithmetic guards, in isolation."""

    def test_a_gap_below_one_wire_unit_is_dropped_not_zeroed(self):
        """0 is not "no protection" -- the broker reads it as a level AT the
        fill, which stops out instantly or is refused outright."""
        from copier.engine.dispatch import relative_protection
        # 0.000004 of a price unit is below 1/100000 and cannot be sent.
        sl, tp = relative_protection(Side.BUY, 1.100000, 1.099996, None)
        assert sl is None
        # One whole unit survives.
        sl, _ = relative_protection(Side.BUY, 1.100000, 1.099990, None)
        assert sl == 1

    def test_an_unknown_side_is_refused_rather_than_guessed(self):
        """Defaulting to SELL would mirror protection onto the wrong side
        of the market."""
        from copier.engine.dispatch import relative_protection
        with pytest.raises(ValueError, match="unknown side"):
            relative_protection("BUY", 1.10, 1.05, None)  # a str, not Side


def test_a_copy_placed_without_its_protection_is_logged(seed_accounts, repo):
    """Unprotected beats unplaced -- but never silently."""
    sent = []

    def mock_send(account_id, msg):
        sent.append((account_id, msg))
        return defer.succeed(None)

    bucket = Mock()
    bucket.acquire.return_value = defer.succeed(None)
    dispatcher = Dispatcher(mock_send, repo, bucket, clock=Clock())

    # No entry_price: the distance cannot be computed, so the master's stop
    # cannot travel with the copy.
    dispatcher.dispatch([OpenMarket(
        slave_account_id=101, master_position_id=42, symbol_id=100,
        side=Side.BUY, volume=50000, stop_loss=1.05, take_profit=1.15,
        label="copy:m42")], org_id=ORG_ID)

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT severity, payload FROM events "
            "WHERE payload->>'action' = 'protection_dropped'").fetchall()
    assert len(rows) == 1
    severity, payload = rows[0]
    assert severity == 'warning'
    assert sorted(payload['dropped']) == ['stop loss', 'take profit']
    assert payload['master_position_id'] == 42
    # The order still went out: a position you can close beats one never opened.
    assert len(sent) == 1
