# Forex Copy-Trading System

Replicates every trade action from one **master** cTrader account to any number
of **slave** accounts at the same broker (FP Markets), in real time, over the
cTrader Open API. A web dashboard provides monitoring, control, account
onboarding, and a full audit log. One deployment serves **many organizations**
side by side, each with its own book and its own team.

## 1. What this is

The system watches a master trading account and mirrors its market
opens/closes (including partial closes), SL/TP changes, and pending order
lifecycle to a configurable list of slave accounts, each with its own
lot-size multiplier. It runs as four Docker Compose services: a Python/Twisted
**copier** that owns the cTrader connections and does the actual trade
replication, a **FastAPI** backend that serves the dashboard and proxies
control commands to the copier, **Postgres** as the single source of truth
for users, orgs, accounts, mappings, and the audit log, and a one-shot
**migrate** job that applies the schema on startup before `copier`/`api`
start.

### Multi-user, multi-org

Anyone can **register** an account on the instance; registering by itself
grants access to **nothing**. Access is always to a specific **organization**
(org), and it comes from a membership row: you either create an org (which
makes you its Owner) or you join one through an **invite link** an existing
Admin or Owner generated for you. A user can belong to any number of orgs and
switches between them in the dashboard.

An org is the unit of isolation and the unit of trading: one master account,
its slaves, its cTrader ID grants, its mappings, its audit log, its
copying/dry-run switches. **Nothing crosses an org boundary** — every
tenant-facing route lives under `/api/orgs/{org_id}/…`, and a request for an
org you are not a member of returns `404`, not `403`, so the existence of
other orgs never leaks. `e2e/test_multi_org.py` runs two orgs on one stack and
asserts exactly that, including that one org's kill switch leaves the other's
positions open.

Within an org, four roles nest — `viewer < trader < admin < owner`:

| Action | Viewer | Trader | Admin | Owner |
|---|---|---|---|---|
| Overview, accounts list, positions, history, events, symbols, state, live feed | ✓ | ✓ | ✓ | ✓ |
| Members list | ✓ | ✓ | ✓ | ✓ |
| Manual orders, close position, cancel order | | ✓ | ✓ | ✓ |
| Pause / resume / resync, copying toggle, dry-run, **close-all** | | | ✓ | ✓ |
| Account role / multiplier / enable / nickname, OAuth connect & disconnect, drift remedies, symbol refresh | | | ✓ | ✓ |
| Create / revoke invites (roles ≤ admin) | | | ✓ | ✓ |
| Change member roles, remove members, rename org, delete org | | | | ✓ |

An org always has at least one Owner: demoting or removing the last one is
rejected. Any member may leave an org themselves, except a last Owner.

```mermaid
flowchart LR
    subgraph dockerCompose["Docker Compose"]
        copier["copier\n(Python + Twisted / OpenApiPy)\ntrading engine, internal control port 8080"]
        api["api\n(FastAPI + uvicorn)\nserves dashboard, REST + WebSocket, OAuth"]
        postgres[("postgres\nusers · orgs · accounts\nmappings · events\nLISTEN/NOTIFY")]
    end

    ctrader["cTrader Open API servers\ndemo.ctraderapi.com / live.ctraderapi.com\nTCP + TLS, protobuf"]
    browser["Operator browser\nReact dashboard (static build, served by api)\nhttp://localhost:8000"]

    copier <-->|"TLS / protobuf\nmaster events, slave orders"| ctrader
    copier <-->|"state, audit log\npg_notify('events')"| postgres
    api <-->|"SQL reads\nLISTEN 'events'"| postgres
    api -- "control commands\n(pause/resume/resync/dry-run/drift)\nDocker-internal network only" --> copier
    browser <-->|"REST + WebSocket"| api
```

