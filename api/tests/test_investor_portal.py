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
    """The desk's HTTP surface. The org-wide WebSocket feed (/api/ws) refuses
    investors too -- that one needs a live server, so it is covered by
    test_events_ws.py::test_ws_refuses_investors_but_still_serves_viewers."""
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


# ---------------------------------------------------------------- deposits


def _investor_with_wallet(org_client, make_user, login_as, db, link_to=None):
    client, org_id, seed = org_client
    _wallet(db, org_id)
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    if link_to is not None:
        seed(link_to, role="slave")
        _link(db, link_to, investor)
    login_as(client, investor)
    return client, org_id, investor


def test_an_investor_files_a_deposit_notice(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5000", "coin": "usdt", "txid": " abc123 ", "note": "sent from Binance"},
        headers=_csrf(client))
    assert r.status_code == 201
    body = r.json()
    assert body["amount"] == 5000.0 and body["coin"] == "USDT" and body["txid"] == "abc123"
    assert body["status"] == "pending" and body["account_id"] is None
    assert client.get(f"/api/orgs/{org_id}/investor/deposits").json() == [body]
    with psycopg.connect(db, autocommit=True) as conn:
        (severity, payload, actor) = conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    assert severity == "warning" and payload["action"] == "investor_deposit_noticed"
    assert payload["summary"] == "Deposit notice: 5000.00 USDT from inv@example.com"
    assert actor == "inv@example.com"


@pytest.mark.parametrize("body, needle", [
    ({"amount": "-5", "coin": "USDT", "txid": "t"}, "amount"),
    ({"amount": "5.001", "coin": "USDT", "txid": "t"}, "decimals"),
    ({"amount": "5", "coin": "USDT", "txid": "  "}, "txid"),
    ({"amount": "5", "coin": "BTC", "txid": "t"}, "USDT"),
])
def test_a_bad_notice_is_refused_with_the_reason(org_client, make_user, login_as, db, body, needle):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json=body, headers=_csrf(client))
    assert r.status_code == 400 and needle in r.json()["detail"]


def test_notices_are_refused_while_no_wallet_is_configured(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client))
    assert r.status_code == 409 and "not open" in r.json()["detail"]


def test_ten_notices_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    for i in range(10):
        r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
            "amount": "1", "coin": "USDT", "txid": f"t{i}"}, headers=_csrf(client))
        assert r.status_code == 201
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "1", "coin": "USDT", "txid": "t10"}, headers=_csrf(client))
    assert r.status_code == 429


def test_admin_confirms_a_notice_once_and_it_counts(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5000", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    admin = {"email": "admin@example.com", "password": "a-solid-password"}
    login_as(client, admin)
    queue = client.get(f"/api/orgs/{org_id}/investor-deposits?status=pending").json()
    assert [d["id"] for d in queue] == [dep["id"]] and queue[0]["email"] == "inv@example.com"

    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "confirmed", "note": "seen on chain"}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed" and r.json()["account_id"] == 1001
    assert r.json()["decision_note"] == "seen on chain"

    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "rejected", "note": "changed my mind"}, headers=_csrf(client))
    assert r.status_code == 409 and "confirmed" in r.json()["detail"]

    _state(client, {1001: {"balance": 5000.0, "equity": 5000.0, "open_pnl": 0.0, "positions": []}})
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/summary").json()["total_deposited"] == 5000.0


def test_a_rejection_needs_a_note(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    login_as(client, {"email": "admin@example.com", "password": "a-solid-password"})
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "rejected"}, headers=_csrf(client))
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "rejected", "note": "no such transaction"},
                    headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_investors_only_see_their_own_notices_and_cannot_decide(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/deposits").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "confirmed"}, headers=_csrf(client))
    assert r.status_code == 403
    assert client.get(f"/api/orgs/{org_id}/investor-deposits").status_code == 403


# ------------------------------------------------------------- withdrawals


def _funded_investor(org_client, make_user, login_as, db, equity=5120.5):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO investor_deposits (org_id, user_id, account_id, amount, coin, "
                     "txid, status) VALUES (%s, %s, 1001, 5000, 'USDT', 't', 'confirmed')",
                     (org_id, investor["id"]))
    if equity is None:
        _state(client, down=True)
    else:
        _state(client, {1001: {"balance": 5000.0, "equity": equity, "open_pnl": equity - 5000,
                               "positions": []}})
    return client, org_id, investor


def test_an_investor_requests_a_withdrawal_within_available(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "1000", "destination": " TDest999 "}, headers=_csrf(client))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "requested" and body["destination"] == "TDest999"
    assert body["equity_at_request"] == 5120.5 and body["equity_verified"] is True
    assert body["account_id"] == 1001
    summary = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert summary["pending_withdrawn"] == 1000.0 and summary["available"] == 4120.5
    with psycopg.connect(db, autocommit=True) as conn:
        (severity, payload) = conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s ORDER BY id DESC LIMIT 1",
            (org_id,)).fetchone()
    assert severity == "warning" and payload["action"] == "investor_withdrawal_requested"
    assert payload["summary"] == "Withdrawal request: 1000.00 from inv@example.com"


