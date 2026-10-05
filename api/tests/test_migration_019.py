# api/tests/test_migration_019.py
"""Migration 019: the investor portal -- investor role and the
account->investor link. conftest applies EVERY migration, so these assert
the post-migration shape. The three tables 019 also created
(org_investor_wallets, investor_deposits, investor_withdrawals) were
replaced and dropped by 022_client_wallets.sql; see test_migration_022.py.
The one-account-per-investor index it created is dropped by
024_multi_account.sql; see test_migration_024.py."""
import psycopg
import pytest


def test_migration_019_is_recorded_right_after_018(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "019_investor_portal.sql" in names
    assert names.index("019_investor_portal.sql") == names.index("018_risk_engine.sql") + 1


def test_investor_is_a_valid_membership_and_invite_role(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (role,) = conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
            (org_id, investor["id"])).fetchone()
        assert role == "investor"
        conn.execute(
            "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
            "VALUES (%s, 'investor', 'h019', %s, now() + interval '1 day')",
            (org_id, owner["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'guest')",
                (org_id, owner["id"]))
