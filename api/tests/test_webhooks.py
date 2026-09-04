"""TradingView webhook: the only unauthenticated, order-placing route.

Each test below is named for the failure it prevents. The security-negative
ones came out of an adversarial review of the design; the ordering of the
front door, the transaction around dedup, and the classification of copier
failures are all there because a reviewer showed what happened without them.
"""
import hashlib
import json

import httpx
import psycopg
import pytest

from conftest import default_mock_callback
from api.routes import webhooks as wh

SECRET = "tvw_test-secret-value-0123456789"
MASTER = 999
HOOK = "hook_abcdef123456"


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


@pytest.fixture(autouse=True)
def _tradingview_is_the_test_client(monkeypatch):
    """TestClient's peer address is 'testclient'; treat it as TradingView.
    Tests that want an outsider override this."""
    monkeypatch.setattr(wh, "TRADINGVIEW_SOURCE_IPS", frozenset({"testclient"}))


def _arm(db, org_id, *, secret=SECRET, enabled=True, max_lots=1.0,
         max_per_minute=10, max_open=3, hook_id=HOOK):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_webhooks (org_id, hook_id, secret_hash, secret_created_at, "
            "enabled, max_lots, max_per_minute, max_open_positions) "
            "VALUES (%s,%s,%s,now(),%s,%s,%s,%s)",
            (org_id, hook_id, hashlib.sha256(secret.encode()).hexdigest(),
             enabled, max_lots, max_per_minute, max_open))


def _org_settings(db, org_id, **cols):
    with psycopg.connect(db, autocommit=True) as conn:
        for k, v in cols.items():
            conn.execute(f"UPDATE orgs SET {k} = %s WHERE id = %s", (v, org_id))


def _receipts(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT outcome, reason, fingerprint, body_redacted, source_ip "
            "FROM webhook_receipts WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events "
            "WHERE org_id = %s AND payload->>'action' = 'webhook_alert' ORDER BY id",
            (org_id,)).fetchall()


def _copier(client, *, state=None, order=None, close=None, raise_on=None):
    """Route copier calls through a recorder.

    `raise_on` maps a URL fragment to an exception instance to raise, for
    the failure-classification tests.
    """
    calls = []
    state = state if state is not None else {"master_positions": []}

    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        body = json.loads(request.content.decode()) if request.content else {}
        calls.append((url, body))
        for fragment, exc in (raise_on or {}).items():
            if fragment in url:
                raise exc
        if "/state" in url:
            return httpx.Response(200, json=state)
        if "/order" in url:
            return order or httpx.Response(200, json={"status": "submitted", "volume": 100})
        if "/positions/close" in url:
            return close or httpx.Response(200, json={"status": "submitted"})
        if "/resync" in url:
            return httpx.Response(200, json={"status": "resynced", "drift_count": 0})
        return httpx.Response(200, json={"status": "ok"})

    client.app.state.mock_transport.set_callback(callback)
    return calls


def _alert(**over):
    body = {"secret": SECRET, "action": "buy", "symbol": "OANDA:XAUUSD",
            "lots": 0.01, "id": "1693000000"}
    body.update(over)
    return body


def _post(client, body, hook=HOOK, raw=None):
    """No session, no CSRF header -- exactly what TradingView sends."""
    if raw is not None:
        return client.post(f"/api/webhooks/tradingview/{hook}", content=raw,
                           headers={"Content-Type": "text/plain"})
    return client.post(f"/api/webhooks/tradingview/{hook}", json=body)


# ====================================================== the front door


def test_an_outsider_never_reaches_the_database(org_client, db, monkeypatch):
    """The URL is not a secret. Someone holding it must get nothing -- not a
    receipt row, not a bucket slot a real alert could share."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    monkeypatch.setattr(wh, "TRADINGVIEW_SOURCE_IPS", frozenset({"52.89.214.238"}))

    r = _post(client, _alert())

    assert r.status_code == 403
    assert "not a TradingView address" in r.json()["reason"]
    assert _receipts(db, org_id) == []


def test_unknown_hook_is_404_with_no_trace_in_any_org(org_client, db):
    client, org_id, seed = org_client
    r = _post(client, _alert(), hook="nope")
    assert r.status_code == 404
    assert _receipts(db, org_id) == []


def test_wrong_secret_is_refused_and_the_body_is_never_stored(org_client, db):
    """A typo'd template retries forever. The secret it carries must not be
    written to disk on each attempt."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)

    r = _post(client, _alert(secret="tvw_wrong"))

    assert r.status_code == 401
    rows = _receipts(db, org_id)
    assert rows[0][0] == "rejected" and rows[0][3] is None, "body stored on a secret mismatch"


