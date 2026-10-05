# Client Portal Phase 3 (Several Live Accounts per Investor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An investor holds several live MT5 accounts in one workspace, up to a cap the admin sets (default 5); every screen that showed "the account" lets the investor pick one, and the dashboard shows all of them with totals.

**Architecture:** Migration 024 drops the one-account-per-investor unique index and adds `portal_settings.max_live_accounts`. `portal_common` gains the ownership helpers (`linked_accounts`, `owns_account`, `pick_account`, `accounts_used`, `link_account` with the cap) and loses `linked_account`; every investor route that names an account checks ownership, the read routes take `?account_id=`, the summary returns `accounts[]` plus totals from one copier `/state` call, and the admin gets `POST/DELETE investors/{id}/accounts` in place of `PUT investors/{id}/account`. The dashboard adds a "Live accounts" tray, an account switcher (Account and History pages, kept in `?account=`), account dropdowns on Transfer and Deposit, the cap state on Open account, and an accounts drawer plus the cap field for admins.

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 (autocommit) / pytest against real Postgres 16; React 18 / TypeScript strict / react-router 7 / Tailwind 4 tokens / vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-05-client-portal-phase-3-multi-account-design.md` (read it first; it is the authority). **Interfaces:** `docs/superpowers/plans/2026-10-05-client-portal-phase-3-interfaces.md` (every name, route, refusal string and aria-label; binding). **Spec decisions:** the last section of this plan lists every place the spec was silent, ambiguous or wrong against the code and what this plan does there; read it before Task 1.

## Global Constraints

- Branch `client-portal-phase-3` in the worktree `.worktrees/phase3` (created from `main` at `a4c9c9f`, spec commit `68b2d79`). Never `cd` to the main checkout, never `git stash`. Commit on the branch after every task; create no other branches.
- Every commit message ends with these two lines:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN
  ```

  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`.
- `copier/` is never touched.
- API tests need Postgres answering on `127.0.0.1:5433` (if `docker ps` hangs, probe the port instead; do not restart Docker). The worktree has no virtualenv: use the main checkout's. Once per shell, Git Bash from the worktree's `api/`:

  ```bash
  cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/.worktrees/phase3/api"
  export POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' ../../../.env | cut -d= -f2-)"
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test_p3"
  export PYTHONPATH="$(pwd -W)/src"
  PY="/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/api/.venv/Scripts/python.exe"
  ```

  then `"$PY" -m pytest <files> -q -p no:cacheprovider`. The `_p3` database name keeps this run from dropping another agent's scratch database (conftest drops and recreates the database the DSN names). Use `127.0.0.1`, never `localhost` (IPv6 hang). Seven `test_events_ws.py` errors and one EA-download CRLF failure are pre-existing on Windows; do not fix them.
- Dashboard: the worktree has no `node_modules`; run `npm ci` once in `dashboard/`. One file: `npx vitest run <path>`. Gate, from `dashboard/`: `npm test` (palette prover, `tsc --noEmit -p tsconfig.app.json`, vitest; if this machine runs out of workers use `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`) and `npm run build`. There is no lint script. The vitest output must contain no `act(...)` warning.
- Investor routes take `Depends(require_investor)`; admin routes `Depends(require_org_role("admin"))`. Every new org route gets a row in `api/tests/test_rbac_matrix.py` (Task 8).
- Every mutation audits one `events` row through `portal_common.audit_control`; investor-scoped actions carry `payload.user_id` = the investor and the account in the events `account_id` column. Ledger-writing transactions take `pc.lock_investor_ledger` as their FIRST statement and await nothing while it is held.
- An investor route that names an account answers 404 `Account not found` for a foreign or unknown id, never a hint that it exists.
- Dashboard rules: colour only through `--color-*` tokens and the primitives (Button, Input, Select, Badge, Banner, Card, Tabs, Drawer, ConfirmDialog, PageHeader, Loading, PinConfirmDialog, NextStep); data never sits directly on `.glass`; tables are `stack-table` with `data-label` on every data `td`; the one `h1` comes from `PageHeader`; the words "Slave"/"slave" never appear in copy; never a literal "Loading..."; use only class names that already appear in `src/` (so `scripts/palette_check.mjs` needs no new row); `tsc` is strict with `noUnusedLocals`. Tests use `mockUseOrg` from `src/test/orgMock.tsx`, fixtures from `src/test/portalFixtures.ts`, stub `fetch` by URL; a test of a page that renders `Money` ends its `afterEach` with `cleanup()` before `setHidden(false)`.
- Ponytail: reuse the helpers named here, add nothing speculative, mark a deliberate shortcut with a `ponytail:` comment naming its ceiling.
- Deploy is not part of this plan; Task 15 writes the runbook lines.

---

## File map

| File | Change |
|---|---|
| `db/migrations/024_multi_account.sql` (new) | drop the unique index, plain lookup index, `max_live_accounts` |
| `api/tests/test_migration_024.py` (new), `test_migration_019.py`, `test_migration_022.py` | 024 shape; 019's uniqueness test retired; 022's settings columns |
| `api/src/api/portal_common.py`, `api/tests/test_portal_common.py` | ownership helpers, `link_account`, settings column; `linked_account` removed (Task 7) |
| `api/src/api/routes/portal_investor.py` | read routes `?account_id=`, transfer and deposit ownership, summary `accounts[]` |
| `api/src/api/routes/portal_admin.py` | deposit decision, settings, `investors` shape, link/unlink routes |
| `api/src/api/routes/portal_identity.py` | the cap on account requests; fulfil links through `pc.link_account` |
| `api/tests/test_portal_multi_account.py` (new) | phase 3 behaviour, grown over Tasks 3-7 |
| `api/tests/test_portal_account_requests.py`, `test_portal_methods.py`, `test_portal_summary.py`, `test_investor_access.py`, `test_rbac_matrix.py` | updated to the new rules and shapes |
| `dashboard/src/lib/types.ts`, `lib/investor.ts` (+ test), `test/portalFixtures.ts` (+ test) | `AccountSummary`, `InvestorAccount`, `accountName`, `pickAccount`, fixtures |
| `dashboard/src/pages/investor/InvestorDashboard.tsx` (+ test) | Live accounts tray |
| `dashboard/src/pages/investor/AccountSwitcher.tsx` (new), `InvestorAccount.tsx`, `InvestorHistory.tsx` (+ tests) | switcher, `?account=` |
| `dashboard/src/pages/investor/InvestorTransfer.tsx`, `InvestorDeposit.tsx` (+ tests) | one choice per account |
| `dashboard/src/pages/investor/InvestorOpenAccount.tsx` (+ test) | cap state |
| `dashboard/src/pages/Investors.tsx`, `pages/investors/AccountsDrawer.tsx` (new), `PaymentMethodsTab.tsx`, `Investors.test.tsx` | accounts column, drawer, cap field |
| `README.md`, spec status line | runbook, status |

## Tasks

| # | Task |
|---|---|
| 1 | Migration 024 and its test; retire the 019 uniqueness test; 022 settings columns |
| 2 | `portal_common` ownership helpers, `link_account`, `max_live_accounts` in `portal_settings` |
| 3 | Investor read routes, transfers and deposit notices check ownership |
| 4 | Deposit confirmation funds the named account only while it is still owned |
| 5 | The cap: portal settings, account requests, fulfil |
| 6 | Admin link and unlink one account; `investors` lists `accounts[]`; `PUT .../account` removed |
| 7 | Investor summary: `accounts[]`, totals, `account_limit`, one `/state` call; `linked_account` removed |
| 8 | RBAC matrix rows; the full API suite |
| 9 | Dashboard foundation: types, fixtures, `accountName`, `pickAccount` |
| 10 | Investor dashboard: the Live accounts tray |
| 11 | Account page and History page: the account switcher |
| 12 | Transfer and Deposit: one choice per account |
| 13 | Open account: the cap state |
| 14 | Admin Investors: accounts column, accounts drawer, the cap field |
| 15 | Transitional types removed; gates and docs |

---

### Task 1: Migration 024 — several accounts per investor, the cap column

**Files:**
- Create: `db/migrations/024_multi_account.sql`
- Create: `api/tests/test_migration_024.py`
- Modify: `api/tests/test_migration_019.py` (delete `test_one_account_per_investor_per_org`)
- Modify: `api/tests/test_migration_022.py` (`COLUMNS["portal_settings"]`)

**Interfaces:**
- Consumes: `db/migrate.py` `apply_migrations` (conftest runs it); conftest `db`, `make_user`, `make_org`.
- Produces: index `accounts_one_per_investor` gone; plain partial index `accounts_by_investor (org_id, investor_user_id) WHERE investor_user_id IS NOT NULL`; column `portal_settings.max_live_accounts INTEGER NOT NULL DEFAULT 5 CHECK (BETWEEN 1 AND 50)` (last column).

- [x] **Step 1: Write the failing migration test**

Create `api/tests/test_migration_024.py`:

```python
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
```

- [x] **Step 2: Run the test to verify it fails**

Run: `"$PY" -m pytest tests/test_migration_024.py -q -p no:cacheprovider`
Expected: FAIL — `024_multi_account.sql` is not in `schema_migrations`.

- [x] **Step 3: Write the migration**

Create `db/migrations/024_multi_account.sql`:

```sql
-- Client portal phase 3: an investor may hold several live accounts in one
-- workspace, up to a cap the admin sets. An account still has exactly one
-- owner (accounts.investor_user_id, unchanged). No row is rewritten: every
-- investor has 0 or 1 account today, which stays valid.
-- See docs/superpowers/specs/2026-10-05-client-portal-phase-3-multi-account-design.md.
DROP INDEX accounts_one_per_investor;

-- The per-investor lookups (portal_common.linked_accounts) keep an index.
CREATE INDEX accounts_by_investor
    ON accounts (org_id, investor_user_id) WHERE investor_user_id IS NOT NULL;

-- Live accounts (owned + open requests) per investor; lowering it below
-- what someone already has only blocks new requests and links.
ALTER TABLE portal_settings ADD COLUMN max_live_accounts INTEGER NOT NULL DEFAULT 5
    CHECK (max_live_accounts BETWEEN 1 AND 50);
```

- [x] **Step 4: Retire the tests 024 makes wrong**

In `api/tests/test_migration_019.py`, delete the whole function `test_one_account_per_investor_per_org` (the index it asserts is dropped by 024; `test_migration_024.py` now covers the link). Append to that module's docstring, before the closing `"""`: ` The one-account-per-investor index it created is dropped by 024_multi_account.sql; see test_migration_024.py.`

In `api/tests/test_migration_022.py`, replace the `portal_settings` entry of `COLUMNS` with:

```python
    "portal_settings": ["org_id", "withdrawal_min", "withdrawal_fee_pct", "updated_by",
                        "updated_at", "max_live_accounts"],
```

- [x] **Step 5: Run the migration tests to verify they pass**

Run: `"$PY" -m pytest tests/test_migration_019.py tests/test_migration_022.py tests/test_migration_024.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add db/migrations/024_multi_account.sql api/tests/test_migration_024.py api/tests/test_migration_019.py api/tests/test_migration_022.py
git commit -m "feat(api): migration 024 -- several accounts per investor, max_live_accounts setting

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 2: `portal_common` ownership helpers, `link_account`, the cap in `portal_settings`

**Files:**
- Modify: `api/src/api/portal_common.py` (section `accounts + equity`; `portal_settings`)
- Modify: `api/tests/test_portal_common.py`

**Interfaces:**
- Consumes: migration 024; `routes/mt5.MT5_OFFLINE_AFTER_S` (unchanged); conftest `seed_mt5`, `org_client`; `portal_helpers.member`, `link`, `add_package`, `open_account_request`.
- Produces (all in `api/src/api/portal_common.py`):
  - `linked_accounts(conn, org_id: int, user_id: int) -> list[int]` — ordered by account id.
  - `owns_account(conn, org_id: int, user_id: int, account_id: Optional[int]) -> bool`.
  - `pick_account(conn, org_id: int, user_id: int, account_id: Optional[int], *, field: str = "account_id") -> int` — raises `HTTPException` 404 `Account not found` / 409 `no account linked yet` / 400 `f"{field} is required"` (deposits pass `field="target_account_id"`).
  - `accounts_used(conn, org_id: int, user_id: int) -> int` — owned accounts + `requested` account requests.
  - `account_limit_text(limit: int) -> str` — `"you have reached the limit of {limit} live accounts"`.
  - `link_account(conn, org_id: int, user_id: int, account_id: int, *, mt5_only: bool = False) -> None` — runs inside the caller's transaction; refusals 400 `The master account cannot be linked to an investor`, 400 `Only an MT5 account can be linked here` (only with `mt5_only=True`; fulfil passes it, the admin link does not -- today's admin link accepts cTrader accounts and keeps doing so), 409 `account_limit_text(max)`, 404 `Account not found in this workspace, or already linked`; already this investor's = no-op.
  - `portal_settings(...)` now returns `{"withdrawal_min": Decimal, "withdrawal_fee_pct": Decimal, "max_live_accounts": int}`.
  - `linked_account` stays until Task 7 (its last caller goes there).

- [x] **Step 1: Write the failing tests**

In `api/tests/test_portal_common.py`, extend the imports:

```python
from conftest import default_mock_callback, seed_mt5
from fastapi import HTTPException
from portal_helpers import (add_package, approved_destination, credit, link, member,
                            open_account_request)
```

Append after `test_equity_for_prefers_live_then_last_known_then_unknown`:

```python
def test_linked_accounts_owns_account_and_pick_account(org_client, make_user, db):
    client, org_id, seed = org_client
    for aid in (1001, 1002, 1003):
        seed(aid, role="slave")
    investor = make_user(email="inv@example.com")
    other = make_user(email="other@example.com")
    member(db, org_id, investor["id"], "investor")
    member(db, org_id, other["id"], "investor")
    uid = investor["id"]

    def refused(account_id):
        with pytest.raises(HTTPException) as exc:
            pc.pick_account(conn, org_id, uid, account_id)
        return exc.value.status_code, exc.value.detail

    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.linked_accounts(conn, org_id, uid) == []
        assert refused(None) == (409, "no account linked yet")
        assert refused(1001) == (404, "Account not found")
    link(db, org_id, uid, 1002)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.pick_account(conn, org_id, uid, None) == 1002
    link(db, org_id, uid, 1001)
    link(db, org_id, other["id"], 1003)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.linked_accounts(conn, org_id, uid) == [1001, 1002]
        assert pc.owns_account(conn, org_id, uid, 1001) is True
        assert pc.owns_account(conn, org_id, uid, 1003) is False
        assert pc.owns_account(conn, org_id, uid, 999) is False
        assert pc.owns_account(conn, org_id, uid, None) is False
        assert refused(None) == (400, "account_id is required")
        assert pc.pick_account(conn, org_id, uid, 1001) == 1001
        assert refused(1003) == (404, "Account not found")
        assert refused(999) == (404, "Account not found")


def test_accounts_used_counts_owned_accounts_and_open_requests(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.accounts_used(conn, org_id, investor["id"]) == 0
    link(db, org_id, investor["id"], 1001)
    req_id = open_account_request(db, org_id, investor["id"], package_id)
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.accounts_used(conn, org_id, investor["id"]) == 2
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (req_id,))
        assert pc.accounts_used(conn, org_id, investor["id"]) == 1
    assert pc.account_limit_text(3) == "you have reached the limit of 3 live accounts"


def test_link_account_keeps_the_link_rules_and_the_cap(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(1001, role="slave")
    first, second, taken = (seed_mt5(db, org_id, f"key-{i}") for i in range(3))
    investor = make_user(email="inv@example.com")
    other = make_user(email="other@example.com")
    member(db, org_id, investor["id"], "investor")
    member(db, org_id, other["id"], "investor")
    link(db, org_id, other["id"], taken)
    uid = investor["id"]

    with psycopg.connect(db, autocommit=True) as conn:
        def refused(account_id):
            with pytest.raises(HTTPException) as exc:
                with conn.transaction():
                    pc.link_account(conn, org_id, uid, account_id, mt5_only=True)
            return exc.value.status_code, exc.value.detail

        assert refused(100) == (400, "The master account cannot be linked to an investor")
        assert refused(1001) == (400, "Only an MT5 account can be linked here")
        assert refused(taken) == (404, "Account not found in this workspace, or already linked")
        assert refused(999) == (404, "Account not found in this workspace, or already linked")
        with conn.transaction():
            pc.link_account(conn, org_id, uid, first)
            pc.link_account(conn, org_id, uid, first)   # already this investor's: a no-op
        conn.execute("UPDATE portal_settings SET max_live_accounts = 1 WHERE org_id = %s",
                     (org_id,))
        assert refused(second) == (409, "you have reached the limit of 1 live accounts")
        conn.execute("UPDATE portal_settings SET max_live_accounts = 2 WHERE org_id = %s",
                     (org_id,))
        with conn.transaction():
            pc.link_account(conn, org_id, uid, second)
        assert pc.linked_accounts(conn, org_id, uid) == sorted([first, second])
```

In `test_portal_settings_creates_the_default_row_once`, replace the two dict assertions with:

```python
    assert first == {"withdrawal_min": Decimal("0.00"), "withdrawal_fee_pct": Decimal("0.000"),
                     "max_live_accounts": 5}
    assert second == {"withdrawal_min": Decimal("50.00"), "withdrawal_fee_pct": Decimal("1.500"),
                      "max_live_accounts": 5}
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_common.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'api.portal_common' has no attribute 'linked_accounts'`, and the settings dicts lack `max_live_accounts`.

- [x] **Step 3: Add the helpers**

In `api/src/api/portal_common.py`, directly after the existing `linked_account` function, add:

```python
def linked_accounts(conn: psycopg.Connection, org_id: int, user_id: int) -> list[int]:
    """Every trading account linked to this investor in this workspace
    (accounts.investor_user_id), oldest id first; [] while none."""
    rows = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts WHERE org_id = %s AND investor_user_id = %s "
        "ORDER BY ctid_trader_account_id", (org_id, user_id)).fetchall()
    return [int(r[0]) for r in rows]


def owns_account(conn: psycopg.Connection, org_id: int, user_id: int,
                 account_id: Optional[int]) -> bool:
    """True when account_id is one of this investor's accounts here."""
    return conn.execute(
        "SELECT 1 FROM accounts WHERE org_id = %s AND investor_user_id = %s "
        "AND ctid_trader_account_id = %s", (org_id, user_id, account_id)).fetchone() is not None


