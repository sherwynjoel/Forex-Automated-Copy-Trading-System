"""Transfers: four allowed pairs. pamm->main and social->main settle at
once; main->account holds wallet money until an admin funds the broker
account and marks done; account->main is capped by the account's
available equity and credits the wallet when marked done."""
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import credit, csrf, link

from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


def W(wallet):
    return {"kind": "wallet", "wallet": wallet}


def A(account_id):
    return {"kind": "account", "account_id": account_id}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


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


def _funded(org_client, make_user, login_as, db, *, main="1000", link_to=None, equity=None):
    """An investor with `main` credited; optionally linked to a freshly
    seeded follower account whose live equity the fake copier reports."""
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    if Decimal(main) != 0:
        credit(db, org_id, investor["id"], Decimal(main))
    if link_to is not None:
        seed(link_to, role="slave")
        link(db, org_id, investor["id"], link_to)
        if equity is None:
            _state(client, down=True)
        else:
            _state(client, {link_to: {"balance": float(equity), "equity": float(equity),
                                      "open_pnl": 0.0, "positions": []}})
    login_as(client, investor)
    return client, org_id, investor


def _transfer(client, org_id, source, target, amount, mpin="123456"):
    return client.post(f"/api/orgs/{org_id}/investor/transfers",
                       json={"source": source, "target": target, "amount": amount, "mpin": mpin},
                       headers=csrf(client))


def _decide(client, org_id, tr_id, status, note=None):
    return client.post(f"/api/orgs/{org_id}/transfers/{tr_id}/decision",
                       json={"status": status, "note": note}, headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()


def _entries(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return [(r[0], float(r[1]), r[2], r[3], r[4]) for r in conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id FROM wallet_entries "
            "WHERE org_id = %s AND user_id = %s ORDER BY id", (org_id, user_id)).fetchall()]


def _figures(db, org_id, user_id, wallet="main"):
    from api.portal_common import wallet_figures
    with psycopg.connect(db, autocommit=True) as conn:
        figures = wallet_figures(conn, org_id, user_id)[wallet]
    return {k: float(v) for k, v in figures.items()}


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject))
        return True


# --------------------------------------------------------- wallet to wallet


