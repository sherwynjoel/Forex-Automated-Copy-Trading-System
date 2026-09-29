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
from typing import Any, Dict, List

import psycopg
from fastapi import APIRouter, Depends

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 10
RATE_LIMITED = "too many requests; try again later"


def create_portal_investor_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-investor"])

    @router.get("/investor/payment-methods", response_model=List[Dict[str, Any]])
    async def my_payment_methods(ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        """Where this workspace receives money: enabled methods only, in
        the admin's display order."""
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s AND enabled "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=True) for r in rows]

    return router
