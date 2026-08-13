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


async def _reload_copier(cfg: ApiConfig) -> bool:
    """Call copier POST /reload endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{cfg.copier_control_url}/reload")
            return response.status_code == 200
    except Exception:
        return False


async def _copier_dry_run(cfg: ApiConfig) -> bool:
    """Call copier POST /dry-run endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{cfg.copier_control_url}/dry-run")
            return response.status_code == 200
    except Exception:
        return False


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
        request: SettingsUpdateRequest,
        _: bool = Depends(require_admin),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Update settings and potentially trigger copier actions."""
        updates = []
        params = []

        if request.copying_enabled is not None:
            updates.append("copying_enabled = %s")
            params.append(request.copying_enabled)

        if request.dry_run is not None:
            updates.append("dry_run = %s")
            params.append(request.dry_run)

        if request.shards is not None:
            updates.append("shards = %s")
            params.append(request.shards)

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

        # If dry_run changed, call copier reload and dry-run
        if request.dry_run is not None:
            reload_ok = await _reload_copier(cfg)
            dry_run_ok = await _copier_dry_run(cfg)
            result["copier_reloaded"] = reload_ok and dry_run_ok

        return result

    @router.post("/control/pause", response_model=Dict[str, Any])
    async def control_pause(
        request: ControlRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy pause command to copier."""
        try:
            # Get the async client from app state
            client = http_request.app.state.http
            url = f"{cfg.copier_control_url}/pause"
            response = await client.post(url, json={"account_id": request.account_id})

            if response.status_code >= 500:
                raise HTTPException(
                    status_code=502,
                    detail="copier unreachable"
                )

            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502,
                detail="copier unreachable"
            )

    @router.post("/control/resume", response_model=Dict[str, Any])
    async def control_resume(
        request: ControlRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy resume command to copier."""
        try:
            # Get the async client from app state
            client = http_request.app.state.http
            url = f"{cfg.copier_control_url}/resume"
            response = await client.post(url, json={"account_id": request.account_id})

            if response.status_code >= 500:
                raise HTTPException(
                    status_code=502,
                    detail="copier unreachable"
                )

            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502,
                detail="copier unreachable"
            )

    @router.post("/control/resync", response_model=Dict[str, Any])
    async def control_resync(
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy resync command to copier."""
        try:
            # Get the async client from app state
            client = http_request.app.state.http
            url = f"{cfg.copier_control_url}/resync"
            response = await client.post(url, json={})

            if response.status_code >= 500:
                raise HTTPException(
                    status_code=502,
                    detail="copier unreachable"
                )

            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502,
                detail="copier unreachable"
            )

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
        try:
            # Get the async client from app state
            client = http_request.app.state.http
            url = f"{cfg.copier_control_url}/state"
            response = await client.get(url)

            if response.status_code >= 500:
                raise HTTPException(
                    status_code=502,
                    detail="copier unreachable"
                )

            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502,
                detail="copier unreachable"
            )

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

        try:
            # Get the async client from app state
            client = http_request.app.state.http
            url = f"{cfg.copier_control_url}/drift/{action}"
            response = await client.post(url, json={})

            if response.status_code >= 500:
                raise HTTPException(
                    status_code=502,
                    detail="copier unreachable"
                )

            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502,
                detail="copier unreachable"
            )

    return router
