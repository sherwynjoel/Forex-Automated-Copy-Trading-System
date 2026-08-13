# Forex Copy-Trading System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-08-13-copy-trading-design.md` (approved; do not deviate from it)

**Goal:** Replicate every trade action from one master cTrader account to ~49 slave accounts at FP Markets in real time via the cTrader Open API, with a React dashboard for monitoring, control, onboarding, and audit.

**Architecture:** Three Docker Compose services: `copier` (Python + Spotware `ctrader-open-api` SDK on Twisted — owns all broker connections, replication decisions, fan-out, reconciliation), `api` (FastAPI — dashboard static serving, REST, WebSocket live feed, OAuth onboarding, admin auth), `postgres` (single source of truth + LISTEN/NOTIFY event bus). The replication decision core is a pure, exhaustively unit-tested module. A fake cTrader protobuf server provides integration coverage without touching real brokers.

**Tech Stack:** Python 3.12, `ctrader-open-api==0.9.2` (Twisted), FastAPI + uvicorn, psycopg 3, argon2-cffi, itsdangerous, cryptography (Fernet), React 18 + Vite + Tailwind CSS v4 + TypeScript, Postgres 16, Docker Compose.

## Global Constraints

Copied from the spec — every task's requirements implicitly include these:

- **TDD is mandatory.** Use superpowers:test-driven-development for every task: write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- Broker: **FP Markets** (confirmed). Endpoints: `live.ctraderapi.com:5035`, `demo.ctraderapi.com:5035` (TCP/TLS protobuf). Demo and live never share a connection; one connection carries many authorized accounts.
- Auth chain per connection: `ProtoOAApplicationAuthReq` once, then `ProtoOAAccountAuthReq` per trading account. OAuth `trading` scope per cTrader ID; access token ~30-day; **refresh token rotates on every refresh and the new one MUST be persisted in the same transaction**.
- Volume units: **protocol volume = lots × `ProtoOASymbol.lotSize`** (lotSize is "in cents": 1.00 lot EURUSD, lotSize 10,000,000 → protocol volume 10,000,000). Never confuse lots, units, or centilots.
- Sizing: **exact mirror** — slave lots = master lots × per-slave multiplier (default 1.0). Volumes rounded to the symbol's `stepVolume`. Below-minimum / margin-failing orders are rejected by the broker and surfaced as alerts — **never silently resized**.
- Trade requests get **no synchronous response**; outcomes arrive as `ProtoOAExecutionEvent` (ORDER_ACCEPTED, ORDER_FILLED, ORDER_REPLACED, ORDER_CANCELLED, ORDER_EXPIRED, ORDER_REJECTED, ORDER_PARTIAL_FILL, …).
- Heartbeat: `ProtoHeartbeatEvent` at least every 10 s or the server disconnects (we send every 8 s).
- Rate limit: token bucket at **40 req/s per connection** (server cap 50). Shard count is a config knob (default 1).
- Slave order labels: `copy:m<masterPositionId>` / `copy:o<masterOrderId>`; label ≤ 100 chars, `clientOrderId` ≤ 50 chars.
- Failures: retry ×3 with backoff 1s/2s/4s on transient errors, then mark slave **degraded** (keeps receiving future events). Slave actions are independent — one failure never blocks other slaves.
- Master rejections/expiries replicate as **no-ops** (logged only). Slave events never trigger replication (loop-proof by construction).
- Master trades made while copier is down are **missed, not replayed**; they appear as drift. Drift is reported, never auto-traded (one-click remedies: close orphan, adopt, dismiss).
- Token refresh proactively when < 25 days of validity remain. `ProtoOAAccountsTokenInvalidatedEvent` → re-auth flow; refresh failure → prominent dashboard alert.
- Tokens encrypted at rest (Fernet, key from env). Secrets live in `.env` (git-ignored); `.env.example` committed. copier's control endpoint bound to the Docker internal network only.
- Dashboard: single admin, argon2 password hash, signed HTTP-only session cookie, CSRF protection, login rate limiting. Exactly one master role enforced.
- Rollout: demo-first (dry-run → 2–3 demo slaves → scale demo → live).

## File Structure

```
trading-bot/
├── .env.example, .gitignore, README.md, docker-compose.yml, docker-compose.test.yml
├── db/                          # shared: schema + migration runner (own Docker build)
│   ├── Dockerfile, __init__.py, migrate.py
│   └── migrations/001_initial.sql
├── copier/                      # trading engine (Twisted)
│   ├── Dockerfile, pyproject.toml
│   ├── src/copier/
│   │   ├── __init__.py, main.py
│   │   ├── domain/              # PURE decision core: models.py, sizing.py, decision.py
│   │   ├── ctrader/             # client.py (SDK wrapper), tokens.py, symbols.py
│   │   ├── engine/              # normalize.py, dispatch.py, service.py, throttle.py,
│   │   │                        # reconcile.py, state.py, control.py
│   │   ├── db/repo.py           # persistence + pg_notify
│   │   └── testing/fake_server.py, fake_main.py, tls.py   # fake cTrader server
│   └── tests/ (unit/, integration/, conftest.py)
├── api/                         # FastAPI service
│   ├── Dockerfile, pyproject.toml
│   ├── src/api/
│   │   ├── __init__.py, main.py, config.py, db.py, auth.py, oauth.py, ws.py
│   │   └── routes/accounts.py, settings_control.py, events.py, state.py
│   └── tests/ (conftest.py, test_*.py)
├── dashboard/                   # React + Vite + Tailwind
│   ├── package.json, vite.config.ts, tsconfig.json, index.html
│   └── src/ (main.tsx, App.tsx, index.css, lib/api.ts, lib/types.ts,
│         components/Layout.tsx, pages/{Login,Overview,Accounts,Positions,Logs}.tsx)
└── e2e/test_full_stack.py       # full-compose end-to-end test
```

Test prerequisite for all DB-touching tasks: `docker compose up -d postgres` (Postgres on host port 5433). All Python tests run on the host in per-service venvs (`copier/.venv`, `api/.venv`).

---

## Phase 1: Foundations — repo scaffolding, Docker Compose, Postgres schema

Deliverable: `docker compose config` valid, all three service skeletons build, migrations create the full §6 schema with the `pg_notify` trigger, and a single-master DB constraint.

### Task 1: Repo scaffolding + Docker Compose

**Files:**
- Create: `.gitignore`, `.env.example`, `docker-compose.yml`
- Create: `db/Dockerfile`, `db/__init__.py`
- Create: `copier/pyproject.toml`, `copier/Dockerfile`, `copier/src/copier/__init__.py`, `copier/tests/unit/test_smoke.py`
- Create: `api/pyproject.toml`, `api/Dockerfile`, `api/src/api/__init__.py`, `api/tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing (greenfield).
- Produces: installable packages `copier` and `api`; compose services `postgres`, `migrate`, `copier`, `api`; env var contract in `.env.example` used by every later task.

- [ ] **Step 1: Write the failing smoke tests**

`copier/tests/unit/test_smoke.py`:
```python
def test_copier_package_importable():
    import copier
    assert copier.__version__ == "0.1.0"
```

`api/tests/test_smoke.py`:
```python
def test_api_package_importable():
    import api
    assert api.__version__ == "0.1.0"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd copier && python3.12 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/pytest tests/unit/test_smoke.py -v
cd ../api && python3.12 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/pytest tests/test_smoke.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'copier'` / `'api'`.

- [ ] **Step 3: Create packages, pyprojects, env files, Dockerfiles, compose**

`copier/src/copier/__init__.py` and `api/src/api/__init__.py`:
```python
__version__ = "0.1.0"
```

`copier/pyproject.toml`:
```toml
[project]
name = "copier"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "ctrader-open-api==0.9.2",
    "psycopg[binary]>=3.2",
    "cryptography>=42",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-twisted>=1.14", "pytest-timeout>=2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/copier"]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
```
(If pip fails resolving the SDK's protobuf pin on 3.12, add `"protobuf==3.20.3"` to dependencies — the SDK's generated code is protobuf-3.x based.)

`api/pyproject.toml`:
```toml
[project]
name = "api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "psycopg[binary]>=3.2",
    "argon2-cffi>=23",
    "itsdangerous>=2.2",
    "httpx>=0.27",
    "cryptography>=42",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-timeout>=2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/api"]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
```

`.gitignore`:
```
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
node_modules/
dashboard/dist/
```

`.env.example` (every variable the system uses; copy to `.env` and fill):
```
# Postgres (dev default password is fine locally; change on a VPS)
POSTGRES_PASSWORD=copytrader
POSTGRES_DSN=postgresql://copytrader:copytrader@postgres:5432/copytrader

# cTrader Open API app credentials — register at https://openapi.ctrader.com (see README)
CTRADER_CLIENT_ID=
CTRADER_CLIENT_SECRET=
CTRADER_REDIRECT_URI=http://localhost:8000/api/oauth/callback
CTRADER_AUTH_URL=https://openapi.ctrader.com/apps/auth
CTRADER_TOKEN_URL=https://openapi.ctrader.com/apps/token
CTRADER_DEMO_HOST=demo.ctraderapi.com
CTRADER_LIVE_HOST=live.ctraderapi.com
CTRADER_PORT=5035

# Secrets — generate FERNET_KEY with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=
SESSION_SECRET=
ADMIN_BOOTSTRAP_PASSWORD=

# Internal wiring
COPIER_CONTROL_URL=http://copier:8080
SHARDS=1
```

`db/Dockerfile`:
```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir "psycopg[binary]>=3.2"
WORKDIR /app
COPY db /app/db
CMD ["python", "-m", "db.migrate"]
```

`db/__init__.py`: empty file.

`copier/Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY copier/pyproject.toml ./
COPY copier/src ./src
RUN pip install --no-cache-dir .
CMD ["python", "-m", "copier.main"]
```

`api/Dockerfile` (dashboard build stage is added in Task 25):
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY api/pyproject.toml ./
COPY api/src ./src
RUN pip install --no-cache-dir .
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`docker-compose.yml`:
```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: copytrader
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-copytrader}
      POSTGRES_DB: copytrader
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U copytrader"]
      interval: 2s
      timeout: 2s
      retries: 15

  migrate:
    build: { context: ., dockerfile: db/Dockerfile }
    environment:
      POSTGRES_DSN: postgresql://copytrader:${POSTGRES_PASSWORD:-copytrader}@postgres:5432/copytrader
    depends_on:
      postgres: { condition: service_healthy }

  copier:
    build: { context: ., dockerfile: copier/Dockerfile }
    env_file: .env
    environment:
      POSTGRES_DSN: postgresql://copytrader:${POSTGRES_PASSWORD:-copytrader}@postgres:5432/copytrader
    depends_on:
      migrate: { condition: service_completed_successfully }
    # control port 8080 is intentionally NOT published — internal network only (spec §8)

  api:
    build: { context: ., dockerfile: api/Dockerfile }
    env_file: .env
    ports:
      - "8000:8000"
    environment:
      POSTGRES_DSN: postgresql://copytrader:${POSTGRES_PASSWORD:-copytrader}@postgres:5432/copytrader
      COPIER_CONTROL_URL: http://copier:8080
    depends_on:
      migrate: { condition: service_completed_successfully }

volumes:
  pgdata: {}
```

- [ ] **Step 4: Install packages editable and verify tests pass**

```bash
cd copier && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest tests/unit/test_smoke.py -v
cd ../api && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest tests/test_smoke.py -v
```
Expected: both PASS.

- [ ] **Step 5: Verify compose config and image builds**

```bash
cp .env.example .env   # dev defaults; FERNET_KEY etc. filled in later tasks
docker compose config -q && docker compose build migrate copier api
```
Expected: exit 0 for all builds.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example docker-compose.yml db/ copier/ api/
git commit -m "chore: scaffold copier/api/db services and Docker Compose"
```

### Task 2: Postgres schema, migration runner, pg_notify trigger

**Files:**
- Create: `db/migrate.py`, `db/migrations/001_initial.sql`
- Create: `copier/tests/conftest.py`
- Test: `copier/tests/unit/test_migrations.py`

**Interfaces:**
- Consumes: compose `postgres` service (host port 5433).
- Produces: `db.migrate.apply_migrations(dsn: str) -> list[str]` (returns applied filenames); full §6 schema (`ctid_connections`, `accounts`, `symbol_cache`, `mappings`, `events`, `settings`, `admin`); `pg_notify('events', <id>)` trigger; pytest fixtures `database` (session-scoped fresh DB, yields DSN string) and `db` (function-scoped, truncated tables, yields DSN string) reused by ALL later DB tests.

- [ ] **Step 1: Write the failing tests**

`copier/tests/conftest.py`:
```python
import os
import pathlib
import sys

import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # makes the top-level `db` package importable

ADMIN_DSN = os.environ.get(
    "TEST_POSTGRES_ADMIN_DSN",
    "postgresql://copytrader:copytrader@localhost:5433/copytrader",
)
TEST_DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql://copytrader:copytrader@localhost:5433/copytrader_test",
)


@pytest.fixture(scope="session")
def database():
    from db.migrate import apply_migrations

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS copytrader_test WITH (FORCE)")
        conn.execute("CREATE DATABASE copytrader_test")
    apply_migrations(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, mappings, symbol_cache, accounts, ctid_connections, admin "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET copying_enabled = true, dry_run = false, shards = 1")
    return database
```

`copier/tests/unit/test_migrations.py`:
```python
import psycopg
import pytest


def test_all_tables_exist(db):
    with psycopg.connect(db) as conn:
        for table in ["ctid_connections", "accounts", "symbol_cache", "mappings",
                      "events", "settings", "admin", "schema_migrations"]:
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row[0] is not None, f"missing table {table}"


def test_settings_has_single_seed_row(db):
    with psycopg.connect(db) as conn:
        row = conn.execute("SELECT copying_enabled, dry_run, shards FROM settings").fetchone()
        assert row == (True, False, 1)


def test_only_one_master_allowed(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at) "
                     "VALUES ('x', 'y', now(), now() + interval '30 days')")
        conn.execute("INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role) "
                     "VALUES (100, 1, 111, false, 'master')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role) "
                         "VALUES (101, 1, 112, false, 'master')")


def test_event_insert_emits_pg_notify(db):
    with psycopg.connect(db, autocommit=True) as listener:
        listener.execute("LISTEN events")
        with psycopg.connect(db, autocommit=True) as writer:
            writer.execute("INSERT INTO events (category, severity, payload) "
                           "VALUES ('control', 'info', '{\"msg\": \"hi\"}')")
        notification = next(listener.notifies(timeout=5))
        assert notification.channel == "events"
        assert notification.payload == "1"


def test_apply_migrations_is_idempotent(database):
    from db.migrate import apply_migrations
    assert apply_migrations(database) == []  # second run applies nothing
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose up -d postgres
cd copier && .venv/bin/pytest tests/unit/test_migrations.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'db.migrate'`.

- [ ] **Step 3: Write the migration runner and initial schema**

`db/migrate.py`:
```python
"""Apply SQL migrations in db/migrations, in filename order, exactly once each."""
import os
import pathlib

import psycopg

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def apply_migrations(dsn: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied


if __name__ == "__main__":
    names = apply_migrations(os.environ["POSTGRES_DSN"])
    print(f"applied: {names or 'nothing (up to date)'}")
```

`db/migrations/001_initial.sql`:
```sql
CREATE TABLE ctid_connections (
    id BIGSERIAL PRIMARY KEY,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    scope TEXT NOT NULL DEFAULT 'trading',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'invalid', 'refresh_failed'))
);

CREATE TABLE accounts (
    ctid_trader_account_id BIGINT PRIMARY KEY,
    ctid_connection_id BIGINT NOT NULL REFERENCES ctid_connections(id) ON DELETE CASCADE,
    trader_login BIGINT NOT NULL,
    is_live BOOLEAN NOT NULL,
    role TEXT NOT NULL DEFAULT 'ignored' CHECK (role IN ('master', 'slave', 'ignored')),
    enabled BOOLEAN NOT NULL DEFAULT true,
    multiplier NUMERIC(10, 4) NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'paused', 'degraded')),
    last_error TEXT
);
-- spec §7: exactly one master enforced
CREATE UNIQUE INDEX accounts_single_master ON accounts ((TRUE)) WHERE role = 'master';

CREATE TABLE symbol_cache (
    account_id BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    symbol_id BIGINT NOT NULL,
    digits INT NOT NULL,
    lot_size BIGINT NOT NULL,
    min_volume BIGINT NOT NULL,
    step_volume BIGINT NOT NULL,
    PRIMARY KEY (account_id, name)
);

CREATE TABLE mappings (
    id BIGSERIAL PRIMARY KEY,
    master_position_id BIGINT,
    master_order_id BIGINT,
    slave_account_id BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    slave_position_id BIGINT,
    slave_order_id BIGINT,
    slave_volume BIGINT,
    client_order_id TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'closed', 'failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (master_position_id IS NOT NULL OR master_order_id IS NOT NULL)
);
CREATE INDEX mappings_by_master_position ON mappings (master_position_id)
    WHERE master_position_id IS NOT NULL;
CREATE INDEX mappings_by_master_order ON mappings (master_order_id)
    WHERE master_order_id IS NOT NULL;

CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    account_id BIGINT,
    category TEXT NOT NULL
        CHECK (category IN ('master_event', 'slave_action', 'connection', 'auth', 'drift', 'control')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    latency_ms INT,
    payload JSONB NOT NULL
);
CREATE INDEX events_by_ts ON events (ts DESC);
CREATE INDEX events_by_account ON events (account_id, ts DESC);

CREATE FUNCTION notify_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('events', NEW.id::text);
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_notify AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION notify_event();

CREATE TABLE settings (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    copying_enabled BOOLEAN NOT NULL DEFAULT true,
    dry_run BOOLEAN NOT NULL DEFAULT false,
    shards INT NOT NULL DEFAULT 1
);
INSERT INTO settings DEFAULT VALUES;

CREATE TABLE admin (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    password_hash TEXT NOT NULL
);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd copier && .venv/bin/pytest tests/unit/test_migrations.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Verify the migrate container works end-to-end**

```bash
docker compose up --build migrate
```
Expected: prints `applied: ['001_initial.sql']`, exits 0. Run again → `applied: nothing (up to date)`.

- [ ] **Step 6: Commit**

```bash
git add db/ copier/tests/
git commit -m "feat: Postgres schema, migration runner, events pg_notify trigger"
```

---

## Phase 2: Pure replication decision core

The most safety-critical code in the system. **No I/O anywhere in `copier/src/copier/domain/`** — pure functions over dataclasses, exhaustively unit-tested, including order-of-magnitude volume cases (spec §5).

### Task 3: Sizing and volume conversion (`sizing.py`)

**Files:**
- Create: `copier/src/copier/domain/__init__.py` (empty), `copier/src/copier/domain/sizing.py`
- Test: `copier/tests/unit/test_sizing.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by decision core and reconcile):
  - `protocol_volume_to_lots(volume: int, lot_size: int) -> Decimal`
  - `lots_to_protocol_volume(lots: Decimal, lot_size: int) -> int`
  - `floor_to_step(volume: int, step_volume: int) -> int`
  - `mirror_volume(master_volume: int, master_lot_size: int, multiplier: Decimal, slave_lot_size: int, slave_step_volume: int) -> int`
  - `partial_close_volume(slave_volume: int, closed_volume: int, remaining_volume: int, step_volume: int) -> int`

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_sizing.py` (EURUSD: `lotSize=10_000_000`, `stepVolume=100_000` = 0.01 lot):
```python
from decimal import Decimal

