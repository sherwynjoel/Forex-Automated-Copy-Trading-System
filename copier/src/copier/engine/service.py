"""Copier service: master/slave event wiring orchestration.

Orchestrates the complete flow:
- Master execution events → normalize → decide → dispatch
- Slave execution events → mapping activation/updates (never decide)
- Pending fill alerts scheduled for unmapped fills
"""

import logging
import time
from typing import Callable, Mapping

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType)

from copier.db.repo import Repo, MappingNotFound
from copier.domain.models import SymbolInfo, SlaveConfig, MasterPendingFilled
from copier.domain.decision import decide
from copier.engine.normalize import normalize
from copier.engine.dispatch import Dispatcher

log = logging.getLogger(__name__)

PENDING_FILL_ALERT_S = 30.0


class CopierService:
    """Master/slave event wiring orchestration.

    Handles execution events from master and slave accounts:
    - Master events: normalize → decide → dispatch (all replication flow)
    - Slave events: update mappings only (no decide call, loop-proof)
    """

    def __init__(
        self,
        repo: Repo,
        dispatcher: Dispatcher,
        master_account_id: int,
        master_symbols_by_id: Mapping[int, SymbolInfo],
        slaves_provider: Callable[[], list[SlaveConfig]],
        clock=None,
    ):
        """Initialize CopierService.

        Args:
            repo: Repository for mappings and events.
            dispatcher: Intent dispatcher.
            master_account_id: Account ID of the master.
            master_symbols_by_id: Master's symbols by ID.
            slaves_provider: Callable returning list of enabled SlaveConfig.
            clock: Optional Twisted Clock for testing; defaults to reactor.
        """
        self._repo = repo
        self._dispatcher = dispatcher
        self._master_account_id = master_account_id
        self._master_symbols_by_id = master_symbols_by_id
        self._slaves_provider = slaves_provider

        if clock is None:
            from twisted.internet import reactor as clock
        self._clock = clock

        # Set by build_app() after CopierApp exists (service is constructed
        # first, so this cannot be a ctor argument). Called after any event
        # that changes positions/orders/mappings, so /state can refresh
        # immediately instead of waiting for the next periodic resync tick.
        self.on_positions_changed: Callable[[], None] | None = None

    def _notify_positions_changed(self) -> None:
        """Invoke on_positions_changed; a callback failure must never break
        event processing."""
        if self.on_positions_changed is None:
            return
        try:
            self.on_positions_changed()
        except Exception:
            log.exception("on_positions_changed callback failed")

    def handle_execution(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle execution event from master or slave.

        Behavior:
        - Master account: normalize → decide → dispatch
        - Slave account: update mappings only (never decide)
        - Unknown account: log and ignore

        Exception boundary: any error during event processing (DB transient failures,
        exception in normalize/decide/dispatch, etc.) is caught, logged, and the pump
        continues to the next event. No single event can crash the event stream.

        Args:
            account_id: Source account ID.
            evt: ProtoOAExecutionEvent message.
        """
        try:
            start_time = time.time_ns() // 1_000_000  # milliseconds

            if account_id == self._master_account_id:
                self._handle_master_event(evt, start_time)
            else:
                self._handle_slave_event(account_id, evt)
        except Exception as e:
            # Catch all exceptions: DB transient failures, normalize/decide/dispatch errors, etc.
            # Log and continue; do not re-raise, so the event pump stays alive.
            error_msg = f"{type(e).__name__}: {str(e)}"
            try:
                self._repo.log_event(
                    'connection',  # Category: infrastructure/DB failures
                    'error',
                    {
                        'action': 'event_processing_failed',
                        'account_id': account_id,
                        'error': error_msg,
                        'error_type': type(e).__name__,
                    },
                )
            except Exception:
                # Even the error log itself failed; give up
                pass

    def _handle_master_event(self, evt: ProtoOAExecutionEvent, start_time: int) -> None:
        """Handle master account event: normalize -> decide -> dispatch.

        Args:
            evt: ProtoOAExecutionEvent.
            start_time: Event processing start time in milliseconds.
        """
        # Measure latency
        normalized = normalize(evt, self._master_symbols_by_id)

        # Log master event always (even if normalized to None)
        latency_ms = (time.time_ns() // 1_000_000) - start_time
        payload = {
            'execution_type': ProtoOAExecutionType.Name(evt.executionType),
            'normalized': type(normalized).__name__ if normalized else None,
        }
        if normalized is None:
            # An event we chose not to act on must explain itself in the
            # log: which order type and symbol the miss was about. A live
            # MARKET_RANGE fill sat invisible behind a bare
            # {"normalized": null} for exactly this lack of detail.
            payload['order_type'] = ProtoOAOrderType.Name(evt.order.orderType)
            payload['symbol_id'] = evt.order.tradeData.symbolId
        self._repo.log_event(
            'master_event',
            'info',
            payload,
            account_id=self._master_account_id,
            latency_ms=latency_ms,
        )

        # If normalization yielded no event, we're done
        if normalized is None:
            return

        # Decide: get intents for all enabled slaves
        slaves = self._slaves_provider()
        intents = decide(normalized, self._repo, slaves)

        # Dispatch intents
        if intents:
            self._dispatcher.dispatch(intents)

        # Schedule pending fill alert if this is a pending fill
        if isinstance(normalized, MasterPendingFilled):
            self._schedule_pending_fill_check(normalized)

        # The master's positions/orders just changed; let /state catch up now.
        self._notify_positions_changed()

    def _schedule_pending_fill_check(self, pending_filled: MasterPendingFilled) -> None:
        """Schedule a check for unmapped slave fills after PENDING_FILL_ALERT_S.

        If any linked mapping still doesn't have slave_position_id, log warning.

        Args:
            pending_filled: The MasterPendingFilled event.
        """
        def check_pending_fills():
            """Check if any slave fills are still pending."""
            # Get all order mappings for this master order
            order_entries = self._repo.order_entries(pending_filled.order_id)
            for entry in order_entries:
                # Check if the position mapping exists with slave_position_id
                position_entries = self._repo.position_entries(pending_filled.position_id)
                mapped = any(
                    pe.slave_account_id == entry.slave_account_id
                    for pe in position_entries
                )
                if not mapped:
                    msg = f"Slave {entry.slave_account_id} order filled but position not yet linked"
                    self._repo.log_event(
                        'slave_action',
                        'warning',
                        {
                            'action': 'pending_fill_alert',
                            'master_position_id': pending_filled.position_id,
                            'slave_account_id': entry.slave_account_id,
                            'message': msg,
                        },
                        account_id=entry.slave_account_id,
                    )

        self._clock.callLater(PENDING_FILL_ALERT_S, check_pending_fills)

    def _is_known_enabled_slave(self, account_id: int) -> bool:
        """Check if account is a known, enabled slave.

        Args:
            account_id: Account ID to check.

        Returns:
            True if account is in slaves_provider() and enabled; False otherwise.
        """
        slaves = self._slaves_provider()
        for slave in slaves:
            if slave.account_id == account_id and slave.enabled:
                return True
        return False

    def _handle_slave_event(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave account event: update mappings, log, never decide.

        Loop-proof by construction: slave events never trigger decide/dispatch.
        Gate: only known, enabled slaves are processed; unknown/disabled accounts
        are logged and ignored (no mutations).

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        # Gate: only process known, enabled slaves
        if not self._is_known_enabled_slave(account_id):
            self._repo.log_event(
                'drift',  # Unknown/disabled account
                'info',
                {
                    'action': 'event_from_unknown_or_disabled_slave',
                    'account_id': account_id,
                    'execution_type': ProtoOAExecutionType.Name(evt.executionType),
                },
                account_id=account_id,
            )
            return

        execution_type = evt.executionType

        # Handle ORDER_FILLED and ORDER_PARTIAL_FILL
        if execution_type in (ProtoOAExecutionType.ORDER_FILLED,
                             ProtoOAExecutionType.ORDER_PARTIAL_FILL):
            self._handle_slave_fill(account_id, evt)
            self._notify_positions_changed()

        # Handle ORDER_ACCEPTED
        elif execution_type == ProtoOAExecutionType.ORDER_ACCEPTED:
            self._handle_slave_order_accepted(account_id, evt)
            self._notify_positions_changed()

        # Handle ORDER_CANCELLED
        elif execution_type == ProtoOAExecutionType.ORDER_CANCELLED:
            self._handle_slave_order_cancelled(account_id, evt)
            self._notify_positions_changed()

        # Handle ORDER_REJECTED
        elif execution_type == ProtoOAExecutionType.ORDER_REJECTED:
            self._handle_slave_order_rejected(account_id, evt)
            self._notify_positions_changed()

        # Log unclassified slave events
        else:
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'unclassified_slave_event',
                    'execution_type': ProtoOAExecutionType.Name(execution_type),
                },
                account_id=account_id,
            )

    def _extract_client_order_id(self, evt: ProtoOAExecutionEvent) -> str | None:
        """Extract clientOrderId from event if present.

        Args:
            evt: ProtoOAExecutionEvent.

        Returns:
            clientOrderId string or None if not present.
        """
        return evt.order.clientOrderId if evt.order.HasField('clientOrderId') else None

    def _handle_slave_fill(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave ORDER_FILLED or ORDER_PARTIAL_FILL.

        Updates:
        - clientOrderId starting "cm" → activate_position_mapping
        - closePositionDetail → reduce_position_mapping
        - slave_order_id matching order mapping → activate_pending_fill

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        client_order_id = self._extract_client_order_id(evt)
        deal_position_id = evt.deal.positionId
        filled_volume = evt.deal.filledVolume
        slave_order_id = evt.order.orderId

        # Check for close (has closePositionDetail)
        if evt.deal.HasField('closePositionDetail'):
            closed_volume = evt.deal.closePositionDetail.closedVolume
            self._repo.reduce_position_mapping(account_id, deal_position_id, closed_volume)
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'position_closed',
                    'slave_position_id': deal_position_id,
                    'closed_volume': closed_volume,
                },
                account_id=account_id,
            )
            return

        # T9c: the execution price is what the Positions screen's per-copy
        # "Fill Price"/"Slippage" columns are built from. It was already
        # being read here for the event log; it is now also handed to the
        # mapping row so GET /state can report it (see repo.mappings.fill_price
        # / db/migrations/003_mapping_fill_price.sql).
        fill_price = evt.deal.executionPrice if evt.deal.HasField('executionPrice') else None

        # Check for position fill (clientOrderId starting "cm")
        if client_order_id and client_order_id.startswith("cm"):
            try:
                self._repo.activate_position_mapping(
                    client_order_id, deal_position_id, filled_volume, fill_price=fill_price,
                )
                self._repo.log_event(
                    'slave_action',
                    'info',
                    {
                        'action': 'position_filled',
                        'client_order_id': client_order_id,
                        'slave_position_id': deal_position_id,
                        'filled_volume': filled_volume,
                        'fill_price': fill_price,
                    },
                    account_id=account_id,
                )
            except MappingNotFound:
                # Unknown clientOrderId - log as drift warning
                self._repo.log_event(
                    'slave_action',
                    'warning',
                    {
                        'action': 'unknown_fill',
                        'client_order_id': client_order_id,
                        'reason': 'No matching position mapping',
                    },
                    account_id=account_id,
                )
            return

        # Check for pending order fill: match by slave_order_id
        try:
            self._repo.activate_pending_fill(
                account_id, slave_order_id, deal_position_id, filled_volume,
                fill_price=fill_price,
            )
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'pending_fill',
                    'client_order_id': client_order_id,
                    'slave_order_id': slave_order_id,
                    'slave_position_id': deal_position_id,
                    'filled_volume': filled_volume,
                    'fill_price': fill_price,
                },
                account_id=account_id,
            )
            return
        except MappingNotFound:
            # slave_order_id didn't match; no order mapping found
            pass

        # Fallthrough: fill matched nothing (neither position "cm" nor pending by order_id)
        self._repo.log_event(
            'slave_action',
            'warning',
            {
                'action': 'unmatched_slave_fill',
                'slave_order_id': slave_order_id,
                'slave_position_id': deal_position_id,
                'client_order_id': client_order_id,
                'reason': 'No matching position or order mapping',
            },
            account_id=account_id,
        )

    def _handle_slave_order_accepted(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave ORDER_ACCEPTED with clientOrderId starting 'co'.

        Updates: activate_order_mapping

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        client_order_id = self._extract_client_order_id(evt)

        if not client_order_id or not client_order_id.startswith("co"):
            return

        slave_order_id = evt.order.orderId
        try:
            self._repo.activate_order_mapping(client_order_id, slave_order_id)
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'order_accepted',
                    'client_order_id': client_order_id,
                    'slave_order_id': slave_order_id,
                },
                account_id=account_id,
            )
        except MappingNotFound:
            self._repo.log_event(
                'slave_action',
                'warning',
                {
                    'action': 'order_accepted_no_mapping',
                    'client_order_id': client_order_id,
                },
                account_id=account_id,
            )

    def _handle_slave_order_cancelled(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave ORDER_CANCELLED on mapped order.

        Updates: close_order_mapping

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        slave_order_id = evt.order.orderId

        try:
            self._repo.close_order_mapping(account_id, slave_order_id)
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'order_cancelled',
                    'slave_order_id': slave_order_id,
                },
                account_id=account_id,
            )
        except MappingNotFound:
            # Order mapping doesn't exist or already closed - log as drift warning
            self._repo.log_event(
                'slave_action',
                'warning',
                {
                    'action': 'order_cancel_no_mapping',
                    'slave_order_id': slave_order_id,
                    'reason': 'Order mapping not found or already closed',
                },
                account_id=account_id,
            )

    def _handle_slave_order_rejected(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave ORDER_REJECTED.

        Updates: fail_mapping with error code
        Logs: error event

        Spec: broker rejections (min-volume, margin) are NOT account status degradations.
        Only alerts as error event.

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        client_order_id = self._extract_client_order_id(evt)
        error_code = evt.errorCode if evt.errorCode else "UNKNOWN"
        error_msg = f"Order rejected: {error_code}"

        # Try to mark mapping as failed if clientOrderId exists
        if client_order_id:
            try:
                self._repo.fail_mapping(client_order_id, error_msg)
            except MappingNotFound:
                pass  # Mapping doesn't exist - no-op

        # Log error event (alert only, no account status change)
        self._repo.log_event(
            'slave_action',
            'error',
            {
                'action': 'order_rejected',
                'client_order_id': client_order_id,
                'error_code': error_code,
                'error': error_msg,
            },
            account_id=account_id,
        )
