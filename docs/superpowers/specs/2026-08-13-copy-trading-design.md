# Forex Copy-Trading System — Design

**Date:** 2026-08-13
**Status:** Approved by user (sections 1–4 approved in brainstorming session)
**Repo:** https://github.com/sherwynjoel/Forex-Automated-Copy-Trading-System

## 1. Purpose

Replicate every trade action from one **master** cTrader account to ~49 **slave**
accounts at the same broker (FP Markets, confirmed by user; supports cTrader
since 2023), in real time, via the cTrader Open API. A web dashboard provides monitoring, control, account
onboarding, and a full audit log.

## 2. Requirements (user-confirmed)

- **Replication scope:** market position open/close (including partial closes),
  SL/TP set and modification, pending orders (limit/stop) place/modify/cancel.
  Master rejections/expiries replicate as no-ops (logged only).
- **Sizing:** exact mirror — slave lots = master lots × per-slave multiplier,
  default 1.0. No balance-proportional scaling.
- **Accounts:** organization across cTrader IDs unknown ("mixed / not sure") →
  system supports connecting any number of cTrader IDs via OAuth; each connected
  ID contributes its trading accounts; user assigns roles in the dashboard.
- **Stack:** Python backend (Spotware official SDK `ctrader-open-api`/OpenApiPy,
  Twisted-based) + React dashboard. User explicitly chose this over TS full-stack.
- **Deployment:** Docker Compose so it runs identically anywhere; user runs it
  locally first and moves to a VPS when ready (their explicit choice:
  "Docker-ready, decide later").
- **Rollout:** demo-first, then live.

## 3. Key cTrader Open API facts (researched 2026-08-13)

- **Endpoints:** TCP/TLS protobuf — `live.ctraderapi.com:5035`,
  `demo.ctraderapi.com:5035`. Demo and live cannot share a connection; one
  connection carries many authorized accounts.
- **Auth chain:** OAuth2 grant per cTrader ID (`trading` scope) → access token
  (~30-day) + refresh token (rotates on every refresh; must persist new one) →
  per connection: `ProtoOAApplicationAuthReq` (app clientId/secret) once, then
  `ProtoOAAccountAuthReq` per trading account. One OAuth grant covers all
  accounts under that cTID **at grant time**; accounts created later require
  re-granting.
- **Account discovery:** `ProtoOAGetAccountListByAccessTokenReq` → accounts with
  `ctidTraderAccountId`, `isLive`, `traderLogin`.
- **Trade messages:** `ProtoOANewOrderReq` (MARKET/LIMIT/STOP; `stopLoss`,
  `takeProfit`, `label` ≤100 chars, `clientOrderId` ≤50 chars),
  `ProtoOAClosePositionReq` (supports partial volume),
  `ProtoOAAmendPositionSLTPReq`, `ProtoOAAmendOrderReq`, `ProtoOACancelOrderReq`.
  Trade requests get **no synchronous response** — outcomes arrive as
  `ProtoOAExecutionEvent` (types: ORDER_ACCEPTED, ORDER_FILLED, ORDER_REPLACED,
  ORDER_CANCELLED, ORDER_EXPIRED, ORDER_REJECTED, ORDER_PARTIAL_FILL, …).
- **Volume units:** protocol volume = base-asset units × 100 (NOT centilots).
  1.00 lot EURUSD (lotSize 100,000) = protocol volume 10,000,000. Convert via
  `ProtoOASymbol.lotSize` ("in cents"): `protocolVolume = lots × lotSize`.
- **Symbols:** numeric `symbolId` per broker environment; resolve name→id per
  account via `ProtoOASymbolsListReq`. Match across accounts by **name**.
- **Heartbeat:** `ProtoHeartbeatEvent` at least every 10 s or server disconnects.
- **Rate limit:** 50 req/s per connection (5/s for historical). Limits are per
  connection, so sharding accounts across connections multiplies throughput.
- **Reconnect resync:** `ProtoOAReconcileReq` returns open positions + pending
  orders for an account. `ProtoOAAccountsTokenInvalidatedEvent` /
  `ProtoOAAccountDisconnectEvent` signal session loss for specific accounts.
- **SDK:** `ctrader-open-api` (PyPI, Spotware official, v0.9.2 2024-06, Twisted).

