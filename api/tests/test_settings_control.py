"""Tests for settings and control endpoints."""
import json

import httpx
import psycopg
import pytest


def test_settings_kill_switch_roundtrip(app_client, db):
    """GET /api/settings and PUT to update copying_enabled."""
    # Login first
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # GET current settings
    response = app_client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "copying_enabled" in data
    assert "dry_run" in data
    assert "shards" in data

    # PUT to disable copying
    response = app_client.put(
        "/api/settings",
        json={"copying_enabled": False},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["copying_enabled"] is False

    # Verify in DB
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute("SELECT copying_enabled FROM settings WHERE id = TRUE").fetchone()
        assert result[0] is False


def test_settings_dry_run_forwards_the_requested_value(app_client, db):
    """N1: PUT /api/settings {"dry_run": true} must proxy the REAL value.

    RED before the fix: this route posted a hardcoded `json={}` to the
    copier's /dry-run, whose DryRunResource reads `body.get("enabled",
    False)` -> False -> `set_dry_run(False)` -> the settings row it had just
    written `true` to was immediately rewritten to `false`. The response,
    built from a row read BEFORE the proxy call, still reported
    `dry_run: true, dry_run_applied: true`. So dry-run could not be turned
    ON through the API at all, the caller was told it had been, and dispatch
    (which re-reads settings per batch) kept sending real orders while the
    operator believed nothing would be sent -- the Stage-1 rollout gate.

    The old test asserted only that /dry-run had been CALLED, which is what
    let the defect through: assertion theater at exactly this seam.
    """
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    dry_run_bodies = []
    settings_after_proxy = {}

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "copier.test" in url and "/dry-run" in url:
            body = json.loads(request.content or b"{}")
            dry_run_bodies.append(body)
            # Behave like the real copier: write what the body says (the
            # absent-key default is False) straight back to the settings row.
            enabled = bool(body.get("enabled", False))
            with psycopg.connect(db, autocommit=True) as conn:
                conn.execute("UPDATE settings SET dry_run = %s WHERE id = TRUE", (enabled,))
                settings_after_proxy["dry_run"] = conn.execute(
                    "SELECT dry_run FROM settings WHERE id = TRUE"
                ).fetchone()[0]
            return httpx.Response(200, json={"status": "ok", "dry_run": enabled})
        from conftest import default_mock_callback
        return default_mock_callback(request)

    app_client.app.state.mock_transport = httpx.MockTransport(tracking_callback)
    app_client.app.state.http = httpx.AsyncClient(transport=app_client.app.state.mock_transport)

    response = app_client.put(
        "/api/settings", json={"dry_run": True}, headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["dry_run_applied"] is True

    assert dry_run_bodies == [{"enabled": True}], dry_run_bodies
    # The end state the operator is actually relying on.
    assert settings_after_proxy["dry_run"] is True
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT dry_run FROM settings WHERE id = TRUE").fetchone()[0] is True


def test_settings_dry_run_off_forwards_false(app_client, db):
    """The other direction must forward `enabled: false`, not simply omit it."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    bodies = []

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "copier.test" in url and "/dry-run" in url:
            bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"status": "ok"})
        from conftest import default_mock_callback
        return default_mock_callback(request)

    app_client.app.state.mock_transport = httpx.MockTransport(tracking_callback)
    app_client.app.state.http = httpx.AsyncClient(transport=app_client.app.state.mock_transport)

    response = app_client.put(
        "/api/settings", json={"dry_run": False}, headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert bodies == [{"enabled": False}]


def test_settings_dry_run_triggers_copier_calls(app_client, db):
    """Changing dry_run should call copier /reload and /dry-run."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Update dry_run
    response = app_client.put(
        "/api/settings",
        json={"dry_run": True},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is True
    # Should indicate copier was contacted and successful
    assert "copier_reloaded" in data
    assert data["copier_reloaded"] is True
    # Dry-run should also be applied
    assert "dry_run_applied" in data
    assert data["dry_run_applied"] is True


def test_settings_copying_enabled_triggers_copier_reload(app_client, db):
    """Changing copying_enabled (kill switch) should call copier /reload."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Update copying_enabled (the kill switch)
    response = app_client.put(
        "/api/settings",
        json={"copying_enabled": False},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["copying_enabled"] is False
    # Should indicate copier was contacted
    assert "copier_reloaded" in data
    assert data["copier_reloaded"] is True


def test_settings_shards_triggers_copier_reload(app_client, db):
    """Changing shards should call copier /reload."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Update shards
    response = app_client.put(
        "/api/settings",
        json={"shards": 2},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["shards"] == 2
    # Should indicate copier was contacted
    assert "copier_reloaded" in data
    assert data["copier_reloaded"] is True


def test_settings_requires_auth(app_client):
    """GET /api/settings without auth returns 401."""
    response = app_client.get("/api/settings")
    assert response.status_code == 401


def test_settings_put_requires_csrf(app_client):
    """PUT /api/settings without CSRF returns 403."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    response = app_client.put(
        "/api/settings",
        json={"copying_enabled": False}
    )
    assert response.status_code == 403


def test_control_pause_proxies_to_copier(app_client):
    """POST /api/control/pause proxies to copier and returns 200."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/control/pause",
        json={"account_id": None},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    data = response.json()
    # Response should be the copier response or at least indicate success
    assert "status" in data or "detail" in data


def test_control_pause_with_account_id(app_client):
    """POST /api/control/pause with account_id."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/control/pause",
        json={"account_id": 12345},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200


def test_control_pause_forwards_payload(app_client):
    """POST /api/control/pause should forward account_id in request body."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Create a tracker to verify the payload is forwarded
    received_requests = []

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        """Track requests to copier."""
        url = str(request.url)
        if "copier.test" in url and "/pause" in url:
            # Capture the request for assertion
            received_requests.append({
                "url": url,
                "method": request.method,
                "body": request.content,
            })
            return httpx.Response(200, json={"status": "ok"})
        # Fall back to default
        from conftest import default_mock_callback
        return default_mock_callback(request)

    # Replace mock transport
    app_client.app.state.mock_transport = httpx.MockTransport(tracking_callback)
    app_client.app.state.http = httpx.AsyncClient(
        transport=app_client.app.state.mock_transport
    )

    # Send pause with account_id
    response = app_client.post(
        "/api/control/pause",
        json={"account_id": 5678},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200

    # Verify the copier received the correct payload
    assert len(received_requests) == 1
    request_info = received_requests[0]
    assert request_info["method"] == "POST"
    assert b"5678" in request_info["body"]  # account_id in request body


def test_control_resume_proxies_to_copier(app_client):
    """POST /api/control/resume proxies to copier."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/control/resume",
        json={"account_id": None},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200


def test_control_resync_proxies_to_copier(app_client):
    """POST /api/control/resync proxies to copier."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/control/resync",
        json={},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200


def test_control_502_when_copier_down(app_client, copier_error_response):
    """Control endpoints return 502 when copier connection fails."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Configure copier to raise connection error
    copier_error_response.raise_connect_error()

    # Should return 502 when copier is unreachable
    response = app_client.post(
        "/api/control/pause",
        json={"account_id": None},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 502
    assert "unreachable" in response.json().get("detail", "").lower()

    copier_error_response.reset()


def test_control_pause_forwards_4xx_from_copier(app_client, copier_error_response):
    """Control pause should forward copier 4xx errors with detail, not convert to 200."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Configure copier to return 404 with detail
    copier_error_response.return_status_json(
        404,
        {"detail": "Account not found in copier"}
    )

    # Should forward the 404 with copier's detail
    response = app_client.post(
        "/api/control/pause",
        json={"account_id": 99999},  # Non-existent account
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 404
    assert "Account not found" in response.json().get("detail", "")

    copier_error_response.reset()


def test_control_handles_malformed_copier_response(app_client, copier_error_response):
    """Control endpoints should return 502 for non-JSON copier responses (not 500)."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Configure copier to return 200 with non-JSON body
    copier_error_response.return_non_json(200, b"Internal server error")

    # Should return 502 on malformed response, not 500
    response = app_client.post(
        "/api/control/pause",
        json={"account_id": None},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 502
    assert "invalid response" in response.json().get("detail", "").lower()

    copier_error_response.reset()


def test_control_requires_auth(app_client):
    """Control endpoints without auth return 401 or 403 (CSRF checked first)."""
    response = app_client.post(
        "/api/control/pause",
        json={"account_id": None}
    )
    # CSRF middleware runs before auth, so we get 403 if no CSRF cookie
    assert response.status_code in (401, 403)


def test_control_requires_csrf(app_client):
    """Control endpoints without CSRF return 403."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    response = app_client.post(
        "/api/control/pause",
        json={"account_id": None}
    )
    assert response.status_code == 403


def test_state_proxy_passthrough(app_client):
    """GET /api/state proxies to copier GET /state."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    response = app_client.get("/api/state")
    assert response.status_code == 200
    # Should be valid JSON response from copier
    data = response.json()
    assert isinstance(data, dict)


def test_state_proxy_requires_auth(app_client):
    """GET /api/state without auth returns 401."""
    response = app_client.get("/api/state")
    assert response.status_code == 401


def test_drift_action_proxy(app_client):
    """POST /api/drift/{action} proxies to copier."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Test close-orphan action
    response = app_client.post(
        "/api/drift/close-orphan",
        json={"id": "drift-abc"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200

    # Test adopt action
    response = app_client.post(
        "/api/drift/adopt",
        json={"id": "drift-abc", "master_position_id": 4242},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200

    # Test dismiss action
    response = app_client.post(
        "/api/drift/dismiss",
        json={"id": "drift-abc"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200


def test_drift_action_forwards_the_request_body(app_client):
    """N3: the drift remedies must forward the body the dashboard sends.

    RED before the fix: this route posted a hardcoded `json={}` and never
    read the incoming body at all, so the copier's drift resources raised
    `ValueError("id required")` -> 500 -> 502 here. All three of spec §7's
    one-click remedies -- and an explicit Stage-3 runbook step -- failed on
    every single click, while the api's own suite asserted only that the
    call had happened against a mock.
    """
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    received = []

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "copier.test" in url and "/drift/" in url:
            received.append({"url": url, "body": json.loads(request.content or b"{}")})
            return httpx.Response(200, json={"status": "ok"})
        from conftest import default_mock_callback
        return default_mock_callback(request)

    app_client.app.state.mock_transport = httpx.MockTransport(tracking_callback)
    app_client.app.state.http = httpx.AsyncClient(transport=app_client.app.state.mock_transport)

    # close-orphan / dismiss carry only the drift item id...
    for action in ("close-orphan", "dismiss"):
        response = app_client.post(
            f"/api/drift/{action}",
            json={"id": f"item-for-{action}"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200

    # ...adopt additionally carries the master position to adopt under.
    response = app_client.post(
        "/api/drift/adopt",
        json={"id": "item-for-adopt", "master_position_id": 987654},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200

    assert len(received) == 3
    by_action = {r["url"].rsplit("/", 1)[-1]: r["body"] for r in received}
    assert by_action["close-orphan"] == {"id": "item-for-close-orphan"}
    assert by_action["dismiss"] == {"id": "item-for-dismiss"}
    assert by_action["adopt"] == {"id": "item-for-adopt", "master_position_id": 987654}


def test_drift_action_without_an_id_is_rejected_before_the_copier(app_client):
    """`id` is required by every copier drift resource, so a body without one
    is a client error (422), not a 502 from a copier ValueError."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/drift/close-orphan", json={}, headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 422


def test_drift_invalid_action_400(app_client):
    """POST /api/drift with invalid action returns 400."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/drift/invalid-action",
        json={"id": "whatever"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 400


def test_drift_requires_auth(app_client):
    """Drift endpoint without auth returns 401 or 403 (CSRF checked first)."""
    response = app_client.post("/api/drift/close-orphan", json={"id": "x"})
    # CSRF middleware runs before auth, so we get 403 if no CSRF cookie
    assert response.status_code in (401, 403)


def test_drift_requires_csrf(app_client):
    """Drift endpoint without CSRF returns 403."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    response = app_client.post("/api/drift/close-orphan", json={"id": "x"})
    assert response.status_code == 403
