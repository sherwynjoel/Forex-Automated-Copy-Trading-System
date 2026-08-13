"""Boot sequence and composition root for the copier service.

Wires together all components:
- CTraderClient per environment (demo/live)
- TokenStore for OAuth token management
- Repo for database access
- CopierService for event orchestration
- Reconciler for drift detection
- AccountStateTracker for balance/equity tracking
- Dispatcher with rate limiting
- Control endpoint for operational commands
- Token refresh loop
"""

import logging
import os
from datetime import datetime
from typing import Callable, Mapping

from twisted.internet import defer, task, reactor
from cryptography.fernet import Fernet

from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.domain.models import SymbolInfo, SlaveConfig
from copier.engine.service import CopierService
from copier.engine.reconcile import Reconciler
from copier.engine.state import AccountStateTracker
from copier.engine.dispatch import Dispatcher, SendNotAttempted
from copier.engine.throttle import TokenBucket
from copier.engine.control import make_control_site

log = logging.getLogger(__name__)

# Default shards
DEFAULT_SHARDS = 1
TOKEN_REFRESH_INTERVAL_S = 86400.0  # Once per day


class CopierApp:
    """Composition root: wires all copier components together."""

    def __init__(
        self,
        repo: Repo,
        token_store: TokenStore,
        clients: dict[bool, dict[int, CTraderClient]],  # {is_live: {shard_index: client}}
        service: CopierService,
        reconciler: Reconciler,
        state_tracker: AccountStateTracker,
        dispatcher: Dispatcher,
        clock=None,
    ):
        """Initialize CopierApp.

        Args:
            repo: Repository for database access
            token_store: Token management
            clients: CTrader clients per environment and shard
            service: Event orchestration service
            reconciler: Drift detection
            state_tracker: Account state tracker
            dispatcher: Intent dispatcher
            clock: Optional Twisted Clock (for testing)
        """
        self.repo = repo
        self.token_store = token_store
        self.clients = clients  # {is_live: {shard: CTraderClient}}
        self.service = service
        self.reconciler = reconciler
        self.state_tracker = state_tracker
        self.dispatcher = dispatcher
        self.clock = clock or reactor

    def pause(self, account_id: int | None = None) -> None:
        """Pause copying globally or per-slave."""
        if account_id is None:
            self.repo.set_setting("copying_enabled", False)
            self.repo.log_event(
                'control',
                'info',
                {'action': 'pause_global'},
            )
            log.info("Copying paused globally")
        else:
            self.repo.set_account_status(account_id, 'paused')
            self.repo.log_event(
                'control',
                'info',
                {'action': 'pause_slave', 'account_id': account_id},
                account_id=account_id,
            )
            log.info("Slave %s paused", account_id)

    def resume(self, account_id: int | None = None) -> None:
        """Resume copying globally or per-slave."""
        if account_id is None:
            self.repo.set_setting("copying_enabled", True)
            self.repo.log_event(
                'control',
                'info',
                {'action': 'resume_global'},
            )
            log.info("Copying resumed globally")
        else:
            self.repo.set_account_status(account_id, 'ok')
            self.repo.log_event(
                'control',
                'info',
                {'action': 'resume_slave', 'account_id': account_id},
                account_id=account_id,
            )
            log.info("Slave %s resumed", account_id)

    def set_dry_run(self, enabled: bool) -> None:
        """Enable or disable dry-run mode."""
        self.repo.set_setting("dry_run", enabled)
        log.info("Dry-run mode: %s", "enabled" if enabled else "disabled")

    def resync(self) -> defer.Deferred:
        """Trigger reconciliation."""
        log.info("Starting resync...")
        return self.reconciler.run()

    def reload(self) -> defer.Deferred:
        """Reload accounts and settings."""
        log.info("Reloading accounts and settings...")
        d = defer.maybeDeferred(self._do_reload)
        return d

    def _do_reload(self) -> None:
        """Perform reload (reload accounts, re-auth, etc.)."""
        # TODO: Implement reload logic
        pass

    def get_health(self) -> dict:
        """Get health status."""
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
        """Get current state."""
        snapshot = self.state_tracker.snapshot()
        return {
            "accounts": snapshot,
            "master_positions": [],
            "pending_orders": [],
            "drift": [
                {
                    'id': item.id,
                    'kind': item.kind,
                    'account_id': item.account_id,
                    'position_id': item.position_id,
                    'order_id': item.order_id,
                    'detail': item.detail,
                }
                for item in self.reconciler.current
            ] if hasattr(self.reconciler, 'current') else [],
        }


