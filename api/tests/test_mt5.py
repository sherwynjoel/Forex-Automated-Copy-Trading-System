"""The MT5 bridge's api side: the terminal door, the operator endpoints and
the EA download. Each test is named for the failure it prevents.

The door is the webhook route's discipline applied to a terminal that polls
four times a second: size before parsing, the key before any database read,
a dedicated connection only past the key, and nothing secret in any row or
log line."""
import hashlib
import json
import logging

import httpx
import psycopg
import pytest
from starlette.testclient import TestClient

from conftest import default_mock_callback, seed_mt5
from api import netaddr
from api.routes import mt5 as m5

KEY = "mt5_test-key-value-0123456789abcdefghijklmn"
WRONG = "mt5_not-the-key-0123456789abcdefghijklmnopq"


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _link(db, account_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT last_seen_at, last_ip, balance, equity FROM mt5_links "
            "WHERE account_id = %s", (account_id,)).fetchone()


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT account_id, payload, actor_email FROM events "
            "WHERE org_id = %s AND payload->>'action' = %s ORDER BY id",
            (org_id, action)).fetchall()


def _copier(client, *, sync=None, hello=None, raise_on=None):
    """Route copier calls through a recorder; `raise_on` maps a URL fragment
    to an exception to raise."""
    calls = []

    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        body = json.loads(request.content.decode()) if request.content else {}
        calls.append((url, body))
        for fragment, exc in (raise_on or {}).items():
            if fragment in url:
                raise exc
        if "/mt5/sync" in url:
            return sync or httpx.Response(
                200, content=b"OK\t1757203200400\t250\nCMD\t88\tclose\t669607966\t0",
                headers={"content-type": "text/plain; charset=utf-8"})
        if "/mt5/hello" in url:
            return hello or httpx.Response(200, json={"last_deal_ticket": 700001})
        return httpx.Response(200, json={"status": "ok"})

    client.app.state.mock_transport.set_callback(callback)
    return calls


def _sync_body(**over):
    body = {"v": 1, "seq": 1042, "ts": 1757203200123, "balance": 9784.04,
            "equity": 9790.10, "margin": 120.5, "margin_free": 9669.6,
            "positions": [], "orders": [], "deals": [], "acks": []}
    body.update(over)
    return body


def _hello_body(**over):
    body = {"v": 1, "ea": "1.0.0", "build": 4400, "login": 12345678,
            "broker": "XYZ Ltd", "server": "XYZ-Live3", "currency": "USD",
            "hedging": True, "trade_mode": "demo", "leverage": 500,
            "symbols": [], "chunk": 1, "chunks": 1}
    body.update(over)
    return body


def _post(client, path, body=None, key=KEY, raw=None):
    """No session, no CSRF header -- exactly what the terminal sends. The
    key travels in the header, never the URL."""
    headers = {} if key is None else {m5.KEY_HEADER: key}
    if raw is not None:
        headers["Content-Type"] = "application/json"
        return client.post(path, content=raw, headers=headers)
    return client.post(path, json=body, headers=headers)


def _sync(client, body=None, key=KEY, raw=None):
    return _post(client, "/api/mt5/sync", body if body is not None else _sync_body(),
                 key=key, raw=raw)


def _hello(client, body=None, key=KEY, raw=None):
    return _post(client, "/api/mt5/hello", body if body is not None else _hello_body(),
                 key=key, raw=raw)


# ====================================================== CSRF exemption


def test_the_terminal_door_is_not_blocked_by_the_csrf_middleware(app_client):
    """A terminal has no session and no CSRF cookie. Whatever the route
    answers (404 before it exists, 400 for a missing key once it does), it
    must never be the middleware's 403."""
    app_client.cookies.clear()
    r = app_client.post("/api/mt5/sync", json={"v": 1})
    assert r.status_code != 403
    assert "CSRF" not in r.text


def test_the_exemption_is_the_exact_prefix_not_a_lookalike(app_client):
    """/api/mt5x/ must not ride on /api/mt5/: the trailing slash is
    load-bearing, exactly as it is for /api/webhooks/."""
    app_client.cookies.clear()
    r = app_client.post("/api/mt5x/sync", json={"v": 1})
    assert r.status_code == 403
    assert r.json()["detail"] == "Missing CSRF token"


# ====================================================== the front door


