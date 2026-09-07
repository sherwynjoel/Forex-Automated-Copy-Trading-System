# MT5 Bridge 01 — Database and Copier Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the copier an MT5 lane: migrations 014 and 015, the `copier.mt5` package (protocol, symbols, registry, outbox, ingress with the netting ledger, deals, lane), the service/dispatcher/routing/reconciler seams, the `CopierApp` wiring (`mt5_hello` / `mt5_sync` / `mt5_status`, operator actions, `get_state`, offline detection), the three control endpoints, and a Python fake EA (hedging and netting books) with end-to-end tests — so an MT5 account, hedging or netting, copies from and to cTrader accounts through the existing decision engine.

**Architecture:** The decision core (`domain/decision.py`) already turns platform-neutral `MasterEvent`s into `SlaveIntent`s; cTrader is bound only in `dispatch.build_request` (intent → protobuf), `normalize.normalize` (protobuf → event) and the snapshot/query paths. The MT5 lane plugs in at exactly those points: intents whose slave is MT5 go to a Postgres-backed command outbox delivered on the terminal's next 250 ms poll; the terminal's sync reports feed an in-memory registry (positions, balance, symbols), ack the outbox (mapping activation through a platform-neutral `SlaveFill`), and — for an MT5 master — are diffed into `MasterEvent`s by `ingress.py`. Everything else (sizing, mapping bookkeeping, drift, the dashboard shapes) is unchanged. A netting terminal rides the same lane with two additions: as a follower its copies share one net position per symbol (a mapping's close is an opposite-side `open` that names the mapping; a close the terminal makes itself reduces the mappings oldest-first), and as a master its net position is expanded into virtual master positions kept in `mt5_net_ledger`, so followers copy every add and reduction exactly as they copy a hedging master's positions.

**Tech Stack:** Python 3.12, Twisted (reactor/Deferreds, `task.Clock` in tests), psycopg 3, Postgres 16, pytest + pytest-twisted; all copier tests run inside the `copier` Docker image against the compose `postgres` service.

**Spec:** `docs/superpowers/specs/2026-09-07-mt5-bridge-design.md` (approved; the plan argues from it).

**Contract:** `docs/superpowers/plans/2026-09-07-mt5-bridge-interfaces.md` — binding names, shapes and paths for everything that touches the api, dashboard or EA plans. Every public name in this plan is copied from it; private helpers are additions.

## Global Constraints

Copied from the spec — every task's requirements implicitly include these:

- **TDD is mandatory.** Use superpowers:test-driven-development for every task: write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- MT5 terminals can only make **outbound** HTTPS calls (`WebRequest`), and only to allowed URLs. Nothing can push to a terminal. Therefore the terminal **polls**. Polls are **short** (default every 250 ms, 3 s timeout), never long-polls.
- MQL5 has no JSON library. The EA **builds** JSON (easy) but **parses** a line-based, tab-separated response.
- MT5 volumes are lots (doubles). The copier's engine works in integer "protocol units" with a per-symbol `lot_size`. For MT5 symbols the bridge fixes `lot_size = 100` (units are **centilots**), so the existing sizing arithmetic (`mirror_volume`, `partial_close_volume`, `_lots()`) works unchanged. Units are **centilots** everywhere inside the copier for MT5 accounts: `lot_size = 100`, `volume = round(lots * 100)`. Conversions happen only in `protocol.py` (report → centilots) and `outbox.py` (payload lots ← centilots).
- MT5 has no numeric symbol id. The bridge assigns `symbol_id = crc32(name)` per account (collision within one account's list resolved by +1 probing).
- Copy lineage rests on **our mapping table** (position ticket ↔ mapping row) and the EA's magic number, never on the comment. The comment is still set (`copy:m<id>`) as a courtesy.
- MT5 accounts get a **synthetic id** from a sequence starting at 1 000 000 000 000; the MT5 login lives in `mt5_links.login` and is copied into `accounts.trader_login` once known.
- One master per org is a database constraint and stays so. MT5→MT5 copying is out of scope. Netting accounts (`hedging:false` in the hello, `ACCOUNT_MARGIN_MODE_RETAIL_NETTING`) are accepted in both roles and never degraded for their mode; `mt5_links.hedging` records the mode and `mt5_status` reports it.
- `open` and `place_pending` commands not delivered within `OPEN_COMMAND_TTL_S = 30` are failed with "terminal offline" and the mapping marked failed. `close`, `amend`, `cancel` never expire. A `sent` command without an ack is re-delivered after 10 s at most 3 times before failing as "no ack from terminal". Commands are delivered in id order.
- Status line: `OK <server_ms> <next_poll_ms>` (hello adds `<last_deal_ticket>`), `RETRY <server_ms> <next_poll_ms>`, `STOP <server_ms> <reason>`. The STOP form is the contract's, not the spec's: the spec's Wire protocol paragraph writes the shorthand `STOP <reason>`, but contract §2 `encode_response` says "for STOP the third field is the reason text", and the contract wins — every status line carries `<server_ms>` as its second field and a STOP reason is read from the third (Task 2's `encode_response`/`parse_response`, Task 15's STOP answers and the fake EA in Task 17 all emit and parse that order; the real EA must too). Command fields are positional and never contain tabs. Prices `0` means "none".
- `set_account_status` semantics: `ok` while a report arrived within `OFFLINE_AFTER_S = 15`; `degraded` "terminal offline since …" after that (checked by a 5 s timer); `degraded` with the broker reason on a rejected command.
- **Sizing**: MT5 follower volume = `mirror_volume(master_volume, master_lot_size, multiplier, 100, step_centilots)`; below the symbol's min is an `Alert` "mirrored volume rounds to 0".
- **Protection on copies**: SL/TP travel inside the `open` command; the `_protect_new_copy` amend is skipped for a copy that already carries the master's levels. If the master's protection changes before the copy is acked, the pending change is applied on ack.
- **Netting follower**: every copy on a symbol lives inside that symbol's single net position; a mapping keeps its own `slave_volume`, and its side is recorded on its `open` command (`Repo.mapping_side`). A master close for a mapping is queued as an `open` on the opposite side for the mapping's volume, `client_order_id` `<mapping coid>:close`, comment `close:m<master>`; its ack is matched to exactly that mapping and reduces it alone. A close the terminal reports on its own (stop, target, manual close) reduces the mappings sharing the net position oldest-first (`Repo.reduce_position_mappings_fifo`). One stop and one target per symbol: the most recently opened or amended copy sets them, and an info event `mt5_netting_protection_override` records each override. Trade page and Close all act on the net position (`close` on the net ticket) on both modes. A netting close never expires: it is a close whatever its command kind.
- **Netting master**: the net position is expanded into virtual master positions kept in `mt5_net_ledger` (persisted, so a restart keeps them): an `IN` deal opens one whose id is the deal ticket → `MasterPositionOpened`; `OUT` consumes the symbol's virtual positions oldest-first → one `MasterPositionClosed` per consumed position (partial when it survives); `INOUT` closes them all and opens the remainder in the new direction; a stop/target change on the net position → `MasterPositionSLTPAmended` for every virtual position of the symbol. The reconciler and `get_state` see the virtual positions (current price from the net position, P&L split by volume share), so drift and copies line up by virtual id.
- **Balance-after on deals**: computed for the batch backwards from the reported current balance (each deal's `profit + swap + commission` subtracted in reverse order), stored, and marked `balance_after_estimated: true`.
- **Money**: `profit`, `swap`, `commission` are in the account currency as MT5 reports them; `gross_profit` = `profit`.
- **Kill switch**: Close all includes MT5 accounts; each MT5 account's summary reports verified counts from the terminal's reports, never sends.
- **Security**: only sha256 of the key is stored (by the api); the copier never sees a key.
- Commit after every task with the message given in that task, ending with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

### Test commands (this repo runs Python tests inside Docker)

`<repo>` is `C:\Users\Sherwyn joel\OneDrive\Desktop\Forex-Automated-Copy-Trading-System` (in Git Bash: `"/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System"`). Run every command from Git Bash (never PowerShell — it strips the quotes and creates `=8` junk files).

- **PURE** (no database; protocol, symbols, ingress, deals, control routes):
  ```bash
  cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && export MSYS_NO_PATHCONV=1 && docker compose run --rm --no-deps -v "$(pwd -W):/repo" -w /repo/copier --entrypoint sh copier -c 'export PYTHONUSERBASE=/tmp/pyuser; pip install -q --user "pytest>=8" "pytest-twisted>=1.14" "pytest-timeout>=2" >/dev/null 2>&1; export PYTHONPATH=/repo/copier/src; python -m pytest tests/unit/<file>.py -q -p no:cacheprovider'
  ```
- **DB** (every test using the `db` fixture, unit or integration; needs `docker compose up -d postgres` once):
  ```bash
  cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && export MSYS_NO_PATHCONV=1 && docker compose run --rm -v "$(pwd -W):/repo" -w /repo/copier --entrypoint sh copier -c 'export PYTHONUSERBASE=/tmp/pyuser; pip install -q --user "pytest>=8" "pytest-twisted>=1.14" "pytest-timeout>=2" >/dev/null 2>&1; export PYTHONPATH=/repo/copier/src TEST_POSTGRES_ADMIN_DSN="$POSTGRES_DSN" TEST_POSTGRES_DSN="${POSTGRES_DSN%/copytrader}/copytrader_test"; python -m pytest tests/unit/<file>.py -q -p no:cacheprovider'
  ```
  For integration files replace `tests/unit/<file>.py` with `tests/integration/<file>.py`. Below, "Run PURE `tests/unit/x.py`" / "Run DB `tests/unit/x.py`" mean the command above with that path substituted. The whole unit suite (`tests/unit -q`) takes ~9 minutes; run the named files during a task and the full suite at the end of a task that touches shared code (Tasks 4, 10, 11, 12, 15).

## File Structure

New:
- `db/migrations/014_mt5_bridge.sql` — schema (Task 1)
- `db/migrations/015_mt5_net_ledger.sql` — the netting master's ledger table (Task 5)
- `copier/src/copier/mt5/__init__.py`, `protocol.py` — wire format (Task 2)
- `copier/src/copier/mt5/symbols.py` — normalisation, crc32 ids, auto-match (Task 3)
- `copier/src/copier/mt5/registry.py` — `MT5Registry` (Task 6; the netting master's ledger views are added in Task 9, where `NetLedger` exists)
- `copier/src/copier/testing/mt5_fixtures.py` — report builders shared by tests (Task 6)
- `copier/src/copier/mt5/outbox.py` — `MT5Outbox`, `AckOutcome`, `command_for_intent` (Task 7)
- `copier/src/copier/mt5/ingress.py` — `master_events_from_report`, `VirtualPosition`, `NetLedger` (Task 9)
- `copier/src/copier/mt5/deals.py` — deal rows + `balance_after` estimate (Task 13)
- `copier/src/copier/mt5/lane.py` — `MT5Lane` (Task 14)
- `copier/src/copier/testing/fake_ea.py` — `FakeEA`, hedging and netting books (Task 17)
- Tests: `copier/tests/unit/test_migration_014.py`, `test_migration_015.py`, `test_mt5_protocol.py`, `test_mt5_symbols.py`, `test_repo_mt5.py`, `test_mt5_registry.py`, `test_mt5_outbox.py`, `test_dispatch_mt5.py`, `test_mt5_ingress.py`, `test_service_mt5.py`, `test_mt5_deals.py`, `test_mt5_lane.py`, `test_main_mt5.py`, `test_control_mt5.py`; `copier/tests/integration/test_mt5_bridge.py`

Modified:
- `copier/src/copier/db/repo.py` — `AccountRow.platform`, MT5 repo methods, `_deal_row`, `load_deals` filters, `reduce_position_mapping(client_order_id=)`, `mapping_side`, `reduce_position_mappings_fifo` (Task 4); `mt5_net_ledger` accessors (Task 5)
- `copier/src/copier/domain/models.py` — `ClosePosition.master_position_id` (Task 7); `copier/src/copier/domain/decision.py:74` stamps it; `copier/tests/unit/test_decision_positions.py` (four assertions)
- `copier/tests/unit/conftest.py` — `seed_mt5_account` fixture (Task 4)
- `copier/src/copier/engine/reconcile.py` — `OrderSnapshot.stop_loss/take_profit` (Task 6), `snapshot_provider` (Task 12), the netting follower's net-volume comparison — `DriftItem.volume`, `compute_drift(netting_slave_ids=, mapping_sides=)`, `Reconciler(netting_slaves=)` (Task 12b)
- `copier/src/copier/engine/dispatch.py` — MT5 routing before `build_request` (Task 8)
- `copier/src/copier/engine/service.py` — `SlaveFill`, `act_on_master_event`, `handle_slave_*` (Task 10)
- `copier/src/copier/engine/routing.py` — `platform_by_account`, canonical keys (Task 11)
- `copier/src/copier/main.py` — wiring (Task 15)
- `copier/src/copier/engine/control.py` — `/mt5/*` (Task 16)
- `copier/tests/unit/test_routing.py`, `test_reconcile.py`, `test_decision_positions.py`, `test_main.py:453`

---

### Task 1: Migration 014 — MT5 bridge tables

**Files:**
- Create: `db/migrations/014_mt5_bridge.sql`
- Test: `copier/tests/unit/test_migration_014.py`

**Interfaces:**
- Consumes: migrations 001–013 (`accounts`, `orgs`, `mappings`, `deals`); `db/migrate.py:10-26` applies `*.sql` in filename order, once each.
- Produces: `accounts.platform`, nullable `accounts.ctid_connection_id` + CHECK `accounts_platform_link`, sequence `mt5_account_id_seq`, tables `mt5_links`, `mt5_commands` (+ index `mt5_commands_open`), `symbol_aliases`, `mt5_deal_watermark` — exactly as contract §1. Every later task's SQL names these columns.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_migration_014.py`:

```python
"""Migration 014: the MT5 bridge tables and the platform column on accounts.

Schema-level assertions only -- behaviour is covered by the tasks that
write to these tables."""

import psycopg
import pytest


def _columns(db, table):
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s", (table,)).fetchall()
    return {r[0] for r in rows}


def _seed_org_and_connection(conn):
    (org_id,) = conn.execute(
        "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    (connection_id,) = conn.execute(
        "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
        " granted_at, expires_at)"
        " VALUES (%s, 'x', 'y', now(), now() + interval '30 days') RETURNING id",
        (org_id,)).fetchone()
    return org_id, connection_id


def test_migration_014_is_recorded_right_after_013(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "014_mt5_bridge.sql" in names
    assert names.index("014_mt5_bridge.sql") == names.index("013_tradingview_webhook.sql") + 1


@pytest.mark.parametrize("table", [
    "mt5_links", "mt5_commands", "symbol_aliases", "mt5_deal_watermark",
])
def test_table_exists(db, table):
    assert _columns(db, table), f"{table} was not created"


def test_accounts_gained_platform_and_an_optional_ctrader_link(db):
    with psycopg.connect(db, autocommit=True) as conn:
        default, nullable = conn.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'accounts' AND column_name = 'platform'").fetchone()
        assert "ctrader" in default and nullable == "NO"
        (link_nullable,) = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'accounts' AND column_name = 'ctid_connection_id'").fetchone()
    assert link_nullable == "YES"


def test_mt5_account_ids_start_in_the_synthetic_range(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (first,) = conn.execute("SELECT nextval('mt5_account_id_seq')").fetchone()
    assert first >= 1_000_000_000_000


def test_platform_link_check_accepts_ctrader_with_link_and_mt5_without(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, connection_id = _seed_org_and_connection(conn)
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (100, %s, %s, 111, false, 'slave', 'ctrader')",
            (org_id, connection_id))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'slave', 'mt5')",
            (org_id,))
        (n,) = conn.execute("SELECT count(*) FROM accounts").fetchone()
    assert n == 2


def test_platform_link_check_rejects_the_other_two_combinations(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, connection_id = _seed_org_and_connection(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
                " trader_login, is_live, role, platform)"
                " VALUES (101, %s, NULL, 111, false, 'slave', 'ctrader')",
                (org_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
                " trader_login, is_live, role, platform)"
                " VALUES (102, %s, %s, 111, false, 'slave', 'mt5')",
                (org_id, connection_id))


def test_existing_rows_default_to_ctrader(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, connection_id = _seed_org_and_connection(conn)
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role) VALUES (103, %s, %s, 111, false, 'slave')",
            (org_id, connection_id))
        (platform,) = conn.execute(
            "SELECT platform FROM accounts WHERE ctid_trader_account_id = 103").fetchone()
    assert platform == "ctrader"


def test_mt5_tables_cascade_from_accounts(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, _ = _seed_org_and_connection(conn)
        (account_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'slave', 'mt5')"
            " RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, 'h1')",
                     (account_id,))
        conn.execute(
            "INSERT INTO mt5_commands (account_id, org_id, kind, payload)"
            " VALUES (%s, %s, 'close', '{\"position\": 1}'::jsonb)", (account_id, org_id))
        conn.execute(
            "INSERT INTO symbol_aliases (account_id, canonical, broker_name, source)"
            " VALUES (%s, 'XAUUSD', 'XAUUSD.r', 'auto')", (account_id,))
        conn.execute("INSERT INTO mt5_deal_watermark (account_id) VALUES (%s)", (account_id,))
        conn.execute("DELETE FROM accounts WHERE ctid_trader_account_id = %s", (account_id,))
        counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("mt5_links", "mt5_commands", "symbol_aliases", "mt5_deal_watermark")]
    assert counts == [0, 0, 0, 0]


def test_command_kind_and_status_are_constrained(db):
    with psycopg.connect(db, autocommit=True) as conn:
        org_id, _ = _seed_org_and_connection(conn)
        (account_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, platform)"
            " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'slave', 'mt5')"
            " RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO mt5_commands (account_id, org_id, kind, payload)"
                " VALUES (%s, %s, 'teleport', '{}'::jsonb)", (account_id, org_id))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO mt5_commands (account_id, org_id, kind, payload, status)"
                " VALUES (%s, %s, 'close', '{}'::jsonb, 'lost')", (account_id, org_id))
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_migration_014.py`.
Expected: FAIL — `test_migration_014_is_recorded_right_after_013` with `AssertionError` (no `014_mt5_bridge.sql` in `schema_migrations`); every `test_table_exists` case fails with `AssertionError: mt5_links was not created` etc.

- [ ] **Step 3: Write the migration**

Create `db/migrations/014_mt5_bridge.sql`:

```sql
-- MT5 bridge (docs/superpowers/specs/2026-09-07-mt5-bridge-design.md).
--
-- An MT5 account is an accounts row like any other -- same primary key
-- space, same role/enabled/multiplier/status columns -- so every existing
-- query keyed on ctid_trader_account_id works unchanged. What differs is
-- how it connects: there is no cTrader OAuth grant, so ctid_connection_id
-- becomes optional and the platform column says which door the account
-- uses. The CHECK keeps the two in step: a cTrader account always has a
-- link, an MT5 account never does. MT5 logins can collide with cTrader
-- ids across brokers, so MT5 accounts draw a synthetic id from a sequence
-- far above any cTrader id; the real login lives on mt5_links.

ALTER TABLE accounts ADD COLUMN platform TEXT NOT NULL DEFAULT 'ctrader'
    CHECK (platform IN ('ctrader', 'mt5'));
ALTER TABLE accounts ALTER COLUMN ctid_connection_id DROP NOT NULL;
ALTER TABLE accounts ADD CONSTRAINT accounts_platform_link
    CHECK ((platform = 'ctrader') = (ctid_connection_id IS NOT NULL));

CREATE SEQUENCE mt5_account_id_seq START 1000000000000;

-- One row per MT5 account: the key the terminal authenticates with
-- (sha256 only -- the api creates the row and the key, the copier only
-- ever updates the terminal-reported columns) and what the terminal has
-- said about itself.
CREATE TABLE mt5_links (
    account_id     BIGINT PRIMARY KEY REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    key_hash       TEXT NOT NULL UNIQUE,          -- sha256 hex of the key; the key itself is never stored
    key_created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    login          BIGINT,
    broker         TEXT,
    server         TEXT,
    currency       TEXT,
    hedging        BOOLEAN,
    trade_mode     TEXT,                          -- 'demo' | 'contest' | 'real'
    leverage       INTEGER,
    ea_version     TEXT,
    ea_build       INTEGER,
    last_seen_at   TIMESTAMPTZ,
    last_ip        TEXT,
    balance        DOUBLE PRECISION,
    equity         DOUBLE PRECISION,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The command outbox. The terminal polls; nothing can push to it, so
-- every open/close/amend/cancel the copier decides waits here (queued),
-- is handed over on the next poll (sent), and is settled by the
-- terminal's acknowledgement (done/failed). Postgres, not memory, so a
-- copier restart loses nothing that was decided.
CREATE TABLE mt5_commands (
    id              BIGSERIAL PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    org_id          BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('open','close','amend','place_pending','amend_pending','cancel_pending')),
    payload         JSONB NOT NULL,
    client_order_id TEXT,
    status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','done','failed')),
    result          JSONB,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    done_at         TIMESTAMPTZ
);
CREATE INDEX mt5_commands_open ON mt5_commands (account_id, status) WHERE status IN ('queued','sent');

-- canonical (what master events carry, e.g. XAUUSD) -> the MT5 broker's
-- name (e.g. XAUUSD.r), per account. 'auto' rows are the copier's guess
-- from the hello; 'manual' rows are the operator's and are never
-- overwritten by a guess.
CREATE TABLE symbol_aliases (
    account_id  BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    canonical   TEXT NOT NULL,                    -- the name master events carry, e.g. XAUUSD
    broker_name TEXT NOT NULL,                    -- the MT5 broker's name, e.g. XAUUSD.r
    source      TEXT NOT NULL CHECK (source IN ('auto','manual')),
    PRIMARY KEY (account_id, canonical)
);

-- The last deal ticket ingested per account. The terminal reports deals
-- with ticket > watermark; a restarted EA resumes from the server's value
-- (hello response), so nothing is double-ingested and nothing is lost.
CREATE TABLE mt5_deal_watermark (
    account_id        BIGINT PRIMARY KEY REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    last_deal_ticket  BIGINT NOT NULL DEFAULT 0,
    last_deal_time_ms BIGINT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_migration_014.py`.
Expected: `12 passed`.

Then run DB `tests/unit/test_migrations.py` and DB `tests/unit/test_migration_012.py`.
Expected: all pass (the `db` fixture's `TRUNCATE ... accounts ... CASCADE` reaches the four new tables through their foreign keys, so no conftest change is needed).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add db/migrations/014_mt5_bridge.sql copier/tests/unit/test_migration_014.py && git commit -m "feat(db): migration 014 -- MT5 bridge tables and accounts.platform

An MT5 account is an accounts row with platform='mt5' and no cTrader
link; the CHECK keeps platform and ctid_connection_id in step. Adds the
link, command outbox, symbol alias and deal watermark tables the copier's
MT5 lane persists through, and the synthetic id sequence.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `copier.mt5.protocol` — the wire format

**Files:**
- Create: `copier/src/copier/mt5/__init__.py` (empty), `copier/src/copier/mt5/protocol.py`
- Test: `copier/tests/unit/test_mt5_protocol.py`

**Interfaces:**
- Consumes: nothing from the copier (pure module).
- Produces (contract §2 `protocol.py`, used by Tasks 6, 7, 9, 13, 14, 15, 17): constants `MT5_KEY_PREFIX`, `PROTOCOL_VERSION`, `CENTILOTS`, `DEFAULT_POLL_MS`, `RETRY_POLL_MS`, `MAX_DEALS_PER_SYNC`; dataclasses `HelloSymbol`, `HelloReport`, `ReportPosition`, `ReportOrder`, `ReportDeal`, `Ack`, `SyncReport`, `Command`; `ProtocolError(ValueError)`; `centilots(lots) -> int`, `lots(volume: int) -> float`; `parse_hello(body: dict) -> HelloReport`; `parse_sync(body: dict) -> SyncReport`; `encode_response(status, server_ms, next_poll_ms, commands, last_deal_ticket=None, reason=None) -> str` (`reason` is the STOP line's third field, as contract §2 declares it; defaulted); `command_line(cmd: Command) -> str`; `parse_response(text) -> tuple[list[str], list[Command]]`.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_mt5_protocol.py`:

```python
"""The MT5 wire format (copier/src/copier/mt5/protocol.py): JSON reports in,
tab-separated command lines out. Pure -- no database, no reactor."""

import pytest

from copier.mt5 import protocol as p

HELLO = {
    "v": 1, "ea": "1.0.0", "build": 4400, "login": 12345678, "broker": "XYZ Ltd",
    "server": "XYZ-Live3", "currency": "USD", "hedging": True, "trade_mode": "real",
    "leverage": 500,
    "symbols": [{"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01,
                 "vmax": 50, "tm": 4}],
    "chunk": 1, "chunks": 3,
}

SYNC = {
    "v": 1, "seq": 1042, "ts": 1757203200123, "balance": 9784.04, "equity": 9790.10,
    "margin": 120.5, "margin_free": 9669.6,
    "positions": [{"t": 669607966, "s": "XAUUSD.r", "side": "BUY", "lots": 0.01,
                   "open": 4468.49, "sl": 4460.0, "tp": 4480.0, "price": 4470.1,
                   "pnl": 1.61, "swap": 0, "comment": "copy:m669607900",
                   "magic": 20260907, "time": 1757203100}],
    "orders": [{"t": 5551, "s": "XAUUSD.r", "type": "BUY_LIMIT", "lots": 0.01,
                "price": 4450.0, "sl": 0, "tp": 0, "comment": "", "magic": 20260907}],
    "deals": [{"t": 700001, "pos": 669607966, "order": 700000, "s": "XAUUSD.r",
               "type": "BUY", "entry": "IN", "lots": 0.01, "price": 4468.49,
               "profit": 0, "swap": 0, "commission": -0.03, "time": 1757203100456,
               "comment": "copy:m669607900", "magic": 20260907}],
    "acks": [{"id": 88, "ok": True, "retcode": 10009, "msg": "done", "pos": 669607966,
              "deal": 700001, "order": 700000, "price": 4468.49, "lots": 0.01}],
}


class TestUnits:
    def test_lots_to_centilots_and_back(self):
        assert p.centilots(0.01) == 1
        assert p.centilots(1.0) == 100
        assert p.centilots(0.37) == 37
        assert p.lots(150) == 1.5
        assert p.lots(1) == 0.01

    def test_constants(self):
        assert (p.MT5_KEY_PREFIX, p.PROTOCOL_VERSION, p.CENTILOTS) == ("mt5_", 1, 100)
        assert (p.DEFAULT_POLL_MS, p.RETRY_POLL_MS, p.MAX_DEALS_PER_SYNC) == (250, 2000, 200)


class TestParseHello:
    def test_spec_example(self):
        hello = p.parse_hello(HELLO)
        assert (hello.login, hello.broker, hello.server, hello.currency) == (
            12345678, "XYZ Ltd", "XYZ-Live3", "USD")
        assert hello.hedging is True and hello.trade_mode == "real" and hello.leverage == 500
        assert (hello.ea_version, hello.ea_build, hello.chunk, hello.chunks) == (
            "1.0.0", 4400, 1, 3)
        assert hello.symbols == [p.HelloSymbol(
            name="XAUUSD.r", digits=2, contract_size=100.0, volume_min=0.01,
            volume_step=0.01, volume_max=50.0, trade_mode=4)]

    def test_missing_optionals_default(self):
        hello = p.parse_hello({"login": 1, "broker": "B", "hedging": False})
        assert hello.symbols == [] and hello.chunk == 1 and hello.chunks == 1
        assert hello.ea_version == "" and hello.leverage == 0 and hello.server == ""
        assert hello.hedging is False

    @pytest.mark.parametrize("body", [
        {"broker": "B", "hedging": True},                                        # no login
        {"login": "abc", "broker": "B", "hedging": True},                        # login not an int
        {"login": 1, "broker": "B", "hedging": True, "symbols": [{"n": "X"}]},   # symbol without digits
        {"login": 1, "broker": "B", "hedging": True, "symbols": "XAUUSD"},       # not a list
        [],
    ])
    def test_shape_errors_raise_protocol_error(self, body):
        with pytest.raises(p.ProtocolError):
            p.parse_hello(body)


class TestParseSync:
    def test_spec_example_converts_lots_to_centilots(self):
        report = p.parse_sync(SYNC)
        assert (report.seq, report.ts_ms, report.balance, report.equity) == (
            1042, 1757203200123, 9784.04, 9790.10)
        assert (report.margin, report.margin_free) == (120.5, 9669.6)
        assert report.positions[0] == p.ReportPosition(
            ticket=669607966, symbol="XAUUSD.r", side="BUY", volume=1, open_price=4468.49,
            stop_loss=4460.0, take_profit=4480.0, current_price=4470.1, pnl=1.61, swap=0.0,
            comment="copy:m669607900", magic=20260907, opened_at_ms=1757203100000)
        assert report.orders[0] == p.ReportOrder(
            ticket=5551, symbol="XAUUSD.r", order_type="BUY_LIMIT", volume=1, price=4450.0,
            stop_loss=None, take_profit=None, comment="", magic=20260907)
        assert report.deals[0] == p.ReportDeal(
            ticket=700001, position=669607966, order=700000, symbol="XAUUSD.r",
            deal_type="BUY", entry="IN", volume=1, price=4468.49, profit=0.0, swap=0.0,
            commission=-0.03, time_ms=1757203100456, comment="copy:m669607900",
            magic=20260907)
        assert report.acks[0] == p.Ack(
            command_id=88, ok=True, retcode=10009, message="done", position=669607966,
            deal=700001, order=700000, price=4468.49, volume=1)

    def test_missing_optional_fields_become_none_or_zero(self):
        report = p.parse_sync({
            "seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
            "positions": [{"t": 1, "s": "EURUSD", "side": "SELL", "lots": 0.5, "open": 1.1}],
            "deals": [{"t": 9, "time": 5}],
            "acks": [{"id": 3, "ok": False}],
        })
        pos = report.positions[0]
        assert (pos.stop_loss, pos.take_profit, pos.current_price, pos.pnl, pos.comment,
                pos.magic, pos.opened_at_ms) == (None, None, None, 0.0, "", 0, 0)
        assert pos.volume == 50 and pos.side == "SELL"
        assert report.orders == [] and report.margin == 0.0 and report.margin_free == 0.0
        deal = report.deals[0]
        assert (deal.position, deal.order, deal.symbol, deal.deal_type, deal.entry,
                deal.volume, deal.price, deal.commission) == (0, 0, "", "OTHER", "", 0, 0.0, 0.0)
        ack = report.acks[0]
        assert (ack.retcode, ack.message, ack.position, ack.deal, ack.order, ack.price,
                ack.volume) == (0, "", None, None, None, None, None)

    def test_a_zero_price_means_none(self):
        report = p.parse_sync({
            "seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
            "positions": [{"t": 1, "s": "EURUSD", "side": "BUY", "lots": 0.01, "open": 1.1,
                           "sl": 0, "tp": 0.0, "price": 0}],
        })
        pos = report.positions[0]
        assert (pos.stop_loss, pos.take_profit, pos.current_price) == (None, None, None)

    @pytest.mark.parametrize("body", [
        {"ts": 2, "balance": 1.0, "equity": 1.0},                                    # no seq
        {"seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
         "positions": [{"t": 1, "s": "X", "side": "LONG", "lots": 1, "open": 1}]},   # bad side
        {"seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0, "acks": [{"ok": True}]},  # ack without id
        {"seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
         "deals": [{"t": 1, "time": 1, "entry": "SIDEWAYS"}]},                        # bad entry
        "not an object",
    ])
    def test_shape_errors_raise_protocol_error(self, body):
        with pytest.raises(p.ProtocolError):
            p.parse_sync(body)


OPEN = p.Command(88, "open", {"symbol": "XAUUSD.r", "side": "BUY", "lots": 0.01,
                              "sl": 4460.0, "tp": 4480.0, "comment": "copy:m669607900"},
                 "cm669607900.1000000000001")
CLOSE = p.Command(89, "close", {"position": 669607966, "lots": 0.01})
AMEND = p.Command(90, "amend", {"position": 669607966, "sl": 4462.0, "tp": 4482.0})
PLACE = p.Command(91, "place_pending", {"symbol": "XAUUSD.r", "type": "BUY_LIMIT",
                                        "lots": 0.01, "price": 4450.0, "sl": 0, "tp": 0,
                                        "expiry_ms": 0, "comment": "copy:o5551"},
                  "co5551.1000000000001")
AMEND_PENDING = p.Command(92, "amend_pending", {"order": 5551, "lots": 0.01,
                                                "price": 4451.0, "sl": 0, "tp": 0})
CANCEL = p.Command(93, "cancel_pending", {"order": 5551})
ALL = [OPEN, CLOSE, AMEND, PLACE, AMEND_PENDING, CANCEL]


class TestCommandLine:
    def test_every_kind_in_the_contract_field_order(self):
        assert p.command_line(OPEN) == (
            "CMD\t88\topen\tXAUUSD.r\tBUY\t0.01\t4460.0\t4480.0\tcopy:m669607900"
            "\tcm669607900.1000000000001")
        assert p.command_line(CLOSE) == "CMD\t89\tclose\t669607966\t0.01"
        assert p.command_line(AMEND) == "CMD\t90\tamend\t669607966\t4462.0\t4482.0"
        assert p.command_line(PLACE) == (
            "CMD\t91\tplace_pending\tXAUUSD.r\tBUY_LIMIT\t0.01\t4450.0\t0\t0\t0\tcopy:o5551"
            "\tco5551.1000000000001")
        assert p.command_line(AMEND_PENDING) == "CMD\t92\tamend_pending\t5551\t0.01\t4451.0\t0\t0"
        assert p.command_line(CANCEL) == "CMD\t93\tcancel_pending\t5551"

    def test_absent_protection_encodes_as_zero_and_a_full_close_as_zero_lots(self):
        line = p.command_line(p.Command(
            1, "open", {"symbol": "EURUSD", "side": "SELL", "lots": 1.0, "comment": ""}, None))
        assert line == "CMD\t1\topen\tEURUSD\tSELL\t1.0\t0\t0\t\t"
        assert p.command_line(p.Command(2, "close", {"position": 5})) == "CMD\t2\tclose\t5\t0"

    def test_a_tab_or_newline_in_a_field_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.command_line(p.Command(
                1, "open", {"symbol": "EUR\tUSD", "side": "BUY", "lots": 1.0, "comment": ""}))
        with pytest.raises(p.ProtocolError):
            p.command_line(p.Command(
                1, "open", {"symbol": "EURUSD", "side": "BUY", "lots": 1.0,
                            "comment": "line\nbreak"}))

    def test_unknown_kind_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.command_line(p.Command(1, "teleport", {}))


class TestEncodeResponse:
    def test_ok_status_line_then_one_line_per_command(self):
        text = p.encode_response("OK", 1757203200400, 250, [OPEN, CLOSE])
        lines = text.split("\n")
        assert lines[0] == "OK\t1757203200400\t250"
        assert lines[1] == p.command_line(OPEN) and lines[2] == p.command_line(CLOSE)
        assert text.endswith("\n") and lines[-1] == ""

    def test_hello_adds_the_watermark_as_a_fourth_field(self):
        assert p.encode_response("OK", 5, 250, [], last_deal_ticket=700001) == "OK\t5\t250\t700001\n"

    def test_retry_and_stop(self):
        assert p.encode_response("RETRY", 5, 2000, []) == "RETRY\t5\t2000\n"
        assert p.encode_response("STOP", 5, 0, [], reason="netting account not supported") == (
            "STOP\t5\tnetting account not supported\n")

    def test_unknown_status_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.encode_response("MAYBE", 5, 250, [])


class TestParseResponse:
    @pytest.mark.parametrize("cmd", ALL, ids=[c.kind for c in ALL])
    def test_round_trip(self, cmd):
        status, commands = p.parse_response(p.encode_response("OK", 1, 250, [cmd]))
        assert status == ["OK", "1", "250"]
        assert commands == [cmd]

    def test_status_only(self):
        assert p.parse_response("STOP\t1\tkey revoked\n") == (["STOP", "1", "key revoked"], [])

    def test_garbage_is_a_protocol_error(self):
        with pytest.raises(p.ProtocolError):
            p.parse_response("")
        with pytest.raises(p.ProtocolError):
            p.parse_response("OK\t1\t250\nCMD\t1\topen\tonly-two-fields\n")
        with pytest.raises(p.ProtocolError):
            p.parse_response("OK\t1\t250\nNOT-A-COMMAND\n")
```

- [ ] **Step 2: Run the test to verify it fails**

Run PURE `tests/unit/test_mt5_protocol.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5'`.

- [ ] **Step 3: Write the module**

Create `copier/src/copier/mt5/__init__.py` as an empty file.

Create `copier/src/copier/mt5/protocol.py`:

```python
"""The wire format between the MirrorFleet EA and the copier.

Shared by the copier (server side) and the Python fake EA used in tests,
and mirrored -- by contract, not by import -- by the MQL5 EA. Requests
(EA -> server) are JSON; responses (server -> EA) are tab-separated lines,
because MQL5 has no JSON parser but splits strings trivially. Every
conversion between the EA's lots and the copier's centilots happens here
(report -> centilots) or in outbox.py (payload lots <- centilots); nothing
else in the copier ever sees a lot as a float.
"""

from dataclasses import dataclass

MT5_KEY_PREFIX = "mt5_"
PROTOCOL_VERSION = 1
CENTILOTS = 100                       # protocol units per 1.00 lot for every MT5 symbol
DEFAULT_POLL_MS = 250
RETRY_POLL_MS = 2000
MAX_DEALS_PER_SYNC = 200

SIDES = ("BUY", "SELL")
PENDING_TYPES = ("BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP")
DEAL_TYPES = ("BUY", "SELL", "BALANCE", "CREDIT", "OTHER")
DEAL_ENTRIES = ("IN", "OUT", "INOUT", "OUT_BY", "")
COMMAND_KINDS = ("open", "close", "amend", "place_pending", "amend_pending", "cancel_pending")


class ProtocolError(ValueError):
    """The other side sent something this version of the protocol cannot read."""


@dataclass(frozen=True)
class HelloSymbol:
    name: str
    digits: int
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    trade_mode: int


@dataclass(frozen=True)
class HelloReport:
    ea_version: str
    ea_build: int
    login: int
    broker: str
    server: str
    currency: str
    hedging: bool
    trade_mode: str
    leverage: int
    symbols: list[HelloSymbol]
    chunk: int
    chunks: int


@dataclass(frozen=True)
class ReportPosition:
    ticket: int
    symbol: str
    side: str                                    # "BUY" | "SELL"
    volume: int                                  # centilots
    open_price: float
    stop_loss: float | None
    take_profit: float | None
    current_price: float | None
    pnl: float
    swap: float
    comment: str
    magic: int
    opened_at_ms: int


@dataclass(frozen=True)
class ReportOrder:
    ticket: int
    symbol: str
    order_type: str                              # "BUY_LIMIT" | "SELL_LIMIT" | "BUY_STOP" | "SELL_STOP"
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None
    comment: str
    magic: int


@dataclass(frozen=True)
class ReportDeal:
    ticket: int
    position: int
    order: int
    symbol: str
    deal_type: str                               # "BUY" | "SELL" | "BALANCE" | "CREDIT" | "OTHER"
    entry: str                                   # "IN" | "OUT" | "INOUT" | "OUT_BY" | "" (balance ops)
    volume: int
    price: float
    profit: float
    swap: float
    commission: float
    time_ms: int
    comment: str
    magic: int


@dataclass(frozen=True)
class Ack:
    command_id: int
    ok: bool
    retcode: int
    message: str
    position: int | None
    deal: int | None
    order: int | None
    price: float | None
    volume: int | None                           # centilots


@dataclass(frozen=True)
class SyncReport:
    seq: int
    ts_ms: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    positions: list[ReportPosition]
    orders: list[ReportOrder]
    deals: list[ReportDeal]
    acks: list[Ack]


@dataclass(frozen=True)
class Command:
    id: int
    kind: str
    payload: dict                                # payload per the contract's table (section 1)
    client_order_id: str | None = None


def centilots(lots_value) -> int:
    """Lots (a float on the wire) -> centilots (the copier's integer units)."""
    return int(round(float(lots_value) * CENTILOTS))


def lots(volume: int) -> float:
    """Centilots -> lots, as the EA wants them."""
    return round(int(volume) / CENTILOTS, 2)


# ---------- request parsing (EA -> server) ----------

def _require(body: dict, key: str, kind, what: str):
    if key not in body or body[key] is None:
        raise ProtocolError(f"{what}: missing field {key!r}")
    try:
        return kind(body[key])
    except (TypeError, ValueError):
        raise ProtocolError(f"{what}: field {key!r} is not a {kind.__name__}")


def _optional(body: dict, key: str, kind, default):
    value = body.get(key)
    if value is None:
        return default
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"field {key!r} is not a {kind.__name__}")


def _price(value) -> float | None:
    """A price field: 0 or absent means none."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"price {value!r} is not a number")
    return price if price != 0 else None


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"ticket {value!r} is not an integer")
    return number if number != 0 else None


def _enum(value, allowed: tuple, what: str) -> str:
    text = str(value).upper()
    if text not in allowed:
        raise ProtocolError(f"{what}: {value!r} is not one of {allowed}")
    return text


def _list(body: dict, key: str) -> list:
    value = body.get(key) or []
    if not isinstance(value, list):
        raise ProtocolError(f"field {key!r} must be a list")
    for item in value:
        if not isinstance(item, dict):
            raise ProtocolError(f"every entry of {key!r} must be an object")
    return value


def parse_hello(body: dict) -> HelloReport:
    if not isinstance(body, dict):
        raise ProtocolError("hello body must be a JSON object")
    symbols = [
        HelloSymbol(
            name=str(_require(raw, "n", str, "hello symbol")),
            digits=_require(raw, "d", int, "hello symbol"),
            contract_size=_optional(raw, "cs", float, 0.0),
            volume_min=_require(raw, "vmin", float, "hello symbol"),
            volume_step=_require(raw, "vstep", float, "hello symbol"),
            volume_max=_optional(raw, "vmax", float, 0.0),
            trade_mode=_optional(raw, "tm", int, 0),
        )
        for raw in _list(body, "symbols")
    ]
    return HelloReport(
        ea_version=str(_optional(body, "ea", str, "")),
        ea_build=_optional(body, "build", int, 0),
        login=_require(body, "login", int, "hello"),
        broker=str(_require(body, "broker", str, "hello")),
        server=str(_optional(body, "server", str, "")),
        currency=str(_optional(body, "currency", str, "")),
        hedging=bool(_require(body, "hedging", bool, "hello")),
        trade_mode=str(_optional(body, "trade_mode", str, "")),
        leverage=_optional(body, "leverage", int, 0),
        symbols=symbols,
        chunk=_optional(body, "chunk", int, 1),
        chunks=_optional(body, "chunks", int, 1),
    )


def parse_sync(body: dict) -> SyncReport:
    """Lots become centilots here and nowhere else; missing optional fields
    become None (prices, tickets) or 0 (money, counters)."""
    if not isinstance(body, dict):
        raise ProtocolError("sync body must be a JSON object")
    positions = [
        ReportPosition(
            ticket=_require(raw, "t", int, "position"),
            symbol=str(_require(raw, "s", str, "position")),
            side=_enum(_require(raw, "side", str, "position"), SIDES, "position side"),
            volume=centilots(_require(raw, "lots", float, "position")),
            open_price=_require(raw, "open", float, "position"),
            stop_loss=_price(raw.get("sl")),
            take_profit=_price(raw.get("tp")),
            current_price=_price(raw.get("price")),
            pnl=_optional(raw, "pnl", float, 0.0),
            swap=_optional(raw, "swap", float, 0.0),
            comment=str(raw.get("comment") or ""),
            magic=_optional(raw, "magic", int, 0),
            # POSITION_TIME is seconds on the terminal (deals carry DEAL_TIME_MSC).
            opened_at_ms=_optional(raw, "time", int, 0) * 1000,
        )
        for raw in _list(body, "positions")
    ]
    orders = [
        ReportOrder(
            ticket=_require(raw, "t", int, "order"),
            symbol=str(_require(raw, "s", str, "order")),
            order_type=_enum(_require(raw, "type", str, "order"), PENDING_TYPES, "order type"),
            volume=centilots(_require(raw, "lots", float, "order")),
            price=_require(raw, "price", float, "order"),
            stop_loss=_price(raw.get("sl")),
            take_profit=_price(raw.get("tp")),
            comment=str(raw.get("comment") or ""),
            magic=_optional(raw, "magic", int, 0),
        )
        for raw in _list(body, "orders")
    ]
    deals = [
        ReportDeal(
            ticket=_require(raw, "t", int, "deal"),
            position=_optional(raw, "pos", int, 0),
            order=_optional(raw, "order", int, 0),
            symbol=str(raw.get("s") or ""),
            deal_type=_enum(_optional(raw, "type", str, "OTHER"), DEAL_TYPES, "deal type"),
            entry=_enum(_optional(raw, "entry", str, ""), DEAL_ENTRIES, "deal entry"),
            volume=centilots(_optional(raw, "lots", float, 0.0)),
            price=_optional(raw, "price", float, 0.0),
            profit=_optional(raw, "profit", float, 0.0),
            swap=_optional(raw, "swap", float, 0.0),
            commission=_optional(raw, "commission", float, 0.0),
            time_ms=_require(raw, "time", int, "deal"),
            comment=str(raw.get("comment") or ""),
            magic=_optional(raw, "magic", int, 0),
        )
        for raw in _list(body, "deals")
    ]
    acks = []
    for raw in _list(body, "acks"):
        volume = raw.get("lots")
        acks.append(Ack(
            command_id=_require(raw, "id", int, "ack"),
            ok=bool(_require(raw, "ok", bool, "ack")),
            retcode=_optional(raw, "retcode", int, 0),
            message=str(raw.get("msg") or ""),
            position=_int_or_none(raw.get("pos")),
            deal=_int_or_none(raw.get("deal")),
            order=_int_or_none(raw.get("order")),
            price=_price(raw.get("price")),
            volume=centilots(volume) if volume is not None else None,
        ))
    return SyncReport(
        seq=_require(body, "seq", int, "sync"),
        ts_ms=_require(body, "ts", int, "sync"),
        balance=_require(body, "balance", float, "sync"),
        equity=_require(body, "equity", float, "sync"),
        margin=_optional(body, "margin", float, 0.0),
        margin_free=_optional(body, "margin_free", float, 0.0),
        positions=positions, orders=orders, deals=deals, acks=acks,
    )


# ---------- response encoding (server -> EA) ----------

def _field(value) -> str:
    """One positional field. The format has no escaping, so a tab or a line
    break inside a value would corrupt every field after it."""
    text = str(value)
    if "\t" in text or "\n" in text or "\r" in text:
        raise ProtocolError(f"field {text!r} would break the line format")
    return text


def _num(value) -> str:
    """A price or lot size: None/0 -> "0" (no protection, full close);
    otherwise the shortest repr that round-trips through StringToDouble."""
    if value is None or float(value) == 0:
        return "0"
    return repr(float(value))


def command_line(cmd: Command) -> str:
    p = cmd.payload
    if cmd.kind == "open":
        fields = [p["symbol"], p["side"], _num(p["lots"]), _num(p.get("sl")), _num(p.get("tp")),
                  p.get("comment", ""), cmd.client_order_id or ""]
    elif cmd.kind == "close":
        fields = [int(p["position"]), _num(p.get("lots"))]
    elif cmd.kind == "amend":
        fields = [int(p["position"]), _num(p.get("sl")), _num(p.get("tp"))]
    elif cmd.kind == "place_pending":
        fields = [p["symbol"], p["type"], _num(p["lots"]), _num(p["price"]), _num(p.get("sl")),
                  _num(p.get("tp")), int(p.get("expiry_ms") or 0), p.get("comment", ""),
                  cmd.client_order_id or ""]
    elif cmd.kind == "amend_pending":
        fields = [int(p["order"]), _num(p["lots"]), _num(p["price"]), _num(p.get("sl")),
                  _num(p.get("tp"))]
    elif cmd.kind == "cancel_pending":
        fields = [int(p["order"])]
    else:
        raise ProtocolError(f"unknown command kind {cmd.kind!r}")
    return "\t".join(["CMD", str(int(cmd.id)), cmd.kind] + [_field(f) for f in fields])


def encode_response(status: str, server_ms: int, next_poll_ms: int, commands: list[Command],
                    last_deal_ticket: int | None = None, reason: str | None = None) -> str:
    """The whole response: one status line, then one CMD line per command.

    status: "OK" | "RETRY" | "STOP". For STOP the third field is the reason
    text (the EA shows it and stops polling). The hello response passes
    last_deal_ticket so a restarted EA resumes its deal watermark.
    """
    if status == "STOP":
        head = ["STOP", str(int(server_ms)), _field(reason or "stopped")]
    elif status in ("OK", "RETRY"):
        head = [status, str(int(server_ms)), str(int(next_poll_ms))]
        if last_deal_ticket is not None:
            head.append(str(int(last_deal_ticket)))
    else:
        raise ProtocolError(f"unknown status {status!r}")
    lines = ["\t".join(head)] + [command_line(c) for c in commands]
    return "\n".join(lines) + "\n"


def parse_response(text: str) -> tuple[list[str], list[Command]]:
    """The EA's side of the format, kept here so the fake EA and the tests
    prove the encoder against the same rules the real EA follows.

    Returns (status line fields, commands). Prices and lots come back as
    floats (a "0" is 0.0 -- "none"), tickets and expiry as ints."""
    lines = [line for line in text.split("\n") if line != ""]
    if not lines:
        raise ProtocolError("empty response")
    status = lines[0].split("\t")
    commands: list[Command] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if parts[0] != "CMD" or len(parts) < 3:
            raise ProtocolError(f"bad command line {line!r}")
        kind, rest = parts[2], parts[3:]
        coid: str | None = None
        try:
            command_id = int(parts[1])
            if kind == "open":
                symbol, side, lots_, sl, tp, comment, coid = rest
                payload = {"symbol": symbol, "side": side, "lots": float(lots_),
                           "sl": float(sl), "tp": float(tp), "comment": comment}
            elif kind == "close":
                position, lots_ = rest
                payload = {"position": int(position), "lots": float(lots_)}
            elif kind == "amend":
                position, sl, tp = rest
                payload = {"position": int(position), "sl": float(sl), "tp": float(tp)}
            elif kind == "place_pending":
                symbol, type_, lots_, price, sl, tp, expiry, comment, coid = rest
                payload = {"symbol": symbol, "type": type_, "lots": float(lots_),
                           "price": float(price), "sl": float(sl), "tp": float(tp),
                           "expiry_ms": int(expiry), "comment": comment}
            elif kind == "amend_pending":
                order, lots_, price, sl, tp = rest
                payload = {"order": int(order), "lots": float(lots_), "price": float(price),
                           "sl": float(sl), "tp": float(tp)}
            elif kind == "cancel_pending":
                (order,) = rest
                payload = {"order": int(order)}
            else:
                raise ProtocolError(f"unknown command kind {kind!r}")
        except ValueError as e:
            # A wrong field count unpacks to ValueError, as does a non-number.
            raise ProtocolError(f"bad command line {line!r}: {e}")
        commands.append(Command(id=command_id, kind=kind, payload=payload,
                                client_order_id=coid or None))
    return status, commands
```

- [ ] **Step 4: Run the tests to verify they pass**

Run PURE `tests/unit/test_mt5_protocol.py`.
Expected: `33 passed`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/__init__.py copier/src/copier/mt5/protocol.py copier/tests/unit/test_mt5_protocol.py && git commit -m "feat(mt5): the EA wire format -- JSON reports in, tab-separated commands out

Report dataclasses, lots<->centilots conversion, parse_hello/parse_sync
with shape errors as ProtocolError, encode_response/command_line in the
contract's field order (no field may carry a tab), and parse_response for
the fake EA. Every kind round-trips.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `copier.mt5.symbols` — normalisation, crc32 ids, auto-match

**Files:**
- Create: `copier/src/copier/mt5/symbols.py`
- Test: `copier/tests/unit/test_mt5_symbols.py`

**Interfaces:**
- Consumes: `SymbolInfo` (`copier/src/copier/domain/models.py:26-33`), `HelloSymbol`, `CENTILOTS` (Task 2).
- Produces (contract §2 `symbols.py`): `SYNONYMS: dict[str, str]`, `SUFFIXES`, `normalise_symbol(name) -> str`, `symbol_id_for(name, taken: set[int]) -> int`, `symbol_infos_from_hello(symbols: list[HelloSymbol]) -> list[SymbolInfo]`, `auto_match(canonical_names, broker_names) -> dict[str, str]`. Used by Tasks 6 and 15.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_mt5_symbols.py`:

```python
"""Symbol naming for the MT5 bridge (copier/src/copier/mt5/symbols.py):
broker names -> canonical names, the crc32 ids the rest of the copier keys
on, and the auto-matcher the hello runs."""

import zlib

import pytest

from copier.domain.models import SymbolInfo
from copier.mt5.protocol import HelloSymbol
from copier.mt5.symbols import (
    SYNONYMS, auto_match, normalise_symbol, symbol_id_for, symbol_infos_from_hello)


@pytest.mark.parametrize("name, canonical", [
    ("XAUUSD.r", "XAUUSD"), ("GOLDm", "XAUUSD"), ("GOLD", "XAUUSD"), ("gold.pro", "XAUUSD"),
    ("XAUUSDM", "XAUUSD"),
    ("SILVER", "XAGUSD"), ("SILVERc", "XAGUSD"),
    ("USTEC", "NAS100"), ("NAS100.i", "NAS100"),
    ("DE40", "GER40"), ("GER40.ecn", "GER40"),
    ("US30", "US30"), ("DJ30", "US30"), ("US30.m", "US30"),
    ("US500", "US500"), ("SPX500", "US500"),
    ("UK100", "UK100"),
    ("USOIL", "USOIL"), ("WTI", "USOIL"), ("OIL", "USOIL"),
    ("BRENT", "UKOIL"), ("UKOIL", "UKOIL"),
    ("BTCUSD", "BTCUSD"), ("BITCOIN", "BTCUSD"),
    ("ETHUSD.x", "ETHUSD"),
    ("EURUSD", "EURUSD"), ("EURUSD.r", "EURUSD"), ("EURUSDm", "EURUSD"), ("eurusd_i", "EURUSD"),
    ("GBPJPY.i", "GBPJPY"), ("AUDCADc", "AUDCAD"),
    ("USTEC.cash", "USTECCASH"),      # no rule fits: the operator maps it by hand
])
def test_normalise_symbol(name, canonical):
    assert normalise_symbol(name) == canonical


def test_the_synonym_table_from_the_spec():
    expected = [
        ("GOLD", "XAUUSD"), ("XAUUSD", "XAUUSD"), ("SILVER", "XAGUSD"), ("XAGUSD", "XAGUSD"),
        ("USTEC", "NAS100"), ("NAS100", "NAS100"), ("DE40", "GER40"), ("GER40", "GER40"),
        ("US30", "US30"), ("DJ30", "US30"), ("US500", "US500"), ("SPX500", "US500"),
        ("UK100", "UK100"), ("USOIL", "USOIL"), ("WTI", "USOIL"), ("OIL", "USOIL"),
        ("BRENT", "UKOIL"), ("UKOIL", "UKOIL"), ("BTCUSD", "BTCUSD"), ("BITCOIN", "BTCUSD"),
        ("ETHUSD", "ETHUSD"),
    ]
    for stem, canonical in expected:
        assert SYNONYMS[stem] == canonical


class TestSymbolIds:
    def test_crc32_masked_to_31_bits(self):
        assert symbol_id_for("XAUUSD.r", set()) == zlib.crc32(b"XAUUSD.r") & 0x7FFFFFFF

    def test_a_collision_probes_upwards(self):
        base = symbol_id_for("EURUSD", set())
        assert symbol_id_for("EURUSD", {base}) == base + 1
        assert symbol_id_for("EURUSD", {base, base + 1}) == base + 2

    def test_hello_symbols_become_centilot_symbol_infos(self):
        infos = symbol_infos_from_hello([
            HelloSymbol("XAUUSD.r", 2, 100.0, 0.01, 0.01, 50.0, 4),
            HelloSymbol("EURUSD.r", 5, 100000.0, 0.01, 0.01, 100.0, 4),
            HelloSymbol("BTCUSD", 2, 1.0, 0.1, 0.1, 10.0, 4),
        ])
        assert infos[0] == SymbolInfo(
            symbol_id=zlib.crc32(b"XAUUSD.r") & 0x7FFFFFFF, name="XAUUSD.r", digits=2,
            lot_size=100, min_volume=1, step_volume=1)
        assert (infos[2].min_volume, infos[2].step_volume) == (10, 10)
        assert len({i.symbol_id for i in infos}) == 3

    def test_a_step_below_one_centilot_is_clamped_to_one(self):
        (info,) = symbol_infos_from_hello([HelloSymbol("TINY", 2, 1.0, 0.001, 0.001, 1.0, 4)])
        assert info.step_volume == 1 and info.min_volume == 0


class TestAutoMatch:
    def test_exact_then_normalised_then_synonym(self):
        matched = auto_match(
            ["EURUSD", "XAUUSD", "GBPJPY", "NAS100", "US30"],
            ["EURUSD.r", "GOLD.r", "XAUUSD", "USTEC", "DJ30.pro"],
        )
        assert matched == {"EURUSD": "EURUSD.r", "XAUUSD": "XAUUSD",
                           "NAS100": "USTEC", "US30": "DJ30.pro"}
        assert "GBPJPY" not in matched

    def test_the_first_broker_symbol_wins_a_normalised_tie(self):
        assert auto_match(["XAUUSD"], ["GOLD.r", "GOLDm"]) == {"XAUUSD": "GOLD.r"}

    def test_a_canonical_name_that_is_itself_a_synonym_matches(self):
        # A cTrader master that names gold "GOLD" copies to a broker naming it "XAUUSD.r".
        assert auto_match(["GOLD"], ["XAUUSD.r"]) == {"GOLD": "XAUUSD.r"}

    def test_empty_inputs(self):
        assert auto_match([], ["EURUSD"]) == {}
        assert auto_match(["EURUSD"], []) == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run PURE `tests/unit/test_mt5_symbols.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5.symbols'`.

- [ ] **Step 3: Write the module**

Create `copier/src/copier/mt5/symbols.py`:

```python
"""Symbol naming for the MT5 bridge.

The engine matches symbols across accounts by NAME (that is how a cTrader
master's "XAUUSD" finds each slave's "XAUUSD"). MT5 brokers decorate names
("XAUUSD.r", "GOLDm", "USTEC") and have no numeric symbol id, so this
module does two things: it guesses which broker name is which canonical
instrument, and it mints the stable integer id every symbol_id-keyed path
in the copier expects.
"""

import re
import zlib
from typing import Iterable

from copier.domain.models import SymbolInfo
from copier.mt5.protocol import CENTILOTS, HelloSymbol

# Normalised stem -> canonical name. Both sides of every synonym are keys
# so that a canonical name normalises to itself.
SYNONYMS: dict[str, str] = {
    "GOLD": "XAUUSD", "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "XAGUSD": "XAGUSD",
    "USTEC": "NAS100", "NAS100": "NAS100",
    "DE40": "GER40", "GER40": "GER40",
    "US30": "US30", "DJ30": "US30",
    "US500": "US500", "SPX500": "US500",
    "UK100": "UK100",
    "USOIL": "USOIL", "WTI": "USOIL", "OIL": "USOIL",
    "BRENT": "UKOIL", "UKOIL": "UKOIL",
    "BTCUSD": "BTCUSD", "BITCOIN": "BTCUSD",
    "ETHUSD": "ETHUSD",
}

# Broker decorations, checked (upper-cased) at the end of the raw name.
SUFFIXES = (".ECN", ".PRO", ".R", ".X", ".M", ".I", "_I")
# A trailing account-type letter ("EURUSDm", "GOLDc") is stripped only when
# what remains is a known instrument, so a real 7-letter name is never cut.
TRAILING_LETTERS = ("M", "C", "I")

_NOT_ALNUM = re.compile(r"[^A-Z0-9]")
_FX_PAIR = re.compile(r"[A-Z]{6}")


def _is_known(stem: str) -> bool:
    return stem in SYNONYMS or _FX_PAIR.fullmatch(stem) is not None


def normalise_symbol(name: str) -> str:
    """"XAUUSD.r" -> "XAUUSD", "GOLDm" -> "XAUUSD", "eurusd_i" -> "EURUSD"."""
    upper = name.strip().upper()
    for suffix in SUFFIXES:
        if upper.endswith(suffix) and len(upper) > len(suffix):
            upper = upper[: -len(suffix)]
            break
    stem = _NOT_ALNUM.sub("", upper)
    if stem in SYNONYMS:
        return SYNONYMS[stem]
    if len(stem) > 1 and stem[-1] in TRAILING_LETTERS and _is_known(stem[:-1]):
        return SYNONYMS.get(stem[:-1], stem[:-1])
    return stem


def symbol_id_for(name: str, taken: set[int]) -> int:
    """crc32 of the broker name, masked positive; +1 probing within one
    account's list keeps ids unique there."""
    symbol_id = zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF
    while symbol_id in taken:
        symbol_id += 1
    return symbol_id


def symbol_infos_from_hello(symbols: list[HelloSymbol]) -> list[SymbolInfo]:
    """The hello's symbol list as the engine's SymbolInfo rows: lot_size is
    always CENTILOTS, volumes are centilots, ids are crc32 with probing."""
    out: list[SymbolInfo] = []
    taken: set[int] = set()
    for s in symbols:
        symbol_id = symbol_id_for(s.name, taken)
        taken.add(symbol_id)
        out.append(SymbolInfo(
            symbol_id=symbol_id, name=s.name, digits=s.digits, lot_size=CENTILOTS,
            min_volume=round(s.volume_min * CENTILOTS),
            step_volume=max(1, round(s.volume_step * CENTILOTS)),
        ))
    return out


def auto_match(canonical_names: Iterable[str], broker_names: Iterable[str]) -> dict[str, str]:
    """canonical -> broker_name. Exact name first, then the same normalised
    stem (which already folds synonyms). Unmatched canonicals are omitted;
    a manual alias set by the operator always wins over this guess (see
    Repo.save_symbol_aliases)."""
    brokers = list(dict.fromkeys(broker_names))
    by_exact = {b: b for b in brokers}
    by_stem: dict[str, str] = {}
    for b in brokers:
        by_stem.setdefault(normalise_symbol(b), b)
    out: dict[str, str] = {}
    for canonical in canonical_names:
        if canonical in by_exact:
            out[canonical] = canonical
            continue
        stem = normalise_symbol(canonical)
        if stem in by_stem:
            out[canonical] = by_stem[stem]
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run PURE `tests/unit/test_mt5_symbols.py`.
Expected: `41 passed`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/symbols.py copier/tests/unit/test_mt5_symbols.py && git commit -m "feat(mt5): symbol normalisation, crc32 ids and the auto-matcher

Broker decorations and the spec's synonym table fold to a canonical
name; every MT5 symbol gets a stable crc32 id (+1 probing on collision)
and a centilot SymbolInfo, so the symbol_id-keyed engine paths need no
change.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Repo additions — `AccountRow.platform`, links, command queue, aliases, watermark, MT5 deals

**Files:**
- Modify: `copier/src/copier/db/repo.py:39-51` (AccountRow), `:352-377` (load_accounts), `:792-830` (upsert_deals → `_deal_row`), `:847-919` (load_deals filters), `:1129-1153` (`reduce_position_mapping` gains `client_order_id`), append a new `# ---------- MT5 bridge ----------` section before `# ---------- partitions ----------` (line 1415)
- Modify: `copier/tests/unit/conftest.py` (append the `seed_mt5_account` fixture)
- Test: `copier/tests/unit/test_repo_mt5.py`

**Interfaces:**
- Consumes: migration 014 tables (Task 1).
- Produces (contract §2 "Repo additions", used by Tasks 6, 7, 13, 14, 15): `AccountRow.platform: str = "ctrader"`, `AccountRow.connection_id: int | None`; `Repo.load_mt5_link(account_id) -> dict | None`; `Repo.upsert_mt5_link_hello(account_id, *, login, broker, server, currency, hedging, trade_mode, leverage, ea_version, ea_build) -> None`; `Repo.touch_mt5_link(account_id, *, balance, equity, seen_at) -> None`; `Repo.enqueue_mt5_command(account_id, org_id, kind, payload, client_order_id) -> int`; `Repo.mt5_commands_open(account_id) -> list[dict]` (keys `id, account_id, org_id, kind, payload, client_order_id, status, attempts, created_at, sent_at`); `Repo.mark_mt5_commands_sent(ids, sent_at) -> None`; `Repo.complete_mt5_command(command_id, ok, result, done_at, account_id=None) -> dict | None` (keys `id, account_id, org_id, kind, client_order_id, payload`); `Repo.fail_stale_mt5_opens(account_id, older_than) -> list[int]`; `Repo.load_symbol_aliases(account_id) -> dict[str, str]`; `Repo.save_symbol_aliases(account_id, aliases, source) -> None`; `Repo.mt5_watermark(account_id) -> tuple[int, int]`; `Repo.set_mt5_watermark(account_id, ticket, time_ms) -> None`; `Repo.upsert_mt5_deals(account_id, org_id, rows) -> int`; private additions `Repo.load_mt5_cash_flow(account_id, from_ms, to_ms) -> list[dict]`, `Repo.load_deals(..., until_ms=None, position_id=None)`, module function `_deal_row(account_id, org_id, d) -> dict`; test fixture `seed_mt5_account(org_id, role="slave", multiplier="1.0", enabled=True) -> int`.
- Netting support (contract §2 "Repo additions", used by Tasks 7, 10 and 15): `Repo.mapping_side(client_order_id) -> str | None` (the side of the `open` command with that coid); private `Repo.mt5_open_payload(client_order_id) -> dict | None` (that command's whole payload — the outbox needs the broker symbol too); `Repo.reduce_position_mappings_fifo(slave_account_id, slave_position_id, closed_volume) -> list[dict]` (active mappings sharing that slave position reduced oldest first by `(created_at, id)`, rows reaching 0 closed; returns the rows touched, oldest first, each with `id, client_order_id, master_position_id, closed_volume, slave_volume, status`); and the existing `Repo.reduce_position_mapping(slave_account_id, slave_position_id, closed_volume, client_order_id=None)` gains the optional predicate so one of several mappings sharing a slave position can be reduced alone — and when it is given it is the ONLY predicate besides the account: the ticket argument is not consulted, because the EA acks a netting close that emptied the net position with ticket 0 (plan 04 `DoOpen` reports the position the terminal shows after the fill, none), and a client_order_id is unique on its own. `fail_stale_mt5_opens` never expires an `open` whose `client_order_id` ends in `:close` (a netting follower's close, Task 7). The `mt5_net_ledger` accessors (`load_net_ledger` / `upsert_net_ledger` / `delete_net_ledger`) ship with their table in Task 5, so this task leaves the suite green on its own.

- [ ] **Step 1: Add the shared test fixture**

Append to `copier/tests/unit/conftest.py` (after the `_release_repo_connections` fixture):

```python


@pytest.fixture
def seed_mt5_account(db):
    """seed_mt5_account(org_id, role='slave', multiplier='1.0', enabled=True)
    -> the synthetic account id of a fresh MT5 account in that org, with the
    mt5_links row the api would have created (a dummy key hash; the copier
    never reads it)."""
    import psycopg

    def _seed(org_id, role="slave", multiplier="1.0", enabled=True):
        with psycopg.connect(db, autocommit=True) as conn:
            (account_id,) = conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id,
                                      trader_login, is_live, role, enabled, multiplier, platform)
                VALUES (nextval('mt5_account_id_seq'), NULL, %s, 0, false, %s, %s, %s, 'mt5')
                RETURNING ctid_trader_account_id
                """,
                (org_id, role, enabled, multiplier),
            ).fetchone()
            conn.execute(
                "INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, %s)",
                (account_id, f"hash-{account_id}"),
            )
        return account_id

    return _seed
```

- [ ] **Step 2: Write the failing test**

Create `copier/tests/unit/test_repo_mt5.py`:

```python
"""Repo additions for the MT5 bridge (copier/src/copier/db/repo.py): links,
the command queue, symbol aliases, the deal watermark and MT5 deals."""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from copier.db.repo import Repo


@pytest.fixture
def world(db, seed_mt5_account):
    """One org with a cTrader master (100) and one MT5 slave -> (repo, org_id, mt5_id)."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
        conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
            " granted_at, expires_at) VALUES (%s, 'x', 'y', now(), now() + interval '30 days')",
            (org_id,))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role) VALUES (100, %s, 1, 111, false, 'master')",
            (org_id,))
    mt5_id = seed_mt5_account(org_id)
    return Repo(db), org_id, mt5_id


def test_load_accounts_reports_the_platform(world):
    repo, _org_id, mt5_id = world
    by_id = {a.account_id: a for a in repo.load_accounts()}
    assert by_id[100].platform == "ctrader" and by_id[100].connection_id == 1
    assert by_id[mt5_id].platform == "mt5" and by_id[mt5_id].connection_id is None
    assert mt5_id >= 1_000_000_000_000


def test_hello_updates_the_link_and_copies_the_login_onto_the_account(world):
    repo, _org_id, mt5_id = world
    repo.upsert_mt5_link_hello(
        mt5_id, login=12345678, broker="XYZ Ltd", server="XYZ-Live3", currency="USD",
        hedging=True, trade_mode="real", leverage=500, ea_version="1.0.0", ea_build=4400)
    link = repo.load_mt5_link(mt5_id)
    assert (link["login"], link["broker"], link["server"], link["currency"],
            link["hedging"]) == (12345678, "XYZ Ltd", "XYZ-Live3", "USD", True)
    assert (link["trade_mode"], link["leverage"], link["ea_version"],
            link["ea_build"]) == ("real", 500, "1.0.0", 4400)
    assert link["last_seen_at"] is None and "key_hash" not in link
    account = next(a for a in repo.load_accounts() if a.account_id == mt5_id)
    assert account.trader_login == 12345678


def test_load_mt5_link_is_none_for_a_ctrader_account(world):
    repo, _org_id, _mt5_id = world
    assert repo.load_mt5_link(100) is None


def test_touch_records_balance_equity_and_last_seen(world):
    repo, _org_id, mt5_id = world
    seen = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
    repo.touch_mt5_link(mt5_id, balance=9784.04, equity=9790.10, seen_at=seen)
    link = repo.load_mt5_link(mt5_id)
    assert (link["balance"], link["equity"], link["last_seen_at"]) == (9784.04, 9790.10, seen)


class TestCommandQueue:
    def test_enqueue_then_open_in_id_order(self, world):
        repo, org_id, mt5_id = world
        first = repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 0.01, "sl": 0, "tp": 0,
             "comment": "copy:m1"}, f"cm1.{mt5_id}")
        second = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        rows = repo.mt5_commands_open(mt5_id)
        assert [r["id"] for r in rows] == [first, second]
        assert rows[0]["status"] == "queued" and rows[0]["attempts"] == 0
        assert rows[0]["sent_at"] is None and rows[0]["created_at"] is not None
        assert rows[0]["payload"]["symbol"] == "EURUSD.r"
        assert rows[0]["client_order_id"] == f"cm1.{mt5_id}" and rows[0]["org_id"] == org_id
        assert rows[1]["kind"] == "close" and rows[1]["client_order_id"] is None
        assert repo.mt5_commands_open(100) == []

    def test_mark_sent_counts_attempts_and_keeps_the_row_open(self, world):
        repo, org_id, mt5_id = world
        cid = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        sent = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        repo.mark_mt5_commands_sent([cid], sent)
        repo.mark_mt5_commands_sent([cid], sent + timedelta(seconds=10))
        (row,) = repo.mt5_commands_open(mt5_id)
        assert (row["status"], row["attempts"], row["sent_at"]) == (
            "sent", 2, sent + timedelta(seconds=10))
        repo.mark_mt5_commands_sent([], sent)   # nothing to do, no error

    def test_complete_returns_the_row_once_and_settles_it(self, world):
        repo, org_id, mt5_id = world
        cid = repo.enqueue_mt5_command(
            mt5_id, org_id, "open", {"symbol": "EURUSD.r"}, f"cm1.{mt5_id}")
        done = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        result = {"ok": True, "retcode": 10009, "message": "done", "position": 7, "deal": 8,
                  "order": 9, "price": 1.1, "lots": 0.01}
        row = repo.complete_mt5_command(cid, True, result, done, account_id=mt5_id)
        assert (row["id"], row["kind"], row["client_order_id"], row["account_id"],
                row["org_id"]) == (cid, "open", f"cm1.{mt5_id}", mt5_id, org_id)
        assert row["payload"] == {"symbol": "EURUSD.r"}
        # A duplicate ack changes nothing and reports nothing.
        assert repo.complete_mt5_command(cid, True, result, done, account_id=mt5_id) is None
        assert repo.mt5_commands_open(mt5_id) == []
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            status, stored, done_at = conn.execute(
                "SELECT status, result, done_at FROM mt5_commands WHERE id = %s", (cid,)
            ).fetchone()
        assert (status, stored, done_at) == ("done", result, done)

    def test_a_failed_ack_marks_the_row_failed(self, world):
        repo, org_id, mt5_id = world
        cid = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        row = repo.complete_mt5_command(
            cid, False, {"ok": False, "retcode": 10036, "message": "position closed"},
            datetime.now(timezone.utc))
        assert row["id"] == cid
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (status,) = conn.execute(
                "SELECT status FROM mt5_commands WHERE id = %s", (cid,)).fetchone()
        assert status == "failed"

    def test_complete_ignores_another_accounts_command_and_unknown_ids(self, world, seed_mt5_account):
        repo, org_id, mt5_id = world
        other = seed_mt5_account(org_id)
        cid = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        assert repo.complete_mt5_command(
            cid, True, {"ok": True}, datetime.now(timezone.utc), account_id=other) is None
        assert [r["id"] for r in repo.mt5_commands_open(mt5_id)] == [cid]
        assert repo.complete_mt5_command(
            999_999, True, {"ok": True}, datetime.now(timezone.utc)) is None

    def test_fail_stale_opens_expires_only_old_opens_and_fails_their_mappings(self, world):
        repo, org_id, mt5_id = world
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        repo.create_order_mapping(43, mt5_id, f"co43.{mt5_id}", org_id=org_id)
        old_open = repo.enqueue_mt5_command(
            mt5_id, org_id, "open", {"symbol": "EURUSD.r"}, f"cm42.{mt5_id}")
        old_pending = repo.enqueue_mt5_command(
            mt5_id, org_id, "place_pending", {"symbol": "EURUSD.r"}, f"co43.{mt5_id}")
        old_close = repo.enqueue_mt5_command(
            mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        # A netting follower's close is an opposite-side open named after its
        # mapping with the ':close' suffix (Task 7): a close, so it never expires.
        old_netting_close = repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "SELL", "lots": 1.0, "sl": 0, "tp": 0,
             "comment": "close:m42"}, f"cm42.{mt5_id}:close")
        fresh_open = repo.enqueue_mt5_command(
            mt5_id, org_id, "open", {"symbol": "EURUSD.r"}, f"cm44.{mt5_id}")
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE mt5_commands SET created_at = now() - interval '60 seconds'"
                " WHERE id = ANY(%s)",
                ([old_open, old_pending, old_close, old_netting_close],))

        older_than = datetime.now(timezone.utc) - timedelta(seconds=30)
        expired = repo.fail_stale_mt5_opens(mt5_id, older_than)

        assert sorted(expired) == sorted([old_open, old_pending])
        assert [r["id"] for r in repo.mt5_commands_open(mt5_id)] == [
            old_close, old_netting_close, fresh_open]
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (message,) = conn.execute(
                "SELECT result->>'message' FROM mt5_commands WHERE id = %s", (old_open,)
            ).fetchone()
        assert message == "terminal offline"
        by_coid = {m["client_order_id"]: m for m in repo.mapping_rows(org_id=org_id)}
        assert by_coid[f"cm42.{mt5_id}"]["status"] == "failed"
        assert by_coid[f"cm42.{mt5_id}"]["error"] == "terminal offline"
        assert by_coid[f"co43.{mt5_id}"]["status"] == "failed"
        assert repo.fail_stale_mt5_opens(mt5_id, older_than) == []


class TestSymbolAliases:
    def test_roundtrip_and_manual_beats_auto(self, world):
        repo, _org_id, mt5_id = world
        assert repo.load_symbol_aliases(mt5_id) == {}
        repo.save_symbol_aliases(mt5_id, {"EURUSD": "EURUSD.r", "XAUUSD": "GOLD.r"}, "auto")
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r", "XAUUSD": "GOLD.r"}
        repo.save_symbol_aliases(mt5_id, {"XAUUSD": "XAUUSD.pro"}, "manual")
        # An auto pass never overwrites what the operator set by hand.
        repo.save_symbol_aliases(mt5_id, {"XAUUSD": "GOLDm", "EURUSD": "EURUSDm"}, "auto")
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSDm", "XAUUSD": "XAUUSD.pro"}
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            sources = dict(conn.execute(
                "SELECT canonical, source FROM symbol_aliases WHERE account_id = %s",
                (mt5_id,)).fetchall())
        assert sources == {"EURUSD": "auto", "XAUUSD": "manual"}

    def test_an_empty_broker_name_removes_the_alias(self, world):
        repo, _org_id, mt5_id = world
        repo.save_symbol_aliases(mt5_id, {"EURUSD": "EURUSD.r"}, "manual")
        repo.save_symbol_aliases(mt5_id, {"EURUSD": ""}, "manual")
        assert repo.load_symbol_aliases(mt5_id) == {}


class TestWatermark:
    def test_defaults_to_zero_and_only_moves_forward(self, world):
        repo, _org_id, mt5_id = world
        assert repo.mt5_watermark(mt5_id) == (0, 0)
        repo.set_mt5_watermark(mt5_id, 700005, 1_757_203_100_456)
        repo.set_mt5_watermark(mt5_id, 700003, 1_757_203_000_000)   # an older batch cannot rewind it
        assert repo.mt5_watermark(mt5_id) == (700005, 1_757_203_100_456)


def _deal(deal_id, ts, close=None, side="BUY", **extra):
    row = {"deal_id": deal_id, "order_id": deal_id + 1, "position_id": 9, "symbol_id": 77,
           "symbol": "EURUSD.r", "side": side, "volume": 100, "filled_volume": 100,
           "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
           "create_timestamp": ts, "execution_timestamp": ts, "close": close}
    row.update(extra)
    return row


class TestMt5Deals:
    def test_upsert_counts_new_rows_only(self, world):
        repo, org_id, mt5_id = world
        rows = [
            _deal(1, 1000, balance_after=9990.0),
            _deal(2, 2000, close={"entry_price": 1.1, "gross_profit": 5.0, "swap": 0.0,
                                  "commission": -0.03, "balance": 10000.0, "closed_volume": 100}),
        ]
        assert repo.upsert_mt5_deals(mt5_id, org_id, rows) == 2
        assert repo.upsert_mt5_deals(mt5_id, org_id, rows) == 0
        assert repo.upsert_mt5_deals(mt5_id, org_id, rows + [_deal(3, 3000)]) == 1
        closes = [d for d in repo.load_deals(mt5_id) if d["close"]]
        assert len(closes) == 1 and closes[0]["close"]["balance"] == 10000.0

    def test_estimated_balance_after_is_stored_for_deals_that_are_not_closes(self, world):
        repo, org_id, mt5_id = world
        repo.upsert_mt5_deals(mt5_id, org_id, [_deal(1, 1000, balance_after=9990.0)])
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT balance_after, is_close, gross_profit FROM deals"
                " WHERE account_id = %s AND deal_id = 1", (mt5_id,)).fetchone()
        assert (float(row[0]), row[1], row[2]) == (9990.0, False, None)

    def test_load_deals_filters_by_until_and_position(self, world):
        repo, org_id, mt5_id = world
        repo.upsert_mt5_deals(
            mt5_id, org_id, [_deal(1, 1000), _deal(2, 2000, position_id=10), _deal(3, 3000)])
        assert [d["deal_id"] for d in repo.load_deals(mt5_id, since_ms=1000, until_ms=2000)] == [1, 2]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id, position_id=10)] == [2]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id)] == [1, 2, 3]

    def test_cash_flow_reads_balance_operations_only(self, world):
        repo, org_id, mt5_id = world
        repo.upsert_mt5_deals(mt5_id, org_id, [
            _deal(1, 1000, side="BALANCE", volume=0, filled_volume=0, execution_price=None,
                  commission=None, gross_profit=500.0, balance_after=10500.0),
            _deal(2, 2000),
            _deal(3, 3000, side="CREDIT", volume=0, filled_volume=0, execution_price=None,
                  commission=None, gross_profit=-100.0, balance_after=10400.0),
        ])
        entries = repo.load_mt5_cash_flow(mt5_id, 0, 5000)
        assert entries == [
            {"deal_id": 1, "side": "BALANCE", "amount": 500.0, "balance_after": 10500.0,
             "timestamp": 1000},
            {"deal_id": 3, "side": "CREDIT", "amount": -100.0, "balance_after": 10400.0,
             "timestamp": 3000},
        ]
        assert repo.load_mt5_cash_flow(mt5_id, 1500, 5000) == entries[1:]


class TestNetting:
    """A netting follower's copies share one net position: several mapping
    rows with the same slave_position_id (ticket 9001 here)."""

    def _three_copies(self, repo, org_id, mt5_id):
        # Oldest first: 42 (100), 43 (50), 44 (30). created_at is what
        # "oldest" means, so it is made unambiguous rather than left to the
        # millisecond the rows happened to land in.
        for n, (master_id, volume) in enumerate(((42, 100), (43, 50), (44, 30))):
            coid = f"cm{master_id}.{mt5_id}"
            repo.create_position_mapping(master_id, mt5_id, coid, org_id=org_id)
            repo.activate_position_mapping(mt5_id, coid, 9001, volume)
            with psycopg.connect(repo.dsn, autocommit=True) as conn:
                conn.execute(
                    "UPDATE mappings SET created_at = now() - interval '10 seconds'"
                    " + %s * interval '1 second' WHERE client_order_id = %s",
                    (n, coid))

    def _by_coid(self, repo, org_id):
        return {m["client_order_id"]: (m["status"], m["slave_volume"])
                for m in repo.mapping_rows(org_id=org_id)}

    def test_mapping_side_reads_the_open_commands_payload(self, world):
        repo, org_id, mt5_id = world
        repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "SELL", "lots": 0.5, "sl": 0, "tp": 0,
             "comment": "copy:m42"}, f"cm42.{mt5_id}")
        # The mapping's later close is an opposite-side open under the
        # ':close' suffix; it never answers for the mapping's own side.
        repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 0.5, "sl": 0, "tp": 0,
             "comment": "close:m42"}, f"cm42.{mt5_id}:close")
        assert repo.mapping_side(f"cm42.{mt5_id}") == "SELL"
        assert repo.mt5_open_payload(f"cm42.{mt5_id}")["symbol"] == "EURUSD.r"
        assert repo.mapping_side(f"cm43.{mt5_id}") is None
        assert repo.mt5_open_payload(f"cm43.{mt5_id}") is None

    def test_fifo_reduction_consumes_the_oldest_mapping_first(self, world):
        repo, org_id, mt5_id = world
        self._three_copies(repo, org_id, mt5_id)
        touched = repo.reduce_position_mappings_fifo(mt5_id, 9001, 120)
        assert [(t["client_order_id"], t["master_position_id"], t["closed_volume"],
                 t["slave_volume"], t["status"]) for t in touched] == [
            (f"cm42.{mt5_id}", 42, 100, 0, "closed"), (f"cm43.{mt5_id}", 43, 20, 30, "active")]
        assert self._by_coid(repo, org_id) == {
            f"cm42.{mt5_id}": ("closed", 0), f"cm43.{mt5_id}": ("active", 30),
            f"cm44.{mt5_id}": ("active", 30)}

    def test_fifo_reduction_closes_everything_and_then_finds_nothing(self, world):
        repo, org_id, mt5_id = world
        self._three_copies(repo, org_id, mt5_id)
        touched = repo.reduce_position_mappings_fifo(mt5_id, 9001, 500)   # more than the copies hold
        assert [t["closed_volume"] for t in touched] == [100, 50, 30]
        assert {status for status, _v in self._by_coid(repo, org_id).values()} == {"closed"}
        assert repo.reduce_position_mappings_fifo(mt5_id, 9001, 10) == []
        assert repo.reduce_position_mappings_fifo(mt5_id, 9002, 10) == []
        assert repo.reduce_position_mappings_fifo(mt5_id, 9001, 0) == []

    def test_reduce_by_client_order_id_touches_only_that_mapping(self, world):
        repo, org_id, mt5_id = world
        self._three_copies(repo, org_id, mt5_id)
        repo.reduce_position_mapping(mt5_id, 9001, 20, client_order_id=f"cm43.{mt5_id}")
        assert self._by_coid(repo, org_id)[f"cm43.{mt5_id}"] == ("active", 30)
        # The coid alone names the mapping: a netting close that EMPTIES the
        # net position is acked with ticket 0 (plan 04 DoOpen reports the
        # position the terminal shows after the fill -- none), and the
        # ticket the ack carries must not decide whether the reduction lands.
        repo.reduce_position_mapping(mt5_id, 0, 30, client_order_id=f"cm43.{mt5_id}")
        assert self._by_coid(repo, org_id) == {
            f"cm42.{mt5_id}": ("active", 100), f"cm43.{mt5_id}": ("closed", 0),
            f"cm44.{mt5_id}": ("active", 30)}
        # Without the predicate the old behaviour stands: every active row on
        # the ticket is reduced (one mapping per position on a hedging account).
        repo.reduce_position_mapping(mt5_id, 9001, 30)
        assert self._by_coid(repo, org_id) == {
            f"cm42.{mt5_id}": ("active", 70), f"cm43.{mt5_id}": ("closed", 0),
            f"cm44.{mt5_id}": ("closed", 0)}
```

- [ ] **Step 3: Run the test to verify it fails**

Run DB `tests/unit/test_repo_mt5.py`.
Expected: FAIL — `test_load_accounts_reports_the_platform` with `AttributeError: 'AccountRow' object has no attribute 'platform'`; the rest with `AttributeError: 'Repo' object has no attribute 'upsert_mt5_link_hello'` (and similar).

- [ ] **Step 4: Implement**

In `copier/src/copier/db/repo.py`, replace the `AccountRow` dataclass (lines 39-51) with:

```python
@dataclass(frozen=True)
class AccountRow:
    """Account row representation."""
    account_id: int
    org_id: int
    connection_id: int | None      # NULL for MT5 accounts (migration 014)
    trader_login: int
    is_live: bool
    role: str
    enabled: bool
    multiplier: Decimal
    status: str
    last_error: str | None
    # 'ctrader' | 'mt5'. Defaulted so every existing keyword construction
    # (tests, routing) stays valid.
    platform: str = "ctrader"
```

Directly below it (still before `class Repo:`), add the module-level row builder:

```python
def _deal_row(account_id: int, org_id: int | None, d: dict) -> dict:
    """One `deals` row from a queries._map_deal-shaped dict.

    Two top-level fallbacks exist for MT5 deals, which have no
    closePositionDetail: `balance_after` (the estimate main.py computes for
    every deal, close or not) and `gross_profit` (a BALANCE/CREDIT
    operation's amount). A cTrader dict never carries them, so those rows
    are unchanged.
    """
    close = d.get('close') or {}
    return {
        'account_id': account_id,
        'deal_id': d['deal_id'],
        'org_id': org_id,
        'order_id': d.get('order_id'),
        'position_id': d.get('position_id'),
        'symbol_id': d.get('symbol_id'),
        'symbol': d.get('symbol'),
        'side': d.get('side'),
        'volume': d.get('volume'),
        'filled_volume': d.get('filled_volume'),
        'execution_price': d.get('execution_price'),
        'status': d.get('status'),
        'commission': d.get('commission'),
        'create_timestamp': d.get('create_timestamp'),
        'execution_timestamp': d['execution_timestamp'],
        'is_close': bool(d.get('close')),
        'entry_price': close.get('entry_price'),
        'gross_profit': close.get('gross_profit', d.get('gross_profit')),
        'swap': close.get('swap'),
        'balance_after': close.get('balance', d.get('balance_after')),
        'closed_volume': close.get('closed_volume'),
    }
```

Replace `load_accounts` (lines 352-377) with:

```python
    def load_accounts(self) -> list[AccountRow]:
        """Load all accounts."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ctid_trader_account_id, org_id, ctid_connection_id, trader_login, is_live,
                       role, enabled, multiplier, status, last_error, platform
                FROM accounts
                """
            ).fetchall()

        return [
            AccountRow(
                account_id=row[0],
                org_id=row[1],
                connection_id=row[2],
                trader_login=row[3],
                is_live=row[4],
                role=row[5],
                enabled=row[6],
                multiplier=row[7],  # Already a Decimal from psycopg
                status=row[8],
                last_error=row[9],
                platform=row[10],
            )
            for row in rows
        ]
```

Replace the body of `upsert_deals` (lines 792-830) with:

```python
    def upsert_deals(
        self, account_id: int, org_id: int | None, deals: list[dict]
    ) -> None:
        """Store deals in the shape queries._map_deal produces.

        Keyed (account_id, deal_id) and upserted, which is what lets the
        backfill re-run a window safely after a broker error. Queued on
        the async writer when one is attached, else written inline.
        """
        for d in deals:
            row = _deal_row(account_id, org_id, d)
            if self.writer is not None:
                self.writer.submit(
                    'deals', row, upsert_key=('account_id', 'deal_id'))
            else:
                self._upsert_deal_inline(row)
```

In `load_deals` (lines 847-919) change the signature to:

```python
    def load_deals(
        self, account_id: int, since_ms: int | None = None,
        limit: int = 50_000, until_ms: int | None = None,
        position_id: int | None = None,
    ) -> list[dict]:
```

and, right after the `since_ms` block (`params.append(since_ms)`) and before `sql += " ORDER BY execution_timestamp LIMIT %s"`, add:

```python
        if until_ms is not None:
            sql += " AND execution_timestamp <= %s"
            params.append(until_ms)
        if position_id is not None:
            sql += " AND position_id = %s"
            params.append(position_id)
```

Replace `reduce_position_mapping` (lines 1129-1153) with:

```python
    def reduce_position_mapping(
        self,
        slave_account_id: int,
        slave_position_id: int,
        closed_volume: int,
        client_order_id: str | None = None,
    ) -> None:
        """Reduce a position mapping by the closed volume.

        Sets status to 'closed' when slave_volume reaches 0.
        Atomic: uses single UPDATE with GREATEST to avoid TOCTOU race on concurrent reduces.

        `client_order_id`, when given, pins the reduction to ONE mapping. On
        a hedging account a slave position has exactly one mapping, so the
        (account, position) pair is enough; on a netting MT5 follower every
        copy on a symbol shares the symbol's single net position, and a close
        the copier itself queued names its mapping (the outbox's ':close'
        suffix, Task 7) -- reducing every row on that ticket would close
        copies the master still holds. When it is given it is the ONLY
        predicate besides the account -- the ticket is not consulted: the
        EA acks the net position the terminal shows AFTER the fill, which
        is 0 when that close emptied it (plan 04 DoOpen), and a
        client_order_id is unique on its own.
        """
        with self._connect() as conn:
            # Atomic single-statement update: decrement slave_volume safely,
            # set status='closed' when volume <= 0, update timestamp
            row = conn.execute(
                """
                UPDATE mappings
                SET slave_volume = GREATEST(slave_volume - %s, 0),
                    status = CASE WHEN slave_volume - %s <= 0 THEN 'closed' ELSE 'active' END,
                    updated_at = now()
                WHERE slave_account_id = %s AND status = 'active'
                  AND CASE WHEN %s::text IS NULL THEN slave_position_id = %s
                           ELSE client_order_id = %s END
                RETURNING slave_volume, status
                """,
                (closed_volume, closed_volume, slave_account_id,
                 client_order_id, slave_position_id, client_order_id),
            ).fetchone()
```

Insert the new section before `# ---------- partitions ----------` (line 1415):

```python
    # ---------- MT5 bridge ----------

    _MT5_LINK_COLUMNS = (
        "account_id", "login", "broker", "server", "currency", "hedging", "trade_mode",
        "leverage", "ea_version", "ea_build", "last_seen_at", "last_ip", "balance", "equity",
        "key_created_at", "created_at",
    )

    def load_mt5_link(self, account_id: int) -> dict | None:
        """The terminal-side facts of one MT5 account, or None when it has no
        link row. The key hash is deliberately never read here."""
        with self._connect() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                return cur.execute(
                    f"SELECT {', '.join(self._MT5_LINK_COLUMNS)} FROM mt5_links "
                    "WHERE account_id = %s",
                    (account_id,),
                ).fetchone()

    def upsert_mt5_link_hello(
        self, account_id: int, *, login, broker, server, currency, hedging, trade_mode,
        leverage, ea_version, ea_build,
    ) -> None:
        """Record what the terminal said about itself in its hello.

        The link row is created by the api together with its key hash (the
        copier never sees a key), so this only ever UPDATEs it. The login is
        also copied onto accounts.trader_login, which every 'cTID {id}'
        label in the dashboard falls back to.
        """
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mt5_links
                   SET login = %s, broker = %s, server = %s, currency = %s, hedging = %s,
                       trade_mode = %s, leverage = %s, ea_version = %s, ea_build = %s
                 WHERE account_id = %s
                """,
                (login, broker, server, currency, hedging, trade_mode, leverage,
                 ea_version, ea_build, account_id),
            )
            conn.execute(
                "UPDATE accounts SET trader_login = %s WHERE ctid_trader_account_id = %s",
                (login, account_id),
            )

    def touch_mt5_link(self, account_id: int, *, balance, equity, seen_at) -> None:
        """Stamp a sync report's balance/equity and its arrival time."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE mt5_links SET balance = %s, equity = %s, last_seen_at = %s "
                "WHERE account_id = %s",
                (balance, equity, seen_at, account_id),
            )

    def enqueue_mt5_command(
        self, account_id: int, org_id: int, kind: str, payload: dict,
        client_order_id: str | None,
    ) -> int:
        with self._connect() as conn:
            (command_id,) = conn.execute(
                """
                INSERT INTO mt5_commands (account_id, org_id, kind, payload, client_order_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (account_id, org_id, kind, Jsonb(payload), client_order_id),
            ).fetchone()
        return command_id

    def mt5_commands_open(self, account_id: int) -> list[dict]:
        """Every queued or sent command of one account, in id order."""
        with self._connect() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                return cur.execute(
                    """
                    SELECT id, account_id, org_id, kind, payload, client_order_id, status,
                           attempts, created_at, sent_at
                      FROM mt5_commands
                     WHERE account_id = %s AND status IN ('queued', 'sent')
                     ORDER BY id
                    """,
                    (account_id,),
                ).fetchall()

    def mark_mt5_commands_sent(self, ids: list[int], sent_at) -> None:
        """Handed to the terminal (again): status 'sent', attempts + 1."""
        if not ids:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE mt5_commands SET status = 'sent', sent_at = %s, attempts = attempts + 1 "
                "WHERE id = ANY(%s)",
                (sent_at, [int(i) for i in ids]),
            )

    def complete_mt5_command(
        self, command_id: int, ok: bool, result: dict, done_at, account_id: int | None = None,
    ) -> dict | None:
        """Settle one command on its ack.

        Returns the row (id, account_id, org_id, kind, client_order_id,
        payload) or None when the id is unknown, already settled, or -- with
        `account_id` given -- belongs to another account. A duplicate or
        foreign ack therefore changes nothing, which is what makes the
        terminal's re-acks safe.
        """
        with self._connect() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                return cur.execute(
                    """
                    UPDATE mt5_commands
                       SET status = %s, result = %s, done_at = %s
                     WHERE id = %s AND status IN ('queued', 'sent')
                       AND (%s::bigint IS NULL OR account_id = %s)
                    RETURNING id, account_id, org_id, kind, client_order_id, payload
                    """,
                    ('done' if ok else 'failed', Jsonb(result), done_at, command_id,
                     account_id, account_id),
                ).fetchone()

    def fail_stale_mt5_opens(self, account_id: int, older_than) -> list[int]:
        """Expire market copies the terminal never picked up.

        A market copy delivered minutes late is a different trade, so an
        open/place_pending created before `older_than` and still not done
        is failed ("terminal offline"), and the pending mapping rows those
        commands were created for are failed with it -- otherwise the
        Positions screen would show a copy pending forever. Closes, amends
        and cancels never expire -- nor does a netting follower's close,
        which is an `open` whose client_order_id ends in ':close' (Task 7).
        Returns the expired command ids.
        """
        result = Jsonb({"ok": False, "retcode": 0, "message": "terminal offline",
                        "position": None, "deal": None, "order": None, "price": None,
                        "lots": None})
        with self._connect() as conn:
            rows = conn.execute(
                """
                UPDATE mt5_commands
                   SET status = 'failed', result = %s, done_at = now()
                 WHERE account_id = %s AND kind IN ('open', 'place_pending')
                   AND status IN ('queued', 'sent') AND created_at < %s
                   AND (client_order_id IS NULL OR client_order_id NOT LIKE '%%:close')
                RETURNING id, client_order_id
                """,
                (result, account_id, older_than),
            ).fetchall()
            coids = [r[1] for r in rows if r[1]]
            if coids:
                conn.execute(
                    """
                    UPDATE mappings
                       SET status = 'failed', error = 'terminal offline', updated_at = now()
                     WHERE slave_account_id = %s AND client_order_id = ANY(%s)
                       AND status = 'pending'
                    """,
                    (account_id, coids),
                )
        return [r[0] for r in rows]

    def load_symbol_aliases(self, account_id: int) -> dict[str, str]:
        """canonical -> broker_name for one MT5 account."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT canonical, broker_name FROM symbol_aliases WHERE account_id = %s",
                (account_id,),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def save_symbol_aliases(self, account_id: int, aliases: dict[str, str], source: str) -> None:
        """Upsert canonical -> broker_name. An 'auto' write never overwrites
        a 'manual' row; an empty broker_name removes the alias."""
        with self._connect() as conn:
            with conn.transaction():
                for canonical, broker_name in aliases.items():
                    if not broker_name:
                        conn.execute(
                            "DELETE FROM symbol_aliases WHERE account_id = %s AND canonical = %s",
                            (account_id, canonical),
                        )
                        continue
                    conn.execute(
                        """
                        INSERT INTO symbol_aliases (account_id, canonical, broker_name, source)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (account_id, canonical) DO UPDATE
                            SET broker_name = EXCLUDED.broker_name, source = EXCLUDED.source
                          WHERE NOT (symbol_aliases.source = 'manual' AND EXCLUDED.source = 'auto')
                        """,
                        (account_id, canonical, broker_name, source),
                    )

    def mt5_watermark(self, account_id: int) -> tuple[int, int]:
        """(last_deal_ticket, last_deal_time_ms); (0, 0) before any deal."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_deal_ticket, last_deal_time_ms FROM mt5_deal_watermark "
                "WHERE account_id = %s",
                (account_id,),
            ).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def set_mt5_watermark(self, account_id: int, ticket: int, time_ms: int) -> None:
        """Advance the watermark; it never moves backwards."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mt5_deal_watermark (account_id, last_deal_ticket, last_deal_time_ms)
                VALUES (%s, %s, %s)
                ON CONFLICT (account_id) DO UPDATE SET
                    last_deal_ticket = GREATEST(mt5_deal_watermark.last_deal_ticket,
                                                EXCLUDED.last_deal_ticket),
                    last_deal_time_ms = GREATEST(mt5_deal_watermark.last_deal_time_ms,
                                                 EXCLUDED.last_deal_time_ms),
                    updated_at = now()
                """,
                (account_id, ticket, time_ms),
            )

    def upsert_mt5_deals(self, account_id: int, org_id: int | None, rows: list[dict]) -> int:
        """Store MT5 deals (queries._map_deal's shape, `balance_after`
        estimated by the caller) and return how many were NEW.

        Inline and ON CONFLICT DO NOTHING rather than the writer: the count
        is what makes a re-sent batch harmless, and the caller advances the
        watermark only once the rows are down.
        """
        inserted = 0
        with self._connect() as conn:
            for d in rows:
                row = _deal_row(account_id, org_id, d)
                columns = tuple(sorted(row.keys()))
                cursor = conn.execute(
                    f"INSERT INTO deals ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))}) "
                    "ON CONFLICT (account_id, deal_id) DO NOTHING",
                    [row[c] for c in columns],
                )
                inserted += cursor.rowcount
        return inserted

    def load_mt5_cash_flow(self, account_id: int, from_ms: int, to_ms: int) -> list[dict]:
        """BALANCE/CREDIT deals of one MT5 account in [from_ms, to_ms]: the
        cash-flow history the History page shows for cTrader accounts."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT deal_id, side, gross_profit, balance_after, execution_timestamp
                  FROM deals
                 WHERE account_id = %s AND side IN ('BALANCE', 'CREDIT')
                   AND execution_timestamp BETWEEN %s AND %s
                 ORDER BY execution_timestamp, deal_id
                """,
                (account_id, from_ms, to_ms),
            ).fetchall()
        return [
            {"deal_id": r[0], "side": r[1],
             "amount": float(r[2]) if r[2] is not None else 0.0,
             "balance_after": float(r[3]) if r[3] is not None else None,
             "timestamp": r[4]}
            for r in rows
        ]

    # ---------- netting followers ----------

    def mt5_open_payload(self, client_order_id: str) -> dict | None:
        """The payload of the 'open' command queued under that coid (symbol,
        side, lots, ...), or None. A position increase queues a second open
        with the same coid and the same symbol and side, so the first is as
        good as any; the ':close' commands carry a different coid and never
        answer here."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM mt5_commands
                 WHERE client_order_id = %s AND kind = 'open'
                 ORDER BY id LIMIT 1
                """,
                (client_order_id,),
            ).fetchone()
        return row[0] if row else None

    def mapping_side(self, client_order_id: str) -> str | None:
        """The side a copy was opened on ("BUY" | "SELL"), read from its open
        command: the mapping row has no side, and a netting terminal's net
        position may not even show the copy's side."""
        payload = self.mt5_open_payload(client_order_id) or {}
        side = payload.get("side")
        return side if side in ("BUY", "SELL") else None

    def reduce_position_mappings_fifo(
        self, slave_account_id: int, slave_position_id: int, closed_volume: int,
    ) -> list[dict]:
        """A close the terminal reported on a netting follower's net position
        (a stop, a target, the owner closing by hand): the copies sharing
        that ticket are reduced OLDEST FIRST until the closed volume is used
        up; a row that reaches 0 is closed. Returns the rows touched (oldest
        first) with the volume taken from each as `closed_volume`, so the
        caller can log one event per copy.

        One transaction with the rows locked, so a concurrent ack on the
        same ticket cannot interleave with the walk.
        """
        touched: list[dict] = []
        remaining = int(closed_volume)
        if remaining <= 0:
            return touched
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT id, client_order_id, master_position_id, slave_volume
                          FROM mappings
                         WHERE slave_account_id = %s AND slave_position_id = %s
                           AND status = 'active'
                         ORDER BY created_at, id
                         FOR UPDATE
                        """,
                        (slave_account_id, slave_position_id),
                    ).fetchall()
                    for row in rows:
                        if remaining <= 0:
                            break
                        held = int(row["slave_volume"] or 0)
                        if held <= 0:
                            continue
                        take = min(held, remaining)
                        remaining -= take
                        left = held - take
                        cur.execute(
                            """
                            UPDATE mappings
                               SET slave_volume = %s,
                                   status = CASE WHEN %s <= 0 THEN 'closed' ELSE 'active' END,
                                   updated_at = now()
                             WHERE id = %s
                            """,
                            (left, left, row["id"]),
                        )
                        touched.append({
                            "id": row["id"], "client_order_id": row["client_order_id"],
                            "master_position_id": row["master_position_id"],
                            "closed_volume": take, "slave_volume": left,
                            "status": "closed" if left <= 0 else "active",
                        })
        return touched
```

- [ ] **Step 5: Run the tests to verify they pass**

Run DB `tests/unit/test_repo_mt5.py`.
Expected: `21 passed`.

Run DB `tests/unit/test_repo.py`, DB `tests/unit/test_repo_orgs.py`, DB `tests/unit/test_routing.py`, DB `tests/unit/test_main.py`.
Expected: all pass — `AccountRow.platform` is defaulted, `_deal_row` reproduces the old row exactly for cTrader dicts.

- [ ] **Step 6: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/db/repo.py copier/tests/unit/conftest.py copier/tests/unit/test_repo_mt5.py && git commit -m "feat(repo): MT5 links, command queue, symbol aliases, deal watermark, MT5 deals

AccountRow carries platform (default ctrader) and an optional
connection_id. The command queue settles on ack exactly once (duplicate
and foreign acks are no-ops), stale opens expire with their pending
mappings, manual aliases survive auto passes, the watermark never
rewinds, and MT5 deals insert idempotently with an estimated
balance_after. Netting followers: a mapping's side comes from its open
command, copies sharing a net position are reduced oldest-first or one
at a time by client_order_id, and a netting close never expires.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Migration 015 — `mt5_net_ledger` and its repo accessors

**Files:**
- Create: `db/migrations/015_mt5_net_ledger.sql`
- Modify: `copier/src/copier/db/repo.py` — append to the `# ---------- MT5 bridge ----------` section (Task 4), after `reduce_position_mappings_fifo`
- Test: `copier/tests/unit/test_migration_015.py`; `copier/tests/unit/test_repo_mt5.py` (append a class)

**Interfaces:**
- Consumes: migration 014 (Task 1); `db/migrate.py` applies every `db/migrations/*.sql` in filename order and records it in `schema_migrations`.
- Produces (contract §1 `mt5_net_ledger`, contract §2 "Repo additions", used by Tasks 9 and 15): the table `mt5_net_ledger(account_id, virtual_id, symbol, side, volume_open, volume_left, stop_loss, take_profit, opened_at_ms)` with primary key `(account_id, virtual_id)` and index `mt5_net_ledger_by_symbol`; `Repo.load_net_ledger(account_id) -> list[dict]` (keys `virtual_id, symbol, side, volume_open, volume_left, stop_loss, take_profit, opened_at_ms`, ordered by `(opened_at_ms, virtual_id)`), `Repo.upsert_net_ledger(account_id, rows) -> None` (rows in that key shape; `volume_left`, `stop_loss`, `take_profit` updated on conflict), `Repo.delete_net_ledger(account_id, virtual_ids) -> None`.
- Why here and not in Task 4: the accessors need the table, and every task must leave the suite green on its own. Deploy note: migrations run in the `migrate` compose service — rebuild it (`docker compose build migrate`) when deploying; building `api`/`copier` alone silently skips a new migration.

- [ ] **Step 1: Write the failing tests**

Create `copier/tests/unit/test_migration_015.py`:

```python
"""Migration 015: the netting master's ledger table (mt5_net_ledger).

Schema-level assertions only -- the ledger's behaviour is covered by
test_mt5_ingress.py (NetLedger) and test_repo_mt5.py (the accessors)."""

import psycopg
import pytest


def _seed_mt5_account(conn):
    (org_id,) = conn.execute(
        "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    (account_id,) = conn.execute(
        "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
        " trader_login, is_live, role, platform)"
        " VALUES (nextval('mt5_account_id_seq'), %s, NULL, 0, false, 'master', 'mt5')"
        " RETURNING ctid_trader_account_id", (org_id,)).fetchone()
    return account_id


def _row(account_id, virtual_id, **extra):
    row = {"account_id": account_id, "virtual_id": virtual_id, "symbol": "XAUUSD.r",
           "side": "BUY", "volume_open": 50, "volume_left": 50, "stop_loss": None,
           "take_profit": None, "opened_at_ms": 1_757_203_100_000}
    row.update(extra)
    return row


def _insert(conn, row):
    conn.execute(
        "INSERT INTO mt5_net_ledger (account_id, virtual_id, symbol, side, volume_open,"
        " volume_left, stop_loss, take_profit, opened_at_ms)"
        " VALUES (%(account_id)s, %(virtual_id)s, %(symbol)s, %(side)s, %(volume_open)s,"
        " %(volume_left)s, %(stop_loss)s, %(take_profit)s, %(opened_at_ms)s)", row)


def test_migration_015_is_recorded_right_after_014(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "015_mt5_net_ledger.sql" in names
    assert names.index("015_mt5_net_ledger.sql") == names.index("014_mt5_bridge.sql") + 1


def test_table_has_the_contracts_columns(db):
    with psycopg.connect(db, autocommit=True) as conn:
        columns = dict(conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'mt5_net_ledger'").fetchall())
    assert set(columns) == {"account_id", "virtual_id", "symbol", "side", "volume_open",
                            "volume_left", "stop_loss", "take_profit", "opened_at_ms"}
    assert {c for c, nullable in columns.items() if nullable == "YES"} == {
        "stop_loss", "take_profit"}


def test_one_row_per_virtual_position_per_account(db):
    with psycopg.connect(db, autocommit=True) as conn:
        account_id = _seed_mt5_account(conn)
        _insert(conn, _row(account_id, 700001))
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, _row(account_id, 700001))
        _insert(conn, _row(account_id, 700002, side="SELL"))
        (n,) = conn.execute("SELECT count(*) FROM mt5_net_ledger").fetchone()
    assert n == 2


def test_side_is_constrained(db):
    with psycopg.connect(db, autocommit=True) as conn:
        account_id = _seed_mt5_account(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, _row(account_id, 700001, side="LONG"))


def test_rows_cascade_from_accounts(db):
    with psycopg.connect(db, autocommit=True) as conn:
        account_id = _seed_mt5_account(conn)
        _insert(conn, _row(account_id, 700001))
        conn.execute("DELETE FROM accounts WHERE ctid_trader_account_id = %s", (account_id,))
        (n,) = conn.execute("SELECT count(*) FROM mt5_net_ledger").fetchone()
    assert n == 0


def test_the_symbol_index_exists(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'mt5_net_ledger'"
            " AND indexname = 'mt5_net_ledger_by_symbol'").fetchone()
    assert n == 1
```

Append to `copier/tests/unit/test_repo_mt5.py` (after `TestNetting`):

```python


class TestNetLedger:
    """The netting master's virtual positions, persisted so a restart keeps them."""

    def _row(self, virtual_id, **extra):
        row = {"virtual_id": virtual_id, "symbol": "XAUUSD.r", "side": "BUY", "volume_open": 50,
               "volume_left": 50, "stop_loss": None, "take_profit": None,
               "opened_at_ms": 1_757_203_100_000 + virtual_id}
        row.update(extra)
        return row

    def test_roundtrip_in_open_order(self, world):
        repo, _org_id, mt5_id = world
        assert repo.load_net_ledger(mt5_id) == []
        repo.upsert_net_ledger(mt5_id, [self._row(700003), self._row(700001, stop_loss=2390.0)])
        assert repo.load_net_ledger(mt5_id) == [
            self._row(700001, stop_loss=2390.0), self._row(700003)]

    def test_upsert_updates_volume_and_protection_in_place(self, world):
        repo, _org_id, mt5_id = world
        repo.upsert_net_ledger(mt5_id, [self._row(700001)])
        repo.upsert_net_ledger(mt5_id, [self._row(700001, volume_left=20, take_profit=2420.0)])
        repo.upsert_net_ledger(mt5_id, [])                        # nothing to do, no error
        (row,) = repo.load_net_ledger(mt5_id)
        assert (row["volume_open"], row["volume_left"], row["take_profit"]) == (50, 20, 2420.0)

    def test_delete_removes_only_the_named_rows_of_that_account(self, world, seed_mt5_account):
        repo, org_id, mt5_id = world
        other = seed_mt5_account(org_id)
        repo.upsert_net_ledger(mt5_id, [self._row(700001), self._row(700002)])
        repo.upsert_net_ledger(other, [self._row(700001)])
        repo.delete_net_ledger(mt5_id, [700001, 424242])
        repo.delete_net_ledger(mt5_id, [])
        assert [r["virtual_id"] for r in repo.load_net_ledger(mt5_id)] == [700002]
        assert [r["virtual_id"] for r in repo.load_net_ledger(other)] == [700001]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run DB `tests/unit/test_migration_015.py`.
Expected: FAIL — `test_migration_015_is_recorded_right_after_014` with `AssertionError` (`'015_mt5_net_ledger.sql' in names`), the rest with `UndefinedTable: relation "mt5_net_ledger" does not exist`.

Run DB `tests/unit/test_repo_mt5.py`.
Expected: `21 passed`, and the three `TestNetLedger` tests FAIL with `AttributeError: 'Repo' object has no attribute 'load_net_ledger'`.

- [ ] **Step 3: Write the migration and the accessors**

Create `db/migrations/015_mt5_net_ledger.sql` (the DDL verbatim from contract §1):

```sql
-- Netting masters: the net position expanded into virtual master positions (spec "Netting accounts")
CREATE TABLE mt5_net_ledger (
    account_id   BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    virtual_id   BIGINT NOT NULL,                 -- the IN deal's ticket
    symbol       TEXT NOT NULL,                   -- broker name
    side         TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    volume_open  INTEGER NOT NULL,                -- centilots at open
    volume_left  INTEGER NOT NULL,                -- centilots still open
    stop_loss    DOUBLE PRECISION,
    take_profit  DOUBLE PRECISION,
    opened_at_ms BIGINT NOT NULL,
    PRIMARY KEY (account_id, virtual_id)
);
CREATE INDEX mt5_net_ledger_by_symbol ON mt5_net_ledger (account_id, symbol, opened_at_ms);
```

In `copier/src/copier/db/repo.py`, append to the `# ---------- MT5 bridge ----------` section, right after `reduce_position_mappings_fifo` (Task 4) and before `# ---------- partitions ----------`:

```python
    # ---------- netting masters: the virtual-position ledger ----------

    _NET_LEDGER_COLUMNS = ("virtual_id", "symbol", "side", "volume_open", "volume_left",
                           "stop_loss", "take_profit", "opened_at_ms")

    def load_net_ledger(self, account_id: int) -> list[dict]:
        """Every virtual master position of a netting account, oldest first
        -- the order the ledger consumes them in (copier/mt5/ingress.py)."""
        with self._connect() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                return cur.execute(
                    f"SELECT {', '.join(self._NET_LEDGER_COLUMNS)} FROM mt5_net_ledger "
                    "WHERE account_id = %s ORDER BY opened_at_ms, virtual_id",
                    (account_id,),
                ).fetchall()

    def upsert_net_ledger(self, account_id: int, rows: list[dict]) -> None:
        """Insert new virtual positions; refresh what changes on an existing
        one (what is left of it and its protection). One transaction, so a
        report's worth of ledger changes lands whole or not at all."""
        if not rows:
            return
        with self._connect() as conn:
            with conn.transaction():
                for row in rows:
                    conn.execute(
                        """
                        INSERT INTO mt5_net_ledger (account_id, virtual_id, symbol, side,
                                                    volume_open, volume_left, stop_loss,
                                                    take_profit, opened_at_ms)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (account_id, virtual_id) DO UPDATE SET
                            volume_left = EXCLUDED.volume_left,
                            stop_loss = EXCLUDED.stop_loss,
                            take_profit = EXCLUDED.take_profit
                        """,
                        (account_id, row["virtual_id"], row["symbol"], row["side"],
                         row["volume_open"], row["volume_left"], row.get("stop_loss"),
                         row.get("take_profit"), row["opened_at_ms"]),
                    )

    def delete_net_ledger(self, account_id: int, virtual_ids: list[int]) -> None:
        """Virtual positions that were consumed to zero."""
        if not virtual_ids:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM mt5_net_ledger WHERE account_id = %s AND virtual_id = ANY(%s)",
                (account_id, [int(v) for v in virtual_ids]),
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_migration_015.py`.
Expected: `6 passed`.

Run DB `tests/unit/test_repo_mt5.py`.
Expected: `24 passed`.

Run DB `tests/unit/test_migration_014.py`.
Expected: all pass (the 014 checks are unaffected by a later migration).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add db/migrations/015_mt5_net_ledger.sql copier/src/copier/db/repo.py copier/tests/unit/test_migration_015.py copier/tests/unit/test_repo_mt5.py && git commit -m "feat(db): migration 015 -- the netting master's virtual-position ledger

A netting MT5 master's single net position per symbol is expanded into
virtual master positions so followers copy every add and reduction one
by one; the ledger persists them across a copier restart. The repo
loads, upserts and deletes its rows.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `copier.mt5.registry` — the terminal's book in memory

**Files:**
- Create: `copier/src/copier/mt5/registry.py`, `copier/src/copier/testing/mt5_fixtures.py`
- Modify: `copier/src/copier/engine/reconcile.py:81-97` (`OrderSnapshot` gains `stop_loss`/`take_profit`)
- Test: `copier/tests/unit/test_mt5_registry.py`

**Interfaces:**
- Consumes: `Repo.save_symbol_cache/load_symbol_cache/upsert_positions/close_missing_positions/mapping_rows/load_mt5_link` (`copier/src/copier/db/repo.py`), `PositionSnapshot`/`OrderSnapshot` (`engine/reconcile.py:46-97`), `symbol_infos_from_hello` (Task 3), the protocol dataclasses (Task 2).
- Produces (contract §2 `registry.py`, used by Tasks 9, 12, 14, 15): `OFFLINE_AFTER_S = 15.0`; `MT5Registry(repo, clock=None)` with `update_from_hello(account_id, org_id, hello, now)`, `update_from_sync(account_id, org_id, report, now)`, `snapshot(account_id, with_labels=True) -> tuple[list[PositionSnapshot], list[OrderSnapshot]] | None`, `account_block(account_id) -> dict | None`, `last_seen(account_id) -> float | None`, `is_online(account_id, now) -> bool`, `hedging(account_id) -> bool | None`, `margin_mode(account_id) -> str | None` (`"hedging"` | `"netting"`; `None` before any hello — read from the link row after a restart, like `hedging`), `symbol_by_name(account_id, name) -> SymbolInfo | None`; private additions `symbols_by_name(account_id) -> dict[str, SymbolInfo]`, `report(account_id) -> SyncReport | None`, `position(account_id, ticket) -> ReportPosition | None`. The netting master's ledger views — `net_ledger(account_id)`, `snapshot(..., virtual=True)`, `account_block(..., virtual=True)`, `persist_virtual_positions(account_id)`, `update_from_sync(..., persist=)` — are added to this module in Task 9, where `NetLedger` exists. `OrderSnapshot.stop_loss: float | None = None`, `OrderSnapshot.take_profit: float | None = None`. Test builders `copier.testing.mt5_fixtures.position/order/deal/ack/report`.

- [ ] **Step 1: Add the report builders shared by the MT5 tests**

Create `copier/src/copier/testing/mt5_fixtures.py`:

```python
"""Builders for MT5 reports, shared by the unit tests of the MT5 lane.

Every argument has the value a plain 1.00-lot EURUSD.r position/deal would
carry, so a test names only what it is about.
"""

from copier.mt5.protocol import Ack, ReportDeal, ReportOrder, ReportPosition, SyncReport

TS_MS = 1_757_203_200_123


def position(ticket, symbol="EURUSD.r", side="BUY", volume=100, open_price=1.1, sl=None, tp=None,
             price=1.101, pnl=1.0, swap=0.0, comment="", magic=20260907,
             opened_at_ms=1_757_203_100_000) -> ReportPosition:
    return ReportPosition(
        ticket=ticket, symbol=symbol, side=side, volume=volume, open_price=open_price,
        stop_loss=sl, take_profit=tp, current_price=price, pnl=pnl, swap=swap,
        comment=comment, magic=magic, opened_at_ms=opened_at_ms)


def order(ticket, symbol="EURUSD.r", order_type="BUY_LIMIT", volume=100, price=1.09, sl=None,
          tp=None, comment="", magic=20260907) -> ReportOrder:
    return ReportOrder(
        ticket=ticket, symbol=symbol, order_type=order_type, volume=volume, price=price,
        stop_loss=sl, take_profit=tp, comment=comment, magic=magic)


def deal(ticket, position=0, order=0, symbol="EURUSD.r", deal_type="BUY", entry="IN", volume=100,
         price=1.1, profit=0.0, swap=0.0, commission=0.0, time_ms=TS_MS, comment="",
         magic=20260907) -> ReportDeal:
    return ReportDeal(
        ticket=ticket, position=position, order=order, symbol=symbol, deal_type=deal_type,
        entry=entry, volume=volume, price=price, profit=profit, swap=swap,
        commission=commission, time_ms=time_ms, comment=comment, magic=magic)


def ack(command_id, ok=True, retcode=10009, message="done", position=None, deal=None,
        order=None, price=None, volume=None) -> Ack:
    return Ack(command_id=command_id, ok=ok, retcode=retcode, message=message,
               position=position, deal=deal, order=order, price=price, volume=volume)


def report(positions=(), orders=(), deals=(), acks=(), seq=1, ts_ms=TS_MS, balance=10000.0,
           equity=None, margin=0.0, margin_free=None) -> SyncReport:
    return SyncReport(
        seq=seq, ts_ms=ts_ms, balance=balance,
        equity=balance if equity is None else equity, margin=margin,
        margin_free=balance if margin_free is None else margin_free,
        positions=list(positions), orders=list(orders), deals=list(deals), acks=list(acks))
```

- [ ] **Step 2: Write the failing test**

Create `copier/tests/unit/test_mt5_registry.py`:

```python
"""MT5Registry (copier/src/copier/mt5/registry.py): the in-memory book of
every MT5 terminal, fed by its reports and read by reconcile, get_state,
flatten and the queries."""

import psycopg
import pytest

from copier.db.repo import Repo
from copier.domain.models import Side
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.protocol import HelloReport, HelloSymbol
from copier.mt5.registry import OFFLINE_AFTER_S, MT5Registry
from copier.testing.mt5_fixtures import order, position, report

EURUSD = HelloSymbol("EURUSD.r", 5, 100000.0, 0.01, 0.01, 100.0, 4)
XAUUSD = HelloSymbol("XAUUSD.r", 2, 100.0, 0.01, 0.01, 50.0, 4)


def _hello(symbols, hedging=True, chunk=1, chunks=1):
    return HelloReport(
        ea_version="1.0.0", ea_build=4400, login=12345678, broker="B", server="S",
        currency="USD", hedging=hedging, trade_mode="demo", leverage=100,
        symbols=list(symbols), chunk=chunk, chunks=chunks)


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    mt5_id = seed_mt5_account(org_id)
    repo = Repo(db)
    return repo, org_id, mt5_id, MT5Registry(repo)


class TestHello:
    def test_stores_symbols_and_persists_the_cache(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD, XAUUSD]), now=100.0)
        info = registry.symbol_by_name(mt5_id, "XAUUSD.r")
        assert (info.name, info.digits, info.lot_size, info.min_volume, info.step_volume) == (
            "XAUUSD.r", 2, 100, 1, 1)
        assert registry.symbol_by_name(mt5_id, "GBPJPY.r") is None
        cached = repo.load_symbol_cache(mt5_id)
        assert set(cached) == {"EURUSD.r", "XAUUSD.r"} and cached["XAUUSD.r"] == info
        assert registry.hedging(mt5_id) is True and registry.last_seen(mt5_id) == 100.0
        assert registry.margin_mode(mt5_id) == "hedging"

    def test_chunks_accumulate_and_persist_on_the_last_one(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD], chunk=1, chunks=2), now=1.0)
        assert repo.load_symbol_cache(mt5_id) == {}
        registry.update_from_hello(mt5_id, org_id, _hello([XAUUSD], chunk=2, chunks=2), now=2.0)
        assert set(repo.load_symbol_cache(mt5_id)) == {"EURUSD.r", "XAUUSD.r"}
        ids = {registry.symbol_by_name(mt5_id, n).symbol_id for n in ("EURUSD.r", "XAUUSD.r")}
        assert len(ids) == 2

    def test_a_new_first_chunk_replaces_the_old_list(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD, XAUUSD]), now=1.0)
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=2.0)
        assert set(repo.load_symbol_cache(mt5_id)) == {"EURUSD.r"}


class TestRestart:
    def test_symbols_and_hedging_come_back_from_postgres(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD], hedging=False), now=1.0)
        repo.upsert_mt5_link_hello(
            mt5_id, login=1, broker="B", server="S", currency="USD", hedging=False,
            trade_mode="demo", leverage=100, ea_version="1.0.0", ea_build=4400)
        fresh = MT5Registry(repo)   # the copier restarted: memory is gone
        assert fresh.symbol_by_name(mt5_id, "EURUSD.r").lot_size == 100
        assert fresh.hedging(mt5_id) is False and fresh.margin_mode(mt5_id) == "netting"
        assert fresh.report(mt5_id) is None and fresh.snapshot(mt5_id) is None
        assert fresh.account_block(mt5_id) is None and fresh.position(mt5_id, 1) is None

    def test_unknown_account_answers_nothing(self, world):
        _repo, _org_id, _mt5_id, registry = world
        assert registry.hedging(424242) is None and registry.last_seen(424242) is None
        assert registry.margin_mode(424242) is None
        assert registry.is_online(424242, now=5.0) is False


class TestSync:
    def test_persists_positions_and_closes_vanished_ones(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        registry.update_from_sync(mt5_id, org_id, report([
            position(7001, volume=100, comment="copy:m42"), position(7002, volume=50)]), now=2.0)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, symbol, side, volume, label, status, org_id FROM positions"
                " WHERE account_id = %s ORDER BY position_id", (mt5_id,)).fetchall()
        assert rows == [(7001, "EURUSD.r", "BUY", 100, "copy:m42", "open", org_id),
                        (7002, "EURUSD.r", "BUY", 50, None, "open", org_id)]

        registry.update_from_sync(mt5_id, org_id, report(
            [position(7001, volume=60, comment="copy:m42")], seq=2), now=3.0)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions"
                " WHERE account_id = %s ORDER BY position_id", (mt5_id,)).fetchall()
        assert rows == [(7001, 60, "open"), (7002, 50, "closed")]
        assert registry.report(mt5_id).seq == 2
        assert registry.position(mt5_id, 7001).volume == 60
        assert registry.position(mt5_id, 7002) is None

    def test_an_unchanged_book_is_not_rewritten(self, world, monkeypatch):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        calls = []
        original = repo.upsert_positions

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(repo, "upsert_positions", counting)
        registry.update_from_sync(mt5_id, org_id, report([position(7001)]), now=2.0)
        # A moving P&L is not a changed book.
        registry.update_from_sync(mt5_id, org_id, report([position(7001, pnl=2.5)], seq=2), now=2.25)
        assert len(calls) == 1
        registry.update_from_sync(mt5_id, org_id, report([position(7001, sl=1.05)], seq=3), now=2.5)
        assert len(calls) == 2


class TestSnapshot:
    def test_labels_come_from_mappings_else_the_comment(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        repo.activate_position_mapping(mt5_id, f"cm42.{mt5_id}", 7001, 100)
        repo.create_order_mapping(9, mt5_id, f"co9.{mt5_id}", org_id=org_id)
        repo.activate_order_mapping(mt5_id, f"co9.{mt5_id}", 5551)
        registry.update_from_sync(mt5_id, org_id, report(
            positions=[position(7001, comment="wiped by broker", sl=1.05, tp=1.15),
                       position(7002, comment="manual")],
            orders=[order(5551, order_type="SELL_STOP", price=1.08, sl=1.09),
                    order(5552, comment="mine")]), now=2.0)

        positions, orders = registry.snapshot(mt5_id)

        eurusd_id = registry.symbol_by_name(mt5_id, "EURUSD.r").symbol_id
        assert positions == [
            PositionSnapshot(position_id=7001, symbol_id=eurusd_id, side=Side.BUY, volume=100,
                             price=1.1, label="copy:m42", stop_loss=1.05, take_profit=1.15),
            PositionSnapshot(position_id=7002, symbol_id=eurusd_id, side=Side.BUY, volume=100,
                             price=1.1, label="manual"),
        ]
        assert orders == [
            OrderSnapshot(order_id=5551, symbol_id=eurusd_id, volume=100, label="copy:o9",
                          side=Side.SELL, order_type="STOP", price=1.08, stop_loss=1.09,
                          take_profit=None),
            OrderSnapshot(order_id=5552, symbol_id=eurusd_id, volume=100, label="mine",
                          side=Side.BUY, order_type="LIMIT", price=1.09),
        ]
        assert registry.snapshot(mt5_id, with_labels=False)[0][0].label == "wiped by broker"

    def test_a_filled_pending_copy_keeps_its_order_label_on_the_position(self, world):
        """compute_drift's unfilled-order check looks for copy:o<order> on
        the slave position a linked pending copy became."""
        repo, org_id, mt5_id, registry = world
        repo.create_order_mapping(9, mt5_id, f"co9.{mt5_id}", org_id=org_id)
        repo.activate_order_mapping(mt5_id, f"co9.{mt5_id}", 5551)
        repo.link_pending_fill(9, mt5_id, 77)
        repo.activate_pending_fill(mt5_id, 5551, 7003, 100)
        registry.update_from_sync(mt5_id, org_id, report([position(7003)]), now=2.0)
        (positions, _orders) = registry.snapshot(mt5_id)
        assert positions[0].label == "copy:o9"

    def test_an_unknown_symbol_gets_id_zero(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_sync(mt5_id, org_id, report([position(7001, symbol="GBPJPY.r")]), now=2.0)
        (positions, _orders) = registry.snapshot(mt5_id)
        assert positions[0].symbol_id == 0


class TestAccountBlock:
    def test_get_state_shape(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([EURUSD]), now=1.0)
        registry.update_from_sync(mt5_id, org_id, report(
            [position(7001, sl=1.05, tp=1.15, price=1.102, pnl=2.0),
             position(7002, side="SELL", pnl=-0.5, price=1.099)],
            balance=9784.04, equity=9785.54), now=2.0)
        eurusd_id = registry.symbol_by_name(mt5_id, "EURUSD.r").symbol_id
        assert registry.account_block(mt5_id) == {
            "balance": 9784.04, "equity": 9785.54, "open_pnl": 1.5,
            "positions": [
                {"position_id": 7001, "symbol_id": eurusd_id, "symbol": "EURUSD.r", "side": "BUY",
                 "volume": 100, "entry_price": 1.1, "stop_loss": 1.05, "take_profit": 1.15,
                 "pnl_quote": 2.0, "current_price": 1.102},
                {"position_id": 7002, "symbol_id": eurusd_id, "symbol": "EURUSD.r", "side": "SELL",
                 "volume": 100, "entry_price": 1.1, "stop_loss": None, "take_profit": None,
                 "pnl_quote": -0.5, "current_price": 1.099},
            ],
        }


class TestOnline:
    def test_online_within_the_window_offline_after(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_sync(mt5_id, org_id, report(), now=100.0)
        assert registry.last_seen(mt5_id) == 100.0
        assert registry.is_online(mt5_id, now=100.0 + OFFLINE_AFTER_S - 0.01) is True
        assert registry.is_online(mt5_id, now=100.0 + OFFLINE_AFTER_S) is False
        registry.update_from_sync(mt5_id, org_id, report(seq=2), now=200.0)
        assert registry.is_online(mt5_id, now=201.0) is True
```

- [ ] **Step 3: Run the test to verify it fails**

Run DB `tests/unit/test_mt5_registry.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5.registry'`.

- [ ] **Step 4: Extend `OrderSnapshot` and write the registry**

In `copier/src/copier/engine/reconcile.py`, replace lines 81-97 (`class OrderSnapshot`) with:

```python
@dataclass(frozen=True)
class OrderSnapshot:
    """Immutable snapshot of a broker pending order from reconcile response.

    side/order_type/price come straight off the same payload; they were
    simply being discarded, which left the Positions screen unable to say
    anything about a working order beyond its symbol and size.

    stop_loss/take_profit are filled by the MT5 registry (a pending
    order's own protection travels in its report) so the ingress diff can
    see a changed level; the cTrader snapshot leaves them None.
    """
    order_id: int
    symbol_id: int
    volume: int
    label: str
    side: Side | None = None
    # "LIMIT" / "STOP" / ... as the broker enum names it; None if unknown.
    order_type: str | None = None
    # The order's trigger price: limitPrice for LIMIT, stopPrice for STOP.
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
```

Create `copier/src/copier/mt5/registry.py`:

```python
"""In-memory state of every MT5 terminal, fed by its reports.

The terminal is the only thing that knows its own book; it reports it on
every poll. This registry keeps the last report per account -- positions,
orders, balance, equity, the symbol map from the hello -- and serves it to
everything that would otherwise ask a cTrader client: the reconciler (as
PositionSnapshot/OrderSnapshot), get_state (as the accounts block), the
lane's operator actions and queries. Positions are persisted through the
same repo calls the cTrader resync uses, and only when the book actually
changed, so a 250 ms poll does not become four upserts a second.

After a copier restart the registry is empty until the next report; the
symbol map and the hedging flag come back from Postgres on first use.
"""

import logging
from dataclasses import dataclass, field

from copier.domain.models import Side, SymbolInfo
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.protocol import HelloReport, HelloSymbol, ReportPosition, SyncReport
from copier.mt5.symbols import symbol_infos_from_hello

log = logging.getLogger(__name__)

OFFLINE_AFTER_S = 15.0


@dataclass
class _AccountState:
    org_id: int
    hedging: bool | None = None
    hedging_loaded: bool = False
    hello_symbols: list[HelloSymbol] = field(default_factory=list)
    symbols: dict[str, SymbolInfo] | None = None     # by broker name; None = not loaded yet
    report: SyncReport | None = None
    last_seen: float | None = None
    persisted_signature: tuple | None = None


class MT5Registry:
    def __init__(self, repo, clock=None):
        self._repo = repo
        self._clock = clock
        self._accounts: dict[int, _AccountState] = {}

    def _state(self, account_id: int, org_id: int | None = None) -> _AccountState:
        state = self._accounts.get(account_id)
        if state is None:
            state = _AccountState(org_id=org_id if org_id is not None else 0)
            self._accounts[account_id] = state
        if org_id is not None:
            state.org_id = org_id
        return state

    # ---------- symbols ----------

    def symbols_by_name(self, account_id: int) -> dict[str, SymbolInfo]:
        """The account's symbols keyed by BROKER name, from the last hello or
        (after a restart) the symbol cache."""
        state = self._state(account_id)
        if state.symbols is None:
            state.symbols = dict(self._repo.load_symbol_cache(account_id))
        return state.symbols

    def symbol_by_name(self, account_id: int, name: str) -> SymbolInfo | None:
        return self.symbols_by_name(account_id).get(name)

    @staticmethod
    def _symbol_id(symbols: dict[str, SymbolInfo], name: str) -> int:
        info = symbols.get(name)
        return info.symbol_id if info is not None else 0

    # ---------- reports in ----------

    def update_from_hello(self, account_id: int, org_id: int, hello: HelloReport,
                          now: float) -> None:
        """A (chunk of a) hello: the symbol list accumulates across chunks
        and is persisted on the last one; ids are minted over the whole
        list so a collision across chunks is still probed."""
        state = self._state(account_id, org_id)
        state.hedging = hello.hedging
        state.hedging_loaded = True
        state.last_seen = now
        if hello.chunk <= 1:
            state.hello_symbols = []
        state.hello_symbols.extend(hello.symbols)
        state.symbols = {info.name: info for info in symbol_infos_from_hello(state.hello_symbols)}
        if hello.chunk >= hello.chunks:
            self._repo.save_symbol_cache(account_id, state.symbols)

    def update_from_sync(self, account_id: int, org_id: int, report: SyncReport,
                         now: float) -> None:
        """Store the report; persist positions when the book changed."""
        state = self._state(account_id, org_id)
        state.report = report
        state.last_seen = now
        signature = tuple(sorted(
            (p.ticket, p.volume, p.stop_loss, p.take_profit) for p in report.positions))
        if signature == state.persisted_signature:
            return
        positions, _orders = self.snapshot(account_id)
        symbols_by_id = {info.symbol_id: info
                         for info in self.symbols_by_name(account_id).values()}
        self._repo.upsert_positions(account_id, org_id, positions, symbols_by_id)
        self._repo.close_missing_positions(account_id, [p.position_id for p in positions])
        state.persisted_signature = signature

    # ---------- reads ----------

    def report(self, account_id: int) -> SyncReport | None:
        state = self._accounts.get(account_id)
        return state.report if state is not None else None

    def position(self, account_id: int, ticket: int) -> ReportPosition | None:
        report = self.report(account_id)
        if report is None:
            return None
        return next((p for p in report.positions if p.ticket == ticket), None)

    def _labels(self, state: _AccountState, account_id: int) -> dict[int, str]:
        """ticket -> the label reconcile expects. A copy of a master position
        is 'copy:m<master>'; anything that came from a master pending order
        is 'copy:o<order>' on both the order and the position it became,
        which is what compute_drift's unfilled-order check looks for."""
        labels: dict[int, str] = {}
        for m in self._repo.mapping_rows(org_id=state.org_id):
            if m.get("slave_account_id") != account_id:
                continue
            if m.get("master_order_id"):
                label = f"copy:o{m['master_order_id']}"
                if m.get("slave_order_id"):
                    labels[m["slave_order_id"]] = label
                if m.get("slave_position_id"):
                    labels[m["slave_position_id"]] = label
            elif m.get("master_position_id") and m.get("slave_position_id"):
                labels[m["slave_position_id"]] = f"copy:m{m['master_position_id']}"
        return labels

    def snapshot(self, account_id: int, with_labels: bool = True):
        """(positions, orders) as reconcile's snapshot types, or None before
        the first report. with_labels=False skips the mapping lookup (the
        ingress diff needs only the book)."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return None
        symbols = self.symbols_by_name(account_id)
        labels = self._labels(state, account_id) if with_labels else {}
        positions = [
            PositionSnapshot(
                position_id=p.ticket, symbol_id=self._symbol_id(symbols, p.symbol),
                side=Side(p.side), volume=p.volume, price=p.open_price,
                label=labels.get(p.ticket, p.comment),
                stop_loss=p.stop_loss, take_profit=p.take_profit,
            )
            for p in state.report.positions
        ]
        orders = [
            OrderSnapshot(
                order_id=o.ticket, symbol_id=self._symbol_id(symbols, o.symbol),
                volume=o.volume, label=labels.get(o.ticket, o.comment),
                side=Side.BUY if o.order_type.startswith("BUY") else Side.SELL,
                order_type=o.order_type.split("_", 1)[1], price=o.price,
                stop_loss=o.stop_loss, take_profit=o.take_profit,
            )
            for o in state.report.orders
        ]
        return positions, orders

    def account_block(self, account_id: int) -> dict | None:
        """The account's entry in get_state's `accounts` block, in exactly the
        shape AccountStateTracker.snapshot() produces for a cTrader account
        -- balance, equity and marks as the terminal reports them."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return None
        symbols = self.symbols_by_name(account_id)
        report = state.report
        return {
            "balance": report.balance,
            "equity": report.equity,
            "open_pnl": sum(p.pnl for p in report.positions),
            "positions": [
                {"position_id": p.ticket, "symbol_id": self._symbol_id(symbols, p.symbol),
                 "symbol": p.symbol, "side": p.side, "volume": p.volume,
                 "entry_price": p.open_price, "stop_loss": p.stop_loss,
                 "take_profit": p.take_profit, "pnl_quote": p.pnl,
                 "current_price": p.current_price}
                for p in report.positions
            ],
        }

    def last_seen(self, account_id: int) -> float | None:
        state = self._accounts.get(account_id)
        return state.last_seen if state is not None else None

    def is_online(self, account_id: int, now: float) -> bool:
        last = self.last_seen(account_id)
        return last is not None and (now - last) < OFFLINE_AFTER_S

    def hedging(self, account_id: int) -> bool | None:
        """The hello's hedging flag; read from the link row after a restart."""
        state = self._accounts.get(account_id)
        if state is not None and state.hedging_loaded:
            return state.hedging
        link = self._repo.load_mt5_link(account_id)
        if link is None or link.get("hedging") is None:
            return state.hedging if state is not None else None
        state = self._state(account_id)
        state.hedging = link["hedging"]
        state.hedging_loaded = True
        return state.hedging

    def margin_mode(self, account_id: int) -> str | None:
        """"hedging" | "netting" from the hello's flag (spec "Netting
        accounts": both are accepted); None before any hello."""
        hedging = self.hedging(account_id)
        if hedging is None:
            return None
        return "hedging" if hedging else "netting"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run DB `tests/unit/test_mt5_registry.py`.
Expected: `12 passed`.

Run DB `tests/unit/test_reconcile.py` and DB `tests/unit/test_main.py`.
Expected: all pass (`OrderSnapshot`'s two new fields are defaulted; every existing construction is positional up to `label` or keyword).

- [ ] **Step 6: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/registry.py copier/src/copier/testing/mt5_fixtures.py copier/src/copier/engine/reconcile.py copier/tests/unit/test_mt5_registry.py && git commit -m "feat(mt5): the registry -- each terminal's last report, served as snapshots and state

Symbols from the hello (persisted on the last chunk), positions persisted
through the resync's own repo calls only when the book changed, labels
from the mapping table, and the get_state accounts block in the
tracker's exact shape. Symbols and the hedging flag -- margin_mode(),
hedging or netting -- come back from Postgres after a restart.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `copier.mt5.outbox` — the command queue

**Files:**
- Create: `copier/src/copier/mt5/outbox.py`
- Modify: `copier/src/copier/domain/models.py:164-168` (`ClosePosition` gains `master_position_id`), `copier/src/copier/domain/decision.py:74` (stamps it), `copier/tests/unit/test_decision_positions.py:68,76,123,132` (the four `ClosePosition` expectations)
- Test: `copier/tests/unit/test_mt5_outbox.py`

**Interfaces:**
- Consumes: `Repo.enqueue_mt5_command/mt5_commands_open/mark_mt5_commands_sent/complete_mt5_command/fail_stale_mt5_opens/fail_mapping/log_event/mapping_side/mt5_open_payload` (Task 4), `client_order_id_for` (`engine/dispatch.py:43-55`), the intent dataclasses (`domain/models.py:145-210`), `Ack`, `Command`, `lots` (Task 2), `MT5Registry.margin_mode` (Task 6, injected as a callable).
- Produces (contract §2 `outbox.py`, used by Tasks 8, 14, 15): `OPEN_COMMAND_TTL_S = 30.0`, `REDELIVER_AFTER_S = 10.0`, `MAX_ATTEMPTS = 3`, `NO_ACK_MESSAGE`, `OFFLINE_MESSAGE`, `CLOSE_SUFFIX = ":close"`; `AckOutcome(command_id, kind, client_order_id, ok, message, position, order, price, volume, deal=None)` (`deal` is the terminal's deal ticket for the executed command — Task 15 uses it to skip the deal an ack already settled); `command_for_intent(intent, broker_symbol) -> tuple[str, dict]`; `MT5Outbox(repo, clock=None, margin_mode=None)` where `margin_mode: Callable[[int], str | None] | None` is the registry's `margin_mode` (None = every account hedging), with `enqueue_intent(intent, org_id, broker_symbol) -> int`, `enqueue(account_id, org_id, kind, payload, client_order_id=None) -> int`, `deliverable(account_id, now) -> list[Command]`, `apply_acks(account_id, acks) -> list[AckOutcome]`, `pending_count(account_id) -> int`. `now` is epoch seconds (what `reactor.seconds()` returns), compared with the rows' `created_at`/`sent_at`.
- Netting followers (contract §2, the outbox comment): `enqueue_intent(ClosePosition)` on an account whose `margin_mode` is `"netting"` becomes kind `open` on the OPPOSITE side of the mapping's open command (`repo.mapping_side(coid)`; the broker symbol from `repo.mt5_open_payload(coid)`), `lots` = the intent's volume, comment `close:m<master>`, `client_order_id = "<mapping coid>:close"`; `apply_acks` strips the suffix and returns `AckOutcome(kind="close", client_order_id=<mapping coid>, volume=<centilots closed>)`. A `ClosePosition` without a `master_position_id` (an operator's or the reconciler's close of the net position) stays kind `close` on the ticket, on both modes; so does a netting close whose mapping has no open command (logged as `slave_action/warning mt5_netting_close_unsided`). `AmendPositionSLTP` on netting is kind `amend` on the net ticket (last writer wins; Task 15 logs the override). Hedging followers keep kind `close`.
- `ClosePosition.master_position_id: int | None = None` (`domain/models.py`), stamped by `decide()` at `decision.py:74` with the master event's `position_id` — the only way the outbox can name the mapping a close belongs to, since several mappings share one net ticket. The four `test_decision_positions.py` expectations gain `master_position_id=11`.

- [ ] **Step 1: Give `ClosePosition` its master id**

In `copier/tests/unit/test_decision_positions.py`, the four `ClosePosition` expectations (lines 68, 76, 123, 132) gain the master's id, in this order:

```python
    assert out == [m.ClosePosition(slave_account_id=101, position_id=555, volume=10_000_000,
                                   master_position_id=11)]
```

```python
    assert out == [m.ClosePosition(slave_account_id=101, position_id=555, volume=3_000_000,
                                   master_position_id=11)]
```

```python
    assert out1 == [m.ClosePosition(slave_account_id=101, position_id=555, volume=3_000_000,
                                    master_position_id=11)]
```

```python
    assert out2 == [m.ClosePosition(slave_account_id=101, position_id=555, volume=1_500_000,
                                    master_position_id=11)]
```

Run PURE `tests/unit/test_decision_positions.py`.
Expected: FAIL — those four with `TypeError: ClosePosition.__init__() got an unexpected keyword argument 'master_position_id'`.

In `copier/src/copier/domain/models.py`, replace the `ClosePosition` dataclass (lines 164-168) with:

```python
@dataclass(frozen=True)
class ClosePosition:
    slave_account_id: int
    position_id: int
    volume: int
    # The master position this close is a copy of. decide() stamps it; the
    # MT5 outbox needs it on a NETTING follower, where every copy on a
    # symbol shares one net ticket and only the mapping's own id says which
    # copy is being closed. Defaulted so every existing construction (the
    # reconciler's orphan close, operator closes, tests) stays valid.
    master_position_id: int | None = None
```

In `copier/src/copier/domain/decision.py`, line 74 becomes:

```python
        out.append(m.ClosePosition(s.account_id, entry.slave_position_id, vol,
                                   master_position_id=e.position_id))
```

Run PURE `tests/unit/test_decision_positions.py`, PURE `tests/unit/test_models.py`, DB `tests/unit/test_dispatch.py`.
Expected: all pass (the field is defaulted; only `decide()` sets it).

- [ ] **Step 2: Write the failing test**

Create `copier/tests/unit/test_mt5_outbox.py`:

```python
"""MT5Outbox (copier/src/copier/mt5/outbox.py): intents become queued
commands; delivery, re-delivery, expiry and ack settlement."""

import time
from datetime import datetime, timezone

import psycopg
import pytest
from twisted.internet.task import Clock

from copier.db.repo import Repo
from copier.domain.models import (
    Alert, AmendPending, AmendPositionSLTP, CancelPending, ClosePosition, OpenMarket,
    PendingType, PlacePending, Side)
from copier.mt5.outbox import (
    CLOSE_SUFFIX, MAX_ATTEMPTS, NO_ACK_MESSAGE, OPEN_COMMAND_TTL_S, REDELIVER_AFTER_S, AckOutcome,
    MT5Outbox, command_for_intent)
from copier.mt5.protocol import Command
from copier.testing.mt5_fixtures import ack


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    mt5_id = seed_mt5_account(org_id)
    repo = Repo(db)
    return repo, org_id, mt5_id, MT5Outbox(repo, clock=Clock())


def _events(repo, action):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE payload->>'action' = %s ORDER BY id",
            (action,)).fetchall()


def _row(repo, command_id):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM mt5_commands WHERE id = %s", (command_id,)).fetchone()


class TestCommandForIntent:
    def test_open(self):
        intent = OpenMarket(slave_account_id=5, master_position_id=42, symbol_id=7, side=Side.BUY,
                            volume=100, stop_loss=1.09, take_profit=1.12, label="copy:m42",
                            symbol_name="EURUSD", entry_price=1.1)
        assert command_for_intent(intent, "EURUSD.r") == ("open", {
            "symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 1.09, "tp": 1.12,
            "comment": "copy:m42"})

    def test_open_without_protection_carries_zeros(self):
        intent = OpenMarket(slave_account_id=5, master_position_id=42, symbol_id=7, side=Side.SELL,
                            volume=1, stop_loss=None, take_profit=None, label="copy:m42")
        assert command_for_intent(intent, "XAUUSD.r")[1] == {
            "symbol": "XAUUSD.r", "side": "SELL", "lots": 0.01, "sl": 0.0, "tp": 0.0,
            "comment": "copy:m42"}

    def test_close_amend_and_pending_kinds(self):
        assert command_for_intent(ClosePosition(5, 7001, 50), "") == (
            "close", {"position": 7001, "lots": 0.5})
        assert command_for_intent(AmendPositionSLTP(5, 7001, 1.09, None), "") == (
            "amend", {"position": 7001, "sl": 1.09, "tp": 0.0})
        assert command_for_intent(
            PlacePending(5, 9, 7, Side.SELL, PendingType.STOP, 200, 1.08, None, 1.05,
                         1_760_000_000_000, "copy:o9", symbol_name="EURUSD"), "EURUSD.r") == (
            "place_pending", {"symbol": "EURUSD.r", "type": "SELL_STOP", "lots": 2.0,
                              "price": 1.08, "sl": 0.0, "tp": 1.05,
                              "expiry_ms": 1_760_000_000_000, "comment": "copy:o9"})
        assert command_for_intent(
            AmendPending(5, 5551, PendingType.LIMIT, 100, 1.091, 1.08, None), "") == (
            "amend_pending", {"order": 5551, "lots": 1.0, "price": 1.091, "sl": 1.08, "tp": 0.0})
        assert command_for_intent(CancelPending(5, 5551), "") == (
            "cancel_pending", {"order": 5551})

    def test_other_intents_have_no_command(self):
        with pytest.raises(ValueError):
            command_for_intent(Alert(5, "x"), "")


class TestEnqueue:
    def test_enqueue_intent_writes_the_row_with_its_client_order_id(self, world):
        repo, org_id, mt5_id, outbox = world
        intent = OpenMarket(slave_account_id=mt5_id, master_position_id=42, symbol_id=7,
                            side=Side.BUY, volume=100, stop_loss=None, take_profit=None,
                            label="copy:m42", symbol_name="EURUSD")
        command_id = outbox.enqueue_intent(intent, org_id, "EURUSD.r")
        (row,) = repo.mt5_commands_open(mt5_id)
        assert row["id"] == command_id and row["kind"] == "open" and row["org_id"] == org_id
        assert row["payload"]["symbol"] == "EURUSD.r" and row["payload"]["lots"] == 1.0
        assert row["client_order_id"] == f"cm42.{mt5_id}"
        assert outbox.pending_count(mt5_id) == 1

    def test_enqueue_takes_a_raw_command(self, world):
        repo, org_id, mt5_id, outbox = world
        outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.0})
        (row,) = repo.mt5_commands_open(mt5_id)
        assert (row["kind"], row["payload"], row["client_order_id"]) == (
            "close", {"position": 7001, "lots": 0.0}, None)


class TestDeliverable:
    def test_returns_queued_commands_in_id_order_and_marks_them_sent(self, world):
        repo, org_id, mt5_id, outbox = world
        first = outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0})
        second = outbox.enqueue(mt5_id, org_id, "amend", {"position": 1, "sl": 1.0, "tp": 0.0})
        now = time.time()
        commands = outbox.deliverable(mt5_id, now)
        assert commands == [
            Command(first, "close", {"position": 1, "lots": 0.0}, None),
            Command(second, "amend", {"position": 1, "sl": 1.0, "tp": 0.0}, None)]
        rows = repo.mt5_commands_open(mt5_id)
        assert [(r["status"], r["attempts"]) for r in rows] == [("sent", 1), ("sent", 1)]
        assert rows[0]["sent_at"] == datetime.fromtimestamp(now, tz=timezone.utc)
        assert outbox.deliverable(mt5_id, now + 1.0) == []      # sent, still waiting for the ack

    def test_redelivers_after_ten_seconds_up_to_three_attempts_then_fails(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0})
        now = time.time()
        assert [c.id for c in outbox.deliverable(mt5_id, now)] == [command_id]
        assert outbox.deliverable(mt5_id, now + REDELIVER_AFTER_S - 0.5) == []
        assert [c.id for c in outbox.deliverable(mt5_id, now + REDELIVER_AFTER_S + 1)] == [command_id]
        assert [c.id for c in outbox.deliverable(mt5_id, now + 2 * (REDELIVER_AFTER_S + 1))] == [command_id]
        assert _row(repo, command_id)["attempts"] == MAX_ATTEMPTS
        assert outbox.deliverable(mt5_id, now + 3 * (REDELIVER_AFTER_S + 1)) == []
        row = _row(repo, command_id)
        assert (row["status"], row["result"]["message"], row["result"]["ok"]) == (
            "failed", NO_ACK_MESSAGE, False)
        (event,) = _events(repo, "mt5_command_unacked")
        assert event[0] == "error" and event[1]["command_id"] == command_id
        assert event[1]["attempts"] == MAX_ATTEMPTS

    def test_giving_up_on_a_mapped_command_fails_its_mapping(self, world):
        """A close is used (opens expire after 30 s, before three
        re-deliveries are up); the mapping half is the same code path."""
        repo, org_id, mt5_id, outbox = world
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0}, f"cm42.{mt5_id}")
        now = time.time()
        for n in range(MAX_ATTEMPTS + 1):
            outbox.deliverable(mt5_id, now + n * (REDELIVER_AFTER_S + 1))
        (mapping,) = repo.mapping_rows(org_id=org_id)
        assert (mapping["status"], mapping["error"]) == ("failed", NO_ACK_MESSAGE)

    def test_a_stale_open_expires_with_its_mapping_but_a_close_never_does(self, world):
        repo, org_id, mt5_id, outbox = world
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        open_id = outbox.enqueue(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 0.0, "tp": 0.0,
             "comment": "copy:m42"}, f"cm42.{mt5_id}")
        close_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0})
        now = time.time()
        # The terminal comes back OPEN_COMMAND_TTL_S + 1 later.
        commands = outbox.deliverable(mt5_id, now + OPEN_COMMAND_TTL_S + 1)
        assert [c.id for c in commands] == [close_id]
        row = _row(repo, open_id)
        assert (row["status"], row["result"]["message"]) == ("failed", "terminal offline")
        (mapping,) = repo.mapping_rows(org_id=org_id)
        assert (mapping["status"], mapping["error"]) == ("failed", "terminal offline")
        (event,) = _events(repo, "mt5_command_expired")
        assert event[0] == "warning" and event[1]["command_id"] == open_id

    def test_a_fresh_open_is_delivered(self, world):
        repo, org_id, mt5_id, outbox = world
        open_id = outbox.enqueue(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 0.0, "tp": 0.0, "comment": ""})
        assert [c.id for c in outbox.deliverable(mt5_id, time.time())] == [open_id]


class TestApplyAcks:
    def test_an_ack_settles_the_command_and_reports_the_outcome(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 0.0, "tp": 0.0,
             "comment": "copy:m42"}, f"cm42.{mt5_id}")
        outbox.deliverable(mt5_id, time.time())
        outcomes = outbox.apply_acks(mt5_id, [
            ack(command_id, position=7001, deal=8, order=9, price=1.1001, volume=100)])
        assert outcomes == [AckOutcome(
            command_id=command_id, kind="open", client_order_id=f"cm42.{mt5_id}", ok=True,
            message="done", position=7001, order=9, price=1.1001, volume=100, deal=8)]
        row = _row(repo, command_id)
        assert row["status"] == "done" and row["done_at"] is not None
        assert row["result"] == {"ok": True, "retcode": 10009, "message": "done",
                                 "position": 7001, "deal": 8, "order": 9, "price": 1.1001,
                                 "lots": 1.0}
        assert outbox.pending_count(mt5_id) == 0

    def test_a_failed_ack(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.0})
        (outcome,) = outbox.apply_acks(
            mt5_id, [ack(command_id, ok=False, retcode=10036, message="position closed")])
        assert (outcome.ok, outcome.message, outcome.kind, outcome.position) == (
            False, "position closed", "close", 7001)
        assert _row(repo, command_id)["status"] == "failed"

    def test_position_and_order_fall_back_to_the_payload(self, world):
        repo, org_id, mt5_id, outbox = world
        cancel_id = outbox.enqueue(mt5_id, org_id, "cancel_pending", {"order": 5551})
        (outcome,) = outbox.apply_acks(mt5_id, [ack(cancel_id)])
        assert outcome.order == 5551 and outcome.position is None

    def test_duplicate_unknown_and_foreign_acks_are_ignored(self, world, seed_mt5_account):
        repo, org_id, mt5_id, outbox = world
        other = seed_mt5_account(org_id)
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.0})
        theirs = outbox.enqueue(other, org_id, "close", {"position": 8001, "lots": 0.0})
        assert len(outbox.apply_acks(mt5_id, [ack(command_id)])) == 1
        assert outbox.apply_acks(mt5_id, [ack(command_id)]) == []      # duplicate
        assert outbox.apply_acks(mt5_id, [ack(424242)]) == []          # unknown
        assert outbox.apply_acks(mt5_id, [ack(theirs)]) == []          # another account's
        assert _row(repo, theirs)["status"] == "queued"

    def test_a_volume_the_ack_omits_falls_back_to_the_payloads_lots(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.4})
        (outcome,) = outbox.apply_acks(mt5_id, [ack(command_id, deal=8)])
        assert (outcome.volume, outcome.deal) == (40, 8)


def _open(mt5_id, master_position_id=42, side=Side.BUY):
    return OpenMarket(slave_account_id=mt5_id, master_position_id=master_position_id, symbol_id=7,
                      side=side, volume=100, stop_loss=1.09, take_profit=1.12,
                      label=f"copy:m{master_position_id}", symbol_name="EURUSD")


class TestNetting:
    """A netting follower: every copy on a symbol shares one net position,
    so a mapping's close is an opposite-side open that names the mapping."""

    @pytest.fixture
    def netting(self, world):
        repo, org_id, mt5_id, _outbox = world
        return repo, org_id, mt5_id, MT5Outbox(repo, clock=Clock(), margin_mode=lambda _a: "netting")

    def test_a_close_becomes_an_opposite_open_named_after_its_mapping(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id), org_id, "EURUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 40, master_position_id=42), org_id, "")
        row = _row(repo, close_id)
        assert (row["kind"], row["client_order_id"]) == ("open", f"cm42.{mt5_id}{CLOSE_SUFFIX}")
        assert row["payload"] == {"symbol": "EURUSD.r", "side": "SELL", "lots": 0.4, "sl": 0.0,
                                  "tp": 0.0, "comment": "close:m42"}

    def test_a_sell_copy_closes_with_a_buy(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id, side=Side.SELL), org_id, "XAUUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 100, master_position_id=42), org_id, "")
        assert _row(repo, close_id)["payload"]["side"] == "BUY"
        assert _row(repo, close_id)["payload"]["symbol"] == "XAUUSD.r"

    def test_the_close_ack_is_reported_under_the_mappings_own_coid(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id), org_id, "EURUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 40, master_position_id=42), org_id, "")
        outbox.deliverable(mt5_id, time.time())
        (outcome,) = outbox.apply_acks(
            mt5_id, [ack(close_id, position=9001, deal=700005, order=700004, price=1.101, volume=40)])
        assert outcome == AckOutcome(
            command_id=close_id, kind="close", client_order_id=f"cm42.{mt5_id}", ok=True,
            message="done", position=9001, order=700004, price=1.101, volume=40, deal=700005)
        assert _row(repo, close_id)["status"] == "done"

    def test_a_close_without_a_master_id_is_a_plain_close_on_the_net_ticket(self, netting):
        """Trade page, Close all and the reconciler's orphan close act on the
        net position on both modes."""
        repo, org_id, mt5_id, outbox = netting
        close_id = outbox.enqueue_intent(ClosePosition(mt5_id, 9001, 40), org_id, "")
        row = _row(repo, close_id)
        assert (row["kind"], row["payload"], row["client_order_id"]) == (
            "close", {"position": 9001, "lots": 0.4}, None)

    def test_a_mapping_without_an_open_command_falls_back_to_a_plain_close(self, netting):
        """An adopted mapping has no open command to read a side from."""
        repo, org_id, mt5_id, outbox = netting
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 40, master_position_id=77), org_id, "")
        assert _row(repo, close_id)["kind"] == "close"
        (event,) = _events(repo, "mt5_netting_close_unsided")
        assert event[0] == "warning" and event[1]["client_order_id"] == f"cm77.{mt5_id}"

    def test_an_amend_goes_to_the_net_ticket(self, netting):
        repo, org_id, mt5_id, outbox = netting
        amend_id = outbox.enqueue_intent(AmendPositionSLTP(mt5_id, 9001, 1.095, None), org_id, "")
        row = _row(repo, amend_id)
        assert (row["kind"], row["payload"]) == ("amend", {"position": 9001, "sl": 1.095, "tp": 0.0})

    def test_a_netting_close_never_expires(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id), org_id, "EURUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 100, master_position_id=42), org_id, "")
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            conn.execute("UPDATE mt5_commands SET created_at = now() - interval '60 seconds'")
        delivered = outbox.deliverable(mt5_id, time.time())
        assert [c.id for c in delivered] == [close_id]        # the stale open expired, the close did not
        assert _row(repo, close_id)["status"] == "sent"

    def test_hedging_followers_keep_a_plain_close(self, world):
        repo, org_id, mt5_id, _outbox = world
        outbox = MT5Outbox(repo, clock=Clock(), margin_mode=lambda _a: "hedging")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 7001, 50, master_position_id=42), org_id, "")
        assert _row(repo, close_id)["kind"] == "close"
        assert _row(repo, close_id)["payload"] == {"position": 7001, "lots": 0.5}
```

- [ ] **Step 3: Run the test to verify it fails**

Run DB `tests/unit/test_mt5_outbox.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5.outbox'`.

- [ ] **Step 4: Write the module**

Create `copier/src/copier/mt5/outbox.py`:

```python
"""The command outbox: what the copier wants an MT5 terminal to do.

Nothing can push to a terminal, so every decision the engine makes for an
MT5 slave -- and every operator action on an MT5 account -- becomes a row
in mt5_commands that the terminal collects on its next poll (queued ->
sent) and settles with an acknowledgement (done / failed). The rows are
Postgres, not memory: a copier restart loses nothing that was decided.

Delivery rules (spec, "Ordering guarantees"): commands go out in id order;
a `sent` command without an ack is re-delivered after REDELIVER_AFTER_S at
most MAX_ATTEMPTS times, then failed as "no ack from terminal". An `open`
or `place_pending` not delivered within OPEN_COMMAND_TTL_S is failed as
"terminal offline" and its mapping with it -- a market copy placed minutes
late is a different trade. close/amend/cancel never expire.

Netting followers (spec "Netting accounts"): every copy on a symbol lives
inside the symbol's single net position, so a mapping cannot be closed by
ticket -- the ticket is shared. Its close is an OPEN on the opposite side
for the mapping's volume (that is how a netting account reduces), queued
under the mapping's own client_order_id plus CLOSE_SUFFIX, so the ack
comes back naming exactly that mapping; apply_acks strips the suffix and
reports it as a close. A close without a master id (an operator's, Close
all's, the reconciler's) is a plain close on the net ticket, on both
modes. The ':close' opens never expire (repo.fail_stale_mt5_opens).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from copier.db.repo import MappingNotFound
from copier.domain.models import (
    AmendPending, AmendPositionSLTP, CancelPending, ClosePosition, OpenMarket, PendingType,
    PlacePending, SlaveIntent)
from copier.engine.dispatch import client_order_id_for
from copier.mt5.protocol import Ack, Command, lots

log = logging.getLogger(__name__)

OPEN_COMMAND_TTL_S = 30.0
REDELIVER_AFTER_S = 10.0
MAX_ATTEMPTS = 3
NO_ACK_MESSAGE = "no ack from terminal"
OFFLINE_MESSAGE = "terminal offline"
CLOSE_SUFFIX = ":close"


@dataclass(frozen=True)
class AckOutcome:
    command_id: int
    kind: str
    client_order_id: str | None
    ok: bool
    message: str
    position: int | None
    order: int | None
    price: float | None
    volume: int | None                # centilots
    # The terminal's deal ticket for the executed command, so the deal it
    # reports in the same sync is not booked a second time (main.py).
    deal: int | None = None


def _price(value) -> float:
    """Payload prices: 0.0 means none (the contract's `0`/absent)."""
    return float(value) if value is not None else 0.0


def command_for_intent(intent: SlaveIntent, broker_symbol: str) -> tuple[str, dict]:
    """(kind, payload) per the contract's table. Centilots become lots HERE
    and nowhere else on the way out; prices pass through unchanged (a
    master's price is already at its digits; the lane rounds operator
    prices before they get here)."""
    if isinstance(intent, OpenMarket):
        return "open", {
            "symbol": broker_symbol, "side": intent.side.value, "lots": lots(intent.volume),
            "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit),
            "comment": intent.label,
        }
    if isinstance(intent, ClosePosition):
        return "close", {"position": intent.position_id, "lots": lots(intent.volume)}
    if isinstance(intent, AmendPositionSLTP):
        return "amend", {"position": intent.position_id,
                         "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit)}
    if isinstance(intent, PlacePending):
        kind = "LIMIT" if intent.order_type == PendingType.LIMIT else "STOP"
        return "place_pending", {
            "symbol": broker_symbol, "type": f"{intent.side.value}_{kind}",
            "lots": lots(intent.volume), "price": float(intent.price),
            "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit),
            "expiry_ms": int(intent.expiry_ts_ms or 0), "comment": intent.label,
        }
    if isinstance(intent, AmendPending):
        return "amend_pending", {
            "order": intent.order_id, "lots": lots(intent.volume), "price": float(intent.price),
            "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit),
        }
    if isinstance(intent, CancelPending):
        return "cancel_pending", {"order": intent.order_id}
    raise ValueError(f"no MT5 command for intent {type(intent).__name__}")


def _ts(now: float) -> datetime:
    return datetime.fromtimestamp(now, tz=timezone.utc)


class MT5Outbox:
    def __init__(self, repo, clock=None, margin_mode=None):
        """margin_mode: account_id -> "hedging" | "netting" | None (the
        registry's margin_mode). None means every account is hedging."""
        self._repo = repo
        self._clock = clock
        self._margin_mode = margin_mode

    def _now(self) -> float:
        clock = self._clock
        if clock is None:
            from twisted.internet import reactor as clock
        return clock.seconds()

    def _is_netting(self, account_id: int) -> bool:
        return self._margin_mode is not None and self._margin_mode(account_id) == "netting"

    def enqueue_intent(self, intent: SlaveIntent, org_id: int, broker_symbol: str) -> int:
        account_id = intent.slave_account_id
        if (isinstance(intent, ClosePosition) and intent.master_position_id is not None
                and self._is_netting(account_id)):
            queued = self._enqueue_netting_close(intent, org_id)
            if queued is not None:
                return queued
        kind, payload = command_for_intent(intent, broker_symbol)
        return self.enqueue(account_id, org_id, kind, payload, client_order_id_for(intent))

    def _enqueue_netting_close(self, intent: ClosePosition, org_id: int) -> int | None:
        """A netting follower's close of ONE copy: an open on the opposite
        side of the mapping's own open, for the closed volume, under the
        mapping's coid + CLOSE_SUFFIX. None when the mapping has no open
        command to read a side from (an adopted mapping): the caller falls
        back to a plain close on the net ticket and the operator is told."""
        account_id = intent.slave_account_id
        coid = f"cm{intent.master_position_id}.{account_id}"     # client_order_id_for's scheme
        side = self._repo.mapping_side(coid)
        payload = self._repo.mt5_open_payload(coid) or {}
        symbol = payload.get("symbol")
        if side is None or not symbol:
            self._repo.log_event(
                'slave_action', 'warning',
                {'action': 'mt5_netting_close_unsided', 'client_order_id': coid,
                 'position': intent.position_id, 'volume': intent.volume,
                 'detail': 'no open command records this copy\'s side; closing the net '
                           'position by volume instead, which reduces the copies oldest-first'},
                account_id=account_id, org_id=org_id)
            return None
        # SL/TP 0 leaves the net position's levels alone; the real EA passes
        # them as given (Task 17's fake does the same).
        return self.enqueue(account_id, org_id, "open", {
            "symbol": symbol, "side": "SELL" if side == "BUY" else "BUY",
            "lots": lots(intent.volume), "sl": 0.0, "tp": 0.0,
            "comment": f"close:m{intent.master_position_id}",
        }, coid + CLOSE_SUFFIX)

    def enqueue(self, account_id: int, org_id: int, kind: str, payload: dict,
                client_order_id: str | None = None) -> int:
        return self._repo.enqueue_mt5_command(account_id, org_id, kind, payload, client_order_id)

    def deliverable(self, account_id: int, now: float) -> list[Command]:
        """What the terminal gets on this poll: expires stale opens, gives up
        on commands re-delivered MAX_ATTEMPTS times without an ack,
        re-delivers `sent` rows older than REDELIVER_AFTER_S, and marks
        everything returned as sent. `now` is epoch seconds."""
        for command_id in self._repo.fail_stale_mt5_opens(
                account_id, _ts(now - OPEN_COMMAND_TTL_S)):
            self._repo.log_event(
                'slave_action', 'warning',
                {'action': 'mt5_command_expired', 'command_id': command_id,
                 'reason': OFFLINE_MESSAGE,
                 'detail': f'market copy not delivered within {OPEN_COMMAND_TTL_S:.0f}s; '
                           'a copy placed that late is a different trade'},
                account_id=account_id)
        out: list[Command] = []
        ids: list[int] = []
        for row in self._repo.mt5_commands_open(account_id):
            if row["status"] == "sent":
                sent_at = row["sent_at"].timestamp() if row["sent_at"] is not None else now
                if now - sent_at < REDELIVER_AFTER_S:
                    continue
                if row["attempts"] >= MAX_ATTEMPTS:
                    self._give_up(account_id, row, now)
                    continue
            out.append(Command(id=row["id"], kind=row["kind"], payload=row["payload"],
                               client_order_id=row["client_order_id"]))
            ids.append(row["id"])
        if ids:
            self._repo.mark_mt5_commands_sent(ids, _ts(now))
        return out

    def _give_up(self, account_id: int, row: dict, now: float) -> None:
        result = {"ok": False, "retcode": 0, "message": NO_ACK_MESSAGE, "position": None,
                  "deal": None, "order": None, "price": None, "lots": None}
        self._repo.complete_mt5_command(row["id"], False, result, _ts(now), account_id=account_id)
        if row["client_order_id"]:
            try:
                self._repo.fail_mapping(account_id, row["client_order_id"], NO_ACK_MESSAGE)
            except MappingNotFound:
                pass
        self._repo.log_event(
            'slave_action', 'error',
            {'action': 'mt5_command_unacked', 'command_id': row["id"], 'kind': row["kind"],
             'attempts': row["attempts"], 'error': NO_ACK_MESSAGE},
            account_id=account_id, org_id=row["org_id"])

    def apply_acks(self, account_id: int, acks: list[Ack]) -> list[AckOutcome]:
        """Settle acked commands. Unknown, duplicate and foreign ids change
        nothing (the terminal re-acks re-delivered ids by design). A ticket
        the ack omits falls back to the payload's, so a close/cancel ack
        always names what it settled; a volume it omits falls back to the
        payload's lots. A netting follower's ':close' open is reported as
        kind "close" under the mapping's own coid."""
        out: list[AckOutcome] = []
        for ack in acks:
            result = {"ok": ack.ok, "retcode": ack.retcode, "message": ack.message,
                      "position": ack.position, "deal": ack.deal, "order": ack.order,
                      "price": ack.price,
                      "lots": lots(ack.volume) if ack.volume is not None else None}
            row = self._repo.complete_mt5_command(
                ack.command_id, ack.ok, result, _ts(self._now()), account_id=account_id)
            if row is None:
                continue
            payload = row["payload"] or {}
            kind, coid = row["kind"], row["client_order_id"]
            if kind == "open" and coid and coid.endswith(CLOSE_SUFFIX):
                kind, coid = "close", coid[:-len(CLOSE_SUFFIX)]
            volume = ack.volume
            if volume is None and payload.get("lots"):
                volume = int(round(float(payload["lots"]) * 100))
            out.append(AckOutcome(
                command_id=row["id"], kind=kind, client_order_id=coid,
                ok=ack.ok, message=ack.message,
                position=ack.position if ack.position is not None else payload.get("position"),
                order=ack.order if ack.order is not None else payload.get("order"),
                price=ack.price, volume=volume, deal=ack.deal,
            ))
        return out

    def pending_count(self, account_id: int) -> int:
        return len(self._repo.mt5_commands_open(account_id))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run DB `tests/unit/test_mt5_outbox.py`.
Expected: `24 passed`.

- [ ] **Step 6: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/outbox.py copier/tests/unit/test_mt5_outbox.py copier/src/copier/domain/models.py copier/src/copier/domain/decision.py copier/tests/unit/test_decision_positions.py && git commit -m "feat(mt5): the command outbox -- queued, sent, acked, re-delivered, expired

Every slave intent maps to the contract's payload (lots as floats,
symbol = broker name); delivery is in id order with re-delivery after
10 s up to three attempts, market opens expire after 30 s with their
mapping, and acks settle a command exactly once. On a netting follower
a mapping's close is an opposite-side open under the mapping's coid
plus ':close' (ClosePosition now carries the master's id so the outbox
can name it); its ack comes back as a close of exactly that mapping.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Dispatcher — MT5 slaves go to the outbox before `build_request`

**Files:**
- Modify: `copier/src/copier/engine/dispatch.py:4` (typing import), `:221-243` (`__init__`), `:411-423` (`_handle_live_send`), add `_enqueue_for_mt5` right after it
- Test: `copier/tests/unit/test_dispatch_mt5.py`

**Interfaces:**
- Consumes: `MT5Outbox.enqueue_intent` (Task 7), `Repo.log_event/create_position_mapping/create_order_mapping`.
- Produces (used by Task 15): `Dispatcher(send_for_account, repo, bucket, clock=None, mt5_targets=None, mt5_outbox=None)` where `mt5_targets: Callable[[int], Mapping[int, str] | None]` returns `{symbol_id: broker symbol name}` for an MT5 account and `None` for a cTrader one. Behaviour: an intent for an MT5 slave creates its mapping exactly as the live path does, is enqueued, and logs `slave_action/info {'action': 'mt5_command_queued', 'command_id', 'intent_type'}`; an `OpenMarket`/`PlacePending` whose `symbol_id` the account cannot serve logs `slave_action/warning {'action': 'mt5_symbol_unmatched', 'symbol', 'message'}` (naming the Details panel) and creates nothing.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_dispatch_mt5.py`:

```python
"""Dispatcher x MT5 (engine/dispatch.py): intents whose slave is an MT5
terminal are queued on the outbox BEFORE build_request; cTrader intents are
untouched."""

from unittest.mock import Mock

import psycopg
import pytest
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.db.repo import Repo
from copier.domain.models import (
    AmendPending, AmendPositionSLTP, CancelPending, ClosePosition, OpenMarket, PendingType,
    PlacePending, Side)
from copier.engine.dispatch import Dispatcher
from copier.mt5.outbox import MT5Outbox

ORG_ID = 1
CTRADER_SLAVE = 101
EURUSD_ID = 12345          # the bridge's id for the MT5 account's EURUSD.r


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, 'Org A')", (ORG_ID,))
        conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
            " granted_at, expires_at) VALUES (%s, 'a', 'b', now(), now() + interval '1 hour')",
            (ORG_ID,))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, enabled, multiplier)"
            " VALUES (%s, %s, 1, 10001, false, 'slave', true, 1.0)", (CTRADER_SLAVE, ORG_ID))
    mt5_id = seed_mt5_account(ORG_ID)
    repo = Repo(db)
    sent = []

    def mock_send(account_id, msg):
        sent.append((account_id, msg))
        return defer.succeed(None)

    bucket = Mock()
    bucket.acquire.return_value = defer.succeed(None)

    def mt5_targets(account_id):
        return {EURUSD_ID: "EURUSD.r"} if account_id == mt5_id else None

    dispatcher = Dispatcher(mock_send, repo, bucket, clock=Clock(),
                            mt5_targets=mt5_targets, mt5_outbox=MT5Outbox(repo, clock=Clock()))
    return repo, mt5_id, dispatcher, sent


def _open(account_id, symbol_id=EURUSD_ID, master_position_id=42):
    return OpenMarket(slave_account_id=account_id, master_position_id=master_position_id,
                      symbol_id=symbol_id, side=Side.BUY, volume=100, stop_loss=1.09,
                      take_profit=1.12, label=f"copy:m{master_position_id}",
                      symbol_name="EURUSD", entry_price=1.1)


def _events(repo, action):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, account_id, payload FROM events"
            " WHERE payload->>'action' = %s ORDER BY id", (action,)).fetchall()


def test_an_open_for_an_mt5_slave_is_queued_not_sent(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([_open(mt5_id)], org_id=ORG_ID)
    assert sent == []
    (row,) = repo.mt5_commands_open(mt5_id)
    assert row["kind"] == "open" and row["status"] == "queued"
    assert row["payload"] == {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 1.09,
                              "tp": 1.12, "comment": "copy:m42"}
    assert row["client_order_id"] == f"cm42.{mt5_id}"
    (mapping,) = repo.mapping_rows(org_id=ORG_ID)
    assert (mapping["master_position_id"], mapping["slave_account_id"], mapping["status"],
            mapping["symbol"]) == (42, mt5_id, "pending", "EURUSD")
    (event,) = _events(repo, "mt5_command_queued")
    assert event[1] == mt5_id and event[2]["command_id"] == row["id"]
    assert event[2]["intent_type"] == "OpenMarket"


def test_a_ctrader_slave_still_goes_to_the_wire(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([_open(CTRADER_SLAVE, symbol_id=1)], org_id=ORG_ID)
    assert [a for a, _m in sent] == [CTRADER_SLAVE]
    assert repo.mt5_commands_open(CTRADER_SLAVE) == [] and repo.mt5_commands_open(mt5_id) == []


def test_every_intent_kind_becomes_its_command(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([
        ClosePosition(mt5_id, 7001, 50),
        AmendPositionSLTP(mt5_id, 7001, 1.08, None),
        PlacePending(mt5_id, 9, EURUSD_ID, Side.SELL, PendingType.LIMIT, 100, 1.12, None, None,
                     None, "copy:o9", symbol_name="EURUSD"),
        AmendPending(mt5_id, 5551, PendingType.LIMIT, 100, 1.121, None, None),
        CancelPending(mt5_id, 5551),
    ], org_id=ORG_ID)
    assert sent == []
    rows = repo.mt5_commands_open(mt5_id)
    assert [r["kind"] for r in rows] == [
        "close", "amend", "place_pending", "amend_pending", "cancel_pending"]
    assert rows[2]["payload"]["symbol"] == "EURUSD.r"
    assert rows[2]["client_order_id"] == f"co9.{mt5_id}"
    assert [m["master_order_id"] for m in repo.mapping_rows(org_id=ORG_ID)] == [9]


def test_an_unmatched_symbol_is_an_alert_naming_the_details_panel(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([_open(mt5_id, symbol_id=999)], org_id=ORG_ID)
    assert sent == [] and repo.mt5_commands_open(mt5_id) == [] and repo.mapping_rows() == []
    (event,) = _events(repo, "mt5_symbol_unmatched")
    assert event[0] == "warning" and event[1] == mt5_id
    assert "'EURUSD'" in event[2]["message"] and "Details" in event[2]["message"]


def test_the_kill_switch_and_dry_run_gate_mt5_like_everything_else(world):
    repo, mt5_id, dispatcher, sent = world
    repo.set_org_setting(ORG_ID, "copying_enabled", False)
    dispatcher.dispatch([_open(mt5_id)], org_id=ORG_ID)
    assert repo.mt5_commands_open(mt5_id) == [] and repo.mapping_rows() == []
    repo.set_org_setting(ORG_ID, "copying_enabled", True)
    repo.set_org_setting(ORG_ID, "dry_run", True)
    dispatcher.dispatch([_open(mt5_id)], org_id=ORG_ID)
    assert repo.mt5_commands_open(mt5_id) == []                      # dry-run queues nothing
    assert [m["status"] for m in repo.mapping_rows()] == ["pending"]  # ...but records the would-be copy


def test_a_dispatcher_without_an_mt5_lane_behaves_as_before(world):
    repo, mt5_id, _dispatcher, _sent = world
    sent = []
    bucket = Mock()
    bucket.acquire.return_value = defer.succeed(None)
    plain = Dispatcher(lambda a, m: (sent.append(a), defer.succeed(None))[1], repo, bucket,
                       clock=Clock())
    plain.dispatch([_open(CTRADER_SLAVE, symbol_id=1)], org_id=ORG_ID)
    assert sent == [CTRADER_SLAVE]
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_dispatch_mt5.py`.
Expected: FAIL — every test with `TypeError: Dispatcher.__init__() got an unexpected keyword argument 'mt5_targets'`.

- [ ] **Step 3: Implement**

In `copier/src/copier/engine/dispatch.py`, change line 4 to:

```python
from typing import Callable, Mapping, Sequence
```

Replace `__init__` (lines 221-243) with:

```python
    def __init__(
        self,
        send_for_account: Callable[[int, message.Message], defer.Deferred],
        repo: Repo,
        bucket: TokenBucket,
        clock=None,
        mt5_targets: Callable[[int], Mapping[int, str] | None] | None = None,
        mt5_outbox=None,
    ):
        """Initialize dispatcher.

        Args:
            send_for_account: Function that sends a message to an account, returns Deferred.
                Must raise SendNotAttempted for pre-wire failures (connection, throttle).
                Any other exception indicates an ambiguous failure and will NOT be retried.
            repo: Repository for mappings and events.
            bucket: TokenBucket for rate limiting.
            clock: Optional Twisted Clock for testing.
            mt5_targets: account_id -> {symbol_id: broker symbol name} when the
                account is an MT5 terminal, None when it is cTrader (or when
                the process has no MT5 lane at all). Intents for MT5 accounts
                are queued on `mt5_outbox` instead of built into protobuf.
            mt5_outbox: copier.mt5.outbox.MT5Outbox, or None.
        """
        self._send_for_account = send_for_account
        self._repo = repo
        self._bucket = bucket
        if clock is None:
            from twisted.internet import reactor as clock
        self._clock = clock
        self._mt5_targets = mt5_targets
        self._mt5_outbox = mt5_outbox
```

Replace `_handle_live_send` (lines 411-423) with the two methods:

```python
    def _handle_live_send(self, intent: SlaveIntent, org_id: int) -> None:
        """Send request with retry logic -- or, for an MT5 slave, queue it."""
        if self._enqueue_for_mt5(intent, org_id):
            return

        account_id, req = build_request(intent)

        # Create mapping if needed
        self._create_mapping(intent, account_id, org_id)

        # Tell the operator BEFORE the send if this copy is going out
        # without protection its master carries.
        self._warn_dropped_protection(req, account_id, org_id)

        # Send with retries
        self._send_with_retries(account_id, req, attempt=0)

    def _enqueue_for_mt5(self, intent: SlaveIntent, org_id: int) -> bool:
        """Route an MT5 slave's intent to the command outbox.

        Decided BEFORE build_request: an MT5 terminal speaks no protobuf,
        and its symbol ids are the bridge's crc32 values. Mapping creation
        is exactly the live path's (same client_order_id scheme), so the
        ack that comes back through the sync report activates the same row
        a cTrader execution event would. Returns True when the intent was
        handled here.
        """
        if self._mt5_targets is None or self._mt5_outbox is None:
            return False
        account_id = intent.slave_account_id
        targets = self._mt5_targets(account_id)
        if targets is None:
            return False
        broker_symbol = ""
        if isinstance(intent, (OpenMarket, PlacePending)):
            broker_symbol = targets.get(intent.symbol_id)
            if broker_symbol is None:
                self._repo.log_event(
                    'slave_action', 'warning',
                    {'action': 'mt5_symbol_unmatched', 'symbol': intent.symbol_name,
                     'message': f"cannot copy to MT5 account {account_id}: no broker symbol "
                                f"is mapped to {intent.symbol_name!r}; set the mapping in the "
                                f"account's Details panel"},
                    account_id=account_id, org_id=org_id)
                return True
        self._create_mapping(intent, account_id, org_id)
        command_id = self._mt5_outbox.enqueue_intent(intent, org_id, broker_symbol)
        self._repo.log_event(
            'slave_action', 'info',
            {'action': 'mt5_command_queued', 'command_id': command_id,
             'intent_type': type(intent).__name__},
            account_id=account_id, org_id=org_id)
        return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_dispatch_mt5.py`.
Expected: `6 passed`.

Run DB `tests/unit/test_dispatch.py`.
Expected: all pass (both new constructor arguments default to None, and `_handle_live_send` is byte-for-byte the old body once `_enqueue_for_mt5` returns False).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/engine/dispatch.py copier/tests/unit/test_dispatch_mt5.py && git commit -m "feat(dispatch): route an MT5 slave's intents to the outbox before build_request

The mapping row is created exactly as for a cTrader send (same
client_order_id scheme), so the terminal's ack activates the same row.
An intent for a symbol the terminal cannot serve becomes a warning that
names the Details panel and queues nothing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `copier.mt5.ingress` — an MT5 master's report becomes MasterEvents

**Files:**
- Create: `copier/src/copier/mt5/ingress.py`
- Modify: `copier/src/copier/mt5/registry.py` (Task 6) — the netting master's ledger views
- Test: `copier/tests/unit/test_mt5_ingress.py`; `copier/tests/unit/test_mt5_registry.py` (append a class)

**Interfaces:**
- Consumes: the `Master*` event dataclasses (`domain/models.py:46-120`), `PositionSnapshot`/`OrderSnapshot` (Task 6's shape), `SyncReport`, `ReportDeal`, `CENTILOTS` (Task 2), `Repo.load_net_ledger/upsert_positions/close_missing_positions` (Tasks 4–5) for the registry views.
- Produces (contract §2 `ingress.py`, used by Task 15): `master_events_from_report(report, previous, aliases_reverse, symbols, ledger=None) -> list[MasterEvent]`, plus constants `CLOSE_ENTRIES = ("OUT", "OUT_BY", "INOUT")`, `TRADE_TYPES = ("BUY", "SELL")`. `report.deals` must already be filtered to deals not yet ingested (Task 15 does that); `previous` is the registry snapshot of the TERMINAL'S OWN BOOK taken BEFORE this report was applied (net positions, never virtual ones), `None` on the first report after a restart (then only deals produce events).
- Netting masters (contract §2 `ingress.py`): `VirtualPosition(virtual_id, symbol, side, volume_open, volume_left, stop_loss, take_profit, opened_at_ms)` (one row of `mt5_net_ledger`; `row() -> dict` / `VirtualPosition.from_row(dict)` in the repo's key shape); `NetLedger(rows)` (rows as `VirtualPosition`s or repo dicts) with `apply_deal(deal, canonical_symbol, stop_loss=None, take_profit=None) -> list[MasterEvent]` (`IN` → a new virtual position, id = the deal ticket, + `MasterPositionOpened`; `OUT`/`OUT_BY` → consume the symbol's virtual positions oldest-first, one `MasterPositionClosed` each with THAT position's `remaining_volume`; `INOUT` → close all of the symbol, then open the remainder as a new virtual position; the optional levels are the net position's, stamped on a new virtual position), `apply_protection(symbol, stop_loss, take_profit) -> list[MasterEvent]` (`MasterPositionSLTPAmended` for every virtual position of the broker symbol whose levels differ), `positions() -> list[VirtualPosition]` (oldest first), `dirty_rows() -> tuple[list[VirtualPosition], list[int]]` (upserts, deleted virtual ids since the last call; clears). With `ledger` given, `master_events_from_report` routes deals through `ledger.apply_deal` (a deal whose order is a known pending order is `MasterPendingFilled(order_id, position_id=<deal ticket>)` instead of the open) and net-position SL/TP diffs through `ledger.apply_protection`; the pending-order diff is unchanged.
- Registry views (added here, contract §2 `registry.py`): `MT5Registry.net_ledger(account_id) -> NetLedger` (loaded from `repo.load_net_ledger` on first use, then held), `snapshot(account_id, with_labels=True, virtual=False)` (`virtual=True`: the ledger's positions as `PositionSnapshot`s — `position_id` = virtual id, `volume` = `volume_left`, `price` = the net position's open price, SL/TP the virtual position's — with the terminal's orders), `account_block(account_id, virtual=False)` (`virtual=True`: one entry per virtual position, `current_price` from the net position, `pnl_quote` = the net position's P&L split by volume share), `update_from_sync(..., persist=True)` (`persist=False`: store the report, write nothing — the app persists a netting master's book once the ledger has absorbed the report), `persist_virtual_positions(account_id)` (the virtual book into `positions` via `upsert_positions`/`close_missing_positions`).

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_mt5_ingress.py`:

```python
"""master_events_from_report (copier/src/copier/mt5/ingress.py): an MT5
master's deals and book diffs become the MasterEvents decide() consumes."""

import pytest

from copier.domain.models import (
    MasterPendingCancelled, MasterPendingFilled, MasterPendingPlaced, MasterPendingReplaced,
    MasterPositionClosed, MasterPositionOpened, MasterPositionSLTPAmended, PendingType, Side,
    SymbolInfo)
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.ingress import NetLedger, VirtualPosition, master_events_from_report
from copier.testing.mt5_fixtures import deal, order, position, report

XAU = SymbolInfo(symbol_id=7, name="XAUUSD.r", digits=2, lot_size=100, min_volume=1, step_volume=1)
SYMBOLS = {"XAUUSD.r": XAU}
REVERSE = {"XAUUSD.r": "XAUUSD"}          # broker -> canonical


def _prev(positions=(), orders=()):
    return (list(positions), list(orders))


def _pos_snap(ticket, sl=None, tp=None):
    return PositionSnapshot(position_id=ticket, symbol_id=7, side=Side.BUY, volume=100,
                            price=2400.0, label="", stop_loss=sl, take_profit=tp)


def _order_snap(ticket, volume=100, price=2390.0, sl=None, tp=None):
    return OrderSnapshot(order_id=ticket, symbol_id=7, volume=volume, label="", side=Side.BUY,
                         order_type="LIMIT", price=price, stop_loss=sl, take_profit=tp)


class TestDeals:
    def test_in_deal_opens_a_position_in_canonical_terms(self):
        rep = report(
            positions=[position(7001, symbol="XAUUSD.r", volume=100, open_price=2400.5,
                                sl=2390.0, tp=2420.0)],
            deals=[deal(90001, position=7001, order=90000, symbol="XAUUSD.r", deal_type="BUY",
                        entry="IN", volume=100, price=2400.5)])
        assert master_events_from_report(rep, _prev(), REVERSE, SYMBOLS) == [
            MasterPositionOpened(position_id=7001, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100, lot_size=100, stop_loss=2390.0, take_profit=2420.0,
                                 entry_price=2400.5)]

    def test_an_unaliased_symbol_keeps_its_broker_name_and_default_lot_size(self):
        rep = report(deals=[deal(1, position=5, symbol="GBPJPY.r", deal_type="SELL", entry="IN",
                                 volume=30, price=190.1)])
        (event,) = master_events_from_report(rep, _prev(), REVERSE, SYMBOLS)
        assert (event.symbol_name, event.side, event.lot_size, event.stop_loss) == (
            "GBPJPY.r", Side.SELL, 100, None)

    def test_full_close(self):
        rep = report(positions=[], deals=[deal(2, position=7001, symbol="XAUUSD.r",
                                               deal_type="SELL", entry="OUT", volume=100,
                                               price=2410.0, profit=100.0)])
        assert master_events_from_report(rep, _prev([_pos_snap(7001)]), REVERSE, SYMBOLS) == [
            MasterPositionClosed(position_id=7001, symbol_name="XAUUSD", closed_volume=100,
                                 remaining_volume=0)]

    def test_partial_close_reports_what_is_still_open(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r", volume=60)],
                     deals=[deal(2, position=7001, symbol="XAUUSD.r", deal_type="SELL",
                                 entry="OUT", volume=40, price=2410.0)])
        (event,) = master_events_from_report(rep, _prev([_pos_snap(7001)]), REVERSE, SYMBOLS)
        assert (event.closed_volume, event.remaining_volume) == (40, 60)

    @pytest.mark.parametrize("entry", ["OUT_BY", "INOUT"])
    def test_other_closing_entries_close_too(self, entry):
        rep = report(deals=[deal(2, position=7001, symbol="XAUUSD.r", deal_type="SELL",
                                 entry=entry, volume=100)])
        (event,) = master_events_from_report(rep, _prev(), REVERSE, SYMBOLS)
        assert isinstance(event, MasterPositionClosed) and event.remaining_volume == 0

    def test_balance_operations_are_not_trades(self):
        rep = report(deals=[deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0)])
        assert master_events_from_report(rep, _prev(), REVERSE, SYMBOLS) == []

    def test_deals_are_handled_in_time_order(self):
        rep = report(deals=[
            deal(2, position=7001, symbol="XAUUSD.r", deal_type="SELL", entry="OUT", volume=100,
                 time_ms=2000),
            deal(1, position=7001, symbol="XAUUSD.r", deal_type="BUY", entry="IN", volume=100,
                 time_ms=1000)])
        kinds = [type(e).__name__ for e in master_events_from_report(rep, _prev(), REVERSE, SYMBOLS)]
        assert kinds == ["MasterPositionOpened", "MasterPositionClosed"]


class TestProtection:
    def test_a_changed_stop_or_target_is_an_amend(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r", sl=2395.0, tp=2420.0)])
        assert master_events_from_report(
            rep, _prev([_pos_snap(7001, sl=2390.0, tp=2420.0)]), REVERSE, SYMBOLS) == [
            MasterPositionSLTPAmended(position_id=7001, stop_loss=2395.0, take_profit=2420.0)]

    def test_clearing_protection_is_an_amend_with_nones(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r")])
        (event,) = master_events_from_report(
            rep, _prev([_pos_snap(7001, sl=2390.0)]), REVERSE, SYMBOLS)
        assert (event.stop_loss, event.take_profit) == (None, None)

    def test_unchanged_protection_is_silent(self):
        rep = report(positions=[position(7001, symbol="XAUUSD.r", sl=2390.0)])
        assert master_events_from_report(
            rep, _prev([_pos_snap(7001, sl=2390.0)]), REVERSE, SYMBOLS) == []


class TestPendingOrders:
    def test_a_new_order_is_placed(self):
        rep = report(orders=[order(5551, symbol="XAUUSD.r", order_type="SELL_STOP", volume=50,
                                   price=2380.0, sl=2390.0)])
        assert master_events_from_report(rep, _prev(), REVERSE, SYMBOLS) == [
            MasterPendingPlaced(order_id=5551, symbol_name="XAUUSD", side=Side.SELL,
                                order_type=PendingType.STOP, volume=50, lot_size=100,
                                price=2380.0, stop_loss=2390.0, take_profit=None,
                                expiry_ts_ms=None)]

    def test_a_changed_order_is_replaced(self):
        rep = report(orders=[order(5551, symbol="XAUUSD.r", order_type="BUY_LIMIT", volume=100,
                                   price=2385.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551, price=2390.0)]), REVERSE, SYMBOLS) == [
            MasterPendingReplaced(order_id=5551, symbol_name="XAUUSD", lot_size=100,
                                  order_type=PendingType.LIMIT, volume=100, price=2385.0,
                                  stop_loss=None, take_profit=None)]

    def test_an_unchanged_order_is_silent(self):
        rep = report(orders=[order(5551, symbol="XAUUSD.r", order_type="BUY_LIMIT", volume=100,
                                   price=2390.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS) == []

    def test_a_vanished_order_without_a_deal_is_cancelled(self):
        assert master_events_from_report(
            report(), _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS) == [
            MasterPendingCancelled(order_id=5551)]

    def test_a_vanished_order_with_its_deal_is_filled_not_opened(self):
        rep = report(positions=[position(5551, symbol="XAUUSD.r")],
                     deals=[deal(90001, position=5551, order=5551, symbol="XAUUSD.r",
                                 deal_type="BUY", entry="IN", volume=100, price=2390.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS) == [
            MasterPendingFilled(order_id=5551, position_id=5551)]


class TestFirstReport:
    def test_without_a_previous_snapshot_only_deals_speak(self):
        """After a copier restart every pending order and every stop would
        otherwise look new -- and be copied a second time."""
        rep = report(positions=[position(7001, symbol="XAUUSD.r", sl=2390.0)],
                     orders=[order(5551, symbol="XAUUSD.r")],
                     deals=[deal(1, position=7002, symbol="XAUUSD.r", deal_type="BUY",
                                 entry="IN", volume=10)])
        events = master_events_from_report(rep, None, REVERSE, SYMBOLS)
        assert [type(e).__name__ for e in events] == ["MasterPositionOpened"]


def _vp(virtual_id, volume, side="BUY", symbol="XAUUSD.r", sl=None, tp=None, opened_at_ms=None):
    return VirtualPosition(virtual_id=virtual_id, symbol=symbol, side=side, volume_open=volume,
                           volume_left=volume, stop_loss=sl, take_profit=tp,
                           opened_at_ms=opened_at_ms if opened_at_ms is not None else virtual_id * 1000)


def _xau(ticket, entry, volume, side="BUY", price=2400.0, time_ms=None):
    return deal(ticket, position=5, symbol="XAUUSD.r", deal_type=side, entry=entry, volume=volume,
                price=price, time_ms=time_ms if time_ms is not None else ticket * 1000)


class TestNetLedger:
    """A netting master's single net position per symbol, expanded into the
    virtual positions its followers copy."""

    def test_add_add_reduce_consumes_the_oldest_first_and_partially(self):
        ledger = NetLedger([])
        first = ledger.apply_deal(_xau(1, "IN", 50), "XAUUSD", stop_loss=2390.0, take_profit=None)
        second = ledger.apply_deal(_xau(2, "IN", 30, price=2401.0), "XAUUSD")
        assert first == [MasterPositionOpened(
            position_id=1, symbol_name="XAUUSD", side=Side.BUY, volume=50, lot_size=100,
            stop_loss=2390.0, take_profit=None, entry_price=2400.0)]
        assert (second[0].position_id, second[0].volume, second[0].entry_price) == (2, 30, 2401.0)

        reduced = ledger.apply_deal(_xau(3, "OUT", 60, side="SELL"), "XAUUSD")

        assert reduced == [
            MasterPositionClosed(position_id=1, symbol_name="XAUUSD", closed_volume=50,
                                 remaining_volume=0),
            MasterPositionClosed(position_id=2, symbol_name="XAUUSD", closed_volume=10,
                                 remaining_volume=20)]
        (left,) = ledger.positions()
        assert (left.virtual_id, left.volume_open, left.volume_left) == (2, 30, 20)
        upserts, deleted = ledger.dirty_rows()
        assert [(v.virtual_id, v.volume_left) for v in upserts] == [(2, 20)] and deleted == [1]
        assert ledger.dirty_rows() == ([], [])

    def test_full_close_empties_the_symbol(self):
        ledger = NetLedger([_vp(1, 50), _vp(2, 30)])
        events = ledger.apply_deal(_xau(3, "OUT", 80, side="SELL"), "XAUUSD")
        assert [(e.position_id, e.closed_volume, e.remaining_volume) for e in events] == [
            (1, 50, 0), (2, 30, 0)]
        assert ledger.positions() == []
        assert ledger.dirty_rows() == ([], [1, 2])

    def test_a_close_beyond_what_the_ledger_holds_closes_what_it_has(self):
        ledger = NetLedger([_vp(1, 50)])
        events = ledger.apply_deal(_xau(3, "OUT", 80, side="SELL"), "XAUUSD")
        assert [(e.position_id, e.closed_volume) for e in events] == [(1, 50)]
        assert ledger.positions() == []

    def test_reversal_closes_everything_and_opens_the_remainder_the_other_way(self):
        ledger = NetLedger([_vp(1, 50)])
        events = ledger.apply_deal(_xau(9, "INOUT", 80, side="SELL", price=2410.0), "XAUUSD",
                                   stop_loss=2420.0)
        assert events == [
            MasterPositionClosed(position_id=1, symbol_name="XAUUSD", closed_volume=50,
                                 remaining_volume=0),
            MasterPositionOpened(position_id=9, symbol_name="XAUUSD", side=Side.SELL, volume=30,
                                 lot_size=100, stop_loss=2420.0, take_profit=None,
                                 entry_price=2410.0)]
        (left,) = ledger.positions()
        assert (left.virtual_id, left.side, left.volume_left, left.opened_at_ms) == (9, "SELL", 30, 9000)
        upserts, deleted = ledger.dirty_rows()
        assert [v.virtual_id for v in upserts] == [9] and deleted == [1]

    def test_other_symbols_are_untouched(self):
        ledger = NetLedger([_vp(1, 50), _vp(2, 100, symbol="EURUSD.r")])
        ledger.apply_deal(_xau(3, "OUT", 50, side="SELL"), "XAUUSD")
        assert [v.virtual_id for v in ledger.positions()] == [2]

    def test_a_protection_change_fans_out_to_every_virtual_position_of_the_symbol(self):
        ledger = NetLedger([_vp(1, 50, sl=2390.0), _vp(2, 30, sl=2390.0), _vp(3, 30, symbol="EURUSD.r")])
        events = ledger.apply_protection("XAUUSD.r", 2395.0, 2420.0)
        assert events == [
            MasterPositionSLTPAmended(position_id=1, stop_loss=2395.0, take_profit=2420.0),
            MasterPositionSLTPAmended(position_id=2, stop_loss=2395.0, take_profit=2420.0)]
        assert ledger.apply_protection("XAUUSD.r", 2395.0, 2420.0) == []      # nothing changed
        upserts, _deleted = ledger.dirty_rows()
        assert sorted((v.virtual_id, v.stop_loss) for v in upserts) == [(1, 2395.0), (2, 2395.0)]

    def test_balance_operations_and_zero_volume_deals_are_ignored(self):
        ledger = NetLedger([])
        assert ledger.apply_deal(deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0), "") == []
        assert ledger.apply_deal(_xau(4, "IN", 0), "XAUUSD") == []
        assert ledger.positions() == [] and ledger.dirty_rows() == ([], [])

    def test_restart_rebuilds_the_ledger_from_its_rows(self):
        before = NetLedger([])
        before.apply_deal(_xau(1, "IN", 50), "XAUUSD")
        before.apply_deal(_xau(2, "IN", 30), "XAUUSD")
        before.apply_deal(_xau(3, "OUT", 20, side="SELL"), "XAUUSD")
        upserts, deleted = before.dirty_rows()
        rows = [v.row() for v in upserts]                       # what repo.upsert_net_ledger stored
        assert deleted == [] and [(r["virtual_id"], r["volume_left"]) for r in rows] == [(1, 30), (2, 30)]
        assert set(rows[0]) == {"virtual_id", "symbol", "side", "volume_open", "volume_left",
                                "stop_loss", "take_profit", "opened_at_ms"}

        after = NetLedger(rows)                                 # the copier restarted

        assert [(v.virtual_id, v.volume_left) for v in after.positions()] == [(1, 30), (2, 30)]
        events = after.apply_deal(_xau(4, "OUT", 40, side="SELL"), "XAUUSD")
        assert [(e.position_id, e.closed_volume, e.remaining_volume) for e in events] == [
            (1, 30, 0), (2, 10, 20)]


class TestNettingMaster:
    """master_events_from_report with a ledger: deals expand through it and
    the net position's protection fans out to every virtual position."""

    def test_deals_go_through_the_ledger_with_the_net_positions_levels(self):
        ledger = NetLedger([])
        rep = report(positions=[position(5, symbol="XAUUSD.r", volume=80, sl=2390.0)],
                     deals=[_xau(1, "IN", 50), _xau(2, "IN", 30, price=2401.0)])
        events = master_events_from_report(rep, _prev(), REVERSE, SYMBOLS, ledger=ledger)
        assert [(type(e).__name__, e.position_id, e.stop_loss) for e in events] == [
            ("MasterPositionOpened", 1, 2390.0), ("MasterPositionOpened", 2, 2390.0)]
        assert [v.virtual_id for v in ledger.positions()] == [1, 2]

    def test_a_net_stop_change_amends_every_virtual_position(self):
        ledger = NetLedger([_vp(1, 50, sl=2390.0), _vp(2, 30, sl=2390.0)])
        rep = report(positions=[position(5, symbol="XAUUSD.r", volume=80, sl=2395.0)])
        prev = _prev([PositionSnapshot(position_id=5, symbol_id=7, side=Side.BUY, volume=80,
                                       price=2400.0, label="", stop_loss=2390.0)])
        assert master_events_from_report(rep, prev, REVERSE, SYMBOLS, ledger=ledger) == [
            MasterPositionSLTPAmended(position_id=1, stop_loss=2395.0, take_profit=None),
            MasterPositionSLTPAmended(position_id=2, stop_loss=2395.0, take_profit=None)]

    def test_deals_are_absorbed_before_the_protection_diff(self):
        """A reversal in the same report as a stop change: the old virtual
        positions are closed first, so the amend reaches only the new one."""
        ledger = NetLedger([_vp(1, 50, sl=2390.0)])
        rep = report(positions=[position(5, symbol="XAUUSD.r", side="SELL", volume=30, sl=2420.0)],
                     deals=[_xau(9, "INOUT", 80, side="SELL", price=2410.0)])
        prev = _prev([PositionSnapshot(position_id=5, symbol_id=7, side=Side.BUY, volume=50,
                                       price=2400.0, label="", stop_loss=2390.0)])
        events = master_events_from_report(rep, prev, REVERSE, SYMBOLS, ledger=ledger)
        assert [type(e).__name__ for e in events] == ["MasterPositionClosed", "MasterPositionOpened"]
        assert events[1].stop_loss == 2420.0                     # carried by the open, not re-amended

    def test_a_pending_fill_links_to_the_virtual_id(self):
        ledger = NetLedger([])
        rep = report(positions=[position(5, symbol="XAUUSD.r")],
                     deals=[deal(90001, position=5, order=5551, symbol="XAUUSD.r", deal_type="BUY",
                                 entry="IN", volume=100, price=2390.0)])
        assert master_events_from_report(
            rep, _prev(orders=[_order_snap(5551)]), REVERSE, SYMBOLS, ledger=ledger) == [
            MasterPendingFilled(order_id=5551, position_id=90001)]
        assert [v.virtual_id for v in ledger.positions()] == [90001]

    def test_the_first_report_after_a_restart_still_diffs_nothing(self):
        ledger = NetLedger([_vp(1, 50, sl=2390.0)])
        rep = report(positions=[position(5, symbol="XAUUSD.r", sl=2395.0)],
                     orders=[order(5551, symbol="XAUUSD.r")])
        assert master_events_from_report(rep, None, REVERSE, SYMBOLS, ledger=ledger) == []
        assert ledger.positions()[0].stop_loss == 2390.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run PURE `tests/unit/test_mt5_ingress.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5.ingress'`.

- [ ] **Step 3: Write the module**

Create `copier/src/copier/mt5/ingress.py`:

```python
"""An MT5 master's report -> the MasterEvents the decision core consumes.

This is normalize.py's counterpart for a terminal that reports state
rather than pushing execution events: deals say what was traded, and the
difference between this report's book and the previous one says what was
amended, placed, replaced or cancelled. Symbol names are translated from
the broker's to the canonical names the followers know before anything
reaches decide().

Netting masters (spec "Netting accounts"): the terminal holds ONE position
per symbol, so its deals cannot be copied as positions one-to-one. The
NetLedger expands the net position into VIRTUAL master positions: an IN
deal opens one (its id is the deal ticket), an OUT deal consumes them
oldest-first, an INOUT closes them all and opens the remainder the other
way, and a stop/target change on the net position reaches every virtual
position of the symbol. Followers see MasterPositionOpened/Closed/
SLTPAmended by virtual id exactly as they would a hedging master's, and
the ledger's rows are persisted by the caller (mt5_net_ledger) so a
restart keeps them.
"""

from dataclasses import dataclass

from copier.domain.models import (
    MasterEvent, MasterPendingCancelled, MasterPendingFilled, MasterPendingPlaced,
    MasterPendingReplaced, MasterPositionClosed, MasterPositionOpened,
    MasterPositionSLTPAmended, PendingType, Side, SymbolInfo)
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.protocol import CENTILOTS, ReportDeal, SyncReport

CLOSE_ENTRIES = ("OUT", "OUT_BY", "INOUT")
TRADE_TYPES = ("BUY", "SELL")


@dataclass
class VirtualPosition:
    """One row of mt5_net_ledger: a slice of a netting master's net position
    that followers copy as a position of its own. Its id is the IN deal's
    ticket, which is what the followers' mapping rows carry as
    master_position_id."""
    virtual_id: int
    symbol: str                       # broker name
    side: str                         # "BUY" | "SELL"
    volume_open: int                  # centilots at open
    volume_left: int                  # centilots still open
    stop_loss: float | None
    take_profit: float | None
    opened_at_ms: int

    def row(self) -> dict:
        """The repo's key shape (Repo.upsert_net_ledger)."""
        return {"virtual_id": self.virtual_id, "symbol": self.symbol, "side": self.side,
                "volume_open": self.volume_open, "volume_left": self.volume_left,
                "stop_loss": self.stop_loss, "take_profit": self.take_profit,
                "opened_at_ms": self.opened_at_ms}

    @classmethod
    def from_row(cls, row: dict) -> "VirtualPosition":
        return cls(virtual_id=int(row["virtual_id"]), symbol=row["symbol"], side=row["side"],
                   volume_open=int(row["volume_open"]), volume_left=int(row["volume_left"]),
                   stop_loss=row.get("stop_loss"), take_profit=row.get("take_profit"),
                   opened_at_ms=int(row["opened_at_ms"]))


def _order_key(v: VirtualPosition) -> tuple[int, int]:
    return (v.opened_at_ms, v.virtual_id)


class NetLedger:
    """Netting MASTER bookkeeping. Pure: nothing here touches the database;
    the caller persists dirty_rows() after every report."""

    def __init__(self, rows):
        self._positions: list[VirtualPosition] = sorted(
            (r if isinstance(r, VirtualPosition) else VirtualPosition.from_row(r) for r in rows),
            key=_order_key)
        self._dirty: dict[int, VirtualPosition] = {}
        self._deleted: list[int] = []

    def positions(self) -> list[VirtualPosition]:
        """Oldest first: the order OUT deals consume them in."""
        return list(self._positions)

    def _of_symbol(self, symbol: str) -> list[VirtualPosition]:
        return [v for v in self._positions if v.symbol == symbol]

    def _open(self, deal: ReportDeal, volume: int, stop_loss, take_profit) -> VirtualPosition:
        vp = VirtualPosition(
            virtual_id=deal.ticket, symbol=deal.symbol, side=deal.deal_type, volume_open=volume,
            volume_left=volume, stop_loss=stop_loss, take_profit=take_profit,
            opened_at_ms=deal.time_ms)
        self._positions.append(vp)
        self._positions.sort(key=_order_key)
        self._dirty[vp.virtual_id] = vp
        self._deleted = [d for d in self._deleted if d != vp.virtual_id]
        return vp

    def _consume(self, symbol: str, canonical: str, volume: int) -> list[MasterEvent]:
        """Take `volume` out of the symbol's virtual positions oldest-first.
        A close beyond what the ledger holds (a position opened while the
        copier was down) closes what it has and stops."""
        events: list[MasterEvent] = []
        remaining = volume
        for vp in self._of_symbol(symbol):
            if remaining <= 0:
                break
            take = min(vp.volume_left, remaining)
            if take <= 0:
                continue
            vp.volume_left -= take
            remaining -= take
            events.append(MasterPositionClosed(
                position_id=vp.virtual_id, symbol_name=canonical, closed_volume=take,
                remaining_volume=vp.volume_left))
            if vp.volume_left <= 0:
                self._positions.remove(vp)
                self._dirty.pop(vp.virtual_id, None)
                self._deleted.append(vp.virtual_id)
            else:
                self._dirty[vp.virtual_id] = vp
        return events

    def apply_deal(self, deal: ReportDeal, canonical_symbol: str,
                   stop_loss: float | None = None, take_profit: float | None = None,
                   ) -> list[MasterEvent]:
        """One trade deal of the net position -> the events its followers act
        on. `stop_loss`/`take_profit` are the net position's current levels
        (from the report), stamped on a virtual position this deal opens."""
        if deal.deal_type not in TRADE_TYPES or deal.volume <= 0:
            return []
        if deal.entry == "IN":
            vp = self._open(deal, deal.volume, stop_loss, take_profit)
            return [MasterPositionOpened(
                position_id=vp.virtual_id, symbol_name=canonical_symbol, side=Side(vp.side),
                volume=vp.volume_open, lot_size=CENTILOTS, stop_loss=stop_loss,
                take_profit=take_profit, entry_price=deal.price)]
        if deal.entry in ("OUT", "OUT_BY"):
            return self._consume(deal.symbol, canonical_symbol, deal.volume)
        if deal.entry == "INOUT":
            held = sum(v.volume_left for v in self._of_symbol(deal.symbol))
            events = self._consume(deal.symbol, canonical_symbol, held)
            remainder = deal.volume - held
            if remainder > 0:
                vp = self._open(deal, remainder, stop_loss, take_profit)
                events.append(MasterPositionOpened(
                    position_id=vp.virtual_id, symbol_name=canonical_symbol, side=Side(vp.side),
                    volume=remainder, lot_size=CENTILOTS, stop_loss=stop_loss,
                    take_profit=take_profit, entry_price=deal.price))
            return events
        return []

    def apply_protection(self, symbol: str, stop_loss: float | None,
                         take_profit: float | None) -> list[MasterEvent]:
        """The net position's stop/target changed: every virtual position of
        the (broker) symbol takes the new levels, and each one's followers
        are told."""
        events: list[MasterEvent] = []
        for vp in self._of_symbol(symbol):
            if (vp.stop_loss, vp.take_profit) == (stop_loss, take_profit):
                continue
            vp.stop_loss, vp.take_profit = stop_loss, take_profit
            self._dirty[vp.virtual_id] = vp
            events.append(MasterPositionSLTPAmended(
                position_id=vp.virtual_id, stop_loss=stop_loss, take_profit=take_profit))
        return events

    def dirty_rows(self) -> tuple[list[VirtualPosition], list[int]]:
        """(upserts, deleted virtual ids) since the last call; clears both."""
        upserts, deleted = list(self._dirty.values()), list(self._deleted)
        self._dirty, self._deleted = {}, []
        return upserts, deleted


def _side_of(order_type: str) -> Side:
    return Side.BUY if order_type.startswith("BUY") else Side.SELL


def _pending_type_of(order_type: str) -> PendingType:
    return PendingType.LIMIT if order_type.endswith("LIMIT") else PendingType.STOP


def master_events_from_report(
    report: SyncReport,
    previous: tuple[list[PositionSnapshot], list[OrderSnapshot]] | None,
    aliases_reverse: dict[str, str],
    symbols: dict[str, SymbolInfo],
    ledger: NetLedger | None = None,
) -> list[MasterEvent]:
    """
    deals (in time order; the caller passes only deals not yet ingested):
      entry IN  -> MasterPositionOpened (SL/TP from the matching position in
                   this report, entry_price = the deal's), unless the deal's
                   order is a pending order known from `previous` -- then
                   MasterPendingFilled, as cTrader's LIMIT/STOP fill would be
      entry OUT / OUT_BY / INOUT -> MasterPositionClosed with remaining_volume
                   = what this report still shows for the position, else 0
      with `ledger` (a netting master): every trade deal goes through
      ledger.apply_deal instead -- virtual positions, ids = deal tickets; a
      pending order's fill is MasterPendingFilled(position_id=deal ticket)
    positions vs previous: SL/TP changed -> MasterPositionSLTPAmended; with
      `ledger`, through ledger.apply_protection (one amend per virtual
      position of the symbol), AFTER the deals so a reversal in the same
      report does not amend positions it just closed
    orders vs previous: new -> MasterPendingPlaced; volume/price/SL/TP changed
      -> MasterPendingReplaced; gone without a deal -> MasterPendingCancelled
      (gone WITH its deal was emitted as MasterPendingFilled above)
    previous is None (the first report after a copier restart): no diff
      events -- every pending order would otherwise look new and be copied
      twice. `previous` is always the terminal's own book (net positions),
      never the virtual one.
    """
    events: list[MasterEvent] = []
    prev_positions = {p.position_id: p for p in previous[0]} if previous is not None else {}
    prev_orders = {o.order_id: o for o in previous[1]} if previous is not None else {}
    cur_positions = {p.ticket: p for p in report.positions}
    cur_orders = {o.ticket: o for o in report.orders}
    filled_orders = {d.order for d in report.deals if d.order and d.entry == "IN"}

    def canonical(broker_name: str) -> str:
        return aliases_reverse.get(broker_name, broker_name)

    def lot_size(broker_name: str) -> int:
        info = symbols.get(broker_name)
        return info.lot_size if info is not None else CENTILOTS

    for d in sorted(report.deals, key=lambda d: (d.time_ms, d.ticket)):
        if d.deal_type not in TRADE_TYPES:
            continue
        pos = cur_positions.get(d.position)
        if ledger is not None:
            produced = ledger.apply_deal(
                d, canonical(d.symbol),
                stop_loss=pos.stop_loss if pos is not None else None,
                take_profit=pos.take_profit if pos is not None else None)
            if d.entry == "IN" and d.order in prev_orders:
                events.append(MasterPendingFilled(order_id=d.order, position_id=d.ticket))
                continue
            events.extend(produced)
            continue
        if d.entry == "IN":
            if d.order in prev_orders:
                events.append(MasterPendingFilled(order_id=d.order, position_id=d.position))
                continue
            events.append(MasterPositionOpened(
                position_id=d.position, symbol_name=canonical(d.symbol), side=Side(d.deal_type),
                volume=d.volume, lot_size=lot_size(d.symbol),
                stop_loss=pos.stop_loss if pos is not None else None,
                take_profit=pos.take_profit if pos is not None else None,
                entry_price=d.price,
            ))
        elif d.entry in CLOSE_ENTRIES:
            events.append(MasterPositionClosed(
                position_id=d.position, symbol_name=canonical(d.symbol), closed_volume=d.volume,
                remaining_volume=pos.volume if pos is not None else 0,
            ))

    if previous is None:
        return events

    for ticket, pos in cur_positions.items():
        prev = prev_positions.get(ticket)
        if prev is None:
            continue
        if (prev.stop_loss, prev.take_profit) != (pos.stop_loss, pos.take_profit):
            if ledger is not None:
                events.extend(ledger.apply_protection(pos.symbol, pos.stop_loss, pos.take_profit))
            else:
                events.append(MasterPositionSLTPAmended(
                    position_id=ticket, stop_loss=pos.stop_loss, take_profit=pos.take_profit))

    for ticket, o in cur_orders.items():
        prev = prev_orders.get(ticket)
        if prev is None:
            events.append(MasterPendingPlaced(
                order_id=ticket, symbol_name=canonical(o.symbol), side=_side_of(o.order_type),
                order_type=_pending_type_of(o.order_type), volume=o.volume,
                lot_size=lot_size(o.symbol), price=o.price, stop_loss=o.stop_loss,
                take_profit=o.take_profit, expiry_ts_ms=None,
            ))
        elif (prev.volume, prev.price, prev.stop_loss, prev.take_profit) != (
                o.volume, o.price, o.stop_loss, o.take_profit):
            events.append(MasterPendingReplaced(
                order_id=ticket, symbol_name=canonical(o.symbol), lot_size=lot_size(o.symbol),
                order_type=_pending_type_of(o.order_type), volume=o.volume, price=o.price,
                stop_loss=o.stop_loss, take_profit=o.take_profit,
            ))

    for ticket in prev_orders:
        if ticket in cur_orders or ticket in filled_orders:
            continue
        events.append(MasterPendingCancelled(order_id=ticket))

    return events
```

- [ ] **Step 4: Extend the registry with the netting master's ledger views**

The registry (Task 6) could not hold a `NetLedger` before this module existed. In `copier/src/copier/mt5/registry.py`:

(a) Add the import after `from copier.engine.reconcile import OrderSnapshot, PositionSnapshot`:

```python
from copier.mt5.ingress import NetLedger, VirtualPosition
```

(b) `_AccountState` gains a field after `persisted_signature`:

```python
    ledger: NetLedger | None = None            # netting masters; loaded from the repo on first use
```

(c) Replace `update_from_sync` with:

```python
    def update_from_sync(self, account_id: int, org_id: int, report: SyncReport,
                         now: float, persist: bool = True) -> None:
        """Store the report; persist positions when the book changed.

        persist=False stores the report and writes nothing: a netting
        master's positions table holds its VIRTUAL positions, which the app
        persists (persist_virtual_positions) once the ledger has absorbed
        this report's deals."""
        state = self._state(account_id, org_id)
        state.report = report
        state.last_seen = now
        if not persist:
            return
        signature = tuple(sorted(
            (p.ticket, p.volume, p.stop_loss, p.take_profit) for p in report.positions))
        if signature == state.persisted_signature:
            return
        positions, _orders = self.snapshot(account_id)
        self._persist(account_id, org_id, positions)
        state.persisted_signature = signature

    def _persist(self, account_id: int, org_id: int, positions: list[PositionSnapshot]) -> None:
        symbols_by_id = {info.symbol_id: info
                         for info in self.symbols_by_name(account_id).values()}
        self._repo.upsert_positions(account_id, org_id, positions, symbols_by_id)
        self._repo.close_missing_positions(account_id, [p.position_id for p in positions])
```

(d) Replace `snapshot` with:

```python
    def snapshot(self, account_id: int, with_labels: bool = True, virtual: bool = False):
        """(positions, orders) as reconcile's snapshot types, or None before
        the first report. with_labels=False skips the mapping lookup (the
        ingress diff needs only the book). virtual=True answers a netting
        MASTER's book as its ledger's virtual positions -- what the
        followers' mappings and the reconciler line up by -- priced at the
        net position's open price; the orders are the terminal's."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return None
        symbols = self.symbols_by_name(account_id)
        labels = self._labels(state, account_id) if with_labels else {}
        if virtual:
            positions = self._virtual_positions(account_id, state.report, symbols)
        else:
            positions = [
                PositionSnapshot(
                    position_id=p.ticket, symbol_id=self._symbol_id(symbols, p.symbol),
                    side=Side(p.side), volume=p.volume, price=p.open_price,
                    label=labels.get(p.ticket, p.comment),
                    stop_loss=p.stop_loss, take_profit=p.take_profit,
                )
                for p in state.report.positions
            ]
        orders = [
            OrderSnapshot(
                order_id=o.ticket, symbol_id=self._symbol_id(symbols, o.symbol),
                volume=o.volume, label=labels.get(o.ticket, o.comment),
                side=Side.BUY if o.order_type.startswith("BUY") else Side.SELL,
                order_type=o.order_type.split("_", 1)[1], price=o.price,
                stop_loss=o.stop_loss, take_profit=o.take_profit,
            )
            for o in state.report.orders
        ]
        return positions, orders

    def _virtual_positions(self, account_id: int, report: SyncReport,
                           symbols: dict[str, SymbolInfo]) -> list[PositionSnapshot]:
        net_by_symbol = {p.symbol: p for p in report.positions}
        out = []
        for v in self.net_ledger(account_id).positions():
            net = net_by_symbol.get(v.symbol)
            out.append(PositionSnapshot(
                position_id=v.virtual_id, symbol_id=self._symbol_id(symbols, v.symbol),
                side=Side(v.side), volume=v.volume_left,
                price=net.open_price if net is not None else 0.0, label="",
                stop_loss=v.stop_loss, take_profit=v.take_profit))
        return out
```

(e) Replace `account_block` with:

```python
    def account_block(self, account_id: int, virtual: bool = False) -> dict | None:
        """The account's entry in get_state's `accounts` block, in exactly the
        shape AccountStateTracker.snapshot() produces for a cTrader account
        -- balance, equity and marks as the terminal reports them.
        virtual=True (a netting master): one entry per virtual position,
        current price from the net position and its P&L split by volume
        share, so the Positions screen and the copies line up by virtual id."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return None
        symbols = self.symbols_by_name(account_id)
        report = state.report
        if virtual:
            positions = self._virtual_marks(account_id, report, symbols)
        else:
            positions = [
                {"position_id": p.ticket, "symbol_id": self._symbol_id(symbols, p.symbol),
                 "symbol": p.symbol, "side": p.side, "volume": p.volume,
                 "entry_price": p.open_price, "stop_loss": p.stop_loss,
                 "take_profit": p.take_profit, "pnl_quote": p.pnl,
                 "current_price": p.current_price}
                for p in report.positions
            ]
        return {
            "balance": report.balance,
            "equity": report.equity,
            "open_pnl": sum(p.pnl for p in report.positions),
            "positions": positions,
        }

    def _virtual_marks(self, account_id: int, report: SyncReport,
                       symbols: dict[str, SymbolInfo]) -> list[dict]:
        ledger = self.net_ledger(account_id).positions()
        net_by_symbol = {p.symbol: p for p in report.positions}
        held: dict[str, int] = {}
        for v in ledger:
            held[v.symbol] = held.get(v.symbol, 0) + v.volume_left
        out = []
        for v in ledger:
            net = net_by_symbol.get(v.symbol)
            share = v.volume_left / held[v.symbol] if held.get(v.symbol) else 0.0
            out.append({
                "position_id": v.virtual_id, "symbol_id": self._symbol_id(symbols, v.symbol),
                "symbol": v.symbol, "side": v.side, "volume": v.volume_left,
                "entry_price": net.open_price if net is not None else None,
                "stop_loss": v.stop_loss, "take_profit": v.take_profit,
                "pnl_quote": round(net.pnl * share, 2) if net is not None else 0.0,
                "current_price": net.current_price if net is not None else None})
        return out
```

(f) Append after `margin_mode`:

```python
    # ---------- netting masters ----------

    def net_ledger(self, account_id: int) -> NetLedger:
        """The netting master's virtual positions, rebuilt from mt5_net_ledger
        on first use (a restart keeps them) and held from then on. The app
        persists ledger.dirty_rows() after every report (main.py)."""
        state = self._state(account_id)
        if state.ledger is None:
            state.ledger = NetLedger(
                [VirtualPosition.from_row(r) for r in self._repo.load_net_ledger(account_id)])
        return state.ledger

    def persist_virtual_positions(self, account_id: int) -> None:
        """A netting master's positions table holds its virtual positions
        (what the Positions page and a restart read); called by the app
        once the ledger has absorbed the report's deals."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return
        positions, _orders = self.snapshot(account_id, with_labels=False, virtual=True)
        self._persist(account_id, state.org_id, positions)
```

Append to `copier/tests/unit/test_mt5_registry.py`:

```python


class TestNettingMasterViews:
    """A netting MASTER's book as the reconciler and get_state see it: the
    ledger's virtual positions, marked from the net position."""

    def _netting_master(self, world):
        repo, org_id, mt5_id, registry = world
        registry.update_from_hello(mt5_id, org_id, _hello([XAUUSD], hedging=False), now=1.0)
        repo.upsert_net_ledger(mt5_id, [
            {"virtual_id": 1, "symbol": "XAUUSD.r", "side": "BUY", "volume_open": 50,
             "volume_left": 50, "stop_loss": 2390.0, "take_profit": None, "opened_at_ms": 1000},
            {"virtual_id": 2, "symbol": "XAUUSD.r", "side": "BUY", "volume_open": 30,
             "volume_left": 30, "stop_loss": 2390.0, "take_profit": None, "opened_at_ms": 2000}])
        registry.update_from_sync(mt5_id, org_id, report([
            position(5, symbol="XAUUSD.r", volume=80, open_price=2400.4, sl=2390.0, price=2410.0,
                     pnl=80.0)]), now=2.0, persist=False)
        return repo, org_id, mt5_id, registry

    def test_the_ledger_is_loaded_from_postgres_on_first_use_and_held(self, world):
        repo, org_id, mt5_id, registry = self._netting_master(world)
        assert registry.margin_mode(mt5_id) == "netting"
        assert [v.virtual_id for v in registry.net_ledger(mt5_id).positions()] == [1, 2]
        assert registry.net_ledger(mt5_id) is registry.net_ledger(mt5_id)

    def test_the_virtual_snapshot_is_priced_from_the_net_position(self, world):
        repo, org_id, mt5_id, registry = self._netting_master(world)
        xau_id = registry.symbol_by_name(mt5_id, "XAUUSD.r").symbol_id
        positions, orders = registry.snapshot(mt5_id, virtual=True)
        assert positions == [
            PositionSnapshot(position_id=1, symbol_id=xau_id, side=Side.BUY, volume=50,
                             price=2400.4, label="", stop_loss=2390.0, take_profit=None),
            PositionSnapshot(position_id=2, symbol_id=xau_id, side=Side.BUY, volume=30,
                             price=2400.4, label="", stop_loss=2390.0, take_profit=None)]
        assert orders == []
        # The terminal's own book is still there for the ingress diff.
        assert [p.position_id for p in registry.snapshot(mt5_id, with_labels=False)[0]] == [5]

    def test_the_virtual_account_block_splits_pnl_by_volume_share(self, world):
        repo, org_id, mt5_id, registry = self._netting_master(world)
        block = registry.account_block(mt5_id, virtual=True)
        assert (block["balance"], block["equity"], block["open_pnl"]) == (10000.0, 10000.0, 80.0)
        assert [(p["position_id"], p["volume"], p["pnl_quote"], p["current_price"],
                 p["entry_price"], p["stop_loss"]) for p in block["positions"]] == [
            (1, 50, 50.0, 2410.0, 2400.4, 2390.0), (2, 30, 30.0, 2410.0, 2400.4, 2390.0)]
        assert [p["position_id"] for p in registry.account_block(mt5_id)["positions"]] == [5]

    def test_persist_writes_the_virtual_book_not_the_net_ticket(self, world):
        repo, org_id, mt5_id, registry = self._netting_master(world)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM positions WHERE account_id = %s",
                                (mt5_id,)).fetchone() == (0,)        # persist=False wrote nothing
        registry.persist_virtual_positions(mt5_id)
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions WHERE account_id = %s"
                " ORDER BY position_id", (mt5_id,)).fetchall()
        assert rows == [(1, 50, "open"), (2, 30, "open")]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run PURE `tests/unit/test_mt5_ingress.py`.
Expected: `30 passed`.

Run DB `tests/unit/test_mt5_registry.py`.
Expected: `16 passed`.

- [ ] **Step 6: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/ingress.py copier/src/copier/mt5/registry.py copier/tests/unit/test_mt5_ingress.py copier/tests/unit/test_mt5_registry.py && git commit -m "feat(mt5): ingress -- an MT5 master's deals and book diffs as MasterEvents

IN/OUT/INOUT deals open and close (remaining volume from the report), a
changed stop or target amends, and the pending book is diffed into
placed/replaced/cancelled/filled -- all in canonical symbol names. The
first report after a restart yields deal events only. A netting master's
net position is expanded by the NetLedger into virtual positions (IN
opens, OUT consumes oldest-first, INOUT reverses, a net stop change fans
out); the registry serves them as the master's snapshot and marks.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: CopierService — `act_on_master_event`, `SlaveFill`, `handle_slave_*`

**Files:**
- Modify: `copier/src/copier/engine/service.py:15-32` (imports), `:45-52` (add `SlaveFill` + `_same_level` after `master_position_of`), `:156-186` (`_protect_new_copy`), `:315-417` (`_handle_master_event` → four methods), `:562-703` (`_handle_slave_fill` → wrapper + `handle_slave_fill`), `:705-746` (`_handle_slave_order_accepted` → wrapper + `handle_slave_order_accepted`), `:748-786` (cancelled likewise), `:788-827` (rejected → wrapper + `handle_slave_rejection`)
- Test: `copier/tests/unit/test_service_mt5.py`; `copier/tests/unit/test_service.py` must stay green

**Interfaces:**
- Consumes: everything the service already uses.
- Produces (contract §2 "Service and control", used by Task 15): `SlaveFill(account_id, client_order_id, position_id, filled_volume, fill_price, closed_volume, label, order_id=None, stop_loss=None, take_profit=None)` (the last three are defaulted additions: the slave's own order ticket, and the protection the copy already carries); `CopierService.act_on_master_event(org_id, master_account_id, normalized, *, source) -> None`; `handle_slave_fill(org_id, fill) -> None` (a closing fill whose `client_order_id` starts with `cm` reduces exactly that mapping — `reduce_position_mapping(..., client_order_id=)`, Task 4 — which is how a netting MT5 follower's `:close` ack, reported under the mapping's own coid, reduces one of several copies sharing a net ticket; a closing fill without one reduces by position as before, and the `position_closed` event names the coid when it had one); `handle_slave_rejection(org_id, account_id, client_order_id, reason, error_code=None) -> None`; private additions `handle_slave_order_accepted(org_id, account_id, client_order_id, slave_order_id)`, `handle_slave_order_cancelled(org_id, account_id, slave_order_id)`. The cTrader protobuf path keeps every event payload it writes today.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_service_mt5.py`:

```python
"""The platform-neutral seams of CopierService (engine/service.py) that the
MT5 lane drives directly: act_on_master_event, handle_slave_fill,
handle_slave_rejection, handle_slave_order_accepted/cancelled. The cTrader
protobuf path is exercised by test_service.py, whose fixtures this reuses."""

import psycopg
import pytest

from copier.domain.models import (
    MANUAL_ORDER_LABEL, AmendPositionSLTP, ClosePosition, MasterPositionClosed,
    MasterPositionOpened, MasterPositionSLTPAmended, OpenMarket, Side)
from copier.engine.service import SlaveFill

from test_service import (  # noqa: F401  (fixtures are used by name)
    ORG_ID, clock, db_seeded, recording_dispatcher, repo, routing_box, service)


def _events(repo, action=None, category=None):
    query = "SELECT category, severity, payload, latency_ms, account_id FROM events"
    clauses, params = [], []
    if action is not None:
        clauses.append("payload->>'action' = %s")
        params.append(action)
    if category is not None:
        clauses.append("category = %s")
        params.append(category)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


def _fill(account_id=100, coid="cm11.100", position_id=55, volume=10_000_000, price=1.1, **extra):
    return SlaveFill(account_id=account_id, client_order_id=coid, position_id=position_id,
                     filled_volume=volume, fill_price=price, closed_volume=None, label="",
                     **extra)


OPENED = MasterPositionOpened(position_id=11, symbol_name="EURUSD", side=Side.BUY,
                              volume=10_000_000, lot_size=10_000_000, stop_loss=1.09,
                              take_profit=1.12, entry_price=1.1)


def _amends(dispatcher):
    return [i for i in dispatcher.intents if isinstance(i, AmendPositionSLTP)]


class TestActOnMasterEvent:
    def test_decides_dispatches_and_audits_under_the_org(self, service, recording_dispatcher, repo):
        calls = []
        service.on_positions_changed = lambda org_id=None: calls.append(org_id)

        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")

        opens = [i for i in recording_dispatcher.intents if isinstance(i, OpenMarket)]
        assert {i.slave_account_id for i in opens} == {100, 101}
        assert all(i.stop_loss == 1.09 and i.take_profit == 1.12 and i.entry_price == 1.1
                   for i in opens)
        assert [org for _i, org in recording_dispatcher.calls] == [ORG_ID]
        (event,) = _events(repo, category="master_event")
        assert event[2] == {"source": "mt5", "normalized": "MasterPositionOpened"}
        assert event[3] is not None and event[3] >= 0 and event[4] == 999
        assert calls == [ORG_ID]

    def test_a_close_reaches_the_mapped_copies(self, service, recording_dispatcher, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 10_000_000)
        service.act_on_master_event(
            ORG_ID, 999, MasterPositionClosed(position_id=11, symbol_name="EURUSD",
                                              closed_volume=10_000_000, remaining_volume=0),
            source="mt5")
        closes = [i for i in recording_dispatcher.intents if isinstance(i, ClosePosition)]
        assert closes == [ClosePosition(100, 55, 10_000_000)]

    def test_the_audit_row_lands_even_when_dispatch_raises(self, service, recording_dispatcher, repo):
        recording_dispatcher.dispatch.side_effect = RuntimeError("wire fell over")
        with pytest.raises(RuntimeError):
            service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")
        assert len(_events(repo, category="master_event")) == 1


class TestHandleSlaveFill:
    def test_activates_the_mapping_with_the_fill_price(self, service, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill(price=1.10537))
        (mapping,) = repo.mapping_rows(org_id=ORG_ID)
        assert (mapping["status"], mapping["slave_position_id"], mapping["slave_volume"],
                mapping["fill_price"]) == ("active", 55, 10_000_000, 1.10537)
        (event,) = _events(repo, "position_filled")
        assert event[2]["fill_price"] == 1.10537 and event[4] == 100

    def test_a_copy_already_carrying_the_masters_protection_is_not_amended_again(
            self, service, recording_dispatcher, repo):
        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")   # remembers 1.09 / 1.12
        recording_dispatcher.intents.clear()
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill(stop_loss=1.09, take_profit=1.12))
        assert _amends(recording_dispatcher) == []

    def test_a_copy_whose_protection_differs_is_brought_up_to_date(
            self, service, recording_dispatcher, repo):
        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")
        service.act_on_master_event(
            ORG_ID, 999, MasterPositionSLTPAmended(position_id=11, stop_loss=1.095,
                                                   take_profit=1.12), source="mt5")
        recording_dispatcher.intents.clear()
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill(stop_loss=1.09, take_profit=1.12))   # the OLD stop
        assert _amends(recording_dispatcher) == [AmendPositionSLTP(100, 55, 1.095, 1.12)]

    def test_an_unknown_protection_is_restated_as_before(self, service, recording_dispatcher, repo):
        service.act_on_master_event(ORG_ID, 999, OPENED, source="mt5")
        recording_dispatcher.intents.clear()
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_fill(ORG_ID, _fill())        # cTrader-style: levels unknown
        assert _amends(recording_dispatcher) == [AmendPositionSLTP(100, 55, 1.09, 1.12)]

    def test_a_closing_fill_reduces_the_mapping(self, service, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 10_000_000)
        service.handle_slave_fill(ORG_ID, SlaveFill(
            account_id=100, client_order_id=None, position_id=55, filled_volume=4_000_000,
            fill_price=1.11, closed_volume=4_000_000, label=""))
        (entry,) = repo.position_entries(11)
        assert entry.slave_volume == 6_000_000
        (event,) = _events(repo, "position_closed")
        assert event[2] == {"action": "position_closed", "slave_position_id": 55,
                            "closed_volume": 4_000_000}

    def test_a_closing_fill_that_names_its_mapping_reduces_only_that_one(self, service, repo):
        """A netting MT5 follower's copies share one net position; the
        outbox's ':close' ack comes back under the mapping's own coid."""
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm11.100", 55, 6_000_000)
        repo.create_position_mapping(12, 100, "cm12.100", org_id=ORG_ID)
        repo.activate_position_mapping(100, "cm12.100", 55, 4_000_000)
        service.handle_slave_fill(ORG_ID, SlaveFill(
            account_id=100, client_order_id="cm12.100", position_id=55, filled_volume=4_000_000,
            fill_price=1.11, closed_volume=4_000_000, label=""))
        assert repo.position_entries(12) == []
        (entry,) = repo.position_entries(11)
        assert entry.slave_volume == 6_000_000
        (event,) = _events(repo, "position_closed")
        assert event[2] == {"action": "position_closed", "slave_position_id": 55,
                            "closed_volume": 4_000_000, "client_order_id": "cm12.100"}

    def test_a_pending_copy_fills_by_its_order_ticket(self, service, repo):
        repo.create_order_mapping(42, 100, "co42.100", org_id=ORG_ID)
        repo.activate_order_mapping(100, "co42.100", 9999)
        service.handle_slave_fill(ORG_ID, _fill(coid=None, position_id=77, order_id=9999))
        (mapping,) = repo.mapping_rows(org_id=ORG_ID)
        assert (mapping["slave_position_id"], mapping["slave_volume"],
                mapping["fill_price"]) == (77, 10_000_000, 1.1)
        assert _events(repo, "pending_fill")

    def test_a_manual_fill_is_expected_and_an_unmatched_one_is_a_warning(self, service, repo):
        service.handle_slave_fill(ORG_ID, _fill(coid=None, label=MANUAL_ORDER_LABEL))
        assert _events(repo, "manual_fill") and not _events(repo, "unmatched_slave_fill")
        service.handle_slave_fill(ORG_ID, _fill(coid=None, position_id=56))
        (event,) = _events(repo, "unmatched_slave_fill")
        assert event[1] == "warning" and event[2]["slave_position_id"] == 56

    def test_an_unknown_client_order_id_is_a_warning(self, service, repo):
        service.handle_slave_fill(ORG_ID, _fill(coid="cm999.100"))
        (event,) = _events(repo, "unknown_fill")
        assert event[1] == "warning" and event[2]["client_order_id"] == "cm999.100"


class TestRejectionAcceptedCancelled:
    def test_rejection_fails_the_mapping_and_logs(self, service, repo):
        repo.create_position_mapping(11, 100, "cm11.100", org_id=ORG_ID)
        service.handle_slave_rejection(
            ORG_ID, 100, "cm11.100", "terminal rejected open: not enough money")
        (mapping,) = repo.mapping_rows(org_id=ORG_ID)
        assert (mapping["status"], mapping["error"]) == (
            "failed", "terminal rejected open: not enough money")
        (event,) = _events(repo, "order_rejected")
        assert event[1] == "error"
        assert event[2]["error"] == "terminal rejected open: not enough money"
        # No mapping to fail: still logged.
        service.handle_slave_rejection(ORG_ID, 100, None, "terminal rejected close: position closed")
        assert len(_events(repo, "order_rejected")) == 2

    def test_accepted_and_cancelled_pending_copies(self, service, repo):
        repo.create_order_mapping(42, 100, "co42.100", org_id=ORG_ID)
        service.handle_slave_order_accepted(ORG_ID, 100, "co42.100", 9999)
        assert [e.slave_order_id for e in repo.order_entries(42)] == [9999]
        service.handle_slave_order_accepted(ORG_ID, 100, None, 1)   # an operator's order: nothing to link
        service.handle_slave_order_cancelled(ORG_ID, 100, 9999)
        assert repo.order_entries(42) == []
        assert _events(repo, "order_accepted") and _events(repo, "order_cancelled")
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_service_mt5.py`.
Expected: FAIL at collection with `ImportError: cannot import name 'SlaveFill' from 'copier.engine.service'`.

- [ ] **Step 3: Refactor the service**

In `copier/src/copier/engine/service.py`:

(a) Replace the imports at lines 15-32 with:

```python
import logging
import math
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType, ProtoOAOrderType)

from copier.ctrader.symbols import by_id as symbols_by_id
from copier.db.repo import Repo, MappingNotFound
from copier.domain.models import (
    MANUAL_ORDER_LABEL, SymbolInfo, MasterEvent, MasterPendingFilled, AmendPositionSLTP,
    MasterPositionOpened, MasterPositionClosed, MasterPositionSLTPAmended)
from copier.domain.decision import decide
from copier.engine.capture import execution_row
from copier.engine.normalize import normalize
from copier.engine.dispatch import Dispatcher
from copier.engine.routing import OrgRouting
```

(b) Right after `master_position_of` (after line 52) add:

```python


@dataclass(frozen=True)
class SlaveFill:
    """A slave's fill in platform-neutral terms.

    Built from a ProtoOAExecutionEvent for cTrader slaves and from an MT5
    terminal's ack or deal for MT5 slaves, then handled identically by
    handle_slave_fill. `closed_volume` set means this fill CLOSED (part of)
    a position. `order_id` is the slave's own order ticket, which is how a
    filled pending copy finds its order mapping. `stop_loss`/`take_profit`
    are the protection the copy already carries when the platform reports
    it (MT5 does; cTrader fills leave them None, so the master's level is
    re-stated as before).
    """
    account_id: int
    client_order_id: str | None
    position_id: int
    filled_volume: int
    fill_price: float | None
    closed_volume: int | None
    label: str
    order_id: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


def _same_level(a: float | None, b: float | None) -> bool:
    """Two protection levels are the same when both are unset or differ by
    less than any broker's price precision."""
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-7)
```

(c) Replace `_protect_new_copy` (lines 156-186) with:

```python
    def _protect_new_copy(self, account_id: int, client_order_id: str,
                          slave_position_id: int, org_id: int,
                          current: tuple[float | None, float | None] = (None, None)) -> None:
        """Give a just-filled copy the protection its master already has.

        This is the first instant the copy can be amended -- before the
        fill it has no position id, which is exactly why the master's own
        SL/TP event could not reach it. Sent even when the copy may already
        carry the level: re-stating the same stop is a no-op at the broker,
        and the alternative is guessing about a naked position.

        `current` is what the copy is KNOWN to carry (an MT5 open travels
        with its SL/TP, and the terminal reports them back): when it
        already matches the master's level, nothing is sent. cTrader fills
        pass (None, None) -- unknown -- so the level is re-stated as before.

        Never raises. A copy that opened is real money at the broker; a
        failure to protect it must be logged and must not take down the
        fill bookkeeping that follows.
        """
        master_position_id = master_position_of(client_order_id)
        if master_position_id is None:
            return
        levels = self._master_protection.get(master_position_id)
        if levels is None:
            return
        stop_loss, take_profit = levels
        if _same_level(stop_loss, current[0]) and _same_level(take_profit, current[1]):
            return
        try:
            self._dispatcher.dispatch(
                [AmendPositionSLTP(account_id, slave_position_id,
                                   stop_loss, take_profit)],
                org_id=org_id,
            )
        except Exception:
            log.exception(
                "could not protect copy %s on account %s (master %s)",
                slave_position_id, account_id, master_position_id)
```

(d) Replace `_handle_master_event` (lines 315-417) with these four methods:

```python
    def _handle_master_event(
        self,
        org_id: int,
        master_account_id: int,
        evt: ProtoOAExecutionEvent,
        start_time: int,
        routing: OrgRouting,
    ) -> None:
        """Handle a cTrader master account event: normalize -> act.

        Everything here is scoped to `org_id`: the symbol map is that org's
        master's, the slave fleet is that org's, and the dispatch carries
        that org's id -- so an event on one org's master can never reach
        another org's accounts.

        Args:
            org_id: Org that owns this master.
            master_account_id: The master account the event came from.
            evt: ProtoOAExecutionEvent.
            start_time: Event processing start time in milliseconds.
            routing: The routing snapshot this event is being processed against.
        """
        normalized = normalize(evt, self._master_symbols_by_org.get(org_id, {}))
        payload = {
            'execution_type': ProtoOAExecutionType.Name(evt.executionType),
            'normalized': type(normalized).__name__ if normalized else None,
        }
        if normalized is None:
            # An event we chose not to act on must explain itself in the
            # log: which order type and symbol the miss was about. A live
            # MARKET_RANGE fill sat invisible behind a bare
            # {"normalized": null} for exactly this lack of detail.
            payload['order_type'] = ProtoOAOrderType.Name(evt.order.orderType)
            payload['symbol_id'] = evt.order.tradeData.symbolId
            self._audit_master_event(org_id, master_account_id, payload, start_time)
            self._submit_execution(org_id, master_account_id, evt,
                                   is_master=True, routing=routing)
            return

        self._decide_dispatch_audit(org_id, master_account_id, normalized, routing,
                                    payload, start_time)

        # The master trade's OWN economics -- price, volume, side, symbol,
        # broker clock and the quote that was live at the time. The
        # master_event log line above deliberately stays thin; this is the
        # record. Queued, not written, so it costs the reactor nothing.
        self._submit_execution(org_id, master_account_id, evt,
                               is_master=True, routing=routing)

        # Stamp the master half of the slippage measurement onto the copies.
        # STRICTLY after the dispatch above: it is one indexed UPDATE, and
        # nothing may sit between a master fill arriving and the slave
        # orders reaching the wire.
        if evt.deal.HasField('executionPrice') and evt.deal.positionId:
            self._repo.record_master_fill(
                org_id,
                evt.deal.positionId,
                evt.deal.executionPrice,
                evt.deal.executionTimestamp or None,
            )

        self._after_master_event(org_id, normalized)

    def act_on_master_event(self, org_id: int, master_account_id: int,
                            normalized: MasterEvent, *, source: str) -> None:
        """A platform-neutral master event: decide -> dispatch -> audit ->
        bookkeeping. The MT5 lane calls this with what ingress.py derived
        from a terminal's report; the cTrader path goes through
        _handle_master_event, which normalizes first and captures the
        execution row. `source` names where the event came from in the
        audit payload."""
        routing = self._routing_provider()
        start_time = time.time_ns() // 1_000_000
        payload = {'source': source, 'normalized': type(normalized).__name__}
        self._decide_dispatch_audit(org_id, master_account_id, normalized, routing,
                                    payload, start_time)
        self._after_master_event(org_id, normalized)

    def _decide_dispatch_audit(self, org_id: int, master_account_id: int,
                               normalized: MasterEvent, routing: OrgRouting,
                               payload: dict, start_time: int) -> None:
        # Before decide(), so a copy that fills during this very event's
        # dispatch already finds the level recorded.
        self._remember_master_protection(normalized)

        # Decide and dispatch FIRST; the audit row is written in the
        # finally, so it can never sit as a blocking database write in
        # front of the copy handoff, and it is still written even when
        # decide/dispatch raise (the outer handler then logs the failure
        # as well). latency_ms therefore measures the whole internal
        # path: normalize -> decide -> dispatch handoff.
        try:
            # Decide: get intents for THIS ORG's enabled slaves only
            slaves = routing.slaves_by_org.get(org_id, [])
            intents = decide(normalized, self._repo, slaves)

            # Dispatch intents against this org's gates
            if intents:
                self._dispatcher.dispatch(intents, org_id=org_id)
        finally:
            self._audit_master_event(org_id, master_account_id, payload, start_time)

    def _audit_master_event(self, org_id: int, master_account_id: int, payload: dict,
                            start_time: int) -> None:
        """Best-effort: the orders are already at the broker by now, so a
        failed audit write must not replace the real exception, and must
        not skip the post-dispatch bookkeeping. A lost audit row is a
        diagnostics gap; a skipped pending-fill check is a real one."""
        latency_ms = (time.time_ns() // 1_000_000) - start_time
        try:
            self._repo.log_event(
                'master_event',
                'info',
                payload,
                account_id=master_account_id,
                latency_ms=latency_ms,
                org_id=org_id,
            )
        except Exception:
            log.exception(
                "master_event audit write failed (copy already dispatched)")

    def _after_master_event(self, org_id: int, normalized: MasterEvent) -> None:
        # Schedule pending fill alert if this is a pending fill
        if isinstance(normalized, MasterPendingFilled):
            self._schedule_pending_fill_check(org_id, normalized)

        # The master's positions/orders just changed; let /state catch up now.
        self._notify_positions_changed(org_id)
```

(e) Replace `_handle_slave_fill` (lines 562-703) with:

```python
    def _handle_slave_fill(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """A cTrader slave's ORDER_FILLED / ORDER_PARTIAL_FILL, as a SlaveFill."""
        deal = evt.deal
        self.handle_slave_fill(org_id, SlaveFill(
            account_id=account_id,
            client_order_id=self._extract_client_order_id(evt),
            position_id=deal.positionId,
            filled_volume=deal.filledVolume,
            # T9c: the execution price is what the Positions screen's
            # per-copy "Fill Price" column is built from (see
            # repo.mappings.fill_price / db/migrations/003_mapping_fill_price.sql).
            fill_price=deal.executionPrice if deal.HasField('executionPrice') else None,
            closed_volume=(deal.closePositionDetail.closedVolume
                           if deal.HasField('closePositionDetail') else None),
            label=evt.order.tradeData.label,
            order_id=evt.order.orderId,
        ))

    def handle_slave_fill(self, org_id: int, fill: SlaveFill) -> None:
        """A slave's fill, from whichever platform reported it.

        - closed_volume set -> reduce_position_mapping; pinned to the one mapping
          when the fill names a "cm" client_order_id (a netting MT5 follower's
          copies share one net ticket, and the outbox's ':close' ack names the
          copy it closed), else every active mapping on that position as before
        - client_order_id "cm..." -> activate_position_mapping (+ the master's protection)
        - else a pending copy filling: activate_pending_fill by the slave's order ticket
        - else an operator's manual order (expected) or an unmatched fill (warning)
        """
        account_id = fill.account_id

        if fill.closed_volume is not None:
            coid = fill.client_order_id
            coid = coid if coid and coid.startswith("cm") else None
            self._repo.reduce_position_mapping(
                account_id, fill.position_id, fill.closed_volume, client_order_id=coid)
            payload = {
                'action': 'position_closed',
                'slave_position_id': fill.position_id,
                'closed_volume': fill.closed_volume,
            }
            if coid:
                payload['client_order_id'] = coid
            self._repo.log_event('slave_action', 'info', payload,
                                 account_id=account_id, org_id=org_id)
            return

        client_order_id = fill.client_order_id
        if client_order_id and client_order_id.startswith("cm"):
            try:
                self._repo.activate_position_mapping(
                    account_id, client_order_id, fill.position_id, fill.filled_volume,
                    fill_price=fill.fill_price,
                )
                self._repo.log_event(
                    'slave_action',
                    'info',
                    {
                        'action': 'position_filled',
                        'client_order_id': client_order_id,
                        'slave_position_id': fill.position_id,
                        'filled_volume': fill.filled_volume,
                        'fill_price': fill.fill_price,
                    },
                    account_id=account_id,
                    org_id=org_id,
                )
                # The copy exists now, so the master's protection can
                # finally reach it -- it could not when the master's own
                # SL/TP event fired, a quarter of a second ago.
                self._protect_new_copy(
                    account_id, client_order_id, fill.position_id, org_id,
                    current=(fill.stop_loss, fill.take_profit))
            except MappingNotFound:
                # Unknown clientOrderId - log as drift warning
                self._repo.log_event(
                    'slave_action',
                    'warning',
                    {
                        'action': 'unknown_fill',
                        'client_order_id': client_order_id,
                        'reason': 'No matching position mapping',
                    },
                    account_id=account_id,
                    org_id=org_id,
                )
            return

        # Check for pending order fill: match by the slave's own order ticket
        try:
            self._repo.activate_pending_fill(
                account_id, fill.order_id, fill.position_id, fill.filled_volume,
                fill_price=fill.fill_price,
            )
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'pending_fill',
                    'client_order_id': client_order_id,
                    'slave_order_id': fill.order_id,
                    'slave_position_id': fill.position_id,
                    'filled_volume': fill.filled_volume,
                    'fill_price': fill.fill_price,
                },
                account_id=account_id,
                org_id=org_id,
            )
            return
        except MappingNotFound:
            # order ticket didn't match; no order mapping found
            pass

        # An operator-placed manual order's fill matches no mapping BY
        # DESIGN -- it is expected, so it must not raise the unexplained-
        # fill warning below.
        if fill.label == MANUAL_ORDER_LABEL:
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'manual_fill',
                    'slave_order_id': fill.order_id,
                    'slave_position_id': fill.position_id,
                    'filled_volume': fill.filled_volume,
                    'fill_price': fill.fill_price,
                },
                account_id=account_id,
                org_id=org_id,
            )
            return

        # Fallthrough: fill matched nothing (neither position "cm" nor pending by order_id)
        self._repo.log_event(
            'slave_action',
            'warning',
            {
                'action': 'unmatched_slave_fill',
                'slave_order_id': fill.order_id,
                'slave_position_id': fill.position_id,
                'client_order_id': client_order_id,
                'reason': 'No matching position or order mapping',
            },
            account_id=account_id,
            org_id=org_id,
        )
```

(f) Replace `_handle_slave_order_accepted` (lines 705-746) with:

```python
    def _handle_slave_order_accepted(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """A cTrader slave's ORDER_ACCEPTED."""
        self.handle_slave_order_accepted(
            org_id, account_id, self._extract_client_order_id(evt), evt.order.orderId)

    def handle_slave_order_accepted(
        self, org_id: int, account_id: int, client_order_id: str | None, slave_order_id: int
    ) -> None:
        """A pending copy the slave's platform accepted: clientOrderId 'co...'
        -> activate_order_mapping. Anything else (an operator's own order)
        has nothing to link."""
        if not client_order_id or not client_order_id.startswith("co"):
            return

        try:
            self._repo.activate_order_mapping(account_id, client_order_id, slave_order_id)
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'order_accepted',
                    'client_order_id': client_order_id,
                    'slave_order_id': slave_order_id,
                },
                account_id=account_id,
                org_id=org_id,
            )
        except MappingNotFound:
            self._repo.log_event(
                'slave_action',
                'warning',
                {
                    'action': 'order_accepted_no_mapping',
                    'client_order_id': client_order_id,
                },
                account_id=account_id,
                org_id=org_id,
            )
```

(g) Replace `_handle_slave_order_cancelled` (lines 748-786) with:

```python
    def _handle_slave_order_cancelled(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """A cTrader slave's ORDER_CANCELLED."""
        self.handle_slave_order_cancelled(org_id, account_id, evt.order.orderId)

    def handle_slave_order_cancelled(
        self, org_id: int, account_id: int, slave_order_id: int
    ) -> None:
        """A mapped pending copy is gone: close_order_mapping."""
        try:
            self._repo.close_order_mapping(account_id, slave_order_id)
            self._repo.log_event(
                'slave_action',
                'info',
                {
                    'action': 'order_cancelled',
                    'slave_order_id': slave_order_id,
                },
                account_id=account_id,
                org_id=org_id,
            )
        except MappingNotFound:
            # Order mapping doesn't exist or already closed - log as drift warning
            self._repo.log_event(
                'slave_action',
                'warning',
                {
                    'action': 'order_cancel_no_mapping',
                    'slave_order_id': slave_order_id,
                    'reason': 'Order mapping not found or already closed',
                },
                account_id=account_id,
                org_id=org_id,
            )
```

(h) Replace `_handle_slave_order_rejected` (lines 788-827) with:

```python
    def _handle_slave_order_rejected(
        self, org_id: int, account_id: int, evt: ProtoOAExecutionEvent
    ) -> None:
        """A cTrader slave's ORDER_REJECTED."""
        error_code = evt.errorCode if evt.errorCode else "UNKNOWN"
        self.handle_slave_rejection(
            org_id, account_id, self._extract_client_order_id(evt),
            f"Order rejected: {error_code}", error_code=error_code)

    def handle_slave_rejection(
        self, org_id: int, account_id: int, client_order_id: str | None, reason: str,
        error_code: str | None = None,
    ) -> None:
        """The slave's platform refused a copy: fail its mapping (if any) and
        log an error event. Spec: broker rejections (min-volume, margin) are
        NOT account status degradations here -- the MT5 lane sets its own
        degraded status with the terminal's reason (main.py)."""
        if client_order_id:
            try:
                self._repo.fail_mapping(account_id, client_order_id, reason)
            except MappingNotFound:
                pass  # Mapping doesn't exist - no-op

        # Log error event (alert only, no account status change)
        self._repo.log_event(
            'slave_action',
            'error',
            {
                'action': 'order_rejected',
                'client_order_id': client_order_id,
                'error_code': error_code,
                'error': reason,
            },
            account_id=account_id,
            org_id=org_id,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_service_mt5.py`.
Expected: `14 passed`.

Run DB `tests/unit/test_service.py`.
Expected: all pass — the cTrader path writes the same payloads in the same order (audit after dispatch, execution row after the audit, `record_master_fill` after both, pending-fill check and notify last).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/engine/service.py copier/tests/unit/test_service_mt5.py && git commit -m "refactor(service): platform-neutral act_on_master_event and SlaveFill

The cTrader handlers now normalize/extract and hand a MasterEvent or a
SlaveFill to the same code the MT5 lane calls directly. A copy that
already carries the master's protection (an MT5 open travels with its
SL/TP) is no longer amended again; one whose level moved before its ack
still is. A closing fill that names its mapping reduces that mapping
alone (a netting MT5 follower's copies share one net ticket).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Routing — canonical keys for MT5 slaves, `platform_by_account`

**Files:**
- Modify: `copier/src/copier/engine/routing.py:5-43`
- Modify: `copier/tests/unit/test_routing.py:1-11` (imports + `_row` helper), append tests

**Interfaces:**
- Consumes: `AccountRow.platform` (Task 4), `Repo.load_symbol_aliases` (Task 4, passed in as a callable).
- Produces (contract §2, used by Tasks 12 and 15): `OrgRouting.platform_by_account: Mapping[int, str]` (defaulted to `{}` so `make_routing` keeps working); `build_routing(accounts, symbol_loader, alias_loader=None)`; `mt5_symbols_by_canonical(symbols, aliases) -> dict[str, SymbolInfo]` (an MT5 account's symbol cache re-keyed: every aliased canonical name maps to the broker symbol's `SymbolInfo`, and every broker name keeps mapping to itself).

- [ ] **Step 1: Write the failing test**

In `copier/tests/unit/test_routing.py`, replace lines 1-11 with:

```python
from decimal import Decimal

import pytest

from copier.db.repo import AccountRow
from copier.domain.models import SymbolInfo
from copier.engine.routing import build_routing, mt5_symbols_by_canonical


def _row(account_id, org_id, role, enabled=True, status="ok", platform="ctrader"):
    return AccountRow(
        account_id=account_id, org_id=org_id, connection_id=1, trader_login=account_id,
        is_live=False, role=role, enabled=enabled, multiplier=Decimal("1.0"),
        status=status, last_error=None, platform=platform)
```

and append at the end of the file:

```python


XAU_R = SymbolInfo(symbol_id=7, name="XAUUSD.r", digits=2, lot_size=100, min_volume=1, step_volume=1)
GBPJPY_R = SymbolInfo(symbol_id=8, name="GBPJPY.r", digits=3, lot_size=100, min_volume=1, step_volume=1)


def test_mt5_symbols_by_canonical_adds_alias_keys_and_keeps_broker_names():
    keyed = mt5_symbols_by_canonical(
        {"XAUUSD.r": XAU_R, "GBPJPY.r": GBPJPY_R},
        {"XAUUSD": "XAUUSD.r", "GOLD": "XAUUSD.r", "NAS100": "USTEC"})   # USTEC: not in the cache
    assert keyed == {"XAUUSD.r": XAU_R, "GBPJPY.r": GBPJPY_R, "XAUUSD": XAU_R, "GOLD": XAU_R}


def test_an_mt5_slave_is_keyed_by_the_canonical_names_master_events_carry():
    accounts = [_row(100, 1, "master"), _row(5, 1, "slave", platform="mt5")]
    asked = []

    def alias_loader(account_id):
        asked.append(account_id)
        return {"XAUUSD": "XAUUSD.r"}

    routing = build_routing(
        accounts,
        symbol_loader=lambda a: {"XAUUSD.r": XAU_R, "GBPJPY.r": GBPJPY_R} if a == 5 else {},
        alias_loader=alias_loader)
    (slave,) = routing.slaves_by_org[1]
    assert slave.symbols["XAUUSD"] is XAU_R           # what the master's events say
    assert slave.symbols["XAUUSD.r"] is XAU_R         # SymbolInfo.name stays the broker name
    assert slave.symbols["GBPJPY.r"] is GBPJPY_R      # no alias: keyed by its own name
    assert asked == [5]                               # cTrader accounts never consult aliases
    assert routing.platform_by_account == {100: "ctrader", 5: "mt5"}


def test_alias_loader_is_optional_and_platforms_are_still_reported():
    routing = build_routing([_row(100, 1, "master"), _row(101, 1, "slave")],
                            symbol_loader=lambda a: {})
    assert routing.platform_by_account == {100: "ctrader", 101: "ctrader"}
    assert [s.account_id for s in routing.slaves_by_org[1]] == [101]
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_routing.py`.
Expected: FAIL at collection with `ImportError: cannot import name 'mt5_symbols_by_canonical' from 'copier.engine.routing'`.

- [ ] **Step 3: Implement**

In `copier/src/copier/engine/routing.py`, replace lines 5-43 with:

```python
from dataclasses import dataclass, field
from typing import Callable, Mapping

from copier.db.repo import AccountRow
from copier.domain.models import SlaveConfig, SymbolInfo


@dataclass(frozen=True)
class OrgRouting:
    org_by_account: Mapping[int, int]
    master_by_org: Mapping[int, int]
    slaves_by_org: Mapping[int, list[SlaveConfig]]
    # account -> 'ctrader' | 'mt5'. Defaulted so the unit-test fixture that
    # builds routings by hand stays valid.
    platform_by_account: Mapping[int, str] = field(default_factory=dict)


def mt5_symbols_by_canonical(
    symbols: Mapping[str, SymbolInfo], aliases: Mapping[str, str],
) -> dict[str, SymbolInfo]:
    """An MT5 account's symbol cache re-keyed for the engine.

    The cache is keyed by BROKER name ("XAUUSD.r"); master events carry
    CANONICAL names ("XAUUSD"). Every alias adds a canonical key pointing
    at the broker symbol's SymbolInfo -- whose .name stays the broker name,
    which is what the outbox sends -- and every broker name keeps its own
    key, so an unaliased symbol still resolves for a master that happens
    to use the same name.
    """
    out = dict(symbols)
    for canonical, broker_name in aliases.items():
        info = symbols.get(broker_name)
        if info is not None:
            out[canonical] = info
    return out


def build_routing(
    accounts: list[AccountRow],
    symbol_loader: Callable[[int], Mapping[str, SymbolInfo]],
    alias_loader: Callable[[int], Mapping[str, str]] | None = None,
) -> OrgRouting:
    org_by_account: dict[int, int] = {}
    master_by_org: dict[int, int] = {}
    slaves_by_org: dict[int, list[SlaveConfig]] = {}
    platform_by_account: dict[int, str] = {}
    for a in accounts:
        org_by_account[a.account_id] = a.org_id
        platform_by_account[a.account_id] = a.platform
        if a.role == "master":
            master_by_org[a.org_id] = a.account_id
        elif a.role == "slave":
            symbols = symbol_loader(a.account_id)
            if a.platform == "mt5" and alias_loader is not None:
                symbols = mt5_symbols_by_canonical(symbols, alias_loader(a.account_id))
            slaves_by_org.setdefault(a.org_id, []).append(
                SlaveConfig(
                    account_id=a.account_id,
                    enabled=a.enabled and a.status != "paused",
                    multiplier=a.multiplier,
                    symbols=symbols,
                )
            )
    return OrgRouting(
        org_by_account=org_by_account,
        master_by_org=master_by_org,
        slaves_by_org=slaves_by_org,
        platform_by_account=platform_by_account,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_routing.py`.
Expected: all pass (`3` new + the existing ones).

Run DB `tests/unit/test_service.py` and DB `tests/unit/test_main.py`.
Expected: all pass (`make_routing` builds `OrgRouting` without `platform_by_account`, which now defaults).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/engine/routing.py copier/tests/unit/test_routing.py && git commit -m "feat(routing): key an MT5 slave's symbols by canonical name; carry each account's platform

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Reconciler — a `snapshot_provider` for accounts without a client

**Files:**
- Modify: `copier/src/copier/engine/reconcile.py:389-436` (`Reconciler.__init__`), `:438-443` (`_fetch_snapshot` head)
- Modify: `copier/tests/unit/test_reconcile.py:10-11` (imports), append a test class

**Interfaces:**
- Consumes: nothing new.
- Produces (contract §2 "Reconciler._fetch_snapshot", used by Task 15): `Reconciler(clients_by_account, repo, dispatcher, master_account_id, org_id, snapshot_provider=None)` where `snapshot_provider: Callable[[int], tuple[list[PositionSnapshot], list[OrderSnapshot]] | None] | None`; when it answers anything but `None` for an account, that is the account's book and no client is asked.

- [ ] **Step 1: Write the failing test**

In `copier/tests/unit/test_reconcile.py`, replace lines 10-11 (the two `ctrader_open_api` imports) with:

```python
from ctrader_open_api import Client, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileRes
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
```

and append at the end of the file:

```python


class _EmptyBookClient:
    """A cTrader client whose reconcile always answers an empty book, and
    remembers who it was asked about."""

    def __init__(self):
        self.asked = []

    def send(self, req):
        self.asked.append(req.ctidTraderAccountId)
        res = ProtoOAReconcileRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        return defer.succeed(ProtoMessage(payloadType=res.payloadType,
                                          payload=res.SerializeToString()))


class TestSnapshotProvider:
    """An MT5 account has no cTrader client to reconcile against: its book
    comes from the registry, through the snapshot_provider injected next
    to clients_by_account. None from the provider means 'not mine' and the
    account's client is asked as before."""

    MASTER, SLAVE, ORG = 1001, 2001, 1

    @pytest_twisted.inlineCallbacks
    def test_the_provider_answers_for_its_accounts_and_the_client_for_the_rest(self, repo):
        client = _EmptyBookClient()
        mt5_book = ([PositionSnapshot(position_id=7001, symbol_id=1, side=Side.BUY,
                                      volume=100, price=1.1, label="copy:m1")], [])
        reconciler = Reconciler(
            clients_by_account=lambda _account_id: client, repo=repo, dispatcher=Mock(),
            master_account_id=self.MASTER, org_id=self.ORG,
            snapshot_provider=lambda account_id: mt5_book if account_id == self.SLAVE else None)

        items = yield reconciler.run()

        assert client.asked == [self.MASTER]                    # the slave's book came from the provider
        assert reconciler.slave_positions[self.SLAVE] == mt5_book[0]
        assert [i.kind for i in items] == ["orphan_slave_position"]   # copy:* with no mapping row

    @pytest_twisted.inlineCallbacks
    def test_without_a_provider_every_account_uses_its_client(self, repo):
        client = _EmptyBookClient()
        reconciler = Reconciler(
            clients_by_account=lambda _account_id: client, repo=repo, dispatcher=Mock(),
            master_account_id=self.MASTER, org_id=self.ORG)

        yield reconciler.run()

        assert sorted(client.asked) == [self.MASTER, self.SLAVE]
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_reconcile.py`.
Expected: FAIL — `TestSnapshotProvider::test_the_provider_answers_for_its_accounts_and_the_client_for_the_rest` with `TypeError: Reconciler.__init__() got an unexpected keyword argument 'snapshot_provider'`; the second new test passes.

- [ ] **Step 3: Implement**

In `copier/src/copier/engine/reconcile.py`, change the `__init__` signature (lines 389-396) to:

```python
    def __init__(
        self,
        clients_by_account: Callable[[int], CTraderClient],
        repo: Repo,
        dispatcher: Dispatcher,
        master_account_id: int,
        org_id: int,
        snapshot_provider: Callable[[int], tuple[list, list] | None] | None = None,
    ):
```

add to its docstring's Args (after `org_id`):

```python
            snapshot_provider: Answers an account's (positions, orders) from
                somewhere other than a cTrader client -- the MT5 registry --
                or None for "not mine, ask the client". Optional: a process
                with no MT5 lane passes nothing and behaves exactly as before.
```

and after `self.org_id = org_id` (line 412) add:

```python
        self.snapshot_provider = snapshot_provider
```

Replace the head of `_fetch_snapshot` (lines 438-445) with:

```python
    def _fetch_snapshot(self, account_id: int):
        """The account's (positions, orders): from the snapshot_provider when
        it claims the account (an MT5 terminal's last report), else by
        sending ProtoOAReconcileReq to the account's client.

        Returns a Deferred[(list[PositionSnapshot], list[OrderSnapshot])].
        """
        if self.snapshot_provider is not None:
            provided = self.snapshot_provider(account_id)
            if provided is not None:
                positions, orders = provided
                return defer.succeed((list(positions), list(orders)))
        client = self.clients_by_account(account_id)
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = account_id
```

(the `_extract` closure and the `client.send(req)` tail stay exactly as they are).

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_reconcile.py`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/engine/reconcile.py copier/tests/unit/test_reconcile.py && git commit -m "feat(reconcile): let a snapshot provider answer for accounts with no cTrader client

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12b: Reconciler — a netting follower's net position against the sum of its copies

**Files:**
- Modify: `copier/src/copier/engine/reconcile.py` — `DriftItem` (gains `volume`), `compute_drift` (gains `netting_slave_ids` and `mapping_sides`, a sixth check, and check 2 skips the tickets the sixth compares), `Reconciler.__init__` (gains `netting_slaves`), `run` (reads the sides), `close_orphan` (closes `item.volume` when the item carries one)
- Modify: `copier/tests/unit/test_reconcile.py` (append a test class)

**Interfaces:**
- Consumes: `Repo.mapping_side` (Task 4), `snapshot_provider` (Task 12).
- Produces (spec "Netting accounts / As a follower", used by Task 15): `compute_drift(..., dry_run=False, netting_slave_ids=frozenset(), mapping_sides=None)` where `mapping_sides: dict[str, str] | None` maps a client_order_id to `"BUY"|"SELL"`; `DriftItem.volume: int | None = None` (the centilots a net-volume item is about; `None` on every other item, so nothing that builds or reads items today changes); `Reconciler(..., snapshot_provider=None, netting_slaves=None)` where `netting_slaves: Callable[[], set[int]] | None` answers the account ids of the NETTING MT5 FOLLOWERS (all orgs; `run` keeps its own org's enabled ones); `close_orphan` dispatches `ClosePosition` for `item.volume` when the item carries one, else for the position's live volume as today.
- The rule (spec): every copy on a symbol lives inside the symbol's single net position, so "does the position exist" (checks 1 and 2) says nothing about whether it still holds what the copies say. For a netting follower `compute_drift` compares, per net ticket that has active copies, the SIGNED sum of those copies (`+slave_volume` for a BUY copy, `-slave_volume` for a SELL one — opposite copies net against each other there, spec "Opposite positions") with the terminal's signed net volume (`0` when the position is gone): a shortfall is `missing_slave_copy`, an excess `orphan_slave_position`, one item per net ticket with the delta in `volume` and `position_id` = the net ticket. Check 2 skips the tickets so compared (it would otherwise call every copy of a correctly flat position "vanished"); a ticket with a copy whose side is unknown (`mapping_side` None: no open command) is not compared and keeps checks 1 and 2. cTrader and hedging accounts are untouched: volumes are not compared there, as today. A copy whose close is in flight (queued, not yet acked) shows as a transient excess for one run at most — exactly as today a cTrader copy whose master has just closed shows for one run in check 1.

- [ ] **Step 1: Write the failing test**

Append to `copier/tests/unit/test_reconcile.py` (after `TestSnapshotProvider`, Task 12):

```python


class TestNettingVolumeDrift:
    """A netting MT5 follower (account 2001 here): copies of master
    positions share net ticket 9001. The net position the terminal shows
    is compared, as a whole, with the signed sum of its copies."""

    MASTER = [PositionSnapshot(1, 1, Side.BUY, 100, 1.1, ""),
              PositionSnapshot(2, 1, Side.BUY, 50, 1.1, "")]
    SIDES = {"cm1.2001": "BUY", "cm2.2001": "BUY", "cm3.2001": "SELL"}

    @staticmethod
    def _copies(*rows):
        return [{'id': i, 'master_position_id': master, 'slave_account_id': 2001,
                 'slave_position_id': 9001, 'slave_volume': volume, 'status': 'active',
                 'client_order_id': f"cm{master}.2001"}
                for i, (master, volume) in enumerate(rows, start=1)]

    def _drift(self, net, mappings, master=None, netting=frozenset({2001}), sides=SIDES):
        return compute_drift(
            self.MASTER if master is None else master, [], {2001: net}, {2001: []},
            mappings, {2001}, netting_slave_ids=netting, mapping_sides=sides)

    def test_a_net_position_equal_to_the_sum_of_its_copies_is_not_drift(self):
        net = [PositionSnapshot(9001, 1, Side.BUY, 150, 1.1, "copy:m2")]
        assert self._drift(net, self._copies((1, 100), (2, 50))) == []

    def test_a_shortfall_is_a_missing_copy_for_the_delta(self):
        net = [PositionSnapshot(9001, 1, Side.BUY, 120, 1.1, "copy:m2")]
        (item,) = self._drift(net, self._copies((1, 100), (2, 50)))
        assert (item.kind, item.account_id, item.position_id, item.volume) == (
            'missing_slave_copy', 2001, 9001, 30)
        assert "BUY 120" in item.detail and "BUY 150" in item.detail and "short 30" in item.detail

    def test_an_excess_is_an_orphan_for_the_delta(self):
        net = [PositionSnapshot(9001, 1, Side.BUY, 200, 1.1, "copy:m2")]
        (item,) = self._drift(net, self._copies((1, 100), (2, 50)))
        assert (item.kind, item.position_id, item.volume) == ('orphan_slave_position', 9001, 50)

    def test_opposite_copies_net_against_each_other(self):
        master = [PositionSnapshot(1, 1, Side.BUY, 100, 1.1, ""),
                  PositionSnapshot(3, 1, Side.SELL, 100, 1.1, "")]
        # BUY 100 and SELL 100 copies: the terminal shows nothing, and that
        # is right -- no item, and no per-copy "vanished" from check 2.
        copies = self._copies((1, 100), (3, 100))
        assert self._drift([], copies, master=master) == []
        # A residue on either side is an orphan for what it holds.
        net = [PositionSnapshot(9001, 1, Side.SELL, 20, 1.1, "copy:m3")]
        (item,) = self._drift(net, copies, master=master)
        assert (item.kind, item.volume) == ('orphan_slave_position', 20)
        # BUY 100 against SELL 50: the terminal should show BUY 50 ...
        copies = self._copies((1, 100), (3, 50))
        net = [PositionSnapshot(9001, 1, Side.BUY, 50, 1.1, "copy:m3")]
        assert self._drift(net, copies, master=master) == []
        # ... and a terminal on the WRONG side is short by both volumes.
        net = [PositionSnapshot(9001, 1, Side.SELL, 50, 1.1, "copy:m3")]
        (item,) = self._drift(net, copies, master=master)
        assert (item.kind, item.volume) == ('missing_slave_copy', 100)

    def test_a_vanished_net_position_with_copies_that_do_not_cancel_is_one_shortfall(self):
        (item,) = self._drift([], self._copies((1, 100), (2, 50)))
        assert (item.kind, item.position_id, item.volume) == ('missing_slave_copy', 9001, 150)

    def test_hedging_and_ctrader_accounts_are_not_compared(self):
        net = [PositionSnapshot(9001, 1, Side.BUY, 120, 1.1, "copy:m2")]
        assert self._drift(net, self._copies((1, 100), (2, 50)), netting=frozenset()) == []
        assert self._drift(net, self._copies((1, 100), (2, 50)), sides=None) == []

    def test_a_copy_of_unknown_side_leaves_the_ticket_to_checks_one_and_two(self):
        net = [PositionSnapshot(9001, 1, Side.BUY, 120, 1.1, "copy:m2")]
        copies = self._copies((1, 100), (2, 50))
        assert self._drift(net, copies, sides={"cm1.2001": "BUY"}) == []
        items = self._drift([], copies, sides={"cm1.2001": "BUY"})
        assert [(i.kind, i.volume) for i in items] == [
            ('missing_slave_copy', None), ('missing_slave_copy', None)]

    def test_the_net_volume_item_is_stable_and_distinct_from_check_ones_orphan(self):
        net = [PositionSnapshot(9001, 1, Side.BUY, 200, 1.1, "copy:m2")]
        first = self._drift(net, self._copies((1, 100), (2, 50)))
        again = self._drift(net, self._copies((1, 100), (2, 50)))
        assert [i.id for i in first] == [i.id for i in again]
        # The same ticket with NO mapping at all is check 1's orphan: a
        # different item, closing the whole position.
        (orphan,) = self._drift(net, [], master=[])
        assert orphan.id != first[0].id and orphan.volume is None

    @pytest_twisted.inlineCallbacks
    def test_run_reads_the_sides_from_the_open_commands(self, repo, db):
        """End to end through run(): the netting follower is an MT5 account
        (no client; its book comes from the snapshot_provider), the copies'
        sides come from their open commands (Repo.mapping_side)."""
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                      trader_login, is_live, role, enabled, multiplier, platform)
                VALUES (3001, %s, NULL, 0, false, 'slave', true, 1.0, 'mt5')
                """, (ORG_ID,))
            conn.execute(
                """
                INSERT INTO mappings (master_position_id, slave_account_id, slave_position_id,
                                      slave_volume, client_order_id, org_id, status)
                VALUES (1, 3001, 9001, 100, 'cm1.3001', %(org)s, 'active'),
                       (3, 3001, 9001, 50, 'cm3.3001', %(org)s, 'active')
                """, {"org": ORG_ID})
        for master, side in ((1, "BUY"), (3, "SELL")):
            repo.enqueue_mt5_command(
                3001, ORG_ID, "open",
                {"symbol": "EURUSD.r", "side": side, "lots": 1.0, "sl": 0, "tp": 0,
                 "comment": f"copy:m{master}"}, f"cm{master}.3001")
        books = {
            MASTER_ID: ([PositionSnapshot(1, 1, Side.BUY, 100, 1.1, ""),
                         PositionSnapshot(3, 1, Side.SELL, 50, 1.1, "")], []),
            # BUY 100 and SELL 50 copies should net to BUY 50; the terminal shows BUY 80.
            3001: ([PositionSnapshot(9001, 1, Side.BUY, 80, 1.1, "copy:m3")], []),
        }
        reconciler = Reconciler(
            clients_by_account=lambda _account_id: _EmptyBookClient(), repo=repo,
            dispatcher=Mock(), master_account_id=MASTER_ID, org_id=ORG_ID,
            snapshot_provider=books.get, netting_slaves=lambda: {3001})

        items = yield reconciler.run()

        (item,) = [i for i in items if i.account_id == 3001]
        assert (item.kind, item.position_id, item.volume) == ('orphan_slave_position', 9001, 30)

    @pytest_twisted.inlineCallbacks
    def test_close_orphan_closes_only_the_excess_of_a_net_position(self, repo):
        """The copies live INSIDE the net position: the remedy closes the
        excess, never the whole ticket."""
        dispatcher = Mock()
        reconciler = Reconciler(
            clients_by_account=Mock(), repo=repo, dispatcher=dispatcher,
            master_account_id=MASTER_ID, org_id=ORG_ID)
        reconciler.current = [DriftItem(
            id="net-9001", kind='orphan_slave_position', account_id=3001, position_id=9001,
            order_id=None, detail="excess", volume=30)]

        yield reconciler.close_orphan("net-9001")

        dispatcher.dispatch.assert_called_once_with(
            [ClosePosition(slave_account_id=3001, position_id=9001, volume=30)], org_id=ORG_ID)
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_reconcile.py`.
Expected: FAIL — the `TestNettingVolumeDrift` pure tests with `TypeError: compute_drift() got an unexpected keyword argument 'netting_slave_ids'`, `test_run_reads_the_sides_from_the_open_commands` with `TypeError: Reconciler.__init__() got an unexpected keyword argument 'netting_slaves'`, `test_close_orphan_closes_only_the_excess_of_a_net_position` with `TypeError: DriftItem.__init__() got an unexpected keyword argument 'volume'`.

- [ ] **Step 3: Implement**

In `copier/src/copier/engine/reconcile.py`:

(a) `DriftItem` gains a defaulted last field:

```python
@dataclass(frozen=True)
class DriftItem:
    """Immutable drift detection result."""
    id: str  # Stable hash of (kind, account_id, position_id, order_id)
    # 'orphan_slave_position', 'missing_slave_copy', 'unmapped_master_position',
    # 'unfilled_slave_order', 'stale_pending_copy'
    kind: str
    account_id: int | None
    position_id: int | None
    order_id: int | None
    detail: str
    # The centilots the item is about, when it is about a volume rather than
    # a whole position: a netting follower's net position holding less or
    # more than the sum of its copies (check 6). close_orphan closes exactly
    # this much then. None everywhere else.
    volume: int | None = None


def _signed_volume(volume: int) -> str:
    """'BUY 150' / 'SELL 20' / 'flat' for a side-signed centilot volume."""
    if volume == 0:
        return "flat"
    return f"{'BUY' if volume > 0 else 'SELL'} {abs(volume)}"
```

(b) `compute_drift` gains two keyword parameters after `dry_run`:

```python
    dry_run: bool = False,
    netting_slave_ids: frozenset[int] | set[int] = frozenset(),
    mapping_sides: dict[str, str] | None = None,
) -> list[DriftItem]:
```

its docstring's category list gains `6. net volume drift (netting MT5 followers only): a net position holding less (missing_slave_copy) or more (orphan_slave_position) than the signed sum of its active copies` and its Args gain:

```python
        netting_slave_ids: Accounts that are NETTING MT5 followers -- every
            copy on a symbol lives inside the symbol's single net position
            there, so check 6 compares volumes for them. Empty (the
            default) for a fleet without netting followers: nothing changes.
        mapping_sides: client_order_id -> "BUY"|"SELL" for those accounts'
            active copies (Repo.mapping_side); a copy with no entry leaves
            its ticket to checks 1 and 2.
```

Right after the `slave_pos_by_account` index is built (before `# 1. Check for orphan slave positions`), add:

```python
    # 6 is prepared here and reported after 5: a netting MT5 follower's net
    # tickets, each compared AS A WHOLE with the signed sum of its copies.
    # Those tickets are skipped by check 2, which would otherwise report
    # every copy of a correctly flat net position (opposite copies that
    # cancel) as vanished.
    sides = mapping_sides or {}
    net_groups: dict[tuple[int, int], list[dict]] = {}
    for m in mappings:
        if (m.get('status') == 'active' and m.get('slave_position_id')
                and m.get('slave_account_id') in netting_slave_ids):
            net_groups.setdefault((m['slave_account_id'], m['slave_position_id']), []).append(m)
    compared = {key for key, rows in net_groups.items()
                if all(sides.get(m.get('client_order_id')) in ('BUY', 'SELL') for m in rows)}
```

In check 2, right after `if account_id not in enabled_slave_ids: continue`, add:

```python
        if (account_id, slave_pos_id) in compared:
            continue        # a netting follower's net ticket: check 6 compares it as a whole
```

After check 5 (before `# Sort for stable output`), add:

```python
    # 6. A netting MT5 follower's net position against its copies (spec
    #    "Netting accounts / As a follower"). Every copy on a symbol lives
    #    inside the symbol's single net position, so whether the position
    #    EXISTS (checks 1 and 2) says nothing about whether it still holds
    #    what the copies say. Signed by side: opposite copies net against
    #    each other there, and a flat terminal is right when they cancel.
    #    Measured along the copies' side (the terminal's when the copies
    #    cancel): less than the copies hold is a shortfall, more an excess.
    for account_id, ticket in sorted(compared):
        if account_id not in enabled_slave_ids:
            continue
        rows = net_groups[(account_id, ticket)]
        expected = sum(int(m.get('slave_volume') or 0)
                       * (1 if sides[m['client_order_id']] == 'BUY' else -1) for m in rows)
        pos = slave_pos_by_account.get(account_id, {}).get(ticket)
        actual = 0 if pos is None else int(pos.volume) * (1 if pos.side == Side.BUY else -1)
        if actual == expected:
            continue
        direction = 1 if (expected or actual) > 0 else -1
        held, want = actual * direction, abs(expected)
        short = want - held
        drift_items.append(DriftItem(
            id=_stable_id('net_volume', account_id, ticket),
            kind='missing_slave_copy' if short > 0 else 'orphan_slave_position',
            account_id=account_id,
            position_id=ticket,
            order_id=None,
            detail=(f"Netting position {ticket} holds {_signed_volume(actual)} but its "
                    f"{len(rows)} active cop{'y' if len(rows) == 1 else 'ies'} sum to "
                    f"{_signed_volume(expected)} ({'short' if short > 0 else 'excess'} "
                    f"{abs(short)} centilots)"),
            volume=abs(short),
        ))
```

(c) `Reconciler.__init__` gains, after `snapshot_provider` (Task 12):

```python
        netting_slaves: Callable[[], set[int]] | None = None,
```

with the docstring Arg:

```python
            netting_slaves: Answers the account ids of the netting MT5
                followers (compute_drift compares their net positions with
                the sum of their copies, check 6). Optional: a process with
                no MT5 lane passes nothing and compares no volumes.
```

and, after `self.snapshot_provider = snapshot_provider`:

```python
        self.netting_slaves = netting_slaves
```

(d) In `run`, replace the `items = compute_drift(...)` call with:

```python
        # A netting follower's copies are compared by side (check 6); the
        # side of a copy is on its open command (Repo.mapping_side).
        netting_ids = (set(self.netting_slaves()) & enabled_slave_ids
                       if self.netting_slaves is not None else set())
        mapping_sides = {
            m['client_order_id']: self.repo.mapping_side(m['client_order_id'])
            for m in mappings
            if m.get('status') == 'active' and m.get('client_order_id')
            and m.get('slave_account_id') in netting_ids}

        items = compute_drift(
            master_positions, master_orders, slave_positions, slave_orders,
            mappings, enabled_slave_ids, dry_run=dry_run,
            netting_slave_ids=netting_ids, mapping_sides=mapping_sides,
        )
```

(e) In `close_orphan`, replace `volume = self._lookup_slave_volume(item.account_id, item.position_id)` with:

```python
        # A net-volume item (a netting follower's net position holding MORE
        # than its copies) names the excess: the copies live inside that
        # position, so closing the whole ticket would close them too.
        volume = item.volume or self._lookup_slave_volume(item.account_id, item.position_id)
```

and its docstring's "dispatches a full-volume ClosePosition intent" becomes "dispatches a ClosePosition intent for the position's full volume, or for `item.volume` when the item names one (a netting follower's excess over its copies)".

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_reconcile.py`.
Expected: all pass — every existing test passes `netting_slave_ids` empty by default, so checks 1–5 run exactly as before.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/engine/reconcile.py copier/tests/unit/test_reconcile.py && git commit -m "feat(reconcile): compare a netting follower's net position with the sum of its copies

On a netting MT5 follower every copy on a symbol lives inside the
symbol's single net position, so existence checks cannot see a position
that drifted from its copies. Per net ticket the signed sum of the
active copies is compared with the terminal's net volume: a shortfall is
missing_slave_copy, an excess orphan_slave_position, each carrying the
delta, and close_orphan closes exactly that much. cTrader and hedging
accounts are not compared, as before.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: `copier.mt5.deals` — deal rows and the `balance_after` estimate

**Files:**
- Create: `copier/src/copier/mt5/deals.py`
- Test: `copier/tests/unit/test_mt5_deals.py`

**Interfaces:**
- Consumes: `queries._lots` (`engine/queries.py:53-56`), `ReportDeal`, `CENTILOTS` (Task 2), `SymbolInfo`.
- Produces (used by Tasks 14 and 15): `CLOSE_ENTRIES = ("OUT", "OUT_BY", "INOUT")`, `TRADE_TYPES = ("BUY", "SELL")`, `sort_deals(deals) -> list[ReportDeal]`, `balance_after_estimates(deals, current_balance) -> dict[int, float]` (ticket → estimate), `deal_rows(deals, current_balance, symbols, entry_price_for) -> list[dict]` — rows in `queries._map_deal`'s shape plus top-level `balance_after` (every row) and `gross_profit` (BALANCE/CREDIT rows only), which `Repo.upsert_mt5_deals` (Task 4) stores.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_mt5_deals.py`:

```python
"""copier/src/copier/mt5/deals.py: MT5 deals -> the deals table's row shape,
with the spec's backward balance_after estimate."""

from copier.domain.models import SymbolInfo
from copier.mt5.deals import balance_after_estimates, deal_rows
from copier.testing.mt5_fixtures import deal

EURUSD = SymbolInfo(symbol_id=77, name="EURUSD.r", digits=5, lot_size=100, min_volume=1, step_volume=1)
SYMBOLS = {"EURUSD.r": EURUSD}


def test_balance_after_walks_backwards_from_the_current_balance():
    deals = [
        deal(1, position=7001, entry="IN", volume=100, price=1.1, commission=-0.03, time_ms=1000),
        deal(2, position=7001, deal_type="SELL", entry="OUT", volume=100, price=1.105, profit=5.0,
             swap=-0.1, commission=-0.03, time_ms=2000),
        deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0, time_ms=3000),
    ]
    assert balance_after_estimates(deals, current_balance=10500.0) == {
        3: 10500.0, 2: 10000.0, 1: 9995.13}


def test_estimates_follow_time_order_regardless_of_input_order():
    deals = [deal(2, entry="OUT", deal_type="SELL", profit=5.0, time_ms=2000),
             deal(1, entry="IN", commission=-0.03, time_ms=1000)]
    assert balance_after_estimates(deals, 100.0) == {2: 100.0, 1: 95.0}


def test_deal_rows_have_map_deals_shape():
    deals = [
        deal(1, position=7001, order=10, entry="IN", volume=100, price=1.1, commission=-0.03,
             time_ms=1000),
        deal(2, position=7001, order=11, deal_type="SELL", entry="OUT", volume=40, price=1.105,
             profit=2.0, commission=-0.01, time_ms=2000),
    ]
    rows = deal_rows(deals, 10000.0, SYMBOLS,
                     entry_price_for=lambda position_id: 1.1 if position_id == 7001 else None)
    assert rows[0] == {
        "deal_id": 1, "order_id": 10, "position_id": 7001, "symbol_id": 77, "symbol": "EURUSD.r",
        "side": "BUY", "volume": 100, "filled_volume": 100, "volume_lots": "1.00",
        "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
        "create_timestamp": 1000, "execution_timestamp": 1000, "close": None,
        "balance_after": 9998.01, "gross_profit": None}
    assert rows[1]["close"] == {
        "entry_price": 1.1, "gross_profit": 2.0, "swap": 0.0, "commission": -0.01,
        "balance": 10000.0, "closed_volume": 40, "closed_volume_lots": "0.40"}
    assert rows[1]["side"] == "SELL" and rows[1]["balance_after"] == 10000.0


def test_balance_operations_carry_their_amount_and_no_symbol():
    (row,) = deal_rows(
        [deal(3, deal_type="BALANCE", entry="", volume=0, profit=500.0, symbol="", time_ms=3000)],
        10500.0, SYMBOLS, entry_price_for=lambda _p: None)
    assert (row["side"], row["symbol"], row["symbol_id"], row["execution_price"],
            row["commission"], row["close"]) == ("BALANCE", None, None, None, None, None)
    assert (row["gross_profit"], row["balance_after"], row["volume"], row["position_id"]) == (
        500.0, 10500.0, 0, None)


def test_an_unknown_symbol_has_no_id_but_keeps_its_name():
    (row,) = deal_rows([deal(1, position=5, symbol="GBPJPY.r", entry="IN")], 100.0, SYMBOLS,
                       entry_price_for=lambda _p: None)
    assert (row["symbol"], row["symbol_id"]) == ("GBPJPY.r", None)


def test_other_closing_entries_are_closes():
    rows = deal_rows(
        [deal(1, position=5, deal_type="SELL", entry="OUT_BY", volume=10, profit=1.0),
         deal(2, position=6, deal_type="BUY", entry="INOUT", volume=10, profit=-1.0, time_ms=2000)],
        100.0, SYMBOLS, entry_price_for=lambda _p: 1.0)
    assert all(r["close"] is not None for r in rows)
```

- [ ] **Step 2: Run the test to verify it fails**

Run PURE `tests/unit/test_mt5_deals.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5.deals'`.

- [ ] **Step 3: Write the module**

Create `copier/src/copier/mt5/deals.py`:

```python
"""MT5 deals -> the `deals` table's row shape (queries._map_deal), plus the
balance_after estimate the spec asks for.

MT5 stores no per-deal balance. Walking the batch backwards from the
terminal's CURRENT balance -- subtracting each deal's profit + swap +
commission in reverse time order -- gives each deal the balance that stood
right after it, as long as the batch holds every deal since the previous
one. The api marks the field estimated; the History page shows it unchanged.
"""

from typing import Callable, Iterable, Mapping

from copier.domain.models import SymbolInfo
from copier.engine.queries import _lots
from copier.mt5.protocol import CENTILOTS, ReportDeal

CLOSE_ENTRIES = ("OUT", "OUT_BY", "INOUT")
TRADE_TYPES = ("BUY", "SELL")


def sort_deals(deals: Iterable[ReportDeal]) -> list[ReportDeal]:
    return sorted(deals, key=lambda d: (d.time_ms, d.ticket))


def balance_after_estimates(deals: Iterable[ReportDeal], current_balance: float) -> dict[int, float]:
    """ticket -> the balance right after that deal, newest first from the
    current balance."""
    running = float(current_balance)
    out: dict[int, float] = {}
    for d in reversed(sort_deals(deals)):
        out[d.ticket] = round(running, 2)
        running -= d.profit + d.swap + d.commission
    return out


def deal_rows(
    deals: Iterable[ReportDeal],
    current_balance: float,
    symbols: Mapping[str, SymbolInfo],
    entry_price_for: Callable[[int], float | None],
) -> list[dict]:
    """Rows in queries._map_deal's shape. A closing deal gets the `close`
    sub-dict compute_analytics reads (entry price from `entry_price_for`,
    the position's open price as last reported); a BALANCE/CREDIT operation
    carries its amount as top-level `gross_profit` with no `close`, so
    analytics never counts a deposit as a trade."""
    estimates = balance_after_estimates(deals, current_balance)
    rows: list[dict] = []
    for d in sort_deals(deals):
        sym = symbols.get(d.symbol)
        is_trade = d.deal_type in TRADE_TYPES
        close = None
        if is_trade and d.entry in CLOSE_ENTRIES:
            close = {
                "entry_price": entry_price_for(d.position),
                "gross_profit": d.profit,
                "swap": d.swap,
                "commission": d.commission,
                "balance": estimates[d.ticket],
                "closed_volume": d.volume,
                "closed_volume_lots": _lots(d.volume, CENTILOTS),
            }
        rows.append({
            "deal_id": d.ticket,
            "order_id": d.order or None,
            "position_id": d.position or None,
            "symbol_id": sym.symbol_id if sym is not None else None,
            "symbol": d.symbol or None,
            "side": d.deal_type,
            "volume": d.volume,
            "filled_volume": d.volume,
            "volume_lots": _lots(d.volume, CENTILOTS),
            "execution_price": d.price if is_trade else None,
            "status": "FILLED",
            "commission": d.commission if is_trade else None,
            "create_timestamp": d.time_ms,
            "execution_timestamp": d.time_ms,
            "close": close,
            "balance_after": estimates[d.ticket],
            "gross_profit": None if is_trade else d.profit,
        })
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run PURE `tests/unit/test_mt5_deals.py`.
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/deals.py copier/tests/unit/test_mt5_deals.py && git commit -m "feat(mt5): deal rows in _map_deal's shape with the backward balance_after estimate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: `copier.mt5.lane` — operator actions and read models for one MT5 account

**Files:**
- Create: `copier/src/copier/mt5/lane.py`
- Test: `copier/tests/unit/test_mt5_lane.py`

**Interfaces:**
- Consumes: `MT5Registry.report/position/symbol_by_name/symbols_by_name/is_online` (Task 6), `MT5Outbox.enqueue` (Task 7), `Repo.load_deals/load_mt5_cash_flow/load_mt5_link/mt5_commands_open/log_event/org_for_account` (Task 4), `queries._lots`, `main.FLATTEN_ROUNDS`/`FLATTEN_SETTLE_S` (imported lazily inside `flatten`: `main` imports this module).
- Produces (contract §2 `lane.py`, used by Task 15): `validated_price(name, raw) -> float | None`; `MT5Lane(app)` reading `app.repo`, `app.mt5_registry`, `app.mt5_outbox`, `app.clock`, `app._org_for_account(account_id)`; methods `place_order(account_id, org_id, symbol, side, order_type, volume_lots, limit_price, stop_price, stop_loss, take_profit, actor) -> dict`, `close_position(account_id, position_id, volume_lots, actor) -> dict`, `amend_position_sltp(account_id, position_id, stop_loss, take_profit, actor) -> dict`, `cancel_order(account_id, order_id, actor) -> dict`, `flatten(account_id) -> Deferred[dict]` (same summary keys as `CopierApp._flatten_account`), `details(account_id) -> dict`, `deal_history(account_id, from_ms, to_ms) -> dict`, `order_history(...) -> dict`, `cash_flow(...) -> dict`, `position_deals(account_id, position_id, from_ms, to_ms) -> dict`. Every action's dict carries `"status": "submitted"` and a `"command_id"`; every deal dict carries `"balance_after_estimated": True`.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_mt5_lane.py`:

```python
"""MT5Lane (copier/src/copier/mt5/lane.py): operator actions become outbox
commands; the read models come from Postgres and the registry."""

import psycopg
import pytest
from twisted.internet.task import Clock

import copier.main as main_module
from copier.db.repo import Repo
from copier.mt5.lane import MT5Lane
from copier.mt5.outbox import MT5Outbox
from copier.mt5.protocol import HelloReport, HelloSymbol
from copier.mt5.registry import MT5Registry
from copier.testing.mt5_fixtures import order, position, report

MAP_DEAL_KEYS = {"deal_id", "order_id", "position_id", "symbol_id", "symbol", "side", "volume",
                 "filled_volume", "volume_lots", "execution_price", "status", "commission",
                 "create_timestamp", "execution_timestamp", "close"}
CLOSE_KEYS = {"entry_price", "gross_profit", "swap", "commission", "balance", "closed_volume",
              "closed_volume_lots"}


class _StubApp:
    """Just the attributes MT5Lane reads off CopierApp."""

    def __init__(self, repo, clock):
        self.repo = repo
        self.mt5_registry = MT5Registry(repo, clock=clock)
        self.mt5_outbox = MT5Outbox(repo, clock=clock)
        self.clock = clock

    def _org_for_account(self, account_id):
        return self.repo.org_for_account(account_id)


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    mt5_id = seed_mt5_account(org_id)
    repo = Repo(db)
    clock = Clock()
    app = _StubApp(repo, clock)
    app.mt5_registry.update_from_hello(mt5_id, org_id, HelloReport(
        "1.0.0", 4400, 12345678, "XYZ Ltd", "XYZ-Live3", "USD", True, "demo", 500,
        [HelloSymbol("EURUSD.r", 5, 100000.0, 0.01, 0.01, 100.0, 4),
         HelloSymbol("XAUUSD.r", 2, 100.0, 0.01, 0.01, 50.0, 4)], 1, 1), now=0.0)
    repo.upsert_mt5_link_hello(
        mt5_id, login=12345678, broker="XYZ Ltd", server="XYZ-Live3", currency="USD",
        hedging=True, trade_mode="demo", leverage=500, ea_version="1.0.0", ea_build=4400)
    app.mt5_registry.update_from_sync(mt5_id, org_id, report(
        positions=[position(7001, volume=100, sl=1.09),
                   position(7002, symbol="XAUUSD.r", side="SELL", volume=5, open_price=2400.0)],
        orders=[order(5551)], balance=10000.0, equity=10003.0), now=0.0)
    return repo, org_id, mt5_id, app, MT5Lane(app)


def _commands(repo, mt5_id):
    return repo.mt5_commands_open(mt5_id)


def _events(repo, action):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT severity, payload, actor_email, account_id FROM events"
                " WHERE payload->>'action' = %s ORDER BY id", (action,)).fetchall()


class TestOperatorActions:
    def test_market_order_queues_an_open_with_rounded_protection(self, world):
        repo, org_id, mt5_id, app, lane = world
        result = lane.place_order(mt5_id, org_id, "XAUUSD.r", "BUY", "MARKET", 0.5, None, None,
                                  2390.123, 2420.456, "ada@example.com")
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "open" and row["client_order_id"] is None
        assert row["payload"] == {"symbol": "XAUUSD.r", "side": "BUY", "lots": 0.5,
                                  "sl": 2390.12, "tp": 2420.46, "comment": "manual"}
        assert result == {"status": "submitted", "account_id": mt5_id, "symbol": "XAUUSD.r",
                          "side": "BUY", "order_type": "MARKET", "volume": 50,
                          "volume_lots": "0.50", "command_id": row["id"]}
        (event,) = _events(repo, "manual_order")
        assert event["actor_email"] == "ada@example.com"
        assert event["payload"]["protection"] == {"stop_loss": 2390.12, "take_profit": 2420.46}

    def test_pending_order_queues_a_place_pending(self, world):
        repo, org_id, mt5_id, app, lane = world
        lane.place_order(mt5_id, org_id, "EURUSD.r", "SELL", "STOP", 1.0, None, 1.08123456,
                         None, None, None)
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "place_pending"
        assert row["payload"] == {"symbol": "EURUSD.r", "type": "SELL_STOP", "lots": 1.0,
                                  "price": 1.08123, "sl": 0.0, "tp": 0.0, "expiry_ms": 0,
                                  "comment": "manual"}

    def test_volume_is_stepped_and_checked_against_the_minimum(self, world):
        repo, org_id, mt5_id, app, lane = world
        with pytest.raises(ValueError, match="below the minimum"):
            lane.place_order(mt5_id, org_id, "EURUSD.r", "BUY", "MARKET", 0.004, None, None,
                             None, None, None)
        with pytest.raises(ValueError, match="unknown symbol"):
            lane.place_order(mt5_id, org_id, "GBPJPY.r", "BUY", "MARKET", 1.0, None, None,
                             None, None, None)
        assert _commands(repo, mt5_id) == []

    def test_close_full_partial_and_clamped(self, world):
        repo, org_id, mt5_id, app, lane = world
        full = lane.close_position(mt5_id, 7001, None, None)
        partial = lane.close_position(mt5_id, 7001, 0.3, "ada@example.com")
        clamped = lane.close_position(mt5_id, 7001, 5.0, None)
        assert (full["volume"], partial["volume"], clamped["volume"]) == (100, 30, 100)
        assert full["status"] == "submitted" and full["position_id"] == 7001
        rows = _commands(repo, mt5_id)
        assert [r["payload"] for r in rows] == [
            {"position": 7001, "lots": 0.0}, {"position": 7001, "lots": 0.3},
            {"position": 7001, "lots": 0.0}]
        assert full["command_id"] == rows[0]["id"]
        with pytest.raises(ValueError, match="not found"):
            lane.close_position(mt5_id, 424242, None, None)
        assert len(_events(repo, "manual_close")) == 3

    def test_amend_rounds_to_the_symbols_digits(self, world):
        repo, org_id, mt5_id, app, lane = world
        result = lane.amend_position_sltp(mt5_id, 7002, "2390.123", None, None)
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "amend" and row["payload"] == {"position": 7002, "sl": 2390.12,
                                                             "tp": 0.0}
        assert result["stop_loss"] == 2390.12 and result["take_profit"] is None
        with pytest.raises(ValueError):
            lane.amend_position_sltp(mt5_id, 7002, "-1", None, None)

    def test_cancel(self, world):
        repo, org_id, mt5_id, app, lane = world
        assert lane.cancel_order(mt5_id, 5551, None)["order_id"] == 5551
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "cancel_pending" and row["payload"] == {"order": 5551}
        assert _events(repo, "manual_cancel")


class TestFlatten:
    def test_queues_closes_and_cancels_then_verifies_from_the_reports(self, world, monkeypatch):
        repo, org_id, mt5_id, app, lane = world
        monkeypatch.setattr(main_module, "FLATTEN_SETTLE_S", 0.5)
        results = []
        lane.flatten(mt5_id).addCallback(results.append)
        rows = _commands(repo, mt5_id)
        assert [(r["kind"], r["payload"]) for r in rows] == [
            ("close", {"position": 7001, "lots": 0.0}), ("close", {"position": 7002, "lots": 0.0}),
            ("cancel_pending", {"order": 5551})]
        assert results == []                                           # waiting for the terminal
        app.mt5_registry.update_from_sync(mt5_id, org_id, report(seq=2), now=0.4)  # it reports flat
        app.clock.advance(0.5)
        (summary,) = results
        assert summary == {"account_id": mt5_id, "positions_closed": 2, "orders_cancelled": 1,
                           "positions_remaining": [], "orders_remaining": [], "rounds": 1,
                           "error": None}
        (event,) = _events(repo, "kill_switch_flatten")
        assert event["severity"] == "warning" and event["payload"]["positions_closed"] == 2

    def test_a_later_round_never_requeues_a_close_still_in_flight(self, world, monkeypatch):
        repo, org_id, mt5_id, app, lane = world
        monkeypatch.setattr(main_module, "FLATTEN_SETTLE_S", 0.5)
        results = []
        lane.flatten(mt5_id).addCallback(results.append)
        app.clock.advance(0.5)                       # round 1 settled: the terminal said nothing
        assert len(_commands(repo, mt5_id)) == 3     # nothing re-queued
        app.mt5_registry.update_from_sync(mt5_id, org_id, report(
            positions=[position(7002, symbol="XAUUSD.r", side="SELL", volume=5)], seq=2), now=1.0)
        app.clock.advance(1.0)                       # round 2 settled
        app.clock.advance(2.0)                       # round 3 settled
        (summary,) = results
        assert (summary["positions_closed"], summary["orders_cancelled"],
                summary["positions_remaining"], summary["rounds"]) == (1, 1, [7002], 3)
        assert summary["error"] == "1 position(s) and 0 order(s) still open after 3 attempt(s)"
        assert _events(repo, "kill_switch_flatten")[0]["severity"] == "error"

    def test_a_terminal_that_never_reported_is_an_error_not_a_verified_flat(self, world, seed_mt5_account):
        repo, org_id, mt5_id, app, lane = world
        silent = seed_mt5_account(org_id)
        failures = []
        lane.flatten(silent).addErrback(lambda f: failures.append(str(f.value)))
        assert failures and "has not reported" in failures[0]


class TestQueries:
    def _seed_deals(self, repo, org_id, mt5_id):
        repo.upsert_mt5_deals(mt5_id, org_id, [
            {"deal_id": 1, "order_id": 10, "position_id": 7001, "symbol_id": 77,
             "symbol": "EURUSD.r", "side": "BUY", "volume": 100, "filled_volume": 100,
             "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
             "create_timestamp": 1000, "execution_timestamp": 1000, "close": None,
             "balance_after": 9994.97},
            {"deal_id": 2, "order_id": 11, "position_id": 7001, "symbol_id": 77,
             "symbol": "EURUSD.r", "side": "SELL", "volume": 100, "filled_volume": 100,
             "execution_price": 1.105, "status": "FILLED", "commission": -0.03,
             "create_timestamp": 2000, "execution_timestamp": 2000,
             "close": {"entry_price": 1.1, "gross_profit": 5.0, "swap": 0.0,
                       "commission": -0.03, "balance": 10000.0, "closed_volume": 100}},
            {"deal_id": 3, "order_id": 0, "position_id": 0, "symbol_id": None, "symbol": None,
             "side": "BALANCE", "volume": 0, "filled_volume": 0, "execution_price": None,
             "status": "FILLED", "commission": None, "create_timestamp": 3000,
             "execution_timestamp": 3000, "close": None, "gross_profit": 500.0,
             "balance_after": 10500.0},
        ])

    def test_deal_history_has_map_deals_shape_plus_the_estimate_flag(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        history = lane.deal_history(mt5_id, 0, 2500)
        assert history["has_more"] is False
        assert [d["deal_id"] for d in history["deals"]] == [1, 2]      # balance ops are not deals
        opened, closed = history["deals"]
        assert set(opened) == MAP_DEAL_KEYS | {"balance_after_estimated"}
        assert opened["close"] is None and opened["volume_lots"] == "1.00"
        assert opened["balance_after_estimated"] is True
        assert set(closed["close"]) == CLOSE_KEYS
        assert closed["close"]["closed_volume_lots"] == "1.00"
        assert closed["close"]["balance"] == 10000.0

    def test_order_history_is_one_filled_order_per_trade_deal(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        orders = lane.order_history(mt5_id, 0, 5000)
        assert orders["has_more"] is False
        assert orders["orders"][0] == {
            "order_id": 10, "symbol_id": 77, "symbol": "EURUSD.r", "side": "BUY", "volume": 100,
            "volume_lots": "1.00", "order_type": "MARKET", "status": "FILLED",
            "limit_price": None, "stop_price": None, "execution_price": 1.1,
            "executed_volume": 100, "position_id": 7001, "label": "", "open_timestamp": 1000,
            "update_timestamp": 1000, "stop_loss": None, "take_profit": None}
        assert [o["order_id"] for o in orders["orders"]] == [10, 11]

    def test_cash_flow(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        assert lane.cash_flow(mt5_id, 0, 5000) == {"entries": [
            {"id": 3, "type": "DEPOSIT", "amount": 500.0, "balance_after": 10500.0,
             "timestamp": 3000, "note": None}]}

    def test_position_deals(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        deals = lane.position_deals(mt5_id, 7001, 0, 5000)["deals"]
        assert [d["deal_id"] for d in deals] == [1, 2]
        assert lane.position_deals(mt5_id, 9999, 0, 5000) == {"deals": [], "has_more": False}

    def test_details(self, world):
        repo, org_id, mt5_id, app, lane = world
        details = lane.details(mt5_id)
        assert details["platform"] == "mt5" and details["account_id"] == mt5_id
        assert (details["trader_login"], details["balance"], details["deposit_currency"],
                details["leverage"], details["broker_name"], details["account_type"]) == (
            12345678, 10000.0, "USD", 500, "XYZ Ltd", "HEDGED")
        assert details["mt5"]["login"] == 12345678 and details["mt5"]["connected"] is True
        assert details["mt5"]["ea_version"] == "1.0.0"
        positions = {p["position_id"]: p for p in details["open_positions"]}
        assert positions[7001]["volume_lots"] == "1.00" and positions[7001]["stop_loss"] == 1.09
        assert positions[7001]["symbol"] == "EURUSD.r"
        assert positions[7002]["side"] == "SELL" and positions[7002]["volume_lots"] == "0.05"
        (pending,) = details["pending_orders"]
        assert (pending["order_id"], pending["order_type"], pending["side"],
                pending["limit_price"], pending["stop_price"]) == (5551, "LIMIT", "BUY", 1.09, None)
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_mt5_lane.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.mt5.lane'`.

- [ ] **Step 3: Write the module**

Create `copier/src/copier/mt5/lane.py`:

```python
"""Operator actions and read models for ONE MT5 account.

The cTrader paths in main.py talk to a client: reconcile for the live
book, DealList for history, a trade request for an action. An MT5 account
has no client -- it has the registry (the terminal's last report), the
outbox (what the terminal will do next) and the deals table (what it has
done). This lane answers the same calls from those three, in the same
shapes, so the control routes and the dashboard see one kind of account.
"""

import logging
import math

from twisted.internet import defer, task

from copier.domain.models import MANUAL_ORDER_LABEL
from copier.engine.queries import _lots
from copier.mt5.protocol import CENTILOTS, lots

log = logging.getLogger(__name__)

TRADE_SIDES = ("BUY", "SELL")


def validated_price(name: str, raw) -> float | None:
    """A positive, finite price; None for absent/empty; ValueError otherwise.
    Shared with CopierApp.amend_position_sltp."""
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive, finite price")
    return value


class MT5Lane:
    def __init__(self, app):
        """app: CopierApp -- read for repo, mt5_registry, mt5_outbox, clock
        and _org_for_account, so the lane sees exactly the app's wiring."""
        self._app = app

    @property
    def _repo(self):
        return self._app.repo

    @property
    def _registry(self):
        return self._app.mt5_registry

    @property
    def _outbox(self):
        return self._app.mt5_outbox

    def _clock(self):
        clock = self._app.clock
        if clock is None:
            from twisted.internet import reactor as clock
        return clock

    def _org(self, account_id: int) -> int:
        org_id = self._app._org_for_account(account_id)
        if org_id is None:
            raise ValueError(f"account {account_id} not found")
        return org_id

    def _position(self, account_id: int, position_id: int):
        pos = self._registry.position(account_id, position_id)
        if pos is None:
            raise ValueError(f"position {position_id} not found on account {account_id}")
        return pos

    def _digits(self, account_id: int, symbol: str) -> int:
        info = self._registry.symbol_by_name(account_id, symbol)
        return info.digits if info is not None else 5

    @staticmethod
    def _rounded(price, digits: int) -> float:
        """Payload prices are already at the symbol's digits (contract §1);
        0.0 means none."""
        return round(float(price), digits) if price is not None else 0.0

    # ---------- operator actions ----------

    def place_order(self, account_id, org_id, symbol, side, order_type, volume_lots,
                    limit_price, stop_price, stop_loss, take_profit, actor) -> dict:
        info = self._registry.symbol_by_name(account_id, symbol)
        if info is None:
            raise ValueError(f"unknown symbol {symbol!r} for account {account_id}")
        volume = int(round(float(volume_lots) * CENTILOTS))
        if info.step_volume:
            volume -= volume % info.step_volume
        if volume <= 0 or volume < info.min_volume:
            raise ValueError(f"volume {volume_lots} lots is below the minimum for {symbol}")
        sl = self._rounded(stop_loss, info.digits)
        tp = self._rounded(take_profit, info.digits)
        if order_type == "MARKET":
            kind = "open"
            payload = {"symbol": symbol, "side": side, "lots": lots(volume), "sl": sl, "tp": tp,
                       "comment": MANUAL_ORDER_LABEL}
        else:
            price = limit_price if order_type == "LIMIT" else stop_price
            kind = "place_pending"
            payload = {"symbol": symbol, "type": f"{side}_{order_type}", "lots": lots(volume),
                       "price": self._rounded(price, info.digits), "sl": sl, "tp": tp,
                       "expiry_ms": 0, "comment": MANUAL_ORDER_LABEL}
        command_id = self._outbox.enqueue(account_id, org_id, kind, payload, None)
        summary = {"status": "submitted", "account_id": account_id, "symbol": symbol,
                   "side": side, "order_type": order_type, "volume": volume,
                   "volume_lots": f"{volume / CENTILOTS:.2f}", "command_id": command_id}
        protection = ({'stop_loss': sl or None, 'take_profit': tp or None}
                      if (sl or tp) else None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'manual_order',
             **{k: v for k, v in summary.items() if k != 'status'},
             **({'protection': protection} if protection else {})},
            account_id=account_id, org_id=org_id, actor=actor)
        return summary

    def close_position(self, account_id, position_id, volume_lots, actor) -> dict:
        org_id = self._org(account_id)
        pos = self._position(account_id, int(position_id))
        volume = pos.volume
        if volume_lots is not None:
            volume = min(int(round(float(volume_lots) * CENTILOTS)), pos.volume)
            if volume <= 0:
                raise ValueError("volume_lots must be greater than 0")
        payload = {"position": pos.ticket,
                   "lots": 0.0 if volume >= pos.volume else lots(volume)}
        command_id = self._outbox.enqueue(account_id, org_id, "close", payload, None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'manual_close', 'position_id': pos.ticket, 'volume': volume,
             'command_id': command_id},
            account_id=account_id, org_id=org_id, actor=actor)
        return {"status": "submitted", "account_id": account_id, "position_id": pos.ticket,
                "volume": volume, "command_id": command_id}

    def amend_position_sltp(self, account_id, position_id, stop_loss, take_profit, actor) -> dict:
        org_id = self._org(account_id)
        sl = validated_price("stop_loss", stop_loss)
        tp = validated_price("take_profit", take_profit)
        pos = self._position(account_id, int(position_id))
        digits = self._digits(account_id, pos.symbol)
        sl = round(sl, digits) if sl is not None else None
        tp = round(tp, digits) if tp is not None else None
        payload = {"position": pos.ticket, "sl": sl if sl is not None else 0.0,
                   "tp": tp if tp is not None else 0.0}
        command_id = self._outbox.enqueue(account_id, org_id, "amend", payload, None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'amend_sltp', 'position_id': pos.ticket, 'stop_loss': sl,
             'take_profit': tp, 'command_id': command_id},
            account_id=account_id, org_id=org_id, actor=actor)
        return {"status": "submitted", "account_id": account_id, "position_id": pos.ticket,
                "stop_loss": sl, "take_profit": tp, "command_id": command_id}

    def cancel_order(self, account_id, order_id, actor) -> dict:
        org_id = self._org(account_id)
        command_id = self._outbox.enqueue(account_id, org_id, "cancel_pending",
                                          {"order": int(order_id)}, None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'manual_cancel', 'order_id': int(order_id), 'command_id': command_id},
            account_id=account_id, org_id=org_id, actor=actor)
        return {"status": "submitted", "account_id": account_id, "order_id": int(order_id),
                "command_id": command_id}

    @defer.inlineCallbacks
    def flatten(self, account_id: int):
        """Close everything the terminal reports, and VERIFY from its later
        reports -- the same rounds/settle/verified-count contract as
        CopierApp._flatten_account, with the terminal's own book as the
        only evidence. A close already queued for a ticket is not queued
        again: the terminal would refuse the second one."""
        from copier import main as app_main   # constants live with the cTrader flatten
        org_id = self._org(account_id)
        if self._registry.report(account_id) is None:
            raise ValueError(
                f"MT5 account {account_id} has not reported yet: is the terminal connected?")
        clock = self._clock()

        first_positions: set[int] | None = None
        first_orders: set[int] | None = None
        open_positions: list[int] = []
        open_orders: list[int] = []
        rounds_used = 0

        for attempt in range(app_main.FLATTEN_ROUNDS):
            report = self._registry.report(account_id)
            positions, orders = list(report.positions), list(report.orders)
            if first_positions is None:
                first_positions = {p.ticket for p in positions}
                first_orders = {o.ticket for o in orders}
            open_positions = [p.ticket for p in positions]
            open_orders = [o.ticket for o in orders]
            if not positions and not orders:
                break

            rounds_used = attempt + 1
            queued = self._repo.mt5_commands_open(account_id)
            closing = {r["payload"].get("position") for r in queued if r["kind"] == "close"}
            cancelling = {r["payload"].get("order") for r in queued
                          if r["kind"] == "cancel_pending"}
            for p in positions:
                if p.ticket not in closing:
                    self._outbox.enqueue(account_id, org_id, "close",
                                         {"position": p.ticket, "lots": 0.0}, None)
            for o in orders:
                if o.ticket not in cancelling:
                    self._outbox.enqueue(account_id, org_id, "cancel_pending",
                                         {"order": o.ticket}, None)

            yield task.deferLater(
                clock, app_main.FLATTEN_SETTLE_S * (2 ** attempt), lambda: None)
        else:
            report = self._registry.report(account_id)
            open_positions = [p.ticket for p in report.positions]
            open_orders = [o.ticket for o in report.orders]

        first_positions = first_positions or set()
        first_orders = first_orders or set()
        still_open = first_positions & set(open_positions)
        still_working = first_orders & set(open_orders)
        positions_closed = len(first_positions) - len(still_open)
        orders_cancelled = len(first_orders) - len(still_working)

        summary = {
            "account_id": account_id,
            "positions_closed": positions_closed,
            "orders_cancelled": orders_cancelled,
            "positions_remaining": open_positions,
            "orders_remaining": open_orders,
            "rounds": rounds_used,
            "error": None,
        }
        if open_positions or open_orders:
            summary["error"] = (
                f"{len(open_positions)} position(s) and {len(open_orders)} "
                f"order(s) still open after {rounds_used} attempt(s)")

        self._repo.log_event(
            'control', 'error' if summary["error"] else 'warning',
            {'action': 'kill_switch_flatten',
             'positions_closed': positions_closed,
             'orders_cancelled': orders_cancelled,
             'positions_remaining': open_positions,
             'orders_remaining': open_orders,
             'rounds': rounds_used},
            account_id=account_id, org_id=org_id,
        )
        return summary

    # ---------- read models ----------

    def _deal(self, row: dict) -> dict:
        deal = dict(row)
        deal["volume_lots"] = _lots(deal.get("filled_volume"), CENTILOTS)
        if deal.get("close"):
            deal["close"] = {**deal["close"],
                             "closed_volume_lots": _lots(deal["close"].get("closed_volume"),
                                                         CENTILOTS)}
        deal["balance_after_estimated"] = True
        return deal

    def deal_history(self, account_id, from_ms, to_ms) -> dict:
        rows = self._repo.load_deals(account_id, since_ms=from_ms, until_ms=to_ms)
        return {"deals": [self._deal(r) for r in rows if r.get("side") in TRADE_SIDES],
                "has_more": False}

    def order_history(self, account_id, from_ms, to_ms) -> dict:
        """One FILLED order per trade deal: MT5 keeps order history, but a
        deal is the fact the History page shows, and the contract derives."""
        orders = []
        for r in self._repo.load_deals(account_id, since_ms=from_ms, until_ms=to_ms):
            if r.get("side") not in TRADE_SIDES:
                continue
            orders.append({
                "order_id": r["order_id"], "symbol_id": r["symbol_id"], "symbol": r["symbol"],
                "side": r["side"], "volume": r["volume"],
                "volume_lots": _lots(r["volume"], CENTILOTS), "order_type": "MARKET",
                "status": "FILLED", "limit_price": None, "stop_price": None,
                "execution_price": r["execution_price"], "executed_volume": r["filled_volume"],
                "position_id": r["position_id"], "label": "",
                "open_timestamp": r["execution_timestamp"],
                "update_timestamp": r["execution_timestamp"],
                "stop_loss": None, "take_profit": None,
            })
        return {"orders": orders, "has_more": False}

    def cash_flow(self, account_id, from_ms, to_ms) -> dict:
        entries = []
        for r in self._repo.load_mt5_cash_flow(account_id, from_ms, to_ms):
            if r["side"] == "CREDIT":
                kind = "CREDIT"
            else:
                kind = "DEPOSIT" if r["amount"] >= 0 else "WITHDRAW"
            entries.append({"id": r["deal_id"], "type": kind, "amount": r["amount"],
                            "balance_after": r["balance_after"], "timestamp": r["timestamp"],
                            "note": None})
        return {"entries": entries}

    def position_deals(self, account_id, position_id, from_ms, to_ms) -> dict:
        rows = self._repo.load_deals(account_id, since_ms=from_ms, until_ms=to_ms,
                                     position_id=position_id)
        return {"deals": [self._deal(r) for r in rows], "has_more": False}

    def details(self, account_id) -> dict:
        """queries.account_details' shape from the link row and the last
        report, plus "platform" and the "mt5" block the api merges."""
        link = self._repo.load_mt5_link(account_id) or {}
        report = self._registry.report(account_id)
        symbols = self._registry.symbols_by_name(account_id)

        def symbol_id(name):
            info = symbols.get(name)
            return info.symbol_id if info is not None else None

        open_positions = [
            {"position_id": p.ticket, "symbol_id": symbol_id(p.symbol), "symbol": p.symbol,
             "side": p.side, "volume": p.volume, "volume_lots": _lots(p.volume, CENTILOTS),
             "price": p.open_price, "label": p.comment, "stop_loss": p.stop_loss,
             "take_profit": p.take_profit, "swap": p.swap,
             "open_timestamp": p.opened_at_ms or None}
            for p in (report.positions if report is not None else [])
        ]
        pending_orders = [
            {"order_id": o.ticket, "symbol_id": symbol_id(o.symbol), "symbol": o.symbol,
             "side": o.order_type.split("_", 1)[0], "volume": o.volume,
             "volume_lots": _lots(o.volume, CENTILOTS),
             "order_type": o.order_type.split("_", 1)[1],
             "limit_price": o.price if o.order_type.endswith("LIMIT") else None,
             "stop_price": o.price if o.order_type.endswith("STOP") else None,
             "label": o.comment}
            for o in (report.orders if report is not None else [])
        ]
        hedging = link.get("hedging")
        last_seen = link.get("last_seen_at")
        return {
            "account_id": account_id,
            "trader_login": link.get("login"),
            "balance": report.balance if report is not None else link.get("balance"),
            "money_digits": 2,
            "deposit_currency": link.get("currency"),
            "leverage": link.get("leverage"),
            "max_leverage": None,
            "broker_name": link.get("broker"),
            "registration_timestamp": None,
            "account_type": "HEDGED" if hedging else ("NETTED" if hedging is False else "UNKNOWN"),
            "access_rights": "FULL_ACCESS",
            "swap_free": None,
            "is_limited_risk": False,
            "open_positions": open_positions,
            "pending_orders": pending_orders,
            "platform": "mt5",
            "mt5": {
                "login": link.get("login"), "broker": link.get("broker"),
                "server": link.get("server"), "currency": link.get("currency"),
                "hedging": hedging, "trade_mode": link.get("trade_mode"),
                "ea_version": link.get("ea_version"), "ea_build": link.get("ea_build"),
                "last_seen_at": last_seen.isoformat() if last_seen else None,
                "connected": self._registry.is_online(account_id, self._clock().seconds()),
            },
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/unit/test_mt5_lane.py`.
Expected: `14 passed`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/mt5/lane.py copier/tests/unit/test_mt5_lane.py && git commit -m "feat(mt5): the lane -- operator actions as commands, read models from the registry and deals

place/close/amend/cancel queue commands (prices rounded to the symbol's
digits); flatten queues closes and cancels and verifies from the
terminal's reports with the cTrader flatten's summary; details, deal,
order and cash-flow history answer in the cTrader query shapes.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: `CopierApp` wiring — hello/sync/status, the lane, `get_state`, offline detection

**Files:**
- Modify: `copier/src/copier/main.py` — imports `:40-41`, `:55-71`; constants after `:174`; `CopierApp.__init__` `:232-291`; new helpers after `:357`; `_connect_and_authorize` `:734-737`; `_fetch_and_cache_symbols` `:802`; `_client_for_account` `:827-829`; `_refresh_balances_body` `:516-632`; `resync` `:1006-1071`; `_reload_inner` `:1107`, `:1126-1134`, `:1184-1200`; `backfill_deals_once` `:1396-1397`; `_query_context` `:1438-1461`; query wrappers `:1463-1481`, `:2134-2210`; `place_order` `:1558`; `close_position` `:1650-1660`; `amend_position_sltp` `:1718-1790`; `cancel_order` `:1792-1811`; `_flatten_account` `:1997-2020`; `get_state` `:2326-2527`; new MT5 methods after `get_state`; module functions after `_build_send_for_account` `:2620`; `build_app` `:2623-2708`; `boot` before `:2912`
- Modify: `copier/tests/unit/test_main.py:449-453` (the boot loop count becomes 9)
- Test: `copier/tests/unit/test_main_mt5.py`

**Interfaces:**
- Consumes: everything from Tasks 2–14.
- Produces (contract §2 "main.py (CopierApp)", used by Tasks 16 and 17): `CopierApp(..., clients_by_account, mt5_registry, mt5_outbox, clock=None)` with attributes `mt5_registry`, `mt5_outbox`, `mt5_lane`; `mt5_hello(account_id, body) -> {"last_deal_ticket": int}`; `mt5_sync(account_id, body) -> str`; `mt5_status(account_id) -> {"online", "last_seen_at", "pending_commands", "hedging"}`; `check_mt5_offline() -> None` (LoopingCall body, `app.mt5_offline_call`); `MT5_OFFLINE_CHECK_INTERVAL_S = 5.0`, `MT5_LINK_TOUCH_INTERVAL_S = 10.0` (the `touch_mt5_link` write from `mt5_sync` happens at most once per interval per account, tracked in `CopierApp._mt5_last_touch`); private helpers `_clock_seconds`, `_account_row`, `_is_mt5`, `_mt5_aliases`, `_auto_match_aliases`, `_canonical_names_for`, `_apply_mt5_outcome(org_id, account_id, outcome, netting=False, deals_by_ticket=None)` (`deals_by_ticket: dict[int, ReportDeal]` — this report's deals, so an ack is paired with the deal it produced by `AckOutcome.deal`), `_ingest_mt5_deals(account_id, org_id, report, previous_report)`, `_mt5_master_events(account_id, org_id, report, previous, netting=False)`, `_mt5_slave_deals(org_id, account_id, deals, outcomes, netting=False)` (with `CopierApp._mt5_settled_deals: dict[int, set[int]]` — per account, the deal tickets acked by a command that names a copy and not yet seen in a report), `_mt5_back_online`, `_copies_sharing(org_id, account_id, position, except_coid) -> list[int]`, `_record_netting_override(org_id, account_id, position, set_by)`, `_persist_net_ledger(account_id)`; module functions `_build_mt5_targets(routing_provider)`, `_build_mt5_snapshot_provider(routing_provider, registry)`, `_build_mt5_netting_slaves(routing_provider, registry)` (the reconcilers' `netting_slaves`, Task 12b: the MT5 followers whose hello said netting), `_tracker_client_for(clients, accounts, master_account, shards)`. `build_app` now also passes `repo.load_symbol_aliases` to `build_routing`, `snapshot_provider` and `netting_slaves` to every `Reconciler`, `mt5_targets`/`mt5_outbox` to the `Dispatcher`, and `margin_mode=mt5_registry.margin_mode` to the `MT5Outbox`; `state_trackers[org]` is `None` for an org whose accounts are all MT5.
- Netting (spec "Netting accounts"): a netting hello is accepted like a hedging one — `mt5_hello` never degrades an account for its mode and `mt5_sync` never answers `STOP` for it. Follower (the ack rules of contract §2 "Acks of an open on a netting follower"): an `open` outcome is paired with its deal by ticket (`deals_by_ticket`) and the mapping activates on the net ticket — `AckOutcome.position`, or the deal's position when the EA acked `pos` 0 because the fill emptied the net position — whatever the deal's entry; an opposite-side COPY whose fill is an OUT/INOUT (spec "Opposite positions": the master holds BUY and opens SELL) is activated like any other and logs `slave_action/info mt5_netting_opposite_copy {client_order_id, position, entry, volume, nets_against}`, and the older copies it netted against are NOT reduced — their masters are still open. A `close` outcome carrying a `client_order_id` (the outbox's `:close` open) goes through `handle_slave_fill` with `closed_volume`, reducing exactly that mapping by its coid alone (Task 4: the ack's ticket is 0 when the close emptied the position). The deal of any acked command that names a copy is never a terminal-side close: its ticket is remembered in `_mt5_settled_deals` until the terminal reports it (same sync or the next) and skipped then; a close the terminal reports on its own — a stop, a target, the owner's or an operator's own order on the net position — reduces the copies via `reduce_position_mappings_fifo` (hedging keeps the `SlaveFill` → `reduce_position_mapping` path), one `position_closed` event per copy; a refused close or amend fails no mapping; every acked `open`/`amend` on a net ticket shared with other active copies logs `slave_action/info mt5_netting_protection_override {position, set_by, overrides, stop_loss, take_profit}`. Master: `update_from_sync(..., persist=False)`, `master_events_from_report(..., ledger=registry.net_ledger(account))`, `record_master_fill` keyed by the deal ticket (`IN` and `INOUT`), then `_persist_net_ledger` writes the ledger's dirty rows and the virtual positions; `_build_mt5_snapshot_provider` and `get_state` use `snapshot(virtual=True)` / `account_block(virtual=True)` for a netting master.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_main_mt5.py`:

```python
"""CopierApp x MT5 (copier/src/copier/main.py): hello/sync/status, the
outbox round trip, deal ingest, an MT5 master, get_state, offline
detection, the auth loops, operator actions and the kill switch. Drives
the REAL app from build_app with StubSdk-backed cTrader clients, exactly
like test_main.py, whose fixtures this reuses. A Twisted Clock stands in
for the reactor so timestamps are deterministic and nothing scheduled
leaks into the next test."""

import zlib

import psycopg
import pytest
import pytest_twisted
from twisted.internet.task import Clock

import copier.main as main
from copier.ctrader.symbols import by_id as symbols_by_id
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.domain.models import AmendPositionSLTP, ClosePosition, OpenMarket, Side
from copier.engine.reconcile import PositionSnapshot
from copier.mt5 import protocol as p

from test_main import (  # noqa: F401  (fixtures are used by name)
    MASTER_A, ORG_A, SLAVE_A1, SLAVE_B1, _events, _seed_symbol_cache, db_seeded, fernet_key,
    make_stub_client_factory, repo, seed_org_b, token_store)

EURUSD_R = {"n": "EURUSD.r", "d": 5, "cs": 100000, "vmin": 0.01, "vstep": 0.01, "vmax": 100,
            "tm": 4}
XAUUSD_R = {"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01, "vmax": 50, "tm": 4}
EURUSD_R_ID = zlib.crc32(b"EURUSD.r") & 0x7FFFFFFF


def _hello(symbols=(EURUSD_R, XAUUSD_R), hedging=True):
    return {"v": 1, "ea": "1.0.0", "build": 4400, "login": 12345678, "broker": "XYZ Ltd",
            "server": "XYZ-Demo", "currency": "USD", "hedging": hedging, "trade_mode": "demo",
            "leverage": 500, "symbols": list(symbols), "chunk": 1, "chunks": 1}


def _sync(seq=1, positions=(), orders=(), deals=(), acks=(), balance=10000.0, equity=None):
    return {"v": 1, "seq": seq, "ts": 1_757_203_200_000 + seq, "balance": balance,
            "equity": balance if equity is None else equity, "margin": 0,
            "margin_free": balance, "positions": list(positions), "orders": list(orders),
            "deals": list(deals), "acks": list(acks)}


def _pos(ticket, symbol="EURUSD.r", side="BUY", lots=1.0, open_price=1.1, sl=0, tp=0,
         price=1.101, pnl=1.0, comment=""):
    return {"t": ticket, "s": symbol, "side": side, "lots": lots, "open": open_price, "sl": sl,
            "tp": tp, "price": price, "pnl": pnl, "swap": 0, "comment": comment,
            "magic": 20260907, "time": 1757203100}


def _deal(ticket, position, entry="IN", side="BUY", lots=1.0, price=1.1, profit=0.0,
          commission=0.0, time_ms=1_757_203_100_456, order=0, comment=""):
    return {"t": ticket, "pos": position, "order": order, "s": "EURUSD.r", "type": side,
            "entry": entry, "lots": lots, "price": price, "profit": profit, "swap": 0,
            "commission": commission, "time": time_ms, "comment": comment, "magic": 20260907}


def _ack(command_id, ok=True, pos=None, deal=None, order=None, price=None, lots=None,
         retcode=10009, msg="done"):
    return {"id": command_id, "ok": ok, "retcode": retcode, "msg": msg, "pos": pos,
            "deal": deal, "order": order, "price": price, "lots": lots}


def _account(repo, account_id):
    return next(a for a in repo.load_accounts() if a.account_id == account_id)


def _commands(repo, mt5_id):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM mt5_commands WHERE account_id = %s ORDER BY id", (mt5_id,)
            ).fetchall()


def _open_intent(mt5_id, master_position_id=42, symbol_id=EURUSD_R_ID, side=Side.BUY):
    return OpenMarket(slave_account_id=mt5_id, master_position_id=master_position_id,
                      symbol_id=symbol_id, side=side, volume=100, stop_loss=1.09,
                      take_profit=1.12, label=f"copy:m{master_position_id}",
                      symbol_name="EURUSD", entry_price=1.1)


@pytest.fixture
def mt5_world(repo, token_store, seed_mt5_account):
    """Org A (cTrader master 999, slaves 100/101) plus one MT5 slave; the
    master's symbol cache holds EURUSD so the hello can auto-match."""
    _seed_symbol_cache(repo, [MASTER_A, SLAVE_A1])
    mt5_id = seed_mt5_account(ORG_A)
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1, clock=Clock())
    return repo, mt5_id, app


@pytest.fixture
def netting_master_world(db_seeded, fernet_key, seed_mt5_account):
    """Org B: a NETTING MT5 master and the cTrader slave 200, plus a way to
    build a second app on the same database (a copier restart)."""
    org_b = seed_org_b(db_seeded, fernet_key, with_master=False)
    master_id = seed_mt5_account(org_b, role="master")
    repo = Repo(db_seeded)
    _seed_symbol_cache(repo, [SLAVE_B1])

    def build():
        return main.build_app(repo, TokenStore(db_seeded, fernet_key), make_stub_client_factory(),
                              shards=1, clock=Clock())

    return repo, org_b, master_id, build


class TestHello:
    def test_records_the_terminal_symbols_and_aliases(self, mt5_world):
        repo, mt5_id, app = mt5_world
        assert app.mt5_hello(mt5_id, _hello()) == {"last_deal_ticket": 0}
        link = repo.load_mt5_link(mt5_id)
        assert (link["login"], link["broker"], link["hedging"], link["ea_version"]) == (
            12345678, "XYZ Ltd", True, "1.0.0")
        cache = repo.load_symbol_cache(mt5_id)
        assert cache["EURUSD.r"].lot_size == 100 and cache["EURUSD.r"].symbol_id == EURUSD_R_ID
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r"}
        account = _account(repo, mt5_id)
        assert account.trader_login == 12345678 and account.status == "ok"
        (event,) = _events(repo.dsn, "mt5_hello")
        assert event["account_id"] == mt5_id and event["org_id"] == ORG_A
        # Routing now keys the MT5 slave by the canonical name the master's events carry.
        (slave,) = [s for s in app.routing_provider().slaves_by_org[ORG_A]
                    if s.account_id == mt5_id]
        assert slave.symbols["EURUSD"].name == "EURUSD.r"

    def test_the_watermark_is_returned_so_a_restarted_ea_resumes(self, mt5_world):
        repo, mt5_id, app = mt5_world
        repo.set_mt5_watermark(mt5_id, 700005, 1)
        assert app.mt5_hello(mt5_id, _hello()) == {"last_deal_ticket": 700005}

    def test_a_netting_account_is_accepted_and_never_degraded_for_its_mode(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello(hedging=False))
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == ("ok", None)
        assert repo.load_mt5_link(mt5_id)["hedging"] is False
        assert app.mt5_registry.margin_mode(mt5_id) == "netting"
        text = app.mt5_sync(mt5_id, _sync())
        assert text.startswith("OK\t")
        assert app.mt5_status(mt5_id)["hedging"] is False
        (event,) = _events(repo.dsn, "mt5_hello")
        assert event["payload"]["hedging"] is False

    def test_hello_for_a_ctrader_account_is_refused(self, mt5_world):
        _repo, _mt5_id, app = mt5_world
        with pytest.raises(ValueError):
            app.mt5_hello(SLAVE_A1, _hello())


class TestSyncCommandsAndAcks:
    def test_a_queued_copy_is_delivered_and_its_ack_activates_the_mapping(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)

        text = app.mt5_sync(mt5_id, _sync(seq=1))
        status, commands = p.parse_response(text)
        assert status[0] == "OK" and status[2] == str(p.DEFAULT_POLL_MS) and len(status) == 3
        (cmd,) = commands
        assert cmd.kind == "open" and cmd.payload["symbol"] == "EURUSD.r"
        assert cmd.payload["lots"] == 1.0 and cmd.client_order_id == f"cm42.{mt5_id}"

        text = app.mt5_sync(mt5_id, _sync(
            seq=2, positions=[_pos(7001, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 7001, order=700000, comment="copy:m42")],
            acks=[_ack(cmd.id, pos=7001, deal=700001, order=700000, price=1.1001, lots=1.0)]))
        assert p.parse_response(text)[1] == []
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert (mapping["status"], mapping["slave_position_id"], mapping["slave_volume"],
                mapping["fill_price"]) == ("active", 7001, 100, 1.1001)
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)] == [("open", "done")]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id)] == [700001]
        assert repo.mt5_watermark(mt5_id) == (700001, 1_757_203_100_456)

        # The same report again (a re-send): nothing double-counts.
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[_pos(7001, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 7001, order=700000)],
            acks=[_ack(cmd.id, pos=7001, price=1.1001, lots=1.0)]))
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert mapping["slave_volume"] == 100 and len(repo.load_deals(mt5_id)) == 1

    def test_a_rejected_command_fails_the_mapping_and_degrades_the_account(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=1)))[1]
        app.mt5_sync(mt5_id, _sync(seq=2, acks=[_ack(cmd.id, ok=False, retcode=10019,
                                                     msg="No money")]))
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert (mapping["status"], mapping["error"]) == (
            "failed", "terminal rejected open: No money")
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == (
            "degraded", "terminal rejected open: No money")
        # A later successful command clears it, as a successful cTrader send would.
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=43)], org_id=ORG_A)
        (cmd2,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=3)))[1]
        app.mt5_sync(mt5_id, _sync(seq=4, positions=[_pos(7002)],
                                   acks=[_ack(cmd2.id, pos=7002, price=1.1, lots=1.0)]))
        assert _account(repo, mt5_id).status == "ok"

    def test_a_terminal_side_close_reduces_the_mapping_through_its_deal(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=1)))[1]
        app.mt5_sync(mt5_id, _sync(seq=2, positions=[_pos(7001)], deals=[_deal(700001, 7001)],
                                   acks=[_ack(cmd.id, pos=7001, price=1.1, lots=1.0)]))
        # The stop is hit on the terminal: an OUT deal, no ack.
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[],
            deals=[_deal(700002, 7001, entry="OUT", side="SELL", price=1.09, profit=-100.0,
                         time_ms=1_757_203_200_000)],
            balance=9900.0))
        (mapping,) = repo.mapping_rows(org_id=ORG_A)
        assert (mapping["status"], mapping["slave_volume"]) == ("closed", 0)
        closes = [d for d in repo.load_deals(mt5_id) if d["close"]]
        assert len(closes) == 1
        assert closes[0]["close"]["balance"] == 9900.0 and closes[0]["close"]["entry_price"] == 1.1

    def test_an_unknown_account_is_told_to_stop(self, mt5_world):
        _repo, _mt5_id, app = mt5_world
        assert app.mt5_sync(424242, _sync()).startswith("STOP\t")

    def test_status(self, mt5_world):
        repo, mt5_id, app = mt5_world
        assert app.mt5_status(mt5_id) == {"online": False, "last_seen_at": None,
                                          "pending_commands": 0, "hedging": None}
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync())
        app.dispatcher.dispatch([_open_intent(mt5_id)], org_id=ORG_A)
        status = app.mt5_status(mt5_id)
        assert status["online"] is True and status["pending_commands"] == 1
        assert status["hedging"] is True and status["last_seen_at"] is not None

    def test_the_link_write_from_a_sync_is_throttled(self, mt5_world):
        """Four polls a second must not be four UPDATEs a second: balance,
        equity and last_seen_at reach mt5_links at most once per
        MT5_LINK_TOUCH_INTERVAL_S per account (the api throttles its own
        write of those columns the same way)."""
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1, balance=100.0))
        assert repo.load_mt5_link(mt5_id)["balance"] == 100.0
        app.clock.advance(main.MT5_LINK_TOUCH_INTERVAL_S - 0.25)
        app.mt5_sync(mt5_id, _sync(seq=2, balance=200.0))
        assert repo.load_mt5_link(mt5_id)["balance"] == 100.0     # inside the window: not written
        app.clock.advance(0.25)
        app.mt5_sync(mt5_id, _sync(seq=3, balance=300.0))
        link = repo.load_mt5_link(mt5_id)
        assert link["balance"] == 300.0
        assert link["last_seen_at"].timestamp() == app.clock.seconds()


class TestMt5Master:
    @pytest.fixture
    def master_world(self, db_seeded, fernet_key, seed_mt5_account):
        """Org B: an MT5 master and the cTrader slave 200."""
        org_b = seed_org_b(db_seeded, fernet_key, with_master=False)
        master_id = seed_mt5_account(org_b, role="master")
        repo = Repo(db_seeded)
        _seed_symbol_cache(repo, [SLAVE_B1])
        app = main.build_app(repo, TokenStore(db_seeded, fernet_key), make_stub_client_factory(),
                             shards=1, clock=Clock())
        return repo, org_b, master_id, app

    def test_an_mt5_master_gets_an_engine_and_its_deals_fan_out(self, master_world):
        repo, org_b, master_id, app = master_world
        assert app.reconcilers[org_b].master_account_id == master_id
        assert app.state_trackers[org_b] is not None          # quotes ride the slave's connection

        app.mt5_hello(master_id, _hello())
        assert repo.load_symbol_aliases(master_id) == {"EURUSD": "EURUSD.r"}   # from the followers
        assert app.master_symbols_by_org[org_b][EURUSD_R_ID].name == "EURUSD.r"

        app.mt5_sync(master_id, _sync(seq=1))
        app.mt5_sync(master_id, _sync(
            seq=2, positions=[_pos(7001, lots=0.5, sl=1.09)],
            deals=[_deal(700001, 7001, lots=0.5, price=1.1, order=700000)]))
        (mapping,) = repo.mapping_rows(org_id=org_b)
        assert (mapping["master_position_id"], mapping["slave_account_id"],
                mapping["client_order_id"], mapping["symbol"], mapping["status"]) == (
            7001, SLAVE_B1, f"cm7001.{SLAVE_B1}", "EURUSD", "pending")
        # mapping_rows does not select master_fill_price; read it as test_repo.py does.
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (master_fill_price,) = conn.execute(
                "SELECT master_fill_price FROM mappings WHERE client_order_id = %s",
                (mapping["client_order_id"],)).fetchone()
        assert master_fill_price == 1.1

        app.mt5_sync(master_id, _sync(seq=3, positions=[_pos(7001, lots=0.5, sl=1.095)]))
        app.mt5_sync(master_id, _sync(
            seq=4, positions=[],
            deals=[_deal(700002, 7001, entry="OUT", side="SELL", lots=0.5, price=1.105,
                         profit=25.0, time_ms=1_757_203_200_000)]))
        master_events = [e["payload"] for e in _events(repo.dsn)
                         if e["category"] == "master_event"]
        assert master_events == [
            {"source": "mt5", "normalized": "MasterPositionOpened"},
            {"source": "mt5", "normalized": "MasterPositionSLTPAmended"},
            {"source": "mt5", "normalized": "MasterPositionClosed"},
        ]

    def test_get_state_serves_an_mt5_masters_book_from_the_registry(self, master_world):
        repo, org_b, master_id, app = master_world
        app.mt5_hello(master_id, _hello())
        app.mt5_sync(master_id, _sync(
            seq=1, positions=[_pos(7001, lots=0.5, sl=1.09, price=1.102, pnl=3.0)],
            deals=[_deal(700001, 7001, lots=0.5, order=700000)], balance=5000.0, equity=5003.0))
        reconciler = app.reconcilers[org_b]
        reconciler.master_positions, reconciler.master_orders = app.mt5_registry.snapshot(master_id)

        state = app.get_state(org_b)

        (position,) = state["master_positions"]
        assert (position["account_id"], position["symbol"], position["volume"],
                position["volume_lots"], position["pnl_quote"], position["current_price"],
                position["stop_loss"], position["digits"]) == (
            master_id, "EURUSD.r", 50, "0.50", 3.0, 1.102, 1.09, 5)
        assert [c["slave_account_id"] for c in position["copies"]] == [SLAVE_B1]
        assert state["accounts"][master_id]["equity"] == 5003.0


class TestNettingFollower:
    """Every copy on a symbol lives inside the symbol's single net position
    (ticket 9001 here): two master positions, two mappings, one net ticket."""

    def _two_copies(self, repo, mt5_id, app):
        app.mt5_hello(mt5_id, _hello(hedging=False))
        app.mt5_sync(mt5_id, _sync(seq=1))
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=42)], org_id=ORG_A)
        (first,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=2)))[1]
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 9001, order=700000)],
            acks=[_ack(first.id, pos=9001, deal=700001, order=700000, price=1.1, lots=1.0)]))
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=43)], org_id=ORG_A)
        (second,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=4, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)])))[1]
        # The second copy added to the SAME net position: same ticket, 2.00 lots.
        app.mt5_sync(mt5_id, _sync(
            seq=5, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)],
            deals=[_deal(700003, 9001, order=700002, time_ms=1_757_203_100_500)],
            acks=[_ack(second.id, pos=9001, deal=700003, order=700002, price=1.1, lots=1.0)]))
        by_master = self._by_master(repo)
        assert by_master == {42: ("active", 9001, 100), 43: ("active", 9001, 100)}
        return first, second

    @staticmethod
    def _by_master(repo):
        return {m["master_position_id"]: (m["status"], m["slave_position_id"], m["slave_volume"])
                for m in repo.mapping_rows(org_id=ORG_A)}

    def test_a_close_is_an_opposite_open_whose_ack_reduces_exactly_that_mapping(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)

        app.dispatcher.dispatch([ClosePosition(mt5_id, 9001, 100, master_position_id=42)],
                                org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=6, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)])))[1]
        assert cmd.kind == "open" and cmd.client_order_id == f"cm42.{mt5_id}:close"
        assert cmd.payload == {"symbol": "EURUSD.r", "side": "SELL", "lots": 1.0, "sl": 0.0,
                               "tp": 0.0, "comment": "close:m42"}

        # The terminal reduced the net position; its OUT deal rides with the ack.
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)],
            deals=[_deal(700005, 9001, entry="OUT", side="SELL", price=1.101, profit=10.0,
                         time_ms=1_757_203_101_000, order=700004)],
            acks=[_ack(cmd.id, pos=9001, deal=700005, order=700004, price=1.101, lots=1.0)]))

        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("active", 9001, 100)}
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)][-1] == ("open", "done")
        (event,) = _events(repo.dsn, "position_closed")
        assert (event["payload"]["client_order_id"], event["payload"]["closed_volume"]) == (
            f"cm42.{mt5_id}", 100)
        assert [d["deal_id"] for d in repo.load_deals(mt5_id) if d["close"]] == [700005]

    def test_a_close_that_empties_the_net_position_is_acked_with_ticket_0_and_still_closes_its_mapping(
            self, mt5_world):
        """Plan 04's EA acks the net position the terminal shows AFTER the
        fill -- 0 once the last lot is gone (contract §2). The mapping is
        named by the ack's coid, never by that ticket."""
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)

        for seq, master, lots_left, pos_after in ((6, 42, 1.0, 9001), (8, 43, 0.0, 0)):
            app.dispatcher.dispatch([ClosePosition(mt5_id, 9001, 100, master_position_id=master)],
                                    org_id=ORG_A)
            (cmd,) = p.parse_response(app.mt5_sync(mt5_id, _sync(
                seq=seq, positions=[_pos(9001, lots=lots_left + 1.0, sl=1.09, tp=1.12)])))[1]
            assert cmd.client_order_id == f"cm{master}.{mt5_id}:close"
            app.mt5_sync(mt5_id, _sync(
                seq=seq + 1,
                positions=[_pos(9001, lots=lots_left, sl=1.09, tp=1.12)] if lots_left else [],
                deals=[_deal(700000 + seq, 9001, entry="OUT", side="SELL", price=1.101,
                             profit=10.0, time_ms=1_757_203_101_000 + seq, order=700000 + seq - 1)],
                acks=[_ack(cmd.id, pos=pos_after, deal=700000 + seq, order=700000 + seq - 1,
                           price=1.101, lots=1.0)]))

        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("closed", 9001, 0)}
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"])
                for e in _events(repo.dsn, "position_closed")] == [
            (f"cm42.{mt5_id}", 100), (f"cm43.{mt5_id}", 100)]
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)][-2:] == [
            ("open", "done"), ("open", "done")]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id) if d["close"]] == [700006, 700008]

    def test_an_opposite_side_copy_nets_against_the_older_copy_without_closing_it(self, mt5_world):
        """Spec "Opposite positions": the master holds BUY 42 and opens SELL
        45 on the same symbol. On the follower the SELL copy is a plain
        open whose fill REDUCES the net position -- here it empties it, so
        plan 04's EA acks pos 0 and the deal names the position. Both
        masters are open, so both copies stay active with their own
        volumes: the OUT deal is the SELL copy's own fill, not a close of
        the BUY copy."""
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello(hedging=False))
        app.mt5_sync(mt5_id, _sync(seq=1))
        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=42)], org_id=ORG_A)
        (first,) = p.parse_response(app.mt5_sync(mt5_id, _sync(seq=2)))[1]
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)],
            deals=[_deal(700001, 9001, order=700000)],
            acks=[_ack(first.id, pos=9001, deal=700001, order=700000, price=1.1, lots=1.0)]))

        app.dispatcher.dispatch([_open_intent(mt5_id, master_position_id=45, side=Side.SELL)],
                                org_id=ORG_A)
        (second,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=4, positions=[_pos(9001, lots=1.0, sl=1.09, tp=1.12)])))[1]
        assert (second.kind, second.payload["side"], second.client_order_id) == (
            "open", "SELL", f"cm45.{mt5_id}")
        # The terminal netted the SELL against the BUY: one OUT deal for the
        # whole lot, no position left, an ack whose pos is 0.
        app.mt5_sync(mt5_id, _sync(
            seq=5, positions=[],
            deals=[_deal(700003, 9001, entry="OUT", side="SELL", price=1.1, order=700002,
                         time_ms=1_757_203_100_500)],
            acks=[_ack(second.id, pos=0, deal=700003, order=700002, price=1.1, lots=1.0)]))

        assert self._by_master(repo) == {42: ("active", 9001, 100), 45: ("active", 9001, 100)}
        assert [(c["kind"], c["status"]) for c in _commands(repo, mt5_id)] == [
            ("open", "done"), ("open", "done")]
        assert _events(repo.dsn, "position_closed") == []
        assert _events(repo.dsn, "mt5_open_ack_without_position") == []
        (netted,) = _events(repo.dsn, "mt5_netting_opposite_copy")
        assert (netted["payload"]["client_order_id"], netted["payload"]["position"],
                netted["payload"]["entry"], netted["payload"]["volume"],
                netted["payload"]["nets_against"]) == (f"cm45.{mt5_id}", 9001, "OUT", 100, [42])

    def test_a_terminal_side_close_reduces_the_copies_oldest_first(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)

        # The owner trims 1.50 lots by hand: the older copy goes first, then
        # a third of the younger one.
        app.mt5_sync(mt5_id, _sync(
            seq=6, positions=[_pos(9001, lots=0.5, sl=1.09, tp=1.12)],
            deals=[_deal(700005, 9001, entry="OUT", side="SELL", lots=1.5, price=1.09,
                         profit=-150.0, time_ms=1_757_203_101_000)],
            balance=9850.0))
        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("active", 9001, 50)}
        events = _events(repo.dsn, "position_closed")
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"])
                for e in events] == [(f"cm42.{mt5_id}", 100), (f"cm43.{mt5_id}", 50)]

        # Then the net stop takes the rest.
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[],
            deals=[_deal(700007, 9001, entry="OUT", side="SELL", lots=0.5, price=1.09,
                         profit=-50.0, time_ms=1_757_203_102_000)],
            balance=9800.0))
        assert self._by_master(repo) == {42: ("closed", 9001, 0), 43: ("closed", 9001, 0)}
        assert _commands(repo, mt5_id)[-1]["kind"] == "open"      # nothing was queued for the terminal's own closes

    def test_the_shared_protection_override_is_recorded(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)
        (event,) = _events(repo.dsn, "mt5_netting_protection_override")
        assert event["account_id"] == mt5_id
        assert (event["payload"]["position"], event["payload"]["set_by"],
                event["payload"]["overrides"], event["payload"]["stop_loss"],
                event["payload"]["take_profit"]) == (9001, f"cm43.{mt5_id}", [42], 1.09, 1.12)

        # An amend for one copy moves the stop every copy on the symbol shares.
        app.dispatcher.dispatch([AmendPositionSLTP(mt5_id, 9001, 1.095, 1.12)], org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=6, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)])))[1]
        assert cmd.kind == "amend" and cmd.payload == {"position": 9001, "sl": 1.095, "tp": 1.12}
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[_pos(9001, lots=2.0, sl=1.095, tp=1.12)], acks=[_ack(cmd.id, pos=9001)]))
        events = _events(repo.dsn, "mt5_netting_protection_override")
        assert len(events) == 2
        assert (events[1]["payload"]["set_by"], sorted(events[1]["payload"]["overrides"]),
                events[1]["payload"]["stop_loss"]) == (None, [42, 43], 1.095)
        assert _account(repo, mt5_id).status == "ok"

    def test_a_refused_close_leaves_the_mapping_open(self, mt5_world):
        repo, mt5_id, app = mt5_world
        self._two_copies(repo, mt5_id, app)
        app.dispatcher.dispatch([ClosePosition(mt5_id, 9001, 100, master_position_id=42)],
                                org_id=ORG_A)
        (cmd,) = p.parse_response(app.mt5_sync(
            mt5_id, _sync(seq=6, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)])))[1]
        app.mt5_sync(mt5_id, _sync(
            seq=7, positions=[_pos(9001, lots=2.0, sl=1.09, tp=1.12)],
            acks=[_ack(cmd.id, ok=False, retcode=10018, msg="Market is closed")]))
        assert self._by_master(repo) == {42: ("active", 9001, 100), 43: ("active", 9001, 100)}
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == (
            "degraded", "terminal rejected close: Market is closed")


class TestNettingMaster:
    """A netting MT5 master: its deals expand into virtual positions the
    cTrader follower copies one by one."""

    def test_add_add_reduce_and_reverse_fan_out_by_virtual_id(self, netting_master_world):
        repo, org_b, master_id, build = netting_master_world
        app = build()
        app.mt5_hello(master_id, _hello(hedging=False))
        app.mt5_sync(master_id, _sync(seq=1))

        # add, add
        app.mt5_sync(master_id, _sync(
            seq=2, positions=[_pos(5, lots=0.5, sl=1.09)],
            deals=[_deal(700001, 5, lots=0.5, price=1.1, order=700000, time_ms=1_000)]))
        app.mt5_sync(master_id, _sync(
            seq=3, positions=[_pos(5, lots=0.8, sl=1.09)],
            deals=[_deal(700003, 5, lots=0.3, price=1.101, order=700002, time_ms=2_000)]))
        by_master = {m["master_position_id"]: m for m in repo.mapping_rows(org_id=org_b)}
        assert set(by_master) == {700001, 700003}
        assert by_master[700001]["client_order_id"] == f"cm700001.{SLAVE_B1}"
        assert [(r["virtual_id"], r["side"], r["volume_left"], r["stop_loss"])
                for r in repo.load_net_ledger(master_id)] == [
            (700001, "BUY", 50, 1.09), (700003, "BUY", 30, 1.09)]
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (master_fill_price,) = conn.execute(
                "SELECT master_fill_price FROM mappings WHERE client_order_id = %s",
                (f"cm700003.{SLAVE_B1}",)).fetchone()
        assert master_fill_price == 1.101

        # partial reduce: 0.6 lots out -> the older virtual position closes,
        # the younger loses 0.1
        app.mt5_sync(master_id, _sync(
            seq=4, positions=[_pos(5, lots=0.2, sl=1.09)],
            deals=[_deal(700005, 5, entry="OUT", side="SELL", lots=0.6, price=1.105, profit=30.0,
                         time_ms=3_000)]))
        assert [(r["virtual_id"], r["volume_left"]) for r in repo.load_net_ledger(master_id)] == [
            (700003, 20)]
        closes = [d for d in repo.load_deals(master_id) if d["close"]]
        assert closes[0]["close"]["entry_price"] == 1.1 and closes[0]["close"]["closed_volume"] == 60

        # the net stop moves: every virtual position is amended
        app.mt5_sync(master_id, _sync(seq=5, positions=[_pos(5, lots=0.2, sl=1.095)]))

        # reversal: SELL 0.5 against 0.2 BUY -> INOUT: close all, open 0.3 SELL
        app.mt5_sync(master_id, _sync(
            seq=6, positions=[_pos(5, side="SELL", lots=0.3)],
            deals=[_deal(700007, 5, entry="INOUT", side="SELL", lots=0.5, price=1.104, profit=6.0,
                         time_ms=4_000)]))
        assert [(r["virtual_id"], r["side"], r["volume_left"])
                for r in repo.load_net_ledger(master_id)] == [(700007, "SELL", 30)]
        assert 700007 in {m["master_position_id"] for m in repo.mapping_rows(org_id=org_b)}
        master_events = [e["payload"]["normalized"] for e in _events(repo.dsn)
                         if e["category"] == "master_event"]
        assert master_events == [
            "MasterPositionOpened", "MasterPositionOpened",
            "MasterPositionClosed", "MasterPositionClosed",
            "MasterPositionSLTPAmended",
            "MasterPositionClosed", "MasterPositionOpened"]
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions WHERE account_id = %s"
                " ORDER BY position_id", (master_id,)).fetchall()
        assert [r for r in rows if r[2] == "open"] == [(700007, 30, "open")]

    def test_get_state_and_a_restart_see_the_virtual_positions(self, netting_master_world):
        repo, org_b, master_id, build = netting_master_world
        app = build()
        app.mt5_hello(master_id, _hello(hedging=False))
        app.mt5_sync(master_id, _sync(
            seq=1, positions=[_pos(5, lots=0.8, price=1.102, pnl=8.0)],
            deals=[_deal(700001, 5, lots=0.5, time_ms=1_000),
                   _deal(700003, 5, lots=0.3, time_ms=2_000)],
            balance=5000.0, equity=5008.0))
        reconciler = app.reconcilers[org_b]
        reconciler.master_positions, reconciler.master_orders = app._mt5_snapshot_provider(master_id)
        assert [p_.position_id for p_ in reconciler.master_positions] == [700001, 700003]

        state = app.get_state(org_b)

        assert [(pos["position_id"], pos["volume"], pos["pnl_quote"], pos["current_price"])
                for pos in state["master_positions"]] == [
            (700001, 50, 5.0, 1.102), (700003, 30, 3.0, 1.102)]
        assert [(pos["position_id"], pos["pnl_quote"])
                for pos in state["accounts"][master_id]["positions"]] == [
            (700001, 5.0), (700003, 3.0)]
        assert state["accounts"][master_id]["equity"] == 5008.0
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT position_id, volume, status FROM positions WHERE account_id = %s"
                " ORDER BY position_id", (master_id,)).fetchall()
        assert rows == [(700001, 50, "open"), (700003, 30, "open")]

        # The copier restarts: the ledger comes back from Postgres, the first
        # report diffs nothing, and the next OUT still consumes oldest-first.
        fresh = build()
        fresh.mt5_hello(master_id, _hello(hedging=False))
        fresh.mt5_sync(master_id, _sync(seq=1, positions=[_pos(5, lots=0.8)]))
        fresh.mt5_sync(master_id, _sync(
            seq=2, positions=[_pos(5, lots=0.2)],
            deals=[_deal(700005, 5, entry="OUT", side="SELL", lots=0.6, price=1.105, profit=30.0,
                         time_ms=3_000)]))
        assert [(r["virtual_id"], r["volume_left"]) for r in repo.load_net_ledger(master_id)] == [
            (700003, 20)]
        master_events = [e["payload"]["normalized"] for e in _events(repo.dsn)
                         if e["category"] == "master_event"]
        assert master_events[-2:] == ["MasterPositionClosed", "MasterPositionClosed"]


class TestGetState:
    def test_mt5_accounts_are_served_from_the_registry(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.master_symbols_by_org[ORG_A].update(symbols_by_id(repo.load_symbol_cache(MASTER_A)))
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=ORG_A, symbol="EURUSD")
        repo.activate_position_mapping(mt5_id, f"cm42.{mt5_id}", 7001, 100, fill_price=1.1001)
        app.mt5_sync(mt5_id, _sync(positions=[_pos(7001, sl=1.09, tp=1.12, price=1.102, pnl=2.0)],
                                   balance=9784.04, equity=9786.04))
        reconciler = app.reconcilers[ORG_A]
        reconciler.master_positions = [PositionSnapshot(
            position_id=42, symbol_id=1, side=Side.BUY, volume=10_000_000, price=1.1, label="")]
        reconciler.slave_positions = {mt5_id: app.mt5_registry.snapshot(mt5_id)[0]}

        state = app.get_state(ORG_A)

        block = state["accounts"][mt5_id]
        assert (block["balance"], block["equity"], block["open_pnl"]) == (9784.04, 9786.04, 2.0)
        assert block["positions"][0] == {
            "position_id": 7001, "symbol_id": EURUSD_R_ID, "symbol": "EURUSD.r", "side": "BUY",
            "volume": 100, "entry_price": 1.1, "stop_loss": 1.09, "take_profit": 1.12,
            "pnl_quote": 2.0, "current_price": 1.102}
        (copy,) = state["master_positions"][0]["copies"]
        assert (copy["slave_account_id"], copy["slave_position_id"], copy["volume_lots"],
                copy["fill_price"], copy["stop_loss"], copy["take_profit"]) == (
            mt5_id, 7001, "1.00", 1.1001, 1.09, 1.12)


class TestOffline:
    def test_a_silent_terminal_is_degraded_and_a_report_clears_it(self, mt5_world):
        repo, mt5_id, app = mt5_world
        clock = app.clock
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1))
        clock.advance(10.0)
        app.check_mt5_offline()
        assert _account(repo, mt5_id).status == "ok"
        clock.advance(6.0)
        app.check_mt5_offline()
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == (
            "degraded", "terminal offline since 1970-01-01T00:00:00+00:00")
        assert _events(repo.dsn, "mt5_offline")[0]["payload"]["since"] == "1970-01-01T00:00:00+00:00"
        app.check_mt5_offline()                                   # idempotent: no second event
        assert len(_events(repo.dsn, "mt5_offline")) == 1
        assert app.mt5_status(mt5_id)["online"] is False

        app.mt5_sync(mt5_id, _sync(seq=2))
        account = _account(repo, mt5_id)
        assert (account.status, account.last_error) == ("ok", None)
        assert _events(repo.dsn, "mt5_online")

    def test_a_paused_account_is_left_alone(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1))
        repo.set_account_status(mt5_id, "paused")
        app.clock.advance(16.0)
        app.check_mt5_offline()
        assert _account(repo, mt5_id).status == "paused"


class TestCtraderLoopsSkipMt5:
    @pytest_twisted.inlineCallbacks
    def test_startup_never_authorizes_or_fetches_symbols_for_an_mt5_account(self, mt5_world):
        repo, mt5_id, app = mt5_world
        yield app.startup()
        for env_clients in app.clients.values():
            for client in env_clients.values():
                assert mt5_id not in client._accounts
        assert _account(repo, mt5_id).status == "ok"
        assert not [e for e in _events(repo.dsn, "symbol_fetch_failed")
                    if e["account_id"] == mt5_id]
        assert app._client_for_account(_account(repo, mt5_id)) is None
        assert app._query_context(mt5_id)[0] is app.mt5_lane

    @pytest_twisted.inlineCallbacks
    def test_reload_keeps_the_mt5_account_out_of_the_auth_loop(self, mt5_world):
        repo, mt5_id, app = mt5_world
        yield app.reload()
        for env_clients in app.clients.values():
            for client in env_clients.values():
                assert mt5_id not in client._accounts
        assert _account(repo, mt5_id).status == "ok"
        assert app.reconcilers[ORG_A].snapshot_provider is app._mt5_snapshot_provider
        assert app.reconcilers[ORG_A].netting_slaves is app._mt5_netting_slaves


class TestOperatorActions:
    @pytest_twisted.inlineCallbacks
    def test_trade_page_actions_branch_to_the_lane(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(
            positions=[_pos(7001)],
            orders=[{"t": 5551, "s": "EURUSD.r", "type": "BUY_LIMIT", "lots": 0.1,
                     "price": 1.09, "sl": 0, "tp": 0, "comment": "manual", "magic": 20260907}]))

        placed = app.place_order({"account_id": mt5_id, "symbol": "XAUUSD.r", "side": "SELL",
                                  "order_type": "MARKET", "volume_lots": 0.2,
                                  "stop_loss": 2450.129, "actor_email": "ada@example.com"})
        assert placed["status"] == "submitted" and placed["volume"] == 20
        closed = yield app.close_position(mt5_id, 7001, 0.4, actor="ada@example.com")
        assert closed["volume"] == 40
        amended = app.amend_position_sltp(mt5_id, 7001, stop_loss="1.08", take_profit=None,
                                          actor="ada@example.com")
        assert amended["stop_loss"] == 1.08
        cancelled = yield app.cancel_order(mt5_id, 5551, actor="ada@example.com")
        assert cancelled["status"] == "submitted"

        rows = _commands(repo, mt5_id)
        assert [(r["kind"], r["payload"]) for r in rows] == [
            ("open", {"symbol": "XAUUSD.r", "side": "SELL", "lots": 0.2, "sl": 2450.13,
                      "tp": 0.0, "comment": "manual"}),
            ("close", {"position": 7001, "lots": 0.4}),
            ("amend", {"position": 7001, "sl": 1.08, "tp": 0.0}),
            ("cancel_pending", {"order": 5551}),
        ]
        actions = [e for e in _events(repo.dsn)
                   if e["payload"].get("action") in ("manual_order", "manual_close",
                                                     "amend_sltp", "manual_cancel")]
        assert len(actions) == 4 and all(e["payload"]["command_id"] for e in actions)

    def test_dry_run_still_blocks_a_manual_open_on_mt5(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        repo.set_org_setting(ORG_A, "dry_run", True)
        with pytest.raises(ValueError, match="dry-run"):
            app.place_order({"account_id": mt5_id, "symbol": "XAUUSD.r", "side": "BUY",
                             "order_type": "MARKET", "volume_lots": 0.2})

    def test_broker_only_queries_say_so_for_mt5(self, mt5_world):
        _repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        failures = []
        app.get_expected_margin(mt5_id, "EURUSD.r", 1.0).addErrback(
            lambda f: failures.append(str(f.value)))
        app.get_trendbars(mt5_id, "EURUSD.r", "M1", 0, 1).addErrback(
            lambda f: failures.append(str(f.value)))
        assert len(failures) == 2 and all("MT5" in message for message in failures)
        assert app.get_quote(mt5_id, "EURUSD.r") == {"symbol": "EURUSD.r", "bid": None, "ask": None}

    def test_history_queries_read_the_lane(self, mt5_world):
        repo, mt5_id, app = mt5_world
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1, deals=[_deal(700001, 7001, time_ms=1000)]))
        results = {}
        app.get_deal_history(mt5_id, 0, 5000).addCallback(lambda r: results.update(deals=r))
        app.get_order_history(mt5_id, 0, 5000).addCallback(lambda r: results.update(orders=r))
        app.get_cash_flow(mt5_id, 0, 5000).addCallback(lambda r: results.update(cash=r))
        app.get_position_deals(mt5_id, 7001, 0, 5000).addCallback(lambda r: results.update(pos=r))
        app.get_account_details(mt5_id).addCallback(lambda r: results.update(details=r))
        assert [d["deal_id"] for d in results["deals"]["deals"]] == [700001]
        assert results["deals"]["deals"][0]["balance_after_estimated"] is True
        assert [o["position_id"] for o in results["orders"]["orders"]] == [7001]
        assert results["cash"] == {"entries": []}
        assert [d["deal_id"] for d in results["pos"]["deals"]] == [700001]
        assert results["details"]["platform"] == "mt5" and results["details"]["mt5"]["login"] == 12345678
        assert app.get_analytics(mt5_id, 4)["truncated"] is False


class TestKillSwitch:
    def test_close_all_on_an_mt5_account_is_verified_from_its_reports(self, mt5_world, monkeypatch):
        monkeypatch.setattr(main, "FLATTEN_SETTLE_S", 0.5)
        repo, mt5_id, app = mt5_world
        clock = app.clock
        app.mt5_hello(mt5_id, _hello())
        app.mt5_sync(mt5_id, _sync(seq=1, positions=[_pos(7001), _pos(7002)]))

        results = []
        app.close_all(ORG_A, mt5_id).addCallback(results.append)
        commands = p.parse_response(
            app.mt5_sync(mt5_id, _sync(seq=2, positions=[_pos(7001), _pos(7002)])))[1]
        assert [(c.kind, c.payload["position"]) for c in commands] == [("close", 7001), ("close", 7002)]
        app.mt5_sync(mt5_id, _sync(
            seq=3, positions=[],
            deals=[_deal(700001, 7001, entry="OUT", side="SELL", time_ms=1),
                   _deal(700002, 7002, entry="OUT", side="SELL", time_ms=2)],
            acks=[_ack(c.id, pos=c.payload["position"], lots=1.0) for c in commands]))
        clock.advance(0.5)

        (result,) = results
        assert result["paused"] is False
        (summary,) = result["accounts"]
        assert (summary["positions_closed"], summary["orders_cancelled"],
                summary["positions_remaining"], summary["error"]) == (2, 0, [], None)
```

- [ ] **Step 2: Run the test to verify it fails**

Run DB `tests/unit/test_main_mt5.py`.
Expected: FAIL — every test with `AttributeError: 'CopierApp' object has no attribute 'mt5_hello'` (or `'mt5_status'` / `'check_mt5_offline'`).

- [ ] **Step 3: Wire the app — imports, constructor, helpers, the cTrader loops**

All edits are in `copier/src/copier/main.py`, listed in file order.

(a) Lines 40-41 become:

```python
from dataclasses import dataclass, replace as dc_replace
from datetime import datetime, timedelta, timezone
```

(b) Replace the copier imports (lines 55-71) with:

```python
from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.ctrader.symbols import fetch_symbol_map, by_id as symbols_by_id
from copier.db.repo import Repo
from copier.db.writer import AsyncWriter
from copier.domain.models import MANUAL_ORDER_LABEL, Side
from copier.engine.service import CopierService, SlaveFill
from copier.engine.reconcile import Reconciler
from copier.engine.routing import (
    OrgRouting, RoutingCache, build_routing, mt5_symbols_by_canonical)
from copier.engine.state import AccountStateTracker, PositionSnapshot as StatePositionSnapshot
from copier.engine.backfill import next_window, reached_history_bound, WEEK_MS
from copier.engine.dispatch import Dispatcher, relative_protection, SendNotAttempted
from copier.engine.throttle import TokenBucket
from copier.engine.control import make_control_site
from copier.engine import queries
from copier.engine.analytics import compute_analytics
from copier.engine.commission import round_trip_rates
from copier.mt5 import protocol as mt5_protocol
from copier.mt5.deals import CLOSE_ENTRIES, TRADE_TYPES, deal_rows
from copier.mt5.ingress import master_events_from_report
from copier.mt5.lane import MT5Lane, validated_price
from copier.mt5.outbox import MT5Outbox
from copier.mt5.registry import MT5Registry
from copier.mt5.symbols import auto_match
```

(c) After `DEAL_BACKFILL_INTERVAL_S = 30` (line 174) add:

```python
# How often to notice that an MT5 terminal has stopped reporting (spec: a
# 5 s timer; the account reads offline OFFLINE_AFTER_S after its last report).
MT5_OFFLINE_CHECK_INTERVAL_S = 5.0
# The mt5_links balance/equity/last_seen_at write from a sync is throttled to
# this per account -- the same window the api uses for its own write of those
# columns (contract §3) -- so a terminal polling four times a second is not
# four UPDATEs a second on one row.
MT5_LINK_TOUCH_INTERVAL_S = 10.0
```

(d) In `CopierApp.__init__` (line 232-247) add two parameters before `clock=None`:

```python
        clients_by_account: Callable[[int], CTraderClient],
        mt5_registry: MT5Registry,
        mt5_outbox: MT5Outbox,
        clock=None,
    ):
```

and after `self._clients_by_account = clients_by_account` (line 277) add:

```python
        self.mt5_registry = mt5_registry
        self.mt5_outbox = mt5_outbox
        # Answers an MT5 account's book to the reconcilers, and which MT5
        # followers are netting accounts (their net positions are compared
        # with the sum of their copies, Task 12b); built here so reload()
        # hands new Reconcilers the same callables.
        self._mt5_snapshot_provider = _build_mt5_snapshot_provider(routing_provider, mt5_registry)
        self._mt5_netting_slaves = _build_mt5_netting_slaves(routing_provider, mt5_registry)
        self.mt5_lane = MT5Lane(self)
        # canonical -> broker name per MT5 account; cleared on reload() and
        # after every hello, the two moments aliases can change.
        self._mt5_aliases_cache: dict[int, dict[str, str]] = {}
        # account_id -> clock seconds of the last touch_mt5_link write
        # (MT5_LINK_TOUCH_INTERVAL_S throttle).
        self._mt5_last_touch: dict[int, float] = {}
        # account_id -> deal tickets acked by a command that names a copy
        # (an open's fill, a ':close') and not yet seen in a report: the
        # ack did their bookkeeping, so _mt5_slave_deals must not book them
        # again when the terminal reports them -- in the same sync as the
        # ack or, past MAX_DEALS_PER_SYNC, the next one.
        self._mt5_settled_deals: dict[int, set[int]] = {}
```

(e) After `state_tracker_for_account` (after line 357) add:

```python
    def _clock_seconds(self) -> float:
        clock = self.clock
        if clock is None:
            from twisted.internet import reactor as clock
        return clock.seconds()

    def _account_row(self, account_id: int):
        return next((a for a in self.repo.load_accounts() if a.account_id == account_id), None)

    def _is_mt5(self, account_id: int) -> bool:
        """From the routing snapshot: at most one second stale and no
        database round trip, so it can sit on every operator path."""
        return self.routing_provider().platform_by_account.get(account_id) == "mt5"

    def _mt5_aliases(self, account_id: int) -> dict[str, str]:
        aliases = self._mt5_aliases_cache.get(account_id)
        if aliases is None:
            aliases = self.repo.load_symbol_aliases(account_id)
            self._mt5_aliases_cache[account_id] = aliases
        return aliases
```

(f) In `_connect_and_authorize` (lines 734-737) the shard filter becomes:

```python
                shard_accounts = [
                    a for a in accounts
                    if a.platform != 'mt5'
                    and a.is_live == is_live and a.account_id % self.shards == shard
                ]
```

(g) In `_fetch_and_cache_symbols`, the loop head (line 802 `for account in accounts:`) becomes:

```python
        for account in accounts:
            if account.platform == 'mt5':
                # No broker to ask: the terminal's hello fills the symbol
                # cache. An MT5 MASTER's map still has to reach get_state,
                # the tracker and the alias matcher from that cache.
                if account.role == 'master':
                    org_symbols = self.master_symbols_by_org.setdefault(account.org_id, {})
                    org_symbols.clear()
                    org_symbols.update(symbols_by_id(
                        self.repo.load_symbol_cache(account.account_id)))
                continue
```

(the rest of the loop body is unchanged).

(h) `_client_for_account` (lines 827-829) becomes:

```python
    def _client_for_account(self, account) -> CTraderClient | None:
        if account.platform == 'mt5':
            return None   # reached through its own polls, never a cTrader client
        shard = account.account_id % self.shards
        return self.clients.get(account.is_live, {}).get(shard)
```

(i) Replace `_refresh_balances_body` (lines 515-632) with:

```python
    @defer.inlineCallbacks
    def _refresh_balances_body(self, only_org_id: int | None = None):
        if not self.state_trackers:
            return
        try:
            accounts = self.repo.load_accounts()
        except Exception:
            log.exception("refresh_balances: failed to load accounts")
            return

        # The intraday balance/equity sample is gated PER ORG.
        #
        # A single process-wide clock starved every org but one, because
        # this body is also called org-scoped: resync(org_id) ends with
        # refresh_balances(org_id) (see below), and request_resync fires on
        # every position change. Org A trading actively would land one of
        # those fill-driven, A-only invocations on the 5-minute gate inside
        # every window: the clock was stamped, the loop then `continue`d
        # past orgs B..N, and B..N never got an intraday sample AT ALL.
        # Gating per org is safe here in a way it was not with one clock --
        # each org's gate is only ever opened and stamped by that org.
        #
        # routing_provider() is resolved lazily and at most once per pass.
        now = time.monotonic()
        routing = None

        for org_id, tracker in self.state_trackers.items():
            if only_org_id is not None and org_id != only_org_id:
                continue
            # Each org's tracker only ever reads ITS OWN accounts: the
            # tracker's snapshot feeds that org's /state, so a foreign
            # account id here would leak one tenant's balance into another's
            # Overview.
            org_accounts = [a for a in accounts if a.org_id == org_id and a.enabled]
            # MT5 terminals report their own balance and equity on every
            # poll; only cTrader accounts are asked over the wire.
            ctrader_accounts = [a for a in org_accounts if a.platform != 'mt5']
            enabled_ids = [a.account_id for a in ctrader_accounts]
            if not org_accounts:
                continue
            snapshot: dict = {}
            if tracker is not None and enabled_ids:
                try:
                    # refresh_balances() fans out one ProtoOATraderReq per
                    # account and DeferredLists them; the SDK queue paces the
                    # wire. Each request must ride ITS account's environment
                    # client -- an org can mix demo and live accounts, and the
                    # master's client only serves the master's environment.
                    clients_by_account = {}
                    for a in ctrader_accounts:
                        client = self._client_for_account(a)
                        if client is not None:
                            clients_by_account[a.account_id] = client
                    yield tracker.refresh_balances(
                        enabled_ids, clients_by_account=clients_by_account)
                except Exception:
                    log.exception("refresh_balances: broker request failed (org %s)", org_id)
                    continue

                # Fetched once and shared by the daily snapshot write below
                # and the intraday balance-sample write further down, so a
                # failure in either write never leaves `snapshot` undefined
                # for the other. Defaults to empty on failure so both writes
                # below are no-ops rather than raising out of the per-org loop.
                try:
                    snapshot = dict(tracker.snapshot())
                except Exception:
                    log.exception("refresh_balances: snapshot read failed (org %s)", org_id)
                    snapshot = {}
            for a in org_accounts:
                if a.platform != 'mt5':
                    continue
                block = self.mt5_registry.account_block(a.account_id)
                if block is not None:
                    snapshot[a.account_id] = block

            # Daily portfolio snapshot: upsert each refreshed account under
            # today's UTC date -- the last write of a day wins, so yesterday's
            # rows hold yesterday's closing values (Overview's vs-yesterday
            # comparison reads them). org_id comes off the AccountRow being
            # iterated so each desk's overview sums only its own snapshots.
            try:
                today = datetime.utcnow().date()
                for account in org_accounts:
                    state = snapshot.get(account.account_id)
                    if state is None or state.get("balance") is None:
                        continue
                    self.repo.save_portfolio_snapshot(
                        today, account.account_id, state["balance"],
                        state.get("equity"), org_id=account.org_id)
            except Exception:
                log.exception("refresh_balances: snapshot write failed (org %s)", org_id)

            # Intraday balance/equity series, sampled every
            # BALANCE_SAMPLE_INTERVAL_S rather than every poll.
            # portfolio_snapshots above stays daily-close.
            due = (now - self._last_balance_sample.get(org_id, 0.0)
                   >= BALANCE_SAMPLE_INTERVAL_S)
            if due:
                try:
                    # Inside the try, and stamped only after the writes
                    # land: a routing_provider() or repo failure must not
                    # burn this org's 5-minute slot as well as losing the
                    # sample.
                    if routing is None:
                        routing = self.routing_provider()
                    for account_id, snap in snapshot.items():
                        self.repo.record_balance_sample(
                            account_id,
                            routing.org_by_account.get(account_id),
                            snap.get("balance"),
                            snap.get("equity"),
                            # margin_used stays NULL: the state tracker parses
                            # only ProtoOATrader.balance and derives equity
                            # from it. The broker exposes usedMargin per
                            # position, but PositionSnapshot does not carry it,
                            # and threading a new field through the reconcile
                            # path is trade-critical work this task should not
                            # take on. The column exists so it can be
                            # backfilled later without a migration.
                            None,
                            snap.get("open_pnl"),
                        )
                    self._last_balance_sample[org_id] = now
                except Exception:
                    log.exception(
                        "refresh_balances: balance sample write failed (org %s)", org_id)
```

(j) In `resync`, replace the block from `all_items.extend(items or [])` (line 1005) through the `ensure_spot_subscriptions` except (line 1070) with:

```python
            all_items.extend(items or [])
            if routing is None:
                routing = self.routing_provider()
                slave_symbols = {
                    s.account_id: symbols_by_id(s.symbols)
                    for slaves in routing.slaves_by_org.values()
                    for s in slaves
                }
            # MT5 accounts' books live in the registry, which persists them
            # itself and serves get_state directly; the tracker only ever
            # marks cTrader positions.
            mt5_ids = {aid for aid, platform in routing.platform_by_account.items()
                       if platform == 'mt5'}
            tracker = self.state_trackers.get(oid)
            if tracker is not None:
                if reconciler.master_account_id not in mt5_ids:
                    positions = [
                        StatePositionSnapshot(
                            position_id=p.position_id, symbol_id=p.symbol_id, side=p.side,
                            volume=p.volume, price=p.price, label=p.label,
                            stop_loss=p.stop_loss, take_profit=p.take_profit,
                        )
                        for p in reconciler.master_positions
                    ]
                    tracker.set_positions(reconciler.master_account_id, positions)
                # The slaves' books too: the dashboard's per-account position
                # counts and open P&L read from this tracker, and a slave
                # holding live copies must never report an empty book.
                for slave_id, slave_pos in reconciler.slave_positions.items():
                    if slave_id in mt5_ids:
                        continue
                    tracker.set_positions(slave_id, [
                        StatePositionSnapshot(
                            position_id=p.position_id, symbol_id=p.symbol_id,
                            side=p.side, volume=p.volume, price=p.price,
                            label=p.label,
                            # A slave's OWN protection, not the master's: a
                            # copy whose stop never arrived is precisely the
                            # row an operator needs to spot.
                            stop_loss=p.stop_loss, take_profit=p.take_profit,
                        )
                        for p in slave_pos
                    ])
                # Persist too, so the Positions screen survives a restart
                # instead of reading an empty AccountStateTracker until the
                # next broker reconcile lands. Best-effort: a bookkeeping
                # write must never cost the operator the resync itself.
                try:
                    master_org = routing.org_by_account.get(
                        reconciler.master_account_id)
                    if reconciler.master_account_id not in mt5_ids:
                        self.repo.upsert_positions(
                            reconciler.master_account_id, master_org,
                            reconciler.master_positions,
                            self.master_symbols_by_org.get(master_org, {}))
                        # The broker's reconcile response is the truth about
                        # what is open; anything else we hold is stale. One
                        # UPDATE per account -- close_missing_positions is
                        # per-account on this base.
                        self.repo.close_missing_positions(
                            reconciler.master_account_id,
                            [p.position_id for p in reconciler.master_positions])
                    for slave_id, slave_pos in reconciler.slave_positions.items():
                        if slave_id in mt5_ids:
                            continue
                        self.repo.upsert_positions(
                            slave_id, routing.org_by_account.get(slave_id),
                            slave_pos, slave_symbols.get(slave_id, {}))
                        self.repo.close_missing_positions(
                            slave_id, [p.position_id for p in slave_pos])
                except Exception:
                    log.exception(
                        "resync: position persistence failed (org %s)", oid)
                try:
                    yield tracker.ensure_spot_subscriptions()
                except Exception:
                    log.exception("resync: ensure_spot_subscriptions failed (org %s)", oid)
```

(k) In `_reload_inner`: after the `invalidate()` call at the top (line 1107) add

```python
        self._mt5_aliases_cache.clear()
```

; the authorize loop (lines 1126-1134) becomes:

```python
        for account in accounts:
            if account.platform == 'mt5':
                continue   # no cTrader client to (de)authorize
            client = self._client_for_account(account)
            if client is None:
                continue
            effectively_enabled = account.enabled and account.status != 'paused'
            if effectively_enabled:
                yield self._authorize_one(client, account)
            else:
                client.deauthorize_account(account.account_id)
```

; and the reconciler/tracker block (lines 1184-1200) becomes:

```python
            reconciler = self.reconcilers.get(org_id)
            if reconciler is None:
                self.reconcilers[org_id] = Reconciler(
                    clients_by_account=self._clients_by_account, repo=self.repo,
                    dispatcher=self.dispatcher, master_account_id=master_id,
                    org_id=org_id, snapshot_provider=self._mt5_snapshot_provider,
                    netting_slaves=self._mt5_netting_slaves,
                )
            elif reconciler.master_account_id != master_id:
                reconciler.master_account_id = master_id

            tracker = self.state_trackers.get(org_id)
            if tracker is None or tracker._master_account_id != master_id:
                tracker_client = _tracker_client_for(
                    self.clients, accounts, master_account, self.shards)
                self.state_trackers[org_id] = (
                    AccountStateTracker(
                        master_client=tracker_client, repo=self.repo,
                        master_account_id=master_id, symbols_by_id=org_symbols,
                    ) if tracker_client is not None else None)
```

(l) In `backfill_deals_once` (lines 1396-1397) the accounts list becomes:

```python
            accounts = sorted((a for a in self.repo.load_accounts() if a.platform != 'mt5'),
                              key=lambda a: a.account_id)
```

(m) In `_query_context` (line 1444, right after the `account is None` check) add:

```python
        if account.platform == 'mt5':
            # The lane stands in for the client: it answers the same read
            # models from the registry and the deals table.
            return self.mt5_lane, symbols_by_id(self.repo.load_symbol_cache(account_id))
```

- [ ] **Step 4: Wire the app — queries, operator actions, `get_state`**

Still in `copier/src/copier/main.py`:

(n) The three query wrappers at lines 1463-1481 become:

```python
    def get_account_details(self, account_id: int) -> defer.Deferred:
        """Full broker-side profile for one account (see engine/queries.py)."""
        if self._is_mt5(account_id):
            return defer.maybeDeferred(self.mt5_lane.details, account_id)
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.account_details(ctx[0], account_id, ctx[1]))
        return d

    def get_deal_history(self, account_id: int, from_ms: int, to_ms: int) -> defer.Deferred:
        """Deal (fill) history for one account in [from_ms, to_ms]."""
        if self._is_mt5(account_id):
            return defer.maybeDeferred(self.mt5_lane.deal_history, account_id, from_ms, to_ms)
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.deal_history(
            ctx[0], account_id, ctx[1], from_ms, to_ms))
        return d

    def get_order_history(self, account_id: int, from_ms: int, to_ms: int) -> defer.Deferred:
        """Order history for one account in [from_ms, to_ms]."""
        if self._is_mt5(account_id):
            return defer.maybeDeferred(self.mt5_lane.order_history, account_id, from_ms, to_ms)
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.order_history(
            ctx[0], account_id, ctx[1], from_ms, to_ms))
        return d
```

(o) In `place_order`, right after the dry-run check (after line 1558, before `symbol_name = params.get("symbol")`) add:

```python
        if self._is_mt5(account_id):
            if org_id is None:
                raise ValueError(f"account {account_id} not found")
            return self.mt5_lane.place_order(
                account_id, org_id, str(params.get("symbol")), side_name, type_name,
                volume_lots, limit_price, stop_price,
                validated_price("stop_loss", params.get("stop_loss")),
                validated_price("take_profit", params.get("take_profit")),
                params.get("actor_email"))
```

(p) `close_position` (lines 1650-1660): the first statement of the body becomes

```python
        if self._is_mt5(account_id):
            return self.mt5_lane.close_position(account_id, position_id, volume_lots, actor)
        client, symbols = self._query_context(account_id)
```

(q) In `amend_position_sltp` (lines 1718-1790), delete the nested `_price` function (lines 1738-1747) and replace the two lines that used it plus the `_query_context` call (lines 1749-1753) with:

```python
        sl = validated_price("stop_loss", stop_loss)
        tp = validated_price("take_profit", take_profit)

        if self._is_mt5(account_id):
            return self.mt5_lane.amend_position_sltp(account_id, position_id, sl, tp, actor)

        # Resolves the client too, so an unknown account fails here.
        _client, symbols = self._query_context(account_id)
```

(r) `cancel_order` (lines 1792-1811): before `d = defer.maybeDeferred(self._query_context, account_id)` add:

```python
        if self._is_mt5(account_id):
            return defer.maybeDeferred(self.mt5_lane.cancel_order, account_id, order_id, actor)
```

(s) `_flatten_account` (line 2020, the first statement `client, _symbols = self._query_context(account_id)`) becomes:

```python
        if self._is_mt5(account_id):
            summary = yield self.mt5_lane.flatten(account_id)
            return summary
        client, _symbols = self._query_context(account_id)
```

(t) In `get_quote` (after the `sym is None` check, line 2148) add:

```python
        if account.platform == 'mt5':
            # The terminal reports marks only for open positions; there is
            # no quote feed for a symbol nobody holds.
            return {"symbol": symbol_name, "bid": None, "ask": None}
```

(u) In `get_expected_margin`'s `go` (line 2169) and `get_trendbars`'s `go` (line 2187), right after `client, _symbols = ctx` add respectively:

```python
            if isinstance(client, MT5Lane):
                raise ValueError(f"margin estimates are not available for MT5 account {account_id}")
```

```python
            if isinstance(client, MT5Lane):
                raise ValueError(f"trendbars are not available for MT5 account {account_id}")
```

(v) `get_cash_flow` and `get_position_deals` (lines 2197-2210) become:

```python
    def get_cash_flow(self, account_id: int, from_ms: int, to_ms: int) -> defer.Deferred:
        """Deposit/withdrawal history for one account."""
        if self._is_mt5(account_id):
            return defer.maybeDeferred(self.mt5_lane.cash_flow, account_id, from_ms, to_ms)
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.cash_flow_history(
            ctx[0], account_id, from_ms, to_ms))
        return d

    def get_position_deals(self, account_id: int, position_id: int,
                           from_ms: int, to_ms: int) -> defer.Deferred:
        """Every deal of one position (the drill-down view)."""
        if self._is_mt5(account_id):
            return defer.maybeDeferred(
                self.mt5_lane.position_deals, account_id, position_id, from_ms, to_ms)
        d = defer.maybeDeferred(self._query_context, account_id)
        d.addCallback(lambda ctx: queries.position_deals(
            ctx[0], account_id, position_id, ctx[1], from_ms, to_ms))
        return d
```

(w) In `get_state`, replace lines 2351-2363 (from `state_tracker = ...` through the `slave_symbols` helper) with:

```python
        state_tracker = self.state_trackers.get(org_id)
        reconciler = self.reconcilers.get(org_id)
        accounts_snapshot = dict(state_tracker.snapshot()) if state_tracker is not None else {}
        routing = self.routing_provider()
        mt5_ids = {aid for aid, platform in routing.platform_by_account.items()
                   if platform == 'mt5' and routing.org_by_account.get(aid) == org_id}
        # MT5 accounts' balance, equity, open P&L and marked positions come
        # from the terminal's own reports, not from the state tracker.
        # A netting MASTER is marked by virtual position (the ids its copies
        # carry), its P&L split by volume share.
        netting_master = (
            reconciler.master_account_id
            if reconciler is not None and reconciler.master_account_id in mt5_ids
            and self.mt5_registry.margin_mode(reconciler.master_account_id) == "netting"
            else None)
        for account_id in mt5_ids:
            block = self.mt5_registry.account_block(
                account_id, virtual=(account_id == netting_master))
            if block is not None:
                accounts_snapshot[account_id] = block
        mappings = self.repo.mapping_rows(org_id=org_id)

        # An MT5 master's symbols are broker names; its mapping rows carry
        # the canonical names its events were translated to (ingress.py).
        master_reverse_aliases: dict[str, str] = {}
        if reconciler is not None and reconciler.master_account_id in mt5_ids:
            master_reverse_aliases = {
                broker: canonical
                for canonical, broker in self._mt5_aliases(reconciler.master_account_id).items()}

        def canonical(symbol_name: str | None) -> str | None:
            if symbol_name is None:
                return None
            return master_reverse_aliases.get(symbol_name, symbol_name)

        # Per-call memo: several copies usually belong to the same slave, and
        # load_symbol_cache() is a database round trip each time. An MT5
        # slave's cache is keyed by broker name; re-key it by the canonical
        # names the master rows carry, exactly as build_routing does.
        slave_symbol_caches: dict[int, dict] = {}

        def slave_symbols(account_id: int) -> dict:
            if account_id not in slave_symbol_caches:
                symbols = self.repo.load_symbol_cache(account_id)
                if account_id in mt5_ids:
                    symbols = mt5_symbols_by_canonical(symbols, self._mt5_aliases(account_id))
                slave_symbol_caches[account_id] = symbols
            return slave_symbol_caches[account_id]
```

; change line 2412 `if state_tracker is not None and reconciler is not None:` to

```python
        if reconciler is not None:
```

; and in the two `copies_for(...)` calls inside that block (lines 2464 and 2479) pass `canonical(symbol_name)` instead of `symbol_name`:

```python
                    'copies': copies_for('master_position_id', pos.position_id, canonical(symbol_name)),
```

```python
                    'copies': copies_for('master_order_id', order.order_id, canonical(symbol_name)),
```

- [ ] **Step 5: Wire the app — the MT5 endpoints, composition, boot**

(x) After `get_state` (before `# ---------- composition ----------`, line 2530) add:

```python
    # ---------- MT5 terminals ----------

    def mt5_hello(self, account_id: int, body: dict) -> dict:
        """A terminal introduced itself (once per EA start, in chunks). The
        symbol list becomes the account's symbol cache, its canonical names
        are auto-matched, the link row records the terminal, and the
        response tells a restarted EA where its deal watermark stands."""
        account = self._account_row(account_id)
        if account is None or account.platform != 'mt5':
            raise ValueError(f"account {account_id} is not an MT5 account")
        hello = mt5_protocol.parse_hello(body)
        now = self._clock_seconds()
        self.mt5_registry.update_from_hello(account_id, account.org_id, hello, now)
        if hello.chunk >= hello.chunks:
            self.repo.upsert_mt5_link_hello(
                account_id, login=hello.login, broker=hello.broker, server=hello.server,
                currency=hello.currency, hedging=hello.hedging, trade_mode=hello.trade_mode,
                leverage=hello.leverage, ea_version=hello.ea_version, ea_build=hello.ea_build)
            # The symbol cache changed and routing bakes it in.
            invalidate = getattr(self.routing_provider, "invalidate", None)
            if invalidate is not None:
                invalidate()
            self._auto_match_aliases(account_id, account)
            if account.role == 'master':
                # Mutated in place: the org's tracker and the service hold
                # this very dict (see _fetch_and_cache_symbols).
                org_symbols = self.master_symbols_by_org.setdefault(account.org_id, {})
                org_symbols.clear()
                org_symbols.update(symbols_by_id(self.mt5_registry.symbols_by_name(account_id)))
            # Hedging and netting are both accepted (spec "Netting accounts");
            # the mode is recorded on the link row and reported by mt5_status.
            # A reconnecting terminal clears an earlier offline/rejection status.
            if self.repo.clear_degraded(account_id):
                self.repo.log_event(
                    'slave_action', 'info',
                    {'action': 'degraded_cleared', 'reason': 'terminal reconnected'},
                    account_id=account_id, org_id=account.org_id)
            self.repo.log_event(
                'connection', 'info',
                {'action': 'mt5_hello', 'login': hello.login, 'broker': hello.broker,
                 'server': hello.server, 'hedging': hello.hedging,
                 'ea_version': hello.ea_version,
                 'symbols': len(self.mt5_registry.symbols_by_name(account_id))},
                account_id=account_id, org_id=account.org_id)
        last_ticket, _time_ms = self.repo.mt5_watermark(account_id)
        return {"last_deal_ticket": last_ticket}

    def _canonical_names_for(self, routing: OrgRouting, account) -> list[str]:
        """The names the org's master events carry: for a follower, the
        master's own symbols; for an MT5 master, the union of its cTrader
        followers' symbols."""
        org_id = account.org_id
        master_id = routing.master_by_org.get(org_id)
        if master_id is not None and master_id != account.account_id:
            names = {info.name for info in self.master_symbols_by_org.get(org_id, {}).values()}
            return sorted(names or set(self.repo.load_symbol_cache(master_id)))
        names: set[str] = set()
        for slave in routing.slaves_by_org.get(org_id, []):
            if routing.platform_by_account.get(slave.account_id) == 'mt5':
                continue
            names.update(slave.symbols)
        return sorted(names)

    def _auto_match_aliases(self, account_id: int, account) -> None:
        routing = self.routing_provider()
        canonical_names = self._canonical_names_for(routing, account)
        broker_names = list(self.mt5_registry.symbols_by_name(account_id))
        if canonical_names:
            self.repo.save_symbol_aliases(
                account_id, auto_match(canonical_names, broker_names), "auto")
        self._mt5_aliases_cache.pop(account_id, None)
        invalidate = getattr(self.routing_provider, "invalidate", None)
        if invalidate is not None:
            invalidate()

    def mt5_sync(self, account_id: int, body: dict) -> str:
        """One poll from a terminal: its report in, its next commands out.

        Order matters. The report is stored first (so everything below
        sees the terminal's current book); acks settle commands and drive
        mapping bookkeeping; deals are ingested once (watermark) and, for
        the org's master, turned into MasterEvents -- through the net
        ledger when the master is a netting account, whose dirty rows and
        virtual positions are then persisted; finally the outbox hands
        over what is due. The link row's balance/equity/last_seen_at are
        written at most once per MT5_LINK_TOUCH_INTERVAL_S per account
        (the api throttles its own write of those columns the same way).
        """
        now = self._clock_seconds()
        server_ms = int(now * 1000)
        account = self._account_row(account_id)
        if account is None or account.platform != 'mt5':
            return mt5_protocol.encode_response("STOP", server_ms, 0, [], reason="account removed")
        report = mt5_protocol.parse_sync(body)
        org_id = account.org_id
        registry = self.mt5_registry
        netting = registry.margin_mode(account_id) == "netting"
        is_master = self.routing_provider().master_by_org.get(org_id) == account_id

        was_online = registry.is_online(account_id, now)
        previous = registry.snapshot(account_id, with_labels=False)
        previous_report = registry.report(account_id)
        # A netting master's positions table holds its VIRTUAL positions,
        # written by _persist_net_ledger once the ledger has absorbed this
        # report's deals; everyone else persists the terminal's book here.
        registry.update_from_sync(account_id, org_id, report, now,
                                  persist=not (netting and is_master))
        if now - self._mt5_last_touch.get(account_id, float("-inf")) >= MT5_LINK_TOUCH_INTERVAL_S:
            self.repo.touch_mt5_link(
                account_id, balance=report.balance, equity=report.equity,
                seen_at=datetime.fromtimestamp(now, tz=timezone.utc))
            self._mt5_last_touch[account_id] = now
        if not was_online:
            self._mt5_back_online(account_id, org_id)

        outcomes = self.mt5_outbox.apply_acks(account_id, report.acks)
        # An ack is paired with the deal it produced by the deal ticket
        # (contract §2): the EA reports the deal in the same sync as the
        # ack, or the next one past MAX_DEALS_PER_SYNC.
        deals_by_ticket = {d.ticket: d for d in report.deals}
        for outcome in outcomes:
            self._apply_mt5_outcome(org_id, account_id, outcome, netting=netting,
                                    deals_by_ticket=deals_by_ticket)

        fresh = self._ingest_mt5_deals(account_id, org_id, report, previous_report)
        if is_master:
            self._mt5_master_events(account_id, org_id, dc_replace(report, deals=fresh), previous,
                                    netting=netting)
            if netting:
                self._persist_net_ledger(account_id)
        elif account.role == 'slave' and (fresh or outcomes):
            # Called on acks too: an acked deal not in this report is
            # remembered so the next report does not book it again.
            self._mt5_slave_deals(org_id, account_id, fresh, outcomes, netting=netting)

        commands = self.mt5_outbox.deliverable(account_id, now)
        if fresh or outcomes:
            self.request_resync(org_id)
        return mt5_protocol.encode_response(
            "OK", server_ms, mt5_protocol.DEFAULT_POLL_MS, commands)

    def _mt5_back_online(self, account_id: int, org_id: int) -> None:
        if self.repo.clear_degraded(account_id):
            self.repo.log_event('connection', 'info', {'action': 'mt5_online'},
                                account_id=account_id, org_id=org_id)

    def _apply_mt5_outcome(self, org_id: int, account_id: int, outcome,
                           netting: bool = False, deals_by_ticket: dict | None = None) -> None:
        """One settled command -> the same mapping bookkeeping a cTrader
        execution event drives, plus the account's degraded status.

        `deals_by_ticket` is this report's deals: an ack is paired with the
        deal it produced (AckOutcome.deal), which on a netting follower
        says what the fill did to the net position (its entry) and names
        the position when the ack could not (pos 0: the fill emptied it)."""
        deal = (deals_by_ticket or {}).get(outcome.deal) if outcome.deal else None
        if not outcome.ok:
            reason = f"terminal rejected {outcome.kind}: {outcome.message}"
            # A refused close or amend leaves the copy open: only a refused
            # open/place_pending fails its mapping. (A netting follower's
            # close is reported as kind "close" under the mapping's coid.)
            coid = outcome.client_order_id if outcome.kind in ('open', 'place_pending') else None
            self.service.handle_slave_rejection(org_id, account_id, coid, reason)
            self.repo.set_account_status(account_id, 'degraded', reason)
            return
        if outcome.kind == 'open':
            # The EA acks the position the fill landed in. On a netting
            # follower that is the net position the terminal shows AFTER
            # the fill -- 0 when the fill emptied it (plan 04 DoOpen) -- and
            # the deal still names it (contract §2).
            ticket = outcome.position or (deal.position if deal is not None else None)
            if not ticket:
                self.repo.log_event(
                    'slave_action', 'warning',
                    {'action': 'mt5_open_ack_without_position', 'command_id': outcome.command_id,
                     'deal': outcome.deal},
                    account_id=account_id, org_id=org_id)
            else:
                pos = self.mt5_registry.position(account_id, ticket)
                self.service.handle_slave_fill(org_id, SlaveFill(
                    account_id=account_id, client_order_id=outcome.client_order_id,
                    position_id=ticket, filled_volume=outcome.volume or 0,
                    fill_price=outcome.price, closed_volume=None,
                    # An operator's open carries no client_order_id; every copy does.
                    label=MANUAL_ORDER_LABEL if outcome.client_order_id is None else "",
                    order_id=outcome.order,
                    stop_loss=pos.stop_loss if pos is not None else None,
                    take_profit=pos.take_profit if pos is not None else None))
                if netting:
                    # The fill went into the symbol's single net position and
                    # the deal's entry says what it did to it: IN grew it, OUT
                    # reduced or emptied it, INOUT flipped it. An opposite-side
                    # COPY (spec "Opposite positions": the master holds BUY and
                    # opens SELL) is still a copy -- its mapping is active on
                    # the net ticket, and the older copies it netted against
                    # are the master's to close, not this fill's: the deal is
                    # settled by this ack and never reaches the FIFO reduction
                    # (_mt5_slave_deals). Recorded so the operator can see why
                    # the net position shrank without any copy closing.
                    if (outcome.client_order_id and deal is not None
                            and deal.entry in CLOSE_ENTRIES):
                        self.repo.log_event(
                            'slave_action', 'info',
                            {'action': 'mt5_netting_opposite_copy',
                             'client_order_id': outcome.client_order_id, 'position': ticket,
                             'entry': deal.entry, 'volume': outcome.volume or 0,
                             'nets_against': self._copies_sharing(
                                 org_id, account_id, ticket, outcome.client_order_id)},
                            account_id=account_id, org_id=org_id)
                    self._record_netting_override(
                        org_id, account_id, ticket, outcome.client_order_id)
        elif outcome.kind == 'close' and outcome.client_order_id:
            # A netting follower's close of ONE copy: the outbox queued it as
            # an opposite-side open under the mapping's coid + ':close' and
            # reports its ack as this close. Reduce exactly that mapping --
            # by its coid alone (Task 4): the ack's ticket is the net
            # position AFTER the fill, 0 when this close emptied it. The
            # OUT/INOUT deal it produced is settled by this ack and skipped
            # by ticket in _mt5_slave_deals (AckOutcome.deal).
            self.service.handle_slave_fill(org_id, SlaveFill(
                account_id=account_id, client_order_id=outcome.client_order_id,
                position_id=outcome.position or (deal.position if deal is not None else 0),
                filled_volume=outcome.volume or 0,
                fill_price=outcome.price, closed_volume=outcome.volume or 0, label=""))
        elif outcome.kind == 'place_pending':
            self.service.handle_slave_order_accepted(
                org_id, account_id, outcome.client_order_id, outcome.order)
        elif outcome.kind == 'cancel_pending' and outcome.order is not None:
            self.service.handle_slave_order_cancelled(org_id, account_id, outcome.order)
        else:
            # close / amend / amend_pending: the deal or the next report says
            # what changed; the ack is only the receipt.
            self.repo.log_event(
                'slave_action', 'info',
                {'action': f'mt5_{outcome.kind}_done', 'command_id': outcome.command_id,
                 'position': outcome.position, 'order': outcome.order},
                account_id=account_id, org_id=org_id)
            if netting and outcome.kind == 'amend' and outcome.position is not None:
                self._record_netting_override(org_id, account_id, outcome.position, None)
        if self.repo.clear_degraded(account_id):
            self.repo.log_event(
                'slave_action', 'info',
                {'action': 'degraded_cleared', 'reason': 'terminal executed a command'},
                account_id=account_id, org_id=org_id)

    def _record_netting_override(self, org_id: int, account_id: int, position: int,
                                 set_by: str | None) -> None:
        """One stop and one target per symbol on a netting account: the
        latest open or amend sets them for every copy sharing the net
        position (spec "Netting accounts"). Recorded whenever other active
        copies share the ticket, so the operator can see whose levels they
        now carry. `set_by` is the coid of the copy that set them, None for
        an amend (which names no copy)."""
        others = self._copies_sharing(org_id, account_id, position, set_by)
        if not others:
            return
        pos = self.mt5_registry.position(account_id, position)
        self.repo.log_event(
            'slave_action', 'info',
            {'action': 'mt5_netting_protection_override', 'position': position,
             'set_by': set_by, 'overrides': others,
             'stop_loss': pos.stop_loss if pos is not None else None,
             'take_profit': pos.take_profit if pos is not None else None,
             'detail': 'one stop and one target per symbol on a netting account: the latest '
                       'open or amend sets them for every copy sharing the net position'},
            account_id=account_id, org_id=org_id)

    def _copies_sharing(self, org_id: int, account_id: int, position: int,
                        except_coid: str | None) -> list[int]:
        """Master ids of the OTHER active copies on a net ticket (every copy
        on a symbol shares it on a netting follower), sorted."""
        return sorted({
            m['master_position_id'] for m in self.repo.mapping_rows(org_id=org_id)
            if m['slave_account_id'] == account_id and m['slave_position_id'] == position
            and m['status'] == 'active' and m['master_position_id']
            and m['client_order_id'] != except_coid})

    def _ingest_mt5_deals(self, account_id: int, org_id: int, report, previous_report) -> list:
        """Store the deals past the watermark, estimate balance_after for
        them, advance the watermark. Returns the fresh deals (time order).
        Entry prices for the close rows come from the position as it stood
        in the previous report (a fully closed position is gone from this
        one) or in this one."""
        last_ticket, _time_ms = self.repo.mt5_watermark(account_id)
        fresh = sorted((d for d in report.deals if d.ticket > last_ticket),
                       key=lambda d: (d.time_ms, d.ticket))
        if not fresh:
            return []
        symbols = self.mt5_registry.symbols_by_name(account_id)
        entry_prices = {p.ticket: p.open_price
                        for p in (previous_report.positions if previous_report is not None else [])}
        entry_prices.update({p.ticket: p.open_price for p in report.positions})
        rows = deal_rows(fresh, report.balance, symbols, entry_prices.get)
        inserted = self.repo.upsert_mt5_deals(account_id, org_id, rows)
        self.repo.set_mt5_watermark(
            account_id, max(d.ticket for d in fresh), max(d.time_ms for d in fresh))
        # get_analytics reads deal_backfill_state to say whether history is
        # complete; an MT5 account's history is whatever the terminal has
        # sent, complete from its first deal.
        self.repo.set_backfill_state(
            account_id, min(d.time_ms for d in fresh), max(d.time_ms for d in fresh),
            exhausted=True)
        log.info("mt5 account %s: %d new deal(s) ingested", account_id, inserted)
        return fresh

    def _mt5_master_events(self, account_id: int, org_id: int, report, previous,
                           netting: bool = False) -> None:
        """The org's MT5 master reported: derive and act on its events, then
        stamp the master half of the slippage measurement onto the copies.
        A netting master's deals go through its ledger (virtual positions,
        ids = deal tickets)."""
        reverse = {broker: canonical
                   for canonical, broker in self._mt5_aliases(account_id).items()}
        symbols = self.mt5_registry.symbols_by_name(account_id)
        ledger = self.mt5_registry.net_ledger(account_id) if netting else None
        for event in master_events_from_report(report, previous, reverse, symbols, ledger=ledger):
            try:
                self.service.act_on_master_event(org_id, account_id, event, source="mt5")
            except Exception as e:
                # The sync must answer the terminal whatever one event did;
                # the failure is logged exactly as handle_execution logs its own.
                self.repo.log_event(
                    'connection', 'error',
                    {'action': 'event_processing_failed', 'account_id': account_id,
                     'error': f"{type(e).__name__}: {e}", 'error_type': type(e).__name__},
                    org_id=org_id)
        for d in report.deals:
            if d.deal_type not in TRADE_TYPES:
                continue
            # On a netting master the copies are keyed by the deal ticket
            # (the virtual id), a reversal's remainder included.
            if netting and d.entry in ("IN", "INOUT"):
                self.repo.record_master_fill(org_id, d.ticket, d.price, d.time_ms)
            elif not netting and d.entry == "IN":
                self.repo.record_master_fill(org_id, d.position, d.price, d.time_ms)

    def _persist_net_ledger(self, account_id: int) -> None:
        """A netting master's ledger after this report: dirty virtual
        positions to mt5_net_ledger (a restart rebuilds the ledger from
        them), then the positions table from the virtual book (what the
        Positions page and a restart read)."""
        ledger = self.mt5_registry.net_ledger(account_id)
        upserts, deleted = ledger.dirty_rows()
        if upserts:
            self.repo.upsert_net_ledger(account_id, [v.row() for v in upserts])
        if deleted:
            self.repo.delete_net_ledger(account_id, deleted)
        if upserts or deleted:
            self.mt5_registry.persist_virtual_positions(account_id)

    def _mt5_slave_deals(self, org_id: int, account_id: int, deals, outcomes,
                         netting: bool = False) -> None:
        """A follower terminal's own deals: an OUT on a mapped position (a
        stop hit, the owner closing by hand) reduces the mapping -- on a
        netting follower the copies sharing the net position, oldest first
        (reduce_position_mappings_fifo); an IN not already activated by an
        ack is a pending copy filling, an operator's order, or an unmatched
        fill.

        A deal produced by a command that NAMES A COPY is never the
        terminal's own doing, so it is skipped by its ticket: the ack did
        the bookkeeping already -- a copy's open activated its mapping
        whatever the fill did to a net position (grew, reduced, emptied or
        flipped it: an opposite-side copy is a copy, not a close of the
        older ones), a ':close' reduced its mapping under its coid. The
        tickets are kept per account until the terminal reports the deal,
        in the same sync as the ack or the next one (MAX_DEALS_PER_SYNC).
        An operator's own open on the net position is NOT in the set: its
        OUT/INOUT deal really did take volume from the copies, and goes
        through the FIFO reduction like a close by hand."""
        settled = self._mt5_settled_deals.setdefault(account_id, set())
        settled.update(o.deal for o in outcomes
                       if o.ok and o.deal and o.client_order_id and o.kind in ('open', 'close'))
        acked_positions = {o.position for o in outcomes if o.ok and o.kind == 'open' and o.position}
        known = {m['slave_position_id'] for m in self.repo.mapping_rows(org_id=org_id)
                 if m['slave_account_id'] == account_id and m['slave_position_id']}
        for d in deals:
            if d.deal_type not in TRADE_TYPES:
                continue
            if d.ticket in settled:
                settled.discard(d.ticket)
                continue
            if d.entry == "IN":
                # On a netting follower every copy on a symbol shares the net
                # ticket, so an add to a mapped ticket is never a fill of its own.
                if d.position in known or d.position in acked_positions:
                    continue
                pos = self.mt5_registry.position(account_id, d.position)
                fill = SlaveFill(
                    account_id=account_id, client_order_id=None, position_id=d.position,
                    filled_volume=d.volume, fill_price=d.price, closed_volume=None,
                    label=MANUAL_ORDER_LABEL if d.comment == MANUAL_ORDER_LABEL else "",
                    order_id=d.order or None,
                    stop_loss=pos.stop_loss if pos is not None else None,
                    take_profit=pos.take_profit if pos is not None else None)
            elif d.entry in CLOSE_ENTRIES:
                if netting:
                    for row in self.repo.reduce_position_mappings_fifo(
                            account_id, d.position, d.volume):
                        self.repo.log_event(
                            'slave_action', 'info',
                            {'action': 'position_closed', 'slave_position_id': d.position,
                             'client_order_id': row['client_order_id'],
                             'closed_volume': row['closed_volume']},
                            account_id=account_id, org_id=org_id)
                    continue
                fill = SlaveFill(
                    account_id=account_id, client_order_id=None, position_id=d.position,
                    filled_volume=d.volume, fill_price=d.price, closed_volume=d.volume,
                    label="", order_id=d.order or None)
            else:
                continue
            try:
                self.service.handle_slave_fill(org_id, fill)
            except Exception:
                log.exception("mt5 account %s: deal %s bookkeeping failed", account_id, d.ticket)

    def mt5_status(self, account_id: int) -> dict:
        if self._account_row(account_id) is None:
            raise ValueError(f"account {account_id} not found")
        now = self._clock_seconds()
        last = self.mt5_registry.last_seen(account_id)
        return {
            "online": self.mt5_registry.is_online(account_id, now),
            "last_seen_at": (datetime.fromtimestamp(last, tz=timezone.utc)
                             .isoformat(timespec='seconds') if last is not None else None),
            "pending_commands": self.mt5_outbox.pending_count(account_id),
            "hedging": self.mt5_registry.hedging(account_id),
        }

    def check_mt5_offline(self) -> None:
        """LoopingCall body (MT5_OFFLINE_CHECK_INTERVAL_S): an MT5 account
        whose terminal has not reported for OFFLINE_AFTER_S reads degraded
        "terminal offline since ..."; its next report clears it
        (_mt5_back_online). A paused account is the operator's and is left
        alone. Never raises: a LoopingCall whose Deferred fails stops."""
        try:
            now = self._clock_seconds()
            for account in self.repo.load_accounts():
                if account.platform != 'mt5' or account.status == 'paused':
                    continue
                last = self.mt5_registry.last_seen(account.account_id)
                if last is None or self.mt5_registry.is_online(account.account_id, now):
                    continue
                if (account.status == 'degraded'
                        and (account.last_error or '').startswith('terminal offline')):
                    continue
                since = datetime.fromtimestamp(last, tz=timezone.utc).isoformat(timespec='seconds')
                self.repo.set_account_status(
                    account.account_id, 'degraded', f"terminal offline since {since}")
                self.repo.log_event(
                    'connection', 'warning', {'action': 'mt5_offline', 'since': since},
                    account_id=account.account_id, org_id=account.org_id)
        except Exception:
            log.exception("mt5 offline check failed")
```

(y) After `_build_send_for_account` (after line 2620) add:

```python


def _build_mt5_targets(routing_provider: Callable[[], OrgRouting]):
    """account_id -> {symbol_id: broker symbol name} for an MT5 slave, None
    for anything else. Read off the cached routing (the slave's symbols
    are already there, keyed by canonical name AND broker name), so the
    dispatcher pays no database round trip per intent."""
    def mt5_targets(account_id: int):
        routing = routing_provider()
        if routing.platform_by_account.get(account_id) != 'mt5':
            return None
        for slaves in routing.slaves_by_org.values():
            for slave in slaves:
                if slave.account_id == account_id:
                    return {info.symbol_id: info.name for info in slave.symbols.values()}
        return {}

    return mt5_targets


def _build_mt5_snapshot_provider(routing_provider: Callable[[], OrgRouting], registry: MT5Registry):
    """The reconciler's snapshot_provider: an MT5 account's book from the
    registry (empty before its first report -- the terminal is the only
    source, and asking a cTrader client would answer for the wrong
    account), None for a cTrader account. A netting MASTER's book is its
    ledger's virtual positions: the ids its followers' mappings carry, so
    drift lines up by virtual id."""
    def provider(account_id: int):
        routing = routing_provider()
        if routing.platform_by_account.get(account_id) != 'mt5':
            return None
        org_id = routing.org_by_account.get(account_id)
        virtual = (routing.master_by_org.get(org_id) == account_id
                   and registry.margin_mode(account_id) == "netting")
        return registry.snapshot(account_id, virtual=virtual) or ([], [])

    return provider


def _build_mt5_netting_slaves(routing_provider: Callable[[], OrgRouting], registry: MT5Registry):
    """The reconcilers' netting_slaves (Task 12b): the MT5 FOLLOWERS whose
    terminal said hello as a netting account -- the accounts whose net
    positions compute_drift compares with the signed sum of their copies.
    A master is not a follower; an account with no hello yet has no mode
    and is left to the existence checks until it reports."""
    def netting_slaves() -> set[int]:
        routing = routing_provider()
        return {
            account_id for account_id, platform in routing.platform_by_account.items()
            if platform == 'mt5'
            and routing.master_by_org.get(routing.org_by_account.get(account_id)) != account_id
            and registry.margin_mode(account_id) == "netting"}

    return netting_slaves


def _tracker_client_for(clients: dict, accounts, master_account, shards: int):
    """The connection an org's state tracker subscribes quotes on: the
    master's own for a cTrader master; for an MT5 master, any cTrader
    account of the org (the MT5 master's own book is served by the
    registry); None when the org has no cTrader account at all."""
    candidates = [master_account] + [
        a for a in accounts
        if a.org_id == master_account.org_id and a.account_id != master_account.account_id]
    for a in candidates:
        if a.platform == 'mt5':
            continue
        client = clients.get(a.is_live, {}).get(a.account_id % shards)
        if client is not None:
            return client
    return None
```

(z) Replace `build_app` (lines 2623-2708) with:

```python
def build_app(
    repo: Repo,
    token_store: TokenStore,
    client_factory: Callable[[bool], CTraderClient],
    shards: int = DEFAULT_SHARDS,
    clock=None,
) -> CopierApp:
    """Build a fully-wired CopierApp, with one engine per org that has a master.

    Construction order matters: repo -> token_store -> clients -> routing
    -> MT5 registry/outbox -> dispatcher (with a real send_for_account and a
    real outbox from the very first line, never a None/placeholder patched
    in afterward) -> service -> per-org reconcilers (with a real dispatcher
    and snapshot provider) -> per-org state_trackers -> CopierApp.
    """
    accounts = repo.load_accounts()
    # MT5 accounts are reached through their own polls: no cTrader client
    # is built for an environment only they occupy.
    envs_needed = sorted({a.is_live for a in accounts if a.platform != 'mt5'})

    clients: dict[bool, dict[int, CTraderClient]] = {}
    for is_live in envs_needed:
        clients[is_live] = {shard: client_factory(is_live) for shard in range(shards)}

    clients_by_account = _build_clients_by_account(repo, clients, shards)
    send_for_account = _build_send_for_account(clients_by_account)

    # Cached for up to a second: routing used to be rebuilt from the
    # database on every event, which put ~200ms of queries and symbol
    # parsing in front of every copy. reload() invalidates it, so
    # control-plane changes still apply immediately; anything else is at
    # most TTL-stale, which the freshness contract (edits apply on the
    # next event) comfortably absorbs.
    routing_provider = RoutingCache(
        lambda: build_routing(repo.load_accounts(), repo.load_symbol_cache,
                              repo.load_symbol_aliases),
        clock=clock,
    )

    mt5_registry = MT5Registry(repo, clock=clock)
    # The outbox asks the registry for an account's margin mode: a netting
    # follower's close is an opposite-side open (copier/mt5/outbox.py).
    mt5_outbox = MT5Outbox(repo, clock=clock, margin_mode=mt5_registry.margin_mode)
    mt5_snapshot_provider = _build_mt5_snapshot_provider(routing_provider, mt5_registry)
    mt5_netting_slaves = _build_mt5_netting_slaves(routing_provider, mt5_registry)

    bucket = TokenBucket(clock=clock)
    dispatcher = Dispatcher(
        send_for_account=send_for_account, repo=repo, bucket=bucket, clock=clock,
        mt5_targets=_build_mt5_targets(routing_provider), mt5_outbox=mt5_outbox)

    master_symbols_by_org: dict[int, dict] = {}

    service = CopierService(
        repo=repo, dispatcher=dispatcher, routing_provider=routing_provider,
        master_symbols_by_org=master_symbols_by_org, clock=clock,
    )

    initial_routing = build_routing(accounts, repo.load_symbol_cache, repo.load_symbol_aliases)
    reconcilers: dict[int, Reconciler] = {}
    state_trackers: dict[int, AccountStateTracker | None] = {}
    for org_id, master_id in initial_routing.master_by_org.items():
        master_account = next(a for a in accounts if a.account_id == master_id)
        reconcilers[org_id] = Reconciler(
            clients_by_account=clients_by_account, repo=repo,
            dispatcher=dispatcher, master_account_id=master_id, org_id=org_id,
            snapshot_provider=mt5_snapshot_provider, netting_slaves=mt5_netting_slaves,
        )
        # The inner dict is created here and mutated in place from then on, so
        # the tracker and the service keep seeing this org's current symbols.
        org_symbols = master_symbols_by_org.setdefault(org_id, {})
        tracker_client = _tracker_client_for(clients, accounts, master_account, shards)
        state_trackers[org_id] = (
            AccountStateTracker(
                master_client=tracker_client, repo=repo,
                master_account_id=master_id, symbols_by_id=org_symbols,
            ) if tracker_client is not None else None)

    app = CopierApp(
        repo=repo, token_store=token_store, clients=clients, service=service,
        reconcilers=reconcilers, state_trackers=state_trackers,
        dispatcher=dispatcher, client_factory=client_factory, shards=shards,
        master_symbols_by_org=master_symbols_by_org,
        routing_provider=routing_provider, clients_by_account=clients_by_account,
        mt5_registry=mt5_registry, mt5_outbox=mt5_outbox,
        clock=clock,
    )

    # Service is constructed before the app (see module docstring on wiring
    # order), so the position-change hook is attached here instead of via
    # its ctor: any fill/close/cancel refreshes /state within ~1s.
    service.on_positions_changed = app.request_resync
    service.state_tracker_provider = app.state_tracker_for_account

    # Wire every push-event consumer to EVERY client (all shards, both
    # environments) -- slave shards must deliver execution events too, and any
    # client observing a token invalidation must trigger an immediate refresh.
    for env_clients in clients.values():
        for client in env_clients.values():
            app.wire_client(client)

    return app
```

(aa) In `boot`, before `# Drain whatever the writer still holds before the process exits` (line 2912) add:

```python
    # An MT5 terminal that stops polling is noticed within 5 s and shown
    # as "terminal offline since ..." until it reports again.
    mt5_offline_call = task.LoopingCall(app.check_mt5_offline)
    mt5_offline_call.clock = reactor_
    app.mt5_offline_call = mt5_offline_call

    def _start_mt5_offline_loop():
        d = mt5_offline_call.start(MT5_OFFLINE_CHECK_INTERVAL_S, now=False)
        d.addErrback(lambda f: log.error("mt5 offline check loop stopped: %s", f))

    reactor_.callWhenRunning(_start_mt5_offline_loop)

```

- [ ] **Step 6: Update the boot test's loop count**

In `copier/tests/unit/test_main.py`, replace lines 449-453 with:

```python
    # startup + token-refresh loop + balance-refresh loop (N9) + resync
    # loop + cutoff-reminder loop + commission-refresh loop +
    # partition-maintenance loop + deal-backfill loop + MT5 offline check
    # were scheduled, not run inline (no reactor loop here).
    assert len(fake_reactor.callWhenRunning_calls) == 9
    assert app.mt5_offline_call.interval is None       # not started until the reactor runs
```

- [ ] **Step 7: Run the tests to verify they pass**

Run DB `tests/unit/test_main_mt5.py`.
Expected: `30 passed`.

Run DB `tests/unit/test_main.py`, DB `tests/unit/test_control.py`, DB `tests/unit/test_control_queries.py`, DB `tests/unit/test_control_actions.py`, DB `tests/unit/test_cutoff_reminders.py`, DB `tests/unit/test_drift_dismissals.py`, DB `tests/unit/test_hot_path_queries.py`.
Expected: all pass. Then run the whole unit suite (`tests/unit -q`, ~9 minutes) and DB `tests/integration/test_copier_e2e.py`, DB `tests/integration/test_manual_actions.py`.
Expected: all pass — every branch added is behind `platform == 'mt5'` / `_is_mt5`, and a fleet without MT5 accounts takes exactly the old paths.

- [ ] **Step 8: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/main.py copier/tests/unit/test_main_mt5.py copier/tests/unit/test_main.py && git commit -m "feat(copier): the MT5 lane in CopierApp -- hello, sync, status, actions, state

mt5_hello fills the symbol cache and auto-matches aliases; mt5_sync
stores the report, settles acks into mapping bookkeeping, ingests deals
once with an estimated balance_after, turns an MT5 master's report into
MasterEvents, and hands the outbox's due commands back. Operator actions
and the read models branch to the lane; get_state serves MT5 accounts
from the registry; the cTrader auth, symbol, balance and backfill loops
skip MT5 accounts; a 5 s timer marks a silent terminal offline. Netting
accounts are accepted in both roles: an ack is paired with its deal by
ticket, so a follower's opposite-side copy activates on the net ticket
without closing the older copies, a close ack reduces the one mapping
it names (by coid, the ticket being 0 when the close emptied the net
position) and only the terminal's own closes reduce copies oldest-first
(the shared stop/target override is recorded); a master's deals run
through the net ledger, persisted with its virtual positions.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Control endpoints — `POST /mt5/hello`, `POST /mt5/sync`, `GET /mt5/status`

**Files:**
- Modify: `copier/src/copier/engine/control.py` — module docstring `:17-44` (route list), helpers after `_write_json` `:58-64`, new resources before `class RootResource` `:542`, `RootResource.__init__` `:545-568`
- Test: `copier/tests/unit/test_control_mt5.py`

**Interfaces:**
- Consumes: `CopierApp.mt5_hello/mt5_sync/mt5_status` (Task 15).
- Produces (contract §2 "engine/control.py", used by the api plan and Task 17): `POST /mt5/hello {"account_id", "org_id", "report"} -> 200 JSON {"last_deal_ticket"}`; `POST /mt5/sync {"account_id", "org_id", "report"} -> 200 text/plain` (the EA response text verbatim); `GET /mt5/status?account_id=N -> 200 JSON`. A bad body or report (and, for hello, an account the copier does not know) is a 400 JSON `{"error": ...}`, which the api passes through to the terminal unchanged: only an unreachable/timed-out copier or a 5xx becomes a `RETRY` line (contract §3; plan 02 Task 6 `_door` steps 5–7), so a rejected report is never resent as-is. An unknown account on `/mt5/sync` is not a 400 — `mt5_sync` answers it with a 200 `STOP` line (Task 15). Resources `Mt5HelloResource`, `Mt5SyncResource`, `Mt5StatusResource`, `Mt5Resource`; helpers `_write_text(request, text, code=None)`, `_mt5_body(body) -> tuple[int, dict]`.

- [ ] **Step 1: Write the failing test**

Create `copier/tests/unit/test_control_mt5.py`:

```python
"""The /mt5/* control routes (engine/control.py), driven with DummyRequest
against a fake app whose method signatures mirror CopierApp's."""

import json
from io import BytesIO

from twisted.web.test.requesthelper import DummyRequest

from copier.engine.control import (
    Mt5HelloResource, Mt5StatusResource, Mt5SyncResource, RootResource)


class _Mt5App:
    def __init__(self):
        self.calls = []

    def mt5_hello(self, account_id, body):
        self.calls.append(("hello", account_id, body))
        return {"last_deal_ticket": 700001}

    def mt5_sync(self, account_id, body):
        self.calls.append(("sync", account_id, body))
        if body.get("seq") == "bad":
            raise ValueError("sync: field 'seq' is not a int")
        return "OK\t1\t250\nCMD\t88\tclose\t669607966\t0\n"

    def mt5_status(self, account_id):
        self.calls.append(("status", account_id))
        return {"online": True, "last_seen_at": "2026-09-07T10:00:00+00:00",
                "pending_commands": 1, "hedging": True}


def _post(resource, path, body):
    request = DummyRequest(path)
    request.method = b"POST"
    request.content = BytesIO(json.dumps(body).encode())
    resource.render_POST(request)
    return request


def _written(request):
    return b"".join(request.written)


def test_hello_route_forwards_the_report_and_answers_json():
    app = _Mt5App()
    request = _post(Mt5HelloResource(app), [b"mt5", b"hello"],
                    {"account_id": 1000000000001, "org_id": 1, "report": {"login": 5}})
    assert app.calls == [("hello", 1000000000001, {"login": 5})]
    assert json.loads(_written(request)) == {"last_deal_ticket": 700001}
    assert request.responseHeaders.getRawHeaders(b"Content-Type") == [b"application/json"]


def test_sync_route_answers_the_ea_text_verbatim():
    app = _Mt5App()
    request = _post(Mt5SyncResource(app), [b"mt5", b"sync"],
                    {"account_id": 7, "org_id": 1, "report": {"seq": 1}})
    assert app.calls == [("sync", 7, {"seq": 1})]
    assert _written(request) == b"OK\t1\t250\nCMD\t88\tclose\t669607966\t0\n"
    assert request.responseHeaders.getRawHeaders(b"Content-Type") == [b"text/plain; charset=utf-8"]
    assert request.responseCode in (None, 200)


def test_sync_route_maps_a_bad_report_to_400_json():
    app = _Mt5App()
    request = _post(Mt5SyncResource(app), [b"mt5", b"sync"],
                    {"account_id": 7, "report": {"seq": "bad"}})
    assert request.responseCode == 400
    assert "seq" in json.loads(_written(request))["error"]


def test_routes_require_account_id_and_an_object_report():
    bodies = [{"report": {}}, {"account_id": "x", "report": {}},
              {"account_id": 7, "report": "nope"}, {"account_id": 7}]
    for body in bodies:
        request = _post(Mt5SyncResource(_Mt5App()), [b"mt5", b"sync"], body)
        assert request.responseCode == 400, body
        request = _post(Mt5HelloResource(_Mt5App()), [b"mt5", b"hello"], body)
        assert request.responseCode == 400, body


def test_status_route_parses_the_query_string():
    app = _Mt5App()
    request = DummyRequest([b"mt5", b"status"])
    request.method = b"GET"
    request.addArg(b"account_id", b"7")
    Mt5StatusResource(app).render_GET(request)
    assert app.calls == [("status", 7)]
    assert json.loads(_written(request))["pending_commands"] == 1

    missing = DummyRequest([b"mt5", b"status"])
    missing.method = b"GET"
    Mt5StatusResource(app).render_GET(missing)
    assert missing.responseCode == 400


def test_root_mounts_the_mt5_tree():
    root = RootResource(_Mt5App())
    mt5 = root.children[b"mt5"]
    assert set(mt5.children) == {b"hello", b"sync", b"status"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run PURE `tests/unit/test_control_mt5.py`.
Expected: FAIL at collection with `ImportError: cannot import name 'Mt5HelloResource' from 'copier.engine.control'`.

- [ ] **Step 3: Implement**

In `copier/src/copier/engine/control.py`:

(a) Add to the module docstring's route list (after the `/close-all` lines, before the closing `"""` at line 45):

```python
- POST /mt5/hello, POST /mt5/sync: an MT5 terminal's reports, proxied by
  the api with the account it resolved from the key, body
  {"account_id": int, "org_id": int, "report": {...}}; hello answers JSON
  {"last_deal_ticket"}, sync answers the EA's text/plain command lines
- GET /mt5/status?account_id: online flag, last report time, queue depth
```

(b) After `_read_json_body` (after line 68) add:

```python


def _write_text(request, text: str, code: int | None = None) -> None:
    if code is not None:
        request.setResponseCode(code)
    request.setHeader(b"Content-Type", b"text/plain; charset=utf-8")
    request.write(text.encode())
    request.finish()


def _mt5_body(body: dict) -> tuple[int, dict]:
    """(account_id, report) from an /mt5/* body. The api resolved
    account_id from the terminal's key and stamps org_id for its own log;
    the copier re-derives the org from the accounts table, so org_id is
    accepted and not trusted."""
    account_id = body.get("account_id")
    if account_id is None:
        raise ValueError("account_id required")
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        raise ValueError("account_id must be an integer")
    report = body.get("report")
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    return account_id, report
```

(c) Before `class RootResource` (line 542) add:

```python
class Mt5HelloResource(_JsonResource):
    """POST /mt5/hello: a terminal's hello (in chunks), answered with the
    deal watermark it should resume from."""

    def _handle(self, request, body):
        account_id, report = _mt5_body(body)
        return self.app.mt5_hello(account_id, report)


class Mt5SyncResource(_JsonResource):
    """POST /mt5/sync: one sync report in, the EA's text response out --
    text/plain, the command lines the terminal executes. A ValueError
    (bad body or report) is still a JSON 400 through
    _JsonResource.render_POST, and the api passes that 400 through to the
    terminal as is -- only an unreachable copier or a 5xx becomes a RETRY
    line (contract section 3) -- so a rejected report is not resent
    unchanged. An unknown account is a 200 STOP line, not a 400."""

    def _render(self, request):
        body = _read_json_body(request)
        account_id, report = _mt5_body(body)
        _write_text(request, self.app.mt5_sync(account_id, report))
        return server.NOT_DONE_YET


class Mt5StatusResource(_JsonResource):
    """GET /mt5/status?account_id=N: online flag, last report, queue depth."""

    def _handle(self, request, body):
        return self.app.mt5_status(_int_arg(request, b"account_id"))


class Mt5Resource(resource.Resource):
    """Parent resource for /mt5/{hello,sync,status}."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"hello", Mt5HelloResource(app))
        self.putChild(b"sync", Mt5SyncResource(app))
        self.putChild(b"status", Mt5StatusResource(app))


```

(d) In `RootResource.__init__`, after `self.putChild(b"close-all", CloseAllResource(app))` (line 568) add:

```python
        self.putChild(b"mt5", Mt5Resource(app))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run PURE `tests/unit/test_control_mt5.py`.
Expected: `6 passed`.

Run DB `tests/unit/test_control.py`, DB `tests/unit/test_control_actions.py`, DB `tests/unit/test_control_queries.py`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/engine/control.py copier/tests/unit/test_control_mt5.py && git commit -m "feat(control): /mt5/hello, /mt5/sync (text/plain) and /mt5/status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 17: The fake EA and the end-to-end scenarios

**Files:**
- Create: `copier/src/copier/testing/fake_ea.py`
- Test: `copier/tests/integration/test_mt5_bridge.py`

**Interfaces:**
- Consumes: `copier.mt5.protocol` (Task 2), `CopierApp.mt5_hello/mt5_sync` (Task 15), `FakeCTraderServer`, `build_app`, and the helpers of `tests/integration/test_copier_e2e.py` (`_seed_org`, `_insert_accounts`, `_teardown`, `_wait_until`, `_fire_and_forget`, `_market_order`, `_close_position`, `_amend_sltp`, `_events`, and the constants).
- Produces (contract §2 "Fake EA"): `FakeEA(base_url=None, key=None, account_id=None, *, hello_fn=None, sync_fn=None, login=12345678, broker="Fake Broker Ltd", server="Fake-Demo", currency="USD", mode="hedging", symbols=None, prices=None, balance=10000.0, magic=20260907)` — `mode` is `"hedging"` or `"netting"` (the hello says `hedging: mode == "hedging"`) — with `tick() -> list[str] | None` (one hello-if-needed + sync, executing every command), the book (`positions`, `orders`, `deals`, `balance`, `executed`), terminal-side actions `market_order(symbol, side, lots, sl=0.0, tp=0.0, comment="") -> ticket` (0 when a netting fill emptied the net position, as the EA's ack says), `close_by_terminal(ticket, lots=0.0)`, `modify_by_terminal(ticket, sl, tp)`, `place_pending_by_terminal(symbol, order_type, lots, price, sl=0.0, tp=0.0, comment="") -> ticket`, `cancel_by_terminal(ticket)`, `fill_pending(ticket) -> position ticket`, `deposit(amount)`, `set_price(symbol, bid, ask)`, and the EA controls `reject_next(retcode, message)`, `disconnect()`, `reconnect()`, `restart()`. With `hello_fn`/`sync_fn` the EA calls the app directly; with `base_url` it POSTs over HTTP (to `/api/mt5/*` with the key header when `key` is given, else to the copier's `/mt5/*` with `{"account_id", "report"}`).
- Netting book (`mode="netting"`): one position per symbol. An open (a command or `market_order`) on the same side adds to it (`IN` deal, volume-weighted open price); on the opposite side it reduces it (`OUT`), closes it (`OUT`, position gone) or reverses it (`INOUT` for the whole order volume; the position keeps its ticket and turns to the new side with the excess). The ack of an `open` carries, as plan 04's `DoOpen` does, the ticket of the net position the terminal shows AFTER the fill — `pos` 0 when the fill emptied it — and the fill's deal ticket (contract §2 "Acks of an open on a netting follower"). A non-zero SL/TP on the order replaces the position's; zero leaves them. `close`/`close_by_terminal` work on the net ticket in both modes. The ticket survives a reversal in this fake; the copier's ledger keys on the symbol, so a terminal that mints a new one is handled the same way.
- Scenarios (the spec's Testing list plus the netting ones): twelve in all — the eight hedging scenarios, and (i) a cTrader master's two positions netted into one follower position whose close of one master position reduces it by that copy's volume, and whose close of the other empties it (the ack says `pos` 0; the mapping closes by its coid), (ii) the follower's net position closed by the terminal, its copies reduced oldest-first, (iii) a netting MT5 master's add, add, partial reduce and reversal fanning out to cTrader followers as opens, FIFO closes and a new open the other way, (iv) the master holding BUY and opening SELL on the same symbol: the SELL copy nets against the BUY copy inside the net position (an OUT fill) yet both mappings stay active with their own volumes, and the master's later close of the BUY flips the net position (INOUT) closing only the BUY mapping. The fake cTrader server serves one symbol (EURUSD, id 1) and merges same-side fills into one position (N2), so the two master positions of (i)/(ii) are made by hiding the first from the fake's merge lookup while the second fills (`_two_master_positions`).

- [ ] **Step 1: Write the fake EA**

Create `copier/src/copier/testing/fake_ea.py`:

```python
"""A Python stand-in for MirrorFleet.mq5.

Speaks the exact wire format (copier/mt5/protocol.py) against an in-memory
book in HEDGING or NETTING mode: every command kind executes, positions/
orders/deals are reported back, acks carry what the real EA's CTrade
results would. Used by the integration tests through the copier directly
(hello_fn/sync_fn, no HTTP) or -- with base_url -- over HTTP: the api's
machine door when `key` is given (header X-MirrorFleet-Key), else the
copier control port (JSON bodies carrying account_id).

What it models of the real EA's discipline:
- executed command ids live in memory AND in an ack "file"
  (MQL5/Files/MirrorFleet/<login>.acks, last 500); a re-delivered id is
  re-acked from the file, never re-executed -- restart() forgets memory
  but keeps the file;
- acks ride the next sync and are dropped only after an OK response;
- deals are sent with ticket > watermark, the watermark being the server's
  (hello response) and then the last ticket the server accepted;
- an amend that changes nothing acks ok (TRADE_RETCODE_NO_CHANGES is not
  a failure for the EA either), and every price is used as given -- the
  real EA normalises to the symbol's digits before calling CTrade.

The netting book (mode="netting", ACCOUNT_MARGIN_MODE_RETAIL_NETTING):
one position per symbol. An open on the same side adds to it (an IN deal,
volume-weighted open price); on the opposite side it reduces it (OUT),
closes it (OUT, the position is gone) or reverses it (one INOUT deal for
the order's whole volume; the position keeps its ticket and turns to the
new side with the excess). The ack of an open reports, as the real EA's
DoOpen does, the ticket of the net position the terminal shows AFTER the
fill -- pos 0 when the fill emptied it -- and the fill's deal ticket; the
copier pairs the two by the deal ticket and reads the deal's entry. A
non-zero SL/TP on the order replaces the position's, as
CTrade.PositionOpen does on a netting account; zero leaves them. The
hello says hedging:false.
"""

import itertools
import json
import time
import urllib.request

from copier.mt5 import protocol as p

DEFAULT_SYMBOLS = [
    {"n": "EURUSD.r", "d": 5, "cs": 100000, "vmin": 0.01, "vstep": 0.01, "vmax": 100, "tm": 4},
    {"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01, "vmax": 50, "tm": 4},
]
DEFAULT_PRICES = {"EURUSD.r": (1.10000, 1.10010), "XAUUSD.r": (2400.00, 2400.30)}
ACK_FILE_LIMIT = 500
RETCODE_DONE = 10009
RETCODE_INVALID = 10013
RETCODE_POSITION_CLOSED = 10036
MODES = ("hedging", "netting")


class FakeEA:
    def __init__(self, base_url=None, key=None, account_id=None, *, hello_fn=None, sync_fn=None,
                 login=12345678, broker="Fake Broker Ltd", server="Fake-Demo", currency="USD",
                 mode="hedging", symbols=None, prices=None, balance=10000.0, magic=20260907):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self.base_url = base_url
        self.key = key
        self.account_id = account_id
        self._hello_fn = hello_fn
        self._sync_fn = sync_fn
        self.login, self.broker, self.server, self.currency = login, broker, server, currency
        self.mode = mode
        self.hedging = mode == "hedging"
        self.symbols = [dict(s) for s in (symbols if symbols is not None else DEFAULT_SYMBOLS)]
        self.prices = dict(prices or DEFAULT_PRICES)
        self.balance = float(balance)
        self.magic = magic
        # The terminal's book (the broker side: survives restart()).
        self.positions: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self.deals: list[dict] = []
        self._tickets = itertools.count(700001)
        # The EA's own state.
        self._pending_acks: list[dict] = []
        self._memory_ids: set[int] = set()
        self._file_acks: dict[int, dict] = {}
        self._reject_next: tuple[int, str] | None = None
        self.connected = True
        self.needs_hello = True
        self.seq = 0
        self.watermark = 0
        self.executed: list[p.Command] = []
        self.responses: list[str] = []

    # ---------- the book ----------

    def _contract_size(self, symbol: str) -> float:
        return float(next((s["cs"] for s in self.symbols if s["n"] == symbol), 1.0))

    def set_price(self, symbol: str, bid: float, ask: float) -> None:
        self.prices[symbol] = (bid, ask)

    def _mark(self) -> None:
        for pos in self.positions.values():
            bid, ask = self.prices[pos["s"]]
            cs = self._contract_size(pos["s"])
            if pos["side"] == "BUY":
                pos["price"] = bid
                pos["pnl"] = round((bid - pos["open"]) * pos["lots"] * cs, 2)
            else:
                pos["price"] = ask
                pos["pnl"] = round((pos["open"] - ask) * pos["lots"] * cs, 2)

    def equity(self) -> float:
        self._mark()
        return round(self.balance + sum(pos["pnl"] for pos in self.positions.values()), 2)

    def _deal(self, deal, position, order, symbol, side, entry, lots, price, profit=0.0,
              comment=""):
        self.deals.append({
            "t": deal, "pos": position, "order": order, "s": symbol, "type": side, "entry": entry,
            "lots": float(lots), "price": price, "profit": round(profit, 2), "swap": 0.0,
            "commission": 0.0, "time": int(time.time() * 1000), "comment": comment,
            "magic": self.magic})

    def _new_position(self, ticket, symbol, side, lots, price, sl, tp, comment) -> dict:
        self.positions[ticket] = {
            "t": ticket, "s": symbol, "side": side, "lots": float(lots), "open": price,
            "sl": float(sl or 0), "tp": float(tp or 0), "price": price, "pnl": 0.0, "swap": 0.0,
            "comment": comment, "magic": self.magic, "time": int(time.time())}
        return self.positions[ticket]

    def _profit(self, pos: dict, lots: float, price: float) -> float:
        cs = self._contract_size(pos["s"])
        if pos["side"] == "BUY":
            return round((price - pos["open"]) * lots * cs, 2)
        return round((pos["open"] - price) * lots * cs, 2)

    def _net_position(self, symbol: str) -> dict | None:
        return next((pos for pos in self.positions.values() if pos["s"] == symbol), None)

    def _open(self, symbol, side, lots, sl, tp, comment):
        """A market order. Hedging: a new position every time. Netting: the
        symbol's single position absorbs it -- see the module docstring."""
        bid, ask = self.prices[symbol]
        price = ask if side == "BUY" else bid
        lots = float(lots)
        order, deal = next(self._tickets), next(self._tickets)
        pos = self._net_position(symbol) if self.mode == "netting" else None
        if pos is None:
            ticket = next(self._tickets)
            self._new_position(ticket, symbol, side, lots, price, sl, tp, comment)
            self._deal(deal, ticket, order, symbol, side, "IN", lots, price, comment=comment)
            return ticket, order, deal, price
        ticket = pos["t"]
        if sl:
            pos["sl"] = float(sl)
        if tp:
            pos["tp"] = float(tp)
        if pos["side"] == side:
            total = round(pos["lots"] + lots, 2)
            pos["open"] = round((pos["open"] * pos["lots"] + price * lots) / total, 5)
            pos["lots"] = total
            self._deal(deal, ticket, order, symbol, side, "IN", lots, price, comment=comment)
            return ticket, order, deal, price
        closing = min(lots, pos["lots"])
        profit = self._profit(pos, closing, price)
        self.balance = round(self.balance + profit, 2)
        remainder = round(lots - pos["lots"], 2)
        if remainder > 0:
            # A reversal: the whole old volume closes and the excess opens the
            # other way -- one INOUT deal for the order's whole volume.
            self._deal(deal, ticket, order, symbol, side, "INOUT", lots, price, profit,
                       comment=comment)
            pos.update({"side": side, "lots": remainder, "open": price, "comment": comment,
                        "time": int(time.time())})
        else:
            self._deal(deal, ticket, order, symbol, side, "OUT", closing, price, profit,
                       comment=comment)
            pos["lots"] = round(pos["lots"] - closing, 2)
            if pos["lots"] <= 0:
                del self.positions[ticket]
                # The real EA acks the ticket the terminal shows AFTER the
                # fill (plan 04 DoOpen: PositionSelect(symbol)) -- 0 when the
                # fill emptied the net position. The deal still names it.
                ticket = 0
        return ticket, order, deal, price

    def _close(self, ticket, lots):
        """PositionClose / PositionClosePartial on a ticket, in both modes."""
        pos = self.positions[ticket]
        close_lots = pos["lots"] if not lots or float(lots) >= pos["lots"] else float(lots)
        bid, ask = self.prices[pos["s"]]
        price = bid if pos["side"] == "BUY" else ask
        profit = self._profit(pos, close_lots, price)
        self.balance = round(self.balance + profit, 2)
        deal, order = next(self._tickets), next(self._tickets)
        self._deal(deal, ticket, order, pos["s"], "SELL" if pos["side"] == "BUY" else "BUY",
                   "OUT", close_lots, price, profit, comment=pos["comment"])
        pos["lots"] = round(pos["lots"] - close_lots, 2)
        if pos["lots"] <= 0:
            del self.positions[ticket]
        return deal, order, price, close_lots

    # Terminal-side actions: the owner trading by hand, a stop being hit.

    def market_order(self, symbol, side, lots, sl=0.0, tp=0.0, comment="") -> int:
        """The owner trades by hand. Returns the position ticket -- 0 when a
        netting fill emptied the net position, as the EA's ack would say."""
        ticket, _order, _deal, _price = self._open(symbol, side, lots, sl, tp, comment)
        return ticket

    def close_by_terminal(self, ticket, lots=0.0) -> None:
        self._close(ticket, lots)

    def modify_by_terminal(self, ticket, sl=0.0, tp=0.0) -> None:
        pos = self.positions[ticket]
        pos["sl"], pos["tp"] = float(sl or 0), float(tp or 0)

    def place_pending_by_terminal(self, symbol, order_type, lots, price, sl=0.0, tp=0.0,
                                  comment="") -> int:
        ticket = next(self._tickets)
        self.orders[ticket] = {"t": ticket, "s": symbol, "type": order_type, "lots": float(lots),
                               "price": float(price), "sl": float(sl or 0), "tp": float(tp or 0),
                               "comment": comment, "magic": self.magic}
        return ticket

    def cancel_by_terminal(self, ticket) -> None:
        del self.orders[ticket]

    def fill_pending(self, ticket) -> int:
        """The market reached a pending order: it becomes a position whose
        opening deal names the order, as MT5 does. (Hedging book only: the
        netting scenarios drive pendings through the copier's commands.)"""
        o = self.orders.pop(ticket)
        side = o["type"].split("_", 1)[0]
        position, deal = next(self._tickets), next(self._tickets)
        self._new_position(position, o["s"], side, o["lots"], o["price"], o["sl"], o["tp"],
                           o["comment"])
        self._deal(deal, position, ticket, o["s"], side, "IN", o["lots"], o["price"],
                   comment=o["comment"])
        return position

    def deposit(self, amount: float) -> None:
        self.balance = round(self.balance + amount, 2)
        self._deal(next(self._tickets), 0, 0, "", "BALANCE", "", 0.0, 0.0, profit=amount)

    # ---------- the EA ----------

    def reject_next(self, retcode: int, message: str) -> None:
        self._reject_next = (retcode, message)

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    def restart(self) -> None:
        """The terminal restarts: EA memory (executed ids, unsent acks, seq,
        watermark) is gone; the ack file and the book survive; the next
        tick says hello again."""
        self._memory_ids.clear()
        self._pending_acks.clear()
        self.seq = 0
        self.watermark = 0
        self.needs_hello = True
        self.connected = True

    def hello_body(self) -> dict:
        return {"v": p.PROTOCOL_VERSION, "ea": "1.0.0", "build": 4400, "login": self.login,
                "broker": self.broker, "server": self.server, "currency": self.currency,
                "hedging": self.hedging, "trade_mode": "demo", "leverage": 500,
                "symbols": [dict(s) for s in self.symbols], "chunk": 1, "chunks": 1}

    def sync_body(self) -> dict:
        self.seq += 1
        self._mark()
        deals = [dict(d) for d in self.deals if d["t"] > self.watermark][:p.MAX_DEALS_PER_SYNC]
        return {"v": p.PROTOCOL_VERSION, "seq": self.seq, "ts": int(time.time() * 1000),
                "balance": round(self.balance, 2), "equity": self.equity(), "margin": 0.0,
                "margin_free": self.equity(),
                "positions": [dict(pos) for pos in self.positions.values()],
                "orders": [dict(o) for o in self.orders.values()],
                "deals": deals, "acks": [dict(a) for a in self._pending_acks]}

    def tick(self):
        """One EA timer tick: hello if needed, then one sync; executes every
        command in the response. Returns the status line fields, or None
        while disconnected."""
        if not self.connected:
            return None
        if self.needs_hello:
            reply = self._post("hello", self.hello_body())
            self.watermark = int(reply.get("last_deal_ticket") or 0)
            self.needs_hello = False
        body = self.sync_body()
        sent_acks = list(body["acks"])
        text = self._post("sync", body)
        self.responses.append(text)
        status, commands = p.parse_response(text)
        if status[0] == "OK":
            self._pending_acks = [a for a in self._pending_acks if a not in sent_acks]
            if body["deals"]:
                self.watermark = max(self.watermark, max(d["t"] for d in body["deals"]))
            for cmd in commands:
                self.execute(cmd)
        elif status[0] == "STOP":
            self.connected = False
        return status

    def _post(self, kind: str, body: dict):
        if kind == "hello" and self._hello_fn is not None:
            return self._hello_fn(body)
        if kind == "sync" and self._sync_fn is not None:
            return self._sync_fn(body)
        if self.key is not None:
            url = f"{self.base_url}/api/mt5/{kind}"
            headers = {"Content-Type": "application/json", "X-MirrorFleet-Key": self.key}
            payload = body
        else:
            url = f"{self.base_url}/mt5/{kind}"
            headers = {"Content-Type": "application/json"}
            payload = {"account_id": self.account_id, "report": body}
        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            text = response.read().decode()
        return json.loads(text) if kind == "hello" else text

    def execute(self, cmd: p.Command) -> None:
        if cmd.id in self._file_acks or cmd.id in self._memory_ids:
            self._pending_acks.append(dict(self._file_acks[cmd.id]))   # re-acked, never re-run
            return
        ack = self._run(cmd)
        self._memory_ids.add(cmd.id)
        self._file_acks[cmd.id] = ack
        while len(self._file_acks) > ACK_FILE_LIMIT:
            self._file_acks.pop(next(iter(self._file_acks)))
        self._pending_acks.append(dict(ack))
        self.executed.append(cmd)

    def _run(self, cmd: p.Command) -> dict:
        if self._reject_next is not None:
            retcode, message = self._reject_next
            self._reject_next = None
            return self._ack(cmd.id, False, retcode, message)
        payload = cmd.payload
        try:
            if cmd.kind == "open":
                ticket, order, deal, price = self._open(
                    payload["symbol"], payload["side"], payload["lots"], payload.get("sl"),
                    payload.get("tp"), payload.get("comment", ""))
                return self._ack(cmd.id, True, RETCODE_DONE, "done", pos=ticket, deal=deal,
                                 order=order, price=price, lots=float(payload["lots"]))
            if cmd.kind == "close":
                deal, order, price, lots = self._close(int(payload["position"]), payload.get("lots"))
                return self._ack(cmd.id, True, RETCODE_DONE, "done", pos=int(payload["position"]),
                                 deal=deal, order=order, price=price, lots=lots)
            if cmd.kind == "amend":
                pos = self.positions[int(payload["position"])]
                pos["sl"] = float(payload.get("sl") or 0)
                pos["tp"] = float(payload.get("tp") or 0)
                return self._ack(cmd.id, True, RETCODE_DONE, "done", pos=pos["t"])
            if cmd.kind == "place_pending":
                ticket = next(self._tickets)
                self.orders[ticket] = {
                    "t": ticket, "s": payload["symbol"], "type": payload["type"],
                    "lots": float(payload["lots"]), "price": float(payload["price"]),
                    "sl": float(payload.get("sl") or 0), "tp": float(payload.get("tp") or 0),
                    "comment": payload.get("comment", ""), "magic": self.magic}
                return self._ack(cmd.id, True, RETCODE_DONE, "done", order=ticket,
                                 price=float(payload["price"]), lots=float(payload["lots"]))
            if cmd.kind == "amend_pending":
                o = self.orders[int(payload["order"])]
                o["lots"] = float(payload["lots"])
                o["price"] = float(payload["price"])
                o["sl"] = float(payload.get("sl") or 0)
                o["tp"] = float(payload.get("tp") or 0)
                return self._ack(cmd.id, True, RETCODE_DONE, "done", order=o["t"])
            if cmd.kind == "cancel_pending":
                del self.orders[int(payload["order"])]
                return self._ack(cmd.id, True, RETCODE_DONE, "done", order=int(payload["order"]))
        except KeyError as missing:
            return self._ack(cmd.id, False, RETCODE_POSITION_CLOSED, f"no such ticket {missing}")
        return self._ack(cmd.id, False, RETCODE_INVALID, f"unknown command {cmd.kind}")

    @staticmethod
    def _ack(command_id, ok, retcode, message, pos=None, deal=None, order=None, price=None,
             lots=None) -> dict:
        return {"id": command_id, "ok": ok, "retcode": retcode, "msg": message, "pos": pos,
                "deal": deal, "order": order, "price": price, "lots": lots}
```

- [ ] **Step 2: Write the failing integration tests**

Create `copier/tests/integration/test_mt5_bridge.py`:

```python
"""End-to-end tests for the MT5 lane: the REAL CopierApp (build_app) with
a FakeCTraderServer on one side and the FakeEA on the other.

The fake EA talks to the app the way the api would proxy it (hello_fn /
sync_fn call app.mt5_hello / app.mt5_sync directly -- the control route
and the api door are unit-tested elsewhere), executes every command in
the response against its in-memory hedging or netting book, and reports
back on the next tick. Outcomes are asserted from real Postgres state, the recorded
cTrader wire traffic and the EA's book -- never from internal mocks.
"""

from datetime import datetime, timedelta

import psycopg
import pytest
import pytest_twisted
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAClosePositionReq, ProtoOANewOrderReq)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from twisted.internet import reactor, task

import copier.main as main_module
import copier.mt5.outbox as outbox_module
from copier.ctrader.client import CTraderClient, make_sdk_client
from copier.ctrader.tokens import TokenStore
from copier.db.repo import Repo
from copier.main import build_app
from copier.testing.fake_ea import FakeEA
from copier.testing.fake_server import FakeCTraderServer

from integration.test_copier_e2e import (
    ACCESS_TOKEN, FERNET_KEY, MASTER_ID, ONE_LOT, ORG_ID, SLAVE1_ID, SLAVE2_ID, SYMBOL_ID,
    _amend_sltp, _close_position, _events, _fire_and_forget, _insert_accounts, _market_order,
    _seed_org, _teardown, _wait_until)

XAUUSD_ONLY = [{"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01, "vmax": 50,
                "tm": 4}]


def _insert_ctrader_slaves(dsn, connection_id, org_id):
    """Slaves 101 (1.0x) and 102 (0.5x) only -- for the MT5-master scenario."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                  trader_login, is_live, role, enabled, multiplier)
            VALUES (%(s1)s, %(org)s, %(conn)s, 90101, false, 'slave', true, 1.0),
                   (%(s2)s, %(org)s, %(conn)s, 90102, false, 'slave', true, 0.5)
            """,
            {"s1": SLAVE1_ID, "s2": SLAVE2_ID, "org": org_id, "conn": connection_id})


def _insert_mt5_account(dsn, org_id, role):
    with psycopg.connect(dsn, autocommit=True) as conn:
        (account_id,) = conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id,
                                  trader_login, is_live, role, enabled, multiplier, platform)
            VALUES (nextval('mt5_account_id_seq'), NULL, %s, 0, false, %s, true, 1.0, 'mt5')
            RETURNING ctid_trader_account_id
            """, (org_id, role)).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, %s)",
                     (account_id, f"hash-{account_id}"))
    return account_id


def _setup_bridge(dsn, *, mt5_role="slave", ea_symbols=None, ea_mode="hedging"):
    """FakeCTraderServer + real CopierApp + FakeEA.

    mt5_role='slave': cTrader master 100 with slaves 101/102 and the MT5
    slave. mt5_role='master': the MT5 master with cTrader slaves 101/102.
    ea_mode: the fake terminal's book, 'hedging' or 'netting'.
    Returns (server, repo, app, ea, mt5_id); the caller yields
    app.startup(), then ea.tick() once for the hello (the hello auto-matches
    against symbol caches startup fills), and eventually calls _teardown.
    """
    server = FakeCTraderServer(auto_fill=True)
    port = server.listen(reactor)
    org_id = _seed_org(dsn)
    token_store = TokenStore(dsn, FERNET_KEY)
    connection_id = token_store.save_grant(
        ACCESS_TOKEN, "e2e-refresh-token", datetime.utcnow() + timedelta(days=60), org_id)
    if mt5_role == "slave":
        _insert_accounts(dsn, connection_id, org_id)
    else:
        _insert_ctrader_slaves(dsn, connection_id, org_id)
    mt5_id = _insert_mt5_account(dsn, org_id, mt5_role)
    server.accounts = {MASTER_ID: ACCESS_TOKEN, SLAVE1_ID: ACCESS_TOKEN, SLAVE2_ID: ACCESS_TOKEN}
    repo = Repo(dsn)

    def client_factory(is_live: bool) -> CTraderClient:
        return CTraderClient(make_sdk_client("127.0.0.1", port), "e2e-client-id",
                             "e2e-client-secret")

    app = build_app(repo, token_store, client_factory, shards=1)
    ea = FakeEA(account_id=mt5_id,
                hello_fn=lambda body: app.mt5_hello(mt5_id, body),
                sync_fn=lambda body: app.mt5_sync(mt5_id, body),
                symbols=ea_symbols, mode=ea_mode)
    return server, repo, app, ea, mt5_id


class _Ticker:
    """Polls the fake EA on the reactor like its timer would (faster, so
    tests finish); a tick that raises is recorded, not swallowed."""

    def __init__(self, ea, interval=0.1):
        self.ea, self.errors = ea, []
        self._call = task.LoopingCall(self._tick)
        self._call.clock = reactor
        self._call.start(interval, now=False)

    def _tick(self):
        try:
            self.ea.tick()
        except Exception as e:      # pragma: no cover - surfaced by the assertions
            self.errors.append(e)

    def stop(self):
        if self._call.running:
            self._call.stop()


def _mt5_rows(repo, mt5_id, status=None):
    return [r for r in repo.mapping_rows()
            if r["slave_account_id"] == mt5_id and (status is None or r["status"] == status)]


def _all_commands(repo, mt5_id):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM mt5_commands WHERE account_id = %s ORDER BY id", (mt5_id,)
            ).fetchall()


def _reqs(server, req_type, account_id):
    return [r for r in server.requests
            if isinstance(r, req_type) and r.ctidTraderAccountId == account_id]


def _sleep(seconds):
    return task.deferLater(reactor, seconds, lambda: None)


@pytest_twisted.inlineCallbacks
def _open_one_copy(app, repo, ea, mt5_id):
    """Master buys ONE_LOT; wait for the MT5 copy to go active. Returns
    (master_position_id, slave ticket)."""
    client = app.clients[False][0]
    _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
    yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 1)
    (row,) = _mt5_rows(repo, mt5_id, "active")
    return row["master_position_id"], row["slave_position_id"]


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_ctrader_master_fill_opens_an_mt5_copy_that_inherits_protection(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()                                            # hello + first sync
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r"}
        ticker = _Ticker(ea)

        master_position_id, ticket = yield _open_one_copy(app, repo, ea, mt5_id)

        (row,) = _mt5_rows(repo, mt5_id, "active")
        assert row["slave_volume"] == 100                          # 1.00 lot in centilots
        assert row["fill_price"] == pytest.approx(1.10010)         # the fake EA's ask
        pos = ea.positions[ticket]
        assert (pos["s"], pos["side"], pos["lots"], pos["comment"]) == (
            "EURUSD.r", "BUY", 1.0, f"copy:m{master_position_id}")
        assert len(ea.executed) == 1

        # cTrader protects a market position with a SECOND event, after the
        # fill; the copy inherits it through an amend command.
        _fire_and_forget(app.clients[False][0],
                         _amend_sltp(MASTER_ID, master_position_id, stop_loss=1.0900,
                                     take_profit=1.1200))
        yield _wait_until(lambda: (ea.positions[ticket]["sl"], ea.positions[ticket]["tp"]) == (1.09, 1.12))
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_master_partial_close_and_amend_propagate_to_the_mt5_copy(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        master_position_id, ticket = yield _open_one_copy(app, repo, ea, mt5_id)
        client = app.clients[False][0]

        _fire_and_forget(client, _close_position(MASTER_ID, master_position_id, ONE_LOT // 2))
        yield _wait_until(lambda: ea.positions.get(ticket, {}).get("lots") == 0.5)
        yield _wait_until(lambda: _mt5_rows(repo, mt5_id)[0]["slave_volume"] == 50)
        assert _mt5_rows(repo, mt5_id)[0]["status"] == "active"
        (close,) = [c for c in ea.executed if c.kind == "close"]
        assert close.payload == {"position": ticket, "lots": 0.5}

        _fire_and_forget(client, _amend_sltp(MASTER_ID, master_position_id, stop_loss=1.0950))
        yield _wait_until(lambda: ea.positions[ticket]["sl"] == 1.095)
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_mt5_master_deals_copy_to_ctrader_followers(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, mt5_role="master")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r"}
        ticker = _Ticker(ea)

        ticket = ea.market_order("EURUSD.r", "BUY", 0.5)     # the owner trades in the terminal
        yield _wait_until(lambda: len(_reqs(server, ProtoOANewOrderReq, SLAVE1_ID)) == 1
                          and len(_reqs(server, ProtoOANewOrderReq, SLAVE2_ID)) == 1)
        s1 = _reqs(server, ProtoOANewOrderReq, SLAVE1_ID)[0]
        s2 = _reqs(server, ProtoOANewOrderReq, SLAVE2_ID)[0]
        assert (s1.volume, s2.volume) == (ONE_LOT // 2, ONE_LOT // 4)   # 0.5 lot x 1.0 and x 0.5
        assert s1.symbolId == SYMBOL_ID and s1.clientOrderId == f"cm{ticket}.{SLAVE1_ID}"
        yield _wait_until(lambda: len([r for r in repo.mapping_rows() if r["status"] == "active"]) == 2)
        master_events = [e for e in _events(db, "master_event")
                         if e["payload"].get("source") == "mt5"]
        assert [e["payload"]["normalized"] for e in master_events] == ["MasterPositionOpened"]

        ea.close_by_terminal(ticket)
        yield _wait_until(lambda: len(_reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 1
                          and len(_reqs(server, ProtoOAClosePositionReq, SLAVE2_ID)) == 1)
        yield _wait_until(lambda: all(r["status"] == "closed" for r in repo.mapping_rows()))
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_trade_page_order_on_mt5_executes_in_the_terminal(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)

        result = app.place_order({"account_id": mt5_id, "symbol": "EURUSD.r", "side": "SELL",
                                  "order_type": "MARKET", "volume_lots": 0.2,
                                  "actor_email": "ada@example.com"})
        assert result["status"] == "submitted" and result["volume"] == 20
        yield _wait_until(lambda: len(ea.positions) == 1)
        (pos,) = ea.positions.values()
        assert (pos["s"], pos["side"], pos["lots"], pos["comment"]) == ("EURUSD.r", "SELL", 0.2, "manual")
        yield _wait_until(lambda: any(e["payload"].get("action") == "manual_fill"
                                      for e in _events(db, "slave_action")))
        assert _mt5_rows(repo, mt5_id) == []                     # a manual order maps to nothing

        app.place_order({"account_id": mt5_id, "symbol": "EURUSD.r", "side": "BUY",
                         "order_type": "LIMIT", "volume_lots": 0.1, "limit_price": 1.0950})
        yield _wait_until(lambda: len(ea.orders) == 1)
        (order,) = ea.orders.values()
        assert (order["type"], order["lots"], order["price"]) == ("BUY_LIMIT", 0.1, 1.095)

        # The Positions screen sees the terminal's book through the registry.
        state = app.get_state(ORG_ID)
        assert [p["side"] for p in state["accounts"][mt5_id]["positions"]] == ["SELL"]
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_close_all_flattens_ctrader_and_mt5_with_verified_counts(db, monkeypatch):
    monkeypatch.setattr(main_module, "CLOSE_ALL_RESUME_GRACE_S", 0.05)
    monkeypatch.setattr(main_module, "FLATTEN_SETTLE_S", 0.5)
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    for account_id, position_id in ((MASTER_ID, 7001), (SLAVE1_ID, 7002)):
        server.open_positions.setdefault(account_id, []).append({
            "position_id": position_id, "symbol_id": SYMBOL_ID, "volume": 100_000,
            "trade_side": ProtoOATradeSide.BUY, "label": ""})
        server._position_volumes[position_id] = 100_000
    ea.market_order("EURUSD.r", "BUY", 0.3)
    ea.market_order("XAUUSD.r", "SELL", 0.1)
    ea.place_pending_by_terminal("EURUSD.r", "BUY_LIMIT", 0.1, 1.09)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)

        result = yield app.close_all(ORG_ID)

        assert result["paused"] is False
        by_account = {s["account_id"]: s for s in result["accounts"]}
        assert by_account[MASTER_ID]["positions_closed"] == 1
        assert by_account[SLAVE1_ID]["positions_closed"] == 1
        mt5 = by_account[mt5_id]
        assert (mt5["positions_closed"], mt5["orders_cancelled"], mt5["positions_remaining"],
                mt5["orders_remaining"], mt5["error"]) == (2, 1, [], [], None)
        assert ea.positions == {} and ea.orders == {}
        assert repo.get_org(ORG_ID).copying_enabled is True
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_terminal_offline_expires_the_open_but_the_close_survives(db, monkeypatch):
    monkeypatch.setattr(outbox_module, "OPEN_COMMAND_TTL_S", 0.5)
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        master_position_id, ticket = yield _open_one_copy(app, repo, ea, mt5_id)
        client = app.clients[False][0]

        ea.disconnect()
        _fire_and_forget(client, _close_position(MASTER_ID, master_position_id, ONE_LOT))
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.SELL, ONE_LOT))
        yield _wait_until(lambda: len(repo.mt5_commands_open(mt5_id)) == 2)
        yield _sleep(0.7)                                        # past the open's TTL
        ea.reconnect()

        yield _wait_until(lambda: ticket not in ea.positions)
        yield _wait_until(lambda: {r["status"] for r in _mt5_rows(repo, mt5_id)} == {"closed", "failed"})
        failed = next(r for r in _mt5_rows(repo, mt5_id) if r["status"] == "failed")
        assert failed["error"] == "terminal offline"
        assert ea.positions == {} and [c.kind for c in ea.executed] == ["open", "close"]
        rows = _all_commands(repo, mt5_id)
        assert sorted((r["kind"], r["status"]) for r in rows) == [
            ("close", "done"), ("open", "done"), ("open", "failed")]
        (expired,) = [r for r in rows if r["status"] == "failed"]
        assert expired["result"]["message"] == "terminal offline"
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_a_redelivered_command_is_reacked_not_reexecuted_after_a_restart(db, monkeypatch):
    monkeypatch.setattr(outbox_module, "REDELIVER_AFTER_S", 0.2)
    server, repo, app, ea, mt5_id = _setup_bridge(db)
    try:
        yield app.startup()
        ea.tick()
        client = app.clients[False][0]
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(repo.mt5_commands_open(mt5_id)) == 1)

        ea.tick()                     # the open is delivered and executed; its ack waits for the next sync
        assert len(ea.positions) == 1 and len(ea.executed) == 1
        ea.restart()                  # ...and the terminal restarts before that sync
        ea.tick()                     # hello again, then a sync with no ack
        assert _mt5_rows(repo, mt5_id, "active") == []
        yield _sleep(0.3)
        ea.tick()                     # the copier re-delivers; the EA re-acks from its file
        assert len(ea.executed) == 1 and len(ea.positions) == 1
        ea.tick()                     # the re-ack arrives

        (row,) = _mt5_rows(repo, mt5_id, "active")
        assert row["slave_position_id"] in ea.positions
        (command,) = _all_commands(repo, mt5_id)
        assert (command["status"], command["attempts"]) == ("done", 2)
    finally:
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_an_unmatched_symbol_alerts_and_queues_nothing(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_symbols=XAUUSD_ONLY)
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert repo.load_symbol_aliases(mt5_id) == {}
        ticker = _Ticker(ea)

        _fire_and_forget(app.clients[False][0],
                         _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
        yield _wait_until(lambda: len(_reqs(server, ProtoOANewOrderReq, SLAVE1_ID)) == 1)   # cTrader slaves still copy
        yield _wait_until(lambda: any(
            e["account_id"] == mt5_id and e["severity"] == "warning" and "EURUSD" in str(e["payload"])
            for e in _events(db, "slave_action")))
        yield _sleep(0.5)
        assert _all_commands(repo, mt5_id) == [] and _mt5_rows(repo, mt5_id) == []
        assert ea.positions == {} and ea.executed == []
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


# ---------- netting accounts ----------

def _oldest_first(rows):
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def _active_mappings(repo):
    return [r for r in repo.mapping_rows() if r["status"] == "active"]


@pytest_twisted.inlineCallbacks
def _two_master_positions(server, app, repo, ea, mt5_id):
    """Two SEPARATE same-side master positions (1.00 and 0.50 lots), each
    copied to the MT5 follower. cTrader merges a second same-side market
    fill into the existing position (N2) and the fake server does too, so
    the first position is hidden from the fake's merge lookup while the
    second fills and put back afterwards -- the copier sees two distinct
    master fills, as it would for positions opened through different
    routes. Returns (first_master_id, second_master_id), oldest first."""
    client = app.clients[False][0]
    _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT))
    yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 1)
    hidden = server.open_positions[MASTER_ID].pop()
    _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.BUY, ONE_LOT // 2))
    yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 2)
    server.open_positions[MASTER_ID].append(hidden)
    rows = _oldest_first(_mt5_rows(repo, mt5_id, "active"))
    return rows[0]["master_position_id"], rows[1]["master_position_id"]


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_follower_nets_two_copies_and_a_master_close_reduces_its_share(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert ea.hello_body()["hedging"] is False
        assert app.mt5_registry.margin_mode(mt5_id) == "netting"
        assert repo.load_mt5_link(mt5_id)["hedging"] is False
        ticker = _Ticker(ea)

        first_master, second_master = yield _two_master_positions(server, app, repo, ea, mt5_id)

        (net,) = ea.positions.values()                        # ONE net position...
        net_ticket = net["t"]
        assert (net["s"], net["side"], net["lots"]) == ("EURUSD.r", "BUY", 1.5)   # ...its volume the sum
        rows = {r["master_position_id"]: r for r in _mt5_rows(repo, mt5_id, "active")}
        assert rows[first_master]["slave_position_id"] == net_ticket
        assert rows[second_master]["slave_position_id"] == net_ticket
        assert (rows[first_master]["slave_volume"], rows[second_master]["slave_volume"]) == (100, 50)
        assert [c.kind for c in ea.executed] == ["open", "open"]

        # The master closes its FIRST position: the follower's copy of it is
        # closed with an opposite-side open for exactly that copy's volume.
        _fire_and_forget(app.clients[False][0], _close_position(MASTER_ID, first_master, ONE_LOT))
        yield _wait_until(lambda: ea.positions.get(net_ticket, {}).get("lots") == 0.5)
        yield _wait_until(lambda: {r["master_position_id"] for r in _mt5_rows(repo, mt5_id, "active")}
                          == {second_master})

        (close,) = [c for c in ea.executed if c.payload.get("comment", "").startswith("close:")]
        assert (close.kind, close.payload["side"], close.payload["lots"], close.client_order_id) == (
            "open", "SELL", 1.0, f"cm{first_master}.{mt5_id}:close")
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "IN", "OUT"]
        (closed,) = _mt5_rows(repo, mt5_id, "closed")
        assert (closed["master_position_id"], closed["slave_volume"]) == (first_master, 0)
        (active,) = _mt5_rows(repo, mt5_id, "active")
        assert (active["master_position_id"], active["slave_volume"]) == (second_master, 50)
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{first_master}.{mt5_id}", 100)]

        # The master closes its SECOND position too: the follower's last
        # 0.50 goes and the net position is gone, so the EA acks pos 0 (the
        # ticket the terminal shows after the fill: none). The mapping is
        # named by the ack's coid, so it closes all the same.
        _fire_and_forget(app.clients[False][0],
                         _close_position(MASTER_ID, second_master, ONE_LOT // 2))
        yield _wait_until(lambda: ea.positions == {})
        yield _wait_until(lambda: {r["status"] for r in _mt5_rows(repo, mt5_id)} == {"closed"})
        (last_close,) = [c for c in ea.executed
                         if c.client_order_id == f"cm{second_master}.{mt5_id}:close"]
        assert ea._file_acks[last_close.id]["pos"] == 0           # what the EA acked
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "IN", "OUT", "OUT"]
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{first_master}.{mt5_id}", 100), (f"cm{second_master}.{mt5_id}", 50)]
        assert _mt5_rows(repo, mt5_id, "active") == []
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_follower_copies_an_opposite_side_master_position_without_closing_the_older_copy(db):
    """Spec "Opposite positions": the master holds BUY and opens SELL on
    the same symbol. On a netting follower the SELL copy nets against the
    BUY copy inside the one net position -- its fill is an OUT deal on it
    -- yet it is a COPY: its mapping activates on the net ticket and the
    BUY copy, whose master is still open, is untouched. When the master
    then closes the BUY, its ':close' SELL flips the net position (INOUT)
    and only the BUY mapping closes."""
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        client = app.clients[False][0]

        buy_master, net_ticket = yield _open_one_copy(app, repo, ea, mt5_id)
        _fire_and_forget(client, _market_order(MASTER_ID, ProtoOATradeSide.SELL, ONE_LOT // 2))
        yield _wait_until(lambda: len(_mt5_rows(repo, mt5_id, "active")) == 2)

        (net,) = ea.positions.values()
        assert (net["t"], net["side"], net["lots"]) == (net_ticket, "BUY", 0.5)   # 1.00 BUY - 0.50 SELL
        rows = {r["master_position_id"]: r for r in _mt5_rows(repo, mt5_id, "active")}
        sell_master = next(m for m in rows if m != buy_master)
        assert (rows[buy_master]["slave_position_id"], rows[buy_master]["slave_volume"]) == (
            net_ticket, 100)
        assert (rows[sell_master]["slave_position_id"], rows[sell_master]["slave_volume"]) == (
            net_ticket, 50)
        assert [(c.kind, c.payload["side"], c.payload["comment"]) for c in ea.executed] == [
            ("open", "BUY", f"copy:m{buy_master}"), ("open", "SELL", f"copy:m{sell_master}")]
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "OUT"]
        assert [(c["kind"], c["status"]) for c in _all_commands(repo, mt5_id)] == [
            ("open", "done"), ("open", "done")]
        assert [e for e in _events(db, "slave_action")
                if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id] == []
        (netted,) = [e for e in _events(db, "slave_action")
                     if e["payload"].get("action") == "mt5_netting_opposite_copy"]
        assert (netted["payload"]["client_order_id"], netted["payload"]["entry"],
                netted["payload"]["nets_against"]) == (f"cm{sell_master}.{mt5_id}", "OUT", [buy_master])

        # The master closes the BUY: a ':close' SELL 1.00 against BUY 0.50
        # flips the net position to SELL 0.50 -- the SELL copy's own volume.
        _fire_and_forget(client, _close_position(MASTER_ID, buy_master, ONE_LOT))
        yield _wait_until(lambda: {r["master_position_id"] for r in _mt5_rows(repo, mt5_id, "active")}
                          == {sell_master})
        (net,) = ea.positions.values()
        assert (net["side"], net["lots"]) == ("SELL", 0.5)
        assert [d["entry"] for d in ea.deals if d["s"] == "EURUSD.r"] == ["IN", "OUT", "INOUT"]
        (active,) = _mt5_rows(repo, mt5_id, "active")
        assert (active["master_position_id"], active["slave_position_id"],
                active["slave_volume"]) == (sell_master, net_ticket, 50)
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{buy_master}.{mt5_id}", 100)]
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_follower_terminal_close_reduces_the_copies_oldest_first(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        ticker = _Ticker(ea)
        first_master, second_master = yield _two_master_positions(server, app, repo, ea, mt5_id)
        (net,) = ea.positions.values()
        net_ticket = net["t"]

        # The owner's terminal trims 1.00 lot of the 1.50 (a stop on part of
        # it, a manual reduce): the OLDER copy is what goes.
        ea.close_by_terminal(net_ticket, lots=1.0)
        yield _wait_until(lambda: [r["status"] for r in _oldest_first(_mt5_rows(repo, mt5_id))]
                          == ["closed", "active"])
        rows = _oldest_first(_mt5_rows(repo, mt5_id))
        assert (rows[0]["master_position_id"], rows[0]["slave_volume"]) == (first_master, 0)
        assert (rows[1]["master_position_id"], rows[1]["slave_volume"]) == (second_master, 50)

        # Then the net stop takes the rest.
        ea.close_by_terminal(net_ticket)
        yield _wait_until(lambda: {r["status"] for r in _mt5_rows(repo, mt5_id)} == {"closed"})
        assert ea.positions == {}
        closes = [e for e in _events(db, "slave_action")
                  if e["payload"].get("action") == "position_closed" and e["account_id"] == mt5_id]
        assert [(e["payload"]["client_order_id"], e["payload"]["closed_volume"]) for e in closes] == [
            (f"cm{first_master}.{mt5_id}", 100), (f"cm{second_master}.{mt5_id}", 50)]
        assert [c.kind for c in ea.executed] == ["open", "open"]   # nothing queued for the terminal's own closes
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)


@pytest.mark.timeout(60)
@pytest_twisted.inlineCallbacks
def test_netting_mt5_master_add_reduce_and_reverse_fan_out_to_ctrader_followers(db):
    server, repo, app, ea, mt5_id = _setup_bridge(db, mt5_role="master", ea_mode="netting")
    ticker = None
    try:
        yield app.startup()
        ea.tick()
        assert app.mt5_registry.margin_mode(mt5_id) == "netting"
        ticker = _Ticker(ea)

        # add, add: one net position on the terminal, TWO virtual master
        # positions in the ledger, two opens per follower
        ea.market_order("EURUSD.r", "BUY", 0.5)
        yield _wait_until(lambda: len(_active_mappings(repo)) == 2)
        # cTrader merges a follower's second same-side fill into its first
        # position (N2) and the fake server does too, which would leave both
        # mappings on ONE slave position (and a close of one would reduce
        # both). Hide the first copies from the fake's merge lookup while the
        # second fills, as _two_master_positions does for the master, so each
        # copy is a position of its own.
        hidden = {a: server.open_positions[a].pop() for a in (SLAVE1_ID, SLAVE2_ID)}
        ea.market_order("EURUSD.r", "BUY", 0.3)
        yield _wait_until(lambda: len(_active_mappings(repo)) == 4)
        for account_id, entry in hidden.items():
            server.open_positions[account_id].append(entry)
        (net,) = ea.positions.values()
        assert (net["side"], net["lots"]) == ("BUY", 0.8)
        ledger = repo.load_net_ledger(mt5_id)
        first_id, second_id = [r["virtual_id"] for r in ledger]
        assert [(r["side"], r["volume_left"]) for r in ledger] == [("BUY", 50), ("BUY", 30)]
        opens = _reqs(server, ProtoOANewOrderReq, SLAVE1_ID)
        assert [o.clientOrderId for o in opens] == [f"cm{first_id}.{SLAVE1_ID}", f"cm{second_id}.{SLAVE1_ID}"]
        assert [o.volume for o in opens] == [ONE_LOT // 2, ONE_LOT * 3 // 10]
        assert [o.volume for o in _reqs(server, ProtoOANewOrderReq, SLAVE2_ID)] == [
            ONE_LOT // 4, ONE_LOT * 3 // 20]                    # the 0.5x follower

        # partial reduce: 0.6 lots out of 0.8 -> the older virtual position
        # closes, the younger loses 0.1: two closes per follower, oldest first
        ea.market_order("EURUSD.r", "SELL", 0.6)
        yield _wait_until(lambda: len(_reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 2)
        closes = _reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)
        assert [c.volume for c in closes] == [ONE_LOT // 2, ONE_LOT // 10]
        assert [(r["virtual_id"], r["volume_left"]) for r in repo.load_net_ledger(mt5_id)] == [
            (second_id, 20)]

        # reversal: SELL 0.5 against 0.2 BUY -> the last virtual position
        # closes and a NEW one opens the other way
        ea.market_order("EURUSD.r", "SELL", 0.5)
        yield _wait_until(lambda: len(_reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 3
                          and len(_reqs(server, ProtoOANewOrderReq, SLAVE1_ID)) == 3)
        assert _reqs(server, ProtoOAClosePositionReq, SLAVE1_ID)[2].volume == ONE_LOT // 5
        (row,) = repo.load_net_ledger(mt5_id)
        assert (row["side"], row["volume_left"]) == ("SELL", 30)
        reopened = _reqs(server, ProtoOANewOrderReq, SLAVE1_ID)[2]
        assert (reopened.tradeSide, reopened.volume, reopened.clientOrderId) == (
            ProtoOATradeSide.SELL, ONE_LOT * 3 // 10, f"cm{row['virtual_id']}.{SLAVE1_ID}")
        (net,) = ea.positions.values()
        assert (net["side"], net["lots"]) == ("SELL", 0.3)
        assert [d["entry"] for d in ea.deals] == ["IN", "IN", "OUT", "INOUT"]
        master_events = [e["payload"]["normalized"] for e in _events(db, "master_event")
                         if e["payload"].get("source") == "mt5"]
        assert master_events == [
            "MasterPositionOpened", "MasterPositionOpened",
            "MasterPositionClosed", "MasterPositionClosed",
            "MasterPositionClosed", "MasterPositionOpened"]
        assert ticker.errors == []
    finally:
        if ticker:
            ticker.stop()
        _teardown(app, server)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run DB `tests/integration/test_mt5_bridge.py`.
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'copier.testing.fake_ea'` — until Step 1's file exists; with it in place, all twelve scenarios run and pass. (Write the EA first, then the tests, then run: the EA has no test of its own — the scenarios are its test.)

- [ ] **Step 4: Run the tests to verify they pass**

Run DB `tests/integration/test_mt5_bridge.py`.
Expected: `12 passed`.

Then run DB `tests/integration/test_copier_e2e.py` and DB `tests/integration/test_manual_actions.py`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add copier/src/copier/testing/fake_ea.py copier/tests/integration/test_mt5_bridge.py && git commit -m "test(mt5): a fake EA and twelve end-to-end scenarios across both platforms

The fake EA speaks the exact wire format against an in-memory hedging or
netting book (acking, like the real EA, the net position left after a
fill: 0 when it emptied), keeps an ack file across restarts and re-acks
re-delivered ids. Scenarios: cTrader master -> MT5 copy with protection;
partial close and amend; MT5 master -> cTrader followers; a Trade-page
order; Close all across both platforms with verified counts; a terminal
offline (the open expires, the close survives); duplicate delivery after
a restart; an unmatched symbol; a netting follower netting two copies and
closing them one by one by their masters' closes (the last one emptying
the net position), then by the terminal's own close oldest-first; an
opposite-side copy netting against an older copy without closing it; a
netting master's add, add, reduce and reversal fanning out by virtual id.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Order is load-bearing.** Tasks 1–14 each leave the suite green on their own; Task 15 is the only task that changes `build_app`'s composition, and Tasks 16–17 sit on top of it. Do not reorder. Two placements follow from that rule: the `mt5_net_ledger` accessors ship with their table in Task 5 (not with the other repo additions in Task 4), and the registry's ledger views ship with `NetLedger` in Task 9 (not with the registry in Task 6).
- **Five scoped decisions, made on purpose (not TODOs):** (1) an org whose master is MT5 subscribes cTrader quotes on a follower's connection (`_tracker_client_for`), so its cTrader followers' live P&L works; an org with no cTrader account at all has no tracker and its `/state` comes wholly from the registry. (2) The EA normalises prices to the symbol's digits and treats `TRADE_RETCODE_NO_CHANGES` as success; the copier passes master prices through and the lane rounds operator prices (the fake EA models both). (3) `compute_drift` compares volumes ONLY for netting MT5 followers (Task 12b, the spec's "Reconcile compares the sum of active mapping volumes per net position with the terminal's net volume"): per net ticket, the side-signed sum of the active copies against the terminal's net volume — a shortfall is `missing_slave_copy`, an excess `orphan_slave_position`, one item per ticket carrying the delta in `DriftItem.volume`, and `close_orphan` closes exactly that much. Signed, because opposite copies net against each other there and a flat terminal is right when they cancel; so for those tickets check 2's per-copy "vanished" is replaced by the comparison. cTrader and hedging accounts keep today's existence-only checks: volumes are not compared there. (4) A netting follower's `:close` open carries SL/TP `0`, which leaves the net position's levels alone; a netting master's virtual positions are priced at the net position's open price (the ledger stores no entry price; the deal's price reaches followers in `MasterPositionOpened.entry_price` and `master_fill_price`). (5) An ack is paired with the deal it produced by the deal ticket (contract §2 "Acks of an open on a netting follower"; plan 04's `DoOpen` acks the net position left after the fill, 0 when it emptied, and the deal's entry says what the fill did): a copy's own deal — an open's fill whatever its entry, a `:close`'s OUT/INOUT — is never booked as a terminal-side close, an opposite-side copy activates on the net ticket without reducing the older copies, a `:close` reduces its mapping by coid alone, and the tickets of acked deals not yet reported are carried to the next sync (`_mt5_settled_deals`). An operator's own open on the net position names no copy, so its OUT/INOUT deal still reduces the copies oldest-first.
- **Spec coverage check** (spec → task): migration 014 → 1; wire protocol → 2; symbols/auto-match → 3; repo additions incl. `mapping_side` / `reduce_position_mappings_fifo` → 4; migration 015 (`mt5_net_ledger`) + its accessors → 5; registry incl. `margin_mode` → 6; outbox with TTL/redelivery, the netting close as an opposite-side open, `ClosePosition.master_position_id` → 7; Dispatcher seam → 8; ingress incl. `VirtualPosition`/`NetLedger` and the registry's ledger views → 9; `CopierService` split + `SlaveFill` (a close by coid) → 10; `build_routing` canonical keys → 11; `Reconciler._fetch_snapshot` → 12; the netting follower's net position compared with the sum of its copies (`missing_slave_copy` / `orphan_slave_position` with the delta) → 12b; balance_after estimation → 13; `MT5Lane` incl. flatten and queries → 14; `CopierApp` (hello/sync/status, `_query_context`, actions, `get_state`, auth loops skip, offline timer, deals ingest + watermark, netting in both roles: acks paired with their deals, opposite-side copies, the `:close` acked with ticket 0, FIFO closes, the protection override event, the ledger persisted and served) → 15; control endpoints → 16; fake EA (hedging and netting books, the pos-0 ack) + the seven integration scenarios of the spec's Testing section plus the unmatched-symbol Alert and the four netting scenarios (twelve) → 17. The api, dashboard and `.mq5` files are the other three plans.
