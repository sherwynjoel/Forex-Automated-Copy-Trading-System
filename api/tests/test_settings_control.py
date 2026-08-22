"""Tests for org-scoped settings and control endpoints."""
import json

import httpx
import psycopg
import pytest


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_settings_kill_switch_roundtrip(org_client, db):
    """GET /api/orgs/{org_id}/settings and PUT to update copying_enabled."""
    client, org_id, seed = org_client

    # GET current settings
    response = client.get(f"/api/orgs/{org_id}/settings")
    assert response.status_code == 200
    data = response.json()
    assert "copying_enabled" in data
    assert "dry_run" in data

    # PUT to disable copying
    response = client.put(
        f"/api/orgs/{org_id}/settings",
        json={"copying_enabled": False},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["copying_enabled"] is False

    # Verify in DB
    with psycopg.connect(db, autocommit=True) as conn:
        result = conn.execute(
            "SELECT copying_enabled FROM orgs WHERE id = %s", (org_id,)
        ).fetchone()
        assert result[0] is False


def test_settings_dry_run_forwards_the_requested_value(org_client, db):
    """N1: PUT /api/orgs/{org_id}/settings {"dry_run": true} must proxy the
    REAL value.

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
    client, org_id, seed = org_client

    dry_run_bodies = []
    settings_after_proxy = {}

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "copier.test" in url and "/dry-run" in url:
            body = json.loads(request.content or b"{}")
            dry_run_bodies.append(body)
            # Behave like the real copier: write what the body says (the
            # absent-key default is False) straight back to the org row.
            enabled = bool(body.get("enabled", False))
            with psycopg.connect(db, autocommit=True) as conn:
                conn.execute("UPDATE orgs SET dry_run = %s WHERE id = %s", (enabled, org_id))
                settings_after_proxy["dry_run"] = conn.execute(
                    "SELECT dry_run FROM orgs WHERE id = %s", (org_id,)
                ).fetchone()[0]
            return httpx.Response(200, json={"status": "ok", "dry_run": enabled})
        from conftest import default_mock_callback
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(tracking_callback)

    response = client.put(
        f"/api/orgs/{org_id}/settings", json={"dry_run": True}, headers=_csrf(client),
    )
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["dry_run_applied"] is True

    assert dry_run_bodies == [{"org_id": org_id, "enabled": True,
                               "actor_email": "admin@example.com"}], dry_run_bodies
    # The end state the operator is actually relying on.
    assert settings_after_proxy["dry_run"] is True
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute(
            "SELECT dry_run FROM orgs WHERE id = %s", (org_id,)
        ).fetchone()[0] is True


def test_settings_dry_run_off_forwards_false(org_client):
    """The other direction must forward `enabled: false`, not simply omit it."""
    client, org_id, seed = org_client

    bodies = []

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "copier.test" in url and "/dry-run" in url:
            bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"status": "ok"})
        from conftest import default_mock_callback
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(tracking_callback)

    response = client.put(
        f"/api/orgs/{org_id}/settings", json={"dry_run": False}, headers=_csrf(client),
    )
    assert response.status_code == 200
    assert bodies == [{"org_id": org_id, "enabled": False, "actor_email": "admin@example.com"}]


def test_settings_dry_run_triggers_copier_calls(org_client):
    """Changing dry_run should call copier /reload and /dry-run."""
    client, org_id, seed = org_client

    response = client.put(
        f"/api/orgs/{org_id}/settings",
        json={"dry_run": True},
        headers=_csrf(client),
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


def test_settings_copying_enabled_triggers_copier_reload(org_client):
    """Changing copying_enabled (kill switch) should call copier /reload."""
    client, org_id, seed = org_client

    response = client.put(
        f"/api/orgs/{org_id}/settings",
        json={"copying_enabled": False},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["copying_enabled"] is False
    # Should indicate copier was contacted
    assert "copier_reloaded" in data
    assert data["copier_reloaded"] is True


def test_settings_update_returns_two_field_shape(org_client):
    """SettingsResponse only ever carries copying_enabled/dry_run -- the
    process-level `shards` knob is no longer API-exposed. (Was
    test_settings_shards_triggers_copier_reload, which PUT `shards` and
    asserted it echoed back; that's meaningless now that shards isn't part
    of the org-scoped API surface, so this asserts the new two-field shape
    instead.)"""
    client, org_id, seed = org_client

    response = client.put(
        f"/api/orgs/{org_id}/settings",
        json={"copying_enabled": False, "dry_run": True},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["copying_enabled"] is False
    assert data["dry_run"] is True
    assert "shards" not in data
    assert data["copier_reloaded"] is True
    assert data["dry_run_applied"] is True


def test_settings_are_per_org(org_client, make_user, make_org, login_as):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/settings",
                   json={"copying_enabled": False, "dry_run": True},
                   headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["copying_enabled"] is False and r.json()["dry_run"] is True

    # a different org still has defaults
    other = make_user(email="o2@example.com")
    other_org = make_org(name="Two", members=[(other, "admin")])
    login_as(client, other)
    body = client.get(f"/api/orgs/{other_org}/settings").json()
    assert body == {"copying_enabled": True, "dry_run": False}


def test_settings_requires_auth(app_client):
    """GET /api/orgs/{org_id}/settings without auth returns 401."""
    response = app_client.get("/api/orgs/1/settings")
    assert response.status_code == 401


def test_settings_put_requires_csrf(org_client):
    """PUT /api/orgs/{org_id}/settings without CSRF returns 403."""
    client, org_id, seed = org_client

    response = client.put(
        f"/api/orgs/{org_id}/settings",
        json={"copying_enabled": False},
    )
    assert response.status_code == 403


def test_control_pause_proxies_to_copier(org_client):
    """POST /api/orgs/{org_id}/control/pause proxies to copier and returns 200."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": None},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    data = response.json()
    # Response should be the copier response or at least indicate success
    assert "status" in data or "detail" in data