def test_the_secret_under_the_wrong_key_appears_nowhere(org_client, db):
    """'Secret', 'token', or pasted into 'id' -- a non-engineer's mistake.
    The real secret must not survive anywhere we write, by value."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client)

    _post(client, {"Secret": SECRET, "action": "buy", "symbol": "XAUUSD", "lots": 0.01, "id": SECRET})
    # and an AUTHENTICATED alert that also leaks it in another field
    _post(client, _alert(note=SECRET))

    with psycopg.connect(db, autocommit=True) as conn:
        blob = json.dumps(conn.execute(
            "SELECT body_redacted, alert_id, reason FROM webhook_receipts").fetchall(), default=str)
        blob += json.dumps(conn.execute("SELECT payload FROM events").fetchall(), default=str)
    assert SECRET not in blob


def test_body_too_large_is_refused_before_parsing(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    r = _post(client, None, raw=b"x" * (wh.MAX_BODY_BYTES + 1))
    assert r.status_code == 413


def test_text_plain_that_is_not_json_says_so(org_client, db):
    """TradingView sends text/plain when IT judged the message not JSON --
    that judgement is the mistake to explain, not swallow."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    r = _post(client, None, raw=b"buy XAUUSD")
    assert r.status_code == 422
    assert "must be JSON" in r.json()["reason"]


def test_works_with_no_csrf_token_or_session(org_client, db):
    """Implicit in every other test, stated once: TradingView has neither."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client)
    client.cookies.clear()
    assert _post(client, _alert()).status_code == 200


# ============================================================ gates


def test_switched_off_is_refused_even_with_the_right_secret(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id, enabled=False)
    r = _post(client, _alert())
    assert r.status_code == 403 and "switched off" in r.json()["reason"]


def test_the_kill_switch_stops_automation_too(org_client, db):
    """STOP COPYING is the button a person hits in a panic. An alert landing
    a second later must not open a position no slave will ever copy."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _org_settings(db, org_id, copying_enabled=False)
    calls = _copier(client)

    r = _post(client, _alert())

    assert r.status_code == 403 and "copying is stopped" in r.json()["reason"]
    assert not any("/order" in u for u, _ in calls)


def test_dry_run_refuses_like_a_manual_order_does(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _org_settings(db, org_id, dry_run=True)
    r = _post(client, _alert())
    assert r.status_code == 422 and "dry-run" in r.json()["reason"]


def test_no_master_account_is_a_clear_refusal(org_client, db):
    client, org_id, seed = org_client
    _arm(db, org_id)
    r = _post(client, _alert())
    assert r.status_code == 422 and "no master account" in r.json()["reason"]


# ============================================================ orders


def test_a_buy_places_a_market_order_on_the_master_as_tradingview(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)

    r = _post(client, _alert(stop_loss=4570, take_profit=4600))

    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    url, sent = next((u, b) for u, b in calls if "/order" in u)
    assert sent == {"account_id": MASTER, "symbol": "XAUUSD", "side": "BUY",
                    "order_type": "MARKET", "volume_lots": 0.01,
                    "stop_loss": 4570.0, "take_profit": 4600.0,
                    "actor_email": "tradingview"}
    severity, payload, actor = _events(db, org_id)[-1]
    assert actor == "tradingview" and payload["outcome"] == "accepted"


def test_the_same_alert_twice_places_one_order(org_client, db):
    """TradingView resends on non-2xx and two indicators can fire the same
    message in the same second. One trade."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)

    first = _post(client, _alert(id="a"))
    second = _post(client, _alert(id="b"))   # a resend re-renders {{timenow}}

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert second.json()["duplicate_of"] == first.json()["receipt_id"]
    assert sum("/order" in u for u, _ in calls) == 1


def test_a_different_size_is_not_a_duplicate(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client)
    _post(client, _alert(lots=0.01)); _post(client, _alert(lots=0.02))
    assert sum("/order" in u for u, _ in calls) == 2


def test_lots_above_the_org_cap_are_refused(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id, max_lots=0.05)
    calls = _copier(client)
    r = _post(client, _alert(lots=0.5))
    assert r.status_code == 422 and "cap" in r.json()["reason"]
    assert not any("/order" in u for u, _ in calls)


def test_the_per_minute_cap_holds(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id, max_per_minute=2)
    _copier(client)
    assert _post(client, _alert(lots=0.01)).status_code == 200
    assert _post(client, _alert(lots=0.02)).status_code == 200
    r = _post(client, _alert(lots=0.03))
    assert r.status_code == 429


def test_a_buy_against_an_open_sell_is_refused_not_hedged(org_client, db):
    """Reverse-on-signal would open a hedge across the whole fleet."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client, state={"master_positions": [
        {"position_id": 5, "symbol": "XAUUSD", "side": "SELL", "volume": 100}]})
    r = _post(client, _alert())
    assert r.status_code == 422 and "opposite position" in r.json()["reason"]
    assert not any("/order" in u for u, _ in calls)


