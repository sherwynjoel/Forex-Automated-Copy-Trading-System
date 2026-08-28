"""AsyncWriter: the off-reactor batched write path.

These tests fake out `psycopg.connect` (patched on the writer module) so
they exercise batching, grouping, overflow, shutdown and reconnect
semantics without a database. AsyncWriter takes a DSN, exactly like `Repo`
(see the porting spec's D2) -- there is no pool to hand it a fake of, so
the fake instead stands in for the server on the other end of
`psycopg.connect`.
"""

import threading
import time
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.types.json import Jsonb

import copier.db.writer as writer_module
from copier.db.writer import AsyncWriter, build_statement


class FakeConn:
    """Executes against a shared FakeServer so a fail-count set on the
    server is spent across attempts/reconnects, not reset on every new
    connection.

    transaction() models a real BEGIN/COMMIT: statements run inside the
    block only become visible on the server when the block exits cleanly,
    so a fake batch rolls back exactly like a real one.
    """

    def __init__(self, server):
        self._server = server
        self._staged = None
        self.closed = False

    @contextmanager
    def transaction(self):
        self._staged = []
        try:
            yield self
        except BaseException:
            self._staged = None
            raise
        staged, self._staged = self._staged, None
        with self._server.lock:
            self._server.executed.extend(staged)

    def execute(self, sql, params=None):
        with self._server.lock:
            if self._server.connect_fail_times > 0:
                # Models the connection itself going bad (e.g. the server
                # restarted) rather than a bad statement.
                self._server.connect_fail_times -= 1
                error = psycopg.OperationalError
            elif self._server._fail_times > 0 or (
                self._server.fail_table is not None
                and f" {self._server.fail_table} " in sql
            ):
                if self._server._fail_times > 0:
                    self._server._fail_times -= 1
                error = RuntimeError
            else:
                error = None
        if error is not None:
            raise error("boom")
        if self._staged is not None:
            self._staged.append((sql, params))
            return
        with self._server.lock:
            self._server.executed.append((sql, params))

    def close(self):
        self.closed = True


class FakeServer:
    """Stands in for Postgres: records every committed statement and
    controls injected failures, and hands out FakeConns in place of
    `psycopg.connect`.

    connections_opened lets tests prove the writer actually reopened a new
    connection after a connection-level failure, rather than continuing to
    (mis)use the old one.
    """

    def __init__(self, fail_times=0, fail_table=None, connect_fail_times=0):
        self.executed = []
        self._fail_times = fail_times
        self.fail_table = fail_table
        self.connect_fail_times = connect_fail_times
        self.lock = threading.Lock()
        self.connections_opened = 0

    def connect(self, dsn, **kwargs):
        self.connections_opened += 1
        return FakeConn(self)


@pytest.fixture
def fake_server(monkeypatch):
    """Install a FakeServer as copier.db.writer's psycopg.connect and
    return a factory to build one with specific failure injection."""
    def _make(**kwargs) -> FakeServer:
        server = FakeServer(**kwargs)
        monkeypatch.setattr(writer_module.psycopg, "connect", server.connect)
        return server
    return _make


# ---------- build_statement (pure) ----------

def test_build_statement_appends_multi_row_values():
    sql = build_statement("events", ("category", "severity"), None, 2)
    assert sql == (
        "INSERT INTO events (category, severity) VALUES (%s, %s), (%s, %s)"
    )


def test_build_statement_upsert_sets_every_non_key_column():
    sql = build_statement(
        "positions", ("account_id", "position_id", "volume"),
        ("account_id", "position_id"), 1)
    assert sql == (
        "INSERT INTO positions (account_id, position_id, volume) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (account_id, position_id) DO UPDATE SET volume = EXCLUDED.volume"
    )


def test_build_statement_upsert_with_no_updatable_columns_does_nothing():
    sql = build_statement("t", ("a", "b"), ("a", "b"), 1)
    assert sql.endswith("ON CONFLICT (a, b) DO NOTHING")


# ---------- batching ----------

def _drain(server, expected_statements=1, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(server.executed) >= expected_statements:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"expected {expected_statements} statements, saw {len(server.executed)}")


