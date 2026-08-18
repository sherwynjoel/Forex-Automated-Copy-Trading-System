"""Org-scoped authorization: role ranks and the require_org_role dependency."""
from dataclasses import dataclass

import psycopg
from fastapi import Depends, HTTPException

from .auth import require_user
from .db import get_conn

ROLE_RANK = {"viewer": 0, "trader": 1, "admin": 2, "owner": 3}


@dataclass(frozen=True)
class OrgContext:
    org_id: int
    user_id: int
    role: str


def require_org_role(min_role: str):
    """Dependency factory: resolve the caller's membership for the path's
    {org_id}. Non-members (and nonexistent orgs) get 404 so org existence
    never leaks; members below min_role get 403."""
    assert min_role in ROLE_RANK

    def dependency(
        org_id: int,
        user_id: int = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> OrgContext:
        row = conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
            (org_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if ROLE_RANK[row[0]] < ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return OrgContext(org_id=org_id, user_id=user_id, role=row[0])

    return dependency


def require_account_in_org(conn: psycopg.Connection, org_id: int, account_id: int) -> None:
    """404 unless the broker account belongs to this org. Every org-scoped
    route that takes an account_id (path or body) must call this before
    touching the account or proxying to the copier."""
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
        (account_id, org_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
