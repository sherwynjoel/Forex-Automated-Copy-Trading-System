# api/src/api/routes/portal_investor.py
"""The client portal, investor side (phase 1).

One router under /api/orgs/{org_id}. Every route resolves the caller's OWN
rows through ctx.user_id and never takes a user id from the request, so an
investor cannot name anyone else's money. Grown over Tasks 6-10 of the
phase-1 plan: payment methods here; deposits, payout destinations,
withdrawals, transfers, wallet entries and the summary follow.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import LoginRateLimiter
from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc
from .portal_files import file_belongs

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 10
RATE_LIMITED = "too many requests; try again later"


class DepositNotice(BaseModel):
    method_id: int
    amount: Any
    reference: str
    receipt_file_id: Optional[int] = None
    target: str = "wallet"
    target_account_id: Optional[int] = None
    note: Optional[str] = None


def create_portal_investor_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-investor"])
    # Ten money requests of each kind per investor per hour, keyed
    # portal-<kind>:<org>:<user>. A separate instance because the shared
    # login limiter's window is one minute.
    hourly = LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)

    @router.get("/investor/payment-methods", response_model=List[Dict[str, Any]])
    async def my_payment_methods(ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        """Where this workspace receives money: enabled methods only, in
        the admin's display order."""
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s AND enabled "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=True) for r in rows]

    # ------------------------------------------------------------ deposits

    @router.get("/investor/deposits", response_model=List[Dict[str, Any]])
    async def my_deposits(ctx: OrgContext = Depends(require_org_role("investor")),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.DEPOSIT_COLS} FROM deposits WHERE org_id = %s AND user_id = %s "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [pc.deposit_json(r) for r in rows]

    @router.post("/investor/deposits", status_code=201, response_model=Dict[str, Any])
    async def file_deposit(body: DepositNotice,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """'I have sent it': a notice against one enabled payment method.
        The method's kind and label are snapshotted and its fee_pct applied
        now, so a later edit of the method never rewrites history. Nothing
        moves until an admin confirms."""
        try:
            amount = pc.parse_amount(body.amount)
            reference = pc.clean_text(body.reference, "reference")
            note = pc.clean_text(body.note, "note", max_len=500, required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        target = (body.target or "wallet").strip().lower()
        if target not in ("wallet", "account"):
            raise HTTPException(status_code=400, detail="target must be wallet or account")
        method = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods "
            "WHERE id = %s AND org_id = %s AND enabled", (body.method_id, ctx.org_id)).fetchone()
        if not method:
            raise HTTPException(status_code=404, detail="Payment method not found")
        (method_id, kind, label, _enabled, _currency, _details, min_amount, fee_pct,
         _instructions, _sort_order) = method
        min_amount = Decimal(min_amount)
        if amount < min_amount:
            raise HTTPException(status_code=400,
                                detail=f"minimum deposit for this method is {min_amount:.2f}")
        if kind == "bank" and body.receipt_file_id is None:
            raise HTTPException(status_code=400, detail="receipt is required for bank deposits")
        if body.receipt_file_id is not None:
            # The specific "already attached" refusal has to be checked
            # BEFORE the generic file_belongs gate: file_belongs itself
            # excludes any file already referenced by a deposit (spec
            # section 9, enforced there for every other caller too), so
            # checking it first would only ever surface the generic
            # "not found" message and this one would be unreachable.
            used = conn.execute("SELECT 1 FROM deposits WHERE receipt_file_id = %s",
                                (body.receipt_file_id,)).fetchone()
            if used:
                raise HTTPException(
                    status_code=400, detail="receipt file is already attached to another notice")
            if not file_belongs(conn, ctx.org_id, ctx.user_id, body.receipt_file_id,
                                "deposit_receipt"):
                raise HTTPException(status_code=400, detail="receipt file not found")
        target_account_id: Optional[int] = None
        if target == "account":
            linked = pc.linked_account(conn, ctx.org_id, ctx.user_id)
            if linked is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            if body.target_account_id is not None and body.target_account_id != linked:
                raise HTTPException(status_code=404, detail="Account not found")
            target_account_id = linked
        if hourly.is_limited(f"portal-deposit:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        fee = pc.fee_for(amount, Decimal(fee_pct))
        try:
            row = conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, "
                "amount, fee, reference, receipt_file_id, target, target_account_id, note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {pc.DEPOSIT_COLS}",
                (ctx.org_id, ctx.user_id, method_id, kind, label, amount, fee, reference,
                 body.receipt_file_id, target, target_account_id, note)).fetchone()
        except psycopg.errors.UniqueViolation:
            # deposits_one_live_reference: the same transaction is already
            # pending or confirmed in this workspace (possibly another
            # investor's). Two rows for one transfer is the same money
            # counted twice. Rejected and cancelled rows are not in the
            # index, so a re-file after a mistake still works.
            raise HTTPException(status_code=409,
                                detail="A notice with this reference already exists")
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_noticed",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            account_id=target_account_id, deposit_id=out["id"], amount=out["amount"],
            fee=out["fee"], method_id=method_id, method_label=label, reference=reference,
            target=target,
            summary=f"Deposit notice: {amount:.2f} USD via {label} from {ctx.user_email}")
        return out

    @router.post("/investor/deposits/{deposit_id}/cancel", response_model=Dict[str, Any])
    async def cancel_deposit(deposit_id: int,
                             ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """The investor's own move, allowed only while pending. The row is
        kept as cancelled so the history stays whole; the reference is
        free again."""
        current = conn.execute(
            "SELECT status FROM deposits WHERE id = %s AND org_id = %s AND user_id = %s",
            (deposit_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        if not pc.can_transition("deposits", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"deposit is already {current[0]}")
        row = conn.execute(
            "UPDATE deposits SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = 'pending' "
            f"RETURNING {pc.DEPOSIT_COLS}", (ctx.user_id, deposit_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            account_id=out["target_account_id"], deposit_id=deposit_id, amount=out["amount"],
            reference=out["reference"],
            summary=f"Deposit notice #{deposit_id} cancelled by {ctx.user_email}")
        return out

    return router
