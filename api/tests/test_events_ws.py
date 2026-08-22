"""Tests for events REST and WebSocket endpoints."""
import asyncio
import json
import uuid
import psycopg
import pytest
import httpx
import websockets
from datetime import datetime, timedelta
from pathlib import Path
from starlette.websockets import WebSocketDisconnect


def _insert_event(db, org_id, account_id=None, category="control", severity="info", latency_ms=None, payload=None):
    """Helper to insert an event into the test database, scoped to an org."""
    if payload is None:
        payload = {"message": "test event"}
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            """INSERT INTO events
               (org_id, account_id, category, severity, latency_ms, payload)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, ts""",
            (org_id, account_id, category, severity, latency_ms, json.dumps(payload))
        ).fetchone()
        return result


class TestEventsREST:
    """Tests for GET /api/orgs/{org_id}/events endpoint."""

    def test_events_requires_auth(self, app_client):
        """GET /api/orgs/{org_id}/events requires authentication."""
        response = app_client.get("/api/orgs/1/events")
        assert response.status_code == 401

    def test_events_with_auth_returns_empty_list(self, org_client):
        """GET /api/orgs/{org_id}/events returns empty list when no events exist."""
        client, org_id, seed = org_client

        response = client.get(f"/api/orgs/{org_id}/events")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_events_default_limit_200(self, org_client, db):
        """GET /api/orgs/{org_id}/events returns up to 200 events by default (newest first)."""
        client, org_id, seed = org_client

        # Insert 5 events
        seed(12345)

        event_ids = []
        for i in range(5):
            event_id, ts = _insert_event(
                db,
                org_id,
                account_id=12345,
                category="control",
                severity="info",
                latency_ms=100 + i,
                payload={"msg": f"event_{i}"}
            )
            event_ids.append(event_id)

        response = client.get(f"/api/orgs/{org_id}/events")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5
        # Check they're newest first
        assert data[0]["id"] == event_ids[-1]
        assert data[-1]["id"] == event_ids[0]

    def test_events_filtering_by_severity_and_category(self, org_client, db):
        """GET /api/orgs/{org_id}/events filters by severity and category."""
        client, org_id, seed = org_client

        # Insert events with different severity/category
        seed(12345)

        _insert_event(db, org_id, account_id=12345, category="control", severity="info")
        _insert_event(db, org_id, account_id=12345, category="control", severity="warning")
        _insert_event(db, org_id, account_id=12345, category="connection", severity="error")
        _insert_event(db, org_id, account_id=12345, category="auth", severity="info")

        # Filter by severity=warning
        response = client.get(f"/api/orgs/{org_id}/events?severity=warning")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["severity"] == "warning"

        # Filter by category=connection
        response = client.get(f"/api/orgs/{org_id}/events?category=connection")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["category"] == "connection"

        # Filter by both severity and category
        response = client.get(f"/api/orgs/{org_id}/events?severity=info&category=control")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["severity"] == "info"
        assert data[0]["category"] == "control"

    def test_events_filtering_by_account_id(self, org_client, db):
        """GET /api/orgs/{org_id}/events filters by account_id."""
        client, org_id, seed = org_client

        # Insert events from different accounts
        seed(12345)
        seed(12346)

        _insert_event(db, org_id, account_id=12345, category="control", severity="info")
        _insert_event(db, org_id, account_id=12346, category="control", severity="info")
        _insert_event(db, org_id, account_id=None, category="auth", severity="info")

        # Filter by account_id=12345
        response = client.get(f"/api/orgs/{org_id}/events?account_id=12345")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["account_id"] == 12345

    def test_events_since_and_limit(self, org_client, db):
        """GET /api/orgs/{org_id}/events with since and limit parameters."""
        client, org_id, seed = org_client

        seed(12345)

        # Insert 5 events
        event_ids_and_ts = []
        for i in range(5):
            event_id, ts = _insert_event(db, org_id, account_id=12345, category="control", severity="info")
            event_ids_and_ts.append((event_id, ts))

        # Get all events, limit to 2
        response = client.get(f"/api/orgs/{org_id}/events?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Should be newest first
        assert data[0]["id"] == event_ids_and_ts[-1][0]
        assert data[1]["id"] == event_ids_and_ts[-2][0]

        # Get events since a specific timestamp (using the ts from response)
        # The third event's timestamp
        response_all = client.get(f"/api/orgs/{org_id}/events")
        all_events = response_all.json()
        # Find the third event's timestamp from the response
        third_event_ts = all_events[len(all_events) - 3]["ts"]  # third oldest

        response = client.get(f"/api/orgs/{org_id}/events?since={third_event_ts}")
        assert response.status_code == 200
        data = response.json()
        # Should get events after the specified timestamp (newest 2)
        assert len(data) == 2
        assert data[0]["id"] == event_ids_and_ts[-1][0]

    def test_events_response_structure(self, org_client, db):
        """GET /api/orgs/{org_id}/events response has correct structure."""
        client, org_id, seed = org_client

        seed(12345)

        _insert_event(
            db,
            org_id,
            account_id=12345,
            category="control",
            severity="info",
            latency_ms=42,
            payload={"data": "test"}
        )

        response = client.get(f"/api/orgs/{org_id}/events")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        event = data[0]
        assert "id" in event
        assert "ts" in event
        assert "account_id" in event
        assert event["account_id"] == 12345
        assert "category" in event
        assert event["category"] == "control"
        assert "severity" in event
        assert event["severity"] == "info"
        assert "latency_ms" in event
        assert event["latency_ms"] == 42
        assert "payload" in event
        assert event["payload"] == {"data": "test"}

    def test_events_malformed_since_400(self, org_client):
        """GET /api/orgs/{org_id}/events returns 400 for malformed since timestamp."""
        client, org_id, seed = org_client

        response = client.get(f"/api/orgs/{org_id}/events?since=not-a-timestamp")
        assert response.status_code == 400
        assert "ISO 8601" in response.json()["detail"]

    def test_events_limit_capped_to_1000(self, org_client, db):
        """GET /api/orgs/{org_id}/events caps limit to 1000 maximum."""
        client, org_id, seed = org_client

        seed(12345)

        # Insert 10 events
        for i in range(10):
            _insert_event(db, org_id, account_id=12345, category="control", severity="info")

        # Request with limit > 1000 should be capped to 1000
        response = client.get(f"/api/orgs/{org_id}/events?limit=5000")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 10  # Only 10 events exist, but limit was capped to 1000

    def test_events_limit_min_1(self, org_client):
        """GET /api/orgs/{org_id}/events returns 400 for limit < 1."""
        client, org_id, seed = org_client

        response = client.get(f"/api/orgs/{org_id}/events?limit=0")
        assert response.status_code == 400


def test_events_are_org_scoped_and_null_org_hidden(org_client, db):
    """Events belonging to another org, and NULL-org (infrastructure)
    events, are invisible through this org's events endpoint."""
    import psycopg
    client, org_id, seed = org_client
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO events (org_id, category, severity, payload)
               VALUES (%s, 'control', 'info', '{"n": 1}'::jsonb)""", (org_id,))
        conn.execute(
            """INSERT INTO events (org_id, category, severity, payload)
               VALUES (NULL, 'connection', 'info', '{"n": 2}'::jsonb)""")
        conn.execute(
            """INSERT INTO orgs (name) VALUES ('ghost')""")
        conn.execute(
            """INSERT INTO events (org_id, category, severity, payload)
               SELECT id, 'control', 'info', '{"n": 3}'::jsonb FROM orgs
               WHERE name = 'ghost'""")
    events = client.get(f"/api/orgs/{org_id}/events").json()
    assert [e["payload"]["n"] for e in events] == [1]


class TestWebSocket:
    """Tests for WebSocket /api/ws endpoint."""

    def test_ws_rejects_unauthenticated(self, app_client):
        """WebSocket connection is rejected without authentication with 4401 close code."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with app_client.websocket_connect("/api/ws"):
                pass
        assert exc_info.value.code == 4401

    def test_ws_accepts_authenticated(self, app_client_with_lifespan, make_user, make_org, login_as):
        """WebSocket connection is accepted with valid session cookie and org membership."""
        user = make_user(email="ws-accept@example.com")
        org_id = make_org(name="WS Org", members=[(user, "viewer")])
        login_as(app_client_with_lifespan, user)

        # Should be able to connect to WS now
        try:
            with app_client_with_lifespan.websocket_connect(f"/api/ws?org_id={org_id}") as ws:
                # Just check that connection succeeded
                pass
        except Exception as e:
            pytest.fail(f"WS connection should succeed with auth, got: {e}")


def test_ws_delivers_only_own_org_events(live_server, db, make_user, make_org):
    """Two orgs, two sockets: each socket sees only its org's events."""
    import json
    import psycopg
    import httpx
    from websockets.sync.client import connect as ws_connect

    base_url, ws_url = live_server

    def session_cookies(email):
        r = httpx.post(f"{base_url}/api/register", json={
            "email": email, "password": "a-solid-password", "display_name": email})
        assert r.status_code == 204
        return {"session": r.cookies["session"], "csrf": r.cookies["csrf"]}

    cookies_a = session_cookies("a@example.com")
    cookies_b = session_cookies("b@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        (uid_a,) = conn.execute(
            "SELECT id FROM users WHERE email = 'a@example.com'").fetchone()
        (uid_b,) = conn.execute(
            "SELECT id FROM users WHERE email = 'b@example.com'").fetchone()
    org_a = make_org(name="A", members=[({"id": uid_a}, "viewer")])
    org_b = make_org(name="B", members=[({"id": uid_b}, "viewer")])

    def hdr(cookies):
        return {"Cookie": f"session={cookies['session']}"}

    with ws_connect(f"{ws_url}?org_id={org_a}",
                    additional_headers=hdr(cookies_a)) as ws_a, \
         ws_connect(f"{ws_url}?org_id={org_b}",
                    additional_headers=hdr(cookies_b)) as ws_b:
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                """INSERT INTO events (org_id, category, severity, payload)
                   VALUES (%s, 'control', 'info', '{"which": "a"}'::jsonb)""",
                (org_a,))
            conn.execute(
                """INSERT INTO events (org_id, category, severity, payload)
                   VALUES (%s, 'control', 'info', '{"which": "b"}'::jsonb)""",
                (org_b,))
        got_a = json.loads(ws_a.recv(timeout=10))
        got_b = json.loads(ws_b.recv(timeout=10))
        assert got_a["payload"]["which"] == "a" and got_a["org_id"] == org_a
        assert got_b["payload"]["which"] == "b" and got_b["org_id"] == org_b
        # and nothing else arrives on A within a short window
        import pytest as _pytest
        with _pytest.raises(TimeoutError):
            ws_a.recv(timeout=1)


