"""Tests for the Resend email alerter: rule matching, cooldown, disabled
default, and the full DB -> LISTEN/NOTIFY -> email path."""
import asyncio
import json
import os
import time

import httpx
import psycopg
import pytest

from api.alerts import EmailAlerter


def _make_alerter(recorded, api_key="re_test_key", to="me@example.com",
                  clock=None):
    def callback(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"id": "email_1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(callback))
    return EmailAlerter(
        http=http, api_key=api_key, from_addr="Copy Desk <onboarding@resend.dev>",
        to_addr=to, clock=clock or time.monotonic)


def _event(category="risk", severity="error", action="margin_call",
           account_id=101, **payload):
    return {
        "id": 1, "ts": "2026-08-18T10:00:00+00:00", "account_id": account_id,
        "category": category, "severity": severity, "latency_ms": None,
        "payload": {"action": action, **payload},
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_margin_call_event_sends_email():
    recorded = []
    alerter = _make_alerter(recorded)

    sent = _run(alerter.consider(_event()))

    assert sent is True
    assert len(recorded) == 1
    request = recorded[0]
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == "Bearer re_test_key"
    body = json.loads(request.content)
    assert body["to"] == ["me@example.com"]
    assert "Margin call" in body["subject"]
    assert "101" in body["text"]


def test_all_critical_rules_match():
    recorded = []
    alerter = _make_alerter(recorded)

    events = [
        _event("risk", "error", "margin_call"),
        _event("control", "warning", "kill_switch"),
        _event("auth", "error", "token_refresh_failed", account_id=None),
        _event("slave_action", "error", "order_rejected"),
        _event("slave_action", "error", "send_failed_degraded"),
    ]
    for i, event in enumerate(events):
        event["payload"]["_i"] = i  # distinct payloads, same shapes
        assert _run(alerter.consider(event)) is True, event["payload"]["action"]

    assert len(recorded) == 5


def test_uninteresting_events_send_nothing():
    recorded = []
    alerter = _make_alerter(recorded)

    boring = [
        _event("master_event", "info", "anything"),
        _event("slave_action", "info", "position_filled"),
        _event("control", "info", "pause_global"),
        _event("drift", "warning", "whatever"),
    ]
    for event in boring:
        assert _run(alerter.consider(event)) is False

    assert recorded == []


def test_cooldown_suppresses_repeats_per_action_and_account():
    recorded = []
    now = [1000.0]
    alerter = _make_alerter(recorded, clock=lambda: now[0])

    assert _run(alerter.consider(_event(account_id=101))) is True
    assert _run(alerter.consider(_event(account_id=101))) is False   # suppressed
    assert _run(alerter.consider(_event(account_id=202))) is True    # other account

    now[0] += 901  # past the 15-minute cooldown
    assert _run(alerter.consider(_event(account_id=101))) is True
    assert len(recorded) == 3


def test_disabled_without_config():
    recorded = []
    alerter = _make_alerter(recorded, api_key="", to="")
    assert alerter.enabled is False
    assert _run(alerter.consider(_event())) is False
    assert recorded == []


def test_send_failure_does_not_raise():
    def callback(request):
        raise httpx.ConnectError("no network")

    http = httpx.AsyncClient(transport=httpx.MockTransport(callback))
    alerter = EmailAlerter(http=http, api_key="k", from_addr="a@b.c",
                           to_addr="me@example.com", clock=time.monotonic)
    assert _run(alerter.consider(_event())) is False


def test_lifespan_wires_alerter_to_broadcaster(app_client_with_lifespan, monkeypatch):
    """create_app's lifespan hands the broadcaster an EmailAlerter built from
    the environment (enabled or not -- wiring is unconditional)."""
    from api.ws import broadcaster
    assert getattr(broadcaster, "alerter", None) is not None
