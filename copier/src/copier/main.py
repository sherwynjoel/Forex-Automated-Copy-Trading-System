"""Boot sequence and composition root for the copier service.

Wires together all components:
- CTraderClient per environment (demo/live), sharded across N connections
- TokenStore for OAuth token management + daily refresh loop
- Repo for database access
- CopierService for event orchestration (org-routed)
- Reconciler for drift detection -- ONE PER ORG
- AccountStateTracker for balance/equity tracking -- ONE PER ORG
- Dispatcher with rate limiting
- Control endpoint for operational commands (control.py)

Multi-org: the process is shared, the ENGINE is not. Every org with a master
gets its own Reconciler, AccountStateTracker and master symbol map, keyed by
org_id in `reconcilers` / `state_trackers` / `master_symbols_by_org`; the
clients, dispatcher and token store stay process-wide (they are transport,
not tenancy). Every control operation therefore takes the org it acts on, and
none of them may read or write another org's accounts or settings --
`close_all` in particular is the kill switch, so its org boundary is a
correctness requirement, not a convenience.

`build_app()` is the single place that wires the dependency graph together, in
the correct order (repo -> token_store -> clients -> dispatcher(send_for_account)
-> service -> per-org reconcilers(dispatcher) -> per-org state_trackers ->
CopierApp). No component is ever constructed with a placeholder/None dependency
and patched afterward: `send_for_account`/`clients_by_account` close over the
`repo` and the (fully-built-before-use) `clients` dict directly, so they can be
handed to Dispatcher/Reconciler before CopierApp itself exists.

`boot()` wires that app into a (possibly fake, for tests) reactor: it never
calls `reactor.run()` itself, which keeps it unit-testable. `main()` is the
thin, untestable sliver that reads os.environ, builds the real reactor-backed
client factory, calls boot(), and runs the reactor forever.
"""

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from twisted.internet import defer, task
from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOACancelOrderReq, ProtoOAAmendPositionSLTPReq, ProtoOAClosePositionReq, ProtoOAErrorRes,
    ProtoOAGetAccountListByAccessTokenReq, ProtoOANewOrderReq,
    ProtoOARefreshTokenReq, ProtoOAReconcileReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOANotificationType, ProtoOAOrderType, ProtoOATradeSide,
)

from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.ctrader.symbols import fetch_symbol_map, by_id as symbols_by_id
from copier.db.repo import Repo
from copier.db.writer import AsyncWriter
from copier.domain.models import MANUAL_ORDER_LABEL, Side
from copier.engine.service import CopierService
from copier.engine.reconcile import Reconciler
from copier.engine.routing import OrgRouting, RoutingCache, build_routing
from copier.engine.state import AccountStateTracker, PositionSnapshot as StatePositionSnapshot
from copier.engine.backfill import next_window, reached_history_bound, WEEK_MS
from copier.engine.dispatch import Dispatcher, relative_protection, SendNotAttempted
from copier.engine.throttle import TokenBucket
from copier.engine.control import make_control_site
from copier.engine import queries
from copier.engine.analytics import compute_analytics
from copier.engine.commission import round_trip_rates

log = logging.getLogger(__name__)

DEFAULT_SHARDS = 1
TOKEN_REFRESH_INTERVAL_S = 86400.0  # once per day
# N9: how often to re-read every enabled account's balance from the broker.
# Balance changes on every REALIZED close, so a boot-time-only read (which
# is all there was) went stale within hours of live trading -- and stayed
# plausible-looking, because open P&L kept moving underneath it, so
# Overview's balance/equity quietly drifted away from reality rather than
# visibly freezing. 60 s is one ProtoOATraderReq per enabled account per
# minute; those drain on the SDK's 5 msg/s queued path, which for the
# ~50-account target is ~10 s of queue every minute -- comfortably clear,
# and it never touches the instant-write trade path.
BALANCE_REFRESH_INTERVAL_S = 60.0

# Intraday balance/equity series. portfolio_snapshots stays daily-close.
BALANCE_SAMPLE_INTERVAL_S = 5 * 60

RESYNC_INTERVAL_S = 60.0
RESYNC_DEBOUNCE_S = 0.2

# How often to re-derive each master's real commission from its own closed
# trades, and how far back to look. Commission is a term of the account's
# agreement with the broker: it changes when that agreement does, which is
# to say almost never, so six hours is frequent enough to notice and rare
# enough to be invisible on the wire. The window is SEVEN DAYS because
# ProtoOADealListReq will not serve more than a week per request -- the
# same limit the History page states to the operator.
COMMISSION_REFRESH_INTERVAL_S = 21600.0
COMMISSION_HISTORY_DAYS = 7
# ...and how soon after a position changes the rate may be re-read.
#
# The six-hourly loop keeps a KNOWN rate current, which is the easy half.
# Acquiring the FIRST one is the half that matters: an account connected
# five minutes ago has no rate, so every amount-denominated stop on it is
# uncorrected until the loop next comes round -- and this fleet is
# reconnected often enough that "within six hours" meant "never" in
# practice. A position change is the only moment a new round trip can have
# appeared, so it is the only moment worth looking again.
#
# Debounced hard because a single trade produces a burst of position
# changes (master fill, then one per slave, then the closes), and deal
# history is far too heavyweight to ride every one of them.
COMMISSION_LEARN_DEBOUNCE_S = 300.0

# How long the org-wide close_all keeps copying transiently paused AFTER the
# flatten, so the master's close executions drain without fanning out as
# copy-closes against slave positions that were just closed directly. This
# is a heuristic, not a drain signal (closes leave through the rate-limit
# bucket, so a big book can still be sending when the timer starts): a
# straggler master execution after the restore merely attempts to close an
# already-closed copy and lands in the per-intent error path -- log noise,
# no money moved -- because slave mapping updates are not kill-switch-gated.
CLOSE_ALL_RESUME_GRACE_S = 2.0
# The kill switch closes, then CHECKS, then closes what is left. These bound
# that loop.
#
# It used to count the close requests it SENT and report them as positions
# closed. On 28 Aug an operator pressed it and was told 66 positions across
# four accounts were closed; the broker had refused all 66 with
# BLOCKED_PAYLOAD_TYPE and every one was still open. A kill switch that
# reports success while the risk is still on is worse than one that fails
# loudly, because the operator stops looking.
#
# Verifying is the only option available: ProtoOAClosePositionReq carries no
# clientOrderId (it has three fields -- account, position, volume), and
# ProtoOAErrorRes names only an account, never a position. So a refusal
# cannot be attributed to the close that caused it. What CAN be known is
# what is still open, and the only way to know it is to ask again.
FLATTEN_ROUNDS = 3
# The flatten's own throttle. Rate is half the shared bucket's and the burst
# is a twentieth of it, both on purpose.
#
# TokenBucket starts full (throttle.py), so sending a flatten through the
# shared 40-token bucket puts up to forty requests on the wire in ONE reactor
# turn. The production logs show precisely that: thirteen
# BLOCKED_PAYLOAD_TYPE rejections inside a single millisecond, then more at
# 25ms intervals as the 40/s drain took over. Whatever BLOCKED_PAYLOAD_TYPE
# turns out to mean -- the SDK ships no documentation for it, and cTrader has
# a separate REQUEST_FREQUENCY_EXCEEDED code -- a wall of simultaneous
# requests is what preceded it every time.
#
# Shared across the whole flatten rather than per account, so eleven accounts
# closing at once cannot multiply into eleven times the wire rate.
FLATTEN_RATE = 20.0
FLATTEN_BURST = 4.0
# Time for a round's closes to reach the broker and take effect before asking
# what survived. Doubles per round: a broker refusing a burst needs longer,
# not a faster retry.
FLATTEN_SETTLE_S = 0.6
# Admin-set account cutoffs: remind this many calendar days before the date.
# The scan is one cheap SQL query, so hourly keeps the reminder prompt
# without waiting up to a day like the token-refresh cadence would.
CUTOFF_REMINDER_DAYS = 2
CUTOFF_REMINDER_INTERVAL_S = 3600.0
# Daily. Migration 012 creates a year of partitions up front; this only has
# to keep the horizon from shrinking.
PARTITION_MAINTENANCE_INTERVAL_S = 24 * 60 * 60
CONTROL_PORT = 8080
# One account, one window per tick. Deliberately slow: the broker caps
# DealList at a week and 500 rows, and the queued send path is 10 msg/s.
DEAL_BACKFILL_INTERVAL_S = 30
DEAL_BACKFILL_MAX_YEARS = int(os.environ.get("DEAL_BACKFILL_MAX_YEARS", "2"))
# A window the broker answers with has_more is bisected and re-fetched
# rather than skipped (advancing the watermark past it would lose those
# deals forever). Floor and request budget so a pathologically dense
# account cannot turn one tick into an unbounded fan-out: a week bisected
# to one hour is 168 leaves, which needs far fewer than 64 requests unless
# EVERY hour of it holds 500+ deals.
DEAL_WINDOW_MIN_MS = 60 * 60 * 1000
DEAL_WINDOW_MAX_FETCHES = 64
# Row ceiling on the analytics read. Analytics runs SYNCHRONOUSLY on the
# reactor connection (there is no pool on this base -- see the porting
# spec's D1/D3), so an account with years of history must not be able to
# stall every org's quotes and order sends on one unbounded fetch.
ANALYTICS_ROW_LIMIT = 50_000
_SIDE_BY_NAME = {"BUY": ProtoOATradeSide.BUY, "SELL": ProtoOATradeSide.SELL}
# The domain Side, for helpers that reason about direction.
_SIDE_BY_NAME_DOMAIN = {"BUY": Side.BUY, "SELL": Side.SELL}

# Fat-finger ceiling for MANUAL orders (the trade ticket). A manual order on
# the master fans out to every slave scaled by each one's multiplier, so a
# mistyped 100 instead of 1.00 moves the whole fleet's real money. Copied
# trades are NOT subject to this -- they mirror whatever the master did.
def _max_manual_lots() -> float:
    """Never let a mistyped guard value take the trading engine down: a bad
    MAX_MANUAL_ORDER_LOTS falls back to the default and says so."""
    raw = os.environ.get("MAX_MANUAL_ORDER_LOTS", "10")
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    log.warning("MAX_MANUAL_ORDER_LOTS=%r is not a positive number; using 10", raw)
    return 10.0


MAX_MANUAL_ORDER_LOTS = _max_manual_lots()
_ORDER_TYPE_BY_NAME = {
    "MARKET": ProtoOAOrderType.MARKET,
    "LIMIT": ProtoOAOrderType.LIMIT,
    "STOP": ProtoOAOrderType.STOP,
}
# Docker-internal only: host isolation comes from compose NOT publishing this
# port, not from binding loopback. Binding 127.0.0.1 would make the endpoint
# unreachable from other containers on the bridge network (e.g. api -> copier).
CONTROL_BIND_INTERFACE = "0.0.0.0"