def test_pamm_to_main_completes_at_once(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0")
    credit(db, org_id, investor["id"], Decimal("300"), wallet="pamm")
    r = _transfer(client, org_id, W("pamm"), W("main"), "120.50")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "done" and body["done_at"] is not None and body["done_by"] is None
    assert body["source"]["kind"] == "wallet" and body["source"]["wallet"] == "pamm"
    assert body["target"]["kind"] == "wallet" and body["target"]["wallet"] == "main"
    assert body["amount"] == 120.5 and body["currency"] == "USD"
    assert body["equity_at_request"] is None and body["equity_verified"] is False
    assert _entries(db, org_id, investor["id"])[-2:] == [
        ("pamm", -120.5, "transfer", "transfers", body["id"]),
        ("main", 120.5, "transfer", "transfers", body["id"])]
    assert _figures(db, org_id, investor["id"], "pamm")["balance"] == 179.5
    assert _figures(db, org_id, investor["id"], "main")["available"] == 120.5
    assert client.get(f"/api/orgs/{org_id}/investor/transfers").json() == [body]
    severity, payload = _events(db, org_id)[-1]
    assert severity == "info" and payload["action"] == "investor_transfer_requested"
    assert payload["instant"] is True and payload["user_id"] == investor["id"]


def test_social_to_main_is_capped_by_the_social_wallet(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db)
    credit(db, org_id, investor["id"], Decimal("40"), wallet="social")
    r = _transfer(client, org_id, W("social"), W("main"), "40.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (40.00)"
    assert _transfer(client, org_id, W("social"), W("main"), "40").status_code == 201


@pytest.mark.parametrize("source, target", [
    (W("credit"), W("main")), (W("main"), W("pamm")), (W("main"), W("main")),
    (W("pamm"), W("social")), (A(1001), A(1001)), (W("main"), W("credit")),
])
def test_disallowed_pairs_are_refused(org_client, make_user, login_as, db, source, target):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="100")
    credit(db, org_id, investor["id"], Decimal("100"), wallet="credit")
    r = _transfer(client, org_id, source, target, "1")
    assert r.status_code == 400 and r.json()["detail"] == "that transfer is not allowed"


def test_a_wrong_mpin_is_refused_first(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db)
    r = _transfer(client, org_id, W("credit"), W("main"), "1", mpin="000000")
    assert r.status_code == 401 and r.json()["attempts_left"] == 4


# --------------------------------------------------------- wallet to account


def test_account_pairs_need_the_linked_account(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db)
    r = _transfer(client, org_id, W("main"), A(1001), "10")
    assert r.status_code == 409 and r.json()["detail"] == "no account linked yet"
    _, _, seed = org_client
    seed(1001, role="slave")
    seed(1002, role="slave")
    link(db, org_id, investor["id"], 1001)
    _state(client, {1001: {"balance": 0.0, "equity": 0.0, "open_pnl": 0.0, "positions": []}})
    r = _transfer(client, org_id, W("main"), A(1002), "10")
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = _transfer(client, org_id, A(1002), W("main"), "10")
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    assert _transfer(client, org_id, W("main"), A(1001), "10").status_code == 201


def test_main_to_account_holds_the_amount_and_caps_at_available(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    r = _transfer(client, org_id, W("main"), A(1001), "1000.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (1000.00)"
    r = _transfer(client, org_id, W("main"), A(1001), "600")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "requested" and body["done_at"] is None
    assert body["target"] == {"kind": "account", "account_id": 1001} or (
        body["target"]["kind"] == "account" and body["target"]["account_id"] == 1001)
    assert body["equity_at_request"] is None and body["equity_verified"] is False
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 1000.0, "on_hold": 600.0, "available": 400.0}
    assert _entries(db, org_id, investor["id"])[-1][2] == "adjustment", "nothing settled yet"
    r = _transfer(client, org_id, W("main"), A(1001), "500")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (400.00)"
    severity, payload = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_transfer_requested"
    assert payload["summary"] == (
        "Transfer request: 600.00 USD from My wallet to trading account 1001 from inv@example.com")

    r = client.post(f"/api/orgs/{org_id}/investor/transfers/{body['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0
    r = client.post(f"/api/orgs/{org_id}/investor/transfers/{body['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "transfer is already cancelled"
    assert _events(db, org_id)[-1][1]["action"] == "investor_transfer_cancelled"


# --------------------------------------------------------- account to wallet


def test_account_to_main_caps_at_the_accounts_available_equity(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="5120.5")
    r = _transfer(client, org_id, A(1001), W("main"), "100")
    assert r.status_code == 201
    assert r.json()["equity_at_request"] == 5120.5 and r.json()["equity_verified"] is True
    assert r.json()["status"] == "requested"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0, "the account holds it, not main"
    r = _transfer(client, org_id, A(1001), W("main"), "5020.51")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the account's available equity (5020.50)"
    assert _transfer(client, org_id, A(1001), W("main"), "5020.50").status_code == 201


def test_unknown_equity_is_accepted_but_flagged(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001)
    r = _transfer(client, org_id, A(1001), W("main"), "99999")
    assert r.status_code == 201
    assert r.json()["equity_verified"] is False and r.json()["equity_at_request"] is None


def test_last_known_equity_caps_but_is_never_verified(org_client, make_user, login_as, db):
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
    r = _transfer(client, org_id, A(int(aid)), W("main"), "99999")
    assert r.status_code == 400 and "4990.25" in r.json()["detail"]
    r = _transfer(client, org_id, A(int(aid)), W("main"), "1000")
    assert r.status_code == 201
    assert r.json()["equity_at_request"] == 4990.25 and r.json()["equity_verified"] is False


# ------------------------------------------------------------------- admin


def test_admin_approves_then_marks_done_and_main_is_debited(org_client, make_user, login_as, db,
                                                            monkeypatch):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    tr = _transfer(client, org_id, W("main"), A(1001), "600").json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    queue = client.get(f"/api/orgs/{org_id}/transfers?status=requested").json()
    assert [t["id"] for t in queue] == [tr["id"]] and queue[0]["email"] == "inv@example.com"

    r = _decide(client, org_id, tr["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decided_at"] is not None and r.json()["done_at"] is None
    assert _figures(db, org_id, investor["id"])["on_hold"] == 600.0, "approved still holds"
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["done_at"] is not None and r.json()["done_by"] is not None
    assert _entries(db, org_id, investor["id"])[-1] == (
        "main", -600.0, "transfer", "transfers", tr["id"])
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 400.0, "on_hold": 0.0, "available": 400.0}
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 409 and r.json()["detail"] == "transfer is already done"
    r = _decide(client, org_id, tr["id"], "rejected", "no")
    assert r.status_code == 409
    assert len([e for e in _entries(db, org_id, investor["id"]) if e[2] == "transfer"]) == 1
    actions = [p["action"] for _, p in _events(db, org_id)]
    assert actions[-3:] == ["investor_transfer_requested", "investor_transfer_decided",
                            "investor_transfer_decided"]
    assert fake.sent == [("inv@example.com", "Your transfer of 600.00 USD was approved"),
                         ("inv@example.com", "Your transfer of 600.00 USD was done")]


def test_account_to_main_done_credits_main(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="800")
    tr = _transfer(client, org_id, A(1001), W("main"), "100").json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["decided_by"] is not None
    assert _entries(db, org_id, investor["id"]) == [
        ("main", 100.0, "transfer", "transfers", tr["id"])]
    assert _figures(db, org_id, investor["id"])["available"] == 100.0


def test_a_rejection_needs_a_note_and_releases_the_hold(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    tr = _transfer(client, org_id, W("main"), A(1001), "300").json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, tr["id"], "rejected")
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = _decide(client, org_id, tr["id"], "paid")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved, done or rejected"
    assert _decide(client, org_id, 999, "approved").status_code == 404
    r = _decide(client, org_id, tr["id"], "rejected", "broker account closed")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0
    assert _entries(db, org_id, investor["id"])[-1][2] == "adjustment", "nothing settled"


def test_ten_requests_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0")
    credit(db, org_id, investor["id"], Decimal("20"), wallet="pamm")
    for _ in range(10):
        assert _transfer(client, org_id, W("pamm"), W("main"), "1").status_code == 201
    r = _transfer(client, org_id, W("pamm"), W("main"), "1")
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


def test_investors_only_see_their_own_transfers_and_cannot_work_the_queue(
        org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    tr = _transfer(client, org_id, W("main"), A(1001), "10").json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/transfers").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/transfers/{tr['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Transfer not found"
    assert _decide(client, org_id, tr["id"], "approved").status_code == 403
    assert client.get(f"/api/orgs/{org_id}/transfers").status_code == 403


# ------------------------------------------------------ ledger lock wiring
#
# Controller ruling on Task 9: every transaction that writes wallet_entries
# takes lock_investor_ledger, keyed on the INVESTOR, as its first statement,
# and nothing is awaited while it is held. These are the same call-through
# spy checks as test_portal_withdrawals.py's ledger-lock tests -- not a true
# concurrency test (see test_portal_common.py for that): they prove the
# route path calls the helper, with the right (org_id, user_id), through the
# `portal_common` module the routers resolve it from (`pc` in both
# portal_investor.py and portal_admin.py).


def test_instant_wallet_transfer_takes_the_investors_ledger_lock(
        org_client, make_user, login_as, db, monkeypatch):
    """pamm -> main is instant (both ends are wallets): the INSERT and the
    two settle() calls all run inside the one locked transaction."""
    from api import portal_common as pc

    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0")
    credit(db, org_id, investor["id"], Decimal("300"), wallet="pamm")

    calls = []
    real_lock = pc.lock_investor_ledger

    def spy(conn, org_id_, user_id_):
        calls.append((org_id_, user_id_))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "lock_investor_ledger", spy)
    r = _transfer(client, org_id, W("pamm"), W("main"), "120.50")
    assert r.status_code == 201, r.text
    assert calls == [(org_id, investor["id"])]


def test_main_to_account_transfer_takes_the_investors_ledger_lock(
        org_client, make_user, login_as, db, monkeypatch):
    """main -> account has no equity to look up (the account is the
    TARGET, not the source): the cap check against the wallet, the hourly
    check and the INSERT all run inside the one locked transaction."""
    from api import portal_common as pc

    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")

    calls = []
    real_lock = pc.lock_investor_ledger

    def spy(conn, org_id_, user_id_):
        calls.append((org_id_, user_id_))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "lock_investor_ledger", spy)
    r = _transfer(client, org_id, W("main"), A(1001), "600")
    assert r.status_code == 201, r.text
    assert calls == [(org_id, investor["id"])]


def test_account_to_main_transfer_takes_the_lock_after_the_equity_lookup(
        org_client, make_user, login_as, db, monkeypatch):
    """account -> main calls equity_for -- a network round trip to the
    copier -- BEFORE the transaction opens, per the ruling: nothing may be
    awaited while lock_investor_ledger is held. Both equity_for and
    lock_investor_ledger are spied through the same `portal_common` module
    the route resolves them from, and the shared `order` list records which
    one runs first: equity_for must appear before lock_investor_ledger."""
    from api import portal_common as pc

    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="5120.5")

    order = []
    real_equity_for = pc.equity_for
    real_lock = pc.lock_investor_ledger

    async def equity_spy(request, conn, org_id_, account_id_):
        order.append(("equity_for", org_id_, account_id_))
        return await real_equity_for(request, conn, org_id_, account_id_)

    def lock_spy(conn, org_id_, user_id_):
        order.append(("lock_investor_ledger", org_id_, user_id_))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "equity_for", equity_spy)
    monkeypatch.setattr(pc, "lock_investor_ledger", lock_spy)
    r = _transfer(client, org_id, A(1001), W("main"), "100")
    assert r.status_code == 201, r.text
    assert [c[0] for c in order] == ["equity_for", "lock_investor_ledger"], (
        "equity_for (a network call) must complete before the lock is taken")
    assert order[0][1:] == (org_id, 1001)
    assert order[1][1:] == (org_id, investor["id"])


def test_decide_transfer_done_takes_the_lock_for_the_investor_not_the_admin(
        org_client, make_user, login_as, db, monkeypatch):
    """decide_transfer's `done` path settles both legs; the lock must be
    keyed on the transfer's investor, never on the admin who is deciding
    it."""
    from api import portal_common as pc

    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="800")
    tr = _transfer(client, org_id, A(1001), W("main"), "100").json()
    login_as(client, ADMIN)

    calls = []
    real_lock = pc.lock_investor_ledger

    def spy(conn, org_id_, user_id_):
        calls.append((org_id_, user_id_))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "lock_investor_ledger", spy)
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 200, r.text
    assert calls == [(org_id, investor["id"])], "locked on the investor, not the admin"


