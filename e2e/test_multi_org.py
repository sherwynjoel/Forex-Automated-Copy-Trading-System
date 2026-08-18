"""Two orgs on one stack: replication stays inside each org's book, and the
API refuses cross-org access.

Runs against the same already-running compose stack as
`e2e/test_full_stack.py` (which see for the stack, the .env handling, the
`copytrader_e2e` database guard, and the port-override environment
variables); every helper here is imported from it rather than re-written, so
the two tests seed and authenticate identically.

Seeds (direct SQL, via test_full_stack's helpers):
  org A: master 100, slaves 101 (x1.0), 102 (x0.5)  -- the classic trio
  org B: master 200, slave 201 (x1.0)
  users: a@example.com owner of A; b@example.com owner of B (registered via
  /api/register, membership rows inserted via SQL)

Each org gets its OWN ctid_connections row (org_id is NOT NULL there since
005) -- one grant per tenant, exactly as a real second customer's OAuth
consent would produce.

Flow:
  1. POST /reload to the copier (via its compose-internal control port, as
     the existing e2e does) so it sees both orgs and builds an engine for
     each.
  2. Drive a BUY fill on master 100 and a SELL fill on master 200 through the
     fake broker's scenario API. The two use DIFFERENT volumes (1.00 vs 2.00
     lots) so a copy that leaked across orgs would be wrong numerically, not
     just wrong by account id.
  3. Poll mappings until settled, then assert:
     - every mapping row for master_position of 100's fill has org_id = org A
       and slave_account_id in {101, 102}; NOTHING for 201.
     - the 200 fill produced exactly one mapping, org B, slave 201; NOTHING
       for 101/102.
  4. As a@example.com: GET /api/orgs/{orgA}/state -> master_positions show
     100's position; GET /api/orgs/{orgB}/state -> 404.
     GET /api/orgs/{orgB}/accounts -> 404; POST /api/orgs/{orgA}/orders with
     account_id=201 -> 404.
  5. As b@example.com: POST /api/orgs/{orgB}/control/close-all {} -> 200;
     then assert org A's settings still have copying_enabled=true and org
     A's fake-broker positions are still open, while org B's book is flat
     and orgs row B has copying_enabled=false.

Run from the api venv, alongside the single-org test:

    cd api && .venv/bin/pytest ../e2e -v
"""

import time

import httpx
import psycopg
import psycopg.rows
import pytest
from cryptography.fernet import Fernet

from test_full_stack import (
    COPIER_CONTROL_BASE,
    FAKE_CTRADER_SCENARIO_BASE,
    NEGATIVE_WAIT_S,
    POSTGRES_DB_NAME,
    POSTGRES_HOST_PORT,
    REPO_ROOT,
    _connect_to_test_database,
    _dotenv_get,
    _load_dotenv,
    _poll_until,
    _register_owner,
    _reset_fake_broker,
    _seed_account,
    _seed_connection,
    _seed_org,
    _truncate_tenant_tables,
)

# ---------- the two books ----------

ORG_A_MASTER = 100
ORG_A_SLAVE1 = 101   # multiplier 1.0
ORG_A_SLAVE2 = 102   # multiplier 0.5
ORG_B_MASTER = 200
ORG_B_SLAVE = 201    # multiplier 1.0

A_EMAIL = "a@example.com"
B_EMAIL = "b@example.com"
PASSWORD = "a-solid-password"   # >= MIN_PASSWORD_LEN (api/src/api/auth.py)

# lotSize 10_000_000 for the fake server's only symbol (EURUSD).
A_FILL_LOTS = "1.00"
A_SLAVE1_EXPECTED_VOLUME = 10_000_000   # 1.00 * 10_000_000 * 1.0
A_SLAVE2_EXPECTED_VOLUME = 5_000_000    # 1.00 * 10_000_000 * 0.5
B_FILL_LOTS = "2.00"
B_SLAVE_EXPECTED_VOLUME = 20_000_000    # 2.00 * 10_000_000 * 1.0

RESYNC_POLL_INTERVAL_S = 1.0