def test_control_pause_forwards_org_id(org_client):
    """The copier must receive the org context on every scoped command."""
    import json as jsonlib
    client, org_id, seed = org_client
    captured = {}

    import httpx
    from conftest import default_mock_callback

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            captured["url"] = str(request.url)
            captured["body"] = jsonlib.loads(request.content or b"{}")
            return httpx.Response(200, json={"status": "paused"})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    r = client.post(f"/api/orgs/{org_id}/control/pause", json={},
                    headers=_csrf(client))
    assert r.status_code == 200
    assert captured["url"].endswith("/pause")
    assert captured["body"]["org_id"] == org_id


def test_control_pause_with_account_id(org_client):
    """POST /api/orgs/{org_id}/control/pause with account_id."""
    client, org_id, seed = org_client
    seed(12345)

    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": 12345},
        headers=_csrf(client),
    )
    assert response.status_code == 200


def test_control_pause_account_not_in_org_404(org_client):
    """A non-null account_id must be verified against this org before the
    copier is ever contacted."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": 999999},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_control_pause_forwards_payload(org_client):
    """POST /api/orgs/{org_id}/control/pause should forward account_id in
    request body."""
    client, org_id, seed = org_client
    seed(5678)

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
    client.app.state.mock_transport.set_callback(tracking_callback)

    # Send pause with account_id
    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": 5678},
        headers=_csrf(client),
    )
    assert response.status_code == 200

    # Verify the copier received the correct payload
    assert len(received_requests) == 1
    request_info = received_requests[0]
    assert request_info["method"] == "POST"
    assert b"5678" in request_info["body"]  # account_id in request body


def test_control_resume_proxies_to_copier(org_client):
    """POST /api/orgs/{org_id}/control/resume proxies to copier."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/control/resume",
        json={"account_id": None},
        headers=_csrf(client),
    )
    assert response.status_code == 200


