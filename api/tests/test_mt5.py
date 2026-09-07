"""The MT5 bridge's api side: the terminal door, the operator endpoints and
the EA download. Each test is named for the failure it prevents."""
import pytest


# ====================================================== CSRF exemption


def test_the_terminal_door_is_not_blocked_by_the_csrf_middleware(app_client):
    """A terminal has no session and no CSRF cookie. Whatever the route
    answers (404 before it exists, 400 for a missing key once it does), it
    must never be the middleware's 403."""
    app_client.cookies.clear()
    r = app_client.post("/api/mt5/sync", json={"v": 1})
    assert r.status_code != 403
    assert "CSRF" not in r.text


def test_the_exemption_is_the_exact_prefix_not_a_lookalike(app_client):
    """/api/mt5x/ must not ride on /api/mt5/: the trailing slash is
    load-bearing, exactly as it is for /api/webhooks/."""
    app_client.cookies.clear()
    r = app_client.post("/api/mt5x/sync", json={"v": 1})
    assert r.status_code == 403
    assert r.json()["detail"] == "Missing CSRF token"