def test_ws_nonmember_closed_4404(live_server, db, make_user, make_org):
    import httpx
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    base_url, ws_url = live_server
    r = httpx.post(f"{base_url}/api/register", json={
        "email": "x@example.com", "password": "a-solid-password", "display_name": "x"})
    stranger_org = make_org(name="NotYours", members=[])
    try:
        with ws_connect(f"{ws_url}?org_id={stranger_org}",
                        additional_headers={"Cookie": f"session={r.cookies['session']}"}) as ws:
            ws.recv(timeout=5)
            raise AssertionError("socket should have been closed")
    except ConnectionClosed as e:
        assert e.rcvd.code == 4404


def _register_client(base_url, email):
    """A persistent httpx.Client (cookie jar included) logged in as a fresh
    user via the real, live server."""
    client = httpx.Client(base_url=base_url)
    r = client.post("/api/register", json={
        "email": email, "password": "a-solid-password", "display_name": email})
    assert r.status_code == 204
    return client


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_ws_closes_when_membership_revoked(live_server, db, make_org):
    """F1: a member's open socket is closed the moment they're removed from
    the org, instead of continuing to stream that org's live events until
    their tab happens to close."""
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    base_url, ws_url = live_server
    owner = _register_client(base_url, "owner-revoke-member@example.com")
    member = _register_client(base_url, "member-revoke@example.com")

    with psycopg.connect(db, autocommit=True) as conn:
        (owner_id,) = conn.execute(
            "SELECT id FROM users WHERE email = 'owner-revoke-member@example.com'"
        ).fetchone()
        (member_id,) = conn.execute(
            "SELECT id FROM users WHERE email = 'member-revoke@example.com'"
        ).fetchone()
    org_id = make_org(name="RevokeMember", members=[
        ({"id": owner_id}, "owner"), ({"id": member_id}, "viewer")])

    with ws_connect(
        f"{ws_url}?org_id={org_id}",
        additional_headers={"Cookie": f"session={member.cookies.get('session')}"},
    ) as ws:
        r = owner.delete(f"/api/orgs/{org_id}/members/{member_id}",
                          headers=_csrf(owner))
        assert r.status_code == 204

        try:
            ws.recv(timeout=5)
            raise AssertionError("socket should have been closed on removal")
        except ConnectionClosed as e:
            assert e.rcvd.code == 4404


