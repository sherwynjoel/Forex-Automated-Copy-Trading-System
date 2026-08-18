"""Accounts management endpoints."""
from decimal import Decimal, InvalidOperation
from typing import Optional, List, Any

import httpx
import psycopg
from psycopg import errors
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import ApiConfig
from ..db import get_conn
from ..rbac import OrgContext, require_org_role, require_account_in_org
from .settings_control import _proxy_to_copier


class PatchAccountRequest(BaseModel):
    """Request body for PATCH /api/orgs/{org_id}/accounts/{id}."""
    role: Optional[str] = None
    multiplier: Optional[Any] = None  # Accept any type, validate manually
    enabled: Optional[bool] = None
    nickname: Optional[str] = None


class AccountResponse(BaseModel):
    """Response for an account."""
    ctid_trader_account_id: int
    trader_login: int
    is_live: bool
    role: str
    enabled: bool
    multiplier: Decimal
    status: str
    last_error: Optional[str] = None
    connection_status: str = "active"
    nickname: Optional[str] = None


def create_accounts_router() -> APIRouter:
    """Create router for accounts management."""
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["accounts"])

    @router.get("/accounts", response_model=List[AccountResponse])
    async def list_accounts(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> List[AccountResponse]:
        """List this org's accounts with their connection status."""
        rows = conn.execute(
            """SELECT a.ctid_trader_account_id, a.trader_login, a.is_live, a.role, a.enabled,
                      a.multiplier, a.status, a.last_error, c.status as conn_status, a.nickname
               FROM accounts a
               JOIN ctid_connections c ON a.ctid_connection_id = c.id
               WHERE a.org_id = %s
               ORDER BY a.ctid_trader_account_id""",
            (ctx.org_id,),
        ).fetchall()

        return [
            AccountResponse(
                ctid_trader_account_id=row[0],
                trader_login=row[1],
                is_live=row[2],
                role=row[3],
                enabled=row[4],
                multiplier=row[5],
                status=row[6],
                last_error=row[7],
                connection_status=row[8],
                nickname=row[9],
            )
            for row in rows
        ]

    @router.patch("/accounts/{account_id}", response_model=dict)
    async def patch_account(
        account_id: int,
        request: PatchAccountRequest,
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ):
        """Update an account (role, multiplier, enabled)."""
        # Validate request
        if request.role is not None:
            if request.role not in ("master", "slave", "ignored"):
                raise HTTPException(status_code=400, detail="role must be one of: master, slave, ignored")

        validated_multiplier = None
        if request.multiplier is not None:
            # Validate and convert multiplier
            try:
                multiplier_decimal = Decimal(str(request.multiplier))
                if multiplier_decimal <= 0:
                    raise HTTPException(status_code=400, detail="multiplier must be greater than 0")
                validated_multiplier = multiplier_decimal
            except (InvalidOperation, ValueError, TypeError):
                raise HTTPException(status_code=400, detail="multiplier must be a valid positive number")

        require_account_in_org(conn, ctx.org_id, account_id)

        # Build dynamic update
        updates = []
        params = []

        if request.role is not None:
            updates.append("role = %s")
            params.append(request.role)

        if validated_multiplier is not None:
            updates.append("multiplier = %s")
            params.append(validated_multiplier)

        if request.enabled is not None:
            updates.append("enabled = %s")
            params.append(request.enabled)

        if request.nickname is not None:
            # An empty (or whitespace) nickname clears it.
            updates.append("nickname = %s")
            params.append(request.nickname.strip() or None)

        if not updates:
            # No updates requested, just return current account
            row = conn.execute(
                """SELECT ctid_trader_account_id, trader_login, is_live, role, enabled,
                          multiplier, status, last_error, nickname FROM accounts
                   WHERE ctid_trader_account_id = %s AND org_id = %s""",
                (account_id, ctx.org_id)
            ).fetchone()
            return {
                "ctid_trader_account_id": row[0],
                "trader_login": row[1],
                "is_live": row[2],
                "role": row[3],
                "enabled": row[4],
                "multiplier": float(row[5]),
                "status": row[6],
                "last_error": row[7],
                "nickname": row[8],
            }

        params.append(account_id)
        params.append(ctx.org_id)
        update_sql = (
            f"UPDATE accounts SET {', '.join(updates)} "
            "WHERE ctid_trader_account_id = %s AND org_id = %s"
        )

        try:
            conn.execute(update_sql, params)
        except errors.UniqueViolation:
            # This happens when trying to set a second master
            raise HTTPException(
                status_code=409,
                detail="a master already exists"
            )

        # Get updated account
        row = conn.execute(
            """SELECT ctid_trader_account_id, trader_login, is_live, role, enabled,
                      multiplier, status, last_error, nickname FROM accounts
               WHERE ctid_trader_account_id = %s AND org_id = %s""",
            (account_id, ctx.org_id)
        ).fetchone()

        result = {
            "ctid_trader_account_id": row[0],
            "trader_login": row[1],
            "is_live": row[2],
            "role": row[3],
            "enabled": row[4],
            "multiplier": float(row[5]),
            "status": row[6],
            "last_error": row[7],
            "nickname": row[8],
        }

        # If role was changed, trigger copier reload
        if request.role is not None:
            try:
                client = http_request.app.state.http
                url = f"{cfg.copier_control_url}/reload"
                response = await client.post(url)
                result["copier_reloaded"] = response.status_code == 200
            except Exception:
                result["copier_reloaded"] = False

        return result

    @router.get("/accounts/{account_id}/details", response_model=dict)
    async def account_details(
        account_id: int,
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """Full account profile: the copier's live broker-side details
        (balance, leverage, broker, account type, open positions...) merged
        with what only this database knows (nickname, role, multiplier, and
        the OAuth grant behind the account)."""
        row = conn.execute(
            """SELECT a.nickname, a.role, a.enabled, a.multiplier, a.status,
                      a.last_error, a.is_live, a.trader_login,
                      c.granted_at, c.expires_at, c.status, c.scope
               FROM accounts a
               JOIN ctid_connections c ON a.ctid_connection_id = c.id
               WHERE a.ctid_trader_account_id = %s AND a.org_id = %s""",
            (account_id, ctx.org_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")

        client = http_request.app.state.http
        details = await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/details?account_id={account_id}",
            method="GET",
        )

        details.update({
            "nickname": row[0],
            "role": row[1],
            "enabled": row[2],
            "multiplier": float(row[3]),
            "status": row[4],
            "last_error": row[5],
            "is_live": row[6],
            "connection": {
                "granted_at": row[8].isoformat(),
                "expires_at": row[9].isoformat(),
                "status": row[10],
                "scope": row[11],
            },
        })
        if not details.get("trader_login"):
            details["trader_login"] = row[7]
        return details

    @router.get("/accounts/{account_id}/history/{kind}", response_model=dict)
    async def account_history(
        account_id: int,
        kind: str,
        http_request: Request,
        from_ms: int = Query(..., alias="from"),
        to_ms: int = Query(..., alias="to"),
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> dict:
        """Proxy deal/order/cash-flow history for one account from the
        copier. `from`/`to` are epoch milliseconds; cTrader allows at most a
        one-week window per request, so the dashboard pages by date range."""
        if kind not in ("deals", "orders", "cashflow"):
            raise HTTPException(
                status_code=400, detail="kind must be deals, orders or cashflow")
        require_account_in_org(conn, ctx.org_id, account_id)

        client = http_request.app.state.http
        return await _proxy_to_copier(
            client,
            f"{cfg.copier_control_url}/history/{kind}"
            f"?account_id={account_id}&from={from_ms}&to={to_ms}",
            method="GET",
        )

    @router.get("/accounts/{account_id}/symbols", response_model=List[dict])
    async def account_symbols(
        account_id: int,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> List[dict]:
        """The account's tradeable symbols from the local symbol cache (for
        the order ticket) -- no copier or broker round trip."""
        require_account_in_org(conn, ctx.org_id, account_id)

        rows = conn.execute(
            """SELECT name, symbol_id, digits, lot_size, min_volume, step_volume
               FROM symbol_cache WHERE account_id = %s ORDER BY name""",
            (account_id,),
        ).fetchall()
        return [
            {
                "name": r[0],
                "symbol_id": r[1],
                "digits": r[2],
                "min_volume_lots": r[4] / r[3] if r[3] else None,
                "step_volume_lots": r[5] / r[3] if r[3] else None,
            }
            for r in rows
        ]

    @router.delete("/accounts/{account_id}/connection")
    async def disconnect_account(
        account_id: int,
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ):
        """Disconnect the cTrader ID grant behind an account.

        Resolves the account's connection server-side (the dashboard never
        sees raw connection ids), deletes it -- cascading to every account
        discovered under that grant -- and asks the copier to reload so the
        accounts are de-authorized immediately rather than on next restart.
        """
        row = conn.execute(
            "SELECT ctid_connection_id FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
            (account_id, ctx.org_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        connection_id = row[0]

        count_row = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE ctid_connection_id = %s",
            (connection_id,),
        ).fetchone()

        conn.execute(
            "DELETE FROM ctid_connections WHERE id = %s AND org_id = %s",
            (connection_id, ctx.org_id),
        )

        result = {
            "detail": "Connection deleted. Note: tokens remain revocable at ctrader.com",
            "accounts_removed": count_row[0],
            "copier_reloaded": False,
        }
        try:
            client = http_request.app.state.http
            response = await client.post(f"{cfg.copier_control_url}/reload")
            result["copier_reloaded"] = response.status_code == 200
        except Exception:
            pass
        return result

    return router
