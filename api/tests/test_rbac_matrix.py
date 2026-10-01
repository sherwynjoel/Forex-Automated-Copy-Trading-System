"""Spec §4's permission matrix, executed literally: every org-scoped endpoint
is called as every role, plus non-member and anonymous.

ok() = any status that proves AUTHORIZATION passed (2xx, or a 4xx/5xx that
can only come from AFTER the role check — 400 validation, 404 for a
nonexistent account, 409 for a row whose state forbids the change, 502
copier). 401/403/404-membership are the denials under test. Endpoints listed
with a seeded account id 100 where needed.

The mutating portal rows aim at seeded rows with deterministic ids (the db
fixture TRUNCATEs with RESTART IDENTITY, and every parametrised case gets its
own fixture): payment method 1, the investor's approved payout destination 1
and pending destination 2, deposit 1, withdrawal 1, transfer 1. They are
written so that the FIRST allowed role really performs the change and the
later ones get a 409 or 400 from the row's state or the body — never a
403/404 — so what the row proves is authorization, never business rules.
`{investor}` in a path is the investor member's user id, substituted per test.
Investor routes take `require_investor` ("investor_only" below): only the
investor role passes them, and desk members get 403 (spec §14).
"""
import psycopg
import pytest
from psycopg.types.json import Jsonb
from portal_helpers import add_method, approved_destination

ROLES = ["investor", "viewer", "admin"]

MPIN = "123456"