class CopierApp:
    """Composition root: wires all copier components together.

    One process, many tenants: `reconcilers`, `state_trackers` and
    `master_symbols_by_org` are keyed by org_id and hold an entry for exactly
    the orgs that currently have a master account. Orgs without a master have
    no engine at all (they can still be read: get_state() answers empty).
    """

    def __init__(
        self,
        repo: Repo,
        token_store: TokenStore,
        clients: dict[bool, dict[int, CTraderClient]],
        service: CopierService,
        reconcilers: dict[int, Reconciler],
        state_trackers: dict[int, "AccountStateTracker | None"],
        dispatcher: Dispatcher,
        client_factory: Callable[[bool], CTraderClient],
        shards: int,
        master_symbols_by_org: dict[int, dict],
        routing_provider: Callable[[], OrgRouting],
        clients_by_account: Callable[[int], CTraderClient],
        clock=None,
    ):
        self.repo = repo
        self.token_store = token_store
        self.clients = clients
        self.service = service
        self.reconcilers = reconcilers
        self.state_trackers = state_trackers
        self.dispatcher = dispatcher
        self.client_factory = client_factory
        self.shards = shards
        self.master_symbols_by_org = master_symbols_by_org
        self.routing_provider = routing_provider
        # Debounces the position-change-triggered commission read. None,
        # not 0.0, because "never" is not a time: against the reactor's
        # epoch seconds 0.0 happens to read as long ago, but against a
        # clock that starts at zero it reads as just now and swallows the
        # very first look -- the one that matters most, on an account
        # connected while the process was already running.
        self._last_commission_learn: float | None = None
        # Built lazily on first use so an App constructed in a test without
        # a clock still works; see FLATTEN_RATE for why it is separate from
        # the shared throttle.
        self._flatten_bucket = None
        # Required, not optional: reload() builds an engine with it the
        # moment an org acquires a master after boot, and a None here would
        # produce a Reconciler that cannot reach any client at all. Same
        # callable the pre-existing reconcilers were built with (it closes
        # over repo/clients, not over this app -- see
        # _build_clients_by_account), so it is a real ctor dependency rather
        # than something patched on afterwards.
        self._clients_by_account = clients_by_account
        self.clock = clock
        self._resync_in_flight = False
        # Debounce keys: an org id, or None for the fleet-wide sweep.
        self._resync_requested: set = set()
        self._org_resync_in_flight: set = set()
        # PER-ORG intraday balance-sampling clock (see
        # _refresh_balances_body). A single process-wide float let one
        # actively-trading org's fill-driven, org-scoped refresh consume
        # the fleet's 5-minute slot, so every other org went unsampled.
        self._last_balance_sample: dict[int, float] = {}
        # Rotating start offset for the deal-backfill walk; see
        # backfill_deals_once. Advanced every tick so no single account can
        # hold the one-window-per-tick slot.
        self._backfill_cursor = 0

    @property
    def writer(self):
        """The repo's AsyncWriter, or None when nothing is attached.

        Read through the repo rather than snapshotted in the ctor: boot()
        attaches the writer before build_app() runs, but tests routinely
        attach one to an app that already exists, and a stale None here
        would report a healthy writer as absent on /health.
        """
        return getattr(self.repo, "writer", None)

    def reconciler_for(self, org_id: int) -> Reconciler:
        """The org's engine, or ValueError if it has none (no master)."""
        reconciler = self.reconcilers.get(org_id)
        if reconciler is None:
            raise ValueError(f"org {org_id} has no active engine (no master?)")
        return reconciler

    def _require_account_in_org(self, org_id: int, account_id: int) -> None:
        """Guard every account-scoped control action with its tenancy check.

        The copier is the last line of defence for the kill switch: an
        account id that does not belong to `org_id` must fail loudly here,
        never be acted on, whatever the caller believed.
        """
        account = next(
            (a for a in self.repo.load_accounts() if a.account_id == account_id), None)
        if account is None or account.org_id != org_id:
            raise ValueError(f"account {account_id} not in org {org_id}")

    def _org_for_account(self, account_id: int) -> int | None:
        """Which org owns this broker account, or None if it is unknown.

        Push events arrive keyed only by ctidTraderAccountId, so every
        consumer of one has to resolve tenancy itself before writing
        anything org-scoped.

        Goes through the repo's single-row lookup rather than
        routing_provider(): the latter rebuilds the whole OrgRouting, which
        loads every account AND a symbol cache per slave -- an N+1 of
        connections on a path that fires on every pushed balance change.
        Freshness is unchanged; both read the DB on every call.
        """
        try:
            return self.repo.org_for_account(account_id)
        except Exception:
            log.exception("failed to resolve org for account %s", account_id)
            return None

    def state_tracker_for_account(self, account_id: int):
        """The state tracker for whichever org owns this account.

        Wired onto service.state_tracker_provider so a captured execution
        (see CopierService._submit_execution) can read the bid/ask that was
        live on that org's connection at fill time.

        Goes through routing_provider(), which on this base is a
        RoutingCache: the snapshot it serves is at most one second old and
        costs no database round trip, so this is cheap enough to sit on the
        execution path.
        """
        org_id = self.routing_provider().org_by_account.get(account_id)
        if org_id is None:
            return None
        return self.state_trackers.get(org_id)

    # ---------- client event wiring ----------

    def wire_client(self, client: CTraderClient) -> None:
        """Attach every push-event consumer to a client.

        The single wiring site for all three client-creation paths
        (build_app, reload's new-environment clients, discover's bootstrap
        client) -- so a newly built client can never silently miss a
        consumer the others have.
        """
        client.on_execution(self.service.handle_execution)
        client.on_tokens_invalidated(lambda _ids: self.refresh_due_tokens())
        client.on_trader_updated(self._on_trader_updated)
        client.on_margin_call(self._on_margin_call)
        client.on_order_error(self._on_order_rejected)

    def _on_order_rejected(self, evt) -> None:
        """A broker order rejection becomes a visible warning event: the
        events NOTIFY pushes it over the ws, the Trade page surfaces it as
        the order-failure banner, and the Logs page keeps it. Before this,
        a weekend MARKET_CLOSED order looked exactly like success."""
        account_id = getattr(evt, 'ctidTraderAccountId', None)
        account = next(
            (a for a in self.repo.load_accounts() if a.account_id == account_id),
            None)
        try:
            self.repo.log_event(
                'control', 'warning',
                {
                    'action': 'order_rejected',
                    'error_code': str(getattr(evt, 'errorCode', '') or 'UNKNOWN'),
                    'order_id': getattr(evt, 'orderId', 0) or None,
                },
                account_id=account_id,
                org_id=account.org_id if account else None,
            )
        except Exception:
            log.exception("order-rejection event logging failed")

    def _on_trader_updated(self, evt) -> None:
        """Pushed balance change: apply immediately, no waiting for the poll.

        Routed to the OWNING org's tracker: trackers are per-org and each
        one's snapshot feeds that org's /state, so applying a balance to the
        wrong tracker would show one tenant's money on another's Overview.
        """
        account_id = evt.ctidTraderAccountId
        org_id = self._org_for_account(account_id)
        if org_id is None:
            # Not silent: a balance push for an account we cannot place is
            # a real anomaly (an account deleted mid-flight, or a client
            # still authed for a grant that is gone), and dropping it means
            # that account's Overview balance quietly goes stale until the
            # 60s poll -- which would also skip it.
            log.warning(
                "trader update for account %s: no owning org, balance dropped",
                account_id)
            return
        tracker = self.state_trackers.get(org_id)
        if tracker is not None:
            tracker.on_trader_updated(evt)

    def _on_margin_call(self, evt) -> None:
        """Broker margin call: a 'risk' error event -- Logs, the dashboard
        banner, and the email alerter all key off it.

        Stamped with the account's org: the events feed hides NULL-org rows,
        so an unstamped margin call would never reach the desk that owns the
        account being margin-called.
        """
        mc = evt.marginCall
        account_id = evt.ctidTraderAccountId
        org_id = self._org_for_account(account_id)
        if org_id is None:
            # Still logged (a margin call is never dropped), but loudly:
            # routes/events.py filters NULL-org rows out, so this one will
            # not reach any dashboard banner.
            log.warning(
                "margin call for account %s: no owning org, the event will "
                "not be visible on any dashboard", account_id)
        try:
            self.repo.log_event(
                'risk', 'error',
                {'action': 'margin_call',
                 'margin_call_type': ProtoOANotificationType.Name(mc.marginCallType),
                 'margin_level_threshold': mc.marginLevelThreshold},
                account_id=account_id,
                org_id=org_id,
            )
        except Exception:
            log.exception("failed to log margin call event")

    # ---------- lifecycle ----------

    @defer.inlineCallbacks
    def startup(self):
        """Load accounts, connect/auth per environment, fetch symbols, reconcile."""
        accounts = self.repo.load_accounts()
        log.info("startup: loaded %d accounts", len(accounts))
        if not accounts:
            log.warning("startup: no accounts configured; service will idle")
            self.repo.log_event('connection', 'warning', {'message': 'no accounts configured'})
            return

        yield self._connect_and_authorize(accounts)
        # force=True: a fresh process boot should always see the broker's
        # CURRENT symbol set, never a stale DB cache from a previous
        # process lifetime (see _fetch_and_cache_symbols' docstring for why
        # reload(), unlike startup(), does not force this).
        yield self._fetch_and_cache_symbols(accounts, force=True)

        try:
            yield self.resync()
        except Exception:
            log.exception("startup: initial reconciliation failed")
            self.repo.log_event('connection', 'error', {'action': 'initial_reconcile_failed'})

        yield self.refresh_balances()
        # AFTER authorization, not alongside it. Started as its own
        # now=True LoopingCall this raced _connect_and_authorize and asked
        # the broker for deal history 0.3s before the account was
        # authorized, which comes back INVALID_REQUEST -- so the copier
        # booted with no rate at all and every amend stayed uncorrected
        # until the next six-hourly tick.
        yield self.refresh_commission_rates()

        log.info("startup: complete")

    def refresh_balances(self, org_id: int | None = None) -> defer.Deferred:
        """Re-read every enabled account's balance/equity from the broker.

        N9: this used to happen exactly once, inline in startup(). Balance
        moves on every realized close, so Overview's balance and equity for
        master and slaves were stale within hours of live trading. Now
        called from startup(), from resync() (so an operator-triggered
        resync also refreshes what they are about to look at), and on a
        BALANCE_REFRESH_INTERVAL_S LoopingCall wired in boot().

        `org_id=None` sweeps every org (startup, the periodic loop). An
        operator-triggered resync passes ITS org, for the same reason
        resync() itself is org-scoped: one tenant pressing Resync must not
        put a ProtoOATraderReq on every other tenant's accounts. It also
        keeps the call's latency proportional to the org, not to the whole
        deployment -- the SDK paces the wire at 5 messages/second, so a
        process-wide sweep on every single-org resync grows the operator's
        (and the api proxy's) wait with every org added.

        Never raises or errbacks: it is a LoopingCall body, and a
        LoopingCall whose Deferred fails stops looping permanently -- a
        single transient broker hiccup would otherwise silently end balance
        refreshing for the life of the process.
        """
        d = defer.maybeDeferred(self._refresh_balances_body, org_id)
        d.addErrback(lambda f: log.error("refresh_balances: unexpected failure: %s", f))
        return d

    @defer.inlineCallbacks
    def _refresh_balances_body(self, only_org_id: int | None = None):
        if not self.state_trackers:
            return
        try:
            accounts = self.repo.load_accounts()
        except Exception:
            log.exception("refresh_balances: failed to load accounts")
            return

        # The intraday balance/equity sample is gated PER ORG.
        #
        # A single process-wide clock starved every org but one, because
        # this body is also called org-scoped: resync(org_id) ends with
        # refresh_balances(org_id) (see below), and request_resync fires on
        # every position change. Org A trading actively would land one of
        # those fill-driven, A-only invocations on the 5-minute gate inside
        # every window: the clock was stamped, the loop then `continue`d
        # past orgs B..N, and B..N never got an intraday sample AT ALL.
        # Gating per org is safe here in a way it was not with one clock --
        # each org's gate is only ever opened and stamped by that org.
        #
        # routing_provider() is resolved lazily and at most once per pass.
        now = time.monotonic()
        routing = None

        for org_id, tracker in self.state_trackers.items():
            if tracker is None:
                continue
            if only_org_id is not None and org_id != only_org_id:
                continue
            # Each org's tracker only ever reads ITS OWN accounts: the
            # tracker's snapshot feeds that org's /state, so a foreign
            # account id here would leak one tenant's balance into another's
            # Overview.
            org_accounts = [a for a in accounts if a.org_id == org_id and a.enabled]
            enabled_ids = [a.account_id for a in org_accounts]
            if not enabled_ids:
                continue
            try:
                # refresh_balances() fans out one ProtoOATraderReq per
                # account and DeferredLists them; the SDK queue paces the
                # wire. Each request must ride ITS account's environment
                # client -- an org can mix demo and live accounts, and the
                # master's client only serves the master's environment.
                clients_by_account = {}
                for a in org_accounts:
                    client = self._client_for_account(a)
                    if client is not None:
                        clients_by_account[a.account_id] = client
                yield tracker.refresh_balances(
                    enabled_ids, clients_by_account=clients_by_account)
            except Exception:
                log.exception("refresh_balances: broker request failed (org %s)", org_id)
                continue

            # Fetched once and shared by the daily snapshot write below and
            # the intraday balance-sample write further down, so a failure
            # in either write never leaves `snapshot` undefined for the
            # other. Defaults to empty on failure so both writes below are
            # no-ops rather than raising out of the per-org loop.
            try:
                snapshot = tracker.snapshot()
            except Exception:
                log.exception("refresh_balances: snapshot read failed (org %s)", org_id)
                snapshot = {}

            # Daily portfolio snapshot: upsert each refreshed account under
            # today's UTC date -- the last write of a day wins, so yesterday's
            # rows hold yesterday's closing values (Overview's vs-yesterday
            # comparison reads them). org_id comes off the AccountRow being
            # iterated so each desk's overview sums only its own snapshots.
            try:
                today = datetime.utcnow().date()
                for account in org_accounts:
                    state = snapshot.get(account.account_id)
                    if state is None or state.get("balance") is None:
                        continue
                    self.repo.save_portfolio_snapshot(
                        today, account.account_id, state["balance"],
                        state.get("equity"), org_id=account.org_id)
            except Exception:
                log.exception("refresh_balances: snapshot write failed (org %s)", org_id)

            # Intraday balance/equity series, sampled every
            # BALANCE_SAMPLE_INTERVAL_S rather than every poll.
            # portfolio_snapshots above stays daily-close.
            due = (now - self._last_balance_sample.get(org_id, 0.0)
                   >= BALANCE_SAMPLE_INTERVAL_S)
            if due:
                try:
                    # Inside the try, and stamped only after the writes
                    # land: a routing_provider() or repo failure must not
                    # burn this org's 5-minute slot as well as losing the
                    # sample.
                    if routing is None:
                        routing = self.routing_provider()
                    for account_id, snap in snapshot.items():
                        self.repo.record_balance_sample(
                            account_id,
                            routing.org_by_account.get(account_id),
                            snap.get("balance"),
                            snap.get("equity"),
                            # margin_used stays NULL: the state tracker parses
                            # only ProtoOATrader.balance and derives equity
                            # from it. The broker exposes usedMargin per
                            # position, but PositionSnapshot does not carry it,
                            # and threading a new field through the reconcile
                            # path is trade-critical work this task should not
                            # take on. The column exists so it can be
                            # backfilled later without a migration.
                            None,
                            snap.get("open_pnl"),
                        )
                    self._last_balance_sample[org_id] = now
                except Exception:
                    log.exception(
                        "refresh_balances: balance sample write failed (org %s)", org_id)

    def _maybe_learn_commission(self) -> None:
        """Re-read commission after a position change, at most every
        COMMISSION_LEARN_DEBOUNCE_S. See that constant for why."""
        clock = self.clock
        if clock is None:
            from twisted.internet import reactor as clock
        last = self._last_commission_learn
        if last is not None and clock.seconds() - last < COMMISSION_LEARN_DEBOUNCE_S:
            return
        self.refresh_commission_rates()

    def refresh_commission_rates(self) -> defer.Deferred:
        """Re-derive every master's real commission from its own trades.

        A money-denominated stop inverts the P&L formula, which yields
        GROSS profit; the broker's commission then comes out of it, so a
        "$1.50" target paid $1.26 and a "$1.50" stop lost $1.78. Correcting
        that needs the commission, and the only trustworthy source is the
        account's own history -- see engine/commission.py for why the
        symbol record cannot be read instead.

        MASTERS ONLY, deliberately. The operator sets the amount on the
        master and every copy mirrors the resulting PRICE, so the master's
        rate is the one the arithmetic uses. Deriving it for all fifty
        accounts would put fifty ProtoOADealListReq on the wire to answer a
        question nothing asks.

        Never raises or errbacks: it is a LoopingCall body, and a
        LoopingCall whose Deferred fails stops looping permanently.
        """
        clock = self.clock
        if clock is None:
            from twisted.internet import reactor as clock
        self._last_commission_learn = clock.seconds()
        d = defer.maybeDeferred(self._refresh_commission_rates_body)
        d.addErrback(lambda f: log.error("commission refresh: unexpected failure: %s", f))
        return d

    @defer.inlineCallbacks
    def _refresh_commission_rates_body(self):
        try:
            accounts = self.repo.load_accounts()
        except Exception:
            log.exception("commission refresh: failed to load accounts")
            return
        masters = [a for a in accounts if a.role == 'master' and a.enabled]
        if not masters:
            return

        to_ms = int(time.time() * 1000)
        from_ms = to_ms - int(COMMISSION_HISTORY_DAYS * 86400 * 1000)
        for account in masters:
            try:
                history = yield self.get_deal_history(
                    account.account_id, from_ms, to_ms)
            except queries.QueryFailed as exc:
                # The broker declining history for one account is an
                # expected, non-actionable answer -- a demo account with
                # none, a grant without the right -- and it is already
                # handled: the rate stays unknown and protection behaves
                # exactly as it did before. One line, not a traceback
                # every six hours for the life of the process.
                log.warning("commission refresh: %s (account %s)",
                            exc, account.account_id)
                continue
            except Exception:
                # One master's broker failing must not stop the others,
                # and must not stop the loop.
                log.exception("commission refresh: deal history failed (account %s)",
                              account.account_id)
                continue

            rates = round_trip_rates(history.get("deals") or [])
            if not rates:
                # No complete round trip in the window. Nothing is written,
                # so any rate already learned stays in force.
                continue
            try:
                self.repo.save_commission_rates(account.account_id, rates)
            except Exception:
                log.exception("commission refresh: save failed (account %s)",
                              account.account_id)
                continue
            log.info(
                "commission refresh: account %s learned %d symbol rate(s): %s",
                account.account_id, len(rates),
                ", ".join(f"symbol {sid}={per_unit:.6g}/unit over {n} trade(s)"
                          for sid, (per_unit, n) in sorted(rates.items())),
            )

    @defer.inlineCallbacks
    def _connect_and_authorize(self, accounts):
        for is_live, shard_clients in self.clients.items():
            for shard, client in shard_clients.items():
                client.start()
                try:
                    yield client.ready
                except Exception:
                    log.error("client for is_live=%s shard=%s failed to become ready", is_live, shard)
                    continue
                shard_accounts = [
                    a for a in accounts
                    if a.is_live == is_live and a.account_id % self.shards == shard
                ]
                for account in shard_accounts:
                    yield self._authorize_one(client, account)

    @defer.inlineCallbacks
    def _authorize_one(self, client, account):
        try:
            pair = self.token_store.get(account.connection_id)
            yield client.authorize_account(account.account_id, pair.access_token)
        except Exception as e:
            log.error("failed to authorize account %s: %s", account.account_id, e)
            self.repo.set_account_status(account.account_id, 'degraded', f"authorization failed: {e}")
            return

        # A REFUSAL IS NOT AN ERROR ON THE WIRE. The broker answers a
        # rejected ProtoOAAccountAuthReq with an error MESSAGE, which
        # resolves the send perfectly happily -- so this used to log
        # "authorized account 48434167" one line after the client logged
        # "account auth 48434167 rejected: RET_ACCOUNT_DISABLED", leave the
        # account 'ok', and show it as healthy on the dashboard. Every
        # later request on it then failed as a bare INVALID_REQUEST with
        # nothing anywhere to say the account had never been authorized.
        reason = client.account_auth_error(account.account_id)
        if reason is not None:
            log.error("account %s NOT authorized: broker refused with %s",
                      account.account_id, reason)
            self.repo.set_account_status(
                account.account_id, 'degraded', f"broker refused authorization: {reason}")
            return

        log.info("authorized account %s", account.account_id)
        # Guarded by `WHERE status='degraded'`, so this restores an account
        # the broker has stopped refusing without ever resuming one an
        # operator deliberately paused.
        self.repo.clear_degraded(account.account_id)

    @defer.inlineCallbacks
    def _fetch_and_cache_symbols(self, accounts, force: bool = False):
        """Fetch + cache each account's symbol map from the broker.

        force=True (startup(), a once-per-process-lifetime event): fetches
        every account unconditionally, exactly as before this parameter
        existed.

        force=False (reload(), the default -- see task-30 review finding
        I2): SKIPS any account whose symbol_cache already has at least one
        entry. reload() runs on the hot path of every PUT /api/settings and
        every pause()/resume() (settings_control.py / main.py), so an
        unconditional full re-fetch there would cost two heavyweight broker
        round trips (ProtoOASymbolsListReq + ProtoOASymbolByIdReq) PER
        ACCOUNT on every single operator action -- against a real broker
        with a large symbol universe this risks the api's httpx proxy
        timeout (a misleading 502 on a change that already applied) and
        does needless broker traffic under cTrader's rate limits. A
        brand-new account (the actual reason reload() needs this method at
        all -- see the comment at its call site) has an empty cache and
        still gets fetched the first time reload() ever sees it; every
        later reload() is then free for that account.

        On failure, logs an 'connection'/'error' EVENT (not just a log
        line) so an operator sees a broken symbol fetch via /api/events
        instead of only in container logs -- otherwise a fetch that starts
        failing after this method stops running unconditionally could go
        unnoticed indefinitely.
        """
        for account in accounts:
            if not force and self.repo.load_symbol_cache(account.account_id):
                continue
            client = self._client_for_account(account)
            if client is None:
                continue
            try:
                symbol_map = yield fetch_symbol_map(client, account.account_id)
                self.repo.save_symbol_cache(account.account_id, symbol_map)
                if account.role == 'master':
                    # Mutated in place: the same inner dict object is held by
                    # this org's AccountStateTracker and read through
                    # CopierService's master_symbols_by_org, so rebinding it
                    # would strand both on a stale map.
                    org_symbols = self.master_symbols_by_org.setdefault(account.org_id, {})
                    org_symbols.clear()
                    org_symbols.update(symbols_by_id(symbol_map))
            except Exception as e:
                log.error("failed to fetch symbol map for account %s: %s", account.account_id, e)
                self.repo.log_event(
                    'connection', 'error',
                    {'action': 'symbol_fetch_failed', 'error': str(e)},
                    account_id=account.account_id,
                )

    def _client_for_account(self, account) -> CTraderClient | None:
        shard = account.account_id % self.shards
        return self.clients.get(account.is_live, {}).get(shard)

    def _any_ready_client(self) -> CTraderClient | None:
        for shard_clients in self.clients.values():
            for client in shard_clients.values():
                if client.ready.called:
                    return client
        return None

    # ---------- control operations ----------

    def pause(self, org_id: int, account_id: int | None = None,
              actor: str | None = None) -> defer.Deferred:
        """Pause copying for one org, or one of its slaves, then reload."""
        self.repo.get_org(org_id)  # raises for a missing (or None) org
        if account_id is None:
            self.repo.set_org_setting(org_id, "copying_enabled", False)
            self.repo.log_event('control', 'info', {'action': 'pause_org'},
                                org_id=org_id, actor=actor)
            log.info("copying paused for org %s", org_id)
        else:
            self._require_account_in_org(org_id, account_id)
            self.repo.set_account_status(account_id, 'paused')
            self.repo.log_event(
                'control', 'info', {'action': 'pause_slave', 'account_id': account_id},
                account_id=account_id, org_id=org_id, actor=actor,
            )
            log.info("slave %s paused", account_id)
        return self.reload()

    def resume(self, org_id: int, account_id: int | None = None,
               actor: str | None = None) -> defer.Deferred:
        """Resume copying for one org, or one of its slaves, then reload."""
        self.repo.get_org(org_id)  # raises for a missing (or None) org
        if account_id is None:
            self.repo.set_org_setting(org_id, "copying_enabled", True)
            self.repo.log_event('control', 'info', {'action': 'resume_org'},
                                org_id=org_id, actor=actor)
            log.info("copying resumed for org %s", org_id)
        else:
            self._require_account_in_org(org_id, account_id)
            self.repo.set_account_status(account_id, 'ok')
            self.repo.log_event(
                'control', 'info', {'action': 'resume_slave', 'account_id': account_id},
                account_id=account_id, org_id=org_id, actor=actor,
            )
            log.info("slave %s resumed", account_id)
        return self.reload()

    def set_dry_run(self, org_id: int, enabled: bool,
                    actor: str | None = None) -> None:
        self.repo.get_org(org_id)  # raises for a missing (or None) org
        self.repo.set_org_setting(org_id, "dry_run", enabled)
        self.repo.log_event(
            'control', 'info', {'action': 'set_dry_run', 'enabled': enabled},
            org_id=org_id, actor=actor)
        log.info("dry-run for org %s: %s", org_id, "enabled" if enabled else "disabled")

    def request_resync(self, org_id: int | None = None) -> None:
        """Schedule a near-immediate resync in response to a position-
        changing event, so /state reflects a fill fast instead of waiting
        for the next RESYNC_INTERVAL_S tick. Debounced PER ORG: the burst a
        single trade produces (master fill + one fill per slave) collapses
        into one resync of THAT org's accounts only -- a fleet-wide
        ProtoOAReconcileReq sweep on every fill is what made fills take
        seconds to show up. org_id=None keeps the old whole-fleet sweep.
        """
        if org_id in self._resync_requested:
            return
        self._resync_requested.add(org_id)

        def _fire():
            self._resync_requested.discard(org_id)
            if org_id is None:
                self.periodic_resync()
                return
            if org_id in self._org_resync_in_flight:
                return
            self._org_resync_in_flight.add(org_id)
            d = defer.maybeDeferred(self.resync, org_id)

            def _clear(result):
                self._org_resync_in_flight.discard(org_id)
                return None

            def _swallow(failure):
                self._org_resync_in_flight.discard(org_id)
                log.error("org resync failed: %s", failure)
                return None

            d.addCallbacks(_clear, _swallow)

        clock = self.clock
        if clock is None:
            from twisted.internet import reactor as clock
        clock.callLater(RESYNC_DEBOUNCE_S, _fire)

        # A position just changed, so a round trip we have never priced may
        # have just completed. Debounced inside; deliberately not gated on
        # the resync succeeding, since the rate is read from the broker's
        # history rather than from anything the resync produces.
        self._maybe_learn_commission()

    def periodic_resync(self) -> defer.Deferred:
        """LoopingCall body: keep Positions and drift current between
        operator-triggered resyncs. A master fill updates mappings the
        moment it arrives, but the Positions page reads
        reconciler.master_positions, which only resync() refreshes -- so a
        position opened after boot stayed invisible until someone clicked
        resync (a live Stage-1 trade sat like that).

        Skips a tick rather than overlapping: resync() fans one
        ProtoOAReconcileReq out per enabled account, and a slow broker
        answer must not stack a second fan-out on top. Never raises or
        errbacks, same contract as refresh_balances().
        """
        if self._resync_in_flight or not self.reconcilers:
            return defer.succeed(None)
        self._resync_in_flight = True
        d = defer.maybeDeferred(self.resync)

        def _clear(result):
            self._resync_in_flight = False
            return None

        def _failed(f):
            self._resync_in_flight = False
            log.error("periodic resync: unexpected failure: %s", f)

        d.addCallbacks(_clear, _failed)
        return d

    @defer.inlineCallbacks
    def resync(self, org_id: int | None = None):
        """Run reconciliation for one org (or all) and feed each org's master
        positions into its own state tracker.

        org_id=None is the process-wide sweep (startup, the periodic loop);
        an operator-triggered resync passes exactly the org it belongs to, so
        one tenant's resync never fans ProtoOAReconcileReq out across
        another's accounts.
        """
        org_ids = [org_id] if org_id is not None else list(self.reconcilers.keys())
        sweeping = org_id is None
        all_items = []
        # ONE routing snapshot for the whole pass, resolved lazily, and the
        # slave symbol maps taken off it. build_routing ALREADY loaded each
        # slave's symbol cache into SlaveConfig.symbols; calling
        # load_symbol_cache(slave_id) again per slave would cost a SECOND
        # full symbol-cache SELECT each (500-2000 rows), synchronously on
        # the reactor, ~0.2s after a fill while the 10 msg/s send queue is
        # still draining the fan-out.
        routing = None
        slave_symbols: dict[int, dict] = {}
        for oid in org_ids:
            reconciler = self.reconcilers.get(oid)
            if reconciler is None:
                continue
            # In a SWEEP, one org's failure must not stop the others. Its
            # accounts can be disabled, its tokens stale, its broker down --
            # none of which is a reason to stop refreshing every other
            # tenant. Without this, one org with broker-disabled accounts
            # killed the periodic resync every minute for hours, and since
            # resync is what clears a closed position from the Positions
            # screen, operators were left pressing Close on trades that had
            # already closed.
            #
            # An explicitly requested resync still raises: the operator
            # asked for THAT org and must not be told it succeeded.
            try:
                items = yield reconciler.run()
            except Exception:
                if not sweeping:
                    raise
                log.exception("resync: org %s failed; continuing the sweep", oid)
                continue
            all_items.extend(items or [])
            tracker = self.state_trackers.get(oid)
            if tracker is not None:
                positions = [
                    StatePositionSnapshot(
                        position_id=p.position_id, symbol_id=p.symbol_id, side=p.side,
                        volume=p.volume, price=p.price, label=p.label,
                        stop_loss=p.stop_loss, take_profit=p.take_profit,
                    )
                    for p in reconciler.master_positions
                ]
                tracker.set_positions(reconciler.master_account_id, positions)
                # The slaves' books too: the dashboard's per-account position
                # counts and open P&L read from this tracker, and a slave
                # holding live copies must never report an empty book.
                for slave_id, slave_pos in reconciler.slave_positions.items():
                    tracker.set_positions(slave_id, [
                        StatePositionSnapshot(
                            position_id=p.position_id, symbol_id=p.symbol_id,
                            side=p.side, volume=p.volume, price=p.price,
                            label=p.label,
                            # A slave's OWN protection, not the master's: a
                            # copy whose stop never arrived is precisely the
                            # row an operator needs to spot.
                            stop_loss=p.stop_loss, take_profit=p.take_profit,
                        )
                        for p in slave_pos
                    ])
                # Persist too, so the Positions screen survives a restart
                # instead of reading an empty AccountStateTracker until the
                # next broker reconcile lands. Best-effort: a bookkeeping
                # write must never cost the operator the resync itself.
                try:
                    if routing is None:
                        routing = self.routing_provider()
                        slave_symbols = {
                            s.account_id: symbols_by_id(s.symbols)
                            for slaves in routing.slaves_by_org.values()
                            for s in slaves
                        }
                    master_org = routing.org_by_account.get(
                        reconciler.master_account_id)
                    self.repo.upsert_positions(
                        reconciler.master_account_id, master_org,
                        reconciler.master_positions,
                        self.master_symbols_by_org.get(master_org, {}))
                    # The broker's reconcile response is the truth about
                    # what is open; anything else we hold is stale. One
                    # UPDATE per account -- close_missing_positions is
                    # per-account on this base.
                    self.repo.close_missing_positions(
                        reconciler.master_account_id,
                        [p.position_id for p in reconciler.master_positions])
                    for slave_id, slave_pos in reconciler.slave_positions.items():
                        self.repo.upsert_positions(
                            slave_id, routing.org_by_account.get(slave_id),
                            slave_pos, slave_symbols.get(slave_id, {}))
                        self.repo.close_missing_positions(
                            slave_id, [p.position_id for p in slave_pos])
                except Exception:
                    log.exception(
                        "resync: position persistence failed (org %s)", oid)
                try:
                    yield tracker.ensure_spot_subscriptions()
                except Exception:
                    log.exception("resync: ensure_spot_subscriptions failed (org %s)", oid)
        # N9: an operator running a resync is about to look at Overview;
        # give them a current balance, not the one from process boot. Scoped
        # to the same org(s) this resync covered -- org A's operator pressing
        # Resync must not send broker traffic on org B's accounts, and must
        # not wait for it either.
        yield self.refresh_balances(org_id)
        return all_items

    @defer.inlineCallbacks
    def reload(self):
        """Re-read accounts/settings, (de)authorize accounts, refresh master routing.

        Tolerates zero accounts. Builds clients for newly-needed environments
        lazily (e.g. the first live account ever discovered).
        """
        try:
            yield self._reload_inner()
        finally:
            # Whatever this reload wrote (symbol caches especially) lands
            # after several yields; drop any snapshot an event rebuilt
            # while we were parked.
            invalidate = getattr(self.routing_provider, "invalidate", None)
            if invalidate is not None:
                invalidate()

    @defer.inlineCallbacks
    def _reload_inner(self):
        """The body of reload(); see reload() for the invalidation contract."""
        # The routing snapshot must not outlive whatever change prompted
        # this reload (account added/removed, settings edit). Invalidated
        # again in the finally below: reload() parks on broker round trips
        # and writes the symbol cache AFTER those yields, so an event
        # arriving mid-reload would otherwise refill the cache from a
        # half-written state and serve it for a further full TTL.
        invalidate = getattr(self.routing_provider, "invalidate", None)
        if invalidate is not None:
            invalidate()

        accounts = self.repo.load_accounts()
        envs_needed = {a.is_live for a in accounts}

        for is_live in envs_needed:
            env_clients = self.clients.setdefault(is_live, {})
            for shard in range(self.shards):
                if shard in env_clients:
                    continue
                client = self.client_factory(is_live)
                self.wire_client(client)
                env_clients[shard] = client
                client.start()
                try:
                    yield client.ready
                except Exception:
                    log.error("reload: client for is_live=%s shard=%s failed to become ready", is_live, shard)

        for account in accounts:
            client = self._client_for_account(account)
            if client is None:
                continue
            effectively_enabled = account.enabled and account.status != 'paused'
            if effectively_enabled:
                yield self._authorize_one(client, account)
            else:
                client.deauthorize_account(account.account_id)

        # Mirrors startup()'s authorize -> fetch-symbols ordering: accounts
        # added (via discover()/direct insert) and enabled AFTER the process
        # already booted only ever go through reload(), never startup()
        # again -- without this, their symbol_cache (needed by every slave's
        # mirror_volume sizing) and, for a newly (re)designated master, their
        # org's master symbol map (needed by normalize()'s unknown-symbol gate)
        # would stay empty for the lifetime of the process, silently
        # dropping every master event / mis-sizing every slave copy with no
        # error surfaced anywhere. Caught by the compose-level e2e test
        # (e2e/test_full_stack.py), which seeds accounts and calls /reload
        # against an already-running copier that started with zero accounts.
        #
        # Restricted to effectively-enabled accounts and left at
        # force=False (cache-miss-only, see _fetch_and_cache_symbols):
        # reload() runs on the hot path of every settings/pause/resume
        # call, so (a) an account just deauthorized above has no live
        # account-auth on this connection to fetch against -- skipping it
        # avoids a guaranteed failure every reload, not just a logged one --
        # and (b) an already-cached account costs zero broker round trips
        # on every subsequent reload instead of two, every time (task-30
        # review finding I2).
        accounts_needing_symbols = [
            a for a in accounts if a.enabled and a.status != 'paused'
        ]
        yield self._fetch_and_cache_symbols(accounts_needing_symbols)

        # Rebuild each org's engine wiring from the accounts table. The
        # in-memory per-org symbol dicts are refreshed unconditionally from
        # the DB cache -- decoupled from whether _fetch_and_cache_symbols
        # actually hit the broker this cycle. Without that, a former SLAVE
        # (already cached from being a slave) promoted to master would hit
        # the cache-miss skip above and never populate its org's master
        # symbol map at all, since only a successful FETCH (not a cache hit)
        # updates it in _fetch_and_cache_symbols. This is a plain local DB
        # read, not a broker round trip, so doing it every reload is free.
        routing = self.routing_provider()
        live_orgs = set(routing.master_by_org.keys())

        for org_id, master_id in routing.master_by_org.items():
            master_account = next(
                (a for a in accounts if a.account_id == master_id), None)
            if master_account is None:
                continue
            org_symbols = self.master_symbols_by_org.setdefault(org_id, {})
            org_symbols.clear()
            org_symbols.update(symbols_by_id(
                self.repo.load_symbol_cache(master_id)))

            reconciler = self.reconcilers.get(org_id)
            if reconciler is None:
                self.reconcilers[org_id] = Reconciler(
                    clients_by_account=self._clients_by_account, repo=self.repo,
                    dispatcher=self.dispatcher, master_account_id=master_id,
                    org_id=org_id,
                )
            elif reconciler.master_account_id != master_id:
                reconciler.master_account_id = master_id

            tracker = self.state_trackers.get(org_id)
            if tracker is None or tracker._master_account_id != master_id:
                master_client = self._client_for_account(master_account)
                self.state_trackers[org_id] = AccountStateTracker(
                    master_client=master_client, repo=self.repo,
                    master_account_id=master_id, symbols_by_id=org_symbols,
                )

        # Orgs that lost their master (or were deleted) lose their engines:
        # leaving a reconciler behind would keep reconciling -- and keep
        # answering /state for -- accounts the org no longer routes, and a
        # left-behind symbol map is still read by CopierService.
        for org_id in (set(self.reconcilers) | set(self.state_trackers)
                       | set(self.master_symbols_by_org)):
            if org_id not in live_orgs:
                self.reconcilers.pop(org_id, None)
                self.state_trackers.pop(org_id, None)
                self.master_symbols_by_org.pop(org_id, None)

        self.repo.log_event('control', 'info', {'action': 'reload', 'account_count': len(accounts)})

    @defer.inlineCallbacks
    def discover(self, connection_id: int):
        """Discover accounts reachable with a connection's access token and
        upsert them into the org that owns THAT connection.

        A broker account already claimed by another org is never rewritten
        (repo.upsert_account's ownership guard makes that atomic): it is
        skipped, counted as a conflict, and surfaced as an 'error' event on
        the discovering org, so an operator sees why the account they expected
        never appeared instead of silently stealing it from its owner.
        """
        client = self._any_ready_client()
        built_new = False
        if client is None:
            client = self.client_factory(False)
            built_new = True
            client.start()
            yield client.ready

        pair = self.token_store.get(connection_id)
        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = pair.access_token
        res = Protobuf.extract((yield client.send(req)))

        org_id = self.repo.connection_org(connection_id)
        discovered = list(res.ctidTraderAccount)
        conflicts = []
        for acc in discovered:
            applied = self.repo.upsert_account(
                account_id=acc.ctidTraderAccountId,
                connection_id=connection_id,
                org_id=org_id,
                trader_login=acc.traderLogin,
                is_live=acc.isLive,
            )
            if not applied:
                conflicts.append(acc.ctidTraderAccountId)
                self.repo.log_event(
                    'control', 'error',
                    {'action': 'discover_conflict',
                     'account_id': acc.ctidTraderAccountId,
                     'detail': 'account already connected to another organization'},
                    account_id=acc.ctidTraderAccountId, org_id=org_id,
                )

        if built_new:
            self.wire_client(client)
            self.clients.setdefault(False, {}).setdefault(0, client)

        self.repo.log_event(
            'control', 'info',
            {'action': 'discover', 'connection_id': connection_id,
             'account_count': len(discovered), 'conflicts': conflicts},
            org_id=org_id,
        )
        return discovered

    def refresh_due_tokens(self) -> defer.Deferred:
        """Refresh every token due for renewal. Never raises/errbacks (LoopingCall-safe)."""
        d = defer.maybeDeferred(self._refresh_due_tokens_body)
        d.addErrback(lambda f: log.error("refresh_due_tokens: unexpected failure: %s", f))
        return d

    def _refresh_due_tokens_body(self) -> defer.Deferred:
        try:
            due = self.token_store.due_for_refresh(datetime.utcnow())
        except Exception:
            log.exception("refresh_due_tokens: failed to query due connections")
            return defer.succeed(None)
        return defer.DeferredList(
            [self._refresh_one_token(cid) for cid in due], consumeErrors=True,
        )

    @defer.inlineCallbacks
    def _refresh_one_token(self, connection_id: int):
        try:
            client = self._any_ready_client()
            if client is None:
                raise RuntimeError("no ready client available for token refresh")
            pair = self.token_store.get(connection_id)
            req = ProtoOARefreshTokenReq()
            req.refreshToken = pair.refresh_token
            res = Protobuf.extract((yield client.send(req)))
            if isinstance(res, ProtoOAErrorRes):
                raise RuntimeError(f"refresh rejected: {res.errorCode}")
            new_expires = datetime.utcnow() + timedelta(seconds=res.expiresIn)
            # Persist the rotated pair before anything else touches it (spec §5).
            self.token_store.rotate(connection_id, res.accessToken, res.refreshToken, new_expires)
            self.repo.log_event(
                'auth', 'info', {'action': 'token_refreshed', 'connection_id': connection_id},
            )
        except Exception as e:
            log.error("token refresh failed for connection %s: %s", connection_id, e)
            try:
                self.token_store.mark(connection_id, 'refresh_failed')
            except Exception:
                log.exception("failed to mark connection %s as refresh_failed", connection_id)
            try:
                self.repo.log_event(
                    'auth', 'error',
                    {'action': 'token_refresh_failed', 'connection_id': connection_id, 'error': str(e)},
                    account_id=None,
                )
            except Exception:
                log.exception("failed to log token_refresh_failed event")

    # ---------- deal backfill ----------

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    @defer.inlineCallbacks
    def _fetch_deal_window(self, client, account_id, symbols, from_ms, to_ms):
        """Every deal in [from_ms, to_ms], bisecting around the broker's
        500-row cap.

        queries.deal_history caps a response at DEAL_LIST_MAX_ROWS and
        reports the overflow as has_more. The caller advances the watermark
        past the window afterwards and the walk NEVER revisits it, so a
        window that reported has_more used to lose every deal past the 500th
        permanently -- compute_analytics under-reporting closed_trades,
        net_pnl, max_drawdown and the equity curve with `exhausted` eventually
        True and `truncated` False, i.e. no signal at all.

        Splitting and re-fetching is safe: deals are upserted on
        (account_id, deal_id), so the overlap at each midpoint costs nothing.
        In the ordinary case this is exactly one request.
        """
        pending = [(from_ms, to_ms)]
        deals: list[dict] = []
        fetches = 0
        while pending:
            lo, hi = pending.pop()
            result = yield queries.deal_history(
                client, account_id, symbols, lo, hi)
            fetches += 1
            deals.extend(result["deals"])
            if not result.get("has_more"):
                continue
            if hi - lo <= DEAL_WINDOW_MIN_MS or fetches >= DEAL_WINDOW_MAX_FETCHES:
                # >500 deals for one account inside an hour, or a week that
                # needed more than 64 requests. Nothing further this API can
                # do; say so loudly rather than lose rows in silence.
                log.error(
                    "deal backfill: account %s window [%s, %s] still reports "
                    "has_more after %d requests; deals in it are unreachable "
                    "through DealList", account_id, lo, hi, fetches)
                continue
            mid = (lo + hi) // 2
            pending.append((lo, mid))
            pending.append((mid, hi))
        return deals

    @defer.inlineCallbacks
    def backfill_deals_once(self):
        """Fetch ONE window for ONE account per tick, ROUND-ROBIN.

        Deliberately one account/window per tick rather than a greedy loop
        over every account: the broker caps DealList at a week and 500
        rows, and the queued send path runs at 10 msg/s, so a fan-out here
        would hammer it.

        The starting offset rotates on every tick and advances whether or
        not work was found. Scanning from index 0 and returning after the
        first account with a window starved every other account
        PERMANENTLY: next_window() hands an already-exhausted account a
        forward catch-up window on essentially every tick (its
        backfilled_to_ms was stamped with the PREVIOUS tick's now_ms, so it
        is always strictly less than the current one), so account #1
        consumed every tick forever while accounts #2..N never had a single
        deal fetched -- get_backfill_state None, get_analytics zeros,
        truncated=True, a blank Performance page for the life of the
        deployment and across restarts, because the watermark is durable.

        Accounts are sorted by id so the rotation is stable from tick to
        tick; repo.load_accounts() has no ORDER BY.

        Never raises or errbacks: this is a LoopingCall body, and a
        LoopingCall whose Deferred fails stops looping permanently.
        """
        try:
            accounts = sorted(self.repo.load_accounts(),
                              key=lambda a: a.account_id)
            if not accounts:
                return
            start = self._backfill_cursor % len(accounts)
            self._backfill_cursor = start + 1
            now_ms = self._now_ms()
            for offset in range(len(accounts)):
                acc = accounts[(start + offset) % len(accounts)]
                state = self.repo.get_backfill_state(acc.account_id)
                window = next_window(state, now_ms, DEAL_BACKFILL_MAX_YEARS)
                if window is None:
                    continue
                from_ms, to_ms = window
                # The base's own sharding-aware resolver: an org can mix
                # demo and live accounts, and each shard has its own client.
                client = self._client_for_account(acc)
                if client is None:
                    continue
                symbols = symbols_by_id(
                    self.repo.load_symbol_cache(acc.account_id))
                deals = yield self._fetch_deal_window(
                    client, acc.account_id, symbols, from_ms, to_ms)
                self.repo.upsert_deals(acc.account_id, acc.org_id, deals)
                # `exhausted` means "we walked back as far as we are going
                # to", never "this window happened to be empty". A slave
                # that has not been copied to in a week -- the common case
                # -- used to be marked complete on tick 1, discarding its
                # entire history while reporting truncated=False. Once set
                # it stays set: the forward catch-up branch re-walks recent
                # time and must not un-exhaust the account.
                exhausted = bool(state and state.get("exhausted")) or \
                    reached_history_bound(
                        from_ms, now_ms, DEAL_BACKFILL_MAX_YEARS)
                self.repo.set_backfill_state(
                    acc.account_id, from_ms, to_ms, exhausted=exhausted)
                return  # one window per tick
        except Exception:
            log.exception("deal backfill tick failed")

    # ---------- on-demand broker queries ----------

    def _query_context(self, account_id: int):
        """Resolve (client, symbols_by_id) for one account or raise ValueError."""
        account = next(
            (a for a in self.repo.load_accounts() if a.account_id == account_id), None)
        if account is None:
            raise ValueError(f"account {account_id} not found")
        client = self._client_for_account(account)
        if client is None:
            raise ValueError(f"no client for account {account_id}")

        # Say what is actually wrong. Querying an account the broker has
        # refused returns INVALID_REQUEST for every request type, which
        # reached the operator as "400: trader details failed:
        # INVALID_REQUEST" -- true, useless, and indistinguishable from a
        # malformed request on our side. Only an explicit refusal short
        # circuits here: auth still in flight is not a refusal, and that
        # request should take its chances as it always has.
        reason = client.account_auth_error(account_id)
        if reason is not None:
            raise ValueError(
                f"account {account_id} is not authorized: the broker refused "
                f"with {reason}")

        return client, symbols_by_id(self.repo.load_symbol_cache(account_id))

    def get_account_details(self, account_id: int) -> defer.Deferred:
        """Full broker-side profile for one account (see engine/queries.py)."""
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.account_details(ctx[0], account_id, ctx[1]))
        return d

    def get_deal_history(self, account_id: int, from_ms: int, to_ms: int) -> defer.Deferred:
        """Deal (fill) history for one account in [from_ms, to_ms]."""
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.deal_history(
            ctx[0], account_id, ctx[1], from_ms, to_ms))
        return d

    def get_order_history(self, account_id: int, from_ms: int, to_ms: int) -> defer.Deferred:
        """Order history for one account in [from_ms, to_ms]."""
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.order_history(
            ctx[0], account_id, ctx[1], from_ms, to_ms))
        return d

    # ---------- operator trade actions ----------

    def place_order(self, params: dict) -> dict:
        """Place a manual order on ANY connected account from the dashboard.

        Validates against the account's symbol cache (symbol name, minimum
        and step volume), converts lots to protocol units, and sends through
        Dispatcher.send_direct -- the gate-free operator path with the same
        throttle/retry/degraded semantics as copy sends.  The order is
        labeled MANUAL_ORDER_LABEL: on the master it replicates through the
        normal copy pipeline like any master trade; on a slave the label
        keeps reconcile from flagging the resulting position as orphan
        drift.

        Like every trade request, the broker sends no synchronous reply --
        this returns "submitted", and the outcome arrives as an execution
        event (visible in Positions/Logs within ~1-2s).
        """
        account_id = params.get("account_id")
        if account_id is None:
            raise ValueError("account_id required")
        account_id = int(account_id)

        side_name = str(params.get("side", "")).upper()
        if side_name not in _SIDE_BY_NAME:
            raise ValueError("side must be BUY or SELL")

        type_name = str(params.get("order_type", "")).upper()
        if type_name not in _ORDER_TYPE_BY_NAME:
            raise ValueError("order_type must be MARKET, LIMIT or STOP")

        try:
            volume_lots = float(params.get("volume_lots"))
        except (TypeError, ValueError):
            raise ValueError("volume_lots must be a number")
        # NaN/inf pass every comparison below and reach the broker as garbage.
        if not math.isfinite(volume_lots):
            raise ValueError("volume_lots must be a finite number")
        if volume_lots <= 0:
            raise ValueError("volume_lots must be greater than 0")
        if volume_lots > MAX_MANUAL_ORDER_LOTS:
            raise ValueError(
                f"volume {volume_lots} lots exceeds the manual-order limit of "
                f"{MAX_MANUAL_ORDER_LOTS} lots (fat-finger guard; raise "
                f"MAX_MANUAL_ORDER_LOTS to change it)")

        limit_price = params.get("limit_price")
        stop_price = params.get("stop_price")
        if type_name == "LIMIT" and limit_price is None:
            raise ValueError("limit_price required for LIMIT orders")
        if type_name == "STOP" and stop_price is None:
            raise ValueError("stop_price required for STOP orders")
        for field in ("limit_price", "stop_price", "stop_loss", "take_profit"):
            raw = params.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be a number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be a positive, finite price")

        # Resolves the client too, so an unknown account fails here.
        self._query_context(account_id)

        # Dry-run is the rollout safety gate: while it is on, copied trades
        # are simulated, so letting a MANUAL order through would put real
        # money on the wire the operator believes is in safe mode -- and
        # desynchronize master from slaves, whose copies stay simulated.
        # Closing risk is never blocked; only opening it.
        org_id = self._org_for_account(account_id)
        if org_id is not None and self.repo.get_org(org_id).dry_run:
            raise ValueError(
                "this workspace is in dry-run: manual orders are disabled "
                "until dry-run is turned off")

        symbol_name = params.get("symbol")
        sym = self.repo.load_symbol_cache(account_id).get(symbol_name)
        if sym is None:
            raise ValueError(f"unknown symbol {symbol_name!r} for account {account_id}")

        volume = int(volume_lots * sym.lot_size)
        if sym.step_volume:
            volume -= volume % sym.step_volume
        if volume < sym.min_volume:
            raise ValueError(
                f"volume {volume_lots} lots is below the minimum for {symbol_name}")

        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = account_id
        req.symbolId = sym.symbol_id
        req.orderType = _ORDER_TYPE_BY_NAME[type_name]
        req.tradeSide = _SIDE_BY_NAME[side_name]
        req.volume = volume
        req.label = MANUAL_ORDER_LABEL
        if limit_price is not None:
            req.limitPrice = float(limit_price)
        if stop_price is not None:
            req.stopPrice = float(stop_price)

        protection_audit: dict | None = None
        raw_sl = params.get("stop_loss")
        raw_tp = params.get("take_profit")
        if raw_sl is not None or raw_tp is not None:
            sl = float(raw_sl) if raw_sl is not None else None
            tp = float(raw_tp) if raw_tp is not None else None
            if type_name == "MARKET":
                # Absolute SL/TP are rejected on a market order; the broker
                # wants the distance from the fill. Measure it from the side
                # the order will actually cross.
                bid, ask = self._spot_for(account_id, symbol_name)
                reference = ask if side_name == "BUY" else bid
                if reference is None:
                    raise ValueError(
                        f"no live price for {symbol_name} yet, so a stop loss or "
                        f"take profit cannot be placed with a market order -- "
                        f"try again in a moment, or place the order without "
                        f"protection and set it on the position")
                sl_rel, tp_rel = relative_protection(
                    _SIDE_BY_NAME_DOMAIN[side_name], reference, sl, tp)
                if sl is not None and sl_rel is None:
                    raise ValueError(
                        f"a {side_name} stop loss must sit on the losing side of "
                        f"the market ({reference})")
                if tp is not None and tp_rel is None:
                    raise ValueError(
                        f"a {side_name} take profit must sit on the winning side "
                        f"of the market ({reference})")
                if sl_rel is not None:
                    req.relativeStopLoss = sl_rel
                if tp_rel is not None:
                    req.relativeTakeProfit = tp_rel
                # The broker applies these distances to the FILL, which is
                # not exactly the price quoted here. Record both so a stop
                # that lands somewhere unexpected can be explained.
                protection_audit = {
                    'requested_stop_loss': sl, 'requested_take_profit': tp,
                    'reference_price': reference,
                    'relative_stop_loss': sl_rel,
                    'relative_take_profit': tp_rel,
                    'note': 'market-order protection is placed relative to '
                            'the fill, so the final level moves with slippage',
                }
            else:
                # LIMIT and STOP orders do take absolute prices.
                if sl is not None:
                    req.stopLoss = sl
                if tp is not None:
                    req.takeProfit = tp
                protection_audit = {'stop_loss': sl, 'take_profit': tp}

        self.dispatcher.send_direct(account_id, req)
        summary = {
            "status": "submitted", "account_id": account_id,
            "symbol": symbol_name, "side": side_name, "order_type": type_name,
            "volume": volume, "volume_lots": f"{volume / sym.lot_size:.2f}",
        }
        self.repo.log_event(
            'control', 'info',
            {'action': 'manual_order',
             **{k: v for k, v in summary.items() if k != 'status'},
             **({'protection': protection_audit} if protection_audit else {})},
            account_id=account_id, org_id=org_id, actor=params.get("actor_email"),
        )
        return summary

    @defer.inlineCallbacks
    def close_position(self, account_id: int, position_id: int, volume_lots=None,
                       actor: str | None = None):
        """Close (or partially close) one position on any account.

        Reads the position's CURRENT volume fresh from the broker
        (ProtoOAReconcileReq) rather than trusting the caller: a full close
        sends exactly the live volume, and a partial close is clamped to it.
        """
        client, symbols = self._query_context(account_id)
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = account_id
        rec = queries.extract_or_raise((yield client.send(req)), "reconcile")

        pos = next((p for p in rec.position if p.positionId == position_id), None)
        if pos is None:
            raise ValueError(
                f"position {position_id} not found on account {account_id}")

        volume = pos.tradeData.volume
        if volume_lots is not None:
            sym = symbols.get(pos.tradeData.symbolId)
            if sym is None:
                raise ValueError(
                    f"no symbol info for symbol id {pos.tradeData.symbolId}")
            volume = min(int(float(volume_lots) * sym.lot_size), volume)
            if volume <= 0:
                raise ValueError("volume_lots must be greater than 0")

        close_req = ProtoOAClosePositionReq()
        close_req.ctidTraderAccountId = account_id
        close_req.positionId = position_id
        close_req.volume = volume
        self.dispatcher.send_direct(account_id, close_req)
        self.repo.log_event(
            'control', 'info',
            {'action': 'manual_close', 'position_id': position_id, 'volume': volume},
            account_id=account_id, org_id=self._org_for_account(account_id),
            actor=actor,
        )
        return {"status": "submitted", "account_id": account_id,
                "position_id": position_id, "volume": volume}

    def _digits_for_position(self, account_id: int, position_id: int, symbols) -> int | None:
        """Decimal places the symbol under an open position is quoted to.

        Returns None when the position or its symbol cannot be resolved, in
        which case the caller must send the price unchanged rather than
        guess at a precision -- rounding to the wrong number of places is
        its own way of moving somebody's stop.
        """
        try:
            org_id = self._org_for_account(account_id)
            reconciler = self.reconcilers.get(org_id) if org_id is not None else None
            if reconciler is None:
                return None
            positions = list(reconciler.master_positions or [])
            for book in (reconciler.slave_positions or {}).values():
                positions.extend(book or [])
            for pos in positions:
                if pos.position_id == position_id:
                    # _query_context hands back symbols keyed by symbol_id.
                    info = (symbols or {}).get(pos.symbol_id)
                    return info.digits if info is not None else None
        except Exception:
            log.exception("could not resolve digits for position %s", position_id)
        return None

    def amend_position_sltp(self, account_id: int, position_id: int,
                            stop_loss=None, take_profit=None,
                            actor: str | None = None) -> dict:
        """Set (or clear) the stop loss and take profit on an open position.

        Both protections are sent together, which is what the broker's
        amend means: a value omitted here is REMOVED from the position, not
        left alone. The caller therefore always states the full intent --
        the dashboard pre-fills the current values so an edit to one does
        not silently drop the other.

        On the MASTER this needs no special handling to reach the fleet:
        the broker's execution event arrives as MasterPositionSLTPAmended
        and the decision engine already fans an AmendPositionSLTP out to
        every mapped slave copy.
        """
        account_id = int(account_id)
        position_id = int(position_id)

        def _price(name, raw):
            if raw is None or raw == "":
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive, finite price")
            return value

        sl = _price("stop_loss", stop_loss)
        tp = _price("take_profit", take_profit)

        # Resolves the client too, so an unknown account fails here.
        _client, symbols = self._query_context(account_id)

        # ROUND TO WHAT THE SYMBOL IS QUOTED TO, here rather than trusting
        # the caller. A price carrying more decimals than the broker quotes
        # is refused outright -- INVALID_REQUEST, with the protection
        # silently not set -- and it is trivially easy to produce one:
        # 4573.27 - (100 / 90) is 4572.158888888889 in binary floating
        # point, which is what a dashboard computing a money-denominated
        # stop actually arrives at.
        #
        # The browser rounds too, but it must not be the only thing that
        # does: a stale cached bundle, another client, or a direct API call
        # would each put an unusable price on the wire, and the failure
        # gives the operator no clue why their stop never took.
        digits = self._digits_for_position(account_id, position_id, symbols)
        if digits is not None:
            if sl is not None:
                sl = round(sl, digits)
            if tp is not None:
                tp = round(tp, digits)

        req = ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = account_id
        req.positionId = position_id
        if sl is not None:
            req.stopLoss = sl
        if tp is not None:
            req.takeProfit = tp
        self.dispatcher.send_direct(account_id, req)

        self.repo.log_event(
            'control', 'info',
            {'action': 'amend_sltp', 'position_id': position_id,
             'stop_loss': sl, 'take_profit': tp},
            account_id=account_id, org_id=self._org_for_account(account_id),
            actor=actor,
        )
        return {"status": "submitted", "account_id": account_id,
                "position_id": position_id, "stop_loss": sl, "take_profit": tp}

    def cancel_order(self, account_id: int, order_id: int,
                     actor: str | None = None) -> defer.Deferred:
        """Cancel one working order on any account."""
        def _send(_ctx):
            req = ProtoOACancelOrderReq()
            req.ctidTraderAccountId = account_id
            req.orderId = order_id
            self.dispatcher.send_direct(account_id, req)
            self.repo.log_event(
                'control', 'info',
                {'action': 'manual_cancel', 'order_id': order_id},
                account_id=account_id, org_id=self._org_for_account(account_id),
                actor=actor,
            )
            return {"status": "submitted", "account_id": account_id,
                    "order_id": order_id}

        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(_send)
        return d

    @defer.inlineCallbacks
    def close_all(self, org_id: int, account_id: int | None = None,
                  actor: str | None = None):
        """Kill switch for ONE org: flatten one of its accounts, or
        (account_id=None) every enabled, non-paused account in the org --
        master included.

        The org-wide flatten pauses THAT ORG's copying FIRST, so the
        master closes it is about to send cannot fan out as copy-closes while
        the slaves' positions are simultaneously being closed directly (each
        close would otherwise race its own copy) -- then RESTORES the prior
        copying state after a short grace (CLOSE_ALL_RESUME_GRACE_S) that
        lets the master's close executions drain. The button closes
        contracts; it does not stop the copier. An org whose copying was
        already off stays off, and the restore is a compare-and-set on
        orgs.settings_version so an operator write landing mid-flatten
        (STOP COPYING from another tab, a second close-all) is never
        clobbered -- see _restore_copying_after_flatten. No other org's settings or accounts are ever
        touched, and a single-account flatten pauses nothing at all -- but
        still only accepts an account of its own org.

        The org is resolved FIRST, before anything is written or sent, so a
        caller that mis-binds its arguments (a positional account_id landing
        in org_id, or no org at all) fails loudly. Without this guard an
        unknown/None org would write no setting, match no target, and still
        return {"status": "flattened", "paused": true, "accounts": []} -- a
        kill switch reporting success having closed NOTHING.
        """
        org = self.repo.get_org(org_id)  # raises for a missing (or None) org

        if account_id is not None:
            self._require_account_in_org(org_id, int(account_id))
            # Safely, like the org-wide path: an account that cannot be
            # reached must come back as a summary saying so, not as a bare
            # exception. The dashboard reads accounts[0] and now shows what
            # was left open -- a 500 tells the operator only that something
            # went wrong, not whether they are still carrying the position.
            summary = yield self._flatten_account_safely(int(account_id))
            results = [summary]
            paused = False
        else:
            was_copying = org.copying_enabled
            self.repo.set_org_setting(org_id, "copying_enabled", False)
            guard_version = self.repo.get_org_settings_version(org_id)
            results = []
            # EVERYTHING between the pause write and the finally lives
            # inside the try: a DB hiccup in load_accounts must still reach
            # the restore, or copying is left silently off.
            try:
                targets = [
                    a for a in self.repo.load_accounts()
                    if a.org_id == org_id and a.enabled and a.status != 'paused'
                ]
                # CONCURRENTLY, not one account after another. Each flatten
                # opens with a reconcile round trip (~130ms), and serialising
                # them meant the last account's closes did not reach the wire
                # until well over a second after the operator pressed the
                # button -- measured at 1.4s across 11 accounts, with the
                # positions live throughout. The dispatcher's token bucket
                # still paces the wire, so this does not send anything
                # faster; it just stops each account waiting its turn to be
                # asked.
                results = yield defer.gatherResults(
                    [self._flatten_account_safely(a.account_id) for a in targets])
            finally:
                # Restore the org's prior copying state -- after a grace
                # that lets in-flight master close executions arrive while
                # still paused, so they cannot fan out against copies the
                # loop above just closed. Runs even if the loop blew up:
                # a failed flatten must not leave copying silently off.
                if was_copying:
                    clock = self.clock
                    if clock is None:
                        from twisted.internet import reactor as clock
                    yield task.deferLater(
                        clock, CLOSE_ALL_RESUME_GRACE_S, lambda: None)
                    self._restore_copying_after_flatten(org_id, guard_version)
            # Report the setting as it actually stands now, not as this
            # call assumes it left it: a skipped or failed restore must
            # never be narrated to the UI as "copying survived".
            try:
                paused = not self.repo.get_org(org_id).copying_enabled
            except Exception:
                paused = True  # unknown: claim the safe state

        self.repo.log_event(
            'control', 'warning',
            {'action': 'kill_switch', 'org_wide': account_id is None,
             'copying_paused': paused, 'accounts': results},
            account_id=account_id, org_id=org_id, actor=actor,
        )
        return {"status": "flattened", "paused": paused, "accounts": results}

    def _restore_copying_after_flatten(self, org_id: int, guard_version: int) -> None:
        """Put copying back on after an org-wide flatten -- unless someone
        else wrote the org's settings while it ran.

        The restore is an atomic compare-and-set on orgs.settings_version
        (trigger-bumped on every orgs write from EITHER process; the api
        writes settings straight to Postgres). A moved version means an
        operator acted mid-flatten: honor their write, log why. A DB error
        is retried once; if the restore still fails, copying stays OFF (the
        safe direction for money) and a control/error event says so instead
        of the UI silently claiming copying survived.
        """
        for attempt in (1, 2):
            try:
                if self.repo.restore_copying_if_unchanged(org_id, guard_version):
                    return
                self.repo.log_event(
                    'control', 'warning',
                    {'action': 'close_all_restore_skipped',
                     'detail': 'settings were changed during the flatten; '
                               'leaving them as the newer write put them'},
                    org_id=org_id)
                return
            except Exception as e:
                log.error("close_all: restore attempt %s failed for org %s: %s",
                          attempt, org_id, e)
        try:
            self.repo.log_event(
                'control', 'error',
                {'action': 'close_all_restore_failed',
                 'detail': 'copying is still OFF after the flatten; '
                           'resume it manually'},
                org_id=org_id)
        except Exception:
            log.error("close_all: could not even log the failed restore for org %s", org_id)

    def _flatten_bucket_for_sends(self):
        """The flatten's dedicated throttle, created on first use."""
        if self._flatten_bucket is None:
            clock = self.clock
            if clock is None:
                from twisted.internet import reactor as clock
            self._flatten_bucket = TokenBucket(
                rate=FLATTEN_RATE, capacity=FLATTEN_BURST, clock=clock)
        return self._flatten_bucket

    def _flatten_account_safely(self, account_id: int) -> defer.Deferred:
        """_flatten_account that reports a failure instead of raising it.

        One account's broker refusing to talk must not abandon the other ten
        -- and with the accounts running concurrently, a raised failure would
        take the whole gatherResults down with it.
        """
        d = defer.maybeDeferred(self._flatten_account, account_id)

        def _as_result(failure):
            log.error("close_all: flatten %s failed: %s", account_id, failure.value)
            return {"account_id": account_id, "positions_closed": 0,
                    "orders_cancelled": 0, "positions_remaining": None,
                    "orders_remaining": None, "rounds": 0,
                    "error": str(failure.value)}

        d.addErrback(_as_result)
        return d

    @defer.inlineCallbacks
    def _reconcile_book(self, client, account_id: int):
        """The account's open positions and working orders, from the broker.

        A FRESH snapshot, never this process's mappings: orphans and
        positions opened by hand in the platform must die too.
        """
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = account_id
        rec = queries.extract_or_raise((yield client.send(req)), "reconcile")

        # This snapshot is the ONLY evidence the kill switch has, and a
        # verdict built on another account's book would be worse than no
        # verdict: an empty answer meant for a flat account would read as
        # "verified flat" over a book that is still open. The SDK matches
        # responses to requests by the id() of a Deferred, which is a reused
        # memory address whose entry is dropped on timeout, so a mismatch is
        # not impossible -- and the response says whose book it is, so there
        # is no reason to take it on trust.
        answered_for = getattr(rec, "ctidTraderAccountId", None)
        if answered_for is not None and answered_for != account_id:
            raise queries.QueryFailed(
                f"reconcile for account {account_id} was answered with account "
                f"{answered_for}'s book")
        return list(rec.position), list(rec.order)

    @defer.inlineCallbacks
    def _flatten_account(self, account_id: int):
        """Close everything on one account, and VERIFY that it closed.

        Sends closes, waits, asks the broker what survived, and sends again
        for whatever did -- up to FLATTEN_ROUNDS times. The numbers returned
        are then MEASURED (what was there minus what is left) rather than
        assumed from the requests that went out.

        This is the whole point of the change. The previous version counted
        sends: it reported 66 positions closed across four accounts while the
        broker had refused all 66 and every one was still open. Because a
        refusal cannot be tied back to the close that caused it -- a close
        request carries no correlator and the error names only an account --
        counting sends can never be made honest. Asking again can.

        Retries are not an optimisation here, they are the recovery: pressing
        the button repeatedly is exactly what the operator had to do by hand,
        and it worked, so the loop does it for them. What it still cannot
        close it NAMES, so the failure is in front of the operator rather
        than in a log.
        """
        client, _symbols = self._query_context(account_id)
        clock = self.clock
        if clock is None:
            from twisted.internet import reactor as clock

        first_positions: set[int] | None = None
        first_orders: set[int] | None = None
        open_positions: list[int] = []
        open_orders: list[int] = []
        rounds_used = 0

        for attempt in range(FLATTEN_ROUNDS):
            positions, orders = yield self._reconcile_book(client, account_id)
            if first_positions is None:
                first_positions = {p.positionId for p in positions}
                first_orders = {o.orderId for o in orders}

            open_positions = [p.positionId for p in positions]
            open_orders = [o.orderId for o in orders]
            if not positions and not orders:
                break

            rounds_used = attempt + 1
            bucket = self._flatten_bucket_for_sends()
            for p in positions:
                close_req = ProtoOAClosePositionReq()
                close_req.ctidTraderAccountId = account_id
                close_req.positionId = p.positionId
                close_req.volume = p.tradeData.volume
                self.dispatcher.send_direct(account_id, close_req, bucket=bucket)
            for o in orders:
                cancel_req = ProtoOACancelOrderReq()
                cancel_req.ctidTraderAccountId = account_id
                cancel_req.orderId = o.orderId
                self.dispatcher.send_direct(account_id, cancel_req, bucket=bucket)

            yield task.deferLater(
                clock, FLATTEN_SETTLE_S * (2 ** attempt), lambda: None)
        else:
            # The loop ran out having just sent a round, so the last thing
            # known is what was open BEFORE it. Ask once more: the number
            # reported has to be measured even when the news is bad.
            positions, orders = yield self._reconcile_book(client, account_id)
            open_positions = [p.positionId for p in positions]
            open_orders = [o.orderId for o in orders]

        # Counted against what was there to begin with, and only for what
        # this call set out to close. A position opened DURING the flatten
        # (a straggler master fill landing before copying paused) is still
        # reported as open -- it is genuinely open -- but it never inflates
        # the closed count.
        first_positions = first_positions or set()
        first_orders = first_orders or set()
        still_open = first_positions & set(open_positions)
        still_working = first_orders & set(open_orders)
        positions_closed = len(first_positions) - len(still_open)
        orders_cancelled = len(first_orders) - len(still_working)

        summary = {
            "account_id": account_id,
            "positions_closed": positions_closed,
            "orders_cancelled": orders_cancelled,
            # Everything still open, whether this call had seen it before or
            # not. The operator needs the book as it stands, not a diff.
            "positions_remaining": open_positions,
            "orders_remaining": open_orders,
            "rounds": rounds_used,
            "error": None,
        }
        if open_positions or open_orders:
            summary["error"] = (
                f"{len(open_positions)} position(s) and {len(open_orders)} "
                f"order(s) still open after {rounds_used} attempt(s)")

        self.repo.log_event(
            'control', 'error' if summary["error"] else 'warning',
            {'action': 'kill_switch_flatten',
             'positions_closed': positions_closed,
             'orders_cancelled': orders_cancelled,
             'positions_remaining': open_positions,
             'orders_remaining': open_orders,
             'rounds': rounds_used},
            account_id=account_id,
        )
        return summary

    def _spot_for(self, account_id: int, symbol_name: str):
        """(bid, ask) for a symbol, resolved in the MASTER's namespace.

        The org's tracker subscribes on the master connection, so its quote
        store is keyed by MASTER symbol ids -- looking it up with the target
        account's id would read whatever instrument happens to share that
        number on the slave's broker. Resolve by NAME, which is how the rest
        of the copy engine matches symbols across accounts.

        Also subscribes, so a symbol with no open position starts ticking
        and a caller told to "try again in a moment" actually can.
        """
        org_id = self._org_for_account(account_id)
        tracker = self.state_trackers.get(org_id) if org_id is not None else None
        if tracker is None:
            return None, None
        master_sym = next(
            (si for si in self.master_symbols_by_org.get(org_id, {}).values()
             if si.name == symbol_name), None)
        if master_sym is None:
            return None, None
        try:
            tracker.ensure_spot_subscription(master_sym.symbol_id)
        except Exception:  # subscription is best-effort; the read decides
            pass
        # quote() answers None (not a pair) before the symbol's first tick.
        spot = tracker.quote(master_sym.symbol_id)
        return spot if spot else (None, None)

    def get_quote(self, account_id: int, symbol_name: str) -> dict:
        """Live (bid, ask) for one of the account's symbols, from the org's
        state tracker. Asking also subscribes the symbol on the master
        connection (fire-and-forget), so a symbol with no open position
        starts ticking for the next poll. Null bid/ask before the first
        tick -- the caller polls, it does not error."""
        account = next(
            (a for a in self.repo.load_accounts() if a.account_id == account_id),
            None)
        if account is None:
            raise ValueError(f"unknown account {account_id}")
        sym = self.repo.load_symbol_cache(account_id).get(symbol_name)
        if sym is None:
            raise ValueError(
                f"unknown symbol {symbol_name!r} for account {account_id}")

        tracker = self.state_trackers.get(account.org_id)
        if tracker is None:
            return {"symbol": symbol_name, "bid": None, "ask": None}

        d = tracker.ensure_spot_subscription(sym.symbol_id)
        d.addErrback(lambda f: log.warning(
            "quote subscription for %s failed: %s", symbol_name, f))
        spot = tracker.quote(sym.symbol_id)
        return {
            "symbol": symbol_name,
            "bid": spot[0] if spot else None,
            "ask": spot[1] if spot else None,
        }

    def get_expected_margin(self, account_id: int, symbol_name: str,
                            volume_lots) -> defer.Deferred:
        """Pre-trade margin estimate for volume_lots of symbol_name."""
        def go(ctx):
            client, _symbols = ctx
            sym = self.repo.load_symbol_cache(account_id).get(symbol_name)
            if sym is None:
                raise ValueError(
                    f"unknown symbol {symbol_name!r} for account {account_id}")
            volume = int(float(volume_lots) * sym.lot_size)
            if volume <= 0:
                raise ValueError("volume_lots must be greater than 0")
            return queries.expected_margin(client, account_id, sym, volume)

        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(go)
        return d

    def get_trendbars(self, account_id: int, symbol_name: str, period: str,
                      from_ms: int, to_ms: int) -> defer.Deferred:
        """Historical candles for one of the account's symbols."""
        def go(ctx):
            client, _symbols = ctx
            sym = self.repo.load_symbol_cache(account_id).get(symbol_name)
            if sym is None:
                raise ValueError(
                    f"unknown symbol {symbol_name!r} for account {account_id}")
            return queries.trendbars(client, account_id, sym, period, from_ms, to_ms)

        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(go)
        return d

    def get_cash_flow(self, account_id: int, from_ms: int, to_ms: int) -> defer.Deferred:
        """Deposit/withdrawal history for one account."""
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.cash_flow_history(
            ctx[0], account_id, from_ms, to_ms))
        return d

    def get_position_deals(self, account_id: int, position_id: int,
                           from_ms: int, to_ms: int) -> defer.Deferred:
        """Every deal of one position (the drill-down view)."""
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.position_deals(
            ctx[0], account_id, position_id, ctx[1], from_ms, to_ms))
        return d

    def get_analytics(self, account_id: int, weeks: int = 4) -> dict:
        """Performance aggregation over the stored `deals` table.

        The old path fanned out to the broker one week at a time,
        sequentially, bounded by a hard 26-week ceiling and a slow-command
        timeout. From `deals` (the background backfill above) this is ONE
        indexed query instead.

        Stays synchronous on the reactor connection: there is no pool on
        this base (porting spec D1/D2), so deferToThread would need a
        connection per worker and would contend with the single-threaded
        `Repo._connect()`. The bound is ANALYTICS_ROW_LIMIT instead --
        retention is forever and `deals` projects ~400k rows/year, so an
        unbounded fetch two years in would stall every org's quotes,
        heartbeats and order sends at once.

        `weeks == 0` means "all stored history": since_ms is None and no
        clamp applies. Every other value keeps the pre-existing
        max(1, min(weeks, 26)) clamp -- the api caller (routes/insights.py)
        never sends 0, so it is unaffected.

        `truncated` used to mean "the broker had more deals than we
        fetched" (paging truncation). Read from the database that question
        does not apply -- the new meaning is "the backfill has not yet
        covered the requested window", from deal_backfill_state:
          - no state row yet -> True (backfill has not started)
          - state.exhausted -> False (we hold everything the broker has)
          - otherwise -> True iff backfilled_from_ms is None or newer than
            the window start (for weeks == 0 the window start is the
            beginning of time, so this is True unless exhausted).
        Without this, a freshly deployed system renders a near-empty
        Performance page with no signal that backfill is still running,
        which reads to the user as data loss.
        """
        weeks = int(weeks)
        if weeks == 0:
            since_ms = None
        else:
            weeks = max(1, min(weeks, 26))
            since_ms = self._now_ms() - weeks * WEEK_MS

        deals = self.repo.load_deals(
            account_id, since_ms, limit=ANALYTICS_ROW_LIMIT)
        stats = compute_analytics(deals)

        state = self.repo.get_backfill_state(account_id)
        if state is None:
            truncated = True
        elif state["exhausted"]:
            truncated = False
        elif state["backfilled_from_ms"] is None:
            truncated = True
        elif since_ms is None:
            truncated = True
        else:
            truncated = state["backfilled_from_ms"] > since_ms

        return {**stats, "weeks": weeks, "truncated": truncated}

    # ---------- read models ----------

    def get_health(self) -> dict:
        """Process status plus one row per org: its master (or None) and its
        own copying_enabled/dry_run. There is no global pair to report."""
        routing = self.routing_provider()
        orgs = []
        for org in self.repo.load_orgs():
            orgs.append({
                "org_id": org.org_id,
                "master": routing.master_by_org.get(org.org_id),
                "copying_enabled": org.copying_enabled,
                "dry_run": org.dry_run,
            })
        writer = self.writer
        return {
            "status": "ok",
            "orgs": orgs,
            # The async writer drops rather than blocks under overflow, so
            # its health is the only place a silent loss of executions,
            # position upserts and event logs becomes visible.
            "db_writer": {
                "healthy": writer.healthy if writer else None,
                "dropped": writer.dropped if writer else 0,
            },
        }

    def get_ticks(self, org_id: int) -> dict:
        """Live quotes and per-account marks for one org: everything the
        dashboard needs to tick prices in place between full refreshes.

        In-memory reads ONLY (the spot store and the tracker snapshot):
        no database, no broker round trips -- the api polls this several
        times a second while dashboard sockets are open. Deliberately no
        org validation either (unlike get_state): the api only polls orgs
        whose sockets already passed membership auth, and a DB read here
        would defeat the endpoint's point. Unknown org answers empty.
        """
        tracker = self.state_trackers.get(org_id)
        if tracker is None:
            return {"quotes": {}, "accounts": {}}
        accounts = {}
        for account_id, snap in tracker.snapshot().items():
            accounts[str(account_id)] = {
                "equity": snap["equity"],
                "open_pnl": snap["open_pnl"],
                "positions": [
                    {"position_id": p["position_id"], "symbol": p["symbol"],
                     "current_price": p["current_price"],
                     "pnl_quote": p["pnl_quote"]}
                    for p in snap["positions"]
                ],
            }
        return {"quotes": tracker.live_quotes(), "accounts": accounts}

    def get_state(self, org_id: int) -> dict:
        """Read model behind GET /state (and, proxied, GET /api/state).

        T9c -- the copies and master rows carry the fields the dashboard's
        Positions screen actually renders, not just raw ids:

        - per-copy `fill_price` (from the mapping row, stamped at
          activation) and `volume_lots` (the copy's own volume expressed in
          the SLAVE's lots, via that slave's cached symbol lot_size). The
          screen's "Fill Price" column rendered a literal "-" forever
          without the first, and volumes were shown in raw protocol units
          without the second.
        - per-master-position `symbol` (name, not `ID:<n>`), `volume_lots`
          and live `pnl_quote` from the state tracker.

        Everything here is a local read (mapping rows, the symbol cache, the
        in-memory tracker snapshot) -- no broker round trips -- so it stays
        cheap enough for the dashboard's 5 s poll.

        Strictly org-scoped: every source it reads (the org's tracker, the
        org's reconciler, mapping_rows(org_id=...), the org's master symbol
        map) belongs to `org_id` alone. An org with no engine (no master)
        answers with empty lists rather than raising or borrowing another
        org's state.
        """
        state_tracker = self.state_trackers.get(org_id)
        reconciler = self.reconcilers.get(org_id)
        accounts_snapshot = state_tracker.snapshot() if state_tracker is not None else {}
        mappings = self.repo.mapping_rows(org_id=org_id)

        # Per-call memo: several copies usually belong to the same slave, and
        # load_symbol_cache() is a database round trip each time.
        slave_symbol_caches: dict[int, dict] = {}

        def slave_symbols(account_id: int) -> dict:
            if account_id not in slave_symbol_caches:
                slave_symbol_caches[account_id] = self.repo.load_symbol_cache(account_id)
            return slave_symbol_caches[account_id]

        def lots(volume, lot_size) -> str | None:
            if volume is None or not lot_size:
                return None
            return f"{volume / lot_size:.2f}"

        def master_symbol(symbol_id: int):
            return self.master_symbols_by_org.get(org_id, {}).get(symbol_id)

        # The slave's own broker snapshot, so a copy can report the
        # protection actually sitting on it rather than the master's
        # intent. Keyed by (account, position).
        slave_protection: dict[tuple[int, int], tuple] = {}
        if reconciler is not None:
            for slave_id, snaps in (reconciler.slave_positions or {}).items():
                for snap in snaps:
                    slave_protection[(slave_id, snap.position_id)] = (
                        snap.stop_loss, snap.take_profit)

        def copies_for(key: str, value: int, symbol_name: str | None) -> list[dict]:
            out = []
            for m in mappings:
                if m.get(key) != value:
                    continue
                slave_account_id = m.get('slave_account_id')
                slave_lot_size = None
                if symbol_name is not None and slave_account_id is not None:
                    sym = slave_symbols(slave_account_id).get(symbol_name)
                    slave_lot_size = sym.lot_size if sym is not None else None
                out.append({
                    'slave_account_id': slave_account_id,
                    'slave_position_id': m.get('slave_position_id'),
                    'slave_order_id': m.get('slave_order_id'),
                    'slave_volume': m.get('slave_volume'),
                    'volume_lots': lots(m.get('slave_volume'), slave_lot_size),
                    'fill_price': m.get('fill_price'),
                    **dict(zip(
                        ('stop_loss', 'take_profit'),
                        slave_protection.get(
                            (slave_account_id, m.get('slave_position_id')),
                            (None, None)))),
                    'status': m.get('status'),
                    'error': m.get('error'),
                })
            return out

        master_positions = []
        pending_orders = []
        if state_tracker is not None and reconciler is not None:
            # Live per-position P&L, keyed by position id, from the same
            # snapshot the accounts block is built from -- so the Positions
            # screen and the Overview never disagree about a position.
            master_pnl_by_position: dict[int, float | None] = {}
            master_px_by_position: dict[int, float | None] = {}
            for tracked in accounts_snapshot.get(
                    reconciler.master_account_id, {}).get('positions', []):
                master_pnl_by_position[tracked['position_id']] = tracked.get('pnl_quote')
                master_px_by_position[tracked['position_id']] = tracked.get('current_price')

            # What the broker charges the MASTER, learned from its own
            # closed trades (engine/commission.py). The amend dialog turns
            # a money amount into a price and has to allow for it, or a
            # "$1.50 stop" loses $1.50 plus the commission.
            try:
                commission_rates = self.repo.load_commission_rates(
                    reconciler.master_account_id)
            except Exception:
                # A rate we cannot read is a rate we do not have, which the
                # dashboard already handles by not adjusting. Never let it
                # cost the operator the Positions screen.
                log.exception("state: could not load commission rates")
                commission_rates = {}

            for pos in reconciler.master_positions:
                sym = master_symbol(pos.symbol_id)
                symbol_name = sym.name if sym is not None else None
                master_positions.append({
                    'position_id': pos.position_id,
                    # Whose book this is: the desk amends/closes against it,
                    # and inferring it from a copy row would name a SLAVE.
                    'account_id': reconciler.master_account_id,
                    'symbol_id': pos.symbol_id,
                    'symbol': symbol_name,
                    'side': pos.side.value,
                    'volume': pos.volume,
                    'volume_lots': lots(pos.volume, sym.lot_size if sym is not None else None),
                    'price': pos.price,
                    # How many decimals this symbol is quoted to. A price
                    # carrying more than the broker quotes is refused
                    # outright, so anything computing one needs this.
                    'digits': sym.digits if sym is not None else None,
                    # Round-trip commission on ONE unit (protocol volume /
                    # 100). None means never observed, and the caller must
                    # then adjust nothing rather than assume free.
                    'commission_per_unit': commission_rates.get(pos.symbol_id),
                    'stop_loss': pos.stop_loss,
                    'take_profit': pos.take_profit,
                    'pnl_quote': master_pnl_by_position.get(pos.position_id),
                    'current_price': master_px_by_position.get(pos.position_id),
                    'label': pos.label,
                    'copies': copies_for('master_position_id', pos.position_id, symbol_name),
                })
            for order in reconciler.master_orders:
                sym = master_symbol(order.symbol_id)
                symbol_name = sym.name if sym is not None else None
                pending_orders.append({
                    'order_id': order.order_id,
                    'symbol_id': order.symbol_id,
                    'symbol': symbol_name,
                    'side': order.side.value if order.side is not None else None,
                    'order_type': order.order_type,
                    'price': order.price,
                    'volume': order.volume,
                    'volume_lots': lots(order.volume, sym.lot_size if sym is not None else None),
                    'label': order.label,
                    'copies': copies_for('master_order_id', order.order_id, symbol_name),
                })

        # A fresh process has no reconciler snapshot until the first resync
        # lands: Reconciler.master_positions starts empty and is only filled
        # in by reconcile_once() talking to the broker. Serve the last known
        # positions from the database rather than an empty screen -- the
        # next resync overwrites these (and drops 'stale') moments later.
        # pnl_quote/current_price need a live quote we do not have before
        # that resync, so they stay null rather than inventing a number.
        if reconciler is not None and not master_positions:
            for r in self.repo.load_open_positions(org_id):
                if r['account_id'] != reconciler.master_account_id:
                    continue
                sym = slave_symbols(r['account_id']).get(r['symbol'])
                master_positions.append({
                    'position_id': r['position_id'],
                    'account_id': r['account_id'],
                    'symbol_id': sym.symbol_id if sym is not None else None,
                    'symbol': r['symbol'],
                    'side': r['side'],
                    'volume': r['volume'],
                    'volume_lots': lots(r['volume'],
                                        sym.lot_size if sym is not None else None),
                    'price': r['entry_price'],
                    'digits': sym.digits if sym is not None else None,
                    'commission_per_unit': None,
                    'stop_loss': r['stop_loss'],
                    'take_profit': r['take_profit'],
                    'pnl_quote': None,
                    'current_price': None,
                    'label': None,
                    'copies': copies_for('master_position_id', r['position_id'],
                                         r['symbol']),
                    'stale': True,
                })

        return {
            "accounts": accounts_snapshot,
            "master_positions": master_positions,
            "pending_orders": pending_orders,
            "drift": [
                {
                    'id': item.id, 'kind': item.kind, 'account_id': item.account_id,
                    'position_id': item.position_id, 'order_id': item.order_id, 'detail': item.detail,
                }
                for item in (reconciler.current if reconciler is not None else [])
            ],
        }


