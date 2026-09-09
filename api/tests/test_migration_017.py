"""Migration 017: the VT HTF->LTF bridge -- vt_htf_snapshots (the latest HTF
permission per org+symbol) and org_webhooks.vt_ltf_timeframes (the LTF
timeframes a workspace currently allows to trade). conftest builds the
scratch database by applying EVERY migration, so these assert the
post-migration shape rather than the migration's own SQL."""
import psycopg
import pytest


def test_migration_017_is_recorded_right_after_016(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "017_vt_bridge.sql" in names
    assert names.index("017_vt_bridge.sql") == names.index("016_deal_label.sql") + 1


def test_vt_htf_snapshots_has_one_row_per_org_and_symbol(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, price, tf) "
            "VALUES (%s, 'XAUUSD', 'long', 4385.34, 4413.15, 4301.91, 4355.28, '60')",
            (org_id,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, price, tf) "
                "VALUES (%s, 'XAUUSD', 'short', 1, 2, 3, 4, '15')", (org_id,))


def test_vt_htf_snapshots_bias_is_checked(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, price, tf) "
                "VALUES (%s, 'XAUUSD', 'sideways', 1, 2, 3, 4, '60')", (org_id,))


def test_org_webhooks_vt_ltf_timeframes_defaults_to_empty(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_webhooks (org_id, hook_id, secret_hash, secret_created_at) "
            "VALUES (%s, 'hook_017test', 'h', now())", (org_id,))
        (tfs,) = conn.execute(
            "SELECT vt_ltf_timeframes FROM org_webhooks WHERE org_id = %s", (org_id,)).fetchone()
    assert tfs == []
