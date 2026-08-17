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


def _state_from_connect(app_client) -> str:
    """Drive GET /api/oauth/connect and return the `state` it minted."""
    import urllib.parse

    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    assert response.status_code == 307
    params = urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).query)
    state = params.get("state", [None])[0]
    assert state
    return state


def test_oauth_state_does_not_carry_the_session_cookie(app_client, db):
    """N4: the state parameter must be an opaque nonce and nothing else.

    RED before the fix: state was
    `URLSafeTimedSerializer(...).dumps({"state": ..., "session": session})`
    -- SIGNED, NOT ENCRYPTED -- so its payload was plain base64 JSON
    containing the full, currently-valid admin session cookie. That value
    travelled in a query string to openapi.ctrader.com (their request logs),
    sat in browser history, and came back in the callback URL; anyone who
    read it could decode it and replay the admin session for up to 12 h.
    """
    import base64

    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    session_cookie = app_client.cookies.get("session")
    assert session_cookie

    state = _state_from_connect(app_client)

    # Nothing recoverable from the state resembles the session, whether read
    # raw or base64-decoded the way itsdangerous' payload used to be.
    assert session_cookie not in state
    decodable = [state] + state.split(".")
    for chunk in decodable:
        padded = chunk + "=" * (-len(chunk) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
        except Exception:
            continue
        assert session_cookie not in decoded
        assert "session" not in decoded.lower()


def test_oauth_state_is_stored_only_as_a_digest(app_client, db):
    """Neither the nonce nor the session is recoverable from the database."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    session_cookie = app_client.cookies.get("session")

    state = _state_from_connect(app_client)

    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute("SELECT state_hash, session FROM oauth_states").fetchall()

    assert len(rows) == 1
    state_hash, stored_session = rows[0]
    assert state_hash != state
    assert stored_session != session_cookie
    # Both are SHA-256 hex digests bound to the real values.
    import hashlib
    assert state_hash == hashlib.sha256(state.encode()).hexdigest()
    assert stored_session == hashlib.sha256(session_cookie.encode()).hexdigest()


def test_callback_rejects_state_from_different_session(app_client, db):
    """N4: a state minted under one admin session must not be usable under
    another (session binding survives the switch to an opaque nonce).

    Mints a real state in session A, then presents it while holding a
    DIFFERENT but equally VALID admin session -- the shape a CSRF/session-
    fixation attempt actually takes now that the state carries no session
    payload of its own to forge. (Session B is minted directly rather than
    by logging in twice: the session cookie is a signed
    `{"authenticated": true}` with a second-granular timestamp, so two
    logins in the same second produce a byte-identical cookie and would not
    be a different session at all.)
    """
    from itsdangerous import URLSafeTimedSerializer
    from api.config import ApiConfig

    # --- session A mints a state ---
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    session_a = app_client.cookies.get("session")
    state = _state_from_connect(app_client)

    # --- session B: a different, still-valid admin session ---
    cfg = ApiConfig.from_env()
    session_b = URLSafeTimedSerializer(cfg.session_secret, salt="session").dumps(
        {"authenticated": True, "sid": "another-browser"}
    )
    assert session_b != session_a
    app_client.cookies.set("session", session_b)

    response = app_client.get(
        f"/api/oauth/callback?code=test-code&state={state}",
        follow_redirects=False
    )
    assert response.status_code == 403

    # ...and the mismatch must NOT have burned the legitimate state: the
    # atomic consume includes the session predicate, so session A can still
    # complete its own flow.
    with psycopg.connect(db, autocommit=True) as conn:
        consumed = conn.execute("SELECT consumed_at FROM oauth_states").fetchone()[0]
    assert consumed is None


def test_callback_rejects_an_expired_state(app_client, db):
    """The TTL that used to ride along in the signed state is now enforced
    against oauth_states.created_at."""
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204
    state = _state_from_connect(app_client)

    from api.oauth import STATE_TTL_SECONDS
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE oauth_states SET created_at = now() - make_interval(secs => %s)",
            (STATE_TTL_SECONDS + 60,),
        )

    response = app_client.get(
        f"/api/oauth/callback?code=test-code&state={state}", follow_redirects=False,
    )
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


def test_callback_missing_code(app_client):
    """GET /api/oauth/callback without a code returns 400; a code without
    state and without any pending connect flow returns 403 (no nonce to
    consume), not 400 -- cTrader never echoes state, so its absence alone
    is not an error."""
    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Missing code (state present)
    response = app_client.get("/api/oauth/callback?state=test", follow_redirects=False)
    assert response.status_code == 400

    # Missing both
    response = app_client.get("/api/oauth/callback", follow_redirects=False)
    assert response.status_code == 400

    # Code but no state AND no pending flow for this session
    response = app_client.get("/api/oauth/callback?code=test", follow_redirects=False)
    assert response.status_code == 403


def test_callback_without_state_consumes_pending_session_nonce(app_client, db):
    """The real cTrader callback shape: `?code=...` with NO state echoed.

    RED before the fix: every live OAuth attempt died with 400 "Missing
    authorization code or state" because cTrader's authorize endpoint only
    appends `code` to the redirect. The callback must fall back to the
    pending nonce bound to this admin session -- and consuming it must stay
    single-use.
    """
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Mint a pending state via connect (the browser leg cTrader won't return)
    state = _state_from_connect(app_client)
    assert state

    # Callback exactly as cTrader sends it: code only
    response = app_client.get(
        "/api/oauth/callback?code=test-auth-code", follow_redirects=False
    )
    assert response.status_code == 307
    assert "connected=1" in response.headers.get("location", "")

    # The pending nonce was consumed and a grant stored
    with psycopg.connect(db, autocommit=True) as conn:
        consumed = conn.execute("SELECT consumed_at FROM oauth_states").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM ctid_connections").fetchone()[0]
    assert consumed is not None
    assert count == 1

    # Replay without state finds no pending nonce left -> rejected
    response = app_client.get(
        "/api/oauth/callback?code=test-auth-code", follow_redirects=False
    )
    assert response.status_code == 403


def test_callback_malformed_token_response(app_client, db):
    """Malformed token response (non-JSON) returns 400."""
    # Create a mock that returns invalid JSON
    def mock_callback_malformed(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "openapi.ctrader.com/apps/token" in url:
            return httpx.Response(200, text="invalid json{]")
        elif "copier.test" in url:
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200)

    # Patch the app's http client (URL is first positional arg)
    async def mock_post(url, *args, **kwargs):
        request = httpx.Request("POST", url)
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
    """Token response missing required fields (refreshToken) returns 400."""
    # Create a mock that returns incomplete token response (missing refreshToken)
    def mock_callback_incomplete(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "openapi.ctrader.com/apps/token" in url:
            # Missing refreshToken - this should trigger field validation, not JSON parse error
            return httpx.Response(200, json={"accessToken": "at", "expiresIn": 2592000})
        elif "copier.test" in url:
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200)

    # Patch the app's http client (URL is first positional arg in post(url, data=..., timeout=...))
    async def mock_post(url, *args, **kwargs):
        request = httpx.Request("POST", url)
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

    # Try callback with incomplete response (missing refreshToken)
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    # Should return 400 due to missing refreshToken field validation
    assert response.status_code == 400

    # Restore original
    app_client.app.state.http.post = original_post


def test_callback_discover_failure_still_stores_grant(app_client, db):
    """GET /api/oauth/callback stores grant even if discovery POST fails."""
    # Create a mock that fails on discovery
    def mock_callback_discover_fail(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "openapi.ctrader.com/apps/token" in url:
            return httpx.Response(200, json={"accessToken": "at", "refreshToken": "rt", "expiresIn": 2592000})
        elif "copier.test" in url:
            # Make discovery fail with 500
            return httpx.Response(500, json={"error": "discovery failed"})
        return httpx.Response(200)

    # Patch the app's http client to fail discovery
    async def mock_post(url, *args, **kwargs):
        request = httpx.Request("POST", url)
        return mock_callback_discover_fail(request)

    original_post = app_client.app.state.http.post
    app_client.app.state.http.post = mock_post

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

    # Callback (discovery will fail but grant should still be stored)
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )

    # Should redirect with warning but still succeed
    assert response.status_code == 307
    location = response.headers.get("location")
    assert "connected=1" in location
    assert "warning=discover_failed" in location

    # Verify grant was stored DESPITE discovery failure
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            "SELECT id FROM ctid_connections ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert result is not None, "Grant should be stored even if discovery fails"

    # Restore original
    app_client.app.state.http.post = original_post


def test_callback_concurrency_replay_prevention(app_client, db):
    """Concurrent callbacks with same state are atomically prevented (race-safe single-use)."""
    import asyncio
    import threading

    # First login
    response = app_client.post("/api/login", json={"password": "hunter2!"})
    assert response.status_code == 204

    # Get a valid state
    response = app_client.get("/api/oauth/connect", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers.get("location")

    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]
    assert state

    # Use the state once (first callback succeeds)
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )
    assert response.status_code == 307, "First callback should succeed"

    # Immediately try to replay the same state (second callback fails)
    response = app_client.get(
        f"/api/oauth/callback?code=test-auth-code&state={state}",
        follow_redirects=False
    )
    assert response.status_code == 403, "Replay should be rejected (already consumed)"

    # Verify only ONE grant was stored (not two from concurrent/race conditions)
    with psycopg.connect(db, autocommit=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM ctid_connections").fetchone()[0]

    assert count == 1, "Only one grant should exist (atomic consume prevents double-consumption)"


def test_oauth_routes_require_admin(app_client):
    """OAuth routes require admin session."""
    # Access connect endpoint without login
    response = app_client.get("/api/oauth/connect")
    assert response.status_code == 401

    # Access callback endpoint without login
    response = app_client.get("/api/oauth/callback?code=test&state=test")
    assert response.status_code == 401
