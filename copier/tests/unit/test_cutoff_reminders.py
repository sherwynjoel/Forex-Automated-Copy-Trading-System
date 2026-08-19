"""Tests for the admin-set account cutoff date and its reminder scanner.

The cutoff is a one-time, per-account DATE set by an org admin (migration
007). Two days before it, the copier's scanner logs a 'reminder' event --
which reaches the dashboard via the events feed/WS and Telegram via the
API's notifier -- once per distinct cutoff value: changing the date re-arms
the reminder, re-saving the same date does not.
"""
import datetime

import psycopg
import pytest

from copier.db.repo import Repo


# ---------- migration 007: schema ----------

def test_accounts_have_admin_set_cutoff_columns(db):
    """007 adds a nullable admin-set cutoff_date and the send-once stamp."""
    with psycopg.connect(db) as conn:
        rows = conn.execute(
            """SELECT column_name, data_type, is_nullable
               FROM information_schema.columns
               WHERE table_name = 'accounts'
                 AND column_name IN ('cutoff_date', 'cutoff_reminder_sent_for')"""
        ).fetchall()
    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert by_name.get("cutoff_date") == ("date", "YES")
    assert by_name.get("cutoff_reminder_sent_for") == ("date", "YES")


def test_reminder_is_an_allowed_event_category(db):
    """007 widens events.category for cutoff reminders."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO events (category, severity, payload) "
            "VALUES ('reminder', 'warning', '{\"action\": \"cutoff_approaching\"}')")


# ---------- repo: the due-reminder scan ----------

@pytest.fixture
def seeded(db):
    """One org + connection; returns (org_id, seed) where seed(...) inserts
    an account with an optional cutoff/sent-for date."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Desk') RETURNING id").fetchone()
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'a', 'r', now(), now() + interval '30 days')
               RETURNING id""",
            (org_id,)).fetchone()

    def seed(account_id, cutoff_date=None, sent_for=None, nickname=None):
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                       org_id, trader_login, is_live, cutoff_date,
                       cutoff_reminder_sent_for, nickname)
                   VALUES (%s, %s, %s, %s, false, %s, %s, %s)""",
                (account_id, connection_id, org_id, account_id,
                 cutoff_date, sent_for, nickname))
        return account_id

    return org_id, seed


def _today():
    return datetime.date.today()


def test_due_when_cutoff_within_two_days(db, seeded):
    """Accounts whose cutoff is at most `days_before` calendar days away are
    due; further-out cutoffs and cutoff-less accounts are not."""
    org_id, seed = seeded
    seed(100, cutoff_date=_today() + datetime.timedelta(days=1), nickname="near")
    seed(101, cutoff_date=_today() + datetime.timedelta(days=2), nickname="edge")
    seed(102, cutoff_date=_today() + datetime.timedelta(days=10))
    seed(103)  # no cutoff set

    due = Repo(db).accounts_due_cutoff_reminder(days_before=2)

    assert [d["account_id"] for d in due] == [100, 101]
    edge = due[1]
    assert edge["org_id"] == org_id
    assert edge["nickname"] == "edge"
    assert edge["trader_login"] == 101
    assert edge["cutoff_date"] == _today() + datetime.timedelta(days=2)
    assert edge["days_left"] == 2


def test_not_due_again_once_marked_sent(db, seeded):
    """mark_cutoff_reminder_sent stamps the cutoff value; the same value
    never reminds twice (idempotent across restarts)."""
    _, seed = seeded
    cutoff = _today() + datetime.timedelta(days=1)
    seed(100, cutoff_date=cutoff)
    repo = Repo(db)

    assert len(repo.accounts_due_cutoff_reminder(days_before=2)) == 1
    repo.mark_cutoff_reminder_sent(100, cutoff)
    assert repo.accounts_due_cutoff_reminder(days_before=2) == []


def test_changing_the_cutoff_rearms_the_reminder(db, seeded):
    """An admin moving the date makes the account due again for the NEW
    date, even though the old one was already reminded."""
    _, seed = seeded
    old = _today() - datetime.timedelta(days=20)
    seed(100, cutoff_date=_today() + datetime.timedelta(days=1), sent_for=old)

    due = Repo(db).accounts_due_cutoff_reminder(days_before=2)

    assert [d["account_id"] for d in due] == [100]


# ---------- the scanner: scan -> event -> stamp ----------

def test_scan_logs_one_reminder_event_then_goes_quiet(db, seeded):
    """The scan turns a due account into exactly one org-stamped 'reminder'
    event (the events INSERT is what fans out to the WS feed and Telegram),
    and the DB stamp keeps every later scan quiet."""
    from copier import main as copier_main

    org_id, seed = seeded
    cutoff = _today() + datetime.timedelta(days=2)
    seed(100, cutoff_date=cutoff, nickname="FTMO demo")
    repo = Repo(db)

    copier_main.check_cutoff_reminders(repo)

    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT severity, account_id, org_id, payload FROM events "
            "WHERE category = 'reminder'").fetchall()
    assert len(rows) == 1
    severity, account_id, event_org, payload = rows[0]
    assert severity == "warning"
    assert account_id == 100
    assert event_org == org_id
    assert payload["action"] == "cutoff_approaching"
    assert payload["cutoff_date"] == cutoff.isoformat()
    assert payload["days_left"] == 2
    assert payload["nickname"] == "FTMO demo"
    assert payload["trader_login"] == 100

    copier_main.check_cutoff_reminders(repo)
    with psycopg.connect(db, autocommit=True) as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM events WHERE category = 'reminder'").fetchone()
    assert count == 1


def test_scan_never_raises():
    """LoopingCall body: a failing Deferred stops the loop permanently, so
    the scan must swallow even a dead database."""
    from copier import main as copier_main

    class _BrokenRepo:
        def accounts_due_cutoff_reminder(self, days_before):
            raise RuntimeError("db down")

    copier_main.check_cutoff_reminders(_BrokenRepo())  # must not raise
