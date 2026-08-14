"""Compose-level end-to-end test: exercises the FULL stack (postgres,
migrate, copier, api, fake-ctrader) exactly as brought up by:

    docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build

This test never starts or stops any container itself -- it asserts against a
stack that is already running, driving it the way an operator (or the
dashboard) would: seed one connected cTrader "account" (the one piece of
setup no HTTP endpoint does for you -- an OAuth grant -- so it's a direct SQL
insert, Fernet-encrypted the same way copier.ctrader.tokens.TokenStore
encrypts a real grant), tell the copier to pick it up, log in to the
dashboard API, drive a master fill through the fake broker's scenario-control
API, and assert the copy fans out to both slaves, the kill switch stops it
end-to-end, and the dashboard's own state view reflects the result.

Run from the api venv (httpx + psycopg + cryptography are already project
dependencies there -- see api/pyproject.toml):

    cd api && .venv/bin/pytest ../e2e/test_full_stack.py -v

Reads FERNET_KEY / ADMIN_BOOTSTRAP_PASSWORD / POSTGRES_PASSWORD directly out
of the repo-root .env (git-ignored, so this test has no other way to know
them) -- the exact values docker-compose.yml's `env_file: .env` handed to
the copier/api containers this test talks to.

TLS note (why this test needs no CA/cert wiring of its own): the copier
connects to fake-ctrader the same way it connects to demo.ctraderapi.com --
via ctrader_open_api.Client, which builds a bare Twisted `ssl:host:port`
endpoint string (see copier/src/copier/ctrader/client.py:make_sdk_client).
Twisted's parser for that form (twisted.internet.endpoints._parseClientSSL /
_parseClientSSLOptions) only enables certificate verification when a
`hostname` or `caCertsDir` parameter is present in the string -- neither
ever is here -- so `CertificateOptions(trustRoot=None, ...)` is built with
`verify=False`: NO certificate validation happens for ANY cTrader
connection, real or fake, today. That is exactly why the existing in-process
integration suite (copier/tests/integration/test_copier_e2e.py) already
connects straight to FakeCTraderServer's self-signed cert with zero special
trust setup, and it is why this compose-level test needs none either --
docker-compose.test.yml only points CTRADER_DEMO_HOST at fake-ctrader; no
CA file, no verify knob, nothing added to the TLS path.
"""

import time
from pathlib import Path
from typing import Any, Callable

import httpx
import psycopg
import psycopg.rows
import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------- fixed addresses this test drives (see docker-compose.test.yml) ----------

API_BASE = "http://127.0.0.1:8000"
COPIER_CONTROL_BASE = "http://127.0.0.1:8081"       # test-only publish (base compose never exposes 8080)
FAKE_CTRADER_SCENARIO_BASE = "http://127.0.0.1:9000"

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

SLAVE1_EXPECTED_VOLUME = 10_000_000   # 1.00 lot * lotSize 10_000_000 * multiplier 1.0
SLAVE2_EXPECTED_VOLUME = 5_000_000    # 1.00 lot * lotSize 10_000_000 * multiplier 0.5

DEADLINE_S = 60.0
POLL_INTERVAL_S = 0.5
NEGATIVE_WAIT_S = 5.0


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
    `TRUNCATE ... CASCADE` (step 1 below). Port 127.0.0.1:5433 is published
    by BOTH the base `docker-compose.yml` (a developer's ordinary dev
    stack, database `copytrader`) and this project's test stack
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
            f"could not connect to database '{POSTGRES_DB_NAME}' on 127.0.0.1:5433 ({e}).\n"
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


# ---------- the flow (task-30 brief's 8 steps) ----------