from copier.domain.sizing import (floor_to_step, lots_to_protocol_volume, mirror_volume,
                                  partial_close_volume, protocol_volume_to_lots)

LOT = 10_000_000     # ProtoOASymbol.lotSize for EURUSD ("in cents")
STEP = 100_000       # 0.01 lot


def test_spec_example_one_lot_eurusd_is_ten_million():
    # spec §3: 1.00 lot EURUSD = protocol volume 10,000,000
    assert lots_to_protocol_volume(Decimal("1.00"), LOT) == 10_000_000
    assert protocol_volume_to_lots(10_000_000, LOT) == Decimal("1")


def test_hundredth_lot():
    assert lots_to_protocol_volume(Decimal("0.01"), LOT) == 100_000
    assert protocol_volume_to_lots(100_000, LOT) == Decimal("0.01")


def test_floor_to_step():
    assert floor_to_step(3_333_333, STEP) == 3_300_000
    assert floor_to_step(3_300_000, STEP) == 3_300_000
    assert floor_to_step(99_999, STEP) == 0


def test_mirror_default_multiplier_is_exact():
    assert mirror_volume(10_000_000, LOT, Decimal("1.0"), LOT, STEP) == 10_000_000


def test_mirror_half_multiplier():
    assert mirror_volume(10_000_000, LOT, Decimal("0.5"), LOT, STEP) == 5_000_000


def test_mirror_never_returns_lots_or_centilots():
    # order-of-magnitude guard (spec §5): 1 lot must be 10_000_000, never 1, 100, or 100_000
    v = mirror_volume(10_000_000, LOT, Decimal("1.0"), LOT, STEP)
    assert v not in (1, 100, 100_000)
    assert v == 10_000_000


def test_mirror_rounds_down_to_step():
    # 0.10 lot * 0.333 = 0.0333 lot -> floors to 0.03 lot
    assert mirror_volume(1_000_000, LOT, Decimal("0.333"), LOT, STEP) == 300_000


def test_mirror_below_one_step_rounds_to_zero():
    # caller must alert, never send 0 (Task 5)
    assert mirror_volume(100_000, LOT, Decimal("0.5"), LOT, STEP) == 0


def test_partial_close_half():
    assert partial_close_volume(10_000_000, 5_000_000, 5_000_000, STEP) == 5_000_000


def test_partial_close_uneven_fraction_floors_to_step():
    # master closes 1/3 -> slave closes floor(10M/3) floored to step
    assert partial_close_volume(10_000_000, 1_000_000, 2_000_000, STEP) == 3_300_000


def test_full_close_returns_entire_slave_volume():
    # remaining 0 = full close: return everything regardless of step rounding
    assert partial_close_volume(9_999_999, 3_000_000, 0, STEP) == 9_999_999


def test_partial_close_never_exceeds_slave_volume():
    assert partial_close_volume(200_000, 999_999_999, 1, STEP) <= 200_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd copier && .venv/bin/pytest tests/unit/test_sizing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copier.domain'`.

- [ ] **Step 3: Implement `sizing.py`**

```python
"""Lots <-> protocol-volume conversion. protocol volume = lots * ProtoOASymbol.lotSize."""
from decimal import ROUND_HALF_UP, Decimal


def protocol_volume_to_lots(volume: int, lot_size: int) -> Decimal:
    return (Decimal(volume) / Decimal(lot_size)).normalize()


def lots_to_protocol_volume(lots: Decimal, lot_size: int) -> int:
    return int((lots * lot_size).to_integral_value(rounding=ROUND_HALF_UP))


def floor_to_step(volume: int, step_volume: int) -> int:
    if step_volume <= 0:
        return volume
    return (volume // step_volume) * step_volume


def mirror_volume(master_volume: int, master_lot_size: int, multiplier: Decimal,
                  slave_lot_size: int, slave_step_volume: int) -> int:
    lots = protocol_volume_to_lots(master_volume, master_lot_size) * multiplier
    return floor_to_step(lots_to_protocol_volume(lots, slave_lot_size), slave_step_volume)


def partial_close_volume(slave_volume: int, closed_volume: int, remaining_volume: int,
                         step_volume: int) -> int:
    if remaining_volume == 0:
        return slave_volume
    total = closed_volume + remaining_volume
    exact = Decimal(slave_volume) * Decimal(closed_volume) / Decimal(total)
    return min(floor_to_step(int(exact), step_volume), slave_volume)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd copier && .venv/bin/pytest tests/unit/test_sizing.py -v`
Expected: 12 PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/domain/ copier/tests/unit/test_sizing.py
git commit -m "feat: pure lots<->protocol-volume sizing with step rounding"
```

### Task 4: Domain models (`models.py`)

**Files:**
- Create: `copier/src/copier/domain/models.py`
- Test: `copier/tests/unit/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces every type the rest of the system passes around (all frozen dataclasses; exact fields below are the contract for Tasks 5–20):
  - Enums `Side` (BUY/SELL), `PendingType` (LIMIT/STOP)
  - `SymbolInfo(symbol_id: int, name: str, digits: int, lot_size: int, min_volume: int, step_volume: int)`
  - `SlaveConfig(account_id: int, enabled: bool, multiplier: Decimal, symbols: Mapping[str, SymbolInfo])`
  - Master events: `MasterPositionOpened(position_id, symbol_name, side, volume, lot_size, stop_loss, take_profit)`, `MasterPositionClosed(position_id, symbol_name, closed_volume, remaining_volume)`, `MasterPositionSLTPAmended(position_id, stop_loss, take_profit)`, `MasterPendingPlaced(order_id, symbol_name, side, order_type, volume, lot_size, price, stop_loss, take_profit, expiry_ts_ms)`, `MasterPendingReplaced(order_id, symbol_name, lot_size, order_type, volume, price, stop_loss, take_profit)`, `MasterPendingCancelled(order_id)`, `MasterPendingFilled(order_id, position_id)`, `MasterRejected(reason)`; union alias `MasterEvent`
  - Mapping lookups: `PositionMappingEntry(slave_account_id, slave_position_id, slave_volume)`, `OrderMappingEntry(slave_account_id, slave_order_id)`, protocol `MappingState` with `position_entries(master_position_id) -> Sequence[PositionMappingEntry]` and `order_entries(master_order_id) -> Sequence[OrderMappingEntry]`
  - Slave intents: `OpenMarket(slave_account_id, master_position_id, symbol_id, side, volume, stop_loss, take_profit, label)`, `ClosePosition(slave_account_id, position_id, volume)`, `AmendPositionSLTP(slave_account_id, position_id, stop_loss, take_profit)`, `PlacePending(slave_account_id, master_order_id, symbol_id, side, order_type, volume, price, stop_loss, take_profit, expiry_ts_ms, label)`, `AmendPending(slave_account_id, order_id, order_type, volume, price, stop_loss, take_profit)`, `CancelPending(slave_account_id, order_id)`, `LinkPendingFill(slave_account_id, master_order_id, master_position_id)`, `Alert(slave_account_id: int | None, message: str)`; union alias `SlaveIntent`

Field types: ids are `int`, volumes are `int` protocol units, prices/SL/TP are `float | None`, `multiplier` is `Decimal`, `expiry_ts_ms` is `int | None`.

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_models.py`:
```python
from decimal import Decimal

from copier.domain import models as m


def test_master_events_are_frozen_and_hashable():
    e = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                               volume=10_000_000, lot_size=10_000_000,
                               stop_loss=1.09, take_profit=None)
    assert hash(e)  # frozen dataclass


def test_slave_config_holds_symbol_map():
    sym = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                       lot_size=10_000_000, min_volume=100_000, step_volume=100_000)
    cfg = m.SlaveConfig(account_id=101, enabled=True,
                        multiplier=Decimal("1.0"), symbols={"EURUSD": sym})
    assert cfg.symbols["EURUSD"].lot_size == 10_000_000


def test_intent_union_members_exist():
    for name in ["OpenMarket", "ClosePosition", "AmendPositionSLTP", "PlacePending",
                 "AmendPending", "CancelPending", "LinkPendingFill", "Alert"]:
        assert hasattr(m, name)


def test_mapping_state_is_a_protocol():
    class Fake:
        def position_entries(self, master_position_id):
            return [m.PositionMappingEntry(101, 555, 10_000_000)]
        def order_entries(self, master_order_id):
            return []
    state: m.MappingState = Fake()
    assert state.position_entries(11)[0].slave_position_id == 555
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd copier && .venv/bin/pytest tests/unit/test_models.py -v`
Expected: FAIL — `ImportError` / `AttributeError`.

- [ ] **Step 3: Implement `models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Mapping, Protocol, Sequence


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class PendingType(Enum):
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass(frozen=True)
class SymbolInfo:
    symbol_id: int
    name: str
    digits: int
    lot_size: int      # ProtoOASymbol.lotSize (protocol units per 1.00 lot)
    min_volume: int
    step_volume: int


@dataclass(frozen=True)
class SlaveConfig:
    account_id: int
    enabled: bool
    multiplier: Decimal
    symbols: Mapping[str, SymbolInfo]


# ---------- master events (normalized from ProtoOAExecutionEvent) ----------

@dataclass(frozen=True)
class MasterPositionOpened:
    position_id: int
    symbol_name: str
    side: Side
    volume: int          # protocol units filled on the master
    lot_size: int        # master's lotSize for the symbol
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class MasterPositionClosed:
    position_id: int
    symbol_name: str
    closed_volume: int
    remaining_volume: int   # 0 => full close


@dataclass(frozen=True)
class MasterPositionSLTPAmended:
    position_id: int
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class MasterPendingPlaced:
    order_id: int
    symbol_name: str
    side: Side
    order_type: PendingType
    volume: int
    lot_size: int
    price: float
    stop_loss: float | None
    take_profit: float | None
    expiry_ts_ms: int | None


@dataclass(frozen=True)
class MasterPendingReplaced:
    order_id: int
    symbol_name: str
    lot_size: int
    order_type: PendingType
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class MasterPendingCancelled:
    order_id: int


@dataclass(frozen=True)
class MasterPendingFilled:
    order_id: int
    position_id: int


@dataclass(frozen=True)
class MasterRejected:
    reason: str


MasterEvent = (MasterPositionOpened | MasterPositionClosed | MasterPositionSLTPAmended
               | MasterPendingPlaced | MasterPendingReplaced | MasterPendingCancelled
               | MasterPendingFilled | MasterRejected)


# ---------- mapping state (implemented by copier.db.repo.Repo) ----------

@dataclass(frozen=True)
class PositionMappingEntry:
    slave_account_id: int
    slave_position_id: int
    slave_volume: int


@dataclass(frozen=True)
class OrderMappingEntry:
    slave_account_id: int
    slave_order_id: int


class MappingState(Protocol):
    def position_entries(self, master_position_id: int) -> Sequence[PositionMappingEntry]: ...
    def order_entries(self, master_order_id: int) -> Sequence[OrderMappingEntry]: ...


# ---------- slave intents (decision core output) ----------

@dataclass(frozen=True)
class OpenMarket:
    slave_account_id: int
    master_position_id: int
    symbol_id: int
    side: Side
    volume: int
    stop_loss: float | None
    take_profit: float | None
    label: str


@dataclass(frozen=True)
class ClosePosition:
    slave_account_id: int
    position_id: int
    volume: int


@dataclass(frozen=True)
class AmendPositionSLTP:
    slave_account_id: int
    position_id: int
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class PlacePending:
    slave_account_id: int
    master_order_id: int
    symbol_id: int
    side: Side
    order_type: PendingType
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None
    expiry_ts_ms: int | None
    label: str


@dataclass(frozen=True)
class AmendPending:
    slave_account_id: int
    order_id: int
    order_type: PendingType
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class CancelPending:
    slave_account_id: int
    order_id: int


@dataclass(frozen=True)
class LinkPendingFill:
    slave_account_id: int
    master_order_id: int
    master_position_id: int


@dataclass(frozen=True)
class Alert:
    slave_account_id: int | None
    message: str


SlaveIntent = (OpenMarket | ClosePosition | AmendPositionSLTP | PlacePending
               | AmendPending | CancelPending | LinkPendingFill | Alert)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd copier && .venv/bin/pytest tests/unit/test_models.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/domain/models.py copier/tests/unit/test_models.py
git commit -m "feat: domain models for master events, mappings, slave intents"
```

### Task 5: Decision core — market positions (open / close / partial close / SL-TP)

**Files:**
- Create: `copier/src/copier/domain/decision.py`
- Test: `copier/tests/unit/test_decision_positions.py`

**Interfaces:**
- Consumes: Task 3 sizing functions, Task 4 models.
- Produces: `decide(event: MasterEvent, mappings: MappingState, slaves: Sequence[SlaveConfig]) -> list[SlaveIntent]` — THE spec §5 pure function. Tasks 6, 16, 19–20 call exactly this signature.

Behavior contract (spec §5 table):
- Actions are generated **only for enabled slaves**; disabled/paused slaves are skipped (their divergence later shows up as drift).
- Open: symbol missing on slave → `Alert`; mirrored volume rounds to 0 → `Alert`; otherwise `OpenMarket` with the master's SL/TP and label `copy:m<masterPositionId>`. Volumes below `minVolume` are still sent — the broker rejects and that surfaces as an alert (never silently resized).
- Close: for each `PositionMappingEntry` of the master position (an enabled slave may have several if the master position grew): full close (`remaining_volume == 0`) closes the entry's whole `slave_volume`; partial close closes `partial_close_volume(...)` of it; a 0-rounded partial → `Alert`. Enabled slave with no entry → `Alert` ("no copy to close").
- A second `MasterPositionOpened` for an already-mapped position id is a position **increase**: emit `OpenMarket` for the delta volume with the same label (creates an additional mapping entry).
- SL/TP amend: `AmendPositionSLTP` per mapped entry; no entry → `Alert`.

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_decision_positions.py`:
```python
from decimal import Decimal

from copier.domain import models as m
from copier.domain.decision import decide

EURUSD = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                      lot_size=10_000_000, min_volume=100_000, step_volume=100_000)


def slave(account_id=101, enabled=True, mult="1.0", symbols=None):
    return m.SlaveConfig(account_id=account_id, enabled=enabled,
                         multiplier=Decimal(mult),
                         symbols={"EURUSD": EURUSD} if symbols is None else symbols)


class MapState:
    def __init__(self, positions=None, orders=None):
        self._p = positions or {}
        self._o = orders or {}
    def position_entries(self, master_position_id):
        return self._p.get(master_position_id, [])
    def order_entries(self, master_order_id):
        return self._o.get(master_order_id, [])


OPEN = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                              volume=10_000_000, lot_size=10_000_000,
                              stop_loss=1.09, take_profit=1.12)


def test_open_fans_out_to_enabled_slaves_with_label():
    out = decide(OPEN, MapState(), [slave(101), slave(102)])
    assert [type(i) for i in out] == [m.OpenMarket, m.OpenMarket]
    assert out[0] == m.OpenMarket(slave_account_id=101, master_position_id=11, symbol_id=1,
                                  side=m.Side.BUY, volume=10_000_000,
                                  stop_loss=1.09, take_profit=1.12, label="copy:m11")


def test_open_applies_multiplier():
    out = decide(OPEN, MapState(), [slave(101, mult="0.5")])
    assert out[0].volume == 5_000_000


def test_open_skips_disabled_slaves():
    assert decide(OPEN, MapState(), [slave(101, enabled=False)]) == []


def test_open_missing_symbol_alerts():
    out = decide(OPEN, MapState(), [slave(101, symbols={})])
    assert isinstance(out[0], m.Alert) and out[0].slave_account_id == 101
    assert "EURUSD" in out[0].message


def test_open_zero_rounded_volume_alerts_never_sends_zero():
    tiny = m.MasterPositionOpened(position_id=12, symbol_name="EURUSD", side=m.Side.SELL,
                                  volume=100_000, lot_size=10_000_000,
                                  stop_loss=None, take_profit=None)
    out = decide(tiny, MapState(), [slave(101, mult="0.5")])
    assert isinstance(out[0], m.Alert)


def test_full_close_closes_entire_mapped_volume():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 10_000_000)]})
    ev = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                closed_volume=10_000_000, remaining_volume=0)
    out = decide(ev, st, [slave(101)])
    assert out == [m.ClosePosition(slave_account_id=101, position_id=555, volume=10_000_000)]


def test_partial_close_closes_same_fraction_of_slave_volume():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 6_000_000)]})
    ev = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                closed_volume=5_000_000, remaining_volume=5_000_000)
    out = decide(ev, st, [slave(101)])
    assert out == [m.ClosePosition(slave_account_id=101, position_id=555, volume=3_000_000)]


def test_close_with_no_mapping_alerts():
    ev = m.MasterPositionClosed(position_id=99, symbol_name="EURUSD",
                                closed_volume=1, remaining_volume=0)
    out = decide(ev, MapState(), [slave(101)])
    assert isinstance(out[0], m.Alert)


def test_close_skips_disabled_slave_entry():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 10_000_000)]})
    ev = m.MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                closed_volume=10_000_000, remaining_volume=0)
    assert decide(ev, st, [slave(101, enabled=False)]) == []


def test_increase_emits_delta_open_for_mapped_position():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 10_000_000)]})
    inc = m.MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=m.Side.BUY,
                                 volume=2_000_000, lot_size=10_000_000,
                                 stop_loss=None, take_profit=None)
    out = decide(inc, st, [slave(101)])
    assert out == [m.OpenMarket(101, 11, 1, m.Side.BUY, 2_000_000, None, None, "copy:m11")]


def test_sltp_amend_maps_to_each_entry():
    st = MapState(positions={11: [m.PositionMappingEntry(101, 555, 1),
                                  m.PositionMappingEntry(102, 777, 1)]})
    ev = m.MasterPositionSLTPAmended(position_id=11, stop_loss=1.05, take_profit=None)
    out = decide(ev, st, [slave(101), slave(102)])
    assert out == [m.AmendPositionSLTP(101, 555, 1.05, None),
                   m.AmendPositionSLTP(102, 777, 1.05, None)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd copier && .venv/bin/pytest tests/unit/test_decision_positions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copier.domain.decision'`.

