"""Tests for authentication and CSRF/rate limiting."""
import os
import pytest


def test_empty_session_secret_fails(monkeypatch):
    """Empty SESSION_SECRET should fail loudly."""
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://copytrader:copytrader@localhost:5433/copytrader_test")
    monkeypatch.setenv("SESSION_SECRET", "")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "hunter2!")
    monkeypatch.setenv("COPIER_CONTROL_URL", "http://copier.test")

    from api.config import ApiConfig

    with pytest.raises(ValueError, match="SESSION_SECRET"):
        ApiConfig.from_env()


def test_empty_bootstrap_password_fails(monkeypatch):
    """Empty ADMIN_BOOTSTRAP_PASSWORD should fail loudly."""
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://copytrader:copytrader@localhost:5433/copytrader_test")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "")
    monkeypatch.setenv("COPIER_CONTROL_URL", "http://copier.test")

    from api.config import ApiConfig

    with pytest.raises(ValueError, match="ADMIN_BOOTSTRAP_PASSWORD"):
        ApiConfig.from_env()


def test_csrf_cookie_not_session_cookie(app_client):
    """CSRF cookie value must be different from session cookie value."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    session_cookie = response.cookies.get("session")
    csrf_cookie = response.cookies.get("csrf")

    # CSRF cookie must be different from session cookie
    assert session_cookie != csrf_cookie, "CSRF cookie must not equal session cookie"


def test_rate_limit_by_real_ip_not_xff(app_client):
    """Rate limiter should use real client IP, not spoofed X-Forwarded-For."""
    # Make 5 attempts from the real client IP
    for i in range(5):
        response = app_client.post(
            "/api/login",
            json={"password": "wrongpassword"},
        )
        assert response.status_code == 401

    # 6th attempt with spoofed X-Forwarded-For should still be rate limited
    # because the real client IP is the same
    response = app_client.post(
        "/api/login",
        json={"password": "wrongpassword"},
        headers={"X-Forwarded-For": "192.168.1.1"},
    )
    assert response.status_code == 429, "Rate limit should be by real IP, not X-Forwarded-For"


def test_mutation_no_csrf_cookie_403(app_client):
    """Mutation with no CSRF cookie should return 403."""
    # Try logout without being logged in (no cookies)
    response = app_client.post("/api/logout")
    assert response.status_code == 403


def test_tampered_session_cookie_401(app_client):
    """Tampered session cookie should be rejected with 401."""
    # Login successfully
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Try to access /api/me with tampered session cookie
    response = app_client.get(
        "/api/me",
        cookies={"session": "tampered-value"}
    )
    assert response.status_code == 401


def test_login_wrong_password_401(app_client):
    """Login with wrong password should return 401."""
    response = app_client.post("/api/login", json={"password": "wrongpassword"})
    assert response.status_code == 401


def test_login_sets_session_and_csrf_cookies(app_client):
    """Successful login should set both session and csrf cookies."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    cookies = response.cookies
    assert "session" in cookies
    assert "csrf" in cookies


def test_admin_password_stored_as_argon2(app_client, db):
    """Admin password should be stored as argon2 hash in database."""
    import psycopg

    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute("SELECT password_hash FROM admin WHERE id = TRUE").fetchone()
        password_hash = result[0]
        assert password_hash.startswith("$argon2")


def test_protected_route_401_without_session(app_client):
    """GET /api/me without session cookie should return 401."""
    response = app_client.get("/api/me")
    assert response.status_code == 401


def test_mutation_without_csrf_header_403(app_client):
    """Mutation without CSRF header should return 403."""
    # First login to get cookies
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Now try to logout without CSRF header
    response = app_client.post("/api/logout")
    assert response.status_code == 403


def test_mutation_with_csrf_header_ok(app_client):
    """Mutation with proper CSRF header should succeed."""
    # First login to get cookies
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Now logout with CSRF header
    response = app_client.post(
        "/api/logout",
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 204


def test_sixth_login_attempt_within_minute_429(app_client):
    """Sixth login attempt within a minute should return 429."""
    # Make 5 attempts with wrong password
    for i in range(5):
        response = app_client.post("/api/login", json={"password": "wrongpassword"})
        assert response.status_code == 401

    # 6th attempt should be rate limited
    response = app_client.post("/api/login", json={"password": "wrongpassword"})
    assert response.status_code == 429


def test_logout_clears_session(app_client):
    """Logout should clear session cookie."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Verify session is set
    response = app_client.get("/api/me")
    assert response.status_code == 200

    # Logout with CSRF header
    response = app_client.post(
        "/api/logout",
        headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 204

    # Session should be cleared
    response = app_client.get("/api/me")
    assert response.status_code == 401
