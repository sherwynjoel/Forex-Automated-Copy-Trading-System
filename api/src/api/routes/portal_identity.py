# api/src/api/routes/portal_identity.py
"""The client portal, phase 2: identity. One router under
/api/orgs/{org_id} with both sides of three features -- the KYC profile
(investor saves and submits, admin decides), account packages (admin
defines, investor lists) and live account requests (investor requests,
admin reveals the passwords once per need, fulfils or rejects).

Investor routes resolve the caller's OWN rows through ctx.user_id and
never take a user id from the request. Rules and serialisers live in
api/portal_identity.py (imported as pid)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc
from .. import portal_identity as pid
from .portal_files import file_belongs

logger = logging.getLogger(__name__)


class MpinBody(BaseModel):
    mpin: Any = None


def _profile_row(conn: psycopg.Connection, org_id: int, user_id: int):
    return conn.execute(
        f"SELECT {pid.PROFILE_COLS} FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchone()


def create_portal_identity_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-identity"])

    # ------------------------------------------------------------ KYC, investor

    @router.get("/investor/profile", response_model=Dict[str, Any])
    async def my_profile(ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = _profile_row(conn, ctx.org_id, ctx.user_id)
        return pid.profile_json(row) if row else pid.empty_profile(ctx.user_id)

    @router.put("/investor/profile", response_model=Dict[str, Any])
    async def save_profile(body: Dict[str, Any] = Body(...),
                           ctx: OrgContext = Depends(require_investor),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Save any subset of the profile fields (the dashboard saves one
        step at a time). Locked while submitted. An approved profile stays
        approved through contact edits; anything else sends it back to
        draft and clears the approval."""
        unknown = sorted(set(body) - set(pid.PROFILE_FIELDS))
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown field: {unknown[0]}")
        try:
            changes = {key: pid.clean_profile_field(key, value) for key, value in body.items()}
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = _profile_row(conn, ctx.org_id, ctx.user_id)
        before = pid.profile_json(row) if row else pid.empty_profile(ctx.user_id)
        if before["status"] == "submitted":
            raise HTTPException(status_code=409, detail="your profile is under review")
        if not changes:
            return before
        slots_after = {slot: changes.get(slot, before[slot]) for slot in pid.FILE_SLOTS}
        used = [v for v in slots_after.values() if v is not None]
        if len(used) != len(set(used)):
            raise HTTPException(status_code=400, detail="each document needs its own file")
        for slot, purpose in pid.FILE_SLOTS.items():
            new = changes.get(slot)
            if new is not None and new != before[slot] and not file_belongs(
                    conn, ctx.org_id, ctx.user_id, new, purpose):
                raise HTTPException(status_code=400,
                                    detail=f"{pid.FILE_LABELS[slot]} file not found")
        reverify = before["status"] == "approved" and pid.needs_reverification(before, changes)
        status = "draft" if reverify else before["status"]
        cols = list(changes)
        sets = [f"{c} = EXCLUDED.{c}" for c in cols] + ["status = %s", "updated_at = now()"]
        if reverify:
            sets += ["decided_by = NULL", "decided_at = NULL", "decision_note = NULL"]
        # The status read above guards the write: a submit that landed in
        # between makes this a no-op, reported as a conflict.
        row = conn.execute(
            f"INSERT INTO kyc_profiles (org_id, user_id, {', '.join(cols)}) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (org_id, user_id) DO UPDATE SET {', '.join(sets)} "
            "WHERE kyc_profiles.status = %s "
            f"RETURNING {pid.PROFILE_COLS}",
            (ctx.org_id, ctx.user_id, *changes.values(), status, before["status"])).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="your profile changed; reload it")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_profile_saved",
            actor_email=ctx.user_email, user_id=ctx.user_id, fields=sorted(cols),
            reverify=reverify)
        return pid.profile_json(row)

    @router.post("/investor/profile/submit", response_model=Dict[str, Any])
    async def submit_profile(body: MpinBody,
                             ctx: OrgContext = Depends(require_investor),
                             conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        row = _profile_row(conn, ctx.org_id, ctx.user_id)
        profile = pid.profile_json(row) if row else pid.empty_profile(ctx.user_id)
        if profile["status"] not in ("draft", "rejected"):
            raise HTTPException(status_code=409,
                                detail=f"your profile is already {profile['status']}")
        if profile["missing"]:
            return JSONResponse(status_code=400, content={
                "detail": "complete your profile first: " + ", ".join(profile["missing"]),
                "missing": profile["missing"]})
        # updated_at (the last of PROFILE_COLS) pins the row we checked for
        # completeness: a save landing in between makes this a 409, not an
        # incomplete submission.
        row = conn.execute(
            "UPDATE kyc_profiles SET status = 'submitted', submitted_at = now(), "
            "decided_by = NULL, decided_at = NULL, decision_note = NULL, updated_at = now() "
            "WHERE org_id = %s AND user_id = %s AND status = %s AND updated_at = %s "
            f"RETURNING {pid.PROFILE_COLS}",
            (ctx.org_id, ctx.user_id, profile["status"], row[-1])).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="your profile changed; reload it")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_kyc_submitted",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            summary=f"Identity verification submitted by {ctx.user_email}")
        return pid.profile_json(row)

    return router
