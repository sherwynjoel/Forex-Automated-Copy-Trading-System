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
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..db import get_conn
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc
from ..auth import LoginRateLimiter
from .portal_files import file_belongs
from .portal_investor import RATE_LIMITED

logger = logging.getLogger(__name__)

SUBJECT_COLS = "id, label, enabled, sort, created_at"

TICKETS_PER_HOUR = 10
# Each investor reply emails every admin: a looser limit than new tickets,
# but a limit (keyed portal-ticket-reply:<org>:<user>).
REPLIES_PER_HOUR = 60
MAX_IMAGES = 3
STATUSES = ("new", "open", "closed")
TICKET_COLS = ("id, user_id, subject_id, subject_label, status, created_at, updated_at, "
               "last_message_at, closed_at, closed_by")
MESSAGE_COLS = "id, author_id, from_desk, body, file_ids, created_at"
# Which side spoke last, for the desk's "waiting on us" flag. Correlated on
# the ticket aliased t.
LAST_FROM_DESK = ("(SELECT m.from_desk FROM ticket_messages m WHERE m.ticket_id = t.id "
                  "ORDER BY m.id DESC LIMIT 1)")
# The ONE "waiting on the desk" rule: not closed, and the investor spoke
# last. Every Ticket's waiting_on_desk flag and requests/summary's tickets
# count read it; the dashboard only reads the flag.
WAITING_ON_DESK = f"(t.status <> 'closed' AND NOT COALESCE({LAST_FROM_DESK}, false))"
# Every ticket read selects these, in ticket_json's order.
TICKET_SELECT = f"{pc.qualify(TICKET_COLS, 't')}, {LAST_FROM_DESK}, {WAITING_ON_DESK}"


class TicketBody(BaseModel):
    # Any, checked by hand: a typed field would 422 before our messages.
    subject_id: Any = None
    body: Any = None
    file_ids: Any = None


class MessageBody(BaseModel):
    body: Any = None
    file_ids: Any = None


class DeskReply(BaseModel):
    # Text only: the upload route is the investor's (spec decisions).
    body: Any = None


def ticket_json(row, viewer_id: Optional[int] = None) -> Dict[str, Any]:
    """A TICKET_SELECT row, optionally followed by the investor's email and
    display_name. `viewer_id` narrows an investor's own view: closed_by is
    hidden (set to None) unless it names the viewer themselves, so a desk
    close never names the desk user to the investor. Omitted (the desk's
    own queue and thread) keeps the real identity."""
    (ticket_id, user_id, subject_id, subject_label, status, created_at, updated_at,
     last_message_at, closed_at, closed_by, last_from_desk, waiting_on_desk) = row[:12]
    if viewer_id is not None and closed_by not in (None, viewer_id):
        closed_by = None
    out = {"id": ticket_id, "user_id": user_id, "subject_id": subject_id,
           "subject_label": subject_label, "status": status, "created_at": pc._iso(created_at),
           "updated_at": pc._iso(updated_at), "last_message_at": pc._iso(last_message_at),
           "closed_at": pc._iso(closed_at), "closed_by": closed_by,
           "last_from_desk": bool(last_from_desk), "waiting_on_desk": bool(waiting_on_desk)}
    if len(row) > 12:
        out["email"], out["display_name"] = row[12], row[13]
    return out


def message_json(row) -> Dict[str, Any]:
    message_id, author_id, from_desk, body, file_ids, created_at, author_name = row
    return {"id": message_id, "author_id": author_id, "author_name": author_name,
            "from_desk": bool(from_desk), "body": body,
            "file_ids": [int(f) for f in file_ids or []], "created_at": pc._iso(created_at)}


