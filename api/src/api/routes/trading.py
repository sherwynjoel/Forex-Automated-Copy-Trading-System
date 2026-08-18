"""Trading action proxies: manual orders, position close, order cancel,
and the kill switch -- now org-scoped.

Still deliberately thin on TRADE validation (symbol/volume/side/price rules
live in the copier), but the API owns TENANCY validation: the body's
account_id must belong to the caller's org before anything is proxied, and
close-all always carries the org id so the copier can never flatten outside
that org's book.
"""
from typing import Any, Dict

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import ApiConfig
from ..db import get_conn
from ..rbac import OrgContext, require_org_role, require_account_in_org
from .settings_control import COPIER_SLOW_COMMAND_TIMEOUT_S, _proxy_to_copier


def _required_account_id(body: Dict[str, Any]) -> int:
    account_id = body.get("account_id")
    if account_id is None:
        raise HTTPException(status_code=400, detail="account_id required")
    try:
        return int(account_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="account_id must be an integer")


def create_trading_router() -> APIRouter:
    """Router for manual trading actions and the kill switch."""
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["trading"])

    @router.post("/orders", response_model=Dict[str, Any])
    async def place_order(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("trader")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Place a manual order on one of THIS org's accounts."""
        require_account_in_org(conn, ctx.org_id, _required_account_id(body))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/order", method="POST", json=body)

    @router.post("/positions/close", response_model=Dict[str, Any])
    async def close_position(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("trader")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Close (or partially close) one position on an org account."""
        require_account_in_org(conn, ctx.org_id, _required_account_id(body))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/positions/close",
            method="POST", json=body)

    @router.post("/orders/cancel", response_model=Dict[str, Any])
    async def cancel_order(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("trader")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Cancel one working order on an org account."""
        require_account_in_org(conn, ctx.org_id, _required_account_id(body))
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/orders/cancel",
            method="POST", json=body)

    @router.post("/control/close-all", response_model=Dict[str, Any])
    async def close_all(
        body: Dict[str, Any],
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Kill switch: flatten one org account ({"account_id": N}) or this
        org's whole book ({} -- also pauses the org's copying, see the
        copier). Always org-bound; there is no all-orgs flatten."""
        forward: Dict[str, Any] = {"org_id": ctx.org_id}
        if body and body.get("account_id") is not None:
            account_id = _required_account_id(body)
            require_account_in_org(conn, ctx.org_id, account_id)
            forward["account_id"] = account_id
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/close-all",
            method="POST", json=forward,
            timeout=COPIER_SLOW_COMMAND_TIMEOUT_S)

    return router
