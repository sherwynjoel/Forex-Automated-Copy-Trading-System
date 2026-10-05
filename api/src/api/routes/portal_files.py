"""Files for the client portal: the receipts and proofs an investor uploads
with a deposit notice or a payout destination, served back to their owner
and to the org's admins. Bytes live in app.state.uploads (uploads.py); the
`files` row holds the type, size, hash and storage key.

The type is decided by sniffing the bytes, never by the client's header
or file name; a PDF is served as an attachment so a browser never renders
it inline from our origin.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ..auth import LoginRateLimiter
from ..db import get_conn
from ..rbac import OrgContext, require_investor, require_org_role
from ..uploads import (ACCEPTED_PURPOSES, ALLOWED, MAX_UPLOAD_BYTES, UPLOADS_PER_HOUR,
                       UploadStore, detect_type)

logger = logging.getLogger(__name__)

FILE_COLS = "id, org_id, user_id, purpose, content_type, size_bytes, storage_key, created_at"


def file_belongs(conn: psycopg.Connection, org_id: int, user_id: int,
                 file_id: Optional[int], purpose: str) -> bool:
    """Whether file_id is this investor's file of this purpose in this org,
    and not yet attached anywhere: not to a deposit or a payout destination
    (spec section 9: a file is referenced by at most one request row) and
    not to any slot of a KYC profile (phase 2: one file per document slot)
    and not to any support-ticket message (phase 4).
    None (no file attached) is fine. Routes call this before storing a file
    id, so this is the one gate that rule needs."""
    if file_id is None:
        return True
    row = conn.execute(
        "SELECT 1 FROM files WHERE id = %s AND org_id = %s AND user_id = %s AND purpose = %s "
        "  AND NOT EXISTS (SELECT 1 FROM deposits d WHERE d.receipt_file_id = files.id) "
        "  AND NOT EXISTS (SELECT 1 FROM payout_destinations p WHERE p.proof_file_id = files.id) "
        "  AND NOT EXISTS (SELECT 1 FROM kyc_profiles k WHERE files.id IN "
        "      (k.id_front_file_id, k.id_back_file_id, k.address_proof_file_id, k.photo_file_id)) "
        # ponytail: scans the ticket messages per check; add a GIN index on
        # file_ids if threads ever grow into the hundreds of thousands.
        "  AND NOT EXISTS (SELECT 1 FROM ticket_messages t WHERE files.id = ANY(t.file_ids))",
        (file_id, org_id, user_id, purpose)).fetchone()
    return row is not None


def _serve(request: Request, conn: psycopg.Connection, org_id: int, file_id: int,
           user_id: Optional[int] = None) -> Response:
    """The bytes of one file with the headers spec section 9 asks for.
    `user_id` narrows the lookup to the owner (investor route); None means
    any file in the org (admin route). Missing rows and missing bytes are
    both 404 -- the caller learns nothing about which."""
    sql = f"SELECT {FILE_COLS} FROM files WHERE id = %s AND org_id = %s"
    params: list = [file_id, org_id]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    content_type, storage_key = row[4], row[6]
    store: UploadStore = request.app.state.uploads
    try:
        data = store.read(storage_key)
    except (FileNotFoundError, ValueError):
        logger.error("file %s has a row but no bytes at %s", file_id, storage_key)
        raise HTTPException(status_code=404, detail="File not found")
    ext = ALLOWED.get(content_type, "bin")
    disposition = "attachment" if content_type == "application/pdf" else "inline"
    return Response(content=data, media_type=content_type, headers={
        "Content-Disposition": f'{disposition}; filename="file-{row[0]}.{ext}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=0",
    })


def create_portal_files_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-files"])
    limiter = LoginRateLimiter(max_attempts=UPLOADS_PER_HOUR, window_s=3600)

    @router.post("/investor/files", status_code=201)
    async def upload_file(request: Request,
                          # An empty default, not Form(...): FastAPI treats a blank
                          # Form value as missing, and a required field would then
                          # 422 before our own check. With "" both a blank and an
                          # absent purpose fall through to the 400 below.
                          purpose: str = Form(""),
                          file: UploadFile = File(...),
                          ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)):
        if purpose not in ACCEPTED_PURPOSES:
            raise HTTPException(status_code=400, detail="purpose is not accepted yet")
        # One byte past the cap is enough to know it is too big; never
        # buffer an unbounded body.
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="file too large (5 MB max)")
        if not data:
            raise HTTPException(status_code=400, detail="file is empty")
        detected = detect_type(data[:16])
        if detected is None:
            raise HTTPException(status_code=400, detail="unsupported file type")
        content_type, ext = detected
        if purpose == "ticket_attachment" and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="attachments must be images")
        # Only a well-formed upload spends a slot in the hourly budget.
        if limiter.is_limited(f"portal-upload:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many uploads; try again later")
        store: UploadStore = request.app.state.uploads
        digest = hashlib.sha256(data).hexdigest()
        # The key needs the row id, so: insert with a placeholder key, write
        # the bytes, then set the real key -- all in one transaction, so a
        # failed disk write leaves no row behind.
        with conn.transaction():
            file_id, created_at = conn.execute(
                "INSERT INTO files (org_id, user_id, purpose, content_type, size_bytes, sha256, "
                "storage_key) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (ctx.org_id, ctx.user_id, purpose, content_type, len(data), digest,
                 f"pending/{ctx.org_id}/{secrets.token_hex(8)}")).fetchone()
            key = store.key(ctx.org_id, int(file_id), ext)
            store.write(key, data)
            conn.execute("UPDATE files SET storage_key = %s WHERE id = %s", (key, file_id))
        return {"id": int(file_id), "purpose": purpose, "content_type": content_type,
                "size_bytes": len(data), "created_at": created_at.isoformat()}

    @router.get("/investor/files/{file_id}")
    async def read_own_file(file_id: int, request: Request,
                            ctx: OrgContext = Depends(require_investor),
                            conn: psycopg.Connection = Depends(get_conn)):
        return _serve(request, conn, ctx.org_id, file_id, user_id=ctx.user_id)

    @router.get("/files/{file_id}")
    async def read_any_file(file_id: int, request: Request,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)):
        return _serve(request, conn, ctx.org_id, file_id)

    return router
