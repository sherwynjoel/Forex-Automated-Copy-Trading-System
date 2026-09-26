# api/tests/test_migration_021.py
"""Migration 021: MPIN state on users. conftest applies EVERY migration, so
these assert the post-migration shape."""
import psycopg


def test_migration_021_is_recorded_right_after_020(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "021_mpin.sql" in names
    assert names.index("021_mpin.sql") == names.index("020_single_admin.sql") + 1


def test_users_gain_the_four_mpin_columns_with_safe_defaults(db, make_user):
    user = make_user(mpin=None)
    with psycopg.connect(db, autocommit=True) as conn:
        cols = dict(conn.execute(
            """SELECT column_name, is_nullable FROM information_schema.columns
               WHERE table_name = 'users' AND column_name LIKE 'mpin_%'""").fetchall())
        assert cols == {"mpin_hash": "YES", "mpin_failed_attempts": "NO",
                        "mpin_locked_until": "YES", "mpin_set_at": "YES"}
        row = conn.execute(
            "SELECT mpin_hash, mpin_failed_attempts, mpin_locked_until, mpin_set_at "
            "FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row == (None, 0, None, None)