- **copier** — trading engine only. Owns the cTrader connections (one per
  environment needed: demo, live), heartbeats, reconnect with backoff, token
  refresh, master event subscription, slave order fan-out, and
  reconciliation. Writes every event/action/error to Postgres. Exposes a
  minimal internal HTTP control endpoint (Docker network only, port 8080,
  never published to the host) for pause/resume, resync, dry-run toggle, and
  drift-fix commands.
- **api** — FastAPI + uvicorn. Serves the dashboard's static build, the REST
  API, a WebSocket live feed (fed by Postgres `LISTEN/NOTIFY`), the OAuth
  redirect/callback for connecting cTrader IDs, and session auth. Owns
  authorization: it resolves the caller's role in the org named by the URL
  before anything is read, written, or proxied. Forwards control commands to
  the copier's internal endpoint. The api being down never affects copying —
  the copier keeps trading independently.
- **postgres** — single source of truth: users, orgs, memberships and
  invites, connected cTrader IDs (encrypted tokens), accounts and roles,
  symbol cache, position/order mappings, the append-only event log, and each
  org's copying/dry-run switches.

## 2. Prerequisite: register an Open API app

Before anything else, register an app at **https://openapi.ctrader.com**.
Spotware reviews applications manually, so do this first — it can take a
while and everything else in this README depends on the credentials it
issues.

- Describe the app honestly: **personal copy-trading across your own
  accounts**. That's what this system does — one master account you already
  trade, mirrored to slave accounts you also own.
- Set the **redirect URI** to `http://localhost:8000/api/oauth/callback` for
  local/demo use. When you move the stack to a VPS, update the redirect URI
  both in the cTrader app settings and in `CTRADER_REDIRECT_URI` in `.env` to
  match the VPS's public URL.
- Once approved, you receive a `clientId` and `clientSecret`. Keep these
  secret — they go into `.env`, never into git.

## 3. Setup

```bash
cp .env.example .env
```

Edit `.env` and fill in:

- `CTRADER_CLIENT_ID` / `CTRADER_CLIENT_SECRET` — from step 2 above.
- `FERNET_KEY` — the key used to encrypt OAuth tokens at rest. Generate one:

  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

  If your host Python doesn't have the `cryptography` package installed,
  either run it inside the copier venv after following the Development setup
  below (`copier/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`),
  or use Docker with no local setup at all:

  ```bash
  docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
  ```