def clean_attachments(conn: psycopg.Connection, org_id: int, user_id: int,
                      raw: object) -> List[int]:
    """The message's images: a list of at most three distinct ids, each the
    investor's own ticket_attachment upload not attached anywhere yet.
    Raises LedgerError with the message the route returns as a 400."""
    if raw is None:
        return []
    if not isinstance(raw, list) or any(isinstance(f, bool) or not isinstance(f, int) for f in raw):
        raise pc.LedgerError("file_ids must be a list of file ids")
    if len(raw) > MAX_IMAGES:
        raise pc.LedgerError("at most 3 images per message")
    if len(set(raw)) != len(raw):
        raise pc.LedgerError("each image may be attached once")
    for file_id in raw:
        if not file_belongs(conn, org_id, user_id, file_id, "ticket_attachment"):
            raise pc.LedgerError("image not found")
    # ponytail: checked then inserted without a lock -- two messages sending
    # the same new file at the same instant could both pass.
    return list(raw)


def search_clause(q: Optional[str], status: Optional[str]) -> tuple[str, list]:
    """Extra WHERE text (on tickets aliased t) for ?status= and ?q=; q matches
    the subject or any message, case-insensitively, with % and _ literal."""
    where, params = "", []
    if status is not None:
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail="status must be new, open or closed")
        where += " AND t.status = %s"
        params.append(status)
    text = (q or "").strip()
    if text:
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += (" AND (t.subject_label ILIKE %s OR EXISTS (SELECT 1 FROM ticket_messages s "
                  "WHERE s.ticket_id = t.id AND s.body ILIKE %s))")
        params += [like, like]
    return where, params


def load_thread(conn: psycopg.Connection, org_id: int, ticket_id: int,
                user_id: Optional[int] = None) -> Dict[str, Any]:
    """The ticket with its investor's email/name and every message, oldest
    first. `user_id` narrows to the owner (investor routes): another
    investor's ticket is the same 404 as a missing one, and the investor's
    view never names desk staff -- desk messages carry no author_id or
    author_name (the page shows "Support desk") and a desk close no
    closed_by."""
    sql = (f"SELECT {TICKET_SELECT}, u.email, u.display_name "
           "FROM tickets t JOIN users u ON u.id = t.user_id WHERE t.id = %s AND t.org_id = %s")
    params: list = [ticket_id, org_id]
    if user_id is not None:
        sql += " AND t.user_id = %s"
        params.append(user_id)
    row = conn.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    messages = conn.execute(
        f"SELECT {pc.qualify(MESSAGE_COLS, 'm')}, a.display_name FROM ticket_messages m "
        "LEFT JOIN users a ON a.id = m.author_id WHERE m.ticket_id = %s ORDER BY m.id",
        (ticket_id,)).fetchall()
    thread = {**ticket_json(row, user_id), "messages": [message_json(m) for m in messages]}
    if user_id is not None:
        for message in thread["messages"]:
            if message["from_desk"]:
                message["author_id"] = message["author_name"] = None
    return thread


def add_message(conn: psycopg.Connection, org_id: int, ticket_id: int, author_id: int,
                from_desk: bool, body: str, file_ids: List[int]) -> None:
    conn.execute(
        "INSERT INTO ticket_messages (ticket_id, org_id, author_id, from_desk, body, file_ids) "
        "VALUES (%s, %s, %s, %s, %s, %s::bigint[])",
        (ticket_id, org_id, author_id, from_desk, body, file_ids))