- [ ] **Step 3: Implement `decision.py` (position half)**

```python
"""Pure replication decision core: (master_event, mapping_state, slave_configs) -> [SlaveIntent].

No I/O. No clocks. No randomness. Everything here must stay exhaustively unit-tested.
"""
from typing import Sequence

from copier.domain import models as m
from copier.domain.sizing import mirror_volume, partial_close_volume


def decide(event: m.MasterEvent, mappings: m.MappingState,
           slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    match event:
        case m.MasterPositionOpened():
            return _position_opened(event, slaves)
        case m.MasterPositionClosed():
            return _position_closed(event, mappings, slaves)
        case m.MasterPositionSLTPAmended():
            return _position_sltp(event, mappings, slaves)
        case m.MasterRejected():
            return []  # spec §5: master rejections replicate as no-ops (logged by caller)
        case _:
            return _decide_pending(event, mappings, slaves)  # Task 6


def _enabled(slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveConfig]:
    return [s for s in slaves if s.enabled]


def _by_id(slaves: Sequence[m.SlaveConfig]) -> dict[int, m.SlaveConfig]:
    return {s.account_id: s for s in _enabled(slaves)}


def _position_opened(e: m.MasterPositionOpened,
                     slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    for s in _enabled(slaves):
        sym = s.symbols.get(e.symbol_name)
        if sym is None:
            out.append(m.Alert(s.account_id,
                       f"cannot copy position {e.position_id}: symbol {e.symbol_name!r} not available"))
            continue
        vol = mirror_volume(e.volume, e.lot_size, s.multiplier, sym.lot_size, sym.step_volume)
        if vol == 0:
            out.append(m.Alert(s.account_id,
                       f"cannot copy position {e.position_id}: mirrored volume rounds to 0"))
            continue
        out.append(m.OpenMarket(s.account_id, e.position_id, sym.symbol_id, e.side, vol,
                                e.stop_loss, e.take_profit, f"copy:m{e.position_id}"))
    return out


def _position_closed(e: m.MasterPositionClosed, mappings: m.MappingState,
                     slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    enabled = _by_id(slaves)
    entries = mappings.position_entries(e.position_id)
    covered: set[int] = set()
    for entry in entries:
        s = enabled.get(entry.slave_account_id)
        if s is None:
            continue
        covered.add(entry.slave_account_id)
        sym = s.symbols.get(e.symbol_name)
        step = sym.step_volume if sym is not None else 1
        vol = partial_close_volume(entry.slave_volume, e.closed_volume,
                                   e.remaining_volume, step)
        if vol == 0:
            out.append(m.Alert(s.account_id,
                       f"partial close of position {e.position_id} rounds to 0 on slave"))
            continue
        out.append(m.ClosePosition(s.account_id, entry.slave_position_id, vol))
    for account_id in enabled.keys() - covered:
        out.append(m.Alert(account_id,
                   f"master closed position {e.position_id} but slave has no mapped copy"))
    return out


def _position_sltp(e: m.MasterPositionSLTPAmended, mappings: m.MappingState,
                   slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    enabled = _by_id(slaves)
    entries = mappings.position_entries(e.position_id)
    covered: set[int] = set()
    for entry in entries:
        if entry.slave_account_id not in enabled:
            continue
        covered.add(entry.slave_account_id)
        out.append(m.AmendPositionSLTP(entry.slave_account_id, entry.slave_position_id,
                                       e.stop_loss, e.take_profit))
    for account_id in enabled.keys() - covered:
        out.append(m.Alert(account_id,
                   f"SL/TP change on master position {e.position_id} but slave has no mapped copy"))
    return out


def _decide_pending(event, mappings, slaves) -> list[m.SlaveIntent]:
    raise NotImplementedError  # implemented in Task 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd copier && .venv/bin/pytest tests/unit/test_decision_positions.py -v`
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/domain/decision.py copier/tests/unit/test_decision_positions.py
git commit -m "feat: decision core for market position open/close/partial/SLTP"
```

### Task 6: Decision core — pending orders, fills, rejections

**Files:**
- Modify: `copier/src/copier/domain/decision.py` (replace `_decide_pending` stub)
- Test: `copier/tests/unit/test_decision_pending.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces: complete `decide(...)` covering the full spec §5 table. No other signature changes.

Behavior contract:
- `MasterPendingPlaced` → `PlacePending` per enabled slave (mirror volume, same type/price/SL/TP/expiry, label `copy:o<masterOrderId>`); missing symbol / zero volume → `Alert`.
- `MasterPendingReplaced` → `AmendPending` per mapped entry (re-mirrored volume, new price/SL/TP); no entry for an enabled slave → `Alert`.
- `MasterPendingCancelled` (cancel or expiry) → `CancelPending` per mapped entry.
- `MasterPendingFilled` → **no new slave open** (the slave's own pending order fills broker-side); emit `LinkPendingFill` per mapped entry so the service links resulting positions; enabled slave without an entry → `Alert`.
- `MasterRejected` → `[]` (already covered in Task 5, re-asserted here).

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_decision_pending.py`:
```python
from decimal import Decimal

from copier.domain import models as m
from copier.domain.decision import decide

EURUSD = m.SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                      lot_size=10_000_000, min_volume=100_000, step_volume=100_000)


def slave(account_id=101, enabled=True, mult="1.0"):
    return m.SlaveConfig(account_id=account_id, enabled=enabled,
                         multiplier=Decimal(mult), symbols={"EURUSD": EURUSD})


class MapState:
    def __init__(self, orders=None):
        self._o = orders or {}
    def position_entries(self, master_position_id):
        return []
    def order_entries(self, master_order_id):
        return self._o.get(master_order_id, [])


PLACED = m.MasterPendingPlaced(order_id=42, symbol_name="EURUSD", side=m.Side.SELL,
                               order_type=m.PendingType.LIMIT, volume=1_000_000,
                               lot_size=10_000_000, price=1.1150,
                               stop_loss=1.1200, take_profit=1.1000, expiry_ts_ms=None)


def test_pending_place_mirrors_type_price_sltp_with_label():
    out = decide(PLACED, MapState(), [slave(101)])
    assert out == [m.PlacePending(slave_account_id=101, master_order_id=42, symbol_id=1,
                                  side=m.Side.SELL, order_type=m.PendingType.LIMIT,
                                  volume=1_000_000, price=1.1150, stop_loss=1.1200,
                                  take_profit=1.1000, expiry_ts_ms=None, label="copy:o42")]


def test_pending_place_applies_multiplier():
    out = decide(PLACED, MapState(), [slave(101, mult="2.0")])
    assert out[0].volume == 2_000_000


def test_pending_replace_amends_mapped_order_with_remirrored_volume():
    st = MapState(orders={42: [m.OrderMappingEntry(101, 900)]})
    ev = m.MasterPendingReplaced(order_id=42, symbol_name="EURUSD", lot_size=10_000_000,
                                 order_type=m.PendingType.LIMIT, volume=3_000_000,
                                 price=1.1100, stop_loss=None, take_profit=None)
    out = decide(ev, st, [slave(101)])
    assert out == [m.AmendPending(slave_account_id=101, order_id=900,
                                  order_type=m.PendingType.LIMIT, volume=3_000_000,
                                  price=1.1100, stop_loss=None, take_profit=None)]


def test_pending_replace_without_mapping_alerts():
    ev = m.MasterPendingReplaced(order_id=42, symbol_name="EURUSD", lot_size=10_000_000,
                                 order_type=m.PendingType.STOP, volume=1, price=1.0,
                                 stop_loss=None, take_profit=None)
    out = decide(ev, MapState(), [slave(101)])
    assert isinstance(out[0], m.Alert)


def test_pending_cancel_cancels_each_mapped_order():
    st = MapState(orders={42: [m.OrderMappingEntry(101, 900), m.OrderMappingEntry(102, 901)]})
    out = decide(m.MasterPendingCancelled(order_id=42), st, [slave(101), slave(102)])
    assert out == [m.CancelPending(101, 900), m.CancelPending(102, 901)]


def test_pending_fill_links_and_never_opens():
    st = MapState(orders={42: [m.OrderMappingEntry(101, 900)]})
    out = decide(m.MasterPendingFilled(order_id=42, position_id=77), st,
                 [slave(101), slave(102)])
    assert m.LinkPendingFill(101, 42, 77) in out
    assert not any(isinstance(i, m.OpenMarket) for i in out)
    # slave 102 has no mapped order -> alert (spec: slave order not yet placed/filled -> alert)
    assert any(isinstance(i, m.Alert) and i.slave_account_id == 102 for i in out)


