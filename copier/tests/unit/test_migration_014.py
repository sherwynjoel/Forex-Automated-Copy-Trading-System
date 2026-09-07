"""Migration 014: the MT5 bridge tables and the platform column on accounts.

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


def _seed_org_and_connection(conn):
    (org_id,) = conn.execute(
        "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    (connection_id,) = conn.execute(
        "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
        " granted_at, expires_at)"
        " VALUES (%s, 'x', 'y', now(), now() + interval '30 days') RETURNING id",
        (org_id,)).fetchone()
    return org_id, connection_id


def test_migration_014_is_recorded_right_after_013(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "014_mt5_bridge.sql" in names
    assert names.index("014_mt5_bridge.sql") == names.index("013_tradingview_webhook.sql") + 1


@pytest.mark.parametrize("table", [
    "mt5_links", "mt5_commands", "symbol_aliases", "mt5_deal_watermark",
])
def test_table_exists(db, table):
    assert _columns(db, table), f"{table} was not created"


def test_accounts_gained_platform_and_an_optional_ctrader_link(db):
    with psycopg.connect(db, autocommit=True) as conn:
        default, nullable = conn.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'accounts' AND column_name = 'platform'").fetchone()
        assert "ctrader" in default and nullable == "NO"
        (link_nullable,) = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'accounts' AND column_name = 'ctid_connection_id'").fetchone()
    assert link_nullable == "YES"


def test_mt5_account_ids_start_in_the_synthetic_range(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (first,) = conn.execute("SELECT nextval('mt5_account_id_seq')").fetchone()
    assert first >= 1_000_000_000_000


def test_platform_link_check_accepts_ctrader_with_link_and_mt5_without(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, connection_id = _seed_org_and_connection(conn)
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (100, %s, %s, 111, false, 'slave', 'ctrader')",
            (org_id, connection_id))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'slave', 'mt5')",
            (org_id,))
        (n,) = conn.execute("SELECT count(*) FROM accounts").fetchone()
    assert n == 2


def test_platform_link_check_rejects_the_other_two_combinations(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, connection_id = _seed_org_and_connection(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
                " trader_login, is_live, role, platform)"
                " VALUES (101, %s, NULL, 111, false, 'slave', 'ctrader')",
                (org_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
                " trader_login, is_live, role, platform)"
                " VALUES (102, %s, %s, 111, false, 'slave', 'mt5')",
                (org_id, connection_id))


def test_existing_rows_default_to_ctrader(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, connection_id = _seed_org_and_connection(conn)
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role) VALUES (103, %s, %s, 111, false, 'slave')",
            (org_id, connection_id))
        (platform,) = conn.execute(
            "SELECT platform FROM accounts WHERE ctid_trader_account_id = 103").fetchone()
    assert platform == "ctrader"


def test_mt5_tables_cascade_from_accounts(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, _ = _seed_org_and_connection(conn)
        (account_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'slave', 'mt5')"
            " RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, 'h1')",
                     (account_id,))
        conn.execute(
            "INSERT INTO mt5_commands (account_id, org_id, kind, payload)"
            " VALUES (%s, %s, 'close', '{\"position\": 1}'::jsonb)", (account_id, org_id))
        conn.execute(
            "INSERT INTO symbol_aliases (account_id, canonical, broker_name, source)"
            " VALUES (%s, 'XAUUSD', 'XAUUSD.r', 'auto')", (account_id,))
        conn.execute("INSERT INTO mt5_deal_watermark (account_id) VALUES (%s)", (account_id,))
        conn.execute("DELETE FROM accounts WHERE ctid_trader_account_id = %s", (account_id,))
        counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("mt5_links", "mt5_commands", "symbol_aliases", "mt5_deal_watermark")]
    assert counts == [0, 0, 0, 0]


def test_command_kind_and_status_are_constrained(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, _ = _seed_org_and_connection(conn)
        (account_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'slave', 'mt5')"
            " RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO mt5_commands (account_id, org_id, kind, payload)"
                " VALUES (%s, %s, 'teleport', '{}'::jsonb)", (account_id, org_id))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO mt5_commands (account_id, org_id, kind, payload, status)"
                " VALUES (%s, %s, 'close', '{}'::jsonb, 'lost')", (account_id, org_id))
