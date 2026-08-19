"""Tests for the Telegram notifier: account cutoff reminders reach the
operator's Telegram chat.

Cutoff reminders are in-app + Telegram BY DESIGN, not email, so this
notifier matches only the 'reminder' events the copier's scanner logs --
the EmailAlerter's rules are untouched. Otherwise it mirrors the alerter's
contract: instance-wide (one bot, one chat, configured from the
environment), inert without config, cooled down per (action, account),
never raises.
"""
import asyncio
import json
import time

import httpx

from api.telegram import TelegramNotifier


def _make_notifier(recorded, token="123:abc", chat_id="42", clock=None):
    def callback(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(callback))
    return TelegramNotifier(http=http, bot_token=token, chat_id=chat_id,
                            clock=clock or time.monotonic)


def _reminder_event(account_id=101, org_id=1, **payload):
    base = {"action": "cutoff_approaching", "cutoff_date": "2026-09-16",
            "days_left": 2, "nickname": "FTMO demo", "trader_login": 555}
    base.update(payload)
    return {
        "id": 1, "ts": "2026-09-14T10:00:00+00:00", "account_id": account_id,
        "category": "reminder", "severity": "warning", "latency_ms": None,
        "payload": base, "org_id": org_id,
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_cutoff_reminder_sends_telegram_message():
    recorded = []
    notifier = _make_notifier(recorded)

    sent = _run(notifier.consider(_reminder_event()))

    assert sent is True
    assert len(recorded) == 1
    request = recorded[0]
    assert str(request.url) == "https://api.telegram.org/bot123:abc/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == "42"
    assert "FTMO demo" in body["text"]
    assert "2026-09-16" in body["text"]
    assert "2 days" in body["text"]


def test_falls_back_to_account_id_without_nickname():
    recorded = []
    notifier = _make_notifier(recorded)

    assert _run(notifier.consider(_reminder_event(nickname=None))) is True
    assert "101" in json.loads(recorded[0].content)["text"]


def test_non_reminder_events_send_nothing():
    """Margin calls, kill switches etc. stay on the email path; Telegram is
    the cutoff-reminder channel only."""
    recorded = []
    notifier = _make_notifier(recorded)

    not_ours = [
        {**_reminder_event(), "category": "risk", "severity": "error",
         "payload": {"action": "margin_call"}},
        {**_reminder_event(), "category": "control", "severity": "warning",
         "payload": {"action": "kill_switch"}},
        {**_reminder_event(), "severity": "info"},
    ]
    for event in not_ours:
        assert _run(notifier.consider(event)) is False

    assert recorded == []


def test_disabled_without_config():
    recorded = []
    notifier = _make_notifier(recorded, token="", chat_id="")
    assert notifier.enabled is False
    assert _run(notifier.consider(_reminder_event())) is False
    assert recorded == []


def test_cooldown_suppresses_repeats_per_action_and_account():
    recorded = []
    now = [1000.0]
    notifier = _make_notifier(recorded, clock=lambda: now[0])

    assert _run(notifier.consider(_reminder_event(account_id=101))) is True
    assert _run(notifier.consider(_reminder_event(account_id=101))) is False
    assert _run(notifier.consider(_reminder_event(account_id=202))) is True

    now[0] += 901
    assert _run(notifier.consider(_reminder_event(account_id=101))) is True
    assert len(recorded) == 3


def test_send_failure_does_not_raise():
    def callback(request):
        raise httpx.ConnectError("no network")

    http = httpx.AsyncClient(transport=httpx.MockTransport(callback))
    notifier = TelegramNotifier(http=http, bot_token="123:abc", chat_id="42",
                                clock=time.monotonic)
    assert _run(notifier.consider(_reminder_event())) is False


def test_telegram_api_rejection_returns_false():
    def callback(request):
        return httpx.Response(403, json={"ok": False, "description": "bot blocked"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(callback))
    notifier = TelegramNotifier(http=http, bot_token="123:abc", chat_id="42",
                                clock=time.monotonic)
    assert _run(notifier.consider(_reminder_event())) is False


def test_lifespan_wires_telegram_to_broadcaster(app_client_with_lifespan, monkeypatch):
    """create_app's lifespan hands the broadcaster a TelegramNotifier built
    from the environment, alongside the EmailAlerter (wiring is
    unconditional; enablement is config)."""
    from api.ws import broadcaster
    assert getattr(broadcaster, "telegram", None) is not None
