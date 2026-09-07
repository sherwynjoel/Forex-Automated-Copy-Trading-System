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
import math
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType)

from copier.ctrader.symbols import by_id as symbols_by_id
from copier.db.repo import Repo, MappingNotFound
from copier.domain.models import (
    MANUAL_ORDER_LABEL, SymbolInfo, MasterEvent, MasterPendingFilled, AmendPositionSLTP,
    MasterPositionOpened, MasterPositionClosed, MasterPositionSLTPAmended)
from copier.domain.decision import decide
from copier.engine.capture import execution_row
from copier.engine.normalize import normalize
from copier.engine.dispatch import Dispatcher
from copier.engine.routing import OrgRouting

log = logging.getLogger(__name__)

PENDING_FILL_ALERT_S = 30.0

# Cap on remembered master protection levels. Entries are dropped when the
# master position closes, so this only bounds the pathological case where a
# close is never seen (a reconnect mid-trade); without it a long-lived
# process could accumulate one entry per position it ever saw.
MAX_REMEMBERED_PROTECTION = 2000


def master_position_of(client_order_id: str | None) -> int | None:
    """"cm665938284.48434542" -> 665938284, or None if it is not a copy."""
    if not client_order_id or not client_order_id.startswith("cm"):
        return None
    try:
        return int(client_order_id[2:].split(".", 1)[0])
    except ValueError:
        return None


@dataclass(frozen=True)
class SlaveFill:
    """A slave's fill in platform-neutral terms.

    Built from a ProtoOAExecutionEvent for cTrader slaves and from an MT5
    terminal's ack or deal for MT5 slaves, then handled identically by
    handle_slave_fill. `closed_volume` set means this fill CLOSED (part of)
    a position. `order_id` is the slave's own order ticket, which is how a
    filled pending copy finds its order mapping. `stop_loss`/`take_profit`
    are the protection the copy already carries when the platform reports
    it (MT5 does; cTrader fills leave them None, so the master's level is
    re-stated as before).
    """
    account_id: int
    client_order_id: str | None
    position_id: int
    filled_volume: int
    fill_price: float | None
    closed_volume: int | None
    label: str
    order_id: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