def test_master_rejection_is_a_noop():
    assert decide(m.MasterRejected(reason="NOT_ENOUGH_MONEY"), MapState(), [slave(101)]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd copier && .venv/bin/pytest tests/unit/test_decision_pending.py -v`
Expected: FAIL — `NotImplementedError` from `_decide_pending`.

- [ ] **Step 3: Replace `_decide_pending` with the real implementation**

```python
def _decide_pending(event, mappings: m.MappingState,
                    slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    enabled = _by_id(slaves)
    match event:
        case m.MasterPendingPlaced() as e:
            for s in _enabled(slaves):
                sym = s.symbols.get(e.symbol_name)
                if sym is None:
                    out.append(m.Alert(s.account_id,
                               f"cannot copy order {e.order_id}: symbol {e.symbol_name!r} not available"))
                    continue
                vol = mirror_volume(e.volume, e.lot_size, s.multiplier,
                                    sym.lot_size, sym.step_volume)
                if vol == 0:
                    out.append(m.Alert(s.account_id,
                               f"cannot copy order {e.order_id}: mirrored volume rounds to 0"))
                    continue
                out.append(m.PlacePending(s.account_id, e.order_id, sym.symbol_id, e.side,
                                          e.order_type, vol, e.price, e.stop_loss,
                                          e.take_profit, e.expiry_ts_ms, f"copy:o{e.order_id}"))
        case m.MasterPendingReplaced() as e:
            covered: set[int] = set()
            for entry in mappings.order_entries(e.order_id):
                s = enabled.get(entry.slave_account_id)
                if s is None:
                    continue
                covered.add(entry.slave_account_id)
                sym = s.symbols.get(e.symbol_name)
                if sym is None:
                    out.append(m.Alert(s.account_id,
                               f"cannot amend order copy of {e.order_id}: symbol missing"))
                    continue
                vol = mirror_volume(e.volume, e.lot_size, s.multiplier,
                                    sym.lot_size, sym.step_volume)
                out.append(m.AmendPending(s.account_id, entry.slave_order_id, e.order_type,
                                          vol, e.price, e.stop_loss, e.take_profit))
            for account_id in enabled.keys() - covered:
                out.append(m.Alert(account_id,
                           f"master replaced order {e.order_id} but slave has no mapped order"))
        case m.MasterPendingCancelled() as e:
            for entry in mappings.order_entries(e.order_id):
                if entry.slave_account_id in enabled:
                    out.append(m.CancelPending(entry.slave_account_id, entry.slave_order_id))
        case m.MasterPendingFilled() as e:
            covered = set()
            for entry in mappings.order_entries(e.order_id):
                if entry.slave_account_id not in enabled:
                    continue
                covered.add(entry.slave_account_id)
                out.append(m.LinkPendingFill(entry.slave_account_id, e.order_id, e.position_id))
            for account_id in enabled.keys() - covered:
                out.append(m.Alert(account_id,
                           f"master order {e.order_id} filled but slave has no mapped order"))
    return out
```

- [ ] **Step 4: Run the full decision suite**

Run: `cd copier && .venv/bin/pytest tests/unit/test_decision_positions.py tests/unit/test_decision_pending.py tests/unit/test_sizing.py -v`
Expected: all PASS (decision core complete).

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/domain/decision.py copier/tests/unit/test_decision_pending.py
git commit -m "feat: decision core pending-order lifecycle, fills, rejection no-ops"
```

---

## Phase 3: cTrader connection layer

Wraps the Spotware SDK (`ctrader_open_api`): app auth, per-account auth, heartbeat ≤ 10 s, reconnect with backoff, token storage with rotation. Pure/injectable pieces are unit-tested here; live-wire behavior is integration-tested against the fake server in Phase 4.

### Task 7: Token store — Fernet at rest, rotation in one transaction, refresh due-list

**Files:**
- Create: `copier/src/copier/ctrader/__init__.py` (empty), `copier/src/copier/ctrader/tokens.py`
- Test: `copier/tests/unit/test_tokens.py`

**Interfaces:**
- Consumes: Task 2 schema (`ctid_connections`), `db` fixture.
- Produces (used by Tasks 19, 22):
  - `TokenPair(connection_id: int, access_token: str, refresh_token: str, expires_at: datetime, status: str)`
  - `TokenStore(dsn: str, fernet_key: str)` with `save_grant(access_token, refresh_token, expires_at) -> int`, `get(connection_id) -> TokenPair`, `rotate(connection_id, access_token, refresh_token, expires_at) -> None`, `due_for_refresh(now: datetime) -> list[int]`, `mark(connection_id, status: str) -> None`
  - Constant `REFRESH_THRESHOLD = timedelta(days=25)` — a connection is due when `expires_at - now < REFRESH_THRESHOLD`.

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_tokens.py`:
```python
from datetime import datetime, timedelta, timezone

import psycopg
from cryptography.fernet import Fernet

from copier.ctrader.tokens import REFRESH_THRESHOLD, TokenStore

KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def make_store(db):
    return TokenStore(db, KEY)


def test_save_grant_encrypts_tokens_at_rest(db):
    store = make_store(db)
    cid = store.save_grant("access-1", "refresh-1", NOW + timedelta(days=30))
    with psycopg.connect(db) as conn:
        enc_a, enc_r = conn.execute(
            "SELECT access_token_enc, refresh_token_enc FROM ctid_connections WHERE id=%s",
            (cid,)).fetchone()
    assert "access-1" not in enc_a and "refresh-1" not in enc_r     # never plaintext
    assert Fernet(KEY.encode()).decrypt(enc_a.encode()).decode() == "access-1"


def test_get_roundtrips(db):
    store = make_store(db)
    cid = store.save_grant("a", "r", NOW + timedelta(days=30))
    pair = store.get(cid)
    assert (pair.access_token, pair.refresh_token, pair.status) == ("a", "r", "active")


def test_rotate_persists_new_refresh_token(db):
    # spec: refresh token rotates on every refresh; new one MUST be persisted
    store = make_store(db)
    cid = store.save_grant("a1", "r1", NOW + timedelta(days=30))
    store.rotate(cid, "a2", "r2", NOW + timedelta(days=60))
    pair = store.get(cid)
    assert (pair.access_token, pair.refresh_token) == ("a2", "r2")
    assert pair.expires_at == NOW + timedelta(days=60)
    assert pair.status == "active"          # rotation revives invalid connections


def test_due_for_refresh_under_25_days_remaining(db):
    store = make_store(db)
    due = store.save_grant("a", "r", NOW + timedelta(days=24))
    fresh = store.save_grant("a", "r", NOW + timedelta(days=26))
    assert store.due_for_refresh(NOW) == [due]
    assert fresh not in store.due_for_refresh(NOW)


def test_mark_status(db):
    store = make_store(db)
    cid = store.save_grant("a", "r", NOW + timedelta(days=30))
    store.mark(cid, "refresh_failed")
    assert store.get(cid).status == "refresh_failed"


def test_threshold_constant():
    assert REFRESH_THRESHOLD == timedelta(days=25)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd copier && .venv/bin/pytest tests/unit/test_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copier.ctrader'`.

- [ ] **Step 3: Implement `tokens.py`**

```python
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from cryptography.fernet import Fernet

REFRESH_THRESHOLD = timedelta(days=25)


@dataclass(frozen=True)
class TokenPair:
    connection_id: int
    access_token: str
    refresh_token: str
    expires_at: datetime
    status: str


class TokenStore:
    def __init__(self, dsn: str, fernet_key: str):
        self._dsn = dsn
        self._fernet = Fernet(fernet_key.encode())

    def _enc(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def _dec(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()

    def save_grant(self, access_token: str, refresh_token: str, expires_at: datetime) -> int:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                "INSERT INTO ctid_connections"
                " (access_token_enc, refresh_token_enc, granted_at, expires_at)"
                " VALUES (%s, %s, now(), %s) RETURNING id",
                (self._enc(access_token), self._enc(refresh_token), expires_at),
            ).fetchone()
            conn.commit()
            return row[0]

    def get(self, connection_id: int) -> TokenPair:
        with psycopg.connect(self._dsn) as conn:
            a, r, exp, status = conn.execute(
                "SELECT access_token_enc, refresh_token_enc, expires_at, status"
                " FROM ctid_connections WHERE id=%s", (connection_id,)).fetchone()
        return TokenPair(connection_id, self._dec(a), self._dec(r), exp, status)

    def rotate(self, connection_id: int, access_token: str, refresh_token: str,
               expires_at: datetime) -> None:
        # single transaction: rotated refresh token is never lost (spec §5)
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "UPDATE ctid_connections SET access_token_enc=%s, refresh_token_enc=%s,"
                " expires_at=%s, status='active' WHERE id=%s",
                (self._enc(access_token), self._enc(refresh_token), expires_at, connection_id))
            conn.commit()

    def due_for_refresh(self, now: datetime) -> list[int]:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT id FROM ctid_connections"
                " WHERE status='active' AND expires_at - %s < %s ORDER BY id",
                (now, REFRESH_THRESHOLD)).fetchall()
        return [r[0] for r in rows]

    def mark(self, connection_id: int, status: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute("UPDATE ctid_connections SET status=%s WHERE id=%s",
                         (status, connection_id))
            conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd copier && .venv/bin/pytest tests/unit/test_tokens.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/ctrader/ copier/tests/unit/test_tokens.py
git commit -m "feat: Fernet token store with transactional refresh rotation"
```

### Task 8: `CTraderClient` wrapper — app auth, account auth registry, heartbeat, event routing

**Files:**
- Create: `copier/src/copier/ctrader/client.py`
- Test: `copier/tests/unit/test_client.py`

**Interfaces:**
- Consumes: `ctrader_open_api` SDK (`Client`, `TcpProtocol`, `Protobuf`), Task 4 models (none directly), protobuf messages `ProtoOAApplicationAuthReq`, `ProtoOAAccountAuthReq`, `ProtoHeartbeatEvent`, `ProtoOAExecutionEvent`, `ProtoOAAccountsTokenInvalidatedEvent`, `ProtoOAAccountDisconnectEvent`.
- Produces (used by Tasks 10–20):
  - `HEARTBEAT_INTERVAL_S = 8.0`
  - `make_sdk_client(host: str, port: int)` → SDK `Client(host, port, TcpProtocol, retryPolicy=backoffPolicy(initialDelay=1.0, maxDelay=60.0, factor=2.0))` — reconnect-with-backoff comes from Twisted's `ClientService` retry policy.
  - `CTraderClient(sdk, client_id: str, client_secret: str, clock=None)` with:
    - `start() -> None`, `stop() -> None`
    - `ready: Deferred` — fires (once) after the first successful app auth
    - `authorize_account(account_id: int, access_token: str) -> Deferred` — sends `ProtoOAAccountAuthReq`, registers the account for automatic re-auth on every reconnect
    - `deauthorize_account(account_id: int) -> None` — drop from re-auth registry
    - `send(msg) -> Deferred` — raw passthrough to `sdk.send`
    - `on_execution(cb: Callable[[int, ProtoOAExecutionEvent], None])` (cb gets `ctidTraderAccountId`, event)
    - `on_account_disconnect(cb: Callable[[int], None])`, `on_tokens_invalidated(cb: Callable[[list[int]], None])`, `on_spot(cb: Callable[[object], None])` (spot events used by Task 18)

The SDK object is injected, so unit tests use a `StubSdk`; the same wrapper runs unmodified against the fake server (Task 10) and real cTrader.

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_client.py`:
```python
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAApplicationAuthReq, ProtoOAExecutionEvent)
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.ctrader.client import HEARTBEAT_INTERVAL_S, CTraderClient


class StubSdk:
    def __init__(self):
        self.sent = []
        self.running = False
        self._connected_cb = self._disconnected_cb = self._message_cb = None

    def setConnectedCallback(self, cb): self._connected_cb = cb
    def setDisconnectedCallback(self, cb): self._disconnected_cb = cb
    def setMessageReceivedCallback(self, cb): self._message_cb = cb
    def startService(self): self.running = True
    def stopService(self): self.running = False

    def send(self, msg, **kwargs):
        self.sent.append(msg)
        return defer.succeed(None)

    # test helpers
    def connect(self): self._connected_cb(self)
    def disconnect(self): self._disconnected_cb(self, "lost")
    def deliver(self, payload): self._message_cb(self, payload)


def make():
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    return sdk, clock, client


def of_type(sent, t):
    return [s for s in sent if isinstance(s, t)]


def test_start_starts_sdk_and_connect_sends_app_auth():
    sdk, _, _ = make()
    assert sdk.running
    sdk.connect()
    reqs = of_type(sdk.sent, ProtoOAApplicationAuthReq)
    assert len(reqs) == 1
    assert (reqs[0].clientId, reqs[0].clientSecret) == ("cid", "csecret")


def test_heartbeat_every_8s_after_auth_stops_on_disconnect():
    sdk, clock, _ = make()
    sdk.connect()
    clock.advance(HEARTBEAT_INTERVAL_S)
    clock.advance(HEARTBEAT_INTERVAL_S)
    assert len(of_type(sdk.sent, ProtoHeartbeatEvent)) == 2
    assert HEARTBEAT_INTERVAL_S <= 10.0        # spec: at least every 10 s
    sdk.disconnect()
    clock.advance(HEARTBEAT_INTERVAL_S * 3)
    assert len(of_type(sdk.sent, ProtoHeartbeatEvent)) == 2


def test_authorize_account_sends_account_auth():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "tok-1001")
    reqs = of_type(sdk.sent, ProtoOAAccountAuthReq)
    assert (reqs[0].ctidTraderAccountId, reqs[0].accessToken) == (1001, "tok-1001")


def test_reconnect_reauths_app_and_all_registered_accounts():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "t1")
    client.authorize_account(1002, "t2")
    sdk.disconnect()
    sdk.connect()   # ClientService reconnected
    assert len(of_type(sdk.sent, ProtoOAApplicationAuthReq)) == 2
    reauthed = of_type(sdk.sent, ProtoOAAccountAuthReq)
    assert {r.ctidTraderAccountId for r in reauthed[-2:]} == {1001, 1002}


def test_execution_events_routed_with_account_id():
    sdk, _, client = make()
    seen = []
    client.on_execution(lambda account_id, evt: seen.append((account_id, evt)))
    sdk.connect()
    evt = ProtoOAExecutionEvent()
    evt.ctidTraderAccountId = 1001
    sdk.deliver(evt)
    assert seen and seen[0][0] == 1001


def test_ready_fires_after_first_app_auth():
    sdk, _, client = make()
    fired = []
    client.ready.addCallback(fired.append)
    sdk.connect()
    assert fired
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd copier && .venv/bin/pytest tests/unit/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copier.ctrader.client'`.

- [ ] **Step 3: Implement `client.py`**

```python
"""Thin wrapper around the Spotware SDK client.

Owns: app auth on every (re)connect, per-account re-auth registry, 8 s heartbeat,
typed routing of pushed events. Reconnect scheduling itself is Twisted
ClientService's retryPolicy (see make_sdk_client).
"""
import logging
from typing import Callable

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAAccountDisconnectEvent,
    ProtoOAAccountsTokenInvalidatedEvent, ProtoOAApplicationAuthReq,
    ProtoOAExecutionEvent, ProtoOASpotEvent)
from twisted.application.internet import backoffPolicy
from twisted.internet import defer, task

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 8.0   # server requires <= 10 s


def make_sdk_client(host: str, port: int) -> Client:
    return Client(host, port, TcpProtocol,
                  retryPolicy=backoffPolicy(initialDelay=1.0, maxDelay=60.0, factor=2.0))


class CTraderClient:
    def __init__(self, sdk, client_id: str, client_secret: str, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._sdk = sdk
        self._client_id = client_id
        self._client_secret = client_secret
        self._accounts: dict[int, str] = {}          # account_id -> access token
        self._exec_cbs: list[Callable] = []
        self._disc_cbs: list[Callable] = []
        self._invalid_cbs: list[Callable] = []
        self._spot_cbs: list[Callable] = []
        self.ready: defer.Deferred = defer.Deferred()
        self._hb = task.LoopingCall(self._heartbeat)
        self._hb.clock = clock
        sdk.setConnectedCallback(self._on_connected)
        sdk.setDisconnectedCallback(self._on_disconnected)
        sdk.setMessageReceivedCallback(self._on_message)

    def start(self) -> None:
        self._sdk.startService()

    def stop(self) -> None:
        if self._hb.running:
            self._hb.stop()
        self._sdk.stopService()

    def authorize_account(self, account_id: int, access_token: str) -> defer.Deferred:
        self._accounts[account_id] = access_token
        return self._send_account_auth(account_id)

    def deauthorize_account(self, account_id: int) -> None:
        self._accounts.pop(account_id, None)

    def send(self, msg) -> defer.Deferred:
        return self._sdk.send(msg)

    def on_execution(self, cb) -> None: self._exec_cbs.append(cb)
    def on_account_disconnect(self, cb) -> None: self._disc_cbs.append(cb)
    def on_tokens_invalidated(self, cb) -> None: self._invalid_cbs.append(cb)
    def on_spot(self, cb) -> None: self._spot_cbs.append(cb)

    # ---- internals ----

    def _on_connected(self, _sdk) -> None:
        req = ProtoOAApplicationAuthReq()
        req.clientId = self._client_id
        req.clientSecret = self._client_secret
        d = self._sdk.send(req)
        d.addCallback(self._on_app_authed)
        d.addErrback(lambda f: log.error("app auth failed: %s", f))

    def _on_app_authed(self, _res) -> None:
        if not self._hb.running:
            self._hb.start(HEARTBEAT_INTERVAL_S, now=False)
        for account_id in list(self._accounts):
            self._send_account_auth(account_id)
        if not self.ready.called:
            self.ready.callback(self)

    def _send_account_auth(self, account_id: int) -> defer.Deferred:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = account_id
        req.accessToken = self._accounts[account_id]
        d = self._sdk.send(req)
        d.addErrback(lambda f: log.error("account auth %s failed: %s", account_id, f))
        return d

    def _on_disconnected(self, _sdk, reason) -> None:
        log.warning("disconnected: %s", reason)
        if self._hb.running:
            self._hb.stop()

    def _heartbeat(self) -> None:
        d = self._sdk.send(ProtoHeartbeatEvent())
        d.addErrback(lambda _f: None)   # heartbeats have no response; ignore timeouts

    def _on_message(self, _sdk, message) -> None:
        payload = message
        if not isinstance(message, (ProtoOAExecutionEvent, ProtoOASpotEvent,
                                    ProtoOAAccountsTokenInvalidatedEvent,
                                    ProtoOAAccountDisconnectEvent)):
            try:
                payload = Protobuf.extract(message)
            except Exception:
                return
        if isinstance(payload, ProtoOAExecutionEvent):
            for cb in self._exec_cbs:
                cb(payload.ctidTraderAccountId, payload)
        elif isinstance(payload, ProtoOASpotEvent):
            for cb in self._spot_cbs:
                cb(payload)
        elif isinstance(payload, ProtoOAAccountDisconnectEvent):
            for cb in self._disc_cbs:
                cb(payload.ctidTraderAccountId)
        elif isinstance(payload, ProtoOAAccountsTokenInvalidatedEvent):
            for cb in self._invalid_cbs:
                cb(list(payload.ctidTraderAccountIds))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd copier && .venv/bin/pytest tests/unit/test_client.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/ctrader/client.py copier/tests/unit/test_client.py
git commit -m "feat: CTraderClient wrapper with app/account auth, heartbeat, event routing"
```

---

## Phase 4: Fake cTrader server + connection integration

A local Twisted TLS server speaking real Open API protobufs (spec §9). It backs every integration test from here on and the e2e stage. Wire format: 4-byte big-endian length prefix (`Int32StringReceiver`) around `ProtoMessage{payloadType, payload, clientMsgId}` — identical to the SDK's `TcpProtocol`.

### Task 9: Fake cTrader protobuf server

**Files:**
- Create: `copier/src/copier/testing/__init__.py` (empty), `copier/src/copier/testing/tls.py`, `copier/src/copier/testing/fake_server.py`
- Test: `copier/tests/integration/__init__.py` (empty), `copier/tests/integration/test_fake_server.py`

**Interfaces:**
- Consumes: `ctrader_open_api` protobufs, Twisted, `cryptography` (self-signed cert).
- Produces (used by Tasks 10–12, 19–20, 29–30):
  - `copier.testing.tls.make_self_signed_context() -> twisted.internet.ssl.CertificateOptions`
  - `FakeCTraderServer(auto_fill: bool = True)` with:
    - `listen(reactor) -> int` (returns bound port; TLS via self-signed cert)
    - state: `accounts: dict[int, str]` (account_id → expected access token; empty dict = accept any), `symbols` (defaults to EURUSD: id 1, digits 5, lotSize 10_000_000, minVolume 100_000, stepVolume 100_000)
    - recorders: `requests: list` (every trade request protobuf received, in order), `app_auths: list[tuple[str, str]]`, `account_auths: list[int]`, `heartbeats: list[float]`
    - scripting: `push_execution(evt: ProtoOAExecutionEvent)`, `push_spot(symbol_id: int, bid: int, ask: int)`, `drop_all_connections()`, `next_tokens: tuple[str, str] | None` (response for `ProtoOARefreshTokenReq`)
    - handles: `ProtoOAApplicationAuthReq/Res`, `ProtoOAAccountAuthReq/Res`, `ProtoOASymbolsListReq/Res` (light symbols), `ProtoOASymbolByIdReq/Res` (full `ProtoOASymbol` incl. `lotSize`), `ProtoOAReconcileReq/Res` (from scriptable `open_positions`/`pending_orders` per account), `ProtoOAGetAccountListByAccessTokenReq/Res`, `ProtoOARefreshTokenReq/Res`, `ProtoOASubscribeSpotsReq/Res`, `ProtoOATraderReq/Res` (scriptable `balances: dict[int, int]`), `ProtoHeartbeatEvent`, and all five trade requests `ProtoOANewOrderReq`, `ProtoOAClosePositionReq`, `ProtoOAAmendPositionSLTPReq`, `ProtoOAAmendOrderReq`, `ProtoOACancelOrderReq`
    - `auto_fill=True`: a `ProtoOANewOrderReq` MARKET is answered by pushed `ProtoOAExecutionEvent`s ORDER_ACCEPTED then ORDER_FILLED (new `positionId` allocated from a counter, `clientOrderId`/`label` echoed, `filledVolume` = requested volume); a LIMIT/STOP req → ORDER_ACCEPTED only (new `orderId`); `ProtoOAClosePositionReq` → ORDER_FILLED with `closePositionDetail` and remaining volume computed from its own position book.

Implementation notes (write real code, this is a summary of the required shape):
- Protocol class extends `twisted.protocols.basic.Int32StringReceiver` with `MAX_LENGTH = 16 * 1024 * 1024`; `stringReceived` parses `ProtoMessage`, dispatches on `payloadType` via a handler dict keyed by `ProtoOAPayloadType`/`ProtoPayloadType` enum values; every response is wrapped `ProtoMessage(payloadType=res.payloadType, payload=res.SerializeToString(), clientMsgId=<request's clientMsgId>)` so the SDK's Deferred correlation works.
- `tls.py`: generate an RSA-2048 key + self-signed X.509 cert (CN=localhost, 1-day validity) with `cryptography.x509`, return `ssl.CertificateOptions(privateKey=..., certificate=...)`. The SDK's `ssl:` client endpoint does not verify certs, so self-signed works; if your Twisted version does verify, connect via hostname `localhost` matching the CN.

- [ ] **Step 1: Write the failing integration test** (drives the server with the raw SDK client — proves wire compatibility)

