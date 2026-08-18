"""Compose-level end-to-end test: exercises the FULL stack (postgres,
migrate, copier, api, fake-ctrader) exactly as brought up by:

    docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build

This test never starts or stops any container itself -- it asserts against a
stack that is already running, driving it the way an operator (or the
dashboard) would: seed ONE ORG plus one connected cTrader "account" (the two
pieces of setup no HTTP endpoint does for you -- an OAuth grant, and an org
that a freshly registered user is already a member of -- so they are direct
SQL inserts, the tokens Fernet-encrypted the same way
copier.ctrader.tokens.TokenStore encrypts a real grant), tell the copier to
pick it up, register a user and drive that org's API, push a master fill
through the fake broker's scenario-control API, and assert the copy fans out
to both slaves, the kill switch stops it end-to-end, and the dashboard's own
state view reflects the result.

Every tenant-owned row carries an `org_id` since db/migrations/005, and every
tenant-facing route lives under `/api/orgs/{org_id}/...`; the org seeded here
is the one this whole test operates inside. `e2e/test_multi_org.py` is the
companion that proves two orgs on one stack stay apart.

Run from the api venv (httpx + psycopg + cryptography are already project
dependencies there -- see api/pyproject.toml):

    cd api && .venv/bin/pytest ../e2e/test_full_stack.py -v

Reads FERNET_KEY / POSTGRES_PASSWORD directly out of the repo-root .env
(git-ignored, so this test has no other way to know them) -- the exact values
docker-compose.yml's `env_file: .env` handed to the copier/api containers
this test talks to. The dashboard password is NOT among them: since 005 there
is no bootstrap password to log in with, so the test registers its own user
through POST /api/register.

TLS note (T1): the copier now VERIFIES cTrader's certificate chain against
the platform trust store and checks the hostname on every connection
(copier/src/copier/ctrader/client.py:client_tls_options) -- which no
self-signed certificate minted in-process by fake-ctrader can satisfy. So
docker-compose.test.yml sets `CTRADER_TLS_INSECURE=1` on the copier service,
and ONLY there: it appears nowhere in docker-compose.yml, and the copier
logs a WARNING every time it honours it. Nothing in this test touches the
TLS path; the knob in the overlay is the whole of it. (The default path's
refusal of exactly this certificate is proved by
copier/tests/integration/test_client_integration.py:
test_default_tls_path_rejects_the_self_signed_fake_server.)
"""

import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import psycopg
import psycopg.rows
import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------- addresses this test drives (see docker-compose.test.yml) ----------
#
# The defaults are the ports the documented single-stack run publishes. Every
# one is overridable because the stack can equally be brought up as its OWN
# compose project alongside a dev stack -- `docker compose -p tbe2e -f
# docker-compose.yml -f docker-compose.test.yml -f docker-compose.tbe2e-ports.yml`
# -- which remaps all four to avoid colliding with (and, via a shared volume,
# destroying) the dev stack. See docker-compose.tbe2e-ports.yml.

API_BASE = os.environ.get("E2E_API_BASE", "http://127.0.0.1:8000")
# test-only publish (base compose never exposes the copier's control port 8080)
COPIER_CONTROL_BASE = os.environ.get("E2E_COPIER_CONTROL_BASE", "http://127.0.0.1:8081")
FAKE_CTRADER_SCENARIO_BASE = os.environ.get("E2E_FAKE_CTRADER_BASE", "http://127.0.0.1:9000")
POSTGRES_HOST_PORT = os.environ.get("E2E_POSTGRES_PORT", "5433")

# Must match docker-compose.test.yml's POSTGRES_DB=copytrader_e2e override
# (applied there to postgres/migrate/copier/api alike). Deliberately NOT
# "copytrader" -- see _connect_to_test_database's docstring (task-30 review
# finding I4): the base docker-compose.yml also publishes 127.0.0.1:5433,
# with database name "copytrader", so a distinct name here is what makes a
# connection from this test to a developer's ordinary (non-test) stack fail
# loudly instead of silently truncating their real data.
POSTGRES_DB_NAME = "copytrader_e2e"