- `SESSION_SECRET` — any long random string (e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
- `POSTGRES_PASSWORD` — the local default (`copytrader`) is fine for local
  use; change it before running on a VPS.
- `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` — **leave both empty on
  a fresh install.** They exist only for upgrades: a deployment that predates
  multi-org has its accounts gathered into a member-less `Default` org by the
  migration, and setting both here creates that user on boot and makes them
  its Owner — the only way to claim it. On a new install you register through
  the UI instead.

Then bring the stack up:

```bash
docker compose up -d --build
```

This builds and starts `postgres`, runs the `migrate` service to apply the
schema, then starts `copier` and `api`. With no `CTRADER_CLIENT_ID`/`SECRET`
configured yet (or with placeholder values), the copier idles safely — it
only errors if you try to actually connect accounts.

Open **http://localhost:8000** and **register the first user** — email,
password (10 characters minimum), display name. There is no preset admin
login; the first person to register is simply the first user, and registering
gives them no access to anything that already exists.

Then:

1. **Create an organization.** Whoever creates it is its Owner. Everything
   below happens inside that org, and you can create more later (one per
   customer, per desk, per master account — an org holds exactly one master).
2. **Invite your team** from the org's Members screen: generate an invite
   link for the role you want them to have (`viewer`, `trader`, or `admin` —
   an invite can never grant Owner), and send it to them. They register (or
   log in) and open the link to join. Roles are per org, so the same person
   can be an Admin in one org and a Viewer in another. Change or revoke
   anyone's role from the same screen.
3. Go to **Accounts → Connect cTrader ID**. This opens the cTrader OAuth
   consent popup — no broker username or password is ever entered into this
   system. One OAuth grant covers **every trading account under that cTrader
   ID at the time you grant it**; if you add accounts to that cTID later,
   you'll need to re-grant (see Operations below). The grant, and every
   account under it, belongs to the org you connected it from.
4. You can revoke access at any time from your account settings at
   ctrader.com, independent of this system.
5. Once accounts are discovered, assign roles: exactly **one master per org**
   (enforced — the UI/API rejects a second), and any number of **slaves**,
   each with its own **multiplier** (slave lots = master lots × multiplier,
   default `1.0`).

## 4. Rollout stages (demo-first — do not skip)

Roll out gradually against **demo** accounts before touching anything live.
Each stage gates the next.

**Stage 1 — dry-run vs. a demo master.**
Assign a demo account as master, turn dry-run mode on, connect zero or more
demo slaves. Place manual trades directly in the cTrader platform (not
through this system) and confirm they appear in the dashboard's Logs screen
as master events, with `dry_run` "would-send" entries for what would have
been copied. This single step verifies two things at once: that manually
placed platform trades actually arrive over the Open API (undocumented by
Spotware, but expected — see spec §10.3), and gives you a first read on two
open unknowns: how many accounts you can authorize on one connection, and
whether there's any per-app aggregate rate limit beyond the documented
per-connection limit.

**Stage 2 — demo master → 2–3 demo slaves, dry-run off.**
Turn dry-run off and let copying run live against a small number of demo
slaves. Verify fills, partial closes, SL/TP propagation, pending order
place/modify/cancel, and that multipliers produce the expected slave volumes.

**Stage 3 — scale to your full demo slave count.**
Add the rest of your demo slaves. Exercise the operational controls before
trusting them with real money:
- the global kill switch (pauses all copying),
- per-slave pause,
- restarting the copier process (`docker compose restart copier`) while the
  master has open trades — trades made while the copier was down are
  **missed, not replayed**, and should surface as drift, never as a delayed
  copy,
- reconnect behavior after a restart,
- the drift remedies (close-orphan / adopt / dismiss) on whatever drift the
  restart produced.

**Stage 4 — live accounts.**
Only after stages 1–3 behave exactly as expected, connect real accounts.
Start with the smallest multiplier you can tolerate on your first live
slave, confirm a few real trades copy correctly, then scale up.

## 5. Operations

- **Copy pause** — a per-org pause (Overview screen or `POST
  /api/orgs/{org_id}/control/pause` with no account id). It stops all copying for that org
  immediately, without affecting any other org; resume with the matching resume control.
  It does not disconnect accounts or lose state — mappings and settings are untouched.
- **Kill switch (close-all)** — flattens actual positions, not just copying.
  The desk strip's "Close all positions" button (every page; requires typing
  `CLOSE ALL`) closes every open position and cancels every working order in
  every enabled account of that org from a fresh broker snapshot, and pauses copying
  first so the master's closes can't race their own copy-closes. Per-account:
  the "Flatten" button on the Accounts screen (`POST
  /api/orgs/{org_id}/control/close-all` with `{"account_id": N}`) does the same for one account without touching
  the org's pause.
- **Manual orders** — the Trade screen places market/limit/stop orders (with
  optional SL/TP, volume in lots) on ANY connected account. Orders are
  labeled `manual`: a manual order on the **master** fills and replicates
  through the normal copy pipeline exactly like a platform trade; a manual
  order on a **slave** is deliberately not copied anywhere, its fill is
  logged as `manual_fill` (info, not an unmatched-fill warning), and the
  resulting position is never flagged as orphan drift. The same screen
  closes/partially closes positions and cancels working orders on the
  selected account.
