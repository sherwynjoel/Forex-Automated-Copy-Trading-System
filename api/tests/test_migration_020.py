# api/tests/test_migration_020.py
"""Migration 020: Owner, Admin and Trader collapse into one 'admin' role.
conftest applies EVERY migration, so the `db` tests assert the post-migration
shape; the upgrade path (a live database whose rows still say owner and
trader) is exercised on a scratch database stopped at 019."""
import pathlib

import psycopg
import pytest

from conftest import ADMIN_DSN

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
UPGRADE_DB = "copytrader_mig020"
UPGRADE_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{UPGRADE_DB}"


def test_migration_020_is_recorded_right_after_019(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "020_single_admin.sql" in names
    assert names.index("020_single_admin.sql") == names.index("019_investor_portal.sql") + 1


@pytest.mark.parametrize("role", ["owner", "trader"])
def test_retired_roles_are_rejected_by_both_tables(db, make_user, make_org, role):
    admin = make_user()
    other = make_user(email="other@example.com")
    org_id = make_org(members=[(admin, "admin")])
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
                (org_id, other["id"], role))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
                "VALUES (%s, %s, 'h020', %s, now() + interval '1 day')",
                (org_id, role, admin["id"]))


def test_the_three_roles_are_still_accepted(db, make_user, make_org):
    admin = make_user()
    viewer = make_user(email="v@example.com")
    investor = make_user(email="i@example.com")
    org_id = make_org(members=[(admin, "admin"), (viewer, "viewer"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        roles = sorted(r[0] for r in conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s", (org_id,)).fetchall())
        assert roles == ["admin", "investor", "viewer"]
        for role in ("admin", "viewer", "investor"):
            conn.execute(
                "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
                "VALUES (%s, %s, %s, %s, now() + interval '1 day')",
                (org_id, role, f"h020-{role}", admin["id"]))


def test_upgrade_rewrites_roles_and_revokes_open_trader_invites(database):
    """The live database has owner/admin/trader/viewer/investor rows and a
    few invites when 020 arrives. Owners and traders become admins, an
    unconsumed trader link is revoked (not upgraded), a consumed one is
    relabelled so it passes the new constraint, and nothing else moves."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {UPGRADE_DB}")
    try:
        with psycopg.connect(UPGRADE_DSN) as conn:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name.startswith("020_"):
                    continue
                conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES "
                "(1, 'o@x.com', 'h', 'O'), (2, 'a@x.com', 'h', 'A'), "
                "(3, 't@x.com', 'h', 'T'), (4, 'v@x.com', 'h', 'V'), "
                "(5, 'i@x.com', 'h', 'I')")
            conn.execute("INSERT INTO orgs (id, name) VALUES (1, 'Desk')")
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES "
                "(1, 1, 'owner'), (1, 2, 'admin'), (1, 3, 'trader'), "
                "(1, 4, 'viewer'), (1, 5, 'investor')")
            conn.execute(
                "INSERT INTO org_invites "
                "(org_id, role, token_hash, created_by, expires_at, consumed_at) VALUES "
                "(1, 'trader', 'open-trader', 1, now() + interval '1 day', NULL), "
                "(1, 'trader', 'used-trader', 1, now() + interval '1 day', now()), "
                "(1, 'viewer', 'open-viewer', 1, now() + interval '1 day', NULL)")
            conn.execute((MIGRATIONS_DIR / "020_single_admin.sql").read_text())
            conn.commit()

        with psycopg.connect(UPGRADE_DSN, autocommit=True) as conn:
            roles = dict(conn.execute(
                "SELECT user_id, role FROM org_memberships ORDER BY user_id").fetchall())
            assert roles == {1: "admin", 2: "admin", 3: "admin", 4: "viewer", 5: "investor"}
            invites = dict(conn.execute(
                "SELECT token_hash, role FROM org_invites").fetchall())
            assert invites == {"used-trader": "admin", "open-viewer": "viewer"}
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
