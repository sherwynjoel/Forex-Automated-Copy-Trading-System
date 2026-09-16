# Server-Side Risk Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any TradingView alert that omits its own stop-loss/target/trailing gets them filled in by MirrorFleet itself, from a per-org-per-symbol rule, so protection and trailing work with any indicator — not just the two Pine bots that already compute their own.

**Architecture:** A new `org_risk_rules` table (one row per org+symbol: default stop/target distance in points, trailing on/off + its start/step) and a `position_trailing_state` table (the ratchet's memory: best price seen, current stop). The api's webhook order-building code is **unchanged**. All new behavior lives in the copier: `CopierService._after_master_event` fills in the rule's defaults the instant a position opens (using the real fill price, via the existing `AmendPositionSLTP` dispatch path) and seeds trailing state; a new `LoopingCall`, `CopierApp.check_trailing_stops`, walks every tracked position on a timer and ratchets the stop via the existing `amend_position_sltp` method — the same mechanism the manual Trade page's amend button already uses, which already propagates from master to every slave.

**Tech Stack:** Postgres (migration), Python/Twisted (copier), Python/FastAPI (api), React/TypeScript (dashboard).

**Spec:** `docs/superpowers/specs/2026-09-16-server-side-risk-engine-design.md`

## Global Constraints

- `*_points` values are raw price-distance numbers in the instrument's own quote units — same convention as the existing Pine bots' `Start trailing after (points)` / `Trail step (points)` inputs. No unit conversion anywhere in this feature.
- Every call that changes a position's stop-loss or take-profit MUST send both values together — omitting one clears it at the broker (`AmendPositionResource`'s own docstring: "an omitted protection is REMOVED").
- Trailing only ever moves the **stop**. The target never moves once set. (Spec's Goal section, non-goals.)
- A symbol with no `org_risk_rules` row behaves identically to today — no default stop/target, no trailing. This feature only ever adds behavior for symbols the owner has explicitly configured.
- No `LoopingCall` body may raise out of its top-level `try/except` — a failing tick must not stop future ticks (every existing loop in `copier/src/copier/main.py` follows this; the new one must too).

## How tests actually run in this repo

**Corrected during setup — do not use `docker compose run --rm <service> pytest`, it fails: the compose-built images are production-only and have no dev dependencies (no pytest).** The real convention (see `README.md`'s "Per-service test commands"): a local venv per service, against a Postgres reachable from the host. This worktree's own `postgres` is published on **`127.0.0.1:5434`** (not the repo's usual 5433 — that port is the main checkout's, already running, and would collide).

One-time setup (already done for this worktree as of Task 1's dispatch — an implementer only needs this if a venv is somehow missing):
```bash
cd copier && python3 -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows path; use .venv/bin/pip on Linux/Mac
cd ../api && python3 -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
cd ../dashboard && npm install
```

Every `Run:` line below that says `docker compose run --rm copier python -m pytest ...` actually means:
```bash
cd copier && TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:copytrader@127.0.0.1:5434/copytrader" TEST_POSTGRES_DSN="postgresql://copytrader:copytrader@127.0.0.1:5434/copytrader_test" .venv/Scripts/pytest <same args>
```
And every `docker compose run --rm api python -m pytest ...` actually means the same, from `api/` instead of `copier/`. `docker compose run --rm dashboard npm test ...` actually means `cd dashboard && npm test <same args>` (no DB, no env vars needed). Use `.venv/bin/pytest` instead of `.venv/Scripts/pytest` on a Linux/Mac executor.

---

## Task 1: Database migration

**Files:**
- Create: `db/migrations/018_risk_engine.sql`

**Interfaces:**
- Produces: tables `org_risk_rules` (columns: `org_id`, `symbol`, `stop_points`, `target_points`, `trailing_enabled`, `trail_start_points`, `trail_step_points`, `updated_at`) and `position_trailing_state` (columns: `account_id`, `position_id`, `best_price`, `current_stop`, `updated_at`). Every later task reads/writes these exact column names.

- [ ] **Step 1: Write the migration file**

```sql
-- Server-side risk engine: any alert that omits its own stop/target/
-- trailing gets them filled in from a per-org-per-symbol rule, and
-- trailing (moving the stop as price moves favourably) is managed here
-- instead of inside individual Pine scripts. See
-- docs/superpowers/specs/2026-09-16-server-side-risk-engine-design.md.

-- One row per org+symbol the owner has configured. No row = no default,
-- no trailing -- a symbol left unconfigured behaves exactly as before
-- this feature existed.
CREATE TABLE org_risk_rules (
    org_id              BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    symbol              TEXT NOT NULL,
    -- Raw price-distance in the instrument's own quote units, same
    -- convention as the Pine bots' own points inputs. NULL = no default
    -- for that side (the alert's own value, or nothing, is used).
    stop_points         DOUBLE PRECISION,
    target_points       DOUBLE PRECISION,
    trailing_enabled    BOOLEAN NOT NULL DEFAULT false,
    -- Required (enforced in the api layer, not here) when trailing_enabled.
    trail_start_points  DOUBLE PRECISION,
    trail_step_points   DOUBLE PRECISION,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, symbol)
);

-- The trailing ratchet's memory: a stop must only ever move in the
-- favourable direction, which requires remembering the best price a
-- position has reached, not just its current price. One row per
-- currently-tracked position; created when a trailing-enabled position
-- opens, deleted when it closes.
CREATE TABLE position_trailing_state (
    account_id    BIGINT NOT NULL,
    position_id   BIGINT NOT NULL,
    best_price    DOUBLE PRECISION NOT NULL,
    current_stop  DOUBLE PRECISION NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, position_id)
);
```

- [ ] **Step 2: Run the migration locally and confirm both tables exist**

Run (from the repo root, PowerShell):
```
docker compose up migrate
docker compose exec postgres psql -U copytrader -d copytrader -c "\d org_risk_rules" -c "\d position_trailing_state"
```
Expected: both `\d` outputs print the columns listed above with no errors.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/018_risk_engine.sql
git commit -m "feat(db): add org_risk_rules and position_trailing_state tables"
```

---

## Task 2: Copier domain — the trailing ratchet formula

**Files:**
- Create: `copier/src/copier/domain/trailing.py`
- Test: `copier/tests/unit/test_trailing.py`

**Interfaces:**
- Consumes: nothing (pure function, no I/O).
- Produces: `compute_trailed_stop(side: str, entry_price: float, best_price: float, trail_start_points: float, trail_step_points: float, current_stop: float) -> float` — the new stop price, or `current_stop` unchanged if trailing hasn't started yet or the computed value would move the stop backward. Task 4 and Task 5 both call this exact signature.

- [ ] **Step 1: Write the failing tests**

```python
# copier/tests/unit/test_trailing.py
"""The trailing ratchet formula: a direct port of the same step-trail
math already used in the owner's Pine bots (rajan-dollar-bot.pine,
ut-bot-2026-elite.pine), so a rule configured here behaves identically
to what the trader already understands from those scripts.
"""
from copier.domain.trailing import compute_trailed_stop


def test_long_before_trail_start_stop_does_not_move():
    # price has moved 3 points favourably, trail starts at 5 -- too early
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=103.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=95.0)
    assert new_stop == 95.0


def test_long_exactly_at_trail_start_moves_to_first_step():
    # maxFav == trail_start exactly: new_stop = entry + (start - step + 0)
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=105.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=95.0)
    assert new_stop == 104.0  # 100 + (5 - 1 + floor(0/1)*1)


def test_long_past_trail_start_steps_in_whole_increments():
    # maxFav = 107.5, so 2.5 points past start -> floor(2.5/1) = 2 steps
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=107.5,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=95.0)
    assert new_stop == 106.0  # 100 + (5 - 1 + 2*1)


