# api/tests/test_migration_025.py
"""Migration 025: notifications, support tickets, bonuses and settings.
conftest applies every migration, so these assert the post-migration shape
and the database rules the phase 4 routes lean on. 025 only creates tables;
no existing table changes."""
from decimal import Decimal

import psycopg
import pytest

TABLES = ("notifications", "notification_prefs", "user_settings", "ticket_subjects",
          "tickets", "ticket_messages", "bonus_rules", "bonuses")


@pytest.fixture
def people(db, make_user, make_org):
    admin = make_user(email="admin@example.com")
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(admin, "admin"), (investor, "investor")])
    return org_id, admin["id"], investor["id"]


def test_migration_025_is_recorded_right_after_024(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "025_portal_engagement.sql" in names
    assert names.index("025_portal_engagement.sql") == names.index("024_multi_account.sql") + 1


def test_the_eight_tables_exist(db):
    with psycopg.connect(db, autocommit=True) as conn:
        found = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)", (list(TABLES),)).fetchall()}
    assert found == set(TABLES)


def test_notification_rows_are_bounded_and_unread_is_indexed(db, people):
    org_id, _admin, uid = people
    insert = ("INSERT INTO notifications (org_id, user_id, topic, title, body, link) "
              "VALUES (%s, %s, %s, %s, %s, %s)")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(insert, (org_id, uid, "money", "T", "B", "/org/1/invest"))
        conn.execute(insert, (org_id, uid, "bonus", "T", "B", None))
        for topic, title, body, link in (("chat", "T", "B", None), ("money", "x" * 121, "B", None),
                                         ("money", "T", "x" * 501, None),
                                         ("money", "T", "B", "https://evil.example")):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(insert, (org_id, uid, topic, title, body, link))
        defs = dict(conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'notifications'").fetchall())
    assert "read_at IS NULL" in defs["notifications_unread"]
    assert "(org_id, user_id, created_at DESC, id DESC)" in defs["notifications_by_user"]


def test_prefs_default_on_and_the_theme_is_one_of_four(db, people):
    org_id, _admin, uid = people
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO notification_prefs (org_id, user_id) VALUES (%s, %s)",
                     (org_id, uid))
        assert conn.execute(
            "SELECT money, identity, support, bonus FROM notification_prefs WHERE user_id = %s",
            (uid,)).fetchone() == (True, True, True, True)
        conn.execute("INSERT INTO user_settings (user_id) VALUES (%s)", (uid,))
        assert conn.execute("SELECT theme FROM user_settings WHERE user_id = %s",
                            (uid,)).fetchone() == ("system",)
        for good in ("light", "dim", "dark", "system"):
            conn.execute("UPDATE user_settings SET theme = %s WHERE user_id = %s", (good, uid))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE user_settings SET theme = 'neon' WHERE user_id = %s", (uid,))


def test_ticket_rules(db, people):
    org_id, _admin, uid = people
    message = ("INSERT INTO ticket_messages (ticket_id, org_id, author_id, from_desk, body, "
               "file_ids) VALUES (%s, %s, %s, false, %s, %s::bigint[])")
    with psycopg.connect(db, autocommit=True) as conn:
        (subject_id,) = conn.execute(
            "INSERT INTO ticket_subjects (org_id, label) VALUES (%s, 'Deposits') RETURNING id",
            (org_id,)).fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO ticket_subjects (org_id, label) VALUES (%s, 'deposits')",
                         (org_id,))
        (ticket_id,) = conn.execute(
            "INSERT INTO tickets (org_id, user_id, subject_id, subject_label) "
            "VALUES (%s, %s, %s, 'Deposits') RETURNING id", (org_id, uid, subject_id)).fetchone()
        assert conn.execute("SELECT status, closed_at FROM tickets WHERE id = %s",
                            (ticket_id,)).fetchone() == ("new", None)
        with pytest.raises(psycopg.errors.CheckViolation):
            # closed needs closed_at, and closed_at needs closed
            conn.execute("UPDATE tickets SET status = 'closed' WHERE id = %s", (ticket_id,))
        conn.execute(message, (ticket_id, org_id, uid, "Hi", [1, 2, 3]))
        for body, files in (("", []), ("x" * 4001, []), ("Hi", [1, 2, 3, 4])):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(message, (ticket_id, org_id, uid, body, files))
        conn.execute("DELETE FROM ticket_subjects WHERE id = %s", (subject_id,))
        assert conn.execute("SELECT subject_id, subject_label FROM tickets WHERE id = %s",
                            (ticket_id,)).fetchone() == (None, "Deposits")


def test_bonus_rules_default_off_and_bonuses_pay_once(db, people):
    org_id, _admin, uid = people
    insert = ("INSERT INTO bonuses (org_id, user_id, source, source_id, amount) "
              "VALUES (%s, %s, %s, %s, %s)")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO bonus_rules (org_id) VALUES (%s)", (org_id,))
        assert conn.execute(
            "SELECT signup_enabled, signup_amount, kyc_enabled, kyc_amount, deposit_enabled, "
            "deposit_pct, deposit_cap FROM bonus_rules WHERE org_id = %s", (org_id,)).fetchone() == (
            False, Decimal("0.00"), False, Decimal("0.00"), False, Decimal("0.000"), None)
        for column, value in (("signup_amount", -1), ("deposit_pct", Decimal("100.001")),
                              ("deposit_cap", 0)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(f"UPDATE bonus_rules SET {column} = %s WHERE org_id = %s",
                             (value, org_id))
        conn.execute("UPDATE bonus_rules SET deposit_pct = 100 WHERE org_id = %s", (org_id,))
        conn.execute(insert, (org_id, uid, "signup", None, 50))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (org_id, uid, "signup", None, 50))
        conn.execute(insert, (org_id, uid, "deposit", 7, 10))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (org_id, uid, "deposit", 7, 10))
        conn.execute(insert, (org_id, uid, "manual", None, -5))
        conn.execute(insert, (org_id, uid, "manual", None, -5))   # manual rows repeat freely
        for source, source_id, amount in (("kyc", None, 0), ("kyc", None, -5),
                                          ("deposit", None, 5), ("bogus", None, 5)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(insert, (org_id, uid, source, source_id, amount))
