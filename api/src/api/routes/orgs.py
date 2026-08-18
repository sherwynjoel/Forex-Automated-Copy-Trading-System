"""Org lifecycle: create, settings-bearing GET, members, invites, join."""
import hashlib
import secrets
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_user
from ..db import get_conn
from ..rbac import OrgContext, ROLE_RANK, require_org_role

INVITE_TTL_DAYS = 7


class CreateOrgRequest(BaseModel):
    name: str


class PatchOrgRequest(BaseModel):
    name: Optional[str] = None


class CreateInviteRequest(BaseModel):
    role: str


class JoinRequest(BaseModel):
    token: str


class PatchMemberRequest(BaseModel):
    role: str


def _owner_count(conn, org_id: int) -> int:
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM org_memberships WHERE org_id = %s AND role = 'owner'",
        (org_id,),
    ).fetchone()
    return count


def create_orgs_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs", tags=["orgs"])

    @router.post("", status_code=201)
    async def create_org(
        body: CreateOrgRequest,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        with conn.transaction():
            (org_id,) = conn.execute(
                "INSERT INTO orgs (name) VALUES (%s) RETURNING id", (name,)
            ).fetchone()
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner')",
                (org_id, user_id),
            )
        return {"id": org_id, "name": name, "role": "owner"}

    @router.post("/join")
    async def join_org(
        body: JoinRequest,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        token_hash = hashlib.sha256(body.token.encode()).hexdigest()
        with conn.transaction():
            row = conn.execute(
                """UPDATE org_invites SET consumed_at = now(), consumed_by = %s
                   WHERE token_hash = %s AND consumed_at IS NULL
                     AND expires_at > now()
                   RETURNING org_id, role""",
                (user_id, token_hash),
            ).fetchone()
            if not row:
                raise HTTPException(
                    status_code=410, detail="Invite is invalid, expired, or already used")
            org_id, role = row
            inserted = conn.execute(
                """INSERT INTO org_memberships (org_id, user_id, role)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING role""",
                (org_id, user_id, role),
            ).fetchone()
            if not inserted:
                raise HTTPException(status_code=409, detail="Already a member")
        return {"org_id": org_id, "role": role}

    @router.get("/{org_id}")
    async def get_org(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        row = conn.execute(
            "SELECT id, name, copying_enabled, dry_run, created_at FROM orgs WHERE id = %s",
            (ctx.org_id,),
        ).fetchone()
        return {"id": row[0], "name": row[1], "copying_enabled": row[2],
                "dry_run": row[3], "created_at": row[4].isoformat(),
                "role": ctx.role}

    @router.patch("/{org_id}")
    async def patch_org(
        body: PatchOrgRequest,
        ctx: OrgContext = Depends(require_org_role("owner")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="name must not be empty")
            conn.execute("UPDATE orgs SET name = %s WHERE id = %s", (name, ctx.org_id))
        row = conn.execute(
            "SELECT id, name FROM orgs WHERE id = %s", (ctx.org_id,)).fetchone()
        return {"id": row[0], "name": row[1]}

    @router.delete("/{org_id}", status_code=204)
    async def delete_org(
        ctx: OrgContext = Depends(require_org_role("owner")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        # Cascades memberships, invites, connections, accounts, mappings.
        # Broker positions are NOT touched; the dashboard warns about that.
        conn.execute("DELETE FROM orgs WHERE id = %s", (ctx.org_id,))

    @router.get("/{org_id}/members")
    async def list_members(
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, m.role, m.created_at
               FROM org_memberships m JOIN users u ON u.id = m.user_id
               WHERE m.org_id = %s ORDER BY m.created_at""",
            (ctx.org_id,),
        ).fetchall()
        return [{"user_id": r[0], "email": r[1], "display_name": r[2],
                 "role": r[3], "joined_at": r[4].isoformat()} for r in rows]

    @router.patch("/{org_id}/members/{member_user_id}")
    async def patch_member(
        member_user_id: int,
        body: PatchMemberRequest,
        ctx: OrgContext = Depends(require_org_role("owner")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        if body.role not in ROLE_RANK:
            raise HTTPException(status_code=400, detail="Unknown role")
        with conn.transaction():
            row = conn.execute(
                "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s "
                "FOR UPDATE",
                (ctx.org_id, member_user_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            if (row[0] == "owner" and body.role != "owner"
                    and _owner_count(conn, ctx.org_id) == 1):
                raise HTTPException(
                    status_code=409, detail="An org must keep at least one owner")
            conn.execute(
                "UPDATE org_memberships SET role = %s WHERE org_id = %s AND user_id = %s",
                (body.role, ctx.org_id, member_user_id),
            )
        return {"user_id": member_user_id, "role": body.role}

    @router.delete("/{org_id}/members/{member_user_id}", status_code=204)
    async def remove_member(
        member_user_id: int,
        ctx: OrgContext = Depends(require_org_role("viewer")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        # Owners may remove anyone; anyone may remove THEMSELVES (leave).
        if ctx.role != "owner" and member_user_id != ctx.user_id:
            raise HTTPException(status_code=403, detail="Insufficient role")
        with conn.transaction():
            row = conn.execute(
                "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s "
                "FOR UPDATE",
                (ctx.org_id, member_user_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            if row[0] == "owner" and _owner_count(conn, ctx.org_id) == 1:
                raise HTTPException(
                    status_code=409, detail="An org must keep at least one owner")
            conn.execute(
                "DELETE FROM org_memberships WHERE org_id = %s AND user_id = %s",
                (ctx.org_id, member_user_id),
            )

    @router.post("/{org_id}/invites", status_code=201)
    async def create_invite(
        body: CreateInviteRequest,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        if body.role not in ("admin", "trader", "viewer"):
            raise HTTPException(
                status_code=400, detail="Invites can grant admin, trader, or viewer")
        token = secrets.token_urlsafe(32)
        row = conn.execute(
            """INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at)
               VALUES (%s, %s, %s, %s, now() + make_interval(days => %s))
               RETURNING id, expires_at""",
            (ctx.org_id, body.role, hashlib.sha256(token.encode()).hexdigest(),
             ctx.user_id, INVITE_TTL_DAYS),
        ).fetchone()
        # The raw token is returned exactly once; only its hash is stored.
        return {"id": row[0], "role": body.role, "token": token,
                "expires_at": row[1].isoformat()}

    @router.get("/{org_id}/invites")
    async def list_invites(
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        rows = conn.execute(
            """SELECT id, role, created_at, expires_at, consumed_at
               FROM org_invites WHERE org_id = %s ORDER BY created_at DESC""",
            (ctx.org_id,),
        ).fetchall()
        return [{"id": r[0], "role": r[1], "created_at": r[2].isoformat(),
                 "expires_at": r[3].isoformat(),
                 "consumed": r[4] is not None} for r in rows]

    @router.delete("/{org_id}/invites/{invite_id}", status_code=204)
    async def revoke_invite(
        invite_id: int,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        cursor = conn.execute(
            "DELETE FROM org_invites WHERE id = %s AND org_id = %s",
            (invite_id, ctx.org_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Invite not found")

    return router
