# Multi-Org RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the single-user, single-portfolio copy-trading system into a multi-user, multi-organization application with per-org role-based access control (Owner/Admin/Trader/Viewer), where each org owns exactly one copy-trading book (one master, N slaves, its own kill switch and dry-run flag).

**Architecture:** One new SQL migration introduces `users`/`orgs`/`org_memberships`/`org_invites` and stamps `org_id` onto every tenant-owned table. The FastAPI layer replaces the boolean `require_admin` with a `require_org_role(min_role)` dependency and moves every org-owned route under `/api/orgs/{org_id}/…`. The single copier process partitions its engine per org (routing, settings gates, reconcile, kill switch). The React dashboard gains register/login/join/members pages and an org-scoped URL space `/org/:orgId/…`.

**Tech Stack:** Postgres 16 (raw SQL, no ORM), FastAPI + psycopg3 + itsdangerous + argon2, Python 3.12 + Twisted (copier), React 18 + Vite + TypeScript + Tailwind v4 (dashboard), pytest / pytest-twisted / vitest.

**Spec:** `docs/superpowers/specs/2026-08-18-multi-org-rbac-design.md` — the plan argues from the spec; read both.

## Global Constraints

- **Test DB:** pytest suites need env vars pointing at host port **5435** with the password from the repo-root `.env`. Before any pytest run, from the repo root:
  ```bash
  PW=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2)
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${PW}@localhost:5435/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${PW}@localhost:5435/copytrader_test"
  ```
- **Never run two test suites concurrently** (api and copier share the `copytrader_test` scratch DB), and never run the compose e2e on this machine while other suites run.
- **Suite commands:** api: `cd api && .venv/bin/pytest tests`; copier: `cd copier && .venv/bin/pytest tests --timeout=60` (~9 min — run targeted files during development, the full suite at task end); dashboard: `cd dashboard && npm test` (runs `tsc --noEmit` then vitest).
- **No ORM:** all SQL is inline strings via psycopg3; follow that convention.
- **Role order (everywhere):** `viewer < trader < admin < owner`. Non-member → **404**; member below required role → **403**.
- **Real-money endpoints** (`orders`, `positions/close`, `orders/cancel`, `control/close-all`) are the highest-stakes checks — never weaken their tests.
- **Session mechanism unchanged:** itsdangerous cookie + CSRF double-submit; only the payload changes to `{"user_id": <id>}`.
- Commit after every task with a conventional-commit message ending in `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Working directory is the worktree root `/Users/srimanikandanr/.superset/projects/trading-bot/.claude/worktrees/multi-org` (branch `worktree-multi-org`). Never touch the main checkout.

## File Structure

New files:
- `db/migrations/005_multi_org.sql` — schema + backfill
- `api/src/api/rbac.py` — `OrgContext`, `ROLE_RANK`, `require_user`, `require_org_role`
- `api/src/api/routes/orgs.py` — org CRUD, members, invites, join
- `api/tests/test_orgs.py`, `api/tests/test_rbac_matrix.py`, `api/tests/test_migration_005.py`
- `copier/src/copier/engine/routing.py` — `OrgRouting` + `build_routing()`
- `copier/tests/unit/test_routing.py`, plus per-org tests added to existing files
- `dashboard/src/lib/org.tsx` — org context provider + `useOrg()`
- `dashboard/src/lib/roles.ts` — `can()` permission helper
- `dashboard/src/pages/Register.tsx`, `Welcome.tsx`, `Join.tsx`, `Members.tsx` (+ tests)
- `e2e/test_multi_org.py`

Heavily modified: `api/src/api/auth.py`, `config.py`, `main.py`, `ws.py`, `oauth.py`, all of `api/src/api/routes/`, `copier/src/copier/db/repo.py`, `engine/service.py`, `engine/dispatch.py`, `engine/reconcile.py`, `engine/control.py`, `copier/src/copier/main.py`, `dashboard/src/App.tsx`, `components/Layout.tsx`, `lib/api.ts`, `lib/types.ts`, every dashboard page, both `conftest.py` files, `e2e/test_full_stack.py` seeds stay as-is (org A), `.env.example`, `README.md`.

---

### Task 1: Migration 005 — schema, backfill, conftest updates

**Files:**
- Create: `db/migrations/005_multi_org.sql`
- Create: `api/tests/test_migration_005.py`
- Modify: `api/tests/conftest.py:41-46` (truncate list + admin bootstrap removal comes in Task 2; here only the truncate list)
- Modify: `copier/tests/conftest.py:34-39`

**Interfaces:**
- Consumes: existing schema from migrations 001–004.
- Produces: tables `users`, `orgs`, `org_memberships`, `org_invites`; `org_id` columns on `ctid_connections`, `accounts`, `mappings`, `events` (nullable), `oauth_states`; `accounts_single_master` unique per org; `settings` reduced to `shards`; `admin` dropped. Later tasks rely on these exact names.

- [ ] **Step 1: Write the failing migration test**

`api/tests/test_migration_005.py`:

```python
"""Migration 005: multi-org schema. Runs against the session-scoped test DB
(which conftest builds by applying ALL migrations to a fresh database), so
these tests assert the post-migration shape. The legacy-backfill path is
exercised separately by building a scratch DB stopped at 004."""
import pathlib

import psycopg
import pytest

from .conftest import ADMIN_DSN

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
BACKFILL_DB = "copytrader_mig005"
BACKFILL_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{BACKFILL_DB}"


def test_new_tables_exist(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
    assert {"users", "orgs", "org_memberships", "org_invites"} <= names
    assert "admin" not in names


def test_org_id_columns_and_master_index(db):
    with psycopg.connect(db, autocommit=True) as conn:
        cols = {
            (r[0], r[1]) for r in conn.execute(
                """SELECT table_name, column_name FROM information_schema.columns
                   WHERE column_name = 'org_id'"""
            )
        }
        assert {"ctid_connections", "accounts", "mappings", "events",
                "oauth_states"} <= {t for t, _ in cols}
        # settings keeps only process config
        settings_cols = {
            r[0] for r in conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'settings'"""
            )
        }
        assert settings_cols == {"id", "shards"}
        # the master-uniqueness index is per-org now
        (indexdef,) = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'accounts_single_master'"
        ).fetchone()
        assert "(org_id)" in indexdef and "role = 'master'" in indexdef


def test_two_masters_allowed_across_orgs_not_within(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_a = conn.execute(
            "INSERT INTO orgs (name) VALUES ('A') RETURNING id").fetchone()[0]
        org_b = conn.execute(
            "INSERT INTO orgs (name) VALUES ('B') RETURNING id").fetchone()[0]
        conn_a = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days') RETURNING id""",
            (org_a,)).fetchone()[0]
        conn_b = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days') RETURNING id""",
            (org_b,)).fetchone()[0]

        def add_account(org, connection, acc_id, role):
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                         org_id, trader_login, is_live, role)
                   VALUES (%s, %s, %s, %s, false, %s)""",
                (acc_id, connection, org, acc_id, role))

        add_account(org_a, conn_a, 100, "master")
        add_account(org_b, conn_b, 200, "master")  # second master, other org: OK
        with pytest.raises(psycopg.errors.UniqueViolation):
            add_account(org_a, conn_a, 101, "master")  # second master, same org


def test_legacy_backfill_creates_default_org(database):
    """Apply 001–004 to a scratch DB, seed legacy single-tenant data, then
    apply 005 and assert everything landed in a 'Default' org."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {BACKFILL_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {BACKFILL_DB}")
    try:
        with psycopg.connect(BACKFILL_DSN) as conn:
            for name in ("001_initial.sql", "002_oauth_states.sql",
                         "003_mapping_fill_price.sql", "004_account_nickname.sql"):
                conn.execute((MIGRATIONS_DIR / name).read_text())
            conn.execute(
                """INSERT INTO ctid_connections
                   (access_token_enc, refresh_token_enc, granted_at, expires_at)
                   VALUES ('x', 'x', now(), now() + interval '30 days')""")
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                         trader_login, is_live, role)
                   VALUES (100, 1, 100, false, 'master')""")
            conn.execute("UPDATE settings SET dry_run = true")
            conn.execute((MIGRATIONS_DIR / "005_multi_org.sql").read_text())
            conn.commit()
        with psycopg.connect(BACKFILL_DSN, autocommit=True) as conn:
            org = conn.execute(
                "SELECT id, name, copying_enabled, dry_run FROM orgs").fetchone()
            assert org[1] == "Default"
            assert org[2] is True and org[3] is True  # copied from old settings
            (acc_org,) = conn.execute(
                "SELECT org_id FROM accounts WHERE ctid_trader_account_id = 100"
            ).fetchone()
            assert acc_org == org[0]
            (conn_org,) = conn.execute(
                "SELECT org_id FROM ctid_connections").fetchone()
            assert conn_org == org[0]
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {BACKFILL_DB} WITH (FORCE)")


def test_fresh_db_has_no_orgs(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM orgs").fetchone()
    assert count == 0
```

Note: `test_migration_005.py` uses the `db` fixture, whose truncate list you update in Step 3 — until the migration file exists, tests fail at session setup (`apply_migrations` won't create the new tables), which is the failure you want to see.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd api && .venv/bin/pytest tests/test_migration_005.py -x -q
```
Expected: FAIL/ERROR — `relation "users" does not exist` (or truncate list errors), because `005_multi_org.sql` doesn't exist yet.

- [ ] **Step 3: Write the migration and update both conftest truncate lists**

Create `db/migrations/005_multi_org.sql`:

```sql
-- Multi-org: users, orgs, memberships, invites; org_id on every tenant-owned
-- table; per-org master uniqueness; settings reduced to process config; the
-- single-row admin table replaced by real users.

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));

CREATE TABLE orgs (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    copying_enabled BOOLEAN NOT NULL DEFAULT true,
    dry_run BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE org_memberships (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'trader', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);

CREATE TABLE org_invites (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'trader', 'viewer')),
    token_hash TEXT NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX org_invites_by_org ON org_invites (org_id);

ALTER TABLE ctid_connections ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;
ALTER TABLE accounts ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;
ALTER TABLE mappings ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;
-- No FK on events: the audit log must survive org deletion.
ALTER TABLE events ADD COLUMN org_id BIGINT;
ALTER TABLE oauth_states ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;

-- Legacy single-tenant deployments: gather everything into a 'Default' org,
-- carrying the old global kill-switch/dry-run values with it.
DO $$
DECLARE
    default_org_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM ctid_connections) OR EXISTS (SELECT 1 FROM accounts) THEN
        INSERT INTO orgs (name, copying_enabled, dry_run)
        SELECT 'Default', s.copying_enabled, s.dry_run FROM settings s WHERE s.id = TRUE
        RETURNING id INTO default_org_id;
        UPDATE ctid_connections SET org_id = default_org_id;
        UPDATE accounts SET org_id = default_org_id;
        UPDATE mappings SET org_id = default_org_id;
        UPDATE events SET org_id = default_org_id;
    END IF;
END $$;

-- OAuth states are short-lived CSRF state; in-flight flows may fail during
-- the upgrade, harmlessly.
DELETE FROM oauth_states;

ALTER TABLE ctid_connections ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE accounts ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE mappings ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE oauth_states ALTER COLUMN org_id SET NOT NULL;

DROP INDEX accounts_single_master;
CREATE UNIQUE INDEX accounts_single_master ON accounts (org_id) WHERE role = 'master';
CREATE INDEX mappings_by_org ON mappings (org_id);
CREATE INDEX events_by_org ON events (org_id, ts DESC);

ALTER TABLE settings DROP COLUMN copying_enabled;
ALTER TABLE settings DROP COLUMN dry_run;

DROP TABLE admin;
```

In `api/tests/conftest.py`, replace the `db` fixture body (lines 40-46):

```python
@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, mappings, symbol_cache, accounts, ctid_connections, "
            "oauth_states, org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET shards = 1")
    return database
```

In `copier/tests/conftest.py`, replace the `db` fixture body (lines 32-40) the same way:

```python
@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, mappings, symbol_cache, accounts, ctid_connections, "
            "org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET shards = 1")
    return database
```

(The old `UPDATE settings SET copying_enabled = true, dry_run = false, shards = 1` would now fail — those columns are gone. Existing tests that read/write those settings columns will break here; they are fixed in Tasks 2–13 as each component is converted. Run only `test_migration_005.py` for this task's green gate.)

- [ ] **Step 4: Run the migration test to verify it passes**

```bash
cd api && .venv/bin/pytest tests/test_migration_005.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/005_multi_org.sql api/tests/test_migration_005.py api/tests/conftest.py copier/tests/conftest.py
git commit -m "feat(db): multi-org schema — users, orgs, memberships, invites, org_id everywhere"
```

---

### Task 2: API authentication — users, register/login, bootstrap

**Files:**
- Modify: `api/src/api/auth.py` (rewrite most of it)
- Modify: `api/src/api/config.py:12,26,36-37,54`
- Modify: `api/src/api/main.py:41`
- Modify: `api/tests/conftest.py` (env vars + bootstrap; add `make_user` / `login_as` / `make_org` fixtures)
- Modify: `api/tests/test_auth.py` (rewrite)

**Interfaces:**
- Consumes: `users` table (Task 1).
- Produces (used by every later API task):
  - `require_user(session, cfg) -> int` — FastAPI dependency returning the authenticated `user_id`, raising 401.
  - Session payload: `{"user_id": <int>}` signed with the existing serializer (`salt="session"`, max_age 43200).
  - `ensure_bootstrap_user(dsn: str, email: str, password: str) -> None`.
  - `POST /api/register {email, password, display_name}` → 204 + cookies; `POST /api/login {email, password}` → 204 + cookies; `GET /api/me` → `{"user": {"id", "email", "display_name"}, "orgs": [{"id", "name", "role"}]}`.
  - CSRF exempt paths: `/api/login` **and** `/api/register`.
  - Test fixtures in `conftest.py`: `make_user(db) -> dict`, `login_as(client, user) -> None`, `make_org(db) -> callable` (see Step 3).
- `hash_password` / `verify_password` / `LoginRateLimiter` / `get_client_ip` / `get_session_serializer` keep their existing signatures.

- [ ] **Step 1: Write the failing tests**

Replace `api/tests/test_auth.py` with:

```python
"""Auth: register, login, sessions, /api/me, bootstrap."""


def test_register_sets_session_and_csrf_cookies(app_client):
    r = app_client.post("/api/register", json={
        "email": "ada@example.com", "password": "correct-horse", "display_name": "Ada"})
    assert r.status_code == 204
    assert "session" in app_client.cookies
    assert "csrf" in app_client.cookies
    me = app_client.get("/api/me").json()
    assert me["user"]["email"] == "ada@example.com"
    assert me["user"]["display_name"] == "Ada"
    assert me["orgs"] == []


def test_register_rejects_short_password(app_client):
    r = app_client.post("/api/register", json={
        "email": "b@example.com", "password": "short", "display_name": "B"})
    assert r.status_code == 400
    assert "10" in r.json()["detail"]


def test_register_duplicate_email_case_insensitive(app_client):
    body = {"email": "dup@example.com", "password": "long-enough-pw", "display_name": "D"}
    assert app_client.post("/api/register", json=body).status_code == 204
    body["email"] = "DUP@example.com"
    r = app_client.post("/api/register", json=body)
    assert r.status_code == 409


def test_login_with_email_and_password(app_client, make_user):
    user = make_user(email="carl@example.com", password="a-solid-password")
    r = app_client.post("/api/login", json={
        "email": "carl@example.com", "password": "a-solid-password"})
    assert r.status_code == 204
    assert app_client.get("/api/me").json()["user"]["id"] == user["id"]


def test_login_wrong_password_is_401(app_client, make_user):
    make_user(email="carl@example.com", password="a-solid-password")
    r = app_client.post("/api/login", json={
        "email": "carl@example.com", "password": "wrong-password!"})
    assert r.status_code == 401


def test_login_unknown_email_is_401_not_distinguishable(app_client):
    r = app_client.post("/api/login", json={
        "email": "ghost@example.com", "password": "whatever-long"})
    assert r.status_code == 401


def test_me_unauthenticated_is_401(app_client):
    assert app_client.get("/api/me").status_code == 401


def test_logout_clears_session(app_client, make_user):
    make_user(email="e@example.com", password="a-solid-password")
    app_client.post("/api/login", json={"email": "e@example.com", "password": "a-solid-password"})
    csrf = app_client.cookies.get("csrf")
    r = app_client.post("/api/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert app_client.get("/api/me").status_code == 401


def test_login_rate_limited_per_email_ip(app_client, make_user):
    make_user(email="rl@example.com", password="a-solid-password")
    for _ in range(5):
        app_client.post("/api/login", json={
            "email": "rl@example.com", "password": "wrong-password!"})
    r = app_client.post("/api/login", json={
        "email": "rl@example.com", "password": "a-solid-password"})
    assert r.status_code == 429


def test_bootstrap_user_claims_default_org(db):
    import psycopg
    from api.auth import ensure_bootstrap_user

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (name) VALUES ('Default')")
    ensure_bootstrap_user(db, "root@example.com", "bootstrap-password")
    ensure_bootstrap_user(db, "root@example.com", "bootstrap-password")  # idempotent
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            """SELECT u.email, m.role, o.name FROM org_memberships m
               JOIN users u ON u.id = m.user_id JOIN orgs o ON o.id = m.org_id"""
        ).fetchall()
    assert rows == [("root@example.com", "owner", "Default")]


def test_bootstrap_user_without_default_org_creates_only_user(db):
    import psycopg
    from api.auth import ensure_bootstrap_user

    ensure_bootstrap_user(db, "root@example.com", "bootstrap-password")
    with psycopg.connect(db, autocommit=True) as conn:
        (users,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        (memberships,) = conn.execute("SELECT COUNT(*) FROM org_memberships").fetchone()
    assert users == 1 and memberships == 0
```

- [ ] **Step 2: Add the shared fixtures to `api/tests/conftest.py`**

These are test infrastructure for this and every later task, so they land with the first test that needs them. Append at the end of `conftest.py`:

```python
@pytest.fixture
def make_user(db):
    """Create a user directly in the DB; returns {id, email, password, display_name}."""
    from api.auth import hash_password

    def _make(email="user@example.com", password="a-solid-password", display_name="User"):
        with psycopg.connect(db, autocommit=True) as conn:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (email, hash_password(password), display_name),
            ).fetchone()
        return {"id": user_id, "email": email, "password": password,
                "display_name": display_name}

    return _make


@pytest.fixture
def make_org(db):
    """Create an org with memberships: make_org(name, members=[(user, role), ...])."""

    def _make(name="Desk", members=()):
        with psycopg.connect(db, autocommit=True) as conn:
            (org_id,) = conn.execute(
                "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (name,)
            ).fetchone()
            for user, role in members:
                conn.execute(
                    "INSERT INTO org_memberships (org_id, user_id, role) "
                    "VALUES (%s, %s, %s)",
                    (org_id, user["id"], role),
                )
        return org_id

    return _make


@pytest.fixture
def login_as():
    """Log a TestClient in as a make_user() user (sets session+csrf cookies)."""

    def _login(client, user):
        r = client.post("/api/login", json={
            "email": user["email"], "password": user["password"]})
        assert r.status_code == 204, f"login failed: {r.status_code} {r.text}"

    return _login
```

Also in `conftest.py`: in `app_client`, `app_client_with_lifespan`, and `_set_live_test_env`, delete the two `ADMIN_BOOTSTRAP_PASSWORD` lines and both `ensure_admin(db, "hunter2!")` calls plus their `from api.auth import ensure_admin` imports (three sites each). Nothing replaces them — tests create users via `make_user`.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_auth.py -x -q
```
Expected: FAIL — `POST /api/register` returns 404 (route doesn't exist), `ImportError: ensure_bootstrap_user`.

- [ ] **Step 4: Rewrite `api/src/api/auth.py`**

Keep lines 1-35 (imports, `_hasher`, `hash_password`, `verify_password`) and `get_session_serializer`, `LoginRateLimiter`, `get_client_ip` as they are, with one change — the rate limiter is keyed by an arbitrary string now (docstring says "key" not "ip"); its logic is unchanged. Replace `ensure_admin` and `require_admin`, and rewrite `create_auth_router`:

```python
SESSION_MAX_AGE_S = 43200  # 12 hours
MIN_PASSWORD_LEN = 10


def ensure_bootstrap_user(dsn: str, email: str, password: str) -> None:
    """Idempotently create the bootstrap user; if a 'Default' org exists with
    zero memberships (the legacy-migration case), make them its Owner. This is
    the only way to claim a migrated legacy org (spec §3)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()
        if row:
            user_id = row[0]
        else:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (email, hash_password(password), email.split("@")[0]),
            ).fetchone()
        org = conn.execute(
            """SELECT o.id FROM orgs o
               WHERE o.name = 'Default'
                 AND NOT EXISTS (SELECT 1 FROM org_memberships m WHERE m.org_id = o.id)"""
        ).fetchone()
        if org:
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner') ON CONFLICT DO NOTHING",
                (org[0], user_id),
            )