def test_control_resync_proxies_to_copier(org_client):
    """POST /api/orgs/{org_id}/control/resync proxies to copier."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/control/resync",
        json={},
        headers=_csrf(client),
    )
    assert response.status_code == 200


def test_control_502_when_copier_down(org_client, copier_error_response):
    """Control endpoints return 502 when copier connection fails."""
    client, org_id, seed = org_client

    # Configure copier to raise connection error
    copier_error_response.raise_connect_error()

    # Should return 502 when copier is unreachable
    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": None},
        headers=_csrf(client),
    )
    assert response.status_code == 502
    assert "unreachable" in response.json().get("detail", "").lower()

    copier_error_response.reset()


def test_control_pause_forwards_4xx_from_copier(org_client, copier_error_response):
    """Control pause should forward copier 4xx errors with detail, not
    convert to 200. Uses account_id=None (no org-membership check involved)
    so this exercises _proxy_to_copier's own 4xx forwarding, not the
    account-in-org guard."""
    client, org_id, seed = org_client

    # Configure copier to return 404 with detail
    copier_error_response.return_status_json(
        404,
        {"detail": "Account not found in copier"}
    )

    # Should forward the 404 with copier's detail
    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": None},
        headers=_csrf(client),
    )
    assert response.status_code == 404
    assert "Account not found" in response.json().get("detail", "")

    copier_error_response.reset()


def test_control_handles_malformed_copier_response(org_client, copier_error_response):
    """Control endpoints should return 502 for non-JSON copier responses (not 500)."""
    client, org_id, seed = org_client

    # Configure copier to return 200 with non-JSON body
    copier_error_response.return_non_json(200, b"Internal server error")

    # Should return 502 on malformed response, not 500
    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": None},
        headers=_csrf(client),
    )
    assert response.status_code == 502
    assert "invalid response" in response.json().get("detail", "").lower()

    copier_error_response.reset()


def test_control_requires_auth(app_client):
    """Control endpoints without auth return 401 or 403 (CSRF checked first)."""
    response = app_client.post(
        "/api/orgs/1/control/pause",
        json={"account_id": None}
    )
    # CSRF middleware runs before auth, so we get 403 if no CSRF cookie
    assert response.status_code in (401, 403)


def test_control_requires_csrf(org_client):
    """Control endpoints without CSRF return 403."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/control/pause",
        json={"account_id": None}
    )
    assert response.status_code == 403


def test_state_proxy_passthrough(org_client):
    """GET /api/orgs/{org_id}/state proxies to copier GET /state."""
    client, org_id, seed = org_client

    response = client.get(f"/api/orgs/{org_id}/state")
    assert response.status_code == 200
    # Should be valid JSON response from copier
    data = response.json()
    assert isinstance(data, dict)


def test_state_proxies_with_org_query(org_client):
    client, org_id, seed = org_client
    captured = {}

    import httpx
    from conftest import default_mock_callback

    def capture(request: httpx.Request) -> httpx.Response:
        if "copier.test" in str(request.url):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"accounts": {}, "master_positions": [],
                                             "pending_orders": [], "drift": []})
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(capture)
    r = client.get(f"/api/orgs/{org_id}/state")
    assert r.status_code == 200
    assert f"org_id={org_id}" in captured["url"]


def test_state_proxy_requires_auth(app_client):
    """GET /api/orgs/{org_id}/state without auth returns 401."""
    response = app_client.get("/api/orgs/1/state")
    assert response.status_code == 401


def test_drift_action_proxy(org_client):
    """POST /api/orgs/{org_id}/drift/{action} proxies to copier."""
    client, org_id, seed = org_client

    # Test close-orphan action
    response = client.post(
        f"/api/orgs/{org_id}/drift/close-orphan",
        json={"id": "drift-abc"},
        headers=_csrf(client),
    )
    assert response.status_code == 200

    # Test adopt action
    response = client.post(
        f"/api/orgs/{org_id}/drift/adopt",
        json={"id": "drift-abc", "master_position_id": 4242},
        headers=_csrf(client),
    )
    assert response.status_code == 200

    # Test dismiss action
    response = client.post(
        f"/api/orgs/{org_id}/drift/dismiss",
        json={"id": "drift-abc"},
        headers=_csrf(client),
    )
    assert response.status_code == 200


