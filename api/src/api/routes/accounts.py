"""Accounts management endpoints."""
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, List, Any

import httpx
import psycopg
from psycopg.types.json import Jsonb
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
    # ISO date (YYYY-MM-DD); an empty string clears it, like nickname.
    cutoff_date: Optional[str] = None


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
    cutoff_date: Optional[date] = None


# Upper bound on an account's copy multiplier. Scaling beyond this is
# almost always a typo rather than an intent (see patch_account).
logger = logging.getLogger(__name__)

MAX_MULTIPLIER = Decimal("10")


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
                      a.multiplier, a.status, a.last_error, c.status as conn_status, a.nickname,
                      a.cutoff_date
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
                cutoff_date=row[10],
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
                # Fat-finger guard: the multiplier scales EVERY copied trade
                # on this account, so a typo of 100 for 1.0 multiplies the
                # slave's real exposure a hundredfold.
                if multiplier_decimal > MAX_MULTIPLIER:
                    raise HTTPException(
                        status_code=400,
                        detail=f"multiplier must be {MAX_MULTIPLIER} or less")
                validated_multiplier = multiplier_decimal
            except (InvalidOperation, ValueError, TypeError):
                raise HTTPException(status_code=400, detail="multiplier must be a valid positive number")

        # An empty (or whitespace) cutoff_date clears it, like nickname.
        validated_cutoff = None
        if request.cutoff_date is not None and request.cutoff_date.strip():
            try:
                validated_cutoff = date.fromisoformat(request.cutoff_date.strip())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="cutoff_date must be an ISO date (YYYY-MM-DD), or empty to clear")

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

        if request.cutoff_date is not None:
            updates.append("cutoff_date = %s")
            params.append(validated_cutoff)

        if not updates:
            # No updates requested, just return current account
            row = conn.execute(
                """SELECT ctid_trader_account_id, trader_login, is_live, role, enabled,
                          multiplier, status, last_error, nickname, cutoff_date FROM accounts
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
                "cutoff_date": row[9],
            }

        params.append(account_id)
        params.append(ctx.org_id)
        update_sql = (
            f"UPDATE accounts SET {', '.join(updates)} "
            "WHERE ctid_trader_account_id = %s AND org_id = %s"
        )

        # Promoting a master demotes the previous one ATOMICALLY (the
        # connection is autocommit, so the explicit transaction is what
        # makes demote+promote one action instead of a 409 scavenger
        # hunt). A UniqueViolation -- a concurrent promotion racing this
        # one -- rolls the demotion back and surfaces as the usual 409.
        demoted: list[int] = []
        try:
            with conn.transaction():
                if request.role == "master":
                    # EVERY other account becomes a slave -- the old master
                    # and any still-Ignored accounts alike. Choosing a
                    # master means "this one leads, everyone else follows".
                    rows = conn.execute(
                        """UPDATE accounts SET role = 'slave'
                           WHERE org_id = %s AND role != 'slave'
                             AND ctid_trader_account_id != %s
                           RETURNING ctid_trader_account_id""",
                        (ctx.org_id, account_id),
                    ).fetchall()
                    demoted = [r[0] for r in rows]
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
                      multiplier, status, last_error, nickname, cutoff_date FROM accounts
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
            "cutoff_date": row[9],
        }
        if demoted:
            result["demoted_to_slave"] = demoted

        # Role, multiplier and enabled decide how much money moves and
        # where; these writes never reach the copier's own event log, so
        # the audit row is written here with the acting user.
        audited = {k: v for k, v in {
            "role": request.role,
            "multiplier": (float(validated_multiplier)
                           if validated_multiplier is not None else None),
            "enabled": request.enabled,
        }.items() if v is not None}
        if audited:
            if demoted:
                audited["demoted_to_slave"] = demoted
            try:
                conn.execute(
                    "INSERT INTO events (org_id, account_id, category, severity, "
                    "payload, actor_email) VALUES (%s, %s, 'control', 'info', %s, %s)",
                    (ctx.org_id, account_id,
                     Jsonb({"action": "account_changed", **audited}),
                     ctx.user_email),
                )
            except Exception:
                logger.exception("failed to audit account change for %s", account_id)

        # Reload the copier for every field its routing snapshot bakes in:
        # role, enabled and multiplier all decide what gets copied and how
        # big. The copier caches that snapshot briefly for latency, and
        # this call is what makes an edit apply on the very next event
        # instead of whenever the cache happens to expire.
        if (request.role is not None or request.enabled is not None
                or validated_multiplier is not None):
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
            """SELECT c.name, c.symbol_id, c.digits, c.lot_size,
                      c.min_volume, c.step_volume, k.per_unit
               FROM symbol_cache c
               LEFT JOIN symbol_commission k
                      ON k.account_id = c.account_id
                     AND k.symbol_id = c.symbol_id
               WHERE c.account_id = %s ORDER BY c.name""",
            (account_id,),
        ).fetchall()
        return [
            {
                "name": r[0],
                "symbol_id": r[1],
                "digits": r[2],
                "min_volume_lots": r[4] / r[3] if r[3] else None,
                "step_volume_lots": r[5] / r[3] if r[3] else None,
                # Protocol units per 1.00 lot. The order ticket needs it to
                # express a stop or target as an AMOUNT of money rather than
                # a price: profit = price_move * (lots * lot_size / 100), so
                # without the contract size that sum cannot be inverted.
                "lot_size": r[3],
                # Round-trip commission on ONE unit, learned from this
                # account's own closed trades. Inverting the P&L formula
                # gives a GROSS amount, and the broker takes its cut out of
                # that, so a ticket asking for "$1.50" needs this to place
                # the target where $1.50 actually arrives. NULL means never
                # observed -- adjust nothing rather than assume free.
                "commission_per_unit": float(r[6]) if r[6] is not None else None,
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
