"""The MT5 bridge's api side.

    MT5 terminal (EA)  --HTTPS every 250 ms-->  POST /api/mt5/sync   (key checked first)
                                                 |  proxies with account_id
                                                 v
                                          copier POST /mt5/sync

Two kinds of door live here. The MACHINE door (`/api/mt5/hello`,
`/api/mt5/sync`) is sessionless and CSRF-exempt (auth.CSRF_EXEMPT_PREFIXES);
it authenticates the terminal with a per-account key sent in the
`X-MirrorFleet-Key` header -- never the URL -- resolves the account, and
hands the report to the copier, which owns every decision. The api never
accepts a command from the EA: the terminal only reports and acknowledges.
The OPERATOR endpoints (session + role) create MT5 accounts,
rotate keys and edit symbol aliases; the download router serves the EA.

THE MACHINE DOOR, IN ORDER (the webhook route's discipline: nothing that
costs a database read happens before the request has proved its size and
carried a key):

  1. body size cap        -- hello 512 KB, sync 256 KB, before parsing -> 413
  2. key header present   -- 400; a URL parameter is never read
  3. sha256 lookup        -- one indexed read on a dedicated connection;
                             miss -> per-source bucket, 401. A removed
                             account or a rotated key is the same miss.
  4. parse JSON           -- 400; the body is never stored or logged
  5. proxy                -- POST {copier}/mt5/{hello|sync} with
                             {account_id, org_id, report}, 2 s timeout;
                             anything but a 200 back -> RETRY (sync) / 503 (hello)
  6. touch the link       -- last_seen_at / last_ip / balance / equity,
                             at most once per 10 s per account
  7. pass through         -- the copier's 200 body and content-type, as is

WHY A RESEND IS HARMLESS HERE (unlike TradingView). A sync carries the
terminal's full snapshot, the deals past its watermark and the acks for the
commands it executed; the copier ingests all of it idempotently (deals by
ticket, acks by command id). So every copier answer but a 200 -- never
connected, timed out mid-read, answered 5xx, or answered its own 400 (a
report it rejected, or an account it has not loaded yet) -- maps to the
same answer: `RETRY` for sync (200 text/plain in the wire grammar, so the
EA backs off to 2 s polls and sends the same report again) and 503 with
`retry_ms` for hello. The copier's 400 is a JSON `{"error": ...}` that is
not in the wire grammar and that the EA could do nothing with but poll
again, so it is never passed to the terminal; its text goes to the api log.
No outcome is "unknown", and a failed proxy does not touch the link.

KEYS. `mt5_` + token_urlsafe(32), shown to the operator exactly once; only
the sha256 is stored (mt5_links.key_hash). No key is ever written to a URL
or a log line: the one warning the door emits on a miss carries the source
address only, and any exception text that reaches a log goes through
webhooks.scrub_secrets, which redacts the `mt5_` prefix by value.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

import httpx
import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response

from ..auth import LoginRateLimiter
from ..config import ApiConfig
from ..netaddr import client_ip
# Shared with the webhook door on purpose: one JSON helper, one sha256, one
# exception describer, one redaction. They are private to that module by
# convention only; this module is the second door they were written for.
from .webhooks import _describe, _hash, _json, scrub_secrets

logger = logging.getLogger(__name__)

MT5_KEY_PREFIX = "mt5_"
KEY_HEADER = "X-MirrorFleet-Key"
# hello carries the broker's symbol list (chunks of 300); sync carries a
# snapshot plus at most 200 deals. Both caps are checked before parsing.
HELLO_MAX_BODY_BYTES = 512 * 1024
SYNC_MAX_BODY_BYTES = 256 * 1024
# The EA gives a request 3 s; the copier must answer well inside that.
COPIER_TIMEOUT_S = 2.0
# The poll interval the EA falls back to while the copier is away.
RETRY_POLL_MS = 2000
# mt5_links.last_seen_at / last_ip / balance / equity are written at most
# this often per account: the terminal polls four times a second.
LAST_SEEN_THROTTLE_S = 10.0
BAD_KEY_PER_MINUTE = 10
# A link with no report inside this window is "offline" on the Accounts
# page (the copier uses the same 15 s to mark the account degraded).
MT5_OFFLINE_AFTER_S = 15
DOWNLOAD_PATH = "/downloads/MirrorFleet.mq5"
# The EA's own InpServer default; the install steps name it when no public
# origin is configured.
EA_DEFAULT_SERVER = "https://mirrorfleet.com"


def _number(value: Any) -> Optional[float]:
    """A JSON number or nothing -- a terminal's report is untrusted input,
    and a bool is not a balance."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _retry(kind: str, reason: str) -> Response:
    """Nothing was applied; the EA backs off to RETRY_POLL_MS and sends the
    same report again. Sync answers in the wire grammar the EA parses; hello
    answers 503 (its caller checks the status, not the body). `reason` is
    for the human reading a hello answer or the log, never for the terminal."""
    if kind == "sync":
        return PlainTextResponse(f"RETRY\t{int(time.time() * 1000)}\t{RETRY_POLL_MS}")
    return _json(503, {"status": "failed", "reason": reason, "retry_ms": RETRY_POLL_MS})


