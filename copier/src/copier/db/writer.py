"""Off-reactor batched writer for rows nothing waits on.

Split by purpose (see docs/superpowers/specs/2026-08-26-trade-persistence-design.md):
mapping creation stays synchronous because a slave fill can return in ~100ms
and activate_position_mapping looks the row up by client_order_id -- a miss
raises MappingNotFound and degrades into a phantom drift warning. Everything
else (event logs, executions, position upserts, balance samples, backfilled
deals) is submitted here and written by one daemon thread, so the Twisted
reactor never blocks on observability data.

Overflow drops rather than blocks. Delaying an order to record a log line is
the wrong trade in a copy-trading hot path.

CONNECTION OWNERSHIP: there is no connection pool in this codebase (see the
porting spec's D1/D2). `Repo` holds exactly one cached, autocommit connection
leased through `Repo._connect()`, and that method is explicitly documented as
single-threaded: "the copier runs everything on the Twisted reactor thread,
so the one connection is never contended." This writer runs its batches on
its own daemon thread, and psycopg connections are not safe for concurrent
use -- handing the writer thread a connection that the reactor thread might
also be using at the same instant is a data-corruption bug waiting to
happen, not a performance one. So `AsyncWriter` never calls
`Repo._connect()`; it opens and owns exactly one connection of its own,
configured the same hardened way `Repo._open()` configures its (see
`_open()` below), used only by the writer thread. Net topology: one
connection on the reactor thread (Repo), one on the writer thread
(AsyncWriter), no sharing, no pool, no contention.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass

import psycopg

log = logging.getLogger(__name__)

DEFAULT_MAXSIZE = 10_000
DEFAULT_BATCH_SIZE = 200
DEFAULT_BATCH_INTERVAL_S = 0.05
DROP_LOG_INTERVAL_S = 10.0
# Upper bound on each blocking queue.get() while collecting a batch, so a
# stop request is noticed within one poll slice instead of waiting out the
# rest of batch_interval_s -- otherwise flush_and_stop() can return before
# a batch the thread already dequeued has actually been written.
STOP_POLL_S = 0.05


@dataclass(frozen=True)
class WriteOp:
    """One row bound for one table.

    upsert_key None means append-only; a tuple names the conflict target,
    and every non-key column is overwritten from EXCLUDED.
    """
    table: str
    row: dict
    upsert_key: tuple[str, ...] | None = None


def build_statement(
    table: str,
    columns: tuple[str, ...],
    upsert_key: tuple[str, ...] | None,
    n_rows: int,
) -> str:
    """Build one multi-row INSERT (optionally an upsert).

    Pure: no I/O, no state. Rows are grouped by (table, columns, upsert_key)
    before this is called, so every row in a batch shares a column list.
    """
    cols = ", ".join(columns)
    placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
    values = ", ".join([placeholders] * n_rows)
    sql = f"INSERT INTO {table} ({cols}) VALUES {values}"
    if upsert_key:
        target = ", ".join(upsert_key)
        updates = [c for c in columns if c not in upsert_key]
        if updates:
            setters = ", ".join(f"{c} = EXCLUDED.{c}" for c in updates)
            sql += f" ON CONFLICT ({target}) DO UPDATE SET {setters}"
        else:
            sql += f" ON CONFLICT ({target}) DO NOTHING"
    return sql


class AsyncWriter:
    """A bounded queue plus one daemon thread that batches inserts.

    Owns exactly one psycopg connection, opened lazily on first use and
    reopened whenever a connection-level failure closes it -- see
    `_open()` and `_get_conn()`.
    """

    def __init__(
        self,
        dsn: str,
        maxsize: int = DEFAULT_MAXSIZE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_interval_s: float = DEFAULT_BATCH_INTERVAL_S,
    ):
        self._dsn = dsn
        self._conn: psycopg.Connection | None = None
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._batch_size = batch_size
        self._batch_interval_s = batch_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dropped = 0
        self._last_drop_log = 0.0
        self._healthy = True

    # ---------- reactor-thread API ----------

    def submit(
        self, table: str, row: dict, upsert_key: tuple[str, ...] | None = None
    ) -> bool:
        """Queue a row. Returns False if it was dropped. Never blocks."""
        try:
            self._q.put_nowait(WriteOp(table, row, upsert_key))
            return True
        except queue.Full:
            self._dropped += 1
            now = time.monotonic()
            if now - self._last_drop_log > DROP_LOG_INTERVAL_S:
                self._last_drop_log = now
                log.warning(
                    "db writer queue full; dropped %d rows so far", self._dropped
                )
            return False

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def healthy(self) -> bool:
        return self._healthy

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="db-writer", daemon=True
        )
        self._thread.start()

    def flush_and_stop(self, timeout_s: float = 5.0) -> None:
        """Drain what is queued, then stop. Bounded so shutdown can't hang."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
        # Whatever the thread did not reach, write inline on the caller's
        # thread -- shutdown is not the hot path, and losing the tail of the
        # audit log on a clean restart would be gratuitous.
        remaining = self._drain_nowait()
        if remaining:
            try:
                self._write_batch(remaining)
            except Exception:
                log.exception("db writer: final flush failed; %d rows lost",
                              len(remaining))
        self._close_conn()

    # ---------- connection ownership ----------

    def _open(self) -> psycopg.Connection:
        """Open the writer's own connection, hardened exactly like
        `Repo._open()` -- autocommit, no server-side prepared statements,
        and TCP keepalives so a dead peer is discovered rather than
        blocking this thread for the kernel's retransmit timeout. See the
        module docstring for why this is a *separate* connection from
        Repo's rather than a shared one.
        """
        return psycopg.connect(
            self._dsn,
            autocommit=True,
            prepare_threshold=None,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )

    def _get_conn(self) -> psycopg.Connection:
        """Return the writer's connection, opening it on first use or after
        a previous connection-level failure closed it."""
        if self._conn is None:
            self._conn = self._open()
        return self._conn

    def _close_conn(self) -> None:
        """Drop the writer's connection, if any. Used both when a
        connection-level failure means it must be reopened, and on
        shutdown."""
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # ---------- writer thread ----------

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._collect_batch()
            if not batch:
                continue
            self._write_with_retry(batch)

    def _collect_batch(self) -> list[WriteOp]:
        """Accumulate up to batch_size rows or batch_interval_s, whichever
        comes first.

        Waits in short slices (capped at STOP_POLL_S) rather than one long
        block on the full remaining budget, so a stop request is noticed
        promptly even when batch_interval_s is large -- otherwise this can
        sit blocked in queue.get() past flush_and_stop's join timeout,
        holding an already-dequeued row that drain_nowait() can no longer
        see in the queue.
        """
        batch: list[WriteOp] = []
        deadline = time.monotonic() + self._batch_interval_s
        while len(batch) < self._batch_size:
            if self._stop.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            wait = min(remaining, STOP_POLL_S)
            try:
                batch.append(self._q.get(timeout=wait))
            except queue.Empty:
                continue
        return batch

    def _drain_nowait(self) -> list[WriteOp]:
        batch: list[WriteOp] = []
        while True:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                return batch

    def _write_with_retry(self, batch: list[WriteOp]) -> None:
        """Try the batch up to twice, then drop it and mark unhealthy.

        A connection-level failure (psycopg.OperationalError /
        InterfaceError) means the connection itself is bad: the writer
        drops it here so the next attempt -- within this retry, or on the
        next batch -- opens a fresh one via `_get_conn()`. Because every
        group in a batch commits inside one `conn.transaction()` (see
        `_write_batch`), nothing was made durable before the failure, so
        retrying is safe and cannot double-write.

        A data-level failure (a bad row, a constraint violation) is not
        fixed by a new connection -- the second attempt is expected to
        fail identically. That is fine: the loop is bounded at two
        attempts regardless of failure kind, so a poison row costs exactly
        one dropped batch, never a permanent retry loop.
        """
        for attempt in (1, 2):
            try:
                self._write_batch(batch)
                self._healthy = True
                return
            except (psycopg.OperationalError, psycopg.InterfaceError):
                log.exception(
                    "db writer: batch attempt %d failed (connection error); "
                    "reopening connection", attempt)
                self._close_conn()
            except Exception:
                log.exception("db writer: batch attempt %d failed", attempt)
        self._healthy = False
        log.error("db writer: dropping %d rows after 2 failed attempts",
                  len(batch))

    @staticmethod
    def _group(batch: list[WriteOp]) -> dict[tuple, list[dict]]:
        """Rows by (table, columns, upsert_key), de-duplicated within each
        upsert group by conflict key -- last write wins.

        The de-duplication is not an optimization. Postgres raises SQLSTATE
        21000 ("ON CONFLICT DO UPDATE command cannot affect row a second
        time") if ONE `INSERT ... ON CONFLICT` statement carries two rows
        with the same conflict target, which two resyncs of the same org
        landing inside one 50ms batch produce routinely. That error would
        fail the batch, fail the retry identically, and drop every
        unrelated table's rows with it.
        """
        groups: dict[tuple, list[dict] | dict[tuple, dict]] = {}
        for op in batch:
            columns = tuple(sorted(op.row.keys()))
            key = (op.table, columns, op.upsert_key)
            if op.upsert_key:
                bucket = groups.setdefault(key, {})
                bucket[tuple(op.row[c] for c in op.upsert_key)] = op.row
            else:
                groups.setdefault(key, []).append(op.row)
        return {
            key: list(rows.values()) if isinstance(rows, dict) else rows
            for key, rows in groups.items()
        }

    def _write_batch(self, batch: list[WriteOp]) -> None:
        groups = self._group(batch)
        conn = self._get_conn()
        # One explicit transaction around EVERY group in the batch. The
        # connection is autocommit (see `_open()`), so without this each
        # conn.execute() commits on its own: if group 3 of 5 raised,
        # groups 1 and 2 were already durable and _write_with_retry's
        # re-run of the WHOLE batch inserted them a second time. `events`
        # and `executions` are append-only (BIGSERIAL id, no natural key,
        # no upsert key), so those duplicates could never be de-duplicated
        # after the fact -- the WS feed, the audit log and every
        # latency_ms percentile would double-count forever.
        # conn.transaction() on an autocommit connection emits an explicit
        # BEGIN/COMMIT (the same pattern Repo.save_symbol_cache relies on),
        # which makes the retry genuinely idempotent.
        with conn.transaction():
            for (table, columns, upsert_key), rows in groups.items():
                sql = build_statement(table, columns, upsert_key, len(rows))
                params = [row[c] for row in rows for c in columns]
                conn.execute(sql, params)