- **Account details & nicknames** — the Accounts screen's Details drawer
  shows everything the broker exposes (balance, currency, leverage, broker
  name, account type, access rights, swap-free, registration date, live
  open positions) plus the OAuth grant behind the account. The account
  holder's name/email are NOT available — cTrader's Open API exposes no
  personal profile data — so each account can carry an operator-set
  nickname instead (stored in `accounts.nickname`).
- **Trade history** — the History screen serves account-wise closed
  positions (realized P&L reconstructed from closing deals), every fill,
  and the order log, straight from the broker
  (`GET /api/orgs/{org_id}/accounts/{id}/history/{deals,orders}?from&to`). cTrader caps
  each request at a one-week window, so the screen pages by week.
- **Disconnect** — the Accounts screen's Disconnect button removes the
  cTrader ID grant behind an account (`DELETE
  /api/orgs/{org_id}/accounts/{id}/connection`), which cascades to every account under
  that same grant and triggers a copier reload so they de-authorize
  immediately. Open positions are not touched.
- **Per-slave pause** — pauses one slave account without affecting the
  master subscription or other slaves (`POST /api/orgs/{org_id}/control/pause` /
  `/resume` with an `account_id`).
- **Degraded slaves** — `degraded` is purely a transport/send problem, never
  a trading one. A slave is marked `degraded` only when a *send* to it
  exhausts its retry ladder (3 attempts, 1s/2s/4s backoff, for the
  pre-wire "never reached the broker" failure case) or hits a non-retryable,
  ambiguous send failure (the request's fate on the broker is unknown, so it
  isn't retried). Treat it as a connectivity issue: check the copier's
  connection health (`docker compose logs copier`) and the specific send-failure
  message shown on the degraded slave's card on the Overview screen (hover for
  the full text). A degraded slave is not disabled —
  it keeps receiving every future master event (each action is
  independent) — and it clears automatically on its next successful send
  (status back to `ok`, the stale error message dropped, and a
  `degraded_cleared` entry written to Logs), or you can pause/resume it
  manually to force one. A slave you deliberately *paused* is never resumed
  this way — only `degraded` clears.
- **Order rejected on a copy** — a separate, non-degrading path: the broker
  itself declines an individual copy order that *was* successfully sent —
  most commonly margin or below-minimum volume. The slave account stays
  `ok`; only that one mapping is marked `failed`, with the broker's reason
  visible as its `error` in the per-slave copy row on the Positions screen,
  plus a matching error-severity event in Logs. There is no account-level or
  Overview indicator for this case, so check Positions/Logs to catch it —
  the fix is usually funding or position-sizing on that slave account, not a
  copier restart.
- **Token refresh & re-grant** — the copier proactively refreshes each
  connected cTrader ID's OAuth token before it expires (access tokens last
  about 30 days, refresh tokens rotate on every refresh). If a token is
  invalidated out of band, cTrader sends a
  `ProtoOAAccountsTokenInvalidatedEvent`; the copier reacts by attempting an
  immediate refresh. If that refresh fails, the connection is marked
  `refresh_failed`: the Overview screen shows a prominent warning banner for
  it, and the underlying failure is also written as an error-severity event
  in Logs. Clear it by reconnecting that cTrader ID from **Accounts →
  Connect cTrader ID** (a normal re-grant, same OAuth flow as the first
  connection).
- **New accounts under an already-granted cTID** — a grant only covers the
  accounts that existed under that cTrader ID at grant time. If you open a
  new account under a cTID you've already connected, it won't show up until
  you re-grant (Connect cTrader ID again for that same cTID).
- **Connection sizing (`SHARDS`)** — every account authorized on one
  connection is re-authenticated one at a time after each reconnect, and the
  cTrader SDK drains its outbound queue at 5 messages/second, so a
  reconnect costs roughly `accounts / 5` seconds before the last account on
  that connection is trading again. Keep no more than **~20–25 accounts per
  connection** until you have observed the real per-connection account limit
  on demo (Stage 1): set `SHARDS` in `.env` to
  `ceil(accounts / 20)` — e.g. `SHARDS=3` for ~50 accounts. `SHARDS` is read
  from the environment at boot only; changing it needs a copier restart. (The
  `shards` value shown in the settings API is **not** wired to the copier —
  it is display-only.)
