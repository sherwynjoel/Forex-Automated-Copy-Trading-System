# Server-Side Risk Engine — Design

Status: approved by the owner on 2026-09-16.

## Goal

Today, stop-loss / target / trailing only exist when a Pine script builds
them itself (the Dollar Bot and UT Bot 2026 Elite scripts compute them
client-side and send them in the alert). Any other indicator the owner's
trader switches to for a given market condition sends a bare `buy`/`sell`
with no protection at all, and there is no way to trail a stop unless the
indicator was specifically coded to do it.

This feature moves that job into MirrorFleet itself, so **every** trade
placed through automation gets a stop-loss, a target, and (optionally)
a trailing stop — regardless of which indicator triggered it:

- If the alert supplies its own `stop_loss`/`take_profit` (as the two wired
  bots already do), MirrorFleet uses those numbers exactly, unchanged from
  today.
- If the alert omits them, and the org has configured a rule for that
  symbol, MirrorFleet fills in the configured default.
- If a symbol's rule has trailing turned on, MirrorFleet trails the stop
  itself from that point forward — regardless of whether the starting stop
  came from the alert or from the rule's default — using the same
  amend-the-master mechanism that already propagates to every slave.

Non-goals (owner-confirmed by omission — not raised, not building them):
per-account rule overrides (rules are per-org-per-symbol, not
per-account), trailing the *target* (only the stop ever moves, matching
how the existing Pine bots behave), partial closes / scale-out at target,
any R:R or position-sizing logic beyond the lot cap that already exists.

## Ground truth the design rests on