# ---------- composition ----------

def _build_clients_by_account(
    repo: Repo, clients: dict[bool, dict[int, CTraderClient]], shards: int,
) -> Callable[[int], CTraderClient]:
    """Return a callable resolving an account_id to its sharded CTraderClient.

    Closes over `repo` and the (mutable) `clients` dict directly rather than
    over a not-yet-constructed CopierApp, so it can be handed to
    Dispatcher/Reconciler before CopierApp itself exists.
    """
    def clients_by_account(account_id: int) -> CTraderClient:
        account = next((a for a in repo.load_accounts() if a.account_id == account_id), None)
        if account is None:
            raise SendNotAttempted(f"account {account_id} not found")
        shard = account_id % shards
        client = clients.get(account.is_live, {}).get(shard)
        if client is None:
            raise SendNotAttempted(f"no client for account {account_id} (is_live={account.is_live}, shard={shard})")
        return client

    return clients_by_account


def _build_send_for_account(
    clients_by_account: Callable[[int], CTraderClient],
) -> Callable:
    def send_for_account(account_id: int, message):
        """Send a trade request (Dispatcher's sole caller) to an account's client.

        Raises SendNotAttempted for pre-wire failures (unknown account, no
        client for that account's environment/shard, app-level auth not yet
        ready, or the account never having been account-auth'd on this
        client) -- all safe to retry. Any other exception is ambiguous and
        must NOT be retried by the caller.

        Uses send_no_reply(), not send(): every message Dispatcher builds
        (new order, close, amend SL/TP, amend order, cancel) is a trade
        request the real cTrader server never tags a synchronous reply to
        (outcomes arrive later as untagged execution-event broadcasts), so
        waiting on send()'s response Deferred would time out on every single
        successful send and get misread as an ambiguous failure.

        send_no_reply()'s own Deferred can still fail -- e.g. no connected
        transport within its bounded wait, or whenConnected() itself failing
        outright -- but protocol.send() is only ever invoked *inside* its
        success callback, so by construction any failure here means the
        message never reached a transport, let alone the wire. That is
        exactly SendNotAttempted's contract (see its docstring), so it is
        reclassified as such here: left as a bare failure it would otherwise
        hit Dispatcher's ambiguous-failure branch and degrade the account on
        the very first transient blip instead of retrying at 1s/2s/4s.

        Also gates on client.is_account_authed(account_id), not just
        _accounts registry membership (NEW-1): send_no_reply's instant=True
        write reaches the wire in the same reactor turn a connection is
        confirmed, with no FIFO queue serializing it behind that
        connection's own (still in-flight) account-auth request the way
        there incidentally was before instant=True. _accounts membership
        only proves authorize_account() was ever called, not that auth has
        completed on the CURRENT connection -- so on every reconnect there
        is a window where _accounts already contains the account but the
        wire does not yet know it. Without this gate a trade sent in that
        window reaches the server on an unauthorized connection and is
        rejected -- silently, since there is no tagged response Deferred
        for it to fail. Gating here instead means that window raises
        SendNotAttempted -- provably nothing reached the wire -- so the
        retry ladder (1s/2s/4s) carries the send past re-auth instead of
        racing it.
        """
        client = clients_by_account(account_id)  # may raise SendNotAttempted
        if not client.ready.called:
            raise SendNotAttempted(f"client for account {account_id} not app-authed yet")
        if account_id not in getattr(client, '_accounts', {account_id: None}):
            raise SendNotAttempted(f"account {account_id} not yet account-authed on its client")
        is_authed = getattr(client, 'is_account_authed', None)
        if is_authed is not None and not is_authed(account_id):
            raise SendNotAttempted(
                f"account {account_id} auth not yet confirmed on the current connection"
            )

        def _reclassify_as_not_attempted(failure):
            raise SendNotAttempted(
                f"account {account_id}: no connected transport for send: {failure.value!r}"
            )

        d = client.send_no_reply(message)
        d.addErrback(_reclassify_as_not_attempted)
        return d

    return send_for_account


