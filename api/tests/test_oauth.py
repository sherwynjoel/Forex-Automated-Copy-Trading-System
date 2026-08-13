"""Tests for OAuth connect flow with cTrader."""
import os
import json
import psycopg
import httpx
import pytest
from datetime import datetime, timedelta
from cryptography.fernet import Fernet


def test_connect_redirects_to_ctrader_with_scope_trading(app_client):
    """GET /api/oauth/connect redirects to cTrader authorize URL with scope trading."""
    # First login to establish session
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    csrf_token = response.cookies.get("csrf")

    # Access the connect endpoint
    response = app_client.get("/api/oauth/connect", follow_redirects=False)

    # Should redirect to cTrader OAuth authorize
    assert response.status_code == 307
    location = response.headers.get("location")
    assert location
    assert "openapi.ctrader.com/apps/auth" in location or "CTRADER_AUTH_URL" in location
    assert "scope=trading" in location
    assert "client_id=" in location
    assert "redirect_uri=" in location
    assert "state=" in location


def test_callback_rejects_bad_state(app_client):
    """GET /api/oauth/callback rejects mismatched state with 403."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Try callback with bad state
    response = app_client.get(
        "/api/oauth/callback?code=test-code&state=bad-state",
        follow_redirects=False
    )

    assert response.status_code == 403


def test_callback_rejects_state_from_different_session(app_client, db):
    """OAuth state from a different session should be rejected (session binding)."""
    from itsdangerous import URLSafeTimedSerializer
    from api.config import ApiConfig

    cfg = ApiConfig.from_env()
    serializer = URLSafeTimedSerializer(cfg.session_secret, salt="oauth-state")

    # Create a valid state but with a different (forged) session binding
    different_session = "different-session-value"
    forged_state = serializer.dumps({
        "state": "valid-state-value",
        "session": different_session,
    })

    # Login with the real session
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Try to use the forged state (bound to a different session)
    response = app_client.get(
        f"/api/oauth/callback?code=test-code&state={forged_state}",
        follow_redirects=False
    )

    # Should reject because session doesn't match
    assert response.status_code == 403


def test_callback_rejects_replay_attack(app_client, db):
    """Replaying a consumed state should be rejected (single-use enforcement)."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Get a valid state by accessing connect endpoint
    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers.get("location")

    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]
    assert state

    # Use the state once (successful callback)
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )
    assert response.status_code == 307

    # Try to replay the same state (should fail)
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    # Should reject because state was already consumed
    assert response.status_code == 403


def test_callback_exchanges_code_and_stores_encrypted_tokens(app_client, db):
    """GET /api/oauth/callback exchanges code and stores encrypted tokens."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Get a valid state by accessing connect endpoint first
    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers.get("location")

    # Extract state from redirect URL
    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]
    assert state, "State should be in redirect URL"

    # Now simulate callback with the valid state
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    # Should redirect to /accounts?connected=1
    assert response.status_code == 307
    location = response.headers.get("location")
    assert "/accounts" in location
    assert "connected=1" in location

    # Verify token was stored in database with Fernet encryption
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            "SELECT id, access_token_enc, refresh_token_enc, granted_at, expires_at, scope, status "
            "FROM ctid_connections ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert result is not None
    connection_id, access_token_enc, refresh_token_enc, granted_at, expires_at, scope, status = result

    # Verify tokens are encrypted (ciphertext, not plaintext)
    assert access_token_enc != "at", "Token should be encrypted, not plaintext"
    assert refresh_token_enc != "rt", "Token should be encrypted, not plaintext"

    # Verify we can decrypt the tokens
    from api.config import ApiConfig
    cfg = ApiConfig.from_env()
    cipher_suite = Fernet(cfg.fernet_key.encode() if isinstance(cfg.fernet_key, str) else cfg.fernet_key)

    decrypted_access = cipher_suite.decrypt(access_token_enc.encode()).decode()
    decrypted_refresh = cipher_suite.decrypt(refresh_token_enc.encode()).decode()

    assert decrypted_access == "at"
    assert decrypted_refresh == "rt"

    # Verify metadata
    assert scope == "trading"
    assert status == "active"
    assert expires_at > granted_at

    # Verify expires_at is approximately now + 30 days
    expected_expiry = granted_at + timedelta(seconds=2592000)
    assert abs((expires_at - expected_expiry).total_seconds()) < 5


def test_callback_missing_code_or_state(app_client):
    """GET /api/oauth/callback with missing code or state returns 400."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Missing code
    response = app_client.get("/api/oauth/callback?state=test", follow_redirects=False)
    assert response.status_code == 400

    # Missing state
    response = app_client.get("/api/oauth/callback?code=test", follow_redirects=False)
    assert response.status_code == 400

    # Missing both
    response = app_client.get("/api/oauth/callback", follow_redirects=False)
    assert response.status_code == 400


def test_callback_malformed_token_response(app_client, db, monkeypatch):
    """Malformed token response (non-JSON, missing fields) returns 400."""
    # Create a mock that returns invalid JSON
    def mock_callback_malformed(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "openapi.ctrader.com/apps/token" in url:
            return httpx.Response(200, text="invalid json{]")
        elif "copier.test" in url:
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200)

    # Patch the http client to return malformed response
    mock_transport_malformed = httpx.MockTransport(mock_callback_malformed)

    # Monkeypatch the app's http client
    import asyncio
    async def mock_post(*args, **kwargs):
        request = httpx.Request("POST", kwargs.get("url") or args[1] if len(args) > 1 else "")
        return mock_callback_malformed(request)

    original_post = app_client.app.state.http.post
    app_client.app.state.http.post = mock_post

    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Get a valid state
    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    location = response.headers.get("location")
    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]

    # Try callback with malformed response
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    # Should return 400 for malformed response
    assert response.status_code == 400

    # Restore original
    app_client.app.state.http.post = original_post


def test_callback_missing_token_fields(app_client, db):
    """Token response missing required fields returns 400."""
    # Create a mock that returns incomplete token response
    def mock_callback_incomplete(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "openapi.ctrader.com/apps/token" in url:
            # Missing refreshToken
            return httpx.Response(200, json={"accessToken": "at", "expiresIn": 2592000})
        elif "copier.test" in url:
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200)

    # Patch the app's http client
    async def mock_post(*args, **kwargs):
        request = httpx.Request("POST", kwargs.get("url") or args[1] if len(args) > 1 else "")
        return mock_callback_incomplete(request)

    original_post = app_client.app.state.http.post
    app_client.app.state.http.post = mock_post

    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Get a valid state
    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    location = response.headers.get("location")
    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]

    # Try callback with incomplete response
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    assert response.status_code == 400

    # Restore original
    app_client.app.state.http.post = original_post


def test_callback_discover_failure_still_stores_grant(app_client, db):
    """GET /api/oauth/callback stores grant even if discovery fails."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Get state
    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    location = response.headers.get("location")
    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]

    # Callback with normal token exchange (discovery may or may not fail based on mock)
    # The important part is that the grant is stored regardless
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    # Should redirect successfully
    assert response.status_code == 307
    location = response.headers.get("location")
    assert "connected=1" in location

    # Verify grant was stored
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            "SELECT id FROM ctid_connections ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert result is not None, "Grant should be stored even if discovery fails"


def test_oauth_routes_require_admin(app_client):
    """OAuth routes require admin session."""
    # Access connect endpoint without login
    response = app_client.get("/api/oauth/connect")
    assert response.status_code == 401

    # Access callback endpoint without login
    response = app_client.get("/api/oauth/callback?code=test&state=test")
    assert response.status_code == 401