def test_long_never_moves_backward_even_if_best_price_regresses():
    # a stale/out-of-order best_price must not un-ratchet a stop already
    # moved further forward by an earlier, better reading
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=101.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=106.0)
    assert new_stop == 106.0


def test_short_mirrors_long_in_the_opposite_direction():
    new_stop = compute_trailed_stop(
        side="SELL", entry_price=100.0, best_price=93.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=105.0)
    assert new_stop == 96.0  # 100 - (5 - 1 + floor(2/1)*1)


def test_short_never_moves_backward():
    new_stop = compute_trailed_stop(
        side="SELL", entry_price=100.0, best_price=99.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=94.0)
    assert new_stop == 94.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_trailing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copier.domain.trailing'`

- [ ] **Step 3: Write the implementation**

```python
# copier/src/copier/domain/trailing.py
"""The trailing-stop ratchet: entry price + how far favourably price has
moved -> a new stop, never moving backward. A direct port of the same
step-trail formula the owner's Pine bots already run client-side
(rajan-dollar-bot.pine, ut-bot-2026-elite.pine) -- ported here so
MirrorFleet's own trailing behaves identically for a trader who already
understands those bots, whether the position came from one of them or
from any other indicator.
"""
import math


def compute_trailed_stop(side: str, entry_price: float, best_price: float,
                         trail_start_points: float, trail_step_points: float,
                         current_stop: float) -> float:
    """The stop a trailing-enabled position should carry right now.

    `best_price` is the best (most favourable) price the position has
    reached so far, not necessarily its current price -- callers own
    tracking that across ticks. Returns `current_stop` unchanged if
    trailing has not started yet (best_price has not moved
    trail_start_points past entry) or if the computed value would move
    the stop backward -- a stop only ever tightens.
    """
    if side == "BUY":
        favourable = best_price - entry_price
    else:
        favourable = entry_price - best_price

    if favourable < trail_start_points:
        return current_stop

    steps = math.floor((favourable - trail_start_points) / trail_step_points)
    distance = trail_start_points - trail_step_points + steps * trail_step_points

    if side == "BUY":
        candidate = entry_price + distance
        return max(candidate, current_stop)
    else:
        candidate = entry_price - distance
        return min(candidate, current_stop)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_trailing.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add copier/src/copier/domain/trailing.py copier/tests/unit/test_trailing.py
git commit -m "feat(copier): add the trailing-stop ratchet formula"
```

---

## Task 3: Copier repo — data access for risk rules and trailing state

**Files:**
- Modify: `copier/src/copier/db/repo.py`
- Test: `copier/tests/unit/test_repo_risk_rules.py`

**Interfaces:**
- Consumes: `Repo._connect()` (existing context manager), the table schema from Task 1.
- Produces (all new `Repo` methods, called by Task 4 and Task 5):
  - `load_risk_rule(org_id: int, symbol: str) -> RiskRule | None`
  - `upsert_trailing_state(account_id: int, position_id: int, best_price: float, current_stop: float) -> None`
  - `load_trailing_state(account_id: int, position_id: int) -> TrailingState | None`
  - `delete_trailing_state(account_id: int, position_id: int) -> None`
  - `load_all_trailing_state_rows() -> list[TrailingState]`
  - New types `RiskRule` and `TrailingState` (plain `@dataclass`, matching the style of existing row types such as `AccountRow`/`OrgRow` in this same file).

- [ ] **Step 1: Find the existing row-type pattern to match**

Run: `docker compose run --rm copier grep -n "class OrgRow" -A 6 src/copier/db/repo.py`
Expected: shows a small `@dataclass` with plain fields — match this exact shape for `RiskRule`/`TrailingState`.

- [ ] **Step 2: Write the failing tests**

```python
# copier/tests/unit/test_repo_risk_rules.py
"""Repo access for org_risk_rules and position_trailing_state. Uses the
same fixture/style already established for this file's other repo
tests -- a real Postgres connection against the test database, one test
per behavior, no mocking of the DB layer itself.
"""
import pytest
from copier.db.repo import Repo


@pytest.fixture
def repo(postgres_dsn):
    # postgres_dsn: existing fixture already used by this test package's
    # other repo tests (see copier/tests/unit/conftest.py) -- if the
    # fixture name differs, use whatever conftest.py already provides
    # for a live-DB Repo in this test directory.
    return Repo(postgres_dsn)


def test_load_risk_rule_returns_none_when_unconfigured(repo, db):
    assert repo.load_risk_rule(org_id=999, symbol="XAUUSD") is None


def test_load_risk_rule_returns_the_configured_row(repo, db):
    db.execute(
        "INSERT INTO org_risk_rules (org_id, symbol, stop_points, target_points, "
        "trailing_enabled, trail_start_points, trail_step_points) "
        "VALUES (1, 'XAUUSD', 50.0, 150.0, true, 10.0, 5.0)")
    rule = repo.load_risk_rule(org_id=1, symbol="XAUUSD")
    assert rule.stop_points == 50.0
    assert rule.target_points == 150.0
    assert rule.trailing_enabled is True
    assert rule.trail_start_points == 10.0
    assert rule.trail_step_points == 5.0


def test_upsert_then_load_trailing_state_round_trips(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    state = repo.load_trailing_state(account_id=1, position_id=100)
    assert state.best_price == 4200.0
    assert state.current_stop == 4150.0


def test_upsert_trailing_state_overwrites_on_conflict(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4210.0, current_stop=4160.0)
    state = repo.load_trailing_state(account_id=1, position_id=100)
    assert state.best_price == 4210.0
    assert state.current_stop == 4160.0


def test_delete_trailing_state_removes_the_row(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    repo.delete_trailing_state(account_id=1, position_id=100)
    assert repo.load_trailing_state(account_id=1, position_id=100) is None


def test_load_all_trailing_state_rows_returns_every_tracked_position(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    repo.upsert_trailing_state(account_id=2, position_id=200,
                               best_price=1.09, current_stop=1.085)
    rows = repo.load_all_trailing_state_rows()
    assert {(r.account_id, r.position_id) for r in rows} == {(1, 100), (2, 200)}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_repo_risk_rules.py -v`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'load_risk_rule'`

- [ ] **Step 4: Add the row types and methods to `repo.py`**

Add near the other row `@dataclass`es (same section as `OrgRow`/`AccountRow`):

```python
@dataclass
class RiskRule:
    org_id: int
    symbol: str
    stop_points: float | None
    target_points: float | None
    trailing_enabled: bool
    trail_start_points: float | None
    trail_step_points: float | None


