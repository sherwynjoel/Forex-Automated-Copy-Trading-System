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
from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from .settings_control import COPIER_SLOW_COMMAND_TIMEOUT_S, _proxy_to_copier

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


def _deposit_json(row) -> Dict[str, Any]:
    (dep_id, user_id, account_id, amount, coin, txid, note, status, decided_by,
     decided_at, decision_note, created_at) = row[:12]
    out = {"id": dep_id, "user_id": user_id,
           "account_id": int(account_id) if account_id is not None else None,
           "amount": float(amount), "coin": coin, "txid": txid, "note": note,
           "status": status, "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "created_at": _iso(created_at)}
    if len(row) > 12:
        out["email"], out["display_name"] = row[12], row[13]
    return out


_DEPOSIT_COLS = ("id, user_id, account_id, amount, coin, txid, note, status, decided_by, "
                 "decided_at, decision_note, created_at")


_WITHDRAWAL_COLS = ("id, user_id, account_id, amount, destination, status, equity_at_request, "
                    "equity_verified, decided_by, decided_at, decision_note, paid_by, paid_at, "
                    "txid, created_at")


def _withdrawal_json(row) -> Dict[str, Any]:
    (wd_id, user_id, account_id, amount, destination, status, eq_req, verified, decided_by,
     decided_at, decision_note, paid_by, paid_at, txid, created_at) = row[:15]
    out = {"id": wd_id, "user_id": user_id, "account_id": int(account_id),
           "amount": float(amount), "destination": destination, "status": status,
           "equity_at_request": float(eq_req) if eq_req is not None else None,
           "equity_verified": bool(verified), "decided_by": decided_by,
           "decided_at": _iso(decided_at), "decision_note": decision_note,
           "paid_by": paid_by, "paid_at": _iso(paid_at), "txid": txid,
           "created_at": _iso(created_at)}
    if len(row) > 15:
        out["email"], out["display_name"] = row[15], row[16]
    return out


class DepositNotice(BaseModel):
    amount: Any
    coin: str
    txid: str
    note: Optional[str] = None


class Decision(BaseModel):
    status: str
    note: Optional[str] = None


class WithdrawalRequest(BaseModel):
    amount: Any
    destination: str


class PaidBody(BaseModel):
    txid: str


# ------------------------------------------------------------ investor


def create_investor_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["investor"])
    # Ten notices/requests per investor per hour. A separate instance
    # because the shared login limiter's window is one minute.
    hourly = LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)

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

    @router.get("/investor/deposits", response_model=List[Dict[str, Any]])
    async def my_deposits(ctx: OrgContext = Depends(require_org_role("investor")),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {_DEPOSIT_COLS} FROM investor_deposits "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [_deposit_json(r) for r in rows]

    @router.post("/investor/deposits", status_code=201, response_model=Dict[str, Any])
    async def file_deposit(body: DepositNotice,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        wallet = _wallet(conn, ctx.org_id)
        if wallet is None:
            raise HTTPException(status_code=409, detail="Deposits are not open yet")
        try:
            amount = parse_amount(body.amount)
            coin = clean_text(body.coin, "coin", max_len=16).upper()
            txid = clean_text(body.txid, "txid")
            note = clean_text(body.note, "note", max_len=500, required=False)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if coin != wallet["coin"].upper():
            raise HTTPException(
                status_code=400,
                detail=f"this workspace accepts {wallet['coin']} on {wallet['network']}, not {coin}")
        if hourly.is_limited(f"investor-deposit:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many deposit notices; try again later")
        row = conn.execute(
            "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid, note) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_DEPOSIT_COLS}",
            (ctx.org_id, ctx.user_id, amount, coin, txid, note)).fetchone()
        out = _deposit_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_deposit_noticed", "warning",
               {"deposit_id": out["id"], "amount": out["amount"], "coin": coin, "txid": txid,
                "user_id": ctx.user_id,
                "summary": f"Deposit notice: {amount:.2f} {coin} from {ctx.user_email}"})
        return out

    @router.get("/investor/withdrawals", response_model=List[Dict[str, Any]])
    async def my_withdrawals(ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {_WITHDRAWAL_COLS} FROM investor_withdrawals "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [_withdrawal_json(r) for r in rows]

    @router.post("/investor/withdrawals", status_code=201, response_model=Dict[str, Any])
    async def request_withdrawal(body: WithdrawalRequest, http_request: Request,
                                 ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn),
                                 cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = _linked_account(conn, ctx.org_id, ctx.user_id)
        if account_id is None:
            raise HTTPException(status_code=409, detail="no account linked yet")
        try:
            amount = parse_amount(body.amount)
            destination = clean_text(body.destination, "destination")
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        deposits, withdrawals = _ledger_rows(conn, ctx.org_id, ctx.user_id)
        equity, _source, _positions = await _equity_for(
            http_request.app.state.http, cfg, conn, ctx.org_id, account_id)
        available = summarise(deposits, withdrawals, equity).available
        if available is not None and amount > available:
            raise HTTPException(
                status_code=400,
                detail=f"amount exceeds what is available to withdraw ({available:.2f})")
        if hourly.is_limited(f"investor-withdrawal:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many withdrawal requests; try again later")
        row = conn.execute(
            "INSERT INTO investor_withdrawals (org_id, user_id, account_id, amount, destination, "
            "equity_at_request, equity_verified) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {_WITHDRAWAL_COLS}",
            (ctx.org_id, ctx.user_id, account_id, amount, destination, equity,
             equity is not None)).fetchone()
        out = _withdrawal_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_withdrawal_requested", "warning",
               {"withdrawal_id": out["id"], "amount": out["amount"], "destination": destination,
                "user_id": ctx.user_id, "equity_verified": out["equity_verified"],
                "summary": f"Withdrawal request: {amount:.2f} from {ctx.user_email}"},
               account_id=account_id)
        return out

    def _require_linked(conn: psycopg.Connection, ctx: OrgContext) -> int:
        account_id = _linked_account(conn, ctx.org_id, ctx.user_id)
        if account_id is None:
            raise HTTPException(status_code=409, detail="no account linked yet")
        return account_id

    @router.get("/investor/positions", response_model=Dict[str, Any])
    async def my_positions(http_request: Request,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn),
                           cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = _require_linked(conn, ctx)
        _equity, source, positions = await _equity_for(
            http_request.app.state.http, cfg, conn, ctx.org_id, account_id)
        keys = ("position_id", "symbol", "side", "volume", "entry_price", "current_price",
                "stop_loss", "take_profit", "pnl_quote")
        return {"equity_source": source,
                "positions": [{k: p.get(k) for k in keys} for p in positions if isinstance(p, dict)]}

    @router.get("/investor/analytics", response_model=Dict[str, Any])
    async def my_analytics(http_request: Request, weeks: int = 4,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn),
                           cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = _require_linked(conn, ctx)
        weeks = max(1, min(weeks, 12))
        return await _proxy_to_copier(
            http_request.app.state.http,
            f"{cfg.copier_control_url}/analytics?account_id={account_id}&weeks={weeks}",
            method="GET", timeout=COPIER_SLOW_COMMAND_TIMEOUT_S)

    @router.get("/investor/history/{kind}", response_model=Dict[str, Any])
    async def my_history(kind: str, http_request: Request,
                         from_ms: int = Query(..., alias="from"),
                         to_ms: int = Query(..., alias="to"),
                         ctx: OrgContext = Depends(require_org_role("investor")),
                         conn: psycopg.Connection = Depends(get_conn),
                         cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        if kind not in ("deals", "orders", "cashflow"):
            raise HTTPException(status_code=400, detail="kind must be deals, orders or cashflow")
        account_id = _require_linked(conn, ctx)
        return await _proxy_to_copier(
            http_request.app.state.http,
            f"{cfg.copier_control_url}/history/{kind}"
            f"?account_id={account_id}&from={from_ms}&to={to_ms}", method="GET")

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

    @router.get("/investor-deposits", response_model=List[Dict[str, Any]])
    async def deposit_queue(status: Optional[str] = None,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "d.org_id = %s" + (" AND d.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            "SELECT d.id, d.user_id, d.account_id, d.amount, d.coin, d.txid, d.note, d.status, "
            "d.decided_by, d.decided_at, d.decision_note, d.created_at, u.email, u.display_name "
            "FROM investor_deposits d JOIN users u ON u.id = d.user_id "
            f"WHERE {where} ORDER BY (d.status = 'pending') DESC, d.created_at DESC, d.id DESC",
            params).fetchall()
        return [_deposit_json(r) for r in rows]

    @router.post("/investor-deposits/{deposit_id}/decision", response_model=Dict[str, Any])
    async def decide_deposit(deposit_id: int, body: Decision,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if not can_transition(DEPOSIT_TRANSITIONS, "pending", new_status):
            raise HTTPException(status_code=400, detail="status must be confirmed or rejected")
        try:
            note = clean_text(body.note, "note", max_len=500, required=(new_status == "rejected"))
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id FROM investor_deposits WHERE id = %s AND org_id = %s",
            (deposit_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        linked = _linked_account(conn, ctx.org_id, current[1]) if new_status == "confirmed" else None
        row = conn.execute(
            "UPDATE investor_deposits SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s, account_id = COALESCE(%s, account_id) "
            "WHERE id = %s AND org_id = %s AND status = 'pending' "
            f"RETURNING {_DEPOSIT_COLS}",
            (new_status, ctx.user_id, note, linked, deposit_id, ctx.org_id)).fetchone()
        if not row:
            (status_now,) = conn.execute(
                "SELECT status FROM investor_deposits WHERE id = %s", (deposit_id,)).fetchone()
            raise HTTPException(status_code=409, detail=f"deposit is already {status_now}")
        out = _deposit_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_deposit_decided", "info",
               {"deposit_id": deposit_id, "status": new_status, "note": note,
                "user_id": current[1], "amount": out["amount"], "coin": out["coin"]},
               account_id=out["account_id"])
        return out

    @router.get("/investor-withdrawals", response_model=List[Dict[str, Any]])
    async def withdrawal_queue(status: Optional[str] = None,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "w.org_id = %s" + (" AND w.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            "SELECT w.id, w.user_id, w.account_id, w.amount, w.destination, w.status, "
            "w.equity_at_request, w.equity_verified, w.decided_by, w.decided_at, "
            "w.decision_note, w.paid_by, w.paid_at, w.txid, w.created_at, u.email, u.display_name "
            "FROM investor_withdrawals w JOIN users u ON u.id = w.user_id "
            f"WHERE {where} ORDER BY (w.status IN ('requested', 'approved')) DESC, "
            "w.created_at DESC, w.id DESC", params).fetchall()
        return [_withdrawal_json(r) for r in rows]

    @router.post("/investor-withdrawals/{withdrawal_id}/decision", response_model=Dict[str, Any])
    async def decide_withdrawal(withdrawal_id: int, body: Decision,
                                ctx: OrgContext = Depends(require_org_role("admin")),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="status must be approved or rejected")
        try:
            note = clean_text(body.note, "note", max_len=500, required=(new_status == "rejected"))
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id FROM investor_withdrawals WHERE id = %s AND org_id = %s",
            (withdrawal_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if not can_transition(WITHDRAWAL_TRANSITIONS, current[0], new_status):
            raise HTTPException(status_code=409, detail=f"withdrawal is already {current[0]}")
        row = conn.execute(
            "UPDATE investor_withdrawals SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {_WITHDRAWAL_COLS}",
            (new_status, ctx.user_id, note, withdrawal_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="withdrawal was decided by someone else")
        out = _withdrawal_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_withdrawal_decided", "info",
               {"withdrawal_id": withdrawal_id, "status": new_status, "note": note,
                "user_id": current[1], "amount": out["amount"]}, account_id=out["account_id"])
        return out

    @router.post("/investor-withdrawals/{withdrawal_id}/paid", response_model=Dict[str, Any])
    async def mark_paid(withdrawal_id: int, body: PaidBody,
                        ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            txid = clean_text(body.txid, "txid")
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id FROM investor_withdrawals WHERE id = %s AND org_id = %s",
            (withdrawal_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if not can_transition(WITHDRAWAL_TRANSITIONS, current[0], "paid"):
            raise HTTPException(status_code=409, detail=f"withdrawal is {current[0]}, not approved")
        row = conn.execute(
            "UPDATE investor_withdrawals SET status = 'paid', paid_by = %s, paid_at = now(), "
            "txid = %s WHERE id = %s AND org_id = %s AND status = 'approved' "
            f"RETURNING {_WITHDRAWAL_COLS}",
            (ctx.user_id, txid, withdrawal_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="withdrawal was changed by someone else")
        out = _withdrawal_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_withdrawal_paid", "info",
               {"withdrawal_id": withdrawal_id, "txid": txid, "user_id": current[1],
                "amount": out["amount"]}, account_id=out["account_id"])
        return out

    return router
