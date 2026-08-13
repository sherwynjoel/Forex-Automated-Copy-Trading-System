"""Tests for events REST and WebSocket endpoints."""
import json
import psycopg
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from starlette.websockets import WebSocketDisconnect


def _seed_account(db, ctid_trader_account_id, ctid_connection_id, trader_login, is_live):
    """Helper to insert an account into the test database."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO accounts
               (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, "slave", True, 1.0)
        )


def _seed_connection(db):
    """Helper to insert a connection into the test database."""
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            """INSERT INTO ctid_connections
               (access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, %s, now(), now() + interval '30 days')
               RETURNING id""",
            ("enc_access", "enc_refresh")
        ).fetchone()
        return result[0]


def _insert_event(db, account_id=None, category="control", severity="info", latency_ms=None, payload=None):
    """Helper to insert an event into the test database."""
    if payload is None:
        payload = {"message": "test event"}
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            """INSERT INTO events
               (account_id, category, severity, latency_ms, payload)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id, ts""",
            (account_id, category, severity, latency_ms, json.dumps(payload))
        ).fetchone()
        return result


class TestEventsREST:
    """Tests for GET /api/events endpoint."""

    def test_events_requires_auth(self, app_client):
        """GET /api/events requires authentication."""
        response = app_client.get("/api/events")
        assert response.status_code == 401

    def test_events_with_auth_returns_empty_list(self, app_client):
        """GET /api/events returns empty list when no events exist."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        response = app_client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_events_default_limit_200(self, app_client, db):
        """GET /api/events returns up to 200 events by default (newest first)."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        # Insert 5 events
        conn_id = _seed_connection(db)
        _seed_account(db, 12345, conn_id, 111111, True)

        event_ids = []
        for i in range(5):
            event_id, ts = _insert_event(
                db,
                account_id=12345,
                category="control",
                severity="info",
                latency_ms=100 + i,
                payload={"msg": f"event_{i}"}
            )
            event_ids.append(event_id)

        response = app_client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5
        # Check they're newest first
        assert data[0]["id"] == event_ids[-1]
        assert data[-1]["id"] == event_ids[0]

    def test_events_filtering_by_severity_and_category(self, app_client, db):
        """GET /api/events filters by severity and category."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        # Insert events with different severity/category
        conn_id = _seed_connection(db)
        _seed_account(db, 12345, conn_id, 111111, True)

        _insert_event(db, account_id=12345, category="control", severity="info")
        _insert_event(db, account_id=12345, category="control", severity="warning")
        _insert_event(db, account_id=12345, category="connection", severity="error")
        _insert_event(db, account_id=12345, category="auth", severity="info")

        # Filter by severity=warning
        response = app_client.get("/api/events?severity=warning")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["severity"] == "warning"

        # Filter by category=connection
        response = app_client.get("/api/events?category=connection")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["category"] == "connection"

        # Filter by both severity and category
        response = app_client.get("/api/events?severity=info&category=control")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["severity"] == "info"
        assert data[0]["category"] == "control"

    def test_events_filtering_by_account_id(self, app_client, db):
        """GET /api/events filters by account_id."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        # Insert events from different accounts
        conn_id = _seed_connection(db)
        _seed_account(db, 12345, conn_id, 111111, True)
        _seed_account(db, 12346, conn_id, 222222, True)

        _insert_event(db, account_id=12345, category="control", severity="info")
        _insert_event(db, account_id=12346, category="control", severity="info")
        _insert_event(db, account_id=None, category="auth", severity="info")

        # Filter by account_id=12345
        response = app_client.get("/api/events?account_id=12345")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["account_id"] == 12345

    def test_events_since_and_limit(self, app_client, db):
        """GET /api/events with since and limit parameters."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        conn_id = _seed_connection(db)
        _seed_account(db, 12345, conn_id, 111111, True)

        # Insert 5 events
        event_ids_and_ts = []
        for i in range(5):
            event_id, ts = _insert_event(db, account_id=12345, category="control", severity="info")
            event_ids_and_ts.append((event_id, ts))

        # Get all events, limit to 2
        response = app_client.get("/api/events?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Should be newest first
        assert data[0]["id"] == event_ids_and_ts[-1][0]
        assert data[1]["id"] == event_ids_and_ts[-2][0]

        # Get events since a specific timestamp (using the ts from response)
        # The third event's timestamp
        response_all = app_client.get("/api/events")
        all_events = response_all.json()
        # Find the third event's timestamp from the response
        third_event_ts = all_events[len(all_events) - 3]["ts"]  # third oldest

        response = app_client.get(f"/api/events?since={third_event_ts}")
        assert response.status_code == 200
        data = response.json()
        # Should get events after the specified timestamp (newest 2)
        assert len(data) == 2
        assert data[0]["id"] == event_ids_and_ts[-1][0]

    def test_events_response_structure(self, app_client, db):
        """GET /api/events response has correct structure."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        conn_id = _seed_connection(db)
        _seed_account(db, 12345, conn_id, 111111, True)

        _insert_event(
            db,
            account_id=12345,
            category="control",
            severity="info",
            latency_ms=42,
            payload={"data": "test"}
        )

        response = app_client.get("/api/events")
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

    def test_events_malformed_since_400(self, app_client):
        """GET /api/events returns 400 for malformed since timestamp."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        response = app_client.get("/api/events?since=not-a-timestamp")
        assert response.status_code == 400
        assert "ISO 8601" in response.json()["detail"]

    def test_events_limit_capped_to_1000(self, app_client, db):
        """GET /api/events caps limit to 1000 maximum."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        conn_id = _seed_connection(db)
        _seed_account(db, 12345, conn_id, 111111, True)

        # Insert 10 events
        for i in range(10):
            _insert_event(db, account_id=12345, category="control", severity="info")

        # Request with limit > 1000 should be capped to 1000
        response = app_client.get("/api/events?limit=5000")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 10  # Only 10 events exist, but limit was capped to 1000

    def test_events_limit_min_1(self, app_client):
        """GET /api/events returns 400 for limit < 1."""
        # Login first
        response = app_client.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        response = app_client.get("/api/events?limit=0")
        assert response.status_code == 400


class TestWebSocket:
    """Tests for WebSocket /api/ws endpoint."""

    def test_ws_rejects_unauthenticated(self, app_client):
        """WebSocket connection is rejected without authentication with 4401 close code."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with app_client.websocket_connect("/api/ws"):
                pass
        assert exc_info.value.code == 4401

    def test_ws_accepts_authenticated(self, app_client_with_lifespan):
        """WebSocket connection is accepted with valid session cookie."""
        # Login first
        response = app_client_with_lifespan.post("/api/login", json={"password": "hunter2!"})
        assert response.status_code == 204

        # Should be able to connect to WS now
        try:
            with app_client_with_lifespan.websocket_connect("/api/ws") as ws:
                # Just check that connection succeeded
                pass
        except Exception as e:
            pytest.fail(f"WS connection should succeed with auth, got: {e}")

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