- **The propagation mechanism already exists.** `POST
  /api/orgs/{org_id}/positions/amend` → copier's `POST
  /positions/amend` → `CopierApp.amend_position_sltp(account_id,
  position_id, stop_loss=..., take_profit=...)`. This is the exact
  mechanism a person uses today from the Trade page, and amending the
  master's protection already propagates to every slave through the
  normal copy path. No new broker-adapter code is needed for either
  cTrader or MT5 — the trailing engine just calls this on a timer instead
  of a person clicking a button.
- **Sharp edge to design around:** `AmendPositionResource`'s own docstring
  is explicit — *"an omitted protection is REMOVED"*. The endpoint does
  not do partial updates. Every trailing-engine amend call MUST resend
  both `stop_loss` and `take_profit` together (the new stop, and whatever
  the target currently is), or it will silently wipe the target the first
  time it moves the stop.
- **The periodic-loop pattern already exists**, many times over, in
  `copier/src/copier/main.py`'s `boot()`: `task.LoopingCall(app.some_method)`
  wired to `reactor_.clock`, `now=True/False` chosen per whether startup()
  already did the first pass, and the method body always swallows its own
  exceptions (`d.addErrback(...)`) because a `LoopingCall` whose Deferred
  fails stops looping permanently. The trailing check is a new instance of
  this exact pattern (see `check_mt5_offline`, `periodic_resync`,
  `refresh_balances` for precedent) — not a new mechanism.
- **Per-org-per-symbol config already has a precedent**: `symbol_aliases`
  (JSONB on `org_webhooks`, edited through the existing webhook-settings
  admin surface) is exactly this shape today (org → symbol → configured
  value). The new risk rules follow the same ownership and audit pattern
  (`settings_changed` event, admin-only).
- **The webhook contract's optional fields are unchanged.** `stop_loss`
  and `take_profit` in `tradingview_alerts.parse_alert` are already
  optional (`None` when absent) — the api's order-building code in
  `webhooks.py` needs **no change at all**: it already passes these
  through when present and omits them when absent, which is exactly the
  signal the copier-side fill-in step (below) needs.
- **`POST /order` does not return a fill.** `CopierApp.place_order`
  returns `{"status": "submitted", ...}` synchronously — the broker's
  fill arrives later as an execution event. This rules out "place order,
  read back the fill price, amend" as something the api layer can do in
  one request/response cycle. The fill-in step has to live where the fill
  event itself lands: `copier/src/copier/engine/service.py`'s
  `CopierService`, which already turns a market-order fill into a
  `MasterPositionOpened` event (`entry_price` included) for both cTrader
  (`engine/normalize.py`) and MT5 (`mt5/ingress.py`) — one shared handler
  covers both platforms. `CopierService` already has direct access to
  `self._repo` and `self._dispatcher` (or `mt5_lane`), so no new plumbing
  between processes is needed.

## Data model

**`org_risk_rules`** — one row per org+symbol the owner has configured.
Absence of a row means "no default, no trailing" — a symbol with no rule
behaves exactly as today (alert's own fields or nothing).

```sql
CREATE TABLE org_risk_rules (
    org_id              BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    symbol              TEXT NOT NULL,
    stop_points         DOUBLE PRECISION,          -- NULL = no default stop
    target_points       DOUBLE PRECISION,          -- NULL = no default target
    trailing_enabled    BOOLEAN NOT NULL DEFAULT false,
    trail_start_points  DOUBLE PRECISION,           -- required if trailing_enabled
    trail_step_points   DOUBLE PRECISION,           -- required if trailing_enabled
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, symbol)
);
```

`*_points` are raw price-distance values in the instrument's own quote
units — the same convention the existing Pine bots already use for
`Start trailing after (points)` / `Trail step (points)`, so a trader
moving a rule from a Pine input to this table is copying the same number,
not converting units.

**`position_trailing_state`** — the ratchet's memory. A stop must only
ever move in the favourable direction; that requires remembering the best
price a position has reached, not just its current price (price can pull
back toward the stop between checks without the stop unwinding).

```sql
CREATE TABLE position_trailing_state (
    account_id    BIGINT NOT NULL,
    position_id   BIGINT NOT NULL,
    best_price    DOUBLE PRECISION NOT NULL,   -- best favourable price seen
    current_stop  DOUBLE PRECISION NOT NULL,   -- last stop the engine set
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, position_id)
);
```

Row is created the first time a trailing-enabled position is seen, and
deleted when the position closes (hooked into wherever the copier already
notices a position is gone — the same place `reconciler`/`repo` already
drops closed positions from its tracked state).

## Fill-time behaviour (filling in defaults)

**Correction from the first draft of this spec**: this does not happen in
`api/src/api/routes/webhooks.py`. The api's order-building code is
unchanged — it sends `stop_loss`/`take_profit` when the alert has them,
omits them when it doesn't, exactly as today. Everything below happens
copier-side, in `CopierService` (`engine/service.py`), triggered off the
`MasterPositionOpened` event that already fires the instant a market
order fills (cTrader via `engine/normalize.py`, MT5 via
`mt5/ingress.py` — same event type, two producers, one handler). This
event already carries `entry_price`, `symbol`, `stop_loss`, `take_profit`
— everything needed, with no new plumbing between the api and the
copier processes.

`CopierService` already special-cases a `MasterPositionOpened` whose
`(stop_loss, take_profit) == (None, None)` (existing code, near
`_remember_master_protection`) — that is exactly the "alert sent
nothing" case. Extend that same branch:

```
rule = repo.load_risk_rule(org_id, event.symbol)
if rule is not None:
    stop_loss   = event.stop_loss   if event.stop_loss   is not None else (rule.stop_points   and entry_price ± rule.stop_points)
    take_profit = event.take_profit if event.take_profit is not None else (rule.target_points and entry_price ± rule.target_points)
    if (stop_loss, take_profit) != (event.stop_loss, event.take_profit):
        amend_position_sltp(event.account_id, event.position_id, stop_loss, take_profit, actor="risk-engine")
    if rule.trailing_enabled:
        repo.upsert_trailing_state(event.account_id, event.position_id,
                                   best_price=entry_price, current_stop=stop_loss)
