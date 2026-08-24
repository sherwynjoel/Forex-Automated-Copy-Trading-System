"""Copier service: master/slave event wiring orchestration.

Orchestrates the complete flow:
- Master execution events → normalize → decide → dispatch
- Slave execution events → mapping activation/updates (never decide)
- Pending fill alerts scheduled for unmapped fills

Multi-org: every inbound execution event is first resolved to the org that
owns its account (via the routing table), and everything downstream --
which symbols normalize() sees, which slaves decide() may target, which
org's gates dispatch() reads, which org stamps the events -- is scoped to
THAT org. An account whose org cannot be resolved is logged and dropped.
"""

import logging
import time
from typing import Callable, Mapping

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType)

from copier.db.repo import Repo, MappingNotFound
from copier.domain.models import (
    MANUAL_ORDER_LABEL, SymbolInfo, MasterPendingFilled)
from copier.domain.decision import decide
from copier.engine.normalize import normalize
from copier.engine.dispatch import Dispatcher
from copier.engine.routing import OrgRouting

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
        routing_provider: Callable[[], OrgRouting],
        master_symbols_by_org: Mapping[int, dict[int, SymbolInfo]],
        clock=None,
    ):
        """Initialize CopierService.

        Args:
            repo: Repository for mappings and events.
            dispatcher: Intent dispatcher.
            routing_provider: Callable returning the current OrgRouting
                (account -> org, org -> master, org -> slave fleet). Called
                per event so a reload() takes effect without rewiring.
            master_symbols_by_org: org_id -> {symbol_id: SymbolInfo} for that
                org's master. The OUTER dict object is shared with CopierApp,
                which mutates the inner dicts in place on reload, so this
                service always sees the current symbol cache.
            clock: Optional Twisted Clock for testing; defaults to reactor.
        """
        self._repo = repo
        self._dispatcher = dispatcher
        self._routing_provider = routing_provider
        self._master_symbols_by_org = master_symbols_by_org

        if clock is None:
            from twisted.internet import reactor as clock
        self._clock = clock

        # Set by build_app() after CopierApp exists (service is constructed
        # first, so this cannot be a ctor argument). Called after any event
        # that changes positions/orders/mappings, so /state can refresh
        # immediately instead of waiting for the next periodic resync tick.
        self.on_positions_changed: Callable[[int | None], None] | None = None

    def _notify_positions_changed(self, org_id: int | None = None) -> None:
        """Invoke on_positions_changed; a callback failure must never break
        event processing."""
        if self.on_positions_changed is None:
            return
        try:
            self.on_positions_changed(org_id)
        except Exception:
            log.exception("on_positions_changed callback failed")

    def handle_execution(self, account_id: int, evt: ProtoOAExecutionEvent) -> None:
        """Handle execution event from master or slave.

        Behavior:
        - Org's own master account: normalize → decide → dispatch (that org only)
        - Any other account in a known org: update mappings only (never decide)
        - Account belonging to no known org: log and ignore

        Exception boundary: any error during event processing (DB transient failures,
        exception in normalize/decide/dispatch, etc.) is caught, logged, and the pump
        continues to the next event. No single event can crash the event stream.

        Args:
            account_id: Source account ID.
            evt: ProtoOAExecutionEvent message.
        """
        # Declared before the try so the catch-all below can stamp the error
        # event with the org whenever resolution already succeeded.
        org_id = None
        try:
            start_time = time.time_ns() // 1_000_000  # milliseconds

            routing = self._routing_provider()
            org_id = routing.org_by_account.get(account_id)
            if org_id is None:
                self._repo.log_event(
                    'drift', 'info',
                    {'action': 'event_from_unknown_account', 'account_id': account_id,
                     'execution_type': ProtoOAExecutionType.Name(evt.executionType)},
                    account_id=account_id,
                )
                return

            if routing.master_by_org.get(org_id) == account_id:
                self._handle_master_event(org_id, account_id, evt, start_time, routing)
            else:
                self._handle_slave_event(org_id, account_id, evt, routing)
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
                    org_id=org_id,
                )
            except Exception:
                # Even the error log itself failed; give up
                pass

    def _handle_master_event(
        self,
        org_id: int,
        master_account_id: int,
        evt: ProtoOAExecutionEvent,
        start_time: int,
        routing: OrgRouting,
    ) -> None:
        """Handle master account event: normalize -> decide -> dispatch.

        Everything here is scoped to `org_id`: the symbol map is that org's
        master's, the slave fleet is that org's, and the dispatch carries
        that org's id -- so an event on one org's master can never reach
        another org's accounts.

        Args:
            org_id: Org that owns this master.
            master_account_id: The master account the event came from.
            evt: ProtoOAExecutionEvent.
            start_time: Event processing start time in milliseconds.
            routing: The routing snapshot this event is being processed against.
        """
        normalized = normalize(evt, self._master_symbols_by_org.get(org_id, {}))

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

        # Decide and dispatch FIRST; the audit row is written in the
        # finally, so it can never sit as a blocking database write in
        # front of the copy handoff, and it is still written even when
        # decide/dispatch raise (the outer handler then logs the failure
        # as well). latency_ms therefore measures the whole internal
        # path: normalize -> decide -> dispatch handoff.
        try:
            if normalized is not None:
                # Decide: get intents for THIS ORG's enabled slaves only
                slaves = routing.slaves_by_org.get(org_id, [])
                intents = decide(normalized, self._repo, slaves)

                # Dispatch intents against this org's gates
                if intents:
                    self._dispatcher.dispatch(intents, org_id=org_id)
        finally:
            # Best-effort: the orders are already at the broker by now, so
            # a failed audit write must not replace the real exception, and
            # must not skip the post-dispatch bookkeeping below. A lost
            # audit row is a diagnostics gap; a skipped pending-fill check
            # is a real one.
            latency_ms = (time.time_ns() // 1_000_000) - start_time
            try:
                self._repo.log_event(
                    'master_event',
                    'info',
                    payload,
                    account_id=master_account_id,
                    latency_ms=latency_ms,
                    org_id=org_id,
                )
            except Exception:
                log.exception(
                    "master_event audit write failed (copy already dispatched)")

        # If normalization yielded no event, we're done
        if normalized is None:
            return

        # Schedule pending fill alert if this is a pending fill
        if isinstance(normalized, MasterPendingFilled):
            self._schedule_pending_fill_check(org_id, normalized)

        # The master's positions/orders just changed; let /state catch up now.
        self._notify_positions_changed(org_id)

    def _schedule_pending_fill_check(
        self, org_id: int, pending_filled: MasterPendingFilled
    ) -> None:
        """Schedule a check for unmapped slave fills after PENDING_FILL_ALERT_S.

        If any linked mapping still doesn't have slave_position_id, log warning.

        Org scoping: master order and position ids are PER-ACCOUNT broker
        sequences, so two orgs routinely hold mapping rows carrying the same
        master_order_id. `order_entries`/`position_entries` are keyed on that
        id alone -- deliberately, since they are the MappingState protocol
        decide() consumes -- so this check filters foreign rows out itself
        against the routing table, resolved at CHECK time (30s later the
        fleet may have been reloaded). Without the filter, org A's check
        would emit a pending_fill_alert naming ANOTHER org's slave account,
        stamped with org A's org_id.

        Args:
            org_id: Org that owns the master this pending fill came from.
            pending_filled: The MasterPendingFilled event.
        """
        def check_pending_fills():
            """Check if any of THIS ORG's slave fills are still pending."""
            routing = self._routing_provider()
            # Get all order mappings for this master order
            order_entries = self._repo.order_entries(pending_filled.order_id)
            for entry in order_entries:
                if routing.org_by_account.get(entry.slave_account_id) != org_id:
                    continue  # another org's row that merely shares the master id
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
                        org_id=org_id,
                    )

        self._clock.callLater(PENDING_FILL_ALERT_S, check_pending_fills)

    def _handle_slave_event(
        self,
        org_id: int,
        account_id: int,
        evt: ProtoOAExecutionEvent,
        routing: OrgRouting,
    ) -> None:
        """Handle slave account event: update mappings, log, never decide.

        Loop-proof by construction: slave events never trigger decide/dispatch.
        Gate: only accounts that are enabled slaves OF THIS ORG are processed;
        anything else (disabled, paused, a slave of a different org, an
        'ignored'-role account) is logged and ignored (no mutations).

        Args:
            org_id: Org that owns this account.
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
            routing: The routing snapshot this event is being processed against.
        """
        # Gate: only process this org's known, enabled slaves
        is_enabled_slave = any(
            s.account_id == account_id and s.enabled
            for s in routing.slaves_by_org.get(org_id, [])
        )
        if not is_enabled_slave:
            self._repo.log_event(
                'drift',  # Unknown/disabled account
                'info',
                {
                    'action': 'event_from_unknown_or_disabled_slave',
                    'account_id': account_id,
                    'execution_type': ProtoOAExecutionType.Name(evt.executionType),
                },
                account_id=account_id,
                org_id=org_id,
            )
            return

        execution_type = evt.executionType

        # Handle ORDER_FILLED and ORDER_PARTIAL_FILL
        if execution_type in (ProtoOAExecutionType.ORDER_FILLED,
                             ProtoOAExecutionType.ORDER_PARTIAL_FILL):
            self._handle_slave_fill(org_id, account_id, evt)
            self._notify_positions_changed(org_id)

        # Handle ORDER_ACCEPTED
        elif execution_type == ProtoOAExecutionType.ORDER_ACCEPTED:
            self._handle_slave_order_accepted(org_id, account_id, evt)
            self._notify_positions_changed(org_id)

        # Handle ORDER_CANCELLED
        elif execution_type == ProtoOAExecutionType.ORDER_CANCELLED:
            self._handle_slave_order_cancelled(org_id, account_id, evt)
            self._notify_positions_changed(org_id)

        # Handle ORDER_REJECTED
        elif execution_type == ProtoOAExecutionType.ORDER_REJECTED:
            self._handle_slave_order_rejected(org_id, account_id, evt)
            self._notify_positions_changed(org_id)

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
                org_id=org_id,
            )

    def _extract_client_order_id(self, evt: ProtoOAExecutionEvent) -> str | None:
        """Extract clientOrderId from event if present.

        Args:
            evt: ProtoOAExecutionEvent.

        Returns:
            clientOrderId string or None if not present.
        """
        return evt.order.clientOrderId if evt.order.HasField('clientOrderId') else None

    def _handle_slave_fill(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """Handle slave ORDER_FILLED or ORDER_PARTIAL_FILL.

        Updates:
        - clientOrderId starting "cm" → activate_position_mapping
        - closePositionDetail → reduce_position_mapping
        - slave_order_id matching order mapping → activate_pending_fill

        Args:
            org_id: Org that owns this slave.
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
                org_id=org_id,
            )
            return

        # T9c: the execution price is what the Positions screen's per-copy
        # "Fill Price" column is built from. It was already
        # being read here for the event log; it is now also handed to the
        # mapping row so GET /state can report it (see repo.mappings.fill_price
        # / db/migrations/003_mapping_fill_price.sql).
        fill_price = evt.deal.executionPrice if evt.deal.HasField('executionPrice') else None

        # Check for position fill (clientOrderId starting "cm")
        if client_order_id and client_order_id.startswith("cm"):
            try:
                self._repo.activate_position_mapping(
                    account_id, client_order_id, deal_position_id, filled_volume,
                    fill_price=fill_price,
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
                    org_id=org_id,
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
                    org_id=org_id,
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
                org_id=org_id,
            )
            return
        except MappingNotFound:
            # slave_order_id didn't match; no order mapping found
            pass

        # An operator-placed manual order's fill matches no mapping BY
        # DESIGN -- it is expected, so it must not raise the unexplained-
        # fill warning below.
        if evt.order.tradeData.label == MANUAL_ORDER_LABEL:
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'manual_fill',
                    'slave_order_id': slave_order_id,
                    'slave_position_id': deal_position_id,
                    'filled_volume': filled_volume,
                    'fill_price': fill_price,
                },
                account_id=account_id,
                org_id=org_id,
            )
            return

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
            org_id=org_id,
        )

    def _handle_slave_order_accepted(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """Handle slave ORDER_ACCEPTED with clientOrderId starting 'co'.

        Updates: activate_order_mapping

        Args:
            org_id: Org that owns this slave.
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        client_order_id = self._extract_client_order_id(evt)

        if not client_order_id or not client_order_id.startswith("co"):
            return

        slave_order_id = evt.order.orderId
        try:
            self._repo.activate_order_mapping(account_id, client_order_id, slave_order_id)
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'order_accepted',
                    'client_order_id': client_order_id,
                    'slave_order_id': slave_order_id,
                },
                account_id=account_id,
                org_id=org_id,
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
                org_id=org_id,
            )

    def _handle_slave_order_cancelled(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """Handle slave ORDER_CANCELLED on mapped order.

        Updates: close_order_mapping

        Args:
            org_id: Org that owns this slave.
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
                org_id=org_id,
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
                org_id=org_id,
            )

    def _handle_slave_order_rejected(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """Handle slave ORDER_REJECTED.

        Updates: fail_mapping with error code
        Logs: error event

        Spec: broker rejections (min-volume, margin) are NOT account status degradations.
        Only alerts as error event.

        Args:
            org_id: Org that owns this slave.
            account_id: Slave account ID.
            evt: ProtoOAExecutionEvent.
        """
        client_order_id = self._extract_client_order_id(evt)
        error_code = evt.errorCode if evt.errorCode else "UNKNOWN"
        error_msg = f"Order rejected: {error_code}"

        # Try to mark mapping as failed if clientOrderId exists
        if client_order_id:
            try:
                self._repo.fail_mapping(account_id, client_order_id, error_msg)
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
            org_id=org_id,
        )