def test_max_open_positions_bounds_a_leaked_secret(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id, max_open=2)
    calls = _copier(client, state={"master_positions": [
        {"position_id": 1, "symbol": "EURUSD", "side": "BUY", "volume": 1},
        {"position_id": 2, "symbol": "GBPUSD", "side": "BUY", "volume": 1}]})
    r = _post(client, _alert())
    assert r.status_code == 422 and "open positions" in r.json()["reason"]
    assert not any("/order" in u for u, _ in calls)


# ============================================================ close


def test_close_flattens_every_master_position_on_the_symbol(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client, state={"master_positions": [
        {"position_id": 11, "symbol": "XAUUSD", "side": "BUY", "volume": 100},
        {"position_id": 12, "symbol": "XAUUSD", "side": "BUY", "volume": 100},
        {"position_id": 13, "symbol": "EURUSD", "side": "BUY", "volume": 100}]})

    r = _post(client, _alert(action="close"))

    assert r.status_code == 200
    assert r.json()["positions_closed"] == [11, 12]
    closes = [b for u, b in calls if "/positions/close" in u]
    assert [c["position_id"] for c in closes] == [11, 12]
    assert all(c["actor_email"] == "tradingview" for c in closes)


def test_close_with_nothing_open_resyncs_first_then_reports_honestly(org_client, db):
    """A close 300ms after its entry can read a stale, empty book. Ask for a
    fresh one before concluding there is nothing -- then say so as a
    warning, never as a green accepted."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    calls = _copier(client, state={"master_positions": []})

    r = _post(client, _alert(action="close"))

    assert r.status_code == 200
    assert r.json()["status"] == "nothing_to_close"
    assert any("/resync" in u for u, _ in calls), "did not re-read the book"
    assert not any("/positions/close" in u for u, _ in calls)
    assert _events(db, org_id)[-1][0] == "warning"


# ========================================== copier failure classification


def test_copier_unreachable_is_503_and_frees_the_fingerprint(org_client, db):
    """Nothing was sent, so TradingView's resend is WANTED -- and must not
    then be swallowed as a duplicate of the attempt that never happened."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client, raise_on={"/order": httpx.ConnectError("refused")})

    r = _post(client, _alert())
    assert r.status_code == 503 and r.json()["status"] == "failed"

    calls = _copier(client)   # copier back
    r2 = _post(client, _alert(id="resend"))
    assert r2.json()["status"] == "accepted", "the resend was treated as a duplicate"
    assert sum("/order" in u for u, _ in calls) == 1


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.PoolTimeout("t")])
def test_connect_and_pool_timeouts_mean_nothing_was_sent(org_client, db, exc):
    """Both are TimeoutException subclasses; the review found them being
    misfiled as 'may be on the wire', which then blocked the resend."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client, raise_on={"/order": exc})
    assert _post(client, _alert()).status_code == 503


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.RemoteProtocolError("t"),
                                 httpx.ReadError("t")])
def test_a_failure_after_the_request_was_written_is_unknown_not_a_resend(org_client, db, exc):
    """The order MAY be live. A 5xx here is the double trade."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client, raise_on={"/order": exc})

    r = _post(client, _alert())

    assert r.status_code == 200 and r.json()["status"] == "unknown"
    assert _events(db, org_id)[-1][0] == "error"
    # and the fingerprint is KEPT, so a resend is a duplicate
    calls = _copier(client)
    assert _post(client, _alert(id="resend")).json()["status"] == "duplicate"
    assert not any("/order" in u for u, _ in calls)