def _build_clients_by_account(app: CopierApp) -> Callable[[int], CTraderClient]:
    """Build a function that returns the CTraderClient for an account.

    Uses sharding to distribute accounts across clients (key: (is_live, shard_index)).
    """
    shards = app.repo.get_settings().shards

    def clients_by_account(account_id: int) -> CTraderClient:
        account = next(
            (a for a in app.repo.load_accounts() if a.account_id == account_id),
            None
        )
        if not account:
            raise ValueError(f"Account {account_id} not found")

        # Shard: spread slaves across clients
        shard_index = account_id % shards
        clients_for_env = app.clients.get(account.is_live, {})
        client = clients_for_env.get(shard_index)
        if not client:
            raise ValueError(f"No client for account {account_id} (env={account.is_live}, shard={shard_index})")
        return client

    return clients_by_account


def _build_send_for_account(
    app: CopierApp,
) -> Callable:
    """Build send_for_account function that honors SendNotAttempted contract.

    For each account, selects the appropriate client and sends the message.
    Must raise SendNotAttempted for pre-wire failures (connection down, throttle).
    Any other exception is ambiguous and will NOT be retried.
    """
    clients_by_account = _build_clients_by_account(app)

    def send_for_account(account_id: int, message):
        """Send a message to an account's client.

        Raises:
            SendNotAttempted: Request never reached the wire (safe to retry).
            Any other exception: Ambiguous failure, don't retry.
        """
        try:
            client = clients_by_account(account_id)
        except ValueError as e:
            # Connection or client lookup failed — this is a pre-wire failure
            raise SendNotAttempted(f"Client not available for account {account_id}") from e

        if not client:
            raise SendNotAttempted(f"No client for account {account_id}")

        # Check if connection is ready
        if hasattr(client, 'ready') and not client.ready.called:
            raise SendNotAttempted(f"Client for account {account_id} not ready")

        # Send the message (may still fail, but attempt was made)
        return client.send(message)

    return send_for_account


@defer.inlineCallbacks
def startup_app(app: CopierApp) -> defer.Deferred:
    """Startup sequence: connect/auth accounts, load symbols, initialize services.

    Returns:
        Deferred that fires when startup is complete
    """
    log.info("Copier startup starting...")

    try:
        # Load accounts
        accounts = app.repo.load_accounts()
        log.info("Loaded %d accounts", len(accounts))

        if not accounts:
            log.warning("No accounts configured; service will idle")
            app.repo.log_event('connection', 'warning', {'message': 'No accounts configured'})
            return

        # Connect and authorize clients per environment
        for is_live in [False, True]:
            env_name = "live" if is_live else "demo"
            env_accounts = [a for a in accounts if a.is_live == is_live]
            if not env_accounts:
                log.info("No %s accounts", env_name)
                continue

            log.info("Connecting %d %s accounts", len(env_accounts), env_name)

            # Create clients per shard
            settings = app.repo.get_settings()
            shards = settings.shards
            for shard_index in range(shards):
                shard_accounts = [
                    a for a in env_accounts if a.account_id % shards == shard_index
                ]
                if not shard_accounts:
                    continue

                if is_live not in app.clients:
                    app.clients[is_live] = {}

                client = app.clients[is_live].get(shard_index)
                if not client:
                    log.error("No client created for %s shard %d", env_name, shard_index)
                    continue

                # Start the client
                client.start()
                log.info("Started %s client (shard %d)", env_name, shard_index)

                # Wait for ready
                yield client.ready
                log.info("%s client (shard %d) ready", env_name, shard_index)

                # Authorize accounts on this shard
                for account in shard_accounts:
                    try:
                        token_pair = app.token_store.get(account.connection_id)
                        d = client.authorize_account(account.account_id, token_pair.access_token)
                        yield d
                        log.info("Authorized account %s on %s client", account.account_id, env_name)
                    except Exception as e:
                        log.error("Failed to authorize account %s: %s", account.account_id, e)
                        app.repo.set_account_status(
                            account.account_id, 'degraded',
                            f"Authorization failed: {e}"
                        )

        # Load symbol maps and build slaves_provider
        def slaves_provider():
            accounts = app.repo.load_accounts()
            return [
                SlaveConfig(
                    account_id=a.account_id,
                    enabled=a.enabled,
                )
                for a in accounts if a.role == 'slave' and a.enabled
            ]

        # Run initial reconciliation
        log.info("Running initial reconciliation...")
        yield app.reconciler.run()

        # Refresh balances
        account_ids = [a.account_id for a in accounts if a.enabled]
        if account_ids:
            log.info("Refreshing balances for %d accounts", len(account_ids))
            yield app.state_tracker.refresh_balances(account_ids)

        # Subscribe to spot prices
        log.info("Subscribing to spot prices...")
        yield app.state_tracker.ensure_spot_subscriptions()

        log.info("Copier startup complete")

    except Exception as e:
        log.exception("Startup failed: %s", e)
        app.repo.log_event('connection', 'error', {'action': 'startup_failed', 'error': str(e)})
        raise


