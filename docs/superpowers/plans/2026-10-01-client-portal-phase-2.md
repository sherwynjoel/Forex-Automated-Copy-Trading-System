# Client Portal Phase 2 (Verification, Profile, Security, Account Requests) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Investors complete a profile and identity verification that an admin reviews; verified investors request a live MT5 account from admin-defined packages and an admin hands the login over; everyone sees their sign-in history, and a password change needs the MPIN.

**Architecture:** Migration 023 adds `kyc_profiles`, `account_packages`, `account_requests` and `login_events`. A rules module (`api/src/api/portal_identity.py`) owns field validation, re-verification, the MT5 password policy, Fernet sealing and the serialisers; one new router (`routes/portal_identity.py`) carries every KYC, package and account-request route, investor and admin side; `auth.py` writes and reads sign-in history. The dashboard gains three investor pages (Profile & verification, Open account, Security), two Requests desk tabs, an Investors packages tab and KYC column, a dashboard verification card and a regrouped investor nav.

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 (autocommit) / cryptography Fernet / pytest against real Postgres 16; React 18 / TypeScript strict / react-router 7 / Tailwind 4 tokens / vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-01-client-portal-phase-2-identity-design.md` (read it first; it is the authority). **Interfaces:** `docs/superpowers/plans/2026-10-01-client-portal-phase-2-interfaces.md` (every name, route, refusal string and aria-label; binding). **Code facts:** `docs/reference/mirrorfleet-subsystem-maps.md`; phase 1's `docs/superpowers/plans/2026-09-29-client-portal-phase-1-interfaces.md`. **Spec decisions:** the last section of this plan lists every place the spec was silent or ambiguous and what this plan does there; read it before Task 1.

## Global Constraints

- Branch `client-portal-phase-2`, created from `portal-followups` at or after `f9cb004` (that commit adds `rbac.require_investor`, which every investor route here uses). Work in a worktree (superpowers:using-git-worktrees): another agent uses the main checkout. Commit on the branch after every task; create no other branches.
- Every commit message ends with these two lines:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM
  ```

  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`.
- `copier/` is never touched.
- API tests need Docker Desktop running and Postgres up. Once per shell, Git Bash from `api/` (password from the repo-root `.env`, key `POSTGRES_PASSWORD`):

  ```bash
  docker compose -f ../docker-compose.yml up -d postgres
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test_p2"
  export PYTHONPATH="$(pwd -W)/src"
  ```

  then `.venv/Scripts/python -m pytest <files> -q -p no:cacheprovider`. The `_p2` database name keeps this run from dropping another agent's scratch database (conftest drops and recreates the database the DSN names). Use `127.0.0.1`, never `localhost`. Seven `test_events_ws.py` errors and one EA-download CRLF failure are pre-existing on Windows; do not fix them.
- Dashboard gate, from `dashboard/`: `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1` (this is `npm test` with the worker cap this machine needs) and `npm run build`. There is no lint script. One file: `npx vitest run <path>`. The vitest output must contain no `act(...)` warning.
- MPIN step-up: a route that takes `mpin` calls `require_mpin(conn, user_id, body.mpin)` before any other check and returns its Response when it is not None. The dashboard calls those routes with `{ redirectOn401: false }`.
- Investor routes take `Depends(require_investor)`; admin routes `Depends(require_org_role("admin"))`. Every new org route gets a row in `api/tests/test_rbac_matrix.py` (Task 10).
- Every mutation audits one `events` row through `portal_common.audit_control`; investor-scoped actions start with `investor_` and carry `payload.user_id` = the investor. Audit payloads carry field names and ids, never profile values or passwords. Investors are emailed on every decision through `portal_common.notify_investor`.
- Every new table is in the `db` fixture TRUNCATE list in `api/tests/conftest.py`.
- Dashboard rules: colour only through `--color-*` tokens and the primitives (Button, Input, Select, Badge, Banner, Card, Tabs, Drawer, ConfirmDialog, PageHeader, Loading, PinInput, FileInput, PinConfirmDialog, NextStep); data never sits directly on `.glass`; tables are `stack-table` with `data-label` on every data `td`; the one `h1` comes from `PageHeader`; the words "Slave"/"slave" never appear in copy; never a literal "Loading..."; use only class names that already appear in `src/` (so `scripts/palette_check.mjs` needs no new row); `tsc` is strict with `noUnusedLocals`. Tests use `mockUseOrg` from `src/test/orgMock.tsx`, fixtures from `src/test/portalFixtures.ts`, stub `fetch` by URL tail; a test of a page that renders `Money` ends its `afterEach` with `cleanup()` before `setHidden(false)`.
- Deploy is not part of this plan; Task 17 writes the runbook lines.

---

## File map

| File | Change |
|---|---|
| `db/migrations/023_portal_identity.sql` (new) | four tables |
| `api/tests/test_migration_023.py` (new), `api/tests/conftest.py`, `api/tests/portal_helpers.py` | shape; TRUNCATE list; `add_package`, `kyc_profile`, `open_account_request` |
| `api/src/api/uploads.py`, `api/src/api/routes/portal_files.py`, `api/tests/test_uploads.py` | KYC purposes; one file per KYC slot |
| `api/src/api/auth.py`, `api/src/api/routes/mpin.py`, `api/src/api/routes/portal_investor.py`, `api/tests/test_login_events.py` (new), `api/tests/test_auth.py` | sign-in history; password change needs the MPIN |
| `api/src/api/portal_identity.py` (new), `api/tests/test_portal_identity_rules.py` (new) | identity rules and serialisers |
| `api/src/api/routes/portal_identity.py` (new), `api/src/api/main.py` | the identity router, grown over Tasks 5–9 |
| `api/tests/test_portal_kyc.py`, `test_portal_packages.py`, `test_portal_account_requests.py` (new) | behaviour per task |
| `api/src/api/routes/portal_admin.py`, `api/tests/test_portal_summary.py` | `kyc_status` on investors; requests summary counts |
| `api/src/api/alerts.py`, `api/src/api/telegram.py` | three warning actions |
| `api/tests/test_rbac_matrix.py` | rows for every new route |
| `dashboard/src/lib/types.ts`, `lib/identity.ts` (new), `test/portalFixtures.ts` | types, vocabulary, fixtures |
| `dashboard/src/components/SignInHistory.tsx` (new), `components/AccountSecurity.tsx` | sign-ins card; password needs the MPIN |
| `dashboard/src/pages/investor/InvestorSecurity.tsx`, `InvestorProfile.tsx`, `InvestorOpenAccount.tsx` (new, + tests) | investor pages |
| `dashboard/src/pages/investor/InvestorAccount.tsx`, `InvestorDashboard.tsx`, `pages/Members.tsx` (+ tests) | login card, verification card, sign-ins |
| `dashboard/src/pages/Requests.tsx`, `pages/requests/RequestTabs.tsx`, `RequestDetailsDrawer.tsx`, `VerificationTab.tsx`, `AccountRequestsTab.tsx` (new, + tests) | desk tabs |
| `dashboard/src/pages/Investors.tsx`, `pages/investors/PackagesTab.tsx` (new, + tests) | packages tab, KYC column |
| `dashboard/src/components/layout/nav.ts`, `pages/groups/investor.ts`, `App.tsx` (+ nav tests) | routes and nav |
| `README.md`, spec status line | runbook, status |

## Tasks

| # | Task |
|---|---|
| 1 | Migration 023, migration test, conftest and helpers |
| 2 | Uploads accept the KYC purposes |
| 3 | Sign-in history and the password change's MPIN |
| 4 | `portal_identity.py` rules and serialisers |
| 5 | KYC, investor side (router created) |
| 6 | KYC, admin side; `kyc_status` on summary and investors |
| 7 | Account packages |
| 8 | Account requests, investor side |
| 9 | Account requests, admin side; requests summary; alert rules |
| 10 | RBAC matrix rows and the full API suite |
| 11 | Dashboard foundation |
| 12 | Security page, MPIN on password change, desk sign-ins |
| 13 | Profile & verification page |
| 14 | Open account page and the Account page login card |
| 15 | Requests desk: Verification and Account requests tabs |
| 16 | Investors packages tab and KYC column, dashboard verification card, nav |
| 17 | Gates and docs |

---

### Task 1: Migration 023 — the identity tables, their test, conftest and helpers

**Files:**
- Create: `db/migrations/023_portal_identity.sql`
- Create: `api/tests/test_migration_023.py`
- Modify: `api/tests/conftest.py` (the `db` fixture's TRUNCATE statement)
- Modify: `api/tests/portal_helpers.py` (append helpers)

**Interfaces:**
- Consumes: `db/migrate.py` `apply_migrations` (conftest runs it); conftest `db`, `make_user`, `make_org`; `portal_helpers.seed_file`.
- Produces: tables `kyc_profiles`, `account_packages`, `account_requests`, `login_events` exactly as the interfaces doc's Database section; `portal_helpers.COMPLETE_PROFILE`, `add_package`, `kyc_profile`, `open_account_request`.

- [ ] **Step 1: Write the failing migration test**

Create `api/tests/test_migration_023.py`:

```python
# api/tests/test_migration_023.py
"""Migration 023: the client portal's identity tables -- KYC profiles,
account packages, account requests and the sign-in history. conftest
applies every migration, so these tests assert the post-migration shape and
the rules the database itself enforces."""
import psycopg
import pytest

from portal_helpers import add_package, seed_file

COLUMNS = {
    "kyc_profiles": [
        "org_id", "user_id", "full_name", "gender", "date_of_birth", "phone", "address_line",
        "area", "landmark", "city", "state", "postal_code", "country_residence",
        "country_citizenship", "id_type", "id_number", "id_front_file_id", "id_back_file_id",
        "address_proof_file_id", "photo_file_id", "status", "submitted_at", "decided_by",
        "decided_at", "decision_note", "updated_at"],
    "account_packages": [
        "id", "org_id", "name", "min_deposit", "currency", "spread_label", "leverage_options",
        "enabled", "sort_order", "created_at", "updated_at"],
    "account_requests": [
        "id", "org_id", "user_id", "package_id", "package_name", "leverage", "main_password_enc",
        "investor_password_enc", "status", "mt5_login", "mt5_server", "account_id", "decided_by",
        "decided_at", "decision_note", "created_at"],
    "login_events": ["id", "user_id", "ip", "user_agent", "outcome", "created_at"],
}
INDEXES = ["kyc_profiles_queue", "account_packages_by_org", "account_requests_queue",
           "account_requests_one_open", "login_events_by_user"]


def _people(make_user, make_org):
    admin = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(admin, "admin"), (investor, "investor")])
    return org_id, admin, investor


def _request(conn, org_id, user_id, package_id, **cols):
    names = ["org_id", "user_id", "package_id", "package_name", "leverage", *cols]
    values = [org_id, user_id, package_id, "Standard", 100, *cols.values()]
    (req_id,) = conn.execute(
        f"INSERT INTO account_requests ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(values))}) RETURNING id", values).fetchone()
    return req_id


def test_migration_023_is_recorded_right_after_022(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "023_portal_identity.sql" in names
    assert names.index("023_portal_identity.sql") == names.index("022_client_wallets.sql") + 1


@pytest.mark.parametrize("table", list(COLUMNS))
def test_each_table_has_exactly_the_spec_columns_in_order(db, table):
    with psycopg.connect(db, autocommit=True) as conn:
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,)).fetchall()]
    assert cols == COLUMNS[table]


def test_the_named_indexes_exist(db):
    with psycopg.connect(db, autocommit=True) as conn:
        defs = dict(conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(%s)",
            (INDEXES,)).fetchall())
    assert sorted(defs) == sorted(INDEXES)
    one_open = defs["account_requests_one_open"]
    assert "UNIQUE" in one_open and "(org_id, user_id)" in one_open and "'requested'" in one_open
    assert "(org_id, status, submitted_at)" in defs["kyc_profiles_queue"]
    assert "(org_id, status, created_at)" in defs["account_requests_queue"]
    assert "(org_id, sort_order, id)" in defs["account_packages_by_org"]
    assert "(user_id, created_at DESC)" in defs["login_events_by_user"]


