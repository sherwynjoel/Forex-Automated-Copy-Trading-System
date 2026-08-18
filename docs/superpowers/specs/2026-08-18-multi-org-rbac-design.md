# Multi-User, Multi-Org RBAC — Design

**Date:** 2026-08-18
**Status:** Approved (brainstorm 2026-08-18)
**Branch:** `worktree-multi-org`

## 1. Overview

Convert the single-user, single-portfolio copy-trading system into a
multi-user, multi-organization application. An **organization** is both the
collaboration boundary and the portfolio: each org owns exactly one
copy-trading book — one master account, N slave accounts, its own kill
switch and dry-run flag. Users register themselves, create orgs, and join
other orgs via invite links, with per-org roles (Owner / Admin / Trader /
Viewer) gating what they can do. Because several endpoints place real-money
orders, the role checks are the system's primary safety boundary.

Decisions made during brainstorming:

- **Exactly one portfolio per org** — org and portfolio collapse into one
  concept. No separate `portfolios` table.
- **Self-signup + invite links** — anyone can register (email + password)
  and create orgs; joining an existing org requires an invite link. No
  email sending.
- **Roles:** Owner / Admin / Trader / Viewer.
- **Runtime:** one shared copier process, internally partitioned by org.

### Non-goals (explicitly out of scope)

- Billing, plans, quotas.
- Email delivery (invites are copyable links).
- Per-org copier processes or containers.
- Per-org Fernet keys or per-org cTrader OAuth apps (the single registered
  app, redirect URI, and `FERNET_KEY` stay global).
- Per-org rate-limit fairness inside the copier (the global 40 req/s token
  bucket stays; noted as follow-up).
- Surfacing infrastructure events (`org_id IS NULL`) in any UI.

## 2. Data model — migration `005_multi_org.sql`

### New tables

```sql
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,          -- argon2, same hasher as today
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));

CREATE TABLE orgs (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    copying_enabled BOOLEAN NOT NULL DEFAULT true,   -- moved from settings
    dry_run BOOLEAN NOT NULL DEFAULT false,          -- moved from settings
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
    token_hash TEXT NOT NULL UNIQUE,      -- SHA-256 of the raw token
    created_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,      -- created_at + 7 days
    consumed_at TIMESTAMPTZ,
    consumed_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);
```

Invite tokens are 32-byte urlsafe random strings shown once at creation;
only the SHA-256 digest is stored (same pattern as `oauth_states`). Invites
are **single-use** (consumed atomically, like OAuth states) and cannot
grant `owner` — ownership comes only from creating the org or promotion by
an existing Owner.

### Modified tables

- `ctid_connections` — add `org_id BIGINT NOT NULL REFERENCES orgs(id)
  ON DELETE CASCADE`.
- `accounts` — add `org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE
  CASCADE`. The primary key stays `ctid_trader_account_id` (the broker's
  global id): **a broker account may belong to at most one org**. Two
  engines replicating onto one live account would be a real-money hazard,
  so a second org connecting an already-claimed account gets a conflict,
  not a duplicate row. Replace the master-singleton index:

  ```sql
  DROP INDEX accounts_single_master;
  CREATE UNIQUE INDEX accounts_single_master
      ON accounts (org_id) WHERE role = 'master';
  ```

- `mappings` — add `org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE
  CASCADE` plus index `mappings_by_org (org_id)`.
- `events` — add `org_id BIGINT` (nullable, no FK — events are append-only
  and must survive org deletion) plus index `events_by_org (org_id, ts
  DESC)`. `org_id IS NULL` marks infrastructure events (engine start/stop,
  broker host connect/disconnect not attributable to one org); these are
  hidden from all org feeds.
- `oauth_states` — add `org_id BIGINT NOT NULL REFERENCES orgs(id) ON
  DELETE CASCADE`. Existing rows are deleted by the migration (they are
  short-lived CSRF state; in-flight OAuth dances during the upgrade fail
  harmlessly).
- `settings` — drop `copying_enabled` and `dry_run` (moved to `orgs`).
  The singleton row survives holding only process config (`shards`).
- `admin` — **dropped**. Replaced by `users`.

### Legacy data backfill (same migration)

If `ctid_connections` has any rows, the migration creates one org named
`Default` and backfills its `id` into `ctid_connections.org_id`,
`accounts.org_id`, `mappings.org_id`, and `events.org_id` (all existing
events attributed to it), and copies the old `settings.copying_enabled` /
`settings.dry_run` values onto the org. On a fresh database no org is
created. `NOT NULL` constraints are added after backfill.

## 3. Authentication

The session mechanism is unchanged (signed `itsdangerous` cookie + CSRF
double-submit cookie, 12 h expiry, `samesite=lax`, `secure` from config).
Only the payload changes: `{"authenticated": true}` becomes
`{"user_id": <id>}`. A fresh session cookie is issued on every login
(prevents fixation).

