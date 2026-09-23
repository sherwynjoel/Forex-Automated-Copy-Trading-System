"""Spec §4's permission matrix, executed literally: every org-scoped endpoint
is called as every role, plus non-member and anonymous.

ok() = any status that proves AUTHORIZATION passed (2xx, or a 4xx/5xx that
can only come from AFTER the role check — 400 validation, 404 for a
nonexistent account, 409 for a row whose state forbids the change, 502
copier). 401/403/404-membership are the denials under test. Endpoints listed
with a seeded account id 100 where needed.

The mutating investor rows aim at the seeded deposit 1 and withdrawal 1
(ids are deterministic: the db fixture TRUNCATEs with RESTART IDENTITY, and
every parametrised case gets its own fixture). They are written so that the
FIRST allowed role really performs the change and the later ones get a 409
from the row's state — never a 403/404 — so what the row proves is
authorization, never business rules. `{investor}` in a path is the investor
member's user id, substituted per test.
"""
import psycopg
import pytest

ROLES = ["investor", "viewer", "trader", "admin", "owner"]

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
                                                  "volume_lots": 0.01},         "trader"),
    ("POST",   "positions/close",                {"account_id": 100, "position_id": 1}, "trader"),
    ("POST",   "orders/cancel",                  {"account_id": 100, "order_id": 1},    "trader"),
    ("PUT",    "settings",                       {"copying_enabled": False},     "admin"),
    ("POST",   "control/pause",                  {},                             "admin"),
    ("POST",   "control/resume",                 {},                             "admin"),
    ("POST",   "control/resync",                 {},                             "admin"),
    ("POST",   "control/close-all",              {},                             "admin"),
    ("POST",   "drift/dismiss",                  {"id": "abc"},                  "admin"),
    ("PATCH",  "accounts/100",                   {"enabled": True},              "admin"),
    ("DELETE", "accounts/100/connection",        None,                           "admin"),
    ("GET",    "accounts/100/symbol-aliases",    None,                           "trader"),
    ("PUT",    "accounts/100/symbol-aliases",    {"aliases": {}},                "admin"),
    ("POST",   "mt5/accounts",                   {"nickname": "VPS"},            "admin"),
    ("POST",   "mt5/accounts/100/key",           None,                           "admin"),
    ("GET",    "oauth/connect",                  None,                           "admin"),
    ("POST",   "invites",                        {"role": "viewer"},             "admin"),
    ("GET",    "invites",                        None,                           "admin"),
    ("PATCH",  "",                               {"name": "Renamed"},            "owner"),
    ("DELETE", "",                               None,                           "owner"),
    ("GET",    "investor/summary",                None,                          "investor"),
    ("GET",    "investor/deposits",               None,                          "investor"),
    ("GET",    "investor/withdrawals",            None,                          "investor"),
    ("GET",    "investor-wallet",                 None,                          "admin"),
    ("GET",    "investors",                       None,                          "admin"),
    ("GET",    "investor-deposits",               None,                          "admin"),
    ("GET",    "investor-withdrawals",            None,                          "admin"),
    ("POST",   "investor/deposits",               {"amount": "10", "coin": "USDT",
                                                   "txid": "matrix-filed"},      "investor"),
    ("POST",   "investor/withdrawals",            {"amount": "10",
                                                   "destination": "TDest"},      "investor"),
    ("PUT",    "investor-wallet",                 {"coin": "USDT", "network": "TRC20",
                                                   "address": "TAddr456"},       "admin"),
    ("PUT",    "investors/{investor}/account",    {"account_id": None},           "admin"),
    ("POST",   "investor-deposits/1/decision",    {"status": "rejected",
                                                   "note": "matrix"},            "admin"),
    ("POST",   "investor-withdrawals/1/decision", {"status": "rejected",
                                                   "note": "matrix"},            "admin"),
    ("POST",   "investor-withdrawals/1/paid",     {"txid": "matrix"},             "admin"),
]

RANK = {"investor": -1, "viewer": 0, "trader": 1, "admin": 2, "owner": 3}


@pytest.fixture
def matrix_org(app_client, make_user, make_org, db, login_as):
    """One org, one user per role, one seeded master account 100."""
    users = {role: make_user(email=f"{role}@example.com") for role in ROLES}
    org_id = make_org(name="Matrix", members=[(users[r], r) for r in ROLES])
    outsider = make_user(email="outsider@example.com")
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
            "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
            "VALUES (%s, 'USDT', 'TRC20', 'TAddr123')", (org_id,))
        # A pending notice and a requested withdrawal belonging to the
        # investor member, so the decision/paid rows of the matrix have a
        # real row to aim at. RESTART IDENTITY makes both ids 1. The
        # withdrawal hangs off an MT5-style account (no cTrader connection)
        # rather than account 100: investor_withdrawals.account_id is ON
        # DELETE RESTRICT, and account 100 is the one
        # `DELETE accounts/100/connection` cascades away.
        (mt5_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', true) "
            "RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute(
            "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
            "VALUES (%s, %s, 100, 'USDT', 'matrix-seeded')",
            (org_id, users["investor"]["id"]))
        conn.execute(
            "INSERT INTO investor_withdrawals (org_id, user_id, account_id, amount, destination) "
            "VALUES (%s, %s, %s, 50, 'TDest')",
            (org_id, users["investor"]["id"], mt5_id))
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

    # DELETE org and connection-delete are destructive — only probe DENIED
    # roles for them, and prove the allowed role separately in
    # test_destructive_rows_allowed to keep the fixture intact per param.
    destructive = (method == "DELETE")
    for role in ROLES:
        allowed = RANK[role] >= RANK[min_role]
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
    dedicated fixture instance."""
    client, org_id, users, _ = matrix_org
    login_as(client, users["admin"])
    r = _call(client, "DELETE", org_id, "accounts/100/connection", None)
    assert r.status_code == 200
    login_as(client, users["owner"])
    r = _call(client, "DELETE", org_id, "", None)
    assert r.status_code == 204