def test_a_request_above_available_is_refused_with_the_figure(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "4000", "destination": "TDest"}, headers=_csrf(client))
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "2000", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 400 and "1120.50" in r.json()["detail"]


def test_a_request_with_unknown_equity_is_accepted_but_flagged(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db, equity=None)
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "99999", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 201
    assert r.json()["equity_verified"] is False and r.json()["equity_at_request"] is None


def test_no_account_no_withdrawal(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "5", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 409 and "no account linked" in r.json()["detail"]


def test_approve_then_paid_and_the_ledger_moves(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    wd = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "1000", "destination": "TDest"}, headers=_csrf(client)).json()
    admin = {"email": "admin@example.com", "password": "a-solid-password"}
    login_as(client, admin)
    queue = client.get(f"/api/orgs/{org_id}/investor-withdrawals?status=requested").json()
    assert [w["id"] for w in queue] == [wd["id"]] and queue[0]["email"] == "inv@example.com"

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/paid",
                    json={"txid": "x"}, headers=_csrf(client))
    assert r.status_code == 409 and "requested" in r.json()["detail"]

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "approved"}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "approved"

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/paid",
                    json={"txid": "  "}, headers=_csrf(client))
    assert r.status_code == 400 and "txid" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/paid",
                    json={"txid": "chain-tx-1"}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "paid" and r.json()["txid"] == "chain-tx-1"
    assert r.json()["paid_at"] is not None

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "rejected", "note": "too late"}, headers=_csrf(client))
    assert r.status_code == 409

    login_as(client, investor)
    summary = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert summary["total_withdrawn"] == 1000.0 and summary["net_deposits"] == 4000.0
    assert summary["pending_withdrawn"] == 0.0
    with psycopg.connect(db, autocommit=True) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT payload->>'action' FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()]
    assert actions[-3:] == ["investor_withdrawal_requested", "investor_withdrawal_decided",
                            "investor_withdrawal_paid"]


def test_an_approved_request_can_still_be_rejected_with_a_note(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    wd = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "10", "destination": "TDest"}, headers=_csrf(client)).json()
    login_as(client, {"email": "admin@example.com", "password": "a-solid-password"})
    client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                json={"status": "approved"}, headers=_csrf(client))
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "rejected"}, headers=_csrf(client))
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "rejected", "note": "address did not match"},
                    headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_ten_withdrawal_requests_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    for _ in range(10):
        assert client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
            "amount": "1", "destination": "TDest"}, headers=_csrf(client)).status_code == 201
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "1", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 429


def test_investors_only_see_their_own_withdrawals_and_cannot_decide(
        org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    wd = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "10", "destination": "TDest"}, headers=_csrf(client)).json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/withdrawals").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "approved"}, headers=_csrf(client))
    assert r.status_code == 403
    assert client.get(f"/api/orgs/{org_id}/investor-withdrawals").status_code == 403


# ------------------------------------------------------------ read-throughs