@dataclass
class TrailingState:
    account_id: int
    position_id: int
    best_price: float
    current_stop: float
```

Add these methods to the `Repo` class:

```python
    def load_risk_rule(self, org_id: int, symbol: str) -> RiskRule | None:
        """The org's configured risk rule for one symbol, if any. None
        means: no default stop/target, no trailing -- this feature adds
        nothing for that symbol."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT org_id, symbol, stop_points, target_points, "
                "trailing_enabled, trail_start_points, trail_step_points "
                "FROM org_risk_rules WHERE org_id = %s AND symbol = %s",
                (org_id, symbol),
            ).fetchone()
        if not row:
            return None
        return RiskRule(org_id=row[0], symbol=row[1], stop_points=row[2],
                        target_points=row[3], trailing_enabled=row[4],
                        trail_start_points=row[5], trail_step_points=row[6])

    def upsert_trailing_state(self, account_id: int, position_id: int,
                              best_price: float, current_stop: float) -> None:
        """Create or overwrite one position's trailing memory."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO position_trailing_state "
                "(account_id, position_id, best_price, current_stop, updated_at) "
                "VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT (account_id, position_id) DO UPDATE SET "
                "best_price = EXCLUDED.best_price, current_stop = EXCLUDED.current_stop, "
                "updated_at = now()",
                (account_id, position_id, best_price, current_stop),
            )

    def load_trailing_state(self, account_id: int, position_id: int) -> TrailingState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT account_id, position_id, best_price, current_stop "
                "FROM position_trailing_state WHERE account_id = %s AND position_id = %s",
                (account_id, position_id),
            ).fetchone()
        if not row:
            return None
        return TrailingState(account_id=row[0], position_id=row[1],
                             best_price=row[2], current_stop=row[3])

    def delete_trailing_state(self, account_id: int, position_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM position_trailing_state WHERE account_id = %s AND position_id = %s",
                (account_id, position_id),
            )

    def load_all_trailing_state_rows(self) -> list[TrailingState]:
        """Every currently-tracked position, across every org and
        account -- what the trailing LoopingCall sweeps each tick."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account_id, position_id, best_price, current_stop "
                "FROM position_trailing_state"
            ).fetchall()
        return [TrailingState(account_id=r[0], position_id=r[1],
                              best_price=r[2], current_stop=r[3]) for r in rows]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_repo_risk_rules.py -v`
Expected: all 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add copier/src/copier/db/repo.py copier/tests/unit/test_repo_risk_rules.py
git commit -m "feat(copier): add repo access for org_risk_rules and position_trailing_state"
```

---

## Task 4: Copier engine — fill in defaults and seed trailing state on fill

**Files:**
- Modify: `copier/src/copier/engine/service.py`
- Test: `copier/tests/unit/test_service_risk_engine.py`

**Interfaces:**
- Consumes: `Repo.load_risk_rule` and `Repo.upsert_trailing_state`/`delete_trailing_state` (Task 3), `compute_trailed_stop` is NOT used here (only Task 5 trails; this task only seeds), `AmendPositionSLTP` and `Dispatcher.dispatch` (existing), `MasterPositionOpened`/`MasterPositionClosed` (existing, `copier/src/copier/domain/models.py`).
- Produces: `_after_master_event` gains a `master_account_id` parameter (both call sites updated) and now fills in rule defaults + seeds/clears `position_trailing_state`. Nothing outside this file calls `_after_master_event` directly (it is private), so this is a self-contained change.

- [ ] **Step 1: Write the failing tests**

```python
# copier/tests/unit/test_service_risk_engine.py
"""The risk-engine fill-in step: when a master position opens with no
stop/target (a bare alert from an indicator that doesn't compute its
own), and the org has a configured org_risk_rules row for that symbol,
CopierService amends the position to the rule's defaults and seeds
position_trailing_state if trailing is on. An alert that already carries
its own stop/target is left exactly alone -- MirrorFleet never overrides
what an indicator explicitly sent.
"""
from unittest.mock import MagicMock
from copier.engine.service import CopierService
from copier.domain.models import MasterPositionOpened, MasterPositionClosed, Side
from copier.domain.models import AmendPositionSLTP
from copier.db.repo import RiskRule


def _service(repo):
    return CopierService(
        repo=repo, dispatcher=MagicMock(),
        routing_provider=lambda: MagicMock(slaves_by_org={}),
        master_symbols_by_org={},
    )


def test_bare_open_with_no_rule_does_nothing():
    repo = MagicMock()
    repo.load_risk_rule.return_value = None
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service._dispatcher.dispatch.assert_not_called()
    repo.upsert_trailing_state.assert_not_called()


def test_bare_open_with_a_rule_amends_to_the_defaults():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=None,
                                 take_profit=None, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    (intents,), kwargs = service._dispatcher.dispatch.call_args
    assert kwargs == {"org_id": 1}
    assert intents == [AmendPositionSLTP(10, 1, 4150.0, 4350.0)]
    repo.upsert_trailing_state.assert_not_called()  # trailing_enabled is False


def test_open_that_already_carries_its_own_levels_is_left_alone():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=50.0, target_points=150.0,
        trailing_enabled=False, trail_start_points=None, trail_step_points=None)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=4100.0,
                                 take_profit=4400.0, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    service._dispatcher.dispatch.assert_not_called()


def test_trailing_enabled_seeds_state_even_when_alert_supplied_its_own_stop():
    repo = MagicMock()
    repo.load_risk_rule.return_value = RiskRule(
        org_id=1, symbol="XAUUSD", stop_points=None, target_points=None,
        trailing_enabled=True, trail_start_points=10.0, trail_step_points=5.0)
    service = _service(repo)
    event = MasterPositionOpened(position_id=1, symbol_name="XAUUSD", side=Side.BUY,
                                 volume=100000, lot_size=100, stop_loss=4150.0,
                                 take_profit=4400.0, entry_price=4200.0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    repo.upsert_trailing_state.assert_called_once_with(
        account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0)


def test_full_close_clears_trailing_state():
    repo = MagicMock()
    service = _service(repo)
    event = MasterPositionClosed(position_id=1, symbol_name="XAUUSD",
                                 closed_volume=100000, remaining_volume=0)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    repo.delete_trailing_state.assert_called_once_with(account_id=10, position_id=1)


def test_partial_close_does_not_clear_trailing_state():
    repo = MagicMock()
    service = _service(repo)
    event = MasterPositionClosed(position_id=1, symbol_name="XAUUSD",
                                 closed_volume=50000, remaining_volume=50000)

    service._after_master_event(org_id=1, master_account_id=10, normalized=event)

    repo.delete_trailing_state.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_service_risk_engine.py -v`
Expected: FAIL — `TypeError: _after_master_event() got an unexpected keyword argument 'master_account_id'`

- [ ] **Step 3: Update `_after_master_event` and both call sites**

In `copier/src/copier/engine/service.py`, change the signature and body:

```python
    def _after_master_event(self, org_id: int, master_account_id: int,
                            normalized: MasterEvent) -> None:
        # Schedule pending fill alert if this is a pending fill
        if isinstance(normalized, MasterPendingFilled):
            self._schedule_pending_fill_check(org_id, normalized)

        self._apply_risk_engine(org_id, master_account_id, normalized)

        # The master's positions/orders just changed; let /state catch up now.
        self._notify_positions_changed(org_id)

    def _apply_risk_engine(self, org_id: int, master_account_id: int,
                           normalized: MasterEvent) -> None:
        """Fill in a configured symbol's default stop/target on a bare
        open, and track/clear trailing state -- the server-side half of
        every indicator getting protection, not just the two Pine bots
        that compute their own. See
        docs/superpowers/specs/2026-09-16-server-side-risk-engine-design.md.

        Runs AFTER decide/dispatch/audit (called from _after_master_event,
        itself always last): the copy fan-out to every slave is never
        delayed or risked by this step.
        """
        if isinstance(normalized, MasterPositionClosed):
            if normalized.remaining_volume == 0:
                self._repo.delete_trailing_state(
                    account_id=master_account_id, position_id=normalized.position_id)
            return

        if not isinstance(normalized, MasterPositionOpened):
            return

        rule = self._repo.load_risk_rule(org_id, normalized.symbol_name)
        if rule is None:
            return

        entry = normalized.entry_price
        if entry is None:
            return  # no fill price to measure a *_points distance from

        side_sign = 1 if normalized.side == Side.BUY else -1
        stop_loss = normalized.stop_loss
        if stop_loss is None and rule.stop_points is not None:
            stop_loss = entry - side_sign * rule.stop_points
        take_profit = normalized.take_profit
        if take_profit is None and rule.target_points is not None:
            take_profit = entry + side_sign * rule.target_points

        if (stop_loss, take_profit) != (normalized.stop_loss, normalized.take_profit):
            try:
                self._dispatcher.dispatch(
                    [AmendPositionSLTP(master_account_id, normalized.position_id,
                                       stop_loss, take_profit)],
                    org_id=org_id,
                )
            except Exception:
                log.exception(
                    "risk engine: could not amend master position %s on account %s",
                    normalized.position_id, master_account_id)

        if rule.trailing_enabled and stop_loss is not None:
            self._repo.upsert_trailing_state(
                account_id=master_account_id, position_id=normalized.position_id,
                best_price=entry, current_stop=stop_loss)
```

Update both call sites (the two places that currently call `self._after_master_event(org_id, normalized)`):

```python
        # in _handle_master_event, replace:
        self._after_master_event(org_id, normalized)
        # with:
        self._after_master_event(org_id, master_account_id, normalized)
```
```python
        # in act_on_master_event, replace:
        self._after_master_event(org_id, normalized)
        # with:
        self._after_master_event(org_id, master_account_id, normalized)
```

Add the import at the top of the file alongside the existing `MasterPositionOpened, MasterPositionClosed` import:
```python
from copier.domain.models import (
    MANUAL_ORDER_LABEL, SymbolInfo, MasterEvent, MasterPendingFilled, AmendPositionSLTP,
    MasterPositionOpened, MasterPositionClosed, MasterPositionSLTPAmended, Side)
```
(only add `Side` if it is not already imported here — check the existing import line first; `MasterPositionOpened, MasterPositionClosed` are already imported per the file's current header.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_service_risk_engine.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Run the full copier unit suite to confirm nothing else broke**

Run: `docker compose run --rm copier python -m pytest tests/unit -v`
Expected: all tests PASS (the two `_after_master_event` call sites were the only other code touching this signature)

- [ ] **Step 6: Commit**

```bash
git add copier/src/copier/engine/service.py copier/tests/unit/test_service_risk_engine.py
git commit -m "feat(copier): fill in risk-rule defaults and seed trailing state on fill"
```

---

## Task 5: Copier — the trailing check loop

**Files:**
- Modify: `copier/src/copier/main.py`
- Modify: `copier/src/copier/db/repo.py` (four new methods: `get_org_for_account`, `get_position_side_and_entry`, `load_risk_rule_for_position`, `load_position_protection` — additive only, alongside Task 3's earlier additions to this same file, which already exist by the time this task runs)
- Test: `copier/tests/unit/test_trailing_loop.py`

**Interfaces:**
- Consumes: `Repo.load_all_trailing_state_rows`, `Repo.delete_trailing_state`, `Repo.upsert_trailing_state` (Task 3), `compute_trailed_stop` (Task 2), `CopierApp.amend_position_sltp` (existing, line ~1847), `CopierApp._is_mt5`, `self.state_trackers`, `self.mt5_registry` (existing).
- Produces: `CopierApp.check_trailing_stops()` — new method, wired as a `LoopingCall` in `boot()`. Nothing else calls this method.

- [ ] **Step 1: Write the failing tests**

```python
# copier/tests/unit/test_trailing_loop.py
"""CopierApp.check_trailing_stops: the periodic sweep that ratchets every
trailing-enabled position's stop. Mirrors the style of the other
LoopingCall-body tests in this file (test_main.py's check_mt5_offline
coverage) -- a real CopierApp with its repo/registries stubbed, asserting
on what amend_position_sltp was called with.
"""
from unittest.mock import MagicMock, patch
from copier.main import CopierApp
from copier.db.repo import TrailingState


def _app_with_price(current_price, is_mt5=False):
    app = CopierApp.__new__(CopierApp)  # bypass __init__'s broker wiring
    app.repo = MagicMock()
    app.amend_position_sltp = MagicMock(return_value={"status": "submitted"})
    app._is_mt5 = MagicMock(return_value=is_mt5)
    if is_mt5:
        app.mt5_registry = MagicMock()
        app.mt5_registry.position_price = MagicMock(return_value=current_price)
    else:
        tracker = MagicMock()
        tracker.position_current_price = MagicMock(return_value=current_price)
        app.state_trackers = {1: tracker}
        app.repo.get_org_for_account = MagicMock(return_value=1)
    return app


def test_a_position_past_trail_start_gets_amended():
    app = _app_with_price(current_price=4212.0)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0)]
    app.repo.get_position_side_and_entry = MagicMock(return_value=("BUY", 4200.0))
    app.repo.load_risk_rule_for_position = MagicMock(
        return_value=MagicMock(trail_start_points=10.0, trail_step_points=5.0))
    app.repo.load_position_protection = MagicMock(return_value=(4150.0, 4400.0))

    app.check_trailing_stops()

    app.amend_position_sltp.assert_called_once_with(10, 1, 4207.0, 4400.0, actor="risk-engine")
    app.repo.upsert_trailing_state.assert_called_once_with(
        account_id=10, position_id=1, best_price=4212.0, current_stop=4207.0)


def test_a_position_not_found_live_is_cleaned_up():
    app = _app_with_price(current_price=None)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0)]

    app.check_trailing_stops()

    app.repo.delete_trailing_state.assert_called_once_with(account_id=10, position_id=1)
    app.amend_position_sltp.assert_not_called()


def test_a_failing_amend_does_not_stop_the_rest_of_the_tick():
    app = _app_with_price(current_price=4212.0)
    app.repo.load_all_trailing_state_rows.return_value = [
        TrailingState(account_id=10, position_id=1, best_price=4200.0, current_stop=4150.0),
        TrailingState(account_id=10, position_id=2, best_price=4200.0, current_stop=4150.0)]
    app.repo.get_position_side_and_entry = MagicMock(return_value=("BUY", 4200.0))
    app.repo.load_risk_rule_for_position = MagicMock(
        return_value=MagicMock(trail_start_points=10.0, trail_step_points=5.0))
    app.repo.load_position_protection = MagicMock(return_value=(4150.0, 4400.0))
    app.amend_position_sltp = MagicMock(side_effect=[Exception("boom"), {"status": "submitted"}])

    app.check_trailing_stops()  # must not raise

    assert app.amend_position_sltp.call_count == 2
```

**Note for the implementer**: the test doubles above assume three small repo helpers this task also adds — `get_org_for_account`, `get_position_side_and_entry(account_id, position_id)`, `load_risk_rule_for_position(account_id, position_id)` (looks up the position's current symbol/org and joins to `org_risk_rules`), and `load_position_protection(account_id, position_id)` (the position's current stop/take-profit, so the take-profit half of the amend is never guessed). Before writing these, grep `repo.py` for `_org_for_account` on `CopierApp` (main.py line ~1907, used by `amend_position_sltp` itself) and reuse the same org-resolution logic rather than inventing a second way to do it. Write each with the same `with self._connect() as conn:` pattern as Task 3's methods, join `org_risk_rules` through whatever table already maps a live position to its symbol and org (the same source `amend_position_sltp`'s own `_query_context`/`_digits_for_position` already use for cTrader; the MT5 equivalent in `mt5_lane`) — read those two existing methods first (`main.py` lines ~1869-1887) to match, rather than guessing the join.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_trailing_loop.py -v`
Expected: FAIL — `AttributeError: 'CopierApp' object has no attribute 'check_trailing_stops'`

- [ ] **Step 3: Add the constant**

Near `MT5_OFFLINE_CHECK_INTERVAL_S = 5.0` at the top of `main.py`:

```python
# How often every trailing-enabled position's stop is re-checked and
# ratcheted forward. Frequent enough to feel responsive on gold/majors,
# infrequent enough to stay well inside the existing TokenBucket's
# request-rate budget alongside everything else it already throttles.
TRAILING_CHECK_INTERVAL_S = 5.0
```

- [ ] **Step 4: Write `check_trailing_stops`**

Add as a method on `CopierApp`, near `check_mt5_offline`:

```python
    def check_trailing_stops(self) -> None:
        """LoopingCall body (TRAILING_CHECK_INTERVAL_S): ratchet the stop
        of every trailing-enabled position forward as price moves
        favourably, using the same amend_position_sltp a person uses
        from the Trade page -- which already propagates to every slave.
        Never raises: a LoopingCall whose Deferred fails stops looping
        permanently, and one bad price read or one failed amend must not
        silently end trailing for every other open position for the rest
        of the process's life."""
        for state in self.repo.load_all_trailing_state_rows():
            try:
                self._check_one_trailing_position(state)
            except Exception:
                log.exception("trailing check failed for account %s position %s",
                              state.account_id, state.position_id)

    def _check_one_trailing_position(self, state) -> None:
        current_price = self._current_position_price(state.account_id, state.position_id)
        if current_price is None:
            # Position is gone from both live sources -- closed, stopped
            # out, anything. MasterPositionClosed should already have
            # cleared this row; this is the defensive second path.
            self.repo.delete_trailing_state(
                account_id=state.account_id, position_id=state.position_id)
            return

        side, entry_price = self.repo.get_position_side_and_entry(
            state.account_id, state.position_id)
        rule = self.repo.load_risk_rule_for_position(state.account_id, state.position_id)
        if rule is None or not rule.trailing_enabled:
            return

        best_price = max(state.best_price, current_price) if side == "BUY" \
            else min(state.best_price, current_price)

        from copier.domain.trailing import compute_trailed_stop
        new_stop = compute_trailed_stop(
            side=side, entry_price=entry_price, best_price=best_price,
            trail_start_points=rule.trail_start_points,
            trail_step_points=rule.trail_step_points,
            current_stop=state.current_stop)

        if new_stop != state.current_stop:
            _, take_profit = self.repo.load_position_protection(
                state.account_id, state.position_id)
            self.amend_position_sltp(state.account_id, state.position_id,
                                     new_stop, take_profit, actor="risk-engine")

        self.repo.upsert_trailing_state(
            account_id=state.account_id, position_id=state.position_id,
            best_price=best_price, current_stop=new_stop)

    def _current_position_price(self, account_id: int, position_id: int) -> float | None:
        """One position's live current price, cTrader or MT5. No single
        existing helper covers both platforms -- branches the same way
        the rest of this class already does via _is_mt5."""
        if self._is_mt5(account_id):
            return self.mt5_registry.position_price(account_id, position_id)
        org_id = self.repo.get_org_for_account(account_id)
        tracker = self.state_trackers.get(org_id)
        if tracker is None:
            return None
        return tracker.position_current_price(position_id)
