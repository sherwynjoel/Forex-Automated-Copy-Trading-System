"""Boot sequence and composition root for the copier service.

Wires together all components:
- CTraderClient per environment (demo/live), sharded across N connections
- TokenStore for OAuth token management + daily refresh loop
- Repo for database access
- CopierService for event orchestration
- Reconciler for drift detection
- AccountStateTracker for balance/equity tracking
- Dispatcher with rate limiting
- Control endpoint for operational commands (control.py)

`build_app()` is the single place that wires the dependency graph together, in
the correct order (repo -> token_store -> clients -> dispatcher(send_for_account)
-> service -> reconciler(dispatcher) -> state_tracker -> CopierApp). No
component is ever constructed with a placeholder/None dependency and patched
afterward: `send_for_account`/`clients_by_account` close over the `repo` and
the (fully-built-before-use) `clients` dict directly, so they can be handed to
Dispatcher/Reconciler before CopierApp itself exists.

`boot()` wires that app into a (possibly fake, for tests) reactor: it never
calls `reactor.run()` itself, which keeps it unit-testable. `main()` is the
thin, untestable sliver that reads os.environ, builds the real reactor-backed
client factory, calls boot(), and runs the reactor forever.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from twisted.internet import defer, task
from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAErrorRes, ProtoOAGetAccountListByAccessTokenReq, ProtoOARefreshTokenReq,
)

from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.ctrader.symbols import fetch_symbol_map, by_id as symbols_by_id
from copier.db.repo import Repo
from copier.domain.models import SlaveConfig
from copier.engine.service import CopierService
from copier.engine.reconcile import Reconciler
from copier.engine.state import AccountStateTracker, PositionSnapshot as StatePositionSnapshot
from copier.engine.dispatch import Dispatcher, SendNotAttempted
from copier.engine.throttle import TokenBucket
from copier.engine.control import make_control_site

log = logging.getLogger(__name__)

DEFAULT_SHARDS = 1
TOKEN_REFRESH_INTERVAL_S = 86400.0  # once per day
CONTROL_PORT = 8080
# Docker-internal only: host isolation comes from compose NOT publishing this
# port, not from binding loopback. Binding 127.0.0.1 would make the endpoint
# unreachable from other containers on the bridge network (e.g. api -> copier).
CONTROL_BIND_INTERFACE = "0.0.0.0"


class CopierApp:
    """Composition root: wires all copier components together."""

    def __init__(
        self,
        repo: Repo,
        token_store: TokenStore,
        clients: dict[bool, dict[int, CTraderClient]],
        service: CopierService,
        reconciler: Reconciler,
        state_tracker: AccountStateTracker | None,
        dispatcher: Dispatcher,
        client_factory: Callable[[bool], CTraderClient],
        shards: int,
        master_symbols_by_id: dict,
        master_account_id: int | None,
        clock=None,
    ):
        self.repo = repo
        self.token_store = token_store
        self.clients = clients
        self.service = service
        self.reconciler = reconciler
        self.state_tracker = state_tracker
        self.dispatcher = dispatcher
        self.client_factory = client_factory
        self.shards = shards
        self.master_symbols_by_id = master_symbols_by_id
        self.master_account_id = master_account_id
        self.clock = clock

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

        enabled_ids = [a.account_id for a in accounts if a.enabled]
        if enabled_ids and self.state_tracker is not None:
            try:
                yield self.state_tracker.refresh_balances(enabled_ids)
            except Exception:
                log.exception("startup: refresh_balances failed")

        log.info("startup: complete")

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
                    self.master_symbols_by_id.clear()
                    self.master_symbols_by_id.update(symbols_by_id(symbol_map))
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

    def pause(self, account_id: int | None = None) -> defer.Deferred:
        """Pause copying globally or for a single slave, then reload."""
        if account_id is None:
            self.repo.set_setting("copying_enabled", False)
            self.repo.log_event('control', 'info', {'action': 'pause_global'})
            log.info("copying paused globally")
        else:
            self.repo.set_account_status(account_id, 'paused')
            self.repo.log_event(
                'control', 'info', {'action': 'pause_slave', 'account_id': account_id},
                account_id=account_id,
            )
            log.info("slave %s paused", account_id)
        return self.reload()

    def resume(self, account_id: int | None = None) -> defer.Deferred:
        """Resume copying globally or for a single slave, then reload."""
        if account_id is None:
            self.repo.set_setting("copying_enabled", True)
            self.repo.log_event('control', 'info', {'action': 'resume_global'})
            log.info("copying resumed globally")
        else:
            self.repo.set_account_status(account_id, 'ok')
            self.repo.log_event(
                'control', 'info', {'action': 'resume_slave', 'account_id': account_id},
                account_id=account_id,
            )
            log.info("slave %s resumed", account_id)
        return self.reload()

    def set_dry_run(self, enabled: bool) -> None:
        self.repo.set_setting("dry_run", enabled)
        self.repo.log_event('control', 'info', {'action': 'set_dry_run', 'enabled': enabled})
        log.info("dry-run mode: %s", "enabled" if enabled else "disabled")

    @defer.inlineCallbacks
    def resync(self):
        """Run reconciliation and feed the master's open positions into state_tracker."""
        items = yield self.reconciler.run()
        if self.state_tracker is not None and self.master_account_id is not None:
            positions = [
                StatePositionSnapshot(
                    position_id=p.position_id, symbol_id=p.symbol_id, side=p.side,
                    volume=p.volume, price=p.price, label=p.label,
                )
                for p in self.reconciler.master_positions
            ]
            self.state_tracker.set_positions(self.master_account_id, positions)
            try:
                yield self.state_tracker.ensure_spot_subscriptions()
            except Exception:
                log.exception("resync: ensure_spot_subscriptions failed")
        return items

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
                client.on_execution(self.service.handle_execution)
                client.on_tokens_invalidated(lambda _ids: self.refresh_due_tokens())
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
        # mirror_volume sizing) and, for a newly (re)designated master,
        # master_symbols_by_id (needed by normalize()'s unknown-symbol gate)
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

        master_account = next((a for a in accounts if a.role == 'master'), None)
        new_master_id = master_account.account_id if master_account else None

        # Refresh the IN-MEMORY master_symbols_by_id from whatever is
        # currently cached in the DB for the master, unconditionally --
        # decoupled from whether _fetch_and_cache_symbols actually hit the
        # broker this cycle. Without this, a former SLAVE (already cached
        # from being a slave) promoted to master would hit the cache-miss
        # skip above and never populate master_symbols_by_id at all, since
        # only a successful FETCH (not a cache hit) updates it in
        # _fetch_and_cache_symbols. This is a plain local DB read, not a
        # broker round trip, so doing it every reload is free.
        if master_account is not None:
            master_symbol_cache = self.repo.load_symbol_cache(master_account.account_id)
            self.master_symbols_by_id.clear()
            self.master_symbols_by_id.update(symbols_by_id(master_symbol_cache))

        if new_master_id != self.master_account_id:
            self.master_account_id = new_master_id
            self.service._master_account_id = new_master_id
            self.reconciler.master_account_id = new_master_id
            if master_account is not None:
                master_client = self._client_for_account(master_account)
                self.state_tracker = AccountStateTracker(
                    master_client=master_client, repo=self.repo,
                    master_account_id=new_master_id, symbols_by_id=self.master_symbols_by_id,
                )
            else:
                self.state_tracker = None

        self.repo.log_event('control', 'info', {'action': 'reload', 'account_count': len(accounts)})

    @defer.inlineCallbacks
    def discover(self, connection_id: int):
        """Discover accounts reachable with a connection's access token and upsert them."""
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

        discovered = list(res.ctidTraderAccount)
        for acc in discovered:
            self.repo.upsert_account(
                account_id=acc.ctidTraderAccountId,
                connection_id=connection_id,
                trader_login=acc.traderLogin,
                is_live=acc.isLive,
            )

        if built_new:
            client.on_execution(self.service.handle_execution)
            client.on_tokens_invalidated(lambda _ids: self.refresh_due_tokens())
            self.clients.setdefault(False, {}).setdefault(0, client)

        self.repo.log_event(
            'control', 'info',
            {'action': 'discover', 'connection_id': connection_id, 'account_count': len(discovered)},
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

    # ---------- read models ----------

    def get_health(self) -> dict:
        settings = self.repo.get_settings()
        accounts = self.repo.load_accounts()
        master_account = next((a for a in accounts if a.role == 'master'), None)
        return {
            "status": "ok",
            "master": master_account.account_id if master_account else None,
            "copying_enabled": settings.copying_enabled,
            "dry_run": settings.dry_run,
        }

    def get_state(self) -> dict:
        accounts_snapshot = self.state_tracker.snapshot() if self.state_tracker is not None else {}
        mappings = self.repo.mapping_rows()

        def copies_for(key: str, value: int) -> list[dict]:
            return [
                {
                    'slave_account_id': m.get('slave_account_id'),
                    'slave_position_id': m.get('slave_position_id'),
                    'slave_order_id': m.get('slave_order_id'),
                    'slave_volume': m.get('slave_volume'),
                    'status': m.get('status'),
                    'error': m.get('error'),
                }
                for m in mappings
                if m.get(key) == value
            ]

        master_positions = []
        pending_orders = []
        if self.state_tracker is not None:
            for pos in self.reconciler.master_positions:
                master_positions.append({
                    'position_id': pos.position_id,
                    'symbol_id': pos.symbol_id,
                    'side': pos.side.value,
                    'volume': pos.volume,
                    'price': pos.price,
                    'label': pos.label,
                    'copies': copies_for('master_position_id', pos.position_id),
                })
            for order in self.reconciler.master_orders:
                pending_orders.append({
                    'order_id': order.order_id,
                    'symbol_id': order.symbol_id,
                    'volume': order.volume,
                    'label': order.label,
                    'copies': copies_for('master_order_id', order.order_id),
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
                for item in self.reconciler.current
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
    """Build a fully-wired CopierApp.

    Construction order matters: repo -> token_store -> clients -> dispatcher
    (with a real send_for_account from the very first line, never a
    None/placeholder patched in afterward) -> service -> reconciler (with a
    real dispatcher) -> state_tracker -> CopierApp.
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

    master_account = next((a for a in accounts if a.role == 'master'), None)
    master_account_id = master_account.account_id if master_account else None
    master_symbols_by_id: dict = {}

    def slaves_provider() -> list[SlaveConfig]:
        return [
            SlaveConfig(
                account_id=a.account_id,
                enabled=a.enabled and a.status != 'paused',
                multiplier=a.multiplier,
                symbols=repo.load_symbol_cache(a.account_id),
            )
            for a in repo.load_accounts()
            if a.role == 'slave'
        ]

    service = CopierService(
        repo=repo, dispatcher=dispatcher, master_account_id=master_account_id,
        master_symbols_by_id=master_symbols_by_id, slaves_provider=slaves_provider, clock=clock,
    )

    reconciler = Reconciler(
        clients_by_account=clients_by_account, repo=repo, dispatcher=dispatcher,
        master_account_id=master_account_id,
    )

    state_tracker = None
    if master_account is not None:
        master_client = clients[master_account.is_live][master_account.account_id % shards]
        state_tracker = AccountStateTracker(
            master_client=master_client, repo=repo,
            master_account_id=master_account_id, symbols_by_id=master_symbols_by_id,
        )

    app = CopierApp(
        repo=repo, token_store=token_store, clients=clients, service=service,
        reconciler=reconciler, state_tracker=state_tracker, dispatcher=dispatcher,
        client_factory=client_factory, shards=shards, master_symbols_by_id=master_symbols_by_id,
        master_account_id=master_account_id, clock=clock,
    )

    # Wire execution + tokens-invalidated to EVERY client (all shards, both
    # environments) -- slave shards must deliver execution events too, and any
    # client observing a token invalidation must trigger an immediate refresh.
    for env_clients in clients.values():
        for client in env_clients.values():
            client.on_execution(service.handle_execution)
            client.on_tokens_invalidated(lambda _ids, app=app: app.refresh_due_tokens())

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

    site = make_control_site(app)
    reactor_.listenTCP(CONTROL_PORT, site, interface=CONTROL_BIND_INTERFACE)
    log.info("control endpoint listening on %s:%d", CONTROL_BIND_INTERFACE, CONTROL_PORT)

    return app


def main():
    """Boot the copier service and run forever."""
    config = read_env()
    log.info("copier boot: client_id=%s demo=%s live=%s shards=%d", config.client_id,
              config.demo_host, config.live_host, config.shards)

    from twisted.internet import reactor
    boot(config, reactor)
    reactor.run()


if __name__ == "__main__":
    main()