## 4. Architecture

Three Docker Compose services:

```
copier (Python + OpenApiPy/Twisted)  ←TLS/protobuf→  cTrader servers
   ↕ Postgres (state + audit log + LISTEN/NOTIFY event feed)
api (Python + FastAPI)  ←REST/WebSocket→  React dashboard (static build served by api)
postgres
```

- **copier** — trading engine only. Owns cTrader connections (one per
  environment needed: demo, live; sharding to N connections per environment is a
  config knob, default 1), heartbeats, reconnect with backoff, token refresh,
  master event subscription, slave order fan-out, reconciliation. Writes every
  event/action/error to Postgres and emits `pg_notify` for the live feed.
  Exposes a minimal internal HTTP control endpoint (Docker network only) for
  commands: pause/resume (global + per slave), resync, account role reload,
  dry-run toggle, drift-fix actions.
- **api** — FastAPI + uvicorn. Serves dashboard static build, REST API,
  WebSocket live feed (fed by Postgres LISTEN/NOTIFY), OAuth redirect/callback
  for connecting cTrader IDs, admin session auth. Forwards control commands to
  copier's internal endpoint. api being down never affects copying.
- **postgres** — single source of truth (schema in §6).

Rationale for process split: OpenApiPy is Twisted; FastAPI is asyncio. Isolation
keeps the trading engine independent of dashboard failures and avoids
Twisted/asyncio reactor entanglement.

## 5. Replication engine

### Event → action rules (master events only)

| Master event | Slave action per enabled slave |
|---|---|
| ORDER_FILLED / ORDER_PARTIAL_FILL, deal opens/increases position | `ProtoOANewOrderReq` MARKET, same symbol (by name) + side, mirrored volume, master's SL/TP, label `copy:m<masterPositionId>` |
| Deal closes position volume (full/partial) | `ProtoOAClosePositionReq` on mapped slave position, same fraction of the slave position's volume |
| SL/TP amended on position | `ProtoOAAmendPositionSLTPReq` on mapped position |
| Pending order accepted (LIMIT/STOP) | `ProtoOANewOrderReq` same type/price/SL/TP, label `copy:o<masterOrderId>` |
| Pending order replaced | `ProtoOAAmendOrderReq` on mapped order |
| Pending order cancelled/expired | `ProtoOACancelOrderReq` on mapped order |
| Master ORDER_REJECTED / errors | No slave action; logged |
| Pending order fills on master | No new slave open — the slave's own mapped pending order fills broker-side; engine links resulting positions via the order mapping and reconciles fill differences (slave order not yet filled → alert) |

- Slave events never trigger replication (loop-proof by construction); they are
  consumed only to update mappings (e.g., copy order filled → record slave
  positionId) and logs.
- The decision core is a **pure module**: `(master_event, mapping_state,
  slave_configs) → [SlaveIntent]`. No I/O. This is where sizing, volume
  conversion, partial-close fractions, and mapping lookups live; exhaustively
  unit-tested, including order-of-magnitude volume cases.

### Sizing & symbols

- slave lots = master lots × multiplier (default 1.0; editable per slave).
- lots ↔ protocol volume via each account's symbol `lotSize`; volumes rounded to
  the symbol's `stepVolume`; below-minimum or margin-failing orders are rejected
  by the broker and surfaced as alerts — never silently resized.
- Per-account symbol map (name → symbolId, digits, lotSize, min/step volume)
  built on account auth, cached, refreshed on demand.

### Mappings & persistence

Postgres table maps `(master_position_id | master_order_id) × slave_account →
slave_position_id | slave_order_id`, with status (pending/active/closed/failed)
and audit timestamps. Labels on slave orders (`copy:m…`/`copy:o…`) allow mapping
reconstruction from broker state if DB and reality diverge.

### Failure handling

- Slave actions are independent; failures never block other slaves.
- Retry ×3 with short backoff (1s/2s/4s) on transient errors; then mark slave
  **degraded** with the exact error; degraded slaves keep receiving future
  master events (each action independent) but every failure alerts.
- Reconnect/restart: re-auth accounts, `ProtoOAReconcileReq` everywhere, compare
  to mappings → **drift report** in dashboard. Drift is reported, never
  auto-traded. One-click remedies: close orphaned slave position, adopt
  unmapped position into mapping, dismiss.