```

**Note for the implementer**: `mt5_registry.position_price` and `state_trackers[...].position_current_price` as named above are the simplest plausible names for "one position's live price" on each existing tracker class, but were not directly confirmed against `mt5_registry.py`/the tracker class during planning — grep both files for how `get_ticks` (main.py line ~2438) reads `current_price` per position (`tracker.snapshot()[...]["current_price"]` for cTrader; the MT5 equivalent inside `get_state`, main.py line ~2466) and either reuse that exact accessor or add a one-line helper method on the tracker/registry class with this same signature if a direct by-position lookup doesn't already exist. Update the test doubles in Step 1 to match whatever the real accessor turns out to be named.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `docker compose run --rm copier python -m pytest tests/unit/test_trailing_loop.py -v`
Expected: all 3 tests PASS (after reconciling the accessor names per the note above)

- [ ] **Step 6: Wire the LoopingCall in `boot()`**

Add alongside the other loop registrations (same block as `mt5_offline_call`):

```python
    # Ratchets every trailing-enabled position's stop forward as price
    # moves favourably; a person would otherwise have to do this by hand
    # from the Trade page's amend button.
    trailing_check_call = task.LoopingCall(app.check_trailing_stops)
    trailing_check_call.clock = reactor_
    app.trailing_check_call = trailing_check_call

    def _start_trailing_check_loop():
        d = trailing_check_call.start(TRAILING_CHECK_INTERVAL_S, now=False)
        d.addErrback(lambda f: log.error("trailing check loop stopped: %s", f))

    reactor_.callWhenRunning(_start_trailing_check_loop)
