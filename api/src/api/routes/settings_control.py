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


class DriftActionRequest(BaseModel):
    """Request body for POST /api/drift/{close-orphan,adopt,dismiss}.

    Mirrors what the dashboard sends (dashboard/src/pages/Positions.tsx)
    and what the copier's drift resources require
    (copier/src/copier/engine/control.py): `id` is the DriftItem id for
    every action; `master_position_id` is additionally required by `adopt`
    and ignored by the other two. Validation of the action-specific
    combination stays with the copier, which owns the drift state -- this
    model exists so the body is PARSED AND FORWARDED at all.
    """
    id: str
    master_position_id: Optional[int] = None


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
            # Forward 4xx from copier. Its control endpoint reports
            # failures as {"error": ...} (copier/src/copier/engine/control.py),
            # so read that key too instead of forwarding raw JSON text.
            try:
                payload = response.json()
                detail = payload.get("detail") or payload.get("error") or response.text
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

                # Also call dry-run if dry_run setting changed.
                #
                # The body MUST carry the requested value: the copier's
                # DryRunResource reads `body.get("enabled", False)` and
                # writes that straight back to the settings row
                # (copier/src/copier/engine/control.py ->
                # CopierApp.set_dry_run). Posting `json={}` -- as this did
                # -- therefore made every "turn dry-run ON" request
                # immediately turn it back OFF in the database, while this
                # endpoint reported `dry_run: true, dry_run_applied: true`
                # from a row it had read BEFORE the proxy call. Dry-run
                # could not be enabled through the API at all, the caller
                # was told it had been, and Dispatcher (which re-reads
                # settings per batch) went on sending real orders. That is
                # the Stage-1 rollout gate, so it has to be the real value.
                if request_data.dry_run is not None:
                    try:
                        await _proxy_to_copier(
                            client,
                            f"{cfg.copier_control_url}/dry-run",
                            method="POST",
                            json={"enabled": request_data.dry_run},
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
        request: DriftActionRequest,
        http_request: Request,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy a drift remedy (and its body) to the copier.

        The body was previously dropped entirely (`json={}`), so all three
        one-click remedies -- spec §7's drift remedies and an explicit
        Stage-3 runbook step -- failed on every click: the copier's
        resources raise `ValueError("id required")`, which becomes a 500 and
        is mapped to a 502 here. The dashboard was sending `{id}` /
        `{id, master_position_id}` the whole time.

        `exclude_none` keeps `master_position_id` out of the close-orphan and
        dismiss bodies rather than sending an explicit null the copier would
        just ignore.
        """
        # Validate action
        if action not in ("close-orphan", "adopt", "dismiss"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action: {action}. Must be one of: close-orphan, adopt, dismiss"
            )

        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/drift/{action}"
        return await _proxy_to_copier(
            client, url, method="POST", json=request.model_dump(exclude_none=True),
        )

    return router
