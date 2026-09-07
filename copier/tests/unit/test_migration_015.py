"""Migration 015: the netting master's ledger table (mt5_net_ledger).

Schema-level assertions only -- the ledger's behaviour is covered by
test_mt5_ingress.py (NetLedger) and test_repo_mt5.py (the accessors)."""

import psycopg
import pytest


def _seed_mt5_account(conn):
    (org_id,) = conn.execute(
        "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    (account_id,) = conn.execute(
        "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
        " trader_login, is_live, role, platform)"
        " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'master', 'mt5')"
        " RETURNING ctid_trader_account_id", (org_id,)).fetchone()
    return account_id


def _row(account_id, virtual_id, **extra):
    row = {"account_id": account_id, "virtual_id": virtual_id, "symbol": "XAUUSD.r",
           "side": "BUY", "volume_open": 50, "volume_left": 50, "stop_loss": None,
           "take_profit": None, "opened_at_ms": 1_757_203_100_000}
    row.update(extra)
    return row


def _insert(conn, row):
    conn.execute(
        "INSERT INTO mt5_net_ledger (account_id, virtual_id, symbol, side, volume_open,"
        " volume_left, stop_loss, take_profit, opened_at_ms)"
        " VALUES (%(account_id)s, %(virtual_id)s, %(symbol)s, %(side)s, %(volume_open)s,"
        " %(volume_left)s, %(stop_loss)s, %(take_profit)s, %(opened_at_ms)s)", row)


def test_migration_015_is_recorded_right_after_014(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "015_mt5_net_ledger.sql" in names
    assert names.index("015_mt5_net_ledger.sql") == names.index("014_mt5_bridge.sql") + 1


def test_table_has_the_contracts_columns(db):
    with psycopg.connect(db, autocommit=True) as conn:
        columns = dict(conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'mt5_net_ledger'").fetchall())
    assert set(columns) == {"account_id", "virtual_id", "symbol", "side", "volume_open",
                            "volume_left", "stop_loss", "take_profit", "opened_at_ms"}
    assert {c for c, nullable in columns.items() if nullable == "YES"} == {
        "stop_loss", "take_profit"}


def test_one_row_per_virtual_position_per_account(db):
    with psycopg.connect(db, autocommit=True) as conn:
        account_id = _seed_mt5_account(conn)
        _insert(conn, _row(account_id, 700001))
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, _row(account_id, 700001))
        _insert(conn, _row(account_id, 700002, side="SELL"))
        (n,) = conn.execute("SELECT count(*) FROM mt5_net_ledger").fetchone()
    assert n == 2


def test_side_is_constrained(db):
    with psycopg.connect(db, autocommit=True) as conn:
        account_id = _seed_mt5_account(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, _row(account_id, 700001, side="LONG"))


def test_rows_cascade_from_accounts(db):
    with psycopg.connect(db, autocommit=True) as conn:
        account_id = _seed_mt5_account(conn)
        _insert(conn, _row(account_id, 700001))
        conn.execute("DELETE FROM accounts WHERE ctid_trader_account_id = %s", (account_id,))
        (n,) = conn.execute("SELECT count(*) FROM mt5_net_ledger").fetchone()
    assert n == 0


def test_the_symbol_index_exists(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'mt5_net_ledger'"
            " AND indexname = 'mt5_net_ledger_by_symbol'").fetchone()
    assert n == 1