# (method, path_tail, body, min_role)
MATRIX = [
    ("GET",    "accounts",                       None,                          "viewer"),
    ("GET",    "accounts/100/details",           None,                          "viewer"),
    ("GET",    "accounts/100/history/deals?from=0&to=1", None,                  "viewer"),
    ("GET",    "accounts/100/symbols",           None,                          "viewer"),
    ("GET",    "accounts/100/margin-estimate?symbol=EURUSD&volume_lots=0.01",
                                                 None,                          "viewer"),
    ("GET",    "accounts/100/trendbars?symbol=EURUSD&period=M1&from=0&to=1",
                                                 None,                          "viewer"),
    ("GET",    "accounts/100/positions/1/deals?from=0&to=1", None,              "viewer"),
    ("GET",    "accounts/100/analytics",         None,                          "viewer"),
    ("GET",    "overview",                       None,                          "viewer"),
    ("GET",    "settings",                       None,                          "viewer"),
    ("GET",    "state",                          None,                          "viewer"),
    ("GET",    "events",                         None,                          "viewer"),
    ("GET",    "members",                        None,                          "viewer"),
    ("POST",   "orders",                         {"account_id": 100, "symbol": "EURUSD",
                                                  "side": "BUY", "order_type": "MARKET",
                                                  "volume_lots": 0.01},         "admin"),
    ("POST",   "positions/close",                {"account_id": 100, "position_id": 1}, "admin"),
    ("POST",   "orders/cancel",                  {"account_id": 100, "order_id": 1},    "admin"),
    ("PUT",    "settings",                       {"copying_enabled": False},     "admin"),
    ("POST",   "control/pause",                  {},                             "admin"),
    ("POST",   "control/resume",                 {},                             "admin"),
    ("POST",   "control/resync",                 {},                             "admin"),
    ("POST",   "control/close-all",              {},                             "admin"),
    ("POST",   "drift/dismiss",                  {"id": "abc"},                  "admin"),
    ("PATCH",  "accounts/100",                   {"enabled": True},              "admin"),
    ("DELETE", "accounts/100/connection",        None,                           "admin"),
    ("GET",    "accounts/100/symbol-aliases",    None,                           "admin"),
    ("PUT",    "accounts/100/symbol-aliases",    {"aliases": {}},                "admin"),
    ("POST",   "mt5/accounts",                   {"nickname": "VPS"},            "admin"),
    ("POST",   "mt5/accounts/100/key",           None,                           "admin"),
    ("GET",    "oauth/connect",                  None,                           "admin"),
    ("POST",   "invites",                        {"role": "viewer"},             "admin"),
    ("GET",    "invites",                        None,                           "admin"),
    ("PATCH",  "",                               {"name": "Renamed"},            "admin"),
    ("DELETE", "",                               None,                           "admin"),
    # ---- investor portal, investor side
    ("GET",    "investor/summary",               None,                          "investor_only"),
    ("GET",    "investor/payment-methods",       None,                          "investor_only"),
    ("GET",    "investor/deposits",              None,                          "investor_only"),
    ("GET",    "investor/payout-destinations",   None,                          "investor_only"),
    ("GET",    "investor/withdrawals",           None,                          "investor_only"),
    ("GET",    "investor/transfers",             None,                          "investor_only"),
    ("GET",    "investor/wallet-entries",        None,                          "investor_only"),
    ("GET",    "investor/positions",             None,                          "investor_only"),
    ("POST",   "investor/deposits",              {"method_id": 1, "amount": "10",
                                                  "reference": "matrix-filed",
                                                  "target": "wallet"},          "investor_only"),
    ("POST",   "investor/payout-destinations",   {"kind": "crypto", "nickname": "Matrix",
                                                  "details": {"coin": "USDT", "network": "TRC20",
                                                              "address": "TMatrix"},
                                                  "mpin": MPIN},                "investor_only"),
    ("POST",   "investor/withdrawals",           {"destination_id": 1, "amount": "0",
                                                  "mpin": MPIN},                "investor_only"),
    ("POST",   "investor/transfers",             {"source": {"kind": "wallet", "wallet": "main"},
                                                  "target": {"kind": "wallet", "wallet": "pamm"},
                                                  "amount": "10", "mpin": MPIN}, "investor_only"),
    # ---- investor portal, admin side
    ("GET",    "investors",                      None,                          "admin"),
    ("GET",    "investors/{investor}/wallet-entries", None,                     "admin"),
    ("PUT",    "investors/{investor}/account",   {"account_id": None},           "admin"),
    ("POST",   "investors/{investor}/adjustments", {"wallet": "main", "amount": "1",
                                                    "note": "matrix", "mpin": MPIN}, "admin"),
    ("GET",    "payment-methods",                None,                          "admin"),
    ("POST",   "payment-methods",                {"kind": "crypto", "label": "BTC",
                                                  "details": {"coin": "BTC", "network": "BTC",
                                                              "address": "bc1matrix"}}, "admin"),
    ("PATCH",  "payment-methods/1",              {"label": "Renamed"},           "admin"),
    ("DELETE", "payment-methods/1",              None,                           "admin"),
    ("GET",    "portal-settings",                None,                          "admin"),
    ("PUT",    "portal-settings",                {"withdrawal_min": "0",
                                                  "withdrawal_fee_pct": "0"},   "admin"),
    ("GET",    "requests/summary",               None,                          "admin"),
    ("GET",    "deposits",                       None,                          "admin"),
    ("GET",    "withdrawals",                    None,                          "admin"),
    ("GET",    "transfers",                      None,                          "admin"),
    ("GET",    "payout-destinations",            None,                          "admin"),
    ("POST",   "deposits/1/decision",            {"status": "rejected", "note": "matrix"}, "admin"),
    ("POST",   "withdrawals/1/decision",         {"status": "rejected", "note": "matrix"}, "admin"),
    ("POST",   "withdrawals/1/paid",             {"txid": "matrix"},             "admin"),
    ("POST",   "transfers/1/decision",           {"status": "rejected", "note": "matrix"}, "admin"),
    ("POST",   "payout-destinations/2/decision", {"status": "rejected", "note": "matrix"}, "admin"),
]

RANK = {"investor": -1, "viewer": 0, "admin": 1}