def desk_link(org_id: int, ticket_id: int) -> str:
    return f"/org/{org_id}/requests?tab=support&ticket={ticket_id}"


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

    # Ten new tickets per investor per hour, keyed portal-ticket:<org>:<user>;
    # its own instance, as each portal router keeps one. Replies share the
    # instance under their own key with REPLIES_PER_HOUR.
    hourly = LoginRateLimiter(max_attempts=TICKETS_PER_HOUR, window_s=3600)

    async def _close(conn: psycopg.Connection, ctx: OrgContext, ticket_id: int, *,
                     owner: Optional[int]) -> tuple[int, str]:
        """Close a ticket (the owner's own, or any of the org's for the desk);
        audits ticket_closed and returns (investor id, subject label)."""
        sql = "SELECT status, user_id, subject_label FROM tickets WHERE id = %s AND org_id = %s"
        params: list = [ticket_id, ctx.org_id]
        if owner is not None:
            sql += " AND user_id = %s"
            params.append(owner)
        current = conn.execute(sql, params).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Ticket not found")
        row = conn.execute(
            "UPDATE tickets SET status = 'closed', closed_at = now(), closed_by = %s, "
            "updated_at = now() WHERE id = %s AND org_id = %s AND status <> 'closed' RETURNING id",
            (ctx.user_id, ticket_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="ticket is already closed")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_closed", actor_email=ctx.user_email,
            user_id=current[1], ticket_id=ticket_id, from_desk=owner is None,
            summary=f"Ticket #{ticket_id} closed by {ctx.user_email}")
        return current[1], current[2]

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

    # ------------------------------------------------------------ tickets, investor

    @router.post("/investor/tickets", status_code=201, response_model=Dict[str, Any])
    async def open_ticket(body: TicketBody, http_request: Request,
                          ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        subject_id = body.subject_id
        if isinstance(subject_id, bool) or not isinstance(subject_id, int):
            raise HTTPException(status_code=400, detail="subject_id is required")
        try:
            text = pc.clean_text(body.body, "body", max_len=4000)
            files = clean_attachments(conn, ctx.org_id, ctx.user_id, body.file_ids)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        subject = conn.execute(
            "SELECT label FROM ticket_subjects WHERE id = %s AND org_id = %s AND enabled",
            (subject_id, ctx.org_id)).fetchone()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found")
        if hourly.is_limited(f"portal-ticket:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        label = subject[0]
        with conn.transaction():
            (ticket_id,) = conn.execute(
                "INSERT INTO tickets (org_id, user_id, subject_id, subject_label) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (ctx.org_id, ctx.user_id, subject_id, label)).fetchone()
            add_message(conn, ctx.org_id, ticket_id, ctx.user_id, False, text, files)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_opened", actor_email=ctx.user_email,
            user_id=ctx.user_id, ticket_id=ticket_id, subject=label, images=len(files),
            summary=f"Ticket #{ticket_id} opened by {ctx.user_email}: {label}")
        await pc.notify_admins(conn, http_request, ctx.org_id, "support",
                               f"New ticket #{ticket_id}: {label}",
                               f"From {ctx.user_email}\n\n{text}", desk_link(ctx.org_id, ticket_id))
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    @router.get("/investor/tickets", response_model=List[Dict[str, Any]])
    async def my_tickets(status: Optional[str] = None, q: Optional[str] = None,
                         ctx: OrgContext = Depends(require_investor),
                         conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where, params = search_clause(q, status)
        # ponytail: LIMIT 500, page when one investor has more tickets than that
        rows = conn.execute(
            f"SELECT {TICKET_SELECT} FROM tickets t "
            f"WHERE t.org_id = %s AND t.user_id = %s{where} "
            "ORDER BY t.last_message_at DESC, t.id DESC LIMIT 500",
            (ctx.org_id, ctx.user_id, *params)).fetchall()
        return [ticket_json(r, ctx.user_id) for r in rows]

    @router.get("/investor/tickets/{ticket_id}", response_model=Dict[str, Any])
    async def my_ticket(ticket_id: int, ctx: OrgContext = Depends(require_investor),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    @router.post("/investor/tickets/{ticket_id}/messages", status_code=201,
                 response_model=Dict[str, Any])
    async def reply_as_investor(ticket_id: int, body: MessageBody, http_request: Request,
                                ctx: OrgContext = Depends(require_investor),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """A reply to a closed ticket opens it again; new stays new."""
        try:
            text = pc.clean_text(body.body, "body", max_len=4000)
            files = clean_attachments(conn, ctx.org_id, ctx.user_id, body.file_ids)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if hourly.is_limited(f"portal-ticket-reply:{ctx.org_id}:{ctx.user_id}",
                             max_attempts=REPLIES_PER_HOUR):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        with conn.transaction():
            row = conn.execute(
                "UPDATE tickets SET status = CASE WHEN status = 'closed' THEN 'open' "
                "ELSE status END, closed_at = NULL, closed_by = NULL, last_message_at = now(), "
                "updated_at = now() WHERE id = %s AND org_id = %s AND user_id = %s "
                "RETURNING subject_label", (ticket_id, ctx.org_id, ctx.user_id)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Ticket not found")
            add_message(conn, ctx.org_id, ticket_id, ctx.user_id, False, text, files)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_replied", actor_email=ctx.user_email,
            user_id=ctx.user_id, ticket_id=ticket_id, from_desk=False, images=len(files))
        await pc.notify_admins(conn, http_request, ctx.org_id, "support",
                               f"Reply on ticket #{ticket_id}: {row[0]}",
                               f"From {ctx.user_email}\n\n{text}", desk_link(ctx.org_id, ticket_id))
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    @router.post("/investor/tickets/{ticket_id}/close", response_model=Dict[str, Any])
    async def close_as_investor(ticket_id: int, ctx: OrgContext = Depends(require_investor),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        await _close(conn, ctx, ticket_id, owner=ctx.user_id)
        return load_thread(conn, ctx.org_id, ticket_id, ctx.user_id)

    # ------------------------------------------------------------ tickets, desk

    @router.get("/tickets", response_model=List[Dict[str, Any]])
    async def ticket_queue(status: Optional[str] = None, q: Optional[str] = None,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where, params = search_clause(q, status)
        # ponytail: LIMIT 500, add paging when an org has more tickets than that
        rows = conn.execute(
            f"SELECT {TICKET_SELECT}, u.email, u.display_name "
            "FROM tickets t JOIN users u ON u.id = t.user_id "
            f"WHERE t.org_id = %s{where} "
            "ORDER BY (t.status <> 'closed') DESC, t.last_message_at DESC, t.id DESC LIMIT 500",
            (ctx.org_id, *params)).fetchall()
        return [ticket_json(r) for r in rows]

    @router.get("/tickets/{ticket_id}", response_model=Dict[str, Any])
    async def ticket_thread(ticket_id: int, ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return load_thread(conn, ctx.org_id, ticket_id)

    @router.post("/tickets/{ticket_id}/messages", status_code=201, response_model=Dict[str, Any])
    async def reply_as_desk(ticket_id: int, body: DeskReply, http_request: Request,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """The first desk reply moves new -> open; a closed ticket is refused
        (the investor opens it again by replying)."""
        try:
            text = pc.clean_text(body.body, "body", max_len=4000)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with conn.transaction():
            row = conn.execute(
                "UPDATE tickets SET status = CASE WHEN status = 'new' THEN 'open' ELSE status END, "
                "last_message_at = now(), updated_at = now() "
                "WHERE id = %s AND org_id = %s AND status <> 'closed' "
                "RETURNING user_id, subject_label", (ticket_id, ctx.org_id)).fetchone()
            if not row:
                exists = conn.execute("SELECT 1 FROM tickets WHERE id = %s AND org_id = %s",
                                      (ticket_id, ctx.org_id)).fetchone()
                if exists:
                    raise HTTPException(status_code=409, detail="ticket is closed")
                raise HTTPException(status_code=404, detail="Ticket not found")
            add_message(conn, ctx.org_id, ticket_id, ctx.user_id, True, text, [])
        investor_id, label = row
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="ticket_replied", actor_email=ctx.user_email,
            user_id=investor_id, ticket_id=ticket_id, from_desk=True, images=0)
        await pc.notify(conn, http_request, ctx.org_id, investor_id, "support",
                        f"New reply on ticket #{ticket_id}: {label}", text,
                        pc.investor_link(ctx.org_id, f"support?ticket={ticket_id}"))
        return load_thread(conn, ctx.org_id, ticket_id)

    @router.post("/tickets/{ticket_id}/close", response_model=Dict[str, Any])
    async def close_as_desk(ticket_id: int, http_request: Request,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        investor_id, label = await _close(conn, ctx, ticket_id, owner=None)
        await pc.notify(conn, http_request, ctx.org_id, investor_id, "support",
                        f"Ticket #{ticket_id} was closed",
                        f"Subject: {label}\nReply on the ticket to open it again.",
                        pc.investor_link(ctx.org_id, f"support?ticket={ticket_id}"))
        return load_thread(conn, ctx.org_id, ticket_id)

    return router
