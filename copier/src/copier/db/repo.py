"""Repository layer for mappings, events, settings, accounts, and symbol cache."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

import psycopg
from psycopg.types.json import Jsonb

from copier.domain.models import SymbolInfo, PositionMappingEntry, OrderMappingEntry


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
        if name not in ("copying_enabled", "dry_run", "shards"):
            raise ValueError(f"Unknown setting: {name}")

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                f"UPDATE settings SET {name} = %s WHERE id = true",
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
        """Save symbol cache for account."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
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
        """Create a pending position mapping."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO mappings (master_position_id, slave_account_id, client_order_id, status)
                VALUES (%s, %s, %s, 'pending')
                """,
                (master_position_id, slave_account_id, client_order_id),
            )

    def activate_position_mapping(
        self,
        client_order_id: str,
        slave_position_id: int,
        slave_volume: int,
    ) -> None:
        """Activate a position mapping."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE mappings
                SET slave_position_id = %s, slave_volume = %s, status = 'active', updated_at = now()
                WHERE client_order_id = %s
                """,
                (slave_position_id, slave_volume, client_order_id),
            )

    def reduce_position_mapping(
        self,
        slave_account_id: int,
        slave_position_id: int,
        closed_volume: int,
    ) -> None:
        """Reduce a position mapping by the closed volume.

        Sets status to 'closed' when slave_volume reaches 0.
        """
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            # Get current slave_volume
            row = conn.execute(
                """
                SELECT id, slave_volume FROM mappings
                WHERE slave_account_id = %s AND slave_position_id = %s AND status = 'active'
                """,
                (slave_account_id, slave_position_id),
            ).fetchone()

            if not row:
                return

            mapping_id, current_volume = row
            new_volume = current_volume - closed_volume

            if new_volume <= 0:
                # Close the mapping
                conn.execute(
                    """
                    UPDATE mappings
                    SET slave_volume = 0, status = 'closed', updated_at = now()
                    WHERE id = %s
                    """,
                    (mapping_id,),
                )
            else:
                # Update volume
                conn.execute(
                    """
                    UPDATE mappings
                    SET slave_volume = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (new_volume, mapping_id),
                )

    def fail_mapping(self, client_order_id: str, error: str) -> None:
        """Mark a mapping as failed with an error message."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE mappings
                SET status = 'failed', error = %s, updated_at = now()
                WHERE client_order_id = %s
                """,
                (error, client_order_id),
            )

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
        """Activate an order mapping."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE mappings
                SET slave_order_id = %s, status = 'active', updated_at = now()
                WHERE client_order_id = %s
                """,
                (slave_order_id, client_order_id),
            )

    def close_order_mapping(self, slave_account_id: int, slave_order_id: int) -> None:
        """Close an order mapping."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE mappings
                SET status = 'closed', updated_at = now()
                WHERE slave_account_id = %s AND slave_order_id = %s AND status = 'active'
                """,
                (slave_account_id, slave_order_id),
            )

    def link_pending_fill(
        self,
        master_order_id: int,
        slave_account_id: int,
        master_position_id: int,
    ) -> None:
        """Link pending fill by stamping master_position_id onto the order mapping row."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE mappings
                SET master_position_id = %s, updated_at = now()
                WHERE master_order_id = %s AND slave_account_id = %s
                """,
                (master_position_id, master_order_id, slave_account_id),
            )

    def activate_pending_fill(
        self,
        slave_account_id: int,
        slave_order_id: int,
        slave_position_id: int,
        slave_volume: int,
    ) -> None:
        """Activate the pending fill by converting to position mapping."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE mappings
                SET slave_position_id = %s, slave_volume = %s, status = 'active', updated_at = now()
                WHERE slave_account_id = %s AND slave_order_id = %s AND status = 'active'
                """,
                (slave_position_id, slave_volume, slave_account_id, slave_order_id),
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
                       status, error, created_at, updated_at
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
