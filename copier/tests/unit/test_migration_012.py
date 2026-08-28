"""Migration 011: the trade-record tables.

Schema-level assertions only -- behaviour is covered by the tasks that
write to these tables."""

import psycopg
import pytest


def _columns(db, table):
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s", (table,)).fetchall()
    return {r[0] for r in rows}


@pytest.mark.parametrize("table", [
    "executions", "positions", "deals", "deal_backfill_state", "balance_samples",
])
def test_table_exists(db, table):
    assert _columns(db, table), f"{table} was not created"


def test_executions_carries_the_master_trade_economics(db):
    cols = _columns(db, "executions")
    for required in ("execution_price", "volume", "side", "symbol",
                     "execution_timestamp", "bid_at_exec", "ask_at_exec",
                     "is_master", "raw"):
        assert required in cols, f"executions.{required} missing"


def test_executions_is_partitioned_by_range(db):
    with psycopg.connect(db, autocommit=True) as conn:
        strategy = conn.execute(
            "SELECT partstrat FROM pg_partitioned_table p "
            "JOIN pg_class c ON c.oid = p.partrelid WHERE c.relname = 'executions'"
        ).fetchone()
    assert strategy is not None and strategy[0] == 'r'


def test_executions_has_partitions_covering_at_least_a_year_ahead(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhparent WHERE c.relname = 'executions'"
        ).fetchone()
    # 12 monthly partitions + 1 DEFAULT
    assert n >= 13


def test_an_execution_row_lands_in_a_monthly_partition_not_the_default(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO executions (account_id, is_master, execution_type, raw) "
            "VALUES (100, true, 'ORDER_FILLED', '{}'::jsonb)")
        (relname,) = conn.execute(
            "SELECT tableoid::regclass::text FROM executions LIMIT 1").fetchone()
    assert not relname.endswith("_default"), (
        f"row landed in {relname}: monthly partitions are missing")


def test_mappings_gained_the_master_fill_columns(db):
    cols = _columns(db, "mappings")
    assert "master_fill_price" in cols
    assert "master_exec_timestamp" in cols


def test_deals_is_keyed_for_idempotent_backfill(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO deals (account_id, deal_id, execution_timestamp) "
            "VALUES (100, 555, 1700000000000)")
        conn.execute(
            "INSERT INTO deals (account_id, deal_id, execution_timestamp) "
            "VALUES (100, 555, 1700000000000) "
            "ON CONFLICT (account_id, deal_id) DO NOTHING")
        (n,) = conn.execute("SELECT count(*) FROM deals").fetchone()
    assert n == 1