`copier/tests/integration/test_fake_server.py`:
```python
import pytest
import pytest_twisted
from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq, ProtoOASymbolsListReq, ProtoOASymbolByIdReq)
from twisted.internet import defer, reactor

from copier.testing.fake_server import FakeCTraderServer


@pytest.fixture
def server():
    srv = FakeCTraderServer()
    srv.accounts = {1001: "tok-1001"}
    port = srv.listen(reactor)
    yield srv, port
    srv.shutdown()


@pytest_twisted.inlineCallbacks
def test_sdk_client_can_auth_and_list_symbols(server):
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    req = ProtoOAApplicationAuthReq()
    req.clientId, req.clientSecret = "cid", "csecret"
    res = yield sdk.send(req)
    assert isinstance(Protobuf.extract(res), ProtoOAApplicationAuthRes)
    assert srv.app_auths == [("cid", "csecret")]

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)
    assert srv.account_auths == [1001]

    syms = ProtoOASymbolsListReq()
    syms.ctidTraderAccountId = 1001
    light = Protobuf.extract((yield sdk.send(syms)))
    assert [s.symbolName for s in light.symbol] == ["EURUSD"]

    by_id = ProtoOASymbolByIdReq()
    by_id.ctidTraderAccountId = 1001
    by_id.symbolId.append(light.symbol[0].symbolId)
    full = Protobuf.extract((yield sdk.send(by_id)))
    assert full.symbol[0].lotSize == 10_000_000

    yield sdk.stopService()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd copier && .venv/bin/pytest tests/integration/test_fake_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copier.testing.fake_server'`.

- [ ] **Step 3: Implement `tls.py` and `fake_server.py`** per the interface notes above. Core skeleton:

