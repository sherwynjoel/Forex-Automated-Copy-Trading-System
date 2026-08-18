"""Spec §4's permission matrix, executed literally: every org-scoped endpoint
is called as every role, plus non-member and anonymous.

ok() = any status that proves AUTHORIZATION passed (2xx, or a 4xx/5xx that
can only come from AFTER the role check — 400 validation, 404 for a
nonexistent account, 502 copier). 401/403/404-membership are the denials
under test. Endpoints listed with a seeded account id 100 where needed.
"""
import psycopg
import pytest

ROLES = ["viewer", "trader", "admin", "owner"]

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
    ("GET",    "oauth/connect",                  None,                           "admin"),
    ("POST",   "invites",                        {"role": "viewer"},             "admin"),
    ("GET",    "invites",                        None,                           "admin"),
    ("PATCH",  "",                               {"name": "Renamed"},            "owner"),
    ("DELETE", "",                               None,                           "owner"),
]

RANK = {"viewer": 0, "trader": 1, "admin": 2, "owner": 3}


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
