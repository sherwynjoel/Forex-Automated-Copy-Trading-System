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
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_investor, require_org_role
from .. import portal_common as pc
from .. import portal_identity as pid
from .portal_admin import parse_min
from .portal_files import file_belongs

logger = logging.getLogger(__name__)


class MpinBody(BaseModel):
    mpin: Any = None


class PackageBody(BaseModel):
    name: str
    min_deposit: Any = "0"
    currency: str = "USD"
    spread_label: Optional[str] = None
    leverage_options: Any = None
    enabled: bool = True
    sort_order: int = 0


class PackagePatch(BaseModel):
    name: Optional[str] = None
    min_deposit: Any = None
    currency: Optional[str] = None
    spread_label: Optional[str] = None   # "" clears it
    leverage_options: Any = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class AccountRequestBody(BaseModel):
    # Any, checked after the MPIN: a typed field would 422 first.
    package_id: Any = None
    leverage: Any = None
    main_password: Any = None
    investor_password: Any = None
    mpin: Any = None


class FulfilBody(BaseModel):
    mt5_login: Any = None
    mt5_server: Any = None
    account_id: Optional[int] = None
    note: Optional[str] = None


class RejectBody(BaseModel):
    note: Optional[str] = None


# Joins a row aliased {t} to the org's CURRENT investor members only.
INVESTOR_MEMBER_JOIN = ("JOIN org_memberships m ON m.org_id = {t}.org_id "
                        "AND m.user_id = {t}.user_id AND m.role = 'investor'")


