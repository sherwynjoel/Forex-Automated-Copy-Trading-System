# api/tests/test_portal_multi_account.py
"""Client portal phase 3: several live accounts per investor. Every investor
route that names an account checks the investor owns it; the read routes
pick one with ?account_id=; a deposit funds the named account only while
it is still owned; a workspace cap bounds the count; admins link and unlink
one account at a time; the summary carries every account and the totals.
Grown over Tasks 3-7 of the phase 3 plan."""
from decimal import Decimal

import httpx
import psycopg
import pytest

from conftest import default_mock_callback, seed_mt5
from portal_helpers import (add_method, add_package, credit, csrf, kyc_profile, link, member,
                            open_account_request)

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
READS = ("investor/positions", "investor/analytics", "investor/history/deals?from=0&to=1")
W = {"kind": "wallet", "wallet": "main"}


def A(account_id):
    return {"kind": "account", "account_id": account_id}


def _q(tail, account_id):
    return f"{tail}{'&' if '?' in tail else '?'}account_id={account_id}"


def _live(equity, positions=()):
    return {"balance": float(equity), "equity": float(equity), "open_pnl": 0.0,
            "positions": list(positions)}


def _copier(client, accounts=None, down=False):
    """Fake the copier: /state answers `accounts` ({id: _live(...)}) or 502
    when down; analytics and history answer empty. Returns the list of
    copier URLs asked, in order."""
    seen = []

    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        seen.append(url)
        if "/state" in url:
            if down:
                return httpx.Response(502, json={"detail": "down"})
            return httpx.Response(200, json={
                "status": "ok", "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        if "/analytics" in url:
            return httpx.Response(200, json={"closed_trades": 0, "weeks": 4})
        if "/history/" in url:
            return httpx.Response(200, json={"deals": [], "has_more": False})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)
    return seen


@pytest.fixture
def two(org_client, make_user, login_as, db):
    """An investor (logged in, 1000 in main) owning accounts 1001 and 1002;
    account 1003 belongs to another investor. Returns (client, org_id, investor)."""
    client, org_id, seed = org_client
    for aid in (1001, 1002, 1003):
        seed(aid, role="slave")
    investor = make_user(email="inv@example.com")
    other = make_user(email="other@example.com")
    member(db, org_id, investor["id"], "investor")
    member(db, org_id, other["id"], "investor")
    link(db, org_id, investor["id"], 1001)
    link(db, org_id, investor["id"], 1002)
    link(db, org_id, other["id"], 1003)
    credit(db, org_id, investor["id"], Decimal("1000"))
    login_as(client, investor)
    return client, org_id, investor


# ------------------------------------------------------------ read routes (Task 3)


def test_read_routes_need_a_choice_once_there_are_several(two):
    client, org_id, _ = two
    seen = _copier(client, {1001: _live(10), 1002: _live(20, [
        {"position_id": 8, "symbol": "EURUSD", "side": "SELL", "volume": 1}])})
    for tail in READS:
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 400 and r.json()["detail"] == "account_id is required", tail
        for foreign in (1003, 999):
            r = client.get(f"/api/orgs/{org_id}/{_q(tail, foreign)}")
            assert r.status_code == 404 and r.json()["detail"] == "Account not found", tail
        assert client.get(f"/api/orgs/{org_id}/{_q(tail, 1002)}").status_code == 200, tail
    assert any("/analytics?account_id=1002&weeks=4" in u for u in seen)
    assert any("/history/deals?account_id=1002&from=0&to=1" in u for u in seen)
    body = client.get(f"/api/orgs/{org_id}/investor/positions?account_id=1002").json()
    assert body["equity_source"] == "live"
    assert [p["position_id"] for p in body["positions"]] == [8]


def test_one_account_needs_no_choice_and_none_is_still_409(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    _copier(client, {1001: _live(10)})
    for tail in READS:
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 409 and r.json()["detail"] == "no account linked yet", tail
        r = client.get(f"/api/orgs/{org_id}/{_q(tail, 1001)}")
        assert r.status_code == 404 and r.json()["detail"] == "Account not found", tail
    link(db, org_id, investor["id"], 1001)
    for tail in READS:
        assert client.get(f"/api/orgs/{org_id}/{tail}").status_code == 200, tail


# ------------------------------------------------------------ transfers and notices (Task 3)


def test_transfers_name_an_owned_account(two):
    client, org_id, _ = two
    _copier(client, {1001: _live(500), 1002: _live(300)})

    def move(source, target):
        return client.post(f"/api/orgs/{org_id}/investor/transfers",
                           json={"source": source, "target": target, "amount": "10",
                                 "mpin": "123456"}, headers=csrf(client))

    for foreign in (1003, 999):
        for source, target in ((W, A(foreign)), (A(foreign), W)):
            r = move(source, target)
            assert r.status_code == 404 and r.json()["detail"] == "Account not found", foreign
    r = move(W, A(1002))
    assert r.status_code == 201 and r.json()["target"] == {"kind": "account", "account_id": 1002}
    r = move(A(1002), W)
    assert r.status_code == 201 and r.json()["source"] == {"kind": "account", "account_id": 1002}
    assert r.json()["equity_at_request"] == 300.0


def test_an_account_deposit_names_an_owned_account_once_there_are_several(two, db):
    client, org_id, _ = two
    method_id = add_method(db, org_id)

    def notice(ref, **over):
        return client.post(f"/api/orgs/{org_id}/investor/deposits",
                           json={"method_id": method_id, "amount": "100", "reference": ref,
                                 "target": "account", **over}, headers=csrf(client))

    r = notice("tx-1")
    assert r.status_code == 400 and r.json()["detail"] == "target_account_id is required"
    for foreign in (1003, 999):
        r = notice("tx-2", target_account_id=foreign)
        assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = notice("tx-3", target_account_id=1002)
    assert r.status_code == 201 and r.json()["target_account_id"] == 1002


# ------------------------------------------------------------ deposit decision (Task 4)


def test_confirming_funds_the_named_account_while_it_is_still_owned(two, db, login_as):
    client, org_id, _ = two
    method_id = add_method(db, org_id)
    ids = []
    for ref, account_id in (("tx-a", 1002), ("tx-b", 1001)):
        r = client.post(f"/api/orgs/{org_id}/investor/deposits",
                        json={"method_id": method_id, "amount": "100", "reference": ref,
                              "target": "account", "target_account_id": account_id},
                        headers=csrf(client))
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET investor_user_id = NULL WHERE ctid_trader_account_id = 1001")
    client.cookies.clear()
    login_as(client, ADMIN)
    notes = []
    for dep_id in ids:
        r = client.post(f"/api/orgs/{org_id}/deposits/{dep_id}/decision",
                        json={"status": "confirmed"}, headers=csrf(client))
        assert r.status_code == 200, r.text
        notes.append(r.json()["decision_note"])
    # 1002 is still owned: funded. 1001 was unlinked since the notice: wallet.
    assert notes == [None, "(no account linked; credited to wallet)"]
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute("SELECT target_account_id, amount, status FROM transfers "
                            "WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()
    assert rows == [(1002, Decimal("100.00"), "approved")]


# ------------------------------------------------------------ the cap (Task 5)


def test_the_cap_is_a_portal_setting_from_1_to_50(org_client):
    client, org_id, _ = org_client
    url = f"/api/orgs/{org_id}/portal-settings"
    base = {"withdrawal_min": "0", "withdrawal_fee_pct": "0"}
    assert client.get(url).json()["max_live_accounts"] == 5
    for bad in (0, 51, "5", True, 2.5):
        r = client.put(url, json={**base, "max_live_accounts": bad}, headers=csrf(client))
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "max_live_accounts must be a whole number from 1 to 50"
    r = client.put(url, json={**base, "max_live_accounts": 2}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["max_live_accounts"] == 2
    r = client.put(url, json=base, headers=csrf(client))         # omitted: kept
    assert r.status_code == 200 and r.json()["max_live_accounts"] == 2
    assert client.get(url).json()["max_live_accounts"] == 2


# ------------------------------------------------------------ admin link / unlink (Task 6)


def _events(db, org_id, actions):
    """(events.account_id, action, payload user_id) of these actions, in order."""
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT account_id, payload->>'action', (payload->>'user_id')::bigint FROM events "
            "WHERE org_id = %s AND payload->>'action' = ANY(%s) ORDER BY id",
            (org_id, list(actions))).fetchall()


def test_admin_links_and_unlinks_one_account_at_a_time(org_client, make_user, db):
    client, org_id, seed = org_client
    first, second, third = (seed_mt5(db, org_id, f"key-{i}") for i in range(3))
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    uid = investor["id"]
    _copier(client, down=True)
    base = f"/api/orgs/{org_id}/investors/{uid}/accounts"
    for aid in (first, second):
        r = client.post(base, json={"account_id": aid}, headers=csrf(client))
        assert r.status_code == 201 and r.json() == {"user_id": uid, "account_id": aid}
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert [a["account_id"] for a in row["accounts"]] == [first, second]
    r = client.delete(f"{base}/{third}", headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = client.delete(f"{base}/{first}", headers=csrf(client))
    assert r.status_code == 204
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert [a["account_id"] for a in row["accounts"]] == [second]
    r = client.put(f"/api/orgs/{org_id}/investors/{uid}/account", json={"account_id": None},
                   headers=csrf(client))
    assert r.status_code in (404, 405), "the single-link route is gone"
    viewer = make_user(email="v@example.com")
    member(db, org_id, viewer["id"], "viewer")
    for r in (client.post(f"/api/orgs/{org_id}/investors/{viewer['id']}/accounts",
                          json={"account_id": third}, headers=csrf(client)),
              client.delete(f"/api/orgs/{org_id}/investors/{viewer['id']}/accounts/{third}",
                            headers=csrf(client))):
        assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    assert _events(db, org_id, ("investor_account_linked", "investor_account_unlinked")) == [
        (first, "investor_account_linked", uid), (second, "investor_account_linked", uid),
        (first, "investor_account_unlinked", uid)]


def test_unlink_is_refused_while_the_account_has_an_open_transfer(two, db, login_as):
    """An open transfer may still be funding (target_account_id) or
    withdrawing from (source_account_id) the account -- either end blocks
    the unlink, and the account stays owned until the transfer is settled
    one way or another (rejected here; 'done' clears it the same way)."""
    client, org_id, investor = two
    uid = investor["id"]

    def transfer(**cols):
        base = {"org_id": org_id, "user_id": uid, "amount": 50, "status": "requested"}
        row = {**base, **cols}
        cols_sql = ", ".join(row)
        marks = ", ".join(["%s"] * len(row))
        with psycopg.connect(db, autocommit=True) as conn:
            (tr_id,) = conn.execute(
                f"INSERT INTO transfers ({cols_sql}) VALUES ({marks}) RETURNING id",
                tuple(row.values())).fetchone()
        return tr_id

    tr_target = transfer(source_kind="wallet", source_wallet="main",
                         target_kind="account", target_account_id=1001)
    tr_source = transfer(source_kind="account", source_account_id=1002,
                         target_kind="wallet", target_wallet="main")

    client.cookies.clear()
    login_as(client, ADMIN)
    base = f"/api/orgs/{org_id}/investors/{uid}/accounts"

    for account_id, tr_id in ((1001, tr_target), (1002, tr_source)):
        r = client.delete(f"{base}/{account_id}", headers=csrf(client))
        assert r.status_code == 409
        assert r.json()["detail"] == "this account has open transfers; finish or reject them first"
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute("UPDATE transfers SET status = 'approved' WHERE id = %s", (tr_id,))
        r = client.delete(f"{base}/{account_id}", headers=csrf(client))
        assert r.status_code == 409, "approved also keeps it open"
        with psycopg.connect(db, autocommit=True) as conn:
            (owner,) = conn.execute(
                "SELECT investor_user_id FROM accounts WHERE ctid_trader_account_id = %s",
                (account_id,)).fetchone()
        assert owner == uid, "the refused unlink must not touch the link"

        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute("UPDATE transfers SET status = 'rejected' WHERE id = %s", (tr_id,))
        r = client.delete(f"{base}/{account_id}", headers=csrf(client))
        assert r.status_code == 204


def test_the_cap_blocks_new_links_and_lowering_it_keeps_what_exists(
        org_client, make_user, login_as, db):
    client, org_id, _ = org_client
    first, second, third = (seed_mt5(db, org_id, f"key-{i}") for i in range(3))
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    _copier(client, down=True)
    settings = f"/api/orgs/{org_id}/portal-settings"
    rules = {"withdrawal_min": "0", "withdrawal_fee_pct": "0"}
    base = f"/api/orgs/{org_id}/investors/{investor['id']}/accounts"
    assert client.put(settings, json={**rules, "max_live_accounts": 2},
                      headers=csrf(client)).status_code == 200
    for aid in (first, second):
        assert client.post(base, json={"account_id": aid}, headers=csrf(client)).status_code == 201
    r = client.post(base, json={"account_id": third}, headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["detail"] == "you have reached the limit of 2 live accounts"
    assert client.put(settings, json={**rules, "max_live_accounts": 1},
                      headers=csrf(client)).status_code == 200
    (row,) = client.get(f"/api/orgs/{org_id}/investors").json()
    assert len(row["accounts"]) == 2, "lowering the cap unlinks nothing"
    kyc_profile(db, org_id, investor["id"], status="approved")
    package_id = add_package(db, org_id)
    client.cookies.clear()
    login_as(client, investor)
    r = client.post(f"/api/orgs/{org_id}/investor/account-requests",
                    json={"package_id": package_id, "leverage": 100, "main_password": "Main1234",
                          "investor_password": "Look1234", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["detail"] == "you have reached the limit of 1 live accounts"
    assert client.get(f"/api/orgs/{org_id}/investor/positions?account_id={second}").status_code == 200


# ------------------------------------------------------------ summary (Task 7)


def test_the_summary_lists_every_account_with_totals_from_one_state_call(two, db):
    client, org_id, investor = two
    uid = investor["id"]
    with psycopg.connect(db, autocommit=True) as conn:
        # 1002 has no live figure, only what its MT5 terminal last reported.
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (1002, 'h-1002', 250, 250)")
        for aid, amount in ((1001, 600), (1002, 200)):
            conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
                "target_account_id, amount, status, done_at) "
                "VALUES (%s, %s, 'wallet', 'main', 'account', %s, %s, 'done', now())",
                (org_id, uid, aid, amount))
        conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_account_id, target_kind, "
            "target_wallet, amount, status) VALUES (%s, %s, 'account', 1001, 'wallet', 'main', 50, "
            "'requested')", (org_id, uid))
        conn.execute(
            "INSERT INTO account_requests (org_id, user_id, package_name, leverage, status, "
            "mt5_login, mt5_server, account_id, decided_at) "
            "VALUES (%s, %s, 'Standard', 100, 'fulfilled', 5001, 'Broker-Live', 1001, now())",
            (org_id, uid))
    open_account_request(db, org_id, uid, add_package(db, org_id))
    seen = _copier(client, {1001: _live(700, [{"position_id": 7, "symbol": "XAUUSD"}])})
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert len([u for u in seen if "/state" in u]) == 1
    first, second = body["accounts"]
    assert first == {"account_id": 1001, "nickname": None, "platform": "ctrader", "status": "ok",
                     "last_error": None, "connected": True, "mt5_login": 5001,
                     "mt5_server": "Broker-Live", "equity_source": "live", "equity": 700.0,
                     "net_funded": 600.0, "profit": 100.0, "account_available": 650.0,
                     "open_positions": 1}
    assert second["account_id"] == 1002 and second["mt5_login"] is None
    assert second["mt5_server"] is None and second["equity_source"] == "last known"
    assert second["equity"] == 250.0 and second["profit"] == 50.0
    assert body["equity"] == 950.0 and body["equity_source"] == "last known"
    assert body["net_funded"] == 800.0 and body["profit"] == 150.0
    assert body["open_positions"] == 1
    assert body["account_limit"] == {"max": 5, "used": 3}, "two accounts and one open request"
    for gone in ("account", "link_state", "account_available"):
        assert gone not in body


def test_one_unknown_equity_makes_the_total_unknown(two):
    client, org_id, _ = two
    _copier(client, {1001: _live(700)})      # 1002: no live figure and no MT5 report
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert [a["equity_source"] for a in body["accounts"]] == ["live", "unknown"]
    assert body["equity"] is None and body["profit"] is None
    assert body["equity_source"] == "unknown" and body["net_funded"] == 0.0