def main():
    """Boot the copier service."""
    # Read environment variables
    postgres_dsn = os.environ.get(
        "POSTGRES_DSN",
        "postgresql://copytrader:copytrader@localhost:5433/copytrader"
    )
    fernet_key = os.environ.get("FERNET_KEY")
    if not fernet_key:
        raise RuntimeError("FERNET_KEY environment variable not set")

    client_id = os.environ.get("CTRADER_CLIENT_ID")
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET")
    demo_host = os.environ.get("CTRADER_DEMO_HOST", "demo.ctraderapi.com")
    live_host = os.environ.get("CTRADER_LIVE_HOST", "live.ctraderapi.com")
    ctrader_port = int(os.environ.get("CTRADER_PORT", "5035"))
    shards = int(os.environ.get("SHARDS", DEFAULT_SHARDS))

    log.info("Copier boot: FERNET_KEY=%s, CTRADER_CLIENT_ID=%s", fernet_key[:20], client_id[:20])

    # Build components
    repo = Repo(postgres_dsn)
    token_store = TokenStore(postgres_dsn, fernet_key)

    # Build SDK clients per environment (lazily instantiated per shard)
    clients = {}  # {is_live: {shard: CTraderClient}}

    # Demo environment clients
    clients[False] = {}
    for shard_index in range(shards):
        sdk_client = make_sdk_client(demo_host, ctrader_port)
        clients[False][shard_index] = CTraderClient(sdk_client, client_id, client_secret)

    # Live environment clients
    clients[True] = {}
    for shard_index in range(shards):
        sdk_client = make_sdk_client(live_host, ctrader_port)
        clients[True][shard_index] = CTraderClient(sdk_client, client_id, client_secret)

    # Get master account (always on first client)
    accounts = repo.load_accounts()
    master_account = next((a for a in accounts if a.role == 'master'), None)
    if not master_account:
        log.warning("No master account configured")

    # Token bucket for rate limiting (10 req/sec)
    bucket = TokenBucket(capacity=10, refill_rate=10)

    # Build reconciler (needs access to clients_by_account)
    def clients_by_account_lambda(account_id: int) -> CTraderClient:
        account = next((a for a in repo.load_accounts() if a.account_id == account_id), None)
        if not account:
            raise ValueError(f"Account {account_id} not found")
        shard = account_id % shards
        return clients[account.is_live][shard]

    reconciler = Reconciler(
        clients_by_account=clients_by_account_lambda,
        repo=repo,
        dispatcher=None,  # Will be set after dispatcher creation
        master_account_id=master_account.account_id if master_account else None,
    )

    # Build dispatcher with send_for_account
    send_for_account = _build_send_for_account(None)  # Placeholder, will update

    dispatcher = Dispatcher(
        send_for_account=send_for_account,
        repo=repo,
        bucket=bucket,
    )

    # Build state tracker
    master_client = clients[master_account.is_live][0] if master_account else clients[False][0]
    master_symbols_by_id = {}  # TODO: Load symbol maps
    state_tracker = AccountStateTracker(
        master_client=master_client,
        repo=repo,
        master_account_id=master_account.account_id if master_account else None,
        symbols_by_id=master_symbols_by_id,
    )

    # Build service
    def slaves_provider():
        accounts = repo.load_accounts()
        return [
            SlaveConfig(
                account_id=a.account_id,
                enabled=a.enabled,
            )
            for a in accounts if a.role == 'slave' and a.enabled
        ]

    service = CopierService(
        repo=repo,
        dispatcher=dispatcher,
        master_account_id=master_account.account_id if master_account else None,
        master_symbols_by_id=master_symbols_by_id,
        slaves_provider=slaves_provider,
    )

    # Create app
    app = CopierApp(
        repo=repo,
        token_store=token_store,
        clients=clients,
        service=service,
        reconciler=reconciler,
        state_tracker=state_tracker,
        dispatcher=dispatcher,
    )

    # Update send_for_account to use the app
    send_for_account = _build_send_for_account(app)
    dispatcher._send_for_account = send_for_account
    reconciler.dispatcher = dispatcher

    # Register callbacks
    if master_account:
        master_client.on_execution(lambda account_id, evt: service.handle_execution(account_id, evt))
        master_client.on_tokens_invalidated(lambda account_ids: _on_tokens_invalidated(app, account_ids))

    # Schedule startup
    reactor.callWhenRunning(lambda: startup_app(app).addErrback(lambda f: log.error("Startup failed: %s", f)))

    # Schedule token refresh loop
    def refresh_due_tokens():
        now = datetime.utcnow()
        due_connections = token_store.due_for_refresh(now)
        for connection_id in due_connections:
            _refresh_token(app, connection_id)

    token_refresh_call = task.LoopingCall(refresh_due_tokens)
    token_refresh_call.clock = reactor
    reactor.callWhenRunning(lambda: token_refresh_call.start(TOKEN_REFRESH_INTERVAL_S, now=False))

    # Start control endpoint
    site = make_control_site(app)
    reactor.listenTCP(8080, site, interface='127.0.0.1')
    log.info("Control endpoint listening on 127.0.0.1:8080")

    # Run reactor
    reactor.run()