```

`entry_price` here is the real fill price the event already carries —
computed the same way the wired bots compute their stop from the actual
entry candle rather than a pre-trade estimate, just server-side instead
of in Pine. An alert that already supplied both `stop_loss` and
`take_profit` skips the amend entirely (nothing changed, so nothing is
sent) but still gets a `position_trailing_state` row seeded if the
symbol's rule has trailing on — trailing applies regardless of where the
starting stop came from, per the Goal section above.

Symmetrically, `CopierService`'s existing `MasterPositionClosed` handling
(same file, already used to clean up other per-position tracking state)
gets one line added: `repo.delete_trailing_state(account_id, position_id)`.

## The trailing engine

One new `LoopingCall`, `app.check_trailing_stops`, alongside the existing
ones in `boot()`. Interval: `TRAILING_CHECK_INTERVAL_S`, proposed at 5
seconds — frequent enough to feel responsive against how fast gold/majors
move, infrequent enough to stay well inside cTrader's request-rate budget
(the existing `TokenBucket` throttle, 40 req/s, already caps everything
amend calls go through). Exact value is a plan-time tuning question, not
fixed further here.

Each tick, for every row in `position_trailing_state`:

1. Look up the position's current price. No single cross-platform helper
   for "one position's current price" exists today — branch the same way
   the rest of `main.py` already does (`self._is_mt5(account_id)`):
   cTrader reads from `self.state_trackers[org_id].snapshot()` (the same
   in-memory source `get_ticks` reads), MT5 reads from
   `self.mt5_registry`. If the position is not found in either (already
   closed, broker-side stop-out, anything), delete its
   `position_trailing_state` row on this same tick and skip it — a
   defensive second cleanup path alongside the `MasterPositionClosed`
   hook above, since that guarantees a vanished position's row cannot
   linger even if some future close path doesn't go through that event.
2. Update `best_price = max(best_price, current)` for a long position (
   `min` for a short).
3. If `best_price` has moved at least `trail_start_points` past entry,
   compute the new stop using the same ratchet formula the Pine bots
   already use (ported directly, not reinvented):

   ```
   new_stop = entry + (trail_start - trail_step
              + floor((best_fav - trail_start) / trail_step) * trail_step)
   ```
   (mirrored for a short: entry minus the equivalent, stop only ever
   moves closer to price, never away from it — `max`/`min` against the
   position's existing stop guards this even if the formula's own math
   were ever off by a step).
4. If `new_stop != current_stop`, call the amend endpoint with **both**
   `stop_loss: new_stop` and `take_profit: <whatever it already is>` (read
   from the position, not recomputed — see the "sharp edge" above), then
   update `position_trailing_state.current_stop`.

Never raises out of the `LoopingCall` body, matching every other loop in
this file — one bad price read or one failed amend must not silently end
trailing for every other open position for the rest of the process's
life. Failures log and the next tick tries again.

## Where you configure it

A new section on the existing **Automation** page, directly below the
current Limits section (max lots / alerts-per-minute / max open
positions) — same page, same admin-only editing surface, same
`settings_changed` audit trail. One row per symbol: stop, target,
trailing on/off with its start/step values when on. Backed by a small
CRUD addition to the webhook-settings API (list/upsert/delete a
`org_risk_rules` row), not a new router.

## Testing

- **Unit** (`copier/tests/unit/test_trailing.py`, new): the ratchet
  formula itself — long and short, exact boundary at `trail_start`, stop
  never moves backward even if `best_price` were fed out of order, stops
  produce identical numbers to the existing Pine formula for the same
  inputs (a direct port, checked against hand-computed cases from the
  Pine bots' own behaviour).
- **Unit** (`api/tests/test_risk_rules.py`, new): the risk-rules CRUD
  endpoints only (admin-only, validation, audit) — the plain buy/sell
  webhook path itself is regression-covered by its existing tests, which
  must keep passing unchanged since that code is not touched.
- **Unit** (`copier/tests/unit/test_service_risk_engine.py`, new): the
  fill-in logic in `CopierService` — alert's own values always win, a
  symbol with no configured rule behaves identically to today (no amend
  call at all), `stop_points`/`target_points` correctly applied against
  `entry_price`, a `position_trailing_state` row is seeded only when
  `trailing_enabled` is true.
- **Integration**: a position that crosses `trail_start` gets its stop
  amended with the target preserved (regression-guards the "omitted =
  removed" sharp edge directly); a closed position's `position_trailing_state`
  row is cleaned up via `MasterPositionClosed`; a trailing-check tick that
  fails for one position does not stop the loop for the next tick or the
  next position; a position missing from both `state_trackers` and
  `mt5_registry` has its row cleaned up defensively.

## Rollout

Same pattern as every prior change: migration (`org_risk_rules`,
`position_trailing_state`) → `docker compose up migrate` → rebuild +
restart `api` (new CRUD endpoints only — the webhook order-building path
is untouched), `dashboard` (new Automation section), and `copier` (new
`LoopingCall`, new fill-in logic in `CopierService`, new amend-on-trail
logic) — this is the first of this session's features that actually
touches `copier`, unlike the VT bridge which needed no copier changes at
all.