- `POST /api/register` — `{email, password, display_name}`. Password
  minimum 10 characters. Auto-login on success (sets cookies). Rate-limited
  per IP.
- `POST /api/login` — `{email, password}` (replaces password-only login).
  The existing in-memory rate limiter keys on `(lower(email), ip)`.
- `POST /api/logout` — unchanged behavior.
- `GET /api/me` — returns `{user: {id, email, display_name}, orgs:
  [{id, name, role}]}`.

### Bootstrap

`ADMIN_BOOTSTRAP_PASSWORD` is removed from required config. New optional
env vars `BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD`: on API
startup (lifespan, same slot as today's `ensure_admin()`), if both are set,
idempotently create that user, and if a `Default` org exists with zero
memberships (the legacy-backfill case), make the bootstrap user its Owner.
This is the only way to claim a migrated legacy org.

## 4. Authorization

### Role hierarchy and matrix

`viewer < trader < admin < owner`. A single `require_org_role(min_role)`
FastAPI dependency replaces `require_admin`: it resolves the session user,
loads their membership for the `{org_id}` path parameter, and returns the
`(user, org, role)` context. **Non-members get 404** (org existence never
leaks); members below the required role get 403.

| Action | Viewer | Trader | Admin | Owner |
|---|---|---|---|---|
| Overview, accounts list, positions, history, events, symbols, state, WS | ✓ | ✓ | ✓ | ✓ |
| Members list | ✓ | ✓ | ✓ | ✓ |
| Manual orders, close position, cancel order | | ✓ | ✓ | ✓ |
| Pause / resume / resync, copying toggle, dry-run, **close-all** | | | ✓ | ✓ |
| Account role / multiplier / enable / nickname, OAuth connect & disconnect, drift remedies, symbol refresh | | | ✓ | ✓ |
| Create / revoke invites (roles ≤ admin) | | | ✓ | ✓ |
| Change member roles, remove members, rename org, delete org | | | | ✓ |

Invariant: an org always has ≥ 1 Owner. Demoting or removing the last
Owner is rejected (409). Any member may leave an org themselves, except a
last Owner.

### Route restructure

All org-scoped routes move under `/api/orgs/{org_id}/…`, keeping their
current tails: `accounts`, `accounts/{id}` (PATCH), `accounts/{id}/details
| history/deals | history/orders | symbols`, `accounts/{id}/connection`
(DELETE), `settings` (GET/PUT — now org fields `copying_enabled`,
`dry_run`), `control/pause|resume|resync`, `control/close-all`, `state`,
`drift/close-orphan|adopt|dismiss`, `orders`, `positions/close`,
`orders/cancel`, `events`, `oauth/connect`.

New unscoped routes: `POST /api/orgs` (create org; creator becomes Owner),
`POST /api/orgs/join` (`{token}`, authenticated; consumes invite, creates
membership). New org-scoped: `GET/PATCH/DELETE /api/orgs/{org_id}`,
`GET /api/orgs/{org_id}/members`, `PATCH|DELETE
/api/orgs/{org_id}/members/{user_id}`, `POST|GET|DELETE
/api/orgs/{org_id}/invites[/{invite_id}]`.

`GET /api/oauth/callback` keeps its global path (the registered redirect
URI is fixed); the org context comes from the `oauth_states` row written at
connect time. Org deletion is Owner-only with a confirm phrase in the UI;
it cascades DB rows but never touches broker positions — the UI warns that
open positions remain at the broker.

### WebSocket

`GET /api/ws?org_id=N`. The handler authenticates the session cookie
(as today), then verifies org membership before accepting; non-members get
close code 4404. The `EventBroadcaster` becomes a dict `org_id →
set[socket]`; on each NOTIFY the api fetches the event row and pushes it
only to that org's sockets. `org_id IS NULL` events are pushed to no one.

## 5. Copier engine — per-org partitioning

The engine's routing question changes from "is this account id *the*
master" to "*which org's* master is this account". Concretely:

- `repo.load_accounts()` / `mapping_rows()` return `org_id`; new
  `repo.load_orgs()` returns per-org `{copying_enabled, dry_run}`.
- The engine keeps an org-partitioned book: `org_id → {master_account_id,
  slave ids, copying_enabled, dry_run, paused}`. `service.py`,
  `reconcile.py`, and `state.py` replace every `== self._master_account_id`
  comparison with a lookup of the event account's org followed by "is it
  that org's master". Slave events still only update mappings.
- Master events from org A fan out only to org A's slaves; `decide()`
  (already a pure function of `(event, mappings, slaves)`) is called with
  org-scoped inputs and needs no change.
- Drift/reconciliation runs per org; drift state is keyed by org.
- `close_all(org_id)` pauses **that org's** copying, then flattens only
  that org's enabled accounts. There is no all-orgs flatten endpoint.
- Control port endpoints gain an `org_id` parameter where scoped:
  `/pause /resume /resync /dry-run /close-all /state /drift/*`. `/health`
  stays global; `/discover` derives the org from the connection row;
  `/reload` reloads all orgs. The port stays Docker-internal and
  unauthenticated — the API remains the sole identity/authz boundary and
  passes `org_id` on every command.
- Discovery conflict rule: if a discovered account already exists under a
  *different* org, skip the upsert, write an `error`-severity event to the
  discovering org, and the API surfaces "account already connected to
  another organization". No stealing.
- Connection sharding (`account_id % shards`), the shared TLS pools, and
  the global token bucket are unchanged.

## 6. Frontend

- **New pages:** `/register`, `/login` (email + password), `/join/:token`
  (prompts login/register first, then joins and redirects into the org),
  `/welcome` (shown when the user has no orgs: create one or paste an
  invite link), and per-org `members` (list, role changes, invite-link
  generation with copy button).
- **Org context in the URL:** app routes become `/org/:orgId`,
  `/org/:orgId/accounts`, `/positions`, `/trade`, `/history`, `/logs`,
  `/members`. The Layout gains an org switcher; the last-used org is
  remembered in `localStorage` for the root redirect.
- `api.ts` gains an org-scoped request helper; `useLiveRefresh` passes
  `org_id` to the socket. `types.ts` adds `User`, `Org`, `Role`,
  `Member`, `Invite`.
- **Role-aware UI:** a `can(role, action)` helper mirrors the server
  matrix; controls below the caller's role are hidden (trade ticket,
  kill switches, member management). The server enforces regardless.