def test_copier_5xx_is_unknown(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client, order=httpx.Response(500, text="boom"))
    r = _post(client, _alert())
    assert r.status_code == 200 and r.json()["status"] == "unknown"


def test_a_copier_still_starting_is_503_so_tradingview_resends(org_client, db):
    """'no client for account' two seconds after a restart is a 'not now',
    and a 422 would lose the alert for good."""
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client, order=httpx.Response(400, json={"error": f"no client for account {MASTER}"}))
    assert _post(client, _alert()).status_code == 503


def test_a_genuine_copier_validation_error_is_422(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client, order=httpx.Response(400, json={"error": "unknown symbol 'XAUUSD' for account 999"}))
    r = _post(client, _alert())
    # "not found" is a transient phrase; this message must not match it
    assert r.status_code == 422 and "unknown symbol" in r.json()["reason"]


# ======================================================= operator side


def test_rotating_the_secret_shows_it_once_and_stores_only_the_hash(org_client, db):
    client, org_id, seed = org_client
    r = client.post(f"/api/orgs/{org_id}/webhook/secret", headers=_csrf(client))
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert secret.startswith("tvw_")
    assert secret in r.json()["template"]
    with psycopg.connect(db, autocommit=True) as conn:
        (stored,) = conn.execute(
            "SELECT secret_hash FROM org_webhooks WHERE org_id = %s", (org_id,)).fetchone()
    assert stored == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in json.dumps(client.get(f"/api/orgs/{org_id}/webhook").json())


def test_the_rotated_secret_authenticates_and_the_old_one_stops(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client)
    new = client.post(f"/api/orgs/{org_id}/webhook/secret", headers=_csrf(client)).json()
    assert _post(client, _alert(secret=SECRET), hook=new["hook_id"]).status_code == 401
    assert _post(client, _alert(secret=new["secret"]), hook=new["hook_id"]).status_code == 200


def test_enabling_needs_a_secret_a_master_and_an_https_origin(org_client, db, monkeypatch):
    client, org_id, seed = org_client
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://mirrorfleet.test")
    r = client.put(f"/api/orgs/{org_id}/webhook", json={"enabled": True}, headers=_csrf(client))
    assert r.status_code == 400 and "secret" in r.json()["detail"]

    client.post(f"/api/orgs/{org_id}/webhook/secret", headers=_csrf(client))
    r = client.put(f"/api/orgs/{org_id}/webhook", json={"enabled": True}, headers=_csrf(client))
    assert r.status_code == 400 and "master" in r.json()["detail"]

    seed(MASTER, role="master")
    r = client.put(f"/api/orgs/{org_id}/webhook", json={"enabled": True}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["changed"]["enabled"]["to"] is True


def test_caps_are_validated_and_audited(org_client, db):
    client, org_id, seed = org_client
    client.post(f"/api/orgs/{org_id}/webhook/secret", headers=_csrf(client))
    assert client.put(f"/api/orgs/{org_id}/webhook", json={"max_lots": 100},
                      headers=_csrf(client)).status_code == 400
    r = client.put(f"/api/orgs/{org_id}/webhook", json={"max_lots": 0.5, "max_per_minute": 5},
                   headers=_csrf(client))
    assert r.status_code == 200
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute("SELECT payload FROM events WHERE payload->>'action' = "
                           "'webhook_settings_changed' ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0]["max_lots"]["to"] == 0.5


def test_a_trader_can_read_but_not_rotate_or_enable(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    trader = make_user(email="trader@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'trader')",
                     (org_id, trader.id if hasattr(trader, "id") else trader["id"]))
    login_as(client, trader)
    assert client.get(f"/api/orgs/{org_id}/webhook").status_code == 200
    assert client.post(f"/api/orgs/{org_id}/webhook/secret", headers=_csrf(client)).status_code == 403
    assert client.put(f"/api/orgs/{org_id}/webhook", json={"enabled": True},
                      headers=_csrf(client)).status_code == 403


def test_recent_receipts_are_listed_for_the_operator(org_client, db):
    client, org_id, seed = org_client
    seed(MASTER, role="master"); _arm(db, org_id)
    _copier(client)
    _post(client, _alert()); _post(client, _alert(secret="tvw_bad"))
    recent = client.get(f"/api/orgs/{org_id}/webhook").json()["recent"]
    assert [r["outcome"] for r in recent] == ["rejected", "accepted"]