def test_rows_for_one_table_batch_into_a_single_statement(fake_server):
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=200, batch_interval_s=0.02)
    w.start()
    try:
        for i in range(5):
            w.submit("events", {"category": "control", "severity": "info"})
        _drain(server, 1)
        sql, params = server.executed[0]
        assert sql.count("(%s, %s)") == 5
        assert len(params) == 10
    finally:
        w.flush_and_stop()


def test_different_tables_become_separate_statements(fake_server):
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=200, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "control"})
        w.submit("executions", {"account_id": 1})
        _drain(server, 2)
        tables = sorted(sql.split()[2] for sql, _ in server.executed)
        assert tables == ["events", "executions"]
    finally:
        w.flush_and_stop()


def test_batch_flushes_on_size_before_the_interval_elapses(fake_server):
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=2, batch_interval_s=10.0)
    w.start()
    try:
        w.submit("events", {"category": "a"})
        w.submit("events", {"category": "b"})
        _drain(server, 1, timeout=1.0)
    finally:
        w.flush_and_stop()


# ---------- overflow ----------

def test_submit_drops_instead_of_blocking_when_the_queue_is_full():
    """The reactor must NEVER block on a full queue -- losing an
    observability row beats delaying an order."""
    w = AsyncWriter("dsn", maxsize=2, batch_interval_s=10.0)
    # deliberately not started: nothing drains, so the queue fills
    assert w.submit("events", {"a": 1}) is True
    assert w.submit("events", {"a": 2}) is True
    assert w.submit("events", {"a": 3}) is False
    assert w.dropped == 1


# ---------- shutdown ----------

def test_flush_and_stop_writes_everything_still_queued(fake_server):
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=200, batch_interval_s=5.0)
    w.start()
    w.submit("events", {"category": "a"})
    w.flush_and_stop(timeout_s=2.0)
    assert len(server.executed) == 1


def test_flush_and_stop_closes_the_writers_connection(fake_server):
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=200, batch_interval_s=0.02)
    w.start()
    w.submit("events", {"category": "a"})
    _drain(server, 1)
    conn = w._conn
    assert conn is not None
    w.flush_and_stop(timeout_s=2.0)
    assert conn.closed is True
    assert w._conn is None


# ---------- error isolation ----------

def test_a_failing_batch_is_retried_once_then_dropped_and_marks_unhealthy(fake_server):
    fake_server(fail_times=2)
    w = AsyncWriter("dsn", batch_size=1, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "a"})
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and w.healthy:
            time.sleep(0.01)
        assert w.healthy is False
    finally:
        w.flush_and_stop()


def test_writer_thread_survives_a_failed_batch_and_keeps_draining(fake_server):
    server = fake_server(fail_times=2)
    w = AsyncWriter("dsn", batch_size=1, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "a"})
        time.sleep(0.2)
        w.submit("events", {"category": "b"})
        _drain(server, 1, timeout=2.0)
    finally:
        w.flush_and_stop()


# ---------- connection recovery ----------

def test_writer_reopens_its_connection_after_a_connection_level_failure(fake_server):
    """A connection-level failure (OperationalError/InterfaceError) must
    not brick the writer the way a data-level failure does: the writer must
    drop the bad connection, open a fresh one, and later rows must still
    land -- proving the thread survives something like a Postgres restart.
    """
    server = fake_server(connect_fail_times=1)
    w = AsyncWriter("dsn", batch_size=1, batch_interval_s=0.02)
    w.start()
    try:
        # First batch: attempt 1 opens connection #1 and hits the injected
        # connection failure; the writer must drop it and retry on a fresh
        # connection #2, which succeeds.
        w.submit("events", {"category": "a"})
        _drain(server, 1, timeout=2.0)
        assert w.healthy is True
        assert server.connections_opened >= 2, (
            "writer did not reopen its connection after a connection-level "
            "failure")

        # Second batch, reusing the now-good connection, must still land.
        w.submit("events", {"category": "b"})
        _drain(server, 2, timeout=2.0)
    finally:
        w.flush_and_stop()


def test_a_connection_level_failure_does_not_wedge_in_a_retry_loop(fake_server):
    """Even if the connection keeps failing to reconnect, the batch is
    dropped after 2 attempts -- never retried forever."""
    server = fake_server(connect_fail_times=999)
    w = AsyncWriter("dsn", batch_size=1, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "a"})
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and w.healthy:
            time.sleep(0.01)
        assert w.healthy is False
        assert server.executed == []
    finally:
        w.flush_and_stop()


