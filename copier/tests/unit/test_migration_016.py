"""Migration 016: deals.label -- an MT5 copy's own "copy:m<master>" mark,
so the History page's fleet view can match it back to its master trade
(test_mt5_deals.py / test_repo_mt5.py cover the read/write behaviour)."""

import psycopg


def test_migration_016_is_recorded_right_after_015(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "016_deal_label.sql" in names
    assert names.index("016_deal_label.sql") == names.index("015_mt5_net_ledger.sql") + 1


def test_deals_has_a_nullable_label_column(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (nullable,) = conn.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'deals' AND column_name = 'label'").fetchone()
    assert nullable == "YES"


def test_existing_deal_rows_keep_a_null_label(db):
    """The column arrives on an existing table -- every row already there
    must not be rejected or forced into an empty string."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO deals (account_id, deal_id, execution_timestamp)"
            " VALUES (1, 1, 1000)")
        (label,) = conn.execute(
            "SELECT label FROM deals WHERE account_id = 1 AND deal_id = 1").fetchone()
        conn.execute("DELETE FROM deals WHERE account_id = 1 AND deal_id = 1")
    assert label is None
