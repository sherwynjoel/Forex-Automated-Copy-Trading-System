"""Settings and control proxy endpoints."""
from typing import Optional, Dict, Any
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import psycopg

from ..auth import require_admin
from ..config import ApiConfig
from ..db import get_conn


class SettingsResponse(BaseModel):
    """Response for settings."""
    copying_enabled: bool
    dry_run: bool
    shards: int


class SettingsUpdateRequest(BaseModel):
    """Request body for updating settings."""
    copying_enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    shards: Optional[int] = None


class ControlRequest(BaseModel):
    """Request body for control endpoints."""
    account_id: Optional[int] = None


async def _proxy_to_copier(
    client: httpx.AsyncClient,
    url: str,
    method: str = "POST",
    json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Proxy a request to copier, handling errors appropriately.

    Returns the response JSON or raises HTTPException.
    """
    try:
        if method == "GET":
            response = await client.get(url)
        else:
            response = await client.post(url, json=json or {})

        # Forward non-2xx responses faithfully
        if response.status_code >= 500:
            raise HTTPException(status_code=502, detail="copier unreachable")

        if response.status_code >= 400:
            # Forward 4xx from copier
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text or "copier error"
            raise HTTPException(status_code=response.status_code, detail=detail)

        # Handle successful responses
        try:
            return response.json()
        except Exception:
            # Non-JSON response from copier
            raise HTTPException(
                status_code=502,
                detail="copier returned invalid response"
            )

    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="copier unreachable")


def create_settings_control_router() -> APIRouter:
    """Create router for settings and control endpoints."""
    router = APIRouter(prefix="/api", tags=["settings", "control"])

    @router.get("/settings", response_model=SettingsResponse)
    async def get_settings(
        _: bool = Depends(require_admin),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> SettingsResponse:
        """Get current settings."""
        row = conn.execute(
            "SELECT copying_enabled, dry_run, shards FROM settings WHERE id = TRUE"
        ).fetchone()

        if not row:
            raise HTTPException(status_code=500, detail="Settings not found")

        return SettingsResponse(
            copying_enabled=row[0],
            dry_run=row[1],
            shards=row[2],
        )

    @router.put("/settings", response_model=Dict[str, Any])
    async def update_settings(
        request_data: SettingsUpdateRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Update settings and trigger copier reload on any change."""
        updates = []
        params = []

        if request_data.copying_enabled is not None:
            updates.append("copying_enabled = %s")
            params.append(request_data.copying_enabled)

        if request_data.dry_run is not None:
            updates.append("dry_run = %s")
            params.append(request_data.dry_run)

        if request_data.shards is not None:
            updates.append("shards = %s")
            params.append(request_data.shards)

        if updates:
            update_sql = f"UPDATE settings SET {', '.join(updates)} WHERE id = TRUE"
            conn.execute(update_sql, params)

        # Get updated settings
        row = conn.execute(
            "SELECT copying_enabled, dry_run, shards FROM settings WHERE id = TRUE"
        ).fetchone()

        result = {
            "copying_enabled": row[0],
            "dry_run": row[1],
            "shards": row[2],
        }

        # On ANY settings change, notify copier
        if updates:
            try:
                client = http_request.app.state.http

                # Always call reload when settings change
                await _proxy_to_copier(
                    client,
                    f"{cfg.copier_control_url}/reload",
                    method="POST",
                    json={},
                )
                result["copier_reloaded"] = True

                # Also call dry-run if dry_run setting changed
                if request_data.dry_run is not None:
                    try:
                        await _proxy_to_copier(
                            client,
                            f"{cfg.copier_control_url}/dry-run",
                            method="POST",
                            json={},
                        )
                        result["dry_run_applied"] = True
                    except HTTPException:
                        # dry-run failed; reflect this in response
                        result["dry_run_applied"] = False
            except HTTPException:
                result["copier_reloaded"] = False

        return result

    @router.post("/control/pause", response_model=Dict[str, Any])
    async def control_pause(
        request: ControlRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy pause command to copier."""
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/pause"
        return await _proxy_to_copier(
            client,
            url,
            method="POST",
            json={"account_id": request.account_id},
        )

    @router.post("/control/resume", response_model=Dict[str, Any])
    async def control_resume(
        request: ControlRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy resume command to copier."""
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/resume"
        return await _proxy_to_copier(
            client,
            url,
            method="POST",
            json={"account_id": request.account_id},
        )

    @router.post("/control/resync", response_model=Dict[str, Any])
    async def control_resync(
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy resync command to copier."""
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/resync"
        return await _proxy_to_copier(client, url, method="POST", json={})

    return router


def create_state_router() -> APIRouter:
    """Create router for state proxy endpoint."""
    router = APIRouter(prefix="/api", tags=["state"])

    @router.get("/state", response_model=Dict[str, Any])
    async def get_state(
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy GET state from copier."""
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/state"
        return await _proxy_to_copier(client, url, method="GET")

    @router.post("/drift/{action}", response_model=Dict[str, Any])
    async def drift_action(
        action: str,
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy drift action to copier."""
        # Validate action
        if action not in ("close-orphan", "adopt", "dismiss"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action: {action}. Must be one of: close-orphan, adopt, dismiss"
            )

        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/drift/{action}"
        return await _proxy_to_copier(client, url, method="POST", json={})

    return router