def test_removing_the_linked_mt5_account_keeps_its_transfers_with_no_account(
        org_client, make_user, login_as, db):
    """transfers.*_account_id are ON DELETE SET NULL, and the account-side
    CHECKs allow that NULL. Removing an MT5 account that transfers name
    succeeds (it used to be a 500 from the CHECK) and both rows stay
    readable by the desk and the investor, the account end naming none."""
    from conftest import seed_mt5
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    account_id = seed_mt5(db, org_id, "mt5_transfer-test-key-0123456789abcdefghijk")
    link(db, org_id, investor["id"], account_id)
    with psycopg.connect(db, autocommit=True) as conn:
        (into,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
            "target_account_id, amount) VALUES (%s, %s, 'wallet', 'main', 'account', %s, 10) "
            "RETURNING id", (org_id, investor["id"], account_id)).fetchone()
        (out,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_account_id, "
            "target_kind, target_wallet, amount, status, done_at) "
            "VALUES (%s, %s, 'account', %s, 'wallet', 'main', 20, 'done', now()) RETURNING id",
            (org_id, investor["id"], account_id)).fetchone()

    r = client.delete(f"/api/orgs/{org_id}/mt5/accounts/{account_id}", headers=csrf(client))

    assert r.status_code == 200, r.text
    desk = {t["id"]: t for t in client.get(f"/api/orgs/{org_id}/transfers").json()}
    assert desk[into]["source"] == W("main")
    assert desk[into]["target"] == {"kind": "account", "account_id": None}
    assert desk[out]["source"] == {"kind": "account", "account_id": None}
    assert desk[out]["target"] == W("main")
    login_as(client, investor)
    mine = client.get(f"/api/orgs/{org_id}/investor/transfers")
    assert mine.status_code == 200
    assert sorted(t["id"] for t in mine.json()) == sorted([into, out])


