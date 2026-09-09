# VT HTF→LTF Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an hourly VT Screener alert act as a standing permission gate (direction + a stop-target band) on one or more lower-timeframe VT Screener triggers, which place the actual limit order through MirrorFleet's existing pipeline.

**Architecture:** Both alert kinds ride the org's existing TradingView webhook door (same URL, same secret), told apart by a new `role` key in the JSON body. A new pure-logic module parses and gates; a new table holds the latest HTF snapshot per `(org, symbol)`; the LTF path calls the copier's already-working `/order` endpoint with `order_type: LIMIT`. A new per-org allowlist of LTF timeframes, editable from the Automation page, is gate 0.

**Tech Stack:** Python (FastAPI, psycopg3) for `api`; TypeScript/React for `dashboard`; Pine Script v6 for the TradingView relay; Postgres for storage. No changes to `copier`.

**Spec:** `docs/superpowers/specs/2026-09-09-vt-htf-ltf-bridge-design.md`

## Global Constraints

- Both alert kinds share ONE webhook URL and ONE secret per org — no new credential type.
- `vt_ltf_timeframes` defaults to `'{}'` (empty) — an unconfigured workspace trades no LTF timeframe until an admin explicitly turns one on.
- `tol` is **not** a hardcoded price number — it is `TOL_PCT * (hi - lo)`, `TOL_PCT = 0.02`, computed from the HTF band's own width so it scales with the instrument.
- Staleness multiplier is fixed at `2 * tf_minutes * 60` seconds — not configurable in this build.
- The existing `action`-shaped webhook contract (used today by `tradingview/fvg-multi-timeframe.pine`) must keep working byte-for-byte unchanged — every task that touches `webhooks.py` must leave its existing tests green.
- No changes to `copier` — `/order` already accepts `order_type: LIMIT` with `limit_price`/`stop_loss`/`take_profit`.
- No R:R filters, daily caps, cooldowns, extra position sizing, or momentum/EMA filters — explicitly out of scope.

## Ruled deviations during execution

The code below is the plan AS WRITTEN; execution surfaced defects in it that
were ruled on by the controller (the run ledger was git-ignored scratch, so
the rulings are recorded here). Where a snippet below disagrees with the
committed code, the CODE is authoritative:

- `TestCheckGate._ltf()` helper: keyword-collision bug; replaced with a
  dict-merge override pattern.
- `parse_vt_alert`: the plan's `.strip().lower()` normalization on
  role/bias/tf contradicted the plan's own strict tests; STRICT parsing
  shipped (both producers are scripts emitting exact lowercase).
- The VT LTF trigger path enforces `max_per_minute` inside its dedup
  transaction (the plan omitted it).
- The VT path's `max_open` guard counts open positions PLUS resting pending
  orders from /state (the plan's guard was blind to resting LIMIT orders),
  and its opposite-position guard also sees resting opposite orders.
- `org_webhooks.symbol_aliases` renames are applied to VT alerts of both
  roles before storage and gating (the plan skipped them).
- The Automation test's ambiguous `getByText(/Entry timeframes/i)` became
  `getByRole('heading', ...)`.
- `check_gate` gained a `valid` gate after the allowlist check, and the Pine
  relay fires once per distinct held entry level, both from the final
  whole-branch review.

---

## File Structure

- `db/migrations/017_vt_bridge.sql` (new) — `vt_htf_snapshots` table + `org_webhooks.vt_ltf_timeframes` column.
- `api/tests/test_migration_017.py` (new) — post-migration schema shape.
- `api/src/api/vt_bridge_alerts.py` (new) — pure parsing (`parse_vt_alert`) and gating (`check_gate`) logic, no I/O. Mirrors `tradingview_alerts.py`'s split.
- `api/tests/test_vt_bridge_alerts.py` (new) — unit tests for the above.
- `api/src/api/routes/webhooks.py` (modify) — role dispatch, `_handle_vt_bridge` and its helpers, the settings endpoint extension.
- `api/tests/test_webhooks.py` (modify) — integration tests for the full htf/ltf flow and every rejection path.
- `dashboard/src/lib/types.ts` (modify) — `vt_ltf_timeframes: string[]` on `WebhookSettings`.
- `dashboard/src/pages/Automation.tsx` (modify) — new "Entry timeframes" multi-select.
- `dashboard/src/pages/Automation.test.tsx` (modify) — fixture + new test for the control.
- `tradingview/vt-htf-ltf-relay.pine` (new) — the relay script, one instance per chart, `Role` input switching HTF/LTF behaviour.

---

### Task 1: Migration — `vt_htf_snapshots` and `org_webhooks.vt_ltf_timeframes`

**Files:**
- Create: `db/migrations/017_vt_bridge.sql`
- Test: `api/tests/test_migration_017.py`

**Interfaces:**
- Produces: table `vt_htf_snapshots(org_id, symbol, bias, entry, stop, target, price, tf, received_at)`, PK `(org_id, symbol)`, `bias CHECK (bias IN ('long','short'))`. Column `org_webhooks.vt_ltf_timeframes TEXT[] NOT NULL DEFAULT '{}'::TEXT[]`.

- [ ] **Step 1: Write the failing migration-shape tests**

```python
# api/tests/test_migration_017.py
"""Migration 017: the VT HTF->LTF bridge -- vt_htf_snapshots (the latest HTF
permission per org+symbol) and org_webhooks.vt_ltf_timeframes (the LTF
timeframes a workspace currently allows to trade). conftest builds the
scratch database by applying EVERY migration, so these assert the
post-migration shape rather than the migration's own SQL."""
import psycopg
import pytest


def test_migration_017_is_recorded_right_after_016(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "017_vt_bridge.sql" in names
    assert names.index("017_vt_bridge.sql") == names.index("016_deal_label.sql") + 1


def test_vt_htf_snapshots_has_one_row_per_org_and_symbol(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, price, tf) "
            "VALUES (%s, 'XAUUSD', 'long', 4385.34, 4413.15, 4301.91, 4355.28, '60')",
            (org_id,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, price, tf) "
                "VALUES (%s, 'XAUUSD', 'short', 1, 2, 3, 4, '15')", (org_id,))


def test_vt_htf_snapshots_bias_is_checked(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, price, tf) "
                "VALUES (%s, 'XAUUSD', 'sideways', 1, 2, 3, 4, '60')", (org_id,))


def test_org_webhooks_vt_ltf_timeframes_defaults_to_empty(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_webhooks (org_id, hook_id, secret_hash, secret_created_at) "
            "VALUES (%s, 'hook_017test', 'h', now())", (org_id,))
        (tfs,) = conn.execute(
            "SELECT vt_ltf_timeframes FROM org_webhooks WHERE org_id = %s", (org_id,)).fetchone()
    assert tfs == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `api/`, against the throwaway test container per the project's established Docker test pattern):
`pytest tests/test_migration_017.py -v`
Expected: FAIL — `017_vt_bridge.sql` not found / `vt_htf_snapshots` does not exist.

- [ ] **Step 3: Write the migration**

```sql
-- db/migrations/017_vt_bridge.sql
-- VT HTF->LTF bridge: an hourly VT Screener reading is a standing
-- permission (direction + a stop-target band) for a lower-timeframe VT
-- Screener trigger, which places the actual order. See
-- docs/superpowers/specs/2026-09-09-vt-htf-ltf-bridge-design.md.
--
-- One row per (org, symbol) -- the LATEST htf reading only, no history.
-- Every valid htf alert overwrites the previous one.
CREATE TABLE vt_htf_snapshots (
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    bias        TEXT NOT NULL CHECK (bias IN ('long', 'short')),
    entry       DOUBLE PRECISION NOT NULL,
    stop        DOUBLE PRECISION NOT NULL,
    target      DOUBLE PRECISION NOT NULL,
    -- HTF chart price at relay time. Informational only -- never gated on.
    price       DOUBLE PRECISION NOT NULL,
    -- e.g. "60". Informational, and the staleness math's minutes-per-tf.
    tf          TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, symbol)
);

