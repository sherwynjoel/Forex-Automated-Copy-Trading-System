# api/src/api/routes/portal_admin.py
"""The client portal, admin side (phase 1).

One router under /api/orgs/{org_id}, every route require_org_role("admin").
Payment methods (where the org receives money) and the portal settings pair
live here from Task 6; the Requests desk (deposits, withdrawals, transfers,
payout destinations), the investors list, ledger reads and adjustments are
added by Tasks 7-10.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

logger = logging.getLogger(__name__)

METHOD_KINDS = ("crypto", "bank")
# The detail keys each kind must carry (400 "<field> is required" when
# missing) and the ones it may carry. Anything else is dropped so the JSON
# column only ever holds what the dashboard renders.
REQUIRED_DETAILS = {"crypto": ("coin", "network", "address"),
                    "bank": ("bank_name", "holder", "account_number", "code")}
OPTIONAL_DETAILS = {"crypto": ("memo",), "bank": ("bank_address", "country")}


class MethodBody(BaseModel):
    kind: str
    label: str
    currency: str = "USD"
    details: dict
    min_amount: Any = "0"
    fee_pct: Any = "0"
    instructions: Optional[str] = None
    sort_order: int = 0


class MethodPatch(BaseModel):
    label: Optional[str] = None
    currency: Optional[str] = None
    details: Optional[dict] = None
    min_amount: Any = None
    fee_pct: Any = None
    instructions: Optional[str] = None   # "" clears it
    sort_order: Optional[int] = None
    enabled: Optional[bool] = None


class SettingsBody(BaseModel):
    withdrawal_min: Any
    withdrawal_fee_pct: Any


def clean_details(kind: str, raw: Any) -> dict:
    """Trimmed details for `kind`: every required key present and non-blank
    (else LedgerError "<field> is required"), optional keys kept when
    given, unknown keys dropped."""
    if not isinstance(raw, dict):
        raise pc.LedgerError(f"{REQUIRED_DETAILS[kind][0]} is required")
    out: dict = {}
    for key in REQUIRED_DETAILS[kind]:
        out[key] = pc.clean_text(raw.get(key), key, max_len=128)
    for key in OPTIONAL_DETAILS[kind]:
        value = pc.clean_text(raw.get(key), key, max_len=256, required=False)
        if value is not None:
            out[key] = value
    return out


def parse_min(raw: object, field: str) -> Decimal:
    """A minimum may be zero (meaning none); above zero it follows
    parse_amount and its messages."""
    if raw is None or raw == "":
        return Decimal("0")
    try:
        if not isinstance(raw, bool) and Decimal(str(raw)) == 0:
            return Decimal("0")
    except (InvalidOperation, ValueError):
        pass
    return pc.parse_amount(raw, field)


def parse_pct(raw: object, field: str) -> Decimal:
    """0 <= pct < 100 with at most three decimals (NUMERIC(6,3))."""
    message = f"{field} must be between 0 and 99.999"
    if isinstance(raw, bool) or raw is None or raw == "":
        raise pc.LedgerError(message)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise pc.LedgerError(message)
    if not value.is_finite() or value < 0 or value >= 100:
        raise pc.LedgerError(message)
    if value != value.quantize(Decimal("0.001")):
        raise pc.LedgerError(message)
    return value


def create_portal_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-admin"])

    def _method_row(conn: psycopg.Connection, org_id: int, method_id: int):
        row = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE id = %s AND org_id = %s",
            (method_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payment method not found")
        return row

    async def _audit_method(conn: psycopg.Connection, ctx: OrgContext, method_id: int,
                            change: str, kind: str, label: str) -> None:
        # A warning, not an info: this is where every investor is told to
        # send money, and an attacker with an admin session would change
        # exactly this. Both alerters are wired to warnings.
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="payment_method_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            method_id=method_id, change=change, kind=kind, label=label,
            summary=f"Payment method {change}: {label} ({kind}) by {ctx.user_email}")

    # ------------------------------------------------------------ payment methods

    @router.get("/payment-methods", response_model=List[Dict[str, Any]])
    async def list_methods(ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=False) for r in rows]

    @router.post("/payment-methods", status_code=201, response_model=Dict[str, Any])
    async def create_method(body: MethodBody,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        kind = body.kind.strip().lower()
        if kind not in METHOD_KINDS:
            raise HTTPException(status_code=400, detail="kind must be crypto or bank")
        try:
            label = pc.clean_text(body.label, "label", max_len=64)
            currency = (pc.clean_text(body.currency, "currency", max_len=8,
                                      required=False) or "USD").upper()
            details = clean_details(kind, body.details)
            min_amount = parse_min(body.min_amount, "min_amount")
            fee_pct = parse_pct(body.fee_pct, "fee_pct")
            instructions = pc.clean_text(body.instructions, "instructions", max_len=1000,
                                         required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = conn.execute(
            "INSERT INTO payment_methods (org_id, kind, label, currency, details, min_amount, "
            "fee_pct, instructions, sort_order, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {pc.METHOD_COLS}",
            (ctx.org_id, kind, label, currency, Jsonb(details), min_amount, fee_pct,
             instructions, body.sort_order, ctx.user_id)).fetchone()
        out = pc.method_json(row, public=False)
        await _audit_method(conn, ctx, out["id"], "created", kind, label)
        return out

    @router.patch("/payment-methods/{method_id}", response_model=Dict[str, Any])
    async def update_method(method_id: int, body: MethodPatch,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _method_row(conn, ctx.org_id, method_id)
        kind = current[1]
        sets: list[str] = []
        params: list[Any] = []
        try:
            if body.label is not None:
                sets.append("label = %s")
                params.append(pc.clean_text(body.label, "label", max_len=64))
            if body.currency is not None:
                sets.append("currency = %s")
                params.append((pc.clean_text(body.currency, "currency", max_len=8,
                                             required=False) or "USD").upper())
            if body.details is not None:
                sets.append("details = %s")
                params.append(Jsonb(clean_details(kind, body.details)))
            if body.min_amount is not None:
                sets.append("min_amount = %s")
                params.append(parse_min(body.min_amount, "min_amount"))
            if body.fee_pct is not None:
                sets.append("fee_pct = %s")
                params.append(parse_pct(body.fee_pct, "fee_pct"))
            if body.instructions is not None:
                sets.append("instructions = %s")
                params.append(pc.clean_text(body.instructions, "instructions", max_len=1000,
                                            required=False))
            if body.sort_order is not None:
                sets.append("sort_order = %s")
                params.append(int(body.sort_order))
            if body.enabled is not None:
                sets.append("enabled = %s")
                params.append(bool(body.enabled))
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not sets:
            return pc.method_json(current, public=False)
        sets.append("updated_at = now()")
        row = conn.execute(
            f"UPDATE payment_methods SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
            f"RETURNING {pc.METHOD_COLS}", (*params, method_id, ctx.org_id)).fetchone()
        out = pc.method_json(row, public=False)
        await _audit_method(conn, ctx, method_id, "updated", kind, out["label"])
        return out

    @router.delete("/payment-methods/{method_id}", status_code=204)
    async def delete_method(method_id: int,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)):
        current = _method_row(conn, ctx.org_id, method_id)
        pending = conn.execute(
            "SELECT 1 FROM deposits WHERE method_id = %s AND status = 'pending'",
            (method_id,)).fetchone()
        if pending:
            # The admin still has to decide that notice against the method's
            # details. Decided rows keep their snapshot (method_kind,
            # method_label) and lose only the id (ON DELETE SET NULL).
            raise HTTPException(status_code=409, detail="a pending deposit still uses this method")
        conn.execute("DELETE FROM payment_methods WHERE id = %s AND org_id = %s",
                     (method_id, ctx.org_id))
        await _audit_method(conn, ctx, method_id, "deleted", current[1], current[2])
        return Response(status_code=204)

    # ------------------------------------------------------------ portal settings

    def _settings_json(settings: dict) -> Dict[str, Any]:
        return {"withdrawal_min": pc.money(settings["withdrawal_min"]),
                "withdrawal_fee_pct": float(settings["withdrawal_fee_pct"])}

    @router.get("/portal-settings", response_model=Dict[str, Any])
    async def get_settings(ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return _settings_json(pc.portal_settings(conn, ctx.org_id))

    @router.put("/portal-settings", response_model=Dict[str, Any])
    async def put_settings(body: SettingsBody,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            withdrawal_min = parse_min(body.withdrawal_min, "withdrawal_min")
            fee_pct = parse_pct(body.withdrawal_fee_pct, "withdrawal_fee_pct")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        previous = _settings_json(pc.portal_settings(conn, ctx.org_id))
        conn.execute(
            "UPDATE portal_settings SET withdrawal_min = %s, withdrawal_fee_pct = %s, "
            "updated_by = %s, updated_at = now() WHERE org_id = %s",
            (withdrawal_min, fee_pct, ctx.user_id, ctx.org_id))
        out = {"withdrawal_min": pc.money(withdrawal_min), "withdrawal_fee_pct": float(fee_pct)}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="portal_settings_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, previous=previous, **out,
            summary=f"Withdrawal rules set to min {withdrawal_min:.2f} USD, fee {fee_pct}% "
                    f"by {ctx.user_email}")
        return out

    return router
