"""Email alerts for the events that risk money or copying, via Resend.

The EventBroadcaster hands every event it fetches for the WebSocket feed to
EmailAlerter.consider(); events matching ALERT_RULES become one email each,
rate-limited per (action, account) so a flapping condition cannot flood the
inbox. Configuration comes from the environment (RESEND_API_KEY,
ALERT_EMAIL_FROM, ALERT_EMAIL_TO); with no key or recipient the alerter is
inert -- everything else works identically, it just never sends.
"""
import json
import logging
import os
import time
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
COOLDOWN_S = 900  # one email per (action, account) per 15 minutes

# (category, severity, payload.action) -> subject prefix. Matched exactly;
# everything else is ignored.
ALERT_RULES: dict[tuple[str, str, str], str] = {
    ("risk", "error", "margin_call"): "Margin call",
    ("control", "warning", "kill_switch"): "Kill switch fired",
    ("auth", "error", "token_refresh_failed"): "cTrader token refresh failed",
    ("slave_action", "error", "order_rejected"): "Copy order rejected",
    ("slave_action", "error", "send_failed_degraded"): "Slave degraded",
}


class EmailAlerter:
    """Turns matching events into Resend emails, with a per-key cooldown."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        from_addr: str,
        to_addr: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._http = http
        self._api_key = api_key
        self._from = from_addr
        self._to = to_addr
        self._clock = clock
        self._last_sent: dict[tuple[str, Any], float] = {}

    @classmethod
    def from_env(cls, http: httpx.AsyncClient) -> "EmailAlerter":
        return cls(
            http=http,
            api_key=os.environ.get("RESEND_API_KEY", ""),
            from_addr=os.environ.get(
                "ALERT_EMAIL_FROM", "Copy Desk <onboarding@resend.dev>"),
            to_addr=os.environ.get("ALERT_EMAIL_TO", ""),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._api_key and self._to)

    async def consider(self, event: dict) -> bool:
        """Send an email if the event matches a rule and isn't cooled down.

        Returns True only when an email was actually accepted by Resend.
        Never raises: alerting must not be able to break the event feed.
        """
        if not self.enabled:
            return False

        action = (event.get("payload") or {}).get("action", "")
        rule_key = (event.get("category"), event.get("severity"), action)
        subject_prefix = ALERT_RULES.get(rule_key)
        if subject_prefix is None:
            return False

        cooldown_key = (action, event.get("account_id"))
        now = self._clock()
        last = self._last_sent.get(cooldown_key)
        if last is not None and (now - last) < COOLDOWN_S:
            return False

        account = event.get("account_id")
        subject = f"[Copy Desk] {subject_prefix}" + (
            f" — account {account}" if account else "")
        text = (
            f"{subject_prefix}\n\n"
            f"Time: {event.get('ts')}\n"
            f"Account: {account if account is not None else '—'}\n"
            f"Category: {event.get('category')} / {event.get('severity')}\n\n"
            f"Details:\n{json.dumps(event.get('payload') or {}, indent=2, default=str)}\n\n"
            "Open the dashboard for the full picture."
        )

        try:
            response = await self._http.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"from": self._from, "to": [self._to],
                      "subject": subject, "text": text},
            )
            if response.status_code >= 400:
                logger.error("Resend rejected alert email (%s): %s",
                             response.status_code, response.text[:500])
                return False
        except Exception as e:
            logger.error("Failed to send alert email: %s", e)
            return False

        self._last_sent[cooldown_key] = now
        logger.info("Alert email sent: %s", subject)
        return True