def pick_account(conn: psycopg.Connection, org_id: int, user_id: int,
                 account_id: Optional[int], *, field: str = "account_id") -> int:
    """The account a read route works on: the named one (404 unless the
    investor owns it -- no hint whether it exists), else the only one; 409
    while there is none and 400 when there are several to choose from.
    `field` names the body/query field in the 400 message (deposits pass
    "target_account_id" so their refusal names the field they sent)."""
    if account_id is not None:
        if not owns_account(conn, org_id, user_id, account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        return account_id
    owned = linked_accounts(conn, org_id, user_id)
    if not owned:
        raise HTTPException(status_code=409, detail="no account linked yet")
    if len(owned) > 1:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    return owned[0]


def accounts_used(conn: psycopg.Connection, org_id: int, user_id: int) -> int:
    """What counts against portal_settings.max_live_accounts: the accounts
    the investor owns plus their open ('requested') account requests."""
    (used,) = conn.execute(
        "SELECT (SELECT count(*) FROM accounts "
        "        WHERE org_id = %(o)s AND investor_user_id = %(u)s) "
        "     + (SELECT count(*) FROM account_requests "
        "        WHERE org_id = %(o)s AND user_id = %(u)s AND status = 'requested')",
        {"o": org_id, "u": user_id}).fetchone()
    return int(used)


def account_limit_text(limit: int) -> str:
    return f"you have reached the limit of {limit} live accounts"


def link_account(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int,
                 *, mt5_only: bool = False) -> None:
    """Link one more account to an investor, inside the caller's
    transaction (moved from routes/portal_identity._link_account; the admin
    link route and fulfil both use it): never the master, MT5 only when
    mt5_only (fulfil of an MT5 account request), never an
    account linked to someone else, and at most max_live_accounts per
    investor. Linking an account the investor already owns is a no-op."""
    if owns_account(conn, org_id, user_id, account_id):
        return
    role_row = conn.execute(
        "SELECT role, platform FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
        (account_id, org_id)).fetchone()
    if role_row and role_row[0] == "master":
        raise HTTPException(status_code=400,
                            detail="The master account cannot be linked to an investor")
    if mt5_only and role_row and role_row[1] != "mt5":
        raise HTTPException(status_code=400, detail="Only an MT5 account can be linked here")
    limit = portal_settings(conn, org_id)["max_live_accounts"]
    # ponytail: count then update, no lock -- two admins linking to the same
    # investor at the same instant could pass the cap by one; take
    # lock_investor_ledger here first if that ever matters.
    if len(linked_accounts(conn, org_id, user_id)) >= limit:
        raise HTTPException(status_code=409, detail=account_limit_text(limit))
    updated = conn.execute(
        "UPDATE accounts SET investor_user_id = %s "
        "WHERE ctid_trader_account_id = %s AND org_id = %s AND investor_user_id IS NULL "
        "RETURNING ctid_trader_account_id", (user_id, account_id, org_id)).fetchone()
    if not updated:
        raise HTTPException(status_code=404,
                            detail="Account not found in this workspace, or already linked")
```

- [x] **Step 4: The cap in `portal_settings`**

Replace `portal_settings` with:

```python
def portal_settings(conn: psycopg.Connection, org_id: int) -> dict:
    """{withdrawal_min, withdrawal_fee_pct} as Decimals and max_live_accounts
    as an int; the row is created with the defaults on first read."""
    conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s) ON CONFLICT (org_id) DO NOTHING",
                 (org_id,))
    row = conn.execute(
        "SELECT withdrawal_min, withdrawal_fee_pct, max_live_accounts FROM portal_settings "
        "WHERE org_id = %s", (org_id,)).fetchone()
    return {"withdrawal_min": Decimal(row[0]), "withdrawal_fee_pct": Decimal(row[1]),
            "max_live_accounts": int(row[2])}
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_common.py tests/test_portal_methods.py tests/test_portal_withdrawals.py -q -p no:cacheprovider`
Expected: PASS (the admin settings route still serialises only the two withdrawal keys until Task 5).

- [x] **Step 6: Commit**

```bash
git add api/src/api/portal_common.py api/tests/test_portal_common.py
git commit -m "feat(api): portal_common ownership helpers, link_account with the cap, max_live_accounts setting

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---
### Task 3: Investor read routes, transfers and deposit notices check ownership

**Files:**
- Create: `api/tests/test_portal_multi_account.py`
- Modify: `api/src/api/routes/portal_investor.py` (`file_deposit`, `request_transfer`, `_require_linked`, `my_positions`, `my_analytics`, `my_history`)

**Interfaces:**
- Consumes: `pc.linked_accounts`, `pc.owns_account`, `pc.pick_account` (Task 2); `pc.equity_for` (unchanged; the transfers lock-order test spies on it); `portal_helpers.add_method`, `credit`, `csrf`, `link`, `member`.
- Produces: `GET investor/positions|analytics|history/{kind}` take an optional `account_id: Optional[int]` query; `POST investor/transfers` checks the named account; `POST investor/deposits` checks `target_account_id` (required with several accounts: 400 `target_account_id is required`). Test module `test_portal_multi_account.py` with `READS`, `W`, `A`, `_q`, `_live`, `_copier` and fixture `two`.

- [x] **Step 1: Write the failing tests**

Create `api/tests/test_portal_multi_account.py`:

```python
# api/tests/test_portal_multi_account.py
"""Client portal phase 3: several live accounts per investor. Every investor
route that names an account checks the investor owns it; the read routes
pick one with ?account_id=; a deposit funds the named account only while
it is still owned; a workspace cap bounds the count; admins link and unlink
one account at a time; the summary carries every account and the totals.
Grown over Tasks 3-7 of the phase 3 plan."""
from decimal import Decimal

import httpx
import psycopg
import pytest

from conftest import default_mock_callback, seed_mt5
from portal_helpers import (add_method, add_package, credit, csrf, kyc_profile, link, member,
                            open_account_request)

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
READS = ("investor/positions", "investor/analytics", "investor/history/deals?from=0&to=1")
W = {"kind": "wallet", "wallet": "main"}


def A(account_id):
    return {"kind": "account", "account_id": account_id}


def _q(tail, account_id):
    return f"{tail}{'&' if '?' in tail else '?'}account_id={account_id}"


def _live(equity, positions=()):
    return {"balance": float(equity), "equity": float(equity), "open_pnl": 0.0,
            "positions": list(positions)}


def _copier(client, accounts=None, down=False):
    """Fake the copier: /state answers `accounts` ({id: _live(...)}) or 502
    when down; analytics and history answer empty. Returns the list of
    copier URLs asked, in order."""
    seen = []

    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        seen.append(url)
        if "/state" in url:
            if down:
                return httpx.Response(502, json={"detail": "down"})
            return httpx.Response(200, json={
                "status": "ok", "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        if "/analytics" in url:
            return httpx.Response(200, json={"closed_trades": 0, "weeks": 4})
        if "/history/" in url:
            return httpx.Response(200, json={"deals": [], "has_more": False})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)
    return seen


@pytest.fixture
def two(org_client, make_user, login_as, db):
    """An investor (logged in, 1000 in main) owning accounts 1001 and 1002;
    account 1003 belongs to another investor. Returns (client, org_id, investor)."""
    client, org_id, seed = org_client
    for aid in (1001, 1002, 1003):
        seed(aid, role="slave")
    investor = make_user(email="inv@example.com")
    other = make_user(email="other@example.com")
    member(db, org_id, investor["id"], "investor")
    member(db, org_id, other["id"], "investor")
    link(db, org_id, investor["id"], 1001)
    link(db, org_id, investor["id"], 1002)
    link(db, org_id, other["id"], 1003)
    credit(db, org_id, investor["id"], Decimal("1000"))
    login_as(client, investor)
    return client, org_id, investor


# ------------------------------------------------------------ read routes (Task 3)


def test_read_routes_need_a_choice_once_there_are_several(two):
    client, org_id, _ = two
    seen = _copier(client, {1001: _live(10), 1002: _live(20, [
        {"position_id": 8, "symbol": "EURUSD", "side": "SELL", "volume": 1}])})
    for tail in READS:
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 400 and r.json()["detail"] == "account_id is required", tail
        for foreign in (1003, 999):
            r = client.get(f"/api/orgs/{org_id}/{_q(tail, foreign)}")
            assert r.status_code == 404 and r.json()["detail"] == "Account not found", tail
        assert client.get(f"/api/orgs/{org_id}/{_q(tail, 1002)}").status_code == 200, tail
    assert any("/analytics?account_id=1002&weeks=4" in u for u in seen)
    assert any("/history/deals?account_id=1002&from=0&to=1" in u for u in seen)
    body = client.get(f"/api/orgs/{org_id}/investor/positions?account_id=1002").json()
    assert body["equity_source"] == "live"
    assert [p["position_id"] for p in body["positions"]] == [8]


def test_one_account_needs_no_choice_and_none_is_still_409(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    _copier(client, {1001: _live(10)})
    for tail in READS:
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 409 and r.json()["detail"] == "no account linked yet", tail
        r = client.get(f"/api/orgs/{org_id}/{_q(tail, 1001)}")
        assert r.status_code == 404 and r.json()["detail"] == "Account not found", tail
    link(db, org_id, investor["id"], 1001)
    for tail in READS:
        assert client.get(f"/api/orgs/{org_id}/{tail}").status_code == 200, tail


# ------------------------------------------------------------ transfers and notices (Task 3)


def test_transfers_name_an_owned_account(two):
    client, org_id, _ = two
    _copier(client, {1001: _live(500), 1002: _live(300)})

    def move(source, target):
        return client.post(f"/api/orgs/{org_id}/investor/transfers",
                           json={"source": source, "target": target, "amount": "10",
                                 "mpin": "123456"}, headers=csrf(client))

    for foreign in (1003, 999):
        for source, target in ((W, A(foreign)), (A(foreign), W)):
            r = move(source, target)
            assert r.status_code == 404 and r.json()["detail"] == "Account not found", foreign
    r = move(W, A(1002))
    assert r.status_code == 201 and r.json()["target"] == {"kind": "account", "account_id": 1002}
    r = move(A(1002), W)
    assert r.status_code == 201 and r.json()["source"] == {"kind": "account", "account_id": 1002}
    assert r.json()["equity_at_request"] == 300.0


def test_an_account_deposit_names_an_owned_account_once_there_are_several(two, db):
    client, org_id, _ = two
    method_id = add_method(db, org_id)

    def notice(ref, **over):
        return client.post(f"/api/orgs/{org_id}/investor/deposits",
                           json={"method_id": method_id, "amount": "100", "reference": ref,
                                 "target": "account", **over}, headers=csrf(client))

    r = notice("tx-1")
    assert r.status_code == 400 and r.json()["detail"] == "target_account_id is required"
    for foreign in (1003, 999):
        r = notice("tx-2", target_account_id=foreign)
        assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = notice("tx-3", target_account_id=1002)
    assert r.status_code == 201 and r.json()["target_account_id"] == 1002
```

(The unused imports — `seed_mt5`, `add_package`, `kyc_profile`, `open_account_request` — are used by Tasks 6-7; pytest does not lint them.)

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py -q -p no:cacheprovider`
Expected: FAIL — the read routes ignore `account_id` (200 for a foreign id), the transfer to 1002 may answer 404 (only one linked account is seen, by row order), the deposit without `target_account_id` answers 201.

- [x] **Step 3: Deposit notices name an owned account**

In `file_deposit` (`api/src/api/routes/portal_investor.py`), replace the block

```python
        target_account_id: Optional[int] = None
        if target == "account":
            linked = pc.linked_account(conn, ctx.org_id, ctx.user_id)
            if linked is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            if body.target_account_id is not None and body.target_account_id != linked:
                raise HTTPException(status_code=404, detail="Account not found")
            target_account_id = linked
```

with

```python
        target_account_id: Optional[int] = None
        if target == "account":
            # The 409 is checked before pick_account so owning none wins
            # over a foreign id too (today's order; pick_account alone
            # would 404 a foreign id first, which the read routes want but
            # deposits do not).
            if not pc.linked_accounts(conn, ctx.org_id, ctx.user_id):
                raise HTTPException(status_code=409, detail="no account linked yet")
            target_account_id = pc.pick_account(conn, ctx.org_id, ctx.user_id,
                                                body.target_account_id, field="target_account_id")
```

- [x] **Step 4: Transfers name an owned account**

In `request_transfer`, replace the block

```python
        account_id: Optional[int] = None
        if "account" in (source_kind, target_kind):
            account_id = pc.linked_account(conn, ctx.org_id, ctx.user_id)
            if account_id is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            named = body.source.account_id if source_kind == "account" else body.target.account_id
            if named != account_id:
                raise HTTPException(status_code=404, detail="Account not found")
```

with

```python
        account_id: Optional[int] = None
        if "account" in (source_kind, target_kind):
            named = body.source.account_id if source_kind == "account" else body.target.account_id
            if not pc.owns_account(conn, ctx.org_id, ctx.user_id, named):
                if not pc.linked_accounts(conn, ctx.org_id, ctx.user_id):
                    raise HTTPException(status_code=409, detail="no account linked yet")
                raise HTTPException(status_code=404, detail="Account not found")
            account_id = named
```

(`transfer_pair` already refuses an account end without an id with 400 `that transfer is not allowed`, so `named` is never None here.)

- [x] **Step 5: The read routes take `?account_id=`**

Delete the nested `_require_linked` helper (the `def _require_linked(...)` block just under `# ---- summary`). Replace the three read-throughs with:

```python
    @router.get("/investor/positions", response_model=Dict[str, Any])
    async def my_positions(http_request: Request, account_id: Optional[int] = None,
                           ctx: OrgContext = Depends(require_investor),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        account_id = pc.pick_account(conn, ctx.org_id, ctx.user_id, account_id)
        _equity, source, positions = await pc.equity_for(http_request, conn, ctx.org_id,
                                                          account_id)
        keys = ("position_id", "symbol", "side", "volume", "entry_price", "current_price",
                "stop_loss", "take_profit", "pnl_quote")
        return {"equity_source": source,
                "positions": [{k: p.get(k) for k in keys} for p in positions if isinstance(p, dict)]}

    @router.get("/investor/analytics", response_model=Dict[str, Any])
    async def my_analytics(http_request: Request, weeks: int = 4,
                           account_id: Optional[int] = None,
                           ctx: OrgContext = Depends(require_investor),
                           conn: psycopg.Connection = Depends(get_conn),
                           cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = pc.pick_account(conn, ctx.org_id, ctx.user_id, account_id)
        weeks = max(1, min(weeks, 12))
        return await _proxy_to_copier(
            http_request.app.state.http,
            f"{cfg.copier_control_url}/analytics?account_id={account_id}&weeks={weeks}",
            method="GET", timeout=COPIER_SLOW_COMMAND_TIMEOUT_S)

    @router.get("/investor/history/{kind}", response_model=Dict[str, Any])
    async def my_history(kind: str, http_request: Request,
                         from_ms: int = Query(..., alias="from"),
                         to_ms: int = Query(..., alias="to"),
                         account_id: Optional[int] = None,
                         ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn),
                         cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        if kind not in ("deals", "orders", "cashflow"):
            raise HTTPException(status_code=400, detail="kind must be deals, orders or cashflow")
        account_id = pc.pick_account(conn, ctx.org_id, ctx.user_id, account_id)
        return await _proxy_to_copier(
            http_request.app.state.http,
            f"{cfg.copier_control_url}/history/{kind}"
            f"?account_id={account_id}&from={from_ms}&to={to_ms}", method="GET")
```

Change the comment above them, `# Moved from the old investor router unchanged in behaviour.`, to `# One account per call: ?account_id=, optional while the investor owns one.`

- [x] **Step 6: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_transfers.py tests/test_portal_deposits.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add api/src/api/routes/portal_investor.py api/tests/test_portal_multi_account.py
git commit -m "feat(api): investor read routes take account_id; transfers and deposit notices name an owned account

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 4: Deposit confirmation funds the named account only while it is still owned

**Files:**
- Modify: `api/src/api/routes/portal_admin.py` (`decide_deposit`)
- Modify: `api/tests/test_portal_multi_account.py` (append)

**Interfaces:**
- Consumes: `pc.owns_account` (Task 2); fixture `two`, `ADMIN` (Task 3).
- Produces: a confirmed `target = account` deposit creates the approved main -> account transfer to `deposits.target_account_id` when the investor still owns it, else credits main only, with the unchanged note suffix `(no account linked; credited to wallet)`.

- [x] **Step 1: Write the failing test**

Append to `api/tests/test_portal_multi_account.py`:

```python
# ------------------------------------------------------------ deposit decision (Task 4)


def test_confirming_funds_the_named_account_while_it_is_still_owned(two, db, login_as):
    client, org_id, _ = two
    method_id = add_method(db, org_id)
    ids = []
    for ref, account_id in (("tx-a", 1002), ("tx-b", 1001)):
        r = client.post(f"/api/orgs/{org_id}/investor/deposits",
                        json={"method_id": method_id, "amount": "100", "reference": ref,
                              "target": "account", "target_account_id": account_id},
                        headers=csrf(client))
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET investor_user_id = NULL WHERE ctid_trader_account_id = 1001")
    client.cookies.clear()
    login_as(client, ADMIN)
    notes = []
    for dep_id in ids:
        r = client.post(f"/api/orgs/{org_id}/deposits/{dep_id}/decision",
                        json={"status": "confirmed"}, headers=csrf(client))
        assert r.status_code == 200, r.text
        notes.append(r.json()["decision_note"])
    # 1002 is still owned: funded. 1001 was unlinked since the notice: wallet.
    assert notes == [None, "(no account linked; credited to wallet)"]
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute("SELECT target_account_id, amount, status FROM transfers "
                            "WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()
    assert rows == [(1002, Decimal("100.00"), "approved")]
```