- **New instruments** — the copier fetches each account's symbol map once, at
  startup. Instruments the broker adds later are not copyable until you
  restart the copier (`docker compose restart copier`).
- **Backups** — all durable state lives in the `postgres` Docker volume
  (`pgdata`). Back that volume up (or the underlying Postgres data via
  `pg_dump`) on whatever schedule matches your risk tolerance; there is no
  other persistent state to capture.
- **Logs** — the dashboard's Logs screen is a live, filterable view (account,
  severity, category, date) over the append-only `events` table, streamed
  over WebSocket. For container/process-level logs (startup, connection
  errors, stack traces), use `docker compose logs`, e.g. `docker compose
  logs -f copier` or `docker compose logs -f api`.

### Deploying to a VPS

Everything the stack publishes is bound to **loopback only** — `api` on
`127.0.0.1:8000` and `postgres` on `127.0.0.1:5433` — and the copier's
control port is never published at all. Keep it that way, and note what
"reachable" means here: **registration is open.** Anyone who can reach the
instance can create a user. That is by design — self-signup grants access to
no org, no account and no data, and a new user sees nothing until an existing
member invites them — but it does mean an internet-facing instance will
accumulate unknown accounts, and it makes the session cookie carrying a real
member's identity the thing worth protecting. So the instance should still
sit behind TLS and, ideally, not be publicly reachable at all. When you move
to a VPS:

- **Put a TLS reverse proxy in front of it** (Caddy, nginx + certbot,
  Traefik) terminating HTTPS on 443 and proxying to `127.0.0.1:8000`. The
  simplest alternative, if you are the only operator, is no public exposure
  at all: leave the port on loopback and reach it through an SSH tunnel
  (`ssh -L 8000:127.0.0.1:8000 you@vps`).
- **Keep `COOKIE_SECURE=true`** (the default). It is only ever set to `false`
  for local plain-HTTP testing; over a TLS proxy the real value must stay
  `true`, or the session and CSRF cookies travel without the `Secure`
  attribute.
- **Update the OAuth redirect URI in both places** — `CTRADER_REDIRECT_URI`
  in `.env` *and* the redirect URI registered for your app at
  https://openapi.ctrader.com — to the VPS's public HTTPS URL
  (`https://your-host/api/oauth/callback`). They must match exactly or the
  cTrader consent flow will refuse the callback.
- **Rotate the secrets** you used locally before going live: `SESSION_SECRET`
  and `POSTGRES_PASSWORD` (and `FERNET_KEY` if the local one ever encrypted a
  real grant). Rotating `SESSION_SECRET` invalidates every existing session,
  which is what you want on a move.
- **Clear `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`** once the
  legacy org they were set for has been claimed. The bootstrap is idempotent
  (an existing user is never re-hashed or re-passworded), so keeping them set
  changes nothing except leaving a working login password sitting in `.env`.
- Do **not** widen the published ports to `0.0.0.0` to "make it reachable" —
  proxy to loopback instead.

## 6. Development

### Repo layout

```
copier/           Python/Twisted trading engine
  src/copier/     ctrader client, decision engine, dispatch, reconciliation, control HTTP endpoint
  tests/unit/     pure decision-core and component tests (no I/O)
  tests/integration/  fake cTrader server (TLS) + real Postgres
  .venv/          local virtualenv (not committed)
api/              FastAPI backend
  src/api/        routes (orgs, accounts, events, settings/control), auth, rbac, oauth, websocket feed
  tests/          route + auth + oauth tests against a real Postgres
  .venv/          local virtualenv (not committed)
dashboard/        React + Vite + TypeScript frontend
  src/pages/      Overview, Accounts, Positions, Trade, History, Logs, Login, Register, Welcome, Members, Join
  src/components/ shared UI (kill switch, layout + desk strip, confirm dialog)
db/
  migrations/     ordered .sql files, applied by the compose `migrate` service
  migrate.py      migration runner
e2e/              compose-level end-to-end tests (single-org and two-org)
docker-compose.yml
docker-compose.test.yml        e2e overlay: fake-ctrader, isolated database
docker-compose.tbe2e-ports.yml e2e port remap, for the `-p tbe2e` project
.env.example
```

