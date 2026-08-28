"""Repository layer for mappings, events, settings, accounts, and symbol cache."""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.types.json import Jsonb

from copier.domain.models import SymbolInfo, PositionMappingEntry, OrderMappingEntry

log = logging.getLogger(__name__)


class MappingNotFound(KeyError):
    """Raised when a mapping update targets a non-existent row."""
    pass


@dataclass(frozen=True)
class Settings:
    """Global settings row (process config only -- per-org state lives on OrgRow)."""
    shards: int


@dataclass(frozen=True)
class OrgRow:
    """One org's engine-relevant state (per-org kill switch and dry-run)."""
    org_id: int
    name: str
    copying_enabled: bool
    dry_run: bool


@dataclass(frozen=True)
class AccountRow:
    """Account row representation."""
    account_id: int
    org_id: int
    connection_id: int
    trader_login: int
    is_live: bool
    role: str
    enabled: bool
    multiplier: Decimal
    status: str
    last_error: str | None


class Repo:
    """Repository for mappings, events, settings, accounts, and symbol cache.

    Satisfies the MappingState protocol with position_entries() and order_entries() methods.
    """

    # A connection idle longer than this is probed before it is handed out.
    # Short enough that any realistic gap between master events triggers a
    # probe, long enough that a burst of calls within one event does not.
    IDLE_PROBE_S = 2.0

    def __init__(self, dsn: str, writer=None):
        """Initialize repository with database connection string.

        `writer` is an AsyncWriter or None. When present, writes that
        nothing waits on (event logs, position/deal upserts, balance
        samples) are queued on it rather than executed inline on this
        connection -- see db/writer.py's module docstring for why that
        writer owns a wholly separate connection rather than sharing this
        one. Tests construct Repo(dsn) with no writer and get the old
        synchronous behaviour: every write lands before the call returns.
        """
        self.dsn = dsn
        self._conn: psycopg.Connection | None = None
        self._last_used = 0.0
        self.writer = writer

    def _open(self) -> psycopg.Connection:
        """Open a connection configured for long-lived, single-threaded use.

        prepare_threshold=None disables psycopg's automatic prepared
        statements. They were harmless when every call opened its own
        connection, but on a connection held for the process lifetime a
        migration that changes a selected column's type invalidates the
        server-side plan, and the resulting error is a ProgrammingError --
        NOT one of the connection-level errors below -- so the connection
        would be kept and that query would fail until the copier was
        restarted. Queries here are simple and measured at ~0.2ms; the
        plan cache is not worth that failure mode.

        The TCP keepalives matter for the same reason: a connection that is
        idle between market events must discover a dead peer itself rather
        than blocking the single-threaded reactor for the kernel's
        retransmit timeout.
        """
        return psycopg.connect(
            self.dsn,
            autocommit=True,
            prepare_threshold=None,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )

    @contextmanager
    def _connect(self):
        """Lease the cached autocommit connection, (re)opening it if needed.

        Opening a Postgres connection costs a TCP handshake plus SCRAM
        authentication -- ~11ms on the production box -- and the copy hot
        path makes a dozen or more repo calls per master event, so per-call
        connects were pure added latency on every copy. One cached
        connection serves every autocommit call.

        LIVENESS: a connection killed server-side while idle (postgres
        restart, admin termination, an idle-session timeout) still reports
        `closed == False` -- libpq only marks the socket bad after a failed
        read or write. Handing that connection out would fail the caller,
        and on the copy path the caller is a live master fill, so the copy
        would simply never happen. Anything idle longer than IDLE_PROBE_S
        is therefore probed with a trivial round trip (~0.2ms) before it is
        handed out, and replaced if the probe fails.

        The probe is deliberately the only automatic retry here: re-running
        a statement that already reached the server would double-apply
        non-idempotent writes (reduce_position_mapping's volume decrement,
        log_event's insert), so a connection that dies mid-statement fails
        that one call and heals for the next.

        Single-threaded by design: the copier runs everything on the
        Twisted reactor thread, so the one connection is never contended.
        """
        conn = self._conn
        now = time.monotonic()

        if conn is not None and not conn.closed and getattr(conn, "broken", False):
            conn = None  # psycopg knows it is dead even though it is not closed
            self._conn = None

        if conn is not None and not conn.closed and (now - self._last_used) > self.IDLE_PROBE_S:
            try:
                conn.execute("SELECT 1")
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                self._conn = None

        if conn is None or conn.closed:
            conn = self._open()
            self._conn = conn

        self._last_used = now
        try:
            yield conn
        except (psycopg.OperationalError, psycopg.InterfaceError):
            # Connection-level failures only: a constraint violation or a
            # bad query must NOT cost the healthy connection.
            self._conn = None
            try:
                conn.close()
            except Exception:
                pass
            raise
        finally:
            self._last_used = time.monotonic()

    # ---------- events ----------

    def log_event(
        self,
        category: str,
        severity: str,
        payload: dict,
        account_id: int | None = None,
        latency_ms: int | None = None,
        org_id: int | None = None,
        actor: str | None = None,
    ) -> int:
        """Log an event and return its ID.

        Args:
            category: Event category (master_event, slave_action, connection, auth, drift, control)
            severity: Severity level (info, warning, error)
            payload: JSON payload dict
            account_id: Optional account ID
            latency_ms: Optional latency in milliseconds
            org_id: Optional org ID (events.org_id is nullable -- the audit log
                must survive org deletion, so some events are org-less)
            actor: Email of the human who requested this action, forwarded by
                the api. None for autonomous copier activity (copy fills,
                reconnects) -- only operator-initiated actions have an actor.

        Returns:
            The new event ID, or 0 when the row was queued on the async
            writer instead of written inline. No copier caller uses this
            value for anything but truthiness/logging; the only consumer of
            event ids is the api's WS listener, which reads them off
            pg_notify, so the live feed simply trails by at most one batch
            interval when a writer is attached.
        """
        row = {
            'account_id': account_id,
            'org_id': org_id,
            'category': category,
            'severity': severity,
            'latency_ms': latency_ms,
            'payload': Jsonb(payload),
            'actor_email': actor,
        }
        if self.writer is not None:
            self.writer.submit('events', row)
            return 0
        with self._connect() as conn:
            (event_id,) = conn.execute(
                """
                INSERT INTO events (account_id, org_id, category, severity,
                                    latency_ms, payload, actor_email)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (account_id, org_id, category, severity, latency_ms,
                 Jsonb(payload), actor),
            ).fetchone()
        return event_id

    # ---------- settings ----------

    def get_settings(self) -> Settings:
        """Get current global (process-config-only) settings."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT shards FROM settings WHERE id = true"
            ).fetchone()
        if not row:
            raise RuntimeError("Settings row not found")
        return Settings(shards=row[0])

    def set_setting(self, name: str, value: Any) -> None:
        """Set a single global setting value.

        Args:
            name: Setting name (shards)
            value: New value
        """
        # Map setting names to SQL column names
        columns = {
            "shards": "shards",
        }
        if name not in columns:
            raise ValueError(f"Unknown setting: {name}")

        column = columns[name]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE settings SET {column} = %s WHERE id = true",
                (value,),
            )

    # ---------- orgs ----------

    def load_orgs(self) -> list[OrgRow]:
        """Load all orgs."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, copying_enabled, dry_run FROM orgs"
            ).fetchall()
        return [OrgRow(org_id=r[0], name=r[1], copying_enabled=r[2], dry_run=r[3])
                for r in rows]

    def get_org(self, org_id: int) -> OrgRow:
        """Get a single org by id.

        Raises RuntimeError if the org does not exist.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, copying_enabled, dry_run FROM orgs WHERE id = %s",
                (org_id,),
            ).fetchone()
        if not row:
            raise RuntimeError(f"org {org_id} not found")
        return OrgRow(org_id=row[0], name=row[1], copying_enabled=row[2], dry_run=row[3])

    def set_org_setting(self, org_id: int, name: str, value: Any) -> None:
        """Set a single per-org setting value.

        Args:
            org_id: The org to update
            name: Setting name (copying_enabled, dry_run)
            value: New value
        """
        columns = {"copying_enabled": "copying_enabled", "dry_run": "dry_run"}
        if name not in columns:
            raise ValueError(f"Unknown org setting: {name}")
        with self._connect() as conn:
            conn.execute(
                f"UPDATE orgs SET {columns[name]} = %s WHERE id = %s",
                (value, org_id),
            )

    def get_org_settings_version(self, org_id: int) -> int:
        """Current settings_version of an org row (trigger-bumped on every
        write to the row, by either process -- see migration 009)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT settings_version FROM orgs WHERE id = %s", (org_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown org: {org_id}")
            return row[0]

    def restore_copying_if_unchanged(self, org_id: int, expected_version: int) -> bool:
        """Atomically re-enable copying IF nobody wrote the org row since
        expected_version was read; returns whether the write happened.

        This is close_all's restore: a plain write here would clobber an
        operator's STOP COPYING issued mid-flatten, so the WHERE clause
        makes 'someone else wrote in between' lose the race safely (copying
        stays as the interim writer left it).
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE orgs SET copying_enabled = TRUE "
                "WHERE id = %s AND settings_version = %s",
                (org_id, expected_version),
            )
            return cur.rowcount == 1

    def connection_org(self, connection_id: int) -> int:
        """Get the org_id that owns a ctid_connection.

        Raises RuntimeError if the connection does not exist.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT org_id FROM ctid_connections WHERE id = %s", (connection_id,)
            ).fetchone()
        if not row:
            raise RuntimeError(f"connection {connection_id} not found")
        return row[0]

    # ---------- accounts ----------

    def load_accounts(self) -> list[AccountRow]:
        """Load all accounts."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ctid_trader_account_id, org_id, ctid_connection_id, trader_login, is_live,
                       role, enabled, multiplier, status, last_error
                FROM accounts
                """
            ).fetchall()

        return [
            AccountRow(
                account_id=row[0],
                org_id=row[1],
                connection_id=row[2],
                trader_login=row[3],
                is_live=row[4],
                role=row[5],
                enabled=row[6],
                multiplier=row[7],  # Already a Decimal from psycopg
                status=row[8],
                last_error=row[9],
            )
            for row in rows
        ]

    def org_for_account(self, account_id: int) -> int | None:
        """The org owning one broker account, or None if it is unknown.

        The narrow read behind push-event tenancy (CopierApp._on_trader_updated
        and _on_margin_call): those events arrive keyed only by
        ctidTraderAccountId, and every one of them needs the org before it can
        write anything. Resolving that through build_routing() would load the
        WHOLE accounts table and then a symbol cache per slave -- an N+1 of
        connections and queries on a path that fires on every balance change.

        Deliberately uncached: a stale mapping would route one tenant's money
        into another's tracker, which is exactly what the lookup exists to
        prevent. One connection, one indexed lookup on the primary key.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT org_id FROM accounts WHERE ctid_trader_account_id = %s",
                (account_id,),
            ).fetchone()
        return row[0] if row else None

    def set_account_status(self, account_id: int, status: str, last_error: str | None = None) -> None:
        """Set account status and optional error message."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET status = %s, last_error = %s WHERE ctid_trader_account_id = %s",
                (status, last_error, account_id),
            )

    def clear_degraded(self, account_id: int) -> bool:
        """Return a DEGRADED account to 'ok' and drop its stale last_error.

        N6: README §5 promises a degraded slave "clears automatically on its
        next successful send", but nothing in the code ever wrote status
        'ok' outside the manual resume() path -- degraded was sticky until
        an operator intervened. Dispatcher._on_send_success calls this.

        The `status = 'degraded'` guard is what makes it safe to call on
        every successful send: an account an operator deliberately PAUSED
        must never be silently resumed by a send that happens to succeed,
        and an already-'ok' account must not be rewritten (so this returns
        False and the caller logs nothing on the overwhelmingly common
        path). `last_error` is cleared alongside the status because it
        describes the failure that has just been superseded; leaving it set
        on an 'ok' account would keep showing a resolved error through
        GET /api/accounts.

        Returns True if this call actually cleared a degraded account.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE accounts SET status = 'ok', last_error = NULL
                WHERE ctid_trader_account_id = %s AND status = 'degraded'
                """,
                (account_id,),
            )
            return cursor.rowcount > 0

    def upsert_account(
        self,
        account_id: int,
        connection_id: int,
        org_id: int,
        trader_login: int,
        is_live: bool,
    ) -> bool:
        """Upsert an account, guarding cross-org ownership atomically.

        A broker account discovered by one org must never be silently
        rewritten by another org's discovery sweep -- the WHERE clause on the
        ON CONFLICT DO UPDATE makes the update a no-op (rowcount 0) whenever
        the existing row belongs to a different org, instead of racing a
        SELECT-then-UPDATE.

        Returns True if the row was inserted or updated (this org owns it),
        False if another org already owns this account_id.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                      org_id, trader_login, is_live)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ctid_trader_account_id) DO UPDATE SET
                    ctid_connection_id = EXCLUDED.ctid_connection_id,
                    trader_login = EXCLUDED.trader_login,
                    is_live = EXCLUDED.is_live
                WHERE accounts.org_id = EXCLUDED.org_id
                """,
                (account_id, connection_id, org_id, trader_login, is_live),
            )
            return cursor.rowcount > 0

    def accounts_due_cutoff_reminder(self, days_before: int) -> list[dict]:
        """Accounts whose admin-set cutoff_date is at most `days_before`
        calendar days away (or already past) and whose reminder for THAT
        date has not been sent. IS DISTINCT FROM makes a moved cutoff due
        again while a re-saved identical date stays quiet.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ctid_trader_account_id, org_id, nickname, trader_login,
                       cutoff_date, (cutoff_date - CURRENT_DATE) AS days_left
                FROM accounts
                WHERE cutoff_date IS NOT NULL
                  AND cutoff_date <= CURRENT_DATE + %s
                  AND cutoff_reminder_sent_for IS DISTINCT FROM cutoff_date
                ORDER BY ctid_trader_account_id
                """,
                (days_before,),
            ).fetchall()
        return [
            {"account_id": r[0], "org_id": r[1], "nickname": r[2],
             "trader_login": r[3], "cutoff_date": r[4], "days_left": r[5]}
            for r in rows
        ]

    def mark_cutoff_reminder_sent(self, account_id: int, cutoff_date) -> None:
        """Stamp which cutoff value the reminder covered (send-once guard)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET cutoff_reminder_sent_for = %s "
                "WHERE ctid_trader_account_id = %s",
                (cutoff_date, account_id),
            )

    # ---------- drift dismissals ----------

    def load_drift_dismissals(self, org_id: int) -> set[str]:
        """Drift ids the operator dismissed in this org (survives restarts)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT drift_id FROM drift_dismissals WHERE org_id = %s",
                (org_id,),
            ).fetchall()
        return {r[0] for r in rows}

    def save_drift_dismissal(self, org_id: int, drift_id: str) -> None:
        """Record one dismissal; re-dismissing the same item is a no-op."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO drift_dismissals (org_id, drift_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (org_id, drift_id),
            )

    def prune_drift_dismissals(self, org_id: int, keep: set[str]) -> None:
        """Drop this org's dismissals not in `keep` (their condition cleared,
        so a returning condition must alert again). Other orgs untouched."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM drift_dismissals WHERE org_id = %s "
                "AND NOT (drift_id = ANY(%s))",
                (org_id, list(keep)),
            )

    # ---------- symbol cache ----------

    def save_symbol_cache(self, account_id: int, infos: dict[str, SymbolInfo]) -> None:
        """Save symbol cache for account.

        Atomically deletes old cache and inserts new one to prevent partial updates.
        """
        with psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                # Delete existing cache
                conn.execute(
                    "DELETE FROM symbol_cache WHERE account_id = %s",
                    (account_id,),
                )
                # Insert new cache
                for name, info in infos.items():
                    conn.execute(
                        """
                        INSERT INTO symbol_cache (account_id, name, symbol_id, digits, lot_size, min_volume, step_volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (account_id, name, info.symbol_id, info.digits, info.lot_size, info.min_volume, info.step_volume),
                    )

    def load_symbol_cache(self, account_id: int) -> dict[str, SymbolInfo]:
        """Load symbol cache for account."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name, symbol_id, digits, lot_size, min_volume, step_volume
                FROM symbol_cache
                WHERE account_id = %s
                """,
                (account_id,),
            ).fetchall()

        return {
            row[0]: SymbolInfo(
                symbol_id=row[1],
                name=row[0],
                digits=row[2],
                lot_size=row[3],
                min_volume=row[4],
                step_volume=row[5],
            )
            for row in rows
        }

    # ---------- learned commission ----------

    def save_commission_rates(
        self, account_id: int, rates: dict[int, tuple[float, int]]
    ) -> None:
        """Record what the broker charged, per symbol, for one account.

        Upserts rather than replacing the account's set: a refresh reads a
        bounded window of history, so a symbol traded once months ago and
        not since is simply absent from the batch. Deleting on absence
        would throw that rate away and leave the operator's next stop on
        that symbol uncorrected, for no gain -- an old observation of a
        real charge is worth more than none.
        """
        if not rates:
            return
        with self._connect() as conn:
            with conn.transaction():
                for symbol_id, (per_unit, samples) in rates.items():
                    conn.execute(
                        """
                        INSERT INTO symbol_commission
                            (account_id, symbol_id, per_unit, sample_count, observed_at)
                        VALUES (%s, %s, %s, %s, now())
                        ON CONFLICT (account_id, symbol_id) DO UPDATE
                        SET per_unit = EXCLUDED.per_unit,
                            sample_count = EXCLUDED.sample_count,
                            observed_at = EXCLUDED.observed_at
                        """,
                        (account_id, symbol_id, float(per_unit), int(samples)),
                    )

    def load_commission_rates(self, account_id: int) -> dict[int, float]:
        """symbol_id -> round-trip commission per unit, for one account."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT symbol_id, per_unit FROM symbol_commission
                   WHERE account_id = %s""",
                (account_id,),
            ).fetchall()
        return {row[0]: float(row[1]) for row in rows}

    # ---------- positions ----------

    def upsert_positions(
        self,
        account_id: int,
        org_id: int | None,
        snapshots: Sequence[Any],
        symbols_by_id: Mapping[int, SymbolInfo],
    ) -> None:
        """Record this account's open positions.

        Fed from each resync so the Positions screen survives a restart --
        before this, positions existed only in Reconciler.master_positions
        and the in-memory AccountStateTracker, so a fresh process showed
        nothing until the first broker reconcile landed.

        Queued on the async writer when one is attached: nothing waits on
        this, and the next resync repairs anything a dropped row would
        have missed. Falls back to a synchronous inline upsert when there
        is no writer (tests construct Repo(dsn) with none).
        """
        for snap in snapshots:
            info = symbols_by_id.get(snap.symbol_id)
            row = {
                'account_id': account_id,
                'position_id': snap.position_id,
                'org_id': org_id,
                'symbol_id': snap.symbol_id,
                'symbol': info.name if info else None,
                'side': snap.side.name,
                'volume': snap.volume,
                'entry_price': snap.price,
                'stop_loss': getattr(snap, 'stop_loss', None),
                'take_profit': getattr(snap, 'take_profit', None),
                'label': snap.label or None,
                'status': 'open',
            }
            if self.writer is not None:
                self.writer.submit(
                    'positions', row, upsert_key=('account_id', 'position_id'))
            else:
                self._upsert_position_inline(row)

    def _upsert_position_inline(self, row: dict) -> None:
        """Synchronous fallback used when no writer is attached (tests)."""
        columns = tuple(sorted(row.keys()))
        cols = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in columns
            if c not in ('account_id', 'position_id'))
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO positions ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (account_id, position_id) DO UPDATE SET {updates}",
                [row[c] for c in columns],
            )

    def close_missing_positions(
        self, account_id: int, open_position_ids: Sequence[int]
    ) -> None:
        """Mark every stored-open position the broker no longer reports as
        closed. The broker's reconcile response is the truth about what is
        open; anything else we hold is stale.

        This is an UPDATE, not an insert/upsert, so it always runs
        synchronously on this connection -- the async writer only emits
        INSERT/upsert statements (see db/writer.py).

        `position_id = ANY('{}')` is false for every row when
        open_position_ids is empty, so NOT(...) closes everything the
        account holds -- the correct reading of "the broker reports this
        account flat."
        """
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE positions
                   SET status = 'closed',
                       closed_at = COALESCE(closed_at, now()),
                       updated_at = now()
                 WHERE account_id = %s
                   AND status = 'open'
                   AND NOT (position_id = ANY(%s))
                """,
                (account_id, [int(i) for i in open_position_ids]),
            )

    def load_open_positions(self, org_id: int) -> list[dict]:
        """This org's stored-open positions.

        Used to hydrate /state before the first resync of a fresh process
        lands: positions previously existed only in
        Reconciler.master_positions and the in-memory state tracker, so a
        restart showed an empty Positions screen until the broker answered.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account_id, position_id, symbol, side, volume, "
                "entry_price, stop_loss, take_profit "
                "FROM positions WHERE org_id = %s AND status = 'open' "
                "ORDER BY account_id, position_id",
                (org_id,),
            ).fetchall()
        return [
            {"account_id": r[0], "position_id": r[1], "symbol": r[2],
             "side": r[3], "volume": r[4], "entry_price": r[5],
             "stop_loss": r[6], "take_profit": r[7]}
            for r in rows
        ]

    # ---------- balance samples ----------

    def record_balance_sample(
        self,
        account_id: int,
        org_id: int | None,
        balance: float | None,
        equity: float | None,
        margin_used: float | None,
        unrealized_pnl: float | None,
    ) -> None:
        """Append one intraday balance/equity sample.

        portfolio_snapshots keeps its daily-close semantics (Overview
        depends on them); this is the sampled intraday series. Written
        from the balance-refresh LoopingCall, which is already off the
        trade path, and queued on the writer when one is attached.

        Same writer-or-inline-fallback pattern as upsert_positions: tests
        construct Repo(dsn) with no writer and expect the row visible
        immediately.
        """
        row = {
            'account_id': account_id,
            'org_id': org_id,
            'ts': datetime.now(timezone.utc),
            'balance': balance,
            'equity': equity,
            'margin_used': margin_used,
            'unrealized_pnl': unrealized_pnl,
        }
        if self.writer is not None:
            self.writer.submit(
                'balance_samples', row, upsert_key=('account_id', 'ts'))
        else:
            self._record_balance_sample_inline(row)

    def _record_balance_sample_inline(self, row: dict) -> None:
        """Synchronous fallback used when no writer is attached (tests)."""
        columns = tuple(sorted(row.keys()))
        cols = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in columns
            if c not in ('account_id', 'ts'))
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO balance_samples ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (account_id, ts) DO UPDATE SET {updates}",
                [row[c] for c in columns],
            )

    # ---------- deals ----------

    def upsert_deals(
        self, account_id: int, org_id: int | None, deals: list[dict]
    ) -> None:
        """Store deals in the shape queries._map_deal produces.

        Keyed (account_id, deal_id) and upserted, which is what lets the
        backfill re-run a window safely after a broker error. Queued on
        the async writer when one is attached, else written inline.
        """
        for d in deals:
            close = d.get('close') or {}
            row = {
                'account_id': account_id,
                'deal_id': d['deal_id'],
                'org_id': org_id,
                'order_id': d.get('order_id'),
                'position_id': d.get('position_id'),
                'symbol_id': d.get('symbol_id'),
                'symbol': d.get('symbol'),
                'side': d.get('side'),
                'volume': d.get('volume'),
                'filled_volume': d.get('filled_volume'),
                'execution_price': d.get('execution_price'),
                'status': d.get('status'),
                'commission': d.get('commission'),
                'create_timestamp': d.get('create_timestamp'),
                'execution_timestamp': d['execution_timestamp'],
                'is_close': bool(d.get('close')),
                'entry_price': close.get('entry_price'),
                'gross_profit': close.get('gross_profit'),
                'swap': close.get('swap'),
                'balance_after': close.get('balance'),
                'closed_volume': close.get('closed_volume'),
            }
            if self.writer is not None:
                self.writer.submit(
                    'deals', row, upsert_key=('account_id', 'deal_id'))
            else:
                self._upsert_deal_inline(row)

    def _upsert_deal_inline(self, row: dict) -> None:
        """Synchronous fallback used when no writer is attached (tests)."""
        columns = tuple(sorted(row.keys()))
        cols = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in columns
            if c not in ('account_id', 'deal_id'))
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO deals ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (account_id, deal_id) DO UPDATE SET {updates}",
                [row[c] for c in columns],
            )

    def load_deals(
        self, account_id: int, since_ms: int | None = None,
        limit: int = 50_000,
    ) -> list[dict]:
        """Deals for one account, reconstructed into the nested shape
        queries._map_deal produces -- including the `close` sub-dict -- so
        engine.analytics.compute_analytics runs over them with no change to
        its input shape.

        Two fields do NOT round-trip through this table, both deliberately:
        `deals` has a single `commission` column (the deal's own charge),
        not a second one for the broker's closePositionDetail.commission,
        so a reconstructed `close["commission"]` here is the SAME value as
        the row's top-level `commission` rather than that close-specific
        figure; and `volume_lots` is not reconstructed at all (lot size is
        a symbol-cache fact, not a stored deal fact, and rebuilding it here
        would need a join). This is safe for compute_analytics: it reads
        top-level `commission` once per deal and never reads
        `close["commission"]` or `volume_lots`. A caller that needs either
        exact field should query `deals` directly rather than through this
        method.

        `limit` bounds the result set: on this base, analytics runs
        synchronously on the reactor connection (see the porting spec's
        D3), so an account with years of history must not be able to stall
        the reactor with an unbounded fetch.
        """
        sql = (
            "SELECT deal_id, order_id, position_id, symbol_id, symbol, side, "
            "volume, filled_volume, execution_price, status, commission, "
            "create_timestamp, execution_timestamp, is_close, entry_price, "
            "gross_profit, swap, balance_after, closed_volume "
            "FROM deals WHERE account_id = %s"
        )
        params: list = [account_id]
        if since_ms is not None:
            sql += " AND execution_timestamp >= %s"
            params.append(since_ms)
        sql += " ORDER BY execution_timestamp LIMIT %s"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        out = []
        for r in rows:
            (deal_id, order_id, position_id, symbol_id, symbol, side, volume,
             filled_volume, execution_price, status, commission,
             create_ts, exec_ts, is_close, entry_price, gross_profit, swap,
             balance_after, closed_volume) = r
            out.append({
                "deal_id": deal_id,
                "order_id": order_id,
                "position_id": position_id,
                "symbol_id": symbol_id,
                "symbol": symbol,
                "side": side,
                "volume": volume,
                "filled_volume": filled_volume,
                "execution_price": execution_price,
                "status": status,
                "commission": float(commission) if commission is not None else None,
                "create_timestamp": create_ts,
                "execution_timestamp": exec_ts,
                "close": {
                    "entry_price": entry_price,
                    "gross_profit": float(gross_profit) if gross_profit is not None else 0.0,
                    "swap": float(swap) if swap is not None else 0.0,
                    "commission": float(commission) if commission is not None else 0.0,
                    "balance": float(balance_after) if balance_after is not None else 0.0,
                    "closed_volume": closed_volume,
                } if is_close else None,
            })
        return out

    def get_backfill_state(self, account_id: int) -> dict | None:
        """This account's deal-backfill watermark, or None if it has never
        been backfilled."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT backfilled_from_ms, backfilled_to_ms, exhausted "
                "FROM deal_backfill_state WHERE account_id = %s",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return {'backfilled_from_ms': row[0], 'backfilled_to_ms': row[1],
                'exhausted': row[2]}

    def set_backfill_state(
        self, account_id: int, from_ms: int | None, to_ms: int | None,
        exhausted: bool,
    ) -> None:
        """Advance the watermark. from_ms only ever moves backwards and
        to_ms only forwards, so a re-run of an old window cannot rewind
        progress."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deal_backfill_state
                    (account_id, backfilled_from_ms, backfilled_to_ms,
                     exhausted, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (account_id) DO UPDATE SET
                    backfilled_from_ms = LEAST(
                        deal_backfill_state.backfilled_from_ms,
                        EXCLUDED.backfilled_from_ms),
                    backfilled_to_ms = GREATEST(
                        deal_backfill_state.backfilled_to_ms,
                        EXCLUDED.backfilled_to_ms),
                    exhausted = EXCLUDED.exhausted,
                    updated_at = now()
                """,
                (account_id, from_ms, to_ms, exhausted),
            )

    # ---------- mappings ----------

    def create_position_mapping(
        self,
        master_position_id: int,
        slave_account_id: int,
        client_order_id: str,
        org_id: int,
        symbol: str | None = None,
    ) -> None:
        """Create a pending position mapping, or leave the existing one alone.

        N2 -- master POSITION INCREASES. cTrader merges a second
        same-direction fill on the same symbol into the SAME position (new
        volume, volume-weighted entry price) rather than opening a separate
        one, so the master's "add to position" arrives as another
        ORDER_FILLED carrying the ORIGINAL positionId. normalize() turns
        that into a second MasterPositionOpened for an already-mapped
        position and decide() correctly emits an OpenMarket for the DELTA
        (see domain/decision.py and test_decision_positions.py's
        test_increase_emits_delta_open_for_mapped_position). Dispatch then
        derives the same deterministic client_order_id (`cm{master_position_
        id}.{slave_account_id}`, engine/dispatch.py:client_order_id_for) as
        the original open -- which used to hit `mappings.client_order_id
        TEXT UNIQUE`, raise UniqueViolation, and get caught by Dispatcher's
        per-intent handler as `intent_processing_failed`: the increase was
        NEVER SENT to any slave, and every slave was marked degraded for a
        routine trading action.

        ON CONFLICT DO NOTHING makes the mapping row the ONE row per
        (master_position_id, slave_account_id) it has to be, so the send
        proceeds; the increase's own fill then accumulates into that row via
        activate_position_mapping's additive volume (see its docstring).
        The alternative shape -- a per-entry suffix in the client_order_id,
        one row per fill -- was rejected precisely because of the merge
        above: the slave's second fill reports the SAME slave_position_id,
        so the extra rows would collide on (slave_account_id,
        slave_position_id) and reduce_position_mapping would deduct each
        close from every one of them.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_position_id, slave_account_id,
                                      client_order_id, org_id, status, symbol)
                VALUES (%s, %s, %s, %s, 'pending', %s)
                ON CONFLICT (client_order_id) DO NOTHING
                """,
                (master_position_id, slave_account_id, client_order_id, org_id,
                 symbol),
            )

    def record_master_fill(
        self,
        org_id: int,
        master_position_id: int,
        fill_price: float | None,
        exec_timestamp: int | None,
    ) -> None:
        """Stamp the MASTER's fill price and broker clock onto every copy of
        that position, WITHIN ONE ORG.

        mappings.fill_price is the SLAVE's half; without the master's,
        realized slippage and true copy latency were not computable from the
        production database at all (item 4 of the 2026-08-22 slippage
        diagnosis). COALESCE so a fill event that carries no executionPrice
        never blanks a price already recorded.

        org_id is REQUIRED and part of the WHERE clause. cTrader position
        ids are per-account sequences, so two orgs on different brokers or
        environments routinely hold the same positionId -- without the
        predicate, org A's master fill price and broker timestamp would be
        stamped onto org B's mapping rows, and org B's realized-slippage
        query (the single measurement this table exists to enable) would
        report slippage computed against another tenant's master price. The
        bumped updated_at also perturbs reconcile.py's stale-pending drift
        detector on rows that never changed.

        This is a single UPDATE, not an insert/upsert, so it always runs
        synchronously on this connection -- the async writer only emits
        INSERT/upsert statements.
        """
        if fill_price is None and exec_timestamp is None:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mappings
                   SET master_fill_price = COALESCE(%s, master_fill_price),
                       master_exec_timestamp = COALESCE(%s, master_exec_timestamp),
                       updated_at = now()
                 WHERE master_position_id = %s
                   AND org_id = %s
                """,
                (fill_price, exec_timestamp, master_position_id, org_id),
            )

    def activate_position_mapping(
        self,
        slave_account_id: int,
        client_order_id: str,
        slave_position_id: int,
        slave_volume: int,
        fill_price: float | None = None,
    ) -> None:
        """Activate a position mapping, ACCUMULATING the filled volume.

        `slave_volume = COALESCE(slave_volume, 0) + %s` rather than a plain
        assignment (N2): a pending row starts with NULL volume, so a first
        fill still lands at exactly the filled volume, while the second fill
        for the same client_order_id -- the slave's copy of a master
        POSITION INCREASE, which cTrader merges into the slave's existing
        position (same slave_position_id, deal.filledVolume = the delta,
        see create_position_mapping) -- adds to it. The mapping row then
        carries the slave position's true aggregate size, which is what
        every downstream consumer needs: partial_close_volume computes each
        slave's proportional close against it (domain/sizing.py), and
        reduce_position_mapping deducts from it.

        The same accumulation is also the correct reading of a genuine
        ORDER_PARTIAL_FILL followed by its ORDER_FILLED (engine/service.py
        routes both here): each execution event's deal.filledVolume is that
        deal's volume, not a running total, so the previous assignment
        silently discarded everything but the last one.

        The one thing accumulation cannot distinguish is a DUPLICATE
        delivery of the same execution event, which would now double-count.
        cTrader delivers execution events once per connection and the copier
        holds a single connection per account, so this is not reachable
        today; it is the deliberate trade for making increases work at all.

        `fill_price` (T9c) is stamped only when supplied and only if not
        already set: the FIRST fill's execution price is the copy's entry
        price the Positions screen shows against the
        master's entry; a later increase's price would silently redefine it.

        `slave_account_id` is an OWNERSHIP predicate, not a lookup key --
        client_order_id is already unique. It is here because the coid is
        derived and guessable (`cm{master_position_id}.{slave_account_id}`,
        engine/dispatch.py:client_order_id_for) while master position ids are
        per-account broker sequences that collide across orgs: this statement
        writes real-money sizing state, so it accumulates volume onto a row
        only when the account that reported the fill is the row's own slave.
        The service has the account in hand from the execution event.

        Raises MappingNotFound if no mapping exists with this client_order_id
        FOR THAT SLAVE ACCOUNT.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET slave_position_id = %s,
                    slave_volume = COALESCE(slave_volume, 0) + %s,
                    fill_price = COALESCE(fill_price, %s),
                    status = 'active',
                    updated_at = now()
                WHERE client_order_id = %s AND slave_account_id = %s
                """,
                (slave_position_id, slave_volume, fill_price, client_order_id,
                 slave_account_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(
                    f"No mapping found with client_order_id={client_order_id} "
                    f"for slave_account_id={slave_account_id}")

    def reduce_position_mapping(
        self,
        slave_account_id: int,
        slave_position_id: int,
        closed_volume: int,
    ) -> None:
        """Reduce a position mapping by the closed volume.

        Sets status to 'closed' when slave_volume reaches 0.
        Atomic: uses single UPDATE with GREATEST to avoid TOCTOU race on concurrent reduces.
        """
        with self._connect() as conn:
            # Atomic single-statement update: decrement slave_volume safely,
            # set status='closed' when volume <= 0, update timestamp
            row = conn.execute(
                """
                UPDATE mappings
                SET slave_volume = GREATEST(slave_volume - %s, 0),
                    status = CASE WHEN slave_volume - %s <= 0 THEN 'closed' ELSE 'active' END,
                    updated_at = now()
                WHERE slave_account_id = %s AND slave_position_id = %s AND status = 'active'
                RETURNING slave_volume, status
                """,
                (closed_volume, closed_volume, slave_account_id, slave_position_id),
            ).fetchone()

    def fail_mapping(self, slave_account_id: int, client_order_id: str, error: str) -> None:
        """Mark a mapping as failed with an error message.

        `slave_account_id` is an ownership predicate (see
        activate_position_mapping): one slave's rejection must not fail
        another slave's copy, so the row is pinned to the account whose
        broker actually reported the rejection.

        Raises MappingNotFound if no mapping exists with this client_order_id
        FOR THAT SLAVE ACCOUNT.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET status = 'failed', error = %s, updated_at = now()
                WHERE client_order_id = %s AND slave_account_id = %s
                """,
                (error, client_order_id, slave_account_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(
                    f"No mapping found with client_order_id={client_order_id} "
                    f"for slave_account_id={slave_account_id}")

    def create_order_mapping(
        self,
        master_order_id: int,
        slave_account_id: int,
        client_order_id: str,
        org_id: int,
        symbol: str | None = None,
    ) -> None:
        """Create a pending order mapping."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_order_id, slave_account_id,
                                      client_order_id, org_id, status, symbol)
                VALUES (%s, %s, %s, %s, 'pending', %s)
                """,
                (master_order_id, slave_account_id, client_order_id, org_id,
                 symbol),
            )

    def activate_order_mapping(
        self, slave_account_id: int, client_order_id: str, slave_order_id: int
    ) -> None:
        """Activate an order mapping.

        `slave_account_id` is an ownership predicate (see
        activate_position_mapping): the slave_order_id being stamped is only
        meaningful on the account that issued it, so writing it onto another
        account's row would misdirect every later lookup keyed on
        (slave_account_id, slave_order_id) -- close_order_mapping and
        activate_pending_fill both are.

        Raises MappingNotFound if no pending mapping exists with this
        client_order_id FOR THAT SLAVE ACCOUNT.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET slave_order_id = %s, status = 'active', updated_at = now()
                WHERE client_order_id = %s AND slave_account_id = %s
                """,
                (slave_order_id, client_order_id, slave_account_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(
                    f"No mapping found with client_order_id={client_order_id} "
                    f"for slave_account_id={slave_account_id}")

    def close_order_mapping(self, slave_account_id: int, slave_order_id: int) -> None:
        """Close an order mapping.

        Raises MappingNotFound if no active mapping exists with these identifiers.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET status = 'closed', updated_at = now()
                WHERE slave_account_id = %s AND slave_order_id = %s AND status = 'active'
                """,
                (slave_account_id, slave_order_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(
                    f"No active order mapping found: slave_account_id={slave_account_id}, slave_order_id={slave_order_id}"
                )

    def link_pending_fill(
        self,
        master_order_id: int,
        slave_account_id: int,
        master_position_id: int,
    ) -> None:
        """Link pending fill by stamping master_position_id onto the order mapping row.

        Only updates active mappings; stale/failed rows are not touched.
        Raises MappingNotFound if no active order mapping exists.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET master_position_id = %s, updated_at = now()
                WHERE master_order_id = %s AND slave_account_id = %s AND status = 'active'
                """,
                (master_position_id, master_order_id, slave_account_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(
                    f"No active order mapping found: master_order_id={master_order_id}, slave_account_id={slave_account_id}"
                )

    def activate_pending_fill(
        self,
        slave_account_id: int,
        slave_order_id: int,
        slave_position_id: int,
        slave_volume: int,
        fill_price: float | None = None,
    ) -> None:
        """Activate the pending fill by converting to position mapping.

        `fill_price` (T9c) is stamped when supplied so the Positions screen
        can show a real fill price for a copy that came from a
        mirrored pending order, exactly as for a mirrored market open.

        Raises MappingNotFound if no active order mapping exists.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET slave_position_id = %s, slave_volume = %s,
                    fill_price = COALESCE(fill_price, %s),
                    status = 'active', updated_at = now()
                WHERE slave_account_id = %s AND slave_order_id = %s AND status = 'active'
                """,
                (slave_position_id, slave_volume, fill_price, slave_account_id, slave_order_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(
                    f"No active order mapping found: slave_account_id={slave_account_id}, slave_order_id={slave_order_id}"
                )

    def adopt_position_mapping(
        self,
        master_position_id: int,
        slave_account_id: int,
        slave_position_id: int,
        slave_volume: int,
        org_id: int,
    ) -> None:
        """Adopt an existing position (drift remedy)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_position_id, slave_account_id, slave_position_id,
                                      slave_volume, org_id, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (master_position_id, slave_account_id, slave_position_id, slave_volume, org_id),
            )

    def save_portfolio_snapshot(
        self, snapshot_date, account_id: int,
        balance: float | None, equity: float | None,
        org_id: int | None = None,
    ) -> None:
        """Upsert one account's daily snapshot; the last write of a day wins,
        so a day's row converges on that day's closing value.

        `org_id` is stamped from the account row the caller is already
        iterating so the api's per-org overview can sum only its own desk's
        snapshots. It is a plain nullable column with no FK (migration
        006): like events.org_id, the history has to survive the account
        (and its org) going away."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_snapshots (snapshot_date, account_id, balance,
                                                 equity, org_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_date, account_id)
                DO UPDATE SET balance = EXCLUDED.balance, equity = EXCLUDED.equity,
                              org_id = COALESCE(EXCLUDED.org_id, portfolio_snapshots.org_id),
                              updated_at = now()
                """,
                (snapshot_date, account_id, balance, equity, org_id),
            )

    def mapping_rows(self, org_id: int | None = None) -> list[dict]:
        """Get mapping rows as dictionaries, optionally filtered to one org."""
        query = """
            SELECT id, master_position_id, master_order_id, slave_account_id,
                   slave_position_id, slave_order_id, slave_volume, client_order_id,
                   org_id, status, error, fill_price, symbol, created_at, updated_at
            FROM mappings
            """
        params: tuple = ()
        if org_id is not None:
            query += " WHERE org_id = %s"
            params = (org_id,)

        with self._connect() as conn:
            # Dict rows for THIS query only. Scoped to the cursor, never set
            # on the connection: the connection is shared now, and mutating
            # its row_factory would poison every later tuple-indexed read.
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                rows = cur.execute(query, params).fetchall()

        return rows

    # ---------- MappingState protocol ----------

    def position_entries(self, master_position_id: int) -> Sequence[PositionMappingEntry]:
        """Get active position entries for a master position.

        Returns only active mappings with slave_position_id IS NOT NULL.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT slave_account_id, slave_position_id, slave_volume
                FROM mappings
                WHERE master_position_id = %s AND status = 'active' AND slave_position_id IS NOT NULL
                """,
                (master_position_id,),
            ).fetchall()

        return [
            PositionMappingEntry(slave_account_id=row[0], slave_position_id=row[1], slave_volume=row[2])
            for row in rows
        ]

    def order_entries(self, master_order_id: int) -> Sequence[OrderMappingEntry]:
        """Get active order entries for a master order.

        Returns only active mappings.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT slave_account_id, slave_order_id
                FROM mappings
                WHERE master_order_id = %s AND status = 'active'
                """,
                (master_order_id,),
            ).fetchall()

        return [
            OrderMappingEntry(slave_account_id=row[0], slave_order_id=row[1])
            for row in rows
        ]

    # ---------- partitions ----------

    PARTITIONED_TABLES = ("executions", "balance_samples")

    # How long a CREATE TABLE ... PARTITION OF may wait for its
    # ACCESS EXCLUSIVE lock before giving up for the day.
    #
    # Postgres queues lock requests, so a partition creation blocked behind
    # a long ACCESS SHARE holder (the nightly pg_dump that feeds the S3
    # backups) also blocks every later reader behind ITSELF. Without a
    # timeout this call -- synchronous, once a day, on the single reactor
    # connection -- could hold every other repo call for the length of the
    # whole dump. Migration 012 creates twelve months up front and this
    # maintains three ahead, so failing one day is harmless; failing to
    # give up is not.
    PARTITION_LOCK_TIMEOUT = "2s"

    def ensure_partitions(self, months_ahead: int = 3) -> list[str]:
        """Create any missing monthly partitions out to `months_ahead`.

        Migration 012 creates a year up front and a DEFAULT partition as a
        safety net, so this is belt-and-braces -- but a deploy that sits for
        a year would otherwise start dropping rows into DEFAULT, and moving
        them back out later needs a strong lock. Cheap to run daily.

        Each CREATE runs in its own transaction with a transaction-local
        lock_timeout (set_config(..., is_local => true), i.e. SET LOCAL --
        not a session SET, which would leak the bound onto this cached
        connection's NEXT caller). A partition that cannot get its lock is
        skipped and retried on the next daily tick rather than blocking,
        and one skipped partition does not abandon the others.
        """
        created: list[str] = []
        with self._connect() as conn:
            for table in self.PARTITIONED_TABLES:
                for m in range(months_ahead + 1):
                    row = conn.execute(
                        "SELECT to_char(date_trunc('month', now()) "
                        "+ make_interval(months => %s), 'YYYY_MM'), "
                        "(date_trunc('month', now()) "
                        "+ make_interval(months => %s))::date, "
                        "(date_trunc('month', now()) "
                        "+ make_interval(months => %s))::date",
                        (m, m, m + 1),
                    ).fetchone()
                    suffix, lo, hi = row
                    name = f"{table}_{suffix}"
                    exists = conn.execute(
                        "SELECT 1 FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relname = %s AND n.nspname = ANY(current_schemas(true))",
                        (name,),
                    ).fetchone()
                    if exists:
                        continue
                    try:
                        with conn.transaction():
                            # set_config(..., is_local => true) is
                            # SET LOCAL with a bind parameter; plain
                            # SET LOCAL does not accept one.
                            conn.execute(
                                "SELECT set_config('lock_timeout', %s, true)",
                                (self.PARTITION_LOCK_TIMEOUT,))
                            conn.execute(
                                f'CREATE TABLE "{name}" PARTITION OF {table} '
                                f"FOR VALUES FROM ('{lo}') TO ('{hi}')"
                            )
                    except psycopg.errors.LockNotAvailable:
                        log.warning(
                            "partition maintenance: could not lock %s to create "
                            "%s within %s; retrying tomorrow",
                            table, name, self.PARTITION_LOCK_TIMEOUT)
                        continue
                    created.append(name)
        return created
