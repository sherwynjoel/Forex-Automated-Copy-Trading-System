# VT HTF→LTF Bridge — Design

Status: approved by the owner on 2026-09-09; extended with selectable entry
timeframes on 2026-09-09.

## Goal

A higher-timeframe (HTF) signal acts as a *permission gate* on a lower-timeframe
(LTF) entry, both sourced from **VT Screener** (a protected third-party
TradingView indicator already running on the owner's charts, out of scope to
build or modify). VT Screener plots Entry/Stop/Target as `plot()` series —
readable by a separate script via `input.source()` — but shows nothing MirrorFleet
can read directly, and TradingView panes cannot share data with each other. Two
small relay scripts and a stateful gate on the server side join them:

- **1H chart**: VT Screener's current Entry/Stop/Target, relayed every
  confirmed bar. Permission only — never itself traded. Each alert overwrites
  the previous one; MirrorFleet keeps no history, only the latest.
- **LTF chart(s)**: VT Screener's current Entry/Stop/Target on that chart,
  relayed **only** when price actually reaches its own entry level. Not
  fixed to one timeframe — the owner runs this on however many LTF charts
  they choose (1m, 3m, 5m, 15m, ...), and the workspace decides, in
  MirrorFleet's own UI, which of those timeframes are actually allowed to
  trade (see "Selectable entry timeframes" below).
- The trade is built **entirely** from the triggering LTF alert's own levels
  — a market order when its entry triggers, stop at its stop, target at its
  target. (See the `role: "ltf"` section: entry execution is a MARKET order
  at trigger, not a resting limit.) The 1H
  levels are never traded; they exist purely to permit or block an LTF
  trigger.

Non-goals (explicitly excluded by the owner): R:R filters, daily trade caps,
cooldowns, position sizing beyond the alert's own `lots` field, EMA/momentum
filters. All handled elsewhere or not at all.

## Ground truth the design rests on

- MirrorFleet already has a per-org TradingView webhook door
  (`api/src/api/routes/webhooks.py`): `hook_id` in the URL resolves an
  `org_webhooks` row, a shared secret is checked with `hmac.compare_digest`
  against a stored sha256 hash, and `enabled` / `orgs.copying_enabled` /
  `orgs.dry_run` gates run before anything else. This bridge reuses that door
  completely — **same URL, same secret** as the org's existing simple
  action-webhook (used today by the FVG script). MirrorFleet tells the two
  alert shapes apart by a `"role"` key: its absence means the existing
  `{"action": "buy"|"sell"|"close", ...}` contract (untouched); its presence
  means this bridge.
- The copier's order-placement path (`CopierApp.place_order` →
  `MT5Lane.place_order` for MT5, the cTrader `ProtoOANewOrderReq` path for
  cTrader) already accepts `order_type: LIMIT` with `limit_price`,
  `stop_loss`, `take_profit`. No new order-placement or broker-adapter code is
  needed — the bridge only has to call the existing `POST {copier}/order`
  with `order_type: "LIMIT"` instead of `"MARKET"`.
- `webhook_receipts` already has a dedup mechanism keyed on a fingerprint plus
  a time window (`_fingerprint` in `webhooks.py`, checked under
  `pg_advisory_xact_lock(org_id)`). The bridge reuses this table and lock,
  with its own fingerprint function.
- Pine cannot read another script's table cells, only its `plot()` outputs,
  and only via a user-selected `input.source()` in the relay script's own
  settings — not by name lookup in code. The relay script therefore ships
  with `input.source()` slots the owner points at VT Screener's plots after
  adding the relay to the chart; it cannot discover them itself.

## Data model

One new table, `vt_htf_snapshots` — the latest HTF permission per symbol,
per org. No history: every valid HTF alert **upserts** the row.

```sql
CREATE TABLE vt_htf_snapshots (
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    bias        TEXT NOT NULL CHECK (bias IN ('long', 'short')),
    entry       DOUBLE PRECISION NOT NULL,
    stop        DOUBLE PRECISION NOT NULL,
    target      DOUBLE PRECISION NOT NULL,
    price       DOUBLE PRECISION NOT NULL,      -- HTF chart price at relay time, informational
    tf          TEXT NOT NULL,                  -- e.g. "60"; informational + staleness math
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, symbol)
);
```

Keyed on `(org_id, symbol)` only, **not** `tf` — matching "one HTF record per
symbol": there is exactly one active HTF permission per symbol per org at a
time, whatever timeframe produced it.