```

- [ ] **Step 7: Run the full copier unit suite**

Run: `docker compose run --rm copier python -m pytest tests/unit -v`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add copier/src/copier/main.py copier/tests/unit/test_trailing_loop.py
git commit -m "feat(copier): add the trailing-stop check loop"
```

---

## Task 6: API — risk rules CRUD endpoints

**Files:**
- Modify: `api/src/api/routes/webhooks.py`
- Test: `api/tests/test_risk_rules.py`

**Interfaces:**
- Consumes: `require_org_role`, `audit` (existing, already imported in this file), `get_conn` (existing).
- Produces: `GET /api/orgs/{org_id}/risk-rules`, `PUT /api/orgs/{org_id}/risk-rules/{symbol}`, `DELETE /api/orgs/{org_id}/risk-rules/{symbol}` — added inside the existing `create_webhook_settings_router()` function. No new router, no `main.py` change (per the spec's decision — this function is already registered).

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/test_risk_rules.py
"""Risk-rule CRUD: admin-only, audited, same pattern as the existing
webhook-settings endpoints in this same router. A symbol with no rule
simply does not appear in the list -- there is no "disabled" row state,
only present or absent.
"""
def test_list_starts_empty(org_client):
    client, org_id, seed = org_client
    seed(role="admin")
    r = client.get(f"/api/orgs/{org_id}/risk-rules")
    assert r.status_code == 200
    assert r.json() == []


def test_put_creates_a_rule(org_client, db):
    client, org_id, seed = org_client
    seed(role="admin")
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": 150.0,
        "trailing_enabled": False, "trail_start_points": None,
        "trail_step_points": None})
    assert r.status_code == 200
    r2 = client.get(f"/api/orgs/{org_id}/risk-rules")
    assert r2.json() == [{"symbol": "XAUUSD", "stop_points": 50.0,
                          "target_points": 150.0, "trailing_enabled": False,
                          "trail_start_points": None, "trail_step_points": None}]