def _refresh_token(app: CopierApp, connection_id: int) -> None:
    """Refresh a token and handle failures.

    On success: token_store.rotate() persists the new pair.
    On failure: token_store.mark() the connection as refresh_failed and log alert.
    """
    try:
        pair = app.token_store.get(connection_id)
        # TODO: Send ProtoOARefreshTokenReq via appropriate client
        # For now, just log
        log.info("Token refresh for connection %s due at %s", connection_id, pair.expires_at)
    except Exception as e:
        log.error("Token refresh failed for connection %s: %s", connection_id, e)
        try:
            app.token_store.mark(connection_id, 'refresh_failed')
            app.repo.log_event(
                'auth',
                'error',
                {
                    'action': 'token_refresh_failed',
                    'connection_id': connection_id,
                    'error': str(e)
                }
            )
        except Exception as e2:
            log.error("Failed to mark token as refresh_failed: %s", e2)


def _on_tokens_invalidated(app: CopierApp, account_ids: list[int]) -> None:
    """Handle token invalidation event from the broker.

    Trigger immediate token refresh for affected accounts.
    """
    log.warning("Tokens invalidated for accounts: %s", account_ids)
    app.repo.log_event(
        'auth',
        'warning',
        {
            'action': 'tokens_invalidated',
            'account_ids': account_ids
        }
    )
    # TODO: Trigger immediate refresh


if __name__ == "__main__":
    main()
