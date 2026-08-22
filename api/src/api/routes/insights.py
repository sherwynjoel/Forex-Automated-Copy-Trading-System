"""Insight endpoints: margin estimates, candles, position drill-down,
performance analytics (all thin copier proxies), and the DB-computed
per-org overview stats block.

Every route here is org-scoped and read-only, so `viewer` is the threshold
-- the same rank that already reads /accounts, /state and the account
details and history endpoints. Account-taking routes call
require_account_in_org BEFORE proxying: the copier's own query routes are
account-scoped with no notion of orgs (tenancy is enforced here, exactly as
it is for /details and /history), so this check is the only thing standing
between one desk and another desk's broker data.
"""
from typing import Any, Dict, List
from urllib.parse import quote as _urlquote

import psycopg
from fastapi import APIRouter, Depends, Query, Request

from ..config import ApiConfig
from ..db import get_conn
from ..rbac import OrgContext, require_org_role, require_account_in_org
from .settings_control import COPIER_SLOW_COMMAND_TIMEOUT_S, _proxy_to_copier

# Hard ceiling on /analytics' broker fan-out. Each week is one sequential
# ProtoOADealListReq on the CTraderClient that EVERY org's orders share, and
# the SDK paces that wire at 5 messages/second -- so the request's weeks are
# seconds of wire occupancy for the whole deployment, chosen from a one-click
# dropdown by anyone with `viewer`. 12 weeks (~2.4s) is the most a read this
# cheap to trigger may cost everyone else. Out-of-range values are CLAMPED,
# not rejected, exactly as `limit` is on /events -- a range control is not a
# validation error.
MIN_ANALYTICS_WEEKS = 1
MAX_ANALYTICS_WEEKS = 12


