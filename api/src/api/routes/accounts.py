"""Accounts management endpoints."""
from decimal import Decimal, InvalidOperation
from typing import Optional, List, Any

import httpx
import psycopg
from psycopg import errors
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..config import ApiConfig
from ..db import get_conn


class PatchAccountRequest(BaseModel):
    """Request body for PATCH /api/accounts/{id}."""
    role: Optional[str] = None
    multiplier: Optional[Any] = None  # Accept any type, validate manually
    enabled: Optional[bool] = None


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


def create_accounts_router() -> APIRouter:
    """Create router for accounts management."""
    router = APIRouter(prefix="/api", tags=["accounts"])

    @router.get("/accounts", response_model=List[AccountResponse])
    async def list_accounts(
        _: bool = Depends(require_admin),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> List[AccountResponse]:
        """List all accounts with their connection status."""
        rows = conn.execute(
            """SELECT a.ctid_trader_account_id, a.trader_login, a.is_live, a.role, a.enabled,
                      a.multiplier, a.status, a.last_error, c.status as conn_status
               FROM accounts a
               JOIN ctid_connections c ON a.ctid_connection_id = c.id
               ORDER BY a.ctid_trader_account_id"""
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
            )
            for row in rows
        ]

    @router.patch("/accounts/{account_id}", response_model=dict)
    async def patch_account(
        account_id: int,
        request: PatchAccountRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
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

        # Check if account exists
        exists = conn.execute(
            "SELECT 1 FROM accounts WHERE ctid_trader_account_id = %s",
            (account_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Account not found")

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

        if not updates:
            # No updates requested, just return current account
            row = conn.execute(
                """SELECT ctid_trader_account_id, trader_login, is_live, role, enabled,
                          multiplier, status, last_error FROM accounts
                   WHERE ctid_trader_account_id = %s""",
                (account_id,)
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
            }

        params.append(account_id)
        update_sql = f"UPDATE accounts SET {', '.join(updates)} WHERE ctid_trader_account_id = %s"

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
                      multiplier, status, last_error FROM accounts
               WHERE ctid_trader_account_id = %s""",
            (account_id,)
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

    @router.delete("/accounts/{account_id}/connection")
    async def disconnect_account(
        account_id: int,
        http_request: Request,
        _: bool = Depends(require_admin),
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
            "SELECT ctid_connection_id FROM accounts WHERE ctid_trader_account_id = %s",
            (account_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        connection_id = row[0]

        count_row = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE ctid_connection_id = %s",
            (connection_id,),
        ).fetchone()

        conn.execute("DELETE FROM ctid_connections WHERE id = %s", (connection_id,))

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
