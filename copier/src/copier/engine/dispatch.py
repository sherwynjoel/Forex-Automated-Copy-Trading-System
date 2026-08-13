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
    Side
)
from copier.db.repo import Repo, MappingNotFound

log = logging.getLogger(__name__)

RETRY_DELAYS = (1.0, 2.0, 4.0)


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
        req.orderType = ProtoOAOrderType.LIMIT if intent.order_type.value == 'LIMIT' else ProtoOAOrderType.STOP
        req.tradeSide = ProtoOATradeSide.BUY if intent.side == Side.BUY else ProtoOATradeSide.SELL
        req.volume = intent.volume

        # Set limitPrice or stopPrice based on order_type
        if intent.order_type.value == 'LIMIT':
            req.limitPrice = intent.price
        else:  # STOP
            req.stopPrice = intent.price

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

        if intent.order_type.value == 'LIMIT':
            req.limitPrice = intent.price
        else:  # STOP
            req.stopPrice = intent.price

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
        bucket,
        clock=None,
    ):
        """Initialize dispatcher.

        Args:
            send_for_account: Function that sends a message to an account, returns Deferred.
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

        Processing:
        - Alert: log event, no send
        - LinkPendingFill: call repo, log event, no send
        - Kill switch: log suppressed event, no send
        - Otherwise: create mapping if needed, dry-run logs but sends nothing,
          live sends with retry on failure
        """
        settings = self._repo.get_settings()

        for intent in intents:
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
        if isinstance(intent, OpenMarket):
            coid = client_order_id_for(intent)
            self._repo.create_position_mapping(intent.master_position_id, account_id, coid)
        elif isinstance(intent, PlacePending):
            coid = client_order_id_for(intent)
            self._repo.create_order_mapping(intent.master_order_id, account_id, coid)

        # Log dry-run event with request summary
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
        if isinstance(intent, OpenMarket):
            coid = client_order_id_for(intent)
            self._repo.create_position_mapping(intent.master_position_id, account_id, coid)
        elif isinstance(intent, PlacePending):
            coid = client_order_id_for(intent)
            self._repo.create_order_mapping(intent.master_order_id, account_id, coid)

        # Send with retries
        self._send_with_retries(account_id, req, attempt=0)

    def _send_with_retries(self, account_id: int, req: message.Message, attempt: int) -> None:
        """Send a request with retry logic on failure."""
        d = self._bucket.acquire()
        d.addCallback(lambda _: self._send_for_account(account_id, req))
        d.addCallback(lambda _: self._on_send_success(account_id))
        d.addErrback(lambda f: self._on_send_failure(f, account_id, req, attempt))

    def _on_send_success(self, account_id: int) -> None:
        """Handle successful send."""
        # For trade requests, outcomes arrive as execution events handled elsewhere
        pass

    def _on_send_failure(self, failure, account_id: int, req: message.Message, attempt: int) -> None:
        """Handle send failure with retry or degraded."""
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
            # 4th failure: mark account degraded
            error_msg = str(failure.value) if hasattr(failure, 'value') else str(failure)
            self._repo.set_account_status(account_id, 'degraded', error_msg)
            self._repo.log_event(
                'slave_action',
                'error',
                {
                    'action': 'send_failed_degraded',
                    'attempt': attempt + 1,
                    'error': error_msg
                },
                account_id=account_id
            )

    def _request_summary(self, req: message.Message) -> dict:
        """Create a summary of the request for logging."""
        summary = {
            'message_type': type(req).__name__,
            'account_id': req.ctidTraderAccountId,
        }

        # Add key fields based on message type
        if hasattr(req, 'symbolId') and req.symbolId:
            summary['symbol_id'] = req.symbolId
        if hasattr(req, 'orderType') and req.orderType:
            order_type_name = ProtoOAOrderType.Name(req.orderType)
            summary['order_type'] = order_type_name
        if hasattr(req, 'volume') and req.volume:
            summary['volume'] = req.volume
        if hasattr(req, 'tradeSide') and req.tradeSide:
            side_name = ProtoOATradeSide.Name(req.tradeSide)
            summary['trade_side'] = side_name
        if hasattr(req, 'limitPrice') and req.limitPrice:
            summary['limit_price'] = req.limitPrice
        if hasattr(req, 'stopPrice') and req.stopPrice:
            summary['stop_price'] = req.stopPrice
        if hasattr(req, 'positionId') and req.positionId:
            summary['position_id'] = req.positionId
        if hasattr(req, 'orderId') and req.orderId:
            summary['order_id'] = req.orderId

        return summary