@pytest.mark.timeout(180)  # this test's own sequence of bounded waits can outrun api/pyproject.toml's default 60s
def test_full_stack_flow():
    env = _load_dotenv(REPO_ROOT / ".env")
    fernet_key = _dotenv_get(env, "FERNET_KEY")
    admin_password = _dotenv_get(env, "ADMIN_BOOTSTRAP_PASSWORD")
    postgres_password = _dotenv_get(env, "POSTGRES_PASSWORD")
    postgres_dsn = f"postgresql://copytrader:{postgres_password}@127.0.0.1:5433/{POSTGRES_DB_NAME}"

    fernet = Fernet(fernet_key.encode())

    # ---------- 1: seed ctid_connection (Fernet tokens) + accounts 100/101/102, all demo ----------
    with _connect_to_test_database(postgres_dsn) as conn:
        # Clean slate every run: this test owns the whole stack's DB state
        # for its duration (a fresh `docker compose ... up -d --build`
        # starts from an empty database via the one-shot `migrate`
        # service), and truncating up front makes the test safely
        # re-runnable against a stack that's still up. CASCADE also clears
        # symbol_cache (FK'd to accounts) so this run always starts with a
        # cold cache, exercising the real fetch-on-reload path.
        conn.execute(
            "TRUNCATE events, mappings, accounts, ctid_connections, oauth_states RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET copying_enabled = true, dry_run = false WHERE id = true")

        access_token_enc = fernet.encrypt(b"e2e-fake-access-token").decode()
        refresh_token_enc = fernet.encrypt(b"e2e-fake-refresh-token").decode()
        (connection_id,) = conn.execute(
            "INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)"
            " VALUES (%s, %s, now(), now() + interval '60 days') RETURNING id",
            (access_token_enc, refresh_token_enc),
        ).fetchone()

        # FakeCTraderServer.accounts is left empty by fake_main.py, which
        # accepts ANY (account_id, token) pair (see fake_main.py's
        # docstring) -- the token value itself only needs to round-trip
        # through Fernet correctly, exactly as TokenStore.get() will decrypt
        # it when the copier authorizes these accounts below.
        conn.execute(
            """
            INSERT INTO accounts
                (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (%(master)s, %(conn)s, 90100, false, 'master', true, 1.0),
                (%(slave1)s, %(conn)s, 90101, false, 'slave',  true, 1.0),
                (%(slave2)s, %(conn)s, 90102, false, 'slave',  true, 0.5)
            """,
            {"master": MASTER_ID, "slave1": SLAVE1_ID, "slave2": SLAVE2_ID, "conn": connection_id},
        )

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

    # ---------- 3: login -- session + CSRF (double-submit: cookie + X-CSRF-Token header) ----------
    client = httpx.Client(base_url=API_BASE, timeout=15.0)
    login_resp = client.post("/api/login", json={"password": admin_password})
    assert login_resp.status_code == 204, login_resp.text
    csrf_token = client.cookies.get("csrf")
    assert csrf_token, "login did not set a csrf cookie"
    client.headers["X-CSRF-Token"] = csrf_token  # harmless on GET; required by CSRFMiddleware on POST/PUT/DELETE/PATCH

    me_resp = client.get("/api/me")
    assert me_resp.status_code == 200 and me_resp.json()["authenticated"] is True, me_resp.text

    # ---------- 4: POST /fill -- 1.00 lot EURUSD BUY on the master ----------
    fill_resp = httpx.post(
        f"{FAKE_CTRADER_SCENARIO_BASE}/fill",
        json={"account_id": MASTER_ID, "symbol": "EURUSD", "side": "BUY", "volume_lots": "1.00"},
        timeout=10.0,
    )
    assert fill_resp.status_code == 200, fill_resp.text
    master_position_id = fill_resp.json()["position_id"]

    # ---------- 5: poll GET /api/events until 2 slave_action fills appear ----------
    def _slave_fill_events() -> list[dict]:
        resp = client.get("/api/events", params={"category": "slave_action", "limit": 200})
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
    by_slave = {r["slave_account_id"]: r for r in mapping_rows}
    assert by_slave[SLAVE1_ID]["slave_volume"] == SLAVE1_EXPECTED_VOLUME, mapping_rows
    assert by_slave[SLAVE2_ID]["slave_volume"] == SLAVE2_EXPECTED_VOLUME, mapping_rows

    # ---------- 7: kill switch -- disable copying, fill again, assert NO new fills within 5s ----------
    settings_resp = client.put("/api/settings", json={"copying_enabled": False})
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
        resp = client.get("/api/events", params={"category": "slave_action", "limit": 200})
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

    # ---------- 8: GET /api/state -- master position visible with two copies ----------
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
    resync_resp = client.post("/api/control/resync", json={})
    assert resync_resp.status_code == 200, resync_resp.text

    def _state_with_master_position():
        resp = client.get("/api/state")
        assert resp.status_code == 200, resp.text
        state = resp.json()
        matches = [p for p in state.get("master_positions", []) if p["position_id"] == master_position_id]
        return matches[0] if matches else None

    master_position = _poll_until(_state_with_master_position, description="master position visible in /api/state")
    copies = master_position["copies"]
    assert len(copies) == 2, master_position
    assert {c["slave_account_id"] for c in copies} == {SLAVE1_ID, SLAVE2_ID}, master_position
