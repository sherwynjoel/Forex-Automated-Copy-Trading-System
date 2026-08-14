"""Intent dispatcher: converts SlaveIntents to cTrader protobuf requests."""

import logging
from typing import Callable, Sequence

from twisted.internet import defer
from google.protobuf import message

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOANewOrderReq, ProtoOAClosePositionReq, ProtoOAAmendPositionSLTPReq,
    ProtoOAAmendOrderReq, ProtoOACancelOrderReq
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType, ProtoOATradeSide
)

from copier.domain.models import (
    SlaveIntent, OpenMarket, ClosePosition, AmendPositionSLTP,
    PlacePending, AmendPending, CancelPending, LinkPendingFill, Alert,
    Side, PendingType
)
from copier.db.repo import Repo, MappingNotFound
from copier.engine.throttle import TokenBucket

log = logging.getLogger(__name__)

RETRY_DELAYS = (1.0, 2.0, 4.0)


class SendNotAttempted(Exception):
    """Raised when a request never reached the wire.

    This exception indicates a pre-wire failure (e.g., connection not up,
    throttle refused before send). These failures are safe to retry.

    Contract: send_for_account MUST raise SendNotAttempted for LOCAL pre-wire
    failures only. Any other exception indicates an ambiguous failure and will
    NOT be retried.
    """
    pass


def client_order_id_for(intent: SlaveIntent) -> str | None:
    """Generate client order ID for intents that require mapping.

    OpenMarket -> f"cm{master_position_id}.{slave_account_id}"
    PlacePending -> f"co{master_order_id}.{slave_account_id}"
    Others -> None
    """
    if isinstance(intent, OpenMarket):
        return f"cm{intent.master_position_id}.{intent.slave_account_id}"
    elif isinstance(intent, PlacePending):
        return f"co{intent.master_order_id}.{intent.slave_account_id}"
    else:
        return None


def build_request(intent: SlaveIntent) -> tuple[int, message.Message]:
    """Build a protobuf request from a SlaveIntent.

    Returns:
        (slave_account_id, protobuf_message)
    """
    if isinstance(intent, OpenMarket):
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = intent.slave_account_id
        req.symbolId = intent.symbol_id
        req.orderType = ProtoOAOrderType.MARKET
        req.tradeSide = ProtoOATradeSide.BUY if intent.side == Side.BUY else ProtoOATradeSide.SELL
        req.volume = intent.volume
        if intent.stop_loss is not None:
            req.stopLoss = intent.stop_loss
        if intent.take_profit is not None:
            req.takeProfit = intent.take_profit
        req.label = intent.label
        req.clientOrderId = client_order_id_for(intent)
        return intent.slave_account_id, req

    elif isinstance(intent, ClosePosition):
        req = ProtoOAClosePositionReq()
        req.ctidTraderAccountId = intent.slave_account_id
        req.positionId = intent.position_id
        req.volume = intent.volume
        return intent.slave_account_id, req

    elif isinstance(intent, AmendPositionSLTP):
        req = ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = intent.slave_account_id
        req.positionId = intent.position_id
        if intent.stop_loss is not None:
            req.stopLoss = intent.stop_loss
        if intent.take_profit is not None:
            req.takeProfit = intent.take_profit
        return intent.slave_account_id, req

    elif isinstance(intent, PlacePending):
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = intent.slave_account_id
        req.symbolId = intent.symbol_id
        req.orderType = ProtoOAOrderType.LIMIT if intent.order_type == PendingType.LIMIT else ProtoOAOrderType.STOP
        req.tradeSide = ProtoOATradeSide.BUY if intent.side == Side.BUY else ProtoOATradeSide.SELL
        req.volume = intent.volume

        # Set limitPrice or stopPrice based on order_type
        if intent.order_type == PendingType.LIMIT:
            req.limitPrice = intent.price
        elif intent.order_type == PendingType.STOP:
            req.stopPrice = intent.price
        else:
            raise ValueError(f"Unknown order type: {intent.order_type}")

        if intent.stop_loss is not None:
            req.stopLoss = intent.stop_loss
        if intent.take_profit is not None:
            req.takeProfit = intent.take_profit
        if intent.expiry_ts_ms is not None:
            req.expirationTimestamp = intent.expiry_ts_ms
        req.label = intent.label
        req.clientOrderId = client_order_id_for(intent)
        return intent.slave_account_id, req

    elif isinstance(intent, AmendPending):
        req = ProtoOAAmendOrderReq()
        req.ctidTraderAccountId = intent.slave_account_id
        req.orderId = intent.order_id
        req.volume = intent.volume

        if intent.order_type == PendingType.LIMIT:
            req.limitPrice = intent.price
        elif intent.order_type == PendingType.STOP:
            req.stopPrice = intent.price
        else:
            raise ValueError(f"Unknown order type: {intent.order_type}")

        if intent.stop_loss is not None:
            req.stopLoss = intent.stop_loss
        if intent.take_profit is not None:
            req.takeProfit = intent.take_profit
        return intent.slave_account_id, req

    elif isinstance(intent, CancelPending):
        req = ProtoOACancelOrderReq()
        req.ctidTraderAccountId = intent.slave_account_id
        req.orderId = intent.order_id
        return intent.slave_account_id, req

    else:
        raise ValueError(f"Unknown intent type: {type(intent)}")