# ------------------------------------------------------------ the machine door


def create_mt5_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/mt5", tags=["mt5"])
    # account_id -> monotonic time of the last mt5_links write (the throttle).
    last_touch: Dict[int, float] = {}

    @router.post("/hello")
    async def hello(request: Request, cfg: ApiConfig = Depends(ApiConfig.from_env)):
        return await _door(request, cfg, "hello", HELLO_MAX_BODY_BYTES)

    @router.post("/sync")
    async def sync(request: Request, cfg: ApiConfig = Depends(ApiConfig.from_env)):
        return await _door(request, cfg, "sync", SYNC_MAX_BODY_BYTES)

    async def _door(request: Request, cfg: ApiConfig, kind: str, max_bytes: int):
        ip = client_ip(request, cfg)

        # ---- 1. size, before anything is parsed ----
        raw = await request.body()
        if len(raw) > max_bytes:
            return _json(413, {"status": "rejected", "reason": "body too large"})

        # ---- 2. the key, from the header only ----
        key = request.headers.get(KEY_HEADER)
        if not key:
            return _json(400, {"status": "rejected", "reason": f"missing {KEY_HEADER} header"})

        # The dependency-injected connection is opened only past the door:
        # an unauthenticated flood must not open a Postgres connection each.
        with psycopg.connect(cfg.postgres_dsn, autocommit=True) as conn:
            # ---- 3. resolve the account: one indexed read ----
            row = conn.execute(
                "SELECT l.account_id, a.org_id FROM mt5_links l "
                "JOIN accounts a ON a.ctid_trader_account_id = l.account_id "
                "WHERE l.key_hash = %s", (_hash(key),)).fetchone()
            if row is None:
                # Per-SOURCE bucket, consumed on the miss. It throttles the
                # warning, never the shape of the answer: a wrong key is
                # the same 401 every time, and the bucket key holds the
                # address, not the key.
                if not rate_limiter.is_limited(f"mt5-badkey:{ip}", BAD_KEY_PER_MINUTE):
                    logger.warning("mt5 %s with an unknown key from %s", kind, ip)
                return _json(401, {"status": "rejected", "reason": "unknown key"})
            account_id, org_id = int(row[0]), int(row[1])

            # ---- 4. parse ----
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = None
            if not isinstance(body, dict):
                return _json(400, {"status": "rejected",
                                   "reason": "the report must be a JSON object"})

            # ---- 5. proxy ----
            client = request.app.state.http
            try:
                upstream = await client.post(
                    f"{cfg.copier_control_url}/mt5/{kind}",
                    json={"account_id": account_id, "org_id": org_id, "report": body},
                    timeout=COPIER_TIMEOUT_S)
            except httpx.HTTPError as exc:
                logger.warning("mt5 %s for account %s: copier unreachable (%s)",
                               kind, account_id, scrub_secrets(_describe(exc)))
                return _retry(kind, "copier unreachable")
            if upstream.status_code != 200:
                # Only a 200 is in the wire grammar. A 5xx is a copier fault;
                # a 400 is the copier rejecting the report, or not knowing
                # the account yet (the row is inserted here, then the copier
                # is asked to reload). The terminal can do nothing with a
                # JSON error and a resend is harmless, so both are RETRY;
                # the copier's reason is for the log, not the terminal.
                logger.warning("mt5 %s for account %s: copier answered %s (%s)",
                               kind, account_id, upstream.status_code,
                               scrub_secrets(upstream.text[:200]))
                return _retry(kind, f"copier answered {upstream.status_code}")

            # ---- 6. touch the link, throttled ----
            now = time.monotonic()
            if now - last_touch.get(account_id, float("-inf")) >= LAST_SEEN_THROTTLE_S:
                conn.execute(
                    "UPDATE mt5_links SET last_seen_at = now(), last_ip = %s, "
                    "balance = COALESCE(%s, balance), equity = COALESCE(%s, equity) "
                    "WHERE account_id = %s",
                    (ip, _number(body.get("balance")), _number(body.get("equity")),
                     account_id))
                last_touch[account_id] = now

            # ---- 7. pass the copier's 200 through: body and content-type as is ----
            return Response(content=upstream.content, status_code=upstream.status_code,
                            media_type=upstream.headers.get("content-type", "text/plain"))

    return router
