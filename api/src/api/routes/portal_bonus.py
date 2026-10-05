# api/src/api/routes/portal_bonus.py
"""Bonuses (client portal phase 4): the org's bonus rules (admin), manual
grants and claw-backs (admin MPIN), and the investor's own bonus history.
Every bonus lands in the Credit wallet through pc.pay_bonus; credit only
ever moves on to a trading account (portal_ledger.TRANSFER_PAIRS)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc
from .portal_admin import _require_investor, parse_min, parse_signed_amount
from .portal_investor import _parse_day


class RulesBody(BaseModel):
    # Any, validated by parse_rules so every field gets its own message.
    signup_enabled: Any = None
    signup_amount: Any = None
    kyc_enabled: Any = None
    kyc_amount: Any = None
    deposit_enabled: Any = None
    deposit_pct: Any = None
    deposit_cap: Any = None


def rules_json(rules: dict) -> Dict[str, Any]:
    return {"signup_enabled": rules["signup_enabled"],
            "signup_amount": pc.money(rules["signup_amount"]),
            "kyc_enabled": rules["kyc_enabled"], "kyc_amount": pc.money(rules["kyc_amount"]),
            "deposit_enabled": rules["deposit_enabled"],
            "deposit_pct": float(rules["deposit_pct"]),
            "deposit_cap": pc.money(rules["deposit_cap"]),
            "updated_at": pc._iso(rules["updated_at"])}


def parse_deposit_pct(raw: object) -> Decimal:
    """0 <= pct <= 100 with at most three decimals (NUMERIC(6,3)); blank is 0."""
    message = "deposit_pct must be between 0 and 100"
    if raw is None or raw == "":
        return Decimal("0")
    if isinstance(raw, bool):
        raise pc.LedgerError(message)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise pc.LedgerError(message)
    if not value.is_finite() or value < 0 or value > 100:
        raise pc.LedgerError(message)
    if value != value.quantize(Decimal("0.001")):
        raise pc.LedgerError("deposit_pct may have at most three decimals")
    return value


def parse_rules(body: RulesBody) -> dict:
    """The PUT body as column values; LedgerError names the first bad field.
    A rule switched on must pay something."""
    out: dict = {}
    for flag in ("signup_enabled", "kyc_enabled", "deposit_enabled"):
        value = getattr(body, flag)
        if not isinstance(value, bool):
            raise pc.LedgerError(f"{flag} must be true or false")
        out[flag] = value
    for field in ("signup_amount", "kyc_amount"):
        out[field] = parse_min(getattr(body, field), field)
    out["deposit_pct"] = parse_deposit_pct(body.deposit_pct)
    cap = body.deposit_cap
    out["deposit_cap"] = None if cap is None or cap == "" else pc.parse_amount(cap, "deposit_cap")
    for source, field in (("signup", "signup_amount"), ("kyc", "kyc_amount"),
                          ("deposit", "deposit_pct")):
        if out[f"{source}_enabled"] and out[field] == 0:
            raise pc.LedgerError(f"{field} must be above 0 while the {source} rule is on")
    return out


class GrantBody(BaseModel):
    amount: Any = None
    note: Any = None
    mpin: Any = None


BONUS_COLS = "id, source, source_id, amount, note, created_at"


def bonus_json(row) -> Dict[str, Any]:
    bonus_id, source, source_id, amount, note, created_at = row
    return {"id": bonus_id, "source": source,
            "source_id": int(source_id) if source_id is not None else None,
            "amount": pc.money(amount), "note": note, "created_at": pc._iso(created_at),
            "currency": pc.CURRENCY}


def create_portal_bonus_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-bonus"])

    @router.get("/bonus-rules", response_model=Dict[str, Any])
    async def get_rules(ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return rules_json(pc.bonus_rules(conn, ctx.org_id))

    @router.put("/bonus-rules", response_model=Dict[str, Any])
    async def put_rules(body: RulesBody, ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Applies to events from now on; nothing already done is paid."""
        try:
            rules = parse_rules(body)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        previous = rules_json(pc.bonus_rules(conn, ctx.org_id))
        conn.execute(
            "UPDATE bonus_rules SET signup_enabled = %(signup_enabled)s, "
            "signup_amount = %(signup_amount)s, kyc_enabled = %(kyc_enabled)s, "
            "kyc_amount = %(kyc_amount)s, deposit_enabled = %(deposit_enabled)s, "
            "deposit_pct = %(deposit_pct)s, deposit_cap = %(deposit_cap)s, "
            "updated_by = %(updated_by)s, updated_at = now() WHERE org_id = %(org_id)s",
            {**rules, "updated_by": ctx.user_id, "org_id": ctx.org_id})
        out = rules_json(pc.bonus_rules(conn, ctx.org_id))
        changes = {k: v for k, v in out.items() if k != "updated_at"}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="bonus_rules_changed", actor_email=ctx.user_email,
            user_id=ctx.user_id, previous={k: v for k, v in previous.items() if k != "updated_at"},
            **changes, summary=f"Bonus rules changed by {ctx.user_email}")
        return out

    @router.post("/investors/{user_id}/bonuses", status_code=201, response_model=Dict[str, Any])
    async def grant_bonus(user_id: int, body: GrantBody, http_request: Request,
                          ctx: OrgContext = Depends(require_org_role("admin")),
                          conn: psycopg.Connection = Depends(get_conn)):
        """A bonus (positive) or claw-back (negative) on the Credit wallet,
        confirmed with the ADMIN's own MPIN like a ledger adjustment. A
        claw-back may not take the Credit wallet's available figure below
        zero (credit on hold for an open transfer is already spoken for)."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        _require_investor(conn, ctx.org_id, user_id)
        amount = parse_signed_amount(body.amount)
        try:
            note = pc.clean_text(body.note, "note", max_len=500)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            if amount < 0:
                available = pc.wallet_figures(conn, ctx.org_id, user_id)["credit"]["available"]
                if available + amount < 0:
                    raise HTTPException(status_code=400, detail=(
                        "a claw-back cannot take the Credit wallet below zero "
                        f"(available {available:.2f})"))
            bonus_id = pc.pay_bonus(conn, ctx.org_id, user_id, "manual", amount, note=note,
                                    created_by=ctx.user_id)
            row = conn.execute(f"SELECT {BONUS_COLS} FROM bonuses WHERE id = %s",
                               (bonus_id,)).fetchone()
        await pc.announce_bonus(conn, http_request, org_id=ctx.org_id, user_id=user_id,
                                bonus_id=bonus_id, source="manual", amount=amount,
                                actor_email=ctx.user_email, note=note)
        return bonus_json(row)

    @router.get("/investor/bonuses", response_model=List[Dict[str, Any]])
    async def my_bonuses(source: Optional[str] = None,
                         date_from: Optional[str] = Query(None, alias="from"),
                         date_to: Optional[str] = Query(None, alias="to"),
                         ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        if source is not None and source not in pc.BONUS_SOURCES:
            raise HTTPException(status_code=400,
                                detail="source must be one of signup, kyc, deposit, manual")
        try:
            start = _parse_day(date_from, "from")
            end = _parse_day(date_to, "to")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        where = ["org_id = %s", "user_id = %s"]
        params: List[Any] = [ctx.org_id, ctx.user_id]
        if source is not None:
            where.append("source = %s")
            params.append(source)
        if start is not None:
            where.append("created_at >= %s::date")
            params.append(start)
        if end is not None:
            where.append("created_at < %s::date + interval '1 day'")
            params.append(end)
        # ponytail: LIMIT 500, page when an investor has more bonuses than that
        rows = conn.execute(f"SELECT {BONUS_COLS} FROM bonuses WHERE {' AND '.join(where)} "
                            "ORDER BY id DESC LIMIT 500", params).fetchall()
        return [bonus_json(r) for r in rows]

    return router