```python
# fake_server.py (abridged skeleton — implement all handlers listed in Interfaces)
import itertools

from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent, ProtoMessage
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from twisted.internet.protocol import Factory
from twisted.protocols.basic import Int32StringReceiver

from copier.testing.tls import make_self_signed_context


class _Proto(Int32StringReceiver):
    MAX_LENGTH = 16 * 1024 * 1024

    def connectionMade(self):
        self.factory.server.protocols.append(self)

    def stringReceived(self, data):
        msg = ProtoMessage()
        msg.ParseFromString(data)
        self.factory.server.handle(self, msg)

    def send_payload(self, res, client_msg_id=""):
        out = ProtoMessage(payloadType=res.payloadType,
                           payload=res.SerializeToString(),
                           clientMsgId=client_msg_id)
        self.sendString(out.SerializeToString())


class FakeCTraderServer:
    def __init__(self, auto_fill: bool = True):
        self.auto_fill = auto_fill
        self.accounts: dict[int, str] = {}
        self.symbols = [dict(symbol_id=1, name="EURUSD", digits=5, lot_size=10_000_000,
                             min_volume=100_000, step_volume=100_000)]
        self.balances: dict[int, int] = {}
        self.open_positions: dict[int, list] = {}
        self.pending_orders: dict[int, list] = {}
        self.next_tokens: tuple[str, str] | None = None
        self.requests, self.app_auths, self.account_auths, self.heartbeats = [], [], [], []
        self.protocols: list[_Proto] = []
        self._position_ids = itertools.count(5000)
        self._order_ids = itertools.count(9000)
        self._listening = None

    def listen(self, reactor) -> int:
        factory = Factory.forProtocol(_Proto)
        factory.server = self
        self._listening = reactor.listenSSL(0, factory, make_self_signed_context())
        return self._listening.getHost().port

    def shutdown(self):
        self.drop_all_connections()
        if self._listening:
            self._listening.stopListening()

    def drop_all_connections(self):
        for p in list(self.protocols):
            p.transport.loseConnection()
        self.protocols.clear()

    def broadcast(self, event):
        for p in self.protocols:
            p.send_payload(event)

    def push_execution(self, evt): self.broadcast(evt)

    def handle(self, proto, msg):
        # dict-dispatch on msg.payloadType -> parse req, record, reply via
        # proto.send_payload(res, msg.clientMsgId); trade reqs append to self.requests
        # and, when auto_fill, push scripted ProtoOAExecutionEvents. Implement every
        # message listed in this task's Interfaces block.
        ...
```
(The `handle` dispatch table and the auto-fill execution-event builders are the bulk of the work — build each `ProtoOAExecutionEvent` with `executionType`, `order` (incl. `tradeData.symbolId/tradeSide/volume`, `clientOrderId`, `orderType`), `deal` (incl. `filledVolume`, `positionId`, and `closePositionDetail` + remaining volume for closes), and `position` (incl. `tradeData.volume`, `price`) exactly as asserted by the Task 10/13/19 tests.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd copier && .venv/bin/pytest tests/integration/test_fake_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/testing/ copier/tests/integration/
git commit -m "feat: fake cTrader protobuf TLS server for integration tests"
```

### Task 10: Connection-layer integration tests against the fake server

**Files:**
- Test: `copier/tests/integration/test_client_integration.py`
- Modify: `copier/src/copier/ctrader/client.py` only if a test exposes a defect.

**Interfaces:**
- Consumes: Tasks 8–9.
- Produces: proven guarantees later tasks rely on: auth chain order, heartbeat cadence on the wire, reconnect + re-auth, execution-event delivery, `ProtoOARefreshTokenReq` round-trip.

- [ ] **Step 1: Write the tests** (each drives `CTraderClient` + `make_sdk_client("127.0.0.1", port)` against `FakeCTraderServer`; use `pytest_twisted.inlineCallbacks` and a `wait_until(predicate, timeout=10)` helper polling with `deferLater(reactor, 0.05, ...)`):

```python
# test_client_integration.py — test list (write real bodies):
def test_full_auth_chain():
    # start -> ready fires; authorize_account(1001) ->
    # server saw app_auths == [("cid","csecret")] then account_auths == [1001]

def test_heartbeat_arrives_within_10s():
    # after ready, wait_until(server.heartbeats non-empty, timeout=9.5)
    # asserts wire heartbeat <= 10 s (spec)

def test_reconnect_reauths_everything():
    # authorize 1001; server.drop_all_connections();
    # wait_until(len(server.app_auths) == 2 and server.account_auths.count(1001) == 2)
    # (retryPolicy initialDelay 1 s -> total wait < 5 s)

def test_execution_event_reaches_handler():
    # client.on_execution(record); server.push_execution(fill_event(account=1001))
    # wait_until(recorded)

def test_refresh_token_roundtrip():
    # server.next_tokens = ("new-access", "new-refresh")
    # res = yield client.send(ProtoOARefreshTokenReq(refreshToken="old"))
    # extracted = Protobuf.extract(res)
    # assert (extracted.accessToken, extracted.refreshToken) == ("new-access", "new-refresh")
```

- [ ] **Step 2: Run to verify they fail/error meaningfully**

Run: `cd copier && .venv/bin/pytest tests/integration/test_client_integration.py -v`
Expected: failures only where fake-server handlers or wrapper behavior are missing — fix in the respective module (this is the checkpoint that hardens both).

- [ ] **Step 3: Make all pass, then run the whole suite**

Run: `cd copier && .venv/bin/pytest tests -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add copier/tests/integration/test_client_integration.py copier/src/copier
git commit -m "test: connection-layer integration vs fake cTrader server"
```

### Task 11: Symbol map builder (`symbols.py`)

**Files:**
- Create: `copier/src/copier/ctrader/symbols.py`
- Test: `copier/tests/integration/test_symbols.py`

**Interfaces:**
- Consumes: Task 8 `CTraderClient.send`, Task 4 `SymbolInfo`, fake server.
- Produces (used by Tasks 16, 18, 19):
  - `fetch_symbol_map(client: CTraderClient, account_id: int) -> Deferred[dict[str, SymbolInfo]]` — `ProtoOASymbolsListReq` (names/ids) then `ProtoOASymbolByIdReq` (full details incl. `lotSize`, `minVolume`, `stepVolume`, `digits`), keyed by symbol **name** (spec: match across accounts by name).
  - `by_id(symbol_map: dict[str, SymbolInfo]) -> dict[int, SymbolInfo]`

- [ ] **Step 1: Write the failing test**

```python
@pytest_twisted.inlineCallbacks
def test_fetch_symbol_map_builds_name_keyed_infos(server_and_client):
    server, client = server_and_client          # authorized account 1001
    symbol_map = yield fetch_symbol_map(client, 1001)
    info = symbol_map["EURUSD"]
    assert (info.symbol_id, info.lot_size, info.step_volume, info.min_volume, info.digits) == \
           (1, 10_000_000, 100_000, 100_000, 5)
    assert by_id(symbol_map)[1] is info
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: copier.ctrader.symbols`.

- [ ] **Step 3: Implement**

```python
from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq, ProtoOASymbolsListReq
from twisted.internet import defer

from copier.domain.models import SymbolInfo


@defer.inlineCallbacks
def fetch_symbol_map(client, account_id: int):
    req = ProtoOASymbolsListReq()
    req.ctidTraderAccountId = account_id
    light = Protobuf.extract((yield client.send(req)))
    names = {s.symbolId: s.symbolName for s in light.symbol}
    detail_req = ProtoOASymbolByIdReq()
    detail_req.ctidTraderAccountId = account_id
    detail_req.symbolId.extend(names.keys())
    full = Protobuf.extract((yield client.send(detail_req)))
    result: dict[str, SymbolInfo] = {}
    for sym in full.symbol:
        name = names[sym.symbolId]
        result[name] = SymbolInfo(symbol_id=sym.symbolId, name=name, digits=sym.digits,
                                  lot_size=sym.lotSize, min_volume=sym.minVolume,
                                  step_volume=sym.stepVolume)
    return result


def by_id(symbol_map: dict[str, SymbolInfo]) -> dict[int, SymbolInfo]:
    return {info.symbol_id: info for info in symbol_map.values()}
```

- [ ] **Step 4: Run to verify pass**, then **Step 5: Commit**

```bash
git add copier/src/copier/ctrader/symbols.py copier/tests/integration/test_symbols.py
git commit -m "feat: per-account symbol map fetch (name-keyed, lotSize/stepVolume)"
```

---

## Phase 5: Copier service

Wires master events → decision core → throttled fan-out → mapping persistence → pg_notify, plus reconciliation/drift, account state, the internal control endpoint, dry-run mode, and the boot sequence.

### Task 12: Token-bucket throttle (40 req/s per connection)

**Files:**
- Create: `copier/src/copier/engine/__init__.py` (empty), `copier/src/copier/engine/throttle.py`
- Test: `copier/tests/unit/test_throttle.py`

**Interfaces:**
- Consumes: Twisted `IReactorTime` (injectable `Clock`).
- Produces: `TokenBucket(rate: float = 40.0, capacity: float = 40.0, clock=None)` with `acquire() -> Deferred` (fires with `None` when a token is granted, FIFO order). One bucket instance **per connection** (Task 19).

- [ ] **Step 1: Write the failing tests**

`copier/tests/unit/test_throttle.py`:
```python
from twisted.internet.task import Clock

from copier.engine.throttle import TokenBucket


def fired(d):
    out = []
    d.addCallback(out.append)
    return out


def test_first_40_are_immediate():
    bucket = TokenBucket(clock=Clock())
    results = [fired(bucket.acquire()) for _ in range(40)]
    assert all(results)


def test_41st_waits_for_refill():
    clock = Clock()
    bucket = TokenBucket(clock=clock)
    for _ in range(40):
        bucket.acquire()
    out = fired(bucket.acquire())
    assert not out
    clock.advance(0.026)          # one token refills in 1/40 s
    assert out


def test_49_slave_fanout_completes_in_well_under_2s():
    # spec §5: 49-slave fan-out ~1.2 s worst case at 40 req/s
    clock = Clock()
    bucket = TokenBucket(clock=clock)
    outs = [fired(bucket.acquire()) for _ in range(89)]   # 40 burst + 49 queued
    clock.advance(1.3)
    assert all(outs)


def test_fifo_order():
    clock = Clock()
    bucket = TokenBucket(rate=1, capacity=1, clock=clock)
    bucket.acquire()
    order = []
    bucket.acquire().addCallback(lambda _: order.append("first"))
    bucket.acquire().addCallback(lambda _: order.append("second"))
    clock.advance(2.5)
    assert order == ["first", "second"]
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: copier.engine.throttle`.

- [ ] **Step 3: Implement `throttle.py`**

```python
from collections import deque

from twisted.internet import defer


class TokenBucket:
    """FIFO token bucket. Default 40 req/s, burst 40 (server cap is 50 req/s)."""

    def __init__(self, rate: float = 40.0, capacity: float = 40.0, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._clock = clock
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = clock.seconds()
        self._waiters: deque[defer.Deferred] = deque()
        self._pending_call = None

    def acquire(self) -> defer.Deferred:
        self._refill()
        if self._tokens >= 1 and not self._waiters:
            self._tokens -= 1
            return defer.succeed(None)
        d = defer.Deferred()
        self._waiters.append(d)
        self._schedule()
        return d

    def _refill(self) -> None:
        now = self._clock.seconds()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now

    def _schedule(self) -> None:
        if self._pending_call is not None and self._pending_call.active():
            return
        delay = max((1 - self._tokens) / self._rate, 0)
        self._pending_call = self._clock.callLater(delay, self._drain)

    def _drain(self) -> None:
        self._pending_call = None
        self._refill()
        while self._waiters and self._tokens >= 1:
            self._tokens -= 1
            self._waiters.popleft().callback(None)
        if self._waiters:
            self._schedule()
```

- [ ] **Step 4: Run to verify pass** — `cd copier && .venv/bin/pytest tests/unit/test_throttle.py -v` → 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/ copier/tests/unit/test_throttle.py
git commit -m "feat: 40 req/s token-bucket throttle with FIFO waiters"
```

### Task 13: Execution-event normalizer (`normalize.py`)

**Files:**
- Create: `copier/src/copier/engine/normalize.py`
- Test: `copier/tests/unit/test_normalize.py`

**Interfaces:**
- Consumes: `ProtoOAExecutionEvent` + model enums, Task 4 models, Task 11 `by_id` map shape.
- Produces (used by Task 16): `normalize(evt: ProtoOAExecutionEvent, symbols_by_id: Mapping[int, SymbolInfo]) -> MasterEvent | None` (`None` = not replication-relevant: MARKET ORDER_ACCEPTED, swaps, deposits, unknown symbols → log-only).

Normalization rules (exact):
- `ORDER_REJECTED` → `MasterRejected(reason=<errorCode or executionType name>)`.
- `order.orderType == STOP_LOSS_TAKE_PROFIT` (protection order accepted/replaced/cancelled) → `MasterPositionSLTPAmended(position.positionId, position.stopLoss if set else None, position.takeProfit if set else None)`.
- `ORDER_FILLED`/`ORDER_PARTIAL_FILL` with `deal.closePositionDetail` set → `MasterPositionClosed(deal.positionId, symbol_name, closePositionDetail.closedVolume, position.tradeData.volume as remaining)`.
- `ORDER_FILLED`/`ORDER_PARTIAL_FILL`, no close detail, `order.orderType == MARKET` → `MasterPositionOpened(deal.positionId, symbol_name, side, deal.filledVolume, lot_size, sl/tp from position if set)`.
- `ORDER_FILLED`, `order.orderType in (LIMIT, STOP)` → `MasterPendingFilled(order.orderId, deal.positionId)` — never an open.
- `ORDER_ACCEPTED`, orderType LIMIT/STOP → `MasterPendingPlaced(order.orderId, …, price=limitPrice|stopPrice, expiry_ts_ms=expirationTimestamp if set)`.
- `ORDER_REPLACED`, orderType LIMIT/STOP → `MasterPendingReplaced(...)`.
- `ORDER_CANCELLED`/`ORDER_EXPIRED`, orderType LIMIT/STOP → `MasterPendingCancelled(order.orderId)`.

- [ ] **Step 1: Write the failing tests** — build raw protobufs with a helper:

```python
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType, ProtoOATradeSide)

from copier.domain import models as m
from copier.engine.normalize import normalize

SYMS = {1: m.SymbolInfo(1, "EURUSD", 5, 10_000_000, 100_000, 100_000)}


def base_event(execution_type, order_type=ProtoOAOrderType.MARKET, order_id=5,
               position_id=11, volume=10_000_000):
    e = ProtoOAExecutionEvent()
    e.ctidTraderAccountId = 100
    e.executionType = execution_type
    e.order.orderId = order_id
    e.order.orderType = order_type
    e.order.tradeData.symbolId = 1
    e.order.tradeData.tradeSide = ProtoOATradeSide.BUY
    e.order.tradeData.volume = volume
    e.position.positionId = position_id
    e.position.tradeData.volume = volume
    return e


def test_market_fill_opens_position():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED)
    e.deal.positionId = 11
    e.deal.filledVolume = 10_000_000
    out = normalize(e, SYMS)
    assert out == m.MasterPositionOpened(11, "EURUSD", m.Side.BUY, 10_000_000,
                                         10_000_000, None, None)


def test_fill_with_close_detail_is_a_close():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED)
    e.deal.positionId = 11
    e.deal.filledVolume = 4_000_000
    e.deal.closePositionDetail.closedVolume = 4_000_000
    e.position.tradeData.volume = 6_000_000     # remaining
    out = normalize(e, SYMS)
    assert out == m.MasterPositionClosed(11, "EURUSD", 4_000_000, 6_000_000)


def test_limit_accept_is_pending_placed():
    e = base_event(ProtoOAExecutionType.ORDER_ACCEPTED, ProtoOAOrderType.LIMIT, order_id=42)
    e.order.limitPrice = 1.115
    out = normalize(e, SYMS)
    assert isinstance(out, m.MasterPendingPlaced)
    assert (out.order_id, out.order_type, out.price) == (42, m.PendingType.LIMIT, 1.115)


def test_market_accept_is_ignored():
    assert normalize(base_event(ProtoOAExecutionType.ORDER_ACCEPTED), SYMS) is None


def test_limit_fill_is_pending_filled_not_open():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED, ProtoOAOrderType.LIMIT, order_id=42)
    e.deal.positionId = 77
    e.deal.filledVolume = 10_000_000
    assert normalize(e, SYMS) == m.MasterPendingFilled(42, 77)


def test_replace_cancel_expire_pending():
    rep = base_event(ProtoOAExecutionType.ORDER_REPLACED, ProtoOAOrderType.STOP, order_id=42)
    rep.order.stopPrice = 1.2
    out = normalize(rep, SYMS)
    assert isinstance(out, m.MasterPendingReplaced) and out.price == 1.2
    can = base_event(ProtoOAExecutionType.ORDER_CANCELLED, ProtoOAOrderType.LIMIT, order_id=42)
    assert normalize(can, SYMS) == m.MasterPendingCancelled(42)
    exp = base_event(ProtoOAExecutionType.ORDER_EXPIRED, ProtoOAOrderType.STOP, order_id=42)
    assert normalize(exp, SYMS) == m.MasterPendingCancelled(42)


def test_protection_order_is_sltp_amend():
    e = base_event(ProtoOAExecutionType.ORDER_ACCEPTED,
                   ProtoOAOrderType.STOP_LOSS_TAKE_PROFIT)
    e.position.stopLoss = 1.05
    out = normalize(e, SYMS)
    assert out == m.MasterPositionSLTPAmended(11, 1.05, None)


def test_rejection_normalizes_to_rejected():
    e = base_event(ProtoOAExecutionType.ORDER_REJECTED)
    assert isinstance(normalize(e, SYMS), m.MasterRejected)


def test_unknown_symbol_returns_none():
    e = base_event(ProtoOAExecutionType.ORDER_FILLED)
    e.order.tradeData.symbolId = 999
    e.deal.positionId = 11
    e.deal.filledVolume = 1
    assert normalize(e, {}) is None
```

- [ ] **Step 2: Run to verify failure**, **Step 3: Implement `normalize.py`** exactly per the rules table (a single function with early returns; extract `side` via `ProtoOATradeSide`, optional fields via `HasField`), **Step 4: Run to verify 9 PASS.**

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/normalize.py copier/tests/unit/test_normalize.py
git commit -m "feat: ProtoOAExecutionEvent -> MasterEvent normalizer"
```

### Task 14: Repository (`repo.py`) — mappings, events + pg_notify, settings, accounts

**Files:**
- Create: `copier/src/copier/db/__init__.py` (empty), `copier/src/copier/db/repo.py`
- Test: `copier/tests/unit/test_repo.py`

**Interfaces:**
- Consumes: Task 2 schema + fixtures, Task 4 models.
- Produces (used by Tasks 15–20; `Repo` also satisfies the `MappingState` protocol):
  - `Settings(copying_enabled: bool, dry_run: bool, shards: int)`; `AccountRow(account_id, connection_id, trader_login, is_live, role, enabled, multiplier: Decimal, status, last_error)`
  - `Repo(dsn: str)` with:
    - events: `log_event(category, severity, payload: dict, account_id=None, latency_ms=None) -> int`
    - settings: `get_settings() -> Settings`, `set_setting(name: str, value) -> None`
    - accounts: `load_accounts() -> list[AccountRow]`, `set_account_status(account_id, status, last_error=None)`, `upsert_account(account_id, connection_id, trader_login, is_live)`
    - symbol cache: `save_symbol_cache(account_id, infos: dict[str, SymbolInfo])`, `load_symbol_cache(account_id) -> dict[str, SymbolInfo]`
    - mappings: `create_position_mapping(master_position_id, slave_account_id, client_order_id)`, `activate_position_mapping(client_order_id, slave_position_id, slave_volume)`, `reduce_position_mapping(slave_account_id, slave_position_id, closed_volume)` (volume 0 ⇒ status `closed`), `fail_mapping(client_order_id, error)`, `create_order_mapping(master_order_id, slave_account_id, client_order_id)`, `activate_order_mapping(client_order_id, slave_order_id)`, `close_order_mapping(slave_account_id, slave_order_id)`, `link_pending_fill(master_order_id, slave_account_id, master_position_id)` (stamps `master_position_id` onto the order-mapping row), `activate_pending_fill(slave_account_id, slave_order_id, slave_position_id, slave_volume)`, `adopt_position_mapping(master_position_id, slave_account_id, slave_position_id, slave_volume)` (drift remedy), `mapping_rows() -> list[dict]`
    - MappingState: `position_entries(master_position_id)`, `order_entries(master_order_id)` (both return only `status='active'` rows; position entries require `slave_position_id IS NOT NULL`)

- [ ] **Step 1: Write the failing tests** (uses the `db` fixture; seed one connection + accounts 100/101 first):

```python
# key cases (write all):
def test_log_event_writes_row_and_notifies(db): ...        # LISTEN events; payload JSONB roundtrip
def test_settings_roundtrip(db): ...                       # get -> set_setting("dry_run", True) -> get
def test_position_mapping_lifecycle(db):
    # create (pending, client_order_id "cm11.101") -> position_entries(11) == []
    # activate("cm11.101", 555, 10_000_000) -> entries == [PositionMappingEntry(101, 555, 10_000_000)]
    # reduce(101, 555, 4_000_000) -> slave_volume 6_000_000 still active
    # reduce(101, 555, 6_000_000) -> closed, entries == []
def test_fail_mapping_records_error(db): ...
def test_order_mapping_and_pending_fill_link(db):
    # create order mapping "co42.101" -> activate_order_mapping -> order_entries(42) == [(101, 900)]
    # link_pending_fill(42, 101, 77) then activate_pending_fill(101, 900, 555, 1_000_000)
    # -> position_entries(77) == [PositionMappingEntry(101, 555, 1_000_000)]
def test_upsert_and_load_accounts(db): ...                 # multiplier comes back as Decimal
def test_symbol_cache_roundtrip(db): ...                   # SymbolInfo dataclasses in/out
```

- [ ] **Step 2: Run to verify failure**, **Step 3: Implement `Repo`** (plain psycopg, one connection per call, `autocommit=True`; `log_event` uses `psycopg.types.json.Jsonb(payload)`; every mapping mutation sets `updated_at = now()`), **Step 4: Run to verify PASS.**

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/db/ copier/tests/unit/test_repo.py
git commit -m "feat: repo for mappings, audit events with pg_notify, settings, accounts"
```

### Task 15: Intent dispatcher — proto builders, throttle, retry ×3, degraded, dry-run, kill switch

**Files:**
- Create: `copier/src/copier/engine/dispatch.py`
- Test: `copier/tests/unit/test_dispatch.py`

**Interfaces:**
- Consumes: Tasks 3–6, 12, 14; protobuf trade requests.
- Produces (used by Tasks 16–19):
  - `RETRY_DELAYS = (1.0, 2.0, 4.0)`
  - `client_order_id_for(intent) -> str | None` — `OpenMarket` → `f"cm{master_position_id}.{slave_account_id}"`, `PlacePending` → `f"co{master_order_id}.{slave_account_id}"`, else `None` (both ≤ 50 chars).
  - `build_request(intent: SlaveIntent) -> tuple[int, google.protobuf.Message]` — returns `(slave_account_id, req)`:
    - `OpenMarket` → `ProtoOANewOrderReq` (MARKET, `tradeSide`, `volume`, optional `stopLoss`/`takeProfit`, `label`, `clientOrderId`)
    - `ClosePosition` → `ProtoOAClosePositionReq(positionId, volume)` (partial close supported)
    - `AmendPositionSLTP` → `ProtoOAAmendPositionSLTPReq` (sets fields only when not `None`)
    - `PlacePending` → `ProtoOANewOrderReq` (LIMIT→`limitPrice` / STOP→`stopPrice`, optional `expirationTimestamp`, `label`, `clientOrderId`)
    - `AmendPending` → `ProtoOAAmendOrderReq(orderId, volume, limitPrice|stopPrice, sl/tp)`
    - `CancelPending` → `ProtoOACancelOrderReq(orderId)`
  - `Dispatcher(send_for_account: Callable[[int, Message], Deferred], repo: Repo, bucket: TokenBucket, clock=None)` with `dispatch(intents: Sequence[SlaveIntent]) -> None`.

Dispatch behavior contract:
- `Alert` → `repo.log_event('slave_action', 'warning', {"message": ...}, account_id)`; nothing sent.
- `LinkPendingFill` → `repo.link_pending_fill(...)` + info event; nothing sent.
- `get_settings().copying_enabled is False` (kill switch) → log `{"skipped": "kill_switch"}` info event per intent; nothing sent, no mapping rows.
- Otherwise: for `OpenMarket`/`PlacePending` first `create_position_mapping`/`create_order_mapping` with `client_order_id_for(intent)`.
- `dry_run` → log `{"dry_run": true, "would_send": <request summary>}` info event; **nothing sent** (mapping rows stay `pending` and are cleaned by resync — log this).
- Live send: `bucket.acquire()` → `send_for_account(account_id, req)`; on errback retry after `RETRY_DELAYS[attempt]` (attempts 0,1,2 → 3 retries total); after the 4th failure `repo.set_account_status(account_id, 'degraded', error)` + error event. Failures never affect other intents.

- [ ] **Step 1: Write the failing tests** (recording `send_for_account` that returns `defer.succeed`/`defer.fail` per script; `Clock` for retries; real `Repo` on the `db` fixture):

```python
# key cases (write all, with real protobuf assertions):
def test_build_open_market_request(): ...       # field-by-field incl. label + clientOrderId
def test_build_partial_close_request(): ...
def test_build_pending_limit_with_expiry(): ...
def test_alert_logs_and_sends_nothing(db): ...
def test_kill_switch_blocks_sends(db): ...      # set_setting("copying_enabled", False)
def test_dry_run_logs_would_send_and_sends_nothing(db): ...
def test_open_market_creates_pending_mapping_then_sends(db): ...
def test_transient_failure_retries_1s_2s_4s_then_degraded(db):
    # send fails 4x -> clock.advance(1); advance(2); advance(4);
    # assert 4 send attempts, account status 'degraded', error event logged
def test_one_slave_failure_does_not_block_others(db): ...
```

- [ ] **Step 2: Run to verify failure**, **Step 3: Implement `dispatch.py`** per the contract, **Step 4: Run to verify PASS.**

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/dispatch.py copier/tests/unit/test_dispatch.py
git commit -m "feat: intent dispatcher with throttle, retry/degraded, dry-run, kill switch"
```

### Task 16: Copier service — master/slave event wiring

**Files:**
- Create: `copier/src/copier/engine/service.py`
- Test: `copier/tests/unit/test_service.py`

**Interfaces:**
- Consumes: Tasks 4–6, 13–15.
- Produces (used by Tasks 18–19):
  - `PENDING_FILL_ALERT_S = 30.0`
  - `CopierService(repo: Repo, dispatcher: Dispatcher, master_account_id: int, master_symbols_by_id: Mapping[int, SymbolInfo], slaves_provider: Callable[[], list[SlaveConfig]], clock=None)` with `handle_execution(account_id: int, evt: ProtoOAExecutionEvent) -> None`.

Behavior contract:
- Master events: log `master_event` (info; latency_ms measured around handling), `normalize(...)`; `None` → done; else `decide(event, repo, slaves_provider())` → `dispatcher.dispatch(intents)`. After dispatching a `MasterPendingFilled`, schedule a check at `PENDING_FILL_ALERT_S`: any linked mapping still without `slave_position_id` → warning event ("slave order not yet filled").
- Slave events (any non-master account) NEVER call `decide` (loop-proof). They only update mappings/logs:
  - `ORDER_FILLED`/`ORDER_PARTIAL_FILL` with `order.clientOrderId` starting `cm` → `activate_position_mapping(clientOrderId, deal.positionId, deal.filledVolume)` + info event (include fill price for slippage display).
  - fill with `closePositionDetail` → `reduce_position_mapping(account_id, deal.positionId, closedVolume)`.
  - `ORDER_ACCEPTED` with `clientOrderId` starting `co` → `activate_order_mapping(clientOrderId, order.orderId)`.
  - fill of a mapped slave pending order (match `order.orderId` via order mappings) → `activate_pending_fill(account_id, order.orderId, deal.positionId, deal.filledVolume)`.
  - `ORDER_CANCELLED` on mapped slave order → `close_order_mapping`.
  - `ORDER_REJECTED` → `fail_mapping(clientOrderId, errorCode)` if any + error event + `set_account_status(account_id, 'degraded', ...)` is **not** set for a plain broker rejection (min-volume/margin) — rejection alerts as error event only (spec: broker rejections surface as alerts).

- [ ] **Step 1: Write the failing tests** (real `Repo` on `db` fixture; `RecordingDispatcher` capturing intents; protobuf builders reused from Task 13's test helper):

```python
# key cases (write all):
def test_master_fill_dispatches_open_intents(db): ...          # 2 slaves -> 2 OpenMarket
def test_slave_events_never_dispatch(db): ...                  # slave fill -> zero intents
def test_slave_fill_activates_mapping(db): ...                 # clientOrderId "cm11.101"
def test_slave_close_reduces_mapping(db): ...
def test_slave_pending_accept_then_fill_links_position(db): ...
def test_slave_rejection_fails_mapping_and_alerts(db): ...
def test_pending_fill_check_alerts_after_30s(db): ...          # Clock.advance(30)
def test_master_event_is_always_audit_logged(db): ...
```

- [ ] **Step 2: Run to verify failure**, **Step 3: Implement `service.py`**, **Step 4: Run to verify PASS.**

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/service.py copier/tests/unit/test_service.py
git commit -m "feat: copier service wiring master events to fan-out, slave events to mappings"
```

### Task 17: Reconciliation and drift (`reconcile.py`)

**Files:**
- Create: `copier/src/copier/engine/reconcile.py`
- Test: `copier/tests/unit/test_reconcile.py`

**Interfaces:**
- Consumes: Tasks 4, 8, 14, 15; `ProtoOAReconcileReq`/`ProtoOAReconcileRes`.
- Produces (used by Tasks 19–20, 23):
  - `PositionSnapshot(position_id: int, symbol_id: int, side: Side, volume: int, price: float, label: str)`; `OrderSnapshot(order_id: int, symbol_id: int, volume: int, label: str)`
  - `compute_drift(master_positions: list[PositionSnapshot], master_orders: list[OrderSnapshot], slave_positions: dict[int, list[PositionSnapshot]], slave_orders: dict[int, list[OrderSnapshot]], mappings: list[dict], enabled_slave_ids: set[int]) -> list[DriftItem]` — **pure**.
  - `DriftItem(id: str, kind: str, account_id: int, position_id: int | None, order_id: int | None, detail: str)`; kinds: `orphan_slave_position` (slave position labeled `copy:*` with no active mapping, or mapping whose master position is gone), `missing_slave_copy` (active mapping, master position open, slave position vanished), `unmapped_master_position` (master position with zero mapping rows — e.g. opened while copier was down; **missed, not replayed**), `unfilled_slave_order` (order mapping linked to a master fill but slave position never materialized).
  - `Reconciler(clients_by_account: Callable[[int], CTraderClient], repo: Repo, dispatcher: Dispatcher, master_account_id: int)` with `run() -> Deferred[list[DriftItem]]` (sends `ProtoOAReconcileReq` per enabled account, snapshots, computes, logs one `drift` event per item, stores `self.current: list[DriftItem]`), and remedies `close_orphan(item_id)` (dispatches full `ClosePosition` — the only drift action that trades, and only on explicit user click), `adopt(item_id, master_position_id)` (`repo.adopt_position_mapping`; for orphans, master id parsed from label `copy:m<id>` when present), `dismiss(item_id)`.

- [ ] **Step 1: Write the failing tests for `compute_drift`** (pure — no server):

```python
# key cases (write all):
def test_no_drift_when_everything_matches(): ...
def test_orphan_labeled_slave_position_without_mapping(): ...
def test_mapping_whose_master_position_is_gone_is_orphan(): ...
def test_slave_position_vanished_is_missing_copy(): ...
def test_master_position_without_mappings_is_unmapped(): ...   # missed-while-down case
def test_linked_order_without_resulting_position_is_unfilled(): ...
def test_disabled_slaves_are_ignored(): ...
def test_drift_item_ids_are_stable(): ...                      # same input -> same ids
```
Plus `Reconciler` tests against the fake server (scriptable `open_positions`) asserting `run()` fills `current` and logs `drift` events, and that `close_orphan` dispatches exactly one full-volume `ClosePosition`.

- [ ] **Step 2–4: fail → implement → pass** (drift is **reported, never auto-traded**: `run()` itself must never call the dispatcher).

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/reconcile.py copier/tests/unit/test_reconcile.py
git commit -m "feat: reconcile + drift report with close/adopt/dismiss remedies"
```

### Task 18: Account state — balance, equity, open P&L (`state.py`)

**Files:**
- Create: `copier/src/copier/engine/state.py`
- Test: `copier/tests/unit/test_state.py`

**Interfaces:**
- Consumes: Tasks 4, 8, 17 snapshots; `ProtoOATraderReq`/`ProtoOATraderRes`, `ProtoOASubscribeSpotsReq`, `ProtoOASpotEvent`.
- Produces (used by Tasks 19, 23, dashboard Overview/Positions):
  - `unrealized_pnl_quote(side: Side, entry_price: float, volume: int, bid: float, ask: float) -> float` — pure: closing price is `bid` for BUY, `ask` for SELL; `units = volume / 100`; BUY pnl `(bid - entry) * units`, SELL pnl `(entry - ask) * units` (quote currency; account-level sum is documented as an approximation — cross-currency conversion is out of scope v1).
  - `AccountStateTracker(master_client: CTraderClient, repo: Repo, master_account_id: int, symbols_by_id: Mapping[int, SymbolInfo])` with `refresh_balances(account_ids: list[int]) -> Deferred` (`ProtoOATraderReq` each; store `trader.balance / 100.0`), `set_positions(account_id, positions: list[PositionSnapshot])` (from reconcile), `ensure_spot_subscriptions() -> Deferred` (`ProtoOASubscribeSpotsReq` on the master connection for every symbol with an open position — quotes are shared across accounts at the same broker), `on_spot(evt)` (wired to `master_client.on_spot`; spot ints scale ÷ 100000), `snapshot() -> dict` — `{account_id: {"balance": float, "open_pnl": float, "equity": float, "positions": [ {position_id, symbol, side, volume, entry_price, pnl_quote}, ... ]}}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_buy_pnl_uses_bid():
    # BUY 1 lot EURUSD (vol 10_000_000 -> 100_000 units) entry 1.1000, bid 1.1050
    assert unrealized_pnl_quote(Side.BUY, 1.1000, 10_000_000, 1.1050, 1.1052) == pytest.approx(500.0)

def test_sell_pnl_uses_ask():
    assert unrealized_pnl_quote(Side.SELL, 1.1000, 10_000_000, 1.0948, 1.0950) == pytest.approx(500.0)

def test_snapshot_equity_is_balance_plus_open_pnl(): ...   # StubSdk-backed client
def test_spot_event_updates_pnl(): ...                     # push ProtoOASpotEvent, scaled /1e5
def test_subscribes_only_open_position_symbols(): ...
```

- [ ] **Step 2–4: fail → implement → pass.**

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/state.py copier/tests/unit/test_state.py
git commit -m "feat: account state tracker with balance, spot-driven open P&L, equity"
```

### Task 19: Control endpoint, token-refresh loop, boot (`control.py`, `main.py`)

**Files:**
- Create: `copier/src/copier/engine/control.py`, `copier/src/copier/main.py`
- Test: `copier/tests/unit/test_control.py`

**Interfaces:**
- Consumes: everything in Phases 3–5.
- Produces:
  - `CopierApp` (in `main.py`) — the composition root. Fields: `repo`, `token_store`, `clients: dict[bool, CTraderClient]` (key `is_live`; built lazily per environment actually needed; `SHARDS` env sharding is a knob: shard slave accounts across N clients per environment, default 1, each with its own `TokenBucket`), `service`, `reconciler`, `state_tracker`. Methods: `startup() -> Deferred` (load accounts → connect/auth per environment → fetch+cache symbol maps → build `slaves_provider` from DB rows + symbol cache → wire `service.handle_execution` to every client's `on_execution` → `reconciler.run()` → `state_tracker.refresh_balances(...)` + `ensure_spot_subscriptions()`), `reload() -> Deferred` (re-read accounts/settings, authorize newly-added accounts, deauthorize removed ones, refresh master routing — must tolerate zero accounts at boot), `pause(account_id | None)` / `resume(account_id | None)` (global → `set_setting("copying_enabled", ...)`; per-slave → `set_account_status(account_id, 'paused'|'ok')`; both then reload; every call logs a `control` event), `set_dry_run(enabled)`, `resync() -> Deferred` (reconciler.run), `discover(connection_id) -> Deferred` (`ProtoOAGetAccountListByAccessTokenReq` with the connection's access token → `repo.upsert_account` each), `refresh_due_tokens() -> Deferred` (for each `token_store.due_for_refresh(now)`: send `ProtoOARefreshTokenReq(refreshToken=...)` → on response `token_store.rotate(...)` **before** anything else touches the token; on failure `token_store.mark(cid, 'refresh_failed')` + error `auth` event) — scheduled daily via `LoopingCall(86400)`; also invoked immediately when `on_tokens_invalidated` fires.
  - `control.py`: `make_control_site(app: CopierApp) -> twisted.web.server.Site` — JSON-over-HTTP on the Docker-internal network only (`reactor.listenTCP(8080, site)` in main; never published in compose). Routes:
    - `GET /health` → `{"status": "ok", "master": <id|null>, "copying_enabled": bool, "dry_run": bool}`
    - `GET /state` → `{"accounts": state_tracker.snapshot(), "master_positions": [... incl. per-position "copies": mapping rows with slave fill price/status/error ...], "pending_orders": [...], "drift": [DriftItem as dict]}`
    - `POST /pause`, `POST /resume` (JSON body `{"account_id": int | null}`), `POST /resync`, `POST /reload`, `POST /dry-run` (`{"enabled": bool}`), `POST /discover` (`{"connection_id": int}`), `POST /drift/close-orphan` / `/drift/adopt` / `/drift/dismiss` (`{"id": str, "master_position_id": int?}`)
  - `main.py` `main()`: read env (`POSTGRES_DSN`, `FERNET_KEY`, `CTRADER_CLIENT_ID/SECRET`, `CTRADER_DEMO_HOST`, `CTRADER_LIVE_HOST`, `CTRADER_PORT`, `SHARDS`), build `CopierApp`, `reactor.callWhenRunning(app.startup)`, `reactor.listenTCP(8080, make_control_site(app))`, `reactor.run()`.

- [ ] **Step 1: Write the failing tests** — exercise the control site with `twisted.web.test.requesthelper` or by `reactor.listenTCP(0, site)` + `twisted.web.client.Agent`; back `CopierApp` with StubSdk clients and the `db` fixture:

```python
# key cases (write all):
def test_health_reports_settings(db): ...
def test_pause_global_flips_kill_switch_and_logs_control_event(db): ...
def test_pause_single_slave_sets_paused_status(db): ...
def test_dry_run_toggle_persists(db): ...
def test_discover_upserts_accounts_from_token(db): ...        # fake server GetAccountList
def test_refresh_due_tokens_rotates_and_persists(db):
    # token store row expiring in 10 days; fake ProtoOARefreshTokenRes ("a2","r2")
    # -> store.get(cid).refresh_token == "r2", status active
def test_refresh_failure_marks_and_alerts(db): ...
def test_tokens_invalidated_event_triggers_refresh(db): ...
def test_startup_with_zero_accounts_does_not_crash(db): ...
```

- [ ] **Step 2–4: fail → implement → pass.** Then verify the container boots cleanly: `docker compose up --build copier` (no accounts yet: it must idle, control port answering `GET /health` from inside the network — check with `docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://copier:8080/health').read())"` once api exists, or `docker compose run --rm migrate python -c ...`).

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/engine/control.py copier/src/copier/main.py copier/tests/unit/test_control.py
git commit -m "feat: copier control endpoint, token refresh loop, boot sequence"
```

### Task 20: Copier end-to-end integration vs fake server

**Files:**
- Test: `copier/tests/integration/test_copier_e2e.py`

**Interfaces:**
- Consumes: the complete copier (`CopierApp` against `FakeCTraderServer`), Task 2 fixtures.
- Produces: the executable proof of spec §5 — this is the gate for Phase 6.

- [ ] **Step 1: Write the tests** (fixture: fake server with accounts 100 (master) + 101/102 (slaves); DB seeded with one connection, roles, multipliers 1.0 and 0.5; `CopierApp` pointed at `127.0.0.1:<fake port>`; `wait_until` helper):

```python
# scenario list (write real bodies):
def test_master_fill_fans_out_and_maps():
    # push master ORDER_FILLED 1.00 lot EURUSD ->
    # fake server received exactly 2 ProtoOANewOrderReq:
    #   volumes 10_000_000 and 5_000_000, labels "copy:m<pid>", MARKET, BUY
    # auto-fill replies -> both mappings become active with slave_position_id set
    # events table contains master_event + 2 slave_action rows

def test_partial_close_closes_same_fraction():
    # after fills, push master close of half -> 2 ProtoOAClosePositionReq
    # with half of each slave's volume; mappings' slave_volume reduced

def test_sltp_amend_propagates(): ...
def test_pending_order_lifecycle():
    # LIMIT accepted -> 2 ProtoOANewOrderReq LIMIT; replace -> 2 ProtoOAAmendOrderReq;
    # cancel -> 2 ProtoOACancelOrderReq; mappings tracked throughout

def test_master_rejection_is_noop(): ...           # zero requests, one master_event log
def test_dry_run_sends_nothing_but_logs(): ...
def test_kill_switch_blocks_fanout(): ...
def test_reconnect_reauths_and_resyncs():
    # drop_all_connections -> wait for re-auth; drift report generated
```

- [ ] **Step 2: Run — fix any wiring defects until green** (`cd copier && .venv/bin/pytest tests/integration/test_copier_e2e.py -v`, timeout 120s).

- [ ] **Step 3: Run the entire copier suite**

Run: `cd copier && .venv/bin/pytest tests -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add copier/tests/integration/test_copier_e2e.py copier/src
git commit -m "test: copier end-to-end replication against fake cTrader server"
```

---

## Phase 6: API service (FastAPI)

REST + WebSocket + OAuth + admin auth + static dashboard serving. The api never talks to cTrader directly except the OAuth token exchange; trading control is proxied to copier's internal endpoint. api being down never affects copying.

### Task 21: FastAPI skeleton + admin auth (argon2, session cookie, CSRF, rate limit)

**Files:**
- Create: `api/src/api/config.py`, `api/src/api/db.py`, `api/src/api/auth.py`, `api/src/api/main.py`
- Create: `api/tests/conftest.py`
- Test: `api/tests/test_auth.py`

**Interfaces:**
- Consumes: Task 2 schema (`admin` table) + the same Postgres test fixtures (copy `copier/tests/conftest.py`'s `database`/`db` fixtures into `api/tests/conftest.py`, plus an `app_client(db)` fixture yielding a `TestClient` with env `POSTGRES_DSN=db`, `SESSION_SECRET="test-secret"`, `ADMIN_BOOTSTRAP_PASSWORD="hunter2!"`, `COPIER_CONTROL_URL="http://copier.test"`, and an injectable `httpx.MockTransport`).
- Produces (used by Tasks 22–24):
  - `config.py`: `ApiConfig.from_env()` dataclass with every env var the api uses.
  - `db.py`: `get_conn(cfg) -> psycopg.Connection` (per-request dependency, autocommit).
  - `auth.py`: `hash_password(pw) -> str` / `verify_password(hash, pw) -> bool` (argon2-cffi); `ensure_admin(dsn, bootstrap_password)` (insert argon2 hash if `admin` empty — called in lifespan); session serializer (`itsdangerous.URLSafeTimedSerializer(secret, salt="session")`, max age 12 h); `require_admin` dependency (reads `session` cookie → 401 if absent/invalid); `LoginRateLimiter(max_attempts=5, window_s=60)` per client IP; `CSRFMiddleware` — mutating methods except `/api/login` require header `X-CSRF-Token` equal to the `csrf` cookie, else 403; router: `POST /api/login {password}` → 204 + `session` cookie (HttpOnly, SameSite=Lax) + `csrf` cookie (readable), 401 on bad password, 429 over limit; `POST /api/logout` → clears cookies; `GET /api/me` → `{"authenticated": true}` (or 401).
  - `main.py`: `create_app() -> FastAPI` (lifespan: `ensure_admin`, `httpx.AsyncClient` on `app.state.http`; includes auth router; CSRF middleware) and module-level `app = create_app()` for uvicorn.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/test_auth.py — key cases (write all):
def test_login_wrong_password_401(app_client): ...
def test_login_sets_session_and_csrf_cookies(app_client): ...
def test_admin_password_stored_as_argon2(app_client, db): ...   # hash starts "$argon2"
def test_protected_route_401_without_session(app_client): ...   # GET /api/me
def test_mutation_without_csrf_header_403(app_client): ...
def test_mutation_with_csrf_header_ok(app_client): ...
def test_sixth_login_attempt_within_minute_429(app_client): ...
def test_logout_clears_session(app_client): ...
```

- [ ] **Step 2: Run to verify failure** (`cd api && .venv/bin/pytest tests/test_auth.py -v`), **Step 3: Implement**, **Step 4: Run to verify PASS.**

- [ ] **Step 5: Commit**

```bash
git add api/src/api/ api/tests/
git commit -m "feat: api skeleton with argon2 admin auth, sessions, CSRF, rate limit"
```

### Task 22: OAuth connect flow for cTrader IDs

**Files:**
- Create: `api/src/api/oauth.py`
- Modify: `api/src/api/main.py` (include router)
- Test: `api/tests/test_oauth.py`

**Interfaces:**
- Consumes: Task 7 semantics (same `ctid_connections` columns, Fernet key), Task 19 `POST /discover`, Task 21 auth.
- Produces:
  - `GET /api/oauth/connect` (admin-only) → 307 redirect to `{CTRADER_AUTH_URL}?client_id=...&redirect_uri={CTRADER_REDIRECT_URI}&scope=trading&state=<signed state>`.
  - `GET /api/oauth/callback?code=...&state=...` (admin-only) → verify signed state (403 otherwise) → `POST {CTRADER_TOKEN_URL}` with `grant_type=authorization_code, code, redirect_uri, client_id, client_secret` → parse `accessToken`/`refreshToken`/`expiresIn` (accept snake_case fallbacks) → insert `ctid_connections` row with Fernet-encrypted tokens and `expires_at = now + expiresIn` → `POST {COPIER_CONTROL_URL}/discover {"connection_id": id}` (best-effort; failure → redirect with `?warning=discover_failed`) → 307 redirect to `/accounts?connected=1`.

- [ ] **Step 1: Write the failing tests** (route the app's `httpx` client through `MockTransport` handling both the token URL and the copier discover URL):

```python
# key cases (write all):
def test_connect_redirects_to_ctrader_with_scope_trading(app_client): ...
def test_callback_rejects_bad_state(app_client): ...
def test_callback_exchanges_code_and_stores_encrypted_tokens(app_client, db):
    # MockTransport returns {"accessToken": "at", "refreshToken": "rt", "expiresIn": 2592000}
    # -> ctid_connections row exists; Fernet-decrypt roundtrips; discover was POSTed
def test_callback_discover_failure_still_stores_grant(app_client, db): ...
def test_oauth_routes_require_admin(app_client): ...
```

- [ ] **Step 2–4: fail → implement → pass.**

- [ ] **Step 5: Commit**

```bash
git add api/src/api/oauth.py api/src/api/main.py api/tests/test_oauth.py
git commit -m "feat: cTrader ID OAuth connect flow with encrypted token storage"
```

### Task 23: REST — accounts, settings, control proxy

**Files:**
- Create: `api/src/api/routes/__init__.py` (empty), `api/src/api/routes/accounts.py`, `api/src/api/routes/settings_control.py`, `api/src/api/routes/state.py`
- Modify: `api/src/api/main.py` (include routers)
- Test: `api/tests/test_accounts.py`, `api/tests/test_settings_control.py`

**Interfaces:**
- Consumes: Tasks 2, 19 (control routes), 21. All routes admin-only; JSON is snake_case throughout.
- Produces:
  - `GET /api/accounts` → rows of `accounts` joined with connection status: `[{ctid_trader_account_id, trader_login, is_live, role, enabled, multiplier, status, last_error, connection_status}]`.
  - `PATCH /api/accounts/{id}` body any of `{role, multiplier, enabled}` → validates (`multiplier > 0`; role in master/slave/ignored); setting a second master → **409** `{"detail": "a master already exists"}` (DB unique partial index is the enforcement; catch `UniqueViolation`); on success POST `{COPIER_CONTROL_URL}/reload`; copier unreachable → still 200 with `{"copier_reloaded": false}`.
  - `DELETE /api/accounts/connections/{connection_id}` → delete `ctid_connections` row (cascades accounts) — the dashboard "disconnect" (tokens remain revocable at ctrader.com; note in response).
  - `GET /api/settings` → `{copying_enabled, dry_run, shards}`; `PUT /api/settings` (any subset) → write `settings` + POST copier `/reload` (and `/dry-run` when `dry_run` changes).
  - `POST /api/control/pause` / `resume` (body `{"account_id": int | null}`), `POST /api/control/resync` → straight proxies to copier; copier unreachable → **502** `{"detail": "copier unreachable"}` (control actions must not silently no-op).
  - `GET /api/state` → proxy of copier `GET /state`; `POST /api/drift/{action}` (`close-orphan`|`adopt`|`dismiss`) → proxy.

- [ ] **Step 1: Write the failing tests** (MockTransport records copier calls; seed accounts via SQL):

```python
# key cases (write all):
def test_list_accounts(app_client, db): ...
def test_patch_multiplier_and_enabled(app_client, db): ...
def test_second_master_409(app_client, db): ...
def test_role_change_triggers_copier_reload(app_client, db): ...
def test_settings_kill_switch_roundtrip(app_client, db): ...
def test_control_pause_proxies_to_copier(app_client): ...
def test_control_502_when_copier_down(app_client): ...
def test_state_proxy_passthrough(app_client): ...
def test_drift_action_proxy(app_client): ...
```

- [ ] **Step 2–4: fail → implement → pass.**

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/ api/src/api/main.py api/tests/
git commit -m "feat: accounts/settings/control REST with one-master enforcement"
```

### Task 24: Events REST + WebSocket live feed + static serving

**Files:**
- Create: `api/src/api/routes/events.py`, `api/src/api/ws.py`
- Modify: `api/src/api/main.py` (lifespan listener task, routers, static mount)
- Test: `api/tests/test_events_ws.py`

**Interfaces:**
- Consumes: Task 2 `events` table + `pg_notify('events', id)`; Task 21 auth.
- Produces:
  - `GET /api/events?account_id=&severity=&category=&since=&limit=` (defaults `limit=200`, newest first) → `[{id, ts, account_id, category, severity, latency_ms, payload}]`.
  - `ws.py`: `EventBroadcaster` — lifespan task opens `psycopg.AsyncConnection` (autocommit), `LISTEN events`, `async for n in conn.notifies():` fetch that row, `broadcast(json)` to registered sockets; `WS /api/ws` — rejects handshakes without a valid session cookie (close code 4401), then streams each new event as JSON.
  - Static: if `STATIC_DIR` env exists, mount `StaticFiles(directory=STATIC_DIR, html=True)` at `/` plus a catch-all GET returning `index.html` for non-`/api` paths (SPA routing).

- [ ] **Step 1: Write the failing tests**

```python
# key cases (write all):
def test_events_filtering_by_severity_and_category(app_client, db): ...
def test_events_since_and_limit(app_client, db): ...
def test_ws_rejects_unauthenticated(app_client): ...
def test_ws_streams_inserted_event(app_client, db):
    # with app_client.websocket_connect("/api/ws") as ws:
    #     insert an events row via SQL -> ws.receive_json()["category"] == "control"
def test_spa_fallback_serves_index_html(app_client, tmp_path): ...
```

- [ ] **Step 2–4: fail → implement → pass.** Then run the whole api suite: `cd api && .venv/bin/pytest tests -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/ api/tests/
git commit -m "feat: events REST, WebSocket live feed via LISTEN/NOTIFY, static serving"
```

---

## Phase 7: React dashboard

Vite + React 18 + TypeScript + Tailwind v4. Screens per spec §7. Tests: Vitest + Testing Library + jsdom with stubbed `fetch`/`WebSocket`. Dev flow: `npm run dev` proxies `/api` (incl. WS) to `localhost:8000`; production build is served by api.

### Task 25: Dashboard scaffold, api client, login, layout

**Files:**
- Create: `dashboard/package.json`, `dashboard/vite.config.ts`, `dashboard/tsconfig.json`, `dashboard/index.html`, `dashboard/src/main.tsx`, `dashboard/src/index.css`, `dashboard/src/App.tsx`, `dashboard/src/test-setup.ts`, `dashboard/src/lib/api.ts`, `dashboard/src/lib/types.ts`, `dashboard/src/components/Layout.tsx`, `dashboard/src/pages/Login.tsx`
- Modify: `api/Dockerfile` (add the node build stage from Task 1's note: `FROM node:22-slim AS dashboard` → `npm ci` → `npm run build` → final stage `COPY --from=dashboard /dash/dist /app/static` + `ENV STATIC_DIR=/app/static`)
- Test: `dashboard/src/lib/api.test.ts`, `dashboard/src/pages/Login.test.tsx`

**Interfaces:**
- Consumes: api routes from Phase 6.
- Produces (used by Tasks 26–29):
  - `lib/api.ts`: `api<T>(path, init?): Promise<T>` — JSON fetch, `credentials: 'same-origin'`, adds `X-CSRF-Token` from the `csrf` cookie on every request, redirects to `/login` on 401, throws on non-2xx; `eventsSocket(): WebSocket` for `/api/ws`.
  - `lib/types.ts` (snake_case, mirrors api JSON): `Account`, `Settings`, `EventRow`, `DriftItem`, `StateSnapshot` (`{accounts: Record<string, AccountState>, master_positions: MasterPosition[], pending_orders: PendingOrder[], drift: DriftItem[]}`), `MasterPosition` (`{position_id, symbol, side, volume, entry_price, pnl_quote, copies: SlaveCopy[]}`), `SlaveCopy` (`{slave_account_id, status, slave_position_id, slave_volume, fill_price, error}`).
  - `App.tsx`: React Router — `/login` public; `/`, `/accounts`, `/positions`, `/logs` inside `Layout` (nav sidebar + page outlet).
  - Vite config: `server.proxy = {'/api': {target: 'http://localhost:8000', ws: true}}`; vitest `environment: 'jsdom'`, `setupFiles: './src/test-setup.ts'` (imports `@testing-library/jest-dom`).

- [ ] **Step 1: Write the failing tests**

`dashboard/src/lib/api.test.ts`:
```ts
import { afterEach, expect, test, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

test('api attaches CSRF header from cookie', async () => {
  document.cookie = 'csrf=tok123'
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  await api('/api/settings', { method: 'PUT', body: JSON.stringify({ dry_run: true }) })
  const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
  expect(headers['X-CSRF-Token']).toBe('tok123')
})

test('api throws on non-2xx', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('boom', { status: 500 })))
  await expect(api('/api/accounts')).rejects.toThrow('500')
})
```

`dashboard/src/pages/Login.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import Login from './Login'

test('submits password to /api/login', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  render(<MemoryRouter><Login /></MemoryRouter>)
  await userEvent.type(screen.getByLabelText(/password/i), 'hunter2!')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
  expect(fetchMock.mock.calls[0][0]).toBe('/api/login')
})
```

- [ ] **Step 2: Run to verify failure** — `cd dashboard && npm install && npm test` → module-not-found failures.

- [ ] **Step 3: Implement scaffold + api client + Login + Layout**, update `api/Dockerfile`.

- [ ] **Step 4: Verify** — `npm test` PASS, `npm run build` succeeds, `docker compose build api` succeeds (serves the built dashboard).

- [ ] **Step 5: Commit**

```bash
git add dashboard/ api/Dockerfile
git commit -m "feat: dashboard scaffold with api client, login, layout, static build"
```

### Task 26: Overview screen — status, master card, slave grid, kill switch

**Files:**
- Create: `dashboard/src/pages/Overview.tsx`, `dashboard/src/components/KillSwitch.tsx`
- Test: `dashboard/src/pages/Overview.test.tsx`

**Interfaces:**
- Consumes: `GET /api/accounts`, `GET /api/settings`, `GET /api/state`, `PUT /api/settings`, `POST /api/control/pause|resume`.
- Produces (spec §7.1): environment connection status line (demo/live derived from accounts + copier `/health` via `/api/state`); master card (equity, balance, open P&L); slave grid — one tile per slave: status icon (🟢 ok / ⏸ paused / 🔴 degraded), equity, open position count, per-slave pause/resume button; global **kill switch** — a prominent red "STOP COPYING" (or green "RESUME COPYING") button with a `window.confirm` guard calling `PUT /api/settings {copying_enabled}`; dry-run badge when `dry_run` is on. Poll `/api/state` every 5 s.

- [ ] **Step 1: Write the failing tests**

```tsx
// key cases (write all; stub fetch with route->response map helper `stubApi(routes)`):
test('renders master card with equity/balance/pnl', ...)
test('renders slave tiles with status icons', ...)        // ok, paused, degraded fixtures
test('kill switch confirms then PUTs copying_enabled false', ...)
test('per-slave pause posts to /api/control/pause with account_id', ...)
test('shows dry-run badge when dry_run enabled', ...)
```

- [ ] **Step 2–4: fail → implement → pass.**

- [ ] **Step 5: Commit**

```bash
git add dashboard/src
git commit -m "feat: overview screen with master card, slave grid, kill switch"
```

### Task 27: Accounts screen — OAuth connect, roles, multipliers

**Files:**
- Create: `dashboard/src/pages/Accounts.tsx`
- Test: `dashboard/src/pages/Accounts.test.tsx`

**Interfaces:**
- Consumes: `GET /api/accounts`, `PATCH /api/accounts/{id}`, `DELETE /api/accounts/connections/{id}`, `/api/oauth/connect`.
- Produces (spec §7.2): "Connect cTrader ID" button → `window.open('/api/oauth/connect', 'ctrader-oauth', 'width=520,height=680')` popup; on window focus refetch accounts (callback landed). Account table: login, demo/live badge, role `<select>` (master/slave/ignored — a 409 from PATCH shows "a master already exists" inline), multiplier `<input type="number" step="0.01" min="0.01">` (slaves only; PATCH on blur), enabled toggle, connection status, disconnect (confirm → DELETE) and "Re-grant access" (same popup; spec: accounts created after grant need re-granting).

- [ ] **Step 1: Write the failing tests**

```tsx
// key cases (write all):
test('connect button opens oauth popup', ...)
test('role select PATCHes role', ...)
test('409 on second master shows inline error', ...)
test('multiplier edit PATCHes multiplier', ...)
test('disconnect confirms then DELETEs connection', ...)
```

- [ ] **Step 2–4: fail → implement → pass.**

- [ ] **Step 5: Commit**

```bash
git add dashboard/src
git commit -m "feat: accounts screen with OAuth connect, role assignment, multipliers"
```

### Task 28: Positions screen — master positions, per-slave copy status, drift remedies

**Files:**
- Create: `dashboard/src/pages/Positions.tsx`
- Test: `dashboard/src/pages/Positions.test.tsx`

**Interfaces:**
- Consumes: `GET /api/state`, `POST /api/drift/{action}`.
- Produces (spec §7.3): master open positions table (symbol, side, lots — display `volume / lot-size` is not available client-side, so api's `/state` already provides `volume_lots` strings; entry price, P&L) + pending orders table; each row expandable to per-slave copy status: slave account, status (pending/active/failed), fill price, **slippage vs master** (`fill_price - entry_price`, signed, in points), error text. Drift/orphan panel listing `DriftItem`s with the three one-click remedies (each with confirm): Close orphan → `POST /api/drift/close-orphan {id}`, Adopt → `POST /api/drift/adopt {id, master_position_id}` (prompt pre-filled from label when parseable), Dismiss → `POST /api/drift/dismiss {id}`. Poll every 5 s.

(Note for Task 19/23: include `volume_lots` — `str(protocol_volume_to_lots(volume, lot_size))` — on master positions and copies in copier's `/state` response so the dashboard never does volume math.)

- [ ] **Step 1: Write the failing tests**

```tsx
// key cases (write all):
test('renders master positions with lots and pnl', ...)
test('expanding a row shows per-slave copy status with slippage', ...)
test('failed copy shows error text', ...)
test('drift item close-orphan confirms then POSTs', ...)
test('adopt posts master_position_id', ...)
```

- [ ] **Step 2–4: fail → implement → pass.**

- [ ] **Step 5: Commit**

```bash
git add dashboard/src copier/src/copier/engine/control.py
git commit -m "feat: positions screen with per-slave copy status and drift remedies"
```

### Task 29: Logs screen — filterable audit trail, live via WebSocket

**Files:**
- Create: `dashboard/src/pages/Logs.tsx`
- Test: `dashboard/src/pages/Logs.test.tsx`

**Interfaces:**
- Consumes: `GET /api/events` (+ filters), `eventsSocket()`.
- Produces (spec §7.4): filter bar (account select, severity select, category select, date `since` input) driving `GET /api/events` query params; table of events (ts, account, category, severity color-coded, latency_ms, payload pretty-printed expandable); "Live" toggle (default on) appending rows from the WebSocket as they arrive (respecting active filters client-side); auto-reconnect the socket after 3 s on close.

- [ ] **Step 1: Write the failing tests** (stub `WebSocket` with a class capturing the instance and exposing `emit(json)`):

```tsx
// key cases (write all):
test('fetches events with filter query params', ...)
test('renders severity-coded rows', ...)
test('live websocket event prepends a row', ...)
test('live rows respect current severity filter', ...)
```

- [ ] **Step 2–4: fail → implement → pass.** Then `npm test` (full suite) and `npm run build` green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src
git commit -m "feat: logs screen with filters and live WebSocket feed"
```