def _same_level(a: float | None, b: float | None) -> bool:
    """Two protection levels are the same when both are unset or differ by
    less than any broker's price precision."""
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-7)


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

        # Set by build_app()/CopierApp, same as on_positions_changed above:
        # resolves an account to the AccountStateTracker holding its org's
        # live quotes, so a captured execution can carry the bid/ask that
        # was live at fill time. None in unit tests, where a capture then
        # records a null quote rather than failing.
        self.state_tracker_provider: Callable[[int], object | None] | None = None

        # master position id -> (stop_loss, take_profit) currently on it.
        # See _remember_master_protection for the race this exists to close.
        self._master_protection: dict[int, tuple[float | None, float | None]] = {}

    def _remember_master_protection(self, normalized) -> None:
        """Track the master's live SL/TP per position, for copies not yet born.

        THE RACE THIS CLOSES. A market order carrying protection fills
        first and is protected second: cTrader answers with ORDER_FILLED
        whose position carries NO stopLoss, then creates the protection as
        a separate STOP_LOSS_TAKE_PROFIT order about 25ms later. The copies
        meanwhile take ~250ms to come back filled. So the fan-out for that
        second event runs while every copy is still an unfilled order,
        position_entries() returns nothing (it only sees mappings that have
        a slave_position_id), and it logs ten "slave has no mapped copy"
        warnings. The copies then fill and stay unprotected for the life of
        the trade.

        Seen on live positions: master holding 4582.79 / 4584.79 while all
        ten copies showed no stop and no target. Every copy of a protected
        market order was naked, which is the worst way for this to fail --
        the screen said the master was protected, and the risk sat on the
        accounts nobody was looking at.

        Remembering the level is what lets the fill path apply it at the
        first moment the copy actually exists.
        """
        if isinstance(normalized, MasterPositionClosed):
            self._master_protection.pop(normalized.position_id, None)
            return
        if not isinstance(normalized, (MasterPositionOpened,
                                       MasterPositionSLTPAmended)):
            return

        levels = (normalized.stop_loss, normalized.take_profit)
        if levels == (None, None):
            # A cleared amend, or an open with nothing attached. Forgetting
            # is the point: a copy filling later must not be handed
            # protection the master no longer carries.
            self._master_protection.pop(normalized.position_id, None)
            return

        if (normalized.position_id not in self._master_protection
                and len(self._master_protection) >= MAX_REMEMBERED_PROTECTION):
            # Insertion-ordered, so this drops the position seen longest
            # ago -- the one least likely to still be open.
            self._master_protection.pop(next(iter(self._master_protection)))
        self._master_protection[normalized.position_id] = levels

    def _protect_new_copy(self, account_id: int, client_order_id: str,
                          slave_position_id: int, org_id: int,
                          current: tuple[float | None, float | None] = (None, None)) -> None:
        """Give a just-filled copy the protection its master already has.

        This is the first instant the copy can be amended -- before the
        fill it has no position id, which is exactly why the master's own
        SL/TP event could not reach it. Sent even when the copy may already
        carry the level: re-stating the same stop is a no-op at the broker,
        and the alternative is guessing about a naked position.

        `current` is what the copy is KNOWN to carry (an MT5 open travels
        with its SL/TP, and the terminal reports them back): when it
        already matches the master's level, nothing is sent. cTrader fills
        pass (None, None) -- unknown -- so the level is re-stated as before.

        Never raises. A copy that opened is real money at the broker; a
        failure to protect it must be logged and must not take down the
        fill bookkeeping that follows.
        """
        master_position_id = master_position_of(client_order_id)
        if master_position_id is None:
            return
        levels = self._master_protection.get(master_position_id)
        if levels is None:
            return
        stop_loss, take_profit = levels
        if _same_level(stop_loss, current[0]) and _same_level(take_profit, current[1]):
            return
        try:
            self._dispatcher.dispatch(
                [AmendPositionSLTP(account_id, slave_position_id,
                                   stop_loss, take_profit)],
                org_id=org_id,
            )
        except Exception:
            log.exception(
                "could not protect copy %s on account %s (master %s)",
                slave_position_id, account_id, master_position_id)

    def _state_tracker_for(self, account_id: int):
        """Resolve the AccountStateTracker holding this account's live
        quotes. Wired by CopierApp; None in unit tests, which then record a
        null quote rather than failing."""
        if self.state_tracker_provider is None:
            return None
        return self.state_tracker_provider(account_id)

    def _symbols_for(
        self, org_id: int | None, account_id: int, routing: OrgRouting,
        is_master: bool,
    ) -> Mapping[int, SymbolInfo]:
        """The symbol map belonging to THE ACCOUNT THE EVENT CAME FROM.

        The master's map is the shared, mutated-in-place cache CopierApp
        keeps. A slave's is its OWN: build_routing already loaded it into
        SlaveConfig.symbols (keyed by name), so this only re-keys it by id
        -- no database round trip.

        This used to pass an empty map for every slave, so EVERY slave
        execution row stored symbol = NULL: the instrument was missing from
        exactly the rows that measure per-copy slippage, and no query over
        `executions` could group a copy with the master trade it came from
        without re-joining through mappings.

        An account that resolves to no config yields {}, which
        execution_row turns into a null name rather than dropping the row.
        """
        if is_master:
            return self._master_symbols_by_org.get(org_id, {})
        for slave in routing.slaves_by_org.get(org_id, []):
            if slave.account_id == account_id:
                return symbols_by_id(slave.symbols)
        return {}

    def _submit_execution(
        self, org_id: int | None, account_id: int, evt, is_master: bool,
        routing: OrgRouting,
    ) -> None:
        """Queue one `executions` row. Never raises: a capture failure must
        not stop a copy."""
        writer = getattr(self._repo, 'writer', None)
        if writer is None:
            return
        try:
            symbols = self._symbols_for(org_id, account_id, routing, is_master)
            # A quote we do not hold is NULL, never 0: a zero bid/ask would
            # read as a real price and silently poison every spread and
            # slippage figure computed off this table.
            quote = None
            tracker = self._state_tracker_for(account_id)
            symbol_id = evt.order.tradeData.symbolId
            if tracker is not None and symbol_id:
                quote = tracker.quote(symbol_id)
            writer.submit('executions', execution_row(
                evt, account_id=account_id, org_id=org_id,
                is_master=is_master, symbols_by_id=symbols, quote=quote))
        except Exception:
            log.exception("execution capture failed for account %s", account_id)

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
        """Handle a cTrader master account event: normalize -> act.

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
            self._audit_master_event(org_id, master_account_id, payload, start_time)
            self._submit_execution(org_id, master_account_id, evt,
                                   is_master=True, routing=routing)
            return

        self._decide_dispatch_audit(org_id, master_account_id, normalized, routing,
                                    payload, start_time)

        # The master trade's OWN economics -- price, volume, side, symbol,
        # broker clock and the quote that was live at the time. The
        # master_event log line above deliberately stays thin; this is the
        # record. Queued, not written, so it costs the reactor nothing.
        self._submit_execution(org_id, master_account_id, evt,
                               is_master=True, routing=routing)

        # Stamp the master half of the slippage measurement onto the copies.
        # STRICTLY after the dispatch above: it is one indexed UPDATE, and
        # nothing may sit between a master fill arriving and the slave
        # orders reaching the wire.
        if evt.deal.HasField('executionPrice') and evt.deal.positionId:
            self._repo.record_master_fill(
                org_id,
                evt.deal.positionId,
                evt.deal.executionPrice,
                evt.deal.executionTimestamp or None,
            )

        self._after_master_event(org_id, normalized)

    def act_on_master_event(self, org_id: int, master_account_id: int,
                            normalized: MasterEvent, *, source: str) -> None:
        """A platform-neutral master event: decide -> dispatch -> audit ->
        bookkeeping. The MT5 lane calls this with what ingress.py derived
        from a terminal's report; the cTrader path goes through
        _handle_master_event, which normalizes first and captures the
        execution row. `source` names where the event came from in the
        audit payload."""
        routing = self._routing_provider()
        start_time = time.time_ns() // 1_000_000
        payload = {'source': source, 'normalized': type(normalized).__name__}
        self._decide_dispatch_audit(org_id, master_account_id, normalized, routing,
                                    payload, start_time)
        self._after_master_event(org_id, normalized)

    def _decide_dispatch_audit(self, org_id: int, master_account_id: int,
                               normalized: MasterEvent, routing: OrgRouting,
                               payload: dict, start_time: int) -> None:
        # Before decide(), so a copy that fills during this very event's
        # dispatch already finds the level recorded.
        self._remember_master_protection(normalized)

        # Decide and dispatch FIRST; the audit row is written in the
        # finally, so it can never sit as a blocking database write in
        # front of the copy handoff, and it is still written even when
        # decide/dispatch raise (the outer handler then logs the failure
        # as well). latency_ms therefore measures the whole internal
        # path: normalize -> decide -> dispatch handoff.
        try:
            # Decide: get intents for THIS ORG's enabled slaves only
            slaves = routing.slaves_by_org.get(org_id, [])
            intents = decide(normalized, self._repo, slaves)

            # Dispatch intents against this org's gates
            if intents:
                self._dispatcher.dispatch(intents, org_id=org_id)
        finally:
            self._audit_master_event(org_id, master_account_id, payload, start_time)

    def _audit_master_event(self, org_id: int, master_account_id: int, payload: dict,
                            start_time: int) -> None:
        """Best-effort: the orders are already at the broker by now, so a
        failed audit write must not replace the real exception, and must
        not skip the post-dispatch bookkeeping. A lost audit row is a
        diagnostics gap; a skipped pending-fill check is a real one."""
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

    def _after_master_event(self, org_id: int, normalized: MasterEvent) -> None:
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

        # This slave's own half of the trade, carrying ITS symbol and ITS
        # fill -- the row the master's execution is compared against.
        self._submit_execution(org_id, account_id, evt,
                               is_master=False, routing=routing)

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
        """A cTrader slave's ORDER_FILLED / ORDER_PARTIAL_FILL, as a SlaveFill."""
        deal = evt.deal
        self.handle_slave_fill(org_id, SlaveFill(
            account_id=account_id,
            client_order_id=self._extract_client_order_id(evt),
            position_id=deal.positionId,
            filled_volume=deal.filledVolume,
            # T9c: the execution price is what the Positions screen's
            # per-copy "Fill Price" column is built from (see
            # repo.mappings.fill_price / db/migrations/003_mapping_fill_price.sql).
            fill_price=deal.executionPrice if deal.HasField('executionPrice') else None,
            closed_volume=(deal.closePositionDetail.closedVolume
                           if deal.HasField('closePositionDetail') else None),
            label=evt.order.tradeData.label,
            order_id=evt.order.orderId,
        ))

    def handle_slave_fill(self, org_id: int, fill: SlaveFill) -> None:
        """A slave's fill, from whichever platform reported it.

        - closed_volume set -> reduce_position_mapping; pinned to the one mapping
          when the fill names a "cm" client_order_id (a netting MT5 follower's
          copies share one net ticket, and the outbox's ':close' ack names the
          copy it closed), else every active mapping on that position as before
        - client_order_id "cm..." -> activate_position_mapping (+ the master's protection)
        - else a pending copy filling: activate_pending_fill by the slave's order ticket
        - else an operator's manual order (expected) or an unmatched fill (warning)
        """
        account_id = fill.account_id

        if fill.closed_volume is not None:
            coid = fill.client_order_id
            coid = coid if coid and coid.startswith("cm") else None
            self._repo.reduce_position_mapping(
                account_id, fill.position_id, fill.closed_volume, client_order_id=coid)
            payload = {
                'action': 'position_closed',
                'slave_position_id': fill.position_id,
                'closed_volume': fill.closed_volume,
            }
            if coid:
                payload['client_order_id'] = coid
            self._repo.log_event('slave_action', 'info', payload,
                                 account_id=account_id, org_id=org_id)
            return

        client_order_id = fill.client_order_id
        if client_order_id and client_order_id.startswith("cm"):
            try:
                self._repo.activate_position_mapping(
                    account_id, client_order_id, fill.position_id, fill.filled_volume,
                    fill_price=fill.fill_price,
                )
                self._repo.log_event(
                    'slave_action',
                    'info',
                    {
                        'action': 'position_filled',
                        'client_order_id': client_order_id,
                        'slave_position_id': fill.position_id,
                        'filled_volume': fill.filled_volume,
                        'fill_price': fill.fill_price,
                    },
                    account_id=account_id,
                    org_id=org_id,
                )
                # The copy exists now, so the master's protection can
                # finally reach it -- it could not when the master's own
                # SL/TP event fired, a quarter of a second ago.
                self._protect_new_copy(
                    account_id, client_order_id, fill.position_id, org_id,
                    current=(fill.stop_loss, fill.take_profit))
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

        # Check for pending order fill: match by the slave's own order ticket
        try:
            self._repo.activate_pending_fill(
                account_id, fill.order_id, fill.position_id, fill.filled_volume,
                fill_price=fill.fill_price,
            )
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'pending_fill',
                    'client_order_id': client_order_id,
                    'slave_order_id': fill.order_id,
                    'slave_position_id': fill.position_id,
                    'filled_volume': fill.filled_volume,
                    'fill_price': fill.fill_price,
                },
                account_id=account_id,
                org_id=org_id,
            )
            return
        except MappingNotFound:
            # order ticket didn't match; no order mapping found
            pass

        # An operator-placed manual order's fill matches no mapping BY
        # DESIGN -- it is expected, so it must not raise the unexplained-
        # fill warning below.
        if fill.label == MANUAL_ORDER_LABEL:
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'manual_fill',
                    'slave_order_id': fill.order_id,
                    'slave_position_id': fill.position_id,
                    'filled_volume': fill.filled_volume,
                    'fill_price': fill.fill_price,
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
                'slave_order_id': fill.order_id,
                'slave_position_id': fill.position_id,
                'client_order_id': client_order_id,
                'reason': 'No matching position or order mapping',
            },
            account_id=account_id,
            org_id=org_id,
        )

    def _handle_slave_order_accepted(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """A cTrader slave's ORDER_ACCEPTED."""
        self.handle_slave_order_accepted(
            org_id, account_id, self._extract_client_order_id(evt), evt.order.orderId)

    def handle_slave_order_accepted(
        self, org_id: int, account_id: int, client_order_id: str | None, slave_order_id: int
    ) -> None:
        """A pending copy the slave's platform accepted: clientOrderId 'co...'
        -> activate_order_mapping. Anything else (an operator's own order)
        has nothing to link."""
        if not client_order_id or not client_order_id.startswith("co"):
            return

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
        """A cTrader slave's ORDER_CANCELLED."""
        self.handle_slave_order_cancelled(org_id, account_id, evt.order.orderId)

    def handle_slave_order_cancelled(
        self, org_id: int, account_id: int, slave_order_id: int
    ) -> None:
        """A mapped pending copy is gone: close_order_mapping."""
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
        """A cTrader slave's ORDER_REJECTED."""
        error_code = evt.errorCode if evt.errorCode else "UNKNOWN"
        self.handle_slave_rejection(
            org_id, account_id, self._extract_client_order_id(evt),
            f"Order rejected: {error_code}", error_code=error_code)

    def handle_slave_rejection(
        self, org_id: int, account_id: int, client_order_id: str | None, reason: str,
        error_code: str | None = None,
    ) -> None:
        """The slave's platform refused a copy: fail its mapping (if any) and
        log an error event. Spec: broker rejections (min-volume, margin) are
        NOT account status degradations here -- the MT5 lane sets its own
        degraded status with the terminal's reason (main.py)."""
        if client_order_id:
            try:
                self._repo.fail_mapping(account_id, client_order_id, reason)
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
                'error': reason,
            },
            account_id=account_id,
            org_id=org_id,
        )