@pytest.fixture
def matrix_org(app_client, make_user, make_org, db, login_as):
    """One org, one user per role, one seeded master account 100, and one
    open row of every portal request type belonging to the investor."""
    users = {role: make_user(email=f"{role}@example.com") for role in ROLES}
    org_id = make_org(name="Matrix", members=[(users[r], r) for r in ROLES])
    outsider = make_user(email="outsider@example.com")
    investor_id = users["investor"]["id"]
    method_id = add_method(db, org_id)                                    # id 1
    approved_id = approved_destination(db, org_id, investor_id)           # id 1
    with psycopg.connect(db, autocommit=True) as conn:
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (org_id,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role)
               VALUES (100, %s, %s, 100, false, 'master')""",
            (connection_id, org_id))
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'Pending', %s)",                        # id 2
            (org_id, investor_id,
             Jsonb({"coin": "USDT", "network": "TRC20", "address": "TPending"})))
        conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, "
            "amount, reference) VALUES (%s, %s, %s, 'crypto', 'USDT on TRC20', 100, "
            "'matrix-seeded')", (org_id, investor_id, method_id))                # id 1
        conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount) "
            "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…st', 50, 0, 50)",
            (org_id, investor_id, approved_id))                                 # id 1
        conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
            "target_account_id, amount) VALUES (%s, %s, 'wallet', 'main', 'account', 100, 10)",
            (org_id, investor_id))                                              # id 1
    return app_client, org_id, users, outsider


def _call(client, method, org_id, tail, body):
    url = f"/api/orgs/{org_id}/{tail}" if tail else f"/api/orgs/{org_id}"
    headers = {"X-CSRF-Token": client.cookies.get("csrf") or ""}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    if method == "GET":
        return client.get(url, follow_redirects=False)
    return getattr(client, method.lower())(url, **kwargs)


@pytest.mark.parametrize("method,tail,body,min_role", MATRIX,
                         ids=[f"{m} {t or '(org)'}" for m, t, _, _ in MATRIX])
def test_role_thresholds(matrix_org, login_as, method, tail, body, min_role):
    client, org_id, users, outsider = matrix_org
    tail = tail.replace("{investor}", str(users["investor"]["id"]))

    # Anonymous: always 401 (or 403 from CSRF middleware on mutations — both
    # prove denial before any org logic).
    client.cookies.clear()
    r = _call(client, method, org_id, tail, body)
    assert r.status_code in (401, 403), f"anonymous got {r.status_code}"

    # Non-member: 404 — org existence never leaks.
    login_as(client, outsider)
    r = _call(client, method, org_id, tail, body)
    assert r.status_code == 404, f"outsider got {r.status_code}"

    # DELETE rows are destructive — only probe DENIED roles for them, and
    # prove the allowed role separately in test_destructive_rows_allowed to
    # keep the fixture intact per param.
    destructive = (method == "DELETE")
    for role in ROLES:
        allowed = (role == "investor" if min_role == "investor_only"
                   else RANK[role] >= RANK[min_role])
        if destructive and allowed:
            continue
        login_as(client, users[role])
        r = _call(client, method, org_id, tail, body)
        if allowed:
            assert r.status_code not in (401, 403, 404), \
                f"{role} should pass {method} {tail}, got {r.status_code}"
        else:
            assert r.status_code == 403, \
                f"{role} should be 403 on {method} {tail}, got {r.status_code}"


def test_destructive_rows_allowed(matrix_org, login_as):
    """The allowed-role half of the destructive rows, run last against a
    dedicated fixture instance. The payment method is still used by the
    pending deposit, so its DELETE answers 409 -- authorization passed.
    Disconnecting account 100's grant must succeed although transfer 1
    targets it (transfers.target_account_id is ON DELETE SET NULL)."""
    client, org_id, users, _ = matrix_org
    login_as(client, users["admin"])
    r = _call(client, "DELETE", org_id, "payment-methods/1", None)
    assert r.status_code == 409
    r = _call(client, "DELETE", org_id, "accounts/100/connection", None)
    assert r.status_code == 200
    # Transfer 1 targeted account 100; the grant's accounts cascade away and
    # the transfer stays, its account end now naming no account.
    (transfer,) = client.get(f"/api/orgs/{org_id}/transfers").json()
    assert transfer["id"] == 1
    assert transfer["target"] == {"kind": "account", "account_id": None}
    r = _call(client, "DELETE", org_id, "", None)
    assert r.status_code == 204


def test_a_half_session_is_refused_on_every_matrix_route(matrix_org):
    """Email+password alone opens nothing: before the MPIN every org route,
    desk or investor, answers 401 MPIN required."""
    client, org_id, users, _ = matrix_org
    admin = users["admin"]
    client.cookies.clear()
    r = client.post("/api/login", json={"email": admin["email"], "password": admin["password"]})
    assert r.status_code == 204
    for method, tail, body, _min_role in MATRIX:
        if method == "DELETE":
            continue  # destructive rows are proven denied by the 401 below on GET/POST too
        tail = tail.replace("{investor}", str(users["investor"]["id"]))
        r = _call(client, method, org_id, tail, body)
        assert r.status_code == 401, f"{method} {tail} -> {r.status_code}"
        assert r.json()["detail"] == "MPIN required", f"{method} {tail}"