---

## Phase 8: Full-stack end-to-end + rollout docs

### Task 30: Compose-level end-to-end test with fake cTrader service

**Files:**
- Create: `docker-compose.test.yml`, `copier/src/copier/testing/fake_main.py`, `e2e/test_full_stack.py`

**Interfaces:**
- Consumes: everything.
- Produces:
  - `fake_main.py`: runs `FakeCTraderServer` (TLS, port 5035, accepts any token, auto_fill, master account 100 + slaves 101/102 known) plus a tiny HTTP scenario-control site on port 9000: `POST /fill {"account_id": 100, "symbol": "EURUSD", "side": "BUY", "volume_lots": "1.00"}` pushes a master ORDER_FILLED execution event; `POST /close`, `POST /place-limit` similarly. Entry: `python -m copier.testing.fake_main`.
  - `docker-compose.test.yml` (override): adds service `fake-ctrader` (build: copier image, command `python -m copier.testing.fake_main`, publishes `127.0.0.1:9000:9000`); overrides copier env `CTRADER_DEMO_HOST=fake-ctrader`; publishes copier control on `127.0.0.1:8081:8080` (test only — never in the base file).
  - `e2e/test_full_stack.py` (pytest, run from the api venv — has httpx + psycopg): brings nothing up itself; asserts against a running stack.

