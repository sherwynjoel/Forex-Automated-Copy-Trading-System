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
