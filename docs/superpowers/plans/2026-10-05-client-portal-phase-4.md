# Client Portal Phase 4 (Notifications, Support Tickets, Bonus, Settings) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the broker-style portal: an in-app notification centre with a bell, support tickets between investors and the desk, bonuses paid into the Credit wallet by admin rules or by hand (and movable only to a trading account), and a Settings page for appearance and email preferences.

**Architecture:** Migration 025 adds eight tables. `portal_common.notify()` writes a `notifications` row and emails it unless the user's per-topic switch is off; it replaces `notify_investor` at all nine callers. Three new routers: `routes/portal_notifications.py` (notifications, `/api/me/settings`, notification prefs), `routes/portal_support.py` (ticket subjects, investor tickets, the desk queue) and `routes/portal_bonus.py` (bonus rules, manual grants, the investor's bonus history). `pc.pay_bonus` inserts a `bonuses` row (unique indexes make each rule pay once) and settles a `credit` ledger row; the signup, KYC and deposit triggers call it, and `TRANSFER_PAIRS` gains `("credit", "account")`. The dashboard adds a bell in the shell, Notifications and Settings pages (desk and investor), investor Support and Bonus pages, a Support tab in the Requests desk, Ticket subjects and Bonus rules cards under Investors → Portal settings, a Grant bonus dialog and the Credit option on Transfer.

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 (autocommit) / pytest against real Postgres 16; React 18 / TypeScript strict / react-router 7 / Tailwind 4 tokens / vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-05-client-portal-phase-4-design.md` (read it first; it is the authority). **Interfaces:** `docs/superpowers/plans/2026-10-05-client-portal-phase-4-interfaces.md` (every name, route, refusal string and aria-label; binding). **Spec decisions:** the last section of this plan lists every place the spec was silent, ambiguous or wrong against the code and what this plan does there; read it before Task 1.

## Global Constraints

- Branch `client-portal-phase-4` in the worktree `.worktrees/phase4` (created from `main` at `8f9f388`, spec commit `10bbe98`). Never `cd` to the main checkout, never `git stash`. Commit on the branch after every task; create no other branches.
- Every commit message ends with these two lines:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN
  ```

  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`.
- `copier/` is never touched.
- API tests need Postgres answering on `127.0.0.1:5433` (if `docker ps` hangs, probe the port instead; do not restart Docker). The worktree has no virtualenv: use the main checkout's. Once per shell, Git Bash from the worktree's `api/`:

  ```bash
  cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/.worktrees/phase4/api"
  export POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' ../../../.env | cut -d= -f2-)"
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test_p4"
  export PYTHONPATH="$(pwd -W)/src"
  PY="/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/api/.venv/Scripts/python.exe"
  ```

  then `"$PY" -m pytest <files> -q -p no:cacheprovider`. The `_p4` database name keeps this run from dropping another agent's scratch database (conftest drops and recreates the database the DSN names). Use `127.0.0.1`, never `localhost` (IPv6 hang). Seven `test_events_ws.py` errors and one EA-download CRLF failure are pre-existing on Windows; do not fix them.
- Dashboard: the worktree has no `node_modules`; run `npm ci` once in `dashboard/`. One file: `npx vitest run <path>`. Gate, from `dashboard/`: `npm test` (palette prover, `tsc --noEmit -p tsconfig.app.json`, vitest; if this machine runs out of workers use `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`) and `npm run build`. There is no lint script. The vitest output must contain no `act(...)` warning.
- Investor routes take `Depends(require_investor)`; admin routes `Depends(require_org_role("admin"))`; the notification and preference routes take `require_org_role("investor")` (the lowest rank, so every member passes) and only ever touch `ctx.user_id`'s rows. Every new org route gets a row in `api/tests/test_rbac_matrix.py` (Task 12).
- Every mutation audits one `events` row through `portal_common.audit_control`; investor-scoped actions carry `payload.user_id` = the investor. Ticket audits (`ticket_opened`, `ticket_replied`, `ticket_closed`) never carry message text. Every transaction that writes `wallet_entries` (every `pc.pay_bonus` call) takes `pc.lock_investor_ledger` as its FIRST statement and awaits nothing while it is held; `notify`/`announce_bonus` run after the transaction.
- An investor route that names a ticket answers 404 `Ticket not found` for another investor's or an unknown id, never a hint that it exists. A notification route answers 404 `Notification not found` the same way.
- Dashboard rules: colour only through `--color-*` tokens and the primitives (Button, Input, Select, Badge, Banner, Card, Tabs, Drawer, ConfirmDialog, PageHeader, Loading, PinConfirmDialog, FileInput, Menu); data never sits directly on `.glass` (a Card with `inset`, or an `inset` div inside a glass popover); tables are `stack-table` with `data-label` on every data `td`; the one `h1` comes from `PageHeader`; the words "Slave"/"slave" never appear in copy; never a literal "Loading..." (use `Loading`); prefer class names that already appear in `src/` and put a one-off size in an inline `style` (as `Menu` does), so `scripts/palette_check.mjs` needs no new row; `tsc` is strict with `noUnusedLocals`. A page that refetches when a key changes (org, filter, ticket id) guards with a `seq` ref and drops any answer that is not the newest, as `InvestorAccount.tsx` does. No aria-label may equal the text of a Card heading on the same screen. Tests use `mockUseOrg` from `src/test/orgMock.tsx`, fixtures from `src/test/portalFixtures.ts`, and stub `fetch` by URL.
- Ponytail: reuse the helpers named here, add nothing speculative, mark a deliberate shortcut with a `ponytail:` comment naming its ceiling.
- Deploy is not part of this plan; Task 20 writes the runbook lines.

---

## File map

| File | Change |
|---|---|
| `db/migrations/025_portal_engagement.sql` (new), `api/tests/test_migration_025.py` (new), `api/tests/conftest.py` | eight tables; TRUNCATE list |
| `api/src/api/portal_common.py`, `api/tests/test_portal_common.py` | `notify`, `notify_admins`, `email_wanted`, `clip`, `investor_link`; bonus helpers; `credit_funded`, `account_movable`; `notify_investor` removed |
| `api/src/api/portal_ledger.py`, `api/tests/test_portal_ledger.py` | `deposit_bonus`; `("credit", "account")` |
| `api/src/api/routes/portal_admin.py` | notify calls, deposit bonus, `parse_signed_amount`, summary `tickets`, transfer decision re-check of bonus credit |
| `api/src/api/routes/portal_investor.py` | account -> wallet cap and summary `account_available` exclude bonus credit |
| `api/src/api/routes/portal_identity.py` | notify calls, KYC bonus |
| `api/src/api/routes/orgs.py` | signup bonus on join and role change |
| `api/src/api/routes/portal_notifications.py` (new), `api/tests/test_portal_notifications.py` (new), `api/tests/test_portal_settings.py` (new) | notifications, `/api/me/settings`, prefs |
| `api/src/api/routes/portal_support.py` (new), `api/tests/test_portal_tickets.py` (new) | subjects, tickets |
| `api/src/api/routes/portal_bonus.py` (new), `api/tests/test_portal_bonus.py` (new) | rules, triggers, grants, history |
| `api/src/api/uploads.py`, `routes/portal_files.py`, `api/tests/test_uploads.py` | `ticket_attachment` accepted (images only); `file_belongs` |
| `api/src/api/main.py` | wires the three routers |
| `api/src/api/alerts.py`, `api/src/api/telegram.py` | `investor_bonus_paid` warning |
| `api/tests/test_portal_notify_callers.py` (new), `test_portal_summary.py`, `test_portal_account_requests.py`, `test_portal_transfers.py`, `test_rbac_matrix.py` | updated to the new rules and shapes |
| `dashboard/src/lib/types.ts`, `lib/engagement.ts` (+ test), `test/portalFixtures.ts` (+ test) | phase 4 types, labels, fixtures |
| `dashboard/src/hooks/useUnreadCount.ts` (+ test), `lib/notificationActions.ts` (+ test), `components/layout/NotificationBell.tsx` (+ test), `pages/Notifications.tsx` (+ test), `components/Layout.tsx` (+ test) | bell and page |
| `dashboard/src/hooks/useTheme.ts` (+ test), `lib/themeSync.ts` (+ test), `pages/Settings.tsx` (+ test) | settings and theme sync |
| `dashboard/src/pages/support/TicketMessages.tsx`, `pages/investor/InvestorSupport.tsx` (+ test) | investor support |
| `dashboard/src/pages/requests/SupportTab.tsx` (+ test), `RequestTabs.tsx`, `pages/Requests.tsx`, `pages/investors/TicketSubjectsCard.tsx` (+ test), `PaymentMethodsTab.tsx`, `pages/Investors.tsx`, `pages/Investors.test.tsx`, `pages/Requests.test.tsx` | desk support |
| `dashboard/src/pages/investor/InvestorBonus.tsx` (+ test), `InvestorTransfer.tsx` (+ test) | bonus page, credit transfer |
| `dashboard/src/pages/investors/BonusRulesCard.tsx` (+ test), `GrantBonusDialog.tsx` (+ test), `AdjustDialog.tsx` | admin bonus; shared signed-amount check |
| `dashboard/src/App.tsx`, `pages/groups/admin.ts`, `pages/groups/investor.ts`, `components/layout/nav.ts` (+ test) | routes and nav |
| `README.md`, spec status line | runbook, status |

## Tasks

| # | Task |
|---|---|
| 1 | Migration 025 and its test; conftest TRUNCATE |
| 2 | `notify()` with per-topic email prefs; the nine callers moved; `notify_investor` removed |
| 3 | Notification routes: list, unread count, read one, read all |
| 4 | `/api/me/settings` and notification prefs routes |
| 5 | Ticket subjects; uploads accept ticket images; `file_belongs` knows ticket messages |
| 6 | Investor tickets: open, list and search, thread, reply, close, rate limit |
| 7 | Desk tickets: queue, thread, reply, close, desk notifications, summary count |
| 8 | Bonus core: `deposit_bonus`, `pay_bonus` and friends, bonus rules routes |
| 9 | Bonus triggers: signup, KYC, deposit |
| 10 | Manual grant and claw-back; the investor's bonus history; alert rules |
| 11 | Credit -> trading account transfers; bonus credit never moves back out |
| 12 | RBAC matrix rows; the full API suite |
| 13 | Dashboard foundation: types, `lib/engagement.ts`, fixtures |
| 14 | The bell and the Notifications page |
| 15 | Settings page and theme sync |
| 16 | Investor Support pages |
| 17 | Desk Support tab, Ticket subjects card, "Portal settings" tab |
| 18 | Investor Bonus page; Transfer offers Credit |
| 19 | Bonus rules card and Grant bonus dialog |
| 20 | Gates and docs |

---

### Task 1: Migration 025 — notifications, tickets, bonuses, settings

**Files:**
- Create: `db/migrations/025_portal_engagement.sql`
- Create: `api/tests/test_migration_025.py`
- Modify: `api/tests/conftest.py` (the `db` fixture's TRUNCATE)

**Interfaces:**
- Consumes: `db/migrate.py` `apply_migrations` (conftest runs it); conftest `db`, `make_user`, `make_org`.
- Produces: tables `notifications`, `notification_prefs`, `user_settings`, `ticket_subjects`, `tickets`, `ticket_messages`, `bonus_rules`, `bonuses` with the indexes and CHECKs listed in the interfaces doc; conftest truncates all eight.

- [x] **Step 1: Write the failing migration test**

Create `api/tests/test_migration_025.py`:

```python
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
```

- [x] **Step 2: Run the test to verify it fails**

Run: `"$PY" -m pytest tests/test_migration_025.py -q -p no:cacheprovider`
Expected: FAIL — `025_portal_engagement.sql` is not in `schema_migrations`.

- [x] **Step 3: Write the migration**

Create `db/migrations/025_portal_engagement.sql`:

```sql
-- Client portal, phase 4: the in-app notification centre (with per-topic
-- email switches), appearance per user, support tickets between investors
-- and the desk, and bonuses paid into the Credit wallet. Additive only:
-- eight new tables, no existing table changes. files.purpose already allows
-- 'ticket_attachment', wallet_entries.kind 'bonus', wallet_entries.wallet
-- 'credit'.
-- See docs/superpowers/specs/2026-10-05-client-portal-phase-4-design.md
-- section 4.

-- One row per recipient per event. link is an in-app path (never a URL).
CREATE TABLE notifications (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic       TEXT NOT NULL CHECK (topic IN ('money', 'identity', 'support', 'bonus')),
    title       TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    body        TEXT NOT NULL CHECK (char_length(body) <= 500),
    link        TEXT NULL CHECK (link LIKE '/%'),
    read_at     TIMESTAMPTZ NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_by_user
    ON notifications (org_id, user_id, created_at DESC, id DESC);
CREATE INDEX notifications_unread ON notifications (org_id, user_id) WHERE read_at IS NULL;

-- Email switches per user per org; no row means every switch is on.
CREATE TABLE notification_prefs (
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    money       BOOLEAN NOT NULL DEFAULT true,
    identity    BOOLEAN NOT NULL DEFAULT true,
    support     BOOLEAN NOT NULL DEFAULT true,
    bonus       BOOLEAN NOT NULL DEFAULT true,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);

-- Appearance follows the user across orgs and browsers.
CREATE TABLE user_settings (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    theme       TEXT NOT NULL DEFAULT 'system'
                CHECK (theme IN ('light', 'dim', 'dark', 'system')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What an investor may raise a ticket about, defined by an admin.
CREATE TABLE ticket_subjects (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
    enabled     BOOLEAN NOT NULL DEFAULT true,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ticket_subjects_one_label ON ticket_subjects (org_id, lower(label));

-- One support thread. subject_label is a snapshot, so deleting a subject
-- never rewrites an old ticket.
CREATE TABLE tickets (
    id               BIGSERIAL PRIMARY KEY,
    org_id           BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id       BIGINT NULL REFERENCES ticket_subjects(id) ON DELETE SET NULL,
    subject_label    TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'open', 'closed')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ NULL,
    closed_by        BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
CREATE INDEX tickets_queue ON tickets (org_id, status, last_message_at DESC);
CREATE INDEX tickets_by_user ON tickets (org_id, user_id, last_message_at DESC);

-- The thread. file_ids are the investor's own ticket_attachment uploads;
-- routes/portal_files.file_belongs keeps each file to one message.
CREATE TABLE ticket_messages (
    id          BIGSERIAL PRIMARY KEY,
    ticket_id   BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    author_id   BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    from_desk   BOOLEAN NOT NULL,
    body        TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 4000),
    file_ids    BIGINT[] NOT NULL DEFAULT '{}' CHECK (cardinality(file_ids) <= 3),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ticket_messages_thread ON ticket_messages (ticket_id, created_at, id);

-- One row per org, created on first read (portal_common.bonus_rules).
CREATE TABLE bonus_rules (
    org_id           BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    signup_enabled   BOOLEAN NOT NULL DEFAULT false,
    signup_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (signup_amount >= 0),
    kyc_enabled      BOOLEAN NOT NULL DEFAULT false,
    kyc_amount       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (kyc_amount >= 0),
    deposit_enabled  BOOLEAN NOT NULL DEFAULT false,
    deposit_pct      NUMERIC(6,3) NOT NULL DEFAULT 0
                     CHECK (deposit_pct >= 0 AND deposit_pct <= 100),
    deposit_cap      NUMERIC(18,2) NULL CHECK (deposit_cap > 0),
    updated_by       BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every bonus paid; its credit ledger row references it (ref_table
-- 'bonuses'). The partial unique indexes make each rule pay at most once:
-- portal_common.pay_bonus inserts with ON CONFLICT DO NOTHING.
CREATE TABLE bonuses (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source      TEXT NOT NULL CHECK (source IN ('signup', 'kyc', 'deposit', 'manual')),
    source_id   BIGINT NULL,
    amount      NUMERIC(18,2) NOT NULL CHECK (amount <> 0),
    note        TEXT NULL,
    created_by  BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Only a hand-posted claw-back is negative; only a deposit bonus names a row.
    CHECK (amount > 0 OR source = 'manual'),
    CHECK ((source = 'deposit') = (source_id IS NOT NULL))
);
CREATE UNIQUE INDEX bonuses_once_per_user ON bonuses (org_id, user_id, source)
    WHERE source IN ('signup', 'kyc');
CREATE UNIQUE INDEX bonuses_once_per_deposit ON bonuses (org_id, source_id)
    WHERE source = 'deposit';
CREATE INDEX bonuses_by_user ON bonuses (org_id, user_id, created_at DESC, id DESC);
```

- [x] **Step 4: Truncate the new tables between tests**

In `api/tests/conftest.py`, in the `db` fixture, replace the first line of the TRUNCATE string

```python
            "TRUNCATE login_events, account_requests, account_packages, kyc_profiles, "
```

with

```python
            "TRUNCATE bonuses, bonus_rules, ticket_messages, tickets, ticket_subjects, "
            "user_settings, notification_prefs, notifications, "
            "login_events, account_requests, account_packages, kyc_profiles, "
```

(`RESTART IDENTITY` then resets their sequences too, which the RBAC matrix's fixed ids rely on in Task 12.)

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_migration_025.py tests/test_migration_024.py tests/test_migration_022.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add db/migrations/025_portal_engagement.sql api/tests/test_migration_025.py api/tests/conftest.py
git commit -m "feat(api): migration 025 -- notifications, tickets, bonuses and settings tables

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 2: `notify()` with per-topic email prefs; the nine callers moved

**Files:**
- Modify: `api/src/api/portal_common.py` (section `audit + email`)
- Modify: `api/src/api/routes/portal_admin.py` (six calls)
- Modify: `api/src/api/routes/portal_identity.py` (three calls)
- Modify: `api/tests/test_portal_common.py`
- Create: `api/tests/test_portal_notify_callers.py`

**Interfaces:**
- Consumes: migration 025 (`notifications`, `notification_prefs`); `ws.broadcaster.alerter` / `request.app.state.alerter` (unchanged); `portal_helpers.member`, `add_method`, `approved_destination`, `credit`, `kyc_profile`, `add_package`, `open_account_request`, `csrf`, `DEST_CRYPTO`.
- Produces (in `api/src/api/portal_common.py`): `TOPICS`, `clip(text, limit) -> str`, `investor_link(org_id, page) -> str`, `email_wanted(conn, org_id, user_id, topic) -> bool`, `async notify(conn, request, org_id, user_id, topic, title, body, link=None)`, `async notify_admins(conn, request, org_id, topic, title, body, link=None)`. `notify_investor` is deleted. Titles of the nine notifications equal the old email subjects.

- [x] **Step 1: Write the failing tests**

In `api/tests/test_portal_common.py`, replace the whole `test_notify_investor_is_best_effort` function with:

```python
def _notes(db, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT topic, title, body, link, read_at FROM notifications WHERE user_id = %s "
            "ORDER BY id", (user_id,)).fetchall()


def test_notify_writes_a_row_and_emails_unless_the_pref_is_off(db, org_user, monkeypatch):
    org_id, user_id = org_user
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    with psycopg.connect(db, autocommit=True) as conn:
        asyncio.run(pc.notify(conn, request, org_id, user_id, "money", "Subject", "Body",
                              "/org/1/invest"))
        conn.execute("INSERT INTO notification_prefs (org_id, user_id, money) "
                     "VALUES (%s, %s, false)", (org_id, user_id))
        asyncio.run(pc.notify(conn, request, org_id, user_id, "money", "Muted", "Body"))
        asyncio.run(pc.notify(conn, request, org_id, user_id, "bonus", "Still on", "x" * 600))
        asyncio.run(pc.notify(conn, request, org_id, 999999, "money", "Nobody", "Body"))
        monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True),
                            raising=False)
        asyncio.run(pc.notify(conn, request, org_id, user_id, "identity", "Fails quietly", "Body"))
        monkeypatch.setattr(ws_module.broadcaster, "alerter", None, raising=False)
        asyncio.run(pc.notify(conn, request, org_id, user_id, "support", "No alerter", "Body"))
        with pytest.raises(ValueError):
            pc.email_wanted(conn, org_id, user_id, "chat")
    assert [(to, subject) for to, subject, _ in fake.sent] == [
        ("inv@example.com", "Subject"), ("inv@example.com", "Still on")]
    assert fake.sent[1][2] == "x" * 499 + "…"
    rows = _notes(db, user_id)
    assert [r[:2] for r in rows] == [("money", "Subject"), ("money", "Muted"),
                                     ("bonus", "Still on"), ("identity", "Fails quietly"),
                                     ("support", "No alerter")]
    assert rows[0][2:] == ("Body", "/org/1/invest", None)
    assert len(rows[2][2]) == 500


def test_notify_admins_reaches_every_admin_and_nobody_else(db, make_user, make_org, monkeypatch):
    a1, a2 = make_user(email="a1@example.com"), make_user(email="a2@example.com")
    viewer, inv = make_user(email="v@example.com"), make_user(email="i@example.com")
    org_id = make_org(members=[(a1, "admin"), (a2, "admin"), (viewer, "viewer"),
                               (inv, "investor")])
    monkeypatch.setattr(ws_module.broadcaster, "alerter", None, raising=False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with psycopg.connect(db, autocommit=True) as conn:
        asyncio.run(pc.notify_admins(conn, request, org_id, "support", "New ticket #1: Deposits",
                                     "Body", "/org/1/requests?tab=support&ticket=1"))
        rows = conn.execute("SELECT user_id, topic FROM notifications ORDER BY user_id").fetchall()
    assert rows == sorted([(a1["id"], "support"), (a2["id"], "support")])


def test_clip_and_investor_link():
    assert pc.clip("abc", 3) == "abc"
    assert pc.clip("abcd", 3) == "ab…"
    assert pc.investor_link(7, "deposit") == "/org/7/invest/deposit"
    assert pc.TOPICS == ("money", "identity", "support", "bonus")
```

Create `api/tests/test_portal_notify_callers.py`:

```python
# api/tests/test_portal_notify_callers.py
"""Every decision that used to email the investor (the nine notify_investor
callers of phases 1-3) now also leaves one in-app notification with its
topic and an in-app link -- driven through the routes in one walk."""
import psycopg
from psycopg.types.json import Jsonb

from portal_helpers import (DEST_CRYPTO, add_method, add_package, approved_destination, credit,
                            csrf, kyc_profile, member, open_account_request)


def test_the_nine_decisions_each_write_one_notification(org_client, make_user, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    uid = investor["id"]
    member(db, org_id, uid, "investor")
    seed(1001, role="slave")
    method_id = add_method(db, org_id)
    dest_id = approved_destination(db, org_id, uid)
    credit(db, org_id, uid, "500")
    kyc_profile(db, org_id, uid, status="submitted")
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        (dep_id,) = conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, amount, "
            "reference) VALUES (%s, %s, %s, 'crypto', 'USDT on TRC20', 100, 'n-1') RETURNING id",
            (org_id, uid, method_id)).fetchone()
        (wd_id,) = conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount) "
            "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…21', 50, 0, 50) RETURNING id",
            (org_id, uid, dest_id)).fetchone()
        (pending_dest,) = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'Second', %s) RETURNING id",
            (org_id, uid, Jsonb(DEST_CRYPTO))).fetchone()
        (tr_id,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
            "target_account_id, amount) VALUES (%s, %s, 'wallet', 'main', 'account', 1001, 10) "
            "RETURNING id", (org_id, uid)).fetchone()

    def post(tail, body):
        return client.post(f"/api/orgs/{org_id}/{tail}", json=body, headers=csrf(client))

    assert post(f"deposits/{dep_id}/decision", {"status": "confirmed"}).status_code == 200
    assert post(f"withdrawals/{wd_id}/decision", {"status": "approved"}).status_code == 200
    assert post(f"withdrawals/{wd_id}/paid", {"txid": "tx-1"}).status_code == 200
    assert post(f"payout-destinations/{pending_dest}/decision",
                {"status": "approved"}).status_code == 200
    assert post(f"transfers/{tr_id}/decision", {"status": "rejected", "note": "no"}).status_code == 200
    assert post(f"investors/{uid}/adjustments", {"wallet": "main", "amount": "5", "note": "fix",
                                                 "mpin": "123456"}).status_code == 201
    assert post(f"kyc/{uid}/decision", {"status": "approved"}).status_code == 200
    first = open_account_request(db, org_id, uid, package_id)
    assert post(f"account-requests/{first}/reject", {"note": "full"}).status_code == 200
    second = open_account_request(db, org_id, uid, package_id)
    assert post(f"account-requests/{second}/fulfil",
                {"mt5_login": 5001, "mt5_server": "Broker-Live"}).status_code == 200

    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT org_id, topic, title, link FROM notifications WHERE user_id = %s ORDER BY id",
            (uid,)).fetchall()
    p = f"/org/{org_id}/invest"
    assert rows == [
        (org_id, "money", "Your deposit of 100.00 USD was confirmed", f"{p}/deposit"),
        (org_id, "money", "Your withdrawal of 50.00 USD was approved", f"{p}/withdraw"),
        (org_id, "money", "Your withdrawal of 50.00 USD was paid", f"{p}/withdraw"),
        (org_id, "money", "Your payout account TRC20 T…21 was approved", f"{p}/payout-accounts"),
        (org_id, "money", "Your transfer of 10.00 USD was rejected", f"{p}/transfer"),
        (org_id, "money", "Your My wallet was adjusted by +5.00 USD", f"{p}/transactions"),
        (org_id, "identity", "Your identity verification was approved", f"{p}/profile"),
        (org_id, "identity", "Your trading account request was rejected", f"{p}/open-account"),
        (org_id, "identity", "Your trading account is ready", f"{p}/open-account"),
    ]
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_common.py tests/test_portal_notify_callers.py -q -p no:cacheprovider`
Expected: FAIL — `module 'api.portal_common' has no attribute 'notify'`; the callers test finds no notification rows.

- [x] **Step 3: Replace `notify_investor` with `notify`**

In `api/src/api/portal_common.py`, replace the whole `notify_investor` function with:

```python
TOPICS = ("money", "identity", "support", "bonus")
TITLE_MAX = 120
BODY_MAX = 500


def clip(text: str, limit: int) -> str:
    """At most `limit` characters; an ellipsis marks a cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def investor_link(org_id: int, page: str) -> str:
    """The in-app path of an investor page, e.g. investor_link(7, "deposit")."""
    return f"/org/{org_id}/invest/{page}"


def email_wanted(conn: psycopg.Connection, org_id: int, user_id: int, topic: str) -> bool:
    """The user's email switch for `topic` in this org; no prefs row means
    every switch is on. `topic` names a column, so it is checked first."""
    if topic not in TOPICS:
        raise ValueError(f"unknown notification topic {topic!r}")
    row = conn.execute(f"SELECT {topic} FROM notification_prefs WHERE org_id = %s AND user_id = %s",
                       (org_id, user_id)).fetchone()
    return row is None or bool(row[0])


async def notify(conn: psycopg.Connection, request: Request, org_id: int, user_id: int,
                 topic: str, title: str, body: str, link: Optional[str] = None) -> None:
    """One in-app notification for `user_id` in `org_id`, then the same words
    by email (subject = title, text = body) unless their switch for `topic`
    is off. Title and body are clipped to the column limits for both. Best
    effort, like the audit: a failed insert is logged, and a failing or
    missing alerter never fails the request that triggered it. Callers run
    it AFTER their transaction, never while holding the ledger lock."""
    title, body = clip(title, TITLE_MAX), clip(body, BODY_MAX)
    try:
        conn.execute(
            "INSERT INTO notifications (org_id, user_id, topic, title, body, link) "
            "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, user_id, topic, title, body, link))
    except Exception:
        logger.exception("failed to write notification for user %s", user_id)
    if not email_wanted(conn, org_id, user_id, topic):
        return
    alerter = getattr(broadcaster, "alerter", None)
    if alerter is None:
        alerter = getattr(request.app.state, "alerter", None)
    if alerter is None:
        return
    row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return
    try:
        await alerter.send_to(row[0], title, body)
    except Exception:
        logger.exception("notification email failed for user %s", user_id)


async def notify_admins(conn: psycopg.Connection, request: Request, org_id: int, topic: str,
                        title: str, body: str, link: Optional[str] = None) -> None:
    """notify() every admin member of the org (the support desk)."""
    for (admin_id,) in conn.execute(
            "SELECT user_id FROM org_memberships WHERE org_id = %s AND role = 'admin' "
            "ORDER BY user_id", (org_id,)).fetchall():
        await notify(conn, request, org_id, admin_id, topic, title, body, link)
```

- [x] **Step 4: Move the six admin callers**

In `api/src/api/routes/portal_admin.py`, replace each `pc.notify_investor(...)` call as follows (the texts are unchanged; only the call, the org, the topic and the link are new).

In `decide_deposit`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, investor_id, "money",
            f"Your deposit of {out['amount']:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {out['amount']:.2f} USD via {out['method_label']}\n"
            f"{credited_line}Note: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "deposit"))
```

In `decide_withdrawal`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your withdrawal of {amount:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {amount:.2f} USD\nTo: {summary}\n"
            f"Note: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "withdraw"))
```

In `mark_withdrawal_paid`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your withdrawal of {amount:.2f} USD was paid",
            f"Amount: {amount:.2f} USD\nTo: {summary}\nTransaction: {txid}\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "withdraw"))
```

In `decide_destination`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your payout account {summary} was {new_status}",
            f"Status: {new_status}\nPayout account: {summary}\nNote: {note or '—'}\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "payout-accounts"))
```

In `decide_transfer`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your transfer of {amount:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {amount:.2f} USD\nFrom: {from_label}\n"
            f"To: {to_label}\nNote: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "transfer"))
```

In `post_adjustment`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your {label} was adjusted by {amount:+.2f} USD",
            f"Wallet: {label}\nAmount: {amount:+.2f} USD\nNote: {note}\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "transactions"))
```

- [x] **Step 5: Move the three identity callers**

In `api/src/api/routes/portal_identity.py`:

In `decide_kyc`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "identity",
            f"Your identity verification was {new_status}",
            f"Status: {new_status}\nNote: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "profile"))
```

In `fulfil_request`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "identity", "Your trading account is ready",
            f"Login: {login}\nServer: {server}\nPackage: {package_name}\n"
            "Sign in to MetaTrader 5 with the passwords you chose when you requested it.\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "open-account"))
```

In `reject_request`:

```python
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "identity",
            "Your trading account request was rejected",
            f"Package: {package_name}\nNote: {note}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "open-account"))
```

Run: `grep -rn "notify_investor" api/src api/tests`
Expected: no output.

- [x] **Step 6: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_common.py tests/test_portal_notify_callers.py tests/test_portal_deposits.py tests/test_portal_withdrawals.py tests/test_portal_transfers.py tests/test_portal_summary.py tests/test_portal_kyc.py tests/test_portal_account_requests.py -q -p no:cacheprovider`
Expected: PASS (the existing email tests still see the same subjects and texts).

- [x] **Step 7: Commit**

```bash
git add api/src/api/portal_common.py api/src/api/routes/portal_admin.py api/src/api/routes/portal_identity.py api/tests/test_portal_common.py api/tests/test_portal_notify_callers.py
git commit -m "feat(api): notify() writes an in-app notification and honours email prefs; nine callers moved

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 3: Notification routes — list, unread count, read one, read all

**Files:**
- Create: `api/src/api/routes/portal_notifications.py`
- Modify: `api/src/api/main.py` (router wiring)
- Create: `api/tests/test_portal_notifications.py`

**Interfaces:**
- Consumes: `rbac.require_org_role("investor")` (every member passes), `pc._iso`.
- Produces: `create_portal_notifications_router()` (prefix `/api`), `note_json(row) -> dict`, `NOTE_COLS`; routes `GET /api/orgs/{org_id}/notifications?before=&limit=`, `GET .../notifications/unread-count`, `POST .../notifications/{note_id}/read`, `POST .../notifications/read-all` (contracts in the interfaces doc).

- [x] **Step 1: Write the failing tests**

Create `api/tests/test_portal_notifications.py`:

```python
# api/tests/test_portal_notifications.py
"""Notification routes: any member reads and marks only their OWN rows,
newest first with an id cursor; another user's id is a 404."""
import psycopg
import pytest

from portal_helpers import csrf, member


def _user_id(db, email):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


def _seed(db, org_id, user_id, n, *, read=False):
    """n notifications titled N0..N<n-1>, oldest first; returns their ids."""
    ids = []
    with psycopg.connect(db, autocommit=True) as conn:
        for i in range(n):
            (note_id,) = conn.execute(
                "INSERT INTO notifications (org_id, user_id, topic, title, body, link, read_at) "
                "VALUES (%s, %s, 'money', %s, 'Body', '/x', CASE WHEN %s THEN now() END) "
                "RETURNING id", (org_id, user_id, f"N{i}", read)).fetchone()
            ids.append(int(note_id))
    return ids


def _url(org_id, tail=""):
    return f"/api/orgs/{org_id}/notifications{tail}"


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    client, org_id, _seed_account = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    return client, org_id, investor


def test_the_list_is_own_rows_newest_first_with_a_cursor(portal, db):
    client, org_id, investor = portal
    mine = _seed(db, org_id, investor["id"], 3)
    _seed(db, org_id, _user_id(db, "admin@example.com"), 2)
    body = client.get(_url(org_id, "?limit=2")).json()
    assert [n["title"] for n in body["notifications"]] == ["N2", "N1"]
    assert body["has_more"] is True and body["next_before"] == mine[1]
    first = body["notifications"][0]
    assert first == {"id": mine[2], "topic": "money", "title": "N2", "body": "Body", "link": "/x",
                     "read_at": None, "created_at": first["created_at"]}
    rest = client.get(_url(org_id, f"?limit=2&before={mine[1]}")).json()
    assert [n["title"] for n in rest["notifications"]] == ["N0"]
    assert rest["has_more"] is False and rest["next_before"] is None
    assert len(client.get(_url(org_id)).json()["notifications"]) == 3
    assert len(client.get(_url(org_id, "?limit=0")).json()["notifications"]) == 1


def test_unread_count_mark_one_and_mark_all(portal, db):
    client, org_id, investor = portal
    ids = _seed(db, org_id, investor["id"], 3)
    _seed(db, org_id, investor["id"], 1, read=True)
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 3}
    r = client.post(_url(org_id, f"/{ids[0]}/read"), headers=csrf(client))
    assert r.status_code == 200 and r.json()["read_at"] is not None
    again = client.post(_url(org_id, f"/{ids[0]}/read"), headers=csrf(client)).json()
    assert again["read_at"] == r.json()["read_at"]   # a second mark keeps the first time
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 2}
    assert client.post(_url(org_id, "/read-all"), headers=csrf(client)).json() == {"updated": 2}
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 0}


def test_another_users_notification_is_a_404(portal, db):
    client, org_id, _investor = portal
    (theirs,) = _seed(db, org_id, _user_id(db, "admin@example.com"), 1)
    for note_id in (theirs, 999999):
        r = client.post(_url(org_id, f"/{note_id}/read"), headers=csrf(client))
        assert r.status_code == 404 and r.json()["detail"] == "Notification not found"
    client.post(_url(org_id, "/read-all"), headers=csrf(client))
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT read_at FROM notifications WHERE id = %s",
                            (theirs,)).fetchone() == (None,)


def test_rows_of_another_org_stay_in_that_org(portal, db, make_org):
    client, org_id, investor = portal
    other_org = make_org(name="Other", members=[(investor, "investor")])
    _seed(db, other_org, investor["id"], 2)
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 0}
    assert client.get(_url(other_org, "/unread-count")).json() == {"count": 2}


def test_a_desk_member_reads_their_own_too(org_client, db):
    client, org_id, _seed_account = org_client
    _seed(db, org_id, _user_id(db, "admin@example.com"), 1)
    assert client.get(_url(org_id, "/unread-count")).json() == {"count": 1}
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_notifications.py -q -p no:cacheprovider`
Expected: FAIL — 404 on every notifications path (no router).

- [x] **Step 3: Write the router**

Create `api/src/api/routes/portal_notifications.py`:

```python
# api/src/api/routes/portal_notifications.py
"""In-app notifications, email preferences and per-user appearance (client
portal phase 4). Any member, desk or investor, reads and marks only their
OWN rows: every query is keyed on the caller's user id, and another user's
notification id answers 404 exactly like a missing one."""
from __future__ import annotations

from typing import Any, Dict, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

NOTE_COLS = "id, topic, title, body, link, read_at, created_at"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def note_json(row) -> Dict[str, Any]:
    note_id, topic, title, body, link, read_at, created_at = row
    return {"id": note_id, "topic": topic, "title": title, "body": body, "link": link,
            "read_at": pc._iso(read_at), "created_at": pc._iso(created_at)}


