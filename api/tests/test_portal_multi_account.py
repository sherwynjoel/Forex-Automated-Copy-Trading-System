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