-- The LTF timeframes (TradingView's own strings, e.g. "1", "5", "15") this
-- workspace currently allows to trade. Defaults to empty: an unconfigured
-- workspace trades no LTF timeframe until an admin turns one on, the same
-- safe-by-default posture as "no master account" already blocking trading.
ALTER TABLE org_webhooks
    ADD COLUMN vt_ltf_timeframes TEXT[] NOT NULL DEFAULT '{}'::TEXT[];
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_migration_017.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add db/migrations/017_vt_bridge.sql api/tests/test_migration_017.py
git commit -m "$(cat <<'EOF'
feat(db): add the VT HTF->LTF bridge schema

vt_htf_snapshots holds the latest HTF permission per org+symbol
(overwritten on every valid HTF alert, no history). org_webhooks gets
vt_ltf_timeframes, the set of LTF entry timeframes a workspace
currently allows to trade -- defaults to empty, so nothing trades
until explicitly enabled.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `vt_bridge_alerts.py` — parsing and gating, no I/O

**Files:**
- Create: `api/src/api/vt_bridge_alerts.py`
- Test: `api/tests/test_vt_bridge_alerts.py`

**Interfaces:**
- Consumes: `AlertError`, `HARD_MAX_LOTS`, `normalise_ticker` from `api.tradingview_alerts` (existing, unchanged).
- Produces (used by Task 3):
  - `VTAlert(role: str, symbol: str, tf: str, bias: str, entry: float, stop: float, target: float, price: float, valid: bool, trigger: bool | None, lots: float | None, bar_ms: int)` — frozen dataclass.
  - `VTSnapshot(bias: str, stop: float, target: float, tf: str, received_at: datetime)` — frozen dataclass.
  - `GateResult(passed: bool, reason: str | None)` — frozen dataclass.
  - `parse_vt_alert(body: object, max_lots: float) -> VTAlert` — raises `AlertError`.
  - `check_gate(htf: VTSnapshot | None, ltf: VTAlert, allowed_timeframes: set[str], now: datetime) -> GateResult`.
  - `TOL_PCT = 0.02`, `STALE_MULTIPLIER = 2` — module constants.

- [ ] **Step 1: Write the failing unit tests**

```python
# api/tests/test_vt_bridge_alerts.py
"""The VT bridge parser and gate refuse a wrong trade before it can become
one -- same discipline as tradingview_alerts.py, for the htf/ltf contract."""
from datetime import datetime, timedelta, timezone

import pytest

from api.tradingview_alerts import AlertError
from api.vt_bridge_alerts import (
    STALE_MULTIPLIER, TOL_PCT, GateResult, VTAlert, VTSnapshot, check_gate,
    parse_vt_alert)

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _htf_body(**over):
    body = {"secret": "x", "role": "htf", "symbol": "XAUUSD", "tf": "60",
            "bias": "long", "entry": 4385.34, "stop": 4413.15, "target": 4301.91,
            "price": 4355.28, "valid": True, "bar_ms": 1757400000000}
    body.update(over)
    return body


def _ltf_body(**over):
    body = {"secret": "x", "role": "ltf", "symbol": "XAUUSD", "tf": "1",
            "bias": "short", "entry": 4380.63, "stop": 4390.10, "target": 4360.00,
            "price": 4380.63, "valid": True, "trigger": True, "lots": 0.01,
            "bar_ms": 1757400060000}
    body.update(over)
    return body


class TestParseVtAlert:
    def test_a_valid_htf_alert(self):
        alert = parse_vt_alert(_htf_body(), max_lots=1.0)
        assert alert == VTAlert("htf", "XAUUSD", "60", "long", 4385.34, 4413.15,
                                4301.91, 4355.28, True, None, None, 1757400000000)

    def test_a_valid_ltf_alert(self):
        alert = parse_vt_alert(_ltf_body(), max_lots=1.0)
        assert alert == VTAlert("ltf", "XAUUSD", "1", "short", 4380.63, 4390.10,
                                4360.00, 4380.63, True, True, 0.01, 1757400060000)

    def test_bar_ms_as_a_numeric_string_is_accepted(self):
        """Pine renders large timestamps as a quoted string to dodge
        scientific-notation formatting; int() must still parse it."""
        alert = parse_vt_alert(_htf_body(bar_ms="1757400000000"), max_lots=1.0)
        assert alert.bar_ms == 1757400000000

    @pytest.mark.parametrize("role", [None, "", "htf ", "HTF", "ltf2", 42])
    def test_role_must_be_htf_or_ltf(self, role):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(role=role), max_lots=1.0)

    @pytest.mark.parametrize("bias", [None, "", "up", "LONG", 1])
    def test_bias_must_be_long_or_short(self, bias):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(bias=bias), max_lots=1.0)

    @pytest.mark.parametrize("key", ["entry", "stop", "target", "price"])
    def test_prices_must_be_positive_finite_numbers(self, key):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(**{key: -1}), max_lots=1.0)
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(**{key: None}), max_lots=1.0)
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(**{key: float("nan")}), max_lots=1.0)

    def test_valid_must_be_a_real_boolean(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(valid="true"), max_lots=1.0)

    def test_bar_ms_is_required(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(bar_ms=None), max_lots=1.0)

    def test_htf_ignores_a_missing_lots(self):
        body = _htf_body()
        assert "lots" not in body
        alert = parse_vt_alert(body, max_lots=1.0)
        assert alert.lots is None

    def test_ltf_requires_trigger(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_ltf_body(trigger=None), max_lots=1.0)

    def test_ltf_requires_lots(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_ltf_body(lots=None), max_lots=1.0)

    def test_ltf_lots_is_capped_like_the_existing_contract(self):
        with pytest.raises(AlertError, match="above this workspace's cap"):
            parse_vt_alert(_ltf_body(lots=5.0), max_lots=1.0)

    def test_symbol_is_normalised_the_same_way(self):
        alert = parse_vt_alert(_htf_body(symbol="OANDA:XAUUSD"), max_lots=1.0)
        assert alert.symbol == "XAUUSD"


class TestCheckGate:
    def _htf(self, **over):
        base = dict(bias="long", stop=4413.15, target=4301.91, tf="60", received_at=NOW)
        base.update(over)
        return VTSnapshot(**base)

    def _ltf(self, **over):
        alert = parse_vt_alert(_ltf_body(bias="long", entry=4350.0), max_lots=1.0)
        return alert if not over else parse_vt_alert(_ltf_body(bias="long", entry=4350.0, **over), max_lots=1.0)

    def test_passes_when_everything_lines_up(self):
        result = check_gate(self._htf(), self._ltf(), {"1"}, NOW)
        assert result == GateResult(True, None)

    def test_gate_0_rejects_a_timeframe_not_on_the_allowlist(self):
        result = check_gate(self._htf(), self._ltf(), set(), NOW)
        assert not result.passed and "not enabled" in result.reason

    def test_gate_0_runs_before_looking_for_a_snapshot(self):
        """An empty allowlist rejects even when no htf snapshot exists either --
        gate 0 is cheapest-first and must not depend on htf being present."""
        result = check_gate(None, self._ltf(), set(), NOW)
        assert not result.passed and "not enabled" in result.reason

    def test_missing_snapshot_is_rejected(self):
        result = check_gate(None, self._ltf(), {"1"}, NOW)
        assert not result.passed and "no HTF snapshot" in result.reason

    def test_snapshot_exactly_at_the_staleness_boundary_still_passes(self):
        age = timedelta(seconds=STALE_MULTIPLIER * 60 * 60)  # 2 * 60min tf
        htf = self._htf(received_at=NOW - age)
        result = check_gate(htf, self._ltf(), {"1"}, NOW)
        assert result.passed

    def test_snapshot_one_second_past_the_boundary_is_stale(self):
        age = timedelta(seconds=STALE_MULTIPLIER * 60 * 60 + 1)
        htf = self._htf(received_at=NOW - age)
        result = check_gate(htf, self._ltf(), {"1"}, NOW)
        assert not result.passed and "old" in result.reason

    def test_bias_mismatch_is_rejected(self):
        htf = self._htf(bias="short")
        result = check_gate(htf, self._ltf(bias="long"), {"1"}, NOW)
        assert not result.passed and "does not match" in result.reason

    def test_entry_exactly_on_the_band_edge_passes(self):
        htf = self._htf(stop=4413.15, target=4301.91)  # band [4301.91, 4413.15]
        result = check_gate(htf, self._ltf(entry=4301.91), {"1"}, NOW)
        assert result.passed
        result = check_gate(htf, self._ltf(entry=4413.15), {"1"}, NOW)
        assert result.passed

    def test_entry_just_outside_the_tolerance_padded_band_is_rejected(self):
        htf = self._htf(stop=4413.15, target=4301.91)
        width = 4413.15 - 4301.91
        just_outside = 4301.91 - (TOL_PCT * width) - 0.01
        result = check_gate(htf, self._ltf(entry=just_outside), {"1"}, NOW)
        assert not result.passed and "outside the HTF band" in result.reason

    def test_entry_just_inside_the_tolerance_padding_passes(self):
        htf = self._htf(stop=4413.15, target=4301.91)
        width = 4413.15 - 4301.91
        just_inside = 4301.91 - (TOL_PCT * width) + 0.01
        result = check_gate(htf, self._ltf(entry=just_inside), {"1"}, NOW)
        assert result.passed

    def test_band_math_works_for_a_short_htf_too(self):
        """A short HTF has stop ABOVE target -- min/max must not assume order."""
        htf = self._htf(bias="short", stop=4413.15, target=4301.91)
        ltf = self._ltf(bias="short", entry=4380.63)
        result = check_gate(htf, ltf, {"1"}, NOW)
        assert result.passed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_vt_bridge_alerts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.vt_bridge_alerts'`.

- [ ] **Step 3: Write `vt_bridge_alerts.py`**

```python
# api/src/api/vt_bridge_alerts.py
"""Turning a VT HTF->LTF bridge alert into something the copier will accept.

Pure functions, no I/O -- same discipline as tradingview_alerts.py, and for
the same reason: this is where a relay-script mistake becomes either a
clear rejection or a wrong trade.

Two alert kinds share one contract, told apart by `role`:

HTF ("htf"). Permission only, never traded. Every valid reading overwrites
the previous one for that (org, symbol) -- there is no history.

LTF ("ltf"). The trade itself. Only acted on when `trigger` is true; a
`trigger: false` alert is informational (VT Screener relaying its current
levels without price having reached them yet). When it does trigger, it is
checked against the org's most recent HTF snapshot for that symbol:

  0. its own `tf` is on the workspace's allowed-timeframes list
  1. a snapshot exists at all
  2. the snapshot is not stale (older than STALE_MULTIPLIER times its own tf)
  3. its bias matches the snapshot's bias
  4. its entry falls inside the snapshot's stop-target band, padded by
     TOL_PCT of the band's own width (never a fixed price number -- a gold
     band and a EURUSD band differ by orders of magnitude)

First failure wins; `check_gate` is pure and returns which one, so the
caller can log and refuse without a single I/O call inside this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .tradingview_alerts import AlertError, HARD_MAX_LOTS, normalise_ticker

TOL_PCT = 0.02
STALE_MULTIPLIER = 2

ROLES = ("htf", "ltf")
BIASES = ("long", "short")


@dataclass(frozen=True)
class VTAlert:
    role: str               # htf | ltf
    symbol: str              # broker-style, e.g. XAUUSD
    tf: str                  # TradingView's own timeframe string, e.g. "60"
    bias: str                # long | short
    entry: float
    stop: float
    target: float
    price: float             # chart price at relay time, informational
    valid: bool
    trigger: bool | None     # ltf only; None on htf
    lots: float | None       # ltf only (required there); None on htf
    bar_ms: int


@dataclass(frozen=True)
class VTSnapshot:
    """A stored vt_htf_snapshots row, as check_gate needs it."""
    bias: str
    stop: float
    target: float
    tf: str
    received_at: datetime    # timezone-aware


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None


def _number(raw: object, key: str) -> float:
    """float() that refuses the things float() accepts and a trade should not."""
    if isinstance(raw, bool):
        raise AlertError(f"{key} must be a number, got {raw!r}")
    if isinstance(raw, str) and len(raw) > 32:
        raise AlertError(f"{key} is not a valid number")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise AlertError(f"{key} must be a number, got {raw!r}")
    return value


def _finite_positive(body: dict, key: str) -> float:
    raw = body.get(key)
    if raw is None or raw == "":
        raise AlertError(f'{key} is required, e.g. "{key}": 4385.34')
    value = _number(raw, key)
    if not math.isfinite(value) or value <= 0:
        raise AlertError(f"{key} must be a positive price, got {raw!r}")
    return value


def _bool_field(body: dict, key: str) -> bool:
    raw = body.get(key)
    if not isinstance(raw, bool):
        raise AlertError(f'{key} must be true or false, got {raw!r}')
    return raw


def parse_vt_alert(body: object, max_lots: float) -> VTAlert:
    """Validate a VT bridge alert against the org's limits.

    `max_lots` is the org's own cap, applied to `lots` on an ltf alert the
    same way parse_alert applies it -- on top of HARD_MAX_LOTS, never
    instead of it.
    """
    if not isinstance(body, dict):
        raise AlertError(
            "the alert message must be JSON, e.g. "
            '{"role":"ltf","symbol":"XAUUSD","bias":"long",...}')

    role = str(body.get("role", "")).strip().lower()
    if role not in ROLES:
        raise AlertError(f'role must be one of {", ".join(ROLES)}; got {body.get("role")!r}')

    symbol = normalise_ticker(body.get("symbol", body.get("ticker")))

    tf = str(body.get("tf", "")).strip()
    if not tf:
        raise AlertError('tf is required, e.g. "tf": "60"')

    bias = str(body.get("bias", "")).strip().lower()
    if bias not in BIASES:
        raise AlertError(f'bias must be one of {", ".join(BIASES)}; got {body.get("bias")!r}')

    entry = _finite_positive(body, "entry")
    stop = _finite_positive(body, "stop")
    target = _finite_positive(body, "target")
    price = _finite_positive(body, "price")
    valid = _bool_field(body, "valid")

    raw_bar_ms = body.get("bar_ms")
    try:
        bar_ms = int(raw_bar_ms)
    except (TypeError, ValueError):
        raise AlertError('bar_ms is required and must be a whole number of milliseconds')

    if role == "htf":
        return VTAlert(role, symbol, tf, bias, entry, stop, target, price,
                       valid, None, None, bar_ms)

    trigger = _bool_field(body, "trigger")

    raw_lots = body.get("lots")
    if raw_lots is None or raw_lots == "":
        raise AlertError('lots is required for role "ltf", e.g. "lots": 0.01')
    lots = _number(raw_lots, "lots")
    if not math.isfinite(lots) or lots <= 0:
        raise AlertError(f"lots must be greater than 0, got {raw_lots!r}")
    if max_lots is None or not math.isfinite(float(max_lots)) or float(max_lots) <= 0:
        raise AlertError("this workspace has no lot cap configured; set one in Automation")
    cap = min(float(max_lots), HARD_MAX_LOTS)
    if lots > cap:
        raise AlertError(
            f"lots {lots:g} is above this workspace's cap of {cap:g}. "
            f"Raise the cap in Automation settings if that size is intended.")

    return VTAlert(role, symbol, tf, bias, entry, stop, target, price,
                   valid, trigger, lots, bar_ms)


def check_gate(htf: VTSnapshot | None, ltf: VTAlert, allowed_timeframes: set[str],
               now: datetime) -> GateResult:
    """First failure wins. Gate 0 (allowed timeframes) is cheapest and
    org-scoped rather than a function of (htf, ltf), so it runs before even
    checking whether a snapshot exists."""
    if ltf.tf not in allowed_timeframes:
        return GateResult(False, f"entry timeframe {ltf.tf} is not enabled for this workspace")

    if htf is None:
        return GateResult(False, f"no HTF snapshot for {ltf.symbol} yet")

    try:
        htf_tf_minutes = int(htf.tf)
    except ValueError:
        htf_tf_minutes = 0
    max_age_s = STALE_MULTIPLIER * htf_tf_minutes * 60
    age_s = (now - htf.received_at).total_seconds()
    if max_age_s <= 0 or age_s > max_age_s:
        return GateResult(
            False, f"HTF snapshot for {ltf.symbol} is {int(age_s)}s old (max {max_age_s}s)")

    if ltf.bias != htf.bias:
        return GateResult(
            False, f"LTF bias ({ltf.bias}) does not match HTF bias ({htf.bias})")

    lo = min(htf.stop, htf.target)
    hi = max(htf.stop, htf.target)
    tol = TOL_PCT * (hi - lo)
    if not (lo - tol <= ltf.entry <= hi + tol):
        return GateResult(
            False,
            f"LTF entry {ltf.entry:g} is outside the HTF band [{lo:g}, {hi:g}] (±{tol:g})")

    return GateResult(True, None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_vt_bridge_alerts.py -v`
Expected: PASS (all tests in `TestParseVtAlert` and `TestCheckGate`).

- [ ] **Step 5: Commit**

```bash
git add api/src/api/vt_bridge_alerts.py api/tests/test_vt_bridge_alerts.py
git commit -m "$(cat <<'EOF'
feat(api): parse and gate VT HTF->LTF bridge alerts

Pure, no-I/O module mirroring tradingview_alerts.py's split: parse_vt_alert
validates the htf/ltf JSON contract (loud AlertError, same style as the
existing parser); check_gate runs the five ordered checks (allowed
timeframe, snapshot present, not stale, bias match, entry inside a
band-width-relative tolerance) and returns the specific reason on the
first one that fails.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Webhook routing — role dispatch, HTF storage, LTF gating and order placement

**Files:**
- Modify: `api/src/api/routes/webhooks.py`
- Modify: `api/tests/test_webhooks.py`

**Interfaces:**
- Consumes: `VTAlert`, `VTSnapshot`, `GateResult`, `parse_vt_alert`, `check_gate` from Task 2's `api.vt_bridge_alerts`. `AlertError`, `Alert`, `find_master_positions` (already imported). `_json`, `WebhookRejected`, `CopierDown`, `CopierUnknown`, `_copier`, `_insert`, `_finish`, `DEADLINE_S`, `DEDUP_WINDOW_S` (all already defined in this file).
- Produces: nothing new consumed by a later task — this is the last code task before the settings/UI surface (Task 4/5), which read `vt_ltf_timeframes` directly via SQL, not through any function this task adds.

This task has three sub-behaviours, each with its own red/green cycle, because they build on each other within the same new function.

#### 3a. Role dispatch + HTF storage

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/test_webhooks.py` (near the top, after the existing `_alert`/`_post` helpers — these new helpers are used by every test in this task):

```python
def _vt_htf(**over):
    body = {"secret": SECRET, "role": "htf", "symbol": "XAUUSD", "tf": "60",
            "bias": "long", "entry": 4385.34, "stop": 4413.15, "target": 4301.91,
            "price": 4355.28, "valid": True, "bar_ms": 1757400000000}
    body.update(over)
    return body


def _vt_ltf(**over):
    body = {"secret": SECRET, "role": "ltf", "symbol": "XAUUSD", "tf": "1",
            "bias": "long", "entry": 4350.00, "stop": 4340.00, "target": 4370.00,
            "price": 4350.00, "valid": True, "trigger": True, "lots": 0.01,
            "bar_ms": 1757400060000}
    body.update(over)
    return body


def _htf_snapshot(db, org_id, symbol="XAUUSD"):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT bias, entry, stop, target, price, tf FROM vt_htf_snapshots "
            "WHERE org_id = %s AND symbol = %s", (org_id, symbol)).fetchone()


def _allow_timeframes(db, org_id, *tfs):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE org_webhooks SET vt_ltf_timeframes = %s WHERE org_id = %s",
                     (list(tfs), org_id))


# ============================================================ VT bridge: htf


def test_a_valid_htf_alert_is_stored(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)

    r = _post(client, _vt_htf())

    assert r.status_code == 200 and r.json()["status"] == "accepted"
    row = _htf_snapshot(db, org_id)
    assert row == ("long", 4385.34, 4413.15, 4301.91, 4355.28, "60")


def test_a_second_htf_alert_overwrites_the_first_no_history(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _post(client, _vt_htf(bias="long", entry=4385.34))

    _post(client, _vt_htf(bias="short", entry=4390.00, stop=4400.00, target=4370.00))

    row = _htf_snapshot(db, org_id)
    assert row == ("short", 4390.00, 4400.00, 4370.00, 4355.28, "60")
    with psycopg.connect(db, autocommit=True) as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM vt_htf_snapshots WHERE org_id = %s", (org_id,)).fetchone()
    assert count == 1


def test_an_invalid_htf_reading_is_not_stored_and_does_not_clear_a_good_one(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _post(client, _vt_htf())  # a good snapshot is standing

    r = _post(client, _vt_htf(valid=False, bar_ms=1757400001000))

    assert r.status_code == 200
    assert r.json()["reason"] == "htf setup not valid, not stored"
    row = _htf_snapshot(db, org_id)
    assert row == ("long", 4385.34, 4413.15, 4301.91, 4355.28, "60")  # unchanged


def test_htf_never_calls_the_copier(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)

    _post(client, _vt_htf())

    assert calls == []


def test_a_malformed_vt_alert_is_recorded_not_a_crash(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)

    r = _post(client, _vt_htf(bias="sideways"))

    assert r.status_code == 422
    assert "bias" in r.json()["reason"]


def test_the_existing_action_contract_is_completely_unaffected(org_client, db):
    """Regression: both shapes share _handle -- the FVG script's alerts
    must keep working byte for byte."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)

    r = _post(client, _alert(stop_loss=4570, take_profit=4600))

    assert r.status_code == 200 and r.json()["status"] == "accepted"
    url, sent = next((u, b) for u, b in calls if "/order" in u)
    assert sent["order_type"] == "MARKET"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_webhooks.py -k "htf or existing_action_contract" -v`
Expected: FAIL — `relation "vt_htf_snapshots" does not exist` is wrong (Task 1 already created it); the actual failure is every VT alert falling through to `parse_alert`, which rejects `role`-shaped bodies with `"action must be one of ..."`.

- [ ] **Step 3: Implement role dispatch and HTF storage**

In `api/src/api/routes/webhooks.py`, add the import (near the existing `from ..tradingview_alerts import (...)` block):

```python
from ..vt_bridge_alerts import (
    GateResult, VTAlert, VTSnapshot, check_gate, parse_vt_alert)
```

Also add to the top-level imports: `from datetime import datetime, timezone`.

Change the mailbox SELECT in `_handle` (step 3) to also fetch the new column:

```python
        row = conn.execute(
            "SELECT org_id, secret_hash, enabled, max_lots, max_per_minute, "
            "max_open_positions, symbol_aliases, vt_ltf_timeframes FROM org_webhooks "
            "WHERE hook_id = %s",
            (hook_id,)).fetchone()
        if row is None:
            rate_limiter.is_limited(f"webhook-source:{ip}", 30)
            return _json(404, {"detail": "Not found"})
        (org_id, secret_hash, enabled, max_lots, max_per_minute, max_open, aliases,
         vt_ltf_timeframes) = row
        max_lots = float(max_lots) if max_lots is not None else None
        allowed_tfs = set(vt_ltf_timeframes or [])
```

Replace the `# ---- 7. validate ----` block so it branches before `parse_alert`:

```python
            # ---- 7. validate ----
            if isinstance(body, dict) and "role" in body:
                return await _handle_vt_bridge(
                    conn, request, cfg, org_id, master, max_lots, max_open,
                    allowed_tfs, body, redacted, ip, t0)
            try:
                alert = parse_alert(body, max_lots)
            except AlertError as exc:
                raise WebhookRejected(422, str(exc))
            if isinstance(aliases, dict) and alert.symbol in aliases:
                alert = Alert(alert.action, normalise_ticker(aliases[alert.symbol]),
                              alert.lots, alert.stop_loss, alert.take_profit, alert.alert_id)
        except WebhookRejected as exc:
            return _record(conn, org_id, ip, t0, redacted, None, exc)
```

Add `_handle_vt_bridge` and its helpers after `_handle` (right before `async def _close(...)`), starting with the dispatch shell and HTF branch — the LTF branch is added in 3b/3c below:

```python
    async def _handle_vt_bridge(conn, request, cfg, org_id, master, max_lots, max_open,
                                allowed_tfs, body, redacted, ip, t0):
        """Both VT bridge alert kinds. Told apart by `role`; see
        vt_bridge_alerts.py for the parsing/gating rules this only wires up.
        """
        try:
            vt = parse_vt_alert(body, max_lots)
        except AlertError as exc:
            return _record(conn, org_id, ip, t0, redacted, None, WebhookRejected(422, str(exc)))

        # A VTAlert stands in for an Alert in the shared receipts/audit
        # plumbing -- role becomes the "action" column, so both alert kinds
        # show on the same Automation page list with zero UI changes there.
        receipt_alert = Alert(vt.role, vt.symbol, vt.lots, None, None, str(vt.bar_ms))

        if vt.role == "htf":
            if vt.valid:
                conn.execute(
                    "INSERT INTO vt_htf_snapshots "
                    "(org_id, symbol, bias, entry, stop, target, price, tf, received_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                    "ON CONFLICT (org_id, symbol) DO UPDATE SET "
                    "bias = EXCLUDED.bias, entry = EXCLUDED.entry, stop = EXCLUDED.stop, "
                    "target = EXCLUDED.target, price = EXCLUDED.price, tf = EXCLUDED.tf, "
                    "received_at = EXCLUDED.received_at",
                    (org_id, vt.symbol, vt.bias, vt.entry, vt.stop, vt.target,
                     vt.price, vt.tf))
                return _record_vt_ok(conn, org_id, ip, t0, redacted, receipt_alert, None)
            return _record_vt_ok(conn, org_id, ip, t0, redacted, receipt_alert,
                                 "htf setup not valid, not stored")

        return await _handle_vt_ltf(conn, request, cfg, org_id, master, max_open,
                                    allowed_tfs, vt, receipt_alert, redacted, ip, t0)

    def _record_vt_ok(conn, org_id, ip, t0, redacted, receipt_alert, reason):
        """A VT-bridge outcome that is neither a rejection nor an order --
        an htf store/skip. Always outcome 'accepted', status 200: nothing
        was refused, there just might be nothing further to do."""
        rid = _insert(conn, org_id, ip, t0, redacted, receipt_alert, "accepted", reason, fp=None)
        conn.execute(
            "INSERT INTO events (org_id, category, severity, payload, actor_email) "
            "VALUES (%s, 'control', 'info', %s, 'tradingview')",
            (org_id, Jsonb({"action": "webhook_alert", "receipt_id": rid, "outcome": "accepted",
                            "role": receipt_alert.action, "symbol": receipt_alert.symbol,
                            **({"reason": reason} if reason else {})})))
        return _json(200, {"status": "accepted", "receipt_id": rid, "role": receipt_alert.action,
                                  "symbol": receipt_alert.symbol,
                                  **({"reason": reason} if reason else {})})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webhooks.py -k "htf or existing_action_contract" -v`
Expected: PASS for every `htf` test. The two LTF-shaped helper functions (`_allow_timeframes`, `_vt_ltf`) added in step 1 are unused until 3b — that is expected and not a failure.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/webhooks.py api/tests/test_webhooks.py
git commit -m "$(cat <<'EOF'
feat(api): route VT bridge htf alerts, storing the latest permission

A "role" key in the webhook body now branches before parse_alert into
_handle_vt_bridge, leaving the existing action-shaped contract
untouched. An htf role stores (or, if invalid, skips without clearing
a standing good reading) the org's latest permission snapshot for
that symbol -- never reaches the copier.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

#### 3b. LTF gating (rejections only — no order placement yet)

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/test_webhooks.py`:

```python
# ============================================================ VT bridge: ltf gates


def test_ltf_trigger_false_is_a_no_op(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)

    r = _post(client, _vt_ltf(trigger=False))

    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert r.json()["reason"] == "ltf informational (trigger=false), no action"
    assert calls == []


def test_ltf_trigger_on_a_disallowed_timeframe_is_rejected(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)

    r = _post(client, _vt_ltf(tf="1"))  # allowlist is empty by default

    assert r.status_code == 422 and "not enabled" in r.json()["reason"]
    assert calls == []


def test_ltf_trigger_with_no_htf_ever_received_is_rejected(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _allow_timeframes(db, org_id, "1")
    calls = _copier(client)

    r = _post(client, _vt_ltf())

    assert r.status_code == 422 and "no HTF snapshot" in r.json()["reason"]
    assert calls == []


def test_ltf_trigger_against_a_stale_htf_is_rejected(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _allow_timeframes(db, org_id, "1")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO vt_htf_snapshots (org_id, symbol, bias, entry, stop, target, "
            "price, tf, received_at) VALUES (%s,'XAUUSD','long',4385.34,4340.00,4400.00,"
            "4355.28,'60', now() - interval '3 hours')", (org_id,))
    calls = _copier(client)

    r = _post(client, _vt_ltf())

    assert r.status_code == 422 and "old" in r.json()["reason"]
    assert calls == []


def test_ltf_trigger_with_wrong_bias_is_rejected(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _allow_timeframes(db, org_id, "1")
    _post(client, _vt_htf(bias="short", stop=4413.15, target=4301.91))
    calls = _copier(client)

    r = _post(client, _vt_ltf(bias="long", entry=4350.00))

    assert r.status_code == 422 and "does not match" in r.json()["reason"]
    assert calls == []


def test_ltf_trigger_with_entry_outside_the_band_is_rejected(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _allow_timeframes(db, org_id, "1")
    _post(client, _vt_htf(bias="long", stop=4340.00, target=4400.00))  # band [4340, 4400]
    calls = _copier(client)

    r = _post(client, _vt_ltf(bias="long", entry=4200.00))

    assert r.status_code == 422 and "outside the HTF band" in r.json()["reason"]
    assert calls == []


def test_a_gate_rejection_is_recorded_on_the_receipt(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)

    _post(client, _vt_ltf(tf="1"))

    outcome, reason, *_ = _receipts(db, org_id)[-1]
    assert outcome == "rejected" and "not enabled" in reason
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_webhooks.py -k "ltf" -v`
Expected: FAIL — `_handle_vt_ltf` is called from Task 3a's code but does not exist yet (`NameError`).

- [ ] **Step 3: Implement `_handle_vt_ltf` through the gate check**

Add after `_record_vt_ok` in `webhooks.py`:

```python
    def _vt_fingerprint(org_id, vt: VTAlert) -> str:
        """Same idea as _fingerprint: the alert's TRADING content, keyed so
        a resend of the same bar's signal is one alert, not two."""
        parts = (org_id, "vt", vt.role, vt.symbol, vt.tf, vt.bar_ms)
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def _vt_audit(conn, org_id, master, vt: VTAlert, outcome, detail, ip, t0, receipt_id):
        severity = {"accepted": "info", "duplicate": "info", "rejected": "warning",
                   "failed": "warning", "unknown": "error"}[outcome]
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, latency_ms, "
            "payload, actor_email) VALUES (%s, %s, 'control', %s, %s, %s, 'tradingview')",
            (org_id, master, severity, int((time.monotonic() - t0) * 1000),
             Jsonb({"action": "webhook_alert", "receipt_id": receipt_id, "outcome": outcome,
                    "alert": {"role": vt.role, "symbol": vt.symbol, "tf": vt.tf,
                              "bias": vt.bias, "entry": vt.entry, "stop": vt.stop,
                              "target": vt.target, "lots": vt.lots, "bar_ms": vt.bar_ms},
                    "source_ip": ip, **({"detail": detail} if detail else {})})))

    async def _handle_vt_ltf(conn, request, cfg, org_id, master, max_open, allowed_tfs,
                             vt: VTAlert, receipt_alert, redacted, ip, t0):
        if not vt.trigger:
            return _record_vt_ok(conn, org_id, ip, t0, redacted, receipt_alert,
                                 "ltf informational (trigger=false), no action")

        fp = _vt_fingerprint(org_id, vt)
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (org_id,))
            dup = conn.execute(
                "SELECT id FROM webhook_receipts WHERE org_id = %s AND fingerprint = %s "
                "AND outcome IN ('accepted','unknown') "
                "AND received_at > now() - make_interval(secs => %s) "
                "ORDER BY received_at DESC LIMIT 1",
                (org_id, fp, DEDUP_WINDOW_S)).fetchone()
            if dup:
                receipt_id = _insert(conn, org_id, ip, t0, redacted, receipt_alert, "duplicate",
                                     f"same alert accepted {dup[0]} seconds ago", fp=None)
                return _json(200, {"status": "duplicate", "receipt_id": receipt_id,
                                          "duplicate_of": dup[0]})
            receipt_id = _insert(conn, org_id, ip, t0, redacted, receipt_alert, "accepted",
                                 None, fp=fp)

        htf_row = conn.execute(
            "SELECT bias, stop, target, tf, received_at FROM vt_htf_snapshots "
            "WHERE org_id = %s AND symbol = %s", (org_id, vt.symbol)).fetchone()
        htf = VTSnapshot(htf_row[0], float(htf_row[1]), float(htf_row[2]), htf_row[3],
                         htf_row[4]) if htf_row else None

        gate = check_gate(htf, vt, allowed_tfs, datetime.now(timezone.utc))
        if not gate.passed:
            _finish(conn, receipt_id, "rejected", gate.reason, clear_fp=True)
            _vt_audit(conn, org_id, master, vt, "rejected", {"reason": gate.reason}, ip, t0,
                      receipt_id)
            return _json(422, {"status": "rejected", "receipt_id": receipt_id,
                                      "reason": gate.reason})

        # Order placement is added in the next sub-step.
        _finish(conn, receipt_id, "rejected", "order placement not yet implemented",
               clear_fp=True)
        return _json(422, {"status": "rejected", "receipt_id": receipt_id,
                                  "reason": "order placement not yet implemented"})
```

Note: the final two lines are a deliberate, temporary stand-in removed in 3c below — every 3b test only exercises paths that return before reaching them (trigger=false, gate-0 through gate-4 rejections), so this is safe and every 3b test passes without it ever executing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webhooks.py -k "ltf" -v`
Expected: PASS for every gate-rejection and trigger=false test. (`test_ltf_trigger...` tests that would reach order placement do not exist yet — added in 3c.)

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/webhooks.py api/tests/test_webhooks.py
git commit -m "$(cat <<'EOF'
feat(api): gate VT bridge ltf triggers against the stored htf snapshot

Wires check_gate into the ltf path: dedup under the same advisory-lock
transaction the existing contract uses, then the five ordered checks,
each rejection recorded with its own specific reason on the receipt
and the audit trail. Order placement on a passing gate lands next.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

#### 3c. LTF order placement on a passing gate

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/test_webhooks.py`:

```python
# ============================================================ VT bridge: ltf order


def _pass_the_gate(db, org_id, client):
    _allow_timeframes(db, org_id, "1")
    _post(client, _vt_htf(bias="long", stop=4340.00, target=4400.00))  # band [4340, 4400]


def test_a_passing_gate_places_a_limit_order(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _pass_the_gate(db, org_id, client)
    calls = _copier(client)

    r = _post(client, _vt_ltf(bias="long", entry=4350.00, stop=4340.00, target=4390.00))

    assert r.status_code == 200 and r.json()["status"] == "accepted"
    url, sent = next((u, b) for u, b in calls if "/order" in u)
    assert sent == {"account_id": MASTER, "symbol": "XAUUSD", "side": "BUY",
                    "order_type": "LIMIT", "volume_lots": 0.01, "limit_price": 4350.00,
                    "stop_loss": 4340.00, "take_profit": 4390.00,
                    "actor_email": "tradingview"}


def test_a_short_ltf_places_a_sell_limit(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _allow_timeframes(db, org_id, "1")
    _post(client, _vt_htf(bias="short", stop=4413.15, target=4301.91))
    calls = _copier(client)

    r = _post(client, _vt_ltf(bias="short", entry=4380.63, stop=4390.10, target=4360.00))

    assert r.status_code == 200
    url, sent = next((u, b) for u, b in calls if "/order" in u)
    assert sent["side"] == "SELL" and sent["order_type"] == "LIMIT"


def test_ltf_still_respects_the_opposite_position_guard(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _pass_the_gate(db, org_id, client)
    calls = _copier(client, state={"master_positions": [
        {"position_id": 1, "symbol": "XAUUSD", "side": "SELL", "volume": 100}]})

    r = _post(client, _vt_ltf(bias="long", entry=4350.00))

    assert r.status_code == 422 and "opposite position" in r.json()["reason"]
    assert not any("/order" in u for u, _ in calls)


def test_ltf_still_respects_max_open_positions(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id, max_open=1)
    _pass_the_gate(db, org_id, client)
    calls = _copier(client, state={"master_positions": [
        {"position_id": 1, "symbol": "EURUSD", "side": "BUY", "volume": 100}]})

    r = _post(client, _vt_ltf(bias="long", entry=4350.00))

    assert r.status_code == 422 and "open positions" in r.json()["reason"]
    assert not any("/order" in u for u, _ in calls)


def test_duplicate_ltf_bar_ms_places_one_order(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _pass_the_gate(db, org_id, client)
    calls = _copier(client)

    first = _post(client, _vt_ltf(bar_ms=1))
    second = _post(client, _vt_ltf(bar_ms=1))

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert len([u for u, _ in calls if "/order" in u]) == 1


def test_a_placed_ltf_order_is_audited_as_tradingview(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _pass_the_gate(db, org_id, client)
    _copier(client)

    _post(client, _vt_ltf(bias="long", entry=4350.00))

    severity, payload, actor = _events(db, org_id)[-1]
    assert actor == "tradingview" and payload["outcome"] == "accepted"
    assert payload["alert"]["role"] == "ltf"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_webhooks.py -k "ltf_order or opposite_position_guard or max_open_positions or duplicate_ltf or audited_as_tradingview" -v`
Expected: FAIL — every passing-gate test gets `"order placement not yet implemented"` (422) instead of an accepted 200.

- [ ] **Step 3: Replace the placeholder with real order placement**

In `webhooks.py`, replace the two-line placeholder at the end of `_handle_vt_ltf` (`_finish(conn, receipt_id, "rejected", "order placement not yet implemented", ...)` and its `return`) with:

```python
        client = request.app.app.state.http if hasattr(request.app, "app") else request.app.state.http
        base = cfg.copier_control_url
        try:
            def remaining() -> float:
                left = DEADLINE_S - (time.monotonic() - t0)
                if left <= 0:
                    raise CopierDown("too slow, nothing sent")
                return left

            state = await _copier(client, "GET", f"{base}/state?org_id={org_id}", None, remaining())
            held = find_master_positions(state, vt.symbol)
            opposite = "SELL" if vt.bias == "long" else "BUY"
            if any(str(p.get("side", "")).upper() == opposite for p in held):
                raise WebhookRejected(
                    422, f"master holds an opposite position on {vt.symbol}; "
                         f"reverse-on-signal is not supported -- send close first")
            if len(state.get("master_positions") or []) >= max_open:
                raise WebhookRejected(
                    422, f"master already holds {max_open} open positions; "
                         f"raise the limit in Automation if this is intended")

            order = {
                "account_id": master, "symbol": vt.symbol,
                "side": "BUY" if vt.bias == "long" else "SELL", "order_type": "LIMIT",
                "volume_lots": vt.lots, "limit_price": vt.entry,
                "stop_loss": vt.stop, "take_profit": vt.target,
                "actor_email": "tradingview",
            }
            remaining()  # the deadline check right before the only irreversible call
            summary = await _copier(client, "POST", f"{base}/order", order, remaining())

            _finish(conn, receipt_id, "accepted", None)
            _vt_audit(conn, org_id, master, vt, "accepted", summary, ip, t0, receipt_id)
            return _json(200, {"status": "accepted", "receipt_id": receipt_id,
                                      "role": "ltf", "symbol": vt.symbol,
                                      "account_id": master, "order": summary})

        except WebhookRejected as exc:
            _finish(conn, receipt_id, "rejected", exc.reason, clear_fp=True)
            _vt_audit(conn, org_id, master, vt, "rejected", {"reason": exc.reason}, ip, t0,
                      receipt_id)
            return _json(exc.status, {"status": "rejected", "receipt_id": receipt_id,
                                             "reason": exc.reason})
        except CopierDown as exc:
            _finish(conn, receipt_id, "failed", f"copier unreachable: {exc}", clear_fp=True)
            _vt_audit(conn, org_id, master, vt, "failed", {"reason": str(exc)}, ip, t0,
                      receipt_id)
            return _json(503, {"status": "failed", "receipt_id": receipt_id,
                                      "reason": "copier unreachable"})
        except CopierUnknown as exc:
            _finish(conn, receipt_id, "unknown", f"copier did not confirm: {exc}")
            _vt_audit(conn, org_id, master, vt, "unknown", {"reason": str(exc)}, ip, t0,
                      receipt_id)
            return _json(200, {"status": "unknown", "receipt_id": receipt_id,
                                      "reason": "order may be on the wire -- check Positions"})
```

- [ ] **Step 4: Run the full webhook test file to verify everything passes**

Run: `pytest tests/test_webhooks.py -v`
Expected: PASS — every existing test (the pre-existing `action`-shaped suite) plus every new `htf`/`ltf` test from 3a/3b/3c.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/webhooks.py api/tests/test_webhooks.py
git commit -m "$(cat <<'EOF'
feat(api): place a LIMIT order when an ltf trigger passes the gate

A passing gate now reuses the existing opposite-position and
max-open-positions guards, then calls the same /order endpoint the
action-shaped contract already uses -- order_type LIMIT, limit_price
from the ltf entry, stop_loss/take_profit from its stop/target. No
copier changes: LIMIT with these fields already works end to end.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Admin settings — `vt_ltf_timeframes` on the webhook GET/PUT

**Files:**
- Modify: `api/src/api/routes/webhooks.py`
- Modify: `api/tests/test_webhooks.py`

**Interfaces:**
- Produces: `GET /api/orgs/{org_id}/webhook` response gains `"vt_ltf_timeframes": string[]`. `PUT /api/orgs/{org_id}/webhook` accepts `vt_ltf_timeframes: string[] | null`.

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/test_webhooks.py` (this file already has operator-endpoint tests further down — add near them; grep for `def test_get_webhook` or `/webhook` PUT tests to place these alongside):

```python
# ============================================================ vt_ltf_timeframes setting


def test_get_webhook_reports_vt_ltf_timeframes(org_client, db):
    client, org_id, seed = org_client
    _arm(db, org_id)
    _allow_timeframes(db, org_id, "1", "5")

    r = client.get(f"/api/orgs/{org_id}/webhook")

    assert r.status_code == 200
    assert sorted(r.json()["vt_ltf_timeframes"]) == ["1", "5"]


def test_get_webhook_defaults_vt_ltf_timeframes_to_empty(org_client, db):
    client, org_id, seed = org_client
    _arm(db, org_id)

    r = client.get(f"/api/orgs/{org_id}/webhook")

    assert r.json()["vt_ltf_timeframes"] == []


def test_admin_can_set_vt_ltf_timeframes(org_client, db):
    client, org_id, seed = org_client
    _arm(db, org_id)

    r = client.put(f"/api/orgs/{org_id}/webhook", json={"vt_ltf_timeframes": ["1", "15"]},
                   headers=_csrf(client))

    assert r.status_code == 200
    with psycopg.connect(db, autocommit=True) as conn:
        (tfs,) = conn.execute(
            "SELECT vt_ltf_timeframes FROM org_webhooks WHERE org_id = %s", (org_id,)).fetchone()
    assert sorted(tfs) == ["1", "15"]
    events = _events_by_action(db, org_id, "webhook_settings_changed")
    assert events[-1]["vt_ltf_timeframes"]["to"] == ["1", "15"]


def test_an_unknown_timeframe_is_refused(org_client, db):
    client, org_id, seed = org_client
    _arm(db, org_id)

    r = client.put(f"/api/orgs/{org_id}/webhook", json={"vt_ltf_timeframes": ["7"]},
                   headers=_csrf(client))

    assert r.status_code == 400 and "vt_ltf_timeframes" in r.json()["detail"]
```

If `_events_by_action` does not already exist in this file, add it next to `_events`:

```python
def _events_by_action(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE org_id = %s AND payload->>'action' = %s "
            "ORDER BY id", (org_id, action)).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_webhooks.py -k "vt_ltf_timeframes" -v`
Expected: FAIL — `KeyError: 'vt_ltf_timeframes'` on GET; PUT with that field is silently ignored (no such field on `WebhookUpdate`).

- [ ] **Step 3: Extend the settings endpoint**

In `api/src/api/routes/webhooks.py`, add the allowed set near the other module constants:

```python
# The timeframes the Automation page's multi-select offers, and the only
# values vt_ltf_timeframes may hold -- TradingView's own minute strings for
# the intraday range this bridge is meant for.
VT_LTF_TIMEFRAME_CHOICES = ("1", "3", "5", "15", "30", "45", "60")
```

Extend `WebhookUpdate`:

```python
class WebhookUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_lots: Optional[float] = None
    max_per_minute: Optional[int] = None
    max_open_positions: Optional[int] = None
    symbol_aliases: Optional[Dict[str, str]] = None
    vt_ltf_timeframes: Optional[List[str]] = None
```

(`List` is not yet imported in this file — add it to the existing `from typing import Any, Dict, Optional` line, making it `from typing import Any, Dict, List, Optional`.)

In `get_webhook`, extend the SELECT and the returned dict:

```python
        row = conn.execute(
            "SELECT hook_id, secret_hash IS NOT NULL, secret_created_at, enabled, max_lots, "
            "max_per_minute, max_open_positions, symbol_aliases, vt_ltf_timeframes "
            "FROM org_webhooks WHERE org_id = %s",
            (ctx.org_id,)).fetchone()
```

```python
            "symbol_aliases": row[7] if row else {},
            "vt_ltf_timeframes": row[8] if row else [],
```

(inserted right after the existing `"symbol_aliases"` line in the returned dict.)

In `update_webhook`, extend the `current` SELECT and add the new field's handling:

```python
        current = conn.execute(
            "SELECT secret_hash, enabled, max_lots, max_per_minute, max_open_positions, "
            "symbol_aliases, vt_ltf_timeframes FROM org_webhooks WHERE org_id = %s",
            (ctx.org_id,)).fetchone()
```

```python
        if body.vt_ltf_timeframes is not None:
            bad = [tf for tf in body.vt_ltf_timeframes if tf not in VT_LTF_TIMEFRAME_CHOICES]
            if bad:
                raise HTTPException(
                    400, f"vt_ltf_timeframes: {bad!r} not in {VT_LTF_TIMEFRAME_CHOICES}")
            tfs = sorted(set(body.vt_ltf_timeframes))
            updates.append("vt_ltf_timeframes = %s"); params.append(tfs)
            changed["vt_ltf_timeframes"] = {"from": current[6], "to": tfs}
```

(placed right after the existing `symbol_aliases` block, before the `if updates:` line.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webhooks.py -v`
Expected: PASS — the full file, including everything from Task 3.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/webhooks.py api/tests/test_webhooks.py
git commit -m "$(cat <<'EOF'
feat(api): admins edit vt_ltf_timeframes on the webhook settings endpoint

GET returns the workspace's current allowlist; PUT validates each
entry against the fixed set the Automation page offers and audits the
change the same way every other webhook setting already is.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Dashboard — the "Entry timeframes" control on Automation

**Files:**
- Modify: `dashboard/src/lib/types.ts`
- Modify: `dashboard/src/pages/Automation.tsx`
- Modify: `dashboard/src/pages/Automation.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT /api/orgs/{org_id}/webhook` from Task 4 (`vt_ltf_timeframes: string[]`).

- [ ] **Step 1: Add the field to the type**

```typescript
// dashboard/src/lib/types.ts -- inside WebhookSettings, right after symbol_aliases
  vt_ltf_timeframes: string[]
```

- [ ] **Step 2: Update the test fixture and write the failing test**

In `dashboard/src/pages/Automation.test.tsx`, add `vt_ltf_timeframes: ['5']` to the `webhook` fixture object (right after `symbol_aliases: {},`):

```typescript
const webhook = {
  configured: true, hook_id: 'hookabc', url: 'https://mirrorfleet.test/api/webhooks/tradingview/hookabc',
  url_hint: null, has_secret: true, secret_created_at: '2026-09-06T12:35:11Z', enabled: true,
  max_lots: 0.1, max_per_minute: 10, max_open_positions: 3, symbol_aliases: {},
  vt_ltf_timeframes: ['5'],
  master_account_id: 999, dry_run: false, copying_enabled: true, template: '{}',
  recent: [{ id: 1, received_at: '2026-09-06T17:57:15Z', outcome: 'accepted', reason: null,
             action: 'buy', symbol: 'BTCUSD', lots: 0.01, source_ip: '52.89.214.238', latency_ms: 14 }],
}
```

Add a new test at the end of the file:

```typescript
test('entry timeframes shows what is currently allowed and lets an admin change it', async () => {
  const fetchMock = mockWebhookRoute()
  render(<MemoryRouter><Automation /></MemoryRouter>)

  await waitFor(() => expect(screen.getByText(/Entry timeframes/i)).toBeTruthy())
  const five = await screen.findByRole('checkbox', { name: '5m' })
  const one = screen.getByRole('checkbox', { name: '1m' })
  expect((five as HTMLInputElement).checked).toBe(true)
  expect((one as HTMLInputElement).checked).toBe(false)

  fetchMock.mockClear()
  await act(async () => {
    one.click()
  })
  await act(async () => {
    screen.getByRole('button', { name: /Save entry timeframes/i }).click()
  })

  await waitFor(() => expect(webhookCalls(fetchMock)).toBeGreaterThan(0))
  const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PUT')
  const body = JSON.parse((putCall![1] as RequestInit).body as string)
  expect(sorted(body.vt_ltf_timeframes)).toEqual(['1', '5'])
})

function sorted(a: string[]) { return [...a].sort() }
```

- [ ] **Step 3: Run the test to verify it fails**

Run (from `dashboard/`): `npx vitest run src/pages/Automation.test.tsx`
Expected: FAIL — no element with text "Entry timeframes" exists yet.

- [ ] **Step 4: Add the control to `Automation.tsx`**

Add a timeframe-choices constant near the top of the file (after `const POLL_MS = 5000`):

```typescript
const TF_CHOICES = ['1', '3', '5', '15', '30', '45', '60']
const TF_LABEL: Record<string, string> = {
  '1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m', '45': '45m', '60': '1h',
}
```

Add state next to the existing `draft` state:

```typescript
  const [tfDraft, setTfDraft] = useState<string[] | null>(null)
```

In `refresh`, after the existing `setDraft(...)` call, sync `tfDraft` only when the user has not started editing (mirrors how `draft` itself is seeded once from the server and then left alone):

```typescript
      setTfDraft((current) => current ?? s.vt_ltf_timeframes)
```

Add a save handler next to `saveLimits`:

```typescript
  const saveTimeframes = async () => {
    await put({ vt_ltf_timeframes: tfDraft ?? [] }, 'Entry timeframes saved.')
  }
```

Add a new section in the JSX, right after the closing `</section>` of the existing "limits" section and before the "setup" section:

```tsx
      {/* ---------- entry timeframes ---------- */}
      <section className="rounded-lg border border-line bg-card p-5 space-y-4">
        <div>
          <h2 className="desk-label">Entry timeframes</h2>
          <p className="text-sm text-ink-soft mt-1 max-w-2xl">
            Which lower-timeframe VT Screener triggers are allowed to place a trade.
            An LTF alert on a timeframe not checked here is refused before anything else
            is even looked at. Starts empty — nothing trades until you turn one on.
          </p>
        </div>
        <div className="flex flex-wrap gap-4">
          {TF_CHOICES.map((tf) => (
            <label key={tf} className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                role="checkbox"
                aria-label={TF_LABEL[tf]}
                checked={(tfDraft ?? []).includes(tf)}
                disabled={!control}
                onChange={(e) => {
                  const next = new Set(tfDraft ?? [])
                  if (e.target.checked) next.add(tf); else next.delete(tf)
                  setTfDraft([...next])
                }}
                className="h-4 w-4"
              />
              {TF_LABEL[tf]}
            </label>
          ))}
        </div>
        {control && (
          <button onClick={saveTimeframes} disabled={busy}
                  className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
            Save entry timeframes
          </button>
        )}
      </section>
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/pages/Automation.test.tsx`
Expected: PASS — this test plus every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/pages/Automation.tsx dashboard/src/pages/Automation.test.tsx
git commit -m "$(cat <<'EOF'
feat(automation): let an admin choose which entry timeframes trade

A checkbox row for 1m/3m/5m/15m/30m/45m/1h next to the existing limits,
backed by the new vt_ltf_timeframes setting. Starts showing whatever
the server reports (empty by default), saved as its own PUT so it
does not interfere with the separate max-lots/rate limits save.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Pine relay script

**Files:**
- Create: `tradingview/vt-htf-ltf-relay.pine`

**Interfaces:**
- Produces: the JSON bodies Task 3's `parse_vt_alert` consumes (`role`, `symbol`, `tf`, `bias`, `entry`, `stop`, `target`, `price`, `valid`, `trigger` [ltf only], `lots` [ltf only], `bar_ms`).

There is no automated Pine test harness in this repo (per the spec's Testing section) — this task's verification is a manual compile in TradingView, the same step already used for `tradingview/fvg-multi-timeframe.pine`.

- [ ] **Step 1: Write the script**

```pine
//@version=6
indicator("MirrorFleet VT HTF-LTF Relay", overlay = true)

// Relays VT Screener's Entry/Stop/Target (a separate, already-running,
// protected indicator -- out of scope here) to MirrorFleet as either an
// HTF permission or an LTF trigger. One instance per chart; Role picks
// which. See docs/superpowers/specs/2026-09-09-vt-htf-ltf-bridge-design.md.

// ============================================================ MirrorFleet
secret = input.string("tvw_PASTE_SECRET_HERE", "MirrorFleet secret", group = "MirrorFleet")
role   = input.string("HTF", "Role", options = ["HTF", "LTF"], group = "MirrorFleet")
lots   = input.float(0.01, "Lots (LTF role only)", step = 0.01, minval = 0.01, group = "MirrorFleet")

// ============================================================ VT Screener
// Point each of these at VT Screener's own plot in this script's Settings
// after adding it to the chart -- Pine cannot discover them by name.
entrySrc  = input.source(close, "VT Screener Entry", group = "VT Screener")
stopSrc   = input.source(close, "VT Screener Stop", group = "VT Screener")
targetSrc = input.source(close, "VT Screener Target", group = "VT Screener")

// VT Screener's plots go na between setups; hold the last real value.
var float entry = na
var float stop = na
var float target = na
if not na(entrySrc)
    entry := entrySrc
if not na(stopSrc)
    stop := stopSrc
if not na(targetSrc)
    target := targetSrc

bias  = target > entry ? "long" : "short"
valid = bias == "long" ? (stop < entry and target > entry) : (stop > entry and target < entry)
haveLevels = not na(entry) and not na(stop) and not na(target)

// ============================================================ The MirrorFleet alert
// tf is read automatically, never typed by hand -- adding this script to a
// 1m/3m/5m/15m/... chart is what makes it that timeframe's source. The
// workspace's own Automation page, not this script, decides which
// timeframes actually trade.
tf = timeframe.period

// bar_ms is quoted: str.tostring() on a UNIX-ms value this large can render
// in scientific notation, and as a string it still parses fine on the
// server (int("1757400000000") == int(1757400000000)) -- the same reason
// fvg-multi-timeframe.pine quotes its own bar-time id.
htfMsg() =>
    '{"secret": "' + secret + '", "role": "htf", "symbol": "' + syminfo.ticker +
     '", "tf": "' + tf + '", "bias": "' + bias +
     '", "entry": ' + str.tostring(entry, format.mintick) +
     ', "stop": ' + str.tostring(stop, format.mintick) +
     ', "target": ' + str.tostring(target, format.mintick) +
     ', "price": ' + str.tostring(close, format.mintick) +
     ', "valid": ' + str.tostring(valid) +
     ', "bar_ms": "' + str.tostring(time) + '"}'

ltfMsg() =>
    '{"secret": "' + secret + '", "role": "ltf", "symbol": "' + syminfo.ticker +
     '", "tf": "' + tf + '", "bias": "' + bias +
     '", "entry": ' + str.tostring(entry, format.mintick) +
     ', "stop": ' + str.tostring(stop, format.mintick) +
     ', "target": ' + str.tostring(target, format.mintick) +
     ', "price": ' + str.tostring(close, format.mintick) +
     ', "valid": ' + str.tostring(valid) +
     ', "trigger": true, "lots": ' + str.tostring(lots) +
     ', "bar_ms": "' + str.tostring(time) + '"}'

isHTF = role == "HTF"
isLTF = role == "LTF"

// HTF: once per confirmed bar, while VT Screener's current setup is valid.
if isHTF and haveLevels and valid
    alert(htfMsg(), alert.freq_once_per_bar_close)

// LTF: only on the bar price actually reaches the held entry level.
// Note: if price keeps straddling `entry` for several bars in a row, this
// fires again on each one (a different bar, a different bar_ms, so
// MirrorFleet's dedup does not catch it) -- MirrorFleet's own
// max-open-positions cap is what bounds that, by design (risk is handled
// there, not in this script).
triggered = isLTF and haveLevels and valid and low <= entry and high >= entry
if triggered
    alert(ltfMsg(), alert.freq_once_per_bar_close)

plotshape(isHTF and haveLevels and valid, "HTF reading", shape.circle,
         location.top, color.blue, size = size.tiny)
plotshape(triggered, "LTF trigger", shape.triangleup, location.belowbar,
         color.lime, size = size.small)
```

- [ ] **Step 2: Manual verification (owner does this in TradingView)**

1. Open the Pine Editor, create a new indicator, paste the script above, Save.
2. Compile: expect **0 errors, 0 warnings**. If `plotshape` errors about local scope, that means it ended up inside an `if` — verify both `plotshape` calls are at the top level exactly as written above (this is the same v6 rule that bit `fvg-multi-timeframe.pine` earlier in this project).
3. Add it to an HTF chart (e.g. the 1H XAUUSD chart already carrying VT Screener). In its Settings, set **Role = HTF**, and bind **VT Screener Entry/Stop/Target** to VT Screener's actual plots via the three Source dropdowns.
4. Add a second instance to each LTF chart to be used (e.g. 1m, 5m). In each, set **Role = LTF**, bind the same three sources to that chart's own VT Screener instance, and set **Lots**.
5. Paste the org's MirrorFleet secret into every instance.
6. Create a TradingView alert on each chart, condition "Any alert() function call", webhook URL from the Automation page, and confirm at least one HTF alert lands as `accepted` and, once an LTF trigger passes the gate, an order shows as `accepted` on the Automation page's Recent Alerts list.

- [ ] **Step 3: Commit**

```bash
git add tradingview/vt-htf-ltf-relay.pine
git commit -m "$(cat <<'EOF'
feat(tradingview): relay VT Screener levels as an HTF permission or LTF trigger

One script, a Role input (HTF/LTF) switching behaviour. input.source()
bindings read VT Screener's own Entry/Stop/Target plots (Pine cannot
reach another script's table, only its plots); tf is auto-read from
timeframe.period so the same script is safe to add to any number of
LTF charts unmodified.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Data model (`vt_htf_snapshots`, `vt_ltf_timeframes`) → Task 1. ✓
- JSON contract / parsing → Task 2. ✓
- Webhook routing: role branch, htf storage, gates 0-4, ltf order placement, existing contract unaffected → Task 3 (3a/3b/3c). ✓
- Selectable entry timeframes (allowlist gate + settings API + dashboard UI) → Task 4 + Task 5. ✓
- Pine relay script → Task 6. ✓
- Testing section's specific scenarios (boundary math, staleness boundary, both bias directions, duplicate bar_ms, htf valid:false does not clear a good snapshot, regression on the existing contract) → present verbatim across Tasks 1-4's test steps. ✓
- Rollout (migration → migrate → rebuild api+dashboard, copier untouched) → no code task changes `copier`; execution of the rollout itself is a deploy action for whoever runs this plan, not a plan task, consistent with how every other feature in this project's history was deployed after its plan was executed.

**Placeholder scan:** the one intentional placeholder (3b's temporary `"order placement not yet implemented"` stand-in) is explicitly called out as deliberate and temporary, is exercised by nothing until removed in the next step of the same task, and is removed by 3c before the task tree completes — not a leftover TODO.

**Type consistency:** `VTAlert`, `VTSnapshot`, `GateResult` field names and order match between their Task 2 definition and every Task 3 call site (`check_gate(htf, vt, allowed_tfs, now)`, `VTSnapshot(bias, stop, target, tf, received_at)` built from the SQL SELECT's own column order). `receipt_alert = Alert(vt.role, vt.symbol, vt.lots, None, None, str(vt.bar_ms))` matches `Alert`'s existing field order `(action, symbol, lots, stop_loss, take_profit, alert_id)` unchanged from `tradingview_alerts.py`.