### Per-service test commands

All commands below were run against this repo and pass. The `copier` and
`api` suites talk to a real Postgres, so start the compose `postgres`
service first (it publishes to `127.0.0.1:5433`, which is what both test
suites default to):

```bash
docker compose up -d postgres
```

**Which stack these two suites are valid against: the plain dev stack only.**
They connect as an admin to the `copytrader` database on `127.0.0.1:5433`
(`TEST_POSTGRES_ADMIN_DSN`) purely to `DROP`/`CREATE` their own scratch
database, `copytrader_test` (`TEST_POSTGRES_DSN`), which they then migrate
and truncate per test. Both variables can be overridden if your Postgres is
elsewhere:

```bash
TEST_POSTGRES_ADMIN_DSN=postgresql://copytrader:copytrader@localhost:5433/copytrader \
TEST_POSTGRES_DSN=postgresql://copytrader:copytrader@localhost:5433/copytrader_test \
  .venv/bin/pytest tests
```

Two consequences worth knowing before you lose half an hour to them:

- **The two suites share `copytrader_test`, so never run them concurrently.**
  Each drops and recreates that database at session start; running both at
  once has one suite delete the other's database mid-run.
- **Run the compose e2e as its own project, or it eats this `pgdata`
  volume.** Compose names the volume after the project, so an e2e run with no
  `-p` shares the dev stack's — and the e2e's mandatory `down -v` then deletes
  the dev database along with it. Worse, the overlay sets
  `POSTGRES_DB=copytrader_e2e` and Postgres only creates the
  `POSTGRES_DB`-named database on a *fresh* volume, so a shared volume that
  survives comes back with `copytrader_e2e` and no `copytrader`, and both
  suites above fail on connect. `docker compose -p tbe2e …` (see the
  compose-level e2e section below) gives that run its own volume, network and
  ports, and keeps `down -v` pointed only at data it created.

**copier** (Python 3.12+; create the venv once with `python3 -m venv .venv
&& .venv/bin/pip install -e ".[dev]"` from inside `copier/`):

```bash
cd copier && .venv/bin/pytest tests --timeout=60
```

368 tests (unit + integration), takes roughly ten minutes — most of that is
the integration suite, which spins up a real, self-signed-TLS, in-process
fake cTrader server (`copier/src/copier/testing/fake_server.py`) speaking
the same protobuf messages as the real API (auth, heartbeats, order
placement, fills, rejections, disconnects) and drives the real SDK client
against it. Nothing here talks to the real cTrader network.

**api** (Python 3.12+; create the venv once the same way, from inside
`api/`):

```bash
cd api && .venv/bin/pytest tests
```

152 tests. Uses the same Postgres instance (a fresh `copytrader_test`
database, dropped and recreated per session) and a mocked HTTP transport for
any outbound calls to cTrader's OAuth token endpoint — no real network calls.

**dashboard** (Node 22+; `npm install` once from inside `dashboard/`):

```bash
cd dashboard && npm test
```

Runs `tsc --noEmit` (typecheck) followed by `vitest run`. 17 test files, 113
tests, all component/page tests with mocked `fetch`/WebSocket — no backend
required.

When you're done, tear Postgres back down: `docker compose down -v`.

### Fake-server / end-to-end testing

