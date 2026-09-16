"""Risk-rule CRUD: admin-only, audited, same pattern as the existing
webhook-settings endpoints in this same router. A symbol with no rule
simply does not appear in the list -- there is no "disabled" row state,
only present or absent.
"""
import psycopg


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_list_starts_empty(org_client):
    client, org_id, seed = org_client
    r = client.get(f"/api/orgs/{org_id}/risk-rules")
    assert r.status_code == 200
    assert r.json() == []


def test_put_creates_a_rule(org_client):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": 150.0,
        "trailing_enabled": False, "trail_start_points": None,
        "trail_step_points": None}, headers=_csrf(client))
    assert r.status_code == 200
    r2 = client.get(f"/api/orgs/{org_id}/risk-rules")
    assert r2.json() == [{"symbol": "XAUUSD", "stop_points": 50.0,
                          "target_points": 150.0, "trailing_enabled": False,
                          "trail_start_points": None, "trail_step_points": None}]


def test_put_normalises_the_symbol_the_same_way_alerts_do(org_client):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/risk-rules/OANDA:XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["symbol"] == "XAUUSD"


def test_put_requires_trail_start_and_step_when_trailing_enabled(org_client):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": None, "target_points": None, "trailing_enabled": True,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    assert r.status_code == 400


def test_put_overwrites_an_existing_rule(org_client):
    client, org_id, seed = org_client
    client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 60.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    assert r.json()["stop_points"] == 60.0


def test_delete_removes_the_rule(org_client):
    client, org_id, seed = org_client
    client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    r = client.delete(f"/api/orgs/{org_id}/risk-rules/XAUUSD", headers=_csrf(client))
    assert r.status_code == 204
    assert client.get(f"/api/orgs/{org_id}/risk-rules").json() == []


def test_viewer_role_cannot_write(org_client, make_user, login_as, db):
    """org_client's own user is already an org "admin" (make_org seeds it that
    way); to exercise a lower role we add a second user as "viewer" directly,
    the same way test_webhooks.py's test_a_trader_can_read_but_not_rotate_or_enable
    does for "trader"."""
    client, org_id, seed = org_client
    viewer = make_user(email="viewer@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
            (org_id, viewer["id"]))
    login_as(client, viewer)
    r = client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    assert r.status_code == 403


def test_put_is_audited(org_client, db):
    client, org_id, seed = org_client
    client.put(f"/api/orgs/{org_id}/risk-rules/XAUUSD", json={
        "stop_points": 50.0, "target_points": None, "trailing_enabled": False,
        "trail_start_points": None, "trail_step_points": None}, headers=_csrf(client))
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT payload FROM events WHERE org_id = %s AND payload->>'action' = "
            "'risk_rule_set' ORDER BY ts DESC LIMIT 1", (org_id,)).fetchone()
    assert row is not None