def _fill(account_id: int, side: str, volume_lots: str) -> int:
    """Push a master-side market fill through the fake broker's scenario API
    and return the position it opened."""
    resp = httpx.post(
        f"{FAKE_CTRADER_SCENARIO_BASE}/fill",
        json={"account_id": account_id, "symbol": "EURUSD",
              "side": side, "volume_lots": volume_lots},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["position_id"]


@pytest.mark.timeout(180)  # bounded waits in sequence can outrun api/pyproject.toml's default 60s
def test_two_orgs_never_touch_each_others_books():
    env = _load_dotenv(REPO_ROOT / ".env")
    fernet = Fernet(_dotenv_get(env, "FERNET_KEY").encode())
    postgres_password = _dotenv_get(env, "POSTGRES_PASSWORD")
    postgres_dsn = (f"postgresql://copytrader:{postgres_password}"
                    f"@127.0.0.1:{POSTGRES_HOST_PORT}/{POSTGRES_DB_NAME}")

    # ---------- 1: seed both orgs, one grant and one book each ----------
    with _connect_to_test_database(postgres_dsn) as conn:
        # Both halves of the previous test's state: Postgres, and the fake
        # broker's own book (these two tests share account ids, so a
        # leftover position on 100 would merge into org A's fill).
        _truncate_tenant_tables(conn)
        _reset_fake_broker()

        org_a = _seed_org(conn, "Org A")
        org_b = _seed_org(conn, "Org B")
        connection_a = _seed_connection(conn, fernet, org_a, "e2e-org-a")
        connection_b = _seed_connection(conn, fernet, org_b, "e2e-org-b")

        _seed_account(conn, org_a, connection_a, ORG_A_MASTER, "master", 1.0)
        _seed_account(conn, org_a, connection_a, ORG_A_SLAVE1, "slave", 1.0)
        _seed_account(conn, org_a, connection_a, ORG_A_SLAVE2, "slave", 0.5)
        # A second 'master' is only unique PER ORG since 005
        # (accounts_single_master), which is exactly what this row proves.
        _seed_account(conn, org_b, connection_b, ORG_B_MASTER, "master", 1.0)
        _seed_account(conn, org_b, connection_b, ORG_B_SLAVE, "slave", 1.0)

    a = f"/api/orgs/{org_a}"
    b = f"/api/orgs/{org_b}"

    # ---------- 2: POST /reload -- the copier builds an engine per org ----------
    reload_resp = httpx.post(f"{COPIER_CONTROL_BASE}/reload", json={}, timeout=30.0)
    assert reload_resp.status_code == 200, reload_resp.text
    assert reload_resp.json().get("status") == "reloaded", reload_resp.text

    client_a = _register_owner(postgres_dsn, org_a, A_EMAIL, PASSWORD, "Alice")
    client_b = _register_owner(postgres_dsn, org_b, B_EMAIL, PASSWORD, "Bob")

    # ---------- 3: one master fill in each org, at different sizes ----------
    a_position_id = _fill(ORG_A_MASTER, "BUY", A_FILL_LOTS)
    b_position_id = _fill(ORG_B_MASTER, "SELL", B_FILL_LOTS)
    assert a_position_id != b_position_id, "the fake must not reuse position ids across accounts"

    def _mappings_for(master_position_id: int) -> list[dict]:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            conn.row_factory = psycopg.rows.dict_row
            return conn.execute(
                "SELECT * FROM mappings WHERE master_position_id = %s"
                " ORDER BY slave_account_id",
                (master_position_id,),
            ).fetchall()

    def _settled(master_position_id: int, expected_count: int):
        def check():
            rows = _mappings_for(master_position_id)
            if len(rows) == expected_count and all(r["status"] == "active" for r in rows):
                return rows
            return None
        return check

    a_rows = _poll_until(
        _settled(a_position_id, 2),
        description="org A's master fill to reach both of org A's slaves")
    b_rows = _poll_until(
        _settled(b_position_id, 1),
        description="org B's master fill to reach org B's single slave")

    # Org A's fill fanned out to org A's slaves only, booked into org A.
    assert {r["org_id"] for r in a_rows} == {org_a}, a_rows
    assert {r["slave_account_id"] for r in a_rows} == {ORG_A_SLAVE1, ORG_A_SLAVE2}, a_rows
    a_by_slave = {r["slave_account_id"]: r for r in a_rows}
    assert a_by_slave[ORG_A_SLAVE1]["slave_volume"] == A_SLAVE1_EXPECTED_VOLUME, a_rows
    assert a_by_slave[ORG_A_SLAVE2]["slave_volume"] == A_SLAVE2_EXPECTED_VOLUME, a_rows

    # Org B's fill produced exactly one copy, on org B's slave, booked into org B.
    assert [r["org_id"] for r in b_rows] == [org_b], b_rows
    assert [r["slave_account_id"] for r in b_rows] == [ORG_B_SLAVE], b_rows
    assert b_rows[0]["slave_volume"] == B_SLAVE_EXPECTED_VOLUME, b_rows

    # The whole mappings table, not just the two positions above: no row
    # anywhere pairs one org's id with another org's slave account. This is
    # what would break if routing fell back to a process-global master/slave
    # set the way it did before 005.
    def _all_mapping_pairs() -> set[tuple[int, int]]:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            return {
                (row[0], row[1]) for row in conn.execute(
                    "SELECT org_id, slave_account_id FROM mappings").fetchall()
            }

    assert _all_mapping_pairs() == {
        (org_a, ORG_A_SLAVE1), (org_a, ORG_A_SLAVE2), (org_b, ORG_B_SLAVE),
    }, _all_mapping_pairs()

    # ...and it stays that way: a late cross-org copy would show up as a new
    # row here, which the polls above (satisfied the moment the expected rows
    # exist) could not have caught.
    time.sleep(NEGATIVE_WAIT_S)
    assert len(_mappings_for(a_position_id)) == 2, _mappings_for(a_position_id)
    assert len(_mappings_for(b_position_id)) == 1, _mappings_for(b_position_id)
    assert _all_mapping_pairs() == {
        (org_a, ORG_A_SLAVE1), (org_a, ORG_A_SLAVE2), (org_b, ORG_B_SLAVE),
    }, _all_mapping_pairs()

    # ---------- 4: the API refuses cross-org access ----------
    # master_positions come only from an explicit resync (see
    # test_full_stack.py step 8 for why); org A's owner triggers org A's.
    resync_a = client_a.post(f"{a}/control/resync", json={})
    assert resync_a.status_code == 200, resync_a.text

    def _org_a_state_position():
        resp = client_a.get(f"{a}/state")
        assert resp.status_code == 200, resp.text
        state = resp.json()
        # Org A's state must never mention org B's master position, whether
        # or not org A's own has landed yet.
        assert not [p for p in state.get("master_positions", [])
                    if p["position_id"] == b_position_id], state
        matches = [p for p in state.get("master_positions", [])
                   if p["position_id"] == a_position_id]
        return matches[0] if matches else None

    a_master_position = _poll_until(
        _org_a_state_position, description="org A's master position in org A's state")
    assert {c["slave_account_id"] for c in a_master_position["copies"]} == {
        ORG_A_SLAVE1, ORG_A_SLAVE2}, a_master_position

    # A non-member gets 404, not 403: org existence never leaks (spec §4).
    assert client_a.get(f"{b}/state").status_code == 404
    assert client_a.get(f"{b}/accounts").status_code == 404
    assert client_b.get(f"{a}/accounts").status_code == 404

    # Tenancy is checked on the BODY's account id too, not just the path's
    # org: org A's owner cannot trade org B's account through org A's route.
    cross_org_order = client_a.post(f"{a}/orders", json={
        "account_id": ORG_B_SLAVE, "symbol": "EURUSD", "side": "BUY",
        "order_type": "MARKET", "volume_lots": "0.10",
    })
    assert cross_org_order.status_code == 404, cross_org_order.text

    # ---------- 4b: the insight surface is org-scoped too ----------
    # /overview is computed in the API from accounts/events/mappings/
    # portfolio_snapshots; every one of those queries has to be filtered by
    # org or this page shows one desk another desk's fleet and copy volume.
    overview_a = client_a.get(f"{a}/overview")
    assert overview_a.status_code == 200, overview_a.text
    stats_a = overview_a.json()
    assert stats_a["accounts_connected"] == 3, stats_a   # 100/101/102 only
    assert stats_a["masters"] == 1, stats_a
    assert {c["slave_account_id"] for c in stats_a["recent_copies"]} <= {
        ORG_A_SLAVE1, ORG_A_SLAVE2}, stats_a

    overview_b = client_b.get(f"{b}/overview")
    assert overview_b.status_code == 200, overview_b.text
    stats_b = overview_b.json()
    assert stats_b["accounts_connected"] == 2, stats_b   # 200/201 only
    assert {c["slave_account_id"] for c in stats_b["recent_copies"]} <= {
        ORG_B_SLAVE}, stats_b

    # A non-member never sees the other desk's overview at all.
    assert client_a.get(f"{b}/overview").status_code == 404

    # The four broker proxies check the ACCOUNT's tenancy before proxying,
    # so org A cannot read org B's account through org A's own path -- and
    # the copier (whose query routes take no org at all) is never reached.
    for tail in (
        f"accounts/{ORG_B_SLAVE}/analytics?weeks=1",
        f"accounts/{ORG_B_SLAVE}/margin-estimate?symbol=EURUSD&volume_lots=0.1",
        f"accounts/{ORG_B_SLAVE}/trendbars?symbol=EURUSD&period=H1&from=0&to=1",
        f"accounts/{ORG_B_SLAVE}/positions/1/deals?from=0&to=1",
    ):
        resp = client_a.get(f"{a}/{tail}")
        assert resp.status_code == 404, f"{tail}: {resp.status_code} {resp.text}"

    # ---------- 5: org B's kill switch leaves org A entirely alone ----------
    # Establish org B's book is NOT empty first, or the "flat afterwards"
    # assertion below passes vacuously -- master_positions is also empty for
    # an org whose engine never reconciled at all, or that lost its master.
    resync_b = client_b.post(f"{b}/control/resync", json={})
    assert resync_b.status_code == 200, resync_b.text

    def _org_b_state_position():
        resp = client_b.get(f"{b}/state")
        assert resp.status_code == 200, resp.text
        matches = [p for p in resp.json().get("master_positions", [])
                   if p["position_id"] == b_position_id]
        return matches[0] if matches else None

    b_master_position = _poll_until(
        _org_b_state_position, description="org B's master position in org B's state")
    assert {c["slave_account_id"] for c in b_master_position["copies"]} == {
        ORG_B_SLAVE}, b_master_position

    close_all = client_b.post(f"{b}/control/close-all", json={})
    assert close_all.status_code == 200, close_all.text
    assert close_all.json()["paused"] is True, close_all.text
    assert {r["account_id"] for r in close_all.json()["accounts"]} == {
        ORG_B_MASTER, ORG_B_SLAVE}, close_all.text

    # The org-wide kill switch pauses THAT org's copying, and only that org's.
    b_settings = client_b.get(f"{b}/settings")
    assert b_settings.status_code == 200, b_settings.text
    assert b_settings.json()["copying_enabled"] is False, b_settings.text

    a_settings = client_a.get(f"{a}/settings")
    assert a_settings.status_code == 200, a_settings.text
    assert a_settings.json()["copying_enabled"] is True, a_settings.text

    # Org B's book is genuinely flat AT THE BROKER: each poll re-reconciles
    # org B (a real ProtoOAReconcileReq per org-B account against
    # fake-ctrader) and reads the resulting snapshot back.
    def _org_b_flat():
        resync_b = client_b.post(f"{b}/control/resync", json={})
        assert resync_b.status_code == 200, resync_b.text
        resp = client_b.get(f"{b}/state")
        assert resp.status_code == 200, resp.text
        return resp.json() if not resp.json().get("master_positions") else None

    _poll_until(_org_b_flat, interval_s=RESYNC_POLL_INTERVAL_S,
                description="org B's master book to be flat after its kill switch")

    # ...while org A's positions are untouched, again read straight from the
    # broker via a fresh reconcile.
    resync_a2 = client_a.post(f"{a}/control/resync", json={})
    assert resync_a2.status_code == 200, resync_a2.text

    a_state = client_a.get(f"{a}/state")
    assert a_state.status_code == 200, a_state.text
    still_open = [p for p in a_state.json()["master_positions"]
                  if p["position_id"] == a_position_id]
    assert still_open, a_state.json()
    assert {c["slave_account_id"] for c in still_open[0]["copies"]} == {
        ORG_A_SLAVE1, ORG_A_SLAVE2}, still_open

    # `missing_slave_copy` is raised for an active mapping whose slave
    # position has vanished from the broker while the master's is still open
    # (engine/reconcile.py) -- exactly the shape org B's kill switch would
    # take if it had reached into org A's slaves. There is none.
    assert [i for i in a_state.json().get("drift", [])
            if i["kind"] == "missing_slave_copy"] == [], a_state.json()["drift"]

    assert all(r["status"] == "active" for r in _mappings_for(a_position_id)), \
        _mappings_for(a_position_id)