def create_insights_router() -> APIRouter:
    """Create router for org-scoped insight endpoints."""
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["insights"])

    @router.get("/accounts/{account_id}/margin-estimate", response_model=dict)
    async def margin_estimate(
        account_id: int,
        http_request: Request,
        symbol: str,
        volume_lots: float,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """What margin the broker would require for this order, per side."""
        require_account_in_org(conn, ctx.org_id, account_id)
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/margin-estimate"
            f"?account_id={account_id}&symbol={_urlquote(symbol, safe='')}"
            f"&volume_lots={volume_lots}",
            method="GET",
        )

    @router.get("/accounts/{account_id}/quote", response_model=dict)
    async def quote(
        account_id: int,
        http_request: Request,
        symbol: str,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """Live bid/ask for the trade ticket (null before the first tick)."""
        require_account_in_org(conn, ctx.org_id, account_id)
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/quote"
            f"?account_id={account_id}&symbol={_urlquote(symbol, safe='')}",
            method="GET",
        )

    @router.get("/accounts/{account_id}/trendbars", response_model=dict)
    async def trendbars(
        account_id: int,
        http_request: Request,
        symbol: str,
        period: str,
        from_ms: int = Query(..., alias="from"),
        to_ms: int = Query(..., alias="to"),
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """Historical candles for one of the account's symbols."""
        require_account_in_org(conn, ctx.org_id, account_id)
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/trendbars"
            f"?account_id={account_id}&symbol={_urlquote(symbol, safe='')}"
            f"&period={_urlquote(period, safe='')}&from={from_ms}&to={to_ms}",
            method="GET",
        )

    @router.get("/accounts/{account_id}/positions/{position_id}/deals",
                response_model=dict)
    async def position_deals(
        account_id: int,
        position_id: int,
        http_request: Request,
        from_ms: int = Query(..., alias="from"),
        to_ms: int = Query(..., alias="to"),
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """One position's full deal lifecycle (fills, partial closes, close)."""
        require_account_in_org(conn, ctx.org_id, account_id)
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/position-deals"
            f"?account_id={account_id}&position_id={position_id}"
            f"&from={from_ms}&to={to_ms}",
            method="GET",
        )

    @router.get("/accounts/{account_id}/analytics", response_model=dict)
    async def analytics(
        account_id: int,
        http_request: Request,
        weeks: int = 4,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """Performance aggregation over the last `weeks` of deal history.

        `weeks` is clamped to [MIN_ANALYTICS_WEEKS, MAX_ANALYTICS_WEEKS]
        rather than validated: see the constants for why the ceiling exists.
        The copier clamps again on its own side (it is reachable without the
        api), so this is a budget, not the only guard.

        Unlike the other three proxies this one is bounded by BROKER round
        trips, not local work -- up to MAX_ANALYTICS_WEEKS sequential deal
        requests paced at 5 msg/s -- so it gets the same raised per-request
        timeout as /resync and /close-all. On the shared client's 5s default
        a legitimate multi-week request would 502 while the copier kept
        spending broker budget on a result nobody would receive.
        """
        require_account_in_org(conn, ctx.org_id, account_id)
        weeks = max(MIN_ANALYTICS_WEEKS, min(weeks, MAX_ANALYTICS_WEEKS))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/analytics"
            f"?account_id={account_id}&weeks={weeks}",
            method="GET",
            timeout=COPIER_SLOW_COMMAND_TIMEOUT_S,
        )

    @router.get("/overview", response_model=dict)
    async def overview(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> dict:
        """This org's Overview stats: fleet counts, copies made today,
        yesterday's portfolio snapshot (for the vs-yesterday delta), and the
        recent-copy feed. Live equity/positions come from
        /api/orgs/{org_id}/state; this endpoint covers what only the
        database knows.

        EVERY query below is restricted to ctx.org_id -- an unscoped count
        here would put another desk's fleet size, copy volume and portfolio
        value on this desk's front page.
        """
        accounts = conn.execute(
            "SELECT role, enabled, status FROM accounts WHERE org_id = %s",
            (ctx.org_id,),
        ).fetchall()
        masters = sum(1 for r in accounts if r[0] == "master")
        active_slaves = sum(
            1 for r in accounts
            if r[0] == "slave" and r[1] and r[2] != "paused")
        disabled_or_paused = sum(
            1 for r in accounts
            if r[0] == "slave" and (not r[1] or r[2] == "paused"))
        degraded = sum(1 for r in accounts if r[2] == "degraded")

        copied_today = conn.execute(
            """SELECT COUNT(*) FROM events
               WHERE category = 'slave_action'
                 AND org_id = %s
                 AND payload->>'action' IN ('position_filled', 'pending_fill')
                 AND ts >= date_trunc('day', now())""",
            (ctx.org_id,),
        ).fetchone()[0]

        # portfolio_snapshots.org_id is nullable and FK-less (migration 006),
        # so pre-006 rows and rows whose account has since been disconnected
        # simply drop out of this sum rather than leaking across orgs.
        snapshot = conn.execute(
            """SELECT SUM(balance), SUM(equity) FROM portfolio_snapshots
               WHERE snapshot_date = CURRENT_DATE - 1 AND org_id = %s""",
            (ctx.org_id,),
        ).fetchone()
        yesterday = None
        if snapshot and snapshot[0] is not None:
            yesterday = {
                "total_balance": float(snapshot[0]),
                "total_equity": float(snapshot[1]) if snapshot[1] is not None else None,
            }

        copy_rows = conn.execute(
            """SELECT m.status, m.master_position_id, m.master_order_id,
                      m.slave_account_id, a.trader_login, a.nickname,
                      m.symbol, m.slave_volume, m.fill_price, m.error,
                      m.updated_at
               FROM mappings m
               JOIN accounts a ON a.ctid_trader_account_id = m.slave_account_id
               WHERE m.org_id = %s
               ORDER BY m.updated_at DESC, m.id DESC
               LIMIT 15""",
            (ctx.org_id,),
        ).fetchall()
        recent_copies: List[Dict[str, Any]] = [
            {
                "status": r[0],
                "master_position_id": r[1],
                "master_order_id": r[2],
                "slave_account_id": r[3],
                "slave_login": r[4],
                "slave_nickname": r[5],
                "symbol": r[6],
                "slave_volume": r[7],
                "fill_price": float(r[8]) if r[8] is not None else None,
                "error": r[9],
                "updated_at": r[10].isoformat(),
            }
            for r in copy_rows
        ]

        return {
            "accounts_connected": len(accounts),
            "masters": masters,
            "active_slaves": active_slaves,
            "disabled_or_paused": disabled_or_paused,
            "degraded": degraded,
            "copied_today": copied_today,
            "yesterday": yesterday,
            "recent_copies": recent_copies,
        }

    return router