def test_body_too_large_is_refused_before_the_key_is_looked_at(org_client, db):
    """Size first: an oversized body with a wrong key is 413, not 401, and
    costs no bucket slot and no database read. The caps are per route --
    hello (a symbol list) is allowed 512 KB, sync 256 KB."""
    client, org_id, seed = org_client
    seed_mt5(db, org_id, KEY)

    r = _sync(client, key=WRONG, raw=b"x" * (m5.SYNC_MAX_BODY_BYTES + 1))
    assert r.status_code == 413
    assert r.json() == {"status": "rejected", "reason": "body too large"}
    assert _hello(client, key=WRONG, raw=b"x" * (m5.HELLO_MAX_BODY_BYTES + 1)).status_code == 413
    assert "mt5-badkey:testclient" not in dict(client.app.state.rate_limiter.attempts)
    # under hello's cap but over sync's: hello reads the key, sync does not
    assert _hello(client, key=WRONG, raw=b"x" * (m5.SYNC_MAX_BODY_BYTES + 1)).status_code == 401
    assert len(client.app.state.rate_limiter.attempts["mt5-badkey:testclient"]) == 1


def test_a_missing_key_header_is_400_and_the_url_is_never_read(org_client, db):
    client, org_id, seed = org_client
    seed_mt5(db, org_id, KEY)
    calls = _copier(client)

    r = client.post(f"/api/mt5/sync?key={KEY}", json=_sync_body())

    assert r.status_code == 400
    assert r.json() == {"status": "rejected", "reason": "missing X-MirrorFleet-Key header"}
    assert calls == []


def test_an_unknown_key_is_401_and_fills_a_per_source_bucket(org_client, db, caplog):
    """Eleven wrong keys from one address: every answer is the same 401
    (nothing to learn from the shape), the bucket holds ten slots, the
    warning is logged at most ten times, and the key itself is in no line."""
    client, org_id, seed = org_client
    seed_mt5(db, org_id, KEY)
    calls = _copier(client)
    caplog.set_level(logging.WARNING, logger="api.routes.mt5")

    answers = [_sync(client, key=WRONG) for _ in range(11)]

    assert all(r.status_code == 401 for r in answers)
    assert answers[0].json() == {"status": "rejected", "reason": "unknown key"}
    assert len(client.app.state.rate_limiter.attempts["mt5-badkey:testclient"]) == 10
    assert sum("unknown key" in rec.getMessage() for rec in caplog.records) == 10
    assert WRONG not in caplog.text
    assert calls == []


def test_a_valid_sync_is_proxied_with_the_account_and_passed_through(org_client, db):
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    calls = _copier(client)

    r = _sync(client)

    assert r.status_code == 200, r.text
    assert r.text == "OK\t1757203200400\t250\nCMD\t88\tclose\t669607966\t0"
    assert r.headers["content-type"].startswith("text/plain")
    (url, sent), = calls
    assert url == "http://copier.test/mt5/sync"
    assert sent == {"account_id": account_id, "org_id": org_id, "report": _sync_body()}


def test_a_hello_is_proxied_and_its_json_passed_through(org_client, db):
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    calls = _copier(client)

    r = _hello(client)

    assert r.status_code == 200, r.text
    assert r.json() == {"last_deal_ticket": 700001}
    assert r.headers["content-type"].startswith("application/json")
    (url, sent), = calls
    assert url == "http://copier.test/mt5/hello"
    assert sent == {"account_id": account_id, "org_id": org_id, "report": _hello_body()}


def test_the_door_works_with_no_cookies_at_all(org_client, db):
    client, org_id, seed = org_client
    seed_mt5(db, org_id, KEY)
    _copier(client)
    client.cookies.clear()
    assert _sync(client).status_code == 200


def test_a_non_json_body_is_400_only_after_the_key_check(org_client, db):
    client, org_id, seed = org_client
    seed_mt5(db, org_id, KEY)
    calls = _copier(client)

    assert _sync(client, key=WRONG, raw=b"not json").status_code == 401
    r = _sync(client, raw=b"not json")
    assert r.status_code == 400
    assert r.json() == {"status": "rejected", "reason": "the report must be a JSON object"}
    assert _sync(client, raw=b"[1, 2]").status_code == 400
    assert calls == []