def test_a_profile_starts_as_a_draft_and_checks_its_choices(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO kyc_profiles (org_id, user_id) VALUES (%s, %s)",
                     (org_id, investor["id"]))
        (status,) = conn.execute("SELECT status FROM kyc_profiles WHERE user_id = %s",
                                 (investor["id"],)).fetchone()
        assert status == "draft"
        for column, bad in (("gender", "unknown"), ("id_type", "visa"), ("status", "pending"),
                            ("country_residence", "india"), ("country_citizenship", "in")):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(f"UPDATE kyc_profiles SET {column} = %s WHERE user_id = %s",
                             (bad, investor["id"]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO kyc_profiles (org_id, user_id) VALUES (%s, %s)",
                         (org_id, investor["id"]))


def test_a_deleted_file_empties_its_kyc_slot(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    file_id = seed_file(db, org_id, investor["id"], purpose="kyc_photo")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO kyc_profiles (org_id, user_id, photo_file_id) VALUES (%s, %s, %s)",
                     (org_id, investor["id"], file_id))
        conn.execute("DELETE FROM files WHERE id = %s", (file_id,))
        (photo,) = conn.execute("SELECT photo_file_id FROM kyc_profiles WHERE user_id = %s",
                                (investor["id"],)).fetchone()
    assert photo is None


def test_one_open_account_request_per_investor(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        first = _request(conn, org_id, investor["id"], package_id)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _request(conn, org_id, investor["id"], package_id)
        conn.execute("UPDATE account_requests SET status = 'cancelled' WHERE id = %s", (first,))
        _request(conn, org_id, investor["id"], package_id)


def test_passwords_live_only_while_a_request_is_open(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        req_id = _request(conn, org_id, investor["id"], package_id,
                          main_password_enc="sealed-main", investor_password_enc="sealed-inv")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE account_requests SET status = 'rejected' WHERE id = %s", (req_id,))
        conn.execute("UPDATE account_requests SET status = 'rejected', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (req_id,))


def test_a_fulfilled_request_carries_its_login_and_server(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        req_id = _request(conn, org_id, investor["id"], package_id)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE account_requests SET status = 'fulfilled' WHERE id = %s", (req_id,))
        conn.execute("UPDATE account_requests SET status = 'fulfilled', mt5_login = 5001, "
                     "mt5_server = 'Broker-Live' WHERE id = %s", (req_id,))


def test_packages_need_a_leverage_and_a_non_negative_minimum(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO account_packages (org_id, name, leverage_options) "
                         "VALUES (%s, 'Empty', '{}')", (org_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO account_packages (org_id, name, min_deposit, leverage_options) "
                         "VALUES (%s, 'Negative', -1, '{100}')", (org_id,))
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        req_id = _request(conn, org_id, investor["id"], package_id)
        conn.execute("UPDATE account_requests SET status = 'cancelled' WHERE id = %s", (req_id,))
        conn.execute("DELETE FROM account_packages WHERE id = %s", (package_id,))
        row = conn.execute("SELECT package_id, package_name FROM account_requests WHERE id = %s",
                           (req_id,)).fetchone()
    assert row == (None, "Standard")


def test_login_events_check_the_outcome(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO login_events (user_id, ip, user_agent, outcome) "
                     "VALUES (%s, '10.0.0.1', 'ua', 'password_ok')", (user["id"],))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO login_events (user_id, ip, outcome) "
                         "VALUES (%s, '10.0.0.1', 'locked')", (user["id"],))
```

- [ ] **Step 2: Add the helpers the test imports**

Append to `api/tests/portal_helpers.py`:

```python
# ------------------------------------------------------------ phase 2

COMPLETE_PROFILE = {
    "full_name": "Investor One", "gender": "male", "date_of_birth": "1990-04-02",
    "phone": "+91 98765 43210", "address_line": "12 Lake Road", "city": "Coimbatore",
    "state": "Tamil Nadu", "postal_code": "641001", "country_residence": "IN",
    "country_citizenship": "IN", "id_type": "passport", "id_number": "P1234567"}


def add_package(db, org_id, *, name="Standard", min_deposit="100", leverage=(100, 200, 500),
                spread_label="20-25", enabled=True, sort_order=0) -> int:
    """An account package the org offers."""
    with psycopg.connect(db, autocommit=True) as conn:
        (package_id,) = conn.execute(
            "INSERT INTO account_packages (org_id, name, min_deposit, spread_label, "
            "leverage_options, enabled, sort_order) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (org_id, name, Decimal(str(min_deposit)), spread_label, list(leverage), enabled,
             sort_order)).fetchone()
    return int(package_id)


def kyc_profile(db, org_id, user_id, *, status="approved", **over) -> dict:
    """A complete profile with its four documents seeded as files rows.
    Returns the column values written (file ids included)."""
    files = {
        "id_front_file_id": seed_file(db, org_id, user_id, purpose="kyc_document"),
        "id_back_file_id": seed_file(db, org_id, user_id, purpose="kyc_document"),
        "address_proof_file_id": seed_file(db, org_id, user_id, purpose="kyc_document"),
        "photo_file_id": seed_file(db, org_id, user_id, purpose="kyc_photo"),
    }
    row = {**COMPLETE_PROFILE, **files, **over}
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            f"INSERT INTO kyc_profiles (org_id, user_id, status, submitted_at, {cols}) "
            f"VALUES (%s, %s, %s, CASE WHEN %s::text = 'draft' THEN NULL ELSE now() END, {marks})",
            (org_id, user_id, status, status, *row.values()))
    return row


def open_account_request(db, org_id, user_id, package_id, *, main="Main1234",
                         investor="Inv12345", leverage=100, package_name="Standard") -> int:
    """A 'requested' account request with both passwords sealed under the
    app's Fernet key (os.environ['FERNET_KEY'], which app_client sets)."""
    import os
    from cryptography.fernet import Fernet
    cipher = Fernet(os.environ["FERNET_KEY"].encode())
    with psycopg.connect(db, autocommit=True) as conn:
        (req_id,) = conn.execute(
            "INSERT INTO account_requests (org_id, user_id, package_id, package_name, leverage, "
            "main_password_enc, investor_password_enc) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (org_id, user_id, package_id, package_name, leverage,
             cipher.encrypt(main.encode()).decode(),
             cipher.encrypt(investor.encode()).decode())).fetchone()
    return int(req_id)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_migration_023.py -q -p no:cacheprovider`
Expected: FAIL — `023_portal_identity.sql` not in `schema_migrations`, and the column/table assertions fail (`relation "account_packages" does not exist` from `add_package`).

- [ ] **Step 4: Write the migration**

Create `db/migrations/023_portal_identity.sql`:

```sql
-- Client portal, phase 2: identity. The investor's profile and identity
-- verification (one row per investor per org: the reference's KYC step 1
-- IS the profile), the account packages an admin offers, the live trading
-- account requests investors file against them, and the sign-in history.
-- Additive only: nothing is dropped or copied.
-- See docs/superpowers/specs/2026-10-01-client-portal-phase-2-identity-design.md
-- section 4.

CREATE TABLE kyc_profiles (
    org_id                BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id               BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    full_name             TEXT NULL,
    gender                TEXT NULL CHECK (gender IN ('male', 'female', 'other')),
    date_of_birth         DATE NULL,
    phone                 TEXT NULL,
    address_line          TEXT NULL,
    area                  TEXT NULL,
    landmark              TEXT NULL,
    city                  TEXT NULL,
    state                 TEXT NULL,
    postal_code           TEXT NULL,
    -- ISO 3166 alpha-2, upper case.
    country_residence     TEXT NULL CHECK (country_residence ~ '^[A-Z]{2}$'),
    country_citizenship   TEXT NULL CHECK (country_citizenship ~ '^[A-Z]{2}$'),
    id_type               TEXT NULL CHECK (id_type IN ('passport', 'national_id', 'driving_licence')),
    id_number             TEXT NULL,
    id_front_file_id      BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    id_back_file_id       BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    address_proof_file_id BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    photo_file_id         BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    status                TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
    submitted_at          TIMESTAMPTZ NULL,
    decided_by            BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at            TIMESTAMPTZ NULL,
    decision_note         TEXT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX kyc_profiles_queue ON kyc_profiles (org_id, status, submitted_at);

-- What an investor may request: a live MT5 account type the admin opens by
-- hand at the broker.
CREATE TABLE account_packages (
    id                BIGSERIAL PRIMARY KEY,
    org_id            BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    min_deposit       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (min_deposit >= 0),
    currency          TEXT NOT NULL DEFAULT 'USD',
    spread_label      TEXT NULL,
    leverage_options  INTEGER[] NOT NULL CHECK (cardinality(leverage_options) > 0),
    enabled           BOOLEAN NOT NULL DEFAULT true,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX account_packages_by_org ON account_packages (org_id, sort_order, id);

-- One live account request. The two MT5 passwords the investor chose are
-- Fernet-sealed and exist only while an admin still has to act: the CHECK
-- makes "wiped on decision" a database rule, not a convention.
CREATE TABLE account_requests (
    id                     BIGSERIAL PRIMARY KEY,
    org_id                 BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id                BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package_id             BIGINT NULL REFERENCES account_packages(id) ON DELETE SET NULL,
    package_name           TEXT NOT NULL,
    leverage               INTEGER NOT NULL CHECK (leverage > 0),
    main_password_enc      TEXT NULL,
    investor_password_enc  TEXT NULL,
    status                 TEXT NOT NULL DEFAULT 'requested'
                           CHECK (status IN ('requested', 'fulfilled', 'rejected', 'cancelled')),
    mt5_login              BIGINT NULL,
    mt5_server             TEXT NULL,
    account_id             BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    decided_by             BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at             TIMESTAMPTZ NULL,
    decision_note          TEXT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT account_requests_passwords_only_while_open CHECK (
        status = 'requested' OR (main_password_enc IS NULL AND investor_password_enc IS NULL)),
    CONSTRAINT account_requests_fulfilled_has_login CHECK (
        status <> 'fulfilled' OR (mt5_login IS NOT NULL AND mt5_server IS NOT NULL))
);
CREATE INDEX account_requests_queue ON account_requests (org_id, status, created_at);
CREATE UNIQUE INDEX account_requests_one_open ON account_requests (org_id, user_id)
    WHERE status = 'requested';

-- Sign-in history. Written by /api/login (password_ok, or failed for a
-- known email -- never for an unknown one, so this table cannot probe for
-- accounts) and /api/mpin/verify (mpin_ok). Rows older than 180 days are
-- deleted on write (auth.record_login).
CREATE TABLE login_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip          TEXT NOT NULL,
    user_agent  TEXT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN ('password_ok', 'mpin_ok', 'failed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX login_events_by_user ON login_events (user_id, created_at DESC);
```

- [ ] **Step 5: Add the new tables to the conftest TRUNCATE**

In `api/tests/conftest.py`, the `db` fixture's statement becomes:

```python
        conn.execute(
            "TRUNCATE login_events, account_requests, account_packages, kyc_profiles, "
            "transfers, withdrawals, deposits, wallet_entries, payout_destinations, "
            "portal_settings, payment_methods, files, "
            "events, portfolio_snapshots, mappings, symbol_cache, "
            "executions, positions, deals, deal_backfill_state, balance_samples, "
            "accounts, ctid_connections, "
            "oauth_states, org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_migration_023.py tests/test_migration_022.py -q -p no:cacheprovider`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add db/migrations/023_portal_identity.sql api/tests/test_migration_023.py api/tests/conftest.py api/tests/portal_helpers.py
git commit -m "feat(db): migration 023 -- kyc_profiles, account_packages, account_requests, login_events

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 2: Uploads accept the KYC purposes; a KYC file fills one slot only

**Files:**
- Modify: `api/src/api/uploads.py:21-22`
- Modify: `api/src/api/routes/portal_files.py:24-25, 32-48, 93`
- Test: `api/tests/test_uploads.py`

**Interfaces:**
- Consumes: table `kyc_profiles` (Task 1); `portal_helpers.kyc_profile`, `seed_file`.
- Produces: `uploads.ACCEPTED_PURPOSES`; `file_belongs` refuses a file already in any `kyc_profiles` file column.

- [ ] **Step 1: Write the failing tests**

In `api/tests/test_uploads.py`, replace `test_only_the_phase_1_purposes_are_accepted` (lines 139–144) with:

```python
@pytest.mark.parametrize("purpose", ["kyc_document", "kyc_photo"])
def test_the_kyc_purposes_are_accepted(portal, purpose):
    client, org_id, _, _ = portal
    r = upload(client, org_id, purpose=purpose)
    assert r.status_code == 201, r.text
    assert r.json()["purpose"] == purpose


@pytest.mark.parametrize("purpose", ["ticket_attachment", "avatar", "selfie", ""])
def test_only_the_accepted_purposes_are_stored(portal, purpose):
    client, org_id, _, _ = portal
    r = upload(client, org_id, purpose=purpose)
    assert r.status_code == 400 and r.json()["detail"] == "purpose is not accepted yet"
```

Append to the same file:

```python
def test_file_belongs_refuses_a_file_already_in_a_kyc_slot(portal, db):
    from api.routes.portal_files import file_belongs
    from portal_helpers import kyc_profile, seed_file
    client, org_id, investor, _ = portal
    written = kyc_profile(db, org_id, investor["id"], status="draft")
    loose = seed_file(db, org_id, investor["id"], purpose="kyc_document")
    with psycopg.connect(db, autocommit=True) as conn:
        assert file_belongs(conn, org_id, investor["id"], loose, "kyc_document")
        for slot in ("id_front_file_id", "id_back_file_id", "address_proof_file_id"):
            assert not file_belongs(conn, org_id, investor["id"], written[slot], "kyc_document")
        assert not file_belongs(conn, org_id, investor["id"], written["photo_file_id"], "kyc_photo")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_uploads.py -q -p no:cacheprovider`
Expected: FAIL — the two `kyc_*` uploads answer 400 `purpose is not accepted yet`; the slot test fails on the first `assert not file_belongs`.

- [ ] **Step 3: Accept the purposes**

In `api/src/api/uploads.py` replace lines 21–22 and update the module docstring's second paragraph:

```python
# What the upload route stores today. The files CHECK already lists every
# purpose of every phase; the rest are accepted in their phase.
ACCEPTED_PURPOSES = {"deposit_receipt", "payout_proof", "kyc_document", "kyc_photo"}
ALL_PURPOSES = ACCEPTED_PURPOSES | {"ticket_attachment", "avatar"}
```

Docstring paragraph (replaces "Phase 1 stores deposit receipts and payout proofs; …"):

```python
Phase 1 stores deposit receipts and payout proofs, phase 2 identity
documents and photos; the other purposes are in the database CHECK already
and are accepted here in their phase.
```

In `api/src/api/routes/portal_files.py` change the import (lines 24–25) and the check (line 93):

```python
from ..uploads import (ACCEPTED_PURPOSES, ALLOWED, MAX_UPLOAD_BYTES, UPLOADS_PER_HOUR,
                       UploadStore, detect_type)
```

```python
        if purpose not in ACCEPTED_PURPOSES:
            raise HTTPException(status_code=400, detail="purpose is not accepted yet")
```

- [ ] **Step 4: One file per KYC slot**

Replace `file_belongs` in `api/src/api/routes/portal_files.py`:

```python
def file_belongs(conn: psycopg.Connection, org_id: int, user_id: int,
                 file_id: Optional[int], purpose: str) -> bool:
    """Whether file_id is this investor's file of this purpose in this org,
    and not yet attached anywhere: not to a deposit or a payout destination
    (spec section 9: a file is referenced by at most one request row) and
    not to any slot of a KYC profile (phase 2: one file per document slot).
    None (no file attached) is fine. Routes call this before storing a file
    id, so this is the one gate that rule needs."""
    if file_id is None:
        return True
    row = conn.execute(
        "SELECT 1 FROM files WHERE id = %s AND org_id = %s AND user_id = %s AND purpose = %s "
        "  AND NOT EXISTS (SELECT 1 FROM deposits d WHERE d.receipt_file_id = files.id) "
        "  AND NOT EXISTS (SELECT 1 FROM payout_destinations p WHERE p.proof_file_id = files.id) "
        "  AND NOT EXISTS (SELECT 1 FROM kyc_profiles k WHERE files.id IN "
        "      (k.id_front_file_id, k.id_back_file_id, k.address_proof_file_id, k.photo_file_id))",
        (file_id, org_id, user_id, purpose)).fetchone()
    return row is not None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_uploads.py tests/test_portal_deposits.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/uploads.py api/src/api/routes/portal_files.py api/tests/test_uploads.py
git commit -m "feat(api): uploads accept kyc_document and kyc_photo; a KYC file fills one slot only

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 3: Sign-in history and the password change's MPIN

**Files:**
- Modify: `api/src/api/auth.py` (imports; new `record_login`, `sign_ins` after `_is_proxy_address`; login handler; `PasswordChangeRequest` and `change_password`; new `GET /api/me/sign-ins`)
- Modify: `api/src/api/routes/mpin.py` (`verify_mpin`)
- Create: `api/tests/test_login_events.py`
- Modify: `api/tests/test_auth.py:125,142,166,185,188`

**Interfaces:**
- Consumes: table `login_events` (Task 1); `auth.get_client_ip`; `mpin_core.require_mpin`; `rbac.require_investor`.
- Produces: `auth.LOGIN_HISTORY_DAYS = 180`, `auth.SIGN_INS_MAX = 200`, `auth.record_login(conn, user_id, request, cfg, outcome) -> None`, `auth.sign_ins(conn, user_id, limit=50) -> list[dict]`; route `GET /api/me/sign-ins` (any role; the investor Security page uses it too — ruling: no investor/sign-ins duplicate); `POST /api/me/password` takes `mpin`.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_login_events.py`:

```python
# api/tests/test_login_events.py
"""Sign-in history: /api/login writes password_ok (or failed for a known
email -- never anything for an unknown one), /api/mpin/verify writes mpin_ok,
rows older than 180 days go on the next write, and each user reads only
their own. Plus the password change's MPIN step-up."""
import psycopg

from portal_helpers import csrf, member


def _events(db):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT user_id, ip, user_agent, outcome FROM login_events ORDER BY id").fetchall()


def test_a_full_sign_in_writes_password_ok_then_mpin_ok(app_client, make_user, login_as, db):
    user = make_user(email="a@example.com")
    login_as(app_client, user)
    assert _events(db) == [(user["id"], "testclient", "testclient", "password_ok"),
                           (user["id"], "testclient", "testclient", "mpin_ok")]


def test_a_wrong_password_is_recorded_but_an_unknown_email_is_not(app_client, make_user, db):
    user = make_user(email="a@example.com")
    r = app_client.post("/api/login", json={"email": "a@example.com", "password": "wrong-one!"})
    assert r.status_code == 401
    r = app_client.post("/api/login", json={"email": "nobody@example.com", "password": "x-y-z-1"})
    assert r.status_code == 401
    assert [(e[0], e[3]) for e in _events(db)] == [(user["id"], "failed")]


def test_a_wrong_mpin_writes_nothing(app_client, make_user, db):
    make_user(email="a@example.com")
    assert app_client.post("/api/login", json={
        "email": "a@example.com", "password": "a-solid-password"}).status_code == 204
    r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=csrf(app_client))
    assert r.status_code == 401
    assert [e[3] for e in _events(db)] == ["password_ok"]


def test_rows_older_than_180_days_go_on_the_next_write(app_client, make_user, login_as, db):
    user = make_user(email="a@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        for days, ip in ((181, "10.0.0.181"), (179, "10.0.0.179")):
            conn.execute(
                "INSERT INTO login_events (user_id, ip, outcome, created_at) "
                "VALUES (%s, %s, 'password_ok', now() - make_interval(days => %s))",
                (user["id"], ip, days))
    login_as(app_client, user)
    ips = [e[1] for e in _events(db)]
    assert "10.0.0.181" not in ips and "10.0.0.179" in ips


def test_me_sign_ins_lists_only_your_own_newest_first(app_client, make_user, login_as):
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    login_as(app_client, b)
    app_client.cookies.clear()
    login_as(app_client, a)
    rows = app_client.get("/api/me/sign-ins").json()
    assert [r["outcome"] for r in rows] == ["mpin_ok", "password_ok"]
    assert set(rows[0]) == {"id", "ip", "user_agent", "outcome", "created_at"}
    assert rows[0]["ip"] == "testclient" and rows[0]["user_agent"] == "testclient"
    limited = app_client.get("/api/me/sign-ins?limit=1").json()
    assert [r["outcome"] for r in limited] == ["mpin_ok"]


def test_me_sign_ins_needs_the_mpin(app_client, make_user):
    make_user(email="a@example.com")
    app_client.post("/api/login", json={"email": "a@example.com", "password": "a-solid-password"})
    r = app_client.get("/api/me/sign-ins")
    assert r.status_code == 401 and r.json()["detail"] == "MPIN required"


def test_a_password_change_needs_the_mpin(app_client, make_user, login_as):
    user = make_user(email="p@example.com")
    login_as(app_client, user)
    body = {"current_password": "a-solid-password", "new_password": "brand-new-secret"}
    r = app_client.post("/api/me/password", json=body, headers=csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = app_client.post("/api/me/password", json={**body, "mpin": "000000"},
                        headers=csrf(app_client))
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid MPIN", "attempts_left": 4}
    r = app_client.post("/api/me/password", json={**body, "mpin": "123456"},
                        headers=csrf(app_client))
    assert r.status_code == 204
    app_client.cookies.clear()
    assert app_client.post("/api/login", json={
        "email": "p@example.com", "password": "brand-new-secret"}).status_code == 204
```

In `api/tests/test_auth.py` add `"mpin": "123456"` to each of the five `/api/me/password` JSON bodies (lines 125, 142, 166, 185, 188), e.g.:

```python
    r = app_client.post("/api/me/password", json={
        "current_password": "not-the-password", "new_password": "another-good-one",
        "mpin": "123456"},
        headers=_csrf_headers(app_client))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_login_events.py tests/test_auth.py -q -p no:cacheprovider`
Expected: FAIL — `_events` is empty, `/api/me/sign-ins` is 404, and the password change answers 204 without an MPIN. `test_auth.py` still passes (an extra body field is ignored today).

- [ ] **Step 3: Record and read sign-ins in `auth.py`**

Change the typing import at the top of `api/src/api/auth.py`:

```python
from typing import Any, Optional
```

Insert after `_is_proxy_address`:

```python
LOGIN_HISTORY_DAYS = 180
SIGN_INS_MAX = 200


def record_login(conn, user_id: int, request: Request, cfg: ApiConfig, outcome: str) -> None:
    """One sign-in history row (login_events). Best effort: a failed write is
    logged and never fails the sign-in. Only callers that already know the
    user exists call this, so the table never answers "is this email
    registered?"."""
    ip = get_client_ip(request, trust_proxy=cfg.trust_proxy)
    agent = (request.headers.get("user-agent") or "")[:256] or None
    try:
        # ponytail: delete-on-write, a nightly job if writes ever get hot
        conn.execute(
            "DELETE FROM login_events WHERE user_id = %s "
            "AND created_at < now() - make_interval(days => %s)",
            (user_id, LOGIN_HISTORY_DAYS))
        conn.execute(
            "INSERT INTO login_events (user_id, ip, user_agent, outcome) VALUES (%s, %s, %s, %s)",
            (user_id, ip, agent, outcome))
    except Exception:
        logger.exception("failed to record a sign-in for user %s", user_id)


def sign_ins(conn, user_id: int, limit: int = 50) -> list[dict]:
    """The user's own sign-ins, newest first, at most SIGN_INS_MAX."""
    size = max(1, min(int(limit), SIGN_INS_MAX))
    rows = conn.execute(
        "SELECT id, ip, user_agent, outcome, created_at FROM login_events "
        "WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
        (user_id, size)).fetchall()
    return [{"id": r[0], "ip": r[1], "user_agent": r[2], "outcome": r[3],
             "created_at": r[4].isoformat()} for r in rows]
```

In the login handler replace the two password checks and the session issue:

```python
        if not row:
            verify_password(_DUMMY_HASH, request_data.password)
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if not verify_password(row[1], request_data.password):
            record_login(conn, row[0], request, cfg, "failed")
            raise HTTPException(status_code=401, detail="Invalid email or password")
        record_login(conn, row[0], request, cfg, "password_ok")

        response = Response(status_code=204)
        _issue_session(response, cfg, row[0], row[2], pin=False)
        return response
```

Replace `PasswordChangeRequest` and the start of `change_password`:

```python
    class PasswordChangeRequest(BaseModel):
        current_password: str
        new_password: str
        # The MPIN step-up (phase 2): a stolen session plus a guessed or
        # phished password is still not enough.
        mpin: Any = None

    @router.post("/me/password")
    async def change_password(
        request_data: PasswordChangeRequest,
        user_id: int = Depends(require_user),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        """Rotate the caller's password and disown every other session.

        Requires the MPIN first, then the current password (a stolen session
        alone must not be enough to take an account over). Bumping
        session_version kills all outstanding cookies -- including any the
        attacker holds -- and this browser is handed a freshly versioned one
        so the user stays signed in where they are.
        """
        # Imported here: mpin_core imports from this module.
        from .mpin_core import require_mpin
        failure = require_mpin(conn, user_id, request_data.mpin)
        if failure is not None:
            return failure
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s", (user_id,)
        ).fetchone()
```

(The rest of `change_password` is unchanged.)

Add, right after `logout_everywhere`:

```python
    @router.get("/me/sign-ins")
    async def my_sign_ins(
        limit: int = 50,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        """Where this account signed in from, for the desk's own Security card."""
        return sign_ins(conn, user_id, limit)
```

- [ ] **Step 4: Record the MPIN step in `routes/mpin.py`**

Add `record_login` to the `..auth` import list, then replace `verify_mpin`:

```python
    @router.post("/api/mpin/verify", status_code=204)
    async def verify_mpin(body: VerifyRequest, request: Request,
                          info: SessionInfo = Depends(require_half_session),
                          cfg: ApiConfig = Depends(ApiConfig.from_env),
                          conn: psycopg.Connection = Depends(get_conn)):
        if not MPIN_RE.fullmatch(body.mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = check_mpin(conn, info.user_id, body.mpin)
        if failure is not None:
            return failure
        record_login(conn, info.user_id, request, cfg, "mpin_ok")
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, info.session_version, pin=True)
        return response
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_login_events.py tests/test_auth.py tests/test_mpin.py tests/test_mpin_core.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/src/api/auth.py api/src/api/routes/mpin.py api/tests/test_login_events.py api/tests/test_auth.py
git commit -m "feat(api): sign-in history (login_events, /api/me/sign-ins); a password change needs the MPIN

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 4: `portal_identity.py` — identity rules, sealing and serialisers

**Files:**
- Create: `api/src/api/portal_identity.py`
- Create: `api/tests/test_portal_identity_rules.py`

**Interfaces:**
- Consumes: `portal_ledger.LedgerError`, `clean_text`, `money`; `cryptography.fernet.Fernet` (already a dependency, used by `oauth.py`).
- Produces: every `pid.*` name in the interfaces doc's `portal_identity.py` section, with those exact refusal strings.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_identity_rules.py`:

```python
# api/tests/test_portal_identity_rules.py
"""The identity rules with no database: field cleaning, completeness,
re-verification, the MT5 password policy, leverage lists and sealing."""
from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet, InvalidToken

from api import portal_identity as pid
from api.portal_ledger import LedgerError


def test_the_field_lists_line_up_with_the_table():
    assert pid.PROFILE_FIELDS[0] == "full_name" and pid.PROFILE_FIELDS[-1] == "photo_file_id"
    assert len(pid.PROFILE_FIELDS) == 18
    assert set(pid.FILE_SLOTS) == {"id_front_file_id", "id_back_file_id",
                                   "address_proof_file_id", "photo_file_id"}
    assert pid.REQUIRED_FIELDS == tuple(f for f in pid.PROFILE_FIELDS
                                        if f not in ("area", "landmark", "state"))
    assert pid.PROFILE_COLS.startswith("user_id, full_name, ")
    assert pid.PROFILE_COLS.endswith("photo_file_id, status, submitted_at, decided_by, "
                                     "decided_at, decision_note, updated_at")


@pytest.mark.parametrize("field,raw,expected", [
    ("full_name", "  Ada Lovelace ", "Ada Lovelace"),
    ("full_name", "", None),
    ("area", None, None),
    ("gender", "Female", "female"),
    ("id_type", "DRIVING_LICENCE", "driving_licence"),
    ("country_residence", " in ", "IN"),
    ("date_of_birth", "1990-04-02", date(1990, 4, 2)),
    ("photo_file_id", 12, 12),
    ("photo_file_id", None, None),
])
def test_clean_profile_field_normalises(field, raw, expected):
    assert pid.clean_profile_field(field, raw) == expected


@pytest.mark.parametrize("field,raw,message", [
    ("gender", "x", "gender must be one of male, female, other"),
    ("id_type", "visa", "id_type must be one of passport, national_id, driving_licence"),
    ("country_citizenship", "IND", "country_citizenship must be a two-letter country code"),
    ("date_of_birth", "02/04/1990", "date_of_birth must be a date (YYYY-MM-DD)"),
    ("date_of_birth", (date.today() + timedelta(days=1)).isoformat(),
     "date_of_birth must be in the past"),
    ("id_front_file_id", "12", "id_front_file_id must be a file id"),
    ("id_front_file_id", True, "id_front_file_id must be a file id"),
    ("postal_code", "1" * 17, "postal_code must be at most 16 characters"),
])
def test_clean_profile_field_refuses(field, raw, message):
    with pytest.raises(LedgerError) as exc:
        pid.clean_profile_field(field, raw)
    assert str(exc.value) == message


def test_missing_fields_names_the_required_gaps_in_order():
    profile = pid.empty_profile(5)
    assert profile["missing"] == list(pid.REQUIRED_FIELDS)
    profile.update(full_name="Ada", phone="1", state=None)
    assert "full_name" not in pid.missing_fields(profile)
    assert "state" not in pid.missing_fields(profile)
    assert pid.missing_fields(profile)[0] == "gender"


def test_only_identity_changes_need_reverification():
    before = {**pid.empty_profile(5), "phone": "1", "full_name": "Ada",
              "date_of_birth": "1990-04-02"}
    assert not pid.needs_reverification(before, {"phone": "2", "city": "Chennai"})
    assert not pid.needs_reverification(before, {"full_name": "Ada"})
    assert not pid.needs_reverification(before, {"date_of_birth": date(1990, 4, 2)})
    assert pid.needs_reverification(before, {"full_name": "Ada L"})
    assert pid.needs_reverification(before, {"photo_file_id": 9})
    assert pid.needs_reverification(before, {"country_residence": "GB"})


@pytest.mark.parametrize("raw", ["Abcdefg1", "Z9" + "x" * 30, "Pa$$w0rd!"])
def test_mt5_passwords_that_pass(raw):
    assert pid.check_mt5_password(raw, "main_password") == raw


@pytest.mark.parametrize("raw", ["Abcdef1", "A1" + "x" * 31, "abcdefg1", "ABCDEFG1",
                                 "Abcdefgh", "Abc defg1", None, 12345678])
def test_mt5_passwords_that_fail(raw):
    with pytest.raises(LedgerError) as exc:
        pid.check_mt5_password(raw, "investor_password")
    assert str(exc.value) == ("investor_password must be 8-32 characters without spaces, "
                              "with an upper-case letter, a lower-case letter and a digit")


def test_leverage_options_are_sorted_and_deduplicated():
    assert pid.parse_leverage_options([500, 100, 100, 200]) == [100, 200, 500]
    for bad in ([], None, "100", [0], [3001], [True], [1.5], list(range(1, 14))):
        with pytest.raises(LedgerError) as exc:
            pid.parse_leverage_options(bad)
        assert str(exc.value) == "leverage_options must be a list of whole numbers from 1 to 3000"


def test_seal_round_trips_and_a_foreign_key_cannot_read_it():
    key = Fernet.generate_key().decode()
    token = pid.seal(key, "Main1234")
    assert token != "Main1234" and pid.unseal(key, token) == "Main1234"
    with pytest.raises(InvalidToken):
        pid.unseal(Fernet.generate_key().decode(), token)


def test_profile_json_reads_a_row_and_lists_what_is_missing():
    n = len(pid.PROFILE_FIELDS)
    values = [None] * n
    values[pid.PROFILE_FIELDS.index("full_name")] = "Ada"
    values[pid.PROFILE_FIELDS.index("date_of_birth")] = date(1990, 4, 2)
    row = (5, *values, "draft", None, None, None, None, None, "ada@example.com", "Ada")
    out = pid.profile_json(row)
    assert out["user_id"] == 5 and out["full_name"] == "Ada"
    assert out["date_of_birth"] == "1990-04-02" and out["status"] == "draft"
    assert out["email"] == "ada@example.com" and out["display_name"] == "Ada"
    assert "full_name" not in out["missing"] and "gender" in out["missing"]


def test_package_and_request_json_shapes():
    from decimal import Decimal
    assert pid.package_json((1, "Standard", Decimal("100"), "USD", "20-25", [100, 200], True, 0)) == {
        "id": 1, "name": "Standard", "min_deposit": 100.0, "currency": "USD",
        "spread_label": "20-25", "leverage_options": [100, 200], "enabled": True, "sort_order": 0}
    row = (7, 5, 1, "Standard", 200, "requested", None, None, None, None, None, None, None)
    out = pid.request_json(row)
    assert out == {"id": 7, "user_id": 5, "package_id": 1, "package_name": "Standard",
                   "leverage": 200, "status": "requested", "mt5_login": None, "mt5_server": None,
                   "account_id": None, "decided_by": None, "decided_at": None,
                   "decision_note": None, "created_at": None}
    assert "main_password" not in str(out)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_identity_rules.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'portal_identity'`.

- [ ] **Step 3: Write the module**

Create `api/src/api/portal_identity.py`:

```python
# api/src/api/portal_identity.py
"""Client portal, phase 2: the identity rules shared by the investor and
admin routes -- which profile fields exist and how each is cleaned, what a
complete profile needs, which edits send an approved profile back to
verification, the MT5 password policy, Fernet sealing for the two account
passwords, and the row serialisers. No routes here; one small database
helper (kyc_status)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import psycopg
from cryptography.fernet import Fernet

from .portal_ledger import LedgerError, clean_text, money

PROFILE_FIELDS: tuple[str, ...] = (
    "full_name", "gender", "date_of_birth", "phone", "address_line", "area", "landmark",
    "city", "state", "postal_code", "country_residence", "country_citizenship", "id_type",
    "id_number", "id_front_file_id", "id_back_file_id", "address_proof_file_id",
    "photo_file_id")

# Document slot -> the files.purpose an upload for it must carry.
FILE_SLOTS: dict[str, str] = {
    "id_front_file_id": "kyc_document",
    "id_back_file_id": "kyc_document",
    "address_proof_file_id": "kyc_document",
    "photo_file_id": "kyc_photo",
}
FILE_LABELS: dict[str, str] = {
    "id_front_file_id": "ID front",
    "id_back_file_id": "ID back",
    "address_proof_file_id": "address proof",
    "photo_file_id": "photo",
}
OPTIONAL_FIELDS = frozenset({"area", "landmark", "state"})
REQUIRED_FIELDS: tuple[str, ...] = tuple(f for f in PROFILE_FIELDS if f not in OPTIONAL_FIELDS)
# Spec section 4: editing these keeps an approved profile approved; any
# other change (name, date of birth, gender, countries, ID fields, any
# document) sends it back to draft for re-verification.
CONTACT_FIELDS = frozenset({"phone", "address_line", "area", "landmark", "city", "state",
                            "postal_code"})

TEXT_LIMITS: dict[str, int] = {
    "full_name": 128, "phone": 32, "address_line": 256, "area": 128, "landmark": 128,
    "city": 128, "state": 128, "postal_code": 16, "id_number": 64,
}
CHOICES: dict[str, tuple[str, ...]] = {
    "gender": ("male", "female", "other"),
    "id_type": ("passport", "national_id", "driving_licence"),
}
COUNTRY_FIELDS = ("country_residence", "country_citizenship")
COUNTRY_RE = re.compile(r"[A-Z]{2}")


def clean_profile_field(field: str, raw: object) -> Any:
    """One profile value, normalised for storage. '' and None mean "clear
    it" (None). Raises LedgerError with the message the route returns as a
    400."""
    if field in FILE_SLOTS:
        if raw is None or raw == "":
            return None
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise LedgerError(f"{field} must be a file id")
        return raw
    text = clean_text(raw, field, max_len=TEXT_LIMITS.get(field, 128), required=False)
    if text is None:
        return None
    if field in CHOICES:
        value = text.lower()
        if value not in CHOICES[field]:
            raise LedgerError(f"{field} must be one of {', '.join(CHOICES[field])}")
        return value
    if field in COUNTRY_FIELDS:
        value = text.upper()
        if not COUNTRY_RE.fullmatch(value):
            raise LedgerError(f"{field} must be a two-letter country code")
        return value
    if field == "date_of_birth":
        try:
            born = date.fromisoformat(text)
        except ValueError:
            raise LedgerError("date_of_birth must be a date (YYYY-MM-DD)")
        if born >= date.today():
            raise LedgerError("date_of_birth must be in the past")
        return born
    return text


def missing_fields(profile: dict) -> list[str]:
    """The required fields still empty, in form order."""
    return [f for f in REQUIRED_FIELDS if profile.get(f) in (None, "")]


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def needs_reverification(before: dict, changes: dict) -> bool:
    """Whether these changes touch anything an admin verified. `before` is
    a profile_json dict (dates as ISO strings); `changes` holds cleaned
    values (dates as date objects)."""
    return any(key not in CONTACT_FIELDS and _plain(value) != before.get(key)
               for key, value in changes.items())


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


PROFILE_COLS = ("user_id, " + ", ".join(PROFILE_FIELDS)
                + ", status, submitted_at, decided_by, decided_at, decision_note, updated_at")


def profile_json(row) -> dict:
    """A kyc_profiles row (PROFILE_COLS order, optionally followed by the
    admin join's email and display_name) as the API returns it, with the
    list of required fields still missing."""
    n = len(PROFILE_FIELDS)
    values = dict(zip(PROFILE_FIELDS, row[1:1 + n]))
    values["date_of_birth"] = _iso(values["date_of_birth"])
    status, submitted_at, decided_by, decided_at, note, updated_at = row[1 + n:7 + n]
    out = {"user_id": row[0], **values, "status": status, "submitted_at": _iso(submitted_at),
           "decided_by": decided_by, "decided_at": _iso(decided_at), "decision_note": note,
           "updated_at": _iso(updated_at)}
    out["missing"] = missing_fields(out)
    if len(row) > 7 + n:
        out["email"], out["display_name"] = row[7 + n], row[8 + n]
    return out


def empty_profile(user_id: int) -> dict:
    """What GET investor/profile answers before the first save."""
    return {"user_id": user_id, **{f: None for f in PROFILE_FIELDS}, "status": "draft",
            "submitted_at": None, "decided_by": None, "decided_at": None,
            "decision_note": None, "updated_at": None, "missing": list(REQUIRED_FIELDS)}


def kyc_status(conn: psycopg.Connection, org_id: int, user_id: int) -> str:
    """The investor's verification status; 'draft' before the first save."""
    row = conn.execute("SELECT status FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
                       (org_id, user_id)).fetchone()
    return row[0] if row else "draft"


# Printable ASCII without spaces, 8-32 long: what MT5 servers accept.
MT5_PASSWORD_RE = re.compile(r"[!-~]{8,32}")


def check_mt5_password(raw: object, field: str) -> str:
    if (not isinstance(raw, str) or not MT5_PASSWORD_RE.fullmatch(raw)
            or not re.search(r"[A-Z]", raw) or not re.search(r"[a-z]", raw)
            or not re.search(r"[0-9]", raw)):
        raise LedgerError(f"{field} must be 8-32 characters without spaces, with an "
                          "upper-case letter, a lower-case letter and a digit")
    return raw


MAX_LEVERAGE = 3000


def parse_leverage_options(raw: object) -> list[int]:
    message = f"leverage_options must be a list of whole numbers from 1 to {MAX_LEVERAGE}"
    if not isinstance(raw, list) or not raw or len(raw) > 12:
        raise LedgerError(message)
    out = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LEVERAGE:
            raise LedgerError(message)
        out.append(value)
    return sorted(set(out))


def seal(fernet_key: str, secret: str) -> str:
    """Encrypt with the app's FERNET_KEY, the same key oauth.py seals the
    cTrader tokens with."""
    return Fernet(fernet_key.encode()).encrypt(secret.encode()).decode()


def unseal(fernet_key: str, token: str) -> str:
    """Raises cryptography.fernet.InvalidToken when the key has changed."""
    return Fernet(fernet_key.encode()).decrypt(token.encode()).decode()


PACKAGE_COLS = "id, name, min_deposit, currency, spread_label, leverage_options, enabled, sort_order"


def package_json(row) -> dict:
    (package_id, name, min_deposit, currency, spread_label, leverage_options, enabled,
     sort_order) = row[:8]
    return {"id": package_id, "name": name, "min_deposit": money(min_deposit),
            "currency": currency, "spread_label": spread_label,
            "leverage_options": list(leverage_options), "enabled": bool(enabled),
            "sort_order": sort_order}


REQUEST_COLS = ("id, user_id, package_id, package_name, leverage, status, mt5_login, mt5_server, "
                "account_id, decided_by, decided_at, decision_note, created_at")


def request_json(row) -> dict:
    """An account request without its passwords (they are never selected)."""
    (req_id, user_id, package_id, package_name, leverage, status, mt5_login, mt5_server,
     account_id, decided_by, decided_at, decision_note, created_at) = row[:13]
    out = {"id": req_id, "user_id": user_id, "package_id": package_id,
           "package_name": package_name, "leverage": leverage, "status": status,
           "mt5_login": int(mt5_login) if mt5_login is not None else None,
           "mt5_server": mt5_server,
           "account_id": int(account_id) if account_id is not None else None,
           "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "created_at": _iso(created_at)}
    if len(row) > 13:
        out["email"], out["display_name"] = row[13], row[14]
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_identity_rules.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/portal_identity.py api/tests/test_portal_identity_rules.py
git commit -m "feat(api): portal_identity -- profile field rules, re-verification, MT5 password policy, sealing, serialisers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 5: KYC, investor side — the identity router

**Files:**
- Create: `api/src/api/routes/portal_identity.py`
- Modify: `api/src/api/main.py:226-229` (mount the router)
- Create: `api/tests/test_portal_kyc.py`

**Interfaces:**
- Consumes: `pid.*` (Task 4); `portal_files.file_belongs` (Task 2); `mpin_core.require_mpin`; `rbac.require_investor`; `portal_common.audit_control`, `LedgerError`; `portal_helpers.kyc_profile`, `seed_file`, `member`, `csrf`.
- Produces: `create_portal_identity_router()`; `MpinBody`; `_profile_row(conn, org_id, user_id)`; routes `GET/PUT investor/profile`, `POST investor/profile/submit` with the interfaces doc's refusal strings; audit actions `investor_profile_saved` (info), `investor_kyc_submitted` (warning).

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_kyc.py`:

```python
# api/tests/test_portal_kyc.py
"""KYC: the investor saves a profile in parts, submits it with the MPIN,
and an admin approves or rejects it (Task 6 adds the admin half). An
approved profile stays approved through contact edits and returns to
draft on identity edits."""
import psycopg
import pytest

from portal_helpers import COMPLETE_PROFILE, csrf, kyc_profile, member, seed_file

from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    """org_client's org with an investor member logged in."""
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    return client, org_id, investor


def _put(client, org_id, body):
    return client.put(f"/api/orgs/{org_id}/investor/profile", json=body, headers=csrf(client))


def _submit(client, org_id, mpin="123456"):
    return client.post(f"/api/orgs/{org_id}/investor/profile/submit", json={"mpin": mpin},
                       headers=csrf(client))


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


def _status(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT status FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
                            (org_id, user_id)).fetchone()[0]


def _complete(client, org_id, db, investor):
    """Save every required field and the four documents through the API."""
    files = {slot: seed_file(db, org_id, investor["id"], purpose=purpose) for slot, purpose in (
        ("id_front_file_id", "kyc_document"), ("id_back_file_id", "kyc_document"),
        ("address_proof_file_id", "kyc_document"), ("photo_file_id", "kyc_photo"))}
    r = _put(client, org_id, {**COMPLETE_PROFILE, **files})
    assert r.status_code == 200, r.text
    return files


# ------------------------------------------------------------ read + save


def test_a_new_investor_reads_an_empty_draft(portal):
    client, org_id, investor = portal
    body = client.get(f"/api/orgs/{org_id}/investor/profile").json()
    assert body["user_id"] == investor["id"] and body["status"] == "draft"
    assert body["full_name"] is None and body["photo_file_id"] is None
    assert body["missing"][0] == "full_name" and "photo_file_id" in body["missing"]


def test_saving_in_parts_keeps_the_earlier_parts(portal, db):
    client, org_id, investor = portal
    r = _put(client, org_id, {"full_name": " Ada Lovelace ", "gender": "Female",
                              "date_of_birth": "1990-04-02", "phone": "+44 20 1234"})
    assert r.status_code == 200
    r = _put(client, org_id, {"city": "London", "country_residence": "gb"})
    body = r.json()
    assert body["full_name"] == "Ada Lovelace" and body["gender"] == "female"
    assert body["date_of_birth"] == "1990-04-02" and body["city"] == "London"
    assert body["country_residence"] == "GB" and body["status"] == "draft"
    assert client.get(f"/api/orgs/{org_id}/investor/profile").json() == body
    severity, payload, actor = _events(db, org_id, "investor_profile_saved")[-1]
    assert severity == "info" and actor == "inv@example.com"
    assert payload["user_id"] == investor["id"]
    assert payload["fields"] == ["city", "country_residence"] and payload["reverify"] is False
    assert "London" not in str(payload)


def test_blank_clears_a_field_and_an_empty_body_changes_nothing(portal):
    client, org_id, _ = portal
    _put(client, org_id, {"landmark": "Near the park"})
    assert _put(client, org_id, {"landmark": ""}).json()["landmark"] is None
    assert _put(client, org_id, {}).status_code == 200


@pytest.mark.parametrize("body,detail", [
    ({"nickname": "x"}, "unknown field: nickname"),
    ({"gender": "x"}, "gender must be one of male, female, other"),
    ({"country_citizenship": "IND"}, "country_citizenship must be a two-letter country code"),
    ({"date_of_birth": "2/4/1990"}, "date_of_birth must be a date (YYYY-MM-DD)"),
    ({"photo_file_id": "12"}, "photo_file_id must be a file id"),
])
def test_a_bad_field_is_refused(portal, body, detail):
    client, org_id, _ = portal
    r = _put(client, org_id, body)
    assert r.status_code == 400 and r.json()["detail"] == detail


def test_documents_must_be_the_investors_own_unused_uploads(portal, db, make_user):
    client, org_id, investor = portal
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    theirs = seed_file(db, org_id, other["id"], purpose="kyc_document")
    photo = seed_file(db, org_id, investor["id"], purpose="kyc_photo")
    receipt = seed_file(db, org_id, investor["id"], purpose="deposit_receipt")
    doc = seed_file(db, org_id, investor["id"], purpose="kyc_document")
    for body, detail in (
            ({"id_front_file_id": theirs}, "ID front file not found"),
            ({"id_front_file_id": photo}, "ID front file not found"),
            ({"photo_file_id": receipt}, "photo file not found"),
            ({"id_front_file_id": doc, "id_back_file_id": doc}, "each document needs its own file")):
        r = _put(client, org_id, body)
        assert r.status_code == 400 and r.json()["detail"] == detail, body
    assert _put(client, org_id, {"id_front_file_id": doc}).status_code == 200
    # The same file in the same slot again is not a reuse.
    assert _put(client, org_id, {"id_front_file_id": doc, "phone": "1"}).status_code == 200
    # ...but moving it into another slot is.
    r = _put(client, org_id, {"id_back_file_id": doc})
    assert r.status_code == 400 and r.json()["detail"] == "each document needs its own file"


# ------------------------------------------------------------ submit


def test_submit_needs_the_mpin_and_a_complete_profile(portal, db):
    client, org_id, investor = portal
    _put(client, org_id, {"full_name": "Ada"})
    r = _submit(client, org_id, mpin="12")
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = _submit(client, org_id, mpin="000000")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid MPIN"
    r = _submit(client, org_id)
    assert r.status_code == 400
    body = r.json()
    assert body["missing"][0] == "gender" and "photo_file_id" in body["missing"]
    assert body["detail"] == "complete your profile first: " + ", ".join(body["missing"])
    assert _status(db, org_id, investor["id"]) == "draft"


def test_a_complete_profile_submits_and_locks(portal, db):
    client, org_id, investor = portal
    _complete(client, org_id, db, investor)
    r = _submit(client, org_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "submitted" and body["submitted_at"] and body["missing"] == []
    severity, payload, _ = _events(db, org_id, "investor_kyc_submitted")[-1]
    assert severity == "warning" and payload["user_id"] == investor["id"]
    r = _put(client, org_id, {"phone": "2"})
    assert r.status_code == 409 and r.json()["detail"] == "your profile is under review"
    r = _submit(client, org_id)
    assert r.status_code == 409 and r.json()["detail"] == "your profile is already submitted"


def test_a_rejected_profile_is_edited_then_resubmitted(portal, db):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="rejected")
    r = _put(client, org_id, {"id_number": "P7654321"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    r = _submit(client, org_id)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    assert r.json()["decision_note"] is None


def test_an_approved_profile_survives_contact_edits_but_not_identity_edits(portal, db):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="approved")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE kyc_profiles SET decision_note = 'ok', decided_at = now() "
                     "WHERE user_id = %s", (investor["id"],))
    r = _put(client, org_id, {"phone": "+91 1", "city": "Chennai", "postal_code": "600001"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decision_note"] == "ok"
    r = _put(client, org_id, {"full_name": "Investor Renamed"})
    body = r.json()
    assert body["status"] == "draft" and body["decision_note"] is None and body["decided_at"] is None
    payload = _events(db, org_id, "investor_profile_saved")[-1][1]
    assert payload["reverify"] is True
    r = _submit(client, org_id)
    assert r.status_code == 200 and r.json()["status"] == "submitted"


def test_the_profile_routes_are_for_investors_only(portal, login_as):
    client, org_id, _ = portal
    client.cookies.clear()
    login_as(client, ADMIN)
    assert client.get(f"/api/orgs/{org_id}/investor/profile").status_code == 403
    assert _put(client, org_id, {"phone": "1"}).status_code == 403
    assert _submit(client, org_id).status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_kyc.py -q -p no:cacheprovider`
Expected: FAIL — every route answers 404 (`investor/profile` is not mounted).

- [ ] **Step 3: Create the router with the investor KYC routes**

Create `api/src/api/routes/portal_identity.py`:

```python
# api/src/api/routes/portal_identity.py
"""The client portal, phase 2: identity. One router under
/api/orgs/{org_id} with both sides of three features -- the KYC profile
(investor saves and submits, admin decides), account packages (admin
defines, investor lists) and live account requests (investor requests,
admin reveals the passwords once per need, fulfils or rejects).

Investor routes resolve the caller's OWN rows through ctx.user_id and
never take a user id from the request. Rules and serialisers live in
api/portal_identity.py (imported as pid)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc
from .. import portal_identity as pid
from .portal_files import file_belongs

logger = logging.getLogger(__name__)


class MpinBody(BaseModel):
    mpin: Any = None


def _profile_row(conn: psycopg.Connection, org_id: int, user_id: int):
    return conn.execute(
        f"SELECT {pid.PROFILE_COLS} FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchone()


def create_portal_identity_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-identity"])

    # ------------------------------------------------------------ KYC, investor

    @router.get("/investor/profile", response_model=Dict[str, Any])
    async def my_profile(ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = _profile_row(conn, ctx.org_id, ctx.user_id)
        return pid.profile_json(row) if row else pid.empty_profile(ctx.user_id)

    @router.put("/investor/profile", response_model=Dict[str, Any])
    async def save_profile(body: Dict[str, Any] = Body(...),
                           ctx: OrgContext = Depends(require_investor),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Save any subset of the profile fields (the dashboard saves one
        step at a time). Locked while submitted. An approved profile stays
        approved through contact edits; anything else sends it back to
        draft and clears the approval."""
        unknown = sorted(set(body) - set(pid.PROFILE_FIELDS))
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown field: {unknown[0]}")
        try:
            changes = {key: pid.clean_profile_field(key, value) for key, value in body.items()}
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = _profile_row(conn, ctx.org_id, ctx.user_id)
        before = pid.profile_json(row) if row else pid.empty_profile(ctx.user_id)
        if before["status"] == "submitted":
            raise HTTPException(status_code=409, detail="your profile is under review")
        if not changes:
            return before
        slots_after = {slot: changes.get(slot, before[slot]) for slot in pid.FILE_SLOTS}
        used = [v for v in slots_after.values() if v is not None]
        if len(used) != len(set(used)):
            raise HTTPException(status_code=400, detail="each document needs its own file")
        for slot, purpose in pid.FILE_SLOTS.items():
            new = changes.get(slot)
            if new is not None and new != before[slot] and not file_belongs(
                    conn, ctx.org_id, ctx.user_id, new, purpose):
                raise HTTPException(status_code=400,
                                    detail=f"{pid.FILE_LABELS[slot]} file not found")
        reverify = before["status"] == "approved" and pid.needs_reverification(before, changes)
        status = "draft" if reverify else before["status"]
        cols = list(changes)
        sets = [f"{c} = EXCLUDED.{c}" for c in cols] + ["status = %s", "updated_at = now()"]
        if reverify:
            sets += ["decided_by = NULL", "decided_at = NULL", "decision_note = NULL"]
        # The status read above guards the write: a submit that landed in
        # between makes this a no-op, reported as a conflict.
        row = conn.execute(
            f"INSERT INTO kyc_profiles (org_id, user_id, {', '.join(cols)}) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (org_id, user_id) DO UPDATE SET {', '.join(sets)} "
            "WHERE kyc_profiles.status = %s "
            f"RETURNING {pid.PROFILE_COLS}",
            (ctx.org_id, ctx.user_id, *changes.values(), status, before["status"])).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="your profile changed; reload it")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_profile_saved",
            actor_email=ctx.user_email, user_id=ctx.user_id, fields=sorted(cols),
            reverify=reverify)
        return pid.profile_json(row)

    @router.post("/investor/profile/submit", response_model=Dict[str, Any])
    async def submit_profile(body: MpinBody,
                             ctx: OrgContext = Depends(require_investor),
                             conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        row = _profile_row(conn, ctx.org_id, ctx.user_id)
        profile = pid.profile_json(row) if row else pid.empty_profile(ctx.user_id)
        if profile["status"] not in ("draft", "rejected"):
            raise HTTPException(status_code=409,
                                detail=f"your profile is already {profile['status']}")
        if profile["missing"]:
            return JSONResponse(status_code=400, content={
                "detail": "complete your profile first: " + ", ".join(profile["missing"]),
                "missing": profile["missing"]})
        row = conn.execute(
            "UPDATE kyc_profiles SET status = 'submitted', submitted_at = now(), "
            "decided_by = NULL, decided_at = NULL, decision_note = NULL, updated_at = now() "
            "WHERE org_id = %s AND user_id = %s AND status = %s "
            f"RETURNING {pid.PROFILE_COLS}",
            (ctx.org_id, ctx.user_id, profile["status"])).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="your profile changed; reload it")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_kyc_submitted",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            summary=f"Identity verification submitted by {ctx.user_email}")
        return pid.profile_json(row)

    return router
```

`ApiConfig`, `Request`, `List` and `Optional` stay unused until Tasks 6–9; the Python side has no unused-import gate.

- [ ] **Step 4: Mount the router**

In `api/src/api/main.py` replace lines 226–229:

```python
    from .routes.portal_admin import create_portal_admin_router
    from .routes.portal_identity import create_portal_identity_router
    from .routes.portal_investor import create_portal_investor_router
    app.include_router(create_portal_investor_router())
    app.include_router(create_portal_admin_router())
    app.include_router(create_portal_identity_router())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_kyc.py tests/test_uploads.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_identity.py api/src/api/main.py api/tests/test_portal_kyc.py
git commit -m "feat(api): KYC profile, investor side -- save in parts, submit with the MPIN, re-verify on identity edits

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 6: KYC, admin side; `kyc_status` on the investor summary and the investors list

**Files:**
- Modify: `api/src/api/routes/portal_identity.py` (insert before `return router`)
- Modify: `api/src/api/routes/portal_investor.py` (`investor_summary`)
- Modify: `api/src/api/routes/portal_admin.py` (`list_investors`)
- Modify: `api/tests/test_portal_kyc.py` (append)

**Interfaces:**
- Consumes: `pid.kyc_status`, `pid.profile_json`, `pid.PROFILE_COLS`; `pc.Decision`, `pc.require_note_on_reject`, `pc.qualify`, `pc.notify_investor`.
- Produces: `GET kyc?status=`, `POST kyc/{user_id}/decision`; `investor/summary` key `kyc_status`; `investors` row key `kyc_status`; audit `investor_kyc_decided` (info); email subject `Your identity verification was <status>`.

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_kyc.py`:

```python
# ------------------------------------------------------------ admin


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject, text))
        return True


def _decide(client, org_id, user_id, **body):
    return client.post(f"/api/orgs/{org_id}/kyc/{user_id}/decision", json=body,
                       headers=csrf(client))


def test_the_queue_lists_submitted_first_with_who(portal, db, make_user, login_as):
    client, org_id, investor = portal
    other = make_user(email="other@example.com", display_name="Other")
    member(db, org_id, other["id"], "investor")
    kyc_profile(db, org_id, other["id"], status="approved")
    kyc_profile(db, org_id, investor["id"], status="submitted")
    client.cookies.clear()
    login_as(client, ADMIN)
    rows = client.get(f"/api/orgs/{org_id}/kyc").json()
    assert [r["user_id"] for r in rows] == [investor["id"], other["id"]]
    assert rows[0]["email"] == "inv@example.com" and rows[0]["display_name"] == "Inv One"
    assert rows[0]["full_name"] == COMPLETE_PROFILE["full_name"]
    assert [r["user_id"] for r in client.get(f"/api/orgs/{org_id}/kyc?status=approved").json()] \
        == [other["id"]]


def test_an_admin_approves_and_the_investor_is_emailed(portal, db, login_as, monkeypatch):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="submitted")
    client.cookies.clear()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = _decide(client, org_id, investor["id"], status="approved")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and body["decided_by"] is not None and body["decided_at"]
    assert fake.sent[0][:2] == ("inv@example.com", "Your identity verification was approved")
    severity, payload, actor = _events(db, org_id, "investor_kyc_decided")[-1]
    assert severity == "info" and payload["user_id"] == investor["id"]
    assert payload["status"] == "approved" and actor == "admin@example.com"
    r = _decide(client, org_id, investor["id"], status="rejected", note="late")
    assert r.status_code == 409 and r.json()["detail"] == "profile is approved, not submitted"


def test_a_rejection_needs_a_note_and_bad_input_is_refused(portal, db, login_as):
    client, org_id, investor = portal
    kyc_profile(db, org_id, investor["id"], status="submitted")
    client.cookies.clear()
    login_as(client, ADMIN)
    r = _decide(client, org_id, investor["id"], status="rejected")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = _decide(client, org_id, investor["id"], status="maybe")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved or rejected"
    r = _decide(client, org_id, 9999, status="approved")
    assert r.status_code == 404 and r.json()["detail"] == "Profile not found"
    r = _decide(client, org_id, investor["id"], status="rejected", note="ID photo is blurred")
    assert r.status_code == 200 and r.json()["decision_note"] == "ID photo is blurred"
    assert _status(db, org_id, investor["id"]) == "rejected"


def test_kyc_status_rides_on_the_summary_and_the_investors_list(portal, db, login_as):
    client, org_id, investor = portal
    assert client.get(f"/api/orgs/{org_id}/investor/summary").json()["kyc_status"] == "draft"
    kyc_profile(db, org_id, investor["id"], status="approved")
    assert client.get(f"/api/orgs/{org_id}/investor/summary").json()["kyc_status"] == "approved"
    client.cookies.clear()
    login_as(client, ADMIN)
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert row["kyc_status"] == "approved"


def test_the_admin_kyc_routes_refuse_investors_and_viewers(portal, db, make_user, login_as):
    client, org_id, investor = portal
    assert client.get(f"/api/orgs/{org_id}/kyc").status_code == 403
    assert _decide(client, org_id, investor["id"], status="approved").status_code == 403
    viewer = make_user(email="v@example.com")
    member(db, org_id, viewer["id"], "viewer")
    client.cookies.clear()
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/kyc").status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_kyc.py -q -p no:cacheprovider`
Expected: FAIL — `kyc` routes 404; `kyc_status` KeyError.

- [ ] **Step 3: Admin KYC routes**

In `api/src/api/routes/portal_identity.py` insert before `return router`:

```python
    # ------------------------------------------------------------ KYC, admin

    @router.get("/kyc", response_model=List[Dict[str, Any]])
    async def kyc_queue(status: Optional[str] = None,
                        ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "k.org_id = %s" + (" AND k.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: LIMIT 500, paginate when a workspace has that many profiles
        rows = conn.execute(
            f"SELECT {pc.qualify(pid.PROFILE_COLS, 'k')}, u.email, u.display_name "
            "FROM kyc_profiles k JOIN users u ON u.id = k.user_id "
            f"WHERE {where} ORDER BY (k.status = 'submitted') DESC, "
            "k.submitted_at DESC NULLS LAST, k.updated_at DESC LIMIT 500", params).fetchall()
        return [pid.profile_json(r) for r in rows]

    @router.post("/kyc/{user_id}/decision", response_model=Dict[str, Any])
    async def decide_kyc(user_id: int, body: pc.Decision, http_request: Request,
                         ctx: OrgContext = Depends(require_org_role("admin")),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="status must be approved or rejected")
        try:
            note = pc.require_note_on_reject(new_status, body.note)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
            (ctx.org_id, user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Profile not found")
        if current[0] != "submitted":
            raise HTTPException(status_code=409, detail=f"profile is {current[0]}, not submitted")
        row = conn.execute(
            "UPDATE kyc_profiles SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s, updated_at = now() "
            "WHERE org_id = %s AND user_id = %s AND status = 'submitted' "
            f"RETURNING {pid.PROFILE_COLS}",
            (new_status, ctx.user_id, note, ctx.org_id, user_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_kyc_decided",
            actor_email=ctx.user_email, user_id=user_id, status=new_status, note=note)
        await pc.notify_investor(
            conn, http_request, user_id,
            f"Your identity verification was {new_status}",
            f"Status: {new_status}\nNote: {note or '—'}\n\nOpen the portal for details.")
        return pid.profile_json(row)
```

- [ ] **Step 4: `kyc_status` on the investor summary**

In `api/src/api/routes/portal_investor.py` add `from .. import portal_identity as pid` under `from .. import portal_common as pc`, and in `investor_summary`'s returned dict add, after `"open_positions": ...`:

```python
            "kyc_status": pid.kyc_status(conn, ctx.org_id, ctx.user_id),
```

- [ ] **Step 5: `kyc_status` on the investors list**

In `api/src/api/routes/portal_admin.py` `list_investors`, replace the query and the loop header:

```python
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, m.created_at, a.ctid_trader_account_id,
                      a.nickname, COALESCE(k.status, 'draft')
               FROM org_memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN accounts a ON a.org_id = m.org_id AND a.investor_user_id = u.id
               LEFT JOIN kyc_profiles k ON k.org_id = m.org_id AND k.user_id = u.id
               WHERE m.org_id = %s AND m.role = 'investor'
               ORDER BY u.display_name, u.id""", (ctx.org_id,)).fetchall()
        # One /state round trip for the whole list, never one per investor.
        state = await _org_state(http_request.app.state.http, cfg, ctx.org_id)
        out = []
        for user_id, email, name, joined_at, account_id, nickname, kyc in rows:
```

and add `"kyc_status": kyc,` to the appended dict, after `"pending": pending_counts(...)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_kyc.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/src/api/routes/portal_identity.py api/src/api/routes/portal_investor.py api/src/api/routes/portal_admin.py api/tests/test_portal_kyc.py
git commit -m "feat(api): KYC, admin side -- queue and decision with email; kyc_status on summary and investors

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 7: Account packages — admin CRUD, investor list

**Files:**
- Modify: `api/src/api/routes/portal_identity.py` (imports; bodies after `MpinBody`; routes before `return router`)
- Create: `api/tests/test_portal_packages.py`

**Interfaces:**
- Consumes: `pid.PACKAGE_COLS`, `pid.package_json`, `pid.parse_leverage_options`; `routes.portal_admin.parse_min`; `portal_helpers.add_package`, `open_account_request`.
- Produces: `PackageBody`, `PackagePatch`; `GET/POST account-packages`, `PATCH/DELETE account-packages/{package_id}`, `GET investor/account-packages`; audit `account_package_changed` (info) with `package_id`, `change`, `name`.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_packages.py`:

```python
# api/tests/test_portal_packages.py
"""Account packages: the admin defines what an investor may request
(name, minimum deposit, spread label, leverage choices); investors list
the enabled ones in display order."""
import psycopg
import pytest

from portal_helpers import add_package, csrf, member, open_account_request


def _post(client, org_id, **over):
    body = {"name": "Standard", "min_deposit": "100", "spread_label": "20-25",
            "leverage_options": [500, 100, 200], **over}
    return client.post(f"/api/orgs/{org_id}/account-packages", json=body, headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return [r[0] for r in conn.execute(
            "SELECT payload FROM events WHERE org_id = %s "
            "AND payload->>'action' = 'account_package_changed' ORDER BY id", (org_id,)).fetchall()]


def test_an_admin_creates_lists_edits_and_disables_a_package(org_client, db):
    client, org_id, _seed = org_client
    r = _post(client, org_id)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created == {"id": created["id"], "name": "Standard", "min_deposit": 100.0,
                       "currency": "USD", "spread_label": "20-25",
                       "leverage_options": [100, 200, 500], "enabled": True, "sort_order": 0}
    assert client.get(f"/api/orgs/{org_id}/account-packages").json() == [created]
    r = client.patch(f"/api/orgs/{org_id}/account-packages/{created['id']}",
                     json={"name": "Pro", "spread_label": "", "enabled": False},
                     headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["name"] == "Pro" and r.json()["spread_label"] is None
    assert r.json()["enabled"] is False
    changes = [(p["change"], p["name"]) for p in _events(db, org_id)]
    assert changes == [("created", "Standard"), ("updated", "Pro")]


@pytest.mark.parametrize("over,detail", [
    ({"name": " "}, "name is required"),
    ({"name": "x" * 65}, "name must be at most 64 characters"),
    ({"min_deposit": "-5"}, "min_deposit must be greater than 0"),
    ({"leverage_options": []}, "leverage_options must be a list of whole numbers from 1 to 3000"),
    ({"leverage_options": ["100"]}, "leverage_options must be a list of whole numbers from 1 to 3000"),
    ({"spread_label": "x" * 33}, "spread_label must be at most 32 characters"),
])
def test_a_bad_package_is_refused(org_client, over, detail):
    client, org_id, _seed = org_client
    r = _post(client, org_id, **over)
    assert r.status_code == 400 and r.json()["detail"] == detail


def test_a_zero_minimum_is_allowed(org_client):
    client, org_id, _seed = org_client
    assert _post(client, org_id, min_deposit="0").json()["min_deposit"] == 0.0


def test_unknown_packages_are_404(org_client):
    client, org_id, _seed = org_client
    r = client.patch(f"/api/orgs/{org_id}/account-packages/999", json={"name": "x"},
                     headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Package not found"
    r = client.delete(f"/api/orgs/{org_id}/account-packages/999", headers=csrf(client))
    assert r.status_code == 404


def test_delete_waits_for_open_requests(org_client, make_user, db):
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    package_id = add_package(db, org_id)
    req_id = open_account_request(db, org_id, investor["id"], package_id)
    r = client.delete(f"/api/orgs/{org_id}/account-packages/{package_id}", headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "an open request still uses this package"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (req_id,))
    r = client.delete(f"/api/orgs/{org_id}/account-packages/{package_id}", headers=csrf(client))
    assert r.status_code == 204
    assert _events(db, org_id)[-1]["change"] == "deleted"


def test_investors_see_only_enabled_packages_in_order(org_client, make_user, login_as, db):
    client, org_id, _seed = org_client
    add_package(db, org_id, name="Later", sort_order=2)
    add_package(db, org_id, name="Hidden", enabled=False)
    add_package(db, org_id, name="First", sort_order=1)
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    assert client.get(f"/api/orgs/{org_id}/investor/account-packages").status_code == 403
    client.cookies.clear()
    login_as(client, investor)
    names = [p["name"] for p in client.get(f"/api/orgs/{org_id}/investor/account-packages").json()]
    assert names == ["First", "Later"]
    assert client.get(f"/api/orgs/{org_id}/account-packages").status_code == 403
    assert _post(client, org_id).status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_packages.py -q -p no:cacheprovider`
Expected: FAIL — the package routes answer 404/405.

- [ ] **Step 3: Bodies and imports**

In `api/src/api/routes/portal_identity.py` change the responses import and add the `parse_min` import:

```python
from fastapi.responses import JSONResponse, Response
```

```python
from .portal_admin import parse_min
```

Add after `class MpinBody`:

```python
class PackageBody(BaseModel):
    name: str
    min_deposit: Any = "0"
    currency: str = "USD"
    spread_label: Optional[str] = None
    leverage_options: Any = None
    enabled: bool = True
    sort_order: int = 0


class PackagePatch(BaseModel):
    name: Optional[str] = None
    min_deposit: Any = None
    currency: Optional[str] = None
    spread_label: Optional[str] = None   # "" clears it
    leverage_options: Any = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None
```

- [ ] **Step 4: The package routes**

Insert before `return router`:

```python
    # ------------------------------------------------------------ account packages

    def _package_row(conn: psycopg.Connection, org_id: int, package_id: int):
        row = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE id = %s AND org_id = %s",
            (package_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Package not found")
        return row

    async def _audit_package(conn: psycopg.Connection, ctx: OrgContext, package_id: int,
                             change: str, name: str) -> None:
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="account_package_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, package_id=package_id,
            change=change, name=name,
            summary=f"Account package {change}: {name} by {ctx.user_email}")

    @router.get("/account-packages", response_model=List[Dict[str, Any]])
    async def list_packages(ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE org_id = %s "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pid.package_json(r) for r in rows]

    @router.post("/account-packages", status_code=201, response_model=Dict[str, Any])
    async def create_package(body: PackageBody,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            name = pc.clean_text(body.name, "name", max_len=64)
            min_deposit = parse_min(body.min_deposit, "min_deposit")
            currency = (pc.clean_text(body.currency, "currency", max_len=8,
                                      required=False) or "USD").upper()
            spread = pc.clean_text(body.spread_label, "spread_label", max_len=32, required=False)
            leverage = pid.parse_leverage_options(body.leverage_options)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = conn.execute(
            "INSERT INTO account_packages (org_id, name, min_deposit, currency, spread_label, "
            "leverage_options, enabled, sort_order) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {pid.PACKAGE_COLS}",
            (ctx.org_id, name, min_deposit, currency, spread, leverage, body.enabled,
             body.sort_order)).fetchone()
        out = pid.package_json(row)
        await _audit_package(conn, ctx, out["id"], "created", name)
        return out

    @router.patch("/account-packages/{package_id}", response_model=Dict[str, Any])
    async def update_package(package_id: int, body: PackagePatch,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _package_row(conn, ctx.org_id, package_id)
        sets: list[str] = []
        params: list[Any] = []
        try:
            if body.name is not None:
                sets.append("name = %s")
                params.append(pc.clean_text(body.name, "name", max_len=64))
            if body.min_deposit is not None:
                sets.append("min_deposit = %s")
                params.append(parse_min(body.min_deposit, "min_deposit"))
            if body.currency is not None:
                sets.append("currency = %s")
                params.append((pc.clean_text(body.currency, "currency", max_len=8,
                                             required=False) or "USD").upper())
            if body.spread_label is not None:
                sets.append("spread_label = %s")
                params.append(pc.clean_text(body.spread_label, "spread_label", max_len=32,
                                            required=False))
            if body.leverage_options is not None:
                sets.append("leverage_options = %s")
                params.append(pid.parse_leverage_options(body.leverage_options))
            if body.enabled is not None:
                sets.append("enabled = %s")
                params.append(bool(body.enabled))
            if body.sort_order is not None:
                sets.append("sort_order = %s")
                params.append(int(body.sort_order))
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not sets:
            return pid.package_json(current)
        sets.append("updated_at = now()")
        row = conn.execute(
            f"UPDATE account_packages SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
            f"RETURNING {pid.PACKAGE_COLS}", (*params, package_id, ctx.org_id)).fetchone()
        out = pid.package_json(row)
        await _audit_package(conn, ctx, package_id, "updated", out["name"])
        return out

    @router.delete("/account-packages/{package_id}", status_code=204)
    async def delete_package(package_id: int,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)):
        current = _package_row(conn, ctx.org_id, package_id)
        if conn.execute("SELECT 1 FROM account_requests WHERE package_id = %s "
                        "AND status = 'requested'", (package_id,)).fetchone():
            # Decided requests keep their package_name snapshot and lose
            # only the id (ON DELETE SET NULL); an open one still needs it.
            raise HTTPException(status_code=409, detail="an open request still uses this package")
        conn.execute("DELETE FROM account_packages WHERE id = %s AND org_id = %s",
                     (package_id, ctx.org_id))
        await _audit_package(conn, ctx, package_id, "deleted", current[1])
        return Response(status_code=204)

    @router.get("/investor/account-packages", response_model=List[Dict[str, Any]])
    async def my_packages(ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE org_id = %s AND enabled "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pid.package_json(r) for r in rows]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_packages.py tests/test_portal_kyc.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_identity.py api/tests/test_portal_packages.py
git commit -m "feat(api): account packages -- admin CRUD with audit, investors list the enabled ones

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 8: Account requests, investor side

**Files:**
- Modify: `api/src/api/routes/portal_identity.py` (body after `PackagePatch`; routes before `return router`)
- Create: `api/tests/test_portal_account_requests.py`

**Interfaces:**
- Consumes: `pid.kyc_status`, `pid.check_mt5_password`, `pid.seal`, `pid.REQUEST_COLS`, `pid.request_json`, `pid.package_json`; `pc.linked_account`; `ApiConfig.fernet_key`; `portal_helpers.kyc_profile`, `add_package`, `link`.
- Produces: `AccountRequestBody`; `GET/POST investor/account-requests`, `POST investor/account-requests/{req_id}/cancel`; audits `investor_account_requested` (warning), `investor_account_request_cancelled` (info).

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_account_requests.py`:

```python
# api/tests/test_portal_account_requests.py
"""Live account requests: a verified investor with no trading account asks
for one from a package, with two MT5 passwords sealed until an admin acts;
the admin reveals them, then fulfils (login, server, optional link) or
rejects (Task 9). Decisions wipe the passwords and email the investor."""
import os

import psycopg
import pytest

from portal_helpers import (add_package, csrf, kyc_profile, link, member,
                            open_account_request)

from api import portal_identity as pid
from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


@pytest.fixture
def portal(org_client, make_user, login_as, db):
    """A verified investor logged in and one 1:100/200/500 package.
    Returns (client, org_id, investor, package_id, seed)."""
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    kyc_profile(db, org_id, investor["id"], status="approved")
    package_id = add_package(db, org_id)
    login_as(client, investor)
    return client, org_id, investor, package_id, seed


def _request(client, org_id, default_package, **over):
    # Not named package_id: an over["package_id"] (the 404 case) would
    # collide with it ("got multiple values for argument").
    body = {"package_id": default_package, "leverage": 200, "main_password": "Main1234",
            "investor_password": "Look1234", "mpin": "123456", **over}
    return client.post(f"/api/orgs/{org_id}/investor/account-requests", json=body,
                       headers=csrf(client))


def _sealed(db, req_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT main_password_enc, investor_password_enc FROM account_requests WHERE id = %s",
            (req_id,)).fetchone()


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


# ------------------------------------------------------------ investor


def test_a_verified_investor_requests_an_account(portal, db):
    client, org_id, investor, package_id, _ = portal
    r = _request(client, org_id, package_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "requested" and body["package_name"] == "Standard"
    assert body["leverage"] == 200 and body["user_id"] == investor["id"]
    assert "Main1234" not in r.text and "password" not in r.text
    main_enc, inv_enc = _sealed(db, body["id"])
    assert main_enc != "Main1234"
    assert pid.unseal(os.environ["FERNET_KEY"], main_enc) == "Main1234"
    assert pid.unseal(os.environ["FERNET_KEY"], inv_enc) == "Look1234"
    severity, payload = _events(db, org_id, "investor_account_requested")[-1]
    assert severity == "warning" and payload["user_id"] == investor["id"]
    assert payload["request_id"] == body["id"] and "Main1234" not in str(payload)
    listed = client.get(f"/api/orgs/{org_id}/investor/account-requests").json()
    assert [r["id"] for r in listed] == [body["id"]] and "password" not in str(listed)


def test_the_mpin_comes_first(portal):
    client, org_id, _, package_id, _ = portal
    r = _request(client, org_id, package_id, mpin=None, leverage=7)
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"


def test_an_unverified_investor_is_sent_to_verify(portal, db):
    client, org_id, investor, package_id, _ = portal
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE kyc_profiles SET status = 'submitted' WHERE user_id = %s",
                     (investor["id"],))
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "verify your identity first"


def test_an_investor_with_an_account_or_an_open_request_is_refused(portal, db):
    client, org_id, investor, package_id, seed = portal
    assert _request(client, org_id, package_id).status_code == 201
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "a request is already open"
    seed(1001, role="slave")
    link(db, org_id, investor["id"], 1001)
    r = _request(client, org_id, package_id)
    assert r.status_code == 409 and r.json()["detail"] == "you already have a trading account"


@pytest.mark.parametrize("over,status,detail", [
    ({"package_id": 999}, 404, "Package not found"),
    ({"leverage": 300}, 400, "leverage must be one of 100, 200, 500"),
    ({"leverage": "200"}, 400, "leverage must be one of 100, 200, 500"),
    ({"main_password": "short1A"}, 400,
     "main_password must be 8-32 characters without spaces, with an upper-case letter, "
     "a lower-case letter and a digit"),
    ({"investor_password": "nouppercase1"}, 400,
     "investor_password must be 8-32 characters without spaces, with an upper-case letter, "
     "a lower-case letter and a digit"),
    ({"investor_password": "Main1234"}, 400,
     "the investor password must differ from the main password"),
])
def test_a_bad_request_is_refused(portal, over, status, detail):
    client, org_id, _, package_id, _ = portal
    r = _request(client, org_id, package_id, **over)
    assert r.status_code == status and r.json()["detail"] == detail


def test_a_disabled_package_cannot_be_requested(portal, db):
    client, org_id, _, _, _ = portal
    hidden = add_package(db, org_id, name="Hidden", enabled=False)
    r = _request(client, org_id, hidden)
    assert r.status_code == 404


def test_the_investor_cancels_and_the_passwords_go(portal, db, make_user):
    client, org_id, investor, package_id, _ = portal
    req_id = _request(client, org_id, package_id).json()["id"]
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests/{req_id}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _sealed(db, req_id) == (None, None)
    assert _events(db, org_id, "investor_account_request_cancelled")[-1][1]["request_id"] == req_id
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests/{req_id}/cancel",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "request is already cancelled"
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    theirs = open_account_request(db, org_id, other["id"], package_id)
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests/{theirs}/cancel",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Request not found"
    # A new request after the cancel is fine.
    assert _request(client, org_id, package_id).status_code == 201


def test_desk_members_cannot_use_the_investor_routes(portal, login_as):
    client, org_id, _, package_id, _ = portal
    client.cookies.clear()
    login_as(client, ADMIN)
    assert _request(client, org_id, package_id).status_code == 403
    assert client.get(f"/api/orgs/{org_id}/investor/account-requests").status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_account_requests.py -q -p no:cacheprovider`
Expected: FAIL — `investor/account-requests` answers 404.

- [ ] **Step 3: The body**

In `api/src/api/routes/portal_identity.py` add after `class PackagePatch`:

```python
class AccountRequestBody(BaseModel):
    package_id: int
    leverage: Any = None
    main_password: Any = None
    investor_password: Any = None
    mpin: Any = None
```

- [ ] **Step 4: The investor routes**

Insert before `return router`:

```python
    # ------------------------------------------------------------ account requests, investor

    @router.get("/investor/account-requests", response_model=List[Dict[str, Any]])
    async def my_account_requests(ctx: OrgContext = Depends(require_investor),
                                  conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pid.REQUEST_COLS} FROM account_requests WHERE org_id = %s AND user_id = %s "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [pid.request_json(r) for r in rows]

    @router.post("/investor/account-requests", status_code=201, response_model=Dict[str, Any])
    async def request_account(body: AccountRequestBody,
                              ctx: OrgContext = Depends(require_investor),
                              conn: psycopg.Connection = Depends(get_conn),
                              cfg: ApiConfig = Depends(ApiConfig.from_env)):
        """A live MT5 account from one package. KYC must be approved, the
        investor has no linked account (phase 1 links one per investor) and
        no other open request. The two passwords are sealed with FERNET_KEY
        and live only until an admin decides."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        if pid.kyc_status(conn, ctx.org_id, ctx.user_id) != "approved":
            raise HTTPException(status_code=409, detail="verify your identity first")
        if pc.linked_account(conn, ctx.org_id, ctx.user_id) is not None:
            raise HTTPException(status_code=409, detail="you already have a trading account")
        if conn.execute("SELECT 1 FROM account_requests WHERE org_id = %s AND user_id = %s "
                        "AND status = 'requested'", (ctx.org_id, ctx.user_id)).fetchone():
            raise HTTPException(status_code=409, detail="a request is already open")
        row = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE id = %s AND org_id = %s "
            "AND enabled", (body.package_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Package not found")
        package = pid.package_json(row)
        options = package["leverage_options"]
        leverage = body.leverage
        if isinstance(leverage, bool) or not isinstance(leverage, int) or leverage not in options:
            raise HTTPException(status_code=400, detail="leverage must be one of "
                                + ", ".join(str(o) for o in options))
        try:
            main = pid.check_mt5_password(body.main_password, "main_password")
            investor = pid.check_mt5_password(body.investor_password, "investor_password")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if main == investor:
            # MT5 itself refuses an investor (read-only) password equal to
            # the main one; better to say so before the admin finds out.
            raise HTTPException(status_code=400,
                                detail="the investor password must differ from the main password")
        try:
            row = conn.execute(
                "INSERT INTO account_requests (org_id, user_id, package_id, package_name, "
                "leverage, main_password_enc, investor_password_enc) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {pid.REQUEST_COLS}",
                (ctx.org_id, ctx.user_id, package["id"], package["name"], leverage,
                 pid.seal(cfg.fernet_key, main), pid.seal(cfg.fernet_key, investor))).fetchone()
        except psycopg.errors.UniqueViolation:
            # A second request raced the check above (account_requests_one_open).
            raise HTTPException(status_code=409, detail="a request is already open")
        out = pid.request_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_requested",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            request_id=out["id"], package_name=package["name"], leverage=leverage,
            summary=f"Trading account requested: {package['name']} 1:{leverage} "
                    f"by {ctx.user_email}")
        return out

    @router.post("/investor/account-requests/{req_id}/cancel", response_model=Dict[str, Any])
    async def cancel_account_request(req_id: int,
                                     ctx: OrgContext = Depends(require_investor),
                                     conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status FROM account_requests WHERE id = %s AND org_id = %s AND user_id = %s",
            (req_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Request not found")
        if current[0] != "requested":
            raise HTTPException(status_code=409, detail=f"request is already {current[0]}")
        row = conn.execute(
            "UPDATE account_requests SET status = 'cancelled', decided_by = %s, "
            "decided_at = now(), main_password_enc = NULL, investor_password_enc = NULL "
            "WHERE id = %s AND org_id = %s AND status = 'requested' "
            f"RETURNING {pid.REQUEST_COLS}", (ctx.user_id, req_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_request_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id, request_id=req_id)
        return pid.request_json(row)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_account_requests.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/routes/portal_identity.py api/tests/test_portal_account_requests.py
git commit -m "feat(api): account requests, investor side -- KYC gate, one at a time, sealed MT5 passwords, cancel

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 9: Account requests, admin side; the requests summary; the alert rules

**Files:**
- Modify: `api/src/api/routes/portal_identity.py` (imports; bodies; module helpers `_open_request`, `_link_account`; routes before `return router`)
- Modify: `api/src/api/routes/portal_admin.py` (`requests_summary`)
- Modify: `api/src/api/alerts.py` (`ALERT_RULES`), `api/src/api/telegram.py` (`TELEGRAM_RULES`)
- Modify: `api/tests/test_portal_account_requests.py` (append), `api/tests/test_portal_summary.py:478-479, 500-501`

**Interfaces:**
- Consumes: Task 8's routes and fixtures; `pid.unseal`; `cryptography.fernet.InvalidToken`; `pc.linked_account`, `pc.qualify`, `pc.notify_investor`.
- Produces: `FulfilBody`, `RejectBody`; `GET account-requests?status=`, `POST account-requests/{req_id}/reveal|fulfil|reject`; `requests/summary` keys `kyc`, `account_requests`; audits `account_request_passwords_revealed` (warning), `investor_account_request_decided` (info), `investor_account_linked` (info); three alert rules.

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_portal_account_requests.py`:

```python
# ------------------------------------------------------------ admin

from cryptography.fernet import Fernet

from api.alerts import ALERT_RULES
from api.telegram import TELEGRAM_RULES


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject, text))
        return True


@pytest.fixture
def desk(portal, login_as):
    """Task 8's portal with one open request filed through the API and the
    admin logged in. Returns (client, org_id, investor, package_id, seed, req_id)."""
    client, org_id, investor, package_id, seed = portal
    req_id = _request(client, org_id, package_id).json()["id"]
    client.cookies.clear()
    login_as(client, ADMIN)
    return client, org_id, investor, package_id, seed, req_id


def _act(client, org_id, req_id, verb, **body):
    return client.post(f"/api/orgs/{org_id}/account-requests/{req_id}/{verb}", json=body,
                       headers=csrf(client))


def test_the_admin_queue_lists_open_requests_first_without_passwords(desk, db, make_user):
    client, org_id, investor, package_id, _, req_id = desk
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    older = open_account_request(db, org_id, other["id"], package_id)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (older,))
    rows = client.get(f"/api/orgs/{org_id}/account-requests").json()
    assert [r["id"] for r in rows] == [req_id, older]
    assert rows[0]["email"] == "inv@example.com" and rows[0]["display_name"] == "Inv One"
    assert "password" not in str(rows)
    assert [r["id"] for r in client.get(
        f"/api/orgs/{org_id}/account-requests?status=cancelled").json()] == [older]


def test_reveal_needs_the_admins_mpin_and_is_audited_every_time(desk, db):
    client, org_id, investor, _, _, req_id = desk
    r = _act(client, org_id, req_id, "reveal", mpin="000000")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid MPIN"
    for _ in range(2):
        r = _act(client, org_id, req_id, "reveal", mpin="123456")
        assert r.status_code == 200
        assert r.json() == {"main_password": "Main1234", "investor_password": "Look1234"}
    rows = _events(db, org_id, "account_request_passwords_revealed")
    assert len(rows) == 2
    severity, payload = rows[-1]
    assert severity == "warning" and payload["user_id"] == investor["id"]
    assert payload["request_id"] == req_id and "Main1234" not in str(payload)
    r = _act(client, org_id, 999, "reveal", mpin="123456")
    assert r.status_code == 404 and r.json()["detail"] == "Request not found"


def test_reveal_after_a_key_change_says_what_to_do(desk, db):
    client, org_id, _, _, _, req_id = desk
    foreign = Fernet(Fernet.generate_key())
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET main_password_enc = %s WHERE id = %s",
                     (foreign.encrypt(b"Main1234").decode(), req_id))
    r = _act(client, org_id, req_id, "reveal", mpin="123456")
    assert r.status_code == 409
    assert r.json()["detail"] == ("the passwords can no longer be read; "
                                  "reject this request and ask for a new one")


def test_fulfil_hands_over_the_login_wipes_the_passwords_and_emails(desk, db, monkeypatch):
    client, org_id, investor, _, _, req_id = desk
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=0, mt5_server="Broker-Live")
    assert r.status_code == 400 and r.json()["detail"] == "mt5_login must be a whole number above zero"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001)
    assert r.status_code == 400 and r.json()["detail"] == "mt5_server is required"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server=" Broker-Live ",
             note="opened")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "fulfilled" and body["mt5_login"] == 5001
    assert body["mt5_server"] == "Broker-Live" and body["account_id"] is None
    assert body["decision_note"] == "opened" and body["decided_by"] is not None
    assert _sealed(db, req_id) == (None, None)
    to, subject, text = fake.sent[-1]
    assert (to, subject) == ("inv@example.com", "Your trading account is ready")
    assert "Login: 5001" in text and "Server: Broker-Live" in text and "Main1234" not in text
    payload = _events(db, org_id, "investor_account_request_decided")[-1][1]
    assert payload["status"] == "fulfilled" and payload["user_id"] == investor["id"]
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="Broker-Live")
    assert r.status_code == 409 and r.json()["detail"] == "request is already fulfilled"
    r = _act(client, org_id, req_id, "reveal", mpin="123456")
    assert r.status_code == 409


def test_fulfil_can_link_an_account_under_the_phase_1_rules(desk, db, make_user):
    client, org_id, investor, package_id, seed, req_id = desk
    seed(100, role="master")
    seed(1001, role="slave")
    seed(1002, role="slave")
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    link(db, org_id, other["id"], 1002)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=100)
    assert r.status_code == 400
    assert r.json()["detail"] == "The master account cannot be linked to an investor"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=1002)
    assert r.status_code == 404
    assert r.json()["detail"] == "Account not found in this workspace, or already linked"
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=1001)
    assert r.status_code == 200 and r.json()["account_id"] == 1001
    with psycopg.connect(db, autocommit=True) as conn:
        (owner,) = conn.execute("SELECT investor_user_id FROM accounts "
                                "WHERE ctid_trader_account_id = 1001").fetchone()
    assert owner == investor["id"]
    assert _events(db, org_id, "investor_account_linked")[-1][1]["account_id"] == 1001


def test_fulfil_refuses_a_second_account_for_a_linked_investor(desk, db):
    client, org_id, investor, _, seed, req_id = desk
    seed(1001, role="slave")
    seed(1003, role="slave")
    link(db, org_id, investor["id"], 1003)
    r = _act(client, org_id, req_id, "fulfil", mt5_login=5001, mt5_server="B", account_id=1001)
    assert r.status_code == 409 and r.json()["detail"] == "the investor already has a linked account"
    with psycopg.connect(db, autocommit=True) as conn:
        (status,) = conn.execute("SELECT status FROM account_requests WHERE id = %s",
                                 (req_id,)).fetchone()
    assert status == "requested"


def test_reject_needs_a_note_wipes_and_lets_the_investor_try_again(desk, db, login_as,
                                                                   monkeypatch):
    client, org_id, investor, package_id, _, req_id = desk
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = _act(client, org_id, req_id, "reject")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = _act(client, org_id, req_id, "reject", note="Broker paused new accounts")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _sealed(db, req_id) == (None, None)
    assert fake.sent[-1][1] == "Your trading account request was rejected"
    assert "Broker paused new accounts" in fake.sent[-1][2]
    client.cookies.clear()
    login_as(client, investor)
    assert _request(client, org_id, package_id).status_code == 201


def test_the_requests_summary_counts_verifications_and_account_requests(desk, db, make_user):
    client, org_id, _, _, _, _ = desk
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    kyc_profile(db, org_id, other["id"], status="submitted")
    summary = client.get(f"/api/orgs/{org_id}/requests/summary").json()
    assert summary["kyc"] == 1 and summary["account_requests"] == 1
    assert summary["total"] == 2


def test_the_three_phase_2_warnings_reach_both_alerters():
    for action in ("investor_kyc_submitted", "investor_account_requested",
                   "account_request_passwords_revealed"):
        assert ("control", "warning", action) in ALERT_RULES, action
        assert ("control", "warning", action) in TELEGRAM_RULES, action
```

In `api/tests/test_portal_summary.py` the two exact `requests/summary` assertions become:

```python
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 0, "withdrawals": 0, "transfers": 0, "payout_destinations": 0,
        "kyc": 0, "account_requests": 0, "total": 0}
```

```python
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 1, "withdrawals": 2, "transfers": 2, "payout_destinations": 1,
        "kyc": 0, "account_requests": 0, "total": 6}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_account_requests.py tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: FAIL — admin routes 404; summary lacks `kyc`; the rules test fails.

- [ ] **Step 3: Imports, bodies and helpers**

In `api/src/api/routes/portal_identity.py` add `from cryptography.fernet import InvalidToken` to the imports and, after `class AccountRequestBody`:

```python
class FulfilBody(BaseModel):
    mt5_login: Any = None
    mt5_server: Any = None
    account_id: Optional[int] = None
    note: Optional[str] = None


class RejectBody(BaseModel):
    note: Optional[str] = None


def _open_request(conn: psycopg.Connection, org_id: int, req_id: int):
    """(status, user_id, package_name, main_password_enc, investor_password_enc)
    of a request still waiting on an admin; 404 / 409 otherwise."""
    row = conn.execute(
        "SELECT status, user_id, package_name, main_password_enc, investor_password_enc "
        "FROM account_requests WHERE id = %s AND org_id = %s", (req_id, org_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row[0] != "requested":
        raise HTTPException(status_code=409, detail=f"request is already {row[0]}")
    return row


def _link_account(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int) -> None:
    """Phase 1's link rule (routes/portal_admin.link_account), run inside the
    caller's transaction: never the master, never an account linked to
    someone else, and one account per investor."""
    linked = pc.linked_account(conn, org_id, user_id)
    if linked == account_id:
        return
    if linked is not None:
        raise HTTPException(status_code=409, detail="the investor already has a linked account")
    role_row = conn.execute(
        "SELECT role FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
        (account_id, org_id)).fetchone()
    if role_row and role_row[0] == "master":
        raise HTTPException(status_code=400,
                            detail="The master account cannot be linked to an investor")
    updated = conn.execute(
        "UPDATE accounts SET investor_user_id = %s "
        "WHERE ctid_trader_account_id = %s AND org_id = %s AND investor_user_id IS NULL "
        "RETURNING ctid_trader_account_id", (user_id, account_id, org_id)).fetchone()
    if not updated:
        raise HTTPException(status_code=404,
                            detail="Account not found in this workspace, or already linked")
```

- [ ] **Step 4: The admin routes**

Insert before `return router`:

```python
    # ------------------------------------------------------------ account requests, admin

    @router.get("/account-requests", response_model=List[Dict[str, Any]])
    async def account_request_queue(status: Optional[str] = None,
                                    ctx: OrgContext = Depends(require_org_role("admin")),
                                    conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "r.org_id = %s" + (" AND r.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: LIMIT 500, paginate when a workspace has that many requests
        rows = conn.execute(
            f"SELECT {pc.qualify(pid.REQUEST_COLS, 'r')}, u.email, u.display_name "
            "FROM account_requests r JOIN users u ON u.id = r.user_id "
            f"WHERE {where} ORDER BY (r.status = 'requested') DESC, r.created_at DESC, r.id DESC "
            "LIMIT 500", params).fetchall()
        return [pid.request_json(r) for r in rows]

    @router.post("/account-requests/{req_id}/reveal", response_model=Dict[str, Any])
    async def reveal_passwords(req_id: int, body: MpinBody,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn),
                               cfg: ApiConfig = Depends(ApiConfig.from_env)):
        """The two passwords, for the admin to type into the broker's
        manager. The ADMIN's MPIN confirms it and every reveal is a warning
        in the audit feed; once the request is decided they are gone."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        _status, user_id, _name, main_enc, investor_enc = _open_request(conn, ctx.org_id, req_id)
        try:
            if main_enc is None or investor_enc is None:
                raise InvalidToken
            out = {"main_password": pid.unseal(cfg.fernet_key, main_enc),
                   "investor_password": pid.unseal(cfg.fernet_key, investor_enc)}
        except InvalidToken:
            raise HTTPException(status_code=409, detail=(
                "the passwords can no longer be read; reject this request and ask for a new one"))
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="account_request_passwords_revealed",
            actor_email=ctx.user_email, user_id=user_id, severity="warning", request_id=req_id,
            summary=f"Passwords of account request #{req_id} revealed by {ctx.user_email}")
        return out

    @router.post("/account-requests/{req_id}/fulfil", response_model=Dict[str, Any])
    async def fulfil_request(req_id: int, body: FulfilBody, http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        login = body.mt5_login
        if isinstance(login, bool) or not isinstance(login, int) or login <= 0:
            raise HTTPException(status_code=400,
                                detail="mt5_login must be a whole number above zero")
        try:
            server = pc.clean_text(body.mt5_server, "mt5_server", max_len=64)
            note = pc.clean_text(body.note, "note", max_len=500, required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _status, user_id, package_name, _m, _i = _open_request(conn, ctx.org_id, req_id)
        # The link and the status change land together or not at all.
        with conn.transaction():
            if body.account_id is not None:
                _link_account(conn, ctx.org_id, user_id, body.account_id)
            row = conn.execute(
                "UPDATE account_requests SET status = 'fulfilled', mt5_login = %s, "
                "mt5_server = %s, account_id = %s, decided_by = %s, decided_at = now(), "
                "decision_note = %s, main_password_enc = NULL, investor_password_enc = NULL "
                "WHERE id = %s AND org_id = %s AND status = 'requested' "
                f"RETURNING {pid.REQUEST_COLS}",
                (login, server, body.account_id, ctx.user_id, note, req_id,
                 ctx.org_id)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
        out = pid.request_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_request_decided",
            actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id,
            request_id=req_id, status="fulfilled", mt5_login=login, mt5_server=server, note=note)
        if body.account_id is not None:
            await pc.audit_control(
                conn, org_id=ctx.org_id, action="investor_account_linked",
                actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id)
        await pc.notify_investor(
            conn, http_request, user_id, "Your trading account is ready",
            f"Login: {login}\nServer: {server}\nPackage: {package_name}\n"
            "Sign in to MetaTrader 5 with the passwords you chose when you requested it.\n\n"
            "Open the portal for details.")
        return out

    @router.post("/account-requests/{req_id}/reject", response_model=Dict[str, Any])
    async def reject_request(req_id: int, body: RejectBody, http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            note = pc.clean_text(body.note, "note", max_len=500)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _status, user_id, package_name, _m, _i = _open_request(conn, ctx.org_id, req_id)
        row = conn.execute(
            "UPDATE account_requests SET status = 'rejected', decided_by = %s, "
            "decided_at = now(), decision_note = %s, main_password_enc = NULL, "
            "investor_password_enc = NULL WHERE id = %s AND org_id = %s AND status = 'requested' "
            f"RETURNING {pid.REQUEST_COLS}", (ctx.user_id, note, req_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_request_decided",
            actor_email=ctx.user_email, user_id=user_id, request_id=req_id, status="rejected",
            note=note)
        await pc.notify_investor(
            conn, http_request, user_id, "Your trading account request was rejected",
            f"Package: {package_name}\nNote: {note}\n\nOpen the portal for details.")
        return pid.request_json(row)
```

- [ ] **Step 5: Count the new queues in the requests summary**

In `api/src/api/routes/portal_admin.py` replace the body of `requests_summary`:

```python
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM deposits WHERE org_id = %(o)s AND status = 'pending'),
                 (SELECT count(*) FROM withdrawals
                   WHERE org_id = %(o)s AND status IN ('requested', 'approved')),
                 (SELECT count(*) FROM transfers
                   WHERE org_id = %(o)s AND status IN ('requested', 'approved')),
                 (SELECT count(*) FROM payout_destinations
                   WHERE org_id = %(o)s AND status = 'pending'),
                 (SELECT count(*) FROM kyc_profiles WHERE org_id = %(o)s AND status = 'submitted'),
                 (SELECT count(*) FROM account_requests
                   WHERE org_id = %(o)s AND status = 'requested')""",
            {"o": ctx.org_id}).fetchone()
        counts = {"deposits": int(row[0]), "withdrawals": int(row[1]),
                  "transfers": int(row[2]), "payout_destinations": int(row[3]),
                  "kyc": int(row[4]), "account_requests": int(row[5])}
        return {**counts, "total": sum(counts.values())}
```

- [ ] **Step 6: The alert rules**

In `api/src/api/alerts.py` add to `ALERT_RULES`, after the `payment_method_changed` entry:

```python
    # Phase 2: identity documents and a trading account request wait on an
    # admin; an admin read an investor's MT5 passwords (what a stolen admin
    # session would do).
    ("control", "warning", "investor_kyc_submitted"): "Investor verification submitted",
    ("control", "warning", "investor_account_requested"): "Investor trading account request",
    ("control", "warning", "account_request_passwords_revealed"): "Account request passwords revealed",
```

In `api/src/api/telegram.py` add to `TELEGRAM_RULES`, after `payment_method_changed`:

```python
    ("control", "warning", "investor_kyc_submitted"),
    ("control", "warning", "investor_account_requested"),
    ("control", "warning", "account_request_passwords_revealed"),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_account_requests.py tests/test_portal_summary.py tests/test_alerts.py tests/test_telegram.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/src/api/routes/portal_identity.py api/src/api/routes/portal_admin.py api/src/api/alerts.py api/src/api/telegram.py api/tests/test_portal_account_requests.py api/tests/test_portal_summary.py
git commit -m "feat(api): account requests, admin side -- audited reveal, fulfil with optional link, reject; summary counts; alert rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 10: RBAC matrix rows for every new route; the full API suite

**Files:**
- Modify: `api/tests/test_rbac_matrix.py` (import; `MATRIX`; `matrix_org`; `test_destructive_rows_allowed`)

**Interfaces:**
- Consumes: every route of Tasks 5–9; `portal_helpers.add_package`, `kyc_profile`, `open_account_request`.
- Produces: nothing new; proves authorization for every new org route.

- [ ] **Step 1: Add the rows (they fail until the fixture seeds the rows they aim at)**

Change the helper import:

```python
from portal_helpers import (add_method, add_package, approved_destination, kyc_profile,
                            open_account_request)
```

Add to the module docstring's list of seeded rows: "the investor's submitted KYC profile (files 1–4), account package 1 and the investor's open account request 1".

Append inside `MATRIX`, after the `payout-destinations/2/decision` row:

```python
    # ---- phase 2, investor side
    ("GET",    "investor/profile",               None,                          "investor_only"),
    ("PUT",    "investor/profile",               {"phone": "+91 1"},             "investor_only"),
    ("POST",   "investor/profile/submit",        {"mpin": MPIN},                "investor_only"),
    ("GET",    "investor/account-packages",      None,                          "investor_only"),
    ("GET",    "investor/account-requests",      None,                          "investor_only"),
    ("POST",   "investor/account-requests",      {"package_id": 1, "leverage": 100,
                                                  "main_password": "Main1234",
                                                  "investor_password": "Inv12345",
                                                  "mpin": MPIN},                "investor_only"),
    ("POST",   "investor/account-requests/1/cancel", None,                      "investor_only"),
    # ---- phase 2, admin side
    ("GET",    "kyc",                            None,                          "admin"),
    ("POST",   "kyc/{investor}/decision",        {"status": "rejected", "note": "matrix"}, "admin"),
    ("GET",    "account-packages",               None,                          "admin"),
    ("POST",   "account-packages",               {"name": "Pro", "leverage_options": [100]}, "admin"),
    ("PATCH",  "account-packages/1",             {"name": "Renamed"},           "admin"),
    ("DELETE", "account-packages/1",             None,                          "admin"),
    ("GET",    "account-requests",               None,                          "admin"),
    ("POST",   "account-requests/1/reveal",      {"mpin": MPIN},                "admin"),
    ("POST",   "account-requests/1/fulfil",      {"mt5_login": 5001,
                                                  "mt5_server": "Broker-Live"}, "admin"),
    ("POST",   "account-requests/1/reject",      {"note": "matrix"},            "admin"),
```

What each allowed call proves (all answers come after the role check): the investor's PUT and submit get 409 (the profile is `submitted`); the account request gets 409 (`verify your identity first`); the cancel succeeds; the admin's decision, reveal, fulfil and reject each succeed on their own fixture instance.

- [ ] **Step 2: Run to verify the new rows fail**

Run: `.venv/Scripts/python -m pytest tests/test_rbac_matrix.py -q -p no:cacheprovider`
Expected: FAIL — e.g. `investor/account-requests/1/cancel` answers 404 `Request not found` for the investor (nothing seeded).

- [ ] **Step 3: Seed the rows in `matrix_org`**

In `matrix_org`, after `approved_id = approved_destination(...)` add:

```python
    kyc_profile(db, org_id, investor_id, status="submitted")              # files 1-4
    package_id = add_package(db, org_id)                                  # id 1
    open_account_request(db, org_id, investor_id, package_id)             # id 1
```

(`matrix_org` takes `app_client`, which sets `FERNET_KEY` before this runs.)

In `test_destructive_rows_allowed`, before the org `DELETE`, add:

```python
    r = _call(client, "DELETE", org_id, "account-packages/1", None)
    assert r.status_code == 409   # open account request 1 still uses it
```

- [ ] **Step 4: Run the matrix, then the whole API suite**

Run: `.venv/Scripts/python -m pytest tests/test_rbac_matrix.py -q -p no:cacheprovider`
Expected: PASS.

Run: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: PASS except the known 7 `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 5: Commit**

```bash
git add api/tests/test_rbac_matrix.py
git commit -m "test: RBAC matrix rows for every phase 2 route

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 11: Dashboard foundation — types, identity vocabulary, fixtures, `SignInHistory`

All dashboard commands run from `dashboard/`.

**Files:**
- Modify: `dashboard/src/lib/types.ts` (`InvestorSummary`, `InvestorRow`, `RequestsSummary`; append phase 2 types)
- Create: `dashboard/src/lib/identity.ts`, `dashboard/src/lib/identity.test.ts`
- Modify: `dashboard/src/test/portalFixtures.ts`
- Create: `dashboard/src/components/SignInHistory.tsx`, `dashboard/src/components/SignInHistory.test.tsx`
- Modify: `dashboard/src/pages/requests/RequestDetailsDrawer.tsx` (export `Row`, `Section`, `FilePreview`)

**Interfaces:**
- Consumes: `lib/investor.ts` `BADGE_TONE`, `StatusTone`; `lib/api.ts` `api`; `lib/format.ts` `errorText`, `formatWhen`; the API shapes of Tasks 3–9.
- Produces: the interfaces doc's `types.ts`, `identity.ts`, fixture and `SignInHistory` names.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/lib/identity.test.ts`:

```ts
import { expect, test } from 'vitest'
import {
  deviceLabel, generatePassword, kycBadge, kycLabel, passwordProblem, requestBadge, requestLabel,
} from './identity'

test('KYC and request statuses read as words with a tone', () => {
  expect(kycLabel('draft')).toBe('Not submitted')
  expect(kycLabel('submitted')).toBe('Under review')
  expect(kycLabel('approved')).toBe('Verified')
  expect(kycLabel('rejected')).toBe('Rejected')
  expect(kycBadge('approved')).toBe('profit')
  expect(kycBadge('rejected')).toBe('loss')
  expect(requestLabel('fulfilled')).toBe('Ready')
  expect(requestBadge('requested')).toBe('warn')
})

test('passwordProblem mirrors the server policy', () => {
  for (const ok of ['Abcdefg1', 'Pa$$w0rd!', `Z9${'x'.repeat(30)}`]) expect(passwordProblem(ok)).toBeNull()
  for (const bad of ['Abcdef1', 'abcdefg1', 'ABCDEFG1', 'Abcdefgh', 'Abc defg1', `A1${'x'.repeat(31)}`]) {
    expect(passwordProblem(bad)).toBe(
      'Use 8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit')
  }
})

test('a generated password always passes the policy and differs each time', () => {
  const seen = new Set<string>()
  for (let i = 0; i < 50; i++) {
    const p = generatePassword()
    expect(p).toHaveLength(12)
    expect(passwordProblem(p)).toBeNull()
    seen.add(p)
  }
  expect(seen.size).toBe(50)
})

test('deviceLabel names the browser and the system', () => {
  expect(deviceLabel(null)).toBe('Unknown device')
  expect(deviceLabel('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'))
    .toBe('Chrome on Windows')
  expect(deviceLabel('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'))
    .toBe('Safari on iOS')
  expect(deviceLabel('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36'))
    .toBe('Chrome on Android')
  expect(deviceLabel('curl/8.0')).toBe('Browser')
})
```

Create `dashboard/src/components/SignInHistory.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import SignInHistory from './SignInHistory'
import { signInFixture } from '../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists sign-ins with the IP, the device and the outcome', async () => {
  const fetchMock = vi.fn(async () => jsonResponse([
    signInFixture({ id: 2, ip: '10.0.0.9', outcome: 'failed' }), signInFixture()]))
  vi.stubGlobal('fetch', fetchMock)
  render(<SignInHistory path="/api/me/sign-ins?limit=50" />)
  expect(screen.getByRole('status', { name: 'Loading sign-ins' })).toBeInTheDocument()
  expect(await screen.findByText('10.0.0.9')).toBeInTheDocument()
  expect(screen.getByText('Wrong password')).toBeInTheDocument()
  expect(screen.getByText('Signed in')).toBeInTheDocument()
  expect(screen.getAllByText('Chrome on Windows')).toHaveLength(2)
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/me/sign-ins?limit=50')
})

test('an empty history says so', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([])))
  render(<SignInHistory path="/api/me/sign-ins?limit=50" />)
  expect(await screen.findByText('No sign-ins recorded yet')).toBeInTheDocument()
})

test('a failed load shows why', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'database unavailable' }, 500)))
  render(<SignInHistory path="/api/me/sign-ins?limit=50" />)
  expect(await screen.findByText('database unavailable')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/lib/identity.test.ts src/components/SignInHistory.test.tsx`
Expected: FAIL — cannot resolve `./identity` and `./SignInHistory`.

- [ ] **Step 3: Types**

In `dashboard/src/lib/types.ts`: add `kyc_status: KycStatus` as the last member of `InvestorSummary` (after `open_positions: number`) and of `InvestorRow` (after `pending`); replace `RequestsSummary`:

```ts
export interface RequestsSummary {
  deposits: number; withdrawals: number; transfers: number; payout_destinations: number
  kyc: number; account_requests: number; total: number
}
```

Append at the end of the file:

```ts
// ------------------------------------------------------------ phase 2: identity

export type KycStatus = 'draft' | 'submitted' | 'approved' | 'rejected'
export type Gender = 'male' | 'female' | 'other'
export type IdType = 'passport' | 'national_id' | 'driving_licence'
export type KycTextField =
  | 'full_name' | 'gender' | 'date_of_birth' | 'phone' | 'address_line' | 'area' | 'landmark'
  | 'city' | 'state' | 'postal_code' | 'country_residence' | 'country_citizenship' | 'id_type'
  | 'id_number'
export type KycFileField = 'id_front_file_id' | 'id_back_file_id' | 'address_proof_file_id' | 'photo_file_id'

/** GET investor/profile, PUT investor/profile, and each row of the admin's GET kyc
 *  (which adds email and display_name). `missing` lists the required fields still empty. */
export type KycProfile = { user_id: number }
  & Record<KycTextField, string | null>
  & Record<KycFileField, number | null>
  & {
    status: KycStatus
    submitted_at: string | null
    decided_by: number | null
    decided_at: string | null
    decision_note: string | null
    updated_at: string | null
    missing: string[]
    email?: string
    display_name?: string
  }

export interface AccountPackage {
  id: number; name: string; min_deposit: number; currency: string; spread_label: string | null
  leverage_options: number[]; enabled: boolean; sort_order: number
}

export type AccountRequestStatus = 'requested' | 'fulfilled' | 'rejected' | 'cancelled'

/** Never carries the passwords; the admin reads them through POST .../reveal. */
export interface AccountRequest {
  id: number; user_id: number; package_id: number | null; package_name: string; leverage: number
  status: AccountRequestStatus; mt5_login: number | null; mt5_server: string | null
  account_id: number | null; decided_by: number | null; decided_at: string | null
  decision_note: string | null; created_at: string; email?: string; display_name?: string
}

export interface RevealedPasswords { main_password: string; investor_password: string }

export interface SignIn {
  id: number; ip: string; user_agent: string | null
  outcome: 'password_ok' | 'mpin_ok' | 'failed'; created_at: string
}
```

- [ ] **Step 4: The identity vocabulary**

Create `dashboard/src/lib/identity.ts`:

```ts
import type { BadgeTone } from '../components/Badge'
import { BADGE_TONE, type StatusTone } from './investor'
import type {
  AccountRequestStatus, Gender, IdType, KycFileField, KycStatus, KycTextField, SignIn,
} from './types'

const KYC: Record<KycStatus, [string, StatusTone]> = {
  draft: ['Not submitted', 'quiet'],
  submitted: ['Under review', 'warn'],
  approved: ['Verified', 'ok'],
  rejected: ['Rejected', 'bad'],
}

export function kycLabel(status: KycStatus): string {
  return KYC[status][0]
}

export function kycBadge(status: KycStatus): BadgeTone {
  return BADGE_TONE[KYC[status][1]]
}

const REQUEST: Record<AccountRequestStatus, [string, StatusTone]> = {
  requested: ['Requested', 'warn'],
  fulfilled: ['Ready', 'ok'],
  rejected: ['Rejected', 'bad'],
  cancelled: ['Cancelled', 'quiet'],
}

export function requestLabel(status: AccountRequestStatus): string {
  return REQUEST[status][0]
}

export function requestBadge(status: AccountRequestStatus): BadgeTone {
  return BADGE_TONE[REQUEST[status][1]]
}

export const OUTCOME_LABELS: Record<SignIn['outcome'], string> = {
  password_ok: 'Password accepted',
  mpin_ok: 'Signed in',
  failed: 'Wrong password',
}

/** "Chrome on Windows" from a user agent; good enough to spot a stranger. */
export function deviceLabel(userAgent: string | null): string {
  if (!userAgent) return 'Unknown device'
  const ua = userAgent
  const browser = /Edg\//.test(ua) ? 'Edge'
    : /OPR\//.test(ua) ? 'Opera'
    : /Chrome\//.test(ua) ? 'Chrome'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /Safari\//.test(ua) ? 'Safari'
    : 'Browser'
  // Android before Linux and iOS before macOS: their agents name both.
  const os = /Windows/.test(ua) ? 'Windows'
    : /Android/.test(ua) ? 'Android'
    : /iPhone|iPad/.test(ua) ? 'iOS'
    : /Mac OS X/.test(ua) ? 'macOS'
    : /Linux/.test(ua) ? 'Linux'
    : ''
  return os ? `${browser} on ${os}` : browser
}

export const PASSWORD_RULE =
  '8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit'

/** The server's MT5 password policy (portal_identity.check_mt5_password). */
export function passwordProblem(p: string): string | null {
  const ok = /^[!-~]{8,32}$/.test(p) && /[A-Z]/.test(p) && /[a-z]/.test(p) && /[0-9]/.test(p)
  return ok ? null : `Use ${PASSWORD_RULE}`
}

// No 0/O, 1/l/I: the investor may read this off a screen into MetaTrader.
const UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
const LOWER = 'abcdefghijkmnopqrstuvwxyz'
const DIGITS = '23456789'

function randomIndex(n: number): number {
  const a = new Uint32Array(1)
  crypto.getRandomValues(a)
  return a[0] % n
}

/** A password that always passes passwordProblem: one of each class, the
 *  rest from all three, shuffled. */
export function generatePassword(length = 12): string {
  const all = UPPER + LOWER + DIGITS
  const chars = [UPPER, LOWER, DIGITS].map((set) => set[randomIndex(set.length)])
  while (chars.length < length) chars.push(all[randomIndex(all.length)])
  for (let i = chars.length - 1; i > 0; i--) {
    const j = randomIndex(i + 1)
    ;[chars[i], chars[j]] = [chars[j], chars[i]]
  }
  return chars.join('')
}

export const FIELD_LABELS: Record<KycTextField | KycFileField, string> = {
  full_name: 'Full name',
  gender: 'Gender',
  date_of_birth: 'Date of birth',
  phone: 'Phone',
  address_line: 'Address',
  area: 'Area',
  landmark: 'Landmark',
  city: 'City',
  state: 'State',
  postal_code: 'Postal code',
  country_residence: 'Country of residence',
  country_citizenship: 'Citizenship',
  id_type: 'ID type',
  id_number: 'ID number',
  id_front_file_id: 'ID front',
  id_back_file_id: 'ID back',
  address_proof_file_id: 'Proof of address',
  photo_file_id: 'Your photo',
}

export const GENDERS: readonly (readonly [Gender, string])[] = [
  ['male', 'Male'], ['female', 'Female'], ['other', 'Other'],
]

export const ID_TYPES: readonly (readonly [IdType, string])[] = [
  ['passport', 'Passport'], ['national_id', 'National ID card'], ['driving_licence', 'Driving licence'],
]
```

- [ ] **Step 5: Fixtures**

In `dashboard/src/test/portalFixtures.ts`: add `AccountPackage, AccountRequest, KycProfile, SignIn` to the type import; add `kyc_status: 'approved',` before `...overrides` in `summaryFixture` and in `investorRowFixture`; append:

```ts
export function profileFixture(overrides: Partial<KycProfile> = {}): KycProfile {
  return {
    user_id: 1, full_name: 'Sherwyn Joel', gender: 'male', date_of_birth: '1990-04-02',
    phone: '+91 98765 43210', address_line: '12 Lake Road', area: null, landmark: null,
    city: 'Coimbatore', state: 'Tamil Nadu', postal_code: '641001', country_residence: 'IN',
    country_citizenship: 'IN', id_type: 'passport', id_number: 'P1234567',
    id_front_file_id: 31, id_back_file_id: 32, address_proof_file_id: 33, photo_file_id: 34,
    status: 'draft', submitted_at: null, decided_by: null, decided_at: null, decision_note: null,
    updated_at: WHEN, missing: [],
    ...overrides,
  }
}

export function packageFixture(overrides: Partial<AccountPackage> = {}): AccountPackage {
  return {
    id: 1, name: 'Standard', min_deposit: 100, currency: 'USD', spread_label: '20-25',
    leverage_options: [100, 200, 500], enabled: true, sort_order: 0,
    ...overrides,
  }
}

export function accountRequestFixture(overrides: Partial<AccountRequest> = {}): AccountRequest {
  return {
    id: 7, user_id: 1, package_id: 1, package_name: 'Standard', leverage: 200, status: 'requested',
    mt5_login: null, mt5_server: null, account_id: null, decided_by: null, decided_at: null,
    decision_note: null, created_at: WHEN,
    ...overrides,
  }
}

export function signInFixture(overrides: Partial<SignIn> = {}): SignIn {
  return {
    id: 1, ip: '203.0.113.7',
    user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36',
    outcome: 'mpin_ok', created_at: WHEN,
    ...overrides,
  }
}
```

- [ ] **Step 6: `SignInHistory`**

Create `dashboard/src/components/SignInHistory.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { errorText, formatWhen } from '../lib/format'
import { OUTCOME_LABELS, deviceLabel } from '../lib/identity'
import Badge from './Badge'
import Banner from './Banner'
import Card from './Card'
import Loading from './Loading'
import type { SignIn } from '../lib/types'

/**
 * Where this account signed in from, newest first. Desk members read
 * /api/me/sign-ins, investors their portal route; the caller passes the
 * path. Read once on mount: it is a record, not a live feed.
 */
export default function SignInHistory({ path }: { path: string }) {
  const [rows, setRows] = useState<SignIn[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<SignIn[]>(path).then(
      (r) => { if (live) setRows(r) },
      (err) => {
        if (!live) return
        setError(errorText(err, 'Could not load your sign-ins'))
        setRows([])
      },
    )
    return () => { live = false }
  }, [path])

  return (
    <Card title="Sign-in history" inset>
      {error && <div className="p-4"><Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner></div>}
      {rows == null ? (
        <div className="p-4"><Loading lines={3} label="Loading sign-ins" /></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-4 py-2 font-semibold">Time</th>
                <th className="desk-label px-4 py-2 font-semibold">IP</th>
                <th className="desk-label px-4 py-2 font-semibold">Device</th>
                <th className="desk-label px-4 py-2 font-semibold">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={4} className="text-center py-8 text-ink-faint">No sign-ins recorded yet</td></tr>
              )}
              {rows.map((s) => (
                <tr key={s.id} className="border-b border-line last:border-0">
                  <td data-label="Time" className="num px-4 py-2.5">{formatWhen(s.created_at)}</td>
                  <td data-label="IP" className="num px-4 py-2.5">{s.ip}</td>
                  <td data-label="Device" className="px-4 py-2.5">{deviceLabel(s.user_agent)}</td>
                  <td data-label="Outcome" className="px-4 py-2.5">
                    <Badge tone={s.outcome === 'failed' ? 'loss' : 'neutral'}>{OUTCOME_LABELS[s.outcome]}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
```

- [ ] **Step 7: Export the drawer's building blocks**

In `dashboard/src/pages/requests/RequestDetailsDrawer.tsx` put `export` in front of `function Row`, `function Section` and `function FilePreview` (no other change).

- [ ] **Step 8: Run the tests and the type check**

Run: `npx vitest run src/lib/identity.test.ts src/components/SignInHistory.test.tsx src/test/portalFixtures.test.ts`
Expected: PASS.

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no errors (the new `kyc_status` members are satisfied by the fixtures; no page reads them yet).

- [ ] **Step 9: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/lib/identity.ts dashboard/src/lib/identity.test.ts dashboard/src/test/portalFixtures.ts dashboard/src/components/SignInHistory.tsx dashboard/src/components/SignInHistory.test.tsx dashboard/src/pages/requests/RequestDetailsDrawer.tsx
git commit -m "feat(dashboard): phase 2 types, identity vocabulary, fixtures and the SignInHistory card

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 12: Security page; the password change asks for the MPIN; the desk sees its sign-ins

**Files:**
- Modify: `dashboard/src/components/AccountSecurity.tsx` (full replacement below)
- Create: `dashboard/src/pages/investor/InvestorSecurity.tsx`, `InvestorSecurity.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorAccount.tsx` (drop `AccountSecurity`), `InvestorAccount.test.tsx` (first test)
- Modify: `dashboard/src/pages/Members.tsx`, `Members.test.tsx`, `Members.investor.test.tsx`
- Modify: `dashboard/src/pages/groups/investor.ts`, `dashboard/src/App.tsx`

**Interfaces:**
- Consumes: `SignInHistory` (Task 11); `PinConfirmDialog`; `api(path, init, { redirectOn401: false })`; `POST /api/me/password {current_password, new_password, mpin}` (Task 3).
- Produces: route `invest/security` → `InvestorSecurity`; `AccountSecurity` posts the MPIN; Members shows `SignInHistory path="/api/me/sign-ins?limit=50"`.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investor/InvestorSecurity.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorSecurity from './InvestorSecurity'
import { mockUseOrg } from '../../test/orgMock'
import { signInFixture } from '../../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(opts: { wrongMpinOnce?: boolean } = {}) {
  let passwordPosts = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/me/sign-ins')) return jsonResponse([signInFixture()])
    if (url === '/api/me/password' && init?.method === 'POST') {
      passwordPosts += 1
      if (opts.wrongMpinOnce && passwordPosts === 1) {
        return jsonResponse({ detail: 'Invalid MPIN', attempts_left: 4 }, 401)
      }
      return jsonResponse(null, 204)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function enterPin(dialog: HTMLElement, pin: string) {
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the Security page shows the login forms and the sign-in history', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorSecurity /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Security' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Your login' })).toBeInTheDocument()
  expect(await screen.findByText('Chrome on Windows')).toBeInTheDocument()
  expect(document.title).toBe('Security · MirrorFleet')
  expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/me/sign-ins?limit=50')).toBe(true)
})

test('a password change asks for the MPIN; a wrong one stays in the dialog', async () => {
  const fetchMock = mockRoutes({ wrongMpinOnce: true })
  render(<MemoryRouter><InvestorSecurity /></MemoryRouter>)
  await screen.findByText('Chrome on Windows')
  await userEvent.type(screen.getByLabelText('Current password'), 'old-password-x')
  await userEvent.type(screen.getByLabelText('New password'), 'new-password-y')
  await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
  const dialog = await screen.findByRole('dialog', { name: 'Change your password?' })
  await enterPin(dialog, '000000')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
  expect(await within(dialog).findByText('Wrong MPIN, 4 tries left')).toBeInTheDocument()
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
  expect(await screen.findByText(/signed out/)).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const posts = fetchMock.mock.calls.filter(([u]) => String(u) === '/api/me/password')
  expect(JSON.parse(String((posts[1][1] as RequestInit).body))).toEqual({
    current_password: 'old-password-x', new_password: 'new-password-y', mpin: '123456',
  })
})
```

In `dashboard/src/pages/Members.test.tsx`: inside `mockRoutes`, before the final `return jsonResponse({})`, add

```ts
    if (method === 'GET' && url.startsWith('/api/me/sign-ins')) return jsonResponse([])
```

and replace the test `'every member can change their own password'` with:

```tsx
test('every member can change their own password with the MPIN, and sees their sign-ins', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  const fetchMock = mockRoutes({
    'POST /api/me/password': () => jsonResponse(null, 204),
  })
  renderMembers()

  expect(await screen.findByText('No sign-ins recorded yet')).toBeInTheDocument()
  await userEvent.type(await screen.findByLabelText(/current password/i), 'old-password-x')
  await userEvent.type(screen.getByLabelText(/new password/i), 'new-password-y')
  await userEvent.click(screen.getByRole('button', { name: /change password/i }))
  const dialog = await screen.findByRole('dialog', { name: 'Change your password?' })
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(
      ([u, init]) => String(u) === '/api/me/password'
        && (init as RequestInit)?.method === 'POST')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({
      current_password: 'old-password-x', new_password: 'new-password-y', mpin: '123456',
    })
  })
  // The user is told the blast radius: other devices were signed out.
  expect(await screen.findByText(/signed out/i)).toBeInTheDocument()
})
```

In `dashboard/src/pages/Members.investor.test.tsx`, inside its fetch stub before `return jsonResponse({})`, add:

```ts
    if (url.includes('/sign-ins')) return jsonResponse([])
```

In `dashboard/src/pages/investor/InvestorAccount.test.tsx` replace the first test with:

```tsx
test('the Account page shows who you are and its title; the login forms moved to Security', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Account' })).toBeInTheDocument()
  expect(screen.getByText('Test User')).toBeInTheDocument()
  expect(screen.getByText('user@example.com')).toBeInTheDocument()
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(screen.getByText('Investor')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Your login' })).not.toBeInTheDocument()
  expect(document.title).toBe('Account · MirrorFleet')
  await screen.findByText('XAUUSD')
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorSecurity.test.tsx src/pages/Members.test.tsx src/pages/investor/InvestorAccount.test.tsx`
Expected: FAIL — `./InvestorSecurity` does not resolve; Members has no sign-in card and posts without `mpin`; the Account page still shows "Your login".

- [ ] **Step 3: `AccountSecurity` asks for the MPIN**

Replace `dashboard/src/components/AccountSecurity.tsx` with:

```tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import Banner from './Banner'
import Card from './Card'
import Button from './Button'
import Input from './Input'
import PinConfirmDialog from './PinConfirmDialog'
import PinInput from './PinInput'

/**
 * Your own login, not the org's: rotate the password (confirmed with the
 * MPIN), change the MPIN, and cut every other session loose. Every member
 * owns their own credentials, so none of this sits behind a role check.
 */
export default function AccountSecurity() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [pinOpen, setPinOpen] = useState(false)
  const [currentMpin, setCurrentMpin] = useState('')
  const [newMpin, setNewMpin] = useState('')
  const [confirmMpin, setConfirmMpin] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const navigate = useNavigate()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    setNotice(null)
    setProblem(null)
    setPinOpen(true)
  }

  // Rejections propagate: PinConfirmDialog shows them inline (a wrong MPIN,
  // or the server's "Current password is incorrect") and clears the PIN.
  const changePassword = async (mpin: string) => {
    setBusy(true)
    try {
      await api('/api/me/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: next, mpin }),
      }, { redirectOn401: false })
      setPinOpen(false)
      setCurrent('')
      setNext('')
      setNotice('Password changed. Any other device signed in as you has been signed out.')
    } finally {
      setBusy(false)
    }
  }

  const submitMpin = async (e: React.FormEvent) => {
    e.preventDefault()
    setNotice(null)
    setProblem(null)
    if (newMpin !== confirmMpin) { setProblem('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/me/mpin', {
        method: 'POST',
        body: JSON.stringify({ current_mpin: currentMpin, mpin: newMpin, mpin_confirm: confirmMpin }),
      })
      setCurrentMpin(''); setNewMpin(''); setConfirmMpin('')
      setNotice('MPIN changed. Use the new one at your next sign-in; any other device signed in as you has been signed out.')
    } catch (err) {
      const res = (err as ApiError).response
      const left = res?.body?.attempts_left
      const until = res?.body?.locked_until
      if (res?.status === 401 && typeof left === 'number') {
        setProblem(`Wrong MPIN, ${left} ${left === 1 ? 'try' : 'tries'} left`)
      } else if (res?.status === 423 && typeof until === 'string') {
        const minutes = Math.max(1, Math.ceil((Date.parse(until) - Date.now()) / 60000))
        setProblem(`MPIN locked. Try again in about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`)
      } else {
        setProblem(err instanceof Error ? err.message : 'Could not change the MPIN')
      }
    } finally {
      setBusy(false)
    }
  }

  const signOutEverywhere = async () => {
    setProblem(null)
    setBusy(true)
    try {
      await api('/api/me/logout-all', { method: 'POST' })
      navigate('/login')
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'Could not sign out everywhere')
      setBusy(false)
    }
  }

  return (
    // The card is the section: the Members page and the investor Security
    // page show it as "Your login"; the forms are its parts.
    <Card title="Your login">
    <div className="space-y-4">
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {problem && <Banner kind="error" onDismiss={() => setProblem(null)}>{problem}</Banner>}

      <h3 className="text-base font-semibold text-ink">Change password</h3>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="current-password" className="desk-label block mb-1">
            Current password
          </label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new-password" className="desk-label block mb-1">
            New password
          </label>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={busy || !current || !next}>
          Change password
        </Button>
      </form>

      <h3 className="text-base font-semibold text-ink">Change MPIN</h3>
      <form onSubmit={submitMpin} className="space-y-3">
        <div className="flex flex-wrap items-start gap-4">
          <PinInput id="current-mpin" label="Current MPIN" value={currentMpin} onChange={setCurrentMpin} disabled={busy} />
          <PinInput id="new-mpin" label="New MPIN" value={newMpin} onChange={setNewMpin} disabled={busy} />
          {/* Not "confirm-mpin": PinConfirmDialog's own box carries that id. */}
          <PinInput id="confirm-new-mpin" label="Confirm new MPIN" value={confirmMpin} onChange={setConfirmMpin} disabled={busy} />
        </div>
        <Button type="submit" disabled={busy || currentMpin.length !== 6 || newMpin.length !== 6 || confirmMpin.length !== 6}>
          Change MPIN
        </Button>
      </form>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={signOutEverywhere} disabled={busy}>
          Sign out everywhere
        </Button>
        <p className="text-sm text-ink-soft">
          Ends every session for your account, on this device and any other.
          Use it if you think a login was stolen.
        </p>
      </div>

      <PinConfirmDialog
        open={pinOpen}
        title="Change your password?"
        confirmLabel="Confirm"
        busy={busy}
        onConfirm={changePassword}
        onCancel={() => setPinOpen(false)}
      >
        <p>Every other device you use will have to sign in again.</p>
      </PinConfirmDialog>
    </div>
    </Card>
  )
}
```

- [ ] **Step 4: The Security page**

Create `dashboard/src/pages/investor/InvestorSecurity.tsx`:

```tsx
import { useOrg } from '../../lib/org'
import AccountSecurity from '../../components/AccountSecurity'
import PageHeader from '../../components/PageHeader'
import SignInHistory from '../../components/SignInHistory'

/** Password, MPIN, sessions, and where this account signed in from. */
export default function InvestorSecurity() {
  const { orgId } = useOrg()
  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Security" subtitle="Your password, your MPIN, your sessions and where you signed in from." />
      <AccountSecurity />
      <SignInHistory path="/api/me/sign-ins?limit=50" />
    </div>
  )
}
```

Add to `dashboard/src/pages/groups/investor.ts`:

```ts
export { default as InvestorSecurity } from '../investor/InvestorSecurity'
```

In `dashboard/src/App.tsx` add after `const InvestorAccount = pick(investor, 'InvestorAccount')`:

```tsx
const InvestorSecurity = pick(investor, 'InvestorSecurity')
```

and after `<Route path="invest/account" element={<InvestorAccount />} />`:

```tsx
              <Route path="invest/security" element={<InvestorSecurity />} />
```

- [ ] **Step 5: The Account page drops the login card; Members shows sign-ins**

In `dashboard/src/pages/investor/InvestorAccount.tsx` delete the `import AccountSecurity …` line and the `<AccountSecurity />` line at the end of the JSX.

In `dashboard/src/pages/Members.tsx` add `import SignInHistory from '../components/SignInHistory'` after the `AccountSecurity` import, and directly under `<AccountSecurity />`:

```tsx
      <SignInHistory path="/api/me/sign-ins?limit=50" />
```

- [ ] **Step 6: Run the tests**

Run: `npx vitest run src/pages/investor/InvestorSecurity.test.tsx src/pages/Members.test.tsx src/pages/Members.investor.test.tsx src/pages/investor/InvestorAccount.test.tsx src/App.test.tsx`
Expected: PASS, no `act(...)` warnings.

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/AccountSecurity.tsx dashboard/src/pages/investor/InvestorSecurity.tsx dashboard/src/pages/investor/InvestorSecurity.test.tsx dashboard/src/pages/investor/InvestorAccount.tsx dashboard/src/pages/investor/InvestorAccount.test.tsx dashboard/src/pages/Members.tsx dashboard/src/pages/Members.test.tsx dashboard/src/pages/Members.investor.test.tsx dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Security page with sign-in history; a password change asks for the MPIN; Members shows the desk's own sign-ins

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 13: Profile & verification page

**Files:**
- Create: `dashboard/src/pages/investor/InvestorProfile.tsx`, `InvestorProfile.test.tsx`
- Modify: `dashboard/src/pages/groups/investor.ts`, `dashboard/src/App.tsx`

**Interfaces:**
- Consumes: `GET/PUT investor/profile`, `POST investor/profile/submit`, `POST investor/files` (purposes `kyc_document`, `kyc_photo`); `lib/identity.ts` `FIELD_LABELS`, `GENDERS`, `ID_TYPES`, `fieldValue`, `kycLabel`, `kycBadge`; `FileInput`, `PinConfirmDialog`; `profileFixture`.
- Produces: route `invest/profile` → `InvestorProfile` (title `Profile & verification`).

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investor/InvestorProfile.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorProfile from './InvestorProfile'
import { mockUseOrg } from '../../test/orgMock'
import { profileFixture } from '../../test/portalFixtures'
import type { KycProfile } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const REQUIRED = ['full_name', 'gender', 'date_of_birth', 'phone', 'address_line', 'city', 'postal_code',
  'country_residence', 'country_citizenship', 'id_type', 'id_number', 'id_front_file_id', 'id_back_file_id',
  'address_proof_file_id', 'photo_file_id'] as const
const CONTACT = new Set(['phone', 'address_line', 'area', 'landmark', 'city', 'state', 'postal_code'])

const empty = profileFixture({
  full_name: null, gender: null, date_of_birth: null, phone: null, address_line: null, city: null,
  state: null, postal_code: null, country_residence: null, country_citizenship: null, id_type: null,
  id_number: null, id_front_file_id: null, id_back_file_id: null, address_proof_file_id: null,
  photo_file_id: null, missing: [...REQUIRED],
})

/** A tiny server: PUT merges and recomputes `missing`, an approved profile
 *  drops to draft on any non-contact change, submit locks it. */
function mockRoutes(start: KycProfile) {
  let profile: KycProfile = { ...start }
  let uploads = 90
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') return jsonResponse(profile)
    if (url.endsWith('/investor/profile') && method === 'PUT') {
      const body = JSON.parse(String(init!.body)) as Partial<KycProfile>
      const reverify = profile.status === 'approved' && Object.keys(body).some((k) => !CONTACT.has(k))
      profile = { ...profile, ...body, status: reverify ? 'draft' : profile.status }
      profile.missing = REQUIRED.filter((k) => profile[k] == null)
      return jsonResponse(profile)
    }
    if (url.endsWith('/investor/files') && method === 'POST') {
      uploads += 1
      return jsonResponse({ id: uploads, purpose: (init!.body as FormData).get('purpose'),
                            content_type: 'image/png', size_bytes: 3, created_at: '2026-10-01T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/profile/submit') && method === 'POST') {
      profile = { ...profile, status: 'submitted', submitted_at: '2026-10-01T10:00:00Z' }
      return jsonResponse(profile)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const calls = (fetchMock: ReturnType<typeof mockRoutes>, tail: string, method: string) =>
  fetchMock.mock.calls.filter(([u, init]) => String(u).endsWith(tail) && (init as RequestInit | undefined)?.method === method)

function renderPage() {
  return render(<MemoryRouter><InvestorProfile /></MemoryRouter>)
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:preview'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('a new investor sees an empty draft, the five steps and the title', async () => {
  mockRoutes(empty)
  renderPage()
  expect(screen.getByRole('heading', { level: 1, name: 'Profile & verification' })).toBeInTheDocument()
  expect(await screen.findByText('Not submitted')).toBeInTheDocument()
  const steps = within(screen.getByRole('list', { name: 'Verification steps' })).getAllByRole('button')
  expect(steps.map((b) => b.textContent)).toEqual(['1. Profile', '2. Identity', '3. Address', '4. Photo', '5. Review'])
  expect(steps[0]).toHaveAttribute('aria-current', 'step')
  expect(screen.getByLabelText('Full name')).toHaveValue('')
  expect(document.title).toBe('Profile & verification · MirrorFleet')
})

test('Save and continue sends only the changed fields of the step and moves on', async () => {
  const fetchMock = mockRoutes(empty)
  renderPage()
  await userEvent.type(await screen.findByLabelText('Full name'), 'Ada Lovelace')
  await userEvent.selectOptions(screen.getByLabelText('Gender'), 'female')
  await userEvent.type(screen.getByLabelText('Date of birth'), '1990-04-02')
  await userEvent.type(screen.getByLabelText('Phone'), '+44 20 1234')
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(calls(fetchMock, '/investor/profile', 'PUT')).toHaveLength(1))
  expect(JSON.parse(String((calls(fetchMock, '/investor/profile', 'PUT')[0][1] as RequestInit).body))).toEqual({
    full_name: 'Ada Lovelace', gender: 'female', date_of_birth: '1990-04-02', phone: '+44 20 1234',
  })
  await waitFor(() => expect(screen.getByRole('button', { name: '2. Identity' })).toHaveAttribute('aria-current', 'step'))
  expect(screen.getByText('Saved')).toBeInTheDocument()
})

test('a document is uploaded with its KYC purpose and saved by id', async () => {
  const fetchMock = mockRoutes({ ...profileFixture(), id_front_file_id: null, missing: ['id_front_file_id'] })
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '2. Identity' }))
  await userEvent.upload(screen.getByLabelText('ID front'), new File(['x'], 'front.png', { type: 'image/png' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(calls(fetchMock, '/investor/profile', 'PUT')).toHaveLength(1))
  const upload = calls(fetchMock, '/investor/files', 'POST')[0]
  expect((upload[1] as RequestInit).body instanceof FormData).toBe(true)
  expect(((upload[1] as RequestInit).body as FormData).get('purpose')).toBe('kyc_document')
  expect(JSON.parse(String((calls(fetchMock, '/investor/profile', 'PUT')[0][1] as RequestInit).body)))
    .toEqual({ id_front_file_id: 91 })
  await waitFor(() => expect(screen.getByRole('button', { name: '3. Address' })).toHaveAttribute('aria-current', 'step'))
})

test('Review lists what is still needed and holds the submit back', async () => {
  mockRoutes({ ...profileFixture(), photo_file_id: null, missing: ['photo_file_id'] })
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '5. Review' }))
  expect(screen.getByText('Still needed: Your photo')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Submit for verification' })).toBeDisabled()
  expect(screen.getByText('Passport')).toBeInTheDocument()
})

test('a complete draft is submitted with the MPIN and then locked', async () => {
  const fetchMock = mockRoutes(profileFixture())
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '5. Review' }))
  await userEvent.click(screen.getByRole('button', { name: 'Submit for verification' }))
  const dialog = await screen.findByRole('dialog', { name: 'Submit your profile for verification?' })
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Submit' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(JSON.parse(String((calls(fetchMock, '/investor/profile/submit', 'POST')[0][1] as RequestInit).body)))
    .toEqual({ mpin: '123456' })
  expect(screen.getByText('Under review')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '1. Profile' }))
  expect(screen.getByLabelText('Full name')).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Save and continue' })).not.toBeInTheDocument()
})

test('a rejected profile shows the admin note', async () => {
  mockRoutes(profileFixture({ status: 'rejected', decision_note: 'ID photo is blurred' }))
  renderPage()
  expect(await screen.findByText('Rejected: ID photo is blurred. Fix what the note asks and submit again.'))
    .toBeInTheDocument()
})

test('an approved investor who changes their name is told to submit again', async () => {
  mockRoutes(profileFixture({ status: 'approved' }))
  renderPage()
  const name = await screen.findByLabelText('Full name')
  await userEvent.clear(name)
  await userEvent.type(name, 'Sherwyn J')
  await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
  expect(await screen.findByText('Saved. You changed identity details, so submit again to be verified.'))
    .toBeInTheDocument()
  expect(screen.getByText('Not submitted')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorProfile.test.tsx`
Expected: FAIL — cannot resolve `./InvestorProfile`.

- [ ] **Step 3: Write the page**

Create `dashboard/src/pages/investor/InvestorProfile.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { FIELD_LABELS, GENDERS, ID_TYPES, fieldValue, kycBadge, kycLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { KycFileField, KycProfile, KycTextField, UploadedFile } from '../../lib/types'

type Step = 'profile' | 'identity' | 'address' | 'photo' | 'review'
type FormStep = Exclude<Step, 'review'>

const STEPS: { key: Step; label: string }[] = [
  { key: 'profile', label: 'Profile' },
  { key: 'identity', label: 'Identity' },
  { key: 'address', label: 'Address' },
  { key: 'photo', label: 'Photo' },
  { key: 'review', label: 'Review' },
]

const STEP_FIELDS: Record<FormStep, { text: KycTextField[]; files: KycFileField[] }> = {
  profile: { text: ['full_name', 'gender', 'date_of_birth', 'phone'], files: [] },
  identity: { text: ['country_citizenship', 'id_type', 'id_number'], files: ['id_front_file_id', 'id_back_file_id'] },
  address: {
    text: ['address_line', 'area', 'landmark', 'city', 'state', 'postal_code', 'country_residence'],
    files: ['address_proof_file_id'],
  },
  photo: { text: [], files: ['photo_file_id'] },
}
const TEXT_FIELDS: KycTextField[] = Object.values(STEP_FIELDS).flatMap((s) => s.text)
const FILE_FIELDS: KycFileField[] = Object.values(STEP_FIELDS).flatMap((s) => s.files)
const OPTIONAL = new Set<string>(['area', 'landmark', 'state'])
const COUNTRY = new Set<string>(['country_residence', 'country_citizenship'])
const PHOTO_ACCEPT = ['image/jpeg', 'image/png', 'image/webp']
const FILE_HINT: Record<KycFileField, string> = {
  id_front_file_id: 'The side with your photo. JPEG, PNG, WebP or PDF up to 5 MB.',
  id_back_file_id: 'The other side (for a passport, the page with your address or signature).',
  address_proof_file_id: 'A utility bill or bank statement from the last three months.',
  photo_file_id: 'A clear photo of your face, JPEG, PNG or WebP up to 5 MB.',
}

type Form = Record<KycTextField, string>

function formOf(p: KycProfile): Form {
  return Object.fromEntries(TEXT_FIELDS.map((k) => [k, p[k] ?? ''])) as Form
}

function Field({ field, value, onChange }: { field: KycTextField; value: string; onChange: (v: string) => void }) {
  const id = `kyc-${field}`
  const label = `${FIELD_LABELS[field]}${OPTIONAL.has(field) ? ' (optional)' : ''}`
  if (field === 'gender' || field === 'id_type') {
    const options = field === 'gender' ? GENDERS : ID_TYPES
    return (
      <div>
        <label htmlFor={id} className="desk-label block mb-1">{label}</label>
        <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose one</option>
          {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
        </Select>
      </div>
    )
  }
  const country = COUNTRY.has(field)
  return (
    <div>
      <label htmlFor={id} className="desk-label block mb-1">{label}</label>
      <Input id={id} value={value} type={field === 'date_of_birth' ? 'date' : 'text'}
             num={country || field === 'postal_code' || field === 'id_number'}
             maxLength={country ? 2 : undefined}
             onChange={(e) => onChange(country ? e.target.value.toUpperCase() : e.target.value)} />
      {country && <p className="mt-1 text-xs text-ink-soft">Two-letter code, e.g. IN</p>}
    </div>
  )
}

function StatusBanner({ profile }: { profile: KycProfile }) {
  if (profile.status === 'draft') {
    return <Banner kind="warn" announce={false}>Not submitted yet. Fill in each step, then submit from Review.</Banner>
  }
  if (profile.status === 'submitted') {
    return (
      <Banner kind="notice" announce={false}>
        {`Under review since ${formatWhen(profile.submitted_at)}. You can edit again once an admin has decided.`}
      </Banner>
    )
  }
  if (profile.status === 'approved') {
    return (
      <Banner kind="notice" announce={false}>
        Verified. Changing your phone or address keeps you verified; changing your name, date of birth,
        citizenship, ID or any document means verifying again.
      </Banner>
    )
  }
  return (
    <Banner kind="error" announce={false}>
      {`Rejected: ${profile.decision_note ?? 'no reason given'}. Fix what the note asks and submit again.`}
    </Banner>
  )
}

/**
 * The investor's profile and identity verification, one step at a time.
 * Each step saves only what changed in it (uploads first, then one PUT);
 * Review shows what is still missing and submits with the MPIN.
 */
export default function InvestorProfile() {
  const { orgId } = useOrg()
  const [profile, setProfile] = useState<KycProfile | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [files, setFiles] = useState<Partial<Record<KycFileField, File | null>>>({})
  const [step, setStep] = useState<Step>('profile')
  const [pinOpen, setPinOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const p = await orgApi<KycProfile>(orgId, 'investor/profile')
      setProfile(p)
      setForm(formOf(p))
    } catch (err) {
      setError(errorText(err, 'Could not load your profile'))
    }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const locked = profile?.status === 'submitted'

  const saveStep = async (s: FormStep): Promise<boolean> => {
    if (!profile || !form) return false
    setBusy(true); setError(null); setNotice(null)
    try {
      const body: Record<string, string | number | null> = {}
      for (const k of STEP_FIELDS[s].text) {
        const v = form[k].trim()
        if (v !== (profile[k] ?? '')) body[k] = v === '' ? null : v
      }
      for (const k of STEP_FIELDS[s].files) {
        const file = files[k]
        if (!file) continue
        const fd = new FormData()
        fd.append('purpose', k === 'photo_file_id' ? 'kyc_photo' : 'kyc_document')
        fd.append('file', file)
        body[k] = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      }
      if (Object.keys(body).length > 0) {
        const saved = await orgApi<KycProfile>(orgId, 'investor/profile', { method: 'PUT', body: JSON.stringify(body) })
        setNotice(profile.status === 'approved' && saved.status === 'draft'
          ? 'Saved. You changed identity details, so submit again to be verified.'
          : 'Saved')
        setProfile(saved)
        setForm(formOf(saved))
        setFiles({})
      }
      return true
    } catch (err) {
      setError(errorText(err, 'Could not save your profile'))
      return false
    } finally {
      setBusy(false)
    }
  }

  const saveAndContinue = async (s: FormStep) => {
    if (await saveStep(s)) setStep(STEPS[STEPS.findIndex((x) => x.key === s) + 1].key)
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const submit = async (mpin: string) => {
    setBusy(true)
    try {
      const p = await orgApi<KycProfile>(orgId, 'investor/profile/submit',
        { method: 'POST', body: JSON.stringify({ mpin }) }, { redirectOn401: false })
      setProfile(p)
      setForm(formOf(p))
      setPinOpen(false)
      setNotice('Submitted. An admin reviews your documents; you will get an email with the result.')
    } finally {
      setBusy(false)
    }
  }

  const current = STEPS.find((s) => s.key === step)!

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Profile & verification"
        subtitle="Your details and identity documents. An admin verifies them before you can open a trading account."
        actions={profile ? <Badge tone={kycBadge(profile.status)}>{kycLabel(profile.status)}</Badge> : undefined}
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {!profile || !form ? (
        !error && <Loading lines={4} label="Loading your profile" />
      ) : (
        <>
          <StatusBanner profile={profile} />

          <ol aria-label="Verification steps" className="flex flex-wrap gap-2">
            {STEPS.map((s, i) => (
              <li key={s.key}>
                <Button size="sm" variant={step === s.key ? 'primary' : 'secondary'}
                        aria-current={step === s.key ? 'step' : undefined}
                        onClick={() => setStep(s.key)}>
                  {`${i + 1}. ${s.label}`}
                </Button>
              </li>
            ))}
          </ol>

          {step !== 'review' ? (
            <Card title={current.label}>
              <form onSubmit={(e) => { e.preventDefault(); void saveAndContinue(step) }} noValidate className="space-y-4">
                <fieldset disabled={locked || busy} className="space-y-4">
                  {STEP_FIELDS[step].text.map((k) => (
                    <Field key={k} field={k} value={form[k]} onChange={(v) => setForm({ ...form, [k]: v })} />
                  ))}
                  {STEP_FIELDS[step].files.map((k) => (
                    <div key={k} className="space-y-1">
                      <FileInput id={`kyc-${k}`}
                                 label={profile[k] != null ? `Replace ${FIELD_LABELS[k]}` : FIELD_LABELS[k]}
                                 accept={k === 'photo_file_id' ? PHOTO_ACCEPT : RECEIPT_ACCEPT}
                                 maxBytes={MAX_UPLOAD_BYTES} value={files[k] ?? null}
                                 onChange={(f) => setFiles({ ...files, [k]: f })}
                                 disabled={locked || busy} hint={FILE_HINT[k]} />
                      {profile[k] != null && (
                        <p className="text-sm text-ink-soft">
                          {'On file. '}
                          <a href={`/api/orgs/${orgId}/investor/files/${profile[k]}`} target="_blank" rel="noreferrer"
                             className="text-brand underline underline-offset-2 hover:text-brand-deep">
                            View it
                          </a>
                        </p>
                      )}
                    </div>
                  ))}
                </fieldset>
                {!locked && (
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" disabled={busy}>Save and continue</Button>
                    <Button type="button" variant="secondary" disabled={busy} onClick={() => { void saveStep(step) }}>
                      Save draft
                    </Button>
                  </div>
                )}
              </form>
            </Card>
          ) : (
            <Card title="Review">
              <div className="space-y-4">
                <dl className="inset p-4 grid gap-3 sm:grid-cols-2 text-sm">
                  {TEXT_FIELDS.map((k) => (
                    <div key={k}>
                      <dt className="desk-label">{FIELD_LABELS[k]}</dt>
                      <dd className="text-ink">{fieldValue(k, profile[k])}</dd>
                    </div>
                  ))}
                  {FILE_FIELDS.map((k) => (
                    <div key={k}>
                      <dt className="desk-label">{FIELD_LABELS[k]}</dt>
                      <dd className="text-ink">{profile[k] != null ? 'Uploaded' : 'Not uploaded'}</dd>
                    </div>
                  ))}
                </dl>
                {profile.missing.length > 0 && (
                  <p className="text-sm text-ink-soft">
                    {`Still needed: ${profile.missing.map((k) => FIELD_LABELS[k as KycTextField | KycFileField] ?? k).join(', ')}`}
                  </p>
                )}
                {(profile.status === 'draft' || profile.status === 'rejected') && (
                  <Button onClick={() => setPinOpen(true)} disabled={busy || profile.missing.length > 0}>
                    Submit for verification
                  </Button>
                )}
              </div>
            </Card>
          )}
        </>
      )}

      <PinConfirmDialog
        open={pinOpen}
        title="Submit your profile for verification?"
        confirmLabel="Submit"
        busy={busy}
        onConfirm={submit}
        onCancel={() => setPinOpen(false)}
      >
        <p>An admin checks your details and documents. You cannot edit them while they are under review.</p>
      </PinConfirmDialog>
    </div>
  )
}
```

Add to `dashboard/src/lib/identity.ts` (and to its test) — `fieldValue` is listed in the interfaces doc:

```ts
/** A profile value as people read it: 'Passport' for passport, '—' for nothing. */
export function fieldValue(field: KycTextField, value: string | null): string {
  if (value == null || value === '') return '—'
  const options: readonly (readonly [string, string])[] | null =
    field === 'gender' ? GENDERS : field === 'id_type' ? ID_TYPES : null
  return options?.find(([v]) => v === value)?.[1] ?? value
}
```

Append to `dashboard/src/lib/identity.test.ts` (and add `fieldValue` to its import):

```ts
test('fieldValue shows choices as words and nothing as a dash', () => {
  expect(fieldValue('id_type', 'national_id')).toBe('National ID card')
  expect(fieldValue('gender', 'female')).toBe('Female')
  expect(fieldValue('city', 'Coimbatore')).toBe('Coimbatore')
  expect(fieldValue('city', null)).toBe('—')
})
```

Register the page: `dashboard/src/pages/groups/investor.ts` gains

```ts
export { default as InvestorProfile } from '../investor/InvestorProfile'
```

and `dashboard/src/App.tsx` gains `const InvestorProfile = pick(investor, 'InvestorProfile')` (after the `InvestorSecurity` const) and `<Route path="invest/profile" element={<InvestorProfile />} />` (after the `invest/security` route).

- [ ] **Step 4: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorProfile.test.tsx src/lib/identity.test.ts`
Expected: PASS, no `act(...)` warnings.

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/investor/InvestorProfile.tsx dashboard/src/pages/investor/InvestorProfile.test.tsx dashboard/src/lib/identity.ts dashboard/src/lib/identity.test.ts dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Profile & verification -- five steps with uploads, save draft, review, submit with the MPIN

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 14: Open account page; the Account page shows the MT5 login

**Files:**
- Create: `dashboard/src/pages/investor/InvestorOpenAccount.tsx`, `InvestorOpenAccount.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorAccount.tsx`, `InvestorAccount.test.tsx`
- Modify: `dashboard/src/pages/groups/investor.ts`, `dashboard/src/App.tsx`

**Interfaces:**
- Consumes: `GET investor/profile` (status only), `GET investor/account-packages`, `GET/POST investor/account-requests`, `POST investor/account-requests/{id}/cancel`; `lib/identity.ts` `generatePassword`, `passwordProblem`, `PASSWORD_RULE`, `requestLabel`, `requestBadge`; `NextStep`, `PinConfirmDialog`, `ConfirmDialog`; `packageFixture`, `accountRequestFixture`.
- Produces: route `invest/open-account` → `InvestorOpenAccount`; Account page Card `Your MT5 login`.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investor/InvestorOpenAccount.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorOpenAccount from './InvestorOpenAccount'
import { mockUseOrg } from '../../test/orgMock'
import { accountRequestFixture, packageFixture } from '../../test/portalFixtures'
import type { AccountRequest, KycStatus } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockRoutes(opts: { kyc?: KycStatus; requests?: AccountRequest[] } = {}) {
  let requests = [...(opts.requests ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile')) return jsonResponse({ status: opts.kyc ?? 'approved' })
    if (url.endsWith('/investor/account-packages')) {
      return jsonResponse([packageFixture(), packageFixture({ id: 2, name: 'Pro', min_deposit: 1000, leverage_options: [100] })])
    }
    if (url.endsWith('/investor/account-requests') && method === 'POST') {
      const body = JSON.parse(String(init!.body)) as { package_id: number; leverage: number }
      const created = accountRequestFixture({ id: 9, package_id: body.package_id, leverage: body.leverage })
      requests = [created, ...requests]
      return jsonResponse(created, 201)
    }
    if (url.endsWith('/investor/account-requests')) return jsonResponse(requests)
    const cancel = url.match(/\/investor\/account-requests\/(\d+)\/cancel$/)
    if (cancel && method === 'POST') {
      requests = requests.map((r) => r.id === Number(cancel[1]) ? { ...r, status: 'cancelled' as const } : r)
      return jsonResponse(requests[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const posts = (fetchMock: ReturnType<typeof mockRoutes>) =>
  fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')

function renderPage() {
  return render(<MemoryRouter><InvestorOpenAccount /></MemoryRouter>)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('an unverified investor is sent to verify first', async () => {
  mockRoutes({ kyc: 'submitted' })
  renderPage()
  expect(screen.getByRole('heading', { level: 1, name: 'Open account' })).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: 'Verify your identity first' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Go to Profile & verification' })).toHaveAttribute('href', '/org/1/invest/profile')
  expect(screen.queryByRole('button', { name: 'Choose Standard' })).not.toBeInTheDocument()
  expect(document.title).toBe('Open account · MirrorFleet')
})

test('a verified investor picks a package, generates passwords and requests with the MPIN', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: 'Choose Standard' }))
  expect(screen.getByText('1:100 · 1:200 · 1:500')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText('Leverage'), '200')
  await userEvent.click(screen.getByRole('button', { name: 'Generate main password' }))
  await userEvent.click(screen.getByRole('button', { name: 'Generate investor password' }))
  const main = (screen.getByLabelText('Main password') as HTMLInputElement).value
  const investor = (screen.getByLabelText('Investor password') as HTMLInputElement).value
  expect(main).toHaveLength(12)
  expect(screen.getByLabelText('Main password')).toHaveAttribute('type', 'text')
  await userEvent.click(screen.getByRole('button', { name: 'Request account' }))
  const dialog = await screen.findByRole('dialog', { name: 'Request a Standard account at 1:200?' })
  expect(posts(fetchMock)).toHaveLength(0)
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse(String((posts(fetchMock)[0][1] as RequestInit).body))).toEqual({
    package_id: 1, leverage: 200, main_password: main, investor_password: investor, mpin: '123456',
  })
  expect(await screen.findByRole('heading', { name: 'Your request' })).toBeInTheDocument()
  expect(screen.getByText('Requested')).toBeInTheDocument()
})

test('a weak or repeated password is refused before anything is sent', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: 'Choose Standard' }))
  await userEvent.type(screen.getByLabelText('Main password'), 'short')
  await userEvent.type(screen.getByLabelText('Investor password'), 'Abcdefg1')
  await userEvent.click(screen.getByRole('button', { name: 'Request account' }))
  expect(await screen.findByText(
    'Use 8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit')).toBeInTheDocument()
  await userEvent.clear(screen.getByLabelText('Main password'))
  await userEvent.type(screen.getByLabelText('Main password'), 'Abcdefg1')
  await userEvent.click(screen.getByRole('button', { name: 'Request account' }))
  expect(await screen.findByText('The investor password must differ from the main password')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('an open request can be cancelled, and the packages come back', async () => {
  const fetchMock = mockRoutes({ requests: [accountRequestFixture()] })
  renderPage()
  expect(await screen.findByRole('heading', { name: 'Your request' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Choose Standard' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Cancel request' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel this request?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel request' }))
  expect(await screen.findByRole('button', { name: 'Choose Standard' })).toBeInTheDocument()
  expect(posts(fetchMock).map(([u]) => String(u))).toEqual(['/api/orgs/1/investor/account-requests/7/cancel'])
})

test('a fulfilled request shows the login and server', async () => {
  mockRoutes({ requests: [accountRequestFixture({ status: 'fulfilled', mt5_login: 5001, mt5_server: 'Broker-Live' })] })
  renderPage()
  expect(await screen.findByText('5001')).toBeInTheDocument()
  expect(screen.getByText('Broker-Live')).toBeInTheDocument()
  expect(screen.getByText('Ready')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel request' })).not.toBeInTheDocument()
})

test('a rejected request shows the note and offers the packages again', async () => {
  mockRoutes({ requests: [accountRequestFixture({ status: 'rejected', decision_note: 'Broker paused new accounts' })] })
  renderPage()
  expect(await screen.findByText('Your last request was rejected: Broker paused new accounts')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Choose Pro' })).toBeInTheDocument()
})
```

In `dashboard/src/pages/investor/InvestorAccount.test.tsx`: add `accountRequestFixture` to the fixtures import and `AccountRequest` to the types import; change `function mockRoutes(summary: InvestorSummary)` to `function mockRoutes(summary: InvestorSummary, requests: AccountRequest[] = [])` and add, as its first branch,

```ts
    if (url.endsWith('/investor/account-requests')) return jsonResponse(requests)
```

Append:

```tsx
test('a fulfilled account request shows its MT5 login on the Account page', async () => {
  mockRoutes(linked, [accountRequestFixture({ status: 'fulfilled', mt5_login: 5001, mt5_server: 'Broker-Live' })])
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  const card = (await screen.findByRole('heading', { name: 'Your MT5 login' })).closest('section')!
  expect(within(card).getByText('5001')).toBeInTheDocument()
  expect(within(card).getByText('Broker-Live')).toBeInTheDocument()
  expect(within(card).getByText('Standard · 1:200')).toBeInTheDocument()
  await screen.findByText('XAUUSD')
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investor/InvestorOpenAccount.test.tsx src/pages/investor/InvestorAccount.test.tsx`
Expected: FAIL — `./InvestorOpenAccount` does not resolve; no `Your MT5 login` heading.

- [ ] **Step 3: Write the page**

Create `dashboard/src/pages/investor/InvestorOpenAccount.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { PASSWORD_RULE, generatePassword, passwordProblem, requestBadge, requestLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import NextStep from './NextStep'
import type { AccountPackage, AccountRequest, KycProfile, KycStatus } from '../../lib/types'

function PasswordField({ id, label, hint, value, shown, onChange, onGenerate, generateLabel }: {
  id: string; label: string; hint?: string; value: string; shown: boolean
  onChange: (v: string) => void; onGenerate: () => void; generateLabel: string
}) {
  return (
    <div>
      <label htmlFor={id} className="desk-label block mb-1">{label}</label>
      <div className="flex flex-wrap items-center gap-2">
        <Input id={id} num type={shown ? 'text' : 'password'} autoComplete="new-password" value={value}
               onChange={(e) => onChange(e.target.value)} />
        <Button type="button" variant="ghost" size="sm" aria-label={generateLabel} onClick={onGenerate}>Generate</Button>
      </div>
      {hint && <p className="mt-1 text-xs text-ink-soft">{hint}</p>}
    </div>
  )
}

/**
 * Request a live MT5 account. Verification comes first; then one request at
 * a time: the packages the workspace offers, a leverage and two passwords
 * the investor keeps, confirmed with the MPIN. The admin opens the account
 * at the broker by hand and the login appears here (and on Account).
 */
export default function InvestorOpenAccount() {
  const { orgId } = useOrg()
  const [kyc, setKyc] = useState<KycStatus | null>(null)
  const [packages, setPackages] = useState<AccountPackage[]>([])
  const [requests, setRequests] = useState<AccountRequest[]>([])
  const [chosen, setChosen] = useState<AccountPackage | null>(null)
  const [leverage, setLeverage] = useState('')
  const [mainPassword, setMainPassword] = useState('')
  const [investorPassword, setInvestorPassword] = useState('')
  const [shown, setShown] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [pinOpen, setPinOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [p, pk, rq] = await Promise.all([
        orgApi<KycProfile>(orgId, 'investor/profile'),
        orgApi<AccountPackage[]>(orgId, 'investor/account-packages'),
        orgApi<AccountRequest[]>(orgId, 'investor/account-requests'),
      ])
      setKyc(p.status); setPackages(pk); setRequests(rq)
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load account opening'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const latest = requests[0] ?? null
  const current = latest && (latest.status === 'requested' || latest.status === 'fulfilled') ? latest : null

  const choose = (p: AccountPackage) => {
    setChosen(p)
    setLeverage(String(p.leverage_options[0]))
    setMainPassword(''); setInvestorPassword(''); setShown(false); setFormError(null)
  }

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    const problem = passwordProblem(mainPassword) ?? passwordProblem(investorPassword)
    if (problem) { setFormError(problem); return }
    if (mainPassword === investorPassword) {
      setFormError('The investor password must differ from the main password')
      return
    }
    setFormError(null)
    setPinOpen(true)
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const confirm = async (mpin: string) => {
    if (!chosen) return
    setBusy(true)
    try {
      await orgApi<AccountRequest>(orgId, 'investor/account-requests', {
        method: 'POST',
        body: JSON.stringify({
          package_id: chosen.id, leverage: Number(leverage),
          main_password: mainPassword, investor_password: investorPassword, mpin,
        }),
      }, { redirectOn401: false })
      setPinOpen(false)
      setChosen(null); setMainPassword(''); setInvestorPassword('')
      setNotice('Request sent. An admin opens your account at the broker; you will get an email with your login.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!current) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<AccountRequest>(orgId, `investor/account-requests/${current.id}/cancel`, { method: 'POST' })
      setNotice('Request cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the request'))
    } finally {
      setBusy(false)
      setCancelOpen(false)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Open account" subtitle="Request a live MT5 trading account from the packages this workspace offers." />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {!loaded ? (
        <Loading lines={3} label="Loading account opening" />
      ) : kyc !== 'approved' ? (
        <>
          <NextStep title="Verify your identity first">
            An admin verifies your identity before you can request a trading account: one form and four uploads.
          </NextStep>
          <Button to={`/org/${orgId}/invest/profile`}>Go to Profile & verification</Button>
        </>
      ) : current ? (
        <Card title="Your request">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="desk-label">Package</dt><dd className="text-ink">{current.package_name}</dd></div>
            <div><dt className="desk-label">Leverage</dt><dd className="num text-ink">{`1:${current.leverage}`}</dd></div>
            <div><dt className="desk-label">Status</dt>
              <dd><Badge tone={requestBadge(current.status)}>{requestLabel(current.status)}</Badge></dd></div>
            <div><dt className="desk-label">Requested</dt><dd className="num text-ink">{formatWhen(current.created_at)}</dd></div>
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

      <PinConfirmDialog
        open={pinOpen}
        title={`Request a ${chosen?.name ?? ''} account at 1:${leverage}?`}
        confirmLabel="Request"
        busy={busy}
        onConfirm={confirm}
        onCancel={() => setPinOpen(false)}
      >
        <p>An admin opens the account at the broker with the passwords you chose and emails you the login.</p>
      </PinConfirmDialog>

      <ConfirmDialog
        open={cancelOpen}
        title="Cancel this request?"
        confirmLabel="Cancel request"
        danger
        busy={busy}
        onConfirm={() => { void cancel() }}
        onCancel={() => setCancelOpen(false)}
      >
        <p>Your passwords are deleted with it. You can request again later.</p>
      </ConfirmDialog>
    </div>
  )
}
```

- [ ] **Step 4: The Account page's login card**

In `dashboard/src/pages/investor/InvestorAccount.tsx`: add `AccountRequest` to the types import; add state `const [login, setLogin] = useState<AccountRequest | null>(null)`; in `refresh` replace `const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')` with

```ts
      const [s, requests] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<AccountRequest[]>(orgId, 'investor/account-requests'),
      ])
      setLogin(requests.find((r) => r.status === 'fulfilled') ?? null)
```

and right after the Profile `Card` in the JSX add:

```tsx
      {login && (
        <Card title="Your MT5 login">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-3 text-sm">
            <div><dt className="desk-label">Login</dt><dd className="num text-ink">{login.mt5_login}</dd></div>
            <div><dt className="desk-label">Server</dt><dd className="num text-ink">{login.mt5_server}</dd></div>
            <div><dt className="desk-label">Package</dt>
              <dd className="text-ink">{`${login.package_name} · 1:${login.leverage}`}</dd></div>
          </dl>
        </Card>
      )}
```

Register the page: `dashboard/src/pages/groups/investor.ts` gains `export { default as InvestorOpenAccount } from '../investor/InvestorOpenAccount'`; `dashboard/src/App.tsx` gains `const InvestorOpenAccount = pick(investor, 'InvestorOpenAccount')` and `<Route path="invest/open-account" element={<InvestorOpenAccount />} />` after the `invest/profile` route.

- [ ] **Step 5: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorOpenAccount.test.tsx src/pages/investor/InvestorAccount.test.tsx`
Expected: PASS, no `act(...)` warnings.

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/investor/InvestorOpenAccount.tsx dashboard/src/pages/investor/InvestorOpenAccount.test.tsx dashboard/src/pages/investor/InvestorAccount.tsx dashboard/src/pages/investor/InvestorAccount.test.tsx dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Open account -- verify first, package cards, generated passwords, MPIN request, cancel; Account shows the MT5 login

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 15: Requests desk — Verification and Account requests tabs

**Files:**
- Create: `dashboard/src/pages/requests/VerificationTab.tsx`, `VerificationTab.test.tsx`
- Create: `dashboard/src/pages/requests/AccountRequestsTab.tsx`, `AccountRequestsTab.test.tsx`
- Modify: `dashboard/src/pages/requests/RequestTabs.tsx` (`DeskTab`, `DESK_TABS`, `tabItems`)
- Modify: `dashboard/src/pages/Requests.tsx`, `dashboard/src/pages/Requests.test.tsx`

**Interfaces:**
- Consumes: `GET kyc`, `POST kyc/{user_id}/decision`, `GET account-requests`, `GET accounts`, `POST account-requests/{id}/reveal|fulfil|reject`, `requests/summary` keys `kyc` / `account_requests` (Tasks 6, 9); `FilePreview`, `Row`, `Section` (Task 11); `mpinErrorText` from `components/PinConfirmDialog`; `lib/identity.ts`.
- Produces: `RequestTabs.DeskTab = RequestKind | 'kyc' | 'account_requests'`, `DESK_TABS`; tab components with props `{ orgId: number; control: boolean; show: 'open' | 'all'; onDone: (message: string) => void; onError: (message: string) => void }`.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/requests/VerificationTab.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import VerificationTab from './VerificationTab'
import { profileFixture } from '../../test/portalFixtures'
import type { KycProfile } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const submitted: KycProfile = profileFixture({
  user_id: 5, status: 'submitted', submitted_at: '2026-10-01T09:00:00Z', email: 'ada@example.com', display_name: 'Ada',
})
const approved: KycProfile = profileFixture({
  user_id: 6, status: 'approved', email: 'bob@example.com', display_name: 'Bob', full_name: 'Bob Builder',
})

function mockRoutes() {
  let rows = [{ ...submitted }, { ...approved }]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/kyc')) return jsonResponse(rows)
    const m = url.match(/\/kyc\/(\d+)\/decision$/)
    if (m && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { status: KycProfile['status'] }
      rows = rows.map((r) => r.user_id === Number(m[1]) ? { ...r, status: body.status } : r)
      return jsonResponse(rows.find((r) => r.user_id === Number(m[1])))
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the open view lists submitted profiles; All adds the decided ones', async () => {
  mockRoutes()
  const { rerender } = render(<VerificationTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.getByText('Under review')).toBeInTheDocument()
  expect(screen.queryByText('Bob')).not.toBeInTheDocument()
  rerender(<VerificationTab orgId={1} control show="all" onDone={vi.fn()} onError={vi.fn()} />)
  expect(screen.getByText('Bob')).toBeInTheDocument()
  expect(screen.getByText('Verified')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Approve verification 6' })).not.toBeInTheDocument()
})

test('details show every field and the four documents', async () => {
  mockRoutes()
  render(<VerificationTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Details of verification 5' }))
  const drawer = await screen.findByRole('dialog', { name: 'Verification of Ada' })
  expect(within(drawer).getByText('P1234567')).toBeInTheDocument()
  expect(within(drawer).getByText('Passport')).toBeInTheDocument()
  for (const label of ['ID front', 'ID back', 'Proof of address', 'Your photo']) {
    expect(within(drawer).getByRole('img', { name: label })).toHaveAttribute('src', expect.stringMatching(/^\/api\/orgs\/1\/files\/3[1-4]$/))
  }
})

test('a rejection needs a note; an approval posts and reports back', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<VerificationTab orgId={1} control show="open" onDone={onDone} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Reject verification 5' }))
  let dialog = await screen.findByRole('dialog', { name: 'Reject the verification of Ada' })
  expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled()
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await userEvent.click(screen.getByRole('button', { name: 'Approve verification 5' }))
  dialog = await screen.findByRole('dialog', { name: 'Approve the verification of Ada' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Verification approved'))
  const post = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/kyc/5/decision'))!
  expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ status: 'approved', note: '' })
  expect(await screen.findByText('No open verifications')).toBeInTheDocument()
})

test('a viewer sees the queue but no decisions', async () => {
  mockRoutes()
  render(<VerificationTab orgId={1} control={false} show="open" onDone={vi.fn()} onError={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Approve verification 5' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Details of verification 5' })).toBeInTheDocument()
})
```

Create `dashboard/src/pages/requests/AccountRequestsTab.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import AccountRequestsTab from './AccountRequestsTab'
import { accountRequestFixture } from '../../test/portalFixtures'
import type { Account } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const mt5 = { ctid_trader_account_id: 2001, trader_login: 0, is_live: true, role: 'slave', enabled: true,
  multiplier: 1, status: 'ok', connection_status: 'active', nickname: 'Ada MT5', platform: 'mt5',
  mt5: { login: 5001, broker: 'Broker', server: 'Broker-Live', currency: 'USD', hedging: true,
         trade_mode: 'real', ea_version: '1', last_seen_at: null, connected: true } } as Account
const master = { ...mt5, ctid_trader_account_id: 100, role: 'master', nickname: 'Master' } as Account

function mockRoutes() {
  let rows = [accountRequestFixture({ email: 'ada@example.com', display_name: 'Ada' })]
  let reveals = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/account-requests')) return jsonResponse(rows)
    if (url.endsWith('/accounts')) return jsonResponse([mt5, master])
    if (url.endsWith('/account-requests/7/reveal')) {
      reveals += 1
      if (reveals === 1) return jsonResponse({ detail: 'Invalid MPIN', attempts_left: 4 }, 401)
      return jsonResponse({ main_password: 'Main1234', investor_password: 'Look1234' })
    }
    const m = url.match(/\/account-requests\/7\/(fulfil|reject)$/)
    if (m) {
      rows = rows.map((r) => ({ ...r, status: m[1] === 'fulfil' ? 'fulfilled' as const : 'rejected' as const }))
      return jsonResponse(rows[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof mockRoutes>, tail: string) =>
  JSON.parse(String((fetchMock.mock.calls.find(([u]) => String(u).endsWith(tail))![1] as RequestInit).body))

async function enterPin(scope: HTMLElement, pin: string) {
  await userEvent.click(within(scope).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('reveal with the MPIN, then fulfil with login, server and a linked MT5 account', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={onDone} onError={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.getByText('Standard')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Fulfil account request 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await enterPin(drawer, '000000')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  expect(await within(drawer).findByText('Wrong MPIN, 4 tries left')).toBeInTheDocument()
  await enterPin(drawer, '123456')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  expect(await within(drawer).findByText('Main1234')).toBeInTheDocument()
  expect(within(drawer).getByText('Look1234')).toBeInTheDocument()
  const options = within(within(drawer).getByLabelText('Link to account')).getAllByRole('option')
  expect(options.map((o) => o.textContent)).toEqual(['Do not link', 'Ada MT5 (MT5 5001)'])
  await userEvent.type(within(drawer).getByLabelText('MT5 login'), '5001')
  await userEvent.type(within(drawer).getByLabelText('MT5 server'), 'Broker-Live')
  await userEvent.selectOptions(within(drawer).getByLabelText('Link to account'), '2001')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Account request #7 fulfilled'))
  expect(bodyOf(fetchMock, '/reveal')).toEqual({ mpin: '000000' })
  expect(bodyOf(fetchMock, '/fulfil')).toEqual({ mt5_login: 5001, mt5_server: 'Broker-Live', account_id: 2001, note: null })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(await screen.findByText('No open account requests')).toBeInTheDocument()
})

test('fulfil needs a login number and a server before anything is sent', async () => {
  const fetchMock = mockRoutes()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Fulfil account request 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  expect(await within(drawer).findByText('Enter the MT5 login number')).toBeInTheDocument()
  await userEvent.type(within(drawer).getByLabelText('MT5 login'), '5001')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  expect(await within(drawer).findByText('Enter the MT5 server')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/fulfil'))).toBe(false)
})

test('a rejection needs a note and posts it', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={onDone} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Reject account request 7' }))
  const dialog = await screen.findByRole('dialog', { name: 'Reject the account request of Ada' })
  expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled()
  await userEvent.type(within(dialog).getByLabelText('Note'), 'Broker paused new accounts')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Account request #7 rejected'))
  expect(bodyOf(fetchMock, '/reject')).toEqual({ note: 'Broker paused new accounts' })
})

test('a viewer sees the queue but no actions', async () => {
  mockRoutes()
  render(<AccountRequestsTab orgId={1} control={false} show="open" onDone={vi.fn()} onError={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Fulfil account request 7' })).not.toBeInTheDocument()
})
```

In `dashboard/src/pages/Requests.test.tsx`: in `mockRoutes`, add to the `rows` map `kyc: []`, `'account-requests': []`, `accounts: []` (the existing `endsWith('/${key}')` loop then serves them), and append:

```tsx
test('the desk has Verification and Account requests tabs, and ?tab= opens them', async () => {
  mockRoutes()
  renderPage('/org/1/requests?tab=account_requests')
  expect(await screen.findByText('No open account requests')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Verification (0)' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Account requests (0)' })).toHaveAttribute('aria-selected', 'true')
  await userEvent.click(screen.getByRole('tab', { name: 'Verification (0)' }))
  expect(await screen.findByText('No open verifications')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/requests/VerificationTab.test.tsx src/pages/requests/AccountRequestsTab.test.tsx src/pages/Requests.test.tsx`
Expected: FAIL — the two tab modules do not resolve; the Requests test finds no `Verification (0)` tab.

- [ ] **Step 3: The Verification tab**

Create `dashboard/src/pages/requests/VerificationTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, formatWhen } from '../../lib/format'
import { FIELD_LABELS, fieldValue, kycBadge, kycLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Loading from '../../components/Loading'
import { FilePreview, Row, Section } from './RequestDetailsDrawer'
import type { KycFileField, KycProfile, KycTextField } from '../../lib/types'

export interface DeskTabProps {
  orgId: number
  control: boolean
  show: 'open' | 'all'
  onDone: (message: string) => void
  onError: (message: string) => void
}

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'
const SECTIONS: [string, KycTextField[]][] = [
  ['Profile', ['full_name', 'gender', 'date_of_birth', 'phone']],
  ['Identity', ['country_citizenship', 'id_type', 'id_number']],
  ['Address', ['address_line', 'area', 'landmark', 'city', 'state', 'postal_code', 'country_residence']],
]
const DOCUMENTS: KycFileField[] = ['id_front_file_id', 'id_back_file_id', 'address_proof_file_id', 'photo_file_id']

/** Identity verifications waiting on an admin: the profile, the four
 *  documents, Approve, or Reject with a note. Loads its own queue; the
 *  page's summary refresh follows every decision through onDone. */
export default function VerificationTab({ orgId, control, show, onDone, onError }: DeskTabProps) {
  const [rows, setRows] = useState<KycProfile[] | null>(null)
  const [details, setDetails] = useState<KycProfile | null>(null)
  const [pending, setPending] = useState<{ row: KycProfile; status: 'approved' | 'rejected' } | null>(null)
  const [note, setNote] = useState('')
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setRows(await orgApi<KycProfile[]>(orgId, 'kyc'))
    } catch (err) {
      onError(errorText(err, 'Could not load the verifications'))
      setRows((r) => r ?? [])
    }
  }, [orgId, onError])

  useEffect(() => { load() }, [load])

  const open = (row: KycProfile, status: 'approved' | 'rejected') => {
    setPending({ row, status }); setNote(''); setDialogError(null)
  }

  const decide = async () => {
    if (!pending) return
    setBusy(true); setDialogError(null)
    try {
      await orgApi(orgId, `kyc/${pending.row.user_id}/decision`, {
        method: 'POST', body: JSON.stringify({ status: pending.status, note: note.trim() }),
      })
      const word = pending.status
      setPending(null)
      setDetails(null)
      await load()
      onDone(`Verification ${word}`)
    } catch (err) {
      setDialogError(errorText(err, 'The action failed'))
    } finally {
      setBusy(false)
    }
  }

  if (rows == null) return <Loading lines={4} label="Loading verifications" />
  const visible = show === 'open' ? rows.filter((r) => r.status === 'submitted') : rows
  const who = (r: KycProfile) => r.display_name ?? r.email ?? `investor ${r.user_id}`

  return (
    <>
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className={TH}>Submitted</th>
            <th className={TH}>Investor</th>
            <th className={TH}>Name on ID</th>
            <th className={TH}>ID</th>
            <th className={TH}>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && (
            <tr><td colSpan={6} className="text-center py-8 text-ink-faint">
              {show === 'open' ? 'No open verifications' : 'No verifications yet'}
            </td></tr>
          )}
          {visible.map((r) => (
            <tr key={r.user_id} className="border-b border-line last:border-0 align-top">
              <td data-label="Submitted" className={`num ${TD}`}>{formatWhen(r.submitted_at)}</td>
              <td data-label="Investor" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{r.display_name ?? '—'}</div>
                  <div className="text-xs text-ink-soft">{r.email ?? ''}</div>
                </div>
              </td>
              <td data-label="Name on ID" className={TD}>{r.full_name ?? '—'}</td>
              <td data-label="ID" className={TD}>{`${fieldValue('id_type', r.id_type)} · ${r.country_citizenship ?? '—'}`}</td>
              <td data-label="Status" className={TD}>
                <div className="min-w-0">
                  <Badge tone={kycBadge(r.status)}>{kycLabel(r.status)}</Badge>
                  {r.decision_note && <div className="text-xs text-ink-soft mt-1">{r.decision_note}</div>}
                </div>
              </td>
              <td className={TD}>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <Button variant="ghost" size="sm" aria-label={`Details of verification ${r.user_id}`}
                          onClick={() => setDetails(r)}>Details</Button>
                  {control && r.status === 'submitted' && (
                    <>
                      <Button size="sm" aria-label={`Approve verification ${r.user_id}`} disabled={busy}
                              onClick={() => open(r, 'approved')}>Approve</Button>
                      <Button size="sm" variant="secondary" tone="loss" aria-label={`Reject verification ${r.user_id}`}
                              disabled={busy} onClick={() => open(r, 'rejected')}>Reject</Button>
                    </>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Drawer open={details != null} title={details ? `Verification of ${who(details)}` : ''}
              onClose={() => setDetails(null)}>
        {details && (
          <div className="space-y-4">
            <Section title="Investor">
              <Row label="Name" value={details.display_name ?? '—'} />
              <Row label="Email" value={details.email ?? '—'} />
              <Row label="Status" value={kycLabel(details.status)} />
              {details.decision_note && <Row label="Note" value={details.decision_note} />}
            </Section>
            {SECTIONS.map(([title, fields]) => (
              <Section key={title} title={title}>
                {fields.map((f) => <Row key={f} label={FIELD_LABELS[f]} value={fieldValue(f, details[f])} />)}
              </Section>
            ))}
            {DOCUMENTS.map((f) => {
              const fileId = details[f]
              return fileId != null
                ? <FilePreview key={f} orgId={orgId} fileId={fileId} label={FIELD_LABELS[f]} />
                : <Section key={f} title={FIELD_LABELS[f]}><Row label="File" value="Not uploaded" /></Section>
            })}
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={pending != null}
        title={pending ? `${pending.status === 'approved' ? 'Approve' : 'Reject'} the verification of ${who(pending.row)}` : ''}
        confirmLabel={pending?.status === 'approved' ? 'Approve' : 'Reject'}
        danger={pending?.status === 'rejected'}
        busy={busy}
        disabled={pending?.status === 'rejected' && note.trim() === ''}
        onConfirm={() => { void decide() }}
        onCancel={() => setPending(null)}
      >
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        <label className="block">
          <span className="desk-label block mb-1">{pending?.status === 'rejected' ? 'Note' : 'Note (optional)'}</span>
          <textarea aria-label="Note" value={note} rows={2} onChange={(e) => setNote(e.target.value)}
                    className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
        </label>
      </ConfirmDialog>
    </>
  )
}
```

- [ ] **Step 4: The Account requests tab**

Create `dashboard/src/pages/requests/AccountRequestsTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, formatWhen } from '../../lib/format'
import { requestBadge, requestLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PinInput from '../../components/PinInput'
import Select from '../../components/Select'
import { mpinErrorText } from '../../components/PinConfirmDialog'
import { Row, Section } from './RequestDetailsDrawer'
import type { DeskTabProps } from './VerificationTab'
import type { Account, AccountRequest, RevealedPasswords } from '../../lib/types'

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'
const TEXTAREA = 'w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink'

/**
 * Live account requests. Fulfil opens a drawer: the admin reveals the two
 * passwords with their own MPIN (inline, not a second dialog over the
 * drawer), opens the login at the broker, then records login, server and
 * optionally the MirrorFleet MT5 account to link. Reject needs a note.
 */
export default function AccountRequestsTab({ orgId, control, show, onDone, onError }: DeskTabProps) {
  const [rows, setRows] = useState<AccountRequest[] | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [target, setTarget] = useState<AccountRequest | null>(null)
  const [pin, setPin] = useState('')
  const [pinError, setPinError] = useState<string | null>(null)
  const [revealed, setRevealed] = useState<RevealedPasswords | null>(null)
  const [login, setLogin] = useState('')
  const [server, setServer] = useState('')
  const [accountId, setAccountId] = useState('')
  const [note, setNote] = useState('')
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState<AccountRequest | null>(null)
  const [rejectNote, setRejectNote] = useState('')
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [r, a] = await Promise.all([
        orgApi<AccountRequest[]>(orgId, 'account-requests'),
        orgApi<Account[]>(orgId, 'accounts'),
      ])
      setRows(r); setAccounts(a)
    } catch (err) {
      onError(errorText(err, 'Could not load the account requests'))
      setRows((x) => x ?? [])
    }
  }, [orgId, onError])

  useEffect(() => { load() }, [load])

  const openFulfil = (r: AccountRequest) => {
    setTarget(r); setPin(''); setPinError(null); setRevealed(null)
    setLogin(''); setServer(''); setAccountId(''); setNote(''); setDrawerError(null)
  }
  // Closing forgets the passwords: they are on screen only while needed.
  const closeFulfil = () => { setTarget(null); setRevealed(null); setPin('') }

  const reveal = async () => {
    if (!target || pin.length !== 6) return
    setBusy(true); setPinError(null)
    try {
      setRevealed(await orgApi<RevealedPasswords>(orgId, `account-requests/${target.id}/reveal`,
        { method: 'POST', body: JSON.stringify({ mpin: pin }) }, { redirectOn401: false }))
    } catch (err) {
      setPinError(mpinErrorText(err, 'Could not reveal the passwords'))
    } finally {
      setPin('')
      setBusy(false)
    }
  }

  const fulfil = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!target) return
    if (!/^\d+$/.test(login.trim()) || Number(login.trim()) <= 0) { setDrawerError('Enter the MT5 login number'); return }
    if (!server.trim()) { setDrawerError('Enter the MT5 server'); return }
    setBusy(true); setDrawerError(null)
    try {
      await orgApi(orgId, `account-requests/${target.id}/fulfil`, {
        method: 'POST',
        body: JSON.stringify({
          mt5_login: Number(login.trim()), mt5_server: server.trim(),
          account_id: accountId ? Number(accountId) : null, note: note.trim() || null,
        }),
      })
      const id = target.id
      closeFulfil()
      await load()
      onDone(`Account request #${id} fulfilled`)
    } catch (err) {
      setDrawerError(errorText(err, 'Could not fulfil the request'))
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    if (!rejecting) return
    setBusy(true); setDialogError(null)
    try {
      await orgApi(orgId, `account-requests/${rejecting.id}/reject`, {
        method: 'POST', body: JSON.stringify({ note: rejectNote.trim() }),
      })
      const id = rejecting.id
      setRejecting(null)
      await load()
      onDone(`Account request #${id} rejected`)
    } catch (err) {
      setDialogError(errorText(err, 'The action failed'))
    } finally {
      setBusy(false)
    }
  }

  if (rows == null) return <Loading lines={4} label="Loading account requests" />
  const visible = show === 'open' ? rows.filter((r) => r.status === 'requested') : rows
  const linkable = accounts.filter((a) => a.platform === 'mt5' && a.role !== 'master')
  const who = (r: AccountRequest) => r.display_name ?? r.email ?? `investor ${r.user_id}`

  return (
    <>
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className={TH}>Requested</th>
            <th className={TH}>Investor</th>
            <th className={TH}>Package</th>
            <th className={TH}>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && (
            <tr><td colSpan={5} className="text-center py-8 text-ink-faint">
              {show === 'open' ? 'No open account requests' : 'No account requests yet'}
            </td></tr>
          )}
          {visible.map((r) => (
            <tr key={r.id} className="border-b border-line last:border-0 align-top">
              <td data-label="Requested" className={`num ${TD}`}>{formatWhen(r.created_at)}</td>
              <td data-label="Investor" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{r.display_name ?? '—'}</div>
                  <div className="text-xs text-ink-soft">{r.email ?? ''}</div>
                </div>
              </td>
              <td data-label="Package" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{r.package_name}</div>
                  <div className="num text-xs text-ink-soft">{`1:${r.leverage}`}</div>
                </div>
              </td>
              <td data-label="Status" className={TD}>
                <div className="min-w-0">
                  <Badge tone={requestBadge(r.status)}>{requestLabel(r.status)}</Badge>
                  {r.status === 'fulfilled' && (
                    <div className="num text-xs text-ink-soft mt-1">{`${r.mt5_login} · ${r.mt5_server}`}</div>
                  )}
                  {r.decision_note && <div className="text-xs text-ink-soft mt-1">{r.decision_note}</div>}
                </div>
              </td>
              <td className={TD}>
                {control && r.status === 'requested' && (
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <Button size="sm" aria-label={`Fulfil account request ${r.id}`} disabled={busy}
                            onClick={() => openFulfil(r)}>Fulfil</Button>
                    <Button size="sm" variant="secondary" tone="loss" aria-label={`Reject account request ${r.id}`}
                            disabled={busy}
                            onClick={() => { setRejecting(r); setRejectNote(''); setDialogError(null) }}>
                      Reject
                    </Button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Drawer open={target != null} title={target ? `Fulfil request #${target.id}` : ''} onClose={closeFulfil} busy={busy}>
        {target && (
          <div className="space-y-4">
            <Section title="Request">
              <Row label="Investor" value={who(target)} />
              <Row label="Email" value={target.email ?? '—'} />
              <Row label="Package" value={target.package_name} />
              <Row label="Leverage" value={`1:${target.leverage}`} mono />
            </Section>
            <section>
              <h3 className="desk-label mb-2">Passwords</h3>
              <div className="inset p-3 space-y-3 text-sm">
                {revealed ? (
                  <>
                    <dl className="space-y-1.5">
                      <Row label="Main password" value={revealed.main_password} mono />
                      <Row label="Investor password" value={revealed.investor_password} mono />
                    </dl>
                    <p className="text-xs text-ink-soft">
                      Create the account at the broker with these now. They are deleted when you fulfil or reject.
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-ink-soft">
                      The investor chose both passwords. Confirm with your MPIN to see them; every reveal is logged.
                    </p>
                    <PinInput id="reveal-mpin" label="Your MPIN" value={pin} onChange={setPin} disabled={busy} error={pinError} />
                    <Button size="sm" variant="secondary" disabled={busy || pin.length !== 6} onClick={() => { void reveal() }}>
                      Reveal passwords
                    </Button>
                  </>
                )}
              </div>
            </section>
            <form onSubmit={fulfil} noValidate className="space-y-4">
              {drawerError && <Banner kind="error" onDismiss={() => setDrawerError(null)}>{drawerError}</Banner>}
              <div>
                <label htmlFor="fulfil-login" className="desk-label block mb-1">MT5 login</label>
                <Input id="fulfil-login" num inputMode="numeric" value={login} disabled={busy}
                       onChange={(e) => setLogin(e.target.value)} />
              </div>
              <div>
                <label htmlFor="fulfil-server" className="desk-label block mb-1">MT5 server</label>
                <Input id="fulfil-server" value={server} disabled={busy} onChange={(e) => setServer(e.target.value)} />
              </div>
              <div>
                <label htmlFor="fulfil-account" className="desk-label block mb-1">Link to account</label>
                <Select id="fulfil-account" value={accountId} disabled={busy} onChange={(e) => setAccountId(e.target.value)}>
                  <option value="">Do not link</option>
                  {linkable.map((a) => (
                    <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                      {`${a.nickname ?? a.trader_login} (MT5 ${a.mt5?.login ?? a.ctid_trader_account_id})`}
                    </option>
                  ))}
                </Select>
                <p className="mt-1 text-xs text-ink-soft">
                  Optional: link the MirrorFleet MT5 account so the investor sees its equity and positions.
                </p>
              </div>
              <div>
                <label htmlFor="fulfil-note" className="desk-label block mb-1">Note (optional)</label>
                <textarea id="fulfil-note" value={note} rows={2} disabled={busy}
                          onChange={(e) => setNote(e.target.value)} className={TEXTAREA} />
              </div>
              <Button type="submit" disabled={busy}>Fulfil request</Button>
            </form>
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={rejecting != null}
        title={rejecting ? `Reject the account request of ${who(rejecting)}` : ''}
        confirmLabel="Reject"
        danger
        busy={busy}
        disabled={rejectNote.trim() === ''}
        onConfirm={() => { void reject() }}
        onCancel={() => setRejecting(null)}
      >
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        <label className="block">
          <span className="desk-label block mb-1">Note</span>
          <textarea aria-label="Note" value={rejectNote} rows={2} onChange={(e) => setRejectNote(e.target.value)}
                    className={TEXTAREA} />
        </label>
      </ConfirmDialog>
    </>
  )
}
```

- [ ] **Step 5: Wire the tabs into the desk**

In `dashboard/src/pages/requests/RequestTabs.tsx` add below `REQUEST_KINDS`:

```ts
/** The desk's tabs: the four money queues plus phase 2's two identity queues. */
export type DeskTab = RequestKind | 'kyc' | 'account_requests'
export const DESK_TABS: DeskTab[] = [...REQUEST_KINDS, 'kyc', 'account_requests']
```

and replace `tabItems`:

```ts
export function tabItems(summary: RequestsSummary | null): TabItem[] {
  // `?? 0`: an api that predates a queue answers without its count.
  const n = (k: Exclude<keyof RequestsSummary, 'total'>) => summary?.[k] ?? 0
  return [
    { key: 'deposits', label: `Deposits (${n('deposits')})` },
    { key: 'withdrawals', label: `Withdrawals (${n('withdrawals')})` },
    { key: 'transfers', label: `Transfers (${n('transfers')})` },
    { key: 'payout_destinations', label: `Payout accounts (${n('payout_destinations')})` },
    { key: 'kyc', label: `Verification (${n('kyc')})` },
    { key: 'account_requests', label: `Account requests (${n('account_requests')})` },
  ]
}
```

In `dashboard/src/pages/Requests.tsx`:
- import `DESK_TABS, type DeskTab` from `./requests/RequestTabs` (alongside the existing names) and `AccountRequestsTab from './requests/AccountRequestsTab'`, `VerificationTab from './requests/VerificationTab'`;
- replace `isKind` with

```ts
function isTab(v: string | null): v is DeskTab {
  return DESK_TABS.includes(v as DeskTab)
}
```

- the tab state becomes `const [tab, setTab] = useState<DeskTab>(() => { const t = searchParams.get('tab'); return isTab(t) ? t : 'deposits' })`, and the Tabs `onChange` becomes `(k) => setTab(k as DeskTab)`;
- add, after `const control = can(role, 'control')`:

```ts
  // The identity tabs load their own queues; a decision there refreshes the
  // summary counts here and is announced on the page banner.
  const tabDone = (message: string) => { setNotice(message); void refresh() }
```

- inside `<div className="overflow-x-auto">`, after the `payout_destinations` block, add:

```tsx
              {tab === 'kyc' && (
                <VerificationTab orgId={orgId} control={control} show={show} onDone={tabDone} onError={setError} />
              )}
              {tab === 'account_requests' && (
                <AccountRequestsTab orgId={orgId} control={control} show={show} onDone={tabDone} onError={setError} />
              )}
```

- the PageHeader subtitle becomes: "Every request waiting on you: deposit notices to confirm, withdrawals to approve and pay, transfers to fund, payout accounts to vet, identities to verify and trading accounts to open. The app records; you move the funds."

- [ ] **Step 6: Run the tests and the type check**

Run: `npx vitest run src/pages/requests src/pages/Requests.test.tsx`
Expected: PASS, no `act(...)` warnings.

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/pages/requests dashboard/src/pages/Requests.tsx dashboard/src/pages/Requests.test.tsx
git commit -m "feat(dashboard): Requests desk -- Verification tab with documents and decisions; Account requests tab with MPIN reveal, fulfil and reject

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 16: Investors packages tab and KYC column; dashboard verification card; investor nav

**Files:**
- Create: `dashboard/src/pages/investors/PackagesTab.tsx`, `PackagesTab.test.tsx`
- Modify: `dashboard/src/pages/Investors.tsx`, `dashboard/src/pages/Investors.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorDashboard.tsx`, `InvestorDashboard.test.tsx`
- Modify: `dashboard/src/components/layout/nav.ts`, `nav.test.ts`, `NavRail.test.tsx`, `dashboard/src/components/Layout.test.tsx`

**Interfaces:**
- Consumes: `GET/POST account-packages`, `PATCH/DELETE account-packages/{id}` (Task 7); `InvestorRow.kyc_status`, `InvestorSummary.kyc_status` (Task 6); `kycLabel`, `kycBadge`; `packageFixture`, `investorRowFixture`, `summaryFixture`.
- Produces: Investors tab `packages`; column `Verification`; Card `Identity verification`; `investorNav` groups `['', 'Money', 'Account']`.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/investors/PackagesTab.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import PackagesTab from './PackagesTab'
import { packageFixture } from '../../test/portalFixtures'
import type { AccountPackage } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(opts: { deleteRefused?: boolean } = {}) {
  let rows: AccountPackage[] = [packageFixture()]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/account-packages') && method === 'GET') return jsonResponse(rows)
    if (url.endsWith('/account-packages') && method === 'POST') {
      const body = JSON.parse(String(init!.body)) as Partial<AccountPackage>
      const created = packageFixture({ ...body, id: 2, min_deposit: Number(body.min_deposit) })
      rows = [...rows, created]
      return jsonResponse(created, 201)
    }
    const m = url.match(/\/account-packages\/(\d+)$/)
    if (m && method === 'PATCH') {
      const body = JSON.parse(String(init!.body)) as Partial<AccountPackage>
      rows = rows.map((r) => r.id === Number(m[1]) ? { ...r, ...body } : r)
      return jsonResponse(rows.find((r) => r.id === Number(m[1])))
    }
    if (m && method === 'DELETE') {
      if (opts.deleteRefused) return jsonResponse({ detail: 'an open request still uses this package' }, 409)
      rows = rows.filter((r) => r.id !== Number(m[1]))
      return jsonResponse(null, 204)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof mockRoutes>, method: string) =>
  JSON.parse(String((fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === method)![1] as RequestInit).body))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists packages with their terms', async () => {
  mockRoutes()
  render(<PackagesTab orgId={1} control />)
  expect(await screen.findByText('Standard')).toBeInTheDocument()
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()
  expect(screen.getByText('1:100 · 1:200 · 1:500')).toBeInTheDocument()
  expect(screen.getByText('Enabled')).toBeInTheDocument()
})

test('adds a package from the drawer with a parsed leverage list', async () => {
  const fetchMock = mockRoutes()
  render(<PackagesTab orgId={1} control />)
  await userEvent.click(await screen.findByRole('button', { name: 'Add package' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add account package' })
  await userEvent.type(within(drawer).getByLabelText('Name'), 'Pro')
  await userEvent.clear(within(drawer).getByLabelText('Minimum deposit'))
  await userEvent.type(within(drawer).getByLabelText('Minimum deposit'), '1000')
  await userEvent.clear(within(drawer).getByLabelText('Leverage options'))
  await userEvent.type(within(drawer).getByLabelText('Leverage options'), '200, 100')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save package' }))
  expect(await screen.findByText('Pro')).toBeInTheDocument()
  expect(bodyOf(fetchMock, 'POST')).toEqual({
    name: 'Pro', min_deposit: '1000', currency: 'USD', spread_label: '', leverage_options: [200, 100], sort_order: 0,
  })
})

test('a bad leverage list is refused in the drawer', async () => {
  const fetchMock = mockRoutes()
  render(<PackagesTab orgId={1} control />)
  await userEvent.click(await screen.findByRole('button', { name: 'Add package' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add account package' })
  await userEvent.type(within(drawer).getByLabelText('Name'), 'Pro')
  await userEvent.clear(within(drawer).getByLabelText('Leverage options'))
  await userEvent.type(within(drawer).getByLabelText('Leverage options'), '1:100')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save package' }))
  expect(await within(drawer).findByText('Leverage options: whole numbers from 1 to 3000, separated by commas')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === 'POST')).toBe(false)
})

test('disables a package and refuses a delete the server refuses', async () => {
  const fetchMock = mockRoutes({ deleteRefused: true })
  render(<PackagesTab orgId={1} control />)
  await userEvent.click(await screen.findByRole('button', { name: 'Disable Standard' }))
  expect(await screen.findByText('Disabled')).toBeInTheDocument()
  expect(bodyOf(fetchMock, 'PATCH')).toEqual({ enabled: false })
  await userEvent.click(screen.getByRole('button', { name: 'Delete Standard' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete Standard?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
  expect(await screen.findByText('an open request still uses this package')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('a viewer sees the packages but no actions', async () => {
  mockRoutes()
  render(<PackagesTab orgId={1} control={false} />)
  expect(await screen.findByText('Standard')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Add package' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Edit Standard' })).not.toBeInTheDocument()
})
```

In `dashboard/src/pages/Investors.test.tsx`: in `mockRoutes`, before its final `return jsonResponse({})`, add

```ts
    if (path.endsWith('/account-packages')) return jsonResponse([])
```

and append (`investorRowFixture` already carries `kyc_status: 'approved'`):

```tsx
test('the investors table shows each investor\'s verification, and the packages tab opens', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(await screen.findByRole('columnheader', { name: 'Verification' })).toBeInTheDocument()
  expect(screen.getByText('Verified')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Account packages' }))
  expect(await screen.findByRole('heading', { name: 'Account packages' })).toBeInTheDocument()
  expect(await screen.findByText('No packages yet. Investors cannot request an account until you add one.'))
    .toBeInTheDocument()
})
```

In `dashboard/src/pages/investor/InvestorDashboard.test.tsx`: make `afterEach` start with `cleanup()` (so the unmount happens before `setHidden(false)` broadcasts); append:

```tsx
test('the verification card sends an unverified investor to their profile', async () => {
  mockRoutes({ ...summaryFixture(), kyc_status: 'draft' })
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const card = (await screen.findByRole('heading', { name: 'Identity verification' })).closest('section')!
  expect(within(card).getByText('Not submitted')).toBeInTheDocument()
  expect(within(card).getByRole('link', { name: 'Verify now' })).toHaveAttribute('href', '/org/1/invest/profile')
})

test('a verified investor without an account is offered Open account', async () => {
  mockRoutes({ ...summaryFixture(), kyc_status: 'approved', link_state: 'unlinked', account: null })
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const card = (await screen.findByRole('heading', { name: 'Identity verification' })).closest('section')!
  expect(within(card).getByRole('link', { name: 'Open account' })).toHaveAttribute('href', '/org/1/invest/open-account')
})
```

(The file's `mockRoutes(summaries, entries?)` serves the summary given; `cleanup` and `within` are already imported.)

In `dashboard/src/components/layout/nav.test.ts` replace the first test with:

```ts
test('investorNav is three groups: the Dashboard alone, Money, and Account', () => {
  const groups = investorNav(7)
  expect(groups.map((g) => g.name)).toEqual(['', 'Money', 'Account'])
  expect(groups[0].items).toEqual([{ path: '/org/7/invest', label: 'Dashboard', end: true }])
  expect(groups[1].items.map((i) => [i.label, i.path])).toEqual([
    ['Wallet', '/org/7/invest/wallet'],
    ['Deposit', '/org/7/invest/deposit'],
    ['Withdraw', '/org/7/invest/withdraw'],
    ['Transfer', '/org/7/invest/transfer'],
    ['Transactions', '/org/7/invest/transactions'],
    ['Payout accounts', '/org/7/invest/payout-accounts'],
  ])
  expect(groups[2].items.map((i) => [i.label, i.path])).toEqual([
    ['Trading account', '/org/7/invest/account'],
    ['Profile & verification', '/org/7/invest/profile'],
    ['Open account', '/org/7/invest/open-account'],
    ['Security', '/org/7/invest/security'],
    ['History', '/org/7/invest/history'],
  ])
  expect(groups.flatMap((g) => g.items)).toHaveLength(12)
})
```

In `dashboard/src/components/layout/NavRail.test.tsx` (the viewer/investor test): `getAllByRole('link')` length `9` → `12`; `getByText('Trading')` → `getByText('Account')`; rename the test title's "nine portal links in Money and Trading groups" to "twelve portal links in Money and Account groups".

In `dashboard/src/components/Layout.test.tsx` (`'an investor sees the grouped portal nav…'`): the label list becomes `['Dashboard', 'Wallet', 'Deposit', 'Withdraw', 'Transfer', 'Transactions', 'Payout accounts', 'Trading account', 'Profile & verification', 'Open account', 'Security', 'History']` and `getByText('Trading')` becomes `getByText('Account')`.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/investors/PackagesTab.test.tsx src/pages/Investors.test.tsx src/pages/investor/InvestorDashboard.test.tsx src/components/layout src/components/Layout.test.tsx`
Expected: FAIL — `./PackagesTab` does not resolve; no Verification column/card; nav still has `Trading`.

- [ ] **Step 3: The packages tab**

Create `dashboard/src/pages/investors/PackagesTab.tsx`:

```tsx
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, money } from '../../lib/format'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { AccountPackage } from '../../lib/types'

interface Form { name: string; min_deposit: string; currency: string; spread_label: string; leverage: string; sort_order: string }

const EMPTY: Form = { name: '', min_deposit: '0', currency: 'USD', spread_label: '', leverage: '100, 200, 500', sort_order: '0' }
const AMOUNT = /^\d+(\.\d{1,2})?$/
const FIELDS: { key: keyof Form; label: string; num?: boolean }[] = [
  { key: 'name', label: 'Name' },
  { key: 'min_deposit', label: 'Minimum deposit', num: true },
  { key: 'currency', label: 'Currency' },
  { key: 'spread_label', label: 'Spread' },
  { key: 'leverage', label: 'Leverage options', num: true },
  { key: 'sort_order', label: 'Sort order', num: true },
]

function formOf(p: AccountPackage): Form {
  return {
    name: p.name, min_deposit: String(p.min_deposit), currency: p.currency, spread_label: p.spread_label ?? '',
    leverage: p.leverage_options.join(', '), sort_order: String(p.sort_order),
  }
}

/** "100, 200 500" -> [100, 200, 500]; null unless every part is a whole number 1-3000. */
export function parseLeverage(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean)
  if (parts.length === 0 || parts.some((p) => !/^\d+$/.test(p))) return null
  const nums = parts.map(Number)
  return nums.every((n) => n >= 1 && n <= 3000) ? nums : null
}

type Editing = { mode: 'add' } | { mode: 'edit'; pkg: AccountPackage }

/** What investors may request: name, minimum deposit, spread and leverage
 *  choices. Loads its own list; investors cannot request an account until
 *  at least one package is enabled. */
export default function PackagesTab({ orgId, control }: { orgId: number; control: boolean }) {
  const [rows, setRows] = useState<AccountPackage[] | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [form, setForm] = useState<Form>(EMPTY)
  const [formError, setFormError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<AccountPackage | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await orgApi<AccountPackage[]>(orgId, 'account-packages'))
    } catch (err) {
      setError(errorText(err, 'Could not load the account packages'))
      setRows((r) => r ?? [])
    }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const openAdd = () => { setEditing({ mode: 'add' }); setForm(EMPTY); setFormError(null) }
  const openEdit = (pkg: AccountPackage) => { setEditing({ mode: 'edit', pkg }); setForm(formOf(pkg)); setFormError(null) }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    if (!form.name.trim()) { setFormError('Name is required'); return }
    if (!AMOUNT.test(form.min_deposit.trim())) { setFormError('Minimum deposit: a number with at most two decimals'); return }
    const leverage = parseLeverage(form.leverage)
    if (!leverage) { setFormError('Leverage options: whole numbers from 1 to 3000, separated by commas'); return }
    const body = {
      name: form.name.trim(), min_deposit: form.min_deposit.trim(), currency: form.currency.trim() || 'USD',
      spread_label: form.spread_label.trim(), leverage_options: leverage, sort_order: Number(form.sort_order) || 0,
    }
    setBusy(true); setFormError(null)
    try {
      if (editing.mode === 'add') {
        await orgApi(orgId, 'account-packages', { method: 'POST', body: JSON.stringify(body) })
      } else {
        await orgApi(orgId, `account-packages/${editing.pkg.id}`, { method: 'PATCH', body: JSON.stringify(body) })
      }
      setEditing(null)
      setNotice('Package saved')
      await load()
    } catch (err) {
      setFormError(errorText(err, 'Could not save the package'))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (pkg: AccountPackage) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi(orgId, `account-packages/${pkg.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !pkg.enabled }) })
      setNotice(`${pkg.name} ${pkg.enabled ? 'disabled' : 'enabled'}`)
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not change the package'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!deleting) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi(orgId, `account-packages/${deleting.id}`, { method: 'DELETE' })
      setNotice('Package deleted')
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not delete the package'))
    } finally {
      setBusy(false)
      setDeleting(null)
    }
  }

  return (
    <div className="space-y-4">
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      <Card title="Account packages" inset
            actions={control ? <Button size="sm" onClick={openAdd}>Add package</Button> : undefined}>
        {rows == null ? (
          <div className="p-4"><Loading lines={3} label="Loading packages" /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Name</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Minimum deposit</th>
                  <th className="desk-label px-4 py-2 font-semibold">Spread</th>
                  <th className="desk-label px-4 py-2 font-semibold">Leverage</th>
                  <th className="desk-label px-4 py-2 font-semibold">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={6} className="text-center py-8 text-ink-faint">
                    No packages yet. Investors cannot request an account until you add one.
                  </td></tr>
                )}
                {rows.map((p) => (
                  <tr key={p.id} className="border-b border-line last:border-0 align-top">
                    <td data-label="Name" className="px-4 py-2.5 text-ink">{p.name}</td>
                    <td data-label="Minimum deposit" className="num px-4 py-2.5 text-right">{money(p.min_deposit, p.currency)}</td>
                    <td data-label="Spread" className="num px-4 py-2.5">{p.spread_label ?? '—'}</td>
                    <td data-label="Leverage" className="num px-4 py-2.5">{p.leverage_options.map((l) => `1:${l}`).join(' · ')}</td>
                    <td data-label="Status" className="px-4 py-2.5">
                      <Badge tone={p.enabled ? 'profit' : 'neutral'}>{p.enabled ? 'Enabled' : 'Disabled'}</Badge>
                    </td>
                    <td className="px-4 py-2.5">
                      {control && (
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <Button variant="ghost" size="sm" aria-label={`Edit ${p.name}`} disabled={busy}
                                  onClick={() => openEdit(p)}>Edit</Button>
                          <Button variant="ghost" size="sm" aria-label={`${p.enabled ? 'Disable' : 'Enable'} ${p.name}`}
                                  disabled={busy} onClick={() => { void toggle(p) }}>
                            {p.enabled ? 'Disable' : 'Enable'}
                          </Button>
                          <Button variant="ghost" tone="loss" size="sm" aria-label={`Delete ${p.name}`} disabled={busy}
                                  onClick={() => setDeleting(p)}>Delete</Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Drawer open={editing != null}
              title={editing?.mode === 'edit' ? `Edit ${editing.pkg.name}` : 'Add account package'}
              onClose={() => setEditing(null)} busy={busy}>
        <form onSubmit={save} noValidate className="space-y-4">
          {formError && <Banner kind="error" onDismiss={() => setFormError(null)}>{formError}</Banner>}
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label htmlFor={`package-${f.key}`} className="desk-label block mb-1">{f.label}</label>
              <Input id={`package-${f.key}`} num={f.num} value={form[f.key]} disabled={busy}
                     onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
              {f.key === 'leverage' && <p className="mt-1 text-xs text-ink-soft">e.g. 100, 200, 500</p>}
              {f.key === 'spread_label' && <p className="mt-1 text-xs text-ink-soft">Shown as written, e.g. 20-25 (optional)</p>}
            </div>
          ))}
          <Button type="submit" disabled={busy}>Save package</Button>
        </form>
      </Drawer>

      <ConfirmDialog
        open={deleting != null}
        title={`Delete ${deleting?.name ?? ''}?`}
        confirmLabel="Delete"
        danger
        busy={busy}
        onConfirm={() => { void remove() }}
        onCancel={() => setDeleting(null)}
      >
        <p>Requests already decided keep the package name. A package with an open request cannot be deleted; disable it instead.</p>
      </ConfirmDialog>
    </div>
  )
}
```

- [ ] **Step 4: Investors page — the tab and the column**

In `dashboard/src/pages/Investors.tsx`:
- import `PackagesTab from './investors/PackagesTab'` and `{ kycBadge, kycLabel } from '../lib/identity'`;
- `type Tab = 'investors' | 'methods' | 'packages'`;
- Tabs `items` gain `{ key: 'packages', label: 'Account packages' }` (third);
- the panel's ternary becomes three-way:

```tsx
          {tab === 'investors' ? (
            /* the existing investors Card, unchanged except the column below */
          ) : tab === 'methods' ? (
            <PaymentMethodsTab orgId={orgId} control={control} methods={methods}
                               settings={settings} busy={busy} run={run} />
          ) : (
            <PackagesTab orgId={orgId} control={control} />
          )}
```

- in the investors table add `<th className="desk-label px-5 py-2 font-semibold">Verification</th>` after the Email header, the matching cell after the Email cell:

```tsx
                        <td data-label="Verification" className="px-5 py-2.5">
                          <Badge tone={kycBadge(r.kyc_status)}>{kycLabel(r.kyc_status)}</Badge>
                        </td>
```

  and the empty row's `colSpan={7}` becomes `colSpan={8}`.

- [ ] **Step 5: The dashboard verification card**

In `dashboard/src/pages/investor/InvestorDashboard.tsx` import `{ kycBadge, kycLabel }` from `'../../lib/identity'` and `KycStatus` from the types, add above the default export:

```tsx
const KYC_TEXT: Record<KycStatus, string> = {
  draft: 'Complete your profile and upload your ID to open a trading account.',
  submitted: 'An admin is reviewing your documents.',
  approved: 'You are verified.',
  rejected: 'Your verification was rejected. Open your profile to see why and submit again.',
}

/** The reference's KYC ring, as one card: status, one sentence, one link. */
function VerificationCard({ status, linked, base }: { status: KycStatus; linked: boolean; base: string }) {
  const action = status === 'draft' || status === 'rejected' ? { label: 'Verify now', to: `${base}/profile` }
    : status === 'submitted' ? { label: 'View profile', to: `${base}/profile` }
    : linked ? null
    : { label: 'Open account', to: `${base}/open-account` }
  return (
    <Card title="Identity verification" actions={<Badge tone={kycBadge(status)}>{kycLabel(status)}</Badge>}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-soft">{KYC_TEXT[status]}</p>
        {action && <Button variant="secondary" size="sm" to={action.to}>{action.label}</Button>}
      </div>
    </Card>
  )
}
```

and render it right after the greeting `Card` (inside `{summary && (<> … </>)}`):

```tsx
          <VerificationCard status={summary.kyc_status} linked={summary.link_state === 'linked'} base={base} />
```

- [ ] **Step 6: The investor nav**

In `dashboard/src/components/layout/nav.ts` replace the third group of `investorNav`:

```ts
    {
      name: 'Account',
      items: [
        { path: `${p}/account`, label: 'Trading account' },
        { path: `${p}/profile`, label: 'Profile & verification' },
        { path: `${p}/open-account`, label: 'Open account' },
        { path: `${p}/security`, label: 'Security' },
        { path: `${p}/history`, label: 'History' },
      ],
    },
```

and its doc comment becomes `/** The investor portal: the dashboard, their money, their account and identity. */`.

- [ ] **Step 7: Run the tests and the type check**

Run: `npx vitest run src/pages/investors src/pages/Investors.test.tsx src/pages/investor/InvestorDashboard.test.tsx src/components/layout src/components/Layout.test.tsx`
Expected: PASS, no `act(...)` warnings.

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/pages/investors/PackagesTab.tsx dashboard/src/pages/investors/PackagesTab.test.tsx dashboard/src/pages/Investors.tsx dashboard/src/pages/Investors.test.tsx dashboard/src/pages/investor/InvestorDashboard.tsx dashboard/src/pages/investor/InvestorDashboard.test.tsx dashboard/src/components/layout/nav.ts dashboard/src/components/layout/nav.test.ts dashboard/src/components/layout/NavRail.test.tsx dashboard/src/components/Layout.test.tsx
git commit -m "feat(dashboard): Investors gains Account packages and a Verification column; dashboard verification card; investor nav Account group

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

### Task 17: Gates and docs

**Files:**
- Modify: `README.md` (the "Upgrading with a migration" section)
- Modify: `docs/superpowers/specs/2026-10-01-client-portal-phase-2-identity-design.md` (status line)
- Modify: `docs/superpowers/plans/2026-10-01-client-portal-phase-2.md` (tick the checkboxes)

**Interfaces:**
- Consumes: everything above.
- Produces: a green branch and the deploy notes.

- [ ] **Step 1: Full API suite**

From `api/` with the env of the Global Constraints:

Run: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 known `test_events_ws.py` errors and the 1 EA-download CRLF failure.

- [ ] **Step 2: Full dashboard gate**

From `dashboard/`:

Run: `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`
Expected: palette prover passes, no type errors, every test passes, and the output contains no `act(` warning (check with `npx vitest run --maxWorkers=2 --minWorkers=1 2>&1 | grep -c "not wrapped in act"` → `0`).

Run: `npm run build`
Expected: build succeeds.

- [ ] **Step 3: `copier/` untouched**

Run: `git diff --stat portal-followups..HEAD -- copier/`
Expected: prints nothing.

- [ ] **Step 4: README runbook**

In `README.md`, directly after the paragraph that starts "Take the backup first because a migration can be one-way", add:

```markdown
Migration 023 (client portal phase 2: identity verification, account packages,
account requests, sign-in history) is additive -- it creates four tables and
drops nothing -- so the same sequence applies and `migrate` prints
`applied: ['023_portal_identity.sql']`. After the upgrade, an admin adds at least
one package under **Investors → Account packages**; until then investors see
"No account packages yet" on **Open account**. Changing the password now asks
for the MPIN, on the desk and in the portal alike. The two MT5 passwords of an
open account request are sealed with `FERNET_KEY`: rotating that key makes them
unreadable, and the admin then rejects the request and asks for a new one.
```

- [ ] **Step 5: Spec status**

In the spec, replace `**Status:** draft, awaiting the owner's review` with
`**Status:** implemented on branch client-portal-phase-2 (plan docs/superpowers/plans/2026-10-01-client-portal-phase-2.md); awaiting deploy`.

- [ ] **Step 6: Tick this plan's checkboxes, then commit**

```bash
git add README.md docs/superpowers/specs/2026-10-01-client-portal-phase-2-identity-design.md docs/superpowers/plans/2026-10-01-client-portal-phase-2.md
git commit -m "docs: client portal phase 2 -- upgrade runbook for migration 023, spec status, plan ticked

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM"
```

---

## Spec decisions this plan makes (read before Task 1)

- **Reveal "once"** (spec 5, 8): the admin may reveal while the request is `requested`, as often as needed; every reveal is MPIN-confirmed and audited as a warning, and the passwords are gone once the request is decided. A strict single reveal would need a column the spec does not list and would strand a request whose admin closed the drawer by mistake.
- **Re-verification fields** (spec 4): only phone, address line, area, landmark, city, state and postal code keep an approved profile approved. Gender and country of residence are not on the spec's contact list, so changing them re-verifies.
- **Four files always** (spec 4: "the four files present"): a passport holder uploads the address or signature page as the "ID back".
- **Investor nav** (spec 7 lists Profile & verification, Open account, Security, History under Account): the existing Account page stays reachable as `Trading account`, first in the group, because spec 5 shows the fulfilled login there.
- **Requests desk counts**: `requests/summary` gains `kyc` and `account_requests`, both inside `total`, so the admin's rail badge counts the new queues.
- **Verification card data**: `investor/summary` gains `kyc_status` instead of the dashboard fetching the profile.
- **Extra audit actions** beyond spec 5's list, all info: `investor_profile_saved` (field names only), `investor_account_request_cancelled`, and `investor_account_linked` when a fulfil links an account (the phase 1 action for a link).
- **Extra refusals**: the investor password must differ from the main password (MT5 refuses equal ones); `PUT investor/profile` refuses unknown keys; the database enforces the password wipe and a fulfilled request's login with two CHECK constraints.
