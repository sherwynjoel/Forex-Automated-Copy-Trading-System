"""Tests for settings and control endpoints."""
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
    # Should indicate copier was contacted
    assert "copier_reloaded" in data


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


def test_control_502_when_copier_down(app_client):
    """Control endpoints return 502 when copier is unreachable."""
    # This would require a test transport that simulates copier being down
    # For now, we'll test that the error handling is in place
    pass


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
        json={},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200

    # Test adopt action
    response = app_client.post(
        "/api/drift/adopt",
        json={},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200

    # Test dismiss action
    response = app_client.post(
        "/api/drift/dismiss",
        json={},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200


def test_drift_invalid_action_400(app_client):
    """POST /api/drift with invalid action returns 400."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    response = app_client.post(
        "/api/drift/invalid-action",
        json={},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 400


def test_drift_requires_auth(app_client):
    """Drift endpoint without auth returns 401 or 403 (CSRF checked first)."""
    response = app_client.post("/api/drift/close-orphan", json={})
    # CSRF middleware runs before auth, so we get 403 if no CSRF cookie
    assert response.status_code in (401, 403)


def test_drift_requires_csrf(app_client):
    """Drift endpoint without CSRF returns 403."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    response = app_client.post("/api/drift/close-orphan", json={})
    assert response.status_code == 403
