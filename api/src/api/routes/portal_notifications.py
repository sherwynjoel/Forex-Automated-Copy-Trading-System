# api/src/api/routes/portal_notifications.py
"""In-app notifications, email preferences and per-user appearance (client
portal phase 4). Any member, desk or investor, reads and marks only their
OWN rows: every query is keyed on the caller's user id, and another user's
notification id answers 404 exactly like a missing one."""
from __future__ import annotations

from typing import Any, Dict, Optional

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import require_user
from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

NOTE_COLS = "id, topic, title, body, link, read_at, created_at"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100

THEMES = ("light", "dim", "dark", "system")


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

    # ------------------------------------------------------------ appearance

    @router.get("/me/settings", response_model=Dict[str, Any])
    async def my_settings(user_id: int = Depends(require_user),
                          conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """updated_at is null until the user first saves: the dashboard then
        seeds the account from the browser's own choice instead of resetting it."""
        row = conn.execute("SELECT theme, updated_at FROM user_settings WHERE user_id = %s",
                           (user_id,)).fetchone()
        return {"theme": row[0] if row else "system",
                "updated_at": pc._iso(row[1]) if row else None}

    @router.put("/me/settings", response_model=Dict[str, Any])
    async def save_settings(body: Dict[str, Any] = Body(...),
                            user_id: int = Depends(require_user),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        unknown = sorted(set(body) - {"theme"})
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown field: {unknown[0]}")
        theme = body.get("theme")
        if not isinstance(theme, str) or theme not in THEMES:
            raise HTTPException(status_code=400, detail="theme must be light, dim, dark or system")
        row = conn.execute(
            "INSERT INTO user_settings (user_id, theme) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET theme = EXCLUDED.theme, updated_at = now() "
            "RETURNING theme, updated_at", (user_id, theme)).fetchone()
        return {"theme": row[0], "updated_at": pc._iso(row[1])}

    # ------------------------------------------------------------ email switches

    @router.get("/orgs/{org_id}/notification-prefs", response_model=Dict[str, Any])
    async def my_prefs(ctx: OrgContext = Depends(any_member),
                       conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = conn.execute(
            "SELECT money, identity, support, bonus FROM notification_prefs "
            "WHERE org_id = %s AND user_id = %s", (ctx.org_id, ctx.user_id)).fetchone()
        return dict(zip(pc.TOPICS, map(bool, row))) if row else {t: True for t in pc.TOPICS}

    @router.put("/orgs/{org_id}/notification-prefs", response_model=Dict[str, Any])
    async def save_prefs(body: Dict[str, Any] = Body(...),
                         ctx: OrgContext = Depends(any_member),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """All four switches every time: the dashboard always sends the set."""
        unknown = sorted(set(body) - set(pc.TOPICS))
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown field: {unknown[0]}")
        for topic in pc.TOPICS:
            if not isinstance(body.get(topic), bool):
                raise HTTPException(status_code=400, detail=f"{topic} must be true or false")
        values = [body[t] for t in pc.TOPICS]
        conn.execute(
            "INSERT INTO notification_prefs (org_id, user_id, money, identity, support, bonus) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (org_id, user_id) DO UPDATE SET "
            "money = EXCLUDED.money, identity = EXCLUDED.identity, support = EXCLUDED.support, "
            "bonus = EXCLUDED.bonus, updated_at = now()", (ctx.org_id, ctx.user_id, *values))
        return dict(zip(pc.TOPICS, values))

    return router