class Dispatcher:
    """Dispatches SlaveIntents through the throttle with retry logic and degraded state handling."""

    def __init__(
        self,
        send_for_account: Callable[[int, message.Message], defer.Deferred],
        repo: Repo,
        bucket: TokenBucket,
        clock=None,
    ):
        """Initialize dispatcher.

        Args:
            send_for_account: Function that sends a message to an account, returns Deferred.
                Must raise SendNotAttempted for pre-wire failures (connection, throttle).
                Any other exception indicates an ambiguous failure and will NOT be retried.
            repo: Repository for mappings and events.
            bucket: TokenBucket for rate limiting.
            clock: Optional Twisted Clock for testing.
        """
        self._send_for_account = send_for_account
        self._repo = repo
        self._bucket = bucket
        if clock is None:
            from twisted.internet import reactor as clock
        self._clock = clock

    def dispatch(self, intents: Sequence[SlaveIntent]) -> None:
        """Dispatch a sequence of intents.

        Per-intent exception isolation ensures one intent's failure (e.g., duplicate
        client_order_id, build_request ValueError) never blocks remaining intents.
        Failures are logged and degraded status set per account; processing continues.

        Processing per intent:
        - Alert: log event, no send
        - LinkPendingFill: call repo, log event, no send
        - Kill switch: log suppressed event, no send
        - Otherwise: create mapping if needed, dry-run logs but sends nothing,
          live sends with retry on SendNotAttempted only
        """
        settings = self._repo.get_settings()

        for intent in intents:
            try:
                if isinstance(intent, Alert):
                    self._handle_alert(intent)
                elif isinstance(intent, LinkPendingFill):
                    self._handle_link_pending_fill(intent)
                elif not settings.copying_enabled:
                    self._handle_kill_switch(intent)
                elif settings.dry_run:
                    self._handle_dry_run(intent)
                else:
                    self._handle_live_send(intent)
            except Exception as e:
                # Catch any exception during intent processing (build_request ValueError,
                # repo mapping constraint violations, etc.) and log it per intent without
                # blocking remaining intents.
                account_id = getattr(intent, 'slave_account_id', None)
                error_msg = f"{type(e).__name__}: {str(e)}"
                self._repo.log_event(
                    'slave_action',
                    'error',
                    {
                        'action': 'intent_processing_failed',
                        'intent_type': type(intent).__name__,
                        'error': error_msg
                    },
                    account_id=account_id
                )
                if account_id is not None:
                    self._repo.set_account_status(account_id, 'degraded', error_msg)

    def _handle_alert(self, intent: Alert) -> None:
        """Log alert and don't send."""
        self._repo.log_event(
            'slave_action',
            'warning',
            {'message': intent.message},
            account_id=intent.slave_account_id
        )

    def _handle_link_pending_fill(self, intent: LinkPendingFill) -> None:
        """Link pending fill by updating order mapping."""
        try:
            self._repo.link_pending_fill(
                intent.master_order_id,
                intent.slave_account_id,
                intent.master_position_id
            )
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'link_pending_fill',
                    'master_order_id': intent.master_order_id,
                    'master_position_id': intent.master_position_id
                },
                account_id=intent.slave_account_id
            )
        except MappingNotFound as e:
            self._repo.log_event(
                'slave_action',
                'error',
                {'action': 'link_pending_fill', 'error': str(e)},
                account_id=intent.slave_account_id
            )

    def _create_mapping(self, intent: SlaveIntent, account_id: int) -> None:
        """Create a pending position or order mapping for mapping-aware intents.

        For OpenMarket: creates position mapping.
        For PlacePending: creates order mapping.
        For others: no-op.
        """
        if isinstance(intent, OpenMarket):
            coid = client_order_id_for(intent)
            self._repo.create_position_mapping(intent.master_position_id, account_id, coid)
        elif isinstance(intent, PlacePending):
            coid = client_order_id_for(intent)
            self._repo.create_order_mapping(intent.master_order_id, account_id, coid)

    def _handle_kill_switch(self, intent: SlaveIntent) -> None:
        """Log suppressed intent when kill switch is on."""
        account_id = getattr(intent, 'slave_account_id', None)
        self._repo.log_event(
            'slave_action',
            'info',
            {'skipped': 'kill_switch', 'intent_type': type(intent).__name__},
            account_id=account_id
        )

    def _handle_dry_run(self, intent: SlaveIntent) -> None:
        """Log would-be request without sending; create mappings (stay pending)."""
        account_id, req = build_request(intent)

        # Create mapping if needed (but stays pending)
        self._create_mapping(intent, account_id)

        # Log dry-run event with exact request summary (all fields)
        summary = self._request_summary(req)
        self._repo.log_event(
            'slave_action',
            'info',
            {
                'dry_run': True,
                'would_send': summary,
                'note': 'mapping stays pending, cleaned by resync'
            },
            account_id=account_id
        )

    def _handle_live_send(self, intent: SlaveIntent) -> None:
        """Send request with retry logic."""
        account_id, req = build_request(intent)

        # Create mapping if needed
        self._create_mapping(intent, account_id)

        # Send with retries
        self._send_with_retries(account_id, req, attempt=0)

    def _send_with_retries(self, account_id: int, req: message.Message, attempt: int) -> None:
        """Send a request with retry logic on failure."""
        d = self._bucket.acquire()
        d.addCallback(lambda _: self._send_for_account(account_id, req))
        d.addCallback(lambda _: self._on_send_success(account_id))
        d.addErrback(lambda f: self._on_send_failure(f, account_id, req, attempt))

    def _on_send_success(self, account_id: int) -> None:
        """Handle successful send: clear a degraded account (N6).

        `degraded` means exactly one thing in this system: a *send* to that
        account failed (retry ladder exhausted, or an ambiguous failure that
        must not be retried) -- it is a transport verdict, never a trading
        one. A send that now succeeds is direct evidence that the verdict no
        longer holds, which is precisely what README §5 already told
        operators would happen ("it clears automatically on its next
        successful send"). Nothing implemented it: the only writes of status
        'ok' were the manual resume() path (main.py), so a connectivity blip
        left half the fleet permanently marked degraded on the Overview
        screen with no way back except a manual pause/resume per account.

        repo.clear_degraded() is guarded (`WHERE status='degraded'`), so a
        PAUSED account is never silently resumed and an already-'ok'
        account is not rewritten -- it returns False and nothing is logged,
        which is the case for essentially every send. Only a real recovery
        writes a row and logs the transition, so an operator sees the
        recovery in Logs rather than just watching the red marker vanish.

        The trade request's own outcome (fill/rejection) is not this
        method's business: it arrives later as an execution event.
        """
        if self._repo.clear_degraded(account_id):
            self._repo.log_event(
                'slave_action',
                'info',
                {'action': 'degraded_cleared', 'reason': 'successful send'},
                account_id=account_id,
            )

    def _on_send_failure(self, failure, account_id: int, req: message.Message, attempt: int) -> None:
        """Handle send failure: retry only on SendNotAttempted; other failures mark degraded immediately.

        Contract: SendNotAttempted means the request never reached the wire (pre-wire failure,
        e.g., connection down, throttle refused). These are safe to retry (1s/2s/4s backoff).

        Any other exception is ambiguous — the request state on the broker is unknown, so
        resending is unsafe. Mark account degraded immediately and log the error with full
        request summary.
        """
        if isinstance(failure.value, SendNotAttempted):
            # Safe to retry: request never left the wire
            if attempt < 3:  # 3 retries = 4 total attempts
                retry_delay = RETRY_DELAYS[attempt]
                self._clock.callLater(
                    retry_delay,
                    self._send_with_retries,
                    account_id,
                    req,
                    attempt + 1
                )
            else:
                # 4th SendNotAttempted failure: mark account degraded
                error_msg = str(failure.value)
                self._repo.set_account_status(account_id, 'degraded', error_msg)
                self._repo.log_event(
                    'slave_action',
                    'error',
                    {
                        'action': 'send_failed_degraded',
                        'attempt': attempt + 1,
                        'error': error_msg,
                        'request_summary': self._request_summary(req)
                    },
                    account_id=account_id
                )
        else:
            # Ambiguous failure: request may or may not have reached the broker.
            # Do NOT retry — mark degraded immediately.
            error_msg = str(failure.value) if hasattr(failure, 'value') else str(failure)
            self._repo.set_account_status(account_id, 'degraded', error_msg)
            self._repo.log_event(
                'slave_action',
                'error',
                {
                    'action': 'send_failed_ambiguous_no_retry',
                    'error': error_msg,
                    'request_summary': self._request_summary(req)
                },
                account_id=account_id
            )

    def _request_summary(self, req: message.Message) -> dict:
        """Create an exact summary of all set fields in the request for logging.

        Iterates ListFields() to capture every field an operator needs to verify
        before deploying (volumes, prices, SL/TP, labels, expirations, etc.).
        """
        summary = {
            'message_type': type(req).__name__,
        }

        # Add every field that is explicitly set (ListFields excludes defaults)
        for field_desc, field_value in req.ListFields():
            field_name = field_desc.name

            # Convert enum values to their names for readability
            if field_desc.enum_type is not None:
                # This is an enum field; use its name
                if field_name in ('orderType', 'tradeSide'):
                    if field_name == 'orderType':
                        field_value = ProtoOAOrderType.Name(field_value)
                    elif field_name == 'tradeSide':
                        field_value = ProtoOATradeSide.Name(field_value)

            summary[field_name] = field_value

        return summary
