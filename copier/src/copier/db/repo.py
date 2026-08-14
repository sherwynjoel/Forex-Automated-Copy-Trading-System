"""Repository layer for mappings, events, settings, accounts, and symbol cache."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

import psycopg
from psycopg.types.json import Jsonb

from copier.domain.models import SymbolInfo, PositionMappingEntry, OrderMappingEntry


class MappingNotFound(KeyError):
    """Raised when a mapping update targets a non-existent row."""
    pass


@dataclass(frozen=True)
class Settings:
    """Global settings row."""
    copying_enabled: bool
    dry_run: bool
    shards: int


@dataclass(frozen=True)
class AccountRow:
    """Account row representation."""
    account_id: int
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

    def __init__(self, dsn: str):
        """Initialize repository with database connection string."""
        self.dsn = dsn

    # ---------- events ----------

    def log_event(
        self,
        category: str,
        severity: str,
        payload: dict,
        account_id: int | None = None,
        latency_ms: int | None = None,
    ) -> int:
        """Log an event and return its ID.

        Args:
            category: Event category (master_event, slave_action, connection, auth, drift, control)
            severity: Severity level (info, warning, error)
            payload: JSON payload dict
            account_id: Optional account ID
            latency_ms: Optional latency in milliseconds

        Returns:
            The new event ID
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            (event_id,) = conn.execute(
                """
                INSERT INTO events (account_id, category, severity, latency_ms, payload)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (account_id, category, severity, latency_ms, Jsonb(payload)),
            ).fetchone()
        return event_id

    # ---------- settings ----------

    def get_settings(self) -> Settings:
        """Get current global settings."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT copying_enabled, dry_run, shards FROM settings WHERE id = true"
            ).fetchone()
        if not row:
            raise RuntimeError("Settings row not found")
        return Settings(copying_enabled=row[0], dry_run=row[1], shards=row[2])

    def set_setting(self, name: str, value: Any) -> None:
        """Set a single setting value.

        Args:
            name: Setting name (copying_enabled, dry_run, shards)
            value: New value
        """
        # Map setting names to SQL column names
        columns = {
            "copying_enabled": "copying_enabled",
            "dry_run": "dry_run",
            "shards": "shards",
        }
        if name not in columns:
            raise ValueError(f"Unknown setting: {name}")

        column = columns[name]
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                f"UPDATE settings SET {column} = %s WHERE id = true",
                (value,),
            )

    # ---------- accounts ----------

    def load_accounts(self) -> list[AccountRow]:
        """Load all accounts."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            rows = conn.execute(
                """
                SELECT ctid_trader_account_id, ctid_connection_id, trader_login, is_live,
                       role, enabled, multiplier, status, last_error
                FROM accounts
                """
            ).fetchall()

        return [
            AccountRow(
                account_id=row[0],
                connection_id=row[1],
                trader_login=row[2],
                is_live=row[3],
                role=row[4],
                enabled=row[5],
                multiplier=row[6],  # Already a Decimal from psycopg
                status=row[7],
                last_error=row[8],
            )
            for row in rows
        ]

    def set_account_status(self, account_id: int, status: str, last_error: str | None = None) -> None:
        """Set account status and optional error message."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
        trader_login: int,
        is_live: bool,
    ) -> None:
        """Upsert an account."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ctid_trader_account_id) DO UPDATE SET
                    ctid_connection_id = EXCLUDED.ctid_connection_id,
                    trader_login = EXCLUDED.trader_login,
                    is_live = EXCLUDED.is_live
                """,
                (account_id, connection_id, trader_login, is_live),
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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

    # ---------- mappings ----------

    def create_position_mapping(
        self,
        master_position_id: int,
        slave_account_id: int,
        client_order_id: str,
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_position_id, slave_account_id, client_order_id, status)
                VALUES (%s, %s, %s, 'pending')
                ON CONFLICT (client_order_id) DO NOTHING
                """,
                (master_position_id, slave_account_id, client_order_id),
            )

    def activate_position_mapping(
        self,
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
        price for the slippage the Positions screen shows against the
        master's entry; a later increase's price would silently redefine it.

        Raises MappingNotFound if no mapping exists with this client_order_id.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET slave_position_id = %s,
                    slave_volume = COALESCE(slave_volume, 0) + %s,
                    fill_price = COALESCE(fill_price, %s),
                    status = 'active',
                    updated_at = now()
                WHERE client_order_id = %s
                """,
                (slave_position_id, slave_volume, fill_price, client_order_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(f"No mapping found with client_order_id={client_order_id}")

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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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

    def fail_mapping(self, client_order_id: str, error: str) -> None:
        """Mark a mapping as failed with an error message.

        Raises MappingNotFound if no mapping exists with this client_order_id.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET status = 'failed', error = %s, updated_at = now()
                WHERE client_order_id = %s
                """,
                (error, client_order_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(f"No mapping found with client_order_id={client_order_id}")

    def create_order_mapping(
        self,
        master_order_id: int,
        slave_account_id: int,
        client_order_id: str,
    ) -> None:
        """Create a pending order mapping."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_order_id, slave_account_id, client_order_id, status)
                VALUES (%s, %s, %s, 'pending')
                """,
                (master_order_id, slave_account_id, client_order_id),
            )

    def activate_order_mapping(self, client_order_id: str, slave_order_id: int) -> None:
        """Activate an order mapping.

        Raises MappingNotFound if no pending mapping exists with this client_order_id.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cursor = conn.execute(
                """
                UPDATE mappings
                SET slave_order_id = %s, status = 'active', updated_at = now()
                WHERE client_order_id = %s
                """,
                (slave_order_id, client_order_id),
            )
            if cursor.rowcount == 0:
                raise MappingNotFound(f"No mapping found with client_order_id={client_order_id}")

    def close_order_mapping(self, slave_account_id: int, slave_order_id: int) -> None:
        """Close an order mapping.

        Raises MappingNotFound if no active mapping exists with these identifiers.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
        can show a real fill price / slippage for a copy that came from a
        mirrored pending order, exactly as for a mirrored market open.

        Raises MappingNotFound if no active order mapping exists.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
    ) -> None:
        """Adopt an existing position (drift remedy)."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_position_id, slave_account_id, slave_position_id, slave_volume, status)
                VALUES (%s, %s, %s, %s, 'active')
                """,
                (master_position_id, slave_account_id, slave_position_id, slave_volume),
            )

    def mapping_rows(self) -> list[dict]:
        """Get all mapping rows as dictionaries."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            # Use column names
            conn.row_factory = psycopg.rows.dict_row
            rows = conn.execute(
                """
                SELECT id, master_position_id, master_order_id, slave_account_id,
                       slave_position_id, slave_order_id, slave_volume, client_order_id,
                       status, error, fill_price, created_at, updated_at
                FROM mappings
                """
            ).fetchall()

        return rows

    # ---------- MappingState protocol ----------

    def position_entries(self, master_position_id: int) -> Sequence[PositionMappingEntry]:
        """Get active position entries for a master position.

        Returns only active mappings with slave_position_id IS NOT NULL.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