`copier/tests/integration/` is the closest thing to an end-to-end test
without touching the real cTrader API: `FakeCTraderServer` (in
`copier/src/copier/testing/fake_server.py`) listens on a random local TLS
port using a self-signed cert generated in-process
(`copier/src/copier/testing/tls.py`), and the real `ctrader-open-api` SDK
client connects to it exactly as it would to `demo.ctraderapi.com`. Tests
exercise account auth, heartbeats, order placement and fills, rejections,
disconnects, and reconnection — see `test_client_integration.py`,
`test_fake_server.py`, and `test_symbols.py`. To add a new integration
scenario, extend `FakeCTraderServer`'s handlers rather than mocking the SDK
directly, so the test still exercises the real wire protocol.

### Compose-level end-to-end test

Two tests drive the **whole stack** — postgres, migrate, copier, api, plus a
`fake-ctrader` service running the same `FakeCTraderServer` over TLS on the
real cTrader port — exactly as an operator would:

- `e2e/test_full_stack.py` seeds one org, one OAuth grant and three accounts,
  tells the copier to pick them up, registers a user and makes them the org's
  Owner, pushes master fills through the fake broker's scenario-control API,
  and asserts the fan-out, the position-increase path, the drift remedies,
  dry-run, and the kill switch end to end.
- `e2e/test_multi_org.py` seeds **two** orgs (each with its own grant, master
  and slaves), fills both masters, and asserts that every copy lands inside
  its own org, that each owner gets a `404` for the other's org, and that one
  org's `close-all` leaves the other org's positions open and its copying on.

```bash
cp <main-checkout>/.env .env          # the tests read FERNET_KEY/POSTGRES_PASSWORD from it
docker compose -p tbe2e \
  -f docker-compose.yml -f docker-compose.test.yml -f docker-compose.tbe2e-ports.yml \
  up -d --build
E2E_API_BASE=http://127.0.0.1:8010 \
E2E_COPIER_CONTROL_BASE=http://127.0.0.1:8091 \
E2E_FAKE_CTRADER_BASE=http://127.0.0.1:9010 \
E2E_POSTGRES_PORT=5436 \
  api/.venv/bin/pytest e2e -v
docker compose -p tbe2e down -v
```

`docker-compose.tbe2e-ports.yml` needs **Docker Compose ≥ 2.24** (it uses the
`!override` YAML tag; older CLIs fail with a YAML tag error — check with
`docker compose version`).

Notes:

- **Run it as its own compose project (`-p tbe2e`).** Without a project name
  the e2e stack shares the dev stack's `pgdata` volume, and the mandatory
  `down -v` at the end then deletes your real OAuth grants, accounts and audit
  log. `docker-compose.tbe2e-ports.yml` moves every published port off the
  dev stack's (postgres 5436, api 8010, copier control 8091, fake-ctrader
  9010) so both stacks can be up at once; the four `E2E_*` variables point the
  tests at them. Drop the third `-f` and the variables to run the old
  single-stack way, with the dev stack down.
- The test overlay isolates the test's data in a **different database name**
  (`copytrader_e2e`), so pointing these tests at an ordinary dev stack fails
  loudly on connect instead of `TRUNCATE`-ing your real accounts. It also
  publishes the copier's control port (the base compose never does) because
  the tests need `POST /reload` directly.
- The overlay is also the only place `CTRADER_TLS_INSECURE=1` is set. The
  copier verifies cTrader's certificate chain and hostname by default
  (`copier/src/copier/ctrader/client.py`), which no self-signed fake can
  satisfy; the copier logs a WARNING whenever that variable is honoured.
- **`down -v`, not `down`** — see the shared-`pgdata` trap above. Skipping
  `-v` leaves the volume initialized for `copytrader_e2e`, and re-running the
  e2e against a stale volume starts from another run's data.

For a true end-to-end check against Spotware's infrastructure, there's no
substitute for the demo-account rollout stages in section 4 above — that's
the only place this system talks to the real cTrader Open API before going
live.
