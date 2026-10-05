# api/src/api/routes/portal_support.py
"""Support tickets (client portal phase 4): the subjects an admin offers,
threads between an investor and the desk with up to three images per
message, statuses new -> open -> closed, and the desk's queue. Investor
routes resolve the caller's OWN tickets through ctx.user_id; message text
never goes into the audit trail."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..db import get_conn
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc

logger = logging.getLogger(__name__)

SUBJECT_COLS = "id, label, enabled, sort, created_at"


class SubjectBody(BaseModel):
    label: Any = None
    enabled: bool = True
    sort: int = 0


class SubjectPatch(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None
    sort: Optional[int] = None


def subject_json(row) -> Dict[str, Any]:
    subject_id, label, enabled, sort, created_at = row
    return {"id": subject_id, "label": label, "enabled": bool(enabled), "sort": sort,
            "created_at": pc._iso(created_at)}


def create_portal_support_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-support"])

    def _subject_row(conn: psycopg.Connection, org_id: int, subject_id: int):
        row = conn.execute(
            f"SELECT {SUBJECT_COLS} FROM ticket_subjects WHERE id = %s AND org_id = %s",
            (subject_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Subject not found")
        return row

    async def _audit_subject(conn: psycopg.Connection, ctx: OrgContext, subject_id: int,
                             change: str, label: str) -> None:
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_subject_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, subject_id=subject_id,
            change=change, label=label,
            summary=f"Ticket subject {change}: {label} by {ctx.user_email}")

    # ------------------------------------------------------------ subjects, admin

    @router.get("/ticket-subjects", response_model=List[Dict[str, Any]])
    async def list_subjects(ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(f"SELECT {SUBJECT_COLS} FROM ticket_subjects WHERE org_id = %s "
                            "ORDER BY sort, id", (ctx.org_id,)).fetchall()
        return [subject_json(r) for r in rows]

    @router.post("/ticket-subjects", status_code=201, response_model=Dict[str, Any])
    async def create_subject(body: SubjectBody,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            label = pc.clean_text(body.label, "label", max_len=80)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            row = conn.execute(
                "INSERT INTO ticket_subjects (org_id, label, enabled, sort) "
                f"VALUES (%s, %s, %s, %s) RETURNING {SUBJECT_COLS}",
                (ctx.org_id, label, body.enabled, body.sort)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="a subject with this label already exists")
        await _audit_subject(conn, ctx, row[0], "created", label)
        return subject_json(row)

    @router.patch("/ticket-subjects/{subject_id}", response_model=Dict[str, Any])
    async def update_subject(subject_id: int, body: SubjectPatch,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _subject_row(conn, ctx.org_id, subject_id)
        sets: list[str] = []
        params: list[Any] = []
        if body.label is not None:
            try:
                params.append(pc.clean_text(body.label, "label", max_len=80))
            except pc.LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            sets.append("label = %s")
        if body.enabled is not None:
            sets.append("enabled = %s")
            params.append(body.enabled)
        if body.sort is not None:
            sets.append("sort = %s")
            params.append(body.sort)
        if not sets:
            return subject_json(current)
        try:
            row = conn.execute(
                f"UPDATE ticket_subjects SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
                f"RETURNING {SUBJECT_COLS}", (*params, subject_id, ctx.org_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="a subject with this label already exists")
        await _audit_subject(conn, ctx, subject_id, "updated", row[1])
        return subject_json(row)

    @router.delete("/ticket-subjects/{subject_id}", status_code=204)
    async def delete_subject(subject_id: int,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)):
        """Old tickets keep subject_label (the FK is ON DELETE SET NULL)."""
        current = _subject_row(conn, ctx.org_id, subject_id)
        conn.execute("DELETE FROM ticket_subjects WHERE id = %s AND org_id = %s",
                     (subject_id, ctx.org_id))
        await _audit_subject(conn, ctx, subject_id, "deleted", current[1])
        return Response(status_code=204)

    # ------------------------------------------------------------ subjects, investor

    @router.get("/investor/ticket-subjects", response_model=List[Dict[str, Any]])
    async def my_subjects(ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        """What the Raise ticket dialog offers: enabled subjects only."""
        rows = conn.execute(f"SELECT {SUBJECT_COLS} FROM ticket_subjects WHERE org_id = %s "
                            "AND enabled ORDER BY sort, id", (ctx.org_id,)).fetchall()
        return [subject_json(r) for r in rows]

    return router