def require_user(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
) -> int:
    """Dependency: the authenticated user's id from the session cookie."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    serializer = get_session_serializer(cfg)
    try:
        data = serializer.loads(session, max_age=SESSION_MAX_AGE_S)
        user_id = data.get("user_id")
        if isinstance(user_id, int):
            return user_id
    except (SignatureExpired, BadSignature):
        pass
    raise HTTPException(status_code=401, detail="Not authenticated")


def user_id_from_session_cookie(session: str, cfg: ApiConfig) -> Optional[int]:
    """Same check as require_user, usable outside Depends (the WebSocket)."""
    serializer = get_session_serializer(cfg)
    try:
        data = serializer.loads(session, max_age=SESSION_MAX_AGE_S)
        user_id = data.get("user_id")
        if isinstance(user_id, int):
            return user_id
    except (SignatureExpired, BadSignature):
        pass
    return None
```

In `CSRFMiddleware.dispatch`, replace the login-only exemption (line 121) with:

```python
            if request.url.path not in ("/api/login", "/api/register"):
```

Replace `create_auth_router` with:

```python
def _issue_session(response, cfg: ApiConfig, user_id: int):
    """Set a fresh session + CSRF cookie pair (login-time re-issue prevents
    session fixation)."""
    serializer = URLSafeTimedSerializer(cfg.session_secret, salt="session")
    session_cookie = serializer.dumps({"user_id": user_id})
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie("session", session_cookie, httponly=True, samesite="lax",
                        secure=cfg.cookie_secure, max_age=SESSION_MAX_AGE_S)
    response.set_cookie("csrf", csrf_token, httponly=False, samesite="lax",
                        secure=cfg.cookie_secure, max_age=SESSION_MAX_AGE_S)


def create_auth_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    """Router with register/login/logout/me."""
    from fastapi.responses import Response
    from pydantic import BaseModel

    class RegisterRequest(BaseModel):
        email: str
        password: str
        display_name: str

    class LoginRequest(BaseModel):
        email: str
        password: str

    router = APIRouter(prefix="/api", tags=["auth"])

    @router.post("/register")
    async def register(
        request_data: RegisterRequest,
        request: Request,
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        client_ip = get_client_ip(request, trust_proxy=False)
        if rate_limiter.is_limited(f"register:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")

        email = request_data.email.strip().lower()
        if "@" not in email or len(email) < 3:
            raise HTTPException(status_code=400, detail="A valid email is required")
        if len(request_data.password) < MIN_PASSWORD_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LEN} characters")
        display_name = request_data.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=400, detail="display_name is required")

        try:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (email, hash_password(request_data.password), display_name),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Email already registered")

        response = Response(status_code=204)
        _issue_session(response, cfg, user_id)
        return response

    @router.post("/login")
    async def login(
        request_data: LoginRequest,
        request: Request,
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        client_ip = get_client_ip(request, trust_proxy=False)
        email = request_data.email.strip().lower()
        if rate_limiter.is_limited(f"login:{email}:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")

        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE lower(email) = %s", (email,)
        ).fetchone()
        # Verify against a real hash even for unknown emails so response
        # timing does not reveal which emails exist.
        if not row or not verify_password(row[1], request_data.password):
            if not row:
                verify_password(hash_password("timing-equalizer"), request_data.password)
            raise HTTPException(status_code=401, detail="Invalid email or password")

        response = Response(status_code=204)
        _issue_session(response, cfg, row[0])
        return response

    @router.post("/logout")
    async def logout():
        response = Response(status_code=204)
        response.delete_cookie("session")
        response.delete_cookie("csrf")
        return response

    @router.get("/me")
    async def me(
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        row = conn.execute(
            "SELECT id, email, display_name FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Not authenticated")
        orgs = conn.execute(
            """SELECT o.id, o.name, m.role FROM org_memberships m
               JOIN orgs o ON o.id = m.org_id WHERE m.user_id = %s ORDER BY o.id""",
            (user_id,),
        ).fetchall()
        return {
            "user": {"id": row[0], "email": row[1], "display_name": row[2]},
            "orgs": [{"id": o[0], "name": o[1], "role": o[2]} for o in orgs],
        }

    return router
```

`api/src/api/config.py`: rename the field `admin_bootstrap_password` → delete it; add two optional fields and stop hard-failing on it:

```python
    bootstrap_admin_email: str
    bootstrap_admin_password: str
```

In `from_env()`: delete the `admin_bootstrap_password` read and its `ValueError`; add
`bootstrap_admin_email=os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "")` and
`bootstrap_admin_password=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")` to the returned instance (no validation — the pair is optional).

`api/src/api/main.py` line 41: replace `ensure_admin(cfg.postgres_dsn, cfg.admin_bootstrap_password)` with:

```python
        if cfg.bootstrap_admin_email and cfg.bootstrap_admin_password:
            ensure_bootstrap_user(
                cfg.postgres_dsn, cfg.bootstrap_admin_email, cfg.bootstrap_admin_password)
```

and update the import at line 12-17 from `ensure_admin` to `ensure_bootstrap_user`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_auth.py tests/test_migration_005.py -v
```
Expected: all PASS. (Other api test files are still red — they use `require_admin` routes converted in Tasks 3–8.)

- [ ] **Step 6: Commit**

```bash
git add api/src/api/auth.py api/src/api/config.py api/src/api/main.py api/tests/test_auth.py api/tests/conftest.py
git commit -m "feat(api): user accounts — register/login, user_id sessions, bootstrap claim"
```

---

### Task 3: RBAC core + orgs router (create, members, invites, join)

**Files:**
- Create: `api/src/api/rbac.py`
- Create: `api/src/api/routes/orgs.py`
- Create: `api/tests/test_orgs.py`
- Modify: `api/src/api/main.py` (mount the router)

**Interfaces:**
- Consumes: `require_user` (Task 2), tables from Task 1.
- Produces (used by every org-scoped route in Tasks 4–8):

```python
# api/src/api/rbac.py
ROLE_RANK = {"viewer": 0, "trader": 1, "admin": 2, "owner": 3}

@dataclass(frozen=True)
class OrgContext:
    org_id: int
    user_id: int
    role: str

def require_org_role(min_role: str):
    """Returns a FastAPI dependency resolving the caller's membership in the
    path's {org_id}. Non-member (or nonexistent org) -> 404; member below
    min_role -> 403; else returns OrgContext."""
```

- Routes produced: `POST /api/orgs`, `GET/PATCH/DELETE /api/orgs/{org_id}`, `GET /api/orgs/{org_id}/members`, `PATCH/DELETE /api/orgs/{org_id}/members/{member_user_id}`, `POST/GET /api/orgs/{org_id}/invites`, `DELETE /api/orgs/{org_id}/invites/{invite_id}`, `POST /api/orgs/join`.
- Invite create response: `{"id": int, "role": str, "token": str, "expires_at": iso}` — the raw token appears exactly once, here.

- [ ] **Step 1: Write the failing tests**

`api/tests/test_orgs.py`:

```python
"""Org lifecycle, memberships, invites, RBAC edges."""
import psycopg


def _register(client, email="owner@example.com", name="Owner"):
    r = client.post("/api/register", json={
        "email": email, "password": "a-solid-password", "display_name": name})
    assert r.status_code == 204
    return client.get("/api/me").json()["user"]


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_create_org_makes_creator_owner(app_client):
    _register(app_client)
    r = app_client.post("/api/orgs", json={"name": "Alpha Desk"},
                        headers=_csrf(app_client))
    assert r.status_code == 201
    org = r.json()
    assert org["name"] == "Alpha Desk" and org["role"] == "owner"
    me = app_client.get("/api/me").json()
    assert me["orgs"] == [{"id": org["id"], "name": "Alpha Desk", "role": "owner"}]


def test_org_get_shows_settings_fields(app_client):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.get(f"/api/orgs/{org['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["copying_enabled"] is True and body["dry_run"] is False


def test_nonmember_gets_404_never_403(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "Secret"},
                          headers=_csrf(app_client)).json()
    outsider = make_user(email="out@example.com")
    login_as(app_client, outsider)
    assert app_client.get(f"/api/orgs/{org['id']}").status_code == 404
    assert app_client.get(f"/api/orgs/{org['id']}/members").status_code == 404
    assert app_client.get("/api/orgs/999999").status_code == 404


def test_invite_roundtrip(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "trader"}, headers=_csrf(app_client))
    assert inv.status_code == 201
    token = inv.json()["token"]

    joiner = make_user(email="join@example.com")
    login_as(app_client, joiner)
    r = app_client.post("/api/orgs/join", json={"token": token},
                        headers=_csrf(app_client))
    assert r.status_code == 200
    assert r.json() == {"org_id": org["id"], "role": "trader"}
    # single-use
    r2 = app_client.post("/api/orgs/join", json={"token": token},
                         headers=_csrf(app_client))
    assert r2.status_code == 410


def test_invite_cannot_grant_owner(app_client):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": "owner"}, headers=_csrf(app_client))
    assert r.status_code == 400


def test_expired_invite_is_410(app_client, make_user, login_as, db):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE org_invites SET expires_at = now() - interval '1 hour'")
    joiner = make_user(email="late@example.com")
    login_as(app_client, joiner)
    r = app_client.post("/api/orgs/join", json={"token": inv["token"]},
                        headers=_csrf(app_client))
    assert r.status_code == 410


def test_raw_invite_token_never_stored(app_client, db):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    with psycopg.connect(db, autocommit=True) as conn:
        (stored,) = conn.execute("SELECT token_hash FROM org_invites").fetchone()
    assert stored != inv["token"] and len(stored) == 64  # sha256 hex


def test_member_role_change_and_last_owner_invariant(app_client, make_user, login_as):
    owner = _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    member = make_user(email="m@example.com")
    login_as(app_client, member)
    app_client.post("/api/orgs/join", json={"token": inv["token"]},
                    headers=_csrf(app_client))

    # a viewer cannot change roles
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{owner['id']}",
                         json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 403

    login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})
    # owner promotes member to owner
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "owner"}, headers=_csrf(app_client))
    assert r.status_code == 200
    # two owners now; demoting one is fine
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "admin"}, headers=_csrf(app_client))
    assert r.status_code == 200
    # demoting the LAST owner is rejected
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{owner['id']}",
                         json={"role": "admin"}, headers=_csrf(app_client))
    assert r.status_code == 409
    # removing the last owner is rejected
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{owner['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 409


def test_member_can_leave_but_last_owner_cannot(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "trader"}, headers=_csrf(app_client)).json()
    member = make_user(email="m@example.com")
    login_as(app_client, member)
    app_client.post("/api/orgs/join", json={"token": inv["token"]},
                    headers=_csrf(app_client))
    # a trader can remove THEMSELVES (leave) even though they are not owner
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{member['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 204
    assert app_client.get(f"/api/orgs/{org['id']}").status_code == 404


def test_delete_org_cascades(app_client, db):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.delete(f"/api/orgs/{org['id']}", headers=_csrf(app_client))
    assert r.status_code == 204
    with psycopg.connect(db, autocommit=True) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM orgs").fetchone()
    assert count == 0
    assert app_client.get("/api/me").json()["orgs"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_orgs.py -x -q
```
Expected: FAIL — 404 on `POST /api/orgs` (router not mounted).

- [ ] **Step 3: Implement `rbac.py` and `routes/orgs.py`**

`api/src/api/rbac.py`:

```python
"""Org-scoped authorization: role ranks and the require_org_role dependency."""
from dataclasses import dataclass

import psycopg
from fastapi import Depends, HTTPException

from .auth import require_user
from .db import get_conn

ROLE_RANK = {"viewer": 0, "trader": 1, "admin": 2, "owner": 3}


@dataclass(frozen=True)
class OrgContext:
    org_id: int
    user_id: int
    role: str


def require_org_role(min_role: str):
    """Dependency factory: resolve the caller's membership for the path's
    {org_id}. Non-members (and nonexistent orgs) get 404 so org existence
    never leaks; members below min_role get 403."""
    assert min_role in ROLE_RANK

    def dependency(
        org_id: int,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> OrgContext:
        row = conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
            (org_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if ROLE_RANK[row[0]] < ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return OrgContext(org_id=org_id, user_id=user_id, role=row[0])

    return dependency


def require_account_in_org(conn: psycopg.Connection, org_id: int, account_id: int) -> None:
    """404 unless the broker account belongs to this org. Every org-scoped
    route that takes an account_id (path or body) must call this before
    touching the account or proxying to the copier."""
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
        (account_id, org_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
```

`api/src/api/routes/orgs.py`:

```python
"""Org lifecycle: create, settings-bearing GET, members, invites, join."""
import hashlib
import secrets
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_user
from ..db import get_conn
from ..rbac import OrgContext, ROLE_RANK, require_org_role

INVITE_TTL_DAYS = 7


class CreateOrgRequest(BaseModel):
    name: str


class PatchOrgRequest(BaseModel):
    name: Optional[str] = None


class CreateInviteRequest(BaseModel):
    role: str


class JoinRequest(BaseModel):
    token: str


class PatchMemberRequest(BaseModel):
    role: str


def _owner_count(conn, org_id: int) -> int:
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM org_memberships WHERE org_id = %s AND role = 'owner'",
        (org_id,),
    ).fetchone()
    return count


def create_orgs_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs", tags=["orgs"])

    @router.post("", status_code=201)
    async def create_org(
        body: CreateOrgRequest,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        with conn.transaction():
            (org_id,) = conn.execute(
                "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (name,)
            ).fetchone()
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner')",
                (org_id, user_id),
            )
        return {"id": org_id, "name": name, "role": "owner"}

    @router.post("/join")
    async def join_org(
        body: JoinRequest,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        token_hash = hashlib.sha256(body.token.encode()).hexdigest()
        with conn.transaction():
            row = conn.execute(
                """UPDATE org_invites SET consumed_at = now(), consumed_by = %s
                   WHERE token_hash = %s AND consumed_at IS NULL
                     AND expires_at > now()
                   RETURNING org_id, role""",
                (user_id, token_hash),
            ).fetchone()
            if not row:
                raise HTTPException(
                    status_code=410, detail="Invite is invalid, expired, or already used")
            org_id, role = row
            inserted = conn.execute(
                """INSERT INTO org_memberships (org_id, user_id, role)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING role""",
                (org_id, user_id, role),
            ).fetchone()
            if not inserted:
                raise HTTPException(status_code=409, detail="Already a member")
        return {"org_id": org_id, "role": role}

    @router.get("/{org_id}")
    async def get_org(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        row = conn.execute(
            "SELECT id, name, copying_enabled, dry_run, created_at FROM orgs WHERE id = %s",
            (ctx.org_id,),
        ).fetchone()
        return {"id": row[0], "name": row[1], "copying_enabled": row[2],
                "dry_run": row[3], "created_at": row[4].isoformat(),
                "role": ctx.role}

    @router.patch("/{org_id}")
    async def patch_org(
        body: PatchOrgRequest,
        ctx: OrgContext = Depends(require_org_role("owner")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="name must not be empty")
            conn.execute("UPDATE orgs SET name = %s WHERE id = %s", (name, ctx.org_id))
        row = conn.execute(
            "SELECT id, name FROM orgs WHERE id = %s", (ctx.org_id,)).fetchone()
        return {"id": row[0], "name": row[1]}

    @router.delete("/{org_id}", status_code=204)
    async def delete_org(
        ctx: OrgContext = Depends(require_org_role("owner")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        # Cascades memberships, invites, connections, accounts, mappings.
        # Broker positions are NOT touched; the dashboard warns about that.
        conn.execute("DELETE FROM orgs WHERE id = %s", (ctx.org_id,))

    @router.get("/{org_id}/members")
    async def list_members(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, m.role, m.created_at
               FROM org_memberships m JOIN users u ON u.id = m.user_id
               WHERE m.org_id = %s ORDER BY m.created_at""",
            (ctx.org_id,),
        ).fetchall()
        return [{"user_id": r[0], "email": r[1], "display_name": r[2],
                 "role": r[3], "joined_at": r[4].isoformat()} for r in rows]

    @router.patch("/{org_id}/members/{member_user_id}")
    async def patch_member(
        member_user_id: int,
        body: PatchMemberRequest,
        ctx: OrgContext = Depends(require_org_role("owner")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        if body.role not in ROLE_RANK:
            raise HTTPException(status_code=400, detail="Unknown role")
        with conn.transaction():
            row = conn.execute(
                "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s "
                "FOR UPDATE",
                (ctx.org_id, member_user_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            if (row[0] == "owner" and body.role != "owner"
                    and _owner_count(conn, ctx.org_id) == 1):
                raise HTTPException(
                    status_code=409, detail="An org must keep at least one owner")
            conn.execute(
                "UPDATE org_memberships SET role = %s WHERE org_id = %s AND user_id = %s",
                (body.role, ctx.org_id, member_user_id),
            )
        return {"user_id": member_user_id, "role": body.role}

    @router.delete("/{org_id}/members/{member_user_id}", status_code=204)
    async def remove_member(
        member_user_id: int,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        # Owners may remove anyone; anyone may remove THEMSELVES (leave).
        if ctx.role != "owner" and member_user_id != ctx.user_id:
            raise HTTPException(status_code=403, detail="Insufficient role")
        with conn.transaction():
            row = conn.execute(
                "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s "
                "FOR UPDATE",
                (ctx.org_id, member_user_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            if row[0] == "owner" and _owner_count(conn, ctx.org_id) == 1:
                raise HTTPException(
                    status_code=409, detail="An org must keep at least one owner")
            conn.execute(
                "DELETE FROM org_memberships WHERE org_id = %s AND user_id = %s",
                (ctx.org_id, member_user_id),
            )

    @router.post("/{org_id}/invites", status_code=201)
    async def create_invite(
        body: CreateInviteRequest,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        if body.role not in ("admin", "trader", "viewer"):
            raise HTTPException(
                status_code=400, detail="Invites can grant admin, trader, or viewer")
        token = secrets.token_urlsafe(32)
        row = conn.execute(
            """INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at)
               VALUES (%s, %s, %s, %s, now() + make_interval(days => %s))
               RETURNING id, expires_at""",
            (ctx.org_id, body.role, hashlib.sha256(token.encode()).hexdigest(),
             ctx.user_id, INVITE_TTL_DAYS),
        ).fetchone()
        # The raw token is returned exactly once; only its hash is stored.
        return {"id": row[0], "role": body.role, "token": token,
                "expires_at": row[1].isoformat()}

    @router.get("/{org_id}/invites")
    async def list_invites(
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        rows = conn.execute(
            """SELECT id, role, created_at, expires_at, consumed_at
               FROM org_invites WHERE org_id = %s ORDER BY created_at DESC""",
            (ctx.org_id,),
        ).fetchall()
        return [{"id": r[0], "role": r[1], "created_at": r[2].isoformat(),
                 "expires_at": r[3].isoformat(),
                 "consumed": r[4] is not None} for r in rows]

    @router.delete("/{org_id}/invites/{invite_id}", status_code=204)
    async def revoke_invite(
        invite_id: int,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        cursor = conn.execute(
            "DELETE FROM org_invites WHERE id = %s AND org_id = %s",
            (invite_id, ctx.org_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Invite not found")

    return router
```

In `api/src/api/main.py`, import and mount it after the auth router:

```python
from .routes.orgs import create_orgs_router
...
    app.include_router(create_orgs_router())
```

Route-ordering note: `POST /api/orgs/join` is declared before `GET /api/orgs/{org_id}` inside the router, so `join` never matches as an `org_id`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_orgs.py tests/test_auth.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/rbac.py api/src/api/routes/orgs.py api/tests/test_orgs.py api/src/api/main.py
git commit -m "feat(api): org lifecycle, memberships, invites, require_org_role"
```

---

### Task 4: Org-scope the accounts routes

**Files:**
- Modify: `api/src/api/routes/accounts.py`
- Modify: `api/tests/test_accounts.py`

**Interfaces:**
- Consumes: `require_org_role`, `require_account_in_org` (Task 3).
- Produces: same route tails as today under `prefix="/api/orgs/{org_id}"`:
  `GET …/accounts` (viewer), `PATCH …/accounts/{account_id}` (admin), `GET …/accounts/{account_id}/details` (viewer), `GET …/accounts/{account_id}/history/{kind}` (viewer), `GET …/accounts/{account_id}/symbols` (viewer), `DELETE …/accounts/{account_id}/connection` (admin). Response shapes unchanged.

- [ ] **Step 1: Update the tests**

`api/tests/test_accounts.py` currently logs in with the old password flow and calls `/api/accounts`. Rework its setup and add tenancy cases. Add this helper at the top of the file (used by all its tests) and convert every existing test to it — mechanical changes: each old `app_client.get("/api/accounts")` becomes `client.get(f"/api/orgs/{org_id}/accounts")`, and each seeded `INSERT INTO ctid_connections` / `INSERT INTO accounts` gains the `org_id` column with the fixture org's id:

```python
import pytest


@pytest.fixture
def org_client(app_client, make_user, make_org, login_as, db):
    """app_client logged in as the admin of a fresh org; returns
    (client, org_id, seed) where seed(account_id, role, ...) inserts an
    account owned by that org."""
    import psycopg

    user = make_user(email="admin@example.com")
    org_id = make_org(name="Desk", members=[(user, "admin")])
    login_as(app_client, user)

    with psycopg.connect(db, autocommit=True) as conn:
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""",
            (org_id,),
        ).fetchone()

    def seed(account_id, role="slave", enabled=True, multiplier=1.0, is_live=False):
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                       org_id, trader_login, is_live, role, enabled, multiplier)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (account_id, connection_id, org_id, account_id, is_live,
                 role, enabled, multiplier),
            )
        return account_id

    return app_client, org_id, seed
```

New tenancy tests to append to the file:

```python
def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_accounts_listing_is_org_scoped(org_client, make_user, make_org, login_as, db):
    import psycopg
    client, org_id, seed = org_client
    seed(100, role="master")

    # A second org with its own account
    other_owner = make_user(email="other@example.com")
    other_org = make_org(name="Other", members=[(other_owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (other_conn,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (other_org,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live)
               VALUES (200, %s, %s, 200, false)""", (other_conn, other_org))

    listed = client.get(f"/api/orgs/{org_id}/accounts").json()
    assert [a["ctid_trader_account_id"] for a in listed] == [100]
    # the other org's account is a 404 through THIS org's paths
    assert client.get(f"/api/orgs/{org_id}/accounts/200/symbols").status_code == 404
    r = client.patch(f"/api/orgs/{org_id}/accounts/200",
                     json={"enabled": False}, headers=_csrf(client))
    assert r.status_code == 404


def test_patch_role_master_conflict_is_per_org(org_client):
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(101, role="slave")
    r = client.patch(f"/api/orgs/{org_id}/accounts/101",
                     json={"role": "master"}, headers=_csrf(client))
    assert r.status_code == 409
    assert "master already exists" in r.json()["detail"]


def test_viewer_can_read_but_not_patch(org_client, make_user, login_as, db):
    import psycopg
    client, org_id, seed = org_client
    seed(100, role="master")
    viewer = make_user(email="v@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
            (org_id, viewer["id"]))
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/accounts").status_code == 200
    r = client.patch(f"/api/orgs/{org_id}/accounts/100",
                     json={"enabled": False}, headers=_csrf(client))
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_accounts.py -x -q
```
Expected: FAIL — 404 on `/api/orgs/{org_id}/accounts` (router still mounted at `/api/accounts`).

- [ ] **Step 3: Convert `routes/accounts.py`**

Mechanical conversion pattern, applied to every endpoint in the file:

1. `router = APIRouter(prefix="/api", tags=["accounts"])` → `router = APIRouter(prefix="/api/orgs/{org_id}", tags=["accounts"])`.
2. Replace the import `from ..auth import require_admin` with `from ..rbac import OrgContext, require_org_role, require_account_in_org`.
3. In each handler signature, replace `_: bool = Depends(require_admin)` with `ctx: OrgContext = Depends(require_org_role("viewer"))` for GETs, and `ctx: OrgContext = Depends(require_org_role("admin"))` for `patch_account` and `disconnect_account`.
4. Scope every SQL statement by org:
   - `list_accounts` query gains `WHERE a.org_id = %s` (param `ctx.org_id`).
   - Every `SELECT 1 FROM accounts WHERE ctid_trader_account_id = %s` existence check becomes `require_account_in_org(conn, ctx.org_id, account_id)` (delete the inline check, call the helper first thing in the handler).
   - `patch_account`'s UPDATE and both re-SELECTs gain `AND org_id = %s`.
   - `account_details`'s join query gains `AND a.org_id = %s`.
   - `disconnect_account`'s connection lookup gains `AND org_id = %s`, and the `DELETE FROM ctid_connections` gains `AND org_id = %s`.
5. The 409 detail for `UniqueViolation` stays exactly `"a master already exists"` (the dashboard matches on it).
6. `symbol_cache` reads stay keyed by account_id only — safe because `require_account_in_org` ran first.

Example of the converted `list_accounts` (the same shape applies to the rest):

```python
    @router.get("/accounts", response_model=List[AccountResponse])
    async def list_accounts(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> List[AccountResponse]:
        """List this org's accounts with their connection status."""
        rows = conn.execute(
            """SELECT a.ctid_trader_account_id, a.trader_login, a.is_live, a.role, a.enabled,
                      a.multiplier, a.status, a.last_error, c.status as conn_status, a.nickname
               FROM accounts a
               JOIN ctid_connections c ON a.ctid_connection_id = c.id
               WHERE a.org_id = %s
               ORDER BY a.ctid_trader_account_id""",
            (ctx.org_id,),
        ).fetchall()
        ...  # unchanged row mapping
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_accounts.py -v
```
Expected: all PASS (both the converted legacy tests and the new tenancy tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/accounts.py api/tests/test_accounts.py
git commit -m "feat(api): org-scope accounts routes"
```

---

### Task 5: Org-scope settings, control, and state routes

**Files:**
- Modify: `api/src/api/routes/settings_control.py`
- Modify: `api/tests/test_settings_control.py`

**Interfaces:**
- Consumes: `require_org_role` (Task 3); `orgs.copying_enabled` / `orgs.dry_run` (Task 1).
- Produces, all under `prefix="/api/orgs/{org_id}"`:
  - `GET …/settings` (viewer) → `{"copying_enabled": bool, "dry_run": bool}` — **no `shards`** (process config is no longer API-exposed).
  - `PUT …/settings` (admin) — updates the org row, then proxies copier `/reload` and, when dry_run changed, `/dry-run` with body `{"org_id": <id>, "enabled": <bool>}`.
  - `POST …/control/pause|resume` (admin) — proxy body `{"org_id": <id>, "account_id": <int|null>}`.
  - `POST …/control/resync` (admin) — proxy body `{"org_id": <id>}`.
  - `GET …/state` (viewer) — proxy `GET {copier}/state?org_id=<id>`.
  - `POST …/drift/{action}` (admin) — proxy body gains `"org_id"`.
- `_proxy_to_copier` keeps its exact signature (other routers import it).

- [ ] **Step 1: Update the tests**

In `api/tests/test_settings_control.py`, convert setup to the `org_client` fixture pattern from Task 4 (move that fixture from `test_accounts.py` into `conftest.py` now, since two files need it — delete the copy in `test_accounts.py`). Convert every path: `/api/settings` → `f"/api/orgs/{org_id}/settings"`, `/api/control/pause` → `f"/api/orgs/{org_id}/control/pause"`, `/api/state` → `f"/api/orgs/{org_id}/state"`, `/api/drift/adopt` → `f"/api/orgs/{org_id}/drift/adopt"`, etc. Tests asserting `shards` in settings responses: delete those assertions (and any that PUT `shards`). Add these new tests:

```python
def test_settings_are_per_org(org_client, make_user, make_org, login_as):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/settings",
                   json={"copying_enabled": False, "dry_run": True},
                   headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["copying_enabled"] is False and r.json()["dry_run"] is True

    # a different org still has defaults
    other = make_user(email="o2@example.com")
    other_org = make_org(name="Two", members=[(other, "admin")])
    login_as(client, other)
    body = client.get(f"/api/orgs/{other_org}/settings").json()
    assert body == {"copying_enabled": True, "dry_run": False}


def test_control_pause_forwards_org_id(org_client):
    """The copier must receive the org context on every scoped command."""
    import json as jsonlib
    client, org_id, seed = org_client
    captured = {}

    import httpx
    from .conftest import default_mock_callback

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            captured["url"] = str(request.url)
            captured["body"] = jsonlib.loads(request.content or b"{}")
            return httpx.Response(200, json={"status": "paused"})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    r = client.post(f"/api/orgs/{org_id}/control/pause", json={},
                    headers=_csrf(client))
    assert r.status_code == 200
    assert captured["url"].endswith("/pause")
    assert captured["body"]["org_id"] == org_id


def test_state_proxies_with_org_query(org_client):
    client, org_id, seed = org_client
    captured = {}

    import httpx
    from .conftest import default_mock_callback

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"accounts": {}, "master_positions": [],
                                             "pending_orders": [], "drift": []})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    r = client.get(f"/api/orgs/{org_id}/state")
    assert r.status_code == 200
    assert f"org_id={org_id}" in captured["url"]
```

(`_csrf` helper as in Task 3's tests; add it at the top of the file.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_settings_control.py -x -q
```
Expected: FAIL — 404s on the new paths.

- [ ] **Step 3: Convert `routes/settings_control.py`**

- `SettingsResponse` → fields `copying_enabled: bool`, `dry_run: bool` only. `SettingsUpdateRequest` → drop `shards`.
- Both routers get `prefix="/api/orgs/{org_id}"`. GET settings / state use `Depends(require_org_role("viewer"))`; PUT settings, control endpoints, and drift use `Depends(require_org_role("admin"))`.
- `get_settings` reads `SELECT copying_enabled, dry_run FROM orgs WHERE id = %s` with `ctx.org_id` (the row always exists — membership proved it).
- `update_settings` updates `orgs` (`UPDATE orgs SET … WHERE id = %s`), then proxies `/reload` as today, and when `request_data.dry_run is not None` proxies `/dry-run` with `json={"org_id": ctx.org_id, "enabled": request_data.dry_run}`.
- `control_pause`/`control_resume`: `json={"org_id": ctx.org_id, "account_id": request.account_id}` — and when `request.account_id` is not None, call `require_account_in_org(conn, ctx.org_id, request.account_id)` first (add `conn: psycopg.Connection = Depends(get_conn)` to those signatures).
- `control_resync`: `json={"org_id": ctx.org_id}`.
- `get_state`: `url = f"{cfg.copier_control_url}/state?org_id={ctx.org_id}"`.
- `drift_action`: body becomes `{**request.model_dump(exclude_none=True), "org_id": ctx.org_id}`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_settings_control.py tests/test_accounts.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/settings_control.py api/tests/test_settings_control.py api/tests/test_accounts.py api/tests/conftest.py
git commit -m "feat(api): org-scope settings/control/state, forward org_id to copier"
```

---

### Task 6: Org-scope the trading routes (real money)

**Files:**
- Modify: `api/src/api/routes/trading.py`
- Modify: `api/tests/test_trading.py`

**Interfaces:**
- Consumes: `require_org_role("trader")` / `require_org_role("admin")`, `require_account_in_org`.
- Produces, under `prefix="/api/orgs/{org_id}"`:
  - `POST …/orders`, `POST …/positions/close`, `POST …/orders/cancel` — **trader**; body's `account_id` must belong to the org (404 otherwise) before any proxying.
  - `POST …/control/close-all` — **admin**; forwarded body is `{"org_id": <id>}` or `{"org_id": <id>, "account_id": <int>}` (validated in-org). The API never sends a close-all without an org.

- [ ] **Step 1: Update the tests**

Convert `api/tests/test_trading.py` to the `org_client` fixture and new paths. Add the same two-line `_csrf(client)` helper used in Task 3's tests at the top of the file, then add:

```python
def test_order_for_foreign_account_is_404_and_never_proxied(
        org_client, make_user, make_org, login_as, db):
    """The core cross-tenant money test: org B's account is unreachable
    through org A's trading routes, and the copier is never contacted."""
    import psycopg
    import httpx
    from .conftest import default_mock_callback

    client, org_id, seed = org_client
    seed(100, role="master")
    other = make_user(email="b@example.com")
    other_org = make_org(name="B", members=[(other, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (other_conn,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (other_org,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live)
               VALUES (999, %s, %s, 999, false)""", (other_conn, other_org))

    proxied = []

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            proxied.append(str(request.url))
            return httpx.Response(200, json={"status": "submitted"})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    for path, body in [
        ("orders", {"account_id": 999, "symbol": "EURUSD", "side": "BUY",
                    "order_type": "MARKET", "volume_lots": 0.01}),
        ("positions/close", {"account_id": 999, "position_id": 1}),
        ("orders/cancel", {"account_id": 999, "order_id": 1}),
        ("control/close-all", {"account_id": 999}),
    ]:
        r = client.post(f"/api/orgs/{org_id}/{path}", json=body,
                        headers=_csrf(client))
        assert r.status_code == 404, path
    assert proxied == []


def test_close_all_forwards_org_id(org_client):
    import json as jsonlib
    import httpx
    from .conftest import default_mock_callback

    client, org_id, seed = org_client
    captured = {}

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            captured["body"] = jsonlib.loads(request.content or b"{}")
            return httpx.Response(200, json={"status": "flattened", "paused": True,
                                             "accounts": []})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    r = client.post(f"/api/orgs/{org_id}/control/close-all", json={},
                    headers=_csrf(client))
    assert r.status_code == 200
    assert captured["body"] == {"org_id": org_id}


def test_trader_can_order_but_not_close_all(org_client, make_user, login_as, db):
    import psycopg
    client, org_id, seed = org_client
    seed(100, role="master")
    trader = make_user(email="t@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'trader')",
            (org_id, trader["id"]))
    login_as(client, trader)
    r = client.post(f"/api/orgs/{org_id}/orders",
                    json={"account_id": 100, "symbol": "EURUSD", "side": "BUY",
                          "order_type": "MARKET", "volume_lots": 0.01},
                    headers=_csrf(client))
    assert r.status_code == 200  # mock copier answers 200
    r = client.post(f"/api/orgs/{org_id}/control/close-all", json={},
                    headers=_csrf(client))
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_trading.py -x -q
```
Expected: FAIL — 404 on the new paths.

- [ ] **Step 3: Convert `routes/trading.py`**

Full converted router (it is small enough to show whole):

```python
"""Trading action proxies: manual orders, position close, order cancel,
and the kill switch — now org-scoped.

Still deliberately thin on TRADE validation (symbol/volume/side/price rules
live in the copier), but the API owns TENANCY validation: the body's
account_id must belong to the caller's org before anything is proxied, and
close-all always carries the org id so the copier can never flatten outside
that org's book.
"""
from typing import Any, Dict

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import ApiConfig
from ..db import get_conn
from ..rbac import OrgContext, require_org_role, require_account_in_org
from .settings_control import _proxy_to_copier


def _required_account_id(body: Dict[str, Any]) -> int:
    account_id = body.get("account_id")
    if account_id is None:
        raise HTTPException(status_code=400, detail="account_id required")
    try:
        return int(account_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="account_id must be an integer")


def create_trading_router() -> APIRouter:
    """Router for manual trading actions and the kill switch."""
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["trading"])

    @router.post("/orders", response_model=Dict[str, Any])
    async def place_order(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("trader")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Place a manual order on one of THIS org's accounts."""
        require_account_in_org(conn, ctx.org_id, _required_account_id(body))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/order", method="POST", json=body)

    @router.post("/positions/close", response_model=Dict[str, Any])
    async def close_position(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("trader")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Close (or partially close) one position on an org account."""
        require_account_in_org(conn, ctx.org_id, _required_account_id(body))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/positions/close",
            method="POST", json=body)

    @router.post("/orders/cancel", response_model=Dict[str, Any])
    async def cancel_order(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("trader")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Cancel one working order on an org account."""
        require_account_in_org(conn, ctx.org_id, _required_account_id(body))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/orders/cancel",
            method="POST", json=body)

    @router.post("/control/close-all", response_model=Dict[str, Any])
    async def close_all(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Kill switch: flatten one org account ({"account_id": N}) or this
        org's whole book ({} — also pauses the org's copying, see the
        copier). Always org-bound; there is no all-orgs flatten."""
        forward: Dict[str, Any] = {"org_id": ctx.org_id}
        if body and body.get("account_id") is not None:
            account_id = _required_account_id(body)
            require_account_in_org(conn, ctx.org_id, account_id)
            forward["account_id"] = account_id
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/close-all",
            method="POST", json=forward)

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_trading.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/trading.py api/tests/test_trading.py
git commit -m "feat(api): org-scope trading routes; tenancy check before every proxy"
```

---

### Task 7: Org-scope events and the OAuth flow

**Files:**
- Modify: `api/src/api/routes/events.py`
- Modify: `api/src/api/oauth.py`
- Modify: `api/tests/test_oauth.py`, `api/tests/test_events_ws.py` (events REST parts only; WS parts move in Task 8)

**Interfaces:**
- Consumes: `require_org_role`; `oauth_states.org_id`, `ctid_connections.org_id` (Task 1).
- Produces:
  - `GET /api/orgs/{org_id}/events` (viewer) — same filters, plus implicit `WHERE org_id = %s` (NULL-org infrastructure events are invisible).
  - `GET /api/orgs/{org_id}/oauth/connect` (admin) — stores `org_id` on the `oauth_states` row.
  - `GET /api/oauth/callback` — path unchanged (global, matches the registered redirect URI); requires only a logged-in user (`require_user`); resolves the org from the consumed state row; inserts the connection with that `org_id`; redirects to `/org/{org_id}/accounts?connected=1`.

- [ ] **Step 1: Update the tests**

`test_events_ws.py` REST tests: convert to `org_client`, seed events with `org_id`, and add:

```python
def test_events_are_org_scoped_and_null_org_hidden(org_client, db):
    import psycopg
    client, org_id, seed = org_client
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO events (org_id, category, severity, payload)
               VALUES (%s, 'control', 'info', '{"n": 1}'::jsonb)""", (org_id,))
        conn.execute(
            """INSERT INTO events (org_id, category, severity, payload)
               VALUES (NULL, 'connection', 'info', '{"n": 2}'::jsonb)""")
        conn.execute(
            """INSERT INTO orgs (name) VALUES ('ghost')""")
        conn.execute(
            """INSERT INTO events (org_id, category, severity, payload)
               SELECT id, 'control', 'info', '{"n": 3}'::jsonb FROM orgs
               WHERE name = 'ghost'""")
    events = client.get(f"/api/orgs/{org_id}/events").json()
    assert [e["payload"]["n"] for e in events] == [1]
```

`test_oauth.py`: the connect flow now starts at `f"/api/orgs/{org_id}/oauth/connect"` — convert setup to `org_client` (role admin). Add:

```python
def test_callback_lands_connection_in_the_starting_org(org_client, db):
    import psycopg
    client, org_id, seed = org_client
    r = client.get(f"/api/orgs/{org_id}/oauth/connect", follow_redirects=False)
    assert r.status_code == 307
    state = r.headers["location"].split("state=")[1].split("&")[0]
    cb = client.get(f"/api/oauth/callback?code=abc&state={state}",
                    follow_redirects=False)
    assert cb.status_code == 307
    assert cb.headers["location"] == f"/org/{org_id}/accounts?connected=1"
    with psycopg.connect(db, autocommit=True) as conn:
        (conn_org,) = conn.execute(
            "SELECT org_id FROM ctid_connections ORDER BY id DESC LIMIT 1").fetchone()
    assert conn_org == org_id
```

Existing oauth tests that hit `/api/oauth/connect` unauthenticated (expect 401): keep, path becomes `/api/orgs/1/oauth/connect` and the expectation flips to **401** when no session at all (`require_user` fires before membership) — keep asserting 401.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_oauth.py tests/test_events_ws.py -x -q
```
Expected: FAIL — 404 on new paths.

- [ ] **Step 3: Convert `events.py` and `oauth.py`**

`events.py`: prefix `"/api/orgs/{org_id}"`, dependency `require_org_role("viewer")`, and the query gains a mandatory org clause — replace the `query_parts`/`where_clauses` setup with `where_clauses = ["org_id = %s"]`, `params = [ctx.org_id]` (the optional filters then append as today).

`oauth.py` changes:

1. `create_oauth_router()` returns **two** routers — restructure into `create_oauth_router()` (kept name; now `prefix="/api/orgs/{org_id}/oauth"`, holding `connect`) and `create_oauth_callback_router()` (`prefix="/api/oauth"`, holding `callback`). `main.py` mounts both.
2. `connect`: replace `_: bool = Depends(require_admin)` with `ctx: OrgContext = Depends(require_org_role("admin"))`; keep the session-cookie read (the state stays bound to the session digest). The INSERT becomes:

```python
            conn.execute(
                """
                INSERT INTO oauth_states (state_hash, session, org_id, consumed_at)
                VALUES (%s, %s, %s, NULL)
                ON CONFLICT (state_hash) DO NOTHING
                """,
                (_digest(state), _digest(session), ctx.org_id),
            )
```

3. `callback`: replace `_: bool = Depends(require_admin)` with `user_id: int = Depends(require_user)`. Both consume UPDATEs change `RETURNING state_hash` → `RETURNING state_hash, org_id`, and after the consume:

```python
            org_id = result[1]
```

4. The `ctid_connections` INSERT gains the org:

```python
                INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at, scope, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
```
with `org_id` as the first parameter.
5. Final redirect: `redirect_url = f"/org/{org_id}/accounts?connected=1"` (warning suffix logic unchanged).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_oauth.py tests/test_events_ws.py -v
```
Expected: REST/oauth tests PASS. (Any WS tests in `test_events_ws.py` still fail; they are converted next task — if so, run with `-k "not ws"` for this task's gate and say so in the commit.)

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/events.py api/src/api/oauth.py api/tests/test_oauth.py api/tests/test_events_ws.py
git commit -m "feat(api): org-scope events feed and OAuth connect/callback"
```

---

### Task 8: Org-keyed WebSocket broadcaster

**Files:**
- Modify: `api/src/api/ws.py`
- Modify: `api/tests/test_events_ws.py` (WS tests)

**Interfaces:**
- Consumes: `user_id_from_session_cookie` (Task 2), `events.org_id` (Task 1).
- Produces:
  - `GET /api/ws?org_id=N` — closes 4401 (bad session), 4400 (missing/invalid org_id), 4404 (not a member).
  - `EventBroadcaster.connections: dict[int, set[WebSocket]]`; `connect(ws, org_id)`, `disconnect(ws, org_id)`, `broadcast(org_id, message)`. Events with `org_id IS NULL` are delivered to no one.
  - Event JSON gains `"org_id"`.
- Consumed by: dashboard `eventsSocket(orgId)` (Task 14).

- [ ] **Step 1: Write the failing tests**

In `api/tests/test_events_ws.py`, convert the existing live_server WS test(s): the URL gains `?org_id={org_id}`, and sessions come from a registered user who is a member. Add the isolation test:

```python
def test_ws_delivers_only_own_org_events(live_server, db, make_user, make_org):
    """Two orgs, two sockets: each socket sees only its org's events."""
    import json
    import psycopg
    import httpx
    from websockets.sync.client import connect as ws_connect

    base_url, ws_url = live_server

    def session_cookies(email):
        r = httpx.post(f"{base_url}/api/register", json={
            "email": email, "password": "a-solid-password", "display_name": email})
        assert r.status_code == 204
        return {"session": r.cookies["session"], "csrf": r.cookies["csrf"]}

    cookies_a = session_cookies("a@example.com")
    cookies_b = session_cookies("b@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        (uid_a,) = conn.execute(
            "SELECT id FROM users WHERE email = 'a@example.com'").fetchone()
        (uid_b,) = conn.execute(
            "SELECT id FROM users WHERE email = 'b@example.com'").fetchone()
    org_a = make_org(name="A", members=[({"id": uid_a}, "viewer")])
    org_b = make_org(name="B", members=[({"id": uid_b}, "viewer")])

    def hdr(cookies):
        return {"Cookie": f"session={cookies['session']}"}

    with ws_connect(f"{ws_url}?org_id={org_a}",
                    additional_headers=hdr(cookies_a)) as ws_a, \
         ws_connect(f"{ws_url}?org_id={org_b}",
                    additional_headers=hdr(cookies_b)) as ws_b:
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                """INSERT INTO events (org_id, category, severity, payload)
                   VALUES (%s, 'control', 'info', '{"which": "a"}'::jsonb)""",
                (org_a,))
            conn.execute(
                """INSERT INTO events (org_id, category, severity, payload)
                   VALUES (%s, 'control', 'info', '{"which": "b"}'::jsonb)""",
                (org_b,))
        got_a = json.loads(ws_a.recv(timeout=10))
        got_b = json.loads(ws_b.recv(timeout=10))
        assert got_a["payload"]["which"] == "a" and got_a["org_id"] == org_a
        assert got_b["payload"]["which"] == "b" and got_b["org_id"] == org_b
        # and nothing else arrives on A within a short window
        import pytest as _pytest
        with _pytest.raises(TimeoutError):
            ws_a.recv(timeout=1)


def test_ws_nonmember_closed_4404(live_server, db, make_user, make_org):
    import httpx
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    base_url, ws_url = live_server
    r = httpx.post(f"{base_url}/api/register", json={
        "email": "x@example.com", "password": "a-solid-password", "display_name": "x"})
    stranger_org = make_org(name="NotYours", members=[])
    try:
        with ws_connect(f"{ws_url}?org_id={stranger_org}",
                        additional_headers={"Cookie": f"session={r.cookies['session']}"}) as ws:
            ws.recv(timeout=5)
            raise AssertionError("socket should have been closed")
    except ConnectionClosed as e:
        assert e.rcvd.code == 4404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && .venv/bin/pytest tests/test_events_ws.py -x -q
```
Expected: FAIL — server ignores `org_id` and broadcasts globally / test expectations mismatch.

- [ ] **Step 3: Convert `ws.py`**

In `EventBroadcaster`:

```python
    def __init__(self):
        self.connections: dict[int, set[WebSocket]] = {}
        ...  # rest unchanged

    async def connect(self, ws: WebSocket, org_id: int):
        await ws.accept()
        self.connections.setdefault(org_id, set()).add(ws)

    def disconnect(self, ws: WebSocket, org_id: int):
        self.connections.get(org_id, set()).discard(ws)

    async def broadcast(self, org_id: int | None, message: dict):
        """Send to the org's sockets only. org_id None (infrastructure
        events) is delivered to no one."""
        if org_id is None:
            return
        for ws in list(self.connections.get(org_id, set())):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                self.disconnect(ws, org_id)
```

In `start_listener`, the row fetch adds `org_id`:

```python
                        await cur.execute(
                            """SELECT id, ts, account_id, category, severity, latency_ms,
                                      payload, org_id
                               FROM events WHERE id = %s""",
                            (event_id,)
                        )
```
and the event dict gains `"org_id": row[7]`, then `await self.broadcast(row[7], event)`.

The endpoint:

```python
    @router.websocket("/api/ws")
    async def websocket_endpoint(ws: WebSocket):
        """Org-scoped event stream. Auth: session cookie; membership in the
        org_id query parameter is required."""
        cfg = ApiConfig.from_env()
        session = ws.cookies.get("session")
        user_id = user_id_from_session_cookie(session, cfg) if session else None
        if user_id is None:
            await ws.close(code=4401, reason="Unauthorized")
            return

        raw_org = ws.query_params.get("org_id")
        try:
            org_id = int(raw_org)
        except (TypeError, ValueError):
            await ws.close(code=4400, reason="org_id required")
            return

        with psycopg.connect(cfg.postgres_dsn, autocommit=True) as conn:
            member = conn.execute(
                "SELECT 1 FROM org_memberships WHERE org_id = %s AND user_id = %s",
                (org_id, user_id),
            ).fetchone()
        if not member:
            await ws.close(code=4404, reason="Not found")
            return

        await broadcaster.connect(ws, org_id)
        try:
            while True:
                await ws.receive_text()
        except Exception as e:
            logger.debug(f"WebSocket error: {e}")
        finally:
            broadcaster.disconnect(ws, org_id)
```

Imports change: `import psycopg`, `from .auth import user_id_from_session_cookie` (drop `get_session_serializer` / `SignatureExpired` / `BadSignature` imports if now unused). In conftest's broadcaster resets, `broadcaster.connections.clear()` still works on a dict.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && .venv/bin/pytest tests/test_events_ws.py -v
```
Expected: all PASS.

- [ ] **Step 5: Full api suite + commit**

```bash
cd api && .venv/bin/pytest tests -q
```
Expected: everything passes except possibly `test_smoke.py` (fix trivially if it references removed env/routes — it asserts app creation and static serving).

```bash
git add api/src/api/ws.py api/tests/test_events_ws.py
git commit -m "feat(api): org-keyed WebSocket broadcaster; NULL-org events go nowhere"
```

---

### Task 9: The role-matrix test — every endpoint × every role

**Files:**
- Create: `api/tests/test_rbac_matrix.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8. Produces nothing new — this is the safety net that pins the permission table from the spec. If a later change moves an endpoint's threshold, exactly one row here changes with it.

- [ ] **Step 1: Write the matrix test**

```python
"""Spec §4's permission matrix, executed literally: every org-scoped endpoint
is called as every role, plus non-member and anonymous.

ok() = any status that proves AUTHORIZATION passed (2xx, or a 4xx/5xx that
can only come from AFTER the role check — 400 validation, 404 for a
nonexistent account, 502 copier). 401/403/404-membership are the denials
under test. Endpoints listed with a seeded account id 100 where needed.
"""
import psycopg
import pytest

ROLES = ["viewer", "trader", "admin", "owner"]

# (method, path_tail, body, min_role)
MATRIX = [
    ("GET",    "accounts",                       None,                          "viewer"),
    ("GET",    "accounts/100/details",           None,                          "viewer"),
    ("GET",    "accounts/100/history/deals?from=0&to=1", None,                  "viewer"),
    ("GET",    "accounts/100/symbols",           None,                          "viewer"),
    ("GET",    "settings",                       None,                          "viewer"),
    ("GET",    "state",                          None,                          "viewer"),
    ("GET",    "events",                         None,                          "viewer"),
    ("GET",    "members",                        None,                          "viewer"),
    ("POST",   "orders",                         {"account_id": 100, "symbol": "EURUSD",
                                                  "side": "BUY", "order_type": "MARKET",
                                                  "volume_lots": 0.01},         "trader"),
    ("POST",   "positions/close",                {"account_id": 100, "position_id": 1}, "trader"),
    ("POST",   "orders/cancel",                  {"account_id": 100, "order_id": 1},    "trader"),
    ("PUT",    "settings",                       {"copying_enabled": False},     "admin"),
    ("POST",   "control/pause",                  {},                             "admin"),
    ("POST",   "control/resume",                 {},                             "admin"),
    ("POST",   "control/resync",                 {},                             "admin"),
    ("POST",   "control/close-all",              {},                             "admin"),
    ("POST",   "drift/dismiss",                  {"id": "abc"},                  "admin"),
    ("PATCH",  "accounts/100",                   {"enabled": True},              "admin"),
    ("DELETE", "accounts/100/connection",        None,                           "admin"),
    ("GET",    "oauth/connect",                  None,                           "admin"),
    ("POST",   "invites",                        {"role": "viewer"},             "admin"),
    ("GET",    "invites",                        None,                           "admin"),
    ("PATCH",  "",                               {"name": "Renamed"},            "owner"),
    ("DELETE", "",                               None,                           "owner"),
]

RANK = {"viewer": 0, "trader": 1, "admin": 2, "owner": 3}


@pytest.fixture
def matrix_org(app_client, make_user, make_org, db, login_as):
    """One org, one user per role, one seeded master account 100."""
    users = {role: make_user(email=f"{role}@example.com") for role in ROLES}
    org_id = make_org(name="Matrix", members=[(users[r], r) for r in ROLES])
    outsider = make_user(email="outsider@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (org_id,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role)
               VALUES (100, %s, %s, 100, false, 'master')""",
            (connection_id, org_id))
    return app_client, org_id, users, outsider


def _call(client, method, org_id, tail, body):
    url = f"/api/orgs/{org_id}/{tail}" if tail else f"/api/orgs/{org_id}"
    headers = {"X-CSRF-Token": client.cookies.get("csrf") or ""}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    if method == "GET":
        return client.get(url, follow_redirects=False)
    return getattr(client, method.lower())(url, **kwargs)


@pytest.mark.parametrize("method,tail,body,min_role", MATRIX,
                         ids=[f"{m} {t or '(org)'}" for m, t, _, _ in MATRIX])
def test_role_thresholds(matrix_org, login_as, method, tail, body, min_role):
    client, org_id, users, outsider = matrix_org

    # Anonymous: always 401 (or 403 from CSRF middleware on mutations — both
    # prove denial before any org logic).
    client.cookies.clear()
    r = _call(client, method, org_id, tail, body)
    assert r.status_code in (401, 403), f"anonymous got {r.status_code}"

    # Non-member: 404 — org existence never leaks.
    login_as(client, outsider)
    r = _call(client, method, org_id, tail, body)
    assert r.status_code == 404, f"outsider got {r.status_code}"

    # DELETE org and connection-delete are destructive — only probe DENIED
    # roles for them, and prove the allowed role separately in
    # test_destructive_rows_allowed to keep the fixture intact per param.
    destructive = (method == "DELETE")
    for role in ROLES:
        allowed = RANK[role] >= RANK[min_role]
        if destructive and allowed:
            continue
        login_as(client, users[role])
        r = _call(client, method, org_id, tail, body)
        if allowed:
            assert r.status_code not in (401, 403, 404), \
                f"{role} should pass {method} {tail}, got {r.status_code}"
        else:
            assert r.status_code == 403, \
                f"{role} should be 403 on {method} {tail}, got {r.status_code}"


def test_destructive_rows_allowed(matrix_org, login_as):
    """The allowed-role half of the destructive rows, run last against a
    dedicated fixture instance."""
    client, org_id, users, _ = matrix_org
    login_as(client, users["admin"])
    r = _call(client, "DELETE", org_id, "accounts/100/connection", None)
    assert r.status_code == 200
    login_as(client, users["owner"])
    r = _call(client, "DELETE", org_id, "", None)
    assert r.status_code == 204
```

- [ ] **Step 2: Run and fix**

```bash
cd api && .venv/bin/pytest tests/test_rbac_matrix.py -v
```
Expected: all rows PASS. Any failure is a real threshold bug in Tasks 3–8 — fix the route (to match the spec's table), never the matrix.

- [ ] **Step 3: Full api suite green gate**

```bash
cd api && .venv/bin/pytest tests -q
```
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add api/tests/test_rbac_matrix.py
git commit -m "test(api): parametrized role-matrix over every org endpoint"
```

---

### Task 10: Copier repo — org-aware data layer

**Files:**
- Modify: `copier/src/copier/db/repo.py`
- Modify: `copier/tests/unit/test_repo.py` (or wherever repo tests live — `grep -rl "load_accounts" copier/tests/unit` to find them)

**Interfaces:**
- Consumes: Task 1 schema.
- Produces (exact signatures later copier tasks rely on):

```python
@dataclass(frozen=True)
class Settings:            # process config only now
    shards: int

@dataclass(frozen=True)
class OrgRow:
    org_id: int
    name: str
    copying_enabled: bool
    dry_run: bool

@dataclass(frozen=True)
class AccountRow:          # gains org_id
    account_id: int
    org_id: int
    connection_id: int
    trader_login: int
    is_live: bool
    role: str
    enabled: bool
    multiplier: Decimal
    status: str
    last_error: str | None

class Repo:
    def get_settings(self) -> Settings                     # shards only
    def set_setting(self, name, value)                     # only 'shards' remains valid
    def load_orgs(self) -> list[OrgRow]
    def get_org(self, org_id: int) -> OrgRow               # RuntimeError if missing
    def set_org_setting(self, org_id, name, value)         # copying_enabled | dry_run
    def connection_org(self, connection_id: int) -> int    # RuntimeError if missing
    def load_accounts(self) -> list[AccountRow]            # now includes org_id
    def upsert_account(self, account_id, connection_id, org_id, trader_login,
                       is_live) -> bool                    # False = owned by another org
    def log_event(self, category, severity, payload, account_id=None,
                  latency_ms=None, org_id=None) -> int
    def create_position_mapping(self, master_position_id, slave_account_id,
                                client_order_id, org_id)
    def create_order_mapping(self, master_order_id, slave_account_id,
                             client_order_id, org_id)
    def adopt_position_mapping(self, master_position_id, slave_account_id,
                               slave_position_id, slave_volume, org_id)
    def mapping_rows(self, org_id: int | None = None) -> list[dict]
```
All other Repo methods keep their exact signatures (they address rows by ids that are already org-unique).

- [ ] **Step 1: Write the failing tests**

Locate the existing repo test file (`grep -rl "def test.*settings\|load_accounts" copier/tests/unit`). Update existing settings tests: `get_settings()` now returns only `shards`; tests that set `copying_enabled`/`dry_run` through `set_setting` move to `set_org_setting`. Add a new test file `copier/tests/unit/test_repo_orgs.py`:

```python
"""Org-aware repo layer."""
import psycopg
import pytest

from copier.db.repo import Repo


@pytest.fixture
def seeded(db):
    """Two orgs, one connection each. Returns (repo, org_a, org_b, conn_a, conn_b)."""
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        org_a = conn.execute(
            "INSERT INTO orgs (name) VALUES ('A') RETURNING id").fetchone()[0]
        org_b = conn.execute(
            "INSERT INTO orgs (name, copying_enabled, dry_run) "
            "VALUES ('B', false, true) RETURNING id").fetchone()[0]
        conn_a = conn.execute(
            """INSERT INTO ctid_connections (org_id, access_token_enc,
                   refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
               RETURNING id""", (org_a,)).fetchone()[0]
        conn_b = conn.execute(
            """INSERT INTO ctid_connections (org_id, access_token_enc,
                   refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
               RETURNING id""", (org_b,)).fetchone()[0]
    return repo, org_a, org_b, conn_a, conn_b


def test_load_orgs_and_get_org(seeded):
    repo, org_a, org_b, *_ = seeded
    orgs = {o.org_id: o for o in repo.load_orgs()}
    assert orgs[org_a].copying_enabled is True and orgs[org_a].dry_run is False
    assert orgs[org_b].copying_enabled is False and orgs[org_b].dry_run is True
    assert repo.get_org(org_b).name == "B"


def test_set_org_setting_isolated(seeded):
    repo, org_a, org_b, *_ = seeded
    repo.set_org_setting(org_a, "dry_run", True)
    assert repo.get_org(org_a).dry_run is True
    assert repo.get_org(org_b).dry_run is True  # was already true; untouched
    repo.set_org_setting(org_b, "dry_run", False)
    assert repo.get_org(org_a).dry_run is True


def test_settings_is_shards_only(seeded):
    repo, *_ = seeded
    assert repo.get_settings().shards == 1
    with pytest.raises(ValueError):
        repo.set_setting("copying_enabled", False)


def test_upsert_account_respects_org_ownership(seeded):
    repo, org_a, org_b, conn_a, conn_b = seeded
    assert repo.upsert_account(100, conn_a, org_a, 100, False) is True
    # same org re-upsert: fine
    assert repo.upsert_account(100, conn_a, org_a, 100, False) is True
    # ANOTHER org discovering the same broker account: refused, row unchanged
    assert repo.upsert_account(100, conn_b, org_b, 100, False) is False
    rows = repo.load_accounts()
    assert len(rows) == 1 and rows[0].org_id == org_a


def test_connection_org(seeded):
    repo, org_a, org_b, conn_a, conn_b = seeded
    assert repo.connection_org(conn_a) == org_a
    assert repo.connection_org(conn_b) == org_b


def test_log_event_carries_org(seeded, db):
    repo, org_a, *_ = seeded
    repo.log_event("control", "info", {"x": 1}, org_id=org_a)
    repo.log_event("connection", "info", {"x": 2})
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT org_id, payload->>'x' FROM events ORDER BY id").fetchall()
    assert rows == [(org_a, "1"), (None, "2")]


def test_mapping_rows_org_filter(seeded):
    repo, org_a, org_b, conn_a, conn_b = seeded
    repo.upsert_account(100, conn_a, org_a, 100, False)
    repo.upsert_account(201, conn_b, org_b, 201, False)
    repo.create_position_mapping(1, 100, "cm1.100", org_id=org_a)
    repo.create_position_mapping(2, 201, "cm2.201", org_id=org_b)
    assert {m["org_id"] for m in repo.mapping_rows()} == {org_a, org_b}
    only_a = repo.mapping_rows(org_id=org_a)
    assert len(only_a) == 1 and only_a[0]["client_order_id"] == "cm1.100"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd copier && .venv/bin/pytest tests/unit/test_repo_orgs.py -x -q
```
Expected: FAIL — `ImportError`/`AttributeError` for `OrgRow`, `load_orgs`, etc.

- [ ] **Step 3: Implement in `repo.py`**

- `Settings` → single field `shards: int`; `get_settings()` runs `SELECT shards FROM settings WHERE id = true`.
- `set_setting`: the `columns` dict shrinks to `{"shards": "shards"}` (unknown names still raise `ValueError`).
- Add after `Settings`:

```python
@dataclass(frozen=True)
class OrgRow:
    """One org's engine-relevant state (per-org kill switch and dry-run)."""
    org_id: int
    name: str
    copying_enabled: bool
    dry_run: bool
```

- New methods on `Repo`:

```python
    def load_orgs(self) -> list[OrgRow]:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT id, name, copying_enabled, dry_run FROM orgs"
            ).fetchall()
        return [OrgRow(org_id=r[0], name=r[1], copying_enabled=r[2], dry_run=r[3])
                for r in rows]

    def get_org(self, org_id: int) -> OrgRow:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT id, name, copying_enabled, dry_run FROM orgs WHERE id = %s",
                (org_id,),
            ).fetchone()
        if not row:
            raise RuntimeError(f"org {org_id} not found")
        return OrgRow(org_id=row[0], name=row[1], copying_enabled=row[2], dry_run=row[3])

    def set_org_setting(self, org_id: int, name: str, value) -> None:
        columns = {"copying_enabled": "copying_enabled", "dry_run": "dry_run"}
        if name not in columns:
            raise ValueError(f"Unknown org setting: {name}")
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                f"UPDATE orgs SET {columns[name]} = %s WHERE id = %s",
                (value, org_id),
            )

    def connection_org(self, connection_id: int) -> int:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT org_id FROM ctid_connections WHERE id = %s", (connection_id,)
            ).fetchone()
        if not row:
            raise RuntimeError(f"connection {connection_id} not found")
        return row[0]
```

- `AccountRow` gains `org_id: int` (second field); `load_accounts()` selects `org_id` after `ctid_trader_account_id` and fills it.
- `log_event` gains keyword `org_id: int | None = None`; the INSERT becomes `INSERT INTO events (account_id, org_id, category, severity, latency_ms, payload) VALUES (%s, %s, %s, %s, %s, %s)`.
- `upsert_account` — new signature `(self, account_id, connection_id, org_id, trader_login, is_live) -> bool`, atomic ownership guard:

```python
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cursor = conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                                      org_id, trader_login, is_live)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ctid_trader_account_id) DO UPDATE SET
                    ctid_connection_id = EXCLUDED.ctid_connection_id,
                    trader_login = EXCLUDED.trader_login,
                    is_live = EXCLUDED.is_live
                WHERE accounts.org_id = EXCLUDED.org_id
                """,
                (account_id, connection_id, org_id, trader_login, is_live),
            )
            return cursor.rowcount > 0
```

- `create_position_mapping`, `create_order_mapping`, `adopt_position_mapping`: append an `org_id` parameter; their INSERTs gain the `org_id` column.
- `mapping_rows(self, org_id: int | None = None)`: SELECT list gains `org_id`; when the parameter is given append `WHERE org_id = %s`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd copier && .venv/bin/pytest tests/unit/test_repo_orgs.py -v
```
Expected: all PASS. Then fix the pre-existing repo/unit tests this breaks (callers of `upsert_account`/`create_*_mapping`/`get_settings` in tests): mechanical — thread a seeded `org_id` through. Run:

```bash
cd copier && .venv/bin/pytest tests/unit -q
```
Many engine tests will still be red (they construct `CopierService`/`Dispatcher` with old signatures) — those are Task 11's; the gate for THIS task is: repo tests green, and remaining failures only in engine/service/dispatch/reconcile/main test modules.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/db/repo.py copier/tests
git commit -m "feat(copier): org-aware repo — OrgRow, org_id on accounts/events/mappings, ownership-guarded upsert"
```

---

### Task 11: Engine partitioning — routing, service, dispatcher

**Files:**
- Create: `copier/src/copier/engine/routing.py`
- Create: `copier/tests/unit/test_routing.py`
- Modify: `copier/src/copier/engine/service.py`
- Modify: `copier/src/copier/engine/dispatch.py`
- Modify: `copier/tests/unit/test_service*.py`, `copier/tests/unit/test_dispatch*.py` (thread org fixtures through)

**Interfaces:**
- Consumes: Task 10 repo.
- Produces:

```python
# copier/src/copier/engine/routing.py
@dataclass(frozen=True)
class OrgRouting:
    org_by_account: Mapping[int, int]            # account_id -> org_id
    master_by_org: Mapping[int, int]             # org_id -> master account_id
    slaves_by_org: Mapping[int, list[SlaveConfig]]  # enabled+status-filtered

def build_routing(accounts: list[AccountRow],
                  symbol_loader: Callable[[int], Mapping[str, SymbolInfo]]) -> OrgRouting
```

- `CopierService.__init__(repo, dispatcher, routing_provider, master_symbols_by_org, clock=None)` where `routing_provider: Callable[[], OrgRouting]` and `master_symbols_by_org: Mapping[int, dict[int, SymbolInfo]]` (org_id → symbol_id → SymbolInfo; the outer dict object is shared with CopierApp, which mutates the inner dicts in place on reload).
- `Dispatcher.dispatch(intents, org_id: int)` — copy gates read `repo.get_org(org_id)`; mapping creation and gate/dry-run event logs pass `org_id`. `send_direct` unchanged.

- [ ] **Step 1: Write the failing routing tests**

`copier/tests/unit/test_routing.py`:

```python
from decimal import Decimal

from copier.db.repo import AccountRow
from copier.engine.routing import build_routing


def _row(account_id, org_id, role, enabled=True, status="ok"):
    return AccountRow(
        account_id=account_id, org_id=org_id, connection_id=1, trader_login=account_id,
        is_live=False, role=role, enabled=enabled, multiplier=Decimal("1.0"),
        status=status, last_error=None)


def test_two_orgs_route_independently():
    accounts = [
        _row(100, 1, "master"), _row(101, 1, "slave"), _row(102, 1, "slave"),
        _row(200, 2, "master"), _row(201, 2, "slave"),
        _row(300, 2, "ignored"),
    ]
    routing = build_routing(accounts, symbol_loader=lambda account_id: {})
    assert routing.org_by_account[101] == 1 and routing.org_by_account[201] == 2
    assert routing.master_by_org == {1: 100, 2: 200}
    assert [s.account_id for s in routing.slaves_by_org[1]] == [101, 102]
    assert [s.account_id for s in routing.slaves_by_org[2]] == [201]
    assert 300 in routing.org_by_account  # ignored accounts still resolve to their org


def test_paused_or_disabled_slaves_are_not_enabled():
    accounts = [
        _row(100, 1, "master"),
        _row(101, 1, "slave", enabled=False),
        _row(102, 1, "slave", status="paused"),
    ]
    routing = build_routing(accounts, symbol_loader=lambda account_id: {})
    flags = {s.account_id: s.enabled for s in routing.slaves_by_org[1]}
    assert flags == {101: False, 102: False}


def test_org_without_master():
    routing = build_routing([_row(201, 2, "slave")], symbol_loader=lambda a: {})
    assert 2 not in routing.master_by_org
    assert routing.org_by_account[201] == 2
```

- [ ] **Step 2: Run to verify failure, then implement `routing.py`**

```bash
cd copier && .venv/bin/pytest tests/unit/test_routing.py -x -q   # ModuleNotFoundError
```

```python
"""Per-org routing: which org an account belongs to, each org's master, and
each org's slave fleet. Pure data derived from the accounts table; rebuilt by
CopierApp on boot and on every reload()."""

from dataclasses import dataclass
from typing import Callable, Mapping

from copier.db.repo import AccountRow
from copier.domain.models import SlaveConfig, SymbolInfo


@dataclass(frozen=True)
class OrgRouting:
    org_by_account: Mapping[int, int]
    master_by_org: Mapping[int, int]
    slaves_by_org: Mapping[int, list[SlaveConfig]]


def build_routing(
    accounts: list[AccountRow],
    symbol_loader: Callable[[int], Mapping[str, SymbolInfo]],
) -> OrgRouting:
    org_by_account: dict[int, int] = {}
    master_by_org: dict[int, int] = {}
    slaves_by_org: dict[int, list[SlaveConfig]] = {}
    for a in accounts:
        org_by_account[a.account_id] = a.org_id
        if a.role == "master":
            master_by_org[a.org_id] = a.account_id
        elif a.role == "slave":
            slaves_by_org.setdefault(a.org_id, []).append(
                SlaveConfig(
                    account_id=a.account_id,
                    enabled=a.enabled and a.status != "paused",
                    multiplier=a.multiplier,
                    symbols=symbol_loader(a.account_id),
                )
            )
    return OrgRouting(
        org_by_account=org_by_account,
        master_by_org=master_by_org,
        slaves_by_org=slaves_by_org,
    )
```

Run the routing tests: PASS.

- [ ] **Step 3: Convert `dispatch.py`**

In `Dispatcher.dispatch`, change the signature and gates:

```python
    def dispatch(self, intents: Sequence[SlaveIntent], org_id: int) -> None:
        ...
        org = self._repo.get_org(org_id)

        for intent in intents:
            try:
                if isinstance(intent, Alert):
                    self._handle_alert(intent, org_id)
                elif isinstance(intent, LinkPendingFill):
                    self._handle_link_pending_fill(intent, org_id)
                elif not org.copying_enabled:
                    self._handle_kill_switch(intent, org_id)
                elif org.dry_run:
                    self._handle_dry_run(intent, org_id)
                else:
                    self._handle_live_send(intent, org_id)
            except Exception as e:
                ...  # unchanged, but the log_event call gains org_id=org_id
```

Thread `org_id` through `_handle_alert`, `_handle_link_pending_fill`, `_handle_kill_switch`, `_handle_dry_run`, `_handle_live_send`, and `_create_mapping(intent, account_id, org_id)` — each `repo.log_event(...)` in those methods gains `org_id=org_id`, and `_create_mapping` passes `org_id` to `create_position_mapping`/`create_order_mapping`. `_send_with_retries`, `send_direct`, `_on_send_success`, `_on_send_failure` are untouched (transport-level; their events remain account-attributed — the events' org can be joined later if ever needed, and `send_direct` is also used by org-validated operator paths).

- [ ] **Step 4: Convert `service.py`**

`CopierService.__init__` becomes:

```python
    def __init__(
        self,
        repo: Repo,
        dispatcher: Dispatcher,
        routing_provider: Callable[[], "OrgRouting"],
        master_symbols_by_org: Mapping[int, dict[int, SymbolInfo]],
        clock=None,
    ):
```
storing `self._routing_provider` and `self._master_symbols_by_org` (delete `_master_account_id`, `_master_symbols_by_id`, `_slaves_provider`).

`handle_execution`:

```python
        try:
            start_time = time.time_ns() // 1_000_000

            routing = self._routing_provider()
            org_id = routing.org_by_account.get(account_id)
            if org_id is None:
                self._repo.log_event(
                    'drift', 'info',
                    {'action': 'event_from_unknown_account', 'account_id': account_id,
                     'execution_type': ProtoOAExecutionType.Name(evt.executionType)},
                    account_id=account_id,
                )
                return

            if routing.master_by_org.get(org_id) == account_id:
                self._handle_master_event(org_id, account_id, evt, start_time, routing)
            else:
                self._handle_slave_event(org_id, account_id, evt, routing)
        except Exception as e:
            ...  # the existing catch-all, unchanged except that its
                 # log_event call gains org_id=org_id
```

So the error event carries the org when it is known: initialize `org_id = None` on the line **before** `try`, assign it inside as shown, and pass `org_id=org_id` in the handler's `log_event`.

`_handle_master_event(self, org_id, master_account_id, evt, start_time)`:
- `normalize(evt, self._master_symbols_by_org.get(org_id, {}))`
- the master-event log call: `account_id=master_account_id, org_id=org_id`
- `slaves = routing.slaves_by_org.get(org_id, [])` — pass `routing` in or re-call the provider; simplest: `_handle_master_event(self, org_id, master_account_id, evt, start_time, routing)`.
- `intents = decide(normalized, self._repo, slaves)` — unchanged (pure).
- `self._dispatcher.dispatch(intents, org_id=org_id)`.

`_handle_slave_event(self, org_id, account_id, evt, routing)`:
- `_is_known_enabled_slave` becomes an inline check against `routing.slaves_by_org.get(org_id, [])` (delete the old method).
- Every `log_event` in the slave-handling paths (`_handle_slave_fill`, `_handle_slave_order_accepted`, `_handle_slave_order_cancelled`, `_handle_slave_order_rejected`) gains `org_id=org_id` — thread `org_id` as their first parameter.

`_schedule_pending_fill_check(self, org_id, pending_filled)` — its inner log gains `org_id=org_id`.

- [ ] **Step 5: Update the service/dispatch unit tests and run**

Existing tests construct `CopierService(repo, dispatcher, master_account_id=..., master_symbols_by_id=..., slaves_provider=...)` and `dispatcher.dispatch(intents)`. Update them with a small helper placed in `copier/tests/unit/conftest.py`:

```python
import pytest
from copier.engine.routing import OrgRouting


@pytest.fixture
def make_routing():
    """OrgRouting for a single-org test world:
    make_routing(master=100, slaves=[SlaveConfig...], org_id=1)"""
    def _make(master, slaves, org_id=1):
        org_by_account = {master: org_id, **{s.account_id: org_id for s in slaves}}
        return OrgRouting(
            org_by_account=org_by_account,
            master_by_org={org_id: master},
            slaves_by_org={org_id: list(slaves)},
        )
    return _make
```

Then, mechanically per test file: service construction becomes
`CopierService(repo=repo, dispatcher=dispatcher, routing_provider=lambda: routing, master_symbols_by_org={1: symbols_by_id_dict})`; direct `dispatch(intents)` calls become `dispatch(intents, org_id=1)`; DB-backed tests seed an org row + org-threaded accounts (the Task 10 fixtures show the INSERT shape). Add one NEW cross-org test to the service tests:

```python
def test_master_event_from_org_a_never_reaches_org_b_slaves(...):
    """Two orgs in one routing table; a fill on org A's master produces
    intents only for org A's slaves and dispatch is called with org A's id."""
```
Build it with `OrgRouting(org_by_account={100: 1, 101: 1, 200: 2, 201: 2}, master_by_org={1: 100, 2: 200}, slaves_by_org={1: [slave_101], 2: [slave_201]})`, fire a master fill for account 100 through `handle_execution`, and assert the recorded `dispatch` call's intents all target 101 and its `org_id == 1` (use the same fake-dispatcher pattern the surrounding tests already use).

```bash
cd copier && .venv/bin/pytest tests/unit -q
```
Expected: unit suite green except `test_main*.py`/reconcile/control modules (Task 12–13).

- [ ] **Step 6: Commit**

```bash
git add copier/src/copier/engine/routing.py copier/src/copier/engine/service.py copier/src/copier/engine/dispatch.py copier/tests
git commit -m "feat(copier): per-org engine routing; dispatcher gates on org settings"
```

---

### Task 12: Reconciler — one instance per org

**Files:**
- Modify: `copier/src/copier/engine/reconcile.py`
- Modify: `copier/tests/unit/test_reconcile*.py`

**Interfaces:**
- Consumes: Task 10 repo, Task 11 dispatcher signature.
- Produces: `Reconciler.__init__(clients_by_account, repo, dispatcher, master_account_id, org_id)` — one Reconciler serves exactly one org. `run()` reconciles only that org's accounts/mappings; `close_orphan` dispatches with `org_id`; `compute_drift` (the pure function) is **unchanged** — callers feed it org-scoped inputs.
- Consumed by: Task 13's `CopierApp.reconcilers: dict[int, Reconciler]`.

- [ ] **Step 1: Update tests / add the isolation test**

Existing reconcile tests: constructor calls gain `org_id=1` and DB-backed ones seed an org (Task 10 fixture shape). Add to the reconcile test file:

```python
def test_run_is_org_scoped(db, ...):
    """Org A's reconciler must not fetch snapshots for, or report drift
    against, org B's accounts or mappings."""
```
Build it with two orgs in the DB (masters 100/200, slaves 101/201, one active mapping per org), a fake `clients_by_account` that records which account_ids were snapshot-requested, and a Reconciler with `org_id=org_a`. Assert: snapshot requests ⊆ {100, 101}; `repo.mapping_rows(org_id=org_b)` untouched by any drift logging (no 'drift' events with org B's org_id); `reconciler.current` items reference only org A accounts. Follow the file's existing fake-client pattern for reconcile responses.

- [ ] **Step 2: Run to verify failures**

```bash
cd copier && .venv/bin/pytest tests/unit/test_reconcile*.py -x -q
```
Expected: FAIL — `TypeError` on the new ctor param / org-scoping assertions.

- [ ] **Step 3: Convert `reconcile.py`**

- `__init__` gains `org_id: int`, stored as `self.org_id`.
- `run()` (lines 445-446 area):

```python
        accounts = [a for a in self.repo.load_accounts() if a.org_id == self.org_id]
        enabled_slave_ids = {a.account_id for a in accounts
                             if a.role == 'slave' and a.enabled}
```

- `mappings = self.repo.mapping_rows(org_id=self.org_id)`.
- The dry-run read becomes:

```python
        try:
            dry_run = self.repo.get_org(self.org_id).dry_run
        except Exception:
            log.exception("reconcile: could not read org settings; assuming dry_run=False")
            dry_run = False
```

- Every `self.repo.log_event(...)` in `run()` and `dismiss()` gains `org_id=self.org_id`.
- `close_orphan()`: `self.dispatcher.dispatch([intent], org_id=self.org_id)`.
- `adopt()`: `self.repo.adopt_position_mapping(..., org_id=self.org_id)`.
- `_lookup_slave_volume`'s fallback `self.repo.mapping_rows()` → `self.repo.mapping_rows(org_id=self.org_id)`.

- [ ] **Step 4: Run tests to verify they pass, commit**

```bash
cd copier && .venv/bin/pytest tests/unit/test_reconcile*.py -v
git add copier/src/copier/engine/reconcile.py copier/tests
git commit -m "feat(copier): reconciler is per-org"
```

---

### Task 13: CopierApp — per-org composition, controls, kill switch, state

**Files:**
- Modify: `copier/src/copier/main.py`
- Modify: `copier/tests/unit/test_main*.py` (and any integration tests constructing `build_app`)

**Interfaces:**
- Consumes: Tasks 10–12.
- Produces (control.py in Task 14 and the API rely on these exact signatures):

```python
class CopierApp:
    routing_provider: Callable[[], OrgRouting]     # fresh DB-backed snapshot
    master_symbols_by_org: dict[int, dict]         # org_id -> {symbol_id: SymbolInfo}
    state_trackers: dict[int, AccountStateTracker] # org_id -> tracker (orgs with a master)
    reconcilers: dict[int, Reconciler]             # org_id -> reconciler (orgs with a master)

    def pause(self, org_id: int, account_id: int | None = None) -> Deferred
    def resume(self, org_id: int, account_id: int | None = None) -> Deferred
    def set_dry_run(self, org_id: int, enabled: bool) -> None
    def resync(self, org_id: int | None = None) -> Deferred   # None = every org
    def close_all(self, org_id: int, account_id: int | None = None) -> Deferred
    def get_health(self) -> dict   # {"status": "ok", "orgs": [{org_id, master, copying_enabled, dry_run}]}
    def get_state(self, org_id: int) -> dict   # same shape as before, org-scoped
    def reconciler_for(self, org_id: int) -> Reconciler   # ValueError if org has no engine
    def discover(self, connection_id: int) -> Deferred    # org from the connection row
```
`startup()`, `reload()`, `refresh_balances()`, `periodic_resync()`, `request_resync()`, `refresh_due_tokens()`, `place_order()`, `close_position()`, `cancel_order()`, `get_account_details()`, `get_*_history()` keep their names and external contracts (account-scoped ones are unchanged — tenancy for them is enforced by the API).

- [ ] **Step 1: Update/write tests**

Convert `test_main*.py` construction sites (whatever builds `build_app` / `CopierApp`): seed orgs + org-threaded accounts. Then add these tests (fitting the file's existing fake-client/fake-reactor patterns):

```python
def test_pause_and_dry_run_are_org_scoped(...):
    """pause(org_a) flips orgs.copying_enabled only for org A;
    set_dry_run(org_b, True) only for org B."""

def test_close_all_flattens_only_the_org(...):
    """Two orgs, both with open positions on the fake broker. close_all(org_a)
    pauses org A's copying (org B's stays enabled) and sends
    ProtoOAClosePositionReq only for org A's accounts."""

def test_get_state_is_org_scoped(...):
    """get_state(org_a) surfaces only org A's mappings/positions/drift."""

def test_get_health_lists_all_orgs(...):

def test_discover_conflict_logs_and_skips(...):
    """Account already owned by org A; discovery on org B's connection
    upserts nothing (the row keeps org A) and logs an 'error' event with
    org B's org_id and action 'discover_conflict'."""

def test_reload_rebuilds_per_org_engines(...):
    """After giving org B a master via SQL and calling reload(), the app has
    reconcilers/state_trackers/master_symbols_by_org entries for both orgs;
    after deleting org B, reload() drops them."""
```

- [ ] **Step 2: Run to verify failures**

```bash
cd copier && .venv/bin/pytest tests/unit/test_main*.py -x -q
```

- [ ] **Step 3: Convert `main.py`**

Import `from copier.engine.routing import OrgRouting, build_routing`.

**`CopierApp.__init__`** — replace `service/reconciler/state_tracker/master_symbols_by_id/master_account_id` params with:

```python
        service: CopierService,
        reconcilers: dict[int, Reconciler],
        state_trackers: dict[int, "AccountStateTracker | None"],
        dispatcher: Dispatcher,
        client_factory: Callable[[bool], CTraderClient],
        shards: int,
        master_symbols_by_org: dict[int, dict],
        routing_provider: Callable[[], OrgRouting],
        clock=None,
```
(store each; delete `self.master_account_id`, `self.state_tracker`, `self.reconciler`, `self.master_symbols_by_id`).

**`reconciler_for`**:

```python
    def reconciler_for(self, org_id: int) -> Reconciler:
        reconciler = self.reconcilers.get(org_id)
        if reconciler is None:
            raise ValueError(f"org {org_id} has no active engine (no master?)")
        return reconciler
```

**`pause` / `resume`** — org-scoped settings write; per-slave branch also verifies the account is in the org:

```python
    def pause(self, org_id: int, account_id: int | None = None) -> defer.Deferred:
        """Pause copying for one org, or one of its slaves, then reload."""
        if account_id is None:
            self.repo.set_org_setting(org_id, "copying_enabled", False)
            self.repo.log_event('control', 'info', {'action': 'pause_org'}, org_id=org_id)
            log.info("copying paused for org %s", org_id)
        else:
            self._require_account_in_org(org_id, account_id)
            self.repo.set_account_status(account_id, 'paused')
            self.repo.log_event(
                'control', 'info', {'action': 'pause_slave', 'account_id': account_id},
                account_id=account_id, org_id=org_id,
            )
            log.info("slave %s paused", account_id)
        return self.reload()
```
(`resume` mirrors it with `True` / `'ok'` / `'resume_org'` / `'resume_slave'`.) The helper:

```python
    def _require_account_in_org(self, org_id: int, account_id: int) -> None:
        account = next(
            (a for a in self.repo.load_accounts() if a.account_id == account_id), None)
        if account is None or account.org_id != org_id:
            raise ValueError(f"account {account_id} not in org {org_id}")
```

**`set_dry_run`**:

```python
    def set_dry_run(self, org_id: int, enabled: bool) -> None:
        self.repo.set_org_setting(org_id, "dry_run", enabled)
        self.repo.log_event(
            'control', 'info', {'action': 'set_dry_run', 'enabled': enabled},
            org_id=org_id)
        log.info("dry-run for org %s: %s", org_id, "enabled" if enabled else "disabled")
```

**`resync`** — per-org, defaulting to all:

```python
    @defer.inlineCallbacks
    def resync(self, org_id: int | None = None):
        """Run reconciliation for one org (or all) and feed each org's master
        positions into its state tracker."""
        org_ids = [org_id] if org_id is not None else list(self.reconcilers.keys())
        all_items = []
        for oid in org_ids:
            reconciler = self.reconcilers.get(oid)
            if reconciler is None:
                continue
            items = yield reconciler.run()
            all_items.extend(items or [])
            tracker = self.state_trackers.get(oid)
            if tracker is not None:
                positions = [
                    StatePositionSnapshot(
                        position_id=p.position_id, symbol_id=p.symbol_id, side=p.side,
                        volume=p.volume, price=p.price, label=p.label,
                    )
                    for p in reconciler.master_positions
                ]
                tracker.set_positions(reconciler.master_account_id, positions)
                try:
                    yield tracker.ensure_spot_subscriptions()
                except Exception:
                    log.exception("resync: ensure_spot_subscriptions failed (org %s)", oid)
        yield self.refresh_balances()
        return all_items
```

**`periodic_resync`** guard change: `if self._resync_in_flight or not self.reconcilers:` (replaces the `master_account_id is None` check).

**`close_all`** — the org boundary of the kill switch:

```python
    @defer.inlineCallbacks
    def close_all(self, org_id: int, account_id: int | None = None):
        """Kill switch for ONE org: flatten one of its accounts, or
        (account_id=None) every enabled, non-paused account in the org —
        master included. The org-wide flatten pauses THAT ORG's copying
        first; no other org's settings or accounts are ever touched."""
        if account_id is not None:
            self._require_account_in_org(org_id, int(account_id))
            summary = yield self._flatten_account(int(account_id))
            results = [summary]
            paused = False
        else:
            self.repo.set_org_setting(org_id, "copying_enabled", False)
            paused = True
            targets = [
                a for a in self.repo.load_accounts()
                if a.org_id == org_id and a.enabled and a.status != 'paused'
            ]
            results = []
            for account in targets:
                try:
                    summary = yield self._flatten_account(account.account_id)
                except Exception as e:
                    log.error("close_all: flatten %s failed: %s", account.account_id, e)
                    summary = {"account_id": account.account_id,
                               "positions_closed": 0, "orders_cancelled": 0,
                               "error": str(e)}
                results.append(summary)

        self.repo.log_event(
            'control', 'warning',
            {'action': 'kill_switch', 'org_wide': account_id is None,
             'accounts': results},
            account_id=account_id, org_id=org_id,
        )
        return {"status": "flattened", "paused": paused, "accounts": results}
```
(`_flatten_account` itself is unchanged.)

**`get_health`**:

```python
    def get_health(self) -> dict:
        routing = self.routing_provider()
        orgs = []
        for org in self.repo.load_orgs():
            orgs.append({
                "org_id": org.org_id,
                "master": routing.master_by_org.get(org.org_id),
                "copying_enabled": org.copying_enabled,
                "dry_run": org.dry_run,
            })
        return {"status": "ok", "orgs": orgs}
```

**`get_state(self, org_id: int)`** — the body keeps its exact structure with these substitutions:
- `state_tracker = self.state_trackers.get(org_id)`; `reconciler = self.reconcilers.get(org_id)`.
- `accounts_snapshot = state_tracker.snapshot() if state_tracker is not None else {}`.
- `mappings = self.repo.mapping_rows(org_id=org_id)`.
- `master_symbol()` reads `self.master_symbols_by_org.get(org_id, {})`.
- The `master_pnl_by_position` block keys off `reconciler.master_account_id` instead of `self.master_account_id`, and the two loops read `reconciler.master_positions` / `reconciler.master_orders`; when `reconciler is None`, `master_positions`/`pending_orders` stay `[]` and `drift` is `[]`.

**`startup` / `_fetch_and_cache_symbols`**: in `_fetch_and_cache_symbols`, the master branch becomes:

```python
                if account.role == 'master':
                    org_symbols = self.master_symbols_by_org.setdefault(account.org_id, {})
                    org_symbols.clear()
                    org_symbols.update(symbols_by_id(symbol_map))
```
`startup()` is otherwise unchanged (`resync()` with no args now covers every org).

**`refresh_balances`/`_refresh_balances_body`** — per-org trackers:

```python
    @defer.inlineCallbacks
    def _refresh_balances_body(self):
        if not self.state_trackers:
            return
        try:
            accounts = self.repo.load_accounts()
        except Exception:
            log.exception("refresh_balances: failed to load accounts")
            return
        for org_id, tracker in self.state_trackers.items():
            if tracker is None:
                continue
            enabled_ids = [a.account_id for a in accounts
                           if a.org_id == org_id and a.enabled]
            if not enabled_ids:
                continue
            try:
                yield tracker.refresh_balances(enabled_ids)
            except Exception:
                log.exception("refresh_balances: broker request failed (org %s)", org_id)
```

**`reload`** — replace everything from the `master_account = next(...)` line (443) to the end of the method with per-org engine reconciliation:

```python
        # Rebuild each org's engine wiring from the accounts table. The
        # in-memory per-org symbol dicts are refreshed unconditionally from
        # the DB cache (plain local read; see the pre-multi-org comment on
        # promoted former slaves).
        routing = self.routing_provider()
        live_orgs = set(routing.master_by_org.keys())

        for org_id, master_id in routing.master_by_org.items():
            master_account = next(
                (a for a in accounts if a.account_id == master_id), None)
            if master_account is None:
                continue
            org_symbols = self.master_symbols_by_org.setdefault(org_id, {})
            org_symbols.clear()
            org_symbols.update(symbols_by_id(
                self.repo.load_symbol_cache(master_id)))

            reconciler = self.reconcilers.get(org_id)
            if reconciler is None:
                self.reconcilers[org_id] = Reconciler(
                    clients_by_account=self._clients_by_account, repo=self.repo,
                    dispatcher=self.dispatcher, master_account_id=master_id,
                    org_id=org_id,
                )
            elif reconciler.master_account_id != master_id:
                reconciler.master_account_id = master_id

            tracker = self.state_trackers.get(org_id)
            if tracker is None or tracker._master_account_id != master_id:
                master_client = self._client_for_account(master_account)
                self.state_trackers[org_id] = AccountStateTracker(
                    master_client=master_client, repo=self.repo,
                    master_account_id=master_id, symbols_by_id=org_symbols,
                )

        # Orgs that lost their master (or were deleted) lose their engines.
        for org_id in list(self.reconcilers.keys()):
            if org_id not in live_orgs:
                del self.reconcilers[org_id]
                self.state_trackers.pop(org_id, None)
                self.master_symbols_by_org.pop(org_id, None)

        self.repo.log_event('control', 'info',
                            {'action': 'reload', 'account_count': len(accounts)})
```
This needs `self._clients_by_account` — store the `clients_by_account` callable on the app: `build_app` passes it as a new ctor arg `clients_by_account` (see below).

**`discover`** — resolve the org and enforce ownership:

```python
        org_id = self.repo.connection_org(connection_id)
        discovered = list(res.ctidTraderAccount)
        conflicts = []
        for acc in discovered:
            applied = self.repo.upsert_account(
                account_id=acc.ctidTraderAccountId,
                connection_id=connection_id,
                org_id=org_id,
                trader_login=acc.traderLogin,
                is_live=acc.isLive,
            )
            if not applied:
                conflicts.append(acc.ctidTraderAccountId)
                self.repo.log_event(
                    'control', 'error',
                    {'action': 'discover_conflict',
                     'account_id': acc.ctidTraderAccountId,
                     'detail': 'account already connected to another organization'},
                    account_id=acc.ctidTraderAccountId, org_id=org_id,
                )
        ...
        self.repo.log_event(
            'control', 'info',
            {'action': 'discover', 'connection_id': connection_id,
             'account_count': len(discovered), 'conflicts': conflicts},
            org_id=org_id,
        )
        return discovered
```
(The `DiscoverResource` response in Task 14 adds the conflicts list.)

**`build_app`** — the per-org composition:

```python
    clients_by_account = _build_clients_by_account(repo, clients, shards)
    send_for_account = _build_send_for_account(clients_by_account)

    bucket = TokenBucket(clock=clock)
    dispatcher = Dispatcher(send_for_account=send_for_account, repo=repo,
                            bucket=bucket, clock=clock)

    master_symbols_by_org: dict[int, dict] = {}

    def routing_provider() -> OrgRouting:
        # Fresh DB-backed snapshot per call — same freshness contract the old
        # slaves_provider had (enabled/multiplier edits apply on the next
        # event without waiting for a reload).
        return build_routing(repo.load_accounts(), repo.load_symbol_cache)

    service = CopierService(
        repo=repo, dispatcher=dispatcher, routing_provider=routing_provider,
        master_symbols_by_org=master_symbols_by_org, clock=clock,
    )

    initial_routing = build_routing(accounts, repo.load_symbol_cache)
    reconcilers: dict[int, Reconciler] = {}
    state_trackers: dict[int, AccountStateTracker] = {}
    for org_id, master_id in initial_routing.master_by_org.items():
        master_account = next(a for a in accounts if a.account_id == master_id)
        reconcilers[org_id] = Reconciler(
            clients_by_account=clients_by_account, repo=repo,
            dispatcher=dispatcher, master_account_id=master_id, org_id=org_id,
        )
        org_symbols = master_symbols_by_org.setdefault(org_id, {})
        master_client = clients[master_account.is_live][master_id % shards]
        state_trackers[org_id] = AccountStateTracker(
            master_client=master_client, repo=repo,
            master_account_id=master_id, symbols_by_id=org_symbols,
        )

    app = CopierApp(
        repo=repo, token_store=token_store, clients=clients, service=service,
        reconcilers=reconcilers, state_trackers=state_trackers,
        dispatcher=dispatcher, client_factory=client_factory, shards=shards,
        master_symbols_by_org=master_symbols_by_org,
        routing_provider=routing_provider, clock=clock,
    )
    app._clients_by_account = clients_by_account
    service.on_positions_changed = app.request_resync
    ...  # client wiring loop unchanged
```

- [ ] **Step 4: Run the copier unit suite, then the integration suite**

```bash
cd copier && .venv/bin/pytest tests/unit -q
cd copier && .venv/bin/pytest tests --timeout=60 -q
```
Expected: unit green after mechanical fixture updates; integration tests (fake broker) updated the same way — seed an org, thread `org_id`. Fix as needed; the gate is a fully green copier suite except `tests/unit/test_control*.py` (Task 14).

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/main.py copier/tests
git commit -m "feat(copier): per-org composition — org-scoped pause/dry-run/close-all/state/discover"
```

---

### Task 14: Control endpoint — org parameters

**Files:**
- Modify: `copier/src/copier/engine/control.py`
- Modify: `copier/tests/unit/test_control*.py`

**Interfaces:**
- Consumes: Task 13's `CopierApp` signatures.
- Produces (what the API from Tasks 5–6 already sends):
  - `POST /pause`, `/resume`: body `{"org_id": int, "account_id": int|null}` — org_id required.
  - `POST /resync`: body `{"org_id": int}` — org_id required.
  - `POST /dry-run`: body `{"org_id": int, "enabled": bool}`.
  - `POST /close-all`: body `{"org_id": int, "account_id": int?}`.
  - `GET /state?org_id=N`.
  - `POST /drift/close-orphan|adopt|dismiss`: body gains required `"org_id"`.
  - `GET /health`, `POST /reload`, `POST /discover`, `GET /details`, `GET /history/*`, `POST /order`, `/positions/close`, `/orders/cancel`: request and response shapes unchanged (discover conflicts surface via the `discover_conflict` error events, not the response).

- [ ] **Step 1: Update the control tests**

`test_control*.py`: every pause/resume/resync/dry-run/close-all/drift/state request gains the org parameter; assertions updated to the Task 13 signatures. Add:

```python
def test_scoped_commands_without_org_id_are_400(...):
    """POST /pause, /resume, /resync, /dry-run, /close-all, /drift/dismiss
    with no org_id in the body must 400 with 'org_id required' — a scoped
    command with no org must never fall through to anything global."""

def test_state_requires_org_id(...):
    """GET /state without ?org_id= is a 400."""
```

- [ ] **Step 2: Run to verify failures, then convert `control.py`**

Add one helper next to `_int_arg`:

```python
def _org_id_from(body: dict) -> int:
    org_id = body.get("org_id")
    if org_id is None:
        raise ValueError("org_id required")
    try:
        return int(org_id)
    except (TypeError, ValueError):
        raise ValueError("org_id must be an integer")
```

Resource changes (each `_handle` body):

```python
class StateResource(_JsonResource):
    """GET /state?org_id=N: one org's snapshots, positions, drift."""

    def _handle(self, request, body):
        return self.app.get_state(_int_arg(request, b"org_id"))


class PauseResource(_JsonResource):
    def _handle(self, request, body):
        org_id = _org_id_from(body)
        account_id = body.get("account_id")
        d = self.app.pause(org_id, account_id=account_id)
        d.addCallback(lambda _: {"status": "paused", "org_id": org_id,
                                 "account_id": account_id})
        return d
```
`ResumeResource` mirrors `PauseResource`. `ResyncResource`: `d = self.app.resync(_org_id_from(body))`. `DryRunResource`:

```python
    def _handle(self, request, body):
        org_id = _org_id_from(body)
        enabled = bool(body.get("enabled", False))
        self.app.set_dry_run(org_id, enabled)
        return {"status": "ok", "org_id": org_id, "dry_run": enabled}
```
`CloseAllResource`:

```python
    def _handle(self, request, body):
        org_id = _org_id_from(body)
        account_id = body.get("account_id")
        return self.app.close_all(
            org_id, int(account_id) if account_id is not None else None)
```
The three drift resources resolve their reconciler through the app, e.g.:

```python
class DriftDismissResource(_JsonResource):
    def _handle(self, request, body):
        org_id = _org_id_from(body)
        item_id = body.get("id")
        if item_id is None:
            raise ValueError("id required")
        d = self.app.reconciler_for(org_id).dismiss(item_id)
        d.addCallback(lambda _: {"status": "dismissed", "id": item_id})
        return d
```
(`close-orphan` and `adopt` get the same two-line prelude.) `DiscoverResource` is unchanged — Task 13's `discover()` still returns the discovered accounts list, and conflicts surface through the `discover_conflict` error events.

- [ ] **Step 3: Full copier suite green gate, commit**

```bash
cd copier && .venv/bin/pytest tests --timeout=60 -q
```
Expected: 0 failures (~9 min).

```bash
git add copier/src/copier/engine/control.py copier/tests
git commit -m "feat(copier): control endpoints take org_id on every scoped command"
```

---

### Task 15: Dashboard foundations — API helpers, types, auth pages, routes

**Files:**
- Modify: `dashboard/src/lib/api.ts`, `dashboard/src/lib/types.ts`
- Create: `dashboard/src/lib/roles.ts`, `dashboard/src/lib/org.tsx`
- Modify: `dashboard/src/pages/Login.tsx`
- Create: `dashboard/src/pages/Register.tsx`, `dashboard/src/pages/Welcome.tsx`, `dashboard/src/pages/Join.tsx`
- Modify: `dashboard/src/App.tsx`
- Create/Modify tests: `dashboard/src/lib/roles.test.ts`, `dashboard/src/pages/Login.test.tsx` (update), `Register.test.tsx`, `Join.test.tsx`, `Welcome.test.tsx`, `dashboard/src/App.test.tsx` (update)

**Interfaces:**
- Produces (Tasks 16–17 build on these):

```typescript
// lib/api.ts
export function orgApi<T>(orgId: number, tail: string, init?: RequestInit): Promise<T>
export function eventsSocket(orgId: number): WebSocket   // /api/ws?org_id=N

// lib/roles.ts
export type Role = 'viewer' | 'trader' | 'admin' | 'owner'
export type Action = 'trade' | 'control' | 'manage_members'
export function can(role: Role | null | undefined, action: Action): boolean
// trade: trader+; control (kill switches, settings, account admin, invites,
// drift, connect): admin+; manage_members (roles, removal, rename, delete): owner

// lib/types.ts additions
export interface OrgSummary { id: number; name: string; role: Role }
export interface Me { user: { id: number; email: string; display_name: string }; orgs: OrgSummary[] }
export interface Member { user_id: number; email: string; display_name: string; role: Role; joined_at: string }
export interface Invite { id: number; role: Role; created_at: string; expires_at: string; consumed: boolean }
// Settings loses `shards`: { copying_enabled: boolean; dry_run: boolean }

// lib/org.tsx
export function OrgProvider(props: { children: React.ReactNode }): JSX.Element
export function useOrg(): { orgId: number; role: Role; org: OrgSummary; me: Me; refreshMe: () => Promise<void> }
```
- Route map produced in `App.tsx`:
  - Public: `/login`, `/register`, `/join/:token` (redirects to `/login?next=/join/:token` when unauthenticated)
  - Authenticated, org-less: `/welcome` (create org or paste invite)
  - Org-scoped: `/org/:orgId` (Overview), `/org/:orgId/accounts|positions|trade|history|logs|members`
  - `/` redirects: last-used org from `localStorage["copydesk.lastOrg"]` if still a membership, else first org, else `/welcome`.

- [ ] **Step 1: Write the failing tests**

`dashboard/src/lib/roles.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { can } from './roles'

describe('can', () => {
  it('gates trade at trader', () => {
    expect(can('viewer', 'trade')).toBe(false)
    expect(can('trader', 'trade')).toBe(true)
    expect(can('admin', 'trade')).toBe(true)
    expect(can('owner', 'trade')).toBe(true)
  })
  it('gates control at admin', () => {
    expect(can('trader', 'control')).toBe(false)
    expect(can('admin', 'control')).toBe(true)
  })
  it('gates member management at owner', () => {
    expect(can('admin', 'manage_members')).toBe(false)
    expect(can('owner', 'manage_members')).toBe(true)
  })
  it('denies for missing role', () => {
    expect(can(null, 'trade')).toBe(false)
    expect(can(undefined, 'control')).toBe(false)
  })
})
```

Update `Login.test.tsx`: the form posts `{email, password}` to `/api/login` and navigates to `/` on success (follow the existing test's fetch-mock pattern in that file). New `Register.test.tsx`: renders email/password/display-name inputs, posts to `/api/register`, navigates to `/welcome`; shows the server's `detail` on 409. New `Join.test.tsx`: with a mocked authenticated `/api/me`, posts the route token to `/api/orgs/join` and navigates to `/org/<org_id>`; on 410 shows "invalid or expired". New `Welcome.test.tsx`: create-org form posts to `/api/orgs` and navigates to `/org/<id>`; paste-invite form navigates to `/join/<token>` (accepts either the bare token or a full URL containing `/join/<token>`, extracting the token). Update `App.test.tsx` for the new route map (`/` redirect behavior with zero orgs → `/welcome`).

- [ ] **Step 2: Run to verify failures**

```bash
cd dashboard && npm test
```
Expected: FAIL — missing modules/exports, old Login shape.

- [ ] **Step 3: Implement**

`lib/api.ts` — append:

```typescript
export function orgApi<T>(orgId: number, tail: string, init?: RequestInit): Promise<T> {
  return api<T>(`/api/orgs/${orgId}/${tail}`, init)
}
```
and change `eventsSocket`:

```typescript
export function eventsSocket(orgId: number): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}/api/ws?org_id=${orgId}`
  return new WebSocket(url)
}
```

`lib/roles.ts`:

```typescript
export type Role = 'viewer' | 'trader' | 'admin' | 'owner'
export type Action = 'trade' | 'control' | 'manage_members'

const RANK: Record<Role, number> = { viewer: 0, trader: 1, admin: 2, owner: 3 }
const THRESHOLD: Record<Action, number> = {
  trade: RANK.trader,
  control: RANK.admin,
  manage_members: RANK.owner,
}

/** UI-side mirror of the server's role matrix — hides controls the server
 * would reject. The server enforces regardless. */
export function can(role: Role | null | undefined, action: Action): boolean {
  if (!role || !(role in RANK)) return false
  return RANK[role] >= THRESHOLD[action]
}
```

`lib/org.tsx`:

```tsx
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { api } from './api'
import type { Me, OrgSummary } from './types'

export const LAST_ORG_KEY = 'copydesk.lastOrg'

interface OrgContextValue {
  orgId: number
  role: OrgSummary['role']
  org: OrgSummary
  me: Me
  refreshMe: () => Promise<void>
}

const OrgContext = createContext<OrgContextValue | null>(null)

export function useOrg(): OrgContextValue {
  const value = useContext(OrgContext)
  if (!value) throw new Error('useOrg used outside OrgProvider')
  return value
}

/** Resolves :orgId against /api/me. Not logged in → api() redirects to
 * /login; logged in but not a member of :orgId → /welcome. */
export function OrgProvider({ children }: { children: React.ReactNode }) {
  const { orgId: rawOrgId } = useParams()
  const orgId = Number(rawOrgId)
  const [me, setMe] = useState<Me | null>(null)
  const [failed, setFailed] = useState(false)

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>('/api/me'))
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    refreshMe()
  }, [refreshMe])

  if (failed) return <Navigate to="/login" replace />
  if (!me) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }
  const org = me.orgs.find((o) => o.id === orgId)
  if (!org) return <Navigate to="/welcome" replace />
  localStorage.setItem(LAST_ORG_KEY, String(org.id))
  return (
    <OrgContext.Provider
      value={{ orgId: org.id, role: org.role, org, me, refreshMe }}
    >
      {children}
    </OrgContext.Provider>
  )
}
```

`pages/Login.tsx` — keep the visual shell, replace the single password field with email + password state posting `{ email, password }`, error text from the response body when available, and a footer link: `New here? <Link to="/register">Create an account</Link>`. On success `navigate('/')`.

`pages/Register.tsx` — same visual shell as Login; three fields (display name, email, password with `minLength={10}`); posts to `/api/register`; on success `navigate('/welcome')`; on failure shows the server `detail` (parse `err.message`); footer link back to `/login`.

`pages/Welcome.tsx`:

```tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

export default function Welcome() {
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const createOrg = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const org = await api<{ id: number }>('/api/orgs', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      navigate(`/org/${org.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create organization')
    }
  }

  const useInvite = (e: React.FormEvent) => {
    e.preventDefault()
    const match = invite.match(/\/join\/([A-Za-z0-9_-]+)/)
    const token = match ? match[1] : invite.trim()
    if (token) navigate(`/join/${token}`)
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper px-4">
      <div className="max-w-md w-full space-y-10">
        <h2 className="text-center font-display text-3xl text-brand">Copy Desk</h2>
        {error && (
          <div className="rounded-md bg-loss-wash p-4">
            <p className="text-sm font-medium text-loss-deep">{error}</p>
          </div>
        )}
        <form onSubmit={createOrg} className="space-y-4">
          <h3 className="text-lg font-semibold text-ink">Create an organization</h3>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Organization name"
            aria-label="Organization name"
            className="w-full px-3 py-2 border border-line-strong rounded-md bg-card text-ink sm:text-sm"
          />
          <button
            type="submit"
            className="w-full py-2 px-4 text-sm font-semibold rounded-md text-white bg-brand hover:bg-brand-deep transition-colors"
          >
            Create organization
          </button>
        </form>
        <form onSubmit={useInvite} className="space-y-4">
          <h3 className="text-lg font-semibold text-ink">Or join with an invite</h3>
          <input
            value={invite}
            onChange={(e) => setInvite(e.target.value)}
            placeholder="Paste an invite link or code"
            aria-label="Invite link or code"
            className="w-full px-3 py-2 border border-line-strong rounded-md bg-card text-ink sm:text-sm"
          />
          <button
            type="submit"
            className="w-full py-2 px-4 text-sm font-semibold rounded-md border border-brand text-brand hover:bg-brand-wash transition-colors"
          >
            Join organization
          </button>
        </form>
      </div>
    </div>
  )
}
```

`pages/Join.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'

export default function Join() {
  const { token } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const join = async () => {
      try {
        const result = await api<{ org_id: number }>('/api/orgs/join', {
          method: 'POST',
          body: JSON.stringify({ token }),
        })
        if (!cancelled) navigate(`/org/${result.org_id}`, { replace: true })
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : ''
        setError(
          message.includes('410')
            ? 'This invite is invalid, expired, or already used.'
            : message.includes('409')
              ? 'You are already a member of this organization.'
              : 'Could not join the organization.')
      }
    }
    join()
    return () => { cancelled = true }
  }, [token, navigate])

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper px-4">
      <div className="max-w-md w-full text-center space-y-4">
        <h2 className="font-display text-3xl text-brand">Copy Desk</h2>
        {error ? (
          <p className="text-sm font-medium text-loss-deep">{error}</p>
        ) : (
          <p className="text-sm text-ink-soft">Joining organization…</p>
        )}
      </div>
    </div>
  )
}
```
(Note: `api()` hard-redirects to `/login` on 401, so an unauthenticated visitor lands at login; after logging in they can re-open the invite link. That is acceptable for v1 — no `?next=` plumbing.)

`App.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { api } from './lib/api'
import { LAST_ORG_KEY, OrgProvider } from './lib/org'
import type { Me } from './lib/types'
import Layout from './components/Layout'
import Login from './pages/Login'
import Register from './pages/Register'
import Welcome from './pages/Welcome'
import Join from './pages/Join'
import Members from './pages/Members'
import Overview from './pages/Overview'
import Accounts from './pages/Accounts'
import Positions from './pages/Positions'
import Trade from './pages/Trade'
import History from './pages/History'
import Logs from './pages/Logs'

/** `/` → the last-used org, else the first org, else /welcome. */
function RootRedirect() {
  const [target, setTarget] = useState<string | null>(null)

  useEffect(() => {
    const resolve = async () => {
      try {
        const me = await api<Me>('/api/me')
        const last = Number(localStorage.getItem(LAST_ORG_KEY))
        const org = me.orgs.find((o) => o.id === last) ?? me.orgs[0]
        setTarget(org ? `/org/${org.id}` : '/welcome')
      } catch {
        setTarget('/login')
      }
    }
    resolve()
  }, [])

  if (!target) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }
  return <Navigate to={target} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/join/:token" element={<Join />} />
        <Route path="/welcome" element={<Welcome />} />
        <Route
          path="/org/:orgId"
          element={
            <OrgProvider>
              <Layout />
            </OrgProvider>
          }
        >
          <Route index element={<Overview />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="positions" element={<Positions />} />
          <Route path="trade" element={<Trade />} />
          <Route path="history" element={<History />} />
          <Route path="logs" element={<Logs />} />
          <Route path="members" element={<Members />} />
        </Route>
        <Route path="/" element={<RootRedirect />} />
      </Routes>
    </BrowserRouter>
  )
}
```
`Members` does not exist until Task 16 — create a placeholder now so `tsc` passes: `dashboard/src/pages/Members.tsx` exporting `export default function Members() { return null }` (replaced next task).

`lib/types.ts`: apply the interface additions from this task's header and change `Settings` to `{ copying_enabled: boolean; dry_run: boolean }`.

- [ ] **Step 4: Run dashboard tests**

```bash
cd dashboard && npm test
```
Expected: the new tests pass; page tests for Overview/Accounts/etc. still pass (they haven't changed yet — their pages still call old endpoints, which their mocks still serve; conversion is Task 17). `tsc` failures from the `Settings` type change surface in pages that read `settings.shards` — fix those reads now by deleting the display of `shards` wherever `tsc` points (expected: none or one spot).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src
git commit -m "feat(dashboard): auth pages, org context, role helpers, org-scoped routing"
```

---

### Task 16: Layout — org switcher, role gating, Members page

**Files:**
- Modify: `dashboard/src/components/Layout.tsx`, `dashboard/src/components/Layout.test.tsx`
- Create: `dashboard/src/pages/Members.tsx` (replace the placeholder), `dashboard/src/pages/Members.test.tsx`

**Interfaces:**
- Consumes: `useOrg()`, `can()`, `orgApi()` (Task 15); members/invites endpoints (Task 3).
- Produces: nav + DeskStrip working inside `/org/:orgId`; Members page at `/org/:orgId/members`.

- [ ] **Step 1: Write/adjust the failing tests**

`Layout.test.tsx` — wrap renders in a mocked org context (mock `../lib/org` with `useOrg` returning a configurable value; the file's existing fetch mocks stay, with paths updated to `/api/orgs/1/...`). Add:

```tsx
it('hides the close-all kill switch below admin', ...)   // role: 'trader' → no "Close all positions" button
it('shows the kill switch for admin', ...)
it('hides the Trade nav item for viewers', ...)
it('org switcher lists my orgs and navigates on change', ...)  // two orgs in me.orgs; selecting the other calls navigate('/org/2')
it('hides Members nav below owner? no — members list is viewer-visible', ...)  // Members link always present
```

`Members.test.tsx` (mock `useOrg` similarly):

```tsx
it('lists members with roles', ...)
it('owner can change a role via the role select', ...)       // PATCH /api/orgs/1/members/2
it('non-owner sees read-only roles and no invite form', ...) // viewer: no selects, no invite section
it('admin can create an invite and sees the link once', ...) // POST invites → shows /join/<token> with a Copy button
it('shows the last-owner error from the server', ...)        // PATCH answers 409 → error banner
it('owner can rename the org', ...)                          // PATCH /api/orgs/1 {name}
it('owner sees delete-org with type-to-confirm; others do not', ...) // DELETE /api/orgs/1 after ConfirmDialog
```

- [ ] **Step 2: Run to verify failures, then implement**

`Layout.tsx` conversion:

1. Imports add `useOrg` from `../lib/org`, `can` from `../lib/roles`, `orgApi` from `../lib/api`, and `useParams`-free — org comes from context.
2. `navItems` becomes a function of the org and role:

```tsx
const navItems = (orgId: number, role: Role) => [
  { path: `/org/${orgId}`, label: 'Overview' },
  { path: `/org/${orgId}/accounts`, label: 'Accounts' },
  { path: `/org/${orgId}/positions`, label: 'Positions' },
  ...(can(role, 'trade') ? [{ path: `/org/${orgId}/trade`, label: 'Trade' }] : []),
  { path: `/org/${orgId}/history`, label: 'History' },
  { path: `/org/${orgId}/logs`, label: 'Logs' },
  { path: `/org/${orgId}/members`, label: 'Members' },
]
```
3. `DeskStrip` uses `const { orgId, role } = useOrg()` and fetches `orgApi<Settings>(orgId, 'settings')`, `orgApi<Account[]>(orgId, 'accounts')`, `orgApi<ApiState>(orgId, 'state')`; `handleCloseAll` posts `orgApi(orgId, 'control/close-all', …)`; the whole kill-switch button + ConfirmDialog render only when `can(role, 'control')`. The confirm copy changes to "every enabled account in this organization".
4. `useLiveRefresh(refresh, orgId)` (signature extended in Task 17 — for this task pass `orgId` and update the hook now: `useLiveRefresh(refetch: () => void, orgId: number)`, `eventsSocket(orgId)` inside, `orgId` in the effect deps).
5. Sidebar header gains the org switcher under the "Copy Desk" title:

```tsx
const { orgId, role, me } = useOrg()
const navigate = useNavigate()
...
<select
  aria-label="Organization"
  value={orgId}
  onChange={(e) => navigate(`/org/${e.target.value}`)}
  className="mt-2 w-full text-sm border border-line-strong rounded bg-card text-ink px-2 py-1"
>
  {me.orgs.map((o) => (
    <option key={o.id} value={o.id}>{o.name}</option>
  ))}
</select>
```
6. The static "FP Markets · cTrader" subtitle line stays below the switcher.

`Members.tsx` — full page:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { can, type Role } from '../lib/roles'
import { useOrg } from '../lib/org'
import type { Invite, Member } from '../lib/types'

const ASSIGNABLE: Role[] = ['viewer', 'trader', 'admin', 'owner']
const INVITABLE: Role[] = ['viewer', 'trader', 'admin']

export default function Members() {
  const { orgId, role, me } = useOrg()
  const [members, setMembers] = useState<Member[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [inviteRole, setInviteRole] = useState<Role>('viewer')
  const [newInviteLink, setNewInviteLink] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setMembers(await orgApi<Member[]>(orgId, 'members'))
      if (can(role, 'control')) {
        setInvites(await orgApi<Invite[]>(orgId, 'invites'))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load members')
    }
  }, [orgId, role])

  useEffect(() => { refresh() }, [refresh])

  const changeRole = async (userId: number, newRole: string) => {
    setError(null)
    try {
      await orgApi(orgId, `members/${userId}`, {
        method: 'PATCH', body: JSON.stringify({ role: newRole }),
      })
      await refresh()
    } catch (err) {
      setError(err instanceof Error && err.message.includes('409')
        ? 'An organization must keep at least one owner.'
        : 'Could not change role')
    }
  }

  const removeMember = async (userId: number) => {
    setError(null)
    try {
      await orgApi(orgId, `members/${userId}`, { method: 'DELETE' })
      await refresh()
    } catch (err) {
      setError(err instanceof Error && err.message.includes('409')
        ? 'An organization must keep at least one owner.'
        : 'Could not remove member')
    }
  }

  const createInvite = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      const invite = await orgApi<{ token: string }>(orgId, 'invites', {
        method: 'POST', body: JSON.stringify({ role: inviteRole }),
      })
      setNewInviteLink(`${window.location.origin}/join/${invite.token}`)
      await refresh()
    } catch {
      setError('Could not create invite')
    }
  }

  return (
    <div className="space-y-8">
      <h2 className="text-xl font-semibold text-ink">Members</h2>
      {error && (
        <div className="rounded-md bg-loss-wash p-3 text-sm text-loss-deep">{error}</div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left desk-label border-b border-line">
            <th className="py-2">Name</th><th>Email</th><th>Role</th><th></th>
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id} className="border-b border-line">
              <td className="py-2 text-ink">{m.display_name}</td>
              <td className="text-ink-soft">{m.email}</td>
              <td>
                {can(role, 'manage_members') ? (
                  <select
                    aria-label={`Role for ${m.email}`}
                    value={m.role}
                    onChange={(e) => changeRole(m.user_id, e.target.value)}
                    className="border border-line-strong rounded bg-card px-2 py-1"
                  >
                    {ASSIGNABLE.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                ) : (
                  <span className="text-ink">{m.role}</span>
                )}
              </td>
              <td className="text-right">
                {(can(role, 'manage_members') || m.user_id === me.user.id) && (
                  <button
                    onClick={() => removeMember(m.user_id)}
                    className="text-xs text-loss hover:underline"
                  >
                    {m.user_id === me.user.id ? 'Leave' : 'Remove'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {can(role, 'control') && (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold text-ink">Invites</h3>
          <form onSubmit={createInvite} className="flex items-center gap-3">
            <select
              aria-label="Invite role"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as Role)}
              className="border border-line-strong rounded bg-card px-2 py-1 text-sm"
            >
              {INVITABLE.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <button
              type="submit"
              className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-white hover:bg-brand-deep"
            >
              Create invite link
            </button>
          </form>
          {newInviteLink && (
            <div className="flex items-center gap-2 bg-brand-wash rounded p-3 text-sm">
              <code className="text-ink break-all">{newInviteLink}</code>
              <button
                onClick={() => navigator.clipboard.writeText(newInviteLink)}
                className="text-xs font-medium text-brand hover:underline shrink-0"
              >
                Copy
              </button>
              <span className="desk-label shrink-0">shown once — copy it now</span>
            </div>
          )}
          <ul className="text-sm text-ink-soft space-y-1">
            {invites.map((inv) => (
              <li key={inv.id} className="flex items-center gap-3">
                <span>{inv.role}</span>
                <span>{inv.consumed ? 'used' : `expires ${new Date(inv.expires_at).toLocaleDateString()}`}</span>
                {!inv.consumed && (
                  <button
                    onClick={async () => {
                      await orgApi(orgId, `invites/${inv.id}`, { method: 'DELETE' })
                      await refresh()
                    }}
                    className="text-xs text-loss hover:underline"
                  >
                    Revoke
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
```

Below the invites section in `Members.tsx`, add the owner-only organization controls (spec §4: rename owner-only; delete owner-only behind a typed confirm). Uses the existing `ConfirmDialog` component and the `api` helper directly (org create/delete are not org-prefixed for the redirect case):

```tsx
      {can(role, 'manage_members') && (
        <div className="space-y-4 border-t border-line pt-6">
          <h3 className="text-lg font-semibold text-ink">Organization</h3>
          <form
            onSubmit={async (e) => {
              e.preventDefault()
              await api(`/api/orgs/${orgId}`, { method: 'PATCH', body: JSON.stringify({ name: orgName }) })
              await refreshMe()
            }}
            className="flex items-center gap-3"
          >
            <input
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              aria-label="Organization name"
              className="border border-line-strong rounded bg-card px-2 py-1 text-sm"
            />
            <button type="submit" className="px-3 py-1.5 text-xs font-semibold rounded border border-brand text-brand hover:bg-brand-wash">
              Rename
            </button>
          </form>
          <button
            onClick={() => setDeleteOpen(true)}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-white transition-colors"
          >
            Delete organization
          </button>
          <ConfirmDialog
            open={deleteOpen}
            title="Delete this organization"
            confirmLabel="Delete organization"
            danger
            typeToConfirm="DELETE"
            busy={deleteBusy}
            onConfirm={async () => {
              setDeleteBusy(true)
              try {
                await api(`/api/orgs/${orgId}`, { method: 'DELETE' })
                navigate('/welcome')
              } finally {
                setDeleteBusy(false)
                setDeleteOpen(false)
              }
            }}
            onCancel={() => setDeleteOpen(false)}
          >
            <p>
              This removes the organization, its members, connected cTrader
              grants, and its copy history. Open positions at the broker are
              NOT closed — flatten first if you mean to exit the market. It
              cannot be undone.
            </p>
          </ConfirmDialog>
        </div>
      )}
```
Wire the supporting state at the top of the component: `const [orgName, setOrgName] = useState(org.name)`, `const [deleteOpen, setDeleteOpen] = useState(false)`, `const [deleteBusy, setDeleteBusy] = useState(false)`, `const navigate = useNavigate()`, and pull `org` and `refreshMe` from `useOrg()`. Rename/delete target the bare `/api/orgs/${orgId}` path (no tail), so they use `api` — import it alongside `orgApi` — and add the `ConfirmDialog` and `useNavigate` imports.

- [ ] **Step 3: Run tests, commit**

```bash
cd dashboard && npm test
git add dashboard/src
git commit -m "feat(dashboard): org switcher, role-gated layout, members & invites page"
```

---

### Task 17: Convert the six pages to org-scoped endpoints

**Files:**
- Modify: `dashboard/src/pages/Overview.tsx`, `Accounts.tsx`, `Positions.tsx`, `Trade.tsx`, `History.tsx`, `Logs.tsx`, `dashboard/src/components/KillSwitch.tsx`, `dashboard/src/hooks/useLiveRefresh.ts` (if not already done in Task 16)
- Modify: their `.test.tsx` files

**Interfaces:**
- Consumes: `useOrg()`, `orgApi()`, `can()`; no new interfaces produced.

- [ ] **Step 1: Convert page by page (test first, page second, per page)**

The identical mechanical recipe for each page:

1. In the page's test file, wrap the component in a mocked org context (the same `vi.mock('../lib/org', …)` helper used in Task 16 — extract it to `dashboard/src/test/orgMock.tsx` if it does not exist yet, exporting `mockUseOrg(role: Role, orgId = 1)`), and update every mocked fetch path from `/api/<tail>` to `/api/orgs/1/<tail>`. Run the file: RED.
2. In the page: add `const { orgId, role } = useOrg()`; replace every `api<T>('/api/<tail>', …)` with `orgApi<T>(orgId, '<tail>', …)`; add `orgId` to the `useCallback`/`useEffect` dependency arrays that wrap those calls. Run the file: GREEN.

Page-specific role gating to add while converting (with a test each):

- **Accounts.tsx**: role/multiplier/enable/nickname editors and the disconnect button render only when `can(role, 'control')`; viewers/traders get read-only rows. The "Connect a cTrader ID" link (`/api/oauth/connect`) becomes `/api/orgs/${orgId}/oauth/connect` and renders only for `can(role, 'control')`.
- **Positions.tsx**: per-position close buttons and drift remedy buttons only when `can(role, 'trade')` / `can(role, 'control')` respectively.
- **Trade.tsx**: the whole order ticket requires `can(role, 'trade')`; below that role render a short "Your role does not allow placing orders." notice (belt-and-braces — the nav link is already hidden).
- **KillSwitch.tsx** (used by Overview): renders only when `can(role, 'control')`; its PUT goes to `orgApi(orgId, 'settings', …)`.
- **Logs.tsx / History.tsx / Overview.tsx**: pure path conversion.
- `useLiveRefresh`: confirm the Task 16 signature `useLiveRefresh(refetch, orgId)` is applied and every caller passes `orgId`; update `useLiveRefresh.test.tsx` for the query-parameterized socket URL.

- [ ] **Step 2: Full dashboard suite**

```bash
cd dashboard && npm test
```
Expected: 0 failures, `tsc` clean.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src
git commit -m "feat(dashboard): org-scope all pages; role-gate trading and admin controls"
```

---

### Task 18: E2E, compose/env, docs, final verification

**Files:**
- Modify: `e2e/test_full_stack.py`
- Create: `e2e/test_multi_org.py`
- Modify: `docker-compose.yml` (api service env), `.env.example`, `README.md`

**Interfaces:** consumes everything; produces the final proof.

- [ ] **Step 1: Compose + env + docs**

- `docker-compose.yml`: in the `api` service environment, replace `ADMIN_BOOTSTRAP_PASSWORD` with `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` (both pass-through `${…}` refs, may be empty).
- `.env.example`: replace the `ADMIN_BOOTSTRAP_PASSWORD=` line with:

```
# Optional: claim a migrated legacy ("Default") org on first boot.
# Leave both empty on a fresh install — register through the UI instead.
BOOTSTRAP_ADMIN_EMAIL=
BOOTSTRAP_ADMIN_PASSWORD=
```
- `README.md`: update §"What this is" (multi-user/multi-org, roles), the setup section (register the first user in the UI; orgs and invite links), and the VPS/deployment warning — replace the "single admin password" sentence with: registration is open on a reachable instance, registering grants no access to any existing org, and the instance should still sit behind TLS. Document the role matrix in a short table matching the spec.

- [ ] **Step 2: Update `e2e/test_full_stack.py`**

Its seed SQL gains an org: insert an org row first, thread `org_id` into the `ctid_connections` and `accounts` INSERTs. Its API login flow becomes: `POST /api/register` (email `e2e@example.com`, password `a-solid-password`), then SQL-insert the membership `(org_id, user_id, 'owner')` (registration can't know the seeded org), then all `/api/...` paths become `/api/orgs/{org_id}/...`. Assertions otherwise unchanged — same three accounts, same fills, same mapping expectations.

- [ ] **Step 3: Write `e2e/test_multi_org.py`**

Follow `test_full_stack.py`'s structure (same fixtures/scenario driver). Outline with the load-bearing assertions spelled out:

```python
"""Two orgs on one stack: replication stays inside each org's book, and the
API refuses cross-org access.

Seeds (direct SQL, mirroring test_full_stack.py's helpers):
  org A: master 100, slaves 101 (x1.0), 102 (x0.5)  — the classic trio
  org B: master 200, slave 201 (x1.0)
  users: a@example.com owner of A; b@example.com owner of B (registered via
  /api/register, membership rows inserted via SQL)

Flow:
  1. POST /reload to the copier (via compose-internal helper, as the existing
     e2e does) so it sees both orgs.
  2. Drive a BUY fill on master 100 and a SELL fill on master 200 through the
     fake broker's scenario API.
  3. Poll mappings until settled, then assert:
     - every mapping row for master_position of 100's fill has org_id = org A
       and slave_account_id in {101, 102}; NOTHING for 201.
     - the 200 fill produced exactly one mapping, org B, slave 201; NOTHING
       for 101/102.
  4. As a@example.com: GET /api/orgs/{orgA}/state → master_positions show
     100's position; GET /api/orgs/{orgB}/state → 404.
     GET /api/orgs/{orgB}/accounts → 404; POST /api/orgs/{orgA}/orders with
     account_id=201 → 404.
  5. As b@example.com: POST /api/orgs/{orgB}/control/close-all {} → 200;
     then assert org A's settings still have copying_enabled=true and org
     A's fake-broker positions are still open, while org B's book is flat
     and orgs row B has copying_enabled=false.
"""
```
Write it fully, reusing the existing file's HTTP/session/SQL helper functions (import or copy them — match its style).

- [ ] **Step 4: Run the e2e (nothing else running on this machine)**

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build
cd api && .venv/bin/pytest ../e2e/test_full_stack.py ../e2e/test_multi_org.py -v
docker compose -f docker-compose.yml -f docker-compose.test.yml down -v
```
Expected: both e2e tests pass.

- [ ] **Step 5: Full-repo verification**

Sequentially (never concurrently):

```bash
cd api && .venv/bin/pytest tests -q
cd copier && .venv/bin/pytest tests --timeout=60 -q
cd dashboard && npm test
```
Expected: 0 failures each.

- [ ] **Step 6: Commit**

```bash
git add e2e docker-compose.yml .env.example README.md
git commit -m "feat: two-org e2e, bootstrap env vars, multi-org docs"
```

---

## Execution notes

- Tasks 1→9 (api), 10→14 (copier), 15→17 (dashboard) are strictly ordered within their phase; the copier phase (10–14) and dashboard phase (15–17) both depend on Task 1 but not on each other — an executor may interleave them after Task 9 if useful. Task 18 requires everything.
- Between Task 1 and the end of Task 9, the api suite is intentionally partially red (old admin-auth tests until converted); between Tasks 10 and 14 the copier suite is partially red. Each task names its own green gate — hold to those, and to full-suite green at Tasks 9, 14, 17, 18.
- The spec is the arbiter for any threshold/semantics question; the role-matrix test is its executable form.
