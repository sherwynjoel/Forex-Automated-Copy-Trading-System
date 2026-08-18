"""Migration 005: multi-org schema. Runs against the session-scoped test DB
(which conftest builds by applying ALL migrations to a fresh database), so
these tests assert the post-migration shape. The legacy-backfill path is
exercised separately by building a scratch DB stopped at 004."""
import pathlib

import psycopg
import pytest

from conftest import ADMIN_DSN

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
BACKFILL_DB = "copytrader_mig005"
BACKFILL_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{BACKFILL_DB}"
UPGRADE_DB = "copytrader_mig006"
UPGRADE_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{UPGRADE_DB}"


def test_new_tables_exist(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
    assert {"users", "orgs", "org_memberships", "org_invites"} <= names
    assert "admin" not in names


def test_org_id_columns_and_master_index(db):
    with psycopg.connect(db, autocommit=True) as conn:
        cols = {
            (r[0], r[1]) for r in conn.execute(
                """SELECT table_name, column_name FROM information_schema.columns
                   WHERE column_name = 'org_id'"""
            )
        }
        # portfolio_snapshots gains org_id in 006, on top of 005's set.
        assert {"ctid_connections", "accounts", "mappings", "events",
                "oauth_states", "portfolio_snapshots"} <= {t for t, _ in cols}
        # settings keeps only process config
        settings_cols = {
            r[0] for r in conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'settings'"""
            )
        }
        assert settings_cols == {"id", "shards"}
        # the master-uniqueness index is per-org now
        (indexdef,) = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'accounts_single_master'"
        ).fetchone()
        assert "(org_id)" in indexdef and "role = 'master'" in indexdef


def test_two_masters_allowed_across_orgs_not_within(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_a = conn.execute(
            "INSERT INTO orgs (name) VALUES ('A') RETURNING id").fetchone()[0]
        org_b = conn.execute(
            "INSERT INTO orgs (name) VALUES ('B') RETURNING id").fetchone()[0]
        conn_a = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days') RETURNING id""",
            (org_a,)).fetchone()[0]
        conn_b = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days') RETURNING id""",
            (org_b,)).fetchone()[0]

        def add_account(org, connection, acc_id, role):
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                         org_id, trader_login, is_live, role)
                   VALUES (%s, %s, %s, %s, false, %s)""",
                (acc_id, connection, org, acc_id, role))

        add_account(org_a, conn_a, 100, "master")
        add_account(org_b, conn_b, 200, "master")  # second master, other org: OK
        with pytest.raises(psycopg.errors.UniqueViolation):
            add_account(org_a, conn_a, 101, "master")  # second master, same org


def test_legacy_backfill_creates_default_org(database):
    """Apply 001–004 to a scratch DB, seed legacy single-tenant data, then
    apply 005 and assert everything landed in a 'Default' org."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {BACKFILL_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {BACKFILL_DB}")
    try:
        with psycopg.connect(BACKFILL_DSN) as conn:
            for name in ("001_initial.sql", "002_oauth_states.sql",
                         "003_mapping_fill_price.sql", "004_account_nickname.sql"):
                conn.execute((MIGRATIONS_DIR / name).read_text())
            conn.execute(
                """INSERT INTO ctid_connections
                   (access_token_enc, refresh_token_enc, granted_at, expires_at)
                   VALUES ('x', 'x', now(), now() + interval '30 days')""")
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                         trader_login, is_live, role)
                   VALUES (100, 1, 100, false, 'master')""")
            conn.execute("UPDATE settings SET dry_run = true")
            conn.execute((MIGRATIONS_DIR / "005_multi_org.sql").read_text())
            conn.commit()
        with psycopg.connect(BACKFILL_DSN, autocommit=True) as conn:
            org = conn.execute(
                "SELECT id, name, copying_enabled, dry_run FROM orgs").fetchone()
            assert org[1] == "Default"
            assert org[2] is True and org[3] is True  # copied from old settings
            (acc_org,) = conn.execute(
                "SELECT org_id FROM accounts WHERE ctid_trader_account_id = 100"
            ).fetchone()
            assert acc_org == org[0]
            (conn_org,) = conn.execute(
                "SELECT org_id FROM ctid_connections").fetchone()
            assert conn_org == org[0]
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {BACKFILL_DB} WITH (FORCE)")


def test_fresh_db_has_no_orgs(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM orgs").fetchone()
    assert count == 0


def test_live_upgrade_path_backfills_snapshot_orgs(database):
    """The REAL deployment's ordering: the single-tenant analytics batch's
    005_risk_snapshots_symbol.sql already ran (portfolio_snapshots exists
    and has rows, mappings has `symbol`, events allows 'risk'), and the
    multi-org migrations arrive afterwards.

    The migration runner tracks by filename, so 005_multi_org.sql and
    006_snapshot_org.sql apply on top of that -- and 006 has to hand every
    existing snapshot row to the org its account landed in, or the first
    Overview after the upgrade shows an empty portfolio history.
    """
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {UPGRADE_DB}")
    try:
        with psycopg.connect(UPGRADE_DSN) as conn:
            for name in ("001_initial.sql", "002_oauth_states.sql",
                         "003_mapping_fill_price.sql", "004_account_nickname.sql",
                         # what the live DB has already applied:
                         "005_risk_snapshots_symbol.sql"):
                conn.execute((MIGRATIONS_DIR / name).read_text())
            conn.execute(
                """INSERT INTO ctid_connections
                   (access_token_enc, refresh_token_enc, granted_at, expires_at)
                   VALUES ('x', 'x', now(), now() + interval '30 days')""")
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                         trader_login, is_live, role)
                   VALUES (100, 1, 100, false, 'master')""")
            # One snapshot for a live account, one for an account that has
            # since been disconnected (no accounts row to join to).
            conn.execute(
                """INSERT INTO portfolio_snapshots
                       (snapshot_date, account_id, balance, equity)
                   VALUES (CURRENT_DATE - 1, 100, 1000, 1010),
                          (CURRENT_DATE - 1, 999, 50, 50)""")
            conn.execute((MIGRATIONS_DIR / "005_multi_org.sql").read_text())
            conn.execute((MIGRATIONS_DIR / "006_snapshot_org.sql").read_text())
            conn.commit()

        with psycopg.connect(UPGRADE_DSN, autocommit=True) as conn:
            (default_org,) = conn.execute("SELECT id FROM orgs").fetchone()
            rows = dict(conn.execute(
                "SELECT account_id, org_id FROM portfolio_snapshots").fetchall())
            assert rows[100] == default_org        # backfilled from its account
            assert rows[999] is None               # orphan history, no org

            # 005_multi_org must not have narrowed the category check the
            # risk migration widened.
            conn.execute(
                "INSERT INTO events (category, severity, payload) "
                "VALUES ('risk', 'error', '{\"action\": \"margin_call\"}')")
            # ...nor dropped mappings.symbol.
            conn.execute("SELECT symbol FROM mappings")
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
