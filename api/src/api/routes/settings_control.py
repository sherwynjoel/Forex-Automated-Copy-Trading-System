"""Settings and control proxy endpoints."""
import logging
from typing import Optional, Dict, Any
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import psycopg
from psycopg.types.json import Jsonb

from ..config import ApiConfig
from ..db import get_conn
from ..rbac import OrgContext, require_org_role, require_account_in_org

# Per-request timeout for the two copier commands bounded by BROKER round
# trips rather than by local work: /resync (one ProtoOAReconcileReq per
# account in the org, plus a balance refresh) and /close-all (a reconcile
# plus one close/cancel per open position and working order). The cTrader
# SDK paces its outbound queue at 5 messages/second, so an org with a couple
# of dozen accounts legitimately outruns httpx's 5s default -- and the caller
# then sees "502 copier unreachable" for a command that in fact ran to
# completion. Applied ONLY to those two: the shared AsyncClient stays on the
# default so the dashboard's 5s /state poll still fails fast against a
# half-dead copier (see main.create_app).
logger = logging.getLogger(__name__)

COPIER_SLOW_COMMAND_TIMEOUT_S = 60.0


class SettingsResponse(BaseModel):
    """Response for settings."""
    copying_enabled: bool
    dry_run: bool


class SettingsUpdateRequest(BaseModel):
    """Request body for updating settings."""
    copying_enabled: Optional[bool] = None
    dry_run: Optional[bool] = None


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
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Proxy a request to copier, handling errors appropriately.

    `timeout` overrides the shared client's default for THIS request only;
    leave it None for everything that should keep failing fast (see
    COPIER_SLOW_COMMAND_TIMEOUT_S above). A timeout that does expire is an
    httpx.TimeoutException, itself an httpx.RequestError, so it lands on the
    same 502 path as a copier that is simply not answering.

    Returns the response JSON or raises HTTPException.
    """
    kwargs: Dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    try:
        if method == "GET":
            response = await client.get(url, **kwargs)
        else:
            response = await client.post(url, json=json or {}, **kwargs)

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


def audit(conn: psycopg.Connection, org_id: int, actor: str, action: str,
          detail: Dict[str, Any], account_id: Optional[int] = None) -> None:
    """Write a control-category audit row for something the API did itself.

    Actions the copier performs log themselves; these are the ones it never
    sees -- settings writes and account edits that go straight to Postgres.
    Best-effort: an audit failure must not fail the operation the user asked
    for, but it is logged so the gap is visible.
    """
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, "
            "payload, actor_email) VALUES (%s, %s, 'control', 'info', %s, %s)",
            (org_id, account_id, Jsonb({"action": action, **detail}), actor),
        )
    except Exception:
        logger.exception("failed to write audit event for %s", action)


def create_settings_control_router() -> APIRouter:
    """Create router for settings and control endpoints."""
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["settings", "control"])

    @router.get("/settings", response_model=SettingsResponse)
    async def get_settings(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> SettingsResponse:
        """Get this org's settings."""
        row = conn.execute(
            "SELECT copying_enabled, dry_run FROM orgs WHERE id = %s", (ctx.org_id,)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=500, detail="Settings not found")

        return SettingsResponse(
            copying_enabled=row[0],
            dry_run=row[1],
        )

    @router.put("/settings", response_model=Dict[str, Any])
    async def update_settings(
        request_data: SettingsUpdateRequest,
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Update this org's settings and trigger copier reload on any change."""
        updates = []
        params = []

        if request_data.copying_enabled is not None:
            updates.append("copying_enabled = %s")
            params.append(request_data.copying_enabled)

        if request_data.dry_run is not None:
            updates.append("dry_run = %s")
            params.append(request_data.dry_run)

        before = conn.execute(
            "SELECT copying_enabled, dry_run FROM orgs WHERE id = %s", (ctx.org_id,)
        ).fetchone()

        if updates:
            update_sql = f"UPDATE orgs SET {', '.join(updates)} WHERE id = %s"
            conn.execute(update_sql, params + [ctx.org_id])
            # Copying and dry-run decide whether real money moves; a change
            # to either must be attributable, and the copier never sees the
            # settings write (only the reload that follows it).
            changed = {}
            if request_data.copying_enabled is not None and before and \
                    before[0] != request_data.copying_enabled:
                changed["copying_enabled"] = {
                    "from": before[0], "to": request_data.copying_enabled}
            if request_data.dry_run is not None and before and \
                    before[1] != request_data.dry_run:
                changed["dry_run"] = {"from": before[1], "to": request_data.dry_run}
            if changed:
                audit(conn, ctx.org_id, ctx.user_email, "settings_changed", changed)

        # Get updated settings
        row = conn.execute(
            "SELECT copying_enabled, dry_run FROM orgs WHERE id = %s", (ctx.org_id,)
        ).fetchone()

        result = {
            "copying_enabled": row[0],
            "dry_run": row[1],
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
                            json={"org_id": ctx.org_id, "enabled": request_data.dry_run,
                                  "actor_email": ctx.user_email},
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
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy pause command to copier."""
        if request.account_id is not None:
            require_account_in_org(conn, ctx.org_id, request.account_id)
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/pause"
        return await _proxy_to_copier(
            client,
            url,
            method="POST",
            json={"org_id": ctx.org_id, "account_id": request.account_id,
                  "actor_email": ctx.user_email},
        )

    @router.post("/control/resume", response_model=Dict[str, Any])
    async def control_resume(
        request: ControlRequest,
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy resume command to copier."""
        if request.account_id is not None:
            require_account_in_org(conn, ctx.org_id, request.account_id)
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/resume"
        return await _proxy_to_copier(
            client,
            url,
            method="POST",
            json={"org_id": ctx.org_id, "account_id": request.account_id,
                  "actor_email": ctx.user_email},
        )

    @router.post("/control/resync", response_model=Dict[str, Any])
    async def control_resync(
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy resync command to copier."""
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/resync"
        return await _proxy_to_copier(
            client, url, method="POST",
            json={"org_id": ctx.org_id, "actor_email": ctx.user_email},
            timeout=COPIER_SLOW_COMMAND_TIMEOUT_S)

    return router


def create_state_router() -> APIRouter:
    """Create router for state proxy endpoint."""
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["state"])

    @router.get("/state", response_model=Dict[str, Any])
    async def get_state(
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        """Proxy GET state from copier."""
        client = http_request.app.state.http
        url = f"{cfg.copier_control_url}/state?org_id={ctx.org_id}"
        return await _proxy_to_copier(client, url, method="GET")

    @router.post("/drift/{action}", response_model=Dict[str, Any])
    async def drift_action(
        action: str,
        request: DriftActionRequest,
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
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
            client, url, method="POST",
            json={**request.model_dump(exclude_none=True), "org_id": ctx.org_id,
                  "actor_email": ctx.user_email},
        )

    return router