`org_webhooks` gets one new column: `vt_ltf_timeframes TEXT[] NOT NULL DEFAULT '{}'`
— the set of LTF timeframes (TradingView's own strings: `"1"`, `"3"`, `"5"`,
`"15"`, ...) this workspace currently allows to trade. **Defaults to empty**,
matching the app's existing safe-by-default posture (an unconfigured
workspace already hard-blocks on "no master account"; an unconfigured LTF
allowlist hard-blocks the same way, rather than silently trading every
timeframe a relay script happens to send).

## Selectable entry timeframes

The owner is not limited to one LTF chart. VT Screener (and the relay
script) can run on as many LTF charts as they want live — 1m, 3m, 5m, 15m,
each its own chart, its own relay-script instance, its own TradingView
alert, all pointed at the same webhook URL. Two things make "selectable"
real:

- **Pine side**: the relay script does not ask the owner to type or pick a
  timeframe — it reads `timeframe.period` (the chart's own timeframe,
  built into Pine) and puts that straight into the outgoing `tf` field.
  Adding the relay script to a 3m chart alone is what makes it a 3m source;
  there is nothing to misconfigure.
- **MirrorFleet side**: the Automation page gets a new control next to the
  existing Limits section — a multi-select of the timeframes the workspace
  currently allows (starting empty, per the owner's decision). An LTF
  trigger whose `tf` is not in this set is rejected before the HTF gate
  even runs, with its own distinct reason, so turning a timeframe on or off
  is a dashboard action, never a Pine edit or a new webhook secret.

This becomes gate **0**, ahead of the five already defined, in
`check_gate`'s ordered list — cheapest check first, and it is now also
`org`-scoped rather than purely a function of `(htf, ltf)`, so `check_gate`
gains an `allowed_timeframes: set[str]` parameter:

0. `ltf.tf in allowed_timeframes` — else `"entry timeframe {tf} is not enabled for this workspace"`
1. `ltf.valid is true` — else `"LTF setup is not valid (stop/target on the wrong side of entry)"`
2. `htf is not None` — else `"no HTF snapshot for {symbol} yet"`
3. not stale — else `"HTF snapshot for {symbol} is {age}s old (max {2*tf}s)"`
4. `ltf.bias == htf.bias` — else `"LTF bias ({ltf.bias}) does not match HTF bias ({htf.bias})"`
5. entry inside band — else `"LTF entry {entry} is outside the HTF band [{lo}, {hi}] (±{tol})"`

`vt_ltf_timeframes` is edited through a small addition to the existing
webhook-settings API/UI (same admin-only surface that already edits
`max_lots`/`max_per_minute`/`max_open_positions`), audited the same way
those changes already are (`settings_changed` event).

## JSON contract

Both roles share one endpoint and one secret. `symbol` is normalised the same
way the existing contract does (`normalise_ticker` — strips exchange prefix
and TradingView decorations).

```json
{
  "secret": "tvw_...", "role": "htf", "symbol": "XAUUSD", "tf": "60",
  "bias": "long", "entry": 4385.34, "stop": 4413.15, "target": 4301.91,
  "price": 4355.28, "valid": true, "bar_ms": 1757400000000
}
```

```json
{
  "secret": "tvw_...", "role": "ltf", "symbol": "XAUUSD", "tf": "1",
  "bias": "short", "entry": 4380.63, "stop": 4390.10, "target": 4360.00,
  "price": 4380.63, "valid": true, "trigger": true, "lots": 0.01,
  "bar_ms": 1757400060000
}
```

- `bias` is computed **in Pine**, not re-derived server-side: `target > entry`
  ⇒ `long`, else `short`. The gate trusts the field, exactly as the existing
  contract trusts `action`.
- `lots` is **required on `role: "ltf"`** (owner's decision — same philosophy
  as the existing contract: an explicit field the operator sets per chart,
  capped by the org's `max_lots`, never inferred). Absent/invalid on an `ltf`
  alert is a parse error, same shape as today's "lots is required" error.
  Not required on `role: "htf"`, and ignored if present there.
- `bar_ms` is required on both roles — it is the dedup key's tie-breaker, the
  same role the existing contract's `id` field plays.

## Webhook routing

`_handle` in `webhooks.py` is unchanged up through the `enabled` /
`copying_enabled` / `dry_run` / master-account gates. Immediately before the
existing `parse_alert(body, max_lots)` call, branch on the body:

```python
if "role" in body:
    return await _handle_vt_bridge(conn, request, cfg, org_id, master, max_lots, body, ...)
# else: existing parse_alert(...) path, byte-for-byte unchanged
```

`_handle_vt_bridge` is new code in `webhooks.py` (or a sibling module if it
grows large), calling into a new pure-logic module —
`api/src/api/vt_bridge_alerts.py` — mirroring the split already established
by `tradingview_alerts.py` (pure parsing/validation, no I/O, fully unit
testable):

- `parse_vt_alert(body: object, max_lots: float) -> VTAlert` — same loud,
  specific `AlertError` style as `parse_alert`: missing/wrong-typed fields,
  non-finite or non-positive prices, `bias` not in `("long","short")`,
  `role` not in `("htf","ltf")`, `lots` missing/invalid/over-cap on `ltf`,
  all rejected with an operator-readable reason, never a stack trace.
- `check_gate(htf: VTSnapshot | None, ltf: VTAlert, allowed_timeframes: set[str], now: datetime) -> GateResult` —
  pure function, no I/O, returns `(passed: bool, reason: str | None)`, first
  failure wins. Tolerance is not a caller argument: it is the module
  constant `TOL_PCT = 0.02`, applied to the band's own width inside the
  function. The full ordered check list (0-5) is in "Selectable entry
  timeframes" above, since gate 0 (the allowed-timeframes check) is
  introduced there.

Band math, direction-agnostic (owner's own formula):

```python
lo = min(htf.stop, htf.target)
hi = max(htf.stop, htf.target)
inside = (lo - tol) <= ltf.entry <= (hi + tol)
```

A fixed absolute `tol` would not generalise across instruments (a gold band
is thousands of times wider in price terms than a EURUSD band), so `tol` is
derived from the band's own width rather than a hardcoded price number:
`tol = TOL_PCT * (hi - lo)`, `TOL_PCT = 0.02` (2%) as the starting constant.
Not yet exposed as an org setting; promoting `TOL_PCT` to an `org_webhooks`
column is a follow-up if the fixed default proves wrong in practice, not
part of this build.

**Staleness**: computed at LTF-trigger time, not by a background sweep —
`now() - htf.received_at > 2 * int(htf.tf) * 60` seconds (owner's chosen 2×
multiplier; `tf` is minutes, as TradingView's own timeframe strings are).
A stale snapshot is treated identically to a missing one for gating purposes,
but logged with its own distinct reason so the owner can tell "never arrived"
from "arrived once, then stopped."

### `role: "htf"`

1. Parse. Malformed → `WebhookRejected(422, ...)`, recorded exactly like
   today's malformed-alert path — no crash, no partial write.
2. If `valid` is `true`: upsert `vt_htf_snapshots` on `(org_id, symbol)`.
   Outcome `accepted`, reason `null`.
3. If `valid` is `false`: **not stored** (a previous valid snapshot, if any,
   is left standing — an invalid reading does not erase a still-good
   permission). Outcome `accepted`, reason `"htf setup not valid, not stored"`.
4. Never calls the copier. Never dedups against `webhook_receipts` trading
   fields (there is no trade) — dedup fingerprint is
   `(org_id, "htf", symbol, tf, bar_ms)`, same table/window/lock as today,
   purely to protect against TradingView's own retry storms. As shipped,
   HTF receipts are **not** actually fingerprinted (`fp=None`) — the
   upsert is idempotent and HTF always answers 200, so TradingView never
   resends. The dedup promise above applies to the LTF trigger path.

### `role: "ltf"`

1. Parse (requires `lots`, as above).
2. If `trigger` is `false`: outcome `accepted`, reason
   `"ltf informational (trigger=false), no action"`. No gate evaluated, no
   order.
3. If `trigger` is `true`: dedup fingerprint
   `(org_id, "ltf", symbol, tf, bar_ms)` under the existing advisory-lock
   dedup path, then load the org's `vt_htf_snapshots` row for `symbol` and
   run `check_gate`.
   - Fails: `WebhookRejected(422, reason)` — the specific reason from
     `check_gate`, recorded in `webhook_receipts.reason` exactly like every
     other rejection today, visible on the Automation page's Recent Alerts
     table without any new UI work.
   - Passes: reuses the existing "opposite position" and `max_open`
     guards already in `_handle`. As shipped: the `max_open` guard counts
     open positions **plus** resting pending orders from `/state`; the
     opposite-position guard likewise also sees resting opposite pending
     orders on the symbol, not just filled positions; a per-minute cap
     identical to the existing path's runs inside the dedup transaction; and
     `org_webhooks.symbol_aliases` renames are applied to both `htf` and
     `ltf` alerts before storage and gating. Then
     `POST {copier}/order` with `order_type: "MARKET"`,
     `stop_loss: stop`, `take_profit: target`, `volume_lots: lots`,
     `actor_email: "tradingview"`. (Originally specced as a LIMIT at the
     entry price; changed to MARKET after live testing — the LTF trigger
     only fires once price has already reached the entry level, so a market
     fill lands at the intended entry, whereas a LIMIT placed on the wrong
     side of the current price is rejected by the broker as "invalid price"
     since VT's entry can be a pullback or a breakout level.) Same
     `CopierDown`/`CopierUnknown`
     handling as today (fingerprint freed on `CopierDown` so TradingView's
     resend is not swallowed; `unknown` answered 200 so TradingView does not
     retry into a possibly-live order).

## Pine relay script

One script (not two), `tradingview/vt-htf-ltf-relay.pine`, added once per
chart with a `Role` input (`"HTF"` / `"LTF"`) selecting behaviour — matching
the owner's spec exactly:

- `input.source()` × 3 for VT Screener's Entry, Stop, Target plots (owner
  binds them in the indicator's settings after adding it to each chart).
- `tf: timeframe.period` in the outgoing JSON on both roles — the chart's
  own timeframe, read automatically, never typed in by the owner. This is
  what makes the script safe to add to any number of LTF charts (1m, 3m,
  5m, 15m, ...) unmodified: each instance tags itself correctly, and
  MirrorFleet's allowed-timeframes setting (see "Selectable entry
  timeframes" above) decides which of those instances are actually live.
- Holds the last non-`na` value of each (`var float` + reassign only when not
  `na`), since VT Screener's plots go `na` between setups.
- `bias := target > entry ? "long" : "short"`.
- `valid := bias == "long" ? (stop < entry and target > entry) : (stop > entry and target < entry)`.
- Role `HTF`: fires `alert()` with the `role: "htf"` JSON on every confirmed
  bar close (`alert.freq_once_per_bar_close`) while `valid`.
- Role `LTF`: adds a `Lots` input (`input.float`, matching the FVG script's
  own `Lots` input). Fires `alert()` with the `role: "ltf"` JSON, `trigger:
  true`, only on the bar price actually reaches the held entry level — `low
  <= entry and high >= entry` on a fresh bar, once per bar close, mirroring
  the "confirmed bars only, no repaint" discipline already used in
  `fvg-multi-timeframe.pine`.
- The LTF role fires once per **distinct** held entry level, not on every
  bar that straddles it — a na-guarded `var float alertedEntry`, same
  pattern as `fvg-multi-timeframe.pine`'s `lastSignalledTime`, suppresses
  the repeat alerts that would otherwise stack identical resting orders.
- Same MirrorFleet `secret` input group as the FVG script; same one webhook
  URL, since role/secret are shared per the owner's decision above.

Exact alert-message JSON construction, the `HTF`/`LTF` bar-close vs.
trigger-only distinction, and any needed `plotshape`/`line.new` visuals are
implementation detail for the plan step, not fixed further here.

## Testing

- **Unit** (`api/tests/test_vt_bridge_alerts.py`, new): `parse_vt_alert`
  error cases (missing role, missing lots on ltf, bad bias, non-finite
  prices, oversized lots); `check_gate` — the allowed-timeframes check
  (empty set rejects everything, a `tf` present/absent from the set),
  band boundary exactness (entry exactly at `lo`/`hi`, exactly at
  `lo - tol`/`hi + tol`), bias mismatch, missing snapshot, stale snapshot
  at exactly `2×tf` and just past it, long vs. short band math both
  directions.
- **Integration** (`api/tests/test_webhooks.py`, extended): full htf-then-ltf
  sequences ending in an accepted LIMIT order, across more than one allowed
  LTF timeframe (e.g. a 1m and a 5m trigger against the same HTF snapshot,
  both accepted independently); an LTF trigger on a `tf` the workspace has
  not enabled; ltf trigger with no htf ever received; ltf trigger against a
  stale htf; ltf trigger with wrong bias; ltf trigger with entry outside the
  band; duplicate `bar_ms` on both roles; htf `valid: false` does not clear
  a standing good snapshot; the existing `action`-shaped contract continues
  to pass through completely unaffected (regression coverage, since both
  share `_handle`).
- **Pine**: manual compile-and-verify in the TradingView editor by the owner
  (0 errors/0 warnings), the same verification step already used for the FVG
  script — there is no automated Pine test harness in this repo.

## Rollout

Same pattern as every other change this session: migration (new table +
`org_webhooks.vt_ltf_timeframes` column) → `docker compose up migrate` →
rebuild + restart `api` and `dashboard` (the Automation page's new
allowed-timeframes control). `copier` is untouched — `/order` already does
everything needed — so no `copier` rebuild or restart.