def test_ws_closes_when_org_deleted(live_server, db, make_org):
    """F1: deleting the org closes every socket currently streaming it."""
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    base_url, ws_url = live_server
    owner = _register_client(base_url, "owner-delete-org@example.com")
    member = _register_client(base_url, "member-delete-org@example.com")

    with psycopg.connect(db, autocommit=True) as conn:
        (owner_id,) = conn.execute(
            "SELECT id FROM users WHERE email = 'owner-delete-org@example.com'"
        ).fetchone()
        (member_id,) = conn.execute(
            "SELECT id FROM users WHERE email = 'member-delete-org@example.com'"
        ).fetchone()
    org_id = make_org(name="RevokeOrg", members=[
        ({"id": owner_id}, "owner"), ({"id": member_id}, "viewer")])

    with ws_connect(
        f"{ws_url}?org_id={org_id}",
        additional_headers={"Cookie": f"session={member.cookies.get('session')}"},
    ) as ws:
        r = owner.delete(f"/api/orgs/{org_id}", headers=_csrf(owner))
        assert r.status_code == 204

        try:
            ws.recv(timeout=5)
            raise AssertionError("socket should have been closed on org deletion")
        except ConnectionClosed as e:
            assert e.rcvd.code == 4404


class TestWebSocketLiveDelivery:
    """Delivery tests against a REAL uvicorn server + REAL TCP `websockets` client.

    These prove the full path: INSERT -> pg_notify('events', id) (migration 001 trigger)
    -> EventBroadcaster.LISTEN -> broadcast -> WebSocket client, end to end. There is no
    shared/cross-loop asyncio object here: the server (lifespan listener task + WS handler)
    runs entirely in uvicorn's background-thread event loop, and the test's `websockets`
    client runs entirely in the test's event loop. The only things connecting them are a
    real TCP socket (for the WS) and Postgres itself (for the INSERT -> NOTIFY).
    """

    @pytest.mark.asyncio
    async def test_ws_delivers_inserted_event(self, live_server, db, make_user, make_org):
        """An events row inserted into the app's DB is delivered to a connected WS client
        that is a member of the event's org."""
        base_url, ws_url = live_server

        user = make_user(email="live-delivery@example.com")
        org_id = make_org(name="Live Org", members=[(user, "viewer")])

        # Log in over real HTTP against the live server to get a valid session cookie.
        async with httpx.AsyncClient(base_url=base_url) as http:
            resp = await http.post("/api/login", json={
                "email": user["email"], "password": user["password"]})
            assert resp.status_code == 204, resp.text
            session_cookie = resp.cookies.get("session")
            assert session_cookie, "login did not set a session cookie"

        # ws.py reads the cookie via ws.cookies["session"], populated from the Cookie header.
        headers = {"Cookie": f"session={session_cookie}"}

        async with websockets.connect(
            f"{ws_url}?org_id={org_id}", additional_headers=headers, open_timeout=5
        ) as ws:
            # Confirm the connection actually stayed open (wasn't rejected/closed
            # immediately) before we go on to assert delivery.
            assert ws.close_code is None, (
                f"WS connection closed immediately after handshake, code={ws.close_code} "
                f"reason={ws.close_reason!r} -- auth was likely rejected"
            )

            # Separate psycopg connection, same DSN (`db`) the live app is configured with
            # (POSTGRES_DSN was set to `db` by the live_server fixture). pg_notify only
            # reaches listeners on the SAME database, so this must match exactly.
            payload = {"message": "live delivery test", "nonce": str(uuid.uuid4())}
            with psycopg.connect(db, autocommit=True) as conn:
                row = conn.execute(
                    """INSERT INTO events (org_id, account_id, category, severity, latency_ms, payload)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (org_id, None, "control", "info", None, json.dumps(payload)),
                ).fetchone()
                event_id = row[0]

            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(raw)

        assert data["id"] == event_id
        assert data["category"] == "control"
        assert data["severity"] == "info"
        assert data["payload"] == payload
        assert data["org_id"] == org_id

    @pytest.mark.asyncio
    async def test_ws_rejects_unauthenticated_live(self, live_server):
        """Against the live server, a WS connect without a valid session cookie is rejected."""
        _, ws_url = live_server

        # No LISTEN registration or lifespan race here: ws.py rejects before ever
        # calling ws.accept(), so uvicorn responds with an HTTP-level handshake
        # rejection rather than a post-accept WS close frame carrying code 4401
        # (confirmed against uvicorn's websockets_sansio_impl: a pre-accept
        # "websocket.close" is turned into `conn.reject(HTTPStatus.FORBIDDEN, "")`).
        with pytest.raises((websockets.exceptions.InvalidHandshake, websockets.exceptions.ConnectionClosed)):
            async with websockets.connect(ws_url, open_timeout=5) as ws:
                await ws.recv()


class TestStaticServing:
    """Tests for static file serving."""

    def test_spa_fallback_serves_index_html(self, tmp_path, monkeypatch):
        """SPA fallback serves index.html for non-/api paths when STATIC_DIR is set."""
        # Create a temporary static directory with index.html
        static_dir = tmp_path / "dist"
        static_dir.mkdir()
        index_html = static_dir / "index.html"
        index_html.write_text("<html><body>Test Dashboard</body></html>")

        # Use monkeypatch to set STATIC_DIR (cleaner than direct environ manipulation)
        monkeypatch.setenv("STATIC_DIR", str(static_dir))

        # Re-import to get fresh config
        from api.main import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)

        # GET / should return index.html
        response = client.get("/")
        assert response.status_code == 200
        assert "Test Dashboard" in response.text

        # GET /some-route should also return index.html (SPA fallback)
        response = client.get("/some-route")
        assert response.status_code == 200
        assert "Test Dashboard" in response.text

        # /api paths should not be affected
        response = client.get("/api/nonexistent")
        assert response.status_code == 404

    def test_static_serving_absent_gracefully(self, app_client):
        """Static serving degrades gracefully when STATIC_DIR is not set or missing."""
        import os
        # Make sure STATIC_DIR is not set
        if "STATIC_DIR" in os.environ:
            del os.environ["STATIC_DIR"]

        from api.main import create_app

        app = create_app()
        from fastapi.testclient import TestClient
        client = TestClient(app)

        # App should still work even without static files
        # (root path would 404 or not be served)
        response = client.get("/api/login")
        # Should get a response (200 or error), not a 500
        assert response.status_code != 500


class TestQuotesTicker:
    """The live-price relay: poll the copier's /ticks for orgs with open
    sockets and push CHANGED payloads as category='quotes' messages."""

    def test_ticker_polls_only_watched_orgs_and_suppresses_repeats(self):
        from api.ws import EventBroadcaster

        b = EventBroadcaster()
        b.TICK_INTERVAL_S = 0  # spin instantly in the test

        sent = []

        async def fake_broadcast(org_id, message):
            sent.append((org_id, message))

        b.broadcast = fake_broadcast
        b.connections = {1: {object(): 7}, 2: {}}  # org 2: nobody watching

        bodies = ['{"quotes": {"EURUSD": {"bid": 1.1, "ask": 1.2}}, "accounts": {}}',
                  '{"quotes": {"EURUSD": {"bid": 1.1, "ask": 1.2}}, "accounts": {}}',
                  '{"quotes": {"EURUSD": {"bid": 1.3, "ask": 1.4}}, "accounts": {}}']

        class FakeResponse:
            def __init__(self, text):
                self.status_code = 200
                self.text = text

            def json(self):
                return json.loads(self.text)

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def get(self, url, params=None, **kwargs):
                self.calls.append((url, dict(params)))
                return FakeResponse(bodies[min(len(self.calls) - 1, len(bodies) - 1)])

        client = FakeClient()

        async def run():
            task = asyncio.create_task(
                b.start_ticker("http://copier:8080", client))
            for _ in range(500):
                await asyncio.sleep(0)
                if len(client.calls) >= 3:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())

        # Only org 1 (which has a socket) was ever polled.
        assert all(params == {"org_id": 1} for _, params in client.calls)
        assert all(url == "http://copier:8080/ticks" for url, _ in client.calls)
        # First payload and the changed third one broadcast; the identical
        # second one suppressed.
        assert len(sent) == 2
        org_id, first = sent[0]
        assert org_id == 1
        assert first["category"] == "quotes"
        assert first["payload"]["quotes"]["EURUSD"] == {"bid": 1.1, "ask": 1.2}
        assert sent[1][1]["payload"]["quotes"]["EURUSD"] == {"bid": 1.3, "ask": 1.4}

    def test_a_new_socket_replays_the_cached_quotes_frame(self):
        from api.ws import EventBroadcaster
        from starlette.websockets import WebSocketState

        b = EventBroadcaster()
        b._last_ticks[1] = '{"quotes": {"EURUSD": {"bid": 1.1, "ask": 1.2}}, "accounts": {}}'

        received = []

        class FakeWS:
            application_state = WebSocketState.CONNECTED

            async def send_json(self, message):
                received.append(message)

        asyncio.run(b.connect(FakeWS(), 1, 7))

        assert len(received) == 1
        assert received[0]["category"] == "quotes"
        assert received[0]["payload"]["quotes"]["EURUSD"] == {"bid": 1.1, "ask": 1.2}

        # An org with no cached frame stays silent on connect.
        received.clear()
        asyncio.run(b.connect(FakeWS(), 2, 7))
        assert received == []

    def test_ticker_survives_copier_errors(self):
        from api.ws import EventBroadcaster

        b = EventBroadcaster()
        b.TICK_INTERVAL_S = 0
        b.connections = {1: {object(): 7}}

        class ExplodingClient:
            def __init__(self):
                self.calls = 0

            async def get(self, url, params=None, **kwargs):
                self.calls += 1
                raise RuntimeError("copier restarting")

        client = ExplodingClient()

        async def run():
            task = asyncio.create_task(
                b.start_ticker("http://copier:8080", client))
            for _ in range(500):
                await asyncio.sleep(0)
                if client.calls >= 3:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert client.calls >= 3  # kept retrying, never died


class TestOriginGuard:
    """The WebSocket handshake's cross-site guard. It must protect the feed
    WITHOUT needing configuration -- an unset PUBLIC_ORIGIN that rejected
    every real browser would take the live feed down platform-wide."""

    def test_same_origin_is_allowed_with_no_configuration(self):
        from api.ws import EventBroadcaster
        allowed = EventBroadcaster.origin_allowed
        assert allowed("https://mirrorfleet.com", "", "mirrorfleet.com")
        assert allowed("http://localhost:8000", "", "localhost:8000")

    def test_cross_site_origins_are_rejected(self):
        from api.ws import EventBroadcaster
        allowed = EventBroadcaster.origin_allowed
        assert not allowed("https://evil.com", "", "mirrorfleet.com")
        # Suffix tricks must not pass: a prefix/substring check would.
        assert not allowed("https://mirrorfleet.com.evil.com", "", "mirrorfleet.com")
        assert not allowed("http://localhost.evil.com", "", "localhost:8000")

    def test_public_origin_allows_a_configured_list(self):
        from api.ws import EventBroadcaster
        allowed = EventBroadcaster.origin_allowed
        cfg = "https://mirrorfleet.com, https://www.mirrorfleet.com"
        assert allowed("https://www.mirrorfleet.com", cfg, "anything")
        assert allowed("https://mirrorfleet.com", cfg, "anything")
        # Configured list wins over the Host fallback.
        assert not allowed("https://other.com", cfg, "other.com")

    def test_absent_origin_is_a_non_browser_client(self):
        from api.ws import EventBroadcaster
        # curl and the test suite send none; the session cookie still gates.
        assert EventBroadcaster.origin_allowed(None, "", "mirrorfleet.com")


def test_close_for_user_hangs_up_only_that_users_sockets():
    """Sign-out-everywhere must cut live streams: the session version is
    only checked at handshake, so an open socket would otherwise outlive
    the revocation it was supposed to end."""
    import asyncio
    from api.ws import EventBroadcaster

    closed = []

    class FakeWS:
        def __init__(self, tag):
            self.tag = tag

        async def close(self, code=1000, reason=""):
            closed.append((self.tag, code))

    victim, bystander = FakeWS("victim"), FakeWS("bystander")
    b = EventBroadcaster()
    b.connections = {1: {victim: 7, bystander: 8}}

    asyncio.run(b.close_for_user(7))

    assert closed == [("victim", 4401)]
    assert bystander in b.connections[1]
    assert victim not in b.connections[1]
