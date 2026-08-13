"""Copier service: master/slave event wiring orchestration.

Orchestrates the complete flow:
- Master execution events → normalize → decide → dispatch
- Slave execution events → mapping activation/updates (never decide)
- Pending fill alerts scheduled for unmapped fills
"""

import logging
import time
from typing import Callable, Mapping, Sequence

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType
)

from copier.db.repo import Repo, MappingNotFound
from copier.domain.models import (
    SymbolInfo, SlaveConfig, MasterEvent, MasterPendingFilled,
    MasterPositionClosed
)
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

    def handle_execution(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle execution event from master or slave.

        Behavior:
        - Master account: normalize → decide → dispatch
        - Slave account: update mappings only (never decide)
        - Unknown account: log and ignore

        Args:
            account_id: Source account ID.
            evt: ProtoOAExecutionEvent message.
        """
        start_time = time.time_ns() // 1_000_000  # milliseconds

        if account_id == self._master_account_id:
            self._handle_master_event(evt, start_time)
        else:
            self._handle_slave_event(account_id, evt)

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

    def _handle_slave_event(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave account event: update mappings, log, never decide.

        Loop-proof by construction: slave events never trigger decide/dispatch.

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        execution_type = evt.executionType

        # Handle ORDER_FILLED and ORDER_PARTIAL_FILL
        if execution_type in (ProtoOAExecutionType.ORDER_FILLED,
                             ProtoOAExecutionType.ORDER_PARTIAL_FILL):
            self._handle_slave_fill(account_id, evt)

        # Handle ORDER_ACCEPTED
        elif execution_type == ProtoOAExecutionType.ORDER_ACCEPTED:
            self._handle_slave_order_accepted(account_id, evt)

        # Handle ORDER_CANCELLED
        elif execution_type == ProtoOAExecutionType.ORDER_CANCELLED:
            self._handle_slave_order_cancelled(account_id, evt)

        # Handle ORDER_REJECTED
        elif execution_type == ProtoOAExecutionType.ORDER_REJECTED:
            self._handle_slave_order_rejected(account_id, evt)

    def _handle_slave_fill(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle slave ORDER_FILLED or ORDER_PARTIAL_FILL.

        Updates:
        - clientOrderId starting "cm" → activate_position_mapping
        - closePositionDetail → reduce_position_mapping
        - clientOrderId starting "co" with slave_order_id → activate_pending_fill

        Args:
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        client_order_id = evt.order.clientOrderId if evt.order.HasField('clientOrderId') else None
        deal_position_id = evt.deal.positionId
        filled_volume = evt.deal.filledVolume

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

        # Check for position fill (clientOrderId starting "cm")
        if client_order_id and client_order_id.startswith("cm"):
            try:
                self._repo.activate_position_mapping(client_order_id, deal_position_id, filled_volume)
                fill_price = evt.deal.executionPrice if evt.deal.HasField('executionPrice') else None
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

        # Check for pending order fill (clientOrderId starting "co")
        if client_order_id and client_order_id.startswith("co"):
            # This is a fill of a pending order placed by the slave
            # We need to find the matching order mapping and convert to position
            try:
                # Try to find and activate the pending fill
                # We'll use the slave_order_id from the event
                slave_order_id = evt.order.orderId
                self._repo.activate_pending_fill(account_id, slave_order_id, deal_position_id, filled_volume)
                self._repo.log_event(
                    'slave_action',
                    'info',
                    {
                        'action': 'pending_fill',
                        'client_order_id': client_order_id,
                        'slave_order_id': slave_order_id,
                        'slave_position_id': deal_position_id,
                        'filled_volume': filled_volume,
                    },
                    account_id=account_id,
                )
            except MappingNotFound:
                self._repo.log_event(
                    'slave_action',
                    'warning',
                    {
                        'action': 'pending_fill_no_mapping',
                        'client_order_id': client_order_id,
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
        client_order_id = evt.order.clientOrderId if evt.order.HasField('clientOrderId') else None

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
            # Order mapping doesn't exist or already closed - no-op
            pass

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
        client_order_id = evt.order.clientOrderId if evt.order.HasField('clientOrderId') else None
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
