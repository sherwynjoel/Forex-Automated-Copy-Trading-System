"""Trading action proxies: manual orders, position close, order cancel,
and the kill switch.

Deliberately thin: every body is forwarded verbatim to the copier's control
endpoint, which owns ALL trade validation (symbol/volume/side/price rules
live in CopierApp.place_order and friends).  Copier 4xx detail is forwarded
faithfully by _proxy_to_copier so the dashboard shows the real reason.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from ..auth import require_admin
from ..config import ApiConfig
from .settings_control import _proxy_to_copier


def create_trading_router() -> APIRouter:
    """Create router for manual trading actions and the kill switch."""
    router = APIRouter(prefix="/api", tags=["trading"])

    @router.post("/orders", response_model=Dict[str, Any])
    async def place_order(
        body: Dict[str, Any],
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Place a manual order on any connected account."""
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/order", method="POST", json=body)

    @router.post("/positions/close", response_model=Dict[str, Any])
    async def close_position(
        body: Dict[str, Any],
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Close (or partially close) one position on any account."""
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/positions/close",
            method="POST", json=body)

    @router.post("/orders/cancel", response_model=Dict[str, Any])
    async def cancel_order(
        body: Dict[str, Any],
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Cancel one working order on any account."""
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/orders/cancel",
            method="POST", json=body)

    @router.post("/control/close-all", response_model=Dict[str, Any])
    async def close_all(
        body: Dict[str, Any],
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Kill switch: flatten one account ({"account_id": N}) or every
        enabled account ({} -- also pauses copying, see the copier)."""
        client = http_request.app.state.http
        return await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/close-all",
            method="POST", json=body or {})

    return router