def _recording_copier(client, accounts=None):
    """Answer the copier's read endpoints and remember every URL asked."""
    seen = []
    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        seen.append(url)
        if "/state" in url:
            return httpx.Response(200, json={
                "status": "ok", "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        if "/analytics" in url:
            return httpx.Response(200, json={"closed_trades": 3, "wins": 2, "losses": 1,
                                             "net_pnl": 120.5, "weeks": 4})
        if "/history/" in url:
            return httpx.Response(200, json={"deals": [], "has_more": False})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)
    return seen


def test_read_throughs_need_a_linked_account(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    for tail in ("investor/positions", "investor/analytics",
                 "investor/history/deals?from=0&to=1"):
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 409 and "no account linked" in r.json()["detail"], tail


def test_positions_come_from_the_linked_accounts_state(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    _recording_copier(client, {1001: {"balance": 5000.0, "equity": 5010.0, "open_pnl": 10.0,
        "positions": [{"position_id": 7, "symbol_id": 41, "symbol": "XAUUSD", "side": "BUY",
                       "volume": 1, "stop_loss": 4300.0, "take_profit": 4400.0,
                       "entry_price": 4350.0, "pnl_quote": 10.0, "current_price": 4360.0}]},
        1002: {"balance": 1.0, "equity": 1.0, "open_pnl": 0.0,
               "positions": [{"position_id": 8, "symbol_id": 41, "symbol": "XAUUSD",
                              "side": "SELL", "volume": 1, "entry_price": 1.0}]}})
    body = client.get(f"/api/orgs/{org_id}/investor/positions").json()
    assert body["equity_source"] == "live"
    assert body["positions"] == [{"position_id": 7, "symbol": "XAUUSD", "side": "BUY",
                                  "volume": 1, "entry_price": 4350.0, "current_price": 4360.0,
                                  "stop_loss": 4300.0, "take_profit": 4400.0, "pnl_quote": 10.0}]


def test_analytics_and_history_are_asked_for_the_linked_account_only(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    seen = _recording_copier(client)
    r = client.get(f"/api/orgs/{org_id}/investor/analytics?weeks=99")
    assert r.status_code == 200 and r.json()["net_pnl"] == 120.5
    assert any(u.endswith("/analytics?account_id=1001&weeks=12") for u in seen)
    r = client.get(f"/api/orgs/{org_id}/investor/history/deals?from=5&to=9")
    assert r.status_code == 200 and r.json() == {"deals": [], "has_more": False}
    assert any(u.endswith("/history/deals?account_id=1001&from=5&to=9") for u in seen)
    assert client.get(f"/api/orgs/{org_id}/investor/history/trades?from=0&to=1").status_code == 400


# ------------------------------------------------------------ notifications
import asyncio

from api.alerts import ALERT_RULES, EmailAlerter
from api.telegram import TELEGRAM_RULES, TelegramNotifier
from api import ws as ws_module


def test_the_two_request_actions_reach_both_alerters():
    for rules in (ALERT_RULES, TELEGRAM_RULES):
        assert ("control", "warning", "investor_deposit_noticed") in rules
        assert ("control", "warning", "investor_withdrawal_requested") in rules


def _posted(callback_holder):
    posts = []
    def cb(request):
        posts.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True})
    callback_holder.append(cb)
    return posts


def test_telegram_text_for_investor_actions_is_the_summary_line():
    holder = []
    posts = _posted(holder)
    notifier = TelegramNotifier(http=httpx.AsyncClient(transport=httpx.MockTransport(holder[0])),
                                bot_token="t", chat_id="c")
    event = {"category": "control", "severity": "warning", "account_id": None,
             "payload": {"action": "investor_deposit_noticed", "user_id": 5,
                         "summary": "Deposit notice: 5000.00 USDT from inv@example.com"}}
    other = dict(event, payload={**event["payload"], "user_id": 6})

    async def scenario():
        # One event loop for all three calls: an httpx AsyncClient must not
        # be used across separate asyncio.run() loops.
        first = await notifier.consider(event)
        second = await notifier.consider(other)
        third = await notifier.consider(event)
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first is True
    assert posts[0]["text"] == "💰 Copy Desk: Deposit notice: 5000.00 USDT from inv@example.com"
    assert second is True, "another investor is not cooled down"
    assert third is False, "same investor is cooled down"


def test_email_send_to_posts_to_the_given_address():
    holder = []
    posts = _posted(holder)
    alerter = EmailAlerter(http=httpx.AsyncClient(transport=httpx.MockTransport(holder[0])),
                           api_key="k", from_addr="Desk <d@example.com>", to_addr="")
    assert asyncio.run(alerter.send_to("inv@example.com", "Deposit confirmed", "hello")) is True
    assert posts[-1]["to"] == ["inv@example.com"] and posts[-1]["subject"] == "Deposit confirmed"


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail
    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject))
        return True


def test_a_decision_emails_the_investor_and_a_failed_email_never_fails_the_request(
        org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    dep2 = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "6", "coin": "USDT", "txid": "t2"}, headers=_csrf(client)).json()
    login_as(client, {"email": "admin@example.com", "password": "a-solid-password"})
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "confirmed"}, headers=_csrf(client))
    assert r.status_code == 200
    assert fake.sent == [("inv@example.com", "Your deposit of 5.00 USDT was confirmed")]
    monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep2['id']}/decision",
                    json={"status": "rejected", "note": "no"}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
