"""The investor portal: a role that can see only its own linked account,
the workspace wallet card, deposit notices and withdrawal requests.
Every test that needs the copier's equity fakes /state through the
app's mock transport, the same way test_webhooks.py does."""
import json
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _link(db, account_id, user):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET investor_user_id = %s "
                     "WHERE ctid_trader_account_id = %s", (user["id"], account_id))


def _wallet(db, org_id, coin="USDT", network="TRC20", address="TAddr123"):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
            "VALUES (%s, %s, %s, %s)", (org_id, coin, network, address))


def _state(client, accounts=None, down=False):
    """Fake the copier's /state. `accounts` is {account_id: {equity, balance,
    open_pnl, positions}} exactly as the copier serialises it (string keys)."""
    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            if down:
                return httpx.Response(502, json={"detail": "down"})
            return httpx.Response(200, json={
                "status": "ok",
                "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)


# ---------------------------------------------------------------- the role


def test_an_investor_invite_can_be_created_and_joined(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    r = client.post(f"/api/orgs/{org_id}/invites", json={"role": "investor"},
                    headers=_csrf(client))
    assert r.status_code == 201 and r.json()["role"] == "investor"
    token = r.json()["token"]
    investor = make_user(email="inv@example.com")
    login_as(client, investor)
    r = client.post("/api/orgs/join", json={"token": token}, headers=_csrf(client))
    assert r.status_code == 200 and r.json() == {"org_id": org_id, "role": "investor"}
    me = client.get("/api/me").json()
    assert me["orgs"] == [{"id": org_id, "name": "Desk", "role": "investor"}]


def test_an_investor_is_refused_by_every_desk_endpoint(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    for method, tail in [
        ("GET", "accounts"), ("GET", "accounts/100/details"), ("GET", "state"),
        ("GET", "settings"), ("GET", "events"), ("GET", "members"), ("GET", "overview"),
        ("GET", "accounts/100/analytics"), ("GET", "accounts/100/history/deals?from=0&to=1"),
        ("GET", "webhook"), ("GET", "risk-rules"),
    ]:
        r = client.request(method, f"/api/orgs/{org_id}/{tail}", headers=_csrf(client))
        assert r.status_code == 403, f"{method} {tail} -> {r.status_code}"
    r = client.get(f"/api/orgs/{org_id}")
    assert r.status_code == 403


# ------------------------------------------------------- summary + wallet


def test_summary_before_an_account_is_linked_says_so(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    r = client.get(f"/api/orgs/{org_id}/investor/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["link_state"] == "unlinked" and body["account"] is None
    assert body["equity"] is None and body["profit"] is None
    assert body["total_deposited"] == 0.0
    assert body["org"] == {"id": org_id, "name": "Desk"}


def test_summary_uses_the_linked_accounts_live_equity(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    _link(db, 1001, investor)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid, status) "
                     "VALUES (%s, %s, 5000, 'USDT', 't1', 'confirmed')", (org_id, investor["id"]))
    _state(client, {1001: {"balance": 5000.0, "equity": 5120.5, "open_pnl": 120.5,
                           "positions": []}})
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["link_state"] == "linked" and body["account"]["account_id"] == 1001
    assert body["equity"] == 5120.5 and body["equity_source"] == "live"
    assert body["net_deposits"] == 5000.0 and body["profit"] == 120.5


def test_summary_falls_back_to_last_known_equity_when_the_copier_is_down(
        org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (aid,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled, nickname, investor_user_id) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', "
            "true, 'Inv', %s) RETURNING ctid_trader_account_id",
            (org_id, investor["id"])).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (%s, 'h', 4990.25, 4990.25)", (aid,))
    _state(client, down=True)
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["equity"] == 4990.25 and body["equity_source"] == "last known"
    assert body["account"]["platform"] == "mt5" and body["account"]["connected"] is False


def test_wallet_is_404_until_an_admin_sets_it(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/investor-wallet", json={
        "coin": "USDT", "network": "TRC20", "address": "TAddr123", "memo": None},
        headers=_csrf(client))
    assert r.status_code == 200
    assert client.get(f"/api/orgs/{org_id}/investor-wallet").json() == {
        "coin": "USDT", "network": "TRC20", "address": "TAddr123", "memo": None}
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/wallet").json()["address"] == "TAddr123"
    assert client.put(f"/api/orgs/{org_id}/investor-wallet", json={
        "coin": "X", "network": "Y", "address": "Z"}, headers=_csrf(client)).status_code == 403


def test_wallet_with_no_row_is_404_for_investors(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/wallet").status_code == 404


def test_wallet_address_is_required(org_client):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/investor-wallet", json={
        "coin": "USDT", "network": "TRC20", "address": "  "}, headers=_csrf(client))
    assert r.status_code == 400 and "address" in r.json()["detail"]


# -------------------------------------------------- investors + linking


def test_admin_links_and_unlinks_an_account(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    _state(client, {1001: {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0, "positions": []}})

    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 1001}, headers=_csrf(client))
    assert r.status_code == 200 and r.json() == {"user_id": investor["id"], "account_id": 1001}
    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert rows == [{"user_id": investor["id"], "email": "inv@example.com",
                     "display_name": "User", "account_id": 1001, "nickname": None,
                     "equity": 100.0, "net_deposits": 0.0, "profit": 100.0,
                     "pending_deposits": 0, "pending_withdrawals": 0}]

    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": None}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["account_id"] is None
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] is None
    with psycopg.connect(db, autocommit=True) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT payload->>'action' FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()]
    assert actions == ["investor_account_linked", "investor_account_linked"]


def test_linking_refuses_an_account_from_another_workspace_or_a_non_investor(
        org_client, make_user, make_org, db):
    client, org_id, seed = org_client
    other_owner = make_user(email="o@example.com")
    other_org = make_org(name="Other", members=[(other_owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (cid,) = conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, "
            "granted_at, expires_at) VALUES (%s, 'e', 'e', now(), now() + interval '1 day') "
            "RETURNING id", (other_org,)).fetchone()
        conn.execute("INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                     "trader_login, is_live, role) VALUES (2001, %s, %s, 2001, false, 'slave')",
                     (cid, other_org))
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 2001}, headers=_csrf(client))
    assert r.status_code == 404
    viewer = make_user(email="v@example.com")
    _member(db, org_id, viewer, "viewer")
    seed(1001, role="slave")
    r = client.put(f"/api/orgs/{org_id}/investors/{viewer['id']}/account",
                   json={"account_id": 1001}, headers=_csrf(client))
    assert r.status_code == 404


def test_the_investor_list_asks_the_copier_once(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    seed(1002, role="slave")
    inv1 = make_user(email="inv1@example.com")
    inv2 = make_user(email="inv2@example.com")
    _member(db, org_id, inv1, "investor")
    _member(db, org_id, inv2, "investor")
    _link(db, 1001, inv1)
    _link(db, 1002, inv2)

    calls = {"state": 0}

    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            calls["state"] += 1
            return httpx.Response(200, json={
                "status": "ok",
                "accounts": {"1001": {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0,
                                      "positions": []},
                             "1002": {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0,
                                      "positions": []}},
                "master_positions": [], "pending_orders": [], "drift": []})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)

    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert len(rows) == 2
    assert all(r["equity"] == 100.0 for r in rows)
    assert calls["state"] == 1