# ---------- atomicity (C4) ----------

def test_a_failing_group_rolls_the_whole_batch_back_so_a_retry_cannot_double_write(fake_server):
    """C4: the connection is autocommit, so each conn.execute() used to
    commit on its own. If group 3 of 5 raised, groups 1 and 2 were already
    durable -- and _write_with_retry re-ran the WHOLE batch, inserting them a
    second time. `events` and `executions` are append-only with no natural
    key, so those duplicates could never be removed.

    Here the `positions` group always fails, so both attempts must leave
    NOTHING committed -- not two copies of the `events` group.
    """
    server = fake_server(fail_table="positions")
    w = AsyncWriter("dsn", batch_size=3, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "control", "severity": "info"})
        w.submit("events", {"category": "control", "severity": "warning"})
        w.submit("positions", {"account_id": 1, "position_id": 2},
                 upsert_key=("account_id", "position_id"))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and w.healthy:
            time.sleep(0.01)
        assert w.healthy is False
        assert server.executed == [], (
            f"a failed batch left {server.executed!r} committed; the retry "
            "would insert those append-only rows a second time")
    finally:
        w.flush_and_stop()


def test_a_successful_batch_still_commits_every_group(fake_server):
    """The transaction must not swallow the happy path."""
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=2, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "control"})
        w.submit("executions", {"account_id": 1})
        _drain(server, 2)
    finally:
        w.flush_and_stop()


# ---------- intra-batch de-duplication (C4, scenario B) ----------

def test_two_rows_with_the_same_conflict_key_collapse_to_one(fake_server):
    """Postgres raises SQLSTATE 21000 if one INSERT ... ON CONFLICT carries
    two rows with the same conflict target, which two resyncs of the same
    org inside one 50ms batch produce routinely. That killed the whole
    batch, unrelated tables included. Last write wins."""
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=2, batch_interval_s=0.02)
    w.start()
    try:
        key = ("account_id", "position_id")
        w.submit("positions",
                 {"account_id": 1, "position_id": 2, "volume": 100}, key)
        w.submit("positions",
                 {"account_id": 1, "position_id": 2, "volume": 200}, key)
        _drain(server, 1)
        sql, params = server.executed[0]
        assert sql.count("(%s, %s, %s)") == 1, (
            "both rows went into one ON CONFLICT statement; Postgres "
            "rejects that with 21000")
        assert params == [1, 2, 200], "the later row must win"
    finally:
        w.flush_and_stop()


def test_append_only_rows_are_never_de_duplicated(fake_server):
    """Only upsert groups collapse. Two identical audit-log lines are two
    real events."""
    server = fake_server()
    w = AsyncWriter("dsn", batch_size=2, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "control", "severity": "info"})
        w.submit("events", {"category": "control", "severity": "info"})
        _drain(server, 1)
        sql, _ = server.executed[0]
        assert sql.count("(%s, %s)") == 2
    finally:
        w.flush_and_stop()


# ---------- against real Postgres ----------

def test_a_failed_batch_leaves_no_partially_committed_rows_in_postgres(db):
    """The same property as the fake-server test, proved against the real
    database and the writer's own real, hardened connection."""
    w = AsyncWriter(db, batch_size=3, batch_interval_s=0.02)
    w.start()
    try:
        w.submit("events", {"category": "control", "severity": "info",
                            "payload": Jsonb({"a": 1})})
        w.submit("events", {"category": "control", "severity": "info",
                            "payload": Jsonb({"a": 2})})
        # positions.status has a CHECK (status IN ('open','closed')), so this
        # group always fails -- exactly like a mid-batch connection drop.
        w.submit("positions",
                 {"account_id": 100, "position_id": 1, "status": "bogus",
                  "volume": 10},
                 upsert_key=("account_id", "position_id"))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and w.healthy:
            time.sleep(0.01)
        assert w.healthy is False
    finally:
        w.flush_and_stop(timeout_s=2.0)

    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute("SELECT count(*) FROM events").fetchone()
    assert n == 0, (
        f"{n} events rows survived a failed batch; with two attempts that is "
        "the silent duplication C4 describes")