def test_put_normalises_the_symbol_the_same_way_alerts_do(org_client):
    client, org_id, seed = org_client
    seed(role="admin")
    r = client.put(f"/api/orgs/{org_id}/risk-rules/OANDA:XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None})
    assert r.status_code == 200
    assert r.json()["symbol"] == "XAUUSD"


def test_put_requires_trail_start_and_step_when_trailing_enabled(org_client):
    client, org_id, seed = org_client
    seed(role="admin")
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": None, "target_points": None, "trailing_enabled": True,
        "trail_start_points": None, "trail_step_points": None})
    assert r.status_code == 400


def test_put_overwrites_an_existing_rule(org_client):
    client, org_id, seed = org_client
    seed(role="admin")
    client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None})
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 60.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None})
    assert r.json()["stop_points"] == 60.0


def test_delete_removes_the_rule(org_client):
    client, org_id, seed = org_client
    seed(role="admin")
    client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None})
    r = client.delete(f"/api/orgs/{org_id}/risk-rules/XAUUSD")
    assert r.status_code == 204
    assert client.get(f"/api/orgs/{org_id}/risk-rules").json() == []


def test_viewer_role_cannot_write(org_client):
    client, org_id, seed = org_client
    seed(role="viewer")
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None})
    assert r.status_code == 403


def test_put_is_audited(org_client, db):
    client, org_id, seed = org_client
    seed(role="admin")
    client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None})
    row = db.execute(
        "SELECT payload FROM events WHERE org_id = %s AND payload->>'action' = "
        "'risk_rule_set' ORDER BY ts DESC LIMIT 1", (org_id,)).fetchone()
    assert row is not None
```

**Note for the implementer**: `org_client`/`seed`/`db` fixture shapes should already exist in `api/tests/conftest.py` (the same fixtures `test_webhooks.py` uses) — check that file's existing tests (e.g. `test_a_buy_places_a_market_order_on_the_master_as_tradingview` in `test_webhooks.py`) for the exact `seed(role=...)` call shape before writing these; adjust argument names if they differ from the guess above.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm api python -m pytest tests/test_risk_rules.py -v`
Expected: FAIL — 404s (routes do not exist yet)

- [ ] **Step 3: Add the Pydantic model and endpoints**

In `api/src/api/routes/webhooks.py`, near `WebhookUpdate`:

```python
class RiskRuleUpdate(BaseModel):
    stop_points: Optional[float] = None
    target_points: Optional[float] = None
    trailing_enabled: bool = False
    trail_start_points: Optional[float] = None
    trail_step_points: Optional[float] = None
```

Inside `create_webhook_settings_router()`, alongside the other endpoints on the same `router`:

```python
    @router.get("/risk-rules", response_model=list)
    async def list_risk_rules(ctx: OrgContext = Depends(require_org_role("viewer")),
                              conn: psycopg.Connection = Depends(get_conn)):
        rows = conn.execute(
            "SELECT symbol, stop_points, target_points, trailing_enabled, "
            "trail_start_points, trail_step_points FROM org_risk_rules "
            "WHERE org_id = %s ORDER BY symbol", (ctx.org_id,)).fetchall()
        return [{"symbol": r[0], "stop_points": r[1], "target_points": r[2],
                "trailing_enabled": r[3], "trail_start_points": r[4],
                "trail_step_points": r[5]} for r in rows]

    @router.put("/risk-rules/{symbol}", response_model=Dict[str, Any])
    async def put_risk_rule(symbol: str, body: RiskRuleUpdate,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)):
        """Set (create or replace) the org's default stop/target/trailing
        for one symbol. A rule with no configured stop or target simply
        leaves that side unfilled -- the alert's own value, or nothing,
        still applies."""
        try:
            clean_symbol = normalise_ticker(symbol)
        except AlertError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if body.trailing_enabled and (body.trail_start_points is None
                                      or body.trail_step_points is None):
            raise HTTPException(
                status_code=400,
                detail="trail_start_points and trail_step_points are required when trailing_enabled")
        conn.execute(
            "INSERT INTO org_risk_rules (org_id, symbol, stop_points, target_points, "
            "trailing_enabled, trail_start_points, trail_step_points, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (org_id, symbol) DO UPDATE SET "
            "stop_points = EXCLUDED.stop_points, target_points = EXCLUDED.target_points, "
            "trailing_enabled = EXCLUDED.trailing_enabled, "
            "trail_start_points = EXCLUDED.trail_start_points, "
            "trail_step_points = EXCLUDED.trail_step_points, updated_at = now()",
            (ctx.org_id, clean_symbol, body.stop_points, body.target_points,
             body.trailing_enabled, body.trail_start_points, body.trail_step_points))
        audit(conn, ctx.org_id, ctx.user_email, "risk_rule_set",
              {"symbol": clean_symbol, "stop_points": body.stop_points,
               "target_points": body.target_points,
               "trailing_enabled": body.trailing_enabled})
        return {"symbol": clean_symbol, "stop_points": body.stop_points,
                "target_points": body.target_points,
                "trailing_enabled": body.trailing_enabled,
                "trail_start_points": body.trail_start_points,
                "trail_step_points": body.trail_step_points}

    @router.delete("/risk-rules/{symbol}", status_code=204)
    async def delete_risk_rule(symbol: str,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)):
        clean_symbol = normalise_ticker(symbol)
        conn.execute("DELETE FROM org_risk_rules WHERE org_id = %s AND symbol = %s",
                    (ctx.org_id, clean_symbol))
        audit(conn, ctx.org_id, ctx.user_email, "risk_rule_deleted", {"symbol": clean_symbol})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm api python -m pytest tests/test_risk_rules.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Run the full api test suite to confirm nothing else broke**

Run: `docker compose run --rm api python -m pytest tests -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add api/src/api/routes/webhooks.py api/tests/test_risk_rules.py
git commit -m "feat(api): add risk-rules CRUD endpoints"
```

---

## Task 7: Dashboard — Symbol Risk Rules section on the Automation page

**Files:**
- Modify: `dashboard/src/pages/Automation.tsx`
- Modify: `dashboard/src/lib/types.ts`
- Test: `dashboard/src/pages/Automation.test.tsx`

**Interfaces:**
- Consumes: `orgApi<T>(orgId, tail, init?)` (existing, `dashboard/src/lib/api.ts`), the endpoints from Task 6 (`GET/PUT/DELETE .../risk-rules...`).
- Produces: a `RiskRule` type in `types.ts`; a rendered "Symbol Risk Rules" section on the Automation page with add/edit/delete, below the existing Limits section.

- [ ] **Step 1: Add the type**

In `dashboard/src/lib/types.ts`, alongside `WebhookSettings`:

```typescript
export interface RiskRule {
  symbol: string
  stop_points: number | null
  target_points: number | null
  trailing_enabled: boolean
  trail_start_points: number | null
  trail_step_points: number | null
}
```

- [ ] **Step 2: Write the failing test**

```typescript
// dashboard/src/pages/Automation.test.tsx — add to the existing test file
import { RiskRule } from '../lib/types'

test('lists existing risk rules and can add a new one', async () => {
  const rules: RiskRule[] = [
    { symbol: 'XAUUSD', stop_points: 50, target_points: 150,
      trailing_enabled: false, trail_start_points: null, trail_step_points: null },
  ]
  server.use(
    rest.get('/api/orgs/:orgId/risk-rules', (req, res, ctx) => res(ctx.json(rules))),
    rest.put('/api/orgs/:orgId/risk-rules/:symbol', (req, res, ctx) =>
      res(ctx.json({ symbol: 'EURUSD', stop_points: 20, target_points: 60,
                     trailing_enabled: false, trail_start_points: null,
                     trail_step_points: null }))),
  )
  render(<Automation />)

  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()

  await userEvent.type(screen.getByLabelText(/symbol/i), 'EURUSD')
  await userEvent.type(screen.getByLabelText(/^stop/i), '20')
  await userEvent.type(screen.getByLabelText(/^target/i), '60')
  await userEvent.click(screen.getByRole('button', { name: /add rule/i }))

  expect(await screen.findByText('EURUSD')).toBeInTheDocument()
})

