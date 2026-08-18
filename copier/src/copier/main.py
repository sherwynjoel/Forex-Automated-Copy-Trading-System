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
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from twisted.internet import defer, task
from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOACancelOrderReq, ProtoOAClosePositionReq, ProtoOAErrorRes,
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
from copier.domain.models import MANUAL_ORDER_LABEL
from copier.engine.service import CopierService
from copier.engine.reconcile import Reconciler
from copier.engine.routing import OrgRouting, build_routing
from copier.engine.state import AccountStateTracker, PositionSnapshot as StatePositionSnapshot
from copier.engine.dispatch import Dispatcher, SendNotAttempted
from copier.engine.throttle import TokenBucket
from copier.engine.control import make_control_site
from copier.engine import queries
from copier.engine.analytics import compute_analytics

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
RESYNC_INTERVAL_S = 60.0
RESYNC_DEBOUNCE_S = 0.5
CONTROL_PORT = 8080
_SIDE_BY_NAME = {"BUY": ProtoOATradeSide.BUY, "SELL": ProtoOATradeSide.SELL}
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
        self._resync_requested = False

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
                # refresh_balances() fans out one ProtoOATraderReq per account and
                # DeferredLists them; the SDK's own 5 msg/s queue paces the wire.
                yield tracker.refresh_balances(enabled_ids)
            except Exception:
                log.exception("refresh_balances: broker request failed (org %s)", org_id)
                continue

            # Daily portfolio snapshot: upsert each refreshed account under
            # today's UTC date -- the last write of a day wins, so yesterday's
            # rows hold yesterday's closing values (Overview's vs-yesterday
            # comparison reads them). org_id comes off the AccountRow being
            # iterated so each desk's overview sums only its own snapshots.
            try:
                today = datetime.utcnow().date()
                snapshot = tracker.snapshot()
                for account in org_accounts:
                    state = snapshot.get(account.account_id)
                    if state is None or state.get("balance") is None:
                        continue
                    self.repo.save_portfolio_snapshot(
                        today, account.account_id, state["balance"],
                        state.get("equity"), org_id=account.org_id)
            except Exception:
                log.exception("refresh_balances: snapshot write failed (org %s)", org_id)

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
            log.info("authorized account %s", account.account_id)
        except Exception as e:
            log.error("failed to authorize account %s: %s", account.account_id, e)
            self.repo.set_account_status(account.account_id, 'degraded', f"authorization failed: {e}")

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

    def pause(self, org_id: int, account_id: int | None = None) -> defer.Deferred:
        """Pause copying for one org, or one of its slaves, then reload."""
        self.repo.get_org(org_id)  # raises for a missing (or None) org
        if account_id is None:
            self.repo.set_org_setting(org_id, "copying_enabled", False)
            self.repo.log_event('control', 'info', {'action': 'pause_org'}, org_id=org_id)
            log.info("copying paused for org %s", org_id)
        else:
            self._require_account_in_org(org_id, account_id)
            self.repo.set_account_status(account_id, 'paused')
            self.repo.log_event(
                'control', 'info', {'action': 'pause_slave', 'account_id': account_id},
                account_id=account_id, org_id=org_id,
            )
            log.info("slave %s paused", account_id)
        return self.reload()

    def resume(self, org_id: int, account_id: int | None = None) -> defer.Deferred:
        """Resume copying for one org, or one of its slaves, then reload."""
        self.repo.get_org(org_id)  # raises for a missing (or None) org
        if account_id is None:
            self.repo.set_org_setting(org_id, "copying_enabled", True)
            self.repo.log_event('control', 'info', {'action': 'resume_org'}, org_id=org_id)
            log.info("copying resumed for org %s", org_id)
        else:
            self._require_account_in_org(org_id, account_id)
            self.repo.set_account_status(account_id, 'ok')
            self.repo.log_event(
                'control', 'info', {'action': 'resume_slave', 'account_id': account_id},
                account_id=account_id, org_id=org_id,
            )
            log.info("slave %s resumed", account_id)
        return self.reload()

    def set_dry_run(self, org_id: int, enabled: bool) -> None:
        self.repo.get_org(org_id)  # raises for a missing (or None) org
        self.repo.set_org_setting(org_id, "dry_run", enabled)
        self.repo.log_event(
            'control', 'info', {'action': 'set_dry_run', 'enabled': enabled},
            org_id=org_id)
        log.info("dry-run for org %s: %s", org_id, "enabled" if enabled else "disabled")

    def request_resync(self) -> None:
        """Schedule a near-immediate resync in response to a position-
        changing event, so /state reflects a fill within ~1s instead of
        waiting for the next RESYNC_INTERVAL_S tick. Debounced: the burst
        a single trade produces (master fill + one fill per slave)
        collapses into one resync, and periodic_resync's in-flight guard
        still applies on top.
        """
        if self._resync_requested:
            return
        self._resync_requested = True

        def _fire():
            self._resync_requested = False
            self.periodic_resync()

        clock = self.clock
        if clock is None:
            from twisted.internet import reactor as clock
        clock.callLater(RESYNC_DEBOUNCE_S, _fire)

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
        all_items = []
        for oid in org_ids:
            reconciler = self.reconcilers.get(oid)
            if reconciler is None:
                continue
            items = yield reconciler.run()
            all_items.extend(items or [])
            tracker = self.state_trackers.get(oid)
            if tracker is not None:
                positions = [
                    StatePositionSnapshot(
                        position_id=p.position_id, symbol_id=p.symbol_id, side=p.side,
                        volume=p.volume, price=p.price, label=p.label,
                    )
                    for p in reconciler.master_positions
                ]
                tracker.set_positions(reconciler.master_account_id, positions)
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
        if volume_lots <= 0:
            raise ValueError("volume_lots must be greater than 0")

        limit_price = params.get("limit_price")
        stop_price = params.get("stop_price")
        if type_name == "LIMIT" and limit_price is None:
            raise ValueError("limit_price required for LIMIT orders")
        if type_name == "STOP" and stop_price is None:
            raise ValueError("stop_price required for STOP orders")

        # Resolves the client too, so an unknown account fails here.
        self._query_context(account_id)

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
        if params.get("stop_loss") is not None:
            req.stopLoss = float(params["stop_loss"])
        if params.get("take_profit") is not None:
            req.takeProfit = float(params["take_profit"])

        self.dispatcher.send_direct(account_id, req)
        summary = {
            "status": "submitted", "account_id": account_id,
            "symbol": symbol_name, "side": side_name, "order_type": type_name,
            "volume": volume, "volume_lots": f"{volume / sym.lot_size:.2f}",
        }
        self.repo.log_event(
            'control', 'info',
            {'action': 'manual_order', **{k: v for k, v in summary.items() if k != 'status'}},
            account_id=account_id,
        )
        return summary

    @defer.inlineCallbacks
    def close_position(self, account_id: int, position_id: int, volume_lots=None):
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
            account_id=account_id,
        )
        return {"status": "submitted", "account_id": account_id,
                "position_id": position_id, "volume": volume}

    def cancel_order(self, account_id: int, order_id: int) -> defer.Deferred:
        """Cancel one working order on any account."""
        def _send(_ctx):
            req = ProtoOACancelOrderReq()
            req.ctidTraderAccountId = account_id
            req.orderId = order_id
            self.dispatcher.send_direct(account_id, req)
            self.repo.log_event(
                'control', 'info',
                {'action': 'manual_cancel', 'order_id': order_id},
                account_id=account_id,
            )
            return {"status": "submitted", "account_id": account_id,
                    "order_id": order_id}

        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(_send)
        return d

    @defer.inlineCallbacks
    def close_all(self, org_id: int, account_id: int | None = None):
        """Kill switch for ONE org: flatten one of its accounts, or
        (account_id=None) every enabled, non-paused account in the org --
        master included.

        The org-wide kill switch pauses THAT ORG's copying FIRST, so the
        master closes it is about to send cannot fan out as copy-closes while
        the slaves' positions are simultaneously being closed directly (each
        close would otherwise race its own copy). No other org's settings or
        accounts are ever touched, and a single-account kill switch pauses
        nothing at all -- but still only accepts an account of its own org.

        The org is resolved FIRST, before anything is written or sent, so a
        caller that mis-binds its arguments (a positional account_id landing
        in org_id, or no org at all) fails loudly. Without this guard an
        unknown/None org would write no setting, match no target, and still
        return {"status": "flattened", "paused": true, "accounts": []} -- a
        kill switch reporting success having closed NOTHING.
        """
        self.repo.get_org(org_id)  # raises for a missing (or None) org

        if account_id is not None:
            self._require_account_in_org(org_id, int(account_id))
            summary = yield self._flatten_account(int(account_id))
            results = [summary]
            paused = False
        else:
            self.repo.set_org_setting(org_id, "copying_enabled", False)
            paused = True
            targets = [
                a for a in self.repo.load_accounts()
                if a.org_id == org_id and a.enabled and a.status != 'paused'
            ]
            results = []
            for account in targets:
                try:
                    summary = yield self._flatten_account(account.account_id)
                except Exception as e:
                    log.error("close_all: flatten %s failed: %s", account.account_id, e)
                    summary = {"account_id": account.account_id,
                               "positions_closed": 0, "orders_cancelled": 0,
                               "error": str(e)}
                results.append(summary)

        self.repo.log_event(
            'control', 'warning',
            {'action': 'kill_switch', 'org_wide': account_id is None,
             'accounts': results},
            account_id=account_id, org_id=org_id,
        )
        return {"status": "flattened", "paused": paused, "accounts": results}

    @defer.inlineCallbacks
    def _flatten_account(self, account_id: int):
        """Close every open position and cancel every working order in one
        account, from a FRESH broker snapshot (never this process's
        mappings -- orphans and manual positions must die too)."""
        client, _symbols = self._query_context(account_id)
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = account_id
        rec = queries.extract_or_raise((yield client.send(req)), "reconcile")

        positions_closed = 0
        for p in rec.position:
            close_req = ProtoOAClosePositionReq()
            close_req.ctidTraderAccountId = account_id
            close_req.positionId = p.positionId
            close_req.volume = p.tradeData.volume
            self.dispatcher.send_direct(account_id, close_req)
            positions_closed += 1

        orders_cancelled = 0
        for o in rec.order:
            cancel_req = ProtoOACancelOrderReq()
            cancel_req.ctidTraderAccountId = account_id
            cancel_req.orderId = o.orderId
            self.dispatcher.send_direct(account_id, cancel_req)
            orders_cancelled += 1

        self.repo.log_event(
            'control', 'warning',
            {'action': 'kill_switch_flatten', 'positions_closed': positions_closed,
             'orders_cancelled': orders_cancelled},
            account_id=account_id,
        )
        return {"account_id": account_id, "positions_closed": positions_closed,
                "orders_cancelled": orders_cancelled, "error": None}

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

    @defer.inlineCallbacks
    def get_analytics(self, account_id: int, weeks: int = 4):
        """Performance aggregation over the last `weeks` of deal history.

        cTrader caps each DealListReq at one week, so this loops windows and
        dedupes by deal id (boundary timestamps can land in two windows).
        Capped at 26 weeks = 26 broker round trips.
        """
        weeks = max(1, min(int(weeks), 26))
        client, symbols = self._query_context(account_id)
        now_ms = int(time.time() * 1000)
        week_ms = 7 * 24 * 3600 * 1000

        by_deal_id: dict[int, dict] = {}
        truncated = False
        for i in range(weeks):
            to_ms = now_ms - i * week_ms
            from_ms = to_ms - week_ms
            result = yield queries.deal_history(
                client, account_id, symbols, from_ms, to_ms)
            truncated = truncated or result["has_more"]
            for deal in result["deals"]:
                by_deal_id[deal["deal_id"]] = deal

        stats = compute_analytics(list(by_deal_id.values()))
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
        return {"status": "ok", "orgs": orgs}

    def get_state(self, org_id: int) -> dict:
        """Read model behind GET /state (and, proxied, GET /api/state).

        T9c -- the copies and master rows carry the fields the dashboard's
        Positions screen actually renders, not just raw ids:

        - per-copy `fill_price` (from the mapping row, stamped at
          activation) and `volume_lots` (the copy's own volume expressed in
          the SLAVE's lots, via that slave's cached symbol lot_size). The
          screen's "Fill Price" and "Slippage (pts)" columns rendered a
          literal "-" forever without the first, and volumes were shown in
          raw protocol units without the second.
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
            for tracked in accounts_snapshot.get(
                    reconciler.master_account_id, {}).get('positions', []):
                master_pnl_by_position[tracked['position_id']] = tracked.get('pnl_quote')

            for pos in reconciler.master_positions:
                sym = master_symbol(pos.symbol_id)
                symbol_name = sym.name if sym is not None else None
                master_positions.append({
                    'position_id': pos.position_id,
                    'symbol_id': pos.symbol_id,
                    'symbol': symbol_name,
                    'side': pos.side.value,
                    'volume': pos.volume,
                    'volume_lots': lots(pos.volume, sym.lot_size if sym is not None else None),
                    'price': pos.price,
                    'pnl_quote': master_pnl_by_position.get(pos.position_id),
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
                    'volume': order.volume,
                    'volume_lots': lots(order.volume, sym.lot_size if sym is not None else None),
                    'label': order.label,
                    'copies': copies_for('master_order_id', order.order_id, symbol_name),
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

    def routing_provider() -> OrgRouting:
        # Fresh DB-backed snapshot per call -- the same freshness contract the
        # old slaves_provider had (enabled/multiplier edits apply on the next
        # event without waiting for a reload).
        return build_routing(repo.load_accounts(), repo.load_symbol_cache)

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


def boot(config: BootConfig, reactor_) -> CopierApp:
    """Wire a CopierApp into `reactor_` without ever calling reactor.run().

    Kept separate from main() so the whole boot sequence -- including the
    exact composition that used to crash with `_build_send_for_account(None)`
    -- is exercised by tests with a fake reactor, not just by hand.
    """
    repo = Repo(config.postgres_dsn)
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
