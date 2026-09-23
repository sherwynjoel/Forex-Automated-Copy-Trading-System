"""The investor portal.

Two routers. `create_investor_router` serves a member whose role is
`investor`: every handler first resolves the ONE account linked to the
caller (accounts.investor_user_id) and never accepts an account id from the
request, so an investor cannot name anyone else's account. Any member may
call these -- a desk role simply has no linked account and gets the
"unlinked" answers. `create_investor_admin_router` serves admins: the
wallet card, the investor list, linking, and the deposit and withdrawal
queues. The app records money movements that people make outside it; it
holds no keys and moves nothing.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import LoginRateLimiter
from ..config import ApiConfig
from ..db import get_conn
from ..investor_ledger import (
    DEPOSIT_TRANSITIONS, WITHDRAWAL_TRANSITIONS, LedgerError, can_transition,
    clean_text, parse_amount, summarise, summary_json)
from ..rbac import OrgContext, require_org_role
from ..ws import broadcaster
from .mt5 import MT5_OFFLINE_AFTER_S
from .settings_control import _proxy_to_copier

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 10


# ------------------------------------------------------------ helpers


def _event(conn: psycopg.Connection, org_id: int, actor_email: str, action: str,
           severity: str, detail: Dict[str, Any], account_id: Optional[int] = None) -> None:
    """One audit row per state change. The two "somebody asked" actions are
    warnings so the alerters ping an admin; decisions are info. Best-effort:
    a failed audit write is logged, never surfaced as a failed request."""
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, payload, actor_email) "
            "VALUES (%s, %s, 'control', %s, %s, %s)",
            (org_id, account_id, severity, Jsonb({"action": action, **detail}), actor_email))
    except Exception:
        logger.exception("failed to write investor audit event %s", action)


def _linked_account(conn: psycopg.Connection, org_id: int, user_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts "
        "WHERE org_id = %s AND investor_user_id = %s", (org_id, user_id)).fetchone()
    return int(row[0]) if row else None


def _wallet(conn: psycopg.Connection, org_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT coin, network, address, memo FROM org_investor_wallets WHERE org_id = %s",
        (org_id,)).fetchone()
    if not row:
        return None
    return {"coin": row[0], "network": row[1], "address": row[2], "memo": row[3]}


def _account_card(conn: psycopg.Connection, org_id: int, account_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """SELECT a.nickname, a.platform, a.status, a.last_error,
                  COALESCE(l.last_seen_at > now() - make_interval(secs => %s), false),
                  c.status
           FROM accounts a
           LEFT JOIN mt5_links l ON l.account_id = a.ctid_trader_account_id
           LEFT JOIN ctid_connections c ON a.ctid_connection_id = c.id
           WHERE a.ctid_trader_account_id = %s AND a.org_id = %s""",
        (MT5_OFFLINE_AFTER_S, account_id, org_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    connected = bool(row[4]) if row[1] == "mt5" else row[5] == "active"
    return {"account_id": account_id, "nickname": row[0], "platform": row[1],
            "status": row[2], "last_error": row[3], "connected": connected}


async def _org_state(client, cfg: ApiConfig, org_id: int) -> Optional[dict]:
    """One /state round trip for the whole org. None when the copier is
    down or answers with something that isn't a JSON object -- callers then
    fall back to last-known-equity for every account, not just one."""
    try:
        state = await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/state?org_id={org_id}", method="GET")
    except HTTPException:
        return None
    return state if isinstance(state, dict) else None


def _equity_from(state: Optional[dict], conn: psycopg.Connection,
                  account_id: int) -> tuple[Optional[Decimal], str, list]:
    """Live equity from an already-fetched /state snapshot, else the last
    equity the MT5 terminal reported, else unknown."""
    accounts = state.get("accounts") if isinstance(state, dict) else None
    entry = accounts.get(str(account_id)) if isinstance(accounts, dict) else None
    if isinstance(entry, dict) and entry.get("equity") is not None:
        return Decimal(str(entry["equity"])), "live", list(entry.get("positions") or [])
    row = conn.execute("SELECT equity FROM mt5_links WHERE account_id = %s",
                        (account_id,)).fetchone()
    if row and row[0] is not None:
        return Decimal(str(row[0])), "last known", []
    return None, "unknown", []


async def _equity_for(client, cfg: ApiConfig, conn: psycopg.Connection, org_id: int,
                       account_id: int) -> tuple[Optional[Decimal], str, list]:
    """Single-account convenience wrapper over `_org_state` + `_equity_from`,
    for callers that only ever need one account's equity (the investor's own
    summary; positions and withdrawals in later tasks). Never raises: an
    unreachable copier makes the figure 'last known', not the page an
    error."""
    return _equity_from(await _org_state(client, cfg, org_id), conn, account_id)


def _ledger_rows(conn: psycopg.Connection, org_id: int, user_id: int):
    deposits = [(r[0], Decimal(r[1])) for r in conn.execute(
        "SELECT status, amount FROM investor_deposits WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchall()]
    withdrawals = [(r[0], Decimal(r[1])) for r in conn.execute(
        "SELECT status, amount FROM investor_withdrawals WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchall()]
    return deposits, withdrawals


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ------------------------------------------------------------ investor


def create_investor_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["investor"])

    @router.get("/investor/summary", response_model=Dict[str, Any])
    async def investor_summary(
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("investor")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        (org_name,) = conn.execute("SELECT name FROM orgs WHERE id = %s", (ctx.org_id,)).fetchone()
        account_id = _linked_account(conn, ctx.org_id, ctx.user_id)
        deposits, withdrawals = _ledger_rows(conn, ctx.org_id, ctx.user_id)
        equity, source, _positions = (None, "unknown", [])
        card = None
        if account_id is not None:
            card = _account_card(conn, ctx.org_id, account_id)
            equity, source, _positions = await _equity_for(
                http_request.app.state.http, cfg, conn, ctx.org_id, account_id)
        summary = summarise(deposits, withdrawals, equity)
        return {
            "org": {"id": ctx.org_id, "name": org_name},
            "link_state": "linked" if account_id is not None else "unlinked",
            "account": card,
            "equity_source": source,
            "wallet_configured": _wallet(conn, ctx.org_id) is not None,
            **summary_json(summary),
        }

    @router.get("/investor/wallet", response_model=Dict[str, Any])
    async def investor_wallet(
        ctx: OrgContext = Depends(require_org_role("investor")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> Dict[str, Any]:
        wallet = _wallet(conn, ctx.org_id)
        if wallet is None:
            raise HTTPException(status_code=404, detail="Deposits are not open yet")
        return wallet

    return router


# ------------------------------------------------------------ admin


class WalletUpdate(BaseModel):
    coin: str
    network: str
    address: str
    memo: Optional[str] = None


class LinkBody(BaseModel):
    account_id: Optional[int] = None


def create_investor_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["investor-admin"])

    @router.get("/investor-wallet", response_model=Dict[str, Any])
    async def get_wallet(ctx: OrgContext = Depends(require_org_role("admin")),
                          conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        wallet = _wallet(conn, ctx.org_id)
        if wallet is None:
            raise HTTPException(status_code=404, detail="No wallet configured")
        return wallet

    @router.put("/investor-wallet", response_model=Dict[str, Any])
    async def put_wallet(body: WalletUpdate,
                          ctx: OrgContext = Depends(require_org_role("admin")),
                          conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            coin = clean_text(body.coin, "coin", max_len=16)
            network = clean_text(body.network, "network", max_len=32)
            address = clean_text(body.address, "address")
            memo = clean_text(body.memo, "memo", required=False)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address, memo, "
            "updated_by, updated_at) VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (org_id) DO UPDATE SET coin = EXCLUDED.coin, "
            "network = EXCLUDED.network, address = EXCLUDED.address, memo = EXCLUDED.memo, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            (ctx.org_id, coin, network, address, memo, ctx.user_id))
        _event(conn, ctx.org_id, ctx.user_email, "investor_wallet_set", "info",
               {"coin": coin, "network": network})
        return {"coin": coin, "network": network, "address": address, "memo": memo}

    @router.get("/investors", response_model=List[Dict[str, Any]])
    async def list_investors(
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, a.ctid_trader_account_id, a.nickname,
                      (SELECT count(*) FROM investor_deposits d
                        WHERE d.org_id = m.org_id AND d.user_id = u.id AND d.status = 'pending'),
                      (SELECT count(*) FROM investor_withdrawals w
                        WHERE w.org_id = m.org_id AND w.user_id = u.id
                          AND w.status IN ('requested', 'approved'))
               FROM org_memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN accounts a ON a.org_id = m.org_id AND a.investor_user_id = u.id
               WHERE m.org_id = %s AND m.role = 'investor'
               ORDER BY u.display_name, u.id""", (ctx.org_id,)).fetchall()
        # One /state round trip for the whole list, not one per investor:
        # the copier already returns every account's equity in a single
        # response keyed by account id.
        state = await _org_state(http_request.app.state.http, cfg, ctx.org_id)
        out = []
        for user_id, email, name, account_id, nickname, pend_dep, pend_wd in rows:
            deposits, withdrawals = _ledger_rows(conn, ctx.org_id, user_id)
            equity = None
            if account_id is not None:
                equity, _src, _pos = _equity_from(state, conn, int(account_id))
            s = summary_json(summarise(deposits, withdrawals, equity))
            out.append({"user_id": user_id, "email": email, "display_name": name,
                        "account_id": int(account_id) if account_id is not None else None,
                        "nickname": nickname, "equity": s["equity"],
                        "net_deposits": s["net_deposits"], "profit": s["profit"],
                        "pending_deposits": int(pend_dep), "pending_withdrawals": int(pend_wd)})
        return out

    @router.put("/investors/{user_id}/account", response_model=Dict[str, Any])
    async def link_account(user_id: int, body: LinkBody,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        member = conn.execute(
            "SELECT 1 FROM org_memberships WHERE org_id = %s AND user_id = %s AND role = 'investor'",
            (ctx.org_id, user_id)).fetchone()
        if not member:
            raise HTTPException(status_code=404, detail="Investor not found")
        with conn.transaction():
            conn.execute("UPDATE accounts SET investor_user_id = NULL "
                         "WHERE org_id = %s AND investor_user_id = %s", (ctx.org_id, user_id))
            if body.account_id is not None:
                updated = conn.execute(
                    "UPDATE accounts SET investor_user_id = %s "
                    "WHERE ctid_trader_account_id = %s AND org_id = %s "
                    "AND investor_user_id IS NULL RETURNING ctid_trader_account_id",
                    (user_id, body.account_id, ctx.org_id)).fetchone()
                if not updated:
                    raise HTTPException(status_code=404,
                                        detail="Account not found in this workspace, or already linked")
        _event(conn, ctx.org_id, ctx.user_email, "investor_account_linked", "info",
               {"user_id": user_id, "account_id": body.account_id}, account_id=body.account_id)
        return {"user_id": user_id, "account_id": body.account_id}

    return router