MASTER_ID = 100
SLAVE1_ID = 101   # multiplier 1.0
SLAVE2_ID = 102   # multiplier 0.5

# The operator this test registers and then makes Owner of the seeded org.
E2E_EMAIL = "e2e@example.com"
E2E_PASSWORD = "a-solid-password"   # >= MIN_PASSWORD_LEN (api/src/api/auth.py)

SLAVE1_EXPECTED_VOLUME = 10_000_000   # 1.00 lot * lotSize 10_000_000 * multiplier 1.0
SLAVE2_EXPECTED_VOLUME = 5_000_000    # 1.00 lot * lotSize 10_000_000 * multiplier 0.5

# After the master adds +0.50 lot to the SAME position (step 6b, N2): the
# mapping row aggregates, so 1.50 lots at each slave's multiplier.
SLAVE1_VOLUME_AFTER_INCREASE = 15_000_000
SLAVE2_VOLUME_AFTER_INCREASE = 7_500_000

DEADLINE_S = 60.0
POLL_INTERVAL_S = 0.5
NEGATIVE_WAIT_S = 5.0

# See _register_owner: POST /api/register is rate limited per IP, 5 attempts
# per 60s window, so waiting out one window is always enough.
REGISTER_RATE_LIMIT_RETRIES = 4
REGISTER_RATE_LIMIT_BACKOFF_S = 20.0


# ---------- repo-root .env (git-ignored; see module docstring) ----------

