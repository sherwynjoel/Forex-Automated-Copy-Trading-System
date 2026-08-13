import psycopg
import pytest


def test_all_tables_exist(db):
    with psycopg.connect(db) as conn:
        for table in ["ctid_connections", "accounts", "symbol_cache", "mappings",
                      "events", "settings", "admin", "schema_migrations"]:
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row[0] is not None, f"missing table {table}"


def test_settings_has_single_seed_row(db):
    with psycopg.connect(db) as conn:
        row = conn.execute("SELECT copying_enabled, dry_run, shards FROM settings").fetchone()
        assert row == (True, False, 1)


def test_only_one_master_allowed(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at) "
                     "VALUES ('x', 'y', now(), now() + interval '30 days')")
        conn.execute("INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role) "
                     "VALUES (100, 1, 111, false, 'master')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role) "
                         "VALUES (101, 1, 112, false, 'master')")


def test_event_insert_emits_pg_notify(db):
    with psycopg.connect(db, autocommit=True) as listener:
        listener.execute("LISTEN events")
        with psycopg.connect(db, autocommit=True) as writer:
            writer.execute("INSERT INTO events (category, severity, payload) "
                           "VALUES ('control', 'info', '{\"msg\": \"hi\"}')")
        notification = next(listener.notifies(timeout=5))
        assert notification.channel == "events"
        assert notification.payload == "1"


def test_apply_migrations_is_idempotent(database):
    from db.migrate import apply_migrations
    assert apply_migrations(database) == []  # second run applies nothing
