# api/src/api/routes/portal_notifications.py
"""In-app notifications, email preferences and per-user appearance (client
portal phase 4). Any member, desk or investor, reads and marks only their
OWN rows: every query is keyed on the caller's user id, and another user's
notification id answers 404 exactly like a missing one."""
from __future__ import annotations

from typing import Any, Dict, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

NOTE_COLS = "id, topic, title, body, link, read_at, created_at"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def note_json(row) -> Dict[str, Any]:
    note_id, topic, title, body, link, read_at, created_at = row
    return {"id": note_id, "topic": topic, "title": title, "body": body, "link": link,
            "read_at": pc._iso(read_at), "created_at": pc._iso(created_at)}


def create_portal_notifications_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["portal-notifications"])
    # The lowest rank: every member of the org passes.
    any_member = require_org_role("investor")

    @router.get("/orgs/{org_id}/notifications", response_model=Dict[str, Any])
    async def my_notifications(before: Optional[int] = None, limit: int = DEFAULT_LIMIT,
                               ctx: OrgContext = Depends(any_member),
                               conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Newest first, keyed by id (ids are monotonic, as in the wallet
        entries page); `before` is the last id of the previous page."""
        size = max(1, min(int(limit), MAX_LIMIT))
        where = "org_id = %s AND user_id = %s"
        params: list = [ctx.org_id, ctx.user_id]
        if before is not None:
            where += " AND id < %s"
            params.append(before)
        rows = conn.execute(
            f"SELECT {NOTE_COLS} FROM notifications WHERE {where} ORDER BY id DESC LIMIT %s",
            (*params, size + 1)).fetchall()
        has_more = len(rows) > size
        notes = [note_json(r) for r in rows[:size]]
        return {"notifications": notes, "has_more": has_more,
                "next_before": notes[-1]["id"] if has_more and notes else None}

    @router.get("/orgs/{org_id}/notifications/unread-count", response_model=Dict[str, Any])
    async def unread_count(ctx: OrgContext = Depends(any_member),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        (count,) = conn.execute(
            "SELECT count(*) FROM notifications WHERE org_id = %s AND user_id = %s "
            "AND read_at IS NULL", (ctx.org_id, ctx.user_id)).fetchone()
        return {"count": int(count)}

    @router.post("/orgs/{org_id}/notifications/read-all", response_model=Dict[str, Any])
    async def read_all(ctx: OrgContext = Depends(any_member),
                       conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        cursor = conn.execute(
            "UPDATE notifications SET read_at = now() WHERE org_id = %s AND user_id = %s "
            "AND read_at IS NULL", (ctx.org_id, ctx.user_id))
        return {"updated": cursor.rowcount}

    @router.post("/orgs/{org_id}/notifications/{note_id}/read", response_model=Dict[str, Any])
    async def read_one(note_id: int, ctx: OrgContext = Depends(any_member),
                       conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = conn.execute(
            "UPDATE notifications SET read_at = COALESCE(read_at, now()) "
            "WHERE id = %s AND org_id = %s AND user_id = %s "
            f"RETURNING {NOTE_COLS}", (note_id, ctx.org_id, ctx.user_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        return note_json(row)

    return router