def build_app(
    repo: Repo,
    token_store: TokenStore,
    client_factory: Callable[[bool], CTraderClient],
    shards: int = DEFAULT_SHARDS,
    clock=None,
) -> CopierApp:
    """Build a fully-wired CopierApp, with one engine per org that has a master.

    Construction order matters: repo -> token_store -> clients -> dispatcher
    (with a real send_for_account from the very first line, never a
    None/placeholder patched in afterward) -> service -> per-org reconcilers
    (with a real dispatcher) -> per-org state_trackers -> CopierApp.
    """
    accounts = repo.load_accounts()
    envs_needed = sorted({a.is_live for a in accounts})

    clients: dict[bool, dict[int, CTraderClient]] = {}
    for is_live in envs_needed:
        clients[is_live] = {shard: client_factory(is_live) for shard in range(shards)}

    clients_by_account = _build_clients_by_account(repo, clients, shards)
    send_for_account = _build_send_for_account(clients_by_account)

    bucket = TokenBucket(clock=clock)
    dispatcher = Dispatcher(send_for_account=send_for_account, repo=repo, bucket=bucket, clock=clock)

    master_symbols_by_org: dict[int, dict] = {}

    # Cached for up to a second: routing used to be rebuilt from the
    # database on every event, which put ~200ms of queries and symbol
    # parsing in front of every copy. reload() invalidates it, so
    # control-plane changes still apply immediately; anything else is at
    # most TTL-stale, which the freshness contract (edits apply on the
    # next event) comfortably absorbs.
    routing_provider = RoutingCache(
        lambda: build_routing(repo.load_accounts(), repo.load_symbol_cache),
        clock=clock,
    )

    service = CopierService(
        repo=repo, dispatcher=dispatcher, routing_provider=routing_provider,
        master_symbols_by_org=master_symbols_by_org, clock=clock,
    )

    initial_routing = build_routing(accounts, repo.load_symbol_cache)
    reconcilers: dict[int, Reconciler] = {}
    state_trackers: dict[int, AccountStateTracker] = {}
    for org_id, master_id in initial_routing.master_by_org.items():
        master_account = next(a for a in accounts if a.account_id == master_id)
        reconcilers[org_id] = Reconciler(
            clients_by_account=clients_by_account, repo=repo,
            dispatcher=dispatcher, master_account_id=master_id, org_id=org_id,
        )
        # The inner dict is created here and mutated in place from then on, so
        # the tracker and the service keep seeing this org's current symbols.
        org_symbols = master_symbols_by_org.setdefault(org_id, {})
        master_client = clients[master_account.is_live][master_id % shards]
        state_trackers[org_id] = AccountStateTracker(
            master_client=master_client, repo=repo,
            master_account_id=master_id, symbols_by_id=org_symbols,
        )

    app = CopierApp(
        repo=repo, token_store=token_store, clients=clients, service=service,
        reconcilers=reconcilers, state_trackers=state_trackers,
        dispatcher=dispatcher, client_factory=client_factory, shards=shards,
        master_symbols_by_org=master_symbols_by_org,
        routing_provider=routing_provider, clients_by_account=clients_by_account,
        clock=clock,
    )

    # Service is constructed before the app (see module docstring on wiring
    # order), so the position-change hook is attached here instead of via
    # its ctor: any fill/close/cancel refreshes /state within ~1s.
    service.on_positions_changed = app.request_resync
    service.state_tracker_provider = app.state_tracker_for_account

    # Wire every push-event consumer to EVERY client (all shards, both
    # environments) -- slave shards must deliver execution events too, and any
    # client observing a token invalidation must trigger an immediate refresh.
    for env_clients in clients.values():
        for client in env_clients.values():
            app.wire_client(client)

    return app


