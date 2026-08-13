"""Events audit log REST endpoint."""
from typing import Optional, List
from datetime import datetime

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_admin
from ..db import get_conn


class EventResponse(BaseModel):
    """Response for an event."""
    id: int
    ts: datetime
    account_id: Optional[int]
    category: str
    severity: str
    latency_ms: Optional[int]
    payload: dict


def create_events_router() -> APIRouter:
    """Create router for events REST endpoint."""
    router = APIRouter(prefix="/api", tags=["events"])

    @router.get("/events", response_model=List[EventResponse])
    async def list_events(
        account_id: Optional[int] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 200,
        _: bool = Depends(require_admin),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> List[EventResponse]:
        """Get audit log events with optional filtering.

        Args:
            account_id: Filter by account ID.
            severity: Filter by severity (info, warning, error).
            category: Filter by category (master_event, slave_action, connection, auth, drift, control).
            since: Filter events after this timestamp (ISO format).
            limit: Maximum number of events to return (default 200, max 1000).

        Returns:
            List of events, newest first.
        """
        # Validate and constrain limit
        if limit < 1:
            raise HTTPException(status_code=400, detail="limit must be at least 1")
        if limit > 1000:
            limit = 1000

        # Validate since timestamp if provided
        if since is not None:
            try:
                # Try to parse as ISO format to validate
                datetime.fromisoformat(since.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="since must be a valid ISO 8601 timestamp")

        # Build query with optional filters
        query_parts = ["SELECT id, ts, account_id, category, severity, latency_ms, payload FROM events"]
        params = []
        where_clauses = []

        if account_id is not None:
            where_clauses.append("account_id = %s")
            params.append(account_id)

        if severity is not None:
            where_clauses.append("severity = %s")
            params.append(severity)

        if category is not None:
            where_clauses.append("category = %s")
            params.append(category)

        if since is not None:
            where_clauses.append("ts > %s")
            params.append(since)

        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))

        # Always order by ts DESC (newest first)
        query_parts.append("ORDER BY ts DESC")
        query_parts.append("LIMIT %s")
        params.append(limit)

        query = " ".join(query_parts)

        rows = conn.execute(query, params).fetchall()

        return [
            EventResponse(
                id=row[0],
                ts=row[1],
                account_id=row[2],
                category=row[3],
                severity=row[4],
                latency_ms=row[5],
                payload=row[6],
            )
            for row in rows
        ]

    return router