def create_portal_notifications_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["portal-notifications"])
    # The lowest rank: every member of the org passes.
    any_member = require_org_role("investor")

    @router.get("/orgs/{org_id}/notifications", response_model=Dict[str, Any])
    async def my_notifications(before: Optional[int] = None, limit: int = DEFAULT_LIMIT,
                               ctx: OrgContext = Depends(any_member),
                               conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Newest first, keyed by id (ids are monotonic, as in the wallet
        entries page); `before` is the last id of the previous page."""
        size = max(1, min(int(limit), MAX_LIMIT))
        where = "org_id = %s AND user_id = %s"
        params: list = [ctx.org_id, ctx.user_id]
        if before is not None:
            where += " AND id < %s"
            params.append(before)
        rows = conn.execute(
            f"SELECT {NOTE_COLS} FROM notifications WHERE {where} ORDER BY id DESC LIMIT %s",
            (*params, size + 1)).fetchall()
        has_more = len(rows) > size
        notes = [note_json(r) for r in rows[:size]]
        return {"notifications": notes, "has_more": has_more,
                "next_before": notes[-1]["id"] if has_more and notes else None}

    @router.get("/orgs/{org_id}/notifications/unread-count", response_model=Dict[str, Any])
    async def unread_count(ctx: OrgContext = Depends(any_member),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        (count,) = conn.execute(
            "SELECT count(*) FROM notifications WHERE org_id = %s AND user_id = %s "
            "AND read_at IS NULL", (ctx.org_id, ctx.user_id)).fetchone()
        return {"count": int(count)}

    @router.post("/orgs/{org_id}/notifications/read-all", response_model=Dict[str, Any])
    async def read_all(ctx: OrgContext = Depends(any_member),
                       conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        cursor = conn.execute(
            "UPDATE notifications SET read_at = now() WHERE org_id = %s AND user_id = %s "
            "AND read_at IS NULL", (ctx.org_id, ctx.user_id))
        return {"updated": cursor.rowcount}

    @router.post("/orgs/{org_id}/notifications/{note_id}/read", response_model=Dict[str, Any])
    async def read_one(note_id: int, ctx: OrgContext = Depends(any_member),
                       conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = conn.execute(
            "UPDATE notifications SET read_at = COALESCE(read_at, now()) "
            "WHERE id = %s AND org_id = %s AND user_id = %s "
            f"RETURNING {NOTE_COLS}", (note_id, ctx.org_id, ctx.user_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        return note_json(row)

    return router
```

- [x] **Step 4: Wire the router**

In `api/src/api/main.py`, directly after `app.include_router(create_portal_identity_router())`, add:

```python

    # Client portal phase 4: notifications, email prefs and appearance
    # (any member), support tickets, bonuses.
    from .routes.portal_notifications import create_portal_notifications_router
    app.include_router(create_portal_notifications_router())
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_notifications.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_notifications.py api/src/api/main.py api/tests/test_portal_notifications.py
git commit -m "feat(api): notification routes -- own rows, unread count, mark read

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 4: `/api/me/settings` and notification prefs routes

**Files:**
- Modify: `api/src/api/routes/portal_notifications.py`
- Create: `api/tests/test_portal_settings.py`

**Interfaces:**
- Consumes: `auth.require_user`; `pc.TOPICS`, `pc.email_wanted` (Task 2).
- Produces: `THEMES = ("light", "dim", "dark", "system")`; routes `GET/PUT /api/me/settings` (`{theme, updated_at}`), `GET/PUT /api/orgs/{org_id}/notification-prefs` (`{money, identity, support, bonus}`), refusals in the interfaces doc.

- [x] **Step 1: Write the failing tests**

Create `api/tests/test_portal_settings.py`:

```python
# api/tests/test_portal_settings.py
"""Appearance (per user, every org) and email switches (per user per org)."""
import psycopg

from portal_helpers import csrf, member

from api import portal_common as pc

ALL_ON = {"money": True, "identity": True, "support": True, "bonus": True}


def test_settings_round_trip_and_refusal(org_client):
    client, _org_id, _seed = org_client
    assert client.get("/api/me/settings").json() == {"theme": "system", "updated_at": None}
    r = client.put("/api/me/settings", json={"theme": "dim"}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["theme"] == "dim" and r.json()["updated_at"]
    assert client.get("/api/me/settings").json() == r.json()
    for bad in ("neon", None, 3, ["dim"]):
        r = client.put("/api/me/settings", json={"theme": bad}, headers=csrf(client))
        assert r.status_code == 400
        assert r.json()["detail"] == "theme must be light, dim, dark or system"


def test_settings_need_a_full_session(app_client):
    assert app_client.get("/api/me/settings").status_code == 401


def test_prefs_default_on_round_trip_and_mute_the_email(org_client, make_user, login_as, db):
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    url = f"/api/orgs/{org_id}/notification-prefs"
    assert client.get(url).json() == ALL_ON
    muted = {**ALL_ON, "money": False, "support": False}
    r = client.put(url, json=muted, headers=csrf(client))
    assert r.status_code == 200 and r.json() == muted
    assert client.get(url).json() == muted
    for body, detail in (({**ALL_ON, "money": "no"}, "money must be true or false"),
                         ({"money": True}, "identity must be true or false")):
        r = client.put(url, json=body, headers=csrf(client))
        assert r.status_code == 400 and r.json()["detail"] == detail
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.email_wanted(conn, org_id, investor["id"], "money") is False
        assert pc.email_wanted(conn, org_id, investor["id"], "identity") is True


def test_prefs_are_per_org(org_client, make_user, make_org, login_as, db):
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    other_org = make_org(name="Other", members=[(investor, "investor")])
    login_as(client, investor)
    client.put(f"/api/orgs/{org_id}/notification-prefs", json={**ALL_ON, "bonus": False},
               headers=csrf(client))
    assert client.get(f"/api/orgs/{other_org}/notification-prefs").json() == ALL_ON
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_settings.py -q -p no:cacheprovider`
Expected: FAIL — 404 on `/api/me/settings` and `notification-prefs`.

- [x] **Step 3: Add the routes**

In `api/src/api/routes/portal_notifications.py`, change the FastAPI import to

```python
from fastapi import APIRouter, Body, Depends, HTTPException
```

add `from ..auth import require_user` below `from ..db import get_conn`, add below `MAX_LIMIT = 100`:

```python
THEMES = ("light", "dim", "dark", "system")
```

and add before `return router`:

```python
    # ------------------------------------------------------------ appearance

    @router.get("/me/settings", response_model=Dict[str, Any])
    async def my_settings(user_id: int = Depends(require_user),
                          conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """updated_at is null until the user first saves: the dashboard then
        seeds the account from the browser's own choice instead of resetting it."""
        row = conn.execute("SELECT theme, updated_at FROM user_settings WHERE user_id = %s",
                           (user_id,)).fetchone()
        return {"theme": row[0] if row else "system",
                "updated_at": pc._iso(row[1]) if row else None}

    @router.put("/me/settings", response_model=Dict[str, Any])
    async def save_settings(body: Dict[str, Any] = Body(...),
                            user_id: int = Depends(require_user),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        theme = body.get("theme")
        if not isinstance(theme, str) or theme not in THEMES:
            raise HTTPException(status_code=400, detail="theme must be light, dim, dark or system")
        row = conn.execute(
            "INSERT INTO user_settings (user_id, theme) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET theme = EXCLUDED.theme, updated_at = now() "
            "RETURNING theme, updated_at", (user_id, theme)).fetchone()
        return {"theme": row[0], "updated_at": pc._iso(row[1])}

    # ------------------------------------------------------------ email switches

    @router.get("/orgs/{org_id}/notification-prefs", response_model=Dict[str, Any])
    async def my_prefs(ctx: OrgContext = Depends(any_member),
                       conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = conn.execute(
            "SELECT money, identity, support, bonus FROM notification_prefs "
            "WHERE org_id = %s AND user_id = %s", (ctx.org_id, ctx.user_id)).fetchone()
        return dict(zip(pc.TOPICS, map(bool, row))) if row else {t: True for t in pc.TOPICS}

    @router.put("/orgs/{org_id}/notification-prefs", response_model=Dict[str, Any])
    async def save_prefs(body: Dict[str, Any] = Body(...),
                         ctx: OrgContext = Depends(any_member),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """All four switches every time: the dashboard always sends the set."""
        for topic in pc.TOPICS:
            if not isinstance(body.get(topic), bool):
                raise HTTPException(status_code=400, detail=f"{topic} must be true or false")
        values = [body[t] for t in pc.TOPICS]
        conn.execute(
            "INSERT INTO notification_prefs (org_id, user_id, money, identity, support, bonus) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (org_id, user_id) DO UPDATE SET "
            "money = EXCLUDED.money, identity = EXCLUDED.identity, support = EXCLUDED.support, "
            "bonus = EXCLUDED.bonus, updated_at = now()", (ctx.org_id, ctx.user_id, *values))
        return dict(zip(pc.TOPICS, values))
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_settings.py tests/test_portal_notifications.py tests/test_auth.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/src/api/routes/portal_notifications.py api/tests/test_portal_settings.py
git commit -m "feat(api): per-user appearance and per-org email switches

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 5: Ticket subjects; uploads accept ticket images; `file_belongs` knows ticket messages

**Files:**
- Create: `api/src/api/routes/portal_support.py`
- Modify: `api/src/api/main.py`
- Modify: `api/src/api/uploads.py`, `api/src/api/routes/portal_files.py`
- Create: `api/tests/test_portal_tickets.py`
- Modify: `api/tests/test_uploads.py`

**Interfaces:**
- Consumes: `pc.clean_text`, `pc.audit_control`, `pc._iso`; `portal_helpers.csrf`, `member`, `seed_file`.
- Produces: `create_portal_support_router()` (prefix `/api/orgs/{org_id}`), `subject_json(row)`, `SUBJECT_COLS`; routes `GET/POST ticket-subjects`, `PATCH/DELETE ticket-subjects/{subject_id}`, `GET investor/ticket-subjects`; audit action `ticket_subject_changed` (`change` = created/updated/deleted). `uploads.ACCEPTED_PURPOSES` gains `ticket_attachment`; the upload route answers 400 `attachments must be images` for a non-image ticket attachment; `file_belongs` refuses a file already in any `ticket_messages.file_ids`. Test helpers in `test_portal_tickets.py`: `ADMIN`, `_events(db, org_id, action)`, `_user_id(db, email)`, `_subject(client, org_id, label, **extra)`.

- [x] **Step 1: Write the failing tests**

Create `api/tests/test_portal_tickets.py`:

```python
# api/tests/test_portal_tickets.py
"""Support tickets: subjects (admin), the investor's tickets and the desk's
queue. Tasks 5-7 of the phase 4 plan grow this file."""
import psycopg
import pytest

from portal_helpers import csrf, member, seed_file

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


def _user_id(db, email):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


def _subject(client, org_id, label, **extra):
    return client.post(f"/api/orgs/{org_id}/ticket-subjects", json={"label": label, **extra},
                       headers=csrf(client))


# ------------------------------------------------------------ subjects


def test_admin_manages_subjects_and_investors_see_the_enabled_ones(org_client, make_user,
                                                                   login_as, db):
    client, org_id, _seed = org_client
    r = _subject(client, org_id, "  Deposits ", sort=2)
    assert r.status_code == 201, r.text
    dep = r.json()
    assert (dep["label"], dep["enabled"], dep["sort"]) == ("Deposits", True, 2)
    wd = _subject(client, org_id, "Withdrawals", sort=1).json()
    other = _subject(client, org_id, "Other", sort=3).json()
    r = _subject(client, org_id, "deposits")
    assert r.status_code == 409 and r.json()["detail"] == "a subject with this label already exists"
    assert _subject(client, org_id, " ").json()["detail"] == "label is required"
    assert _subject(client, org_id, "x" * 81).json()["detail"] == "label must be at most 80 characters"
    base = f"/api/orgs/{org_id}/ticket-subjects"
    r = client.patch(f"{base}/{other['id']}", json={"enabled": False}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = client.patch(f"{base}/{wd['id']}", json={"label": "DEPOSITS"}, headers=csrf(client))
    assert r.status_code == 409
    r = client.patch(f"{base}/999", json={"sort": 1}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Subject not found"
    assert [s["label"] for s in client.get(base).json()] == ["Withdrawals", "Deposits", "Other"]
    assert [e[1]["change"] for e in _events(db, org_id, "ticket_subject_changed")] == [
        "created", "created", "created", "updated"]
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    client.cookies.clear()
    login_as(client, investor)
    assert [s["label"] for s in client.get(
        f"/api/orgs/{org_id}/investor/ticket-subjects").json()] == ["Withdrawals", "Deposits"]


def test_deleting_a_used_subject_keeps_the_ticket_label(org_client, make_user, db):
    client, org_id, _seed = org_client
    subject = _subject(client, org_id, "Deposits").json()
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (ticket_id,) = conn.execute(
            "INSERT INTO tickets (org_id, user_id, subject_id, subject_label) "
            "VALUES (%s, %s, %s, 'Deposits') RETURNING id",
            (org_id, investor["id"], subject["id"])).fetchone()
    url = f"/api/orgs/{org_id}/ticket-subjects/{subject['id']}"
    assert client.delete(url, headers=csrf(client)).status_code == 204
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT subject_id, subject_label FROM tickets WHERE id = %s",
                            (ticket_id,)).fetchone() == (None, "Deposits")
    assert client.delete(url, headers=csrf(client)).status_code == 404
    assert _events(db, org_id, "ticket_subject_changed")[-1][1]["change"] == "deleted"


def test_a_file_in_a_ticket_message_is_not_free_any_more(org_client, make_user, db):
    from api.routes.portal_files import file_belongs
    _client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    image = seed_file(db, org_id, investor["id"], purpose="ticket_attachment")
    with psycopg.connect(db, autocommit=True) as conn:
        assert file_belongs(conn, org_id, investor["id"], image, "ticket_attachment")
        (ticket_id,) = conn.execute(
            "INSERT INTO tickets (org_id, user_id, subject_label) VALUES (%s, %s, 'X') "
            "RETURNING id", (org_id, investor["id"])).fetchone()
        conn.execute(
            "INSERT INTO ticket_messages (ticket_id, org_id, author_id, from_desk, body, file_ids) "
            "VALUES (%s, %s, %s, false, 'Hi', %s::bigint[])",
            (ticket_id, org_id, investor["id"], [image]))
        assert not file_belongs(conn, org_id, investor["id"], image, "ticket_attachment")
```

In `api/tests/test_uploads.py`, change the parametrize line of `test_only_the_accepted_purposes_are_stored` to

```python
@pytest.mark.parametrize("purpose", ["avatar", "selfie", ""])
```

and add after that test:

```python
def test_ticket_attachments_are_images_only(portal):
    client, org_id, _, _ = portal
    r = upload(client, org_id, purpose="ticket_attachment")
    assert r.status_code == 201 and r.json()["purpose"] == "ticket_attachment"
    r = upload(client, org_id, PDF, purpose="ticket_attachment", name="a.pdf",
               claimed="application/pdf")
    assert r.status_code == 400 and r.json()["detail"] == "attachments must be images"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_tickets.py tests/test_uploads.py -q -p no:cacheprovider`
Expected: FAIL — 404 on `ticket-subjects`, `purpose is not accepted yet` for `ticket_attachment`, and `file_belongs` still true for an attached image.

- [x] **Step 3: Accept ticket images and keep each file to one message**

In `api/src/api/uploads.py`, replace

```python
ACCEPTED_PURPOSES = {"deposit_receipt", "payout_proof", "kyc_document", "kyc_photo"}
ALL_PURPOSES = ACCEPTED_PURPOSES | {"ticket_attachment", "avatar"}
```

with

```python
ACCEPTED_PURPOSES = {"deposit_receipt", "payout_proof", "kyc_document", "kyc_photo",
                     "ticket_attachment"}
ALL_PURPOSES = ACCEPTED_PURPOSES | {"avatar"}
```

and in the module docstring replace `Phase 1 stores deposit receipts and payout proofs, phase 2 identity
documents and photos; the other purposes are in the database CHECK already
and are accepted here in their phase.` with `Phase 1 stores deposit receipts and payout proofs, phase 2 identity
documents and photos, phase 4 support-ticket images; avatar is in the
database CHECK already and is accepted here in its phase.`

In `api/src/api/routes/portal_files.py`, in `upload_file`, directly after `content_type, ext = detected` add:

```python
        if purpose == "ticket_attachment" and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="attachments must be images")
```

In `file_belongs`, replace the query's last line

```python
        "      (k.id_front_file_id, k.id_back_file_id, k.address_proof_file_id, k.photo_file_id))",
```

with

```python
        "      (k.id_front_file_id, k.id_back_file_id, k.address_proof_file_id, k.photo_file_id)) "
        # ponytail: scans the ticket messages per check; add a GIN index on
        # file_ids if threads ever grow into the hundreds of thousands.
        "  AND NOT EXISTS (SELECT 1 FROM ticket_messages t WHERE files.id = ANY(t.file_ids))",
```

and in its docstring replace `not to any slot of a KYC profile (phase 2: one file per document slot).` with `not to any slot of a KYC profile (phase 2: one file per document slot)
and not to any support-ticket message (phase 4).`

- [x] **Step 4: Write the subjects half of the support router**

Create `api/src/api/routes/portal_support.py`:

```python
# api/src/api/routes/portal_support.py
"""Support tickets (client portal phase 4): the subjects an admin offers,
threads between an investor and the desk with up to three images per
message, statuses new -> open -> closed, and the desk's queue. Investor
routes resolve the caller's OWN tickets through ctx.user_id; message text
never goes into the audit trail."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..db import get_conn
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc

logger = logging.getLogger(__name__)

SUBJECT_COLS = "id, label, enabled, sort, created_at"


class SubjectBody(BaseModel):
    label: Any = None
    enabled: bool = True
    sort: int = 0


class SubjectPatch(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None
    sort: Optional[int] = None


def subject_json(row) -> Dict[str, Any]:
    subject_id, label, enabled, sort, created_at = row
    return {"id": subject_id, "label": label, "enabled": bool(enabled), "sort": sort,
            "created_at": pc._iso(created_at)}


def create_portal_support_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-support"])

    def _subject_row(conn: psycopg.Connection, org_id: int, subject_id: int):
        row = conn.execute(
            f"SELECT {SUBJECT_COLS} FROM ticket_subjects WHERE id = %s AND org_id = %s",
            (subject_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Subject not found")
        return row

    async def _audit_subject(conn: psycopg.Connection, ctx: OrgContext, subject_id: int,
                             change: str, label: str) -> None:
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_subject_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, subject_id=subject_id,
            change=change, label=label,
            summary=f"Ticket subject {change}: {label} by {ctx.user_email}")

    # ------------------------------------------------------------ subjects, admin

    @router.get("/ticket-subjects", response_model=List[Dict[str, Any]])
    async def list_subjects(ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(f"SELECT {SUBJECT_COLS} FROM ticket_subjects WHERE org_id = %s "
                            "ORDER BY sort, id", (ctx.org_id,)).fetchall()
        return [subject_json(r) for r in rows]

    @router.post("/ticket-subjects", status_code=201, response_model=Dict[str, Any])
    async def create_subject(body: SubjectBody,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            label = pc.clean_text(body.label, "label", max_len=80)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            row = conn.execute(
                "INSERT INTO ticket_subjects (org_id, label, enabled, sort) "
                f"VALUES (%s, %s, %s, %s) RETURNING {SUBJECT_COLS}",
                (ctx.org_id, label, body.enabled, body.sort)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="a subject with this label already exists")
        await _audit_subject(conn, ctx, row[0], "created", label)
        return subject_json(row)

    @router.patch("/ticket-subjects/{subject_id}", response_model=Dict[str, Any])
    async def update_subject(subject_id: int, body: SubjectPatch,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _subject_row(conn, ctx.org_id, subject_id)
        sets: list[str] = []
        params: list[Any] = []
        if body.label is not None:
            try:
                params.append(pc.clean_text(body.label, "label", max_len=80))
            except pc.LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            sets.append("label = %s")
        if body.enabled is not None:
            sets.append("enabled = %s")
            params.append(body.enabled)
        if body.sort is not None:
            sets.append("sort = %s")
            params.append(body.sort)
        if not sets:
            return subject_json(current)
        try:
            row = conn.execute(
                f"UPDATE ticket_subjects SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
                f"RETURNING {SUBJECT_COLS}", (*params, subject_id, ctx.org_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="a subject with this label already exists")
        await _audit_subject(conn, ctx, subject_id, "updated", row[1])
        return subject_json(row)

    @router.delete("/ticket-subjects/{subject_id}", status_code=204)
    async def delete_subject(subject_id: int,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)):
        """Old tickets keep subject_label (the FK is ON DELETE SET NULL)."""
        current = _subject_row(conn, ctx.org_id, subject_id)
        conn.execute("DELETE FROM ticket_subjects WHERE id = %s AND org_id = %s",
                     (subject_id, ctx.org_id))
        await _audit_subject(conn, ctx, subject_id, "deleted", current[1])
        return Response(status_code=204)

    # ------------------------------------------------------------ subjects, investor

    @router.get("/investor/ticket-subjects", response_model=List[Dict[str, Any]])
    async def my_subjects(ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        """What the Raise ticket dialog offers: enabled subjects only."""
        rows = conn.execute(f"SELECT {SUBJECT_COLS} FROM ticket_subjects WHERE org_id = %s "
                            "AND enabled ORDER BY sort, id", (ctx.org_id,)).fetchall()
        return [subject_json(r) for r in rows]

    return router
```

In `api/src/api/main.py`, directly after `app.include_router(create_portal_notifications_router())` add:

```python
    from .routes.portal_support import create_portal_support_router
    app.include_router(create_portal_support_router())
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_tickets.py tests/test_uploads.py tests/test_portal_deposits.py tests/test_portal_kyc.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_support.py api/src/api/main.py api/src/api/uploads.py api/src/api/routes/portal_files.py api/tests/test_portal_tickets.py api/tests/test_uploads.py
git commit -m "feat(api): ticket subjects; ticket images accepted and kept to one message

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 6: Investor tickets — open, list and search, thread, reply, close, rate limit

**Files:**
- Modify: `api/src/api/routes/portal_support.py`
- Modify: `api/tests/test_portal_tickets.py`

**Interfaces:**
- Consumes: Task 5's router and helpers; `routes/portal_files.file_belongs`; `routes/portal_investor.RATE_LIMITED`; `auth.LoginRateLimiter`; `pc.notify_admins`, `pc.qualify`.
- Produces (module level in `portal_support.py`): `TICKETS_PER_HOUR = 10`, `REPLIES_PER_HOUR = 60`, `MAX_IMAGES = 3`, `STATUSES`, `TICKET_COLS`, `MESSAGE_COLS`, `LAST_FROM_DESK`, `WAITING_ON_DESK` (the one SQL rule for "waiting on the desk", on tickets aliased `t`), `TICKET_SELECT`, `TicketBody`, `MessageBody`, `DeskReply`, `ticket_json(row)`, `message_json(row)`, `clean_attachments(conn, org_id, user_id, raw) -> list[int]`, `search_clause(q, status) -> tuple[str, list]`, `load_thread(conn, org_id, ticket_id, user_id=None) -> dict` (with `user_id` set, the investor's view: desk messages carry no `author_id`/`author_name` and a desk close no `closed_by`), `add_message(conn, org_id, ticket_id, author_id, from_desk, body, file_ids)`, `desk_link(org_id, ticket_id) -> str`; inside the router `_close(conn, ctx, ticket_id, *, owner) -> (investor_id, subject_label)`. Routes `POST investor/tickets`, `GET investor/tickets?status=&q=`, `GET investor/tickets/{ticket_id}`, `POST investor/tickets/{ticket_id}/messages` (60 replies per hour, 429), `POST investor/tickets/{ticket_id}/close`; every Ticket carries `waiting_on_desk`; audits `ticket_opened`, `ticket_replied`, `ticket_closed`. Test helpers: fixture `portal` -> `(client, org_id, investor, {"deposits": id, "old": id})`, `_open`, `_reply`.

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_tickets.py`:

```python
# ------------------------------------------------------------ investor side


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    """org_client's org with subjects Deposits (enabled) and Old (disabled)
    and an investor member logged in."""
    client, org_id, _seed = org_client
    deposits = _subject(client, org_id, "Deposits").json()
    old = _subject(client, org_id, "Old", enabled=False).json()
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    client.cookies.clear()
    login_as(client, investor)
    return client, org_id, investor, {"deposits": deposits["id"], "old": old["id"]}


def _open(client, org_id, subject_id, body="My deposit is missing", file_ids=None):
    return client.post(f"/api/orgs/{org_id}/investor/tickets",
                       json={"subject_id": subject_id, "body": body, "file_ids": file_ids or []},
                       headers=csrf(client))


def _reply(client, org_id, ticket_id, body="Any news?", file_ids=None, desk=False):
    base = "tickets" if desk else "investor/tickets"
    payload = {"body": body} if desk else {"body": body, "file_ids": file_ids or []}
    return client.post(f"/api/orgs/{org_id}/{base}/{ticket_id}/messages", json=payload,
                       headers=csrf(client))


def test_an_investor_opens_a_ticket_with_images(portal, db):
    client, org_id, investor, subjects = portal
    images = [seed_file(db, org_id, investor["id"], purpose="ticket_attachment") for _ in range(2)]
    r = _open(client, org_id, subjects["deposits"], file_ids=images)
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["status"], t["subject_label"], t["user_id"]) == ("new", "Deposits", investor["id"])
    assert t["last_from_desk"] is False and t["closed_at"] is None
    assert t["waiting_on_desk"] is True
    (m,) = t["messages"]
    assert (m["body"], m["file_ids"], m["from_desk"], m["author_name"]) == (
        "My deposit is missing", images, False, "Inv One")
    severity, payload, actor = _events(db, org_id, "ticket_opened")[-1]
    assert (severity, actor) == ("info", "inv@example.com")
    assert payload == {"action": "ticket_opened", "user_id": investor["id"], "ticket_id": t["id"],
                       "subject": "Deposits", "images": 2,
                       "summary": f"Ticket #{t['id']} opened by inv@example.com: Deposits"}
    with psycopg.connect(db, autocommit=True) as conn:
        notes = conn.execute("SELECT user_id, topic, title, link FROM notifications "
                             "ORDER BY id").fetchall()
    assert notes == [(_user_id(db, "admin@example.com"), "support",
                      f"New ticket #{t['id']}: Deposits",
                      f"/org/{org_id}/requests?tab=support&ticket={t['id']}")]


def test_a_ticket_is_refused_for_a_bad_subject_body_or_images(portal, db, make_user):
    client, org_id, investor, subjects = portal
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    mine = seed_file(db, org_id, investor["id"], purpose="ticket_attachment")
    theirs = seed_file(db, org_id, other["id"], purpose="ticket_attachment")
    receipt = seed_file(db, org_id, investor["id"], purpose="deposit_receipt")
    four = [seed_file(db, org_id, investor["id"], purpose="ticket_attachment") for _ in range(4)]
    dep = subjects["deposits"]
    cases = [
        ({"subject_id": subjects["old"], "body": "x"}, 404, "Subject not found"),
        ({"subject_id": 999, "body": "x"}, 404, "Subject not found"),
        ({"subject_id": None, "body": "x"}, 400, "subject_id is required"),
        ({"subject_id": dep, "body": "  "}, 400, "body is required"),
        ({"subject_id": dep, "body": "x" * 4001}, 400, "body must be at most 4000 characters"),
        ({"subject_id": dep, "body": "x", "file_ids": four}, 400, "at most 3 images per message"),
        ({"subject_id": dep, "body": "x", "file_ids": [mine, mine]}, 400,
         "each image may be attached once"),
        ({"subject_id": dep, "body": "x", "file_ids": [theirs]}, 400, "image not found"),
        ({"subject_id": dep, "body": "x", "file_ids": [receipt]}, 400, "image not found"),
        ({"subject_id": dep, "body": "x", "file_ids": "1"}, 400, "file_ids must be a list of file ids"),
    ]
    for body, status, detail in cases:
        r = client.post(f"/api/orgs/{org_id}/investor/tickets", json=body, headers=csrf(client))
        assert (r.status_code, r.json()["detail"]) == (status, detail), body
    assert _open(client, org_id, dep, file_ids=[mine]).status_code == 201
    r = _open(client, org_id, dep, file_ids=[mine])
    assert r.status_code == 400 and r.json()["detail"] == "image not found"   # already attached


def test_the_list_filters_by_status_and_searches_subject_and_messages(portal, db):
    client, org_id, _investor, subjects = portal
    a = _open(client, org_id, subjects["deposits"], body="USDT never arrived").json()
    b = _open(client, org_id, subjects["deposits"], body="Bank wire 50% fee?").json()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE tickets SET status = 'closed', closed_at = now() WHERE id = %s",
                     (b["id"],))
    url = f"/api/orgs/{org_id}/investor/tickets"
    assert [t["id"] for t in client.get(url).json()] == [b["id"], a["id"]]
    assert [t["id"] for t in client.get(url + "?status=new").json()] == [a["id"]]
    assert [t["id"] for t in client.get(url + "?status=closed").json()] == [b["id"]]
    assert [t["id"] for t in client.get(url + "?q=usdt").json()] == [a["id"]]
    assert [t["id"] for t in client.get(url + "?q=50%25").json()] == [b["id"]]   # % is literal
    assert [t["id"] for t in client.get(url + "?q=deposits").json()] == [b["id"], a["id"]]
    r = client.get(url + "?status=pending")
    assert r.status_code == 400 and r.json()["detail"] == "status must be new, open or closed"


def test_reply_close_and_reopen(portal, db):
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    r = _reply(client, org_id, t["id"], "Any news?")
    assert r.status_code == 201 and r.json()["status"] == "new" and len(r.json()["messages"]) == 2
    close = f"/api/orgs/{org_id}/investor/tickets/{t['id']}/close"
    r = client.post(close, headers=csrf(client))
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["closed_by"]) == ("closed", investor["id"])
    r = client.post(close, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "ticket is already closed"
    body = _reply(client, org_id, t["id"], "Still missing").json()
    assert (body["status"], body["closed_at"], body["closed_by"]) == ("open", None, None)
    thread = client.get(f"/api/orgs/{org_id}/investor/tickets/{t['id']}").json()
    assert [m["body"] for m in thread["messages"]] == [
        "My deposit is missing", "Any news?", "Still missing"]
    assert [e[1]["from_desk"] for e in _events(db, org_id, "ticket_replied")] == [False, False]
    (closed,) = _events(db, org_id, "ticket_closed")
    assert closed[1]["from_desk"] is False and "missing" not in str(closed[1])


def test_another_investors_ticket_is_a_404(portal, db, make_user, login_as):
    client, org_id, _investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    client.cookies.clear()
    login_as(client, other)
    base = f"/api/orgs/{org_id}/investor/tickets"
    for r in (client.get(f"{base}/{t['id']}"), _reply(client, org_id, t["id"]),
              client.post(f"{base}/{t['id']}/close", headers=csrf(client))):
        assert r.status_code == 404 and r.json()["detail"] == "Ticket not found"
    assert client.get(base).json() == []


def test_ten_tickets_an_hour(portal):
    client, org_id, _investor, subjects = portal
    for _ in range(10):
        assert _open(client, org_id, subjects["deposits"]).status_code == 201
    r = _open(client, org_id, subjects["deposits"])
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


async def _no_notify(*_args, **_kwargs):
    """Skips the admin emails the reply rate-limit test would otherwise send."""
    return None


def test_sixty_replies_an_hour(portal, monkeypatch):
    """Each reply emails every admin, so replies are limited too: 60 per
    investor per hour, counted apart from new tickets."""
    from api import portal_common
    monkeypatch.setattr(portal_common, "notify_admins", _no_notify)
    client, org_id, _investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    for _ in range(60):
        assert _reply(client, org_id, t["id"]).status_code == 201
    r = _reply(client, org_id, t["id"])
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"
    thread = client.get(f"/api/orgs/{org_id}/investor/tickets/{t['id']}").json()
    assert len(thread["messages"]) == 61
    assert _open(client, org_id, subjects["deposits"]).status_code == 201, "tickets count apart"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_tickets.py -q -p no:cacheprovider`
Expected: FAIL — 404/405 on `investor/tickets`.

- [x] **Step 3: Add the ticket helpers**

In `api/src/api/routes/portal_support.py`, change the FastAPI import to

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response
```

add below `from .. import portal_common as pc`:

```python
from ..auth import LoginRateLimiter
from .portal_files import file_belongs
from .portal_investor import RATE_LIMITED
```

and below `SUBJECT_COLS = …`:

```python
TICKETS_PER_HOUR = 10
# Each investor reply emails every admin: a looser limit than new tickets,
# but a limit (keyed portal-ticket-reply:<org>:<user>).
REPLIES_PER_HOUR = 60
MAX_IMAGES = 3
STATUSES = ("new", "open", "closed")
TICKET_COLS = ("id, user_id, subject_id, subject_label, status, created_at, updated_at, "
               "last_message_at, closed_at, closed_by")
MESSAGE_COLS = "id, author_id, from_desk, body, file_ids, created_at"
# Which side spoke last, for the desk's "waiting on us" flag. Correlated on
# the ticket aliased t.
LAST_FROM_DESK = ("(SELECT m.from_desk FROM ticket_messages m WHERE m.ticket_id = t.id "
                  "ORDER BY m.id DESC LIMIT 1)")
# The ONE "waiting on the desk" rule: not closed, and the investor spoke
# last. Every Ticket's waiting_on_desk flag and requests/summary's tickets
# count read it; the dashboard only reads the flag.
WAITING_ON_DESK = f"(t.status <> 'closed' AND NOT COALESCE({LAST_FROM_DESK}, false))"
# Every ticket read selects these, in ticket_json's order.
TICKET_SELECT = f"{pc.qualify(TICKET_COLS, 't')}, {LAST_FROM_DESK}, {WAITING_ON_DESK}"


class TicketBody(BaseModel):
    # Any, checked by hand: a typed field would 422 before our messages.
    subject_id: Any = None
    body: Any = None
    file_ids: Any = None


class MessageBody(BaseModel):
    body: Any = None
    file_ids: Any = None


class DeskReply(BaseModel):
    # Text only: the upload route is the investor's (spec decisions).
    body: Any = None


def ticket_json(row) -> Dict[str, Any]:
    """A TICKET_SELECT row, optionally followed by the investor's email and
    display_name."""
    (ticket_id, user_id, subject_id, subject_label, status, created_at, updated_at,
     last_message_at, closed_at, closed_by, last_from_desk, waiting_on_desk) = row[:12]
    out = {"id": ticket_id, "user_id": user_id, "subject_id": subject_id,
           "subject_label": subject_label, "status": status, "created_at": pc._iso(created_at),
           "updated_at": pc._iso(updated_at), "last_message_at": pc._iso(last_message_at),
           "closed_at": pc._iso(closed_at), "closed_by": closed_by,
           "last_from_desk": bool(last_from_desk), "waiting_on_desk": bool(waiting_on_desk)}
    if len(row) > 12:
        out["email"], out["display_name"] = row[12], row[13]
    return out


def message_json(row) -> Dict[str, Any]:
    message_id, author_id, from_desk, body, file_ids, created_at, author_name = row
    return {"id": message_id, "author_id": author_id, "author_name": author_name,
            "from_desk": bool(from_desk), "body": body,
            "file_ids": [int(f) for f in file_ids or []], "created_at": pc._iso(created_at)}


def clean_attachments(conn: psycopg.Connection, org_id: int, user_id: int,
                      raw: object) -> List[int]:
    """The message's images: a list of at most three distinct ids, each the
    investor's own ticket_attachment upload not attached anywhere yet.
    Raises LedgerError with the message the route returns as a 400."""
    if raw is None:
        return []
    if not isinstance(raw, list) or any(isinstance(f, bool) or not isinstance(f, int) for f in raw):
        raise pc.LedgerError("file_ids must be a list of file ids")
    if len(raw) > MAX_IMAGES:
        raise pc.LedgerError("at most 3 images per message")
    if len(set(raw)) != len(raw):
        raise pc.LedgerError("each image may be attached once")
    for file_id in raw:
        if not file_belongs(conn, org_id, user_id, file_id, "ticket_attachment"):
            raise pc.LedgerError("image not found")
    # ponytail: checked then inserted without a lock -- two messages sending
    # the same new file at the same instant could both pass.
    return list(raw)


def search_clause(q: Optional[str], status: Optional[str]) -> tuple[str, list]:
    """Extra WHERE text (on tickets aliased t) for ?status= and ?q=; q matches
    the subject or any message, case-insensitively, with % and _ literal."""
    where, params = "", []
    if status is not None:
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail="status must be new, open or closed")
        where += " AND t.status = %s"
        params.append(status)
    text = (q or "").strip()
    if text:
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += (" AND (t.subject_label ILIKE %s OR EXISTS (SELECT 1 FROM ticket_messages s "
                  "WHERE s.ticket_id = t.id AND s.body ILIKE %s))")
        params += [like, like]
    return where, params


def load_thread(conn: psycopg.Connection, org_id: int, ticket_id: int,
                user_id: Optional[int] = None) -> Dict[str, Any]:
    """The ticket with its investor's email/name and every message, oldest
    first. `user_id` narrows to the owner (investor routes): another
    investor's ticket is the same 404 as a missing one, and the investor's
    view never names desk staff -- desk messages carry no author_id or
    author_name (the page shows "Support desk") and a desk close no
    closed_by."""
    sql = (f"SELECT {TICKET_SELECT}, u.email, u.display_name "
           "FROM tickets t JOIN users u ON u.id = t.user_id WHERE t.id = %s AND t.org_id = %s")
    params: list = [ticket_id, org_id]
    if user_id is not None:
        sql += " AND t.user_id = %s"
        params.append(user_id)
    row = conn.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    messages = conn.execute(
        f"SELECT {pc.qualify(MESSAGE_COLS, 'm')}, a.display_name FROM ticket_messages m "
        "LEFT JOIN users a ON a.id = m.author_id WHERE m.ticket_id = %s ORDER BY m.id",
        (ticket_id,)).fetchall()
    thread = {**ticket_json(row), "messages": [message_json(m) for m in messages]}
    if user_id is not None:
        if thread["closed_by"] not in (None, user_id):
            thread["closed_by"] = None
        for message in thread["messages"]:
            if message["from_desk"]:
                message["author_id"] = message["author_name"] = None
    return thread


def add_message(conn: psycopg.Connection, org_id: int, ticket_id: int, author_id: int,
                from_desk: bool, body: str, file_ids: List[int]) -> None:
    conn.execute(
        "INSERT INTO ticket_messages (ticket_id, org_id, author_id, from_desk, body, file_ids) "
        "VALUES (%s, %s, %s, %s, %s, %s::bigint[])",
        (ticket_id, org_id, author_id, from_desk, body, file_ids))


def desk_link(org_id: int, ticket_id: int) -> str:
    return f"/org/{org_id}/requests?tab=support&ticket={ticket_id}"
```

- [x] **Step 4: Add the investor routes**

In `create_portal_support_router()`, directly after `router = APIRouter(...)`, add:

```python
    # Ten new tickets per investor per hour, keyed portal-ticket:<org>:<user>;
    # its own instance, as each portal router keeps one. Replies share the
    # instance under their own key with REPLIES_PER_HOUR.
    hourly = LoginRateLimiter(max_attempts=TICKETS_PER_HOUR, window_s=3600)

    async def _close(conn: psycopg.Connection, ctx: OrgContext, ticket_id: int, *,
                     owner: Optional[int]) -> tuple[int, str]:
        """Close a ticket (the owner's own, or any of the org's for the desk);
        audits ticket_closed and returns (investor id, subject label)."""
        sql = "SELECT status, user_id, subject_label FROM tickets WHERE id = %s AND org_id = %s"
        params: list = [ticket_id, ctx.org_id]
        if owner is not None:
            sql += " AND user_id = %s"
            params.append(owner)
        current = conn.execute(sql, params).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Ticket not found")
        row = conn.execute(
            "UPDATE tickets SET status = 'closed', closed_at = now(), closed_by = %s, "
            "updated_at = now() WHERE id = %s AND org_id = %s AND status <> 'closed' RETURNING id",
            (ctx.user_id, ticket_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="ticket is already closed")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_closed", actor_email=ctx.user_email,
            user_id=current[1], ticket_id=ticket_id, from_desk=owner is None,
            summary=f"Ticket #{ticket_id} closed by {ctx.user_email}")
        return current[1], current[2]
```

and before `return router` add:

```python
    # ------------------------------------------------------------ tickets, investor

    @router.post("/investor/tickets", status_code=201, response_model=Dict[str, Any])
    async def open_ticket(body: TicketBody, http_request: Request,
                          ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        subject_id = body.subject_id
        if isinstance(subject_id, bool) or not isinstance(subject_id, int):
            raise HTTPException(status_code=400, detail="subject_id is required")
        try:
            text = pc.clean_text(body.body, "body", max_len=4000)
            files = clean_attachments(conn, ctx.org_id, ctx.user_id, body.file_ids)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        subject = conn.execute(
            "SELECT label FROM ticket_subjects WHERE id = %s AND org_id = %s AND enabled",
            (subject_id, ctx.org_id)).fetchone()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found")
        if hourly.is_limited(f"portal-ticket:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        label = subject[0]
        with conn.transaction():
            (ticket_id,) = conn.execute(
                "INSERT INTO tickets (org_id, user_id, subject_id, subject_label) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (ctx.org_id, ctx.user_id, subject_id, label)).fetchone()
            add_message(conn, ctx.org_id, ticket_id, ctx.user_id, False, text, files)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_opened", actor_email=ctx.user_email,
            user_id=ctx.user_id, ticket_id=ticket_id, subject=label, images=len(files),
            summary=f"Ticket #{ticket_id} opened by {ctx.user_email}: {label}")
        await pc.notify_admins(conn, http_request, ctx.org_id, "support",
                               f"New ticket #{ticket_id}: {label}",
                               f"From {ctx.user_email}\n\n{text}", desk_link(ctx.org_id, ticket_id))
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    @router.get("/investor/tickets", response_model=List[Dict[str, Any]])
    async def my_tickets(status: Optional[str] = None, q: Optional[str] = None,
                         ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where, params = search_clause(q, status)
        # ponytail: LIMIT 500, page when one investor has more tickets than that
        rows = conn.execute(
            f"SELECT {TICKET_SELECT} FROM tickets t "
            f"WHERE t.org_id = %s AND t.user_id = %s{where} "
            "ORDER BY t.last_message_at DESC, t.id DESC LIMIT 500",
            (ctx.org_id, ctx.user_id, *params)).fetchall()
        return [ticket_json(r) for r in rows]

    @router.get("/investor/tickets/{ticket_id}", response_model=Dict[str, Any])
    async def my_ticket(ticket_id: int, ctx: OrgContext = Depends(require_investor),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    @router.post("/investor/tickets/{ticket_id}/messages", status_code=201,
                 response_model=Dict[str, Any])
    async def reply_as_investor(ticket_id: int, body: MessageBody, http_request: Request,
                                ctx: OrgContext = Depends(require_investor),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """A reply to a closed ticket opens it again; new stays new."""
        try:
            text = pc.clean_text(body.body, "body", max_len=4000)
            files = clean_attachments(conn, ctx.org_id, ctx.user_id, body.file_ids)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if hourly.is_limited(f"portal-ticket-reply:{ctx.org_id}:{ctx.user_id}",
                             max_attempts=REPLIES_PER_HOUR):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        with conn.transaction():
            row = conn.execute(
                "UPDATE tickets SET status = CASE WHEN status = 'closed' THEN 'open' "
                "ELSE status END, closed_at = NULL, closed_by = NULL, last_message_at = now(), "
                "updated_at = now() WHERE id = %s AND org_id = %s AND user_id = %s "
                "RETURNING subject_label", (ticket_id, ctx.org_id, ctx.user_id)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Ticket not found")
            add_message(conn, ctx.org_id, ticket_id, ctx.user_id, False, text, files)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_replied", actor_email=ctx.user_email,
            user_id=ctx.user_id, ticket_id=ticket_id, from_desk=False, images=len(files))
        await pc.notify_admins(conn, http_request, ctx.org_id, "support",
                               f"Reply on ticket #{ticket_id}: {row[0]}",
                               f"From {ctx.user_email}\n\n{text}", desk_link(ctx.org_id, ticket_id))
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    @router.post("/investor/tickets/{ticket_id}/close", response_model=Dict[str, Any])
    async def close_as_investor(ticket_id: int, ctx: OrgContext = Depends(require_investor),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        await _close(conn, ctx, ticket_id, owner=ctx.user_id)
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_tickets.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_support.py api/tests/test_portal_tickets.py
git commit -m "feat(api): investor support tickets -- open with images, search, reply, close

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 7: Desk tickets — queue, thread, reply, close, desk notifications, summary count

**Files:**
- Modify: `api/src/api/routes/portal_support.py`
- Modify: `api/src/api/routes/portal_admin.py` (`requests_summary`)
- Modify: `api/tests/test_portal_tickets.py`, `api/tests/test_portal_summary.py`

**Interfaces:**
- Consumes: Task 6's helpers, `DeskReply` and `_close`; `pc.notify`, `pc.investor_link`.
- Produces: routes `GET tickets?status=&q=`, `GET tickets/{ticket_id}`, `POST tickets/{ticket_id}/messages`, `POST tickets/{ticket_id}/close`; `requests/summary` gains `tickets` (in `total`).

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_tickets.py`:

```python
# ------------------------------------------------------------ desk side


def test_the_desk_answers_and_the_status_and_summary_follow(portal, db, login_as):
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    client.cookies.clear()
    login_as(client, ADMIN)

    def summary():
        return client.get(f"/api/orgs/{org_id}/requests/summary").json()

    assert summary()["tickets"] == 1 and summary()["total"] == 1
    queue = client.get(f"/api/orgs/{org_id}/tickets").json()
    assert [(q["id"], q["status"], q["email"], q["last_from_desk"], q["waiting_on_desk"])
            for q in queue] == [(t["id"], "new", "inv@example.com", False, True)]
    r = _reply(client, org_id, t["id"], "We are checking", desk=True)
    assert r.status_code == 201, r.text
    thread = r.json()
    assert thread["status"] == "open" and thread["last_from_desk"] is True
    assert thread["waiting_on_desk"] is False
    last = thread["messages"][-1]
    assert (last["from_desk"], last["author_name"], last["file_ids"]) == (True, "User", [])
    assert last["author_id"] == _user_id(db, "admin@example.com"), "the desk sees who answered"
    assert summary()["tickets"] == 0           # answered: waiting on the investor
    client.cookies.clear()
    login_as(client, investor)
    r = _reply(client, org_id, t["id"], "Thanks, any update?")
    assert r.status_code == 201 and r.json()["waiting_on_desk"] is True
    client.cookies.clear()
    login_as(client, ADMIN)
    assert summary()["tickets"] == 1           # the investor spoke last
    r = client.post(f"/api/orgs/{org_id}/tickets/{t['id']}/close", headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert r.json()["waiting_on_desk"] is False
    assert r.json()["closed_by"] == _user_id(db, "admin@example.com")
    assert summary()["tickets"] == 0
    r = _reply(client, org_id, t["id"], "late", desk=True)
    assert r.status_code == 409 and r.json()["detail"] == "ticket is closed"
    assert _reply(client, org_id, 999999, "x", desk=True).status_code == 404
    link = f"/org/{org_id}/invest/support?ticket={t['id']}"
    with psycopg.connect(db, autocommit=True) as conn:
        to_investor = conn.execute("SELECT topic, title, link FROM notifications WHERE user_id = %s "
                                   "ORDER BY id", (investor["id"],)).fetchall()
        to_desk = [r[0] for r in conn.execute(
            "SELECT title FROM notifications WHERE user_id = %s ORDER BY id",
            (_user_id(db, "admin@example.com"),)).fetchall()]
    assert to_investor == [("support", f"New reply on ticket #{t['id']}: Deposits", link),
                           ("support", f"Ticket #{t['id']} was closed", link)]
    assert to_desk == [f"New ticket #{t['id']}: Deposits", f"Reply on ticket #{t['id']}: Deposits"]
    replies = [(e[1]["from_desk"], e[1]["user_id"]) for e in _events(db, org_id, "ticket_replied")]
    assert replies == [(True, investor["id"]), (False, investor["id"])]


def test_the_investor_thread_never_names_desk_staff(portal, db, login_as):
    client, org_id, investor, subjects = portal
    t = _open(client, org_id, subjects["deposits"]).json()
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _reply(client, org_id, t["id"], "We are checking", desk=True).status_code == 201
    assert client.post(f"/api/orgs/{org_id}/tickets/{t['id']}/close",
                       headers=csrf(client)).status_code == 200
    client.cookies.clear()
    login_as(client, investor)
    thread = client.get(f"/api/orgs/{org_id}/investor/tickets/{t['id']}").json()
    assert thread["closed_by"] is None and thread["closed_at"] is not None
    mine, desk = thread["messages"]
    assert (mine["author_id"], mine["author_name"]) == (investor["id"], "Inv One")
    assert (desk["from_desk"], desk["author_id"], desk["author_name"]) == (True, None, None)
    reopened = _reply(client, org_id, t["id"], "Still missing").json()
    assert reopened["messages"][1]["author_name"] is None
    # The investor's own close keeps closed_by: it names nobody else.
    r = client.post(f"/api/orgs/{org_id}/investor/tickets/{t['id']}/close", headers=csrf(client))
    assert r.json()["closed_by"] == investor["id"]


def test_the_desk_queue_filters_and_shows_the_thread(portal, db, login_as):
    client, org_id, investor, subjects = portal
    image = seed_file(db, org_id, investor["id"], purpose="ticket_attachment")
    a = _open(client, org_id, subjects["deposits"], body="Card payment", file_ids=[image]).json()
    b = _open(client, org_id, subjects["deposits"], body="Wire transfer").json()
    client.cookies.clear()
    login_as(client, ADMIN)
    url = f"/api/orgs/{org_id}/tickets"
    assert [t["id"] for t in client.get(url).json()] == [b["id"], a["id"]]
    assert [t["id"] for t in client.get(url + "?q=wire").json()] == [b["id"]]
    assert client.get(url + "?status=open").json() == []
    thread = client.get(f"{url}/{a['id']}").json()
    assert thread["messages"][0]["file_ids"] == [image] and thread["email"] == "inv@example.com"
    assert client.get(f"{url}/999999").status_code == 404
```

In `api/tests/test_portal_summary.py`, in `test_requests_summary_counts_open_rows`, both expected dicts gain `"tickets": 0` before `"total"`:

```python
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 0, "withdrawals": 0, "transfers": 0, "payout_destinations": 0,
        "kyc": 0, "account_requests": 0, "tickets": 0, "total": 0}
```

```python
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 1, "withdrawals": 2, "transfers": 2, "payout_destinations": 1,
        "kyc": 0, "account_requests": 0, "tickets": 0, "total": 6}
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_tickets.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: FAIL — 404 on `tickets`, and the summary has no `tickets` key.

- [x] **Step 3: Add the desk routes**

In `api/src/api/routes/portal_support.py`, before `return router` add:

```python
    # ------------------------------------------------------------ tickets, desk

    @router.get("/tickets", response_model=List[Dict[str, Any]])
    async def ticket_queue(status: Optional[str] = None, q: Optional[str] = None,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where, params = search_clause(q, status)
        # ponytail: LIMIT 500, add paging when an org has more tickets than that
        rows = conn.execute(
            f"SELECT {TICKET_SELECT}, u.email, u.display_name "
            "FROM tickets t JOIN users u ON u.id = t.user_id "
            f"WHERE t.org_id = %s{where} "
            "ORDER BY (t.status <> 'closed') DESC, t.last_message_at DESC, t.id DESC LIMIT 500",
            (ctx.org_id, *params)).fetchall()
        return [ticket_json(r) for r in rows]

    @router.get("/tickets/{ticket_id}", response_model=Dict[str, Any])
    async def ticket_thread(ticket_id: int, ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return load_thread(conn, ctx.org_id, ticket_id)

    @router.post("/tickets/{ticket_id}/messages", status_code=201, response_model=Dict[str, Any])
    async def reply_as_desk(ticket_id: int, body: DeskReply, http_request: Request,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """The first desk reply moves new -> open; a closed ticket is refused
        (the investor opens it again by replying)."""
        try:
            text = pc.clean_text(body.body, "body", max_len=4000)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with conn.transaction():
            row = conn.execute(
                "UPDATE tickets SET status = CASE WHEN status = 'new' THEN 'open' ELSE status END, "
                "last_message_at = now(), updated_at = now() "
                "WHERE id = %s AND org_id = %s AND status <> 'closed' "
                "RETURNING user_id, subject_label", (ticket_id, ctx.org_id)).fetchone()
            if not row:
                exists = conn.execute("SELECT 1 FROM tickets WHERE id = %s AND org_id = %s",
                                      (ticket_id, ctx.org_id)).fetchone()
                if exists:
                    raise HTTPException(status_code=409, detail="ticket is closed")
                raise HTTPException(status_code=404, detail="Ticket not found")
            add_message(conn, ctx.org_id, ticket_id, ctx.user_id, True, text, [])
        investor_id, label = row
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_replied", actor_email=ctx.user_email,
            user_id=investor_id, ticket_id=ticket_id, from_desk=True, images=0)
        await pc.notify(conn, http_request, ctx.org_id, investor_id, "support",
                        f"New reply on ticket #{ticket_id}: {label}", text,
                        pc.investor_link(ctx.org_id, f"support?ticket={ticket_id}"))
        return load_thread(conn, ctx.org_id, ticket_id)

    @router.post("/tickets/{ticket_id}/close", response_model=Dict[str, Any])
    async def close_as_desk(ticket_id: int, http_request: Request,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        investor_id, label = await _close(conn, ctx, ticket_id, owner=None)
        await pc.notify(conn, http_request, ctx.org_id, investor_id, "support",
                        f"Ticket #{ticket_id} was closed",
                        f"Subject: {label}\nReply on the ticket to open it again.",
                        pc.investor_link(ctx.org_id, f"support?ticket={ticket_id}"))
        return load_thread(conn, ctx.org_id, ticket_id)
```

- [x] **Step 4: Count tickets waiting on the desk**

In `api/src/api/routes/portal_admin.py`, below `from .portal_investor import WALLET_LABELS, entries_page, money_ref_label, pending_counts` add

```python
from .portal_support import WAITING_ON_DESK
```

(no cycle: `portal_support` imports `portal_investor` and `portal_files`, never `portal_admin`). In `requests_summary`, replace the last subquery of the SELECT

```python
                 (SELECT count(*) FROM account_requests
                   WHERE org_id = %(o)s AND status = 'requested')""",
```

with

```python
                 (SELECT count(*) FROM account_requests
                   WHERE org_id = %(o)s AND status = 'requested'),
                 (SELECT count(*) FROM tickets t
                   WHERE t.org_id = %(o)s AND """ + WAITING_ON_DESK + ")",
```

(the same rule every Ticket's `waiting_on_desk` flag uses, written once in `portal_support`).

and the `counts` dict with

```python
        counts = {"deposits": int(row[0]), "withdrawals": int(row[1]),
                  "transfers": int(row[2]), "payout_destinations": int(row[3]),
                  "kyc": int(row[4]), "account_requests": int(row[5]),
                  "tickets": int(row[6])}
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_tickets.py tests/test_portal_summary.py tests/test_portal_account_requests.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_support.py api/src/api/routes/portal_admin.py api/tests/test_portal_tickets.py api/tests/test_portal_summary.py
git commit -m "feat(api): the desk's ticket queue, replies and close; summary counts tickets waiting

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 8: Bonus core — `deposit_bonus`, `pay_bonus` and friends, bonus rules routes

**Files:**
- Modify: `api/src/api/portal_ledger.py`, `api/tests/test_portal_ledger.py`
- Modify: `api/src/api/portal_common.py` (new section `bonuses`), `api/tests/test_portal_common.py`
- Create: `api/src/api/routes/portal_bonus.py`
- Modify: `api/src/api/main.py`
- Create: `api/tests/test_portal_bonus.py`

**Interfaces:**
- Consumes: `pl.round_cents`; `pc.settle`, `pc.lock_investor_ledger`, `pc.audit_control`, `pc.notify`, `pc.investor_link`, `pc.money`, `pc.parse_amount`; `routes/portal_admin.parse_min`.
- Produces: `portal_ledger.deposit_bonus(amount, pct, cap) -> Decimal`; in `portal_common`: `BONUS_SOURCES`, `BONUS_NAMES`, `bonus_rules(conn, org_id) -> dict`, `rule_bonus(conn, org_id, source, base=None) -> Decimal`, `pay_bonus(conn, org_id, user_id, source, amount, source_id=None, note=None, created_by=None) -> Optional[int]`, `async announce_bonus(conn, request, *, org_id, user_id, bonus_id, source, amount, actor_email, note=None)`, `async award_rule_bonus(conn, request, org_id, user_id, source, *, actor_email) -> Optional[int]`; `create_portal_bonus_router()` with `GET/PUT bonus-rules`, `RulesBody`, `rules_json(rules)`, `parse_rules(body)`, audit `bonus_rules_changed`. Test helpers in `test_portal_bonus.py`: `ADMIN`, `RULES_OFF`, `_events`, `_rules(client, org_id, **over)`, `_credit_entries(db, user_id) -> [(source, amount)]`, `_investor(make_user, db, org_id, email=…)`, `_strip(rules)`.

- [x] **Step 1: Write the failing tests**

In `api/tests/test_portal_ledger.py`, add `deposit_bonus` to the `from api.portal_ledger import (…)` list and append:

```python
class TestDepositBonus:
    def test_pct_rounds_half_up_to_the_cent_and_the_cap_wins(self):
        assert deposit_bonus(D("1000"), D("10"), None) == D("100.00")
        assert deposit_bonus(D("33.33"), D("12.5"), None) == D("4.17")   # 4.16625
        assert deposit_bonus(D("1000"), D("10"), D("50")) == D("50")
        assert deposit_bonus(D("0.03"), D("12.5"), None) == D("0.00")
        assert deposit_bonus(D("200"), D("100"), None) == D("200.00")
```

In `api/tests/test_portal_common.py`, append:

```python
def test_pay_bonus_pays_once_into_credit(org_user, db):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(RuntimeError):
            pc.pay_bonus(conn, org_id, user_id, "signup", Decimal("5"))
        with conn.transaction():
            pc.lock_investor_ledger(conn, org_id, user_id)
            first = pc.pay_bonus(conn, org_id, user_id, "signup", Decimal("50"))
            again = pc.pay_bonus(conn, org_id, user_id, "signup", Decimal("50"))
            nothing = pc.pay_bonus(conn, org_id, user_id, "kyc", Decimal("0"))
            dep = pc.pay_bonus(conn, org_id, user_id, "deposit", Decimal("10"), source_id=7,
                               note="deposit #7")
            dep_again = pc.pay_bonus(conn, org_id, user_id, "deposit", Decimal("10"), source_id=7)
        assert isinstance(first, int) and isinstance(dep, int)
        assert again is None and nothing is None and dep_again is None
        rows = conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id, note FROM wallet_entries "
            "WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()
        assert rows == [("credit", Decimal("50.00"), "bonus", "bonuses", first, None),
                        ("credit", Decimal("10.00"), "bonus", "bonuses", dep, "deposit #7")]
        assert pc.wallet_figures(conn, org_id, user_id)["credit"]["available"] == Decimal("60.00")


def test_rule_bonus_follows_the_rules(org_user, db):
    org_id, _user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.rule_bonus(conn, org_id, "signup") == 0          # creates the row, all off
        assert pc.bonus_rules(conn, org_id)["deposit_cap"] is None
        conn.execute(
            "UPDATE bonus_rules SET signup_enabled = true, signup_amount = 25, "
            "deposit_enabled = true, deposit_pct = 10, deposit_cap = 30 WHERE org_id = %s",
            (org_id,))
        assert pc.rule_bonus(conn, org_id, "signup") == Decimal("25.00")
        assert pc.rule_bonus(conn, org_id, "kyc") == 0
        assert pc.rule_bonus(conn, org_id, "deposit", Decimal("100")) == Decimal("10.00")
        assert pc.rule_bonus(conn, org_id, "deposit", Decimal("1000")) == Decimal("30.00")
```

Create `api/tests/test_portal_bonus.py`:

```python
# api/tests/test_portal_bonus.py
"""Bonuses: the rules (admin), the three triggers that pay at most once,
manual grants and claw-backs, and the investor's history. Every bonus is a
bonuses row plus a credit ledger row (ref_table 'bonuses'). Tasks 8-10 of
the phase 4 plan grow this file."""
from decimal import Decimal

import psycopg
import pytest

from portal_helpers import csrf, kyc_profile, member

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
RULES_OFF = {"signup_enabled": False, "signup_amount": 0.0, "kyc_enabled": False,
             "kyc_amount": 0.0, "deposit_enabled": False, "deposit_pct": 0.0, "deposit_cap": None}


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


def _rules(client, org_id, **over):
    body = {"signup_enabled": False, "signup_amount": "0", "kyc_enabled": False,
            "kyc_amount": "0", "deposit_enabled": False, "deposit_pct": "0",
            "deposit_cap": None, **over}
    return client.put(f"/api/orgs/{org_id}/bonus-rules", json=body, headers=csrf(client))


def _credit_entries(db, user_id):
    """(bonus source, amount) of every credit ledger row a bonus wrote, oldest first."""
    with psycopg.connect(db, autocommit=True) as conn:
        return [(r[0], float(r[1])) for r in conn.execute(
            "SELECT b.source, w.amount FROM wallet_entries w JOIN bonuses b ON b.id = w.ref_id "
            "WHERE w.ref_table = 'bonuses' AND w.user_id = %s AND w.wallet = 'credit' "
            "ORDER BY w.id", (user_id,)).fetchall()]


def _investor(make_user, db, org_id, email="inv@example.com"):
    investor = make_user(email=email)
    member(db, org_id, investor["id"], "investor")
    return investor


def _strip(rules):
    return {k: v for k, v in rules.items() if k != "updated_at"}


# ------------------------------------------------------------ rules


def test_rules_default_off_and_round_trip(org_client, db):
    client, org_id, _seed = org_client
    url = f"/api/orgs/{org_id}/bonus-rules"
    assert _strip(client.get(url).json()) == RULES_OFF
    r = _rules(client, org_id, signup_enabled=True, signup_amount="50", deposit_enabled=True,
               deposit_pct="12.5", deposit_cap="100")
    assert r.status_code == 200, r.text
    assert _strip(r.json()) == {**RULES_OFF, "signup_enabled": True, "signup_amount": 50.0,
                                "deposit_enabled": True, "deposit_pct": 12.5, "deposit_cap": 100.0}
    assert r.json()["updated_at"]
    assert client.get(url).json() == r.json()
    severity, payload, actor = _events(db, org_id, "bonus_rules_changed")[-1]
    assert (severity, actor) == ("info", "admin@example.com")
    assert payload["previous"]["signup_enabled"] is False and payload["signup_amount"] == 50.0


@pytest.mark.parametrize("over, detail", [
    ({"signup_enabled": "yes"}, "signup_enabled must be true or false"),
    ({"kyc_amount": "-1"}, "kyc_amount must be greater than 0"),
    ({"kyc_amount": "1.234"}, "kyc_amount may have at most two decimals"),
    ({"deposit_pct": "100.5"}, "deposit_pct must be between 0 and 100"),
    ({"deposit_pct": "1.2345"}, "deposit_pct may have at most three decimals"),
    ({"deposit_cap": "0"}, "deposit_cap must be greater than 0"),
    ({"signup_enabled": True}, "signup_amount must be above 0 while the signup rule is on"),
    ({"deposit_enabled": True}, "deposit_pct must be above 0 while the deposit rule is on"),
])
def test_bad_rules_are_refused(org_client, over, detail):
    client, org_id, _seed = org_client
    r = _rules(client, org_id, **over)
    assert r.status_code == 400 and r.json()["detail"] == detail
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_ledger.py tests/test_portal_common.py tests/test_portal_bonus.py -q -p no:cacheprovider`
Expected: FAIL — `cannot import name 'deposit_bonus'`, no `pay_bonus`, 404 on `bonus-rules`.

- [x] **Step 3: `deposit_bonus`**

In `api/src/api/portal_ledger.py`, directly after `fee_for`, add:

```python
def deposit_bonus(amount: Decimal, pct: Decimal, cap: Optional[Decimal]) -> Decimal:
    """The deposit rule's bonus: pct of the confirmed amount, half-up to the
    cent, never above cap (None = no cap). Zero means nothing is paid."""
    bonus = round_cents(amount * pct / Decimal(100))
    return bonus if cap is None else min(bonus, cap)
```

- [x] **Step 4: The bonus helpers**

In `api/src/api/portal_common.py`, change the explicit ledger import to

```python
from .portal_ledger import WALLETS, available, balance, clean_text, deposit_bonus, holds, money
```

and add a new section directly after the `settle` function:

```python
# ------------------------------------------------------------ bonuses


BONUS_SOURCES = ("signup", "kyc", "deposit", "manual")
BONUS_NAMES = {"signup": "welcome bonus", "kyc": "verification bonus",
               "deposit": "deposit bonus", "manual": "bonus"}


def bonus_rules(conn: psycopg.Connection, org_id: int) -> dict:
    """The org's bonus rules; the row is created with every rule off on first
    read. Flags as bools, amounts and pct as Decimals, deposit_cap Decimal or None."""
    conn.execute("INSERT INTO bonus_rules (org_id) VALUES (%s) ON CONFLICT (org_id) DO NOTHING",
                 (org_id,))
    row = conn.execute(
        "SELECT signup_enabled, signup_amount, kyc_enabled, kyc_amount, deposit_enabled, "
        "deposit_pct, deposit_cap, updated_at FROM bonus_rules WHERE org_id = %s",
        (org_id,)).fetchone()
    return {"signup_enabled": bool(row[0]), "signup_amount": Decimal(row[1]),
            "kyc_enabled": bool(row[2]), "kyc_amount": Decimal(row[3]),
            "deposit_enabled": bool(row[4]), "deposit_pct": Decimal(row[5]),
            "deposit_cap": Decimal(row[6]) if row[6] is not None else None,
            "updated_at": row[7]}


def rule_bonus(conn: psycopg.Connection, org_id: int, source: str,
               base: Optional[Decimal] = None) -> Decimal:
    """What the org's rule pays for this event NOW (rules are never applied
    backwards): 0 while the rule is off; the deposit rule takes `base`, the
    confirmed amount."""
    rules = bonus_rules(conn, org_id)
    if not rules[f"{source}_enabled"]:
        return Decimal("0")
    if source == "deposit":
        return deposit_bonus(base, rules["deposit_pct"], rules["deposit_cap"])
    return rules[f"{source}_amount"]


def pay_bonus(conn: psycopg.Connection, org_id: int, user_id: int, source: str, amount: Decimal,
              source_id: Optional[int] = None, note: Optional[str] = None,
              created_by: Optional[int] = None) -> Optional[int]:
    """Record one bonus and credit it, inside the caller's transaction (which
    took lock_investor_ledger first). The bonuses unique indexes make each
    rule pay at most once: a second signup or kyc bonus for the investor, or
    a second one for the same deposit, inserts nothing and returns None. A
    zero amount pays nothing. Returns the bonuses id when paid."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError("pay_bonus must run inside the caller's transaction")
    if amount == 0:
        return None
    row = conn.execute(
        "INSERT INTO bonuses (org_id, user_id, source, source_id, amount, note, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
        (org_id, user_id, source, source_id, amount, note, created_by)).fetchone()
    if row is None:
        return None
    settle(conn, org_id=org_id, user_id=user_id, wallet="credit", amount=amount, kind="bonus",
           ref_table="bonuses", ref_id=int(row[0]), note=note, created_by=created_by)
    return int(row[0])


async def announce_bonus(conn: psycopg.Connection, request: Request, *, org_id: int,
                         user_id: int, bonus_id: int, source: str, amount: Decimal,
                         actor_email: str, note: Optional[str] = None) -> None:
    """Audit one paid bonus and tell the investor (topic bonus). A hand-posted
    bonus is a warning, so it reaches the alerters like a ledger adjustment;
    a rule's bonus is info. Run after the paying transaction."""
    name = BONUS_NAMES[source]
    row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
    who = row[0] if row else f"user {user_id}"
    await audit_control(
        conn, org_id=org_id, action="investor_bonus_paid", actor_email=actor_email,
        user_id=user_id, severity="warning" if source == "manual" else "info",
        bonus_id=bonus_id, source=source, amount=money(amount), note=note,
        summary=f"Bonus {amount:+.2f} USD ({name}) for {who} by {actor_email}")
    noted = f"\nNote: {note}" if note else ""
    if amount > 0:
        title = f"You received a {amount:.2f} USD {name}"
        body = (f"{amount:.2f} USD was paid into your Credit wallet.{noted}\n"
                "Bonus credit can be moved to one of your trading accounts from Transfer.")
    else:
        title = f"A bonus of {-amount:.2f} USD was taken back"
        body = f"{-amount:.2f} USD was taken from your Credit wallet.{noted}"
    await notify(conn, request, org_id, user_id, "bonus", title, body,
                 investor_link(org_id, "bonus"))


async def award_rule_bonus(conn: psycopg.Connection, request: Request, org_id: int,
                           user_id: int, source: str, *, actor_email: str) -> Optional[int]:
    """The signup or kyc rule's bonus, in its own transaction (ledger lock
    first), then audit + notify. Paid at most once per investor; nothing
    while the rule is off. Returns the bonuses id when paid.

    Best effort, like notify: it runs after the join, role change or KYC
    approval has committed, so a failure here is logged and returns None
    rather than answering 500 for a change that already landed. A missed
    bonus is visible in the log and can be granted by hand."""
    try:
        with conn.transaction():
            lock_investor_ledger(conn, org_id, user_id)
            amount = rule_bonus(conn, org_id, source)
            bonus_id = pay_bonus(conn, org_id, user_id, source, amount)
    except Exception:
        logger.exception("%s bonus failed for user %s in org %s", source, user_id, org_id)
        return None
    if bonus_id is not None:
        await announce_bonus(conn, request, org_id=org_id, user_id=user_id, bonus_id=bonus_id,
                             source=source, amount=amount, actor_email=actor_email)
    return bonus_id
```

- [x] **Step 5: The bonus rules routes**

Create `api/src/api/routes/portal_bonus.py`:

```python
# api/src/api/routes/portal_bonus.py
"""Bonuses (client portal phase 4): the org's bonus rules (admin), manual
grants and claw-backs (admin MPIN), and the investor's own bonus history.
Every bonus lands in the Credit wallet through pc.pay_bonus; credit only
ever moves on to a trading account (portal_ledger.TRANSFER_PAIRS)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc
from .portal_admin import parse_min


class RulesBody(BaseModel):
    # Any, validated by parse_rules so every field gets its own message.
    signup_enabled: Any = None
    signup_amount: Any = None
    kyc_enabled: Any = None
    kyc_amount: Any = None
    deposit_enabled: Any = None
    deposit_pct: Any = None
    deposit_cap: Any = None


def rules_json(rules: dict) -> Dict[str, Any]:
    return {"signup_enabled": rules["signup_enabled"],
            "signup_amount": pc.money(rules["signup_amount"]),
            "kyc_enabled": rules["kyc_enabled"], "kyc_amount": pc.money(rules["kyc_amount"]),
            "deposit_enabled": rules["deposit_enabled"],
            "deposit_pct": float(rules["deposit_pct"]),
            "deposit_cap": pc.money(rules["deposit_cap"]),
            "updated_at": pc._iso(rules["updated_at"])}


def parse_deposit_pct(raw: object) -> Decimal:
    """0 <= pct <= 100 with at most three decimals (NUMERIC(6,3)); blank is 0."""
    message = "deposit_pct must be between 0 and 100"
    if raw is None or raw == "":
        return Decimal("0")
    if isinstance(raw, bool):
        raise pc.LedgerError(message)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise pc.LedgerError(message)
    if not value.is_finite() or value < 0 or value > 100:
        raise pc.LedgerError(message)
    if value != value.quantize(Decimal("0.001")):
        raise pc.LedgerError("deposit_pct may have at most three decimals")
    return value


def parse_rules(body: RulesBody) -> dict:
    """The PUT body as column values; LedgerError names the first bad field.
    A rule switched on must pay something."""
    out: dict = {}
    for flag in ("signup_enabled", "kyc_enabled", "deposit_enabled"):
        value = getattr(body, flag)
        if not isinstance(value, bool):
            raise pc.LedgerError(f"{flag} must be true or false")
        out[flag] = value
    for field in ("signup_amount", "kyc_amount"):
        out[field] = parse_min(getattr(body, field), field)
    out["deposit_pct"] = parse_deposit_pct(body.deposit_pct)
    cap = body.deposit_cap
    out["deposit_cap"] = None if cap is None or cap == "" else pc.parse_amount(cap, "deposit_cap")
    for source, field in (("signup", "signup_amount"), ("kyc", "kyc_amount"),
                          ("deposit", "deposit_pct")):
        if out[f"{source}_enabled"] and out[field] == 0:
            raise pc.LedgerError(f"{field} must be above 0 while the {source} rule is on")
    return out


def create_portal_bonus_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-bonus"])

    @router.get("/bonus-rules", response_model=Dict[str, Any])
    async def get_rules(ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return rules_json(pc.bonus_rules(conn, ctx.org_id))

    @router.put("/bonus-rules", response_model=Dict[str, Any])
    async def put_rules(body: RulesBody, ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Applies to events from now on; nothing already done is paid."""
        try:
            rules = parse_rules(body)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        previous = rules_json(pc.bonus_rules(conn, ctx.org_id))
        conn.execute(
            "UPDATE bonus_rules SET signup_enabled = %(signup_enabled)s, "
            "signup_amount = %(signup_amount)s, kyc_enabled = %(kyc_enabled)s, "
            "kyc_amount = %(kyc_amount)s, deposit_enabled = %(deposit_enabled)s, "
            "deposit_pct = %(deposit_pct)s, deposit_cap = %(deposit_cap)s, "
            "updated_by = %(updated_by)s, updated_at = now() WHERE org_id = %(org_id)s",
            {**rules, "updated_by": ctx.user_id, "org_id": ctx.org_id})
        out = rules_json(pc.bonus_rules(conn, ctx.org_id))
        changes = {k: v for k, v in out.items() if k != "updated_at"}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="bonus_rules_changed", actor_email=ctx.user_email,
            user_id=ctx.user_id, previous={k: v for k, v in previous.items() if k != "updated_at"},
            **changes, summary=f"Bonus rules changed by {ctx.user_email}")
        return out

    return router
```

In `api/src/api/main.py`, directly after `app.include_router(create_portal_support_router())` add:

```python
    from .routes.portal_bonus import create_portal_bonus_router
    app.include_router(create_portal_bonus_router())
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_ledger.py tests/test_portal_common.py tests/test_portal_bonus.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add api/src/api/portal_ledger.py api/src/api/portal_common.py api/src/api/routes/portal_bonus.py api/src/api/main.py api/tests/test_portal_ledger.py api/tests/test_portal_common.py api/tests/test_portal_bonus.py
git commit -m "feat(api): bonus rules and pay_bonus -- each rule pays at most once into Credit

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 9: Bonus triggers — signup, KYC, deposit

**Files:**
- Modify: `api/src/api/routes/orgs.py` (`join_org`, `patch_member`)
- Modify: `api/src/api/routes/portal_identity.py` (`decide_kyc`)
- Modify: `api/src/api/routes/portal_admin.py` (`decide_deposit`)
- Modify: `api/tests/test_portal_bonus.py`

**Interfaces:**
- Consumes: `pc.award_rule_bonus`, `pc.rule_bonus`, `pc.pay_bonus`, `pc.announce_bonus` (Task 8); `portal_helpers.kyc_profile`.
- Produces: signup bonus when a member becomes an investor (invite join with role investor; role changed to investor from another role); kyc bonus on KYC approval; deposit bonus on deposit confirmation (`source_id` = deposit id, note `deposit #<id>`, inside the confirmation transaction).

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_bonus.py`:

```python
# ------------------------------------------------------------ triggers


def _invite(client, org_id, role="investor"):
    r = client.post(f"/api/orgs/{org_id}/invites", json={"role": role}, headers=csrf(client))
    return r.json()["token"]


def _join(client, login_as, user, token):
    client.cookies.clear()
    login_as(client, user)
    return client.post("/api/orgs/join", json={"token": token}, headers=csrf(client))


def test_the_signup_bonus_pays_once_on_join_and_on_a_role_change(org_client, make_user,
                                                                 login_as, db):
    client, org_id, _seed = org_client
    assert _rules(client, org_id, signup_enabled=True, signup_amount="50").status_code == 200
    token = _invite(client, org_id)
    joiner = make_user(email="joiner@example.com")
    assert _join(client, login_as, joiner, token).status_code == 200
    assert _credit_entries(db, joiner["id"]) == [("signup", 50.0)]
    client.cookies.clear()
    login_as(client, ADMIN)
    url = f"/api/orgs/{org_id}/members/{joiner['id']}"
    assert client.patch(url, json={"role": "viewer"}, headers=csrf(client)).status_code == 200
    assert client.patch(url, json={"role": "investor"}, headers=csrf(client)).status_code == 200
    assert _credit_entries(db, joiner["id"]) == [("signup", 50.0)]      # once per investor
    promoted = make_user(email="viewer@example.com")
    member(db, org_id, promoted["id"], "viewer")
    r = client.patch(f"/api/orgs/{org_id}/members/{promoted['id']}", json={"role": "investor"},
                     headers=csrf(client))
    assert r.status_code == 200
    assert _credit_entries(db, promoted["id"]) == [("signup", 50.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        notes = conn.execute("SELECT topic, title, link FROM notifications WHERE user_id = %s",
                             (joiner["id"],)).fetchall()
    assert notes == [("bonus", "You received a 50.00 USD welcome bonus",
                      f"/org/{org_id}/invest/bonus")]
    severity, payload, actor = _events(db, org_id, "investor_bonus_paid")[0]
    assert (severity, actor, payload["source"], payload["amount"]) == (
        "info", "joiner@example.com", "signup", 50.0)


def test_no_bonus_while_off_none_back_paid_and_none_for_desk_roles(org_client, make_user,
                                                                   login_as, db):
    client, org_id, _seed = org_client
    token = _invite(client, org_id)
    early = make_user(email="early@example.com")
    assert _join(client, login_as, early, token).status_code == 200
    client.cookies.clear()
    login_as(client, ADMIN)
    _rules(client, org_id, signup_enabled=True, signup_amount="50")
    viewer_token = _invite(client, org_id, role="viewer")
    viewer = make_user(email="viewer@example.com")
    assert _join(client, login_as, viewer, viewer_token).status_code == 200
    assert _credit_entries(db, early["id"]) == [] and _credit_entries(db, viewer["id"]) == []


def test_the_kyc_bonus_pays_once_on_approval(org_client, make_user, db):
    client, org_id, _seed = org_client
    _rules(client, org_id, kyc_enabled=True, kyc_amount="25")
    investor = _investor(make_user, db, org_id)
    rejected = _investor(make_user, db, org_id, email="rej@example.com")
    kyc_profile(db, org_id, investor["id"], status="submitted")
    kyc_profile(db, org_id, rejected["id"], status="submitted")

    def decide(user_id, status, note=None):
        return client.post(f"/api/orgs/{org_id}/kyc/{user_id}/decision",
                           json={"status": status, "note": note}, headers=csrf(client))

    assert decide(investor["id"], "approved").status_code == 200
    assert _credit_entries(db, investor["id"]) == [("kyc", 25.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE kyc_profiles SET status = 'submitted' WHERE user_id = %s",
                     (investor["id"],))
    assert decide(investor["id"], "approved").status_code == 200
    assert _credit_entries(db, investor["id"]) == [("kyc", 25.0)]       # a re-approval pays nothing
    assert decide(rejected["id"], "rejected", "blurry").status_code == 200
    assert _credit_entries(db, rejected["id"]) == []


def test_a_failing_rule_bonus_never_fails_the_join_or_the_approval(org_client, make_user,
                                                                   login_as, db, monkeypatch):
    """award_rule_bonus runs after the primary change committed: a failure is
    logged and the request still answers as if no rule were on."""
    from api import portal_common

    def boom(*_args, **_kwargs):
        raise RuntimeError("ledger down")

    client, org_id, _seed = org_client
    _rules(client, org_id, signup_enabled=True, signup_amount="50", kyc_enabled=True,
           kyc_amount="25")
    token = _invite(client, org_id)
    monkeypatch.setattr(portal_common, "pay_bonus", boom)
    joiner = make_user(email="joiner@example.com")
    r = _join(client, login_as, joiner, token)
    assert r.status_code == 200 and r.json()["role"] == "investor"
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
                            (org_id, joiner["id"])).fetchone() == ("investor",)
    client.cookies.clear()
    login_as(client, ADMIN)
    kyc_profile(db, org_id, joiner["id"], status="submitted")
    r = client.post(f"/api/orgs/{org_id}/kyc/{joiner['id']}/decision",
                    json={"status": "approved", "note": None}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert _credit_entries(db, joiner["id"]) == []
    assert _events(db, org_id, "investor_bonus_paid") == []


def _pending_deposit(db, org_id, user_id, amount, reference):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference) "
            "VALUES (%s, %s, 'crypto', 'USDT on TRC20', %s, %s) RETURNING id",
            (org_id, user_id, Decimal(amount), reference)).fetchone()[0]


def test_the_deposit_bonus_rounds_caps_and_pays_once(org_client, make_user, db):
    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    _rules(client, org_id, deposit_enabled=True, deposit_pct="12.5", deposit_cap="100")
    small = _pending_deposit(db, org_id, uid, "33.33", "d-1")
    mid = _pending_deposit(db, org_id, uid, "500", "d-mid")
    big = _pending_deposit(db, org_id, uid, "5000", "d-2")
    tiny = _pending_deposit(db, org_id, uid, "0.03", "d-3")

    def confirm(dep_id, **extra):
        return client.post(f"/api/orgs/{org_id}/deposits/{dep_id}/decision",
                           json={"status": "confirmed", **extra}, headers=csrf(client))

    assert confirm(small).status_code == 200
    assert confirm(small).status_code == 409          # a double confirm pays nothing more
    # Below the cap, so the base shows: 12.5 % of the credited 400 is 50.00,
    # where the 500 notice amount would have paid 62.50.
    assert confirm(mid, credited_amount="400").status_code == 200
    assert confirm(big, credited_amount="4000").status_code == 200   # capped at 100
    assert confirm(tiny).status_code == 200            # 0.00375 rounds to 0: nothing paid
    assert _credit_entries(db, uid) == [("deposit", 4.17), ("deposit", 50.0),
                                        ("deposit", 100.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute("SELECT source_id, amount, note FROM bonuses WHERE user_id = %s "
                            "ORDER BY id", (uid,)).fetchall()
    assert rows == [(small, Decimal("4.17"), f"deposit #{small}"),
                    (mid, Decimal("50.00"), f"deposit #{mid}"),
                    (big, Decimal("100.00"), f"deposit #{big}")]
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_bonus.py -q -p no:cacheprovider`
Expected: FAIL — no credit rows are written by any trigger.

- [x] **Step 3: Signup on join and on a role change**

In `api/src/api/routes/orgs.py`, change `from fastapi import APIRouter, Depends, HTTPException` to

```python
from fastapi import APIRouter, Depends, HTTPException, Request
```

and add below `from ..ws import broadcaster`:

```python
from .. import portal_common as pc
```

Replace the `join_org` signature and tail:

```python
    @router.post("/join")
    async def join_org(
        body: JoinRequest,
        http_request: Request,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
```

and after the `with conn.transaction():` block (before `return {"org_id": org_id, "role": role}`) add:

```python
        if role == "investor":
            # The welcome bonus, if the org's rule is on: its own transaction
            # (the ledger lock must be the first statement of whatever writes
            # wallet_entries, and this one began with the invite).
            (email,) = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
            await pc.award_rule_bonus(conn, http_request, org_id, user_id, "signup",
                                      actor_email=email)
```

In `patch_member`, add `http_request: Request,` after `body: PatchMemberRequest,`, and replace `return {"user_id": member_user_id, "role": body.role}` with:

```python
        if body.role == "investor" and row[0] != "investor":
            await pc.award_rule_bonus(conn, http_request, ctx.org_id, member_user_id, "signup",
                                      actor_email=ctx.user_email)
        return {"user_id": member_user_id, "role": body.role}
```

- [x] **Step 4: KYC on approval**

In `api/src/api/routes/portal_identity.py`, in `decide_kyc`, directly after the `await pc.notify(...)` call (Task 2) and before `return pid.profile_json(row)`, add:

```python
        if new_status == "approved":
            await pc.award_rule_bonus(conn, http_request, ctx.org_id, user_id, "kyc",
                                      actor_email=ctx.user_email)
```

- [x] **Step 5: Deposit on confirmation**

In `api/src/api/routes/portal_admin.py`, in `decide_deposit`, replace

```python
        linked: Optional[int] = None
        transfer_id: Optional[int] = None
```

with

```python
        linked: Optional[int] = None
        transfer_id: Optional[int] = None
        bonus = Decimal("0")
        bonus_id: Optional[int] = None
```

inside the transaction, at the end of the `if new_status == "confirmed":` block (after the `if linked is not None:` transfer insert), add at the same indentation as `pc.settle(...)`:

```python
                # The deposit rule, on the confirmed (credited) amount: inside
                # this transaction, so the deposit and its bonus land together;
                # bonuses_once_per_deposit makes a replay pay nothing.
                bonus = pc.rule_bonus(conn, ctx.org_id, "deposit", credited)
                bonus_id = pc.pay_bonus(conn, ctx.org_id, investor_id, "deposit", bonus,
                                        source_id=deposit_id, note=f"deposit #{deposit_id}",
                                        created_by=ctx.user_id)
```

and directly after the deposit's `await pc.notify(...)` call, before `return out`, add:

```python
        if bonus_id is not None:
            await pc.announce_bonus(conn, http_request, org_id=ctx.org_id, user_id=investor_id,
                                    bonus_id=bonus_id, source="deposit", amount=bonus,
                                    actor_email=ctx.user_email, note=f"deposit #{deposit_id}")
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_bonus.py tests/test_orgs.py tests/test_portal_kyc.py tests/test_portal_deposits.py tests/test_portal_notify_callers.py -q -p no:cacheprovider`
Expected: PASS (with every rule off, the existing flows write no bonus).

- [x] **Step 7: Commit**

```bash
git add api/src/api/routes/orgs.py api/src/api/routes/portal_identity.py api/src/api/routes/portal_admin.py api/tests/test_portal_bonus.py
git commit -m "feat(api): signup, verification and deposit bonuses paid by the org's rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 10: Manual grant and claw-back; the investor's bonus history; alert rules

**Files:**
- Modify: `api/src/api/routes/portal_admin.py` (extract `parse_signed_amount`)
- Modify: `api/src/api/routes/portal_bonus.py`
- Modify: `api/src/api/alerts.py`, `api/src/api/telegram.py`
- Modify: `api/tests/test_portal_bonus.py`

**Interfaces:**
- Consumes: `mpin_core.require_mpin`; `portal_admin._require_investor`, `SIGNED_AMOUNT`; `portal_investor._parse_day`; `pc.wallet_figures`, `pc.pay_bonus`, `pc.announce_bonus`, `pc.lock_investor_ledger`.
- Produces: `portal_admin.parse_signed_amount(raw) -> Decimal` (used by `post_adjustment` and the grant); in `portal_bonus.py`: `GrantBody`, `BONUS_COLS`, `bonus_json(row)`, routes `POST investors/{user_id}/bonuses` and `GET investor/bonuses?source=&from=&to=`; `("control", "warning", "investor_bonus_paid")` in `ALERT_RULES` and `TELEGRAM_RULES`.

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_bonus.py`:

```python
# ------------------------------------------------------------ manual + history


def test_manual_grant_and_the_claw_back_floor(org_client, make_user, db):
    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    url = f"/api/orgs/{org_id}/investors/{uid}/bonuses"

    def grant(amount, note="Promo", mpin="123456"):
        return client.post(url, json={"amount": amount, "note": note, "mpin": mpin},
                           headers=csrf(client))

    assert grant("20", mpin="000000").status_code == 401
    r = grant("20")
    assert r.status_code == 201, r.text
    assert {k: r.json()[k] for k in ("source", "source_id", "amount", "note", "currency")} == {
        "source": "manual", "source_id": None, "amount": 20.0, "note": "Promo", "currency": "USD"}
    r = grant("-25")
    assert r.status_code == 400
    assert r.json()["detail"] == "a claw-back cannot take the Credit wallet below zero (available 20.00)"
    assert grant("-20", note="Reversed").status_code == 201
    assert _credit_entries(db, uid) == [("manual", 20.0), ("manual", -20.0)]
    for amount, note, detail in (
            ("0", "x", "amount must not be zero"),
            ("--5", "x", "amount must be a signed number with at most two decimals, e.g. -25.00"),
            ("5", " ", "note is required")):
        r = grant(amount, note=note)
        assert (r.status_code, r.json()["detail"]) == (400, detail)
    r = client.post(f"/api/orgs/{org_id}/investors/999/bonuses",
                    json={"amount": "5", "note": "x", "mpin": "123456"}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    assert [e[0] for e in _events(db, org_id, "investor_bonus_paid")] == ["warning", "warning"]
    with psycopg.connect(db, autocommit=True) as conn:
        titles = [r[0] for r in conn.execute(
            "SELECT title FROM notifications WHERE user_id = %s ORDER BY id", (uid,)).fetchall()]
    assert titles == ["You received a 20.00 USD bonus", "A bonus of 20.00 USD was taken back"]


def test_the_adjustment_route_keeps_its_amount_rules(org_client, make_user, db):
    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    url = f"/api/orgs/{org_id}/investors/{uid}/adjustments"
    for amount, detail in (("0", "amount must not be zero"), (None, "amount is required, e.g. 250.00"),
                           ("+-5", "amount must be a signed number with at most two decimals, "
                                   "e.g. -25.00")):
        r = client.post(url, json={"wallet": "main", "amount": amount, "note": "x",
                                   "mpin": "123456"}, headers=csrf(client))
        assert (r.status_code, r.json()["detail"]) == (400, detail)
    r = client.post(url, json={"wallet": "main", "amount": "-12.5", "note": "x", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 201 and r.json()["amount"] == -12.5


def test_the_investor_reads_their_own_bonus_history(org_client, make_user, login_as, db):
    client, org_id, _seed = org_client
    investor = _investor(make_user, db, org_id)
    other = _investor(make_user, db, org_id, email="other@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        for user_id, source, source_id, amount, when in (
                (investor["id"], "signup", None, 50, "2026-09-01T12:00:00Z"),
                (investor["id"], "deposit", 7, 10, "2026-09-15T12:00:00Z"),
                (investor["id"], "manual", None, -5, "2026-09-20T12:00:00Z"),
                (other["id"], "signup", None, 50, "2026-09-01T12:00:00Z")):
            conn.execute(
                "INSERT INTO bonuses (org_id, user_id, source, source_id, amount, note, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (org_id, user_id, source, source_id, amount, f"{source} note", when))
    client.cookies.clear()
    login_as(client, investor)
    url = f"/api/orgs/{org_id}/investor/bonuses"
    rows = client.get(url).json()
    assert [(b["source"], b["amount"]) for b in rows] == [
        ("manual", -5.0), ("deposit", 10.0), ("signup", 50.0)]
    assert rows[1] == {"id": rows[1]["id"], "source": "deposit", "source_id": 7, "amount": 10.0,
                       "note": "deposit note", "created_at": rows[1]["created_at"],
                       "currency": "USD"}
    assert [b["source"] for b in client.get(url + "?source=signup").json()] == ["signup"]
    assert [b["source"] for b in client.get(url + "?from=2026-09-10&to=2026-09-15").json()] == [
        "deposit"]
    r = client.get(url + "?source=bogus")
    assert r.status_code == 400
    assert r.json()["detail"] == "source must be one of signup, kyc, deposit, manual"
    r = client.get(url + "?from=yesterday")
    assert r.status_code == 400 and r.json()["detail"] == "from must be a date (YYYY-MM-DD)"


def test_a_bonus_paid_by_hand_reaches_both_alerters():
    from api.alerts import ALERT_RULES
    from api.telegram import TELEGRAM_RULES
    assert ("control", "warning", "investor_bonus_paid") in ALERT_RULES
    assert ("control", "warning", "investor_bonus_paid") in TELEGRAM_RULES
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_bonus.py -q -p no:cacheprovider`
Expected: FAIL — 404/405 on `investors/{id}/bonuses` and `investor/bonuses`; the alert rule is missing. (`test_the_adjustment_route_keeps_its_amount_rules` already passes: it pins today's behaviour for the refactor in Step 3.)

- [x] **Step 3: Extract `parse_signed_amount`**

In `api/src/api/routes/portal_admin.py`, add directly after `parse_pct`:

```python
def parse_signed_amount(raw: object) -> Decimal:
    """A hand-posted amount (adjustments, manual bonuses): one optional sign,
    at most two decimals, never zero. A run of signs ('+-5', '--5') is never
    read as either sign. Raises HTTPException 400."""
    text = "" if raw is None or isinstance(raw, bool) else str(raw).strip()
    if text and not SIGNED_AMOUNT.fullmatch(text):
        raise HTTPException(status_code=400, detail=(
            "amount must be a signed number with at most two decimals, e.g. -25.00"))
    if text and Decimal(text) == 0:
        raise HTTPException(status_code=400, detail="amount must not be zero")
    try:
        magnitude = pc.parse_amount(text.lstrip("+-") if text else raw)
    except pc.LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return -magnitude if text.startswith("-") else magnitude
```

In `post_adjustment`, replace everything from `raw = "" if body.amount is None …` through `amount = -magnitude if negative else magnitude` with:

```python
        amount = parse_signed_amount(body.amount)
        try:
            note = pc.clean_text(body.note, "note", max_len=500)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
```

- [x] **Step 4: The grant and history routes**

In `api/src/api/routes/portal_bonus.py`, change the imports to:

```python
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc
from .portal_admin import _require_investor, parse_min, parse_signed_amount
from .portal_investor import _parse_day
```

add below `RulesBody`:

```python
class GrantBody(BaseModel):
    amount: Any = None
    note: Any = None
    mpin: Any = None


BONUS_COLS = "id, source, source_id, amount, note, created_at"


def bonus_json(row) -> Dict[str, Any]:
    bonus_id, source, source_id, amount, note, created_at = row
    return {"id": bonus_id, "source": source,
            "source_id": int(source_id) if source_id is not None else None,
            "amount": pc.money(amount), "note": note, "created_at": pc._iso(created_at),
            "currency": pc.CURRENCY}
```

and before `return router` add:

```python
    @router.post("/investors/{user_id}/bonuses", status_code=201, response_model=Dict[str, Any])
    async def grant_bonus(user_id: int, body: GrantBody, http_request: Request,
                          ctx: OrgContext = Depends(require_org_role("admin")),
                          conn: psycopg.Connection = Depends(get_conn)):
        """A bonus (positive) or claw-back (negative) on the Credit wallet,
        confirmed with the ADMIN's own MPIN like a ledger adjustment. A
        claw-back may not take the Credit wallet's available figure below
        zero (credit on hold for an open transfer is already spoken for)."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        _require_investor(conn, ctx.org_id, user_id)
        amount = parse_signed_amount(body.amount)
        try:
            note = pc.clean_text(body.note, "note", max_len=500)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            if amount < 0:
                available = pc.wallet_figures(conn, ctx.org_id, user_id)["credit"]["available"]
                if available + amount < 0:
                    raise HTTPException(status_code=400, detail=(
                        "a claw-back cannot take the Credit wallet below zero "
                        f"(available {available:.2f})"))
            bonus_id = pc.pay_bonus(conn, ctx.org_id, user_id, "manual", amount, note=note,
                                    created_by=ctx.user_id)
            row = conn.execute(f"SELECT {BONUS_COLS} FROM bonuses WHERE id = %s",
                               (bonus_id,)).fetchone()
        await pc.announce_bonus(conn, http_request, org_id=ctx.org_id, user_id=user_id,
                                bonus_id=bonus_id, source="manual", amount=amount,
                                actor_email=ctx.user_email, note=note)
        return bonus_json(row)

    @router.get("/investor/bonuses", response_model=List[Dict[str, Any]])
    async def my_bonuses(source: Optional[str] = None,
                         date_from: Optional[str] = Query(None, alias="from"),
                         date_to: Optional[str] = Query(None, alias="to"),
                         ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        if source is not None and source not in pc.BONUS_SOURCES:
            raise HTTPException(status_code=400,
                                detail="source must be one of signup, kyc, deposit, manual")
        try:
            start = _parse_day(date_from, "from")
            end = _parse_day(date_to, "to")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        where = ["org_id = %s", "user_id = %s"]
        params: List[Any] = [ctx.org_id, ctx.user_id]
        if source is not None:
            where.append("source = %s")
            params.append(source)
        if start is not None:
            where.append("created_at >= %s::date")
            params.append(start)
        if end is not None:
            where.append("created_at < %s::date + interval '1 day'")
            params.append(end)
        # ponytail: LIMIT 500, page when an investor has more bonuses than that
        rows = conn.execute(f"SELECT {BONUS_COLS} FROM bonuses WHERE {' AND '.join(where)} "
                            "ORDER BY id DESC LIMIT 500", params).fetchall()
        return [bonus_json(r) for r in rows]
```

- [x] **Step 5: The alert rules**

In `api/src/api/alerts.py`, in `ALERT_RULES`, directly after the `investor_ledger_adjusted` entry add:

```python
    ("control", "warning", "investor_bonus_paid"): "Investor bonus paid by hand",
```

In `api/src/api/telegram.py`, in `TELEGRAM_RULES`, directly after `("control", "warning", "investor_ledger_adjusted"),` add:

```python
    ("control", "warning", "investor_bonus_paid"),
```

(Only manual grants are warnings; the rules' own bonuses are info and stay out of both channels.)

- [x] **Step 6: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_bonus.py tests/test_portal_summary.py tests/test_alerts.py tests/test_telegram.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add api/src/api/routes/portal_admin.py api/src/api/routes/portal_bonus.py api/src/api/alerts.py api/src/api/telegram.py api/tests/test_portal_bonus.py
git commit -m "feat(api): manual bonuses and claw-backs with the admin MPIN; the investor's bonus history

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 11: Credit -> trading account transfers; bonus credit never moves back out

**Files:**
- Modify: `api/src/api/portal_ledger.py` (`TRANSFER_PAIRS`, `transfer_pair` docstring)
- Modify: `api/src/api/portal_common.py` (`credit_funded`, `account_movable` after `net_funded`)
- Modify: `api/src/api/routes/portal_investor.py` (`request_transfer` cap, `summary` `account_available`)
- Modify: `api/src/api/routes/portal_admin.py` (`decide_transfer` re-checks the cap on approve/done)
- Modify: `api/tests/test_portal_ledger.py`, `api/tests/test_portal_transfers.py`

**Interfaces:**
- Consumes: `routes/portal_investor.request_transfer` and `routes/portal_admin.decide_transfer` (they already hold and settle any source wallet); `pc.open_account_transfers_out`, `pc.floor_cents`; `portal_helpers.approved_destination`.
- Produces: `TRANSFER_PAIRS` = main->account, account->main, pamm->main, social->main, credit->account. `pc.credit_funded(conn, org_id, user_id, account_id) -> Decimal` (sum of `done` transfers with `source_wallet = 'credit'` into that account by that investor). `pc.account_movable(conn, org_id, user_id, account_id, equity) -> Decimal` = `floor_cents(equity - open_account_transfers_out - credit_funded)`, never below 0. The account -> wallet cap in `request_transfer`, the summary's per-account `account_available`, and a new re-check in `decide_transfer` (approve or done of an account -> wallet transfer: `equity_at_request - credit_funded`, 409 when exceeded) all exclude bonus credit. Principal credit stays at the broker; profits made on it may leave.

- [x] **Step 1: Write the failing tests**

In `api/tests/test_portal_ledger.py`, rename `test_the_four_allowed_pairs` to `test_the_allowed_pairs` and add to its parametrize list:

```python
        ({"kind": "wallet", "wallet": "credit"}, {"kind": "account", "account_id": 1001},
         ("credit", "account")),
```

and add to the refusal list of `test_everything_else_is_refused_with_one_message`:

```python
        ({"kind": "wallet", "wallet": "credit"}, {"kind": "wallet", "wallet": "pamm"}),
        ({"kind": "account", "account_id": 1001}, {"kind": "wallet", "wallet": "credit"}),
```

In `api/tests/test_portal_transfers.py`, change `from portal_helpers import credit, csrf, link` to `from portal_helpers import approved_destination, credit, csrf, link`, add `(A(1001), W("credit"))` to the `test_disallowed_pairs_are_refused` parametrize list, and append:

```python
def test_credit_moves_only_to_a_trading_account(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="100")
    credit(db, org_id, investor["id"], Decimal("30"), wallet="credit")
    r = _transfer(client, org_id, W("credit"), A(1001), "40")
    assert r.status_code == 400 and r.json()["detail"] == "amount exceeds what is available (30.00)"
    r = _transfer(client, org_id, W("credit"), A(1001), "30")
    assert r.status_code == 201 and r.json()["status"] == "requested"
    tr_id = r.json()["id"]
    assert _figures(db, org_id, investor["id"], "credit") == {
        "balance": 30.0, "on_hold": 30.0, "available": 0.0}
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _decide(client, org_id, tr_id, "done").status_code == 200
    assert _entries(db, org_id, investor["id"])[-1] == ("credit", -30.0, "transfer", "transfers", tr_id)
    assert _figures(db, org_id, investor["id"], "credit") == {
        "balance": 0.0, "on_hold": 0.0, "available": 0.0}
    # Credit is never withdrawn: a withdrawal only ever debits My wallet.
    client.cookies.clear()
    login_as(client, investor)
    credit(db, org_id, investor["id"], Decimal("30"), wallet="credit")
    dest = approved_destination(db, org_id, investor["id"])
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals",
                    json={"destination_id": dest, "amount": "10", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 400 and r.json()["detail"] == "amount exceeds what is available (0.00)"


def _credit_funded(db, org_id, user_id, account_id):
    from api.portal_common import credit_funded
    with psycopg.connect(db, autocommit=True) as conn:
        return float(credit_funded(conn, org_id, user_id, account_id))


def test_bonus_credit_moved_into_an_account_cannot_move_out(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="1000")
    credit(db, org_id, investor["id"], Decimal("100"), wallet="credit")
    tr_id = _transfer(client, org_id, W("credit"), A(1001), "100").json()["id"]
    assert _credit_funded(db, org_id, investor["id"], 1001) == 0.0, "requested is not funded"
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _decide(client, org_id, tr_id, "done").status_code == 200
    assert _credit_funded(db, org_id, investor["id"], 1001) == 100.0
    assert _credit_funded(db, org_id, investor["id"], 1002) == 0.0, "only that account"
    client.cookies.clear()
    login_as(client, investor)
    (account,) = client.get(f"/api/orgs/{org_id}/investor/summary").json()["accounts"]
    assert account["account_available"] == 900.0, "equity 1000 minus the 100 bonus credit"
    r = _transfer(client, org_id, A(1001), W("main"), "950")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the account's available equity (900.00)"
    assert _transfer(client, org_id, A(1001), W("main"), "900").status_code == 201
    # Profit made on top of the credit may leave: equity 1300 less the 900
    # already requested less the 100 credit leaves 300.
    _state(client, {1001: {"balance": 1300.0, "equity": 1300.0, "open_pnl": 0.0,
                           "positions": []}})
    (account,) = client.get(f"/api/orgs/{org_id}/investor/summary").json()["accounts"]
    assert account["account_available"] == 300.0
    r = _transfer(client, org_id, A(1001), W("main"), "300.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the account's available equity (300.00)"
    assert _transfer(client, org_id, A(1001), W("main"), "300").status_code == 201


def test_account_available_never_goes_below_zero(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="100")
    credit(db, org_id, investor["id"], Decimal("100"), wallet="credit")
    tr_id = _transfer(client, org_id, W("credit"), A(1001), "100").json()["id"]
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _decide(client, org_id, tr_id, "done").status_code == 200
    client.cookies.clear()
    login_as(client, investor)
    # The account lost money: equity 60 is below the 100 credit funded.
    _state(client, {1001: {"balance": 60.0, "equity": 60.0, "open_pnl": 0.0, "positions": []}})
    (account,) = client.get(f"/api/orgs/{org_id}/investor/summary").json()["accounts"]
    assert account["account_available"] == 0.0
    r = _transfer(client, org_id, A(1001), W("main"), "0.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the account's available equity (0.00)"


def test_credit_funded_after_the_request_blocks_the_decision(org_client, make_user, login_as, db):
    """The desk funds the broker credit before marking the credit transfer
    done, so equity can include the credit while credit_funded is still 0.
    The decision re-checks against the equity seen at request time."""
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="1000")
    credit(db, org_id, investor["id"], Decimal("100"), wallet="credit")
    credit_id = _transfer(client, org_id, W("credit"), A(1001), "100").json()["id"]
    r = _transfer(client, org_id, A(1001), W("main"), "1000")
    assert r.status_code == 201, "credit not yet funded: the whole equity is movable"
    out_id = r.json()["id"]
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _decide(client, org_id, credit_id, "done").status_code == 200
    r = _decide(client, org_id, out_id, "approved")
    assert r.status_code == 409
    assert r.json()["detail"] == ("bonus credit cannot leave the account (at most 900.00 "
                                  "may move out); reject this transfer instead")
    r = _decide(client, org_id, out_id, "done")
    assert r.status_code == 409 and "900.00" in r.json()["detail"]
    r = _decide(client, org_id, out_id, "rejected", "bonus credit stays")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _entries(db, org_id, investor["id"])[-1][0] == "credit", "nothing reached main"


def test_a_decision_within_the_cap_is_unaffected(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="1000")
    credit(db, org_id, investor["id"], Decimal("100"), wallet="credit")
    credit_id = _transfer(client, org_id, W("credit"), A(1001), "100").json()["id"]
    out_id = _transfer(client, org_id, A(1001), W("main"), "900").json()["id"]
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _decide(client, org_id, credit_id, "done").status_code == 200
    assert _decide(client, org_id, out_id, "approved").status_code == 200
    r = _decide(client, org_id, out_id, "done")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert _entries(db, org_id, investor["id"])[-1] == ("main", 900.0, "transfer", "transfers",
                                                        out_id)
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_ledger.py tests/test_portal_transfers.py -q -p no:cacheprovider`
Expected: FAIL — `that transfer is not allowed` for credit -> account, then `ImportError: cannot import name 'credit_funded'`.

- [x] **Step 3: Allow the pair**

In `api/src/api/portal_ledger.py`, replace

```python
TRANSFER_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("main", "account"), ("account", "main"), ("pamm", "main"), ("social", "main"),
})
```

with

```python
# Bonus credit moves only to a trading account: never to My wallet, never
# withdrawn (withdrawals only ever debit main).
TRANSFER_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("main", "account"), ("account", "main"), ("pamm", "main"), ("social", "main"),
    ("credit", "account"),
})
```

and in `transfer_pair`'s docstring replace `(credit never moves in phase 1)` with `(credit moves only to a trading account)`.

- [x] **Step 4: Keep bonus credit inside the account**

In `api/src/api/portal_common.py`, directly after the `net_funded` function (before `# ------------------------------------------------------------ locking`), add:

```python
def credit_funded(conn: psycopg.Connection, org_id: int, user_id: int,
                  account_id: int) -> Decimal:
    """Bonus credit this investor moved into this account: done transfers
    from the Credit wallet. The desk funds them as broker credit; that
    principal never moves back out to a wallet (profit made on it may)."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transfers WHERE org_id = %s AND user_id = %s "
        "AND status = 'done' AND source_kind = 'wallet' AND source_wallet = 'credit' "
        "AND target_kind = 'account' AND target_account_id = %s",
        (org_id, user_id, account_id)).fetchone()
    return Decimal(total)


def account_movable(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int,
                    equity: Decimal) -> Decimal:
    """What may still move from the account to a wallet: equity less open
    account->wallet transfers less the bonus credit funded into it, floored
    to the cent and never below zero."""
    movable = floor_cents(equity - open_account_transfers_out(conn, org_id, user_id, account_id)
                          - credit_funded(conn, org_id, user_id, account_id))
    return max(movable, Decimal("0.00"))
```

and in the same file replace the import line Task 8 left

```python
from .portal_ledger import WALLETS, available, balance, clean_text, deposit_bonus, holds, money
```

with

```python
from .portal_ledger import (WALLETS, available, balance, clean_text, deposit_bonus,
                            floor_cents, holds, money)
```

In `api/src/api/routes/portal_investor.py` `request_transfer`, replace

```python
                if equity is not None:
                    account_available = pc.floor_cents(
                        equity - pc.open_account_transfers_out(
                            conn, ctx.org_id, ctx.user_id, account_id))
                    if amount > account_available:
```

with

```python
                if equity is not None:
                    # Bonus credit funded into the account never leaves it.
                    account_available = pc.account_movable(
                        conn, ctx.org_id, ctx.user_id, account_id, equity)
                    if amount > account_available:
```

In the same file's summary route, replace

```python
            if account_equity is not None:
                available = pc.floor_cents(account_equity - pc.open_account_transfers_out(
                    conn, ctx.org_id, ctx.user_id, account_id))
```

with

```python
            if account_equity is not None:
                available = pc.account_movable(conn, ctx.org_id, ctx.user_id, account_id,
                                               account_equity)
```

In `api/src/api/routes/portal_admin.py` `decide_transfer`, replace

```python
        current = conn.execute(
            "SELECT status, user_id, source_kind, source_wallet, source_account_id, "
            "target_kind, target_wallet, target_account_id, amount FROM transfers "
            "WHERE id = %s AND org_id = %s", (tr_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Transfer not found")
        (status_now, user_id, source_kind, source_wallet, source_account, target_kind,
         target_wallet, target_account, amount) = current
```

with

```python
        current = conn.execute(
            "SELECT status, user_id, source_kind, source_wallet, source_account_id, "
            "target_kind, target_wallet, target_account_id, amount, equity_at_request "
            "FROM transfers WHERE id = %s AND org_id = %s", (tr_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Transfer not found")
        (status_now, user_id, source_kind, source_wallet, source_account, target_kind,
         target_wallet, target_account, amount, equity_at_request) = current
```

and, in the same function, replace

```python
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            if new_status == "done":
                # Done straight from requested also records the decision;
```

with

```python
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            # Bonus credit may have been funded into the account after this
            # request was checked (the desk funds the broker credit, then
            # marks the credit transfer done). Re-check against the equity
            # seen at request time, never the live equity: by `done` the
            # desk has already taken the money out at the broker.
            if (new_status in ("approved", "done") and source_kind == "account"
                    and source_account is not None and equity_at_request is not None):
                cap = max(pc.floor_cents(Decimal(equity_at_request) - pc.credit_funded(
                    conn, ctx.org_id, user_id, source_account)), Decimal("0.00"))
                if amount > cap:
                    raise HTTPException(
                        status_code=409,
                        detail=f"bonus credit cannot leave the account (at most {cap:.2f} "
                               "may move out); reject this transfer instead")
            if new_status == "done":
                # Done straight from requested also records the decision;
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_ledger.py tests/test_portal_transfers.py tests/test_portal_withdrawals.py tests/test_portal_multi_account.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: PASS (the summary and multi-account `account_available` figures are unchanged: they move no credit).

- [x] **Step 6: Commit**

```bash
git add api/src/api/portal_ledger.py api/src/api/portal_common.py api/src/api/routes/portal_investor.py api/src/api/routes/portal_admin.py api/tests/test_portal_ledger.py api/tests/test_portal_transfers.py
git commit -m "feat(api): bonus credit transfers to a trading account and never moves back out

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 12: RBAC matrix rows; the full API suite

**Files:**
- Modify: `api/tests/test_rbac_matrix.py`

**Interfaces:**
- Consumes: every route of Tasks 3-10.
- Produces: one matrix row per new org route; the fixture seeds ticket subjects 1 (`Deposits`) and 2 (`Spare`), the investor's ticket 1 with one message, and one notification per role (`users[role]["note"]`), substituted for `{note}` per caller.

- [x] **Step 1: Add the rows**

In `api/tests/test_rbac_matrix.py`, append to `MATRIX` (before its closing `]`):

```python
    # ---- phase 4, any member (each caller's OWN notification is {note})
    ("GET",    "notifications",                  None,                          "investor"),
    ("GET",    "notifications/unread-count",     None,                          "investor"),
    ("POST",   "notifications/read-all",         None,                          "investor"),
    ("POST",   "notifications/{note}/read",      None,                          "investor"),
    ("GET",    "notification-prefs",             None,                          "investor"),
    ("PUT",    "notification-prefs",             {"money": True, "identity": True,
                                                  "support": True, "bonus": True}, "investor"),
    # ---- phase 4, investor side
    ("GET",    "investor/ticket-subjects",       None,                          "investor_only"),
    ("GET",    "investor/tickets",               None,                          "investor_only"),
    ("POST",   "investor/tickets",               {"subject_id": 1, "body": "matrix"}, "investor_only"),
    ("GET",    "investor/tickets/1",             None,                          "investor_only"),
    ("POST",   "investor/tickets/1/messages",    {"body": "matrix"},            "investor_only"),
    ("POST",   "investor/tickets/1/close",       None,                          "investor_only"),
    ("GET",    "investor/bonuses",               None,                          "investor_only"),
    # ---- phase 4, admin side
    ("GET",    "tickets",                        None,                          "admin"),
    ("GET",    "tickets/1",                      None,                          "admin"),
    ("POST",   "tickets/1/messages",             {"body": "matrix"},            "admin"),
    ("POST",   "tickets/1/close",                None,                          "admin"),
    ("GET",    "ticket-subjects",                None,                          "admin"),
    ("POST",   "ticket-subjects",                {"label": "Matrix"},           "admin"),
    ("PATCH",  "ticket-subjects/1",              {"sort": 1},                   "admin"),
    ("DELETE", "ticket-subjects/2",              None,                          "admin"),
    ("GET",    "bonus-rules",                    None,                          "admin"),
    ("PUT",    "bonus-rules",                    {"signup_enabled": False, "signup_amount": "0",
                                                  "kyc_enabled": False, "kyc_amount": "0",
                                                  "deposit_enabled": False, "deposit_pct": "0",
                                                  "deposit_cap": None},         "admin"),
    ("POST",   "investors/{investor}/bonuses",   {"amount": "1", "note": "matrix",
                                                  "mpin": MPIN},                "admin"),
```

- [x] **Step 2: Seed the rows the new routes aim at**

In `matrix_org`, at the end of the `with psycopg.connect(db, autocommit=True) as conn:` block (after the transfer insert), add:

```python
        conn.execute("INSERT INTO ticket_subjects (org_id, label) VALUES (%s, 'Deposits'), "
                     "(%s, 'Spare')", (org_id, org_id))                          # ids 1, 2
        conn.execute("INSERT INTO tickets (org_id, user_id, subject_id, subject_label) "
                     "VALUES (%s, %s, 1, 'Deposits')", (org_id, investor_id))    # id 1
        conn.execute("INSERT INTO ticket_messages (ticket_id, org_id, author_id, from_desk, body) "
                     "VALUES (1, %s, %s, false, 'Help')", (org_id, investor_id))
        # A notification is only ever its owner's: each role gets its own,
        # substituted for {note} per caller.
        for role in ROLES:
            (users[role]["note"],) = conn.execute(
                "INSERT INTO notifications (org_id, user_id, topic, title, body) "
                "VALUES (%s, %s, 'money', 'Matrix', 'Body') RETURNING id",
                (org_id, users[role]["id"])).fetchone()
```

and extend the module docstring's list of seeded rows: after `…and the investor's open account request 1.` add ` Phase 4 adds ticket subjects 1 and 2, the investor's ticket 1, and one notification per role (`{note}` in a path is the CALLER's own notification).`

- [x] **Step 3: Substitute `{note}` per caller**

Replace the whole body of `test_role_thresholds` with:

```python
def test_role_thresholds(matrix_org, login_as, method, tail, body, min_role):
    client, org_id, users, outsider = matrix_org
    tail = tail.replace("{investor}", str(users["investor"]["id"]))

    def own(role):
        """The tail as `role` calls it: {note} is that role's notification."""
        return tail.replace("{note}", str(users[role]["note"]))

    # Anonymous: always 401 (or 403 from CSRF middleware on mutations — both
    # prove denial before any org logic).
    client.cookies.clear()
    r = _call(client, method, org_id, own("investor"), body)
    assert r.status_code in (401, 403), f"anonymous got {r.status_code}"

    # Non-member: 404 — org existence never leaks.
    login_as(client, outsider)
    r = _call(client, method, org_id, own("investor"), body)
    assert r.status_code == 404, f"outsider got {r.status_code}"

    # DELETE rows are destructive — only probe DENIED roles for them, and
    # prove the allowed role separately in test_destructive_rows_allowed to
    # keep the fixture intact per param.
    destructive = (method == "DELETE")
    for role in ROLES:
        allowed = (role == "investor" if min_role == "investor_only"
                   else RANK[role] >= RANK[min_role])
        if destructive and allowed:
            continue
        login_as(client, users[role])
        r = _call(client, method, org_id, own(role), body)
        if allowed:
            assert r.status_code not in (401, 403, 404), \
                f"{role} should pass {method} {tail}, got {r.status_code}"
        else:
            assert r.status_code == 403, \
                f"{role} should be 403 on {method} {tail}, got {r.status_code}"
```

In `test_a_half_session_is_refused_on_every_matrix_route`, replace

```python
        tail = tail.replace("{investor}", str(users["investor"]["id"]))
```

with

```python
        tail = (tail.replace("{investor}", str(users["investor"]["id"]))
                    .replace("{note}", str(users["admin"]["note"])))
```

In `test_destructive_rows_allowed`, directly before `r = _call(client, "DELETE", org_id, "", None)`, add:

```python
    r = _call(client, "DELETE", org_id, "ticket-subjects/2", None)
    assert r.status_code == 204
```

- [x] **Step 4: Run the matrix**

Run: `"$PY" -m pytest tests/test_rbac_matrix.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 5: Full API suite**

Run: `"$PY" -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 known `test_events_ws.py` errors and the 1 EA-download CRLF failure.

- [x] **Step 6: Commit**

```bash
git add api/tests/test_rbac_matrix.py
git commit -m "test: RBAC matrix rows for the phase 4 routes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 13: Dashboard foundation — types, `lib/engagement.ts`, fixtures

**Files:**
- Modify: `dashboard/src/lib/types.ts`
- Create: `dashboard/src/lib/engagement.ts`, `dashboard/src/lib/engagement.test.ts`
- Modify: `dashboard/src/test/portalFixtures.ts`, `dashboard/src/test/portalFixtures.test.ts`

**Interfaces:**
- Consumes: the API shapes of Tasks 3-10 (interfaces doc, "Shapes").
- Produces: the phase 4 types, `RequestsSummary.tickets`; `lib/engagement.ts` (`TOPICS`, `TOPIC_LABELS`, `TOPIC_EMAIL_LABELS`, `TICKET_STATUS_LABELS`, `TICKET_STATUS_TONES`, `BONUS_SOURCES`, `BONUS_SOURCE_LABELS`, `IMAGE_ACCEPT`, `MAX_IMAGES`, `TEXTAREA` (the one textarea class the phase 4 forms share), `safeLink`, `ticketsQuery`); fixtures `notificationFixture`, `ticketFixture`, `ticketMessageFixture`, `threadFixture`, `subjectFixture`, `bonusFixture`, `bonusRulesFixture`.

- [x] **Step 1: Write the failing tests**

Create `dashboard/src/lib/engagement.test.ts`:

```ts
import { expect, test } from 'vitest'
import {
  BONUS_SOURCE_LABELS, TEXTAREA, TICKET_STATUS_LABELS, TOPIC_EMAIL_LABELS, TOPICS, safeLink, ticketsQuery,
} from './engagement'

test('safeLink keeps our own paths and drops anything else', () => {
  expect(safeLink('/org/1/invest/deposit')).toBe('/org/1/invest/deposit')
  expect(safeLink(null)).toBeNull()
  expect(safeLink('https://evil.example')).toBeNull()
  expect(safeLink('//evil.example/x')).toBeNull()
})

test('ticketsQuery adds only the filters that are set', () => {
  expect(ticketsQuery('investor/tickets', 'all', '')).toBe('investor/tickets')
  expect(ticketsQuery('tickets', 'closed', '  wire 50% ')).toBe('tickets?status=closed&q=wire+50%25')
  expect(ticketsQuery('tickets', 'all', 'usdt')).toBe('tickets?q=usdt')
})

test('the shared textarea class stays on palette tokens', () => {
  expect(TEXTAREA).toBe('w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink')
})

test('every topic, status and source has its words', () => {
  expect(TOPICS).toEqual(['money', 'identity', 'support', 'bonus'])
  expect(Object.keys(TOPIC_EMAIL_LABELS)).toEqual(TOPICS)
  expect(TICKET_STATUS_LABELS).toEqual({ new: 'New', open: 'Open', closed: 'Closed' })
  expect(Object.keys(BONUS_SOURCE_LABELS)).toEqual(['signup', 'kyc', 'deposit', 'manual'])
})
```

In `dashboard/src/test/portalFixtures.test.ts`, add `bonusFixture, bonusRulesFixture, notificationFixture, subjectFixture, threadFixture, ticketFixture,` to the import list and append:

```ts
test('the phase 4 builders give complete rows and take overrides', () => {
  expect(notificationFixture({ id: 9 })).toMatchObject({ id: 9, topic: 'money', read_at: null })
  expect(ticketFixture().last_from_desk).toBe(false)
  expect(ticketFixture().waiting_on_desk).toBe(true)
  expect(threadFixture().messages).toHaveLength(1)
  expect(threadFixture({ status: 'closed' }).status).toBe('closed')
  expect(subjectFixture({ enabled: false }).enabled).toBe(false)
  expect(bonusFixture().source).toBe('deposit')
  expect(bonusRulesFixture().deposit_cap).toBe(100)
})
```

- [x] **Step 2: Run the tests to verify they fail**

Run (from `dashboard/`): `npx vitest run src/lib/engagement.test.ts src/test/portalFixtures.test.ts`
Expected: FAIL — `./engagement` does not exist; the fixtures are not exported.

- [x] **Step 3: Types**

In `dashboard/src/lib/types.ts`, change the `RequestsSummary` interface to

```ts
export interface RequestsSummary {
  deposits: number; withdrawals: number; transfers: number; payout_destinations: number
  kyc: number; account_requests: number; tickets: number; total: number
}
```

and append at the end of the file:

```ts
// ------------------------------------------------------------ phase 4: engagement

export type NotificationTopic = 'money' | 'identity' | 'support' | 'bonus'
export interface PortalNotification {
  id: number; topic: NotificationTopic; title: string; body: string
  /** An in-app path, or null. */
  link: string | null
  read_at: string | null; created_at: string
}
export interface NotificationsPage { notifications: PortalNotification[]; has_more: boolean; next_before: number | null }
export type NotificationPrefs = Record<NotificationTopic, boolean>
export type ThemePref = 'light' | 'dim' | 'dark' | 'system'
/** updated_at is null until the user first saves a theme. */
export interface UserSettings { theme: ThemePref; updated_at: string | null }
export type TicketStatus = 'new' | 'open' | 'closed'
export interface TicketSubject { id: number; label: string; enabled: boolean; sort: number; created_at: string }
export interface Ticket {
  id: number; user_id: number; subject_id: number | null; subject_label: string; status: TicketStatus
  created_at: string; updated_at: string; last_message_at: string
  closed_at: string | null; closed_by: number | null
  /** Who spoke last: true while the ticket waits on the investor. */
  last_from_desk: boolean
  /** The server's one "waiting on the desk" rule (not closed, investor spoke last). */
  waiting_on_desk: boolean
  /** On the desk queue and on every thread. */
  email?: string; display_name?: string | null
}
export interface TicketMessage {
  id: number; author_id: number | null; author_name: string | null; from_desk: boolean
  body: string; file_ids: number[]; created_at: string
}
export interface TicketThread extends Ticket { messages: TicketMessage[] }
export type BonusSource = 'signup' | 'kyc' | 'deposit' | 'manual'
export interface Bonus {
  id: number; source: BonusSource; source_id: number | null; amount: number
  note: string | null; created_at: string; currency: string
}
export interface BonusRules {
  signup_enabled: boolean; signup_amount: number; kyc_enabled: boolean; kyc_amount: number
  deposit_enabled: boolean; deposit_pct: number; deposit_cap: number | null; updated_at: string | null
}
```

- [x] **Step 4: `lib/engagement.ts`**

Create `dashboard/src/lib/engagement.ts`:

```ts
import type { BadgeTone } from '../components/Badge'
import type { BonusSource, NotificationTopic, TicketStatus } from './types'

/** The notification topics, in the order the Settings page lists them. */
export const TOPICS: NotificationTopic[] = ['money', 'identity', 'support', 'bonus']

export const TOPIC_LABELS: Record<NotificationTopic, string> = {
  money: 'Money', identity: 'Identity', support: 'Support', bonus: 'Bonus',
}

/** What each email switch covers, as the Settings page names it. */
export const TOPIC_EMAIL_LABELS: Record<NotificationTopic, string> = {
  money: 'Money: deposits, withdrawals, transfers and adjustments',
  identity: 'Identity: verification and trading account requests',
  support: 'Support: ticket replies and closures',
  bonus: 'Bonus: bonuses paid or taken back',
}

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  new: 'New', open: 'Open', closed: 'Closed',
}

export const TICKET_STATUS_TONES: Record<TicketStatus, BadgeTone> = {
  new: 'warn', open: 'brand', closed: 'neutral',
}

export const BONUS_SOURCES: BonusSource[] = ['signup', 'kyc', 'deposit', 'manual']

export const BONUS_SOURCE_LABELS: Record<BonusSource, string> = {
  signup: 'Welcome', kyc: 'Verification', deposit: 'Deposit', manual: 'Manual',
}

/** Ticket images: the server sniffs the bytes and refuses anything else. */
export const IMAGE_ACCEPT = ['image/jpeg', 'image/png', 'image/webp']
export const MAX_IMAGES = 3

/** The textarea look (the one PaymentMethodsTab and AccountRequestsTab
 *  already use); every phase 4 message box takes it from here. */
export const TEXTAREA = 'w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink'

/** An in-app link from the server, or null for anything that is not one of
 *  our own paths: the column only ever holds paths, and this keeps a stray
 *  value from turning into an off-site navigation. */
export function safeLink(link: string | null): string | null {
  return link && link.startsWith('/') && !link.startsWith('//') ? link : null
}

/** A ticket list tail with only the filters that are set. */
export function ticketsQuery(base: string, status: TicketStatus | 'all', q: string): string {
  const p = new URLSearchParams()
  if (status !== 'all') p.set('status', status)
  if (q.trim()) p.set('q', q.trim())
  const s = p.toString()
  return s ? `${base}?${s}` : base
}
```

- [x] **Step 5: Fixtures**

In `dashboard/src/test/portalFixtures.ts`, add `Bonus, BonusRules, PortalNotification, Ticket, TicketMessage, TicketSubject, TicketThread,` to the type import and append:

```ts
export function notificationFixture(overrides: Partial<PortalNotification> = {}): PortalNotification {
  return {
    id: 31, topic: 'money', title: 'Your deposit of 250.00 USD was confirmed',
    body: 'Status: confirmed\nAmount: 250.00 USD via USDT on TRC20', link: '/org/1/invest/deposit',
    read_at: null, created_at: WHEN,
    ...overrides,
  }
}

export function ticketFixture(overrides: Partial<Ticket> = {}): Ticket {
  return {
    id: 7, user_id: 1, subject_id: 2, subject_label: 'Deposits', status: 'new',
    created_at: WHEN, updated_at: WHEN, last_message_at: WHEN, closed_at: null, closed_by: null,
    last_from_desk: false, waiting_on_desk: true,
    ...overrides,
  }
}

export function ticketMessageFixture(overrides: Partial<TicketMessage> = {}): TicketMessage {
  return {
    id: 70, author_id: 1, author_name: 'Sherwyn Joel', from_desk: false,
    body: 'My deposit has not arrived.', file_ids: [], created_at: WHEN,
    ...overrides,
  }
}

export function threadFixture(overrides: Partial<TicketThread> = {}): TicketThread {
  return {
    ...ticketFixture(), email: 'inv@example.com', display_name: 'Sherwyn Joel',
    messages: [ticketMessageFixture()],
    ...overrides,
  }
}

export function subjectFixture(overrides: Partial<TicketSubject> = {}): TicketSubject {
  return { id: 2, label: 'Deposits', enabled: true, sort: 0, created_at: WHEN, ...overrides }
}

export function bonusFixture(overrides: Partial<Bonus> = {}): Bonus {
  return {
    id: 4, source: 'deposit', source_id: 12, amount: 25, note: 'deposit #12', created_at: WHEN,
    currency: 'USD',
    ...overrides,
  }
}

export function bonusRulesFixture(overrides: Partial<BonusRules> = {}): BonusRules {
  return {
    signup_enabled: false, signup_amount: 0, kyc_enabled: false, kyc_amount: 0,
    deposit_enabled: true, deposit_pct: 10, deposit_cap: 100, updated_at: WHEN,
    ...overrides,
  }
}
```

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/lib/engagement.test.ts src/test/portalFixtures.test.ts && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors (a test or page that builds a `RequestsSummary` literal now needs `tickets`; `tsc` names it -- add `tickets: 0`).

- [x] **Step 7: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/lib/engagement.ts dashboard/src/lib/engagement.test.ts dashboard/src/test/portalFixtures.ts dashboard/src/test/portalFixtures.test.ts
git commit -m "feat(dashboard): phase 4 types, engagement labels and fixtures

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 14: The bell and the Notifications page

**Files:**
- Create: `dashboard/src/hooks/useUnreadCount.ts`, `dashboard/src/hooks/useUnreadCount.test.tsx`
- Create: `dashboard/src/lib/notificationActions.ts`, `dashboard/src/lib/notificationActions.test.ts`
- Create: `dashboard/src/components/layout/NotificationBell.tsx`, `dashboard/src/components/layout/NotificationBell.test.tsx`
- Create: `dashboard/src/pages/Notifications.tsx`, `dashboard/src/pages/Notifications.test.tsx`
- Modify: `dashboard/src/components/Layout.tsx`, `dashboard/src/components/Layout.test.tsx`
- Modify: `dashboard/src/App.tsx`, `dashboard/src/pages/groups/admin.ts`, `dashboard/src/pages/groups/investor.ts`
- Modify: `dashboard/src/components/layout/nav.ts`, `dashboard/src/components/layout/nav.test.ts`

**Interfaces:**
- Consumes: `orgApi`; `safeLink`, `TOPIC_LABELS` (Task 13); `notificationFixture`; `Loading`, `Button`, `Badge`, `Card`, `Banner`, `PageHeader`.
- Produces: `UNREAD_POLL_MS`, `useUnreadCount(orgId)` (a `seq` ref: only the newest poll or refresh lands); `lib/notificationActions.ts` `markRead(orgId, n) -> Promise<PortalNotification>`, `markAllRead(orgId) -> Promise<void>`, `withAllRead(list, now?)` (the bell and the page share them); `NotificationBell` (default) props `{ orgId; pageHref; count; onChange }`; `Notifications` page (default export, both groups export it as `Notifications`); routes `notifications` and `invest/notifications`; investor nav Account gains `Notifications`.

- [x] **Step 1: Write the failing tests**

Create `dashboard/src/hooks/useUnreadCount.test.tsx`:

```tsx
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { UNREAD_POLL_MS, useUnreadCount } from './useUnreadCount'

function stubAnswers(...answers: unknown[]) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(
    JSON.stringify(answers.length > 1 ? answers.shift() : answers[0]),
    { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('asks the org for its unread count and polls it every 10 s', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = stubAnswers({ count: 3 }, { count: 1 })
  const { result } = renderHook(() => useUnreadCount(7))
  await waitFor(() => expect(result.current.count).toBe(3))
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/7/notifications/unread-count')
  await act(async () => { vi.advanceTimersByTime(UNREAD_POLL_MS) })
  await waitFor(() => expect(result.current.count).toBe(1))
})

test('an answer without a number leaves the count unknown', async () => {
  const fetchMock = stubAnswers({})
  const { result } = renderHook(() => useUnreadCount(7))
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
  expect(result.current.count).toBeUndefined()
})

test('an older poll answering after a newer refresh never lands', async () => {
  const json = (payload: unknown) => new Response(JSON.stringify(payload),
    { status: 200, headers: { 'Content-Type': 'application/json' } })
  let releaseFirst!: (r: Response) => void
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((res) => { releaseFirst = res }))
    .mockImplementation(async () => json({ count: 0 }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useUnreadCount(7))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  act(() => { result.current.refresh() })      // e.g. after a mark-read
  await waitFor(() => expect(result.current.count).toBe(0))
  await act(async () => {
    releaseFirst(json({ count: 5 }))
    await new Promise((r) => setTimeout(r, 0))
  })
  expect(result.current.count).toBe(0)
})
```

Create `dashboard/src/lib/notificationActions.test.ts`:

```ts
import { afterEach, expect, test, vi } from 'vitest'
import { markAllRead, markRead, withAllRead } from './notificationActions'
import { notificationFixture } from '../test/portalFixtures'

const READ_AT = '2026-10-05T10:00:00Z'

function stub(payload: unknown) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(
    JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.unstubAllGlobals() })

test('markRead posts for an unread row and passes a read one straight through', async () => {
  const unread = notificationFixture({ id: 31 })
  const read = notificationFixture({ id: 30, read_at: READ_AT })
  const fetchMock = stub({ ...unread, read_at: READ_AT })
  expect(await markRead(1, read)).toBe(read)
  expect(fetchMock).not.toHaveBeenCalled()
  expect((await markRead(1, unread)).read_at).toBe(READ_AT)
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/notifications/31/read')
  expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
})

test('markAllRead posts read-all; withAllRead keeps each first read_at', async () => {
  const fetchMock = stub({ updated: 1 })
  await markAllRead(1)
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/notifications/read-all')
  expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
  const list = withAllRead([notificationFixture({ id: 31 }),
                            notificationFixture({ id: 30, read_at: '2026-10-01T10:00:00Z' })], READ_AT)
  expect(list.map((n) => n.read_at)).toEqual([READ_AT, '2026-10-01T10:00:00Z'])
})
```

Create `dashboard/src/components/layout/NotificationBell.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import NotificationBell from './NotificationBell'
import { notificationFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const unread = notificationFixture({ id: 31 })
const read = notificationFixture({
  id: 30, topic: 'identity', title: 'Your identity verification was approved',
  link: '/org/1/invest/profile', read_at: '2026-10-01T10:00:00Z',
})

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/notifications?limit=8')) {
      return jsonResponse({ notifications: [unread, read], has_more: false, next_before: null })
    }
    if (url.endsWith('/notifications/31/read') && init?.method === 'POST') {
      return jsonResponse({ ...unread, read_at: '2026-10-05T10:00:00Z' })
    }
    if (url.endsWith('/notifications/read-all') && init?.method === 'POST') return jsonResponse({ updated: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderBell(count: number | undefined = 1) {
  const onChange = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/1']}>
      <Routes>
        <Route path="/org/1" element={
          <NotificationBell orgId={1} pageHref="/org/1/invest/notifications" count={count} onChange={onChange} />
        } />
        <Route path="/org/1/invest/deposit" element={<div>deposit page</div>} />
        <Route path="/org/1/invest/notifications" element={<div>notifications page</div>} />
      </Routes>
    </MemoryRouter>,
  )
  return onChange
}

async function openPanel(name: string) {
  await userEvent.click(screen.getByRole('button', { name }))
  return screen.findByRole('dialog', { name: 'Latest notifications' })
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the bell names the unread count and lists the latest eight', async () => {
  const fetchMock = mockRoutes()
  renderBell(3)
  const panel = await openPanel('Notifications, 3 unread')
  expect(await within(panel).findByText('Your deposit of 250.00 USD was confirmed')).toBeInTheDocument()
  expect(within(panel).getByText('Your identity verification was approved')).toBeInTheDocument()
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/notifications?limit=8')
  expect(screen.getByRole('button', { name: 'Notifications, 3 unread' })).toHaveAttribute('aria-expanded', 'true')
})

test('with nothing unread the name carries no number', () => {
  mockRoutes()
  renderBell(0)
  expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument()
})

test('a click marks the row read, tells the layout and follows its link', async () => {
  const fetchMock = mockRoutes()
  const onChange = renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await userEvent.click(await within(panel).findByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
  expect(await screen.findByText('deposit page')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, i]) =>
    String(u).endsWith('/notifications/31/read') && (i as RequestInit | undefined)?.method === 'POST')).toBe(true)
  expect(onChange).toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('Mark all read empties the unread state; See all goes to the page', async () => {
  const fetchMock = mockRoutes()
  const onChange = renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.click(within(panel).getByRole('button', { name: 'Mark all read' }))
  await waitFor(() => expect(onChange).toHaveBeenCalled())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/notifications/read-all'))).toBe(true)
  expect(within(panel).getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  await userEvent.click(within(panel).getByRole('link', { name: 'See all notifications' }))
  expect(await screen.findByText('notifications page')).toBeInTheDocument()
})
```

Create `dashboard/src/pages/Notifications.test.tsx`:

```tsx
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Notifications from './Notifications'
import { mockUseOrg } from '../test/orgMock'
import { notificationFixture } from '../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const first = notificationFixture({ id: 31 })
const second = notificationFixture({
  id: 30, topic: 'support', title: 'New reply on ticket #7: Deposits', body: 'We are checking',
  link: '/org/1/invest/support?ticket=7', read_at: '2026-10-01T10:00:00Z',
})
const older = notificationFixture({
  id: 12, topic: 'bonus', title: 'You received a 50.00 USD welcome bonus', link: null,
  read_at: '2026-09-01T10:00:00Z',
})

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/notifications?limit=50&before=30')) {
      return jsonResponse({ notifications: [older], has_more: false, next_before: null })
    }
    if (url.endsWith('/notifications?limit=50')) {
      return jsonResponse({ notifications: [first, second], has_more: true, next_before: 30 })
    }
    if (url.endsWith('/notifications/31/read') && init?.method === 'POST') {
      return jsonResponse({ ...first, read_at: '2026-10-05T10:00:00Z' })
    }
    if (url.endsWith('/notifications/read-all') && init?.method === 'POST') return jsonResponse({ updated: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/org/1/invest/notifications']}>
      <Routes>
        <Route path="/org/1/invest/notifications" element={<Notifications />} />
        <Route path="/org/1/invest/deposit" element={<div>deposit page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists newest first with the read state and loads more', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  expect(await screen.findByRole('heading', { level: 1, name: 'Notifications' })).toBeInTheDocument()
  expect(await screen.findByText('Your deposit of 250.00 USD was confirmed')).toBeInTheDocument()
  expect(screen.getAllByText('Unread')).toHaveLength(1)
  expect(screen.getByText('Support')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('You received a 50.00 USD welcome bonus')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('before=30'))).toBe(true)
})

test('a click marks the row read and follows its link', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
  expect(await screen.findByText('deposit page')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, i]) =>
    String(u).endsWith('/notifications/31/read') && (i as RequestInit | undefined)?.method === 'POST')).toBe(true)
})

test('Mark all read clears every unread badge', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
  await waitFor(() => expect(screen.queryByText('Unread')).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'Mark all read' })).toBeDisabled()
})

test('a late answer for the previous org never lands', async () => {
  let release!: (r: Response) => void
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    if (String(input).startsWith('/api/orgs/1/')) return new Promise<Response>((res) => { release = res })
    return Promise.resolve(jsonResponse({ notifications: [second], has_more: false, next_before: null }))
  }))
  useOrgMock.mockReturnValue(mockUseOrg('investor', 1))
  const view = render(<MemoryRouter><Notifications /></MemoryRouter>)
  useOrgMock.mockReturnValue(mockUseOrg('investor', 2))
  view.rerender(<MemoryRouter><Notifications /></MemoryRouter>)
  expect(await screen.findByText('New reply on ticket #7: Deposits')).toBeInTheDocument()
  await act(async () => {
    release(jsonResponse({ notifications: [first], has_more: false, next_before: null }))
  })
  expect(screen.queryByText('Your deposit of 250.00 USD was confirmed')).not.toBeInTheDocument()
})
```

In `dashboard/src/components/Layout.test.tsx`, make `mockRoutes` answer the unread count: inside its `fetchMock`, directly after the `const respond = (payload: unknown) => …` declaration and before the `/requests/summary` branch (it must come after `respond` is declared, or it hits the temporal dead zone), add:

```tsx
    if (url.includes('/notifications/unread-count')) return respond(overrides['unread'] ?? { count: 0 })
```

and append:

```tsx
test('the bell carries the unread count and links to the desk or portal page', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ unread: { count: 4 } })
  const view = renderShell('/org/1')
  const [deskBell] = await screen.findAllByRole('button', { name: 'Notifications, 4 unread' })
  await userEvent.click(deskBell)
  expect(await screen.findByRole('link', { name: 'See all notifications' }))
    .toHaveAttribute('href', '/org/1/notifications')
  view.unmount()

  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  mockRoutes()
  renderShell('/org/1/invest')
  const [portalBell] = await screen.findAllByRole('button', { name: 'Notifications' })
  await userEvent.click(portalBell)
  expect(await screen.findByRole('link', { name: 'See all notifications' }))
    .toHaveAttribute('href', '/org/1/invest/notifications')
})
```

In `dashboard/src/components/layout/nav.test.ts`, in the first test, add `['Notifications', '/org/7/invest/notifications'],` as the last row of the Account expectation and change `toHaveLength(12)` to `toHaveLength(13)`.

- [x] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/hooks/useUnreadCount.test.tsx src/lib/notificationActions.test.ts src/components/layout/NotificationBell.test.tsx src/pages/Notifications.test.tsx src/components/Layout.test.tsx src/components/layout/nav.test.ts`
Expected: FAIL — the modules do not exist; no bell in the shell; nav has 12 links.

- [x] **Step 3: `useUnreadCount`**

Create `dashboard/src/hooks/useUnreadCount.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../lib/api'

export const UNREAD_POLL_MS = 10000

/**
 * The caller's unread notification count in this org, polled every 10 s
 * (the portal's usual cadence); undefined until the first answer. `refresh`
 * asks again at once, after a mark-read. Every member may ask, so there is
 * no role gate. Every poll and refresh bumps a `seq` ref and only the
 * newest answer lands: an earlier poll that answers after a later refresh
 * is dropped, and so is any answer for the previous org (Layout stays
 * mounted across an org switch).
 */
export function useUnreadCount(orgId: number): { count: number | undefined; refresh: () => void } {
  const [count, setCount] = useState<number | undefined>(undefined)
  const seq = useRef(0)

  const refresh = useCallback(() => {
    const mine = ++seq.current
    orgApi<{ count?: unknown }>(orgId, 'notifications/unread-count').then(
      (r) => {
        if (mine !== seq.current) return
        setCount(typeof r?.count === 'number' ? r.count : undefined)
      },
      () => {
        // A hint, not a record: a failed poll leaves the badge as it was.
      },
    )
  }, [orgId])

  useEffect(() => {
    setCount(undefined)
    refresh()
    const timer = window.setInterval(refresh, UNREAD_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  return { count, refresh }
}
```

Create `dashboard/src/lib/notificationActions.ts`:

```ts
import { orgApi } from './api'
import type { PortalNotification } from './types'

/** Marks one notification read and answers the server's row; a row that is
 *  read already is answered as it is, with no request. The bell and the
 *  Notifications page both go through here. Throws the API error. */
export async function markRead(orgId: number, n: PortalNotification): Promise<PortalNotification> {
  if (n.read_at != null) return n
  return orgApi<PortalNotification>(orgId, `notifications/${n.id}/read`, { method: 'POST' })
}

/** Marks every notification of the caller in this org read. */
export async function markAllRead(orgId: number): Promise<void> {
  await orgApi(orgId, 'notifications/read-all', { method: 'POST' })
}

/** A list after read-all: each row keeps its first read_at, as the server does. */
export function withAllRead(list: PortalNotification[],
                            now: string = new Date().toISOString()): PortalNotification[] {
  return list.map((n) => ({ ...n, read_at: n.read_at ?? now }))
}
```

- [x] **Step 4: `NotificationBell`**

Create `dashboard/src/components/layout/NotificationBell.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import { orgApi } from '../../lib/api'
import { formatWhen } from '../../lib/format'
import { safeLink } from '../../lib/engagement'
import { markAllRead, markRead, withAllRead } from '../../lib/notificationActions'
import type { NotificationsPage, PortalNotification } from '../../lib/types'
import Button from '../Button'
import Loading from '../Loading'

const LATEST = 8
const PANEL_WIDTH = 320

/**
 * The shell's bell: the unread count in the button's name and a small
 * badge, and a glass popover with the latest eight (on an opaque inset),
 * "Mark all read" and a link to the full page. A row click marks it read
 * and follows its in-app link. The popover is portalled and fixed to the
 * trigger, as Menu's is, so no clipping ancestor cuts it off; Escape and an
 * outside click close it. The trigger is 44 px square at every width (a
 * touch target in the tablet top bar as much as on the phone).
 */
export default function NotificationBell({ orgId, pageHref, count, onChange }: {
  orgId: number
  /** The Notifications page of this layout (desk or portal). */
  pageHref: string
  count: number | undefined
  /** After a mark-read: the layout asks the unread count again. */
  onChange: () => void
}) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<PortalNotification[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [pos, setPos] = useState({ top: 0, right: 8 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  // Bumped per open: a slow list for an earlier opening (or org) never lands.
  const seq = useRef(0)

  useEffect(() => { setOpen(false); setItems(null) }, [orgId])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!panelRef.current?.contains(e.target as Node) && !triggerRef.current?.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const toggle = () => {
    if (open) { setOpen(false); return }
    const rect = triggerRef.current?.getBoundingClientRect()
    if (rect) setPos({ top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right) })
    setItems(null)
    setFailed(false)
    setOpen(true)
    const mine = ++seq.current
    orgApi<NotificationsPage>(orgId, `notifications?limit=${LATEST}`).then(
      (p) => { if (mine === seq.current) setItems(Array.isArray(p?.notifications) ? p.notifications : []) },
      () => { if (mine === seq.current) { setItems([]); setFailed(true) } },
    )
  }

  const follow = async (n: PortalNotification) => {
    setOpen(false)
    if (n.read_at == null) {
      try {
        await markRead(orgId, n)
      } catch {
        // The link still works; the badge catches up on the next poll.
      }
      onChange()
    }
    const to = safeLink(n.link)
    if (to) navigate(to)
  }

  const readAll = async () => {
    try {
      await markAllRead(orgId)
      setItems((list) => (list ? withAllRead(list) : null))
    } catch {
      setFailed(true)
    }
    onChange()
  }

  const label = count ? `Notifications, ${count} unread` : 'Notifications'
  return (
    <div className="relative inline-block">
      <Button ref={triggerRef} variant="ghost" tone="neutral" size="sm" aria-label={label}
              aria-haspopup="dialog" aria-expanded={open} onClick={toggle}
              className="relative h-11 w-11 justify-center">
        <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5">
          <path d="M10 3a5 5 0 0 0-5 5v3l-1.5 2.5h13L15 11V8a5 5 0 0 0-5-5zM8 16a2 2 0 0 0 4 0"
                fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {count ? (
          <span aria-hidden="true" style={{ position: 'absolute', top: 0, right: 0 }}
                className="num rounded-full bg-brand px-1 text-xs text-on-accent">
            {count > 99 ? '99+' : count}
          </span>
        ) : null}
      </Button>
      {open && createPortal(
        <div ref={panelRef} role="dialog" aria-label="Latest notifications"
             style={{ position: 'fixed', top: pos.top, right: pos.right, width: PANEL_WIDTH }}
             className="glass z-40 rounded-inset border p-2 shadow-float space-y-2">
          <div className="flex items-center justify-between gap-2 px-1">
            <span className="desk-label">Notifications</span>
            <Button variant="ghost" size="sm" onClick={() => { void readAll() }}
                    disabled={!items?.some((n) => n.read_at == null)}>
              Mark all read
            </Button>
          </div>
          <div className="inset">
            {items == null ? (
              <Loading lines={2} label="Loading notifications" className="p-3" />
            ) : items.length === 0 ? (
              <p className="px-3 py-4 text-sm text-ink-faint">
                {failed ? 'Could not load your notifications' : 'Nothing here yet'}
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {items.map((n) => (
                  <li key={n.id}>
                    <button type="button" onClick={() => { void follow(n) }}
                            className="block w-full px-3 py-2 text-left text-sm hover:bg-brand-wash focus:bg-brand-wash">
                      <span className={`block ${n.read_at == null ? 'font-semibold text-ink' : 'text-ink-soft'}`}>
                        {n.title}
                        {n.read_at == null && <span className="sr-only"> (unread)</span>}
                      </span>
                      <span className="block text-xs text-ink-faint num">{formatWhen(n.created_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <Link to={pageHref} onClick={() => setOpen(false)}
                className="block px-1 text-sm text-brand underline underline-offset-2 hover:text-brand-deep">
            See all notifications
          </Link>
        </div>,
        document.body,
      )}
    </div>
  )
}
```

- [x] **Step 5: The Notifications page**

Create `dashboard/src/pages/Notifications.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { errorText, formatWhen } from '../lib/format'
import { TOPIC_LABELS, safeLink } from '../lib/engagement'
import { markAllRead, markRead, withAllRead } from '../lib/notificationActions'
import Badge from '../components/Badge'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import type { NotificationsPage, PortalNotification } from '../lib/types'

const PAGE = 50

/**
 * Every notification of the signed-in user in this org, newest first, for
 * the desk (/org/:id/notifications) and the portal (invest/notifications).
 * A row click marks it read and follows its link. The bell's badge catches
 * up on its next poll (ponytail: no shared store between the two).
 */
export default function Notifications() {
  const { orgId } = useOrg()
  const navigate = useNavigate()
  const [rows, setRows] = useState<PortalNotification[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Bumped per load: an org switch or a second Load more drops older answers.
  const seq = useRef(0)

  const load = useCallback(async (before: number | null) => {
    const mine = ++seq.current
    setBusy(true)
    try {
      const page = await orgApi<NotificationsPage>(
        orgId, `notifications?limit=${PAGE}${before != null ? `&before=${before}` : ''}`)
      if (mine !== seq.current) return
      setRows((r) => (before == null ? page.notifications : [...r, ...page.notifications]))
      setHasMore(page.has_more)
      setNextBefore(page.next_before)
      setError(null)
    } catch (err) {
      if (mine !== seq.current) return
      setError(errorText(err, 'Could not load your notifications'))
    } finally {
      if (mine === seq.current) { setBusy(false); setLoaded(true) }
    }
  }, [orgId])

  useEffect(() => {
    setRows([]); setLoaded(false)
    void load(null)
  }, [load])

  const open = async (n: PortalNotification) => {
    if (n.read_at == null) {
      try {
        const updated = await markRead(orgId, n)
        setRows((r) => r.map((x) => (x.id === n.id ? updated : x)))
      } catch (err) {
        setError(errorText(err, 'Could not mark it read'))
        return
      }
    }
    const to = safeLink(n.link)
    if (to) navigate(to)
  }

  const readAll = async () => {
    try {
      await markAllRead(orgId)
      setRows((r) => withAllRead(r))
    } catch (err) {
      setError(errorText(err, 'Could not mark them read'))
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        title="Notifications"
        subtitle="Every decision on your requests, every ticket reply and every bonus, newest first. Your email switches live under Settings."
        actions={
          <Button variant="secondary" size="sm" disabled={!rows.some((n) => n.read_at == null)}
                  onClick={() => { void readAll() }}>
            Mark all read
          </Button>
        }
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!loaded ? (
        !error && <Loading lines={4} label="Loading notifications" />
      ) : (
        <Card title="Your notifications" inset>
          <ul className="divide-y divide-line">
            {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No notifications yet</li>}
            {rows.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => { void open(n) }}
                        className="block w-full px-4 py-3 text-left text-sm space-y-1 hover:bg-brand-wash focus:bg-brand-wash">
                  <span className="flex flex-wrap items-center gap-2">
                    {n.read_at == null && <Badge tone="brand">Unread</Badge>}
                    <Badge tone="neutral">{TOPIC_LABELS[n.topic]}</Badge>
                    <span className={n.read_at == null ? 'font-semibold text-ink' : 'text-ink'}>{n.title}</span>
                  </span>
                  <span className="block text-ink-soft whitespace-pre-wrap">{n.body}</span>
                  <span className="block text-xs text-ink-faint num">{formatWhen(n.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
          {hasMore && (
            <div className="p-3">
              <Button variant="secondary" size="sm" disabled={busy} onClick={() => { void load(nextBefore) }}>
                Load more
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
```

- [x] **Step 6: Bell in the shell, routes, nav**

In `dashboard/src/components/Layout.tsx`:
- add the imports `import NotificationBell from './layout/NotificationBell'` and `import { useUnreadCount } from '../hooks/useUnreadCount'`;
- directly after `const groups = …` add:

```tsx
  // One poll feeds both bells (desktop rail and phone top bar).
  const unread = useUnreadCount(orgId)
  const bell = (
    <NotificationBell orgId={orgId} count={unread.count} onChange={unread.refresh}
                      pageHref={investor ? `/org/${orgId}/invest/notifications` : `/org/${orgId}/notifications`} />
  )
```

- turn `const railChrome = (` into `const railChrome = (withBell: boolean) => (` and replace its `<Logo size={26} />` line with

```tsx
        <div className="flex items-center justify-between gap-2">
          <Logo size={26} />
          {withBell && bell}
        </div>
```

- render it as `{railChrome(true)}` inside the `<aside>` and `{railChrome(false)}` inside the menu `Drawer` (the phone top bar has its own bell);
- in the phone top bar, directly after `<Logo size={22} textClass="text-base" />` add `<div className="ml-auto">{bell}</div>`.

In `dashboard/src/pages/groups/admin.ts` and `dashboard/src/pages/groups/investor.ts`, append:

```ts
export { default as Notifications } from '../Notifications'
```

In `dashboard/src/App.tsx`, after `const InvestorOpenAccount = …` add:

```tsx
const Notifications = pick(admin, 'Notifications')
const InvestorNotifications = pick(investor, 'Notifications')
```

and inside the `/org/:orgId` route, after `<Route path="requests" element={<Requests />} />` add `<Route path="notifications" element={<Notifications />} />`, and after `<Route path="invest/open-account" element={<InvestorOpenAccount />} />` add `<Route path="invest/notifications" element={<InvestorNotifications />} />`.

In `dashboard/src/components/layout/nav.ts`, `investorNav`, append to the Account group's items after History:

```ts
        { path: `${p}/notifications`, label: 'Notifications' },
```

- [x] **Step 7: Run the tests and the type check**

Run: `npx vitest run src/hooks/useUnreadCount.test.tsx src/lib/notificationActions.test.ts src/components/layout src/pages/Notifications.test.tsx src/components/Layout.test.tsx src/App.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors, no `act(...)` warning.

- [x] **Step 8: Commit**

```bash
git add dashboard/src/hooks/useUnreadCount.ts dashboard/src/hooks/useUnreadCount.test.tsx dashboard/src/lib/notificationActions.ts dashboard/src/lib/notificationActions.test.ts dashboard/src/components/layout/NotificationBell.tsx dashboard/src/components/layout/NotificationBell.test.tsx dashboard/src/pages/Notifications.tsx dashboard/src/pages/Notifications.test.tsx dashboard/src/components/Layout.tsx dashboard/src/components/Layout.test.tsx dashboard/src/App.tsx dashboard/src/pages/groups/admin.ts dashboard/src/pages/groups/investor.ts dashboard/src/components/layout/nav.ts dashboard/src/components/layout/nav.test.ts
git commit -m "feat(dashboard): the notification bell and the Notifications page

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 15: Settings page and theme sync

**Files:**
- Modify: `dashboard/src/hooks/useTheme.ts`, `dashboard/src/hooks/useTheme.test.ts`
- Create: `dashboard/src/lib/themeSync.ts`, `dashboard/src/lib/themeSync.test.ts`
- Create: `dashboard/src/pages/Settings.tsx`, `dashboard/src/pages/Settings.test.tsx`
- Modify: `dashboard/src/components/Layout.tsx`, `dashboard/src/components/Layout.test.tsx`
- Modify: `dashboard/src/App.tsx`, `dashboard/src/pages/groups/admin.ts`, `dashboard/src/pages/groups/investor.ts`, `dashboard/src/components/layout/nav.ts`, `dashboard/src/components/layout/nav.test.ts`

**Interfaces:**
- Consumes: `GET/PUT /api/me/settings`, `GET/PUT notification-prefs` (Task 4); `ThemePref`, `UserSettings`, `NotificationPrefs`; `TOPICS`, `TOPIC_EMAIL_LABELS`.
- Produces: `useTheme(): { theme, toggle: () => ThemePref, choose: (pref) => void }`, `paletteFor`; `lib/themeSync.ts` (`THEME_PREFS`, `isThemePref`, `localPref`, `saveThemePref`, `syncThemeFromServer`); `Settings` page (both groups); routes `settings`, `invest/settings`; investor nav `Settings`; desk rail button `Settings`.

- [x] **Step 1: Write the failing tests**

Append to `dashboard/src/hooks/useTheme.test.ts` (change its import to `import { paletteFor, useTheme } from './useTheme'`):

```ts
test('choose paints a preference: dim and dark are the night palette, system follows the OS', () => {
  stubMatchMedia(false)
  const { result } = renderHook(() => useTheme())
  act(() => { result.current.choose('dim') })
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(localStorage.getItem('mf.theme')).toBe('dark')
  act(() => { result.current.choose('system') })
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('mf.theme')).toBeNull()
  act(() => { result.current.choose('dark') })
  expect(result.current.theme).toBe('dark')
  expect(paletteFor('light')).toBe('light')
  expect(paletteFor('dark')).toBe('dark')
})

test('a choice in one hook reaches every other hook in the tab; toggle says what it chose', () => {
  const a = renderHook(() => useTheme())
  const b = renderHook(() => useTheme())
  act(() => { a.result.current.choose('dim') })
  expect(b.result.current.theme).toBe('dark')
  let pref = ''
  act(() => { pref = b.result.current.toggle() })
  expect(pref).toBe('light')
  expect(a.result.current.theme).toBe('light')
})
```

Create `dashboard/src/lib/themeSync.test.ts`:

```ts
import { afterEach, expect, test, vi } from 'vitest'
import { isThemePref, localPref, syncThemeFromServer } from './themeSync'

function stubSettings(body: unknown) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(
    JSON.stringify(init?.method === 'PUT' ? JSON.parse(init.body as string) : body),
    { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals() })

test('isThemePref and localPref', () => {
  expect(isThemePref('dim')).toBe(true)
  expect(isThemePref('neon')).toBe(false)
  expect(localPref()).toBeNull()
  localStorage.setItem('mf.theme', 'dark')
  expect(localPref()).toBe('dim')
  localStorage.setItem('mf.theme', 'light')
  expect(localPref()).toBe('light')
})

test("the account's saved theme is applied", async () => {
  stubSettings({ theme: 'dim', updated_at: '2026-10-05T10:00:00Z' })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).toHaveBeenCalledWith('dim')
})

test('an account that never saved one is seeded from this browser, not reset', async () => {
  localStorage.setItem('mf.theme', 'dark')
  const fetchMock = stubSettings({ theme: 'system', updated_at: null })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
  const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')
  expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({ theme: 'dim' })
})

test('an answer that is not a settings body changes nothing', async () => {
  stubSettings({ copying_enabled: true })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
})
```

Create `dashboard/src/pages/Settings.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Settings from './Settings'
import { mockUseOrg } from '../test/orgMock'
import { TOPIC_EMAIL_LABELS } from '../lib/engagement'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(opts: { theme?: string; refusePrefs?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url === '/api/me/settings' && method === 'PUT') {
      return jsonResponse({ ...JSON.parse(init!.body as string), updated_at: '2026-10-05T10:00:00Z' })
    }
    if (url === '/api/me/settings') return jsonResponse({ theme: opts.theme ?? 'system', updated_at: null })
    if (url.endsWith('/notification-prefs') && method === 'PUT') {
      if (opts.refusePrefs) return jsonResponse({ detail: 'database unavailable' }, 500)
      return jsonResponse(JSON.parse(init!.body as string))
    }
    if (url.endsWith('/notification-prefs')) {
      return jsonResponse({ money: true, identity: true, support: true, bonus: true })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).endsWith(fragment) && (init as RequestInit | undefined)?.method === method)
  return JSON.parse((call![1] as RequestInit).body as string)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear()
  delete document.documentElement.dataset.theme
})

test('shows the account theme and saves a new one, painting it at once', async () => {
  const fetchMock = mockRoutes({ theme: 'dark' })
  render(<MemoryRouter><Settings /></MemoryRouter>)
  expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
  expect(await screen.findByRole('radio', { name: 'Dim' })).toBeChecked()   // the server's dark reads as Dim
  await userEvent.click(screen.getByRole('radio', { name: 'Light' }))
  expect(document.documentElement.dataset.theme).toBe('light')
  await waitFor(() => expect(bodyOf(fetchMock, '/api/me/settings', 'PUT')).toEqual({ theme: 'light' }))
  expect(await screen.findByText('Appearance saved')).toBeInTheDocument()
})

test('the four email switches save the whole set', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Settings /></MemoryRouter>)
  const money = await screen.findByRole('switch', { name: TOPIC_EMAIL_LABELS.money })
  expect(money).toBeChecked()
  await userEvent.click(money)
  await waitFor(() => expect(bodyOf(fetchMock, '/notification-prefs', 'PUT'))
    .toEqual({ money: false, identity: true, support: true, bonus: true }))
  expect(await screen.findByText('Email preferences saved')).toBeInTheDocument()
  expect(money).not.toBeChecked()
})

test('a refused save puts the switch back and says why', async () => {
  mockRoutes({ refusePrefs: true })
  render(<MemoryRouter><Settings /></MemoryRouter>)
  const bonus = await screen.findByRole('switch', { name: TOPIC_EMAIL_LABELS.bonus })
  await userEvent.click(bonus)
  expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  expect(bonus).toBeChecked()
})
```

Append to `dashboard/src/components/Layout.test.tsx`:

```tsx
test('the theme toggle saves the choice to the account; the desk rail links to Settings', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderShell('/org/1')
  const [toggle] = await screen.findAllByRole('button', { name: /switch to dim theme/i })
  await userEvent.click(toggle)
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u) === '/api/me/settings'
    && (i as RequestInit | undefined)?.method === 'PUT'
    && JSON.parse((i as RequestInit).body as string).theme === 'dim')).toBe(true))
  expect(screen.getAllByRole('link', { name: 'Settings' })[0]).toHaveAttribute('href', '/org/1/settings')
})
```

In `dashboard/src/components/layout/nav.test.ts`, add `['Settings', '/org/7/invest/settings'],` after the Notifications row and change `toHaveLength(13)` to `toHaveLength(14)`.

- [x] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/hooks/useTheme.test.ts src/lib/themeSync.test.ts src/pages/Settings.test.tsx src/components/Layout.test.tsx src/components/layout/nav.test.ts`
Expected: FAIL — `choose`/`paletteFor` missing, the modules do not exist, no PUT on toggle.

- [x] **Step 3: `useTheme` learns preferences**

In `dashboard/src/hooks/useTheme.ts`, add `import type { ThemePref } from '../lib/types'` below the React import, and below `const STORAGE_KEY = 'mf.theme'` add:

```ts
/** Same-tab broadcast between useTheme instances (the rail, the Settings page). */
const CHANGE_EVENT = 'mf-theme-change'
```

Add after `initialTheme`:

```ts
/** The palette a preference paints. Dim and dark are the one night palette
 *  (ponytail: there is no separate, darker palette yet); system follows the OS. */
export function paletteFor(pref: ThemePref): Theme {
  if (pref === 'system') return systemTheme()
  return pref === 'light' ? 'light' : 'dark'
}
```

Replace the doc comment and the whole `useTheme` function with:

```ts
/** Day/night theme. The document attribute is the single source of truth;
 *  index.html paints localStorage's palette before first paint, so storage
 *  stays the first-paint cache: a palette for an explicit choice, absent
 *  while following the OS. `choose` applies a preference (the account's,
 *  from Settings or the server); `toggle` flips light <-> dim and returns the
 *  preference it chose so the caller can save it to the account. */
export function useTheme(): { theme: Theme; toggle: () => ThemePref; choose: (pref: ThemePref) => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = initialTheme()
    apply(t)
    return t
  })

  // Stay in step with the outside world: another tab (storage event),
  // another hook in this tab (CHANGE_EVENT), or the OS flipping while the
  // user follows it.
  useEffect(() => {
    const follow = (next: Theme) => {
      apply(next)
      setTheme(next)
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return
      if (e.newValue === 'dark' || e.newValue === 'light') follow(e.newValue)
    }
    const onChoose = (e: Event) => {
      const next = (e as CustomEvent<Theme>).detail
      if (next === 'dark' || next === 'light') setTheme(next)
    }
    const onOsChange = (e: { matches: boolean }) => {
      let stored: string | null = null
      try {
        stored = localStorage.getItem(STORAGE_KEY)
      } catch { /* private mode */ }
      if (stored === 'dark' || stored === 'light') return
      follow(e.matches ? 'dark' : 'light')
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener(CHANGE_EVENT, onChoose)
    let mq: MediaQueryList | null = null
    try {
      if (typeof matchMedia === 'function') {
        mq = matchMedia('(prefers-color-scheme: dark)')
        mq.addEventListener?.('change', onOsChange)
      }
    } catch { /* no matchMedia */ }
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(CHANGE_EVENT, onChoose)
      mq?.removeEventListener?.('change', onOsChange)
    }
  }, [])

  const choose = useCallback((pref: ThemePref) => {
    const next = paletteFor(pref)
    apply(next)
    try {
      if (pref === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, next)
    } catch { /* private mode */ }
    setTheme(next)
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: next }))
  }, [])

  const toggle = useCallback((): ThemePref => {
    const pref: ThemePref = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dim'
    choose(pref)
    return pref
  }, [choose])

  return { theme, toggle, choose }
}
```

- [x] **Step 4: `lib/themeSync.ts`**

Create `dashboard/src/lib/themeSync.ts`:

```ts
import { api } from './api'
import type { ThemePref, UserSettings } from './types'