def _open_request(conn: psycopg.Connection, org_id: int, req_id: int):
    """(status, user_id, package_name, main_password_enc, investor_password_enc)
    of a request still waiting on an admin; 404 / 409 otherwise. The filer
    must still be an investor member here (routes/orgs cancels on leaving)."""
    row = conn.execute(
        "SELECT r.status, r.user_id, r.package_name, r.main_password_enc, "
        "r.investor_password_enc FROM account_requests r "
        f"{INVESTOR_MEMBER_JOIN.format(t='r')} WHERE r.id = %s AND r.org_id = %s",
        (req_id, org_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row[0] != "requested":
        raise HTTPException(status_code=409, detail=f"request is already {row[0]}")
    return row


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

    # ------------------------------------------------------------ KYC, admin

    @router.get("/kyc", response_model=List[Dict[str, Any]])
    async def kyc_queue(status: Optional[str] = None,
                        ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "k.org_id = %s" + (" AND k.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: LIMIT 500, paginate when a workspace has that many profiles
        rows = conn.execute(
            f"SELECT {pc.qualify(pid.PROFILE_COLS, 'k')}, u.email, u.display_name "
            "FROM kyc_profiles k JOIN users u ON u.id = k.user_id "
            f"{INVESTOR_MEMBER_JOIN.format(t='k')} "
            f"WHERE {where} ORDER BY (k.status = 'submitted') DESC, "
            "k.submitted_at DESC NULLS LAST, k.updated_at DESC LIMIT 500", params).fetchall()
        return [pid.profile_json(r) for r in rows]

    @router.post("/kyc/{user_id}/decision", response_model=Dict[str, Any])
    async def decide_kyc(user_id: int, body: pc.Decision, http_request: Request,
                         ctx: OrgContext = Depends(require_org_role("admin")),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="status must be approved or rejected")
        try:
            note = pc.require_note_on_reject(new_status, body.note)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            f"SELECT k.status FROM kyc_profiles k {INVESTOR_MEMBER_JOIN.format(t='k')} "
            "WHERE k.org_id = %s AND k.user_id = %s", (ctx.org_id, user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Profile not found")
        if current[0] != "submitted":
            raise HTTPException(status_code=409, detail=f"profile is {current[0]}, not submitted")
        row = conn.execute(
            "UPDATE kyc_profiles SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s, updated_at = now() "
            "WHERE org_id = %s AND user_id = %s AND status = 'submitted' "
            f"RETURNING {pid.PROFILE_COLS}",
            (new_status, ctx.user_id, note, ctx.org_id, user_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_kyc_decided",
            actor_email=ctx.user_email, user_id=user_id, status=new_status, note=note)
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "identity",
            f"Your identity verification was {new_status}",
            f"Status: {new_status}\nNote: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "profile"))
        return pid.profile_json(row)

    # ------------------------------------------------------------ account packages

    def _package_row(conn: psycopg.Connection, org_id: int, package_id: int):
        row = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE id = %s AND org_id = %s",
            (package_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Package not found")
        return row

    async def _audit_package(conn: psycopg.Connection, ctx: OrgContext, package_id: int,
                             change: str, name: str) -> None:
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="account_package_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, package_id=package_id,
            change=change, name=name,
            summary=f"Account package {change}: {name} by {ctx.user_email}")

    @router.get("/account-packages", response_model=List[Dict[str, Any]])
    async def list_packages(ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE org_id = %s "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pid.package_json(r) for r in rows]

    @router.post("/account-packages", status_code=201, response_model=Dict[str, Any])
    async def create_package(body: PackageBody,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            name = pc.clean_text(body.name, "name", max_len=64)
            min_deposit = parse_min(body.min_deposit, "min_deposit")
            currency = (pc.clean_text(body.currency, "currency", max_len=8,
                                      required=False) or "USD").upper()
            spread = pc.clean_text(body.spread_label, "spread_label", max_len=32, required=False)
            leverage = pid.parse_leverage_options(body.leverage_options)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = conn.execute(
            "INSERT INTO account_packages (org_id, name, min_deposit, currency, spread_label, "
            "leverage_options, enabled, sort_order) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {pid.PACKAGE_COLS}",
            (ctx.org_id, name, min_deposit, currency, spread, leverage, body.enabled,
             body.sort_order)).fetchone()
        out = pid.package_json(row)
        await _audit_package(conn, ctx, out["id"], "created", name)
        return out

    @router.patch("/account-packages/{package_id}", response_model=Dict[str, Any])
    async def update_package(package_id: int, body: PackagePatch,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _package_row(conn, ctx.org_id, package_id)
        sets: list[str] = []
        params: list[Any] = []
        try:
            if body.name is not None:
                sets.append("name = %s")
                params.append(pc.clean_text(body.name, "name", max_len=64))
            if body.min_deposit is not None:
                sets.append("min_deposit = %s")
                params.append(parse_min(body.min_deposit, "min_deposit"))
            if body.currency is not None:
                sets.append("currency = %s")
                params.append((pc.clean_text(body.currency, "currency", max_len=8,
                                             required=False) or "USD").upper())
            if body.spread_label is not None:
                sets.append("spread_label = %s")
                params.append(pc.clean_text(body.spread_label, "spread_label", max_len=32,
                                            required=False))
            if body.leverage_options is not None:
                sets.append("leverage_options = %s")
                params.append(pid.parse_leverage_options(body.leverage_options))
            if body.enabled is not None:
                sets.append("enabled = %s")
                params.append(bool(body.enabled))
            if body.sort_order is not None:
                sets.append("sort_order = %s")
                params.append(int(body.sort_order))
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not sets:
            return pid.package_json(current)
        sets.append("updated_at = now()")
        row = conn.execute(
            f"UPDATE account_packages SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
            f"RETURNING {pid.PACKAGE_COLS}", (*params, package_id, ctx.org_id)).fetchone()
        out = pid.package_json(row)
        await _audit_package(conn, ctx, package_id, "updated", out["name"])
        return out

    @router.delete("/account-packages/{package_id}", status_code=204)
    async def delete_package(package_id: int,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)):
        current = _package_row(conn, ctx.org_id, package_id)
        if conn.execute("SELECT 1 FROM account_requests WHERE package_id = %s "
                        "AND status = 'requested'", (package_id,)).fetchone():
            # Decided requests keep their package_name snapshot and lose
            # only the id (ON DELETE SET NULL); an open one still needs it.
            raise HTTPException(status_code=409, detail="an open request still uses this package")
        conn.execute("DELETE FROM account_packages WHERE id = %s AND org_id = %s",
                     (package_id, ctx.org_id))
        await _audit_package(conn, ctx, package_id, "deleted", current[1])
        return Response(status_code=204)

    @router.get("/investor/account-packages", response_model=List[Dict[str, Any]])
    async def my_packages(ctx: OrgContext = Depends(require_investor),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE org_id = %s AND enabled "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pid.package_json(r) for r in rows]

    # ------------------------------------------------------------ account requests, investor

    @router.get("/investor/account-requests", response_model=List[Dict[str, Any]])
    async def my_account_requests(ctx: OrgContext = Depends(require_investor),
                                  conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pid.REQUEST_COLS} FROM account_requests WHERE org_id = %s AND user_id = %s "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [pid.request_json(r) for r in rows]

    @router.post("/investor/account-requests", status_code=201, response_model=Dict[str, Any])
    async def request_account(body: AccountRequestBody,
                              ctx: OrgContext = Depends(require_investor),
                              conn: psycopg.Connection = Depends(get_conn),
                              cfg: ApiConfig = Depends(ApiConfig.from_env)):
        """A live MT5 account from one package. KYC must be approved, no
        other request may be open, and the investor's accounts plus open
        requests must stay under the workspace's max_live_accounts. The two
        passwords are sealed with FERNET_KEY and live only until an admin
        decides."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        if pid.kyc_status(conn, ctx.org_id, ctx.user_id) != "approved":
            raise HTTPException(status_code=409, detail="verify your identity first")
        if conn.execute("SELECT 1 FROM account_requests WHERE org_id = %s AND user_id = %s "
                        "AND status = 'requested'", (ctx.org_id, ctx.user_id)).fetchone():
            raise HTTPException(status_code=409, detail="a request is already open")
        limit = pc.portal_settings(conn, ctx.org_id)["max_live_accounts"]
        if pc.accounts_used(conn, ctx.org_id, ctx.user_id) >= limit:
            raise HTTPException(status_code=409, detail=pc.account_limit_text(limit))
        package_id = body.package_id
        if isinstance(package_id, bool) or not isinstance(package_id, int):
            raise HTTPException(status_code=404, detail="Package not found")
        row = conn.execute(
            f"SELECT {pid.PACKAGE_COLS} FROM account_packages WHERE id = %s AND org_id = %s "
            "AND enabled", (package_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Package not found")
        package = pid.package_json(row)
        options = package["leverage_options"]
        leverage = body.leverage
        if isinstance(leverage, bool) or not isinstance(leverage, int) or leverage not in options:
            raise HTTPException(status_code=400, detail="leverage must be one of "
                                + ", ".join(str(o) for o in options))
        try:
            main = pid.check_mt5_password(body.main_password, "main_password")
            investor = pid.check_mt5_password(body.investor_password, "investor_password")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if main == investor:
            # MT5 itself refuses an investor (read-only) password equal to
            # the main one; better to say so before the admin finds out.
            raise HTTPException(status_code=400,
                                detail="the investor password must differ from the main password")
        try:
            row = conn.execute(
                "INSERT INTO account_requests (org_id, user_id, package_id, package_name, "
                "leverage, main_password_enc, investor_password_enc) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {pid.REQUEST_COLS}",
                (ctx.org_id, ctx.user_id, package["id"], package["name"], leverage,
                 pid.seal(cfg.fernet_key, main), pid.seal(cfg.fernet_key, investor))).fetchone()
        except psycopg.errors.UniqueViolation:
            # A second request raced the check above (account_requests_one_open).
            raise HTTPException(status_code=409, detail="a request is already open")
        out = pid.request_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_requested",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            request_id=out["id"], package_name=package["name"], leverage=leverage,
            summary=f"Trading account requested: {package['name']} 1:{leverage} "
                    f"by {ctx.user_email}")
        return out

    @router.post("/investor/account-requests/{req_id}/cancel", response_model=Dict[str, Any])
    async def cancel_account_request(req_id: int,
                                     ctx: OrgContext = Depends(require_investor),
                                     conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status FROM account_requests WHERE id = %s AND org_id = %s AND user_id = %s",
            (req_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Request not found")
        if current[0] != "requested":
            raise HTTPException(status_code=409, detail=f"request is already {current[0]}")
        row = conn.execute(
            "UPDATE account_requests SET status = 'cancelled', decided_by = %s, "
            "decided_at = now(), main_password_enc = NULL, investor_password_enc = NULL "
            "WHERE id = %s AND org_id = %s AND status = 'requested' "
            f"RETURNING {pid.REQUEST_COLS}", (ctx.user_id, req_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_request_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id, request_id=req_id)
        return pid.request_json(row)

    # ------------------------------------------------------------ account requests, admin

    @router.get("/account-requests", response_model=List[Dict[str, Any]])
    async def account_request_queue(status: Optional[str] = None,
                                    ctx: OrgContext = Depends(require_org_role("admin")),
                                    conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "r.org_id = %s" + (" AND r.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: LIMIT 500, paginate when a workspace has that many requests
        rows = conn.execute(
            f"SELECT {pc.qualify(pid.REQUEST_COLS, 'r')}, u.email, u.display_name "
            "FROM account_requests r JOIN users u ON u.id = r.user_id "
            f"{INVESTOR_MEMBER_JOIN.format(t='r')} "
            f"WHERE {where} ORDER BY (r.status = 'requested') DESC, r.created_at DESC, r.id DESC "
            "LIMIT 500", params).fetchall()
        return [pid.request_json(r) for r in rows]

    @router.post("/account-requests/{req_id}/reveal", response_model=Dict[str, Any])
    async def reveal_passwords(req_id: int, body: MpinBody,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn),
                               cfg: ApiConfig = Depends(ApiConfig.from_env)):
        """The two passwords, for the admin to type into the broker's
        manager. The ADMIN's MPIN confirms it and every reveal is a warning
        in the audit feed; once the request is decided they are gone."""
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        _status, user_id, _name, main_enc, investor_enc = _open_request(conn, ctx.org_id, req_id)
        try:
            if main_enc is None or investor_enc is None:
                raise InvalidToken
            out = {"main_password": pid.unseal(cfg.fernet_key, main_enc),
                   "investor_password": pid.unseal(cfg.fernet_key, investor_enc)}
        except InvalidToken:
            raise HTTPException(status_code=409, detail=(
                "the passwords can no longer be read; reject this request and ask for a new one"))
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="account_request_passwords_revealed",
            actor_email=ctx.user_email, user_id=user_id, severity="warning", request_id=req_id,
            summary=f"Passwords of account request #{req_id} revealed by {ctx.user_email}")
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @router.post("/account-requests/{req_id}/fulfil", response_model=Dict[str, Any])
    async def fulfil_request(req_id: int, body: FulfilBody, http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        login = body.mt5_login
        if isinstance(login, bool) or not isinstance(login, int) or not 0 < login <= 2**63 - 1:
            raise HTTPException(status_code=400,
                                detail="mt5_login must be a whole number above zero")
        try:
            server = pc.clean_text(body.mt5_server, "mt5_server", max_len=64)
            note = pc.clean_text(body.note, "note", max_len=500, required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _status, user_id, package_name, _m, _i = _open_request(conn, ctx.org_id, req_id)
        # The link and the status change land together or not at all.
        if pid.kyc_status(conn, ctx.org_id, user_id) != "approved":
            raise HTTPException(status_code=409, detail=(
                "the investor's verification is no longer approved; "
                "decide it again before fulfilling"))
        # ponytail: pre-check, not a unique index -- two admins fulfilling the
        # same login at the same instant could both pass; add a partial unique
        # index on (org_id, mt5_server, mt5_login) if that ever matters.
        if conn.execute(
                "SELECT 1 FROM account_requests WHERE org_id = %s AND status = 'fulfilled' "
                "AND mt5_server = %s AND mt5_login = %s", (ctx.org_id, server, login)).fetchone():
            raise HTTPException(status_code=409,
                                detail="that MT5 login is already given to another request")
        with conn.transaction():
            if body.account_id is not None:
                pc.link_account(conn, ctx.org_id, user_id, body.account_id, mt5_only=True)
            row = conn.execute(
                "UPDATE account_requests SET status = 'fulfilled', mt5_login = %s, "
                "mt5_server = %s, account_id = %s, decided_by = %s, decided_at = now(), "
                "decision_note = %s, main_password_enc = NULL, investor_password_enc = NULL "
                "WHERE id = %s AND org_id = %s AND status = 'requested' "
                f"RETURNING {pid.REQUEST_COLS}",
                (login, server, body.account_id, ctx.user_id, note, req_id,
                 ctx.org_id)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
        out = pid.request_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_request_decided",
            actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id,
            request_id=req_id, status="fulfilled", mt5_login=login, mt5_server=server, note=note)
        if body.account_id is not None:
            await pc.audit_control(
                conn, org_id=ctx.org_id, action="investor_account_linked",
                actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id)
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "identity", "Your trading account is ready",
            f"Login: {login}\nServer: {server}\nPackage: {package_name}\n"
            "Sign in to MetaTrader 5 with the passwords you chose when you requested it.\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "open-account"))
        return out

    @router.post("/account-requests/{req_id}/reject", response_model=Dict[str, Any])
    async def reject_request(req_id: int, body: RejectBody, http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            note = pc.clean_text(body.note, "note", max_len=500)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _status, user_id, package_name, _m, _i = _open_request(conn, ctx.org_id, req_id)
        row = conn.execute(
            "UPDATE account_requests SET status = 'rejected', decided_by = %s, "
            "decided_at = now(), decision_note = %s, main_password_enc = NULL, "
            "investor_password_enc = NULL WHERE id = %s AND org_id = %s AND status = 'requested' "
            f"RETURNING {pid.REQUEST_COLS}", (ctx.user_id, note, req_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_request_decided",
            actor_email=ctx.user_email, user_id=user_id, request_id=req_id, status="rejected",
            note=note)
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "identity",
            "Your trading account request was rejected",
            f"Package: {package_name}\nNote: {note}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "open-account"))
        return pid.request_json(row)

    return router
