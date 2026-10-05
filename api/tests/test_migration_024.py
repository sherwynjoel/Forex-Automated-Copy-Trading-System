# api/tests/test_migration_024.py
"""Migration 024: several live accounts per investor. conftest applies
every migration, so these assert the post-migration shape. 024 drops one
index and adds one defaulted column; it rewrites no row, so the links and
settings rows that exist before it survive unchanged."""
import psycopg
import pytest


def test_migration_024_is_recorded_right_after_023(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "024_multi_account.sql" in names
    assert names.index("024_multi_account.sql") == names.index("023_portal_identity.sql") + 1


def test_the_unique_index_is_gone_and_a_plain_lookup_index_replaces_it(db):
    with psycopg.connect(db, autocommit=True) as conn:
        defs = dict(conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'accounts'").fetchall())
    assert "accounts_one_per_investor" not in defs
    lookup = defs["accounts_by_investor"]
    assert "UNIQUE" not in lookup and "(org_id, investor_user_id)" in lookup
    assert "investor_user_id IS NOT NULL" in lookup


def test_an_investor_may_own_two_accounts(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (cid,) = conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, "
            "granted_at, expires_at) VALUES (%s, 'e', 'e', now(), now() + interval '1 day') "
            "RETURNING id", (org_id,)).fetchone()
        for aid in (901, 902):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                "trader_login, is_live, role, investor_user_id) "
                "VALUES (%s, %s, %s, %s, false, 'slave', %s)",
                (aid, cid, org_id, aid, investor["id"]))
        owners = conn.execute(
            "SELECT ctid_trader_account_id, investor_user_id FROM accounts WHERE org_id = %s "
            "ORDER BY 1", (org_id,)).fetchall()
    assert owners == [(901, investor["id"]), (902, investor["id"])]


def test_max_live_accounts_defaults_to_5_and_stays_between_1_and_50(db, make_user, make_org):
    org_id = make_org(members=[(make_user(), "admin")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s)", (org_id,))
        assert conn.execute("SELECT max_live_accounts FROM portal_settings WHERE org_id = %s",
                            (org_id,)).fetchone() == (5,)
        for bad in (0, 51):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE portal_settings SET max_live_accounts = %s WHERE org_id = %s",
                             (bad, org_id))
        for good in (1, 50):
            conn.execute("UPDATE portal_settings SET max_live_accounts = %s WHERE org_id = %s",
                         (good, org_id))
