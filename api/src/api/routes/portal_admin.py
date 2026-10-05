# api/src/api/routes/portal_admin.py
"""The client portal, admin side (phase 1).

One router under /api/orgs/{org_id}, every route require_org_role("admin").
Payment methods (where the org receives money) and the portal settings pair
live here from Task 6; the Requests desk (deposits, withdrawals, transfers,
payout destinations), the investors list, ledger reads and adjustments are
added by Tasks 7-10.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_org_role
from .portal_investor import WALLET_LABELS, entries_page, money_ref_label, pending_counts
from .portal_support import WAITING_ON_DESK
from .. import portal_common as pc
# Controller ruling (Task 10): org_state/equity_from already live in
# portal_common (Task 5); imported under these underscore names rather than
# re-defined here, byte-identical to what this router used to carry
# privately.
from ..portal_common import org_state as _org_state, equity_from as _equity_from

logger = logging.getLogger(__name__)

# A hand-posted adjustment: one optional sign, digits, at most two decimals.
# ASCII digits only (re's \d would also take other scripts' digits).
SIGNED_AMOUNT = re.compile(r"[+-]?[0-9]+(\.[0-9]{1,2})?")

METHOD_KINDS = ("crypto", "bank")
# The detail keys each kind must carry (400 "<field> is required" when
# missing) and the ones it may carry. Anything else is dropped so the JSON
# column only ever holds what the dashboard renders.
REQUIRED_DETAILS = {"crypto": ("coin", "network", "address"),
                    "bank": ("bank_name", "holder", "account_number", "code")}
OPTIONAL_DETAILS = {"crypto": ("memo",), "bank": ("bank_address", "country")}


class MethodBody(BaseModel):
    kind: str
    label: str
    currency: str = "USD"
    details: dict
    min_amount: Any = "0"
    fee_pct: Any = "0"
    instructions: Optional[str] = None
    sort_order: int = 0


class MethodPatch(BaseModel):
    label: Optional[str] = None
    currency: Optional[str] = None
    details: Optional[dict] = None
    min_amount: Any = None
    fee_pct: Any = None
    instructions: Optional[str] = None   # "" clears it
    sort_order: Optional[int] = None
    enabled: Optional[bool] = None


class SettingsBody(BaseModel):
    withdrawal_min: Any
    withdrawal_fee_pct: Any
    max_live_accounts: Any = None   # omitted: keep the current cap


class DepositDecision(BaseModel):
    status: str
    credited_amount: Any = None
    note: Optional[str] = None


class PaidBody(BaseModel):
    txid: str


class AdjustmentBody(BaseModel):
    wallet: str
    amount: Any
    note: str
    mpin: Any = None


class LinkBody(BaseModel):
    account_id: int


def clean_details(kind: str, raw: Any) -> dict:
    """Trimmed details for `kind`: every required key present and non-blank
    (else LedgerError "<field> is required"), optional keys kept when
    given, unknown keys dropped."""
    if not isinstance(raw, dict):
        raise pc.LedgerError(f"{REQUIRED_DETAILS[kind][0]} is required")
    out: dict = {}
    for key in REQUIRED_DETAILS[kind]:
        out[key] = pc.clean_text(raw.get(key), key, max_len=128)
    for key in OPTIONAL_DETAILS[kind]:
        value = pc.clean_text(raw.get(key), key, max_len=256, required=False)
        if value is not None:
            out[key] = value
    return out


def parse_min(raw: object, field: str) -> Decimal:
    """A minimum may be zero (meaning none); above zero it follows
    parse_amount and its messages."""
    if raw is None or raw == "":
        return Decimal("0")
    try:
        if not isinstance(raw, bool) and Decimal(str(raw)) == 0:
            return Decimal("0")
    except (InvalidOperation, ValueError):
        pass
    return pc.parse_amount(raw, field)


def parse_pct(raw: object, field: str) -> Decimal:
    """0 <= pct < 100 with at most three decimals (NUMERIC(6,3))."""
    message = f"{field} must be between 0 and 99.999"
    if isinstance(raw, bool) or raw is None or raw == "":
        raise pc.LedgerError(message)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise pc.LedgerError(message)
    if not value.is_finite() or value < 0 or value >= 100:
        raise pc.LedgerError(message)
    if value != value.quantize(Decimal("0.001")):
        raise pc.LedgerError(message)
    return value


def _require_investor(conn: psycopg.Connection, org_id: int, user_id: int) -> str:
    """The investor member's email, or 404 'Investor not found'."""
    row = conn.execute(
        "SELECT u.email FROM org_memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.org_id = %s AND m.user_id = %s AND m.role = 'investor'",
        (org_id, user_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Investor not found")
    return row[0]


def create_portal_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-admin"])

    def _method_row(conn: psycopg.Connection, org_id: int, method_id: int):
        row = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE id = %s AND org_id = %s",
            (method_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payment method not found")
        return row

    async def _audit_method(conn: psycopg.Connection, ctx: OrgContext, method_id: int,
                            change: str, kind: str, label: str, *,
                            before=None, after=None) -> None:
        # A warning, not an info: this is where every investor is told to
        # send money, and an attacker with an admin session would change
        # exactly this. Both alerters are wired to warnings. before/after
        # carry the details so an address swap shows in the alert.
        snaps = {}
        for name, row in (("before", before), ("after", after)):
            if row:
                m = pc.method_json(row, public=False)
                snaps[name] = {k: m[k] for k in ("label", "kind", "enabled", "details")}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="payment_method_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            method_id=method_id, change=change, kind=kind, label=label, **snaps,
            summary=f"Payment method {change}: {label} ({kind}) by {ctx.user_email}")

    # ------------------------------------------------------------ payment methods

    @router.get("/payment-methods", response_model=List[Dict[str, Any]])
    async def list_methods(ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=False) for r in rows]

    @router.post("/payment-methods", status_code=201, response_model=Dict[str, Any])
    async def create_method(body: MethodBody,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        kind = body.kind.strip().lower()
        if kind not in METHOD_KINDS:
            raise HTTPException(status_code=400, detail="kind must be crypto or bank")
        try:
            label = pc.clean_text(body.label, "label", max_len=64)
            currency = (pc.clean_text(body.currency, "currency", max_len=8,
                                      required=False) or "USD").upper()
            details = clean_details(kind, body.details)
            min_amount = parse_min(body.min_amount, "min_amount")
            fee_pct = parse_pct(body.fee_pct, "fee_pct")
            instructions = pc.clean_text(body.instructions, "instructions", max_len=1000,
                                         required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = conn.execute(
            "INSERT INTO payment_methods (org_id, kind, label, currency, details, min_amount, "
            "fee_pct, instructions, sort_order, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {pc.METHOD_COLS}",
            (ctx.org_id, kind, label, currency, Jsonb(details), min_amount, fee_pct,
             instructions, body.sort_order, ctx.user_id)).fetchone()
        out = pc.method_json(row, public=False)
        await _audit_method(conn, ctx, out["id"], "created", kind, label, after=row)
        return out

    @router.patch("/payment-methods/{method_id}", response_model=Dict[str, Any])
    async def update_method(method_id: int, body: MethodPatch,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _method_row(conn, ctx.org_id, method_id)
        kind = current[1]
        sets: list[str] = []
        params: list[Any] = []
        try:
            if body.label is not None:
                sets.append("label = %s")
                params.append(pc.clean_text(body.label, "label", max_len=64))
            if body.currency is not None:
                sets.append("currency = %s")
                params.append((pc.clean_text(body.currency, "currency", max_len=8,
                                             required=False) or "USD").upper())
            if body.details is not None:
                sets.append("details = %s")
                params.append(Jsonb(clean_details(kind, body.details)))
            if body.min_amount is not None:
                sets.append("min_amount = %s")
                params.append(parse_min(body.min_amount, "min_amount"))
            if body.fee_pct is not None:
                sets.append("fee_pct = %s")
                params.append(parse_pct(body.fee_pct, "fee_pct"))
            if body.instructions is not None:
                sets.append("instructions = %s")
                params.append(pc.clean_text(body.instructions, "instructions", max_len=1000,
                                            required=False))
            if body.sort_order is not None:
                sets.append("sort_order = %s")
                params.append(int(body.sort_order))
            if body.enabled is not None:
                sets.append("enabled = %s")
                params.append(bool(body.enabled))
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not sets:
            return pc.method_json(current, public=False)
        sets.append("updated_at = now()")
        row = conn.execute(
            f"UPDATE payment_methods SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
            f"RETURNING {pc.METHOD_COLS}", (*params, method_id, ctx.org_id)).fetchone()
        out = pc.method_json(row, public=False)
        await _audit_method(conn, ctx, method_id, "updated", kind, out["label"],
                            before=current, after=row)
        return out

    @router.delete("/payment-methods/{method_id}", status_code=204)
    async def delete_method(method_id: int,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)):
        current = _method_row(conn, ctx.org_id, method_id)
        pending = conn.execute(
            "SELECT 1 FROM deposits WHERE method_id = %s AND status = 'pending'",
            (method_id,)).fetchone()
        if pending:
            # The admin still has to decide that notice against the method's
            # details. Decided rows keep their snapshot (method_kind,
            # method_label) and lose only the id (ON DELETE SET NULL).
            raise HTTPException(status_code=409, detail="a pending deposit still uses this method")
        conn.execute("DELETE FROM payment_methods WHERE id = %s AND org_id = %s",
                     (method_id, ctx.org_id))
        await _audit_method(conn, ctx, method_id, "deleted", current[1], current[2],
                            before=current)
        return Response(status_code=204)

    # ------------------------------------------------------------ portal settings

    def _settings_json(settings: dict) -> Dict[str, Any]:
        return {"withdrawal_min": pc.money(settings["withdrawal_min"]),
                "withdrawal_fee_pct": float(settings["withdrawal_fee_pct"]),
                "max_live_accounts": settings["max_live_accounts"]}

    @router.get("/portal-settings", response_model=Dict[str, Any])
    async def get_settings(ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return _settings_json(pc.portal_settings(conn, ctx.org_id))

    @router.put("/portal-settings", response_model=Dict[str, Any])
    async def put_settings(body: SettingsBody,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            withdrawal_min = parse_min(body.withdrawal_min, "withdrawal_min")
            fee_pct = parse_pct(body.withdrawal_fee_pct, "withdrawal_fee_pct")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = pc.portal_settings(conn, ctx.org_id)
        cap = current["max_live_accounts"] if body.max_live_accounts is None else body.max_live_accounts
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 50:
            raise HTTPException(status_code=400,
                                detail="max_live_accounts must be a whole number from 1 to 50")
        previous = _settings_json(current)
        conn.execute(
            "UPDATE portal_settings SET withdrawal_min = %s, withdrawal_fee_pct = %s, "
            "max_live_accounts = %s, updated_by = %s, updated_at = now() WHERE org_id = %s",
            (withdrawal_min, fee_pct, cap, ctx.user_id, ctx.org_id))
        out = {"withdrawal_min": pc.money(withdrawal_min), "withdrawal_fee_pct": float(fee_pct),
               "max_live_accounts": cap}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="portal_settings_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, previous=previous, **out,
            summary=f"Withdrawal rules set to min {withdrawal_min:.2f} USD, fee {fee_pct}%, "
                    f"max {cap} live accounts per investor by {ctx.user_email}")
        return out

    # ------------------------------------------------------------ deposits

    @router.get("/deposits", response_model=List[Dict[str, Any]])
    async def deposit_queue(status: Optional[str] = None,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "d.org_id = %s" + (" AND d.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: fixed cap, add paging when an org has more than 500 open requests
        rows = conn.execute(
            f"SELECT {pc.qualify(pc.DEPOSIT_COLS, 'd')}, u.email, u.display_name "
            "FROM deposits d JOIN users u ON u.id = d.user_id "
            f"WHERE {where} ORDER BY (d.status = 'pending') DESC, d.created_at DESC, d.id DESC "
            "LIMIT 500",
            params).fetchall()
        return [pc.deposit_json(r) for r in rows]

    @router.post("/deposits/{deposit_id}/decision", response_model=Dict[str, Any])
    async def decide_deposit(deposit_id: int, body: DepositDecision, http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Confirm (credit main by credited_amount, default amount - fee) or
        reject (note required). The status change, the ledger row and, for
        a trading-account deposit, the approved main -> account transfer
        are one transaction; the UPDATE carries `AND status = 'pending'` so
        a lost race or a replay changes nothing."""
        new_status = body.status.strip().lower()
        if new_status not in ("confirmed", "rejected"):
            raise HTTPException(status_code=400, detail="status must be confirmed or rejected")
        try:
            note = pc.require_note_on_reject(new_status, body.note)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, amount, fee, target, target_account_id FROM deposits "
            "WHERE id = %s AND org_id = %s", (deposit_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        status_now, investor_id, amount, fee, target, target_account_id = current
        if not pc.can_transition("deposits", status_now, new_status):
            raise HTTPException(status_code=409, detail=f"deposit is already {status_now}")
        credited: Optional[Decimal] = None
        if new_status == "confirmed":
            try:
                credited = (Decimal(amount) - Decimal(fee) if body.credited_amount is None
                            else pc.parse_amount(body.credited_amount, "credited_amount"))
            except pc.LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if credited <= 0:
                raise HTTPException(status_code=400,
                                    detail="credited_amount must be greater than 0")
            if credited > Decimal(amount):
                raise HTTPException(status_code=400, detail="credited amount cannot exceed "
                                    f"the notice amount ({Decimal(amount):.2f})")
        linked: Optional[int] = None
        transfer_id: Optional[int] = None
        with conn.transaction():
            # First statement in the transaction, uniformly, whether this
            # decision settles money or not: keeps every ledger-writing
            # transaction on the same rule for Tasks 9-10 rather than
            # special-casing "only when new_status == confirmed".
            pc.lock_investor_ledger(conn, ctx.org_id, investor_id)
            if new_status == "confirmed" and target == "account":
                # The account the notice named, if the investor still owns it
                # NOW; unlinked (or deleted, so NULL) since then -> the wallet.
                if target_account_id is not None and pc.owns_account(
                        conn, ctx.org_id, investor_id, int(target_account_id)):
                    linked = int(target_account_id)
                else:
                    suffix = "(no account linked; credited to wallet)"
                    note = f"{note} {suffix}" if note else suffix
            row = conn.execute(
                "UPDATE deposits SET status = %s, decided_by = %s, decided_at = now(), "
                "decision_note = %s, credited_amount = %s "
                "WHERE id = %s AND org_id = %s AND status = 'pending' "
                f"RETURNING {pc.DEPOSIT_COLS}",
                (new_status, ctx.user_id, note, credited, deposit_id, ctx.org_id)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
            if new_status == "confirmed":
                pc.settle(conn, org_id=ctx.org_id, user_id=investor_id, wallet="main",
                          amount=credited, kind="deposit", ref_table="deposits",
                          ref_id=deposit_id, created_by=ctx.user_id)
                if linked is not None:
                    # Held in main until the admin funds the broker account
                    # and marks the transfer done (Task 9 settles it).
                    (transfer_id,) = conn.execute(
                        "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                        "target_kind, target_account_id, amount, status, decided_by, "
                        "decided_at, decision_note) "
                        "VALUES (%s, %s, 'wallet', 'main', 'account', %s, %s, 'approved', %s, "
                        "now(), %s) RETURNING id",
                        (ctx.org_id, investor_id, linked, credited, ctx.user_id,
                         f"funded from deposit #{deposit_id}")).fetchone()
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_decided",
            actor_email=ctx.user_email, user_id=investor_id, account_id=linked,
            deposit_id=deposit_id, status=new_status, amount=out["amount"],
            credited_amount=out["credited_amount"], note=note, transfer_id=transfer_id,
            summary=f"Deposit #{deposit_id} {new_status} by {ctx.user_email}")
        credited_line = (f"Credited: {out['credited_amount']:.2f} USD\n"
                         if out["credited_amount"] is not None else "")
        await pc.notify(
            conn, http_request, ctx.org_id, investor_id, "money",
            f"Your deposit of {out['amount']:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {out['amount']:.2f} USD via {out['method_label']}\n"
            f"{credited_line}Note: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "deposit"))
        return out

    # ------------------------------------------------------------ withdrawals

    @router.get("/withdrawals", response_model=List[Dict[str, Any]])
    async def withdrawal_queue(status: Optional[str] = None,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "w.org_id = %s" + (" AND w.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: fixed cap, add paging when an org has more than 500 open requests
        # A derived table so the bare column list of WITHDRAWAL_COLS is
        # unambiguous next to users (both carry id and created_at).
        rows = conn.execute(
            f"SELECT {pc.WITHDRAWAL_COLS}, email, display_name FROM ("
            "  SELECT w.*, u.email, u.display_name FROM withdrawals w "
            f"  JOIN users u ON u.id = w.user_id WHERE {where}) AS q "
            "ORDER BY (status IN ('requested', 'approved')) DESC, created_at DESC, id DESC "
            "LIMIT 500",
            params).fetchall()
        return [pc.withdrawal_json(r) for r in rows]

    @router.post("/withdrawals/{wd_id}/decision", response_model=Dict[str, Any])
    async def decide_withdrawal(wd_id: int, body: pc.Decision, http_request: Request,
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
            "SELECT status, user_id, amount, destination_summary FROM withdrawals "
            "WHERE id = %s AND org_id = %s", (wd_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        status_now, user_id, amount, summary = current
        if not pc.can_transition("withdrawals", status_now, new_status):
            raise HTTPException(status_code=409, detail=f"withdrawal is already {status_now}")
        row = conn.execute(
            "UPDATE withdrawals SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {pc.WITHDRAWAL_COLS}",
            (new_status, ctx.user_id, note, wd_id, ctx.org_id, status_now)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.withdrawal_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_decided",
            actor_email=ctx.user_email, user_id=user_id,
            withdrawal_id=wd_id, status=new_status, note=note, amount=out["amount"])
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your withdrawal of {amount:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {amount:.2f} USD\nTo: {summary}\n"
            f"Note: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "withdraw"))
        return out

    @router.post("/withdrawals/{wd_id}/paid", response_model=Dict[str, Any])
    async def mark_withdrawal_paid(wd_id: int, body: PaidBody, http_request: Request,
                                   ctx: OrgContext = Depends(require_org_role("admin")),
                                   conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            txid = pc.clean_text(body.txid, "txid")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, amount, destination_summary FROM withdrawals "
            "WHERE id = %s AND org_id = %s", (wd_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        status_now, user_id, amount, summary = current
        if not pc.can_transition("withdrawals", status_now, "paid"):
            raise HTTPException(status_code=409,
                                detail=f"withdrawal is {status_now}, not approved")
        # Status change and ledger debit in ONE transaction; the unique
        # index on (ref_table, ref_id, wallet) makes a replay a no-op. The
        # investor's ledger lock is taken FIRST, before the UPDATE: without
        # it, a concurrent withdrawal request could read a pre-debit
        # balance and a post-release hold (two separate SELECTs at READ
        # COMMITTED straddling this commit) and pass its cap against money
        # this transaction is about to remove from `main`.
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            row = conn.execute(
                "UPDATE withdrawals SET status = 'paid', paid_by = %s, paid_at = now(), "
                "txid = %s WHERE id = %s AND org_id = %s AND status = 'approved' "
                f"RETURNING {pc.WITHDRAWAL_COLS}",
                (ctx.user_id, txid, wd_id, ctx.org_id)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
            pc.settle(conn, org_id=ctx.org_id, user_id=user_id, wallet="main",
                     amount=-Decimal(amount), kind="withdrawal", ref_table="withdrawals",
                     ref_id=wd_id)
        out = pc.withdrawal_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_paid",
            actor_email=ctx.user_email, user_id=user_id,
            withdrawal_id=wd_id, txid=txid, amount=out["amount"])
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your withdrawal of {amount:.2f} USD was paid",
            f"Amount: {amount:.2f} USD\nTo: {summary}\nTransaction: {txid}\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "withdraw"))
        return out

    # ---------------------------------------------------- payout destinations

    @router.get("/payout-destinations", response_model=List[Dict[str, Any]])
    async def destination_queue(status: Optional[str] = None,
                                ctx: OrgContext = Depends(require_org_role("admin")),
                                conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "d.org_id = %s" + (" AND d.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: fixed cap, add paging when an org has more than 500 open requests
        rows = conn.execute(
            f"SELECT {pc.DESTINATION_COLS}, email, display_name FROM ("
            "  SELECT d.*, u.email, u.display_name FROM payout_destinations d "
            f"  JOIN users u ON u.id = d.user_id WHERE {where}) AS q "
            "ORDER BY (status = 'pending') DESC, created_at DESC, id DESC LIMIT 500",
            params).fetchall()
        return [pc.destination_json(r, full=True) for r in rows]

    @router.post("/payout-destinations/{dest_id}/decision", response_model=Dict[str, Any])
    async def decide_destination(dest_id: int, body: pc.Decision, http_request: Request,
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
            "SELECT status, user_id, kind, details FROM payout_destinations "
            "WHERE id = %s AND org_id = %s", (dest_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Payout account not found")
        status_now, user_id, kind, details = current
        if not pc.can_transition("payout_destinations", status_now, new_status):
            raise HTTPException(status_code=409,
                                detail=f"payout account is already {status_now}")
        row = conn.execute(
            "UPDATE payout_destinations SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {pc.DESTINATION_COLS}",
            (new_status, ctx.user_id, note, dest_id, ctx.org_id, status_now)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.destination_json(row, full=True)
        summary = pc.destination_summary(kind, details)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_destination_decided",
            actor_email=ctx.user_email, user_id=user_id,
            destination_id=dest_id, status=new_status, note=note, destination=summary)
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your payout account {summary} was {new_status}",
            f"Status: {new_status}\nPayout account: {summary}\nNote: {note or '—'}\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "payout-accounts"))
        return out

    # -------------------------------------------------------------- transfers

    @router.get("/transfers", response_model=List[Dict[str, Any]])
    async def transfer_queue(status: Optional[str] = None,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "t.org_id = %s" + (" AND t.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # ponytail: fixed cap, add paging when an org has more than 500 open requests
        rows = conn.execute(
            f"SELECT {pc.TRANSFER_COLS}, email, display_name FROM ("
            "  SELECT t.*, u.email, u.display_name FROM transfers t "
            f"  JOIN users u ON u.id = t.user_id WHERE {where}) AS q "
            "ORDER BY (status IN ('requested', 'approved')) DESC, created_at DESC, id DESC "
            "LIMIT 500",
            params).fetchall()
        return [pc.transfer_json(r) for r in rows]

    @router.post("/transfers/{tr_id}/decision", response_model=Dict[str, Any])
    async def decide_transfer(tr_id: int, body: pc.Decision, http_request: Request,
                              ctx: OrgContext = Depends(require_org_role("admin")),
                              conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "done", "rejected"):
            raise HTTPException(status_code=400,
                                detail="status must be approved, done or rejected")
        try:
            note = pc.require_note_on_reject(new_status, body.note)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, source_kind, source_wallet, source_account_id, "
            "target_kind, target_wallet, target_account_id, amount FROM transfers "
            "WHERE id = %s AND org_id = %s", (tr_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Transfer not found")
        (status_now, user_id, source_kind, source_wallet, source_account, target_kind,
         target_wallet, target_account, amount) = current
        if not pc.can_transition("transfers", status_now, new_status):
            raise HTTPException(status_code=409, detail=f"transfer is already {status_now}")
        amount = Decimal(amount)
        # The per-investor ledger lock is the first statement inside the
        # transaction on every decision path -- not just `done`, which is
        # the only one that settles -- so every writer of wallet_entries
        # for this investor is uniformly serialised (the same rule Task 8
        # applies to withdrawals). Keyed on the transfer's INVESTOR
        # (user_id), never the admin deciding it.
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            if new_status == "done":
                # Done straight from requested also records the decision;
                # done after approved keeps the earlier decision.
                row = conn.execute(
                    "UPDATE transfers SET status = 'done', "
                    "decided_by = COALESCE(decided_by, %s), "
                    "decided_at = COALESCE(decided_at, now()), "
                    "decision_note = COALESCE(%s, decision_note), "
                    "done_by = %s, done_at = now() "
                    "WHERE id = %s AND org_id = %s AND status = %s "
                    # Account removal NULLs the account end (without our lock);
                    # done would settle against nothing, so guard it here.
                    "AND NOT ((source_kind = 'account' AND source_account_id IS NULL) "
                    "OR (target_kind = 'account' AND target_account_id IS NULL)) "
                    f"RETURNING {pc.TRANSFER_COLS}",
                    (ctx.user_id, note, ctx.user_id, tr_id, ctx.org_id, status_now)).fetchone()
            else:
                row = conn.execute(
                    "UPDATE transfers SET status = %s, decided_by = %s, decided_at = now(), "
                    "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
                    f"RETURNING {pc.TRANSFER_COLS}",
                    (new_status, ctx.user_id, note, tr_id, ctx.org_id, status_now)).fetchone()
            if not row and new_status == "done" and conn.execute(
                    "SELECT 1 FROM transfers WHERE id = %s AND status = %s AND "
                    "((source_kind = 'account' AND source_account_id IS NULL) "
                    "OR (target_kind = 'account' AND target_account_id IS NULL))",
                    (tr_id, status_now)).fetchone():
                raise HTTPException(status_code=409, detail="the trading account was removed; "
                                                            "reject this transfer instead")
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
            if new_status == "done":
                if source_wallet is not None:
                    pc.settle(conn, org_id=ctx.org_id, user_id=user_id, wallet=source_wallet,
                             amount=-amount, kind="transfer", ref_table="transfers",
                             ref_id=tr_id)
                if target_wallet is not None:
                    pc.settle(conn, org_id=ctx.org_id, user_id=user_id, wallet=target_wallet,
                             amount=amount, kind="transfer", ref_table="transfers",
                             ref_id=tr_id)
        out = pc.transfer_json(row)
        from_label = money_ref_label(source_kind if source_kind == "account" else source_wallet,
                                     source_account)
        to_label = money_ref_label(target_kind if target_kind == "account" else target_wallet,
                                   target_account)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_transfer_decided",
            actor_email=ctx.user_email, user_id=user_id,
            account_id=source_account if source_account is not None else target_account,
            transfer_id=tr_id, status=new_status, note=note, amount=out["amount"],
            source=from_label, target=to_label)
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your transfer of {amount:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {amount:.2f} USD\nFrom: {from_label}\n"
            f"To: {to_label}\nNote: {note or '—'}\n\nOpen the portal for details.",
            pc.investor_link(ctx.org_id, "transfer"))
        return out

    # ---------------------------------------------------------------- investors

    @router.get("/investors", response_model=List[Dict[str, Any]])
    async def list_investors(http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn),
                             cfg: ApiConfig = Depends(ApiConfig.from_env)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, m.created_at, COALESCE(k.status, 'draft')
               FROM org_memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN kyc_profiles k ON k.org_id = m.org_id AND k.user_id = u.id
               WHERE m.org_id = %s AND m.role = 'investor'
               ORDER BY u.display_name, u.id""", (ctx.org_id,)).fetchall()
        owned: Dict[int, list] = {}
        for user_id, account_id, nickname in conn.execute(
                "SELECT investor_user_id, ctid_trader_account_id, nickname FROM accounts "
                "WHERE org_id = %s AND investor_user_id IS NOT NULL "
                "ORDER BY ctid_trader_account_id", (ctx.org_id,)).fetchall():
            owned.setdefault(user_id, []).append((int(account_id), nickname))
        # One /state round trip for the whole list, never one per investor or account.
        state = await _org_state(http_request.app.state.http, cfg, ctx.org_id)
        out = []
        for user_id, email, name, joined_at, kyc in rows:
            figures = pc.wallet_figures(conn, ctx.org_id, user_id)
            accounts = []
            for account_id, nickname in owned.get(user_id, []):
                equity, source, _positions = _equity_from(state, conn, account_id)
                accounts.append({"account_id": account_id, "nickname": nickname,
                                 "equity": pc.money(equity), "equity_source": source})
            out.append({
                "user_id": user_id, "email": email, "display_name": name,
                "joined_at": joined_at.isoformat(), "accounts": accounts,
                "balances": {w: pc.money(figures[w]["balance"]) for w in pc.WALLETS},
                "on_hold": pc.money(figures["main"]["on_hold"]),
                "available": pc.money(figures["main"]["available"]),
                "pending": pending_counts(conn, ctx.org_id, user_id),
                "kyc_status": kyc,
            })
        return out

    @router.post("/investors/{user_id}/accounts", status_code=201, response_model=Dict[str, Any])
    async def link_investor_account(user_id: int, body: LinkBody,
                                    ctx: OrgContext = Depends(require_org_role("admin")),
                                    conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Add one account to an investor under pc.link_account's rules: not
        the master, nobody else's, and under max_live_accounts (any platform,
        as before; only fulfil insists on MT5)."""
        _require_investor(conn, ctx.org_id, user_id)
        with conn.transaction():
            pc.link_account(conn, ctx.org_id, user_id, body.account_id)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_linked",
            actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id)
        return {"user_id": user_id, "account_id": body.account_id}

    @router.delete("/investors/{user_id}/accounts/{account_id}", status_code=204)
    async def unlink_investor_account(user_id: int, account_id: int,
                                      ctx: OrgContext = Depends(require_org_role("admin")),
                                      conn: psycopg.Connection = Depends(get_conn)):
        """Remove one link; refused while an open transfer still names the
        account on either end -- the account may move to another investor
        once unlinked, and a transfer that later settles (`done`) must
        never credit or debit whoever owns it by then. The investor's
        ledger lock is taken first, the same rule every other writer that
        reads-then-decides against this investor's rows follows, so a
        transfer cannot be approved (or the account re-linked) between the
        check and the UPDATE."""
        _require_investor(conn, ctx.org_id, user_id)
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            if not pc.owns_account(conn, ctx.org_id, user_id, account_id):
                raise HTTPException(status_code=404, detail="Account not found")
            if conn.execute(
                    "SELECT 1 FROM transfers WHERE org_id = %s AND user_id = %s "
                    "AND status IN ('requested', 'approved') "
                    "AND (source_account_id = %s OR target_account_id = %s)",
                    (ctx.org_id, user_id, account_id, account_id)).fetchone():
                raise HTTPException(status_code=409, detail=(
                    "this account has open transfers; finish or reject them first"))
            row = conn.execute(
                "UPDATE accounts SET investor_user_id = NULL WHERE org_id = %s "
                "AND investor_user_id = %s AND ctid_trader_account_id = %s "
                "RETURNING ctid_trader_account_id", (ctx.org_id, user_id, account_id)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Account not found")
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_account_unlinked",
            actor_email=ctx.user_email, user_id=user_id, account_id=account_id)
        return Response(status_code=204)

    @router.get("/investors/{user_id}/wallet-entries", response_model=Dict[str, Any])
    async def investor_wallet_entries(user_id: int, wallet: Optional[str] = None,
                                      kind: Optional[str] = None,
                                      date_from: Optional[str] = Query(None, alias="from"),
                                      date_to: Optional[str] = Query(None, alias="to"),
                                      limit: int = 50, before: Optional[int] = None,
                                      ctx: OrgContext = Depends(require_org_role("admin")),
                                      conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        _require_investor(conn, ctx.org_id, user_id)
        try:
            return entries_page(conn, ctx.org_id, user_id, wallet=wallet, kind=kind,
                                date_from=date_from, date_to=date_to, limit=limit, before=before)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/investors/{user_id}/adjustments", status_code=201,
                 response_model=Dict[str, Any])
    async def post_adjustment(user_id: int, body: AdjustmentBody, http_request: Request,
                              ctx: OrgContext = Depends(require_org_role("admin")),
                              conn: psycopg.Connection = Depends(get_conn)):
        # The ADMIN's own MPIN confirms a hand-posted ledger row. Checked,
        # and the investor resolved, and the wallet/amount/note parsed,
        # all OUTSIDE any transaction -- only the INSERT itself needs the
        # per-investor ledger lock.
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        email = _require_investor(conn, ctx.org_id, user_id)
        wallet = (body.wallet or "").strip().lower()
        if wallet not in pc.WALLETS:
            raise HTTPException(status_code=400,
                                detail="wallet must be one of main, credit, pamm, social")
        raw = "" if body.amount is None or isinstance(body.amount, bool) else str(body.amount).strip()
        # One optional sign, then the number: a run of signs ('+-5', '--5')
        # must never be read as either sign. An empty amount falls through
        # to parse_amount's "is required".
        if raw and not SIGNED_AMOUNT.fullmatch(raw):
            raise HTTPException(status_code=400, detail=(
                "amount must be a signed number with at most two decimals, e.g. -25.00"))
        if raw and Decimal(raw) == 0:
            raise HTTPException(status_code=400, detail="amount must not be zero")
        negative = raw.startswith("-")
        try:
            magnitude = pc.parse_amount(raw.lstrip("+-") if raw else body.amount)
            note = pc.clean_text(body.note, "note", max_len=500)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        amount = -magnitude if negative else magnitude
        # An adjustment has no request row to reference, so it is a plain
        # insert rather than settle(): nothing to make idempotent against.
        # The per-investor ledger lock is the FIRST statement inside the
        # transaction -- keyed on the investor being adjusted, user_id,
        # never the admin posting it (ctx.user_id) -- the same rule every
        # other writer of wallet_entries follows. Nothing is awaited while
        # the lock is held.
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, user_id)
            row = conn.execute(
                "INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, note, "
                "created_by) VALUES (%s, %s, %s, %s, 'adjustment', %s, %s) "
                f"RETURNING {pc.ENTRY_COLS}",
                (ctx.org_id, user_id, wallet, amount, note, ctx.user_id)).fetchone()
        out = pc.entry_json(row)
        label = WALLET_LABELS[wallet]
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_ledger_adjusted",
            actor_email=ctx.user_email, user_id=user_id, severity="warning",
            entry_id=out["id"], wallet=wallet, amount=out["amount"], note=note,
            summary=f"Ledger adjusted: {amount:+.2f} USD on {label} of {email} by {ctx.user_email}")
        await pc.notify(
            conn, http_request, ctx.org_id, user_id, "money",
            f"Your {label} was adjusted by {amount:+.2f} USD",
            f"Wallet: {label}\nAmount: {amount:+.2f} USD\nNote: {note}\n\n"
            "Open the portal for details.",
            pc.investor_link(ctx.org_id, "transactions"))
        return out

    # ---------------------------------------------------------------- requests

    @router.get("/requests/summary", response_model=Dict[str, Any])
    async def requests_summary(ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM deposits WHERE org_id = %(o)s AND status = 'pending'),
                 (SELECT count(*) FROM withdrawals
                   WHERE org_id = %(o)s AND status IN ('requested', 'approved')),
                 (SELECT count(*) FROM transfers
                   WHERE org_id = %(o)s AND status IN ('requested', 'approved')),
                 (SELECT count(*) FROM payout_destinations
                   WHERE org_id = %(o)s AND status = 'pending'),
                 (SELECT count(*) FROM kyc_profiles WHERE org_id = %(o)s AND status = 'submitted'),
                 (SELECT count(*) FROM account_requests
                   WHERE org_id = %(o)s AND status = 'requested'),
                 (SELECT count(*) FROM tickets t
                   WHERE t.org_id = %(o)s AND """ + WAITING_ON_DESK + ")",
            {"o": ctx.org_id}).fetchone()
        counts = {"deposits": int(row[0]), "withdrawals": int(row[1]),
                  "transfers": int(row[2]), "payout_destinations": int(row[3]),
                  "kyc": int(row[4]), "account_requests": int(row[5]),
                  "tickets": int(row[6])}
        return {**counts, "total": sum(counts.values())}

    return router