def test_a_transfer_whose_account_was_removed_cannot_be_done(org_client, make_user, db):
    """Account removal NULLs the transfer's account end. Marking it done
    would settle against nothing, so done is refused; approve and reject
    still work, and the audit names the end 'trading account (removed)'."""
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (into,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
            "amount) VALUES (%s, %s, 'wallet', 'main', 'account', 10) RETURNING id",
            (org_id, investor["id"])).fetchone()
        (out,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, target_kind, target_wallet, "
            "amount) VALUES (%s, %s, 'account', 'wallet', 'main', 20) RETURNING id",
            (org_id, investor["id"])).fetchone()
    removed = "the trading account was removed; reject this transfer instead"
    for tr_id in (into, out):
        r = _decide(client, org_id, tr_id, "done")
        assert r.status_code == 409 and r.json()["detail"] == removed
    assert _decide(client, org_id, into, "approved").status_code == 200
    r = _decide(client, org_id, into, "done")
    assert r.status_code == 409 and r.json()["detail"] == removed
    assert _decide(client, org_id, into, "rejected", "account gone").status_code == 200
    assert _decide(client, org_id, out, "rejected", "account gone").status_code == 200
    decided = [p for _, p in _events(db, org_id) if p["action"] == "investor_transfer_decided"]
    assert decided[-2]["target"] == "trading account (removed)"
    assert decided[-1]["source"] == "trading account (removed)"
    assert _entries(db, org_id, investor["id"]) == []


def test_an_account_removed_after_the_read_still_blocks_done(
        org_client, make_user, login_as, db, monkeypatch):
    """The removal can land between decide_transfer's read and its UPDATE;
    the guard lives in the UPDATE, so done is still refused and nothing
    settles."""
    from api import portal_common as pc

    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="800")
    tr = _transfer(client, org_id, A(1001), W("main"), "100").json()
    login_as(client, ADMIN)
    real_lock = pc.lock_investor_ledger

    def remove_then_lock(conn, org_id_, user_id_):
        with psycopg.connect(db, autocommit=True) as other:
            other.execute("UPDATE transfers SET source_account_id = NULL WHERE id = %s",
                          (tr["id"],))
        return real_lock(conn, org_id_, user_id_)

    monkeypatch.setattr(pc, "lock_investor_ledger", remove_then_lock)
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "the trading account was removed; reject this transfer instead"
    assert _entries(db, org_id, investor["id"]) == []


def test_money_ref_label_names_a_removed_account():
    from api.routes.portal_investor import money_ref_label
    assert money_ref_label("account", None) == "trading account (removed)"
    assert money_ref_label("account", 1001) == "trading account 1001"
    assert money_ref_label("main", None) == "My wallet"
