import psycopg
import pytest


def test_all_tables_exist(db):
    with psycopg.connect(db) as conn:
        for table in ["ctid_connections", "accounts", "symbol_cache", "mappings",
                      "events", "settings", "schema_migrations",
                      # multi-org (migration 005): the single-row `admin`
                      # table is gone, replaced by real users and orgs.
                      "users", "orgs", "org_memberships", "org_invites"]:
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row[0] is not None, f"missing table {table}"
        assert conn.execute("SELECT to_regclass('admin')").fetchone()[0] is None


def test_settings_has_single_seed_row(db):
    """settings is process config only now -- the kill switch and dry-run
    moved onto orgs (migration 005), where they are per tenant."""
    with psycopg.connect(db) as conn:
        row = conn.execute("SELECT shards FROM settings").fetchone()
        assert row == (1,)
        with pytest.raises(psycopg.errors.UndefinedColumn):
            conn.execute("SELECT copying_enabled, dry_run FROM settings")


def test_org_defaults_are_copying_enabled_and_no_dry_run(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Fresh Org') RETURNING id").fetchone()
        row = conn.execute(
            "SELECT copying_enabled, dry_run FROM orgs WHERE id = %s", (org_id,)).fetchone()
        assert row == (True, False)


def test_only_one_master_allowed_per_org(db):
    """The single-master uniqueness index is now scoped to the org: two orgs
    each have their own master, but neither may have two."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_a,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Org A') RETURNING id").fetchone()
        (org_b,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Org B') RETURNING id").fetchone()
        conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
            " granted_at, expires_at)"
            " VALUES (%s, 'x', 'y', now(), now() + interval '30 days'),"
            "        (%s, 'x', 'y', now(), now() + interval '30 days')",
            (org_a, org_b))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role) VALUES (100, %s, 1, 111, false, 'master')",
            (org_a,))
        # A second master in ANOTHER org is fine...
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role) VALUES (200, %s, 2, 222, false, 'master')",
            (org_b,))
        # ... a second master in the SAME org is not.
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
                " trader_login, is_live, role) VALUES (101, %s, 1, 112, false, 'master')",
                (org_a,))


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
