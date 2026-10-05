# api/tests/test_investor_access.py
"""The investor role's access surface: invite+join, the desk-endpoint
refusal, and the account-linking edge cases -- recovered from the
pre-022 test_investor_portal.py (deleted whole by migration 022, which
dropped org_investor_wallets/investor_deposits/investor_withdrawals).
These five never touched those tables -- linking only ever wrote
org_memberships and accounts; the two linking tests were rewritten onto
POST .../investors/{id}/accounts in phase 3. The money-moving
tests around wallets, deposits and withdrawals are re-tested per feature
in Tasks 6-10 once their routes are rebuilt on the new tables."""
import psycopg

from conftest import seed_mt5
from portal_helpers import csrf, member

from api.alerts import ALERT_RULES
from api.telegram import TELEGRAM_RULES


def test_an_investor_invite_can_be_created_and_joined(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    r = client.post(f"/api/orgs/{org_id}/invites", json={"role": "investor"},
                    headers=csrf(client))
    assert r.status_code == 201 and r.json()["role"] == "investor"
    token = r.json()["token"]
    investor = make_user(email="inv@example.com")
    login_as(client, investor)
    r = client.post("/api/orgs/join", json={"token": token}, headers=csrf(client))
    assert r.status_code == 200 and r.json() == {"org_id": org_id, "role": "investor"}
    me = client.get("/api/me").json()
    assert me["orgs"] == [{"id": org_id, "name": "Desk", "role": "investor"}]


def test_an_investor_is_refused_by_every_desk_endpoint(org_client, make_user, login_as, db):
    """The desk's HTTP surface. The org-wide WebSocket feed (/api/ws) refuses
    investors too -- that one needs a live server, so it is covered by
    test_events_ws.py::test_ws_refuses_investors_but_still_serves_viewers."""
    client, org_id, seed = org_client
    seed(100, role="master")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    for method, tail in [
        ("GET", "accounts"), ("GET", "accounts/100/details"), ("GET", "state"),
        ("GET", "settings"), ("GET", "events"), ("GET", "members"), ("GET", "overview"),
        ("GET", "accounts/100/analytics"), ("GET", "accounts/100/history/deals?from=0&to=1"),
        ("GET", "webhook"), ("GET", "risk-rules"),
    ]:
        r = client.request(method, f"/api/orgs/{org_id}/{tail}", headers=csrf(client))
        assert r.status_code == 403, f"{method} {tail} -> {r.status_code}"
    r = client.get(f"/api/orgs/{org_id}")
    assert r.status_code == 403


def test_linking_refuses_an_account_from_another_workspace_or_a_non_investor(
        org_client, make_user, make_org, db):
    client, org_id, seed = org_client
    other_owner = make_user(email="o@example.com")
    other_org = make_org(name="Other", members=[(other_owner, "admin")])
    foreign = seed_mt5(db, other_org, "key-foreign")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    r = client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/accounts",
                    json={"account_id": foreign}, headers=csrf(client))
    assert r.status_code == 404
    viewer = make_user(email="v@example.com")
    member(db, org_id, viewer["id"], "viewer")
    mine = seed_mt5(db, org_id, "key-mine")
    r = client.post(f"/api/orgs/{org_id}/investors/{viewer['id']}/accounts",
                    json={"account_id": mine}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"


def test_the_master_account_cannot_be_linked_to_an_investor(org_client, make_user, db):
    """R17: the master is the desk's own account. Linked to an investor it
    would show them the desk's equity as their balance and let them request
    a withdrawal against it. The refusal leaves the existing link alone."""
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    assert client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/accounts",
                       json={"account_id": 1001}, headers=csrf(client)).status_code == 201

    r = client.post(f"/api/orgs/{org_id}/investors/{investor['id']}/accounts",
                    json={"account_id": 100}, headers=csrf(client))
    assert r.status_code == 400 and "master" in r.json()["detail"]
    with psycopg.connect(db, autocommit=True) as conn:
        links = dict(conn.execute(
            "SELECT ctid_trader_account_id, investor_user_id FROM accounts WHERE org_id = %s",
            (org_id,)).fetchall())
    assert links == {100: None, 1001: investor["id"]}


def test_the_two_request_actions_reach_both_alerters():
    for rules in (ALERT_RULES, TELEGRAM_RULES):
        assert ("control", "warning", "investor_deposit_noticed") in rules
        assert ("control", "warning", "investor_withdrawal_requested") in rules


def test_investor_routes_refuse_desk_members(org_client, make_user, login_as, db):
    """Spec §14: the investor portal is for investors only. A viewer and an
    admin are members, but filing money requests as themselves is refused."""
    client, org_id, _seed = org_client
    viewer = make_user(email="viewer@example.com")
    member(db, org_id, viewer["id"], "viewer")
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    body = {"method_id": 1, "amount": "10", "reference": "x", "target": "wallet"}
    for user in (None, viewer):  # None = the org admin, already logged in
        if user:
            login_as(client, user)
        r = client.get(f"/api/orgs/{org_id}/investor/summary")
        assert r.status_code == 403 and r.json()["detail"] == "Insufficient role"
        r = client.post(f"/api/orgs/{org_id}/investor/deposits", json=body, headers=csrf(client))
        assert r.status_code == 403 and r.json()["detail"] == "Insufficient role"
        r = client.get(f"/api/orgs/{org_id}/investor/files/1")
        assert r.status_code == 403
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/summary").status_code == 200