- [x] **Step 2: Run the test to verify it fails**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py -k confirming -q -p no:cacheprovider`
Expected: FAIL — the decision funds whatever single account `linked_account` returns now (1002 for both notices), so the second also creates a transfer and has no note.

- [x] **Step 3: Fund the named account**

In `decide_deposit` (`api/src/api/routes/portal_admin.py`), replace

```python
        current = conn.execute(
            "SELECT status, user_id, amount, fee, target FROM deposits "
            "WHERE id = %s AND org_id = %s", (deposit_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        status_now, investor_id, amount, fee, target = current
```

with

```python
        current = conn.execute(
            "SELECT status, user_id, amount, fee, target, target_account_id FROM deposits "
            "WHERE id = %s AND org_id = %s", (deposit_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        status_now, investor_id, amount, fee, target, target_account_id = current
```

and replace

```python
            if new_status == "confirmed" and target == "account":
                # The link as it is NOW, not as it was when the notice was
                # filed: the admin funds the account the investor has today.
                linked = pc.linked_account(conn, ctx.org_id, investor_id)
                if linked is None:
                    suffix = "(no account linked; credited to wallet)"
                    note = f"{note} {suffix}" if note else suffix
```

with

```python
            if new_status == "confirmed" and target == "account":
                # The account the notice named, if the investor still owns it
                # NOW; unlinked (or deleted, so NULL) since then -> the wallet.
                if target_account_id is not None and pc.owns_account(
                        conn, ctx.org_id, investor_id, int(target_account_id)):
                    linked = int(target_account_id)
                else:
                    suffix = "(no account linked; credited to wallet)"
                    note = f"{note} {suffix}" if note else suffix
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_deposits.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/src/api/routes/portal_admin.py api/tests/test_portal_multi_account.py
git commit -m "feat(api): a confirmed account deposit funds the named account while the investor still owns it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 5: The cap — portal settings, account requests, fulfil

**Files:**
- Modify: `api/src/api/routes/portal_admin.py` (`SettingsBody`, `_settings_json`, `put_settings`)
- Modify: `api/src/api/routes/portal_identity.py` (delete `_link_account`; `request_account`; `fulfil_request`)
- Modify: `api/tests/test_portal_methods.py` (`test_portal_settings_default_to_zero_and_update_with_audit`)
- Modify: `api/tests/test_portal_account_requests.py` (two tests replaced, two helpers added, docstring)
- Modify: `api/tests/test_portal_multi_account.py` (append)

**Interfaces:**
- Consumes: `pc.portal_settings` (with `max_live_accounts`), `pc.accounts_used`, `pc.account_limit_text`, `pc.link_account` (Task 2).
- Produces: `GET/PUT portal-settings` body and answer `{withdrawal_min, withdrawal_fee_pct, max_live_accounts}`; on PUT `max_live_accounts` is optional (omitted keeps the current value), else a whole number 1-50 or 400 `max_live_accounts must be a whole number from 1 to 50`; audit `portal_settings_changed` carries it in `previous` and in the new values. `POST investor/account-requests` refuses 409 `account_limit_text(max)` when `accounts_used >= max`, checked after the KYC gate and the one-open-request rule; the old 409 `you already have a trading account` is gone. Fulfil links through `pc.link_account` (adds; cap checked; the request being fulfilled is not counted). `test_portal_account_requests.py` gains `_set_cap(db, org_id, cap)` and `_cancel_open(db, user_id)`.

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_multi_account.py`:

```python
# ------------------------------------------------------------ the cap (Task 5)


def test_the_cap_is_a_portal_setting_from_1_to_50(org_client):
    client, org_id, _ = org_client
    url = f"/api/orgs/{org_id}/portal-settings"
    base = {"withdrawal_min": "0", "withdrawal_fee_pct": "0"}
    assert client.get(url).json()["max_live_accounts"] == 5
    for bad in (0, 51, "5", True, 2.5):
        r = client.put(url, json={**base, "max_live_accounts": bad}, headers=csrf(client))
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "max_live_accounts must be a whole number from 1 to 50"
    r = client.put(url, json={**base, "max_live_accounts": 2}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["max_live_accounts"] == 2
    r = client.put(url, json=base, headers=csrf(client))         # omitted: kept
    assert r.status_code == 200 and r.json()["max_live_accounts"] == 2
    assert client.get(url).json()["max_live_accounts"] == 2
```

In `api/tests/test_portal_methods.py`, `test_portal_settings_default_to_zero_and_update_with_audit`, add `"max_live_accounts": 5` to each of the four expected settings dicts, so they read:

```python
    assert client.get(url).json() == {"withdrawal_min": 0.0, "withdrawal_fee_pct": 0.0,
                                      "max_live_accounts": 5}
```

```python
    assert r.status_code == 200 and r.json() == {"withdrawal_min": 50.0, "withdrawal_fee_pct": 2.5,
                                                 "max_live_accounts": 5}
    assert client.get(url).json() == {"withdrawal_min": 50.0, "withdrawal_fee_pct": 2.5,
                                      "max_live_accounts": 5}
```

```python
    assert payload["previous"] == {"withdrawal_min": 0.0, "withdrawal_fee_pct": 0.0,
                                   "max_live_accounts": 5}
```

In `api/tests/test_portal_account_requests.py`, replace `test_an_investor_with_an_account_or_an_open_request_is_refused` with:

```python
def _set_cap(db, org_id, cap):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO portal_settings (org_id, max_live_accounts) VALUES (%s, %s) "
                     "ON CONFLICT (org_id) DO UPDATE SET max_live_accounts = EXCLUDED.max_live_accounts",
                     (org_id, cap))


def _cancel_open(db, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE user_id = %s AND status = 'requested'",
                     (user_id,))


def test_one_open_request_at_a_time_and_the_cap_bounds_accounts(portal, db):
    client, org_id, investor, package_id, seed = portal
    assert _request(client, org_id, package_id).status_code == 201
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "a request is already open"
    _cancel_open(db, investor["id"])
    seed(1001, role="slave")
    link(db, org_id, investor["id"], 1001)
    # An investor with an account may ask for another, under the cap.
    assert _request(client, org_id, package_id).status_code == 201
    _cancel_open(db, investor["id"])
    _set_cap(db, org_id, 1)
    r = _request(client, org_id, package_id)
    assert r.status_code == 409
    assert r.json()["detail"] == "you have reached the limit of 1 live accounts"
```

and replace `test_fulfil_refuses_a_second_account_for_a_linked_investor` with:

```python
def test_fulfil_links_a_second_account_up_to_the_cap(desk, db):
    client, org_id, investor, _, _, req_id = desk
    first, second = seed_mt5(db, org_id, "key-a"), seed_mt5(db, org_id, "key-b")
    link(db, org_id, investor["id"], first)
    _set_cap(db, org_id, 1)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=second)
    assert r.status_code == 409
    assert r.json()["detail"] == "you have reached the limit of 1 live accounts"
    with psycopg.connect(db, autocommit=True) as conn:
        (status,) = conn.execute("SELECT status FROM account_requests WHERE id = %s",
                                 (req_id,)).fetchone()
    assert status == "requested"
    # The open request being fulfilled does not count against itself.
    _set_cap(db, org_id, 2)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=second)
    assert r.status_code == 200 and r.json()["account_id"] == second
    with psycopg.connect(db, autocommit=True) as conn:
        owned = [a for (a,) in conn.execute(
            "SELECT ctid_trader_account_id FROM accounts WHERE investor_user_id = %s ORDER BY 1",
            (investor["id"],)).fetchall()]
    assert owned == sorted([first, second])
```

Change that module docstring's first line from `"""Live account requests: a verified investor with no trading account asks` to `"""Live account requests: a verified investor under the workspace's cap asks`.

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_methods.py tests/test_portal_account_requests.py -q -p no:cacheprovider`
Expected: FAIL — `portal-settings` has no `max_live_accounts`; the second request answers 409 `you already have a trading account`; fulfil answers 409 `the investor already has a linked account`.

- [x] **Step 3: The settings route**

In `api/src/api/routes/portal_admin.py`, replace `class SettingsBody` with:

```python
class SettingsBody(BaseModel):
    withdrawal_min: Any
    withdrawal_fee_pct: Any
    max_live_accounts: Any = None   # omitted: keep the current cap
```

Replace `_settings_json` and `put_settings` with:

```python
    def _settings_json(settings: dict) -> Dict[str, Any]:
        return {"withdrawal_min": pc.money(settings["withdrawal_min"]),
                "withdrawal_fee_pct": float(settings["withdrawal_fee_pct"]),
                "max_live_accounts": settings["max_live_accounts"]}
```

```python
    @router.put("/portal-settings", response_model=Dict[str, Any])
    async def put_settings(body: SettingsBody,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            withdrawal_min = parse_min(body.withdrawal_min, "withdrawal_min")
            fee_pct = parse_pct(body.withdrawal_fee_pct, "withdrawal_fee_pct")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = pc.portal_settings(conn, ctx.org_id)
        cap = current["max_live_accounts"] if body.max_live_accounts is None else body.max_live_accounts
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 50:
            raise HTTPException(status_code=400,
                                detail="max_live_accounts must be a whole number from 1 to 50")
        previous = _settings_json(current)
        conn.execute(
            "UPDATE portal_settings SET withdrawal_min = %s, withdrawal_fee_pct = %s, "
            "max_live_accounts = %s, updated_by = %s, updated_at = now() WHERE org_id = %s",
            (withdrawal_min, fee_pct, cap, ctx.user_id, ctx.org_id))
        out = {"withdrawal_min": pc.money(withdrawal_min), "withdrawal_fee_pct": float(fee_pct),
               "max_live_accounts": cap}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="portal_settings_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, previous=previous, **out,
            summary=f"Withdrawal rules set to min {withdrawal_min:.2f} USD, fee {fee_pct}%, "
                    f"max {cap} live accounts per investor by {ctx.user_email}")
        return out
```

- [x] **Step 4: Account requests and fulfil**

In `api/src/api/routes/portal_identity.py`, delete the whole `_link_account` function (it moved to `pc.link_account` in Task 2).

In `request_account`, replace everything from the docstring through the `"a request is already open"` check with:

```python
        """A live MT5 account from one package. KYC must be approved, no
        other request may be open, and the investor's accounts plus open
        requests must stay under the workspace's max_live_accounts. The two
        passwords are sealed with FERNET_KEY and live only until an admin
        decides."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        if pid.kyc_status(conn, ctx.org_id, ctx.user_id) != "approved":
            raise HTTPException(status_code=409, detail="verify your identity first")
        if conn.execute("SELECT 1 FROM account_requests WHERE org_id = %s AND user_id = %s "
                        "AND status = 'requested'", (ctx.org_id, ctx.user_id)).fetchone():
            raise HTTPException(status_code=409, detail="a request is already open")
        limit = pc.portal_settings(conn, ctx.org_id)["max_live_accounts"]
        if pc.accounts_used(conn, ctx.org_id, ctx.user_id) >= limit:
            raise HTTPException(status_code=409, detail=pc.account_limit_text(limit))
```

(the lines from `package_id = body.package_id` on stay as they are).

In `fulfil_request`, replace `_link_account(conn, ctx.org_id, user_id, body.account_id)` with `pc.link_account(conn, ctx.org_id, user_id, body.account_id, mt5_only=True)`.

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_methods.py tests/test_portal_account_requests.py tests/test_portal_withdrawals.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_admin.py api/src/api/routes/portal_identity.py api/tests/test_portal_multi_account.py api/tests/test_portal_methods.py api/tests/test_portal_account_requests.py
git commit -m "feat(api): max_live_accounts cap on account requests and fulfil; editable in portal settings

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 6: Admin link and unlink one account; `investors` lists `accounts[]`; `PUT .../account` removed

**Files:**
- Modify: `api/src/api/routes/portal_admin.py` (`LinkBody`, `list_investors`, `link_account` route replaced by two routes)
- Modify: `api/tests/test_portal_multi_account.py` (append)
- Modify: `api/tests/test_portal_summary.py` (`test_the_investor_list_has_figures_and_asks_the_copier_once`; delete `test_admin_links_and_unlinks_an_account`)
- Modify: `api/tests/test_investor_access.py` (docstring; two linking tests)

**Interfaces:**
- Consumes: `pc.link_account` (Task 2); `_require_investor`, `_org_state`, `_equity_from` (existing in `portal_admin.py`); `seed_mt5`.
- Produces: `POST investors/{user_id}/accounts` body `LinkBody {account_id: int}` -> 201 `{user_id, account_id}`, audit `investor_account_linked` (info, events `account_id` set); `DELETE investors/{user_id}/accounts/{account_id}` -> 204, 404 `Account not found` unless that investor owns it, audit `investor_account_unlinked`; both 404 `Investor not found` for a non-investor. `PUT investors/{user_id}/account` no longer exists. `GET investors` rows: `account_id`, `nickname`, `equity`, `equity_source` replaced by `accounts: [{account_id, nickname, equity, equity_source}]` (account id order), still one `/state` call for the whole list.

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_multi_account.py`:

```python
# ------------------------------------------------------------ admin link / unlink (Task 6)


def _events(db, org_id, actions):
    """(events.account_id, action, payload user_id) of these actions, in order."""
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT account_id, payload->>'action', (payload->>'user_id')::bigint FROM events "
            "WHERE org_id = %s AND payload->>'action' = ANY(%s) ORDER BY id",
            (org_id, list(actions))).fetchall()


def test_admin_links_and_unlinks_one_account_at_a_time(org_client, make_user, db):
    client, org_id, seed = org_client
    first, second, third = (seed_mt5(db, org_id, f"key-{i}") for i in range(3))
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    uid = investor["id"]
    _copier(client, down=True)
    base = f"/api/orgs/{org_id}/investors/{uid}/accounts"
    for aid in (first, second):
        r = client.post(base, json={"account_id": aid}, headers=csrf(client))
        assert r.status_code == 201 and r.json() == {"user_id": uid, "account_id": aid}
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert [a["account_id"] for a in row["accounts"]] == [first, second]
    r = client.delete(f"{base}/{third}", headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = client.delete(f"{base}/{first}", headers=csrf(client))
    assert r.status_code == 204
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert [a["account_id"] for a in row["accounts"]] == [second]
    r = client.put(f"/api/orgs/{org_id}/investors/{uid}/account", json={"account_id": None},
                   headers=csrf(client))
    assert r.status_code in (404, 405), "the single-link route is gone"
    viewer = make_user(email="v@example.com")
    member(db, org_id, viewer["id"], "viewer")
    for r in (client.post(f"/api/orgs/{org_id}/investors/{viewer['id']}/accounts",
                          json={"account_id": third}, headers=csrf(client)),
              client.delete(f"/api/orgs/{org_id}/investors/{viewer['id']}/accounts/{third}",
                            headers=csrf(client))):
        assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    assert _events(db, org_id, ("investor_account_linked", "investor_account_unlinked")) == [
        (first, "investor_account_linked", uid), (second, "investor_account_linked", uid),
        (first, "investor_account_unlinked", uid)]


def test_the_cap_blocks_new_links_and_lowering_it_keeps_what_exists(
        org_client, make_user, login_as, db):
    client, org_id, _ = org_client
    first, second, third = (seed_mt5(db, org_id, f"key-{i}") for i in range(3))
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    _copier(client, down=True)
    settings = f"/api/orgs/{org_id}/portal-settings"
    rules = {"withdrawal_min": "0", "withdrawal_fee_pct": "0"}
    base = f"/api/orgs/{org_id}/investors/{investor['id']}/accounts"
    assert client.put(settings, json={**rules, "max_live_accounts": 2},
                      headers=csrf(client)).status_code == 200
    for aid in (first, second):
        assert client.post(base, json={"account_id": aid}, headers=csrf(client)).status_code == 201
    r = client.post(base, json={"account_id": third}, headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["detail"] == "you have reached the limit of 2 live accounts"
    assert client.put(settings, json={**rules, "max_live_accounts": 1},
                      headers=csrf(client)).status_code == 200
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert len(row["accounts"]) == 2, "lowering the cap unlinks nothing"
    kyc_profile(db, org_id, investor["id"], status="approved")
    package_id = add_package(db, org_id)
    client.cookies.clear()
    login_as(client, investor)
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests",
                    json={"package_id": package_id, "leverage": 100, "main_password": "Main1234",
                          "investor_password": "Look1234", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["detail"] == "you have reached the limit of 1 live accounts"
    assert client.get(f"/api/orgs/{org_id}/investor/positions?account_id={second}").status_code == 200
```

In `api/tests/test_portal_summary.py`, `test_the_investor_list_has_figures_and_asks_the_copier_once`, replace

```python
    assert ann["account_id"] == 1001 and ann["nickname"] is None
    assert ann["equity"] == 100.0 and ann["equity_source"] == "live"
```

with

```python
    assert ann["accounts"] == [{"account_id": 1001, "nickname": None, "equity": 100.0,
                                "equity_source": "live"}]
```

and replace

```python
    assert bob["account_id"] is None and bob["equity"] is None
    assert bob["equity_source"] == "unknown"
```

with

```python
    assert bob["accounts"] == []
```

Delete `test_admin_links_and_unlinks_an_account` from `test_portal_summary.py` (the multi-account test above replaces it).

In `api/tests/test_investor_access.py`, replace the module docstring sentence

```
These five never touched those tables -- PUT .../investors/{id}/account
only ever wrote org_memberships and accounts -- so they still pass
unmodified against the current schema; only the private _csrf/_member
helpers are rewired onto the shared portal_helpers.
```

with

```
These five never touched those tables -- linking only ever wrote
org_memberships and accounts; the two linking tests were rewritten onto
POST .../investors/{id}/accounts in phase 3.
```

, add `from conftest import seed_mt5` to the imports, and replace the two linking tests with:

```python
def test_linking_refuses_an_account_from_another_workspace_or_a_non_investor(
        org_client, make_user, make_org, db):
    client, org_id, seed = org_client
    other_owner = make_user(email="o@example.com")
    other_org = make_org(name="Other", members=[(other_owner, "admin")])
    foreign = seed_mt5(db, other_org, "key-foreign")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    r = client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/accounts",
                    json={"account_id": foreign}, headers=csrf(client))
    assert r.status_code == 404
    viewer = make_user(email="v@example.com")
    member(db, org_id, viewer["id"], "viewer")
    mine = seed_mt5(db, org_id, "key-mine")
    r = client.post(f"/api/orgs/{org_id}/investors/{viewer['id']}/accounts",
                    json={"account_id": mine}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"


def test_the_master_account_cannot_be_linked_to_an_investor(org_client, make_user, db):
    """R17: the master is the desk's own account. Linked to an investor it
    would show them the desk's equity as their balance and let them request
    a withdrawal against it. The refusal leaves the existing link alone."""
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    assert client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/accounts",
                       json={"account_id": 1001}, headers=csrf(client)).status_code == 201

    r = client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/accounts",
                    json={"account_id": 100}, headers=csrf(client))
    assert r.status_code == 400 and "master" in r.json()["detail"]
    with psycopg.connect(db, autocommit=True) as conn:
        links = dict(conn.execute(
            "SELECT ctid_trader_account_id, investor_user_id FROM accounts WHERE org_id = %s",
            (org_id,)).fetchall())
    assert links == {100: None, 1001: investor["id"]}
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_summary.py tests/test_investor_access.py -q -p no:cacheprovider`
Expected: FAIL — `POST .../investors/{id}/accounts` answers 405, the investors rows have no `accounts` key.

- [x] **Step 3: The investors list**

In `api/src/api/routes/portal_admin.py`, replace `list_investors` with:

```python
    @router.get("/investors", response_model=List[Dict[str, Any]])
    async def list_investors(http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn),
                             cfg: ApiConfig = Depends(ApiConfig.from_env)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, m.created_at, COALESCE(k.status, 'draft')
               FROM org_memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN kyc_profiles k ON k.org_id = m.org_id AND k.user_id = u.id
               WHERE m.org_id = %s AND m.role = 'investor'
               ORDER BY u.display_name, u.id""", (ctx.org_id,)).fetchall()
        owned: Dict[int, list] = {}
        for user_id, account_id, nickname in conn.execute(
                "SELECT investor_user_id, ctid_trader_account_id, nickname FROM accounts "
                "WHERE org_id = %s AND investor_user_id IS NOT NULL "
                "ORDER BY ctid_trader_account_id", (ctx.org_id,)).fetchall():
            owned.setdefault(user_id, []).append((int(account_id), nickname))
        # One /state round trip for the whole list, never one per investor or account.
        state = await _org_state(http_request.app.state.http, cfg, ctx.org_id)
        out = []
        for user_id, email, name, joined_at, kyc in rows:
            figures = pc.wallet_figures(conn, ctx.org_id, user_id)
            accounts = []
            for account_id, nickname in owned.get(user_id, []):
                equity, source, _positions = _equity_from(state, conn, account_id)
                accounts.append({"account_id": account_id, "nickname": nickname,
                                 "equity": pc.money(equity), "equity_source": source})
            out.append({
                "user_id": user_id, "email": email, "display_name": name,
                "joined_at": joined_at.isoformat(), "accounts": accounts,
                "balances": {w: pc.money(figures[w]["balance"]) for w in pc.WALLETS},
                "on_hold": pc.money(figures["main"]["on_hold"]),
                "available": pc.money(figures["main"]["available"]),
                "pending": pending_counts(conn, ctx.org_id, user_id),
                "kyc_status": kyc,
            })
        return out
```

- [x] **Step 4: Link and unlink one account**

Replace `class LinkBody` with:

```python
class LinkBody(BaseModel):
    account_id: int
```

Replace the whole `@router.put("/investors/{user_id}/account", ...)` route (`link_account`) with:

```python
    @router.post("/investors/{user_id}/accounts", status_code=201, response_model=Dict[str, Any])
    async def link_investor_account(user_id: int, body: LinkBody,
                                    ctx: OrgContext = Depends(require_org_role("admin")),
                                    conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Add one account to an investor under pc.link_account's rules: not
        the master, nobody else's, and under max_live_accounts (any platform,
        as before; only fulfil insists on MT5)."""
        _require_investor(conn, ctx.org_id, user_id)
        with conn.transaction():
            pc.link_account(conn, ctx.org_id, user_id, body.account_id)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_linked",
            actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id)
        return {"user_id": user_id, "account_id": body.account_id}

    @router.delete("/investors/{user_id}/accounts/{account_id}", status_code=204)
    async def unlink_investor_account(user_id: int, account_id: int,
                                      ctx: OrgContext = Depends(require_org_role("admin")),
                                      conn: psycopg.Connection = Depends(get_conn)):
        """Remove one link. Open transfers on the account keep their phase 1
        handling: an unlink never touched them, and the investor can no
        longer name the account in a new request."""
        _require_investor(conn, ctx.org_id, user_id)
        row = conn.execute(
            "UPDATE accounts SET investor_user_id = NULL WHERE org_id = %s "
            "AND investor_user_id = %s AND ctid_trader_account_id = %s "
            "RETURNING ctid_trader_account_id", (ctx.org_id, user_id, account_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_unlinked",
            actor_email=ctx.user_email, user_id=user_id, account_id=account_id)
        return Response(status_code=204)
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_summary.py tests/test_investor_access.py tests/test_portal_account_requests.py -q -p no:cacheprovider`
Expected: PASS. (`tests/test_rbac_matrix.py` still lists the removed PUT and fails until Task 8.)

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_admin.py api/tests/test_portal_multi_account.py api/tests/test_portal_summary.py api/tests/test_investor_access.py
git commit -m "feat(api): admins link and unlink one account at a time; investors list carries accounts[]

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 7: Investor summary — `accounts[]`, totals, `account_limit`, one `/state` call; `linked_account` removed

**Files:**
- Modify: `api/src/api/routes/portal_investor.py` (module constant `EQUITY_RANK`; `investor_summary`)
- Modify: `api/src/api/portal_common.py` (delete `linked_account`)
- Modify: `api/tests/test_portal_multi_account.py` (append)
- Modify: `api/tests/test_portal_summary.py` (three summary tests)
- Modify: `api/tests/test_portal_common.py` (`test_net_funded_and_open_account_transfers_out`)

**Interfaces:**
- Consumes: `pc.linked_accounts`, `pc.accounts_used`, `pc.portal_settings` (Task 2); `pc.org_state`, `pc.equity_from`, `pc.net_funded`, `pc.open_account_transfers_out`, `pc.floor_cents`, `_account_card` (existing).
- Produces: `GET investor/summary` without `link_state`, `account`, `account_available`; with `accounts: AccountSummary[]` (account id order; each `{account_id, nickname, platform, status, last_error, connected, mt5_login, mt5_server, equity_source, equity, net_funded, profit, account_available, open_positions}`), totals `equity` (null when any account's is unknown or there is none), `equity_source` (worst: unknown > last known > live; `unknown` with no account), `net_funded`, `profit` (`equity - net_funded` when equity is known), `open_positions`, and `account_limit: {max, used}`. `EQUITY_RANK = {"live": 0, "last known": 1, "unknown": 2}`. `pc.linked_account` no longer exists.

- [x] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_multi_account.py`:

```python
# ------------------------------------------------------------ summary (Task 7)


def test_the_summary_lists_every_account_with_totals_from_one_state_call(two, db):
    client, org_id, investor = two
    uid = investor["id"]
    with psycopg.connect(db, autocommit=True) as conn:
        # 1002 has no live figure, only what its MT5 terminal last reported.
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (1002, 'h-1002', 250, 250)")
        for aid, amount in ((1001, 600), (1002, 200)):
            conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
                "target_account_id, amount, status, done_at) "
                "VALUES (%s, %s, 'wallet', 'main', 'account', %s, %s, 'done', now())",
                (org_id, uid, aid, amount))
        conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_account_id, target_kind, "
            "target_wallet, amount, status) VALUES (%s, %s, 'account', 1001, 'wallet', 'main', 50, "
            "'requested')", (org_id, uid))
        conn.execute(
            "INSERT INTO account_requests (org_id, user_id, package_name, leverage, status, "
            "mt5_login, mt5_server, account_id, decided_at) "
            "VALUES (%s, %s, 'Standard', 100, 'fulfilled', 5001, 'Broker-Live', 1001, now())",
            (org_id, uid))
    open_account_request(db, org_id, uid, add_package(db, org_id))
    seen = _copier(client, {1001: _live(700, [{"position_id": 7, "symbol": "XAUUSD"}])})
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert len([u for u in seen if "/state" in u]) == 1
    first, second = body["accounts"]
    assert first == {"account_id": 1001, "nickname": None, "platform": "ctrader", "status": "ok",
                     "last_error": None, "connected": True, "mt5_login": 5001,
                     "mt5_server": "Broker-Live", "equity_source": "live", "equity": 700.0,
                     "net_funded": 600.0, "profit": 100.0, "account_available": 650.0,
                     "open_positions": 1}
    assert second["account_id"] == 1002 and second["mt5_login"] is None
    assert second["mt5_server"] is None and second["equity_source"] == "last known"
    assert second["equity"] == 250.0 and second["profit"] == 50.0
    assert body["equity"] == 950.0 and body["equity_source"] == "last known"
    assert body["net_funded"] == 800.0 and body["profit"] == 150.0
    assert body["open_positions"] == 1
    assert body["account_limit"] == {"max": 5, "used": 3}, "two accounts and one open request"
    for gone in ("account", "link_state", "account_available"):
        assert gone not in body


def test_one_unknown_equity_makes_the_total_unknown(two):
    client, org_id, _ = two
    _copier(client, {1001: _live(700)})      # 1002: no live figure and no MT5 report
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert [a["equity_source"] for a in body["accounts"]] == ["live", "unknown"]
    assert body["equity"] is None and body["profit"] is None
    assert body["equity_source"] == "unknown" and body["net_funded"] == 0.0
```

In `api/tests/test_portal_summary.py`:

`test_summary_before_an_account_is_linked` — replace

```python
    assert body["link_state"] == "unlinked" and body["account"] is None
    assert body["equity_source"] == "unknown" and body["equity"] is None
    assert body["net_funded"] == 0.0 and body["profit"] is None
    assert body["account_available"] is None and body["open_positions"] == 0
```

with

```python
    assert body["accounts"] == [] and body["account_limit"] == {"max": 5, "used": 0}
    assert body["equity_source"] == "unknown" and body["equity"] is None
    assert body["net_funded"] == 0.0 and body["profit"] is None
    assert body["open_positions"] == 0
```

`test_summary_figures_from_the_ledger_and_live_equity` — replace

```python
    assert body["link_state"] == "linked" and body["account"]["account_id"] == 1001
    assert body["equity"] == 2120.5 and body["equity_source"] == "live"
    assert body["net_funded"] == 2000.0 and body["profit"] == 120.5
    assert body["account_available"] == 2100.0, "equity minus the open account->wallet transfer"
    assert body["open_positions"] == 1
```

with

```python
    (account,) = body["accounts"]
    assert account["account_id"] == 1001
    assert body["equity"] == 2120.5 and body["equity_source"] == "live"
    assert body["net_funded"] == 2000.0 and body["profit"] == 120.5
    assert account["account_available"] == 2100.0, "equity minus the open account->wallet transfer"
    assert body["open_positions"] == 1
```

`test_summary_falls_back_to_last_known_equity_when_the_copier_is_down` — replace

```python
    assert body["equity"] == 4990.25 and body["equity_source"] == "last known"
    assert body["account"]["platform"] == "mt5" and body["account"]["connected"] is False
    assert body["account_available"] == 4990.25 and body["profit"] == 4990.25
```

with

```python
    (account,) = body["accounts"]
    assert body["equity"] == 4990.25 and body["equity_source"] == "last known"
    assert account["platform"] == "mt5" and account["connected"] is False
    assert account["account_available"] == 4990.25 and body["profit"] == 4990.25
```

In `api/tests/test_portal_common.py`, `test_net_funded_and_open_account_transfers_out`, replace

```python
        assert pc.linked_account(conn, org_id, investor["id"]) == 1001
        assert pc.linked_account(conn, org_id, 999999) is None
```

with

```python
        assert pc.linked_accounts(conn, org_id, investor["id"]) == [1001]
        assert pc.linked_accounts(conn, org_id, 999999) == []
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: FAIL — `KeyError: 'accounts'`.

- [x] **Step 3: The summary**

In `api/src/api/routes/portal_investor.py`, after `ENTRIES_MAX_LIMIT = 200` add:

```python
# Worst last: when accounts disagree the summary reports the worst source,
# and one unknown equity makes the total unknown.
EQUITY_RANK = {"live": 0, "last known": 1, "unknown": 2}
```

Replace `investor_summary` with:

```python
    @router.get("/investor/summary", response_model=Dict[str, Any])
    async def investor_summary(http_request: Request,
                               ctx: OrgContext = Depends(require_investor),
                               conn: psycopg.Connection = Depends(get_conn),
                               cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        org_name, display_name, member_since = conn.execute(
            "SELECT o.name, u.display_name, m.created_at FROM org_memberships m "
            "JOIN orgs o ON o.id = m.org_id JOIN users u ON u.id = m.user_id "
            "WHERE m.org_id = %s AND m.user_id = %s", (ctx.org_id, ctx.user_id)).fetchone()
        figures = pc.wallet_figures(conn, ctx.org_id, ctx.user_id)
        deposited, withdrawn = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN kind = 'deposit' THEN amount END), 0), "
            "COALESCE(SUM(CASE WHEN kind = 'withdrawal' THEN -amount END), 0) "
            "FROM wallet_entries WHERE org_id = %s AND user_id = %s",
            (ctx.org_id, ctx.user_id)).fetchone()
        # Transfers between the wallets and the trading accounts, settled.
        transferred_out, transferred_in = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN target_kind = 'account' THEN amount END), 0), "
            "COALESCE(SUM(CASE WHEN source_kind = 'account' THEN amount END), 0) "
            "FROM transfers WHERE org_id = %s AND user_id = %s AND status = 'done'",
            (ctx.org_id, ctx.user_id)).fetchone()
        flow = conn.execute(
            "SELECT (created_at AT TIME ZONE 'UTC')::date AS day, "
            "COALESCE(SUM(CASE WHEN kind = 'deposit' THEN amount END), 0), "
            "COALESCE(SUM(CASE WHEN kind = 'withdrawal' THEN -amount END), 0) "
            "FROM wallet_entries WHERE org_id = %s AND user_id = %s "
            "AND kind IN ('deposit', 'withdrawal') AND created_at >= now() - interval '90 days' "
            "GROUP BY day ORDER BY day", (ctx.org_id, ctx.user_id)).fetchall()
        settings = pc.portal_settings(conn, ctx.org_id)
        deposits_open = conn.execute(
            "SELECT 1 FROM payment_methods WHERE org_id = %s AND enabled LIMIT 1",
            (ctx.org_id,)).fetchone() is not None
        owned = pc.linked_accounts(conn, ctx.org_id, ctx.user_id)
        # One /state round trip for every account, never one per account.
        state = await pc.org_state(http_request.app.state.http, cfg, ctx.org_id) if owned else None
        # The login an account was handed over with; the latest decision wins.
        logins = {int(a): (login, server) for a, login, server in conn.execute(
            "SELECT account_id, mt5_login, mt5_server FROM account_requests "
            "WHERE org_id = %s AND user_id = %s AND status = 'fulfilled' AND account_id IS NOT NULL "
            "ORDER BY decided_at, id", (ctx.org_id, ctx.user_id)).fetchall()}
        accounts = []
        equity: Optional[Decimal] = Decimal("0") if owned else None
        funded_total = Decimal("0")
        sources = []
        open_positions = 0
        for account_id in owned:
            account_equity, source, positions = pc.equity_from(state, conn, account_id)
            funded = pc.net_funded(conn, ctx.org_id, ctx.user_id, account_id)
            available: Optional[Decimal] = None
            if account_equity is not None:
                available = pc.floor_cents(account_equity - pc.open_account_transfers_out(
                    conn, ctx.org_id, ctx.user_id, account_id))
            count = len([p for p in positions if isinstance(p, dict)])
            login, server = logins.get(account_id, (None, None))
            accounts.append({
                **_account_card(conn, ctx.org_id, account_id),
                "mt5_login": login, "mt5_server": server, "equity_source": source,
                "equity": pc.money(account_equity), "net_funded": pc.money(funded),
                "profit": pc.money(account_equity - funded if account_equity is not None else None),
                "account_available": pc.money(available), "open_positions": count})
            equity = None if equity is None or account_equity is None else equity + account_equity
            funded_total += funded
            sources.append(source)
            open_positions += count
        words = (display_name or "").split()
        return {
            "org": {"id": ctx.org_id, "name": org_name},
            "currency": "USD",
            "investor": {"display_name": display_name,
                         "first_name": words[0] if words else display_name,
                         "member_since": member_since.isoformat()},
            "wallets": {w: {"balance": pc.money(figures[w]["balance"]),
                            "on_hold": pc.money(figures[w]["on_hold"]),
                            "available": pc.money(figures[w]["available"])} for w in pc.WALLETS},
            "totals": {"deposited": pc.money(Decimal(deposited)),
                       "withdrawn": pc.money(Decimal(withdrawn)),
                       "transferred_in": pc.money(Decimal(transferred_in)),
                       "transferred_out": pc.money(Decimal(transferred_out))},
            "cash_flow": [{"date": day.isoformat(), "deposits": pc.money(Decimal(dep)),
                           "withdrawals": pc.money(Decimal(wd))} for day, dep, wd in flow],
            "pending": pending_counts(conn, ctx.org_id, ctx.user_id),
            "deposits_open": deposits_open,
            "withdrawal_rules": {"min": pc.money(settings["withdrawal_min"]),
                                 "fee_pct": float(settings["withdrawal_fee_pct"])},
            "accounts": accounts,
            "account_limit": {"max": settings["max_live_accounts"],
                              "used": pc.accounts_used(conn, ctx.org_id, ctx.user_id)},
            "equity_source": max(sources, key=EQUITY_RANK.__getitem__, default="unknown"),
            "equity": pc.money(equity),
            "net_funded": pc.money(funded_total),
            "profit": pc.money(equity - funded_total if equity is not None else None),
            "open_positions": open_positions,
            "kyc_status": pid.kyc_status(conn, ctx.org_id, ctx.user_id),
        }
```

- [x] **Step 4: Remove `linked_account`**

Delete `linked_account` from `api/src/api/portal_common.py`, and in the module docstring change `the linked-account and equity resolvers` to `the account ownership and equity resolvers`. Then prove nothing calls it:

Run: `grep -rnw linked_account api/src api/tests`
Expected: no output.

- [x] **Step 5: Run the tests to verify they pass**

Run: `"$PY" -m pytest tests/test_portal_multi_account.py tests/test_portal_summary.py tests/test_portal_common.py tests/test_portal_transfers.py tests/test_portal_deposits.py tests/test_portal_account_requests.py tests/test_investor_access.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_investor.py api/src/api/portal_common.py api/tests/test_portal_multi_account.py api/tests/test_portal_summary.py api/tests/test_portal_common.py
git commit -m "feat(api): investor summary lists every account with totals and the account limit

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 8: RBAC matrix rows for the new routes; the full API suite

**Files:**
- Modify: `api/tests/test_rbac_matrix.py` (`MATRIX`; `test_destructive_rows_allowed`)

**Interfaces:**
- Consumes: the routes of Task 6.
- Produces: rows for `POST investors/{investor}/accounts` and `DELETE investors/{investor}/accounts/100`; the removed PUT row is gone.

- [x] **Step 1: Replace the PUT row**

In `MATRIX`, replace

```python
    ("PUT",    "investors/{investor}/account",   {"account_id": None},           "admin"),
```

with

```python
    ("POST",   "investors/{investor}/accounts",  {"account_id": 100},            "admin"),
    ("DELETE", "investors/{investor}/accounts/100", None,                        "admin"),
```

What the allowed calls prove: the POST answers 400 (account 100 is the master), after the role check; the DELETE is destructive, so `test_role_thresholds` probes only the denied roles and `test_destructive_rows_allowed` runs the admin's call.

In `test_destructive_rows_allowed`, directly after `login_as(client, users["admin"])`, add:

```python
    r = _call(client, "DELETE", org_id,
              f"investors/{users['investor']['id']}/accounts/100", None)
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"   # not theirs
```

- [x] **Step 2: Run the matrix**

Run: `"$PY" -m pytest tests/test_rbac_matrix.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 3: Run the whole API suite**

Run: `"$PY" -m pytest tests -q -p no:cacheprovider`
Expected: PASS except the known 7 `test_events_ws.py` errors and the one EA-download CRLF failure.

- [x] **Step 4: Commit**

```bash
git add api/tests/test_rbac_matrix.py
git commit -m "test: RBAC matrix rows for the investor account link and unlink routes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 9: Dashboard foundation — types, fixtures, `accountName`, `pickAccount`

**Files:**
- Modify: `dashboard/src/lib/types.ts`
- Modify: `dashboard/src/lib/investor.ts`, `dashboard/src/lib/investor.test.ts`
- Modify: `dashboard/src/test/portalFixtures.ts`, `dashboard/src/test/portalFixtures.test.ts`

**Interfaces:**
- Consumes: the API shapes of Tasks 5-7.
- Produces: types `EquitySource`, `AccountSummary`, `InvestorAccount`; `InvestorSummary.accounts: AccountSummary[]`, `InvestorSummary.account_limit: { max: number; used: number }`; `InvestorRow.accounts: InvestorAccount[]`; `PortalSettings.max_live_accounts: number`. The old `InvestorSummary.link_state | account | account_available` and `InvestorRow.account_id | nickname | equity | equity_source` STAY until Task 15 (transitional, so every task in between compiles); no page reads them after Task 14. `accountName(a: { account_id: number; nickname: string | null; mt5_login?: number | null }): string` -> `"MT5 <login>"`, else the nickname, else `"Account <id>"`. `pickAccount<T extends { account_id: number }>(accounts: T[], wanted: string | null): T | null`. Fixture `accountSummaryFixture(overrides)`; `summaryFixture` adds `accounts: [accountSummaryFixture()]`, `account_limit: { max: 5, used: 1 }`; `investorRowFixture` adds `accounts: [{ account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live' }]`.

- [x] **Step 1: Write the failing tests**

In `dashboard/src/lib/investor.test.ts`, add `accountName` and `pickAccount` to the existing `import { … } from './investor'`, and append inside the `describe('investor helpers', …)` block:

```ts
  test('accountName prefers the MT5 login, then the nickname, then the id', () => {
    expect(accountName({ account_id: 9, nickname: 'Growth', mt5_login: 5001 })).toBe('MT5 5001')
    expect(accountName({ account_id: 9, nickname: 'Growth', mt5_login: null })).toBe('Growth')
    expect(accountName({ account_id: 9, nickname: null })).toBe('Account 9')
  })

  test('pickAccount takes the named account when it is there, else the first', () => {
    const list = [{ account_id: 1 }, { account_id: 2 }]
    expect(pickAccount(list, '2')).toEqual({ account_id: 2 })
    expect(pickAccount(list, '7')).toEqual({ account_id: 1 })
    expect(pickAccount(list, null)).toEqual({ account_id: 1 })
    expect(pickAccount([], '1')).toBeNull()
  })
```

In `dashboard/src/test/portalFixtures.test.ts`, add `accountSummaryFixture` to the import from `./portalFixtures` and append:

```ts
test('the summary carries one account and the cap; the investor row its accounts', () => {
  const s = summaryFixture()
  expect(s.accounts.map((a) => a.account_id)).toEqual([555])
  expect(s.account_limit).toEqual({ max: 5, used: 1 })
  expect(accountSummaryFixture({ mt5_login: null }).mt5_login).toBeNull()
  expect(investorRowFixture().accounts).toEqual([
    { account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live' }])
})
```

- [x] **Step 2: Run them to verify they fail**

Run (from `dashboard/`): `npx vitest run src/lib/investor.test.ts src/test/portalFixtures.test.ts`
Expected: FAIL — `accountName is not a function`, `accountSummaryFixture is not a function`.

- [x] **Step 3: Types**

In `dashboard/src/lib/types.ts`, directly above `/** GET investor/summary. …`, add:

```ts
export type EquitySource = 'live' | 'last known' | 'unknown'

/** One of the investor's live accounts on GET investor/summary, oldest first. */
export interface AccountSummary {
  account_id: number; nickname: string | null; platform: string
  status: string; last_error: string | null; connected: boolean
  /** From the fulfilled account request that linked it, else null. */
  mt5_login: number | null; mt5_server: string | null
  equity_source: EquitySource; equity: number | null; net_funded: number; profit: number | null
  /** Equity less open account -> wallet transfers, floored to cents; null while equity is unknown. */
  account_available: number | null
  open_positions: number
}

/** One account on a row of the admin's GET investors. */
export interface InvestorAccount { account_id: number; nickname: string | null; equity: number | null; equity_source: EquitySource }
```

In `InvestorSummary`, directly after the `withdrawal_rules` line, add:

```ts
  /** The top-level equity, net_funded, profit, open_positions and equity_source are totals over these. */
  accounts: AccountSummary[]
  /** used = owned accounts + open account requests. */
  account_limit: { max: number; used: number }
  // Transitional (phase 3 Task 15 removes them): link_state, account, account_available.
```

In `InvestorRow`, directly after `joined_at: string`, add:

```ts
  accounts: InvestorAccount[]
  // Transitional (phase 3 Task 15 removes them): account_id, nickname, equity, equity_source.
```

Replace `export interface PortalSettings { withdrawal_min: number; withdrawal_fee_pct: number }` with:

```ts
export interface PortalSettings { withdrawal_min: number; withdrawal_fee_pct: number; max_live_accounts: number }
```

- [x] **Step 4: The helpers**

In `dashboard/src/lib/investor.ts`, after `moneyOrDash`, add:

```ts
/** How an account is named to people: its MT5 login when the request that
 *  opened it says so, else the admin's nickname, else its id. */
export function accountName(a: { account_id: number; nickname: string | null; mt5_login?: number | null }): string {
  if (a.mt5_login != null) return `MT5 ${a.mt5_login}`
  return a.nickname ?? `Account ${a.account_id}`
}

/** The account a page shows: the one `?account=` names when it is in the
 *  list, else the first; null with no accounts. */
export function pickAccount<T extends { account_id: number }>(accounts: T[], wanted: string | null): T | null {
  return accounts.find((a) => String(a.account_id) === wanted) ?? accounts[0] ?? null
}
```

- [x] **Step 5: Fixtures**

In `dashboard/src/test/portalFixtures.ts`, add `AccountSummary` to the type import, and above `summaryFixture` add:

```ts
export function accountSummaryFixture(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return {
    account_id: 555, nickname: 'Growth', platform: 'mt5', status: 'ok', last_error: null, connected: true,
    mt5_login: 5001, mt5_server: 'Broker-Live', equity_source: 'live', equity: 1240.25, net_funded: 1000,
    profit: 240.25, account_available: 1240.25, open_positions: 2,
    ...overrides,
  }
}
```

In `summaryFixture`, directly after `withdrawal_rules: { min: 50, fee_pct: 1 },`, add:

```ts
    accounts: [accountSummaryFixture()],
    account_limit: { max: 5, used: 1 },
```

In `investorRowFixture`, directly after `joined_at: '2026-09-01T09:00:00Z',`, add:

```ts
    accounts: [{ account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live' }],
```

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/lib/investor.test.ts src/test/portalFixtures.test.ts && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [x] **Step 7: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/lib/investor.ts dashboard/src/lib/investor.test.ts dashboard/src/test/portalFixtures.ts dashboard/src/test/portalFixtures.test.ts
git commit -m "feat(dashboard): account summary types, accountName and pickAccount, multi-account fixtures

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 10: Investor dashboard — the Live accounts tray

**Files:**
- Modify: `dashboard/src/pages/investor/InvestorDashboard.tsx`
- Modify: `dashboard/src/pages/investor/InvestorDashboard.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary.accounts`, `account_limit`, `equity`, `equity_source`; `accountName` (Task 9); `accountSummaryFixture`.
- Produces: Card `Live accounts`: one row per account — a link named `accountName(a)` to `${base}/account?account=<id>`, `connected` / `terminal offline`, equity, signed profit — a `Total equity` row with `summary.equity` and its source, and the header action link `Open live account` (to `${base}/open-account`) while `account_limit.used < account_limit.max`. No accounts: the existing "Your account is being set up" NextStep. `VerificationCard` gets `linked={summary.accounts.length > 0}`.

- [x] **Step 1: Write the failing tests**

In `dashboard/src/pages/investor/InvestorDashboard.test.tsx`, import `accountSummaryFixture` with the other fixtures, and replace the tail of `linked` and all of `unlinked`:

```ts
  accounts: [accountSummaryFixture({
    account_id: 1001, nickname: 'Inv', mt5_login: null, mt5_server: null, equity: 5120.5,
    net_funded: 3000, profit: 2120.5, account_available: 5120.5, open_positions: 1,
  })],
  account_limit: { max: 5, used: 1 },
  equity_source: 'live', equity: 5120.5, net_funded: 3000, profit: 2120.5, open_positions: 1,
}
const unlinked: InvestorSummary = {
  ...linked, accounts: [], account_limit: { max: 5, used: 0 }, equity_source: 'unknown',
  equity: null, profit: null, open_positions: 0,
}
```

(that is: the `link_state`, `account` and `account_available` lines of both go.)

In `linked investors see the balance, the tiles, the account card, pending lines and recent activity`, replace

```ts
  expect(screen.getByText('Inv')).toBeInTheDocument()
  expect(screen.getByText('connected')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'View account' })).toHaveAttribute('href', '/org/1/invest/account')
```

with

```ts
  expect(screen.getByRole('link', { name: 'Inv' })).toHaveAttribute('href', '/org/1/invest/account?account=1001')
  expect(screen.getByText('connected')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open live account' })).toHaveAttribute('href', '/org/1/invest/open-account')
```

In `unlinked investors see the setup notice with the new copy and no account card`, replace `expect(screen.queryByRole('link', { name: 'View account' })).not.toBeInTheDocument()` with `expect(screen.queryByRole('heading', { name: 'Live accounts' })).not.toBeInTheDocument()`.

In `a verified investor without an account is offered Open account`, replace the `mockRoutes(...)` line with:

```ts
  mockRoutes({ ...summaryFixture(), kyc_status: 'approved', accounts: [], account_limit: { max: 5, used: 0 } })
```

Append:

```ts
test('several accounts each get a row in the tray with the total; at the cap Open live account goes', async () => {
  mockRoutes({
    ...linked,
    accounts: [
      accountSummaryFixture({ account_id: 1001, mt5_login: 5001, equity: 700, profit: 100, connected: true }),
      accountSummaryFixture({ account_id: 1002, mt5_login: null, nickname: 'Swing', equity: 250, profit: -50, connected: false }),
    ],
    account_limit: { max: 2, used: 2 }, equity: 950, equity_source: 'last known',
  })
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const tray = (await screen.findByRole('heading', { name: 'Live accounts' })).closest('section')!
  expect(within(tray).getByRole('link', { name: 'MT5 5001' })).toHaveAttribute('href', '/org/1/invest/account?account=1001')
  expect(within(tray).getByRole('link', { name: 'Swing' })).toHaveAttribute('href', '/org/1/invest/account?account=1002')
  expect(within(tray).getByText('terminal offline')).toBeInTheDocument()
  expect(within(tray).getByText('950.00 USD')).toBeInTheDocument()
  expect(within(tray).getByText(/last known/)).toBeInTheDocument()
  expect(within(tray).queryByRole('link', { name: 'Open live account' })).not.toBeInTheDocument()
})
```

- [x] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorDashboard.test.tsx`
Expected: FAIL — no `Live accounts` heading; `Inv` is not a link.

- [x] **Step 3: The tray**

In `dashboard/src/pages/investor/InvestorDashboard.tsx`, import `accountName` with the other `../../lib/investor` names. Replace

```tsx
          <VerificationCard status={summary.kyc_status} linked={summary.link_state === 'linked'} base={base} />
```

with

```tsx
          <VerificationCard status={summary.kyc_status} linked={summary.accounts.length > 0} base={base} />
```

and replace everything from `{summary.link_state === 'linked' && summary.account ? (` through the `</Card>` that closes that `Trading account` card (the `) : (` and the NextStep after it stay) with:

```tsx
          {summary.accounts.length > 0 ? (
            <Card title="Live accounts"
                  actions={summary.account_limit.used < summary.account_limit.max
                    ? <Button variant="ghost" size="sm" to={`${base}/open-account`}>Open live account</Button>
                    : undefined}>
              <ul className="inset divide-y divide-line text-sm">
                {summary.accounts.map((a) => (
                  <li key={a.account_id} className="px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span className="flex-1 min-w-0">
                      <Button variant="ghost" size="sm" to={`${base}/account?account=${a.account_id}`}>
                        {accountName(a)}
                      </Button>
                    </span>
                    <span className={a.connected ? 'text-profit' : 'text-warn-deep'}>
                      {a.connected ? 'connected' : 'terminal offline'}
                    </span>
                    <Money value={a.equity} unit={unit} />
                    <Money value={a.profit} unit={unit} signed
                           className={a.profit != null && a.profit < 0 ? 'text-loss' : 'text-profit'} />
                  </li>
                ))}
                <li className="px-4 py-2.5 flex flex-wrap items-center justify-between gap-3">
                  <span className="desk-label">Total equity</span>
                  <span className="text-ink font-semibold">
                    <Money value={summary.equity} unit={unit} />
                    <span className="text-xs text-ink-soft"> {summary.equity_source}</span>
                  </span>
                </li>
              </ul>
            </Card>
```

The `) : ( <NextStep title="Your account is being set up"> … </NextStep> )}` branch after it stays as it is.

- [x] **Step 4: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorDashboard.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors, no `act(` warning.

- [x] **Step 5: Commit**

```bash
git add dashboard/src/pages/investor/InvestorDashboard.tsx dashboard/src/pages/investor/InvestorDashboard.test.tsx
git commit -m "feat(dashboard): Live accounts tray on the investor dashboard

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 11: Account page and History page — the account switcher

**Files:**
- Create: `dashboard/src/pages/investor/AccountSwitcher.tsx`
- Modify: `dashboard/src/pages/investor/InvestorAccount.tsx`, `InvestorAccount.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorHistory.tsx`, `InvestorHistory.test.tsx`

**Interfaces:**
- Consumes: `accountName`, `pickAccount` (Task 9); `GET investor/positions|analytics|history/deals` with `account_id` (Task 3); `AccountSummary.mt5_login|mt5_server`.
- Produces: `AccountSwitcher({ accounts: AccountSummary[]; value: number | null; onChange: (accountId: number) => void })` — renders nothing with fewer than two accounts, else a `Select` labelled `Trading account` whose options are `accountName(a)` with value `account_id`. Account and History pages keep the pick in `?account=<id>` (`setSearchParams({ account }, { replace: true })`) and send `account_id=<id>` to the API whenever an account is picked. The Account page's login card (`Your MT5 login`) shows the picked account's `mt5_login` / `mt5_server` and no longer fetches `investor/account-requests`.

- [x] **Step 1: Write the failing tests**

In `dashboard/src/pages/investor/InvestorAccount.test.tsx`:

Change the imports to:

```ts
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorAccount from './InvestorAccount'
import { mockUseOrg } from '../../test/orgMock'
import { accountSummaryFixture, summaryFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'
import type { InvestorSummary } from '../../lib/types'
```

Replace `linked`, `unlinked` and `mockRoutes` with:

```ts
const inv = accountSummaryFixture({
  account_id: 1001, nickname: 'Inv', mt5_login: null, mt5_server: null, equity: 5120.5,
  net_funded: 5000, profit: 120.5, account_available: 5120.5, open_positions: 1,
})
const linked: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', accounts: [inv], account_limit: { max: 5, used: 1 },
  equity_source: 'live', equity: 5120.5, net_funded: 5000, profit: 120.5, open_positions: 1,
}
const unlinked: InvestorSummary = {
  ...linked, accounts: [], account_limit: { max: 5, used: 0 }, equity_source: 'unknown',
  equity: null, profit: null, open_positions: 0,
}

function mockRoutes(summary: InvestorSummary) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.includes('/investor/positions')) {
      return jsonResponse({ equity_source: 'live', positions: [
        { position_id: 7, symbol: 'XAUUSD', side: 'BUY', volume: 1, entry_price: 4350,
          current_price: 4360, stop_loss: 4300, take_profit: 4400, pnl_quote: 10 }] })
    }
    if (url.includes('/investor/analytics')) {
      return jsonResponse({ closed_trades: 3, wins: 2, losses: 1, win_rate: 66.7,
        profit_factor: 2.1, best_trade: 50, worst_trade: -20, avg_win: 40, avg_loss: -20,
        net_pnl: 120.5, gross_wins: 140.5, gross_losses: 20, max_drawdown: 30,
        max_drawdown_pct: 0.6, equity_curve: [{ timestamp: 1, balance: 5000 },
        { timestamp: 2, balance: 5120.5 }], per_symbol: [], weekly: [], weeks: 4, truncated: false })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const urls = (fetchMock: ReturnType<typeof mockRoutes>) => fetchMock.mock.calls.map(([u]) => String(u))

function LocationProbe() {
  return <span data-testid="search">{useLocation().search}</span>
}
```

In `linked investors see the trading account, open positions and the 4-week snapshot`, append `expect(screen.queryByLabelText('Trading account')).not.toBeInTheDocument()`.

Replace `a fulfilled account request shows its MT5 login on the Account page` with:

```tsx
test("the picked account's MT5 login is on the Account page", async () => {
  mockRoutes({ ...linked, accounts: [{ ...inv, mt5_login: 5001, mt5_server: 'Broker-Live' }] })
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  const card = (await screen.findByRole('heading', { name: 'Your MT5 login' })).closest('section')!
  expect(within(card).getByText('5001')).toBeInTheDocument()
  expect(within(card).getByText('Broker-Live')).toBeInTheDocument()
  await screen.findByText('XAUUSD')
})

test('with several accounts a switcher picks one, keeps it in the URL and loads its figures', async () => {
  const swing = { ...inv, account_id: 1002, nickname: 'Swing', mt5_login: 6002, mt5_server: 'Broker-Live' }
  const fetchMock = mockRoutes({ ...linked, accounts: [inv, swing] })
  render(
    <MemoryRouter initialEntries={['/org/1/invest/account?account=1002']}>
      <InvestorAccount /><LocationProbe />
    </MemoryRouter>)
  const picker = await screen.findByLabelText('Trading account')
  await waitFor(() => expect(picker).toHaveValue('1002'))
  expect(await screen.findByText('6002')).toBeInTheDocument()
  expect(urls(fetchMock)).toContain('/api/orgs/1/investor/positions?account_id=1002')
  await userEvent.selectOptions(picker, '1001')
  await waitFor(() => expect(urls(fetchMock)).toContain('/api/orgs/1/investor/positions?account_id=1001'))
  expect(urls(fetchMock)).toContain('/api/orgs/1/investor/analytics?weeks=4&account_id=1001')
  expect(screen.getByTestId('search')).toHaveTextContent('?account=1001')
  await waitFor(() => expect(screen.queryByRole('heading', { name: 'Your MT5 login' })).not.toBeInTheDocument())
})
```

In `dashboard/src/pages/investor/InvestorHistory.test.tsx`:

Change the imports to add `waitFor` from `@testing-library/react` and `import { accountSummaryFixture, summaryFixture } from '../../test/portalFixtures'`. In `beforeEach`, add as the first route of the `fetch` stub:

```ts
    if (url.endsWith('/investor/summary')) return jsonResponse(summaryFixture())
```

Add below `wholeText`:

```ts
const historyCalls = () => (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
  .map(([u]) => String(u)).filter((u) => u.includes('/investor/history/deals'))
```

In `lists closed deals for the last week and pages earlier`, replace

```ts
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
  const first = String(fetchMock.mock.calls[0][0])
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  const second = String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0])
```

with

```ts
  const first = historyCalls()[0]
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  await waitFor(() => expect(historyCalls()).toHaveLength(2))
  const second = historyCalls()[1]
```

Append:

```tsx
test('the history follows the picked account', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(summaryFixture({ accounts: [
        accountSummaryFixture({ account_id: 1001, mt5_login: 5001 }),
        accountSummaryFixture({ account_id: 1002, mt5_login: 6002 })] }))
    }
    if (url.includes('/investor/history/deals')) return jsonResponse({ deals: [deal], has_more: false })
    return jsonResponse({})
  }))
  render(<MemoryRouter initialEntries={['/org/1/invest/history?account=1002']}><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(historyCalls().at(-1)).toMatch(/&account_id=1002$/)
  await userEvent.selectOptions(screen.getByLabelText('Trading account'), '1001')
  await waitFor(() => expect(historyCalls().at(-1)).toMatch(/&account_id=1001$/))
})
```

- [x] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorAccount.test.tsx src/pages/investor/InvestorHistory.test.tsx`
Expected: FAIL — no `Trading account` picker; positions are fetched without `account_id`; the login card waits for an account request.

- [x] **Step 3: The switcher**

Create `dashboard/src/pages/investor/AccountSwitcher.tsx`:

```tsx
import { accountName } from '../../lib/investor'
import Select from '../../components/Select'
import type { AccountSummary } from '../../lib/types'

/**
 * "Trading account" picker for the investor pages that show one account at
 * a time (Account, History). Hidden with fewer than two accounts; the page
 * keeps the pick in `?account=` so a refresh keeps it.
 */
export default function AccountSwitcher({ accounts, value, onChange }: {
  accounts: AccountSummary[]
  value: number | null
  onChange: (accountId: number) => void
}) {
  if (accounts.length < 2) return null
  return (
    <label className="block w-64">
      <span className="desk-label block mb-1">Trading account</span>
      <Select aria-label="Trading account" block value={value ?? ''}
              onChange={(e) => onChange(Number(e.target.value))}>
        {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{accountName(a)}</option>)}
      </Select>
    </label>
  )
}
```

- [x] **Step 4: The Account page**

In `dashboard/src/pages/investor/InvestorAccount.tsx`:

Change the imports: `import { useCallback, useEffect, useRef, useState } from 'react'`, add `import { useSearchParams } from 'react-router-dom'`, import `ACCOUNT_CURRENCY, accountName, pickAccount` from `../../lib/investor`, add `import AccountSwitcher from './AccountSwitcher'`, and drop `AccountRequest` from the type import (`import type { Analytics, InvestorPositions, InvestorSummary } from '../../lib/types'`).

Replace the state and `refresh` at the top of `InvestorAccount` (from `const [summary, …` through the `refresh` `useCallback`) with:

```tsx
  const [params, setParams] = useSearchParams()
  const wanted = params.get('account')
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  // Bumped per load: a switch starts a new load while the previous
  // account's figures may still be in flight; only the newest may land.
  const seq = useRef(0)

  const refresh = useCallback(async () => {
    const mine = ++seq.current
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      const a = pickAccount(s.accounts, wanted)
      const [p, an] = a
        ? await Promise.all([
            orgApi<InvestorPositions>(orgId, `investor/positions?account_id=${a.account_id}`),
            orgApi<Analytics>(orgId, `investor/analytics?weeks=4&account_id=${a.account_id}`),
          ])
        : [null, null] as const
      if (mine !== seq.current) return
      setSummary(s); setPositions(p); setAnalytics(an)
      setError(null)
    } catch (err) {
      if (mine !== seq.current) return
      setError(errorText(err, 'Could not load your account'))
    }
    setLoaded(true)
  }, [orgId, wanted])
```

Replace `const unit = summary?.currency ?? ACCOUNT_CURRENCY` with:

```tsx
  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  const account = summary ? pickAccount(summary.accounts, wanted) : null
```

In the JSX, directly after the error `Banner`, add:

```tsx
      {summary && (
        <AccountSwitcher accounts={summary.accounts} value={account?.account_id ?? null}
                         onChange={(id) => setParams({ account: String(id) }, { replace: true })} />
      )}
```

Replace the `{login && ( <Card title="Your MT5 login"> … </Card> )}` block with:

```tsx
      {account?.mt5_login != null && (
        <Card title="Your MT5 login">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 text-sm">
            <div><dt className="desk-label">Login</dt><dd className="num text-ink">{account.mt5_login}</dd></div>
            <div><dt className="desk-label">Server</dt><dd className="num text-ink">{account.mt5_server ?? '—'}</dd></div>
          </dl>
        </Card>
      )}
```

Replace `{summary && summary.link_state !== 'linked' && (` with `{summary && summary.accounts.length === 0 && (`.

Replace the `{summary?.account && ( <Card title="Trading account"> … </Card> )}` block with:

```tsx
      {account && (
        <Card title="Trading account">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="desk-label">Account</dt>
              <dd className="text-ink">{accountName(account)}</dd></div>
            <div><dt className="desk-label">Platform</dt>
              <dd className="text-ink uppercase">{account.platform}</dd></div>
            <div><dt className="desk-label">Connection</dt>
              <dd className={account.connected ? 'text-profit' : 'text-warn-deep'}>
                {account.connected ? 'connected' : 'terminal offline'}
              </dd></div>
            <div><dt className="desk-label">Open positions</dt>
              <dd className="num text-ink">{account.open_positions}</dd></div>
            <div><dt className="desk-label">Equity</dt>
              <dd className="text-ink"><Money value={account.equity} unit={unit} />
                <span className="text-xs text-ink-soft"> {account.equity_source}</span></dd></div>
            <div><dt className="desk-label">Net funded</dt>
              <dd className="text-ink"><Money value={account.net_funded} unit={unit} /></dd></div>
            <div><dt className="desk-label">Profit</dt>
              <dd className={account.profit != null && account.profit < 0 ? 'text-loss' : 'text-profit'}>
                <Money value={account.profit} unit={unit} /></dd></div>
            <div><dt className="desk-label">Available to move</dt>
              <dd className="text-ink"><Money value={account.account_available} unit={unit} /></dd></div>
          </dl>
        </Card>
      )}
```

The positions and analytics cards stay as they are.

- [x] **Step 5: The History page**

In `dashboard/src/pages/investor/InvestorHistory.tsx`: add `import { useSearchParams } from 'react-router-dom'`, import `ACCOUNT_CURRENCY, pickAccount` from `../../lib/investor`, `import AccountSwitcher from './AccountSwitcher'`, and change the type import to `import type { AccountSummary, Deal, InvestorSummary } from '../../lib/types'`.

At the top of `InvestorHistory`, after `const { orgId } = useOrg()`, add:

```tsx
  const [params, setParams] = useSearchParams()
  const wanted = params.get('account')
  const [accounts, setAccounts] = useState<AccountSummary[]>([])
```

Replace the `try` block of `load` and its dependency list with:

```tsx
    try {
      // The summary names the accounts; with none the history call answers
      // 409 "no account linked yet", shown as today.
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setAccounts(s.accounts)
      const a = pickAccount(s.accounts, wanted)
      const r = await orgApi<{ deals: Deal[]; has_more: boolean }>(
        orgId, `investor/history/deals?from=${windowEnd - WEEK_MS}&to=${windowEnd}`
          + (a ? `&account_id=${a.account_id}` : ''))
      setDeals(r.deals.filter((d) => d.close != null))
    } catch (err) {
      setError(errorText(err, 'Could not load your history'))
    } finally {
      setLoading(false)
    }
  }, [orgId, windowEnd, wanted])
```

In the JSX, directly after the error `Banner`, add:

```tsx
      <AccountSwitcher accounts={accounts} value={pickAccount(accounts, wanted)?.account_id ?? null}
                       onChange={(id) => setParams({ account: String(id) }, { replace: true })} />
```

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorAccount.test.tsx src/pages/investor/InvestorHistory.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors, no `act(` warning.

- [x] **Step 7: Commit**

```bash
git add dashboard/src/pages/investor/AccountSwitcher.tsx dashboard/src/pages/investor/InvestorAccount.tsx dashboard/src/pages/investor/InvestorAccount.test.tsx dashboard/src/pages/investor/InvestorHistory.tsx dashboard/src/pages/investor/InvestorHistory.test.tsx
git commit -m "feat(dashboard): account switcher on the Account and History pages, kept in ?account=

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 12: Transfer and Deposit — one choice per account

**Files:**
- Modify: `dashboard/src/pages/investor/InvestorTransfer.tsx`, `InvestorTransfer.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorDeposit.tsx`, `InvestorDeposit.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary.accounts`; `accountName` (Task 9); `POST investor/transfers`, `POST investor/deposits` with the account id (Task 3).
- Produces: `transferOptions(s)` adds one `account:<id>` option per account, labelled `Trading account` when there is one and `Trading account <accountName>` when there are several, with that account's `account_available`; transfer rows name the account the same way. Deposit: with several accounts and `Deposit to` = `Trading account`, a `Select` labelled `Which trading account` (options `accountName`) picks `target_account_id`; with one account it stays implicit (that account's id, as today).

- [x] **Step 1: Write the failing tests**

In `dashboard/src/pages/investor/InvestorTransfer.test.tsx`, import `accountSummaryFixture` with the other fixtures and replace `linked` / `unlinked`:

```ts
const inv = accountSummaryFixture({
  account_id: 1001, nickname: 'Inv', mt5_login: null, mt5_server: null, equity: 2500, account_available: 2500,
})
const linked: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', accounts: [inv],
  wallets: {
    main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 300, on_hold: 0, available: 300 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
  equity_source: 'live', equity: 2500,
}
const unlinked: InvestorSummary = { ...linked, accounts: [], equity_source: 'unknown', equity: null }
```

Append:

```ts
test('several accounts each become a choice with their own available figure, named in the review', async () => {
  const swing = accountSummaryFixture({ account_id: 1002, nickname: 'Swing', mt5_login: 6002, account_available: 300 })
  const fetchMock = mockRoutes({ summary: { ...linked, accounts: [inv, swing] } })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  const to = await screen.findByLabelText('To')
  expect(within(to).getAllByRole('option').map((o) => o.textContent))
    .toEqual(['Trading account Inv', 'Trading account MT5 6002'])
  await userEvent.selectOptions(screen.getByLabelText('From'), 'account:1002')
  expect(screen.getByText('300.00 USD')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Amount in USD'), '100')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 100.00 USD from Trading account MT5 6002 to My wallet?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    source: { kind: 'account', account_id: 1002 }, target: { kind: 'wallet', wallet: 'main' },
    amount: '100', mpin: '123456',
  })
})
```

In `dashboard/src/pages/investor/InvestorDeposit.test.tsx`, import `accountSummaryFixture` with the other fixtures and `AccountSummary` with the types; replace `summary` with:

```ts
const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', deposits_open: true,
  accounts: [accountSummaryFixture({ account_id: 1001, nickname: 'Inv', mt5_login: null })],
}
```

In `mockRoutes`, add `accounts?: AccountSummary[]` to the `opts` type and replace the summary route with:

```ts
    if (url.endsWith('/investor/summary')) {
      return jsonResponse({
        ...summary, deposits_open: opts.open ?? true,
        ...(opts.linked === false ? { accounts: [] } : {}),
        ...(opts.accounts ? { accounts: opts.accounts } : {}),
      })
    }
```

In `Trading account is offered as the target when an account is linked, and is posted with its id`, after the radio click add `expect(screen.queryByLabelText('Which trading account')).not.toBeInTheDocument()`. Append:

```ts
test('with several accounts the notice names the one picked', async () => {
  const fetchMock = mockRoutes({ accounts: [
    accountSummaryFixture({ account_id: 1001, nickname: 'Inv', mt5_login: null }),
    accountSummaryFixture({ account_id: 1002, nickname: 'Swing', mt5_login: 6002 }),
  ] })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  expect(screen.queryByLabelText('Which trading account')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('radio', { name: 'Trading account' }))
  const which = screen.getByLabelText('Which trading account')
  expect(within(which).getAllByRole('option').map((o) => o.textContent)).toEqual(['Inv', 'MT5 6002'])
  await userEvent.selectOptions(which, '1002')
  await userEvent.click(screen.getByRole('button', { name: '250' }))
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    method_id: 5, amount: '250', reference: 'abc', receipt_file_id: null,
    target: 'account', target_account_id: 1002, note: null,
  })
})
```

- [x] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorTransfer.test.tsx src/pages/investor/InvestorDeposit.test.tsx`
Expected: FAIL — no account option (the page still reads `link_state`), no `Which trading account` select.

- [x] **Step 3: Transfer**

In `dashboard/src/pages/investor/InvestorTransfer.tsx`, import `accountName` with the other `../../lib/investor` names and add `AccountSummary` to the type import. Replace `transferOptions` and `refLabel` with:

```ts
/** "Trading account", or "Trading account MT5 5001" once there are several to tell apart. */
function accountLabel(accounts: AccountSummary[], accountId: number | null | undefined): string {
  const a = accounts.length > 1 ? accounts.find((x) => x.account_id === accountId) : undefined
  return a ? `Trading account ${accountName(a)}` : 'Trading account'
}

export function transferOptions(s: InvestorSummary): TransferOption[] {
  const wallets: WalletKind[] = ['main', 'pamm', 'social']
  const opts: TransferOption[] = wallets.map((w) => ({
    value: `wallet:${w}`, label: walletLabel(w), ref: { kind: 'wallet', wallet: w },
    available: s.wallets[w].available,
  }))
  for (const a of s.accounts) {
    opts.push({
      value: `account:${a.account_id}`, label: accountLabel(s.accounts, a.account_id),
      ref: { kind: 'account', account_id: a.account_id }, available: a.account_available,
    })
  }
  return opts
}

function refLabel(r: MoneyRef, accounts: AccountSummary[]): string {
  return r.kind === 'wallet' && r.wallet ? walletLabel(r.wallet) : accountLabel(accounts, r.account_id)
}
```

In the transfers list, replace `{`${refLabel(t.source)} → ${refLabel(t.target)}`}` with `{`${refLabel(t.source, summary?.accounts ?? [])} → ${refLabel(t.target, summary?.accounts ?? [])}`}`. Update the comment above `PAIRS` to say `"account" is any of the investor's trading accounts`.

- [x] **Step 4: Deposit**

In `dashboard/src/pages/investor/InvestorDeposit.tsx`: import `ACCOUNT_CURRENCY, BADGE_TONE, accountName, statusLabel, statusTone` from `../../lib/investor`, add `import Select from '../../components/Select'`.

Replace `const EMPTY_FORM = { amount: '', reference: '', note: '', target: 'wallet' as Target }` with:

```ts
// `account` is the picked trading account's id as a string; '' = the first.
const EMPTY_FORM = { amount: '', reference: '', note: '', target: 'wallet' as Target, account: '' }
```

Replace `const linked = summary?.link_state === 'linked' && summary.account != null` with:

```ts
  const accounts = summary?.accounts ?? []
  const linked = accounts.length > 0
  const accountId = form.account ? Number(form.account) : (accounts[0]?.account_id ?? null)
```

In the POST body replace `target_account_id: form.target === 'account' ? (summary?.account?.account_id ?? null) : null,` with `target_account_id: form.target === 'account' ? accountId : null,`.

Directly after the `Deposit to` `</fieldset>`, add:

```tsx
              {form.target === 'account' && accounts.length > 1 && (
                <label className="block">
                  <span className="desk-label block mb-1">Which trading account</span>
                  <Select aria-label="Which trading account" block value={accountId ?? ''}
                          onChange={(e) => setForm({ ...form, account: e.target.value })}>
                    {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{accountName(a)}</option>)}
                  </Select>
                </label>
              )}
```

- [x] **Step 5: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorTransfer.test.tsx src/pages/investor/InvestorDeposit.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [x] **Step 6: Commit**

```bash
git add dashboard/src/pages/investor/InvestorTransfer.tsx dashboard/src/pages/investor/InvestorTransfer.test.tsx dashboard/src/pages/investor/InvestorDeposit.tsx dashboard/src/pages/investor/InvestorDeposit.test.tsx
git commit -m "feat(dashboard): Transfer and Deposit offer each trading account when there are several

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 13: Open account — the cap state

**Files:**
- Modify: `dashboard/src/pages/investor/InvestorOpenAccount.tsx`
- Modify: `dashboard/src/pages/investor/InvestorOpenAccount.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary.kyc_status`, `InvestorSummary.account_limit` (Task 9); `POST investor/account-requests` 409 `you have reached the limit of N live accounts` (Task 5, shown inline by `PinConfirmDialog` as returned).
- Produces: the page reads `investor/summary` instead of `investor/profile`. Order: not verified -> "Verify your identity first"; else the latest request's card when it is `requested` or `fulfilled` (as today); then, unless that request is still `requested`: at the cap (`used >= max`) a NextStep titled `You have reached your account limit` with `This workspace allows <max> live accounts per investor. Ask your admin if you need another.`, otherwise the packages and the form (so a fulfilled request no longer blocks asking for another account).

- [x] **Step 1: Write the failing tests**

In `dashboard/src/pages/investor/InvestorOpenAccount.test.tsx`, change the fixture import to `import { accountRequestFixture, packageFixture, summaryFixture } from '../../test/portalFixtures'`, change `mockRoutes`' signature to `function mockRoutes(opts: { kyc?: KycStatus; requests?: AccountRequest[]; limit?: { max: number; used: number } } = {})`, and replace its `/investor/profile` route with:

```ts
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(summaryFixture({ kyc_status: opts.kyc ?? 'approved',
                                           account_limit: opts.limit ?? { max: 5, used: 0 } }))
    }
```

In `a fulfilled request shows the login and server`, rename it to `a fulfilled request shows the login and server, and another account can be requested under the cap` and append:

```ts
  expect(screen.getByRole('button', { name: 'Choose Standard' })).toBeInTheDocument()
```

Append:

```ts
test('at the cap the page explains the limit instead of the packages', async () => {
  mockRoutes({ limit: { max: 2, used: 2 },
               requests: [accountRequestFixture({ status: 'fulfilled', mt5_login: 5001, mt5_server: 'Broker-Live' })] })
  renderPage()
  expect(await screen.findByRole('heading', { name: 'You have reached your account limit' })).toBeInTheDocument()
  expect(screen.getByText('This workspace allows 2 live accounts per investor. Ask your admin if you need another.'))
    .toBeInTheDocument()
  expect(screen.getByText('5001')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Choose Standard' })).not.toBeInTheDocument()
})
```

- [x] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorOpenAccount.test.tsx`
Expected: FAIL — the page still asks `investor/profile` (the mock answers `{}`, so everyone looks unverified).

- [x] **Step 3: The page**

In `dashboard/src/pages/investor/InvestorOpenAccount.tsx`:

Change the type import to `import type { AccountPackage, AccountRequest, InvestorSummary, KycStatus } from '../../lib/types'`.

Change the component doc comment's second sentence to: `Verification comes first; then one request at a time, while the investor's accounts plus open requests stay under the workspace's cap: the packages the workspace offers, a leverage and two passwords the investor keeps, confirmed with the MPIN.`

After `const [kyc, setKyc] = useState<KycStatus | null>(null)` add:

```tsx
  const [limit, setLimit] = useState<InvestorSummary['account_limit'] | null>(null)
```

In `refresh`, replace

```tsx
      const [p, pk, rq] = await Promise.all([
        orgApi<KycProfile>(orgId, 'investor/profile'),
        orgApi<AccountPackage[]>(orgId, 'investor/account-packages'),
        orgApi<AccountRequest[]>(orgId, 'investor/account-requests'),
      ])
      setKyc(p.status); setPackages(pk); setRequests(rq)
```

with

```tsx
      const [s, pk, rq] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<AccountPackage[]>(orgId, 'investor/account-packages'),
        orgApi<AccountRequest[]>(orgId, 'investor/account-requests'),
      ])
      setKyc(s.kyc_status); setLimit(s.account_limit); setPackages(pk); setRequests(rq)
```

After `const current = …` add:

```tsx
  const atCap = limit != null && limit.used >= limit.max
```

Replace the JSX from `) : current ? (` to the `)}` that closes the `{!loaded ? (` conditional (just before `<PinConfirmDialog`) with:

```tsx
      ) : (
        <>
          {current && (
            <Card title="Your request">
              <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                <div><dt className="desk-label">Package</dt><dd className="text-ink">{current.package_name}</dd></div>
                <div><dt className="desk-label">Leverage</dt><dd className="num text-ink">{`1:${current.leverage}`}</dd></div>
                <div><dt className="desk-label">Status</dt>
                  <dd><Badge tone={requestBadge(current.status)}>{requestLabel(current.status)}</Badge></dd></div>
                <div><dt className="desk-label">Requested on</dt><dd className="num text-ink">{formatWhen(current.created_at)}</dd></div>
                {current.status === 'fulfilled' && (
                  <>
                    <div><dt className="desk-label">MT5 login</dt><dd className="num text-ink">{current.mt5_login}</dd></div>
                    <div><dt className="desk-label">Server</dt><dd className="num text-ink">{current.mt5_server}</dd></div>
                  </>
                )}
              </dl>
              {current.status === 'requested' ? (
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <p className="text-sm text-ink-soft flex-1 min-w-0">
                    An admin is opening your account at the broker. You will get an email with your login.
                  </p>
                  <Button variant="secondary" tone="loss" onClick={() => setCancelOpen(true)} disabled={busy}>
                    Cancel request
                  </Button>
                </div>
              ) : (
                <p className="mt-4 text-sm text-ink-soft">
                  Sign in to MetaTrader 5 with this login and the passwords you chose.
                </p>
              )}
            </Card>
          )}
          {current?.status === 'requested' ? null : atCap ? (
            <NextStep title="You have reached your account limit">
              {`This workspace allows ${limit?.max} live accounts per investor. Ask your admin if you need another.`}
            </NextStep>
          ) : (
            <>
              {latest?.status === 'rejected' && (
                <Banner kind="warn" announce={false}>
                  {`Your last request was rejected: ${latest.decision_note ?? 'no reason given'}`}
                </Banner>
              )}
              {packages.length === 0 ? (
                <NextStep title="No account packages yet">
                  This workspace has not published any account packages yet.
                </NextStep>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  {packages.map((p) => (
                    <Card key={p.id} title={p.name}>
                      <dl className="inset p-4 grid grid-cols-2 gap-3 text-sm">
                        <div><dt className="desk-label">Minimum deposit</dt>
                          <dd className="num text-ink">{money(p.min_deposit, p.currency)}</dd></div>
                        <div><dt className="desk-label">Spread</dt>
                          <dd className="num text-ink">{p.spread_label ?? '—'}</dd></div>
                        <div className="col-span-2"><dt className="desk-label">Leverage</dt>
                          <dd className="num text-ink">{p.leverage_options.map((l) => `1:${l}`).join(' · ')}</dd></div>
                      </dl>
                      <div className="mt-4">
                        <Button aria-label={`Choose ${p.name}`} variant={chosen?.id === p.id ? 'primary' : 'secondary'}
                                onClick={() => choose(p)}>
                          {chosen?.id === p.id ? 'Chosen' : 'Choose'}
                        </Button>
                      </div>
                    </Card>
                  ))}
                </div>
              )}

              {chosen && (
                <Card title={`Request a ${chosen.name} account`}>
                  <form onSubmit={review} noValidate className="space-y-4">
                    {formError && <Banner kind="error" onDismiss={() => setFormError(null)}>{formError}</Banner>}
                    <div>
                      <label htmlFor="open-leverage" className="desk-label block mb-1">Leverage</label>
                      <Select id="open-leverage" value={leverage} onChange={(e) => setLeverage(e.target.value)}>
                        {chosen.leverage_options.map((l) => <option key={l} value={l}>{`1:${l}`}</option>)}
                      </Select>
                    </div>
                    <PasswordField id="open-main" label="Main password" value={mainPassword} shown={shown}
                                   onChange={setMainPassword} generateLabel="Generate main password"
                                   onGenerate={() => { setMainPassword(generatePassword()); setShown(true) }} />
                    <PasswordField id="open-investor" label="Investor password" value={investorPassword} shown={shown}
                                   hint="Read-only access: for someone who should see the account but not trade."
                                   onChange={setInvestorPassword} generateLabel="Generate investor password"
                                   onGenerate={() => { setInvestorPassword(generatePassword()); setShown(true) }} />
                    <p className="text-xs text-ink-soft">
                      {`Both: ${PASSWORD_RULE}. Write them down: the admin uses them once to create your account, then MirrorFleet deletes them.`}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" variant="secondary" onClick={() => setShown(!shown)}>
                        {shown ? 'Hide passwords' : 'Show passwords'}
                      </Button>
                      <Button type="submit" disabled={busy}>Request account</Button>
                    </div>
                  </form>
                </Card>
              )}
            </>
          )}
        </>
      )}
```

(Everything inside is today's markup, moved; the only new pieces are the `current &&` wrapper and the `atCap` branch.)

- [x] **Step 4: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorOpenAccount.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [x] **Step 5: Commit**

```bash
git add dashboard/src/pages/investor/InvestorOpenAccount.tsx dashboard/src/pages/investor/InvestorOpenAccount.test.tsx
git commit -m "feat(dashboard): Open account allows another account under the cap and explains the limit at it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 14: Admin Investors — accounts column, accounts drawer, the cap field

**Files:**
- Create: `dashboard/src/pages/investors/AccountsDrawer.tsx`
- Modify: `dashboard/src/pages/Investors.tsx`
- Modify: `dashboard/src/pages/investors/PaymentMethodsTab.tsx` (the settings card)
- Modify: `dashboard/src/pages/Investors.test.tsx`

**Interfaces:**
- Consumes: `InvestorRow.accounts`, `PortalSettings.max_live_accounts` (Task 9); `accountName`, `moneyOrDash`; `POST investors/{id}/accounts`, `DELETE investors/{id}/accounts/{account_id}`, `PUT portal-settings` with `max_live_accounts` (Tasks 5-6).
- Produces: the `Accounts` column (`not linked` / the first account's name / `<name> · <n> accounts`); `Equity` = the sum of the accounts' equities, `—` while any is unknown or there is none; row menu item `Manage accounts` opens `AccountsDrawer` (dialog `<display_name>'s accounts`: one row per account with a `Unlink <name>` button, a `Select` labelled `Account to link` over non-master accounts nobody owns yet, any platform, a `Link` button; the drawer makes its own link/unlink requests and shows a refusal in its own error state, never the page banner); notices `Account linked` / `Account unlinked`. The settings card becomes `Portal settings` with a third field `Max live accounts per investor`, button `Save portal settings`, notice `Portal settings saved`. The row `Select` labelled `Account for <email>` is gone.

- [x] **Step 1: Write the failing tests**

In `dashboard/src/pages/Investors.test.tsx`:

Replace the `investor` fixture's `account_id: 1001, nickname: 'Inv',` and `equity: 6000, equity_source: 'live',` with `accounts: [{ account_id: 1001, nickname: 'Inv', equity: 6000, equity_source: 'live' }],` (keep every other field).

In `mockRoutes`, add to the options type:

```ts
  /** Overrides what `GET .../investors` returns; defaults to `[investor]`. */
  investors?: unknown[]
  /** Makes the link POST answer this instead of 201. */
  refuseLink?: { status: number; body: unknown }
```

change the default settings queue to `[{ withdrawal_min: 0, withdrawal_fee_pct: 0, max_live_accounts: 5 }]`, and replace the first two routes with:

```ts
    if (path.endsWith('/investors')) return jsonResponse(options.investors ?? [investor])
    if (path.endsWith('/investors/5/accounts') && method === 'POST') {
      if (options.refuseLink) return jsonResponse(options.refuseLink.body, options.refuseLink.status)
      return jsonResponse({ user_id: 5, ...JSON.parse(init!.body as string) }, 201)
    }
    if (/\/investors\/5\/accounts\/\d+$/.test(path) && method === 'DELETE') return new Response(null, { status: 204 })
    if (path.endsWith('/accounts')) {
      return jsonResponse([{ ctid_trader_account_id: 1001, trader_login: 1001, is_live: false,
        role: 'slave', enabled: true, multiplier: 1, status: 'ok', connection_status: 'active', platform: 'mt5' },
        { ctid_trader_account_id: 1002, trader_login: 1002, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active', platform: 'mt5' },
        { ctid_trader_account_id: 1003, trader_login: 1003, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' }])
    }
```

Delete the `if (path.endsWith('/investors/5/account')) …` route.

Replace `test('links an account from the row select', …)` with:

```ts
test("Manage accounts lists the investor's accounts, links one more and unlinks one", async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  expect(screen.queryByLabelText('Account for inv@example.com')).not.toBeInTheDocument()
  await chooseFromMenu('Ada Investor', 'Manage accounts')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's accounts" })
  expect(within(drawer).getByText('Inv')).toBeInTheDocument()
  const picker = within(drawer).getByLabelText('Account to link')
  // Any platform, never one already linked: 1001 is Ada's.
  expect(within(picker).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['1002', '1003'])
  // 1003 has no `platform` field (a cTrader account) and must not be
  // mislabelled "MT5" the way the fulfil picker labels its own options.
  expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['1002 (MT5)', '1003 (CTRADER)'])
  await userEvent.click(within(drawer).getByRole('button', { name: 'Link' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investors/5/accounts', 'POST')).toEqual({ account_id: 1002 }))
  expect(await screen.findByText('Account linked')).toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Unlink Inv' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).endsWith('/investors/5/accounts/1001') && (init as RequestInit)?.method === 'DELETE')).toBe(true))
  expect(await screen.findByText('Account unlinked')).toBeInTheDocument()
})

test("the server's refusal to link is shown inside the accounts drawer, not the page banner", async () => {
  mockRoutes({ refuseLink: { status: 409, body: { detail: 'you have reached the limit of 5 live accounts' } } })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Manage accounts')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's accounts" })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Link' }))
  expect(await within(drawer).findByRole('alert')).toHaveTextContent('you have reached the limit of 5 live accounts')
  expect(screen.queryByText('Account linked')).not.toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: "Ada Investor's accounts" })).toBeInTheDocument()
})

test('the accounts column names the first account and counts the rest; equity is their total', async () => {
  mockRoutes({ investors: [{ ...investor, accounts: [
    { account_id: 1001, nickname: 'Inv', equity: 6000, equity_source: 'live' },
    { account_id: 1002, nickname: null, equity: 50, equity_source: 'last known' }] }] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(await screen.findByText('Inv · 2 accounts')).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'Accounts' })).toBeInTheDocument()
  expect(screen.getByText('6,050.00')).toBeInTheDocument()
})
```

In `saves the withdrawal settings`, rename it to `saves the portal settings` and replace its body from `const save = …` on with:

```ts
  const save = screen.getByRole('button', { name: 'Save portal settings' })
  expect(save).toBeDisabled()
  await userEvent.clear(screen.getByLabelText('Minimum withdrawal'))
  await userEvent.type(screen.getByLabelText('Minimum withdrawal'), '25')
  await userEvent.clear(screen.getByLabelText('Withdrawal fee %'))
  await userEvent.type(screen.getByLabelText('Withdrawal fee %'), '1.5')
  await userEvent.clear(screen.getByLabelText('Max live accounts per investor'))
  await userEvent.type(screen.getByLabelText('Max live accounts per investor'), '3')
  expect(save).toBeEnabled()
  await userEvent.click(save)
  await waitFor(() => expect(bodyOf(fetchMock, '/portal-settings', 'PUT'))
    .toEqual({ withdrawal_min: '25', withdrawal_fee_pct: '1.5', max_live_accounts: 3 }))
  expect(await screen.findByText('Portal settings saved')).toBeInTheDocument()
```

In the two poll tests (`the withdrawal settings follow the server while the form is untouched`, `a touched settings form survives a poll tick with different server values`), add `max_live_accounts: 5` to each settings object in their `settings: [...]` lists, and in the first of them append `expect(screen.getByLabelText('Max live accounts per investor')).toHaveValue('5')`.

In `a viewer sees the figures but no actions`, replace `'Save withdrawal settings'` with `'Save portal settings'`.

- [x] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/Investors.test.tsx`
Expected: FAIL — no `Manage accounts` menu item, no `Accounts` column, no `Save portal settings` button.

- [x] **Step 3: The drawer**

Create `dashboard/src/pages/investors/AccountsDrawer.tsx`:

```tsx
import { useState } from 'react'
import { orgApi } from '../../lib/api'
import { errorText } from '../../lib/format'
import { accountName, moneyOrDash } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Select from '../../components/Select'
import type { Account, InvestorRow } from '../../lib/types'
import type { Runner } from './PaymentMethodsTab'

/**
 * One investor's live accounts: Unlink each, or Link one more from the
 * accounts nobody owns yet (any platform; only fulfil insists on MT5). The
 * link/unlink requests run here, not through `run()`, so a refusal shows
 * inside this drawer and never reaches the page banner; `run()` is called
 * only once a request has already succeeded, to refresh the list and show
 * the notice.
 */
export default function AccountsDrawer({ investor, linkable, orgId, run, onClose }: {
  investor: InvestorRow | null
  linkable: Account[]
  orgId: number
  run: Runner
  onClose: () => void
}) {
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const chosen = linkable.some((a) => String(a.ctid_trader_account_id) === pick)
    ? pick : (linkable[0] ? String(linkable[0].ctid_trader_account_id) : '')

  const link = async () => {
    if (!investor || !chosen) return
    setError(null); setBusy(true)
    try {
      await orgApi(orgId, `investors/${investor.user_id}/accounts`, {
        method: 'POST', body: JSON.stringify({ account_id: Number(chosen) }) })
      await run(async () => {}, 'Account linked')
    } catch (err) {
      setError(errorText(err, 'Could not link the account'))
    } finally {
      setBusy(false)
    }
  }

  const unlink = async (accountId: number) => {
    if (!investor) return
    setError(null); setBusy(true)
    try {
      await orgApi(orgId, `investors/${investor.user_id}/accounts/${accountId}`, { method: 'DELETE' })
      await run(async () => {}, 'Account unlinked')
    } catch (err) {
      setError(errorText(err, 'Could not unlink the account'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer open={investor != null} busy={busy} onClose={onClose}
            title={investor ? `${investor.display_name}'s accounts` : ''}>
      {investor && (
        <div className="space-y-4">
          {error && <Banner kind="error">{error}</Banner>}
          <ul className="inset divide-y divide-line text-sm">
            {investor.accounts.length === 0 && (
              <li className="text-center py-6 text-ink-faint">No accounts linked yet</li>
            )}
            {investor.accounts.map((a) => (
              <li key={a.account_id} className="px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="text-ink flex-1 min-w-0">{accountName(a)}</span>
                <span className="num">{moneyOrDash(a.equity)}</span>
                <Button variant="ghost" tone="loss" size="sm" aria-label={`Unlink ${accountName(a)}`}
                        disabled={busy} onClick={() => void unlink(a.account_id)}>
                  Unlink
                </Button>
              </li>
            ))}
          </ul>
          {linkable.length === 0 ? (
            <p className="text-xs text-ink-soft">Every account is already linked; add one under Accounts first.</p>
          ) : (
            <div className="flex flex-wrap items-end gap-3">
              <label className="block">
                <span className="desk-label block mb-1">Account to link</span>
                <Select aria-label="Account to link" value={chosen} onChange={(e) => setPick(e.target.value)}>
                  {linkable.map((a) => (
                    <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                      {`${a.nickname ?? a.trader_login} (${(a.platform ?? 'ctrader').toUpperCase()})`}
                    </option>
                  ))}
                </Select>
              </label>
              <Button disabled={busy || !chosen} onClick={() => void link()}>Link</Button>
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}
```

- [x] **Step 4: The Investors page**

In `dashboard/src/pages/Investors.tsx`:

- Imports: drop `import Select from '../components/Select'` and `import { orgApi } from '../lib/api'` (the drawer now makes its own requests); change `import { moneyOrDash, walletLabel } from '../lib/investor'` to `import { accountName, moneyOrDash, walletLabel } from '../lib/investor'`; add `import AccountsDrawer from './investors/AccountsDrawer'`.
- Below `OpenRequests`, add:

```tsx
/** "not linked", "Growth", or "Growth · 2 accounts". */
function accountsText(r: InvestorRow): string {
  const [first] = r.accounts
  if (!first) return 'not linked'
  return r.accounts.length === 1 ? accountName(first) : `${accountName(first)} · ${r.accounts.length} accounts`
}

/** The accounts' equity together; null while any is unknown, or there is none. */
function totalEquity(r: InvestorRow): number | null {
  if (r.accounts.length === 0 || r.accounts.some((a) => a.equity == null)) return null
  return r.accounts.reduce((sum, a) => sum + (a.equity ?? 0), 0)
}
```

- After `const [adjustFor, setAdjustFor] = …` add `const [accountsFor, setAccountsFor] = useState<number | null>(null)`.
- Delete the old `linkAccount` function (the PUT-based single link; `AccountsDrawer` now makes its own link/unlink requests). Replace `linkedIds` and `unlinked` with:

```tsx
  // What may be linked: any platform, never the master, nobody's yet (the
  // api refuses the rest anyway).
  const linkedIds = new Set(rows.flatMap((r) => r.accounts.map((a) => a.account_id)))
  const linkable = accounts.filter((a) =>
    a.role !== 'master' && !linkedIds.has(a.ctid_trader_account_id))
```

- Header: replace `<th className="desk-label px-5 py-2 font-semibold">Linked account</th>` with `<th className="desk-label px-5 py-2 font-semibold">Accounts</th>`.
- Replace `<td data-label="Equity" className="num px-5 py-2.5 text-right">{moneyOrDash(r.equity)}</td>` with `<td data-label="Equity" className="num px-5 py-2.5 text-right">{moneyOrDash(totalEquity(r))}</td>`.
- Replace the whole `<td data-label="Linked account" …> … </td>` cell with:

```tsx
                        <td data-label="Accounts" className="px-5 py-2.5">{accountsText(r)}</td>
```

- In the row `Menu` items, add first: `{ key: 'accounts', label: 'Manage accounts', onSelect: () => setAccountsFor(r.user_id) },`.
- Directly before `{/* Keyed on the investor so each opening starts with a clean filter. */}`, add:

```tsx
      <AccountsDrawer key={accountsFor ?? 'none'}
                      investor={rows.find((r) => r.user_id === accountsFor) ?? null}
                      linkable={linkable} orgId={orgId} run={run}
                      onClose={() => setAccountsFor(null)} />
```

- [x] **Step 5: The cap field**

In `dashboard/src/pages/investors/PaymentMethodsTab.tsx`:

- Replace `const [rules, setRules] = useState({ withdrawal_min: '0', withdrawal_fee_pct: '0' })` with `const [rules, setRules] = useState({ withdrawal_min: '0', withdrawal_fee_pct: '0', max_live_accounts: '5' })`.
- In the settings `useEffect` and in `saveRules`' `setRules(...)`, add `max_live_accounts: String(settings.max_live_accounts),` / `max_live_accounts: String(saved.max_live_accounts),` beside the two withdrawal keys.
- In `saveRules`, replace `method: 'PUT', body: JSON.stringify(rules) })` with:

```tsx
        // Digits go as a number; anything else goes as typed so the api's
        // "max_live_accounts must be a whole number from 1 to 50" shows.
        method: 'PUT', body: JSON.stringify({ ...rules, max_live_accounts:
          /^\d+$/.test(rules.max_live_accounts.trim()) ? Number(rules.max_live_accounts.trim())
            : rules.max_live_accounts }) })
```

  and replace `'Withdrawal settings saved'` with `'Portal settings saved'`.
- In the card: `title="Withdrawal settings"` becomes `title="Portal settings"`; the paragraph becomes `Applied to every withdrawal request: the smallest amount an investor may ask for and the fee the workspace keeps (both default to 0). The cap counts each investor's live accounts plus an open account request (default 5).`; after the `Withdrawal fee %` label add:

```tsx
            <label className="block w-40">
              <span className="desk-label block mb-1">Max live accounts per investor</span>
              <Input aria-label="Max live accounts per investor" num inputMode="numeric" disabled={!control}
                     value={rules.max_live_accounts}
                     onChange={(e) => editRules({ max_live_accounts: e.target.value })} />
            </label>
```

  and the submit button text `Save withdrawal settings` becomes `Save portal settings`. Update the `// Withdrawal settings: …` comment's first words to `// Portal settings: …`.

- [x] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/Investors.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors (`noUnusedLocals` catches a leftover `Select` or `orgApi` import).

- [x] **Step 7: Commit**

```bash
git add dashboard/src/pages/investors/AccountsDrawer.tsx dashboard/src/pages/Investors.tsx dashboard/src/pages/investors/PaymentMethodsTab.tsx dashboard/src/pages/Investors.test.tsx
git commit -m "feat(dashboard): admins link and unlink investor accounts in a drawer and set the live-account cap

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

### Task 15: Transitional types removed; gates and docs

**Files:**
- Modify: `dashboard/src/lib/types.ts`, `dashboard/src/test/portalFixtures.ts`, `dashboard/src/test/portalFixtures.test.ts`
- Modify: `README.md` (the "Upgrading with a migration" section)
- Modify: `docs/superpowers/specs/2026-10-05-client-portal-phase-3-multi-account-design.md` (status line)
- Modify: `docs/superpowers/plans/2026-10-05-client-portal-phase-3.md` (tick the checkboxes)

**Interfaces:**
- Consumes: everything above.
- Produces: `InvestorSummary` without `link_state`, `account`, `account_available` (and `equity_source: EquitySource`); `InvestorRow` without `account_id`, `nickname`, `equity`, `equity_source`; a green branch and the deploy notes.

- [x] **Step 1: Remove the transitional fields**

In `dashboard/src/lib/types.ts`, delete from `InvestorSummary` the lines `link_state: 'linked' | 'unlinked'`, the four-line `account: { … } | null`, `account_available: number | null` and the `// Transitional …` comment, and change its `equity_source: 'live' | 'last known' | 'unknown'` to `equity_source: EquitySource`. Delete from `InvestorRow` the lines `account_id: number | null`, `nickname: string | null`, `equity: number | null`, `equity_source: string` and its `// Transitional …` comment.

In `dashboard/src/test/portalFixtures.ts`, delete from `summaryFixture` the `link_state: 'linked',` line, the three-line `account: { … },` and `account_available: 1240.25,`; delete from `investorRowFixture` `account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live',`.

In `dashboard/src/test/portalFixtures.test.ts`, delete the line `expect(summaryFixture({ link_state: 'unlinked', account: null }).account).toBeNull()`.

Run: `grep -rnE "link_state|summary\??\.account\b|summary\??\.account_available" dashboard/src`
Expected: no output (the type check in Step 2 proves the `InvestorRow` half).

- [x] **Step 2: Full dashboard gate**

From `dashboard/`:

Run: `npm test` (or the worker-capped form from the Global Constraints)
Expected: palette prover passes, no type errors, every test passes, and `npx vitest run 2>&1 | grep -c "not wrapped in act"` prints `0`.

Run: `npm run build`
Expected: build succeeds.

- [x] **Step 3: Full API suite**

From `api/` with the env of the Global Constraints:

Run: `"$PY" -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 known `test_events_ws.py` errors and the 1 EA-download CRLF failure.

- [x] **Step 4: `copier/` untouched**

Run: `git diff --stat main..HEAD -- copier/`
Expected: prints nothing.

- [x] **Step 5: README runbook**

In `README.md`, directly after the paragraph that starts "Migration 023 (client portal phase 2", add:

```markdown
Migration 024 (client portal phase 3: several live accounts per investor)
drops the one-account-per-investor index and adds
`portal_settings.max_live_accounts` (default 5); it rewrites no row, so the
same sequence applies and `migrate` prints `applied: ['024_multi_account.sql']`.
Admins now link and unlink accounts one at a time under **Investors → Actions →
Manage accounts** (the row's account select is gone; any non-master account
nobody owns can be linked, MT5 or cTrader; fulfilling a request still links
MT5 only) and set the cap under **Investors → Payment methods → Portal settings**.
An investor with several accounts picks one on Account, History, Transfer and
Deposit. Deploy the api and the dashboard together: the old dashboard reads
`account` from the investor summary, which no longer exists.
```

- [x] **Step 6: Spec status and §7 wording**

In the spec, replace `**Status:** approved design; plan to follow` with
`**Status:** implemented on branch client-portal-phase-3 (plan docs/superpowers/plans/2026-10-05-client-portal-phase-3.md); awaiting deploy`.

Also in section 7 (the admin Investors bullet), replace `picker of unlinked MT5
accounts` with `picker of unlinked non-master accounts, any platform` — section
5 already says "any platform as today"; this was the one line left saying
MT5-only for admin linking.

- [x] **Step 7: Tick this plan's checkboxes, then commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/test/portalFixtures.ts dashboard/src/test/portalFixtures.test.ts README.md docs/superpowers/specs/2026-10-05-client-portal-phase-3-multi-account-design.md docs/superpowers/plans/2026-10-05-client-portal-phase-3.md
git commit -m "docs: client portal phase 3 -- upgrade runbook for migration 024, spec status, plan ticked

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN"
```

---

## Spec decisions this plan makes (read before Task 1)

Where the spec was silent or ambiguous:

- **Deposit with several accounts and no `target_account_id`**: 400 `target_account_id is required` (parallel to the read routes' `account_id is required`). One account may stay implicit, as the spec says.
- **A named id when the investor owns none**: the read routes answer 404 `Account not found` (the named id is checked first), not 409; omitting it still gives 409 `no account linked yet`.
- **Transfers without an account id**: unchanged 400 `that transfer is not allowed` from `transfer_pair` (it already refuses an account end with no id) — never reaches the ownership check.
- **Cap check order on a request**: KYC gate, then one open request, then the cap — the more specific refusal wins.
- **`PUT portal-settings`**: `max_live_accounts` is optional (omitted keeps the current value) so the existing body stays valid; the refusal is `max_live_accounts must be a whole number from 1 to 50`; booleans, strings and fractions are refused.
- **Where `_link_account` lives**: moved to `portal_common.link_account` (with the cap) because `routes/portal_identity.py` already imports `routes/portal_admin.py` (`parse_min`), so the admin router importing back would be circular.
- **`mt5_login` when two fulfilled requests name the same account**: the most recently decided one wins.
- **Summary `equity_source` with no account**: `unknown` (as today); `net_funded` 0, `profit` null.
- **Unlink audit**: `investor_account_unlinked` (info) with `account_id` in the events column, like the link.
- **Index name**: `accounts_by_investor`.
- **Migration test**: no upgrade-database test. 024 is `DROP INDEX` + `CREATE INDEX` + `ADD COLUMN … DEFAULT`, which rewrite no row; the post-migration tests cover the shape and the two-accounts case. `test_migration_019`'s uniqueness test is retired and 022's column list gains the new column.
- **Account names in the dashboard**: `accountName` — `MT5 <login>` when the summary has one, else the nickname, else `Account <id>`. Transfer labels read `Trading account` with one account and `Trading account <name>` with several.
- **The History page**: the spec puts "history" on the Account page, but closed trades live on the separate History page (`InvestorHistory.tsx`); it gets the same `AccountSwitcher` and `?account=`, and loads the summary for the list.
- **The Account page's login card**: now from `AccountSummary.mt5_login/mt5_server` (spec 6); the `Package · 1:leverage` line and the `investor/account-requests` fetch are dropped (the Open account page still shows the package).
- **Open account after a fulfilled request**: the fulfilled request's card stays on top (it is the only place an unlinked login shows besides the email), and the packages come back below it while under the cap; the page reads `kyc_status` and `account_limit` from `investor/summary` instead of `investor/profile`.
- **Admin drawer**: there was no investor drawer to extend (only the ledger drawer), so `AccountsDrawer` is new, opened by a `Manage accounts` row action; the row's account `Select` is removed. The equity column shows the accounts' total, `—` while any is unknown.
- **Where the cap is edited**: the existing withdrawal settings card on the Payment methods tab becomes `Portal settings` with a third field, since `GET/PUT portal-settings` is the one settings call.
- **Transitional dashboard types**: Task 9 adds the new fields beside the old ones so every task compiles; Task 15 deletes the old ones and `tsc` proves no page reads them.
- **Cap race**: `link_account` counts then updates without a lock (a `ponytail:` comment says so); two admins linking the same investor at the same instant could pass the cap by one.

Where the spec is wrong against the code:

- Spec 7's admin Investors bullet says the Link picker offers "unlinked MT5 accounts"; the removed `PUT investors/{id}/account` never checked the platform (only fulfil did), and neither does the new `POST investors/{id}/accounts`. Owner-side ruling (2026-10-05): keep today's behaviour -- it accepts any platform; only fulfil passes `mt5_only=True`. Fixed in Task 15 Step 6.
- Spec 7 says the Account page loads "history"; it has positions and analytics only — history is the History page (above).
- Spec 7 speaks of "the investor drawer"; none existed (above).
- Not in the spec but required by the code: `test_migration_019.py` asserts the unique index 024 drops, `test_migration_022.py` pins `portal_settings`' exact column list, and `test_portal_methods.py` pins the exact settings JSON; all three are updated in Tasks 1 and 5.