export const THEME_PREFS: ThemePref[] = ['light', 'dim', 'dark', 'system']

export function isThemePref(v: unknown): v is ThemePref {
  return typeof v === 'string' && (THEME_PREFS as string[]).includes(v)
}

/** This browser's explicit choice (what index.html painted), or null while
 *  it follows the OS. The stored palette 'dark' is the Dim preference. */
export function localPref(): ThemePref | null {
  try {
    const stored = localStorage.getItem('mf.theme')
    if (stored === 'light') return 'light'
    if (stored === 'dark') return 'dim'
  } catch { /* private mode */ }
  return null
}

/** Write the choice to the account. Best effort: the browser already has
 *  it, and the next change tries again. */
export async function saveThemePref(pref: ThemePref): Promise<void> {
  try {
    await api<UserSettings>('/api/me/settings', { method: 'PUT', body: JSON.stringify({ theme: pref }) })
  } catch { /* see above */ }
}

/** After sign-in: paint the account's theme -- or, for an account that never
 *  saved one, seed it from this browser's explicit choice instead of
 *  resetting a choice the user already made here. */
export async function syncThemeFromServer(choose: (pref: ThemePref) => void): Promise<void> {
  let s: Partial<UserSettings> | undefined
  try {
    s = await api<UserSettings>('/api/me/settings')
  } catch {
    return
  }
  if (!s || !isThemePref(s.theme)) return
  const local = localPref()
  if (s.updated_at == null && local != null) {
    await saveThemePref(local)
    return
  }
  choose(s.theme)
}
```

- [x] **Step 5: The Settings page**

Create `dashboard/src/pages/Settings.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { api, orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { errorText } from '../lib/format'
import { TOPICS, TOPIC_EMAIL_LABELS } from '../lib/engagement'
import { isThemePref } from '../lib/themeSync'
import { useTheme } from '../hooks/useTheme'
import Banner from '../components/Banner'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import type { NotificationPrefs, NotificationTopic, ThemePref, UserSettings } from '../lib/types'

/** Light, Dim, System. The server also accepts 'dark', which paints the
 *  same night palette and therefore shows as Dim. */
const THEME_OPTIONS: [ThemePref, string][] = [['light', 'Light'], ['dim', 'Dim'], ['system', 'System']]

/**
 * The signed-in user's own settings: Appearance (per account, every org)
 * and the email switches (per org). Desk: /org/:id/settings from the rail;
 * investors: invest/settings from the nav.
 */
export default function Settings() {
  const { orgId } = useOrg()
  const { choose } = useTheme()
  const [theme, setThemePref] = useState<ThemePref | null>(null)
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Bumped per org: a late answer for the previous org never lands.
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    setPrefs(null)
    Promise.all([
      api<UserSettings>('/api/me/settings'),
      orgApi<NotificationPrefs>(orgId, 'notification-prefs'),
    ]).then(
      ([s, p]) => {
        if (mine !== seq.current) return
        setThemePref(isThemePref(s?.theme) ? s.theme : 'system')
        setPrefs(p)
      },
      (err) => {
        if (mine !== seq.current) return
        setError(errorText(err, 'Could not load your settings'))
      },
    )
  }, [orgId])

  const pickTheme = async (pref: ThemePref) => {
    setThemePref(pref)
    choose(pref)
    setError(null); setNotice(null)
    try {
      await api<UserSettings>('/api/me/settings', { method: 'PUT', body: JSON.stringify({ theme: pref }) })
      setNotice('Appearance saved')
    } catch (err) {
      setError(errorText(err, 'Could not save the appearance'))
    }
  }

  const flip = async (topic: NotificationTopic) => {
    if (!prefs) return
    const before = prefs
    const next = { ...prefs, [topic]: !prefs[topic] }
    const mine = seq.current
    setPrefs(next); setBusy(true); setError(null); setNotice(null)
    try {
      const saved = await orgApi<NotificationPrefs>(orgId, 'notification-prefs', {
        method: 'PUT', body: JSON.stringify(next) })
      if (mine === seq.current) { setPrefs(saved); setNotice('Email preferences saved') }
    } catch (err) {
      if (mine === seq.current) { setPrefs(before); setError(errorText(err, 'Could not save your email preferences')) }
    } finally {
      setBusy(false)
    }
  }

  const shown = theme === 'dark' ? 'dim' : theme
  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        title="Settings"
        subtitle="How the portal looks for you wherever you sign in, and which notifications also reach your inbox."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {theme == null || prefs == null ? (
        !error && <Loading lines={4} label="Loading settings" />
      ) : (
        <>
          <Card title="Appearance">
            <fieldset>
              <legend className="desk-label mb-2">Theme</legend>
              <div className="flex flex-wrap gap-4 text-sm">
                {THEME_OPTIONS.map(([value, label]) => (
                  <label key={value} className="flex items-center gap-2 text-ink">
                    <input type="radio" name="theme" value={value} className="accent-brand"
                           checked={shown === value} onChange={() => { void pickTheme(value) }} />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          </Card>
          <Card title="Email notifications">
            <p className="mb-3 text-sm text-ink-soft">
              Everything still appears under Notifications; these switches only decide what is also emailed.
            </p>
            <div className="space-y-3">
              {TOPICS.map((t) => (
                <label key={t} className="flex items-center justify-between gap-4 cursor-pointer has-[:disabled]:cursor-default">
                  <span className="text-sm text-ink">{TOPIC_EMAIL_LABELS[t]}</span>
                  <span className="relative inline-flex items-center">
                    <input type="checkbox" role="switch" checked={prefs[t]} disabled={busy}
                           onChange={() => { void flip(t) }} className="peer sr-only" />
                    <span aria-hidden="true"
                          className="h-5 w-9 rounded-full bg-ink-faint transition-colors peer-checked:bg-brand peer-disabled:opacity-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand" />
                    <span aria-hidden="true"
                          className="pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-card transition-transform peer-checked:translate-x-4" />
                  </span>
                </label>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
```

- [x] **Step 6: Wire the shell, routes and nav**

In `dashboard/src/components/Layout.tsx`:
- add `import { saveThemePref, syncThemeFromServer } from '../lib/themeSync'`;
- change `const { theme, toggle: toggleTheme } = useTheme()` to `const { theme, toggle: toggleTheme, choose: chooseTheme } = useTheme()` and directly below it add:

```tsx
  // The account's theme follows the user between browsers; localStorage
  // stays the first-paint cache. Once per shell mount.
  useEffect(() => { void syncThemeFromServer(chooseTheme) }, [chooseTheme])
```

- change the theme button's `onClick={toggleTheme}` to `onClick={() => { void saveThemePref(toggleTheme()) }}`;
- in `railChrome`'s bottom `div.border-t`, directly before the Log out button add:

```tsx
        {!investor && (
          <Button variant="ghost" tone="neutral" size="sm" block to={`/org/${orgId}/settings`}
                  className="justify-start">
            Settings
          </Button>
        )}
```

In both `pages/groups/admin.ts` and `pages/groups/investor.ts` append `export { default as Settings } from '../Settings'`.

In `dashboard/src/App.tsx` add `const Settings = pick(admin, 'Settings')` and `const InvestorSettings = pick(investor, 'Settings')`, and the routes `<Route path="settings" element={<Settings />} />` (after `notifications`) and `<Route path="invest/settings" element={<InvestorSettings />} />` (after `invest/notifications`).

In `nav.ts`, append to the investor Account items after Notifications:

```ts
        { path: `${p}/settings`, label: 'Settings' },
```

- [x] **Step 7: Run the tests and the type check**

Run: `npx vitest run src/hooks/useTheme.test.ts src/lib/themeSync.test.ts src/pages/Settings.test.tsx src/components/Layout.test.tsx src/components/layout/nav.test.ts src/App.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [x] **Step 8: Commit**

```bash
git add dashboard/src/hooks/useTheme.ts dashboard/src/hooks/useTheme.test.ts dashboard/src/lib/themeSync.ts dashboard/src/lib/themeSync.test.ts dashboard/src/pages/Settings.tsx dashboard/src/pages/Settings.test.tsx dashboard/src/components/Layout.tsx dashboard/src/components/Layout.test.tsx dashboard/src/App.tsx dashboard/src/pages/groups/admin.ts dashboard/src/pages/groups/investor.ts dashboard/src/components/layout/nav.ts dashboard/src/components/layout/nav.test.ts
git commit -m "feat(dashboard): Settings page -- appearance follows the account, per-topic email switches

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 16: Investor Support pages

**Files:**
- Create: `dashboard/src/pages/support/TicketMessages.tsx`
- Create: `dashboard/src/pages/investor/InvestorSupport.tsx`, `dashboard/src/pages/investor/InvestorSupport.test.tsx`
- Modify: `dashboard/src/App.tsx`, `dashboard/src/pages/groups/investor.ts`, `dashboard/src/components/layout/nav.ts`, `dashboard/src/components/layout/nav.test.ts`

**Interfaces:**
- Consumes: Task 6 routes; `orgApi`, `orgUpload`; `FileInput`, `MAX_UPLOAD_BYTES`; `IMAGE_ACCEPT`, `MAX_IMAGES`, `TEXTAREA`, `TICKET_STATUS_LABELS`, `TICKET_STATUS_TONES`, `ticketsQuery`; fixtures `subjectFixture`, `ticketFixture`, `ticketMessageFixture`, `threadFixture`.
- Produces: `TicketMessages` (default; props `{ messages; fileUrl; viewer }`); `InvestorSupport` (default; its image slots are private: `useImageSlots` keeps each uploaded id once the upload lands, so a send that fails afterwards neither re-uploads nor drops the image on retry, as `InvestorProfile` does); route `invest/support` (`?ticket=<id>` opens a thread); investor nav `Support`.

- [x] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investor/InvestorSupport.test.tsx`:

```tsx
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorSupport from './InvestorSupport'
import { mockUseOrg } from '../../test/orgMock'
import {
  subjectFixture, threadFixture, ticketFixture, ticketMessageFixture,
} from '../../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const subjects = [subjectFixture({ id: 2, label: 'Deposits' }), subjectFixture({ id: 3, label: 'Withdrawals', sort: 1 })]
const t7 = ticketFixture({ id: 7, subject_label: 'Deposits', status: 'new' })
const t8 = ticketFixture({ id: 8, subject_label: 'Withdrawals', status: 'closed', closed_at: '2026-10-02T10:00:00Z' })
const thread7 = threadFixture({
  id: 7, subject_label: 'Deposits',
  messages: [
    ticketMessageFixture({ id: 70, body: 'My deposit has not arrived.', file_ids: [41] }),
    ticketMessageFixture({ id: 71, from_desk: true, author_name: 'Desk Admin', body: 'We are checking.' }),
  ],
})

function mockRoutes(opts: { slowAll?: Promise<Response>; raiseFailsOnce?: boolean } = {}) {
  let raiseFails = opts.raiseFailsOnce ?? false
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/ticket-subjects')) return jsonResponse(subjects)
    if (url.endsWith('/investor/files') && method === 'POST') {
      return jsonResponse({ id: 91, purpose: 'ticket_attachment', content_type: 'image/png',
                            size_bytes: 3, created_at: '2026-10-05T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/tickets') && method === 'POST') {
      if (raiseFails) {
        raiseFails = false
        return jsonResponse({ detail: 'too many requests; try again later' }, 429)
      }
      return jsonResponse({ ...thread7, id: 9, subject_label: 'Withdrawals' }, 201)
    }
    if (url.endsWith('/investor/tickets/7/messages') && method === 'POST') {
      const body = JSON.parse(init!.body as string).body
      return jsonResponse({ ...thread7, messages: [...thread7.messages, ticketMessageFixture({ id: 72, body })] }, 201)
    }
    if (url.endsWith('/investor/tickets/7/close') && method === 'POST') {
      return jsonResponse({ ...thread7, status: 'closed', closed_at: '2026-10-05T10:00:00Z' })
    }
    if (url.endsWith('/investor/tickets/9')) return jsonResponse({ ...thread7, id: 9, subject_label: 'Withdrawals' })
    if (url.endsWith('/investor/tickets/7')) return jsonResponse(thread7)
    if (url.includes('/investor/tickets?status=closed')) return jsonResponse([t8])
    if (url.includes('/investor/tickets?q=')) return jsonResponse([t7])
    if (url.endsWith('/investor/tickets')) return opts.slowAll ?? jsonResponse([t7, t8])
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).endsWith(fragment) && (init as RequestInit | undefined)?.method === method)
  return JSON.parse((call![1] as RequestInit).body as string)
}

function renderPage(entry = '/org/1/invest/support') {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes><Route path="/org/1/invest/support" element={<InvestorSupport />} /></Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists the tickets with status tabs and a search', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  expect(await screen.findByRole('heading', { level: 1, name: 'Support' })).toBeInTheDocument()
  expect(await screen.findByText('Deposits')).toBeInTheDocument()
  expect(screen.getByText('Withdrawals')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Closed' }))
  await waitFor(() => expect(screen.queryByText('Deposits')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/investor/tickets?status=closed'))).toBe(true)
  await userEvent.click(screen.getByRole('tab', { name: 'All' }))
  await userEvent.type(screen.getByLabelText('Search tickets'), 'usdt')
  await userEvent.click(screen.getByRole('button', { name: 'Search' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investor/tickets?q=usdt'))).toBe(true))
})

test('raises a ticket with an image and opens its thread', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('Deposits')
  await userEvent.click(screen.getByRole('button', { name: 'Raise ticket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Raise a ticket' })
  await userEvent.selectOptions(within(dialog).getByLabelText('Subject'), '3')
  await userEvent.type(within(dialog).getByLabelText('Message'), 'Withdrawal stuck')
  await userEvent.upload(within(dialog).getByLabelText('Image 1 (optional)'),
    new File(['png'], 'shot.png', { type: 'image/png' }))
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send ticket' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investor/tickets', 'POST'))
    .toEqual({ subject_id: 3, body: 'Withdrawal stuck', file_ids: [91] }))
  expect(await screen.findByText('Ticket sent. The desk replies here and in your notifications.')).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: '#9 Withdrawals' })).toBeInTheDocument()
})

test('the thread shows the messages and images, sends a reply and closes', async () => {
  const fetchMock = mockRoutes()
  renderPage('/org/1/invest/support?ticket=7')
  expect(await screen.findByRole('heading', { name: '#7 Deposits' })).toBeInTheDocument()
  expect(screen.getByText('My deposit has not arrived.')).toBeInTheDocument()
  expect(screen.getByText('Support desk')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Image 1' })).toHaveAttribute('src', '/api/orgs/1/investor/files/41')
  await userEvent.type(screen.getByLabelText('Your reply'), 'Thanks')
  await userEvent.click(screen.getByRole('button', { name: 'Send reply' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investor/tickets/7/messages', 'POST'))
    .toEqual({ body: 'Thanks', file_ids: [] }))
  expect(await screen.findByText('Reply sent.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Close ticket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Close ticket #7?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, close it' }))
  expect(await screen.findByText('Ticket closed.')).toBeInTheDocument()
  expect(screen.getByText('This ticket is closed. A reply opens it again.')).toBeInTheDocument()
})

test('a failed send keeps the uploaded image, so the retry does not upload it again', async () => {
  const fetchMock = mockRoutes({ raiseFailsOnce: true })
  renderPage()
  await screen.findByText('Deposits')
  await userEvent.click(screen.getByRole('button', { name: 'Raise ticket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Raise a ticket' })
  await userEvent.type(within(dialog).getByLabelText('Message'), 'Withdrawal stuck')
  await userEvent.upload(within(dialog).getByLabelText('Image 1 (optional)'),
    new File(['png'], 'shot.png', { type: 'image/png' }))
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send ticket' }))
  expect(await within(dialog).findByText(/too many requests/)).toBeInTheDocument()
  expect(within(dialog).getByText('Uploaded; it goes with your message')).toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send ticket' }))
  expect(await screen.findByText('Ticket sent. The desk replies here and in your notifications.')).toBeInTheDocument()
  const posts = (fragment: string) => fetchMock.mock.calls.filter(([u, i]) =>
    String(u).endsWith(fragment) && (i as RequestInit | undefined)?.method === 'POST')
  expect(posts('/investor/files')).toHaveLength(1)
  expect(posts('/investor/tickets').map(([, i]) => JSON.parse((i as RequestInit).body as string).file_ids))
    .toEqual([[91], [91]])
})

test('a slow list for an older filter never lands over the newer one', async () => {
  let release!: (r: Response) => void
  mockRoutes({ slowAll: new Promise<Response>((res) => { release = res }) })
  renderPage()
  await userEvent.click(await screen.findByRole('tab', { name: 'Closed' }))
  expect(await screen.findByText('Withdrawals')).toBeInTheDocument()
  await act(async () => { release(jsonResponse([t7, t8])) })
  expect(screen.queryByText('Deposits')).not.toBeInTheDocument()
})
```

In `nav.test.ts`, insert `['Support', '/org/7/invest/support'],` directly after the History row (before Notifications) and change `toHaveLength(14)` to `toHaveLength(15)`.

- [x] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorSupport.test.tsx src/components/layout/nav.test.ts`
Expected: FAIL — `./InvestorSupport` does not exist; nav has no Support.

- [x] **Step 3: The thread view shared by both sides**

Create `dashboard/src/pages/support/TicketMessages.tsx`:

```tsx
import { formatWhen } from '../../lib/format'
import type { TicketMessage } from '../../lib/types'

/** One ticket's thread, oldest first: who wrote, when, the text and its
 *  images. The investor sees "You" and "Support desk"; the desk sees names.
 *  `fileUrl` is the side's own file route (investor: own files; desk: org). */
export default function TicketMessages({ messages, fileUrl, viewer }: {
  messages: TicketMessage[]
  fileUrl: (fileId: number) => string
  viewer: 'investor' | 'desk'
}) {
  const who = (m: TicketMessage) => m.from_desk
    ? (viewer === 'investor' ? 'Support desk' : `${m.author_name ?? 'Admin'} (desk)`)
    : (viewer === 'investor' ? 'You' : m.author_name ?? 'Investor')
  return (
    <ol className="space-y-3">
      {messages.map((m) => (
        <li key={m.id} className="inset p-3 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-semibold text-ink">{who(m)}</span>
            <span className="num text-ink-faint">{formatWhen(m.created_at)}</span>
          </div>
          <p className="text-sm text-ink whitespace-pre-wrap">{m.body}</p>
          {m.file_ids.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {m.file_ids.map((id, i) => (
                <a key={id} href={fileUrl(id)} target="_blank" rel="noreferrer"
                   aria-label={`Open image ${i + 1} of this message`}>
                  <img src={fileUrl(id)} alt={`Image ${i + 1}`}
                       className="h-24 w-24 rounded-inset border border-line object-cover" />
                </a>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}
```

- [x] **Step 4: The investor page**

Create `dashboard/src/pages/investor/InvestorSupport.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import {
  IMAGE_ACCEPT, MAX_IMAGES, TEXTAREA, TICKET_STATUS_LABELS, TICKET_STATUS_TONES, ticketsQuery,
} from '../../lib/engagement'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import FileInput, { MAX_UPLOAD_BYTES } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import Tabs from '../../components/Tabs'
import TicketMessages from '../support/TicketMessages'
import type { Ticket, TicketStatus, TicketSubject, TicketThread, UploadedFile } from '../../lib/types'

type StatusTab = TicketStatus | 'all'
const STATUS_TABS: { key: StatusTab; label: string }[] = [
  { key: 'all', label: 'All' }, { key: 'new', label: 'New' },
  { key: 'open', label: 'Open' }, { key: 'closed', label: 'Closed' },
]
const NO_FILES: (File | null)[] = Array(MAX_IMAGES).fill(null)
const NO_IDS: (number | null)[] = Array(MAX_IMAGES).fill(null)

/**
 * The three optional image slots of one message. A picked file uploads on
 * send; as soon as an upload lands its id is kept and the slot cleared, so
 * a send that fails afterwards neither uploads it again nor drops it on the
 * retry (InvestorProfile's pending-ids pattern). Picking a new file for a
 * slot replaces whatever it held.
 */
function useImageSlots(orgId: number) {
  const [files, setFiles] = useState<(File | null)[]>(NO_FILES)
  const [pending, setPending] = useState<(number | null)[]>(NO_IDS)
  const at = <T,>(list: T[], i: number, value: T) => list.map((x, j) => (j === i ? value : x))

  const pick = (i: number, next: File | null) => {
    setFiles((f) => at(f, i, next))
    setPending((p) => at(p, i, null))
  }

  /** Uploads what is still a File; answers every id, in slot order. */
  const upload = async (): Promise<number[]> => {
    const ids = [...pending]
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      if (!f) continue
      const fd = new FormData()
      fd.append('purpose', 'ticket_attachment')
      fd.append('file', f)
      const id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      ids[i] = id
      setFiles((x) => at(x, i, null))
      setPending((x) => at(x, i, id))
    }
    return ids.filter((id): id is number => id != null)
  }

  const reset = () => { setFiles(NO_FILES); setPending(NO_IDS) }
  return { files, pending, pick, upload, reset }
}

type ImageSlotsState = ReturnType<typeof useImageSlots>

/** One FileInput per slot; a slot whose image is already uploaded says so. */
function ImageSlots({ idBase, slots, disabled }: {
  idBase: string
  slots: ImageSlotsState
  disabled?: boolean
}) {
  return (
    <div className="space-y-3">
      {slots.files.map((f, i) => (
        <FileInput key={i} id={`${idBase}-${i + 1}`} label={`Image ${i + 1} (optional)`}
                   accept={IMAGE_ACCEPT} maxBytes={MAX_UPLOAD_BYTES} value={f} disabled={disabled}
                   hint={slots.pending[i] != null ? 'Uploaded; it goes with your message'
                     : i === 0 ? `JPEG, PNG or WebP, up to 5 MB; at most ${MAX_IMAGES} per message` : undefined}
                   onChange={(next) => slots.pick(i, next)} />
      ))}
    </div>
  )
}

function TicketList({ orgId, reloadKey, onOpen }: {
  orgId: number
  reloadKey: number
  onOpen: (id: number) => void
}) {
  const [status, setStatus] = useState<StatusTab>('all')
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<Ticket[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Bumped per load: a tab click or a search while an older load is in
  // flight drops the older answer.
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    orgApi<Ticket[]>(orgId, ticketsQuery('investor/tickets', status, q)).then(
      (r) => { if (mine === seq.current) { setRows(r); setError(null) } },
      (err) => {
        if (mine !== seq.current) return
        setRows((x) => x ?? [])
        setError(errorText(err, 'Could not load your tickets'))
      },
    )
  }, [orgId, status, q, reloadKey])

  return (
    <div className="space-y-4">
      <Tabs idBase="support" label="Ticket status" value={status}
            onChange={(k) => setStatus(k as StatusTab)} items={STATUS_TABS} />
      <form role="search" onSubmit={(e: FormEvent) => { e.preventDefault(); setQ(draft) }}
            className="flex flex-wrap items-end gap-2">
        <Input aria-label="Search tickets" placeholder="Subject or message" value={draft}
               onChange={(e) => setDraft(e.target.value)} />
        <Button type="submit" variant="secondary" size="sm">Search</Button>
      </form>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      <div id="support-panel" role="tabpanel" aria-labelledby={`support-tab-${status}`}>
        <Card title="Your tickets" inset>
          {rows == null ? (
            <Loading lines={3} label="Loading tickets" className="p-4" />
          ) : (
            <ul className="divide-y divide-line">
              {rows.length === 0 && (
                <li className="text-center py-8 text-ink-faint">
                  {q || status !== 'all' ? 'No tickets match' : 'No tickets yet'}
                </li>
              )}
              {rows.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => onOpen(t.id)}
                          className="block w-full px-4 py-3 text-left text-sm space-y-1 hover:bg-brand-wash focus:bg-brand-wash">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="num text-ink-soft">#{t.id}</span>
                      <span className="text-ink">{t.subject_label}</span>
                      <Badge tone={TICKET_STATUS_TONES[t.status]}>{TICKET_STATUS_LABELS[t.status]}</Badge>
                      {t.last_from_desk && t.status !== 'closed' && <Badge tone="profit">Desk replied</Badge>}
                    </span>
                    <span className="block text-xs text-ink-faint num">Last message {formatWhen(t.last_message_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

function TicketView({ orgId, ticketId, onBack, onNotice }: {
  orgId: number
  ticketId: number
  onBack: () => void
  onNotice: (message: string) => void
}) {
  const [thread, setThread] = useState<TicketThread | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reply, setReply] = useState('')
  const slots = useImageSlots(orgId)
  const [busy, setBusy] = useState(false)
  const [closing, setClosing] = useState(false)
  const seq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++seq.current
    try {
      const t = await orgApi<TicketThread>(orgId, `investor/tickets/${ticketId}`)
      if (mine === seq.current) { setThread(t); setError(null) }
    } catch (err) {
      if (mine === seq.current) setError(errorText(err, 'Could not load this ticket'))
    }
  }, [orgId, ticketId])

  useEffect(() => { void load() }, [load])

  const send = async (e: FormEvent) => {
    e.preventDefault()
    if (!reply.trim()) { setError('Write a message first'); return }
    setBusy(true); setError(null)
    try {
      const file_ids = await slots.upload()
      const t = await orgApi<TicketThread>(orgId, `investor/tickets/${ticketId}/messages`, {
        method: 'POST', body: JSON.stringify({ body: reply.trim(), file_ids }) })
      const reopened = thread?.status === 'closed'
      seq.current++   // a load still in flight must not land over this answer
      setThread(t); setReply(''); slots.reset()
      onNotice(reopened ? 'Reply sent; the ticket is open again.' : 'Reply sent.')
    } catch (err) {
      setError(errorText(err, 'Could not send your reply'))
    } finally {
      setBusy(false)
    }
  }

  const close = async () => {
    setBusy(true)
    try {
      const t = await orgApi<TicketThread>(orgId, `investor/tickets/${ticketId}/close`, { method: 'POST' })
      seq.current++
      setThread(t)
      onNotice('Ticket closed.')
    } catch (err) {
      setError(errorText(err, 'Could not close the ticket'))
    } finally {
      setBusy(false)
      setClosing(false)
    }
  }

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>Back to all tickets</Button>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {thread == null ? (
        !error && <Loading lines={4} label="Loading ticket" />
      ) : (
        <>
          <Card title={`#${thread.id} ${thread.subject_label}`} actions={
            <>
              <Badge tone={TICKET_STATUS_TONES[thread.status]}>{TICKET_STATUS_LABELS[thread.status]}</Badge>
              {thread.status !== 'closed' && (
                <Button variant="secondary" tone="loss" size="sm" disabled={busy} onClick={() => setClosing(true)}>
                  Close ticket
                </Button>
              )}
            </>
          }>
            <TicketMessages messages={thread.messages} viewer="investor"
                            fileUrl={(id) => `/api/orgs/${orgId}/investor/files/${id}`} />
          </Card>
          <Card title="Reply">
            <form onSubmit={send} className="space-y-4">
              {thread.status === 'closed' && (
                <p className="text-sm text-ink-soft">This ticket is closed. A reply opens it again.</p>
              )}
              <label className="block">
                <span className="desk-label block mb-1">Message</span>
                <textarea aria-label="Your reply" rows={4} maxLength={4000} value={reply} disabled={busy}
                          onChange={(e) => setReply(e.target.value)} className={TEXTAREA} />
              </label>
              <ImageSlots idBase="reply-image" slots={slots} disabled={busy} />
              <Button type="submit" disabled={busy}>Send reply</Button>
            </form>
          </Card>
        </>
      )}
      <ConfirmDialog open={closing} title={`Close ticket #${ticketId}?`} confirmLabel="Yes, close it" danger
                     busy={busy} onConfirm={() => { void close() }} onCancel={() => setClosing(false)}>
        <p>You can still reply later; a reply opens it again.</p>
      </ConfirmDialog>
    </div>
  )
}

/**
 * The investor's support: tickets with status tabs and a search, "Raise
 * ticket" (subject, message, up to three images) and a thread view with
 * replies and Close. `?ticket=<id>` -- the link in a support notification --
 * opens a thread.
 */
export default function InvestorSupport() {
  const { orgId } = useOrg()
  const [params, setParams] = useSearchParams()
  const ticketId = Number(params.get('ticket')) || null
  const [subjects, setSubjects] = useState<TicketSubject[] | null>(null)
  const [raising, setRaising] = useState(false)
  const [form, setForm] = useState({ subject: '', body: '' })
  const slots = useImageSlots(orgId)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const subjectsSeq = useRef(0)

  useEffect(() => {
    const mine = ++subjectsSeq.current
    setSubjects(null)
    orgApi<TicketSubject[]>(orgId, 'investor/ticket-subjects').then(
      (s) => { if (mine === subjectsSeq.current) setSubjects(Array.isArray(s) ? s : []) },
      () => { if (mine === subjectsSeq.current) setSubjects([]) },
    )
  }, [orgId])

  const openTicket = (id: number) => setParams({ ticket: String(id) })
  const back = () => { setParams({}); setReloadKey((k) => k + 1) }

  const startRaise = () => {
    setForm({ subject: subjects?.[0] ? String(subjects[0].id) : '', body: '' })
    slots.reset(); setDialogError(null); setRaising(true)
  }

  const raise = async () => {
    if (!form.subject) { setDialogError('Pick a subject'); return }
    if (!form.body.trim()) { setDialogError('Write a message'); return }
    setBusy(true); setDialogError(null)
    try {
      const file_ids = await slots.upload()
      const t = await orgApi<TicketThread>(orgId, 'investor/tickets', {
        method: 'POST',
        body: JSON.stringify({ subject_id: Number(form.subject), body: form.body.trim(), file_ids }),
      })
      setRaising(false)
      slots.reset()
      setNotice('Ticket sent. The desk replies here and in your notifications.')
      openTicket(t.id)
    } catch (err) {
      setDialogError(errorText(err, 'Could not send the ticket'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        title="Support"
        subtitle="Ask the desk about anything in your account. Replies arrive here and in your notifications."
        actions={<Button onClick={startRaise} disabled={!subjects || subjects.length === 0}>Raise ticket</Button>}
      />
      {subjects?.length === 0 && (
        <p className="text-sm text-ink-soft">The desk has not set up support subjects yet.</p>
      )}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {ticketId ? (
        <TicketView key={ticketId} orgId={orgId} ticketId={ticketId} onBack={back} onNotice={setNotice} />
      ) : (
        <TicketList orgId={orgId} reloadKey={reloadKey} onOpen={openTicket} />
      )}
      <ConfirmDialog open={raising} title="Raise a ticket" confirmLabel="Send ticket" busy={busy}
                     onConfirm={() => { void raise() }} onCancel={() => setRaising(false)}>
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        <label className="block">
          <span className="desk-label block mb-1">Subject</span>
          <Select aria-label="Subject" block value={form.subject}
                  onChange={(e) => setForm({ ...form, subject: e.target.value })}>
            {(subjects ?? []).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </Select>
        </label>
        <label className="block">
          <span className="desk-label block mb-1">Message</span>
          <textarea aria-label="Message" rows={5} maxLength={4000} value={form.body}
                    onChange={(e) => setForm({ ...form, body: e.target.value })} className={TEXTAREA} />
        </label>
        <ImageSlots idBase="ticket-image" slots={slots} disabled={busy} />
      </ConfirmDialog>
    </div>
  )
}
```

- [x] **Step 5: Route and nav**

In `pages/groups/investor.ts` append `export { default as InvestorSupport } from '../investor/InvestorSupport'`. In `App.tsx` add `const InvestorSupport = pick(investor, 'InvestorSupport')` and `<Route path="invest/support" element={<InvestorSupport />} />` after `invest/open-account`. In `nav.ts`, insert in the investor Account items directly after History:

```ts
        { path: `${p}/support`, label: 'Support' },
```

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorSupport.test.tsx src/components/layout/nav.test.ts && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors, no `act(...)` warning.

- [x] **Step 7: Commit**

```bash
git add dashboard/src/pages/support/TicketMessages.tsx dashboard/src/pages/investor/InvestorSupport.tsx dashboard/src/pages/investor/InvestorSupport.test.tsx dashboard/src/App.tsx dashboard/src/pages/groups/investor.ts dashboard/src/components/layout/nav.ts dashboard/src/components/layout/nav.test.ts
git commit -m "feat(dashboard): investor Support -- raise tickets with images, threads, replies, close

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 17: Desk Support tab, Ticket subjects card, "Portal settings" tab

**Files:**
- Create: `dashboard/src/pages/requests/SupportTab.tsx`, `dashboard/src/pages/requests/SupportTab.test.tsx`
- Modify: `dashboard/src/pages/requests/RequestTabs.tsx`, `dashboard/src/pages/Requests.tsx`, `dashboard/src/pages/Requests.test.tsx`
- Create: `dashboard/src/pages/investors/TicketSubjectsCard.tsx`, `dashboard/src/pages/investors/TicketSubjectsCard.test.tsx`
- Modify: `dashboard/src/pages/investors/PaymentMethodsTab.tsx`, `dashboard/src/pages/Investors.tsx`, `dashboard/src/pages/Investors.test.tsx`

**Interfaces:**
- Consumes: Tasks 5 and 7 routes; `TicketMessages` (Task 16); `TEXTAREA` (Task 13); `Ticket.waiting_on_desk` (Tasks 6/13); `Row`, `Section` from `RequestDetailsDrawer`; `DeskTabProps` from `VerificationTab`; `RequestsSummary.tickets`.
- Produces: `SupportTab` (default; props `DeskTabProps & { initialTicket: number | null; onDrawerClosed: () => void }`; follows `initialTicket` whenever it changes), `DeskTab` += `'support'`, tab `Support (<n>)`; `Requests` keeps `?tab` in step with the selected tab, follows `?tab`/`?ticket` changes while mounted, and drops `?ticket` when the ticket drawer closes; `TicketSubjectsCard` (default; props `{ orgId; control }`); the Investors tab `Portal settings`; the settings card renamed `Withdrawal and account rules`.

- [x] **Step 1: Write the failing tests**

Create `dashboard/src/pages/requests/SupportTab.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import SupportTab from './SupportTab'
import { threadFixture, ticketFixture, ticketMessageFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const WHEN = '2026-10-05T10:00:00Z'
const queue = [
  ticketFixture({ id: 7, email: 'inv@example.com', display_name: 'Ada Investor', status: 'new' }),
  ticketFixture({ id: 8, subject_label: 'Withdrawals', email: 'bo@example.com', display_name: 'Bo',
                  status: 'open', last_from_desk: true, waiting_on_desk: false }),
  ticketFixture({ id: 9, subject_label: 'Old', email: 'cy@example.com', display_name: 'Cy',
                  status: 'closed', closed_at: WHEN, waiting_on_desk: false }),
]
const thread7 = threadFixture({
  id: 7, email: 'inv@example.com', display_name: 'Ada Investor',
  messages: [ticketMessageFixture({ id: 70, author_name: 'Ada Investor', body: 'My deposit has not arrived.', file_ids: [41] })],
})

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/tickets/7/messages') && method === 'POST') {
      return jsonResponse({ ...thread7, status: 'open', last_from_desk: true, waiting_on_desk: false, messages: [
        ...thread7.messages,
        ticketMessageFixture({ id: 71, from_desk: true, author_name: 'Desk Admin', body: 'We are checking.' })] }, 201)
    }
    if (url.endsWith('/tickets/7/close') && method === 'POST') {
      return jsonResponse({ ...thread7, status: 'closed', closed_at: WHEN, waiting_on_desk: false })
    }
    if (url.endsWith('/tickets/7')) return jsonResponse(thread7)
    if (url.includes('/tickets?q=')) return jsonResponse([queue[1]])
    if (url.endsWith('/tickets')) return jsonResponse(queue)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the open view hides closed tickets and marks the ones waiting on the desk', async () => {
  const fetchMock = mockRoutes()
  render(<SupportTab orgId={1} control show="open" onDone={vi.fn()} initialTicket={null}
                     onDrawerClosed={vi.fn()} />)
  expect(await screen.findByText('#7 Deposits')).toBeInTheDocument()
  expect(screen.getByText('#8 Withdrawals')).toBeInTheDocument()
  expect(screen.queryByText('#9 Old')).not.toBeInTheDocument()
  expect(screen.getAllByText('Waiting on desk')).toHaveLength(1)
  await userEvent.type(screen.getByLabelText('Search tickets'), 'wire')
  await userEvent.click(screen.getByRole('button', { name: 'Search' }))
  await waitFor(() => expect(screen.queryByText('#7 Deposits')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/tickets?q=wire'))).toBe(true)
})

test('the drawer shows the thread, sends a reply and closes the ticket', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<SupportTab orgId={1} control show="all" onDone={onDone} initialTicket={null}
                     onDrawerClosed={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Open ticket 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })
  expect(within(drawer).getByText('My deposit has not arrived.')).toBeInTheDocument()
  expect(within(drawer).getByRole('img', { name: 'Image 1' })).toHaveAttribute('src', '/api/orgs/1/files/41')
  await userEvent.type(within(drawer).getByLabelText('Reply to the investor'), 'We are checking.')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Send reply' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Reply sent'))
  const post = fetchMock.mock.calls.find(([u, i]) =>
    String(u).endsWith('/tickets/7/messages') && (i as RequestInit | undefined)?.method === 'POST')
  expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({ body: 'We are checking.' })
  expect(within(drawer).getByText('Desk Admin (desk)')).toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close ticket' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Ticket closed'))
  expect(within(drawer).getByText('Closed. The investor can open it again by replying.')).toBeInTheDocument()
})

test('a notification link opens its ticket straight away', async () => {
  mockRoutes()
  render(<SupportTab orgId={1} control show="open" onDone={vi.fn()} initialTicket={7}
                     onDrawerClosed={vi.fn()} />)
  expect(await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })).toBeInTheDocument()
})

test('a later link is followed while mounted, and closing the drawer reports it', async () => {
  mockRoutes()
  const onDrawerClosed = vi.fn()
  const { rerender } = render(<SupportTab orgId={1} control show="open" onDone={vi.fn()}
                                          initialTicket={null} onDrawerClosed={onDrawerClosed} />)
  await screen.findByText('#7 Deposits')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  rerender(<SupportTab orgId={1} control show="open" onDone={vi.fn()} initialTicket={7}
                       onDrawerClosed={onDrawerClosed} />)
  const drawer = await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(onDrawerClosed).toHaveBeenCalledTimes(1)
})
```

Create `dashboard/src/pages/investors/TicketSubjectsCard.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import TicketSubjectsCard from './TicketSubjectsCard'
import { subjectFixture } from '../../test/portalFixtures'
import type { TicketSubject } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(refuse?: { status: number; body: unknown }) {
  let subjects: TicketSubject[] = [subjectFixture({ id: 2, label: 'Deposits' })]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : {}
    if (url.endsWith('/ticket-subjects') && method === 'POST') {
      if (refuse) return jsonResponse(refuse.body, refuse.status)
      const s = subjectFixture({ id: 3, ...body })
      subjects = [...subjects, s]
      return jsonResponse(s, 201)
    }
    const m = /\/ticket-subjects\/(\d+)$/.exec(url)
    if (m && method === 'PATCH') {
      subjects = subjects.map((s) => (s.id === Number(m[1]) ? { ...s, ...body } : s))
      return jsonResponse(subjects.find((s) => s.id === Number(m[1])))
    }
    if (m && method === 'DELETE') {
      subjects = subjects.filter((s) => s.id !== Number(m[1]))
      return new Response(null, { status: 204 })
    }
    if (url.endsWith('/ticket-subjects')) return jsonResponse(subjects)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodies = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) =>
  fetchMock.mock.calls
    .filter(([u, i]) => String(u).endsWith(fragment) && (i as RequestInit | undefined)?.method === method)
    .map(([, i]) => JSON.parse((i as RequestInit).body as string))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('adds, renames, disables and deletes subjects', async () => {
  const fetchMock = mockRoutes()
  render(<TicketSubjectsCard orgId={1} control />)
  expect(await screen.findByText('Deposits')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('New subject'), 'Withdrawals')
  await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
  await waitFor(() => expect(bodies(fetchMock, '/ticket-subjects', 'POST')).toEqual([{ label: 'Withdrawals', sort: 1 }]))
  expect(await screen.findByText('Subject added')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Rename Deposits' }))
  const name = screen.getByLabelText('New name for Deposits')
  await userEvent.clear(name)
  await userEvent.type(name, 'Funding')
  await userEvent.click(screen.getByRole('button', { name: 'Save name' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Disable Funding' }))
  await waitFor(() => expect(bodies(fetchMock, '/ticket-subjects/2', 'PATCH'))
    .toEqual([{ label: 'Funding' }, { enabled: false }]))
  expect(await screen.findByText('Disabled')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Delete Withdrawals' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete the subject Withdrawals?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete subject' }))
  await waitFor(() => expect(screen.queryByText('Withdrawals')).not.toBeInTheDocument())
  expect(screen.getByText('Subject deleted')).toBeInTheDocument()
})

test("the server's refusal shows inside the card", async () => {
  mockRoutes({ status: 409, body: { detail: 'a subject with this label already exists' } })
  render(<TicketSubjectsCard orgId={1} control />)
  await screen.findByText('Deposits')
  await userEvent.type(screen.getByLabelText('New subject'), 'deposits')
  await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
  expect(await screen.findByText('a subject with this label already exists')).toBeInTheDocument()
})

test('a viewer sees the subjects but no controls', async () => {
  mockRoutes()
  render(<TicketSubjectsCard orgId={1} control={false} />)
  await screen.findByText('Deposits')
  expect(screen.queryByRole('button', { name: 'Add subject' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Rename Deposits' })).not.toBeInTheDocument()
})
```

In `dashboard/src/pages/Requests.test.tsx`, add `threadFixture, ticketFixture,` to the `portalFixtures` import and append:

```tsx
test('the Support tab carries the waiting count and opens a linked ticket', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/requests/summary')) {
      return jsonResponse({ deposits: 0, withdrawals: 0, transfers: 0, payout_destinations: 0,
                            kyc: 0, account_requests: 0, tickets: 2, total: 2 })
    }
    if (url.endsWith('/tickets/7')) return jsonResponse(threadFixture({ id: 7 }))
    if (url.endsWith('/tickets')) {
      return jsonResponse([ticketFixture({ id: 7, email: 'inv@example.com', display_name: 'Ada' })])
    }
    return jsonResponse([])
  }))
  render(<MemoryRouter initialEntries={['/org/1/requests?tab=support&ticket=7']}><Requests /></MemoryRouter>)
  expect(await screen.findByRole('tab', { name: 'Support (2)' })).toHaveAttribute('aria-selected', 'true')
  expect(await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })).toBeInTheDocument()
})

/** Stands in for the bell: navigates while Requests stays mounted. */
function GoTo({ to }: { to: string }) {
  const navigate = useNavigate()
  return <button type="button" onClick={() => navigate(to)}>Follow link</button>
}

test('a support link followed while the page is open switches tab and opens the ticket every time', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/requests/summary')) {
      return jsonResponse({ deposits: 0, withdrawals: 0, transfers: 0, payout_destinations: 0,
                            kyc: 0, account_requests: 0, tickets: 1, total: 1 })
    }
    if (url.endsWith('/tickets/7')) return jsonResponse(threadFixture({ id: 7 }))
    if (url.endsWith('/tickets')) {
      return jsonResponse([ticketFixture({ id: 7, email: 'inv@example.com', display_name: 'Ada' })])
    }
    return jsonResponse([])
  }))
  render(
    <MemoryRouter initialEntries={['/org/1/requests']}>
      <GoTo to="/org/1/requests?tab=support&ticket=7" />
      <Requests />
    </MemoryRouter>,
  )
  expect(await screen.findByRole('tab', { name: /^Deposits/ })).toHaveAttribute('aria-selected', 'true')
  await userEvent.click(screen.getByRole('button', { name: 'Follow link' }))
  expect(await screen.findByRole('tab', { name: 'Support (1)' })).toHaveAttribute('aria-selected', 'true')
  const drawer = await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  // Closing dropped ?ticket, so the same link is a change again and reopens it.
  await userEvent.click(screen.getByRole('button', { name: 'Follow link' }))
  expect(await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })).toBeInTheDocument()
  // Picking another tab moves ?tab with it, so the link switches back.
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
  await userEvent.click(screen.getByRole('tab', { name: /^Deposits/ }))
  await userEvent.click(screen.getByRole('button', { name: 'Follow link' }))
  expect(await screen.findByRole('tab', { name: 'Support (1)' })).toHaveAttribute('aria-selected', 'true')
  expect(await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })).toBeInTheDocument()
})
```

and change its `import { MemoryRouter } from 'react-router-dom'` line to `import { MemoryRouter, useNavigate } from 'react-router-dom'`.

In `dashboard/src/pages/Investors.test.tsx`: replace every `getByRole('tab', { name: 'Payment methods' })` with `getByRole('tab', { name: 'Portal settings' })` (eleven places; the test titles may keep their words), and in `mockRoutes`, directly before `if (path.endsWith('/account-packages')) …`, add:

```tsx
    if (path.endsWith('/ticket-subjects')) return jsonResponse([])
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/pages/requests/SupportTab.test.tsx src/pages/investors/TicketSubjectsCard.test.tsx src/pages/Requests.test.tsx src/pages/Investors.test.tsx`
Expected: FAIL — the modules do not exist, there is no Support tab, and the Investors tab is still "Payment methods".

- [x] **Step 3: The desk Support tab**

Create `dashboard/src/pages/requests/SupportTab.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi, type ApiError } from '../../lib/api'
import { errorText, formatWhen } from '../../lib/format'
import { TEXTAREA, TICKET_STATUS_LABELS, TICKET_STATUS_TONES, ticketsQuery } from '../../lib/engagement'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import TicketMessages from '../support/TicketMessages'
import { Row, Section } from './RequestDetailsDrawer'
import type { DeskTabProps } from './VerificationTab'
import type { Ticket, TicketThread } from '../../lib/types'

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'

/**
 * The desk's tickets: the queue (open first), a search, and a drawer with
 * the thread, Reply and Close. Loads its own queue; the page's summary
 * refresh follows every action through onDone. `initialTicket` (?ticket=
 * from a support notification) opens that thread at once, and again
 * whenever it changes while the tab is mounted; closing the drawer calls
 * onDrawerClosed so the page can drop ?ticket from the URL.
 */
export default function SupportTab({ orgId, control, show, onDone, initialTicket, onDrawerClosed }:
  DeskTabProps & { initialTicket: number | null; onDrawerClosed: () => void }) {
  const [rows, setRows] = useState<Ticket[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [openId, setOpenId] = useState<number | null>(initialTicket)
  const [thread, setThread] = useState<TicketThread | null>(null)
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  // Latest-applied wins, separately for the queue and the open thread.
  const listSeq = useRef(0)
  const threadSeq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++listSeq.current
    try {
      const r = await orgApi<Ticket[]>(orgId, ticketsQuery('tickets', 'all', q))
      if (mine !== listSeq.current) return
      setRows(r); setLoadError(null)
    } catch (err) {
      if (mine !== listSeq.current) return
      setLoadError(errorText(err, 'Could not load the tickets'))
      setRows((r) => r ?? [])
    }
  }, [orgId, q])

  useEffect(() => { void load() }, [load])

  // A notification followed while this tab is already mounted changes only
  // initialTicket: open that thread too.
  useEffect(() => { if (initialTicket != null) setOpenId(initialTicket) }, [initialTicket])

  const closeDrawer = () => { setOpenId(null); onDrawerClosed() }

  useEffect(() => {
    const mine = ++threadSeq.current
    setThread(null); setReply(''); setDrawerError(null)
    if (openId == null) return
    orgApi<TicketThread>(orgId, `tickets/${openId}`).then(
      (t) => { if (mine === threadSeq.current) setThread(t) },
      (err) => { if (mine === threadSeq.current) setDrawerError(errorText(err, 'Could not load this ticket')) },
    )
  }, [orgId, openId])

  const run = async (fn: () => Promise<TicketThread>, done: string) => {
    setBusy(true); setDrawerError(null)
    try {
      const t = await fn()
      threadSeq.current++
      setThread(t); setReply('')
      await load()
      onDone(done)
    } catch (err) {
      setDrawerError(errorText(err, 'The action failed'))
      // 409: closed by someone else meanwhile -- show the queue as it is now.
      if ((err as ApiError).response?.status === 409) void load()
    } finally {
      setBusy(false)
    }
  }

  const send = (e: FormEvent) => {
    e.preventDefault()
    if (openId == null || !reply.trim()) return
    void run(() => orgApi<TicketThread>(orgId, `tickets/${openId}/messages`, {
      method: 'POST', body: JSON.stringify({ body: reply.trim() }) }), 'Reply sent')
  }

  const close = () => {
    if (openId == null) return
    void run(() => orgApi<TicketThread>(orgId, `tickets/${openId}/close`, { method: 'POST' }), 'Ticket closed')
  }

  if (rows == null) return <Loading lines={4} label="Loading tickets" />
  const visible = show === 'open' ? rows.filter((t) => t.status !== 'closed') : rows

  return (
    <>
      <form role="search" onSubmit={(e) => { e.preventDefault(); setQ(draft) }}
            className="flex flex-wrap items-end gap-2 p-3">
        <Input aria-label="Search tickets" placeholder="Subject or message" value={draft}
               onChange={(e) => setDraft(e.target.value)} />
        <Button type="submit" variant="secondary" size="sm">Search</Button>
      </form>
      {loadError && (
        <div className="p-3 space-y-2">
          <Banner kind="error">{loadError}</Banner>
          <Button size="sm" variant="secondary" onClick={() => { void load() }}>Retry</Button>
        </div>
      )}
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className={TH}>Last message</th>
            <th className={TH}>Investor</th>
            <th className={TH}>Ticket</th>
            <th className={TH}>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && !loadError && (
            <tr><td colSpan={5} className="text-center py-8 text-ink-faint">
              {show === 'open' ? 'No open tickets' : 'No tickets yet'}
            </td></tr>
          )}
          {visible.map((t) => (
            <tr key={t.id} className="border-b border-line last:border-0 align-top">
              <td data-label="Last message" className={`num ${TD}`}>{formatWhen(t.last_message_at)}</td>
              <td data-label="Investor" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{t.display_name ?? '—'}</div>
                  <div className="text-xs text-ink-soft">{t.email ?? ''}</div>
                </div>
              </td>
              <td data-label="Ticket" className={TD}>{`#${t.id} ${t.subject_label}`}</td>
              <td data-label="Status" className={TD}>
                <div className="flex flex-wrap gap-1.5">
                  <Badge tone={TICKET_STATUS_TONES[t.status]}>{TICKET_STATUS_LABELS[t.status]}</Badge>
                  {/* The server's rule (portal_support.WAITING_ON_DESK), the one requests/summary counts. */}
                  {t.waiting_on_desk && <Badge tone="warn">Waiting on desk</Badge>}
                </div>
              </td>
              <td className={TD}>
                <div className="flex justify-end">
                  <Button variant="ghost" size="sm" aria-label={`Open ticket ${t.id}`}
                          onClick={() => setOpenId(t.id)}>Open</Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Drawer open={openId != null} busy={busy} onClose={closeDrawer}
              title={thread ? `Ticket #${thread.id}: ${thread.subject_label}` : `Ticket #${openId ?? ''}`}>
        <div className="space-y-4">
          {drawerError && <Banner kind="error" onDismiss={() => setDrawerError(null)}>{drawerError}</Banner>}
          {thread == null ? (
            !drawerError && <Loading lines={4} label="Loading ticket" />
          ) : (
            <>
              <Section title="Investor">
                <Row label="Name" value={thread.display_name ?? '—'} />
                <Row label="Email" value={thread.email ?? '—'} />
                <Row label="Status" value={TICKET_STATUS_LABELS[thread.status]} />
              </Section>
              <TicketMessages messages={thread.messages} viewer="desk"
                              fileUrl={(id) => `/api/orgs/${orgId}/files/${id}`} />
              {control && thread.status !== 'closed' && (
                <form onSubmit={send} className="space-y-3">
                  <label className="block">
                    <span className="desk-label block mb-1">Reply</span>
                    <textarea aria-label="Reply to the investor" rows={4} maxLength={4000} value={reply}
                              disabled={busy} onChange={(e) => setReply(e.target.value)} className={TEXTAREA} />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" disabled={busy || !reply.trim()}>Send reply</Button>
                    <Button variant="secondary" tone="loss" disabled={busy} onClick={close}>Close ticket</Button>
                  </div>
                </form>
              )}
              {thread.status === 'closed' && (
                <p className="text-sm text-ink-soft">Closed. The investor can open it again by replying.</p>
              )}
            </>
          )}
        </div>
      </Drawer>
    </>
  )
}
```

In `dashboard/src/pages/requests/RequestTabs.tsx`:
- replace `export type DeskTab = RequestKind | 'kyc' | 'account_requests'` with `export type DeskTab = RequestKind | 'kyc' | 'account_requests' | 'support'` and the `DESK_TABS` line with `export const DESK_TABS: DeskTab[] = [...REQUEST_KINDS, 'kyc', 'account_requests', 'support']`; update its comment to `/** The desk's tabs: the four money queues, phase 2's two identity queues and phase 4's support tickets. */`;
- in `tabItems`, append `{ key: 'support', label: `Support (${n('tickets')})` },` after the account requests item.

In `dashboard/src/pages/Requests.tsx`:
- add `import SupportTab from './requests/SupportTab'`;
- replace `const [searchParams] = useSearchParams()` with `const [searchParams, setSearchParams] = useSearchParams()`;
- directly below the `tab` state (the `useState<DeskTab>(() => { … })` block) add:

```tsx
  const urlTab = searchParams.get('tab')
  const initialTicket = Number(searchParams.get('ticket')) || null
  // The page is keyed by pathname only, so a link followed while it is open
  // (a support notification from the bell) changes just the query string:
  // follow its tab. `initialTicket` is a dependency so a link back to the
  // tab already named in the URL still lands.
  useEffect(() => { if (isTab(urlTab)) setTab(urlTab) }, [urlTab, initialTicket])
  // ?tab mirrors the chosen tab, so the next link to ?tab=support is always
  // a change; a tab change also drops ?ticket.
  const chooseTab = (k: DeskTab) => {
    setTab(k)
    setSearchParams((p) => {
      const next = new URLSearchParams(p)
      next.set('tab', k)
      next.delete('ticket')
      return next
    }, { replace: true })
  }
  // Closing the ticket drawer drops ?ticket, so returning to the tab does
  // not reopen it and the same notification opens it again.
  const dropTicket = useCallback(() => {
    setSearchParams((p) => {
      const next = new URLSearchParams(p)
      next.delete('ticket')
      return next
    }, { replace: true })
  }, [setSearchParams])
```

- in the `<Tabs idBase="requests" …>` element replace `onChange={(k) => setTab(k as DeskTab)}` with `onChange={(k) => chooseTab(k as DeskTab)}`;
- after the `account_requests` tab block add:

```tsx
              {tab === 'support' && (
                <SupportTab key={orgId} orgId={orgId} control={control} show={show}
                            onDone={tabDone} initialTicket={initialTicket}
                            onDrawerClosed={dropTicket} />
              )}
```

- in the PageHeader subtitle replace `identities to verify and trading accounts to open.` with `identities to verify, trading accounts to open and support tickets to answer.`

- [x] **Step 4: The Ticket subjects card**

Create `dashboard/src/pages/investors/TicketSubjectsCard.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText } from '../../lib/format'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { TicketSubject } from '../../lib/types'

/**
 * What investors may raise a ticket about. Owns its own load, banners and
 * busy flag (like AccountsDrawer), so a refusal shows here, never on the
 * Investors page banner.
 */
export default function TicketSubjectsCard({ orgId, control }: { orgId: number; control: boolean }) {
  const [rows, setRows] = useState<TicketSubject[] | null>(null)
  const [label, setLabel] = useState('')
  const [renaming, setRenaming] = useState<{ id: number; label: string } | null>(null)
  const [deleting, setDeleting] = useState<TicketSubject | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++seq.current
    try {
      const r = await orgApi<TicketSubject[]>(orgId, 'ticket-subjects')
      if (mine === seq.current) setRows(Array.isArray(r) ? r : [])
    } catch (err) {
      if (mine !== seq.current) return
      setRows((x) => x ?? [])
      setError(errorText(err, 'Could not load the ticket subjects'))
    }
  }, [orgId])

  useEffect(() => { void load() }, [load])

  /** One mutation: busy, the card's banners, a reload. True on success. */
  const run = async (fn: () => Promise<unknown>, done: string): Promise<boolean> => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(done)
      await load()
      return true
    } catch (err) {
      setError(errorText(err, 'The action failed'))
      return false
    } finally {
      setBusy(false)
    }
  }

  const add = (e: FormEvent) => {
    e.preventDefault()
    if (!label.trim()) return
    void run(async () => {
      await orgApi(orgId, 'ticket-subjects', {
        method: 'POST', body: JSON.stringify({ label: label.trim(), sort: rows?.length ?? 0 }) })
      setLabel('')
    }, 'Subject added')
  }

  const patch = (s: TicketSubject, body: Partial<TicketSubject>, done: string) =>
    run(() => orgApi(orgId, `ticket-subjects/${s.id}`, { method: 'PATCH', body: JSON.stringify(body) }), done)

  const saveName = async (s: TicketSubject) => {
    if (renaming && await patch(s, { label: renaming.label.trim() }, 'Subject renamed')) setRenaming(null)
  }

  const remove = async () => {
    if (!deleting) return
    const s = deleting
    setDeleting(null)
    await run(() => orgApi(orgId, `ticket-subjects/${s.id}`, { method: 'DELETE' }), 'Subject deleted')
  }

  return (
    <Card title="Ticket subjects">
      <div className="space-y-3">
        <p className="text-sm text-ink-soft">
          What investors may raise a ticket about. A disabled subject is no longer offered; deleting
          one keeps the wording on tickets already raised.
        </p>
        {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
        {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
        {rows == null ? (
          <Loading lines={2} label="Loading ticket subjects" />
        ) : (
          <div className="inset">
            <ul className="divide-y divide-line">
              {rows.length === 0 && (
                <li className="px-4 py-6 text-center text-ink-faint">
                  No subjects yet — investors cannot raise a ticket until you add one.
                </li>
              )}
              {rows.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                  {renaming?.id === s.id ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <Input aria-label={`New name for ${s.label}`} maxLength={80} value={renaming.label}
                             onChange={(e) => setRenaming({ id: s.id, label: e.target.value })} />
                      <Button size="sm" disabled={busy || !renaming.label.trim()}
                              onClick={() => { void saveName(s) }}>Save name</Button>
                      <Button size="sm" variant="ghost" onClick={() => setRenaming(null)}>Cancel</Button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <span className="text-ink">{s.label}</span>
                      <Badge tone={s.enabled ? 'profit' : 'neutral'}>{s.enabled ? 'Enabled' : 'Disabled'}</Badge>
                    </span>
                  )}
                  {control && renaming?.id !== s.id && (
                    <span className="flex flex-wrap gap-2">
                      <Button variant="secondary" size="sm" disabled={busy} aria-label={`Rename ${s.label}`}
                              onClick={() => setRenaming({ id: s.id, label: s.label })}>Rename</Button>
                      <Button variant="secondary" size="sm" disabled={busy}
                              aria-label={`${s.enabled ? 'Disable' : 'Enable'} ${s.label}`}
                              onClick={() => { void patch(s, { enabled: !s.enabled },
                                                          s.enabled ? 'Subject disabled' : 'Subject enabled') }}>
                        {s.enabled ? 'Disable' : 'Enable'}
                      </Button>
                      <Button variant="ghost" tone="loss" size="sm" disabled={busy} aria-label={`Delete ${s.label}`}
                              onClick={() => setDeleting(s)}>Delete</Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {control && (
          <form onSubmit={add} className="flex flex-wrap items-end gap-2">
            <label className="block">
              <span className="desk-label block mb-1">New subject</span>
              <Input aria-label="New subject" maxLength={80} placeholder="Deposits" value={label}
                     onChange={(e) => setLabel(e.target.value)} />
            </label>
            <Button type="submit" size="sm" disabled={busy || !label.trim()}>Add subject</Button>
          </form>
        )}
      </div>
      <ConfirmDialog open={deleting != null} title={`Delete the subject ${deleting?.label ?? ''}?`}
                     confirmLabel="Delete subject" danger busy={busy}
                     onConfirm={() => { void remove() }} onCancel={() => setDeleting(null)}>
        <p>Investors can no longer pick it. Tickets already raised keep their subject.</p>
      </ConfirmDialog>
    </Card>
  )
}
```

- [x] **Step 5: The Portal settings tab**

In `dashboard/src/pages/Investors.tsx`, change the tab item `{ key: 'methods', label: 'Payment methods' }` to `{ key: 'methods', label: 'Portal settings' }`.

In `dashboard/src/pages/investors/PaymentMethodsTab.tsx`:
- add `import TicketSubjectsCard from './TicketSubjectsCard'`;
- change `<Card title="Portal settings">` to `<Card title="Withdrawal and account rules">` (the tab now carries the name "Portal settings"; two of the same words on one screen would make the heading ambiguous) and the comment `// Portal settings: the same dirty guard …` to `// Withdrawal and account rules: the same dirty guard …`;
- directly after that Card's closing `</Card>` add `<TicketSubjectsCard orgId={orgId} control={control} />`.

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/requests src/pages/investors src/pages/Requests.test.tsx src/pages/Investors.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [x] **Step 7: Commit**

```bash
git add dashboard/src/pages/requests/SupportTab.tsx dashboard/src/pages/requests/SupportTab.test.tsx dashboard/src/pages/requests/RequestTabs.tsx dashboard/src/pages/Requests.tsx dashboard/src/pages/Requests.test.tsx dashboard/src/pages/investors/TicketSubjectsCard.tsx dashboard/src/pages/investors/TicketSubjectsCard.test.tsx dashboard/src/pages/investors/PaymentMethodsTab.tsx dashboard/src/pages/Investors.tsx dashboard/src/pages/Investors.test.tsx
git commit -m "feat(dashboard): the desk answers tickets from Requests; ticket subjects under Portal settings

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 18: Investor Bonus page; Transfer offers Credit

**Files:**
- Create: `dashboard/src/pages/investor/InvestorBonus.tsx`, `dashboard/src/pages/investor/InvestorBonus.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorTransfer.tsx`, `dashboard/src/pages/investor/InvestorTransfer.test.tsx`
- Modify: `dashboard/src/App.tsx`, `dashboard/src/pages/groups/investor.ts`, `dashboard/src/components/layout/nav.ts`, `dashboard/src/components/layout/nav.test.ts`

**Interfaces:**
- Consumes: `GET investor/bonuses` (Task 10), `investor/summary` (`wallets.credit`), credit -> account transfers (Task 11); `BONUS_SOURCES`, `BONUS_SOURCE_LABELS`; `Money`; `bonusFixture`, `summaryFixture`.
- Produces: `InvestorBonus` (default); route `invest/bonus`; investor nav Money `Bonus`; `InvestorTransfer` offers `wallet:credit`, `pairAllowed('wallet:credit', 'account:<id>')`.

- [x] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investor/InvestorBonus.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorBonus from './InvestorBonus'
import { mockUseOrg } from '../../test/orgMock'
import { bonusFixture, summaryFixture } from '../../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary = summaryFixture({
  wallets: { ...summaryFixture().wallets, credit: { balance: 75, on_hold: 25, available: 50 } },
})
const reversed = bonusFixture({ id: 5, source: 'manual', source_id: null, amount: -5, note: 'Reversed' })
const deposit = bonusFixture({ id: 4 })

function mockRoutes(opts: { slowAll?: Promise<Response> } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.includes('/investor/bonuses?')) return jsonResponse([deposit])
    if (url.endsWith('/investor/bonuses')) return opts.slowAll ?? jsonResponse([reversed, deposit])
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows the Credit wallet and the history, filtered by source and dates', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorBonus /></MemoryRouter>)
  expect(await screen.findByRole('heading', { level: 1, name: 'Bonus' })).toBeInTheDocument()
  expect(await screen.findByText('75.00 USD')).toBeInTheDocument()
  expect(screen.getByText('50.00 USD')).toBeInTheDocument()
  expect(screen.getByText('Reversed')).toBeInTheDocument()
  expect(screen.getByText('deposit #12')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Move to a trading account' }))
    .toHaveAttribute('href', '/org/1/invest/transfer')
  await userEvent.selectOptions(screen.getByLabelText('Source'), 'deposit')
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-01' } })
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(screen.queryByText('Reversed')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investor/bonuses?source=deposit&from=2026-09-01'))).toBe(true)
})

test('a late answer for an older filter never lands', async () => {
  let release!: (r: Response) => void
  mockRoutes({ slowAll: new Promise<Response>((res) => { release = res }) })
  render(<MemoryRouter><InvestorBonus /></MemoryRouter>)
  await userEvent.selectOptions(await screen.findByLabelText('Source'), 'deposit')
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(await screen.findByText('deposit #12')).toBeInTheDocument()
  await act(async () => { release(jsonResponse([reversed, deposit])) })
  expect(screen.queryByText('Reversed')).not.toBeInTheDocument()
})
```

In `dashboard/src/pages/investor/InvestorTransfer.test.tsx`, replace the first test with:

```tsx
test('transferOptions lists the four wallets plus each account; pairAllowed follows the rules', () => {
  expect(transferOptions(linked).map((o) => o.value)).toEqual(
    ['wallet:main', 'wallet:credit', 'wallet:pamm', 'wallet:social', 'account:1001'])
  expect(transferOptions(unlinked).map((o) => o.value)).toEqual(
    ['wallet:main', 'wallet:credit', 'wallet:pamm', 'wallet:social'])
  expect(pairAllowed('wallet:main', 'account:1001')).toBe(true)
  expect(pairAllowed('account:1001', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:pamm', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:social', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:credit', 'account:1001')).toBe(true)
  expect(pairAllowed('wallet:credit', 'wallet:main')).toBe(false)
  expect(pairAllowed('account:1001', 'wallet:credit')).toBe(false)
  expect(pairAllowed('wallet:main', 'wallet:pamm')).toBe(false)
  expect(pairAllowed('wallet:pamm', 'account:1001')).toBe(false)
  expect(pairAllowed('wallet:main', 'wallet:main')).toBe(false)
})
```

and append:

```tsx
test('bonus credit can only go to a trading account', async () => {
  const withCredit: InvestorSummary = {
    ...linked, wallets: { ...linked.wallets, credit: { balance: 40, on_hold: 0, available: 40 } },
  }
  const fetchMock = mockRoutes({ summary: withCredit })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.selectOptions(await screen.findByLabelText('From'), 'wallet:credit')
  const to = screen.getByLabelText('To')
  expect(within(to).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['account:1001'])
  expect(screen.getByText('40.00 USD')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Amount in USD'), '40')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 40.00 USD from Credit wallet to Trading account?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    source: { kind: 'wallet', wallet: 'credit' }, target: { kind: 'account', account_id: 1001 },
    amount: '40', mpin: '123456',
  })
})
```

In `nav.test.ts`, add `['Bonus', '/org/7/invest/bonus'],` as the last row of the Money expectation and change `toHaveLength(15)` to `toHaveLength(16)`.

- [x] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorBonus.test.tsx src/pages/investor/InvestorTransfer.test.tsx src/components/layout/nav.test.ts`
Expected: FAIL — no `InvestorBonus`; the transfer options have no credit wallet; no Bonus link.

- [x] **Step 3: The Bonus page**

Create `dashboard/src/pages/investor/InvestorBonus.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { ACCOUNT_CURRENCY } from '../../lib/investor'
import { BONUS_SOURCES, BONUS_SOURCE_LABELS } from '../../lib/engagement'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import type { Bonus, InvestorSummary } from '../../lib/types'

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'
const NO_FILTER = { source: '', from: '', to: '' }

/** The bonuses tail with only the filters that are set. */
function bonusesQuery(f: typeof NO_FILTER): string {
  const p = new URLSearchParams()
  if (f.source) p.set('source', f.source)
  if (f.from) p.set('from', f.from)
  if (f.to) p.set('to', f.to)
  const s = p.toString()
  return s ? `investor/bonuses?${s}` : 'investor/bonuses'
}

/**
 * The investor's bonuses: the Credit wallet they land in (which only ever
 * moves on to a trading account) and the history with Source and date
 * filters.
 */
export default function InvestorBonus() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<Bonus[] | null>(null)
  const [draft, setDraft] = useState(NO_FILTER)
  const [applied, setApplied] = useState(NO_FILTER)
  const [error, setError] = useState<string | null>(null)
  // Bumped per load: Apply (or an org switch) drops an older answer.
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    Promise.all([
      orgApi<InvestorSummary>(orgId, 'investor/summary'),
      orgApi<Bonus[]>(orgId, bonusesQuery(applied)),
    ]).then(
      ([s, b]) => {
        if (mine !== seq.current) return
        setSummary(s); setRows(b); setError(null)
      },
      (err) => {
        if (mine !== seq.current) return
        setRows((r) => r ?? [])
        setError(errorText(err, 'Could not load your bonuses'))
      },
    )
  }, [orgId, applied])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  const credit = summary?.wallets.credit
  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Bonus"
        subtitle="Bonuses are paid into your Credit wallet. Credit can only be moved to one of your trading accounts; it cannot be withdrawn."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {summary && (
        <Card title="Credit wallet" actions={
          <Button variant="secondary" size="sm" to={`/org/${orgId}/invest/transfer`}>Move to a trading account</Button>
        }>
          <div className="inset p-4 grid gap-3 sm:grid-cols-2 text-sm">
            <div>
              <div className="desk-label">Balance</div>
              <div className="text-2xl font-semibold text-ink"><Money value={credit?.balance ?? null} unit={unit} /></div>
            </div>
            <div>
              <div className="desk-label">Available to move</div>
              <div className="text-ink"><Money value={credit?.available ?? null} unit={unit} /></div>
            </div>
          </div>
        </Card>
      )}
      {/* The filters stay usable while a load is in flight; the seq ref
          drops whichever answer is no longer the newest. */}
      <Card title="Bonus history" inset>
        <form onSubmit={(e) => { e.preventDefault(); setApplied(draft) }}
              className="flex flex-wrap items-end gap-3 p-4 border-b border-line">
          <label className="block">
            <span className="desk-label block mb-1">Source</span>
            <Select aria-label="Source" value={draft.source}
                    onChange={(e) => setDraft({ ...draft, source: e.target.value })}>
              <option value="">All</option>
              {BONUS_SOURCES.map((s) => <option key={s} value={s}>{BONUS_SOURCE_LABELS[s]}</option>)}
            </Select>
          </label>
          <label className="block">
            <span className="desk-label block mb-1">From</span>
            <Input type="date" aria-label="From date" value={draft.from}
                   onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
          </label>
          <label className="block">
            <span className="desk-label block mb-1">To</span>
            <Input type="date" aria-label="To date" value={draft.to}
                   onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
          </label>
          <Button type="submit" variant="secondary" size="sm">Apply</Button>
        </form>
        {rows == null ? (
          <Loading lines={3} label="Loading bonuses" className="p-4" />
        ) : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className={TH}>Date</th>
                  <th className={TH}>Source</th>
                  <th className={`${TH} text-right`}>Amount</th>
                  <th className={TH}>Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={4} className="text-center py-8 text-ink-faint">No bonuses yet</td></tr>
                )}
                {rows.map((b) => (
                  <tr key={b.id} className="border-b border-line last:border-0">
                    <td data-label="Date" className={`num ${TD}`}>{formatWhen(b.created_at)}</td>
                    <td data-label="Source" className={TD}>{BONUS_SOURCE_LABELS[b.source]}</td>
                    <td data-label="Amount" className={`${TD} text-right`}><Money value={b.amount} unit={b.currency} signed /></td>
                    <td data-label="Note" className={`${TD} text-ink-soft`}>{b.note ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
```

- [x] **Step 4: Transfer offers Credit**

In `dashboard/src/pages/investor/InvestorTransfer.tsx`:
- replace the `PAIRS` comment and constant with

```tsx
// The allowed pairs (portal_ledger.TRANSFER_PAIRS): "account" is any of the
// investor's trading accounts; bonus credit moves only to a trading account.
const PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['main', 'account'], ['account', 'main'], ['pamm', 'main'], ['social', 'main'], ['credit', 'account'],
]
```

- in `transferOptions`, change `const wallets: WalletKind[] = ['main', 'pamm', 'social']` to `const wallets: WalletKind[] = ['main', 'credit', 'pamm', 'social']`;
- change the PageHeader subtitle to `Move money between your wallets and your trading accounts. Bonus credit moves only to a trading account. Wallet-to-wallet moves complete at once; moves to or from a trading account are done by an admin.`;
- replace the `targets.length === 0` hint's text with
  `{fromOpt.ref.wallet === 'credit' ? 'Bonus credit moves only to a trading account; you have none linked yet.' : 'Link a trading account to move wallet money into it; PAMM and Social wallets can still move to My wallet.'}`.

- [x] **Step 5: Route and nav**

In `pages/groups/investor.ts` append `export { default as InvestorBonus } from '../investor/InvestorBonus'`. In `App.tsx` add `const InvestorBonus = pick(investor, 'InvestorBonus')` and `<Route path="invest/bonus" element={<InvestorBonus />} />` after `invest/support`. In `nav.ts`, append to the investor Money items after Payout accounts:

```ts
        { path: `${p}/bonus`, label: 'Bonus' },
```

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/investor src/components/layout/nav.test.ts src/components/Layout.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [x] **Step 7: Commit**

```bash
git add dashboard/src/pages/investor/InvestorBonus.tsx dashboard/src/pages/investor/InvestorBonus.test.tsx dashboard/src/pages/investor/InvestorTransfer.tsx dashboard/src/pages/investor/InvestorTransfer.test.tsx dashboard/src/App.tsx dashboard/src/pages/groups/investor.ts dashboard/src/components/layout/nav.ts dashboard/src/components/layout/nav.test.ts
git commit -m "feat(dashboard): investor Bonus page; bonus credit moves to a trading account from Transfer

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 19: Bonus rules card and Grant bonus dialog

**Files:**
- Create: `dashboard/src/pages/investors/BonusRulesCard.tsx`, `dashboard/src/pages/investors/BonusRulesCard.test.tsx`
- Create: `dashboard/src/pages/investors/GrantBonusDialog.tsx`, `dashboard/src/pages/investors/GrantBonusDialog.test.tsx`
- Modify: `dashboard/src/pages/investors/AdjustDialog.tsx` (exports `SIGNED_AMOUNT` and `checkSignedAmount`)
- Modify: `dashboard/src/pages/investors/PaymentMethodsTab.tsx`, `dashboard/src/pages/Investors.tsx`, `dashboard/src/pages/Investors.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT bonus-rules` (Task 8), `POST investors/{id}/bonuses` (Task 10); `PinConfirmDialog`; `bonusRulesFixture`, `bonusFixture`, `investorRowFixture`.
- Produces: `BonusRulesCard` (default; `{ orgId; control }`), `GrantBonusDialog` (default; `{ orgId; investor; onCancel; onGranted }`); the Investors row menu item `Grant bonus`; `AdjustDialog.tsx` named exports `SIGNED_AMOUNT` and `checkSignedAmount(amount, note) -> string` (the one client-side signed-amount check both MPIN dialogs run; the adjustment's messages unchanged).

- [x] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investors/BonusRulesCard.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import BonusRulesCard from './BonusRulesCard'
import { bonusRulesFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(refuse?: { status: number; body: unknown }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/bonus-rules') && init?.method === 'PUT') {
      if (refuse) return jsonResponse(refuse.body, refuse.status)
      const body = JSON.parse(init.body as string)
      return jsonResponse(bonusRulesFixture({
        signup_enabled: body.signup_enabled, signup_amount: Number(body.signup_amount),
        deposit_cap: body.deposit_cap == null ? null : Number(body.deposit_cap) }))
    }
    if (url.endsWith('/bonus-rules')) return jsonResponse(bonusRulesFixture())
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('loads the rules and saves them as typed', async () => {
  const fetchMock = mockRoutes()
  render(<BonusRulesCard orgId={1} control />)
  expect(await screen.findByLabelText('Deposit bonus on')).toBeChecked()
  expect(screen.getByLabelText('Deposit bonus %')).toHaveValue('10')
  expect(screen.getByLabelText('Deposit bonus cap')).toHaveValue('100')
  await userEvent.click(screen.getByLabelText('Welcome bonus on'))
  await userEvent.clear(screen.getByLabelText('Welcome bonus amount'))
  await userEvent.type(screen.getByLabelText('Welcome bonus amount'), '50')
  await userEvent.clear(screen.getByLabelText('Deposit bonus cap'))
  await userEvent.click(screen.getByRole('button', { name: 'Save bonus rules' }))
  await waitFor(() => {
    const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
      signup_enabled: true, signup_amount: '50', kyc_enabled: false, kyc_amount: '0',
      deposit_enabled: true, deposit_pct: '10', deposit_cap: null })
  })
  expect(await screen.findByText('Bonus rules saved')).toBeInTheDocument()
  expect(screen.getByLabelText('Deposit bonus cap')).toHaveValue('')
})

test("the server's refusal shows inside the card", async () => {
  mockRoutes({ status: 400, body: { detail: 'signup_amount must be above 0 while the signup rule is on' } })
  render(<BonusRulesCard orgId={1} control />)
  await userEvent.click(await screen.findByLabelText('Welcome bonus on'))
  await userEvent.click(screen.getByRole('button', { name: 'Save bonus rules' }))
  expect(await screen.findByText('signup_amount must be above 0 while the signup rule is on')).toBeInTheDocument()
})

test('a viewer sees the rules but cannot change them', async () => {
  mockRoutes()
  render(<BonusRulesCard orgId={1} control={false} />)
  expect(await screen.findByLabelText('Deposit bonus %')).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Save bonus rules' })).not.toBeInTheDocument()
})
```

Create `dashboard/src/pages/investors/GrantBonusDialog.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import GrantBonusDialog from './GrantBonusDialog'
import { bonusFixture, investorRowFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const investor = investorRowFixture({
  user_id: 5, display_name: 'Ada Investor', balances: { main: 0, credit: 20, pamm: 0, social: 0 },
})

async function enterPin(dialog: HTMLElement, pin: string) {
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('pays a bonus with the admin MPIN', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse(bonusFixture({ id: 9, source: 'manual', source_id: null, amount: 25, note: 'Promo' }), 201))
  vi.stubGlobal('fetch', fetchMock)
  const onGranted = vi.fn()
  render(<GrantBonusDialog orgId={1} investor={investor} onCancel={vi.fn()} onGranted={onGranted} />)
  const dialog = await screen.findByRole('dialog', { name: 'Grant Ada Investor a bonus' })
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '25')
  await userEvent.type(within(dialog).getByLabelText('Bonus note'), 'Promo')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  await waitFor(() => expect(onGranted).toHaveBeenCalledWith(expect.objectContaining({ id: 9, amount: 25 })))
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/investors/5/bonuses')
  expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string))
    .toEqual({ amount: '25', note: 'Promo', mpin: '123456' })
})

test('a zero amount never reaches the server; a refused claw-back shows its reason', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(
    { detail: 'a claw-back cannot take the Credit wallet below zero (available 20.00)' }, 400))
  vi.stubGlobal('fetch', fetchMock)
  render(<GrantBonusDialog orgId={1} investor={investor} onCancel={vi.fn()} onGranted={vi.fn()} />)
  const dialog = await screen.findByRole('dialog', { name: 'Grant Ada Investor a bonus' })
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '0')
  await userEvent.type(within(dialog).getByLabelText('Bonus note'), 'x')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  expect(await within(dialog).findByText('amount must not be zero')).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()
  await userEvent.clear(within(dialog).getByLabelText('Bonus amount'))
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '-25')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  expect(await within(dialog).findByText(
    'a claw-back cannot take the Credit wallet below zero (available 20.00)')).toBeInTheDocument()
})
```

In `dashboard/src/pages/Investors.test.tsx`, in `mockRoutes` directly before `if (path.endsWith('/account-packages')) …` add:

```tsx
    if (path.endsWith('/bonus-rules')) {
      return jsonResponse({ signup_enabled: false, signup_amount: 0, kyc_enabled: false, kyc_amount: 0,
        deposit_enabled: false, deposit_pct: 0, deposit_cap: null, updated_at: null })
    }
    if (path.endsWith('/investors/5/bonuses') && method === 'POST') {
      return jsonResponse({ id: 9, source: 'manual', source_id: null, amount: 25, note: 'Promo',
        created_at: '2026-10-05T10:00:00Z', currency: 'USD' }, 201)
    }
```

and append:

```tsx
test('Grant bonus pays into the Credit wallet with the admin MPIN', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Grant bonus')
  const dialog = await screen.findByRole('dialog', { name: 'Grant Ada Investor a bonus' })
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '25')
  await userEvent.type(within(dialog).getByLabelText('Bonus note'), 'Promo')
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  expect(await screen.findByText("Bonus of 25.00 paid to Ada Investor's Credit wallet")).toBeInTheDocument()
  expect(bodyOf(fetchMock, '/investors/5/bonuses', 'POST')).toEqual({ amount: '25', note: 'Promo', mpin: '123456' })
})

test('the Portal settings tab carries the bonus rules and the ticket subjects', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  expect(await screen.findByRole('heading', { name: 'Bonus rules' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Ticket subjects' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Withdrawal and account rules' })).toBeInTheDocument()
})
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/pages/investors src/pages/Investors.test.tsx`
Expected: FAIL — the two components do not exist; no "Grant bonus" menu item.

- [x] **Step 3: The Bonus rules card**

Create `dashboard/src/pages/investors/BonusRulesCard.tsx`:

```tsx
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText } from '../../lib/format'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { BonusRules } from '../../lib/types'

interface Form {
  signup_enabled: boolean; signup_amount: string
  kyc_enabled: boolean; kyc_amount: string
  deposit_enabled: boolean; deposit_pct: string; deposit_cap: string
}

type Flag = 'signup_enabled' | 'kyc_enabled' | 'deposit_enabled'
type Field = 'signup_amount' | 'kyc_amount' | 'deposit_pct'

const RULES: { flag: Flag; field: Field; name: string; fieldLabel: string; hint: string }[] = [
  { flag: 'signup_enabled', field: 'signup_amount', name: 'Welcome bonus', fieldLabel: 'Welcome bonus amount',
    hint: 'Paid once when someone joins as an investor.' },
  { flag: 'kyc_enabled', field: 'kyc_amount', name: 'Verification bonus', fieldLabel: 'Verification bonus amount',
    hint: 'Paid once when you approve their identity.' },
  { flag: 'deposit_enabled', field: 'deposit_pct', name: 'Deposit bonus', fieldLabel: 'Deposit bonus %',
    hint: 'A share of every deposit you confirm, rounded to the cent.' },
]

function formOf(r: BonusRules): Form {
  return {
    signup_enabled: r.signup_enabled, signup_amount: String(r.signup_amount),
    kyc_enabled: r.kyc_enabled, kyc_amount: String(r.kyc_amount),
    deposit_enabled: r.deposit_enabled, deposit_pct: String(r.deposit_pct),
    deposit_cap: r.deposit_cap == null ? '' : String(r.deposit_cap),
  }
}

function isRules(r: unknown): r is BonusRules {
  return typeof r === 'object' && r != null && typeof (r as BonusRules).signup_enabled === 'boolean'
}

/**
 * The org's bonus rules: welcome, verification and deposit, each switched
 * on or off. Amounts go to the server as typed so its own messages show.
 * Owns its own load and banners; no poll, so nothing overwrites a form the
 * admin is editing.
 */
export default function BonusRulesCard({ orgId, control }: { orgId: number; control: boolean }) {
  const [form, setForm] = useState<Form | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    setForm(null)
    orgApi<BonusRules>(orgId, 'bonus-rules').then(
      (r) => { if (mine === seq.current && isRules(r)) setForm(formOf(r)) },
      (err) => { if (mine === seq.current) setError(errorText(err, 'Could not load the bonus rules')) },
    )
  }, [orgId])

  const edit = (patch: Partial<Form>) => setForm((f) => (f ? { ...f, ...patch } : f))

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!form) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const saved = await orgApi<BonusRules>(orgId, 'bonus-rules', {
        method: 'PUT',
        body: JSON.stringify({ ...form, deposit_cap: form.deposit_cap.trim() === '' ? null : form.deposit_cap.trim() }),
      })
      setForm(formOf(saved))
      setNotice('Bonus rules saved')
    } catch (err) {
      setError(errorText(err, 'Could not save the bonus rules'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Bonus rules">
      <form onSubmit={save} className="space-y-4">
        <p className="text-sm text-ink-soft">
          Bonuses are paid into the investor's Credit wallet, which can only move to one of their
          trading accounts. A rule counts from the moment you switch it on; nothing is paid backwards.
        </p>
        {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
        {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
        {form == null ? (
          !error && <Loading lines={3} label="Loading bonus rules" />
        ) : (
          <>
            {RULES.map((r) => (
              <div key={r.flag} className="flex flex-wrap items-end gap-3">
                <label className="flex items-center gap-2 text-sm text-ink w-48">
                  <input type="checkbox" className="accent-brand" aria-label={`${r.name} on`}
                         checked={form[r.flag]} disabled={!control}
                         onChange={(e) => edit({ [r.flag]: e.target.checked } as Partial<Form>)} />
                  {r.name}
                </label>
                <label className="block w-40">
                  <span className="desk-label block mb-1">{r.field === 'deposit_pct' ? '% of the deposit' : 'Amount (USD)'}</span>
                  <Input aria-label={r.fieldLabel} num inputMode="decimal" disabled={!control}
                         value={form[r.field]} onChange={(e) => edit({ [r.field]: e.target.value } as Partial<Form>)} />
                </label>
                <span className="text-xs text-ink-soft">{r.hint}</span>
              </div>
            ))}
            <label className="block w-40">
              <span className="desk-label block mb-1">Deposit bonus cap (USD)</span>
              <Input aria-label="Deposit bonus cap" num inputMode="decimal" placeholder="No cap" disabled={!control}
                     value={form.deposit_cap} onChange={(e) => edit({ deposit_cap: e.target.value })} />
            </label>
            {control && <Button type="submit" disabled={busy}>Save bonus rules</Button>}
          </>
        )}
      </form>
    </Card>
  )
}
```

- [x] **Step 4: The Grant bonus dialog**

In `dashboard/src/pages/investors/AdjustDialog.tsx`, replace

```tsx
/** A signed amount with at most two decimals: "-25.00", "100", "+12.5". */
const SIGNED_AMOUNT = /^[-+]?\d+(\.\d{1,2})?$/
```

with

```tsx
/** A signed amount with at most two decimals: "-25.00", "100", "+12.5". */
export const SIGNED_AMOUNT = /^[-+]?\d+(\.\d{1,2})?$/

/**
 * The checks every signed-amount MPIN dialog (Adjust, Grant bonus) runs
 * before it posts. Throws the message PinConfirmDialog shows in its error
 * line; answers the trimmed amount.
 */
export function checkSignedAmount(amount: string, note: string): string {
  const raw = amount.trim()
  if (!SIGNED_AMOUNT.test(raw)) {
    throw new Error('Enter a signed amount with at most two decimals, for example -25.00 or 100')
  }
  if (Number(raw) === 0) throw new Error('amount must not be zero')
  if (note.trim() === '') throw new Error('A note is required')
  return raw
}
```

and in its `confirm`, replace

```tsx
    const raw = amount.trim()
    if (!SIGNED_AMOUNT.test(raw)) {
      throw new Error('Enter a signed amount with at most two decimals, for example -25.00 or 100')
    }
    if (Number(raw) === 0) throw new Error('amount must not be zero')
    if (note.trim() === '') throw new Error('A note is required')
    setBusy(true)
```

with

```tsx
    const raw = checkSignedAmount(amount, note)
    setBusy(true)
```

(its tests keep passing: the messages are the same).

Create `dashboard/src/pages/investors/GrantBonusDialog.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { money } from '../../lib/format'
import Input from '../../components/Input'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import { checkSignedAmount } from './AdjustDialog'
import type { Bonus, InvestorRow } from '../../lib/types'

/**
 * Pays a bonus into the investor's Credit wallet (negative: takes one back,
 * never below zero), confirmed with the ADMIN's own MPIN. Validation
 * failures are thrown so PinConfirmDialog shows them and clears the PIN,
 * the same path as AdjustDialog.
 */
export default function GrantBonusDialog({ orgId, investor, onCancel, onGranted }: {
  orgId: number
  investor: InvestorRow | null
  onCancel: () => void
  onGranted: (bonus: Bonus) => void | Promise<void>
}) {
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // A fresh form for every investor; nothing typed for one carries to the next.
  useEffect(() => { setAmount(''); setNote('') }, [investor?.user_id])

  const confirm = async (mpin: string) => {
    if (!investor) return
    // The same checks and messages as AdjustDialog (one copy, exported there).
    const raw = checkSignedAmount(amount, note)
    setBusy(true)
    try {
      // redirectOn401 off: a wrong MPIN stays an inline error.
      const bonus = await api<Bonus>(
        `/api/orgs/${orgId}/investors/${investor.user_id}/bonuses`,
        { method: 'POST', body: JSON.stringify({ amount: raw, note: note.trim(), mpin }) },
        { redirectOn401: false },
      )
      await onGranted(bonus)
    } finally {
      setBusy(false)
    }
  }

  return (
    <PinConfirmDialog
      open={investor != null}
      title={investor ? `Grant ${investor.display_name} a bonus` : ''}
      confirmLabel="Pay bonus"
      busy={busy}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <p>
        Pays into the Credit wallet{investor ? ` (now ${money(investor.balances.credit)})` : ''}. A
        negative amount takes a bonus back, never below zero. The investor is notified either way.
      </p>
      <label className="block">
        <span className="desk-label block mb-1">Amount (USD, signed)</span>
        <Input aria-label="Bonus amount" num inputMode="decimal" placeholder="25.00" value={amount}
               onChange={(e) => setAmount(e.target.value)} />
      </label>
      <label className="block">
        <span className="desk-label block mb-1">Note</span>
        <Input aria-label="Bonus note" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
    </PinConfirmDialog>
  )
}
```

- [x] **Step 5: Wire both into Investors**

In `dashboard/src/pages/investors/PaymentMethodsTab.tsx`, add `import BonusRulesCard from './BonusRulesCard'` and render `<BonusRulesCard orgId={orgId} control={control} />` directly after the `Withdrawal and account rules` Card (before `<TicketSubjectsCard … />`).

In `dashboard/src/pages/Investors.tsx`:
- add `import GrantBonusDialog from './investors/GrantBonusDialog'`;
- add state `const [bonusFor, setBonusFor] = useState<InvestorRow | null>(null)` below `adjustFor`;
- add to the row `Menu` items after `adjust`: `{ key: 'bonus', label: 'Grant bonus', disabled: busy, onSelect: () => setBonusFor(r) },`;
- after `<AdjustDialog … />` add:

```tsx
      <GrantBonusDialog orgId={orgId} investor={bonusFor} onCancel={() => setBonusFor(null)}
                        onGranted={async (b) => {
                          const name = bonusFor?.display_name ?? 'the investor'
                          setBonusFor(null)
                          setError(null)
                          setNotice(b.amount > 0
                            ? `Bonus of ${money(b.amount)} paid to ${name}'s Credit wallet`
                            : `Bonus of ${money(-b.amount)} taken back from ${name}'s Credit wallet`)
                          await refresh()
                        }} />
```

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/investors src/pages/Investors.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors, no `act(...)` warning.

- [x] **Step 7: Commit**

```bash
git add dashboard/src/pages/investors/BonusRulesCard.tsx dashboard/src/pages/investors/BonusRulesCard.test.tsx dashboard/src/pages/investors/GrantBonusDialog.tsx dashboard/src/pages/investors/GrantBonusDialog.test.tsx dashboard/src/pages/investors/AdjustDialog.tsx dashboard/src/pages/investors/PaymentMethodsTab.tsx dashboard/src/pages/Investors.tsx dashboard/src/pages/Investors.test.tsx
git commit -m "feat(dashboard): bonus rules card and the Grant bonus dialog

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 20: Gates and docs

**Files:**
- Modify: `README.md` (the "Upgrading with a migration" section)
- Modify: `docs/superpowers/specs/2026-10-05-client-portal-phase-4-design.md` (status line)
- Modify: `docs/superpowers/plans/2026-10-05-client-portal-phase-4.md` (tick the checkboxes)

**Interfaces:**
- Consumes: everything above.
- Produces: a green branch and the deploy notes.

- [x] **Step 1: Full dashboard gate**

From `dashboard/`:

Run: `npm test` (or the worker-capped form from the Global Constraints)
Expected: palette prover passes, no type errors, every test passes, and `npx vitest run 2>&1 | grep -c "not wrapped in act"` prints `0`.

Run: `npm run build`
Expected: build succeeds.

- [x] **Step 2: Full API suite**

From `api/` with the env of the Global Constraints:

Run: `"$PY" -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 known `test_events_ws.py` errors and the 1 EA-download CRLF failure.

- [x] **Step 3: `copier/` untouched; no stray caller left**

Run: `git diff --stat main..HEAD -- copier/`
Expected: prints nothing.

Run: `grep -rn "notify_investor" api/src api/tests`
Expected: prints nothing.

- [x] **Step 4: README runbook**

In `README.md`, directly after the paragraph that starts "Migration 024 (client portal phase 3", add:

```markdown
Migration 025 (client portal phase 4: notifications, support tickets,
bonuses, settings) is additive -- eight new tables, nothing else touched --
so the same sequence applies and `migrate` prints
`applied: ['025_portal_engagement.sql']`. After the upgrade an admin adds at
least one subject under **Investors → Portal settings → Ticket subjects**
(the tab formerly called Payment methods; until a subject exists investors
cannot raise a ticket) and, if wanted, switches on **Bonus rules** in the
same tab -- a rule pays only for events after it is switched on. Bonuses
land in the investor's Credit wallet, which moves only to a trading account:
fund every Credit -> trading account transfer at the broker as **credit**,
never as balance, before marking it done. The portal keeps that principal
inside the account (an account -> My wallet transfer may take equity less
the credit funded into it; profit made on the credit may leave). Every
portal email now also lands in the bell; each user mutes email per topic
(money, identity, support, bonus) under **Settings**, and the theme follows
the account across browsers. Deploy the api and the dashboard together.
```

- [x] **Step 5: Spec status**

In the spec, replace `**Status:** approved design; plan to follow` with
`**Status:** implemented on branch client-portal-phase-4 (plan docs/superpowers/plans/2026-10-05-client-portal-phase-4.md); awaiting deploy`.

- [x] **Step 6: Tick this plan's checkboxes, then commit**

```bash
git add README.md docs/superpowers/specs/2026-10-05-client-portal-phase-4-design.md docs/superpowers/plans/2026-10-05-client-portal-phase-4.md
git commit -m "docs: client portal phase 4 -- upgrade runbook for migration 025, spec status, plan ticked

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

## Spec decisions this plan makes (read before Task 1)

Where the spec was silent or ambiguous:

- **Notification texts**: titles are the old email subjects word for word (the existing email tests keep passing); bodies are the old email texts. Title and body are clipped to 120 and 500 characters with an ellipsis, for the row and the email alike.
- **Notification links**: one in-app page per event (table in the interfaces doc); support links carry `?ticket=<id>`, which the investor Support page and the desk Support tab open directly.
- **List shape and cursor**: `GET notifications` answers `{notifications, has_more, next_before}` like the wallet-entries page, ordered by id with `before` = an id; `limit` is clamped to 1-100. Marking a read row again keeps its first `read_at`; `read-all` answers `{updated}`.
- **Prefs body**: `PUT notification-prefs` takes all four booleans every time (the dashboard always sends the set).
- **`GET /api/me/settings` carries `updated_at`** (null until the first save). Without it, the first sync after deploy would reset every user's existing browser choice to `system`; with it, an account that never saved one is seeded from the browser instead.
- **Investors need the subject list**: `GET investor/ticket-subjects` (enabled only) is added; the spec lists only the admin's subjects CRUD.
- **Desk replies are text only**: the upload route is the investor's, and an investor can read only their own files, so a desk image could never be shown to them. `POST tickets/{id}/messages` takes `{body}`.
- **Status rules beyond the spec**: an investor reply on a `new` ticket leaves it `new`; a desk reply on a `closed` ticket is refused (409 `ticket is closed`; the investor reopens by replying); closing a closed ticket is 409 `ticket is already closed`. Either side may close; a desk close notifies the investor, an investor close notifies nobody.
- **Images only**: `ticket_attachment` uploads must sniff as JPEG/PNG/WebP (400 `attachments must be images` at upload), and `file_belongs` now also refuses a file already attached to any ticket message, so each image belongs to one message.
- **Search**: case-insensitive `ILIKE` on the subject label and every message body, with `%` and `_` taken literally. Lists are capped at 500 rows (`ponytail:` comments).
- **Summary count**: `tickets` counts every org ticket that is `new` or `open` with the investor's message last, whoever the investor is now; it is part of `total`, so the rail's Requests pill counts tickets too.
- **Desk notifications go to every admin** (the spec's "every admin"); viewers are not notified.
- **The investor never sees desk staff** (preflight P5): the investor's thread carries no `author_id`/`author_name` on desk messages and no `closed_by` for a desk close; the page already shows "Support desk".
- **"Waiting on the desk" is one server rule** (preflight P7): `portal_support.WAITING_ON_DESK` feeds every Ticket's `waiting_on_desk` and the summary count; the dashboard only reads the flag.
- **Rule bonuses after a commit are best effort** (preflight P8): `award_rule_bonus` logs a failure and answers None, so the join, role change or KYC approval that already landed never answers 500.
- **Links into an open page** (preflight P1): Requests follows `?tab`/`?ticket` changes while mounted and drops `?ticket` when the drawer closes, so a bell click always opens its ticket.
- **Bonus rule validation**: flags must be booleans; amounts follow the portal's money rules (0 allowed, two decimals); `deposit_pct` is 0-100 inclusive with three decimals (portal fees stop below 100, a bonus may be 100%); `deposit_cap` is optional; a rule switched on with a zero amount is refused (`<field> must be above 0 while the <source> rule is on`).
- **"Confirmed amount"** for the deposit rule is the credited amount the admin confirmed, not the notice amount.
- **Bonus credit never leaves** (preflight P3): credit -> account is the only way out of the Credit wallet, and an account -> wallet transfer may take only equity less open account -> wallet transfers less `credit_funded` (done credit -> account transfers into that account), floored at 0, in `request_transfer`, the summary's `account_available` and a decision-time re-check against `equity_at_request`. Principal credit stays at the broker; profit made on it may leave, as brokers do.
- **Signup trigger**: paid on a transition INTO investor (invite join with role investor, or a role change from viewer/admin to investor). It runs in its own transaction after the membership write, because the ledger lock must be the first statement of whatever writes `wallet_entries` and the join's transaction starts with the invite. The KYC bonus likewise runs after the approval; the deposit bonus is inside the confirmation's transaction.
- **Audit and alerts**: every bonus audits `investor_bonus_paid`; a rule's bonus is `info`, a hand-paid one `warning` and added to `ALERT_RULES` and `TELEGRAM_RULES` like ledger adjustments (what a stolen admin session would do). Ticket subjects audit `ticket_subject_changed`.
- **Manual grant amounts** follow the adjustment route's signed-amount rules; `parse_signed_amount` is extracted from `post_adjustment` and shared, with a test pinning the adjustment messages first.
- **Bonus history**: the investor's own rows, newest first, `?source=&from=&to=` like the wallet-entries filters, capped at 500.
- **Bell placement**: there is no desktop top bar for investors (and the desk's top strip is the kill-switch strip), so the bell sits in the rail header on desktop and in the phone top bar; one `useUnreadCount` poll feeds both.
- **Desk Settings**: `/org/:id/settings`, linked from the rail's bottom section (the "user menu": theme toggle, Log out) for desk members only; investors reach `invest/settings` from the nav.
- **Investor nav**: Money gains Bonus; Account gains Support, Notifications, Settings (16 links).
- **Tab and card names**: the Investors tab "Payment methods" becomes "Portal settings" (spec); the card already named "Portal settings" inside it becomes "Withdrawal and account rules" so one screen does not carry two different things with the same name. The new Bonus rules and Ticket subjects cards follow it.
- **Three image slots**: the existing upload component holds one file, so the Raise and Reply forms show three optional `FileInput`s.

Where the spec is wrong against the code:

- **Theme values**: the spec offers light / dim / dark / system, but the dashboard has two palettes (light, and the night palette the toggle already calls "Dim", stored as `dark`). The database keeps the spec's four values; `dim` and `dark` both paint the night palette, and Settings offers Light / Dim / System (a saved `dark` shows as Dim). A real darker palette would need new tokens and `palette_check.mjs` rows.
- **`ticket_attachment` was not accepted**: `files.purpose` allows it, but `uploads.ACCEPTED_PURPOSES` refused it and `test_uploads.py` pinned the refusal; Task 5 accepts it and changes that test.
- **`file_belongs` did not know ticket messages**; without Task 5's clause one image could be attached to many messages.
- **"The existing hourly limiter"** is a per-router `LoginRateLimiter` instance; the support router gets its own (10 new tickets per hour, and 60 replies per hour under a separate key, since each reply emails every admin: preflight P4).
- **Admins cannot upload**: the spec's "up to 3 images per message" holds for investor messages only (see above).
- **`requests/summary` is pinned exactly** by `test_portal_summary.py`; both dicts gain `tickets: 0`.
- **`TRANSFER_PAIRS` is pinned** by `test_portal_ledger.py` ("four allowed pairs") and `InvestorTransfer.test.tsx` (`transferOptions`); both are updated in Tasks 11 and 18.