@dataclass(frozen=True)
class BootConfig:
    postgres_dsn: str
    fernet_key: str
    client_id: str
    client_secret: str
    demo_host: str
    live_host: str
    ctrader_port: int
    shards: int


def read_env() -> BootConfig:
    fernet_key = os.environ.get("FERNET_KEY")
    if not fernet_key:
        raise RuntimeError("FERNET_KEY environment variable not set")
    return BootConfig(
        postgres_dsn=os.environ.get(
            "POSTGRES_DSN", "postgresql://copytrader:copytrader@localhost:5433/copytrader"
        ),
        fernet_key=fernet_key,
        client_id=os.environ.get("CTRADER_CLIENT_ID", ""),
        client_secret=os.environ.get("CTRADER_CLIENT_SECRET", ""),
        demo_host=os.environ.get("CTRADER_DEMO_HOST", "demo.ctraderapi.com"),
        live_host=os.environ.get("CTRADER_LIVE_HOST", "live.ctraderapi.com"),
        ctrader_port=int(os.environ.get("CTRADER_PORT", "5035")),
        shards=int(os.environ.get("SHARDS", DEFAULT_SHARDS)),
    )


def make_client_factory(config: BootConfig) -> Callable[[bool], CTraderClient]:
    def factory(is_live: bool) -> CTraderClient:
        host = config.live_host if is_live else config.demo_host
        sdk = make_sdk_client(host, config.ctrader_port)
        return CTraderClient(sdk, config.client_id, config.client_secret)

    return factory