- Master trades made while copier is down are **missed, not replayed** (late
  entry at a different price is worse than no entry); they appear as drift.
- Token refresh proactively at <25 days; rotated refresh token persisted in the
  same transaction. `ProtoOAAccountsTokenInvalidatedEvent` → re-auth flow;
  if refresh fails, alert prominently in dashboard (user must re-grant OAuth).

### Rate limiting

Token-bucket at 40 req/s per connection (cap 50). 49-slave fan-out ≈ 1.2 s worst
case on one connection. Config knob shards slave accounts across N connections
per environment if faster fan-out is needed.

## 6. Data model (Postgres)

- `ctid_connections` — connected cTrader IDs: encrypted access/refresh token
  (Fernet, key from env), grant timestamp, scope, status.
- `accounts` — ctidTraderAccountId, ctid FK, broker login, isLive, role
  (master/slave/ignored), enabled flag, multiplier, status
  (ok/paused/degraded), last error.
- `symbol_cache` — per account: name, symbolId, digits, lotSize, minVolume,
  stepVolume.
- `mappings` — as in §5.
- `events` — append-only audit log: timestamp, account, category (master_event,
  slave_action, connection, auth, drift, control), severity, latency_ms,
  payload JSON. Emits `pg_notify('events', id)` on insert.
- `settings` — global flags: copying enabled (kill switch), dry_run, shards.
- `admin` — single admin user: argon2 password hash.

## 7. Dashboard (React + Vite + Tailwind)

1. **Overview** — environment connection status; master card (equity, balance,
   open P&L); slave grid (status 🟢/⏸/🔴, equity, open positions); global kill
   switch; per-slave pause.
2. **Accounts** — "Connect cTrader ID" (OAuth popup → api callback); discovered
   account list; role assignment (exactly one master enforced); per-slave
   multiplier; disconnect/re-grant.
3. **Positions** — master open positions & pending orders, expandable per-slave
   copy status (fill price, slippage vs master, error). Drift/orphan list with
   one-click remedies.
4. **Logs** — filterable audit trail (account, severity, category, date), live
   via WebSocket.

## 8. Security

- Dashboard: single admin; password from env on first boot, argon2-hashed;
  signed HTTP-only session cookie; CSRF protection; login rate limiting.
- No broker/cTrader passwords ever seen or stored — OAuth only; tokens
  revocable at ctrader.com. Tokens encrypted at rest (Fernet, env key).
- Secrets (`clientId`, `clientSecret`, DB password, Fernet key, admin bootstrap
  password) in `.env`, git-ignored; `.env.example` committed.
- copier's control endpoint bound to Docker internal network only.

## 9. Testing & rollout

- **TDD.** Pure decision core unit-tested exhaustively (sizing, volume
  conversion, partial closes, pending lifecycles, mapping edge cases).
- **Fake cTrader server** (local protobuf server speaking the same messages)
  for integration tests: fills, rejections, disconnects, token expiry,
  heartbeat timeouts.
- **Dry-run mode:** real connections, real master events, slave actions logged
  but not sent. Final gate before live copying; also verifies manual platform
  trades arrive via the API (docs don't state it verbatim; Spotware markets the
  API for copy trading, so expected to work — verify in stage 1).
- **Stages:** (1) dry-run vs demo master with manual trades; (2) demo master →
  2–3 demo slaves live copying; (3) scale demo slaves, test kill switch,
  restarts, drift; (4) real accounts.

## 10. Open questions / user prerequisites

1. **Register app at openapi.ctrader.com** (user): Spotware manually reviews —
   start immediately; yields clientId/clientSecret. Describe the app honestly
   as personal copy-trading across own accounts.
2. ~~Broker identity~~ — **resolved 2026-08-13**: user confirmed the broker is
   FP Markets (fpmarkets.com), which supports cTrader.
3. Undocumented API details to verify on demo: manual-trade event delivery
   (§9), max accounts per connection, any per-app aggregate rate limit.

## 11. Out of scope (v1)

- Balance-proportional or risk-based sizing (multiplier field covers future).
- Multi-master support; slave-side independent trading detection beyond drift.
- Trade filtering rules (symbols/sessions/max exposure caps).
- Mobile app; multi-user dashboard accounts; email/telegram alerting.
