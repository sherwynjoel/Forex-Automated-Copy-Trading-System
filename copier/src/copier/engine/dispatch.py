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


# The broker states a market order's protection in 1/100000 of a price
# unit, measured from the fill.
RELATIVE_PRICE_SCALE = 100_000

# Requests whose protection could not be expressed, keyed by request
# identity, so the send path can warn about it once. Small and short-lived:
# build_request and the send happen back to back.
_DROPPED_PROTECTION: dict[int, tuple] = {}


def relative_protection(
    side: Side,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
) -> tuple[int | None, int | None]:
    """Absolute SL/TP prices -> the relative distances a MARKET order takes.

    The cTrader Open API refuses absolute stopLoss/takeProfit on market
    orders ("Not supported for MARKET orders") because the fill price is
    not known when the order is sent; it wants the distance instead, and
    applies it in the protective direction for the side.

    Returns (relative_stop_loss, relative_take_profit), either of which is
    None when it cannot be expressed -- no reference price, or a level on
    the wrong side of it. Dropping one is deliberate: the alternative is an
    order the broker rejects outright, which would leave the trade unmade
    rather than merely unprotected.
    """
    if side not in (Side.BUY, Side.SELL):
        raise ValueError(f"unknown side {side!r}: cannot place protection")
    if entry_price is None or entry_price <= 0:
        return None, None

    def distance(target: float | None, protective_below: bool) -> int | None:
        if target is None:
            return None
        gap = (entry_price - target) if protective_below else (target - entry_price)
        if gap <= 0:
            return None  # wrong side of the market; the broker would refuse
        units = int(round(gap * RELATIVE_PRICE_SCALE))
        # Below one wire unit the distance is not representable. Sending 0
        # would not mean "no protection" -- the broker's formula reads it as
        # a level AT the fill, which stops out immediately or is refused.
        return units if units >= 1 else None

    is_buy = side == Side.BUY
    # A stop protects below a BUY and above a SELL; a target is the reverse.
    return (distance(stop_loss, protective_below=is_buy),
            distance(take_profit, protective_below=not is_buy))


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
        # A MARKET order takes its protection as a distance from the fill,
        # never as an absolute price -- see relative_protection().
        sl_rel, tp_rel = relative_protection(
            intent.side, intent.entry_price, intent.stop_loss, intent.take_profit)
        if sl_rel is not None:
            req.relativeStopLoss = sl_rel
        if tp_rel is not None:
            req.relativeTakeProfit = tp_rel
        # Recorded on the request so the dispatcher can warn: a copy going
        # out without the protection its master carries is the kind of
        # thing an operator must hear about, not discover in a drawdown.
        dropped = [
            name for name, wanted, got in (
                ("stop loss", intent.stop_loss, sl_rel),
                ("take profit", intent.take_profit, tp_rel),
            ) if wanted is not None and got is None
        ]
        if dropped:
            _DROPPED_PROTECTION[id(req)] = (intent, dropped)
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

    def dispatch(self, intents: Sequence[SlaveIntent], org_id: int) -> None:
        """Dispatch a sequence of intents on behalf of exactly one org.

        `org_id` is the org that owns the master whose event produced these
        intents; the caller (CopierService) resolves it from the routing
        table. Every copy gate below reads THAT org's row, so one org's kill
        switch or dry-run can never suppress -- or fail to suppress --
        another org's copying, and every mapping and event this batch writes
        is stamped with the same org.

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
        org = self._repo.get_org(org_id)

        for intent in intents:
            try:
                if isinstance(intent, Alert):
                    self._handle_alert(intent, org_id)
                elif isinstance(intent, LinkPendingFill):
                    self._handle_link_pending_fill(intent, org_id)
                elif not org.copying_enabled:
                    self._handle_kill_switch(intent, org_id)
                elif org.dry_run:
                    self._handle_dry_run(intent, org_id)
                else:
                    self._handle_live_send(intent, org_id)
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
                    account_id=account_id,
                    org_id=org_id
                )
                if account_id is not None:
                    self._repo.set_account_status(account_id, 'degraded', error_msg)

    def _handle_alert(self, intent: Alert, org_id: int) -> None:
        """Log alert and don't send."""
        self._repo.log_event(
            'slave_action',
            'warning',
            {'message': intent.message},
            account_id=intent.slave_account_id,
            org_id=org_id
        )

    def _handle_link_pending_fill(self, intent: LinkPendingFill, org_id: int) -> None:
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
                account_id=intent.slave_account_id,
                org_id=org_id
            )
        except MappingNotFound as e:
            self._repo.log_event(
                'slave_action',
                'error',
                {'action': 'link_pending_fill', 'error': str(e)},
                account_id=intent.slave_account_id,
                org_id=org_id
            )

    def _create_mapping(self, intent: SlaveIntent, account_id: int, org_id: int) -> None:
        """Create a pending position or order mapping for mapping-aware intents.

        For OpenMarket: creates position mapping.
        For PlacePending: creates order mapping.
        For others: no-op.
        """
        if isinstance(intent, OpenMarket):
            coid = client_order_id_for(intent)
            self._repo.create_position_mapping(
                intent.master_position_id, account_id, coid, org_id=org_id,
                symbol=intent.symbol_name or None)
        elif isinstance(intent, PlacePending):
            coid = client_order_id_for(intent)
            self._repo.create_order_mapping(
                intent.master_order_id, account_id, coid, org_id=org_id,
                symbol=intent.symbol_name or None)

    def _handle_kill_switch(self, intent: SlaveIntent, org_id: int) -> None:
        """Log suppressed intent when this org's kill switch is on."""
        account_id = getattr(intent, 'slave_account_id', None)
        self._repo.log_event(
            'slave_action',
            'info',
            {'skipped': 'kill_switch', 'intent_type': type(intent).__name__},
            account_id=account_id,
            org_id=org_id
        )

    def _warn_dropped_protection(self, req, account_id: int, org_id: int) -> None:
        """Say plainly when a copy went out without protection its master
        had. The order is still worth placing -- an unprotected position can
        be closed, a rejected one was never opened -- but the operator has
        to be told."""
        entry = _DROPPED_PROTECTION.pop(id(req), None)
        if entry is None:
            return
        intent, dropped = entry
        self._repo.log_event(
            'slave_action', 'warning',
            {'action': 'protection_dropped',
             'master_position_id': getattr(intent, 'master_position_id', None),
             'dropped': dropped,
             'stop_loss': intent.stop_loss,
             'take_profit': intent.take_profit,
             'entry_price': intent.entry_price,
             'detail': 'copy placed WITHOUT this protection: the level could '
                       'not be expressed as a distance from the fill'},
            account_id=account_id, org_id=org_id,
        )

    def _handle_dry_run(self, intent: SlaveIntent, org_id: int) -> None:
        """Log would-be request without sending; create mappings (stay pending)."""
        account_id, req = build_request(intent)

        # Create mapping if needed (but stays pending)
        self._create_mapping(intent, account_id, org_id)
        self._warn_dropped_protection(req, account_id, org_id)

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
            account_id=account_id,
            org_id=org_id
        )

    def _handle_live_send(self, intent: SlaveIntent, org_id: int) -> None:
        """Send request with retry logic."""
        account_id, req = build_request(intent)

        # Create mapping if needed
        self._create_mapping(intent, account_id, org_id)

        # Tell the operator BEFORE the send if this copy is going out
        # without protection its master carries.
        self._warn_dropped_protection(req, account_id, org_id)

        # Send with retries
        self._send_with_retries(account_id, req, attempt=0)

    def send_direct(self, account_id: int, req: message.Message) -> None:
        """Send an operator-initiated trade request, bypassing the copy gates.

        Deliberately NOT routed through dispatch(): the copying_enabled and
        dry_run gates exist to stop COPYING, and the operator actions that
        use this (kill-switch flatten, manual orders/closes/cancels from the
        dashboard) must work exactly when those gates are engaged -- a global
        kill switch pauses copying first and then flattens through this very
        path.  Keeps the same throttle, retry ladder (SendNotAttempted only),
        and degraded bookkeeping as every copy send.
        """
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