def check_cutoff_reminders(repo: Repo) -> None:
    """LoopingCall body: one org-stamped 'reminder' event per account whose
    admin-set cutoff is at most CUTOFF_REMINDER_DAYS away, sent once per
    cutoff value (the stamp lives in the DB, so restarts never repeat it).
    The events INSERT is the whole fan-out: pg_notify carries it to the
    dashboard feed and the API's Telegram notifier. Never raises: a
    LoopingCall whose Deferred fails stops looping permanently.
    """
    try:
        due = repo.accounts_due_cutoff_reminder(days_before=CUTOFF_REMINDER_DAYS)
        for row in due:
            repo.log_event(
                'reminder', 'warning',
                {'action': 'cutoff_approaching',
                 'cutoff_date': row['cutoff_date'].isoformat(),
                 'days_left': row['days_left'],
                 'nickname': row['nickname'],
                 'trader_login': row['trader_login']},
                account_id=row['account_id'], org_id=row['org_id'],
            )
            repo.mark_cutoff_reminder_sent(row['account_id'], row['cutoff_date'])
    except Exception:
        log.exception("cutoff reminder scan failed")


def boot(config: BootConfig, reactor_) -> CopierApp:
    """Wire a CopierApp into `reactor_` without ever calling reactor.run().

    Kept separate from main() so the whole boot sequence -- including the
    exact composition that used to crash with `_build_send_for_account(None)`
    -- is exercised by tests with a fake reactor, not just by hand.
    """
    repo = Repo(config.postgres_dsn)
    # The writer opens and owns its OWN connection (porting spec D2): the
    # repo's single cached connection is documented single-threaded, and
    # the writer batches on a daemon thread.
    writer = AsyncWriter(config.postgres_dsn)
    repo.writer = writer
    writer.start()
    repo.ensure_partitions()
    token_store = TokenStore(config.postgres_dsn, config.fernet_key)
    client_factory = make_client_factory(config)

    app = build_app(repo, token_store, client_factory, shards=config.shards)

    def _startup():
        d = app.startup()
        d.addErrback(lambda f: log.error("startup failed: %s", f))
        return d

    reactor_.callWhenRunning(_startup)

    token_refresh_call = task.LoopingCall(app.refresh_due_tokens)
    token_refresh_call.clock = reactor_

    def _start_refresh_loop():
        start_d = token_refresh_call.start(TOKEN_REFRESH_INTERVAL_S, now=False)
        start_d.addErrback(lambda f: log.error("token refresh loop failed to start: %s", f))

    reactor_.callWhenRunning(_start_refresh_loop)

    # N9: keep Overview's balance/equity honest between resyncs.
    balance_refresh_call = task.LoopingCall(app.refresh_balances)
    balance_refresh_call.clock = reactor_
    app.balance_refresh_call = balance_refresh_call

    def _start_balance_loop():
        # now=False: startup() already does the first refresh, and firing
        # immediately here would race it onto the same queued path.
        start_d = balance_refresh_call.start(BALANCE_REFRESH_INTERVAL_S, now=False)
        start_d.addErrback(lambda f: log.error("balance refresh loop failed to start: %s", f))

    reactor_.callWhenRunning(_start_balance_loop)

    # Positions and drift read reconciler state, which only resync
    # refreshes; without this loop they go stale the moment the master
    # trades after boot.
    resync_call = task.LoopingCall(app.periodic_resync)
    resync_call.clock = reactor_
    app.resync_call = resync_call

    def _start_resync_loop():
        # now=False: startup() already runs the first resync.
        start_d = resync_call.start(RESYNC_INTERVAL_S, now=False)
        start_d.addErrback(lambda f: log.error("resync loop failed to start: %s", f))

    reactor_.callWhenRunning(_start_resync_loop)

    cutoff_reminder_call = task.LoopingCall(check_cutoff_reminders, repo)
    cutoff_reminder_call.clock = reactor_
    app.cutoff_reminder_call = cutoff_reminder_call

    def _start_cutoff_reminder_loop():
        # now=True: a reminder that came due while the copier was down
        # should go out at boot, not up to an hour later.
        start_d = cutoff_reminder_call.start(CUTOFF_REMINDER_INTERVAL_S, now=True)
        start_d.addErrback(lambda f: log.error("cutoff reminder loop failed to start: %s", f))

    reactor_.callWhenRunning(_start_cutoff_reminder_loop)

    # What the broker really charges, so a money-denominated stop can be
    # net of it instead of gross.
    commission_refresh_call = task.LoopingCall(app.refresh_commission_rates)
    commission_refresh_call.clock = reactor_
    app.commission_refresh_call = commission_refresh_call

    def _start_commission_loop():
        # now=False: startup() does the first refresh, once the accounts it
        # queries are actually authorized. Firing here instead raced that
        # authorization and every request came back INVALID_REQUEST.
        start_d = commission_refresh_call.start(COMMISSION_REFRESH_INTERVAL_S, now=False)
        start_d.addErrback(
            lambda f: log.error("commission refresh loop failed to start: %s", f))

    reactor_.callWhenRunning(_start_commission_loop)

    def _maintain_partitions():
        """Keep the monthly partition horizon from shrinking.

        Runs INLINE on the reactor, not on a worker thread: there is no
        connection pool on this base and `Repo._connect()` is explicitly
        single-threaded (porting spec D1), so handing this to
        deferToThread would put a second thread on the reactor's own
        psycopg connection. The bound is instead the transaction-local
        lock_timeout ensure_partitions() sets around each CREATE (see
        Repo.PARTITION_LOCK_TIMEOUT): a partition that cannot get its
        ACCESS EXCLUSIVE lock is skipped and retried tomorrow rather than
        queueing -- and queueing is the real hazard, because Postgres
        queues lock requests, so one CREATE blocked behind the nightly
        pg_dump blocks every later reader behind itself.

        Errors are swallowed rather than returned: a LoopingCall whose
        Deferred fails stops looping permanently, and partition
        maintenance is exactly the loop that must survive a bad night.
        """
        try:
            repo.ensure_partitions()
        except Exception:
            log.exception("partition maintenance failed")

    partition_call = task.LoopingCall(_maintain_partitions)
    partition_call.clock = reactor_
    app.partition_call = partition_call

    def _start_partition_loop():
        # now=False: boot() above already ran the first pass.
        d = partition_call.start(PARTITION_MAINTENANCE_INTERVAL_S, now=False)
        d.addErrback(lambda f: log.error(
            "partition maintenance loop stopped: %s", f))

    reactor_.callWhenRunning(_start_partition_loop)

    deal_backfill_call = task.LoopingCall(app.backfill_deals_once)
    deal_backfill_call.clock = reactor_
    app.deal_backfill_call = deal_backfill_call

    def _start_deal_backfill_loop():
        d = deal_backfill_call.start(DEAL_BACKFILL_INTERVAL_S, now=False)
        d.addErrback(lambda f: log.error("deal backfill loop stopped: %s", f))

    reactor_.callWhenRunning(_start_deal_backfill_loop)

    # Drain whatever the writer still holds before the process exits, so a
    # clean restart loses nothing. Bounded: shutdown must not hang.
    reactor_.addSystemEventTrigger(
        'before', 'shutdown', writer.flush_and_stop)

    site = make_control_site(app)
    reactor_.listenTCP(CONTROL_PORT, site, interface=CONTROL_BIND_INTERFACE)
    log.info("control endpoint listening on %s:%d", CONTROL_BIND_INTERFACE, CONTROL_PORT)

    return app


def main():
    """Boot the copier service and run forever."""
    # Without this the process has no root handler at all, so every log.info/
    # log.error in the package is silently dropped and `docker compose logs
    # copier` -- the README's own operational guidance -- shows nothing.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = read_env()
    log.info("copier boot: client_id=%s demo=%s live=%s shards=%d", config.client_id,
              config.demo_host, config.live_host, config.shards)

    from twisted.internet import reactor
    boot(config, reactor)
    reactor.run()


if __name__ == "__main__":
    main()