def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for the repo's plain (unquoted) .env file."""
    if not path.is_file():
        pytest.fail(
            f"{path} not found -- copy it from the main checkout before running this test "
            f"(`cp <main-checkout-root>/.env {path}`); see task-30 brief."
        )
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _dotenv_get(env: dict[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        pytest.fail(f"{key} missing/empty in .env")
    return value


# ---------- DB isolation guard (task-30 review finding I4) ----------

def _connect_to_test_database(dsn: str) -> psycopg.Connection:
    """Connect to the isolated test-only database, or fail LOUDLY and
    explain why rather than letting a bare connection error look like an
    unrelated flake.

    This test's very first act on the database is a destructive
    `TRUNCATE ... CASCADE` (step 1 below). The default port 127.0.0.1:5433
    is published by BOTH the base `docker-compose.yml` (a developer's
    ordinary dev stack, database `copytrader`) and this project's test stack
    (`docker-compose.yml` + `docker-compose.test.yml`, database
    `copytrader_e2e` -- see POSTGRES_DB_NAME above) -- so probing "is
    something listening on 5433" or even "is fake-ctrader up on 9000" is
    NOT sufficient: a developer could have their dev stack running and
    ALSO have layered docker-compose.test.yml on top of it from a stray
    terminal, in which case fake-ctrader would legitimately answer while
    postgres still only has the dev stack's `copytrader` database.
    Naming the target database `copytrader_e2e` closes this for real:
    Postgres only creates the `POSTGRES_DB`-named database on a FRESH data
    volume, so a plain dev stack's `copytrader` database was never renamed
    and `copytrader_e2e` simply does not exist there -- this connection
    fails immediately, before any TRUNCATE is ever sent.
    """
    try:
        return psycopg.connect(dsn, autocommit=True)
    except psycopg.OperationalError as e:
        pytest.fail(
            f"could not connect to database '{POSTGRES_DB_NAME}' on "
            f"127.0.0.1:{POSTGRES_HOST_PORT} ({e}).\n"
            "This test ONLY EVER targets the isolated test-stack database "
            "docker-compose.test.yml creates, by design (see task-30 review finding I4) -- "
            "it never touches a plain dev stack's 'copytrader' database, even though both "
            "publish the same port. If you see this, either:\n"
            "  1. the test stack isn't up -- run: docker compose -f docker-compose.yml "
            "-f docker-compose.test.yml up -d --build\n"
            "  2. a plain dev stack (docker-compose.yml alone, database 'copytrader') is on "
            "127.0.0.1:5433 instead -- stop it first, then bring up the test stack; this guard "
            "is working as intended by refusing to touch it."
        )


# ---------- polling ----------

def _poll_until(predicate: Callable[[], Any], timeout_s: float = DEADLINE_S,
                 interval_s: float = POLL_INTERVAL_S, description: str = "condition") -> Any:
    """Poll `predicate` until it returns a truthy value (returned to the
    caller) or `timeout_s` elapses. `predicate` may freely `assert` -- an
    AssertionError is treated the same as a falsy return (retried), and only
    surfaces (chained, for a useful diff) if the deadline is reached without
    ever succeeding."""
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except AssertionError as e:
            last_error = e
        time.sleep(interval_s)
    if last_error is not None:
        raise AssertionError(f"timed out after {timeout_s}s waiting for {description}") from last_error
    raise AssertionError(f"timed out after {timeout_s}s waiting for {description}")


# ---------- seeding helpers (shared with e2e/test_multi_org.py) ----------
#
# Everything here is a direct SQL insert on purpose: an OAuth grant and an
# org membership are the two things no HTTP endpoint in this system will
# manufacture for a test (the first needs a real cTrader consent flow, the
# second needs an invite issued by an existing member of the org). Every
# other piece of setup below goes through the API, exactly as an operator
# would drive it.

def _truncate_tenant_tables(conn: psycopg.Connection) -> None:
    """Clean slate for a re-runnable test: every tenant-owned table plus the
    users/orgs/memberships that own them.

    CASCADE reaches org_memberships, org_invites and symbol_cache (all FK'd
    to a table named here), so each run also starts with a cold symbol cache
    and exercises the real fetch-on-reload path.
    """
    conn.execute(
        "TRUNCATE events, mappings, accounts, ctid_connections, oauth_states,"
        " org_memberships, org_invites, orgs, users RESTART IDENTITY CASCADE"
    )


def _reset_fake_broker() -> None:
    """Flatten the fake broker's book before seeding.

    The fake-ctrader container outlives any single test (and any single
    run), and it MERGES a same-side market fill into an account's existing
    position, exactly as real cTrader does -- so a leftover position from an
    earlier test would silently inflate this one's volumes. TRUNCATE only
    clears Postgres; this state lives in the fake's own process.
    """
    resp = httpx.post(f"{FAKE_CTRADER_SCENARIO_BASE}/reset", json={}, timeout=10.0)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("status") == "reset", resp.text


def _seed_org(conn: psycopg.Connection, name: str) -> int:
    """One org, copying on and dry-run off -- the state every scenario here
    starts from (since 005 the kill switch and dry-run live per org, not in
    the single global `settings` row)."""
    (org_id,) = conn.execute(
        "INSERT INTO orgs (name, copying_enabled, dry_run) VALUES (%s, true, false)"
        " RETURNING id",
        (name,),
    ).fetchone()
    return org_id


def _seed_connection(conn: psycopg.Connection, fernet: Fernet, org_id: int, label: str) -> int:
    """One cTrader-ID grant owned by `org_id`, tokens encrypted exactly as
    copier.ctrader.tokens.TokenStore encrypts a real one.

    FakeCTraderServer.accounts is left empty by fake_main.py, which accepts
    ANY (account_id, token) pair (see fake_main.py's docstring) -- the token
    value itself only needs to round-trip through Fernet correctly, exactly
    as TokenStore.get() will decrypt it when the copier authorizes these
    accounts.
    """
    (connection_id,) = conn.execute(
        "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
        " granted_at, expires_at)"
        " VALUES (%s, %s, %s, now(), now() + interval '60 days') RETURNING id",
        (org_id,
         fernet.encrypt(f"{label}-access-token".encode()).decode(),
         fernet.encrypt(f"{label}-refresh-token".encode()).decode()),
    ).fetchone()
    return connection_id


def _seed_account(conn: psycopg.Connection, org_id: int, connection_id: int,
                   account_id: int, role: str, multiplier: float) -> None:
    """One demo broker account in `org_id`. trader_login is derived from the
    account id so the seeded logins stay unique and obviously synthetic."""
    conn.execute(
        """
        INSERT INTO accounts
            (ctid_trader_account_id, org_id, ctid_connection_id, trader_login,
             is_live, role, enabled, multiplier)
        VALUES (%s, %s, %s, %s, false, %s, true, %s)
        """,
        (account_id, org_id, connection_id, 90_000 + account_id, role, multiplier),
    )


def _register_owner(postgres_dsn: str, org_id: int, email: str, password: str,
                     display_name: str) -> httpx.Client:
    """Register a user through the real API, make them Owner of `org_id` via
    SQL, and return an httpx client carrying their session + CSRF header.

    Registration deliberately grants NO org access (spec §3) -- a self-signup
    on a reachable instance must not be able to see anyone else's book -- so
    the membership for the org this test seeded straight into Postgres has to
    be inserted the same way. Everything after this point goes through the
    API as that user.
    """
    client = httpx.Client(base_url=API_BASE, timeout=15.0)
    body = {"email": email, "password": password, "display_name": display_name}
    # POST /api/register is rate-limited per client IP (5 per 60s,
    # api/src/api/auth.py). Two e2e tests registering three users between
    # them fits comfortably -- but two back-to-back RUNS against the same
    # long-lived api container may not, and that has nothing to do with what
    # is under test here. Wait the window out rather than fail on it.
    for attempt in range(REGISTER_RATE_LIMIT_RETRIES):
        register_resp = client.post("/api/register", json=body)
        if register_resp.status_code != 429:
            break
        time.sleep(REGISTER_RATE_LIMIT_BACKOFF_S)
    assert register_resp.status_code == 204, register_resp.text

    csrf_token = client.cookies.get("csrf")
    assert csrf_token, "register did not set a csrf cookie"
    # harmless on GET; required by CSRFMiddleware on POST/PUT/DELETE/PATCH
    client.headers["X-CSRF-Token"] = csrf_token

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role)"
            " SELECT %s, id, 'owner' FROM users WHERE lower(email) = lower(%s)",
            (org_id, email),
        )

    me = client.get("/api/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == email, me.text
    assert [(o["id"], o["role"]) for o in me.json()["orgs"]] == [(org_id, "owner")], me.text
    return client


# ---------- the flow (task-30 brief's 8 steps) ----------

@pytest.mark.timeout(180)  # this test's own sequence of bounded waits can outrun api/pyproject.toml's default 60s
def test_full_stack_flow():
    env = _load_dotenv(REPO_ROOT / ".env")
    fernet_key = _dotenv_get(env, "FERNET_KEY")
    postgres_password = _dotenv_get(env, "POSTGRES_PASSWORD")
    postgres_dsn = (f"postgresql://copytrader:{postgres_password}"
                    f"@127.0.0.1:{POSTGRES_HOST_PORT}/{POSTGRES_DB_NAME}")

    fernet = Fernet(fernet_key.encode())

    # ---------- 1: seed org + ctid_connection (Fernet tokens) + accounts 100/101/102, all demo ----------
    with _connect_to_test_database(postgres_dsn) as conn:
        # Clean slate every run: this test owns the whole stack's DB state
        # for its duration (a fresh `docker compose ... up -d --build`
        # starts from an empty database via the one-shot `migrate`
        # service), and truncating up front makes the test safely
        # re-runnable against a stack that's still up -- together with
        # _reset_fake_broker(), which clears the other half of the state
        # (the broker's own book).
        _truncate_tenant_tables(conn)
        _reset_fake_broker()

        org_id = _seed_org(conn, "E2E")
        connection_id = _seed_connection(conn, fernet, org_id, "e2e-fake")
        _seed_account(conn, org_id, connection_id, MASTER_ID, "master", 1.0)
        _seed_account(conn, org_id, connection_id, SLAVE1_ID, "slave", 1.0)
        _seed_account(conn, org_id, connection_id, SLAVE2_ID, "slave", 0.5)

    org = f"/api/orgs/{org_id}"

    # ---------- 2: POST /reload -- copier picks up accounts, auths vs fake-ctrader ----------
    # ReloadResource's HTTP response is only written after CopierApp.reload()
    # yields every account's authorize_account() round trip in sequence (see
    # main.py), so by the time this call returns, all three accounts are
    # already account-authed on the one shared demo connection -- the "give
    # the copier a moment" the task brief mentions is covered by that, plus
    # the 60s-deadline polling in step 5 below as a safety net.
    reload_resp = httpx.post(f"{COPIER_CONTROL_BASE}/reload", json={}, timeout=30.0)
    assert reload_resp.status_code == 200, reload_resp.text
    assert reload_resp.json().get("status") == "reloaded", reload_resp.text

    # ---------- 3: register -- session + CSRF (double-submit: cookie + X-CSRF-Token header) ----------
    # There is no bootstrap password to log in with any more: the first
    # operator on a fresh instance registers, and access to a particular org
    # comes from a membership row (here, seeded -- see _register_owner).
    client = _register_owner(postgres_dsn, org_id, E2E_EMAIL, E2E_PASSWORD, "E2E Operator")

    # ---------- 4: POST /fill -- 1.00 lot EURUSD BUY on the master ----------
    fill_resp = httpx.post(
        f"{FAKE_CTRADER_SCENARIO_BASE}/fill",
        json={"account_id": MASTER_ID, "symbol": "EURUSD", "side": "BUY", "volume_lots": "1.00"},
        timeout=10.0,
    )
    assert fill_resp.status_code == 200, fill_resp.text
    master_position_id = fill_resp.json()["position_id"]

    # ---------- 5: poll GET /api/orgs/{id}/events until 2 slave_action fills appear ----------
    def _slave_fill_events() -> list[dict]:
        resp = client.get(f"{org}/events", params={"category": "slave_action", "limit": 200})
        assert resp.status_code == 200, resp.text
        return [e for e in resp.json() if e["payload"].get("action") == "position_filled"]

    filled_events = _poll_until(
        lambda: (lambda evs: evs if len(evs) >= 2 else None)(_slave_fill_events()),
        description="2 slave_action position_filled events",
    )
    assert {e["account_id"] for e in filled_events} == {SLAVE1_ID, SLAVE2_ID}, filled_events

    # ---------- 6: mappings table -- two active rows, volumes 10_000_000 / 5_000_000 ----------
    def _mapping_rows() -> list[dict]:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            conn.row_factory = psycopg.rows.dict_row
            return conn.execute(
                "SELECT * FROM mappings WHERE master_position_id = %s ORDER BY slave_account_id",
                (master_position_id,),
            ).fetchall()

    def _two_active_mappings():
        rows = _mapping_rows()
        return rows if len(rows) == 2 and all(r["status"] == "active" for r in rows) else None

    mapping_rows = _poll_until(_two_active_mappings, description="2 active mapping rows")
    # Every copy is booked into the org that owns the master (mappings.org_id
    # is NOT NULL since 005, and the whole per-org read model reads through it).
    assert {r["org_id"] for r in mapping_rows} == {org_id}, mapping_rows
    by_slave = {r["slave_account_id"]: r for r in mapping_rows}
    assert by_slave[SLAVE1_ID]["slave_volume"] == SLAVE1_EXPECTED_VOLUME, mapping_rows
    assert by_slave[SLAVE2_ID]["slave_volume"] == SLAVE2_EXPECTED_VOLUME, mapping_rows

    # ---------- 6b: master position INCREASE (+0.50 lot) fans out (N2) ----------
    # cTrader merges a same-direction add on the same symbol into the SAME
    # position, so this second /fill on account 100/EURUSD/BUY reports the
    # ORIGINAL position_id with a delta volume (fake_main mirrors that; see
    # FakeCTraderServer.register_market_fill). Before N2's fix the copier's
    # second OpenMarket reused the deterministic client_order_id
    # `cm{pid}.{slave}`, hit the mappings UNIQUE constraint, and was caught
    # as `intent_processing_failed` -- so the increase reached NO slave and
    # BOTH were marked degraded, for a routine scale-in.
    increase_resp = httpx.post(
        f"{FAKE_CTRADER_SCENARIO_BASE}/fill",
        json={"account_id": MASTER_ID, "symbol": "EURUSD", "side": "BUY", "volume_lots": "0.50"},
        timeout=10.0,
    )
    assert increase_resp.status_code == 200, increase_resp.text
    assert increase_resp.json()["position_id"] == master_position_id, (
        "the fake must report an add to the same position, not a new one"
    )

    def _mappings_reflect_the_increase():
        rows = _mapping_rows()
        if len(rows) != 2:
            return None
        by_slave = {r["slave_account_id"]: r for r in rows}
        if (by_slave[SLAVE1_ID]["slave_volume"] == SLAVE1_VOLUME_AFTER_INCREASE
                and by_slave[SLAVE2_ID]["slave_volume"] == SLAVE2_VOLUME_AFTER_INCREASE):
            return rows
        return None

    increased_rows = _poll_until(
        _mappings_reflect_the_increase,
        description="both mappings aggregated to include the +0.50 lot increase",
    )
    by_slave = {r["slave_account_id"]: r for r in increased_rows}
    assert all(r["status"] == "active" for r in increased_rows), increased_rows
    # One row per (master position, slave) -- an increase aggregates, it does
    # not add rows -- and each still points at that slave's single position.
    assert len(increased_rows) == 2
    # T9c: the fill price is stamped onto the mapping, which is what the
    # Positions screen's Fill Price / Slippage columns render.
    assert all(r["fill_price"] is not None for r in increased_rows), increased_rows

    # Four slave fills total now (two opens + two increases), and neither
    # slave was degraded by the increase.
    increase_fills = _poll_until(
        lambda: (lambda evs: evs if len(evs) >= 4 else None)(_slave_fill_events()),
        description="4 slave_action position_filled events (2 opens + 2 increases)",
    )
    assert len(increase_fills) == 4, increase_fills
    accounts_resp = client.get(f"{org}/accounts")
    assert accounts_resp.status_code == 200, accounts_resp.text
    statuses = {a["ctid_trader_account_id"]: a["status"] for a in accounts_resp.json()}
    assert statuses[SLAVE1_ID] == "ok", statuses
    assert statuses[SLAVE2_ID] == "ok", statuses

    # ---------- 7: kill switch -- disable copying, fill again, assert NO new fills within 5s ----------
    settings_resp = client.put(f"{org}/settings", json={"copying_enabled": False})
    assert settings_resp.status_code == 200, settings_resp.text
    assert settings_resp.json()["copying_enabled"] is False, settings_resp.text

    fills_before_kill_switch = len(_slave_fill_events())

    fill_resp_2 = httpx.post(
        f"{FAKE_CTRADER_SCENARIO_BASE}/fill",
        json={"account_id": MASTER_ID, "symbol": "EURUSD", "side": "SELL", "volume_lots": "1.00"},
        timeout=10.0,
    )
    assert fill_resp_2.status_code == 200, fill_resp_2.text

    # POSITIVE check first (task-30 review finding I3): the negative check
    # below ("no new fills for 5s") passes VACUOUSLY if the master event
    # never reached the copier at all, if the fake failed to broadcast, or
    # if the copier died after step 6 -- none of which have anything to do
    # with the kill switch actually working. Dispatcher logs a
    # `slave_action` event with `{"skipped": "kill_switch", ...}` per
    # suppressed intent (engine/dispatch.py:_handle_kill_switch) -- polling
    # for exactly those two events first proves the master fill genuinely
    # arrived, was normalized and decided into two OpenMarket intents, and
    # was suppressed FOR THE RIGHT REASON, before asserting the (necessary
    # but not sufficient on its own) absence of new fills.
    def _kill_switch_skip_events() -> list[dict]:
        resp = client.get(f"{org}/events", params={"category": "slave_action", "limit": 200})
        assert resp.status_code == 200, resp.text
        return [e for e in resp.json() if e["payload"].get("skipped") == "kill_switch"]

    skip_events = _poll_until(
        lambda: (lambda evs: evs if len(evs) >= 2 else None)(_kill_switch_skip_events()),
        description="2 slave_action kill_switch skip events",
    )
    assert {e["account_id"] for e in skip_events} == {SLAVE1_ID, SLAVE2_ID}, skip_events

    negative_deadline = time.monotonic() + NEGATIVE_WAIT_S
    while time.monotonic() < negative_deadline:
        time.sleep(POLL_INTERVAL_S)
        current = len(_slave_fill_events())
        assert current == fills_before_kill_switch, (
            f"kill switch did not suppress copying end-to-end: "
            f"{current - fills_before_kill_switch} new slave_action position_filled "
            f"event(s) appeared within {NEGATIVE_WAIT_S}s of copying_enabled=false"
        )

    # ---------- 8: GET /api/orgs/{id}/state -- master position visible with two copies ----------
    # get_state()'s master_positions come exclusively from
    # Reconciler.master_positions, populated only by an explicit resync()
    # (CopierApp.resync -> reconciler.run(), sending a real
    # ProtoOAReconcileReq) -- never by /reload and never by the live
    # execution-event stream that drove steps 4-6. Since this copier
    # process booted with zero accounts (startup() found none and returned
    # before ever reconciling) and /reload doesn't reconcile either, nothing
    # has populated master_positions yet at this point in the test; a
    # resync is genuinely required here; this is not a workaround. Proxied
    # through the api (like the dashboard would) rather than hitting
    # :8081 directly, matching production's control-port isolation.
    resync_resp = client.post(f"{org}/control/resync", json={})
    assert resync_resp.status_code == 200, resync_resp.text

    def _state_with_master_position():
        resp = client.get(f"{org}/state")
        assert resp.status_code == 200, resp.text
        state = resp.json()
        matches = [p for p in state.get("master_positions", []) if p["position_id"] == master_position_id]
        return matches[0] if matches else None

    master_position = _poll_until(_state_with_master_position, description="master position visible in /api/state")
    copies = master_position["copies"]
    assert len(copies) == 2, master_position
    assert {c["slave_account_id"] for c in copies} == {SLAVE1_ID, SLAVE2_ID}, master_position

    # T9c: /state carries the fields the Positions screen renders, not just ids.
    assert master_position["symbol"] == "EURUSD", master_position
    assert master_position["volume_lots"] == "1.50", master_position
    for copy in copies:
        assert copy["fill_price"] is not None, copy
        assert copy["volume_lots"] is not None, copy
    by_slave_copy = {c["slave_account_id"]: c for c in copies}
    assert by_slave_copy[SLAVE1_ID]["volume_lots"] == "1.50", copies
    assert by_slave_copy[SLAVE2_ID]["volume_lots"] == "0.75", copies

    # ---------- 9: drift remedy -- the proxy must forward the request body (N3) ----------
    # The kill-switched SELL in step 7 opened a master position with no
    # copies at all, which the resync above reports as
    # `unmapped_master_position` drift. Dismissing it exercises the remedy
    # path end to end: before N3's fix the api proxied a hardcoded `json={}`
    # to the copier, whose drift resources raise ValueError("id required")
    # -> 500 -> 502 here, so every dashboard remedy click failed.
    state = client.get(f"{org}/state").json()
    drift_items = state.get("drift", [])
    assert drift_items, "expected the un-copied (kill-switched) master position to surface as drift"
    dismissable = next(i for i in drift_items if i["kind"] == "unmapped_master_position")

    dismiss_resp = client.post(f"{org}/drift/dismiss", json={"id": dismissable["id"]})
    assert dismiss_resp.status_code == 200, dismiss_resp.text
    assert dismiss_resp.json().get("status") == "dismissed", dismiss_resp.text
    assert dismiss_resp.json().get("id") == dismissable["id"], dismiss_resp.text

    # The copier really acted on the id it was handed, not on an empty body.
    def _dismissal_event():
        resp = client.get(f"{org}/events", params={"category": "drift", "limit": 200})
        assert resp.status_code == 200, resp.text
        matches = [
            e for e in resp.json()
            if e["payload"].get("action") == "dismissed"
            and e["payload"].get("position_id") == dismissable["position_id"]
        ]
        return matches[0] if matches else None

    dismissal = _poll_until(_dismissal_event, description="a drift dismissal event for that item")
    assert dismissal["payload"]["drift_kind"] == "unmapped_master_position", dismissal

    # ---------- 10: dry-run really turns ON through the API (N1) ----------
    # Re-enable copying first (step 7 left the kill switch on, and the kill
    # switch is checked before dry-run in Dispatcher.dispatch), then turn
    # dry-run on and prove a master fill produces "would_send" log entries
    # and NO actual copy.
    resume_resp = client.put(f"{org}/settings", json={"copying_enabled": True})
    assert resume_resp.status_code == 200, resume_resp.text
    assert resume_resp.json()["copying_enabled"] is True, resume_resp.text

    dry_run_resp = client.put(f"{org}/settings", json={"dry_run": True})
    assert dry_run_resp.status_code == 200, dry_run_resp.text
    assert dry_run_resp.json()["dry_run"] is True, dry_run_resp.text
    assert dry_run_resp.json().get("dry_run_applied") is True, dry_run_resp.text

    # The real check: the copier's OWN view of the setting, read back through
    # the api AFTER the proxy call. Before N1's fix the api forwarded an empty
    # body, the copier read the absent "enabled" as False and rewrote the row
    # to false -- while still answering `dry_run: true, dry_run_applied: true`
    # from a row it had read BEFORE the proxy call.
    settings_readback = _poll_until(
        lambda: (lambda s: s if s.get("dry_run") is True else None)(
            client.get(f"{org}/settings").json()
        ),
        description="dry_run to still be true after the copier applied it",
    )
    assert settings_readback["dry_run"] is True, settings_readback

    def _would_send_events() -> list[dict]:
        resp = client.get(f"{org}/events", params={"category": "slave_action", "limit": 300})
        assert resp.status_code == 200, resp.text
        return [e for e in resp.json() if e["payload"].get("dry_run") is True]

    fills_before_dry_run = len(_slave_fill_events())

    dry_fill_resp = httpx.post(
        f"{FAKE_CTRADER_SCENARIO_BASE}/fill",
        json={"account_id": MASTER_ID, "symbol": "EURUSD", "side": "BUY", "volume_lots": "0.10"},
        timeout=10.0,
    )
    assert dry_fill_resp.status_code == 200, dry_fill_resp.text

    would_send = _poll_until(
        lambda: (lambda evs: evs if len(evs) >= 2 else None)(_would_send_events()),
        description="2 slave_action dry_run would_send events",
    )
    assert {e["account_id"] for e in would_send} == {SLAVE1_ID, SLAVE2_ID}, would_send
    assert all(e["payload"].get("would_send") for e in would_send), would_send

    # ...and nothing reached the wire: no new fill was reported for either slave.
    negative_deadline = time.monotonic() + NEGATIVE_WAIT_S
    while time.monotonic() < negative_deadline:
        time.sleep(POLL_INTERVAL_S)
        current = len(_slave_fill_events())
        assert current == fills_before_dry_run, (
            f"dry-run did not suppress sending: {current - fills_before_dry_run} new "
            f"slave_action position_filled event(s) appeared with dry_run=true"
        )