def test_drift_action_forwards_the_request_body(org_client):
    """N3: the drift remedies must forward the body the dashboard sends,
    with the org context added.

    RED before the fix: this route posted a hardcoded `json={}` and never
    read the incoming body at all, so the copier's drift resources raised
    `ValueError("id required")` -> 500 -> 502 here. All three of spec §7's
    one-click remedies -- and an explicit Stage-3 runbook step -- failed on
    every single click, while the api's own suite asserted only that the
    call had happened against a mock.
    """
    client, org_id, seed = org_client

    received = []

    def tracking_callback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "copier.test" in url and "/drift/" in url:
            received.append({"url": url, "body": json.loads(request.content or b"{}")})
            return httpx.Response(200, json={"status": "ok"})
        from conftest import default_mock_callback
        return default_mock_callback(request)

    client.app.state.mock_transport.set_callback(tracking_callback)

    # close-orphan / dismiss carry only the drift item id...
    for action in ("close-orphan", "dismiss"):
        response = client.post(
            f"/api/orgs/{org_id}/drift/{action}",
            json={"id": f"item-for-{action}"},
            headers=_csrf(client),
        )
        assert response.status_code == 200

    # ...adopt additionally carries the master position to adopt under.
    response = client.post(
        f"/api/orgs/{org_id}/drift/adopt",
        json={"id": "item-for-adopt", "master_position_id": 987654},
        headers=_csrf(client),
    )
    assert response.status_code == 200

    assert len(received) == 3
    by_action = {r["url"].rsplit("/", 1)[-1]: r["body"] for r in received}
    assert by_action["close-orphan"] == {
        "id": "item-for-close-orphan", "org_id": org_id,
        "actor_email": "admin@example.com"}
    assert by_action["dismiss"] == {
        "id": "item-for-dismiss", "org_id": org_id,
        "actor_email": "admin@example.com"}
    assert by_action["adopt"] == {
        "id": "item-for-adopt", "master_position_id": 987654, "org_id": org_id,
        "actor_email": "admin@example.com",
    }


def test_drift_action_without_an_id_is_rejected_before_the_copier(org_client):
    """`id` is required by every copier drift resource, so a body without one
    is a client error (422), not a 502 from a copier ValueError."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/drift/close-orphan", json={}, headers=_csrf(client),
    )
    assert response.status_code == 422


def test_drift_invalid_action_400(org_client):
    """POST /api/orgs/{org_id}/drift with invalid action returns 400."""
    client, org_id, seed = org_client

    response = client.post(
        f"/api/orgs/{org_id}/drift/invalid-action",
        json={"id": "whatever"},
        headers=_csrf(client),
    )
    assert response.status_code == 400


def test_drift_requires_auth(app_client):
    """Drift endpoint without auth returns 401 or 403 (CSRF checked first)."""
    response = app_client.post("/api/orgs/1/drift/close-orphan", json={"id": "x"})
    # CSRF middleware runs before auth, so we get 403 if no CSRF cookie
    assert response.status_code in (401, 403)


def test_drift_requires_csrf(org_client):
    """Drift endpoint without CSRF returns 403."""
    client, org_id, seed = org_client

    response = client.post(f"/api/orgs/{org_id}/drift/close-orphan", json={"id": "x"})
    assert response.status_code == 403


def test_viewer_can_read_settings_and_state_but_not_control(org_client, make_user, login_as, db):
    """GET settings/state are viewer-level; PUT settings and every
    control/drift endpoint require admin."""
    client, org_id, seed = org_client
    viewer = make_user(email="viewer@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
            (org_id, viewer["id"]))
    login_as(client, viewer)

    assert client.get(f"/api/orgs/{org_id}/settings").status_code == 200
    assert client.get(f"/api/orgs/{org_id}/state").status_code == 200

    assert client.put(
        f"/api/orgs/{org_id}/settings", json={"copying_enabled": False},
        headers=_csrf(client),
    ).status_code == 403
    assert client.post(
        f"/api/orgs/{org_id}/control/pause", json={}, headers=_csrf(client),
    ).status_code == 403
    assert client.post(
        f"/api/orgs/{org_id}/control/resync", json={}, headers=_csrf(client),
    ).status_code == 403
    assert client.post(
        f"/api/orgs/{org_id}/drift/close-orphan", json={"id": "x"},
        headers=_csrf(client),
    ).status_code == 403