def test_the_first_sync_touches_the_link_and_the_next_ones_are_throttled(
        org_client, db, monkeypatch):
    """last_seen_at / last_ip / balance / equity are written at most once
    per 10 s per account: four syncs a second must not be four writes."""
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    _copier(client)
    assert _link(db, account_id) == (None, None, None, None)

    assert _sync(client, _sync_body(balance=100.0, equity=101.0)).status_code == 200
    seen_at, ip, balance, equity = _link(db, account_id)
    assert seen_at is not None and ip == "testclient" and (balance, equity) == (100.0, 101.0)

    assert _sync(client, _sync_body(balance=200.0, equity=201.0)).status_code == 200
    assert _link(db, account_id) == (seen_at, "testclient", 100.0, 101.0)

    monkeypatch.setattr(m5, "LAST_SEEN_THROTTLE_S", 0.0)
    assert _sync(client, _sync_body(balance=300.0, equity=301.0)).status_code == 200
    later, _, balance, equity = _link(db, account_id)
    assert later > seen_at and (balance, equity) == (300.0, 301.0)


def test_a_hello_touches_last_seen_but_carries_no_balance(org_client, db):
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    _copier(client)

    assert _hello(client).status_code == 200

    seen_at, ip, balance, equity = _link(db, account_id)
    assert seen_at is not None and ip == "testclient"
    assert (balance, equity) == (None, None)


def test_a_removed_account_or_rotated_key_is_the_same_401(org_client, db):
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    _copier(client)
    assert _sync(client).status_code == 200

    new_key = "mt5_rotated-key-value-0123456789abcdefghijk"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE mt5_links SET key_hash = %s WHERE account_id = %s",
                     (hashlib.sha256(new_key.encode()).hexdigest(), account_id))
    assert _sync(client).status_code == 401
    assert _sync(client, key=new_key).status_code == 200

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("DELETE FROM accounts WHERE ctid_trader_account_id = %s", (account_id,))
    r = _sync(client, key=new_key)
    assert r.status_code == 401
    assert r.json() == {"status": "rejected", "reason": "unknown key"}


@pytest.mark.parametrize("failure, reason", [
    ({"raise_on": {"/mt5/": httpx.ConnectError("refused")}}, "copier unreachable"),
    ({"raise_on": {"/mt5/": httpx.ReadTimeout("")}}, "copier unreachable"),
    ({"sync": httpx.Response(500, text="boom"), "hello": httpx.Response(503, text="boom")},
     "copier answered 503"),
    ({"sync": httpx.Response(400, json={"error": "unknown account 1000000000000"}),
      "hello": httpx.Response(400, json={"error": "report: symbols must be a list"})},
     "copier answered 400"),
], ids=["unreachable", "timeout", "5xx", "4xx"])
def test_copier_failure_is_retry_for_sync_and_503_for_hello(org_client, db, failure, reason):
    """Nothing was applied, and a resend is harmless (the report is
    idempotent), so every answer but a 200 maps the same way: RETRY on the
    wire for sync -- the EA backs off to 2 s polls -- and 503 with retry_ms
    for hello. The copier's own 400 (a report it rejected, or an account it
    has not loaded yet) is included: its JSON error is not in the wire
    grammar, so the terminal never sees it -- only the log does."""
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    _copier(client, **failure)

    r = _sync(client)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    status, server_ms, next_poll = r.text.split("\t")
    assert status == "RETRY" and server_ms.isdigit() and next_poll == "2000"

    r = _hello(client)
    assert r.status_code == 503
    assert r.json() == {"status": "failed", "reason": reason, "retry_ms": 2000}
    # a failed proxy is not a report: the link is not touched
    assert _link(db, account_id) == (None, None, None, None)


def test_a_key_inside_an_exception_never_reaches_the_log(org_client, db, caplog):
    client, org_id, seed = org_client
    seed_mt5(db, org_id, KEY)
    _copier(client, raise_on={"/mt5/sync": httpx.ConnectError("refused by " + KEY)})
    caplog.set_level(logging.WARNING, logger="api.routes.mt5")

    assert _sync(client).text.startswith("RETRY\t")

    assert "refused by ***" in caplog.text
    assert KEY not in caplog.text


def test_the_forwarded_address_is_recorded_from_the_container_gateway(
        org_client, db, monkeypatch):
    """Production: Caddy on the host -> 127.0.0.1:8000 -> docker-proxy -> us.
    The peer is the bridge gateway; the terminal's address is in the header
    Caddy appended, and that is what last_ip must say."""
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    _copier(client)
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setattr(netaddr, "_container_gateway", lambda: "172.18.0.1")

    r = TestClient(client.app, client=("172.18.0.1", 40000)).post(
        "/api/mt5/sync", json=_sync_body(),
        headers={m5.KEY_HEADER: KEY, "X-Forwarded-For": "203.0.113.9"})

    assert r.status_code == 200, r.text
    assert _link(db, account_id)[1] == "203.0.113.9"
