"""TradingView alerts placing the master's orders.

    TradingView alert  ->  POST /api/webhooks/tradingview/{hook_id}
                       ->  the org's MASTER account places the order
                       ->  every slave copies it (unchanged, downstream)

This is the only unauthenticated, order-placing route in the API, and it is
written to be safe by default for an operator who is not a security engineer.
TradingView signs nothing; it sends four fixed source IPs, gives the server
three seconds, and puts whatever the operator typed into the alert message
on the wire. Its retry rule, from their own "Webhook resubmission" article,
is narrower than "resends on error" and the safety logic leans on the exact
shape: a resend happens ONLY for HTTP 500-599, never for 504 and never for
any 4xx, up to 3 times, 5 seconds apart. So 503 means "nothing was placed,
please try again" and is the only status that invites a retry; every refusal
is a 4xx so TradingView drops it; and "the order may be live" is a 200 so
nothing is ever resent into it. Every design decision below follows from
those facts.

THE FRONT DOOR, IN ORDER. Nothing that costs a database write or a bucket
slot happens before the request has proved it is TradingView carrying the
right secret:

  1. body size cap        -- 4096 bytes, before parsing
  2. source-IP allowlist  -- no database touched; outsiders never get past
  3. resolve hook_id      -- one indexed read; unknown -> 404
  4. parse JSON           -- regardless of Content-Type (TradingView sends
                             text/plain when IT decides the message is not
                             JSON, which is exactly the mistake to explain)
  5. secret compare       -- constant time, against a sha256; a mismatch is
                             rate-limited per hook and stores NO body
  6. gates                -- enabled, copying on, not dry-run, master present
  7. validate             -- symbol, size against the org's cap
  8. dedup + caps         -- in ONE transaction under an advisory lock
  9. act                  -- order or close, within a 2 s deadline
 10. record               -- receipt row + audit event, actor "tradingview"

The order matters. The adversarial review found that a per-hook rate bucket
placed BEFORE the IP check let anyone holding the URL (which sits in
TradingView's own dialog and in access logs, and is therefore not a secret)
fill the bucket from any address and 429 the org's real exit signals into
oblivion. So no shared bucket stands in front of authentication: a rejected
outsider consumes a per-source-IP slot only, and the sole limit a valid
secret can hit is the org's own accepted-per-minute cap.

WHERE THE SECRET TRAVELS. In the JSON body, never the URL. A URL is written
to Caddy's access log, uvicorn's, TradingView's alert log, and the operator's
clipboard; a secret in that many places has already leaked. The URL carries
an opaque random hook_id -- a mailbox address -- so a stale secret in the
operator's own template still lands in THEIR log where they can see it.

WHY A RESEND IS THE DANGER. TradingView resends on 5xx. If an order was
already handed to the copier and we then answer 5xx (say, a slow read), the
resend is a double trade. So the classification of copier failures is
strict: a connection that was never made is 503 (nothing sent, resend
welcome); anything after the request was written is 200 "unknown" (the order
MAY be on the wire, do not resend), audited at error severity so a person
looks. Transient copier 400s -- "no client for account", the copier still
starting -- are 503 for the same reason: the resend five seconds later will
very likely succeed, and a 422 would lose the alert for good.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import LoginRateLimiter, get_client_ip
from ..config import ApiConfig
from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from ..tradingview_alerts import (
    Alert, AlertError, HARD_MAX_LOTS, find_master_positions, normalise_ticker,
    parse_alert)
from .settings_control import audit

logger = logging.getLogger(__name__)

# TradingView's published webhook sources (support article "About webhooks").
# Extend with TRADINGVIEW_EXTRA_SOURCE_IPS="a.b.c.d,e.f.g.h" if they add one;
# tests monkeypatch the set directly.
TRADINGVIEW_SOURCE_IPS: frozenset[str] = frozenset({
    "52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7",
})

MAX_BODY_BYTES = 4096
# TradingView cancels at 3 s. Everything must finish well inside it, and an
# order must never be PLACED after the point TradingView has already given
# up -- because then it resends, and that is the double trade.
DEADLINE_S = 2.0
COPIER_CALL_TIMEOUT_S = 1.0
# Fingerprint window: TradingView's full resend span is 3 retries x 5 s = 15 s
# after the first delivery, and two indicators can fire the same message in
# the same second. 20 s covers both while staying short enough that a genuine
# re-entry a minute later goes through.
DEDUP_WINDOW_S = 20
# Only what is genuinely a proxy may set X-Forwarded-For for this route.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

SECRET_PREFIX = "tvw_"

# Phrases in a copier 400 that mean "not now" rather than "never".
_TRANSIENT_400 = (
    "no client for account", "not found", "not authorized",
    "no live price", "is starting", "not connected",
)


def _json(status: int, payload: Dict[str, Any]) -> JSONResponse:
    """Starlette's JSONResponse takes content first; this reads status first,
    which is the order every branch below actually thinks in."""
    return JSONResponse(payload, status_code=status)


class WebhookRejected(Exception):
    def __init__(self, status: int, reason: str, outcome: str = "rejected"):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.outcome = outcome


class CopierDown(Exception):
    """Nothing was sent. Safe -- desirable -- for TradingView to resend."""


class CopierUnknown(Exception):
    """The request was written; the outcome is unknown. Must NOT be resent."""


# ------------------------------------------------------------------ helpers


def _source_ips() -> frozenset[str]:
    extra = os.environ.get("TRADINGVIEW_EXTRA_SOURCE_IPS", "")
    return TRADINGVIEW_SOURCE_IPS | frozenset(
        ip.strip() for ip in extra.split(",") if ip.strip())


def _client_ip(request: Request, cfg: ApiConfig) -> str:
    """The address the allowlist judges.

    X-Forwarded-For is believed ONLY when the immediate peer is our own
    reverse proxy on loopback. The review showed that honouring it from any
    peer turns the allowlist into a header check: anyone reaching :8000
    directly could claim to be TradingView. The existing get_client_ip
    honours the header whenever TRUST_PROXY is set; this route is stricter.
    """
    peer = request.client.host if request.client else "unknown"
    if cfg.trust_proxy and peer in _LOOPBACK:
        return get_client_ip(request, trust_proxy=True)
    return peer


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _redact(value: Any) -> Any:
    """Scrub anything secret-shaped, wherever it sits.

    By VALUE, not by key: an operator whose template says "Secret", "token",
    or who pasted the secret into "id" would otherwise write it to disk in
    clear on every retry.
    """
    if isinstance(value, str):
        return "***" if value.startswith(SECRET_PREFIX) else value
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _fingerprint(org_id: int, alert: Alert) -> str:
    """The alert's TRADING content, not its bytes.

    A TradingView resend re-renders {{timenow}}, so byte-identity would let
    a resend through as a "new" alert. Two alerts with the same trading
    intent inside the window are one alert, whatever their id says.
    """
    parts = (org_id, alert.action, alert.symbol, alert.lots,
             alert.stop_loss, alert.take_profit)
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


async def _copier(client: httpx.AsyncClient, method: str, url: str,
                  json_body: Optional[dict], remaining_s: float) -> Dict[str, Any]:
    """One copier call, with the failure classified for TradingView.

    The line that matters is between "never sent" and "may have been sent".
    ConnectError, ConnectTimeout and PoolTimeout all mean no bytes reached
    the copier -- CopierDown, 503, resend welcome. A read or write timeout,
    a protocol error mid-response, or a 5xx all come AFTER the request was
    written -- CopierUnknown, and the caller must answer 2xx so TradingView
    does not resend into a possibly-live order.
    """
    timeout = max(0.05, min(COPIER_CALL_TIMEOUT_S, remaining_s))
    try:
        if method == "GET":
            response = await client.get(url, timeout=timeout)
        else:
            response = await client.post(url, json=json_body or {}, timeout=timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise CopierDown(str(exc))
    except httpx.HTTPError as exc:
        raise CopierUnknown(str(exc))

    if response.status_code >= 500:
        raise CopierUnknown(f"copier answered {response.status_code}")
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        detail = str(payload.get("detail") or payload.get("error") or response.text or "copier error")
        if any(phrase in detail.lower() for phrase in _TRANSIENT_400):
            raise CopierDown(detail)
        raise WebhookRejected(422, detail)
    return payload if isinstance(payload, dict) else {}


def _master_of(conn: psycopg.Connection, org_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts "
        "WHERE org_id = %s AND role = 'master' AND enabled",
        (org_id,)).fetchone()
    return int(row[0]) if row else None


# ------------------------------------------------------------ the receiver


def create_webhooks_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

    @router.post("/tradingview/{hook_id}")
    async def tradingview(hook_id: str, request: Request,
                          cfg: ApiConfig = Depends(ApiConfig.from_env)):
        t0 = time.monotonic()
        ip = _client_ip(request, cfg)

        # ---- 1. size, before anything is parsed ----
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _json(413, {"status": "rejected", "reason": "body too large"})

        # ---- 2. source, before anything is read from the database ----
        if ip not in _source_ips():
            # Per-SOURCE bucket. This must never be a bucket a real alert
            # could share, or a URL alone becomes a denial of service.
            if not rate_limiter.is_limited(f"webhook-source:{ip}", 30):
                logger.warning("webhook from non-TradingView source %s (hook %s)",
                               ip, hook_id[:6])
            return _json(403, {
                "status": "rejected",
                "reason": f"source {ip} is not a TradingView address"})

        # The dependency-injected connection is opened only past the door:
        # an unauthenticated flood must not open a Postgres connection each.
        with psycopg.connect(cfg.postgres_dsn, autocommit=True) as conn:
            return await _handle(conn, request, cfg, hook_id, raw, ip, t0)

    async def _handle(conn, request, cfg, hook_id, raw, ip, t0):
        # ---- 3. resolve the mailbox ----
        row = conn.execute(
            "SELECT org_id, secret_hash, enabled, max_lots, max_per_minute, "
            "max_open_positions, symbol_aliases FROM org_webhooks WHERE hook_id = %s",
            (hook_id,)).fetchone()
        if row is None:
            rate_limiter.is_limited(f"webhook-source:{ip}", 30)
            return _json(404, {"detail": "Not found"})
        org_id, secret_hash, enabled, max_lots, max_per_minute, max_open, aliases = row
        max_lots = float(max_lots) if max_lots is not None else None

        # ---- 4. parse, whatever the content-type says ----
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return _record(conn, org_id, ip, t0, None, None, WebhookRejected(
                422, "the alert message must be JSON -- check the Message box "
                     "matches the template in Automation"))

        # ---- 5. the secret ----
        presented = body.get("secret") if isinstance(body, dict) else None
        ok = (isinstance(presented, str) and secret_hash is not None
              and hmac.compare_digest(_hash(presented), secret_hash))
        if not ok:
            # Limited per hook, and NO body stored: the review showed that a
            # typo'd key ("Secret") would otherwise write the real secret to
            # disk in clear on every retry.
            if rate_limiter.is_limited(f"webhook-badsecret:{hook_id}", 10):
                return _json(401, {"status": "rejected", "reason": "secret mismatch"})
            return _record(conn, org_id, ip, t0, None, None,
                           WebhookRejected(401, "secret mismatch"), store_body=False)

        # Authenticated. From here every outcome is written to the org's log.
        redacted = _redact(body)

        # ---- 6. gates ----
        try:
            if not enabled:
                raise WebhookRejected(403, "automation is switched off for this workspace")
            org = conn.execute(
                "SELECT copying_enabled, dry_run FROM orgs WHERE id = %s", (org_id,)).fetchone()
            if org is None or not org[0]:
                # The kill switch is the button a person reaches for in a
                # panic. It must stop this too, or the master keeps opening
                # positions no slave will ever copy.
                raise WebhookRejected(403, "copying is stopped for this workspace")
            if org[1]:
                raise WebhookRejected(422, "this workspace is in dry-run; automation is disabled until it is off")
            master = _master_of(conn, org_id)
            if master is None:
                raise WebhookRejected(422, "this workspace has no master account")

            # ---- 7. validate ----
            try:
                alert = parse_alert(body, max_lots)
            except AlertError as exc:
                raise WebhookRejected(422, str(exc))
            if isinstance(aliases, dict) and alert.symbol in aliases:
                alert = Alert(alert.action, normalise_ticker(aliases[alert.symbol]),
                              alert.lots, alert.stop_loss, alert.take_profit, alert.alert_id)
        except WebhookRejected as exc:
            return _record(conn, org_id, ip, t0, redacted, None, exc)

        # ---- 8. dedup and caps, atomically ----
        fp = _fingerprint(org_id, alert)
        # get_conn is autocommit: without an explicit transaction the
        # advisory lock is released the instant the SELECT ends and two
        # identical alerts landing together both pass. Reviewed and pinned.
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (org_id,))
            dup = conn.execute(
                "SELECT id FROM webhook_receipts WHERE org_id = %s AND fingerprint = %s "
                "AND outcome IN ('accepted','unknown') "
                "AND received_at > now() - make_interval(secs => %s) "
                "ORDER BY received_at DESC LIMIT 1",
                (org_id, fp, DEDUP_WINDOW_S)).fetchone()
            if dup:
                receipt_id = _insert(conn, org_id, ip, t0, redacted, alert, "duplicate",
                                     f"same alert accepted {dup[0]} seconds ago", fp=None)
                return _json(200, {"status": "duplicate", "receipt_id": receipt_id,
                                          "duplicate_of": dup[0]})
            (accepted_last_minute,) = conn.execute(
                "SELECT count(*) FROM webhook_receipts WHERE org_id = %s "
                "AND outcome IN ('accepted','unknown') "
                "AND received_at > now() - interval '1 minute'", (org_id,)).fetchone()
            if accepted_last_minute >= max_per_minute:
                raise_ = WebhookRejected(429, f"more than {max_per_minute} alerts accepted in a minute; "
                                              f"raise the limit in Automation if this is intended")
                return _record(conn, org_id, ip, t0, redacted, alert, raise_)
            # Provisional row holds the fingerprint from this instant.
            receipt_id = _insert(conn, org_id, ip, t0, redacted, alert, "accepted", None, fp=fp)

        # ---- 9. act ----
        client = request.app.app.state.http if hasattr(request.app, "app") else request.app.state.http
        base = cfg.copier_control_url
        try:
            def remaining() -> float:
                left = DEADLINE_S - (time.monotonic() - t0)
                if left <= 0:
                    raise CopierDown("too slow, nothing sent")
                return left

            if alert.action == "close":
                result = await _close(client, base, master, org_id, alert, remaining)
                outcome = "accepted" if result["positions_closed"] else "nothing_to_close"
                _finish(conn, receipt_id, outcome,
                        None if result["positions_closed"] else "nothing open on that symbol")
                _audit(conn, org_id, master, alert, outcome, None, ip, t0, receipt_id)
                return _json(200, {"status": outcome, "receipt_id": receipt_id,
                                          "action": "close", "symbol": alert.symbol,
                                          **result})

            state = await _copier(client, "GET", f"{base}/state?org_id={org_id}", None, remaining())
            held = find_master_positions(state, alert.symbol)
            opposite = "SELL" if alert.action == "buy" else "BUY"
            if any(str(p.get("side", "")).upper() == opposite for p in held):
                raise WebhookRejected(
                    422, f"master holds an opposite position on {alert.symbol}; "
                         f"reverse-on-signal is not supported -- send close first")
            if len(state.get("master_positions") or []) >= max_open:
                raise WebhookRejected(
                    422, f"master already holds {max_open} open positions; "
                         f"raise the limit in Automation if this is intended")

            order: Dict[str, Any] = {
                "account_id": master, "symbol": alert.symbol,
                "side": alert.action.upper(), "order_type": "MARKET",
                "volume_lots": alert.lots, "actor_email": "tradingview",
            }
            if alert.stop_loss is not None:
                order["stop_loss"] = alert.stop_loss
            if alert.take_profit is not None:
                order["take_profit"] = alert.take_profit
            remaining()  # the deadline check right before the only irreversible call
            summary = await _copier(client, "POST", f"{base}/order", order, remaining())

            _finish(conn, receipt_id, "accepted", None)
            _audit(conn, org_id, master, alert, "accepted", summary, ip, t0, receipt_id)
            return _json(200, {"status": "accepted", "receipt_id": receipt_id,
                                      "action": alert.action, "symbol": alert.symbol,
                                      "account_id": master, "order": summary})

        except WebhookRejected as exc:
            _finish(conn, receipt_id, "rejected", exc.reason, clear_fp=True)
            _audit(conn, org_id, master, alert, "rejected", {"reason": exc.reason}, ip, t0, receipt_id)
            return _json(exc.status,{"status": "rejected", "receipt_id": receipt_id,
                                             "reason": exc.reason})
        except CopierDown as exc:
            # Nothing reached the copier. Free the fingerprint so the resend
            # TradingView is about to make is not swallowed as a duplicate.
            _finish(conn, receipt_id, "failed", f"copier unreachable: {exc}", clear_fp=True)
            _audit(conn, org_id, master, alert, "failed", {"reason": str(exc)}, ip, t0, receipt_id)
            return _json(503, {"status": "failed", "receipt_id": receipt_id,
                                      "reason": "copier unreachable"})
        except CopierUnknown as exc:
            # The request was written. The order MAY be live. Answer 2xx so
            # TradingView does not resend into it, keep the fingerprint, and
            # audit at error severity so a person looks at Positions.
            _finish(conn, receipt_id, "unknown", f"copier did not confirm: {exc}")
            _audit(conn, org_id, master, alert, "unknown", {"reason": str(exc)}, ip, t0, receipt_id)
            return _json(200, {"status": "unknown", "receipt_id": receipt_id,
                                      "reason": "order may be on the wire -- check Positions"})

    async def _close(client, base, master, org_id, alert, remaining):
        state = await _copier(client, "GET", f"{base}/state?org_id={org_id}", None, remaining())
        held = find_master_positions(state, alert.symbol)
        if not held:
            # The reconciler's snapshot lags a fill by a resync. A close that
            # arrives 300ms after the entry it is meant to undo would read an
            # empty book and report success. Ask for a fresh read first.
            await _copier(client, "POST", f"{base}/resync", {"org_id": org_id}, remaining())
            state = await _copier(client, "GET", f"{base}/state?org_id={org_id}", None, remaining())
            held = find_master_positions(state, alert.symbol)
        closed = []
        for pos in held:
            remaining()
            await _copier(client, "POST", f"{base}/positions/close",
                          {"account_id": master, "position_id": pos["position_id"],
                           "actor_email": "tradingview"}, remaining())
            closed.append(pos["position_id"])
        return {"positions_closed": closed}

    # ------------------------------------------------------ recording

    def _insert(conn, org_id, ip, t0, redacted, alert, outcome, reason, fp) -> int:
        (rid,) = conn.execute(
            "INSERT INTO webhook_receipts (org_id, outcome, reason, action, symbol, lots, "
            "fingerprint, alert_id, source_ip, latency_ms, body_redacted) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (org_id, outcome, reason,
             alert.action if alert else None, alert.symbol if alert else None,
             Decimal(str(alert.lots)) if alert and alert.lots is not None else None,
             fp, alert.alert_id if alert else None, ip,
             int((time.monotonic() - t0) * 1000),
             Jsonb(redacted) if redacted is not None else None)).fetchone()
        return int(rid)

    def _finish(conn, receipt_id, outcome, reason, clear_fp=False):
        conn.execute(
            "UPDATE webhook_receipts SET outcome = %s, reason = %s, "
            "fingerprint = CASE WHEN %s THEN NULL ELSE fingerprint END WHERE id = %s",
            (outcome, reason, clear_fp, receipt_id))

    def _record(conn, org_id, ip, t0, redacted, alert, exc: WebhookRejected, store_body=True):
        """A refusal before anything was acted on: one row, one audit line."""
        rid = _insert(conn, org_id, ip, t0, redacted if store_body else None, alert,
                      exc.outcome, exc.reason, fp=None)
        severity = "warning" if exc.status in (401, 403, 429) else "info"
        audit_payload = {"receipt_id": rid, "outcome": exc.outcome, "reason": exc.reason,
                         "source_ip": ip}
        if alert:
            audit_payload.update(action=alert.action, symbol=alert.symbol, lots=alert.lots)
        conn.execute(
            "INSERT INTO events (org_id, category, severity, payload, actor_email) "
            "VALUES (%s, 'control', %s, %s, 'tradingview')",
            (org_id, severity, Jsonb({"action": "webhook_alert", **audit_payload})))
        return _json(exc.status,{"status": exc.outcome, "receipt_id": rid,
                                         "reason": exc.reason})

    def _audit(conn, org_id, master, alert, outcome, detail, ip, t0, receipt_id):
        severity = {"accepted": "info", "duplicate": "info", "nothing_to_close": "warning",
                    "rejected": "warning", "failed": "warning", "unknown": "error"}[outcome]
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, latency_ms, "
            "payload, actor_email) VALUES (%s, %s, 'control', %s, %s, %s, 'tradingview')",
            (org_id, master, severity, int((time.monotonic() - t0) * 1000),
             Jsonb({"action": "webhook_alert", "receipt_id": receipt_id, "outcome": outcome,
                    "alert": {"action": alert.action, "symbol": alert.symbol,
                              "lots": alert.lots, "id": alert.alert_id},
                    "source_ip": ip, **({"detail": detail} if detail else {})})))

    return router


# ------------------------------------------------------ operator endpoints


class WebhookUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_lots: Optional[float] = None
    max_per_minute: Optional[int] = None
    max_open_positions: Optional[int] = None
    symbol_aliases: Optional[Dict[str, str]] = None


def _template(secret: str) -> str:
    """The alert message the operator pastes, with their secret filled in.

    Placeholders render only in the alert dialog's Message box -- not inside
    strategy.entry(alert_message=...) -- which is why the guidance shown
    beside this tells strategy users to keep alert_message to the bare
    action word and let the dialog wrap it.
    """
    return json.dumps({
        "secret": secret,
        "action": "buy",
        "symbol": "{{ticker}}",
        "lots": 0.01,
        "id": "{{timenow}}",
    }, indent=2)


def _public_url(cfg: ApiConfig, hook_id: str) -> Optional[str]:
    origin = (cfg.public_origin.split(",")[0] if cfg.public_origin else "").strip().rstrip("/")
    if not origin.startswith("https://"):
        return None
    return f"{origin}/api/webhooks/tradingview/{hook_id}"


def create_webhook_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["webhooks"])

    @router.get("/webhook", response_model=Dict[str, Any])
    async def get_webhook(ctx: OrgContext = Depends(require_org_role("trader")),
                          conn: psycopg.Connection = Depends(get_conn),
                          cfg: ApiConfig = Depends(ApiConfig.from_env)):
        row = conn.execute(
            "SELECT hook_id, secret_hash IS NOT NULL, secret_created_at, enabled, max_lots, "
            "max_per_minute, max_open_positions, symbol_aliases FROM org_webhooks WHERE org_id = %s",
            (ctx.org_id,)).fetchone()
        recent = conn.execute(
            "SELECT id, received_at, outcome, reason, action, symbol, lots, source_ip, latency_ms "
            "FROM webhook_receipts WHERE org_id = %s ORDER BY received_at DESC LIMIT 50",
            (ctx.org_id,)).fetchall()
        master = _master_of(conn, ctx.org_id)
        dry = conn.execute("SELECT dry_run, copying_enabled FROM orgs WHERE id = %s",
                           (ctx.org_id,)).fetchone()
        return {
            "configured": row is not None,
            "hook_id": row[0] if row else None,
            "url": _public_url(cfg, row[0]) if row else None,
            "url_hint": None if (cfg.public_origin or "").startswith("https://")
                        else "PUBLIC_ORIGIN must be an https:// origin before the URL can be shown",
            "has_secret": bool(row[1]) if row else False,
            "secret_created_at": row[2].isoformat() if row and row[2] else None,
            "enabled": bool(row[3]) if row else False,
            "max_lots": float(row[4]) if row else 0.1,
            "max_per_minute": int(row[5]) if row else 10,
            "max_open_positions": int(row[6]) if row else 3,
            "symbol_aliases": row[7] if row else {},
            "master_account_id": master,
            "dry_run": bool(dry[0]) if dry else False,
            "copying_enabled": bool(dry[1]) if dry else True,
            "template": _template("tvw_YOUR_SECRET"),
            "recent": [
                {"id": r[0], "received_at": r[1].isoformat(), "outcome": r[2], "reason": r[3],
                 "action": r[4], "symbol": r[5], "lots": float(r[6]) if r[6] is not None else None,
                 "source_ip": r[7], "latency_ms": r[8]}
                for r in recent],
        }

    @router.post("/webhook/secret", response_model=Dict[str, Any])
    async def rotate_secret(ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn),
                            cfg: ApiConfig = Depends(ApiConfig.from_env)):
        """Generate (or replace) the secret. Shown exactly once; only its
        sha256 is stored. The old secret stops working immediately."""
        secret = SECRET_PREFIX + secrets.token_urlsafe(32)
        hook_id = secrets.token_urlsafe(12)
        row = conn.execute(
            "INSERT INTO org_webhooks (org_id, hook_id, secret_hash, secret_created_at) "
            "VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (org_id) DO UPDATE SET secret_hash = EXCLUDED.secret_hash, "
            "secret_created_at = now() RETURNING hook_id",
            (ctx.org_id, hook_id, _hash(secret))).fetchone()
        hook_id = row[0]
        audit(conn, ctx.org_id, ctx.user_email, "webhook_secret_rotated", {})
        return {"secret": secret, "hook_id": hook_id, "url": _public_url(cfg, hook_id),
                "template": _template(secret)}

    @router.put("/webhook", response_model=Dict[str, Any])
    async def update_webhook(body: WebhookUpdate,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn),
                             cfg: ApiConfig = Depends(ApiConfig.from_env)):
        current = conn.execute(
            "SELECT secret_hash, enabled, max_lots, max_per_minute, max_open_positions, "
            "symbol_aliases FROM org_webhooks WHERE org_id = %s", (ctx.org_id,)).fetchone()
        if current is None:
            raise HTTPException(400, "generate a secret first")
        updates, params, changed = [], [], {}

        if body.enabled is not None:
            if body.enabled:
                if current[0] is None:
                    raise HTTPException(400, "generate a secret first")
                if _master_of(conn, ctx.org_id) is None:
                    raise HTTPException(400, "this workspace has no master account")
                if _public_url(cfg, "x") is None:
                    raise HTTPException(400, "PUBLIC_ORIGIN must be an https:// origin")
            updates.append("enabled = %s"); params.append(body.enabled)
            if body.enabled != current[1]:
                changed["enabled"] = {"from": current[1], "to": body.enabled}
        if body.max_lots is not None:
            if not (0 < body.max_lots <= HARD_MAX_LOTS):
                raise HTTPException(400, f"max_lots must be between 0 and {HARD_MAX_LOTS:g}")
            updates.append("max_lots = %s"); params.append(Decimal(str(body.max_lots)))
            changed["max_lots"] = {"from": float(current[2]), "to": body.max_lots}
        if body.max_per_minute is not None:
            if not (1 <= body.max_per_minute <= 60):
                raise HTTPException(400, "max_per_minute must be between 1 and 60")
            updates.append("max_per_minute = %s"); params.append(body.max_per_minute)
            changed["max_per_minute"] = {"from": current[3], "to": body.max_per_minute}
        if body.max_open_positions is not None:
            if not (1 <= body.max_open_positions <= 50):
                raise HTTPException(400, "max_open_positions must be between 1 and 50")
            updates.append("max_open_positions = %s"); params.append(body.max_open_positions)
            changed["max_open_positions"] = {"from": current[4], "to": body.max_open_positions}
        if body.symbol_aliases is not None:
            try:
                aliases = {normalise_ticker(k): normalise_ticker(v)
                           for k, v in body.symbol_aliases.items()}
            except AlertError as exc:
                raise HTTPException(400, f"symbol_aliases: {exc}")
            updates.append("symbol_aliases = %s"); params.append(Jsonb(aliases))
            changed["symbol_aliases"] = {"to": aliases}

        if updates:
            conn.execute(f"UPDATE org_webhooks SET {', '.join(updates)} WHERE org_id = %s",
                         params + [ctx.org_id])
            if changed:
                audit(conn, ctx.org_id, ctx.user_email, "webhook_settings_changed", changed)
        return {"status": "ok", "changed": changed}

    return router