test('can delete a risk rule', async () => {
  const rules: RiskRule[] = [
    { symbol: 'XAUUSD', stop_points: 50, target_points: 150,
      trailing_enabled: false, trail_start_points: null, trail_step_points: null },
  ]
  server.use(
    rest.get('/api/orgs/:orgId/risk-rules', (req, res, ctx) => res(ctx.json(rules))),
    rest.delete('/api/orgs/:orgId/risk-rules/:symbol', (req, res, ctx) => res(ctx.status(204))),
  )
  render(<Automation />)
  await screen.findByText('XAUUSD')

  await userEvent.click(screen.getByRole('button', { name: /remove xauusd/i }))

  await waitFor(() => expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument())
})
```

**Note for the implementer**: check the top of `Automation.test.tsx` for the exact mock-server setup (`msw`'s `rest`/`server`, or a different mocking approach already used by this file's existing tests) and match that pattern exactly — the `server.use(...)` shape above assumes `msw`; adjust to whatever this repo's existing Automation tests actually use if different.

- [ ] **Step 3: Run the test to verify it fails**

Run: `docker compose run --rm dashboard npm test -- Automation`
Expected: FAIL — no "Symbol Risk Rules" section, `findByText('XAUUSD')` times out

- [ ] **Step 4: Add the section to `Automation.tsx`**

Add state near the existing `settings`/`draft` state at the top of the component:

```typescript
const [riskRules, setRiskRules] = useState<RiskRule[]>([])
const [newRule, setNewRule] = useState({ symbol: '', stop_points: '', target_points: '',
                                         trailing_enabled: false, trail_start_points: '',
                                         trail_step_points: '' })
```

Add to the existing `refresh` (or equivalent load-on-mount) function, alongside the existing `orgApi<WebhookSettings>(orgId, 'webhook')` call:

```typescript
setRiskRules(await orgApi<RiskRule[]>(orgId, 'risk-rules'))
```

Add handlers near the existing `toggleEnabled`/`put` functions:

```typescript
const addRiskRule = async () => {
  const symbol = newRule.symbol.trim().toUpperCase()
  if (!symbol) return
  const body = {
    stop_points: newRule.stop_points ? Number(newRule.stop_points) : null,
    target_points: newRule.target_points ? Number(newRule.target_points) : null,
    trailing_enabled: newRule.trailing_enabled,
    trail_start_points: newRule.trail_start_points ? Number(newRule.trail_start_points) : null,
    trail_step_points: newRule.trail_step_points ? Number(newRule.trail_step_points) : null,
  }
  const saved = await orgApi<RiskRule>(orgId, `risk-rules/${symbol}`, {
    method: 'PUT', body: JSON.stringify(body) })
  setRiskRules(prev => [...prev.filter(r => r.symbol !== saved.symbol), saved])
  setNewRule({ symbol: '', stop_points: '', target_points: '',
              trailing_enabled: false, trail_start_points: '', trail_step_points: '' })
}

const removeRiskRule = async (symbol: string) => {
  await orgApi(orgId, `risk-rules/${symbol}`, { method: 'DELETE' })
  setRiskRules(prev => prev.filter(r => r.symbol !== symbol))
}
```

Add the section's JSX, matching the existing Limits section's shell (`<section className="rounded-lg border border-line bg-card p-5 space-y-4">`), placed directly after that Limits section:

```tsx
<section className="rounded-lg border border-line bg-card p-5 space-y-4">
  <h2 className="desk-label">Symbol Risk Rules</h2>
  <p className="text-sm text-ink-soft">
    For any alert that doesn't send its own stop/target, MirrorFleet fills these in.
    An alert that already sends its own is always used as-is.
  </p>
  <table className="w-full text-sm">
    <thead>
      <tr>
        <th className="text-left">Symbol</th>
        <th className="text-left">Stop (points)</th>
        <th className="text-left">Target (points)</th>
        <th className="text-left">Trailing</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {riskRules.map(rule => (
        <tr key={rule.symbol}>
          <td>{rule.symbol}</td>
          <td>{rule.stop_points ?? '—'}</td>
          <td>{rule.target_points ?? '—'}</td>
          <td>{rule.trailing_enabled ? 'On' : 'Off'}</td>
          <td>
            <button aria-label={`remove ${rule.symbol.toLowerCase()}`}
                    onClick={() => removeRiskRule(rule.symbol)}>
              Remove
            </button>
          </td>
        </tr>
      ))}
    </tbody>
  </table>
  <div className="flex gap-2 items-end flex-wrap">
    <label>
      Symbol
      <input aria-label="symbol" value={newRule.symbol}
             onChange={e => setNewRule({ ...newRule, symbol: e.target.value })} />
    </label>
    <label>
      Stop (points)
      <input aria-label="stop (points)" value={newRule.stop_points}
             onChange={e => setNewRule({ ...newRule, stop_points: e.target.value })} />
    </label>
    <label>
      Target (points)
      <input aria-label="target (points)" value={newRule.target_points}
             onChange={e => setNewRule({ ...newRule, target_points: e.target.value })} />
    </label>
    <label>
      <input type="checkbox" checked={newRule.trailing_enabled}
             onChange={e => setNewRule({ ...newRule, trailing_enabled: e.target.checked })} />
      Trailing
    </label>
    {newRule.trailing_enabled && (
      <>
        <label>
          Start after (points)
          <input aria-label="start after (points)" value={newRule.trail_start_points}
                 onChange={e => setNewRule({ ...newRule, trail_start_points: e.target.value })} />
        </label>
        <label>
          Step (points)
          <input aria-label="step (points)" value={newRule.trail_step_points}
                 onChange={e => setNewRule({ ...newRule, trail_step_points: e.target.value })} />
        </label>
      </>
    )}
    <button onClick={addRiskRule}>Add rule</button>
  </div>
</section>
```

Add the import at the top of the file:
```typescript
import { RiskRule } from '../lib/types'
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run --rm dashboard npm test -- Automation`
Expected: both new tests PASS

- [ ] **Step 6: Run `tsc --noEmit` and the full dashboard test suite**

Run: `docker compose run --rm dashboard npm test`
Expected: type-check passes, all tests PASS

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/pages/Automation.tsx dashboard/src/pages/Automation.test.tsx dashboard/src/lib/types.ts
git commit -m "feat(dashboard): add Symbol Risk Rules section to Automation page"
```

---

## Final integration pass

- [ ] **Step 1: Run every test suite in the repo**

```bash
docker compose run --rm api python -m pytest tests -v
docker compose run --rm copier python -m pytest tests/unit tests/integration -v
docker compose run --rm dashboard npm test
```
Expected: all PASS.

- [ ] **Step 2: Deploy per the established pattern**

```bash
git fetch origin && git reset --hard origin/main   # on the server
docker compose up migrate
docker compose build api dashboard copier
docker compose up -d api dashboard copier
```

- [ ] **Step 3: Manual smoke test**

On the Automation page, add a rule for a symbol you can safely test with (small lots, demo account), fire a bare `{"action":"buy","symbol":"...","lots":0.01}` alert with no stop/target, and confirm on the Positions page that the position picked up the configured stop and target within a few seconds. Let price move (or place a manual close/reopen to simulate movement if the market is quiet) and confirm the stop moves once `trail_start_points` is crossed.