- [ ] **Step 1: Write the e2e test**

```python
# flow (write real code with polling helpers, 60 s deadlines):
# 1. seed: SQL-insert ctid_connection (Fernet tokens using .env FERNET_KEY) +
#    accounts 100 master / 101 slave (x1.0) / 102 slave (x0.5), all demo
# 2. POST 127.0.0.1:8081/reload  (copier picks up accounts, auths vs fake-ctrader)
# 3. login: POST :8000/api/login with ADMIN_BOOTSTRAP_PASSWORD -> session+csrf
# 4. POST 127.0.0.1:9000/fill (1.00 lot EURUSD BUY)
# 5. poll GET :8000/api/events until 2 slave_action fills appear
# 6. assert mappings table: two active rows, volumes 10_000_000 / 5_000_000
# 7. PUT :8000/api/settings {"copying_enabled": false}; POST /fill again;
#    assert no new slave_action within 5 s (kill switch works end-to-end)
# 8. GET :8000/api/state -> master position visible with two copies
```

- [ ] **Step 2: Run the stack and the test**

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build
cd api && .venv/bin/pytest ../e2e/test_full_stack.py -v
docker compose -f docker-compose.yml -f docker-compose.test.yml down
```
Expected: PASS. Fix wiring until green.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.test.yml copier/src/copier/testing/fake_main.py e2e/
git commit -m "test: full-stack e2e via compose with fake cTrader service"
```

### Task 31: README + rollout runbook

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: the finished system.
- Produces: the operator's manual. Required sections (write them fully, no stubs):
  1. **What this is** — one paragraph + the architecture diagram from spec §4.
  2. **Prerequisite: register an Open API app** at https://openapi.ctrader.com — Spotware reviews manually, so start immediately; describe the app honestly as personal copy-trading across your own accounts; you receive `clientId`/`clientSecret`; set the redirect URI to `http://localhost:8000/api/oauth/callback` (update when moving to a VPS).
  3. **Setup** — `cp .env.example .env`; fill `CTRADER_CLIENT_ID/SECRET`, generate `FERNET_KEY` (command included), set `SESSION_SECRET`, `ADMIN_BOOTSTRAP_PASSWORD`, `POSTGRES_PASSWORD`; `docker compose up -d --build`; open http://localhost:8000; log in; Accounts → Connect cTrader ID (OAuth; no broker passwords ever touch this system; revoke anytime at ctrader.com); assign exactly one master, slaves + multipliers.
  4. **Rollout stages (demo-first — do not skip):** Stage 1: dry-run against a demo master — place manual trades in the cTrader platform, verify they appear in Logs and as `dry_run` would-send entries (this also verifies manual platform trades arrive via the API, and checks the §10 unknowns: max accounts per connection, per-app aggregate limits). Stage 2: demo master → 2–3 demo slaves, dry-run off; verify fills, partial closes, SL/TP, pending orders, multipliers. Stage 3: scale to full demo slave count; test the kill switch, per-slave pause, copier restart (expect missed-while-down trades to surface as drift, not replays), reconnect, drift remedies. Stage 4: live accounts — start with the smallest multiplier you can tolerate.
  5. **Operations** — kill switch semantics; degraded slaves; token refresh + re-grant flow (`ProtoOAAccountsTokenInvalidatedEvent` → prominent alert → reconnect the cTrader ID); accounts added under a cTID after granting require re-grant; backup = Postgres volume; logs = Logs screen or `docker compose logs`.
  6. **Development** — per-service test commands, fake-server e2e instructions, repo layout.

- [ ] **Step 1: Write README.md** per the outline above.
- [ ] **Step 2: Verify every command in it** by running them top-to-bottom in a clean checkout (fresh `docker compose down -v` first).
- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with app registration, setup, demo-first rollout runbook"
```

---

## Self-Review (checked against the spec)

- **Spec coverage:** §2 requirements → Tasks 3–6 (sizing/scope), 27 (role assignment), 1 (stack/deploy), 31 (rollout). §3 API facts → Tasks 8–11, 19 (exact message names preserved throughout). §4 architecture → Tasks 1, 19, 21–24. §5 engine → Tasks 5–6 (table rows 1–8), 12 (rate limit), 15–16 (failure handling, retries, degraded), 17 (drift, missed-not-replayed), 19 (token refresh, invalidation). §6 data model → Task 2 (all seven tables; `mappings` gains `slave_volume`/`client_order_id`/`error` columns — required to compute partial-close fractions and correlate fills; an implementation detail, not a design change). §7 dashboard → Tasks 26–29. §8 security → Tasks 1 (env/secrets), 7 (Fernet), 19 (internal-only control port), 21–22 (admin auth, CSRF, rate limit, OAuth-only). §9 testing/rollout → fake server Tasks 9–10, dry-run Tasks 15/20/30, stages Task 31. §10.1 → Task 31 §2. §10.3 verification steps → Task 31 stage 1.
- **Known deliberate scope choices** (documented in tasks, consistent with spec): actions generated only for enabled slaves (spec table: "per enabled slave"); account-level open P&L is a quote-currency approximation (spec doesn't require currency conversion); position increases map to additional mapping entries.
- **Type consistency:** signatures in Interfaces blocks are the single source of truth; later tasks reference exactly `decide(event, mappings, slaves)`, `mirror_volume(master_volume, master_lot_size, multiplier, slave_lot_size, slave_step_volume)`, `TokenBucket.acquire()`, `Repo` method names, and copier control routes as defined.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-13-copy-trading-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task with review between tasks (REQUIRED SUB-SKILL: superpowers:subagent-driven-development).

**2. Inline Execution** — execute tasks in one session with batch checkpoints (REQUIRED SUB-SKILL: superpowers:executing-plans).