- Existing pages keep their function, scoped to the active org.

## 7. Config / env changes

- Removed: `ADMIN_BOOTSTRAP_PASSWORD` (required today).
- Added (optional pair): `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`.
- Unchanged: `SESSION_SECRET`, `FERNET_KEY`, all `CTRADER_*`,
  `POSTGRES_*`, `COPIER_CONTROL_URL`, `SHARDS`, `COOKIE_SECURE`.
- `.env.example`, README setup and deployment sections updated (the
  "single admin password" warning is replaced by guidance on self-signup:
  the instance is still self-hosted; anyone who can reach it can register,
  but registration grants no access to any existing org).

## 8. Testing

- **api (pytest, real Postgres on port 5435):** new fixtures
  `make_user`, `make_org(owner, …members)`, `login_as(client, user)`. The
  centerpiece is a **parametrized role-matrix test**: every protected
  endpoint × every role (plus non-member and anonymous) → expected status
  (200-class / 403 / 404 / 401). Explicit cross-tenant tests: org B's
  member gets 404 on all org A resources; WS from org B never receives
  org A events (via the `live_server` fixture). Invite lifecycle
  (create, consume, expire, revoke, double-consume), last-Owner invariant,
  register/login validation and rate limits, bootstrap-claim idempotency.
  Truncate lists in `api/tests/conftest.py` gain the new tables.
- **copier (pytest + pytest-twisted):** two-org fixtures; interleaved
  master events route only to their own org's slaves; `close_all(org)`
  flattens only that org; per-org reconcile and settings reload; discovery
  conflict rule. Truncate list in `copier/tests/conftest.py` updated.
- **dashboard (vitest):** register/login/join flows, org switcher,
  welcome page, role gating (viewer sees no trade ticket or kill
  switches), members page.
- **e2e:** seed two orgs through real SQL (org A: master 100 + slaves
  101/102 as today; org B: master 200 + slave 201), drive fills for both
  masters through the fake broker, assert copies land only within each
  org, and assert an org B session cannot read org A data over the API.
- Per project convention: suites run against host port 5435, **never
  concurrently** with each other or the compose e2e.

## 9. Security notes

- Real-money endpoints (`orders`, `positions/close`, `orders/cancel`,
  `close-all`) are the highest-stakes checks; the role-matrix test pins
  every one of them.
- 404-for-non-members prevents org/resource enumeration; 403 only within
  an org.
- Invite tokens: 32-byte urlsafe, hashed at rest, single-use, 7-day
  expiry, revocable.
- Session fixation prevented by re-issuing the cookie at login; CSRF
  double-submit unchanged and covers `join` and all org routes. `register`
  is CSRF-exempt like `login` — both are pre-session endpoints where no
  CSRF cookie exists yet.
- The copier control port keeps its "Docker network only" trust model —
  compose must continue not publishing port 8080; every scoped command
  carries an explicit `org_id` chosen by the API, never by the browser.
- The login rate limiter stays in-memory (single-replica deployment);
  keyed by `(email, ip)`, with a per-IP cap on `register`.
