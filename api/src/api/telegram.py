"""Telegram notifications for account cutoff reminders.

The EventBroadcaster offers every event it fetches for the WebSocket feed
to TelegramNotifier.consider(), exactly as it does EmailAlerter.consider()
-- but the rules differ: cutoff reminders are in-app + Telegram by design,
not email, so this notifier matches only the 'reminder' events the
copier's scanner logs, and ALERT_RULES stays untouched. Instance-wide (one
bot, one chat), configured from the environment (TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID); with either missing the notifier is inert -- everything
else works identically, it just never sends.
"""
import logging
import os
import time
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
COOLDOWN_S = 900  # one message per (action, account) per 15 minutes

# (category, severity, payload.action), matched exactly like ALERT_RULES.
TELEGRAM_RULES: set[tuple[str, str, str]] = {
    ("reminder", "warning", "cutoff_approaching"),
    ("control", "error", "webhook_alert"),
    ("control", "warning", "webhook_alert"),
}


class TelegramNotifier:
    """Turns matching events into Telegram messages, with a per-key cooldown."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        bot_token: str,
        chat_id: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._http = http
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._clock = clock
        self._last_sent: dict[tuple[str, Any], float] = {}

    @classmethod
    def from_env(cls, http: httpx.AsyncClient) -> "TelegramNotifier":
        return cls(
            http=http,
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def consider(self, event: dict) -> bool:
        """Send a Telegram message if the event matches a rule and isn't
        cooled down.

        Returns True only when Telegram actually accepted the message.
        Never raises: notifying must not be able to break the event feed.
        """
        if not self.enabled:
            return False

        payload = event.get("payload") or {}
        action = payload.get("action", "")
        if (event.get("category"), event.get("severity"), action) not in TELEGRAM_RULES:
            return False

        cooldown_key = (action, event.get("account_id"))
        now = self._clock()
        last = self._last_sent.get(cooldown_key)
        if last is not None and (now - last) < COOLDOWN_S:
            return False

        name = payload.get("nickname") or f"account {event.get('account_id')}"
        login = payload.get("trader_login")
        login_part = f" (login {login})" if login is not None else ""
        days = payload.get("days_left")
        days_part = (
            f" — {days} day{'' if days == 1 else 's'} left" if days is not None else "")
        text = (
            f"⏰ Copy Desk: {name}{login_part} reaches its cutoff on "
            f"{payload.get('cutoff_date')}{days_part}."
        )

        try:
            response = await self._http.post(
                TELEGRAM_URL.format(token=self._bot_token),
                json={"chat_id": self._chat_id, "text": text},
            )
            if response.status_code >= 400:
                logger.error("Telegram rejected notification (%s): %s",
                             response.status_code, response.text[:500])
                return False
        except Exception as e:
            logger.error("Failed to send Telegram notification: %s", e)
            return False

        self._last_sent[cooldown_key] = now
        logger.info("Telegram notification sent for account %s", event.get("account_id"))
        return True
