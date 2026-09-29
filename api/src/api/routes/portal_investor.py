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
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import LoginRateLimiter
from ..db import get_conn
from ..mpin_core import require_mpin
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc
from .portal_files import file_belongs

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 10
RATE_LIMITED = "too many requests; try again later"


class DepositNotice(BaseModel):
    method_id: int
    amount: Any
    reference: str
    receipt_file_id: Optional[int] = None
    target: str = "wallet"
    target_account_id: Optional[int] = None
    note: Optional[str] = None


class DestinationBody(BaseModel):
    kind: str
    nickname: str
    details: dict
    proof_file_id: Optional[int] = None
    mpin: Any = None


class WithdrawalRequest(BaseModel):
    destination_id: int
    amount: Any
    mpin: Any = None


class MoneyRef(BaseModel):
    kind: str
    wallet: Optional[str] = None
    account_id: Optional[int] = None


class TransferRequest(BaseModel):
    source: MoneyRef
    target: MoneyRef
    amount: Any
    mpin: Any = None


BANK_REQUIRED = ("bank_name", "holder", "account_number", "code")
BANK_OPTIONAL = ("bank_address", "country")
CRYPTO_REQUIRED = ("coin", "network", "address")


def clean_destination_details(kind: str, details: object) -> dict:
    """Keep only the keys a destination of this kind carries, each trimmed
    and bounded. A missing required key raises LedgerError('<key> is
    required'), which the route turns into the 400 the dashboard shows
    under that field."""
    raw = details if isinstance(details, dict) else {}
    out: Dict[str, str] = {}
    required = BANK_REQUIRED if kind == "bank" else CRYPTO_REQUIRED
    for key in required:
        out[key] = pc.clean_text(raw.get(key), key, max_len=128)
    if kind == "bank":
        for key in BANK_OPTIONAL:
            value = pc.clean_text(raw.get(key), key, max_len=256, required=False)
            if value is not None:
                out[key] = value
    return out


WALLET_LABELS: Dict[str, str] = {
    "main": "My wallet", "credit": "Credit wallet", "pamm": "PAMM wallet",
    "social": "Social wallet"}


def money_ref_label(kind: str, account_id: Optional[int]) -> str:
    """'My wallet' / 'PAMM wallet' / … or 'trading account <id>', for audit
    summaries and investor emails."""
    if kind == "account":
        return f"trading account {account_id}"
    return WALLET_LABELS.get(kind, kind)


def create_portal_investor_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-investor"])
    # Ten money requests of each kind per investor per hour, keyed
    # portal-<kind>:<org>:<user>. A separate instance because the shared
    # login limiter's window is one minute.
    hourly = LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)

    @router.get("/investor/payment-methods", response_model=List[Dict[str, Any]])
    async def my_payment_methods(ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        """Where this workspace receives money: enabled methods only, in
        the admin's display order."""
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s AND enabled "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=True) for r in rows]

    # ------------------------------------------------------------ deposits

    @router.get("/investor/deposits", response_model=List[Dict[str, Any]])
    async def my_deposits(ctx: OrgContext = Depends(require_org_role("investor")),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.DEPOSIT_COLS} FROM deposits WHERE org_id = %s AND user_id = %s "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [pc.deposit_json(r) for r in rows]

    @router.post("/investor/deposits", status_code=201, response_model=Dict[str, Any])
    async def file_deposit(body: DepositNotice,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """'I have sent it': a notice against one enabled payment method.
        The method's kind and label are snapshotted and its fee_pct applied
        now, so a later edit of the method never rewrites history. Nothing
        moves until an admin confirms."""
        try:
            amount = pc.parse_amount(body.amount)
            reference = pc.clean_text(body.reference, "reference")
            note = pc.clean_text(body.note, "note", max_len=500, required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        target = (body.target or "wallet").strip().lower()
        if target not in ("wallet", "account"):
            raise HTTPException(status_code=400, detail="target must be wallet or account")
        method = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods "
            "WHERE id = %s AND org_id = %s AND enabled", (body.method_id, ctx.org_id)).fetchone()
        if not method:
            raise HTTPException(status_code=404, detail="Payment method not found")
        (method_id, kind, label, _enabled, _currency, _details, min_amount, fee_pct,
         _instructions, _sort_order) = method
        min_amount = Decimal(min_amount)
        if amount < min_amount:
            raise HTTPException(status_code=400,
                                detail=f"minimum deposit for this method is {min_amount:.2f}")
        if kind == "bank" and body.receipt_file_id is None:
            raise HTTPException(status_code=400, detail="receipt is required for bank deposits")
        if body.receipt_file_id is not None:
            # The specific "already attached" refusal has to be checked
            # BEFORE the generic file_belongs gate: file_belongs itself
            # excludes any file already referenced by a deposit (spec
            # section 9, enforced there for every other caller too), so
            # checking it first would only ever surface the generic
            # "not found" message and this one would be unreachable.
            #
            # The probe is scoped to the CALLER'S OWN file (org, user,
            # purpose): an unscoped "does this id belong to any deposit
            # anywhere" query would be a yes/no oracle any investor could
            # run over sequential file ids across every org's receipts, so
            # a foreign or another investor's file must fall through to the
            # generic "not found" message just like a nonexistent one.
            used = conn.execute(
                "SELECT 1 FROM deposits d JOIN files f ON f.id = d.receipt_file_id "
                "WHERE d.receipt_file_id = %s AND f.org_id = %s AND f.user_id = %s "
                "AND f.purpose = %s",
                (body.receipt_file_id, ctx.org_id, ctx.user_id, "deposit_receipt")).fetchone()
            if used:
                raise HTTPException(
                    status_code=400, detail="receipt file is already attached to another notice")
            if not file_belongs(conn, ctx.org_id, ctx.user_id, body.receipt_file_id,
                                "deposit_receipt"):
                raise HTTPException(status_code=400, detail="receipt file not found")
        target_account_id: Optional[int] = None
        if target == "account":
            linked = pc.linked_account(conn, ctx.org_id, ctx.user_id)
            if linked is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            if body.target_account_id is not None and body.target_account_id != linked:
                raise HTTPException(status_code=404, detail="Account not found")
            target_account_id = linked
        if hourly.is_limited(f"portal-deposit:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        fee = pc.fee_for(amount, Decimal(fee_pct))
        try:
            row = conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, "
                "amount, fee, reference, receipt_file_id, target, target_account_id, note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {pc.DEPOSIT_COLS}",
                (ctx.org_id, ctx.user_id, method_id, kind, label, amount, fee, reference,
                 body.receipt_file_id, target, target_account_id, note)).fetchone()
        except psycopg.errors.UniqueViolation:
            # deposits_one_live_reference: the same transaction is already
            # pending or confirmed in this workspace (possibly another
            # investor's). Two rows for one transfer is the same money
            # counted twice. Rejected and cancelled rows are not in the
            # index, so a re-file after a mistake still works.
            raise HTTPException(status_code=409,
                                detail="A notice with this reference already exists")
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_noticed",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            account_id=target_account_id, deposit_id=out["id"], amount=out["amount"],
            fee=out["fee"], method_id=method_id, method_label=label, reference=reference,
            target=target,
            summary=f"Deposit notice: {amount:.2f} USD via {label} from {ctx.user_email}")
        return out

    @router.post("/investor/deposits/{deposit_id}/cancel", response_model=Dict[str, Any])
    async def cancel_deposit(deposit_id: int,
                             ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """The investor's own move, allowed only while pending. The row is
        kept as cancelled so the history stays whole; the reference is
        free again."""
        current = conn.execute(
            "SELECT status FROM deposits WHERE id = %s AND org_id = %s AND user_id = %s",
            (deposit_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        if not pc.can_transition("deposits", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"deposit is already {current[0]}")
        row = conn.execute(
            "UPDATE deposits SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = 'pending' "
            f"RETURNING {pc.DEPOSIT_COLS}", (ctx.user_id, deposit_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            account_id=out["target_account_id"], deposit_id=deposit_id, amount=out["amount"],
            reference=out["reference"],
            summary=f"Deposit notice #{deposit_id} cancelled by {ctx.user_email}")
        return out

    # ------------------------------------------------- payout destinations

    @router.get("/investor/payout-destinations", response_model=List[Dict[str, Any]])
    async def my_destinations(ctx: OrgContext = Depends(require_org_role("investor")),
                              conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.DESTINATION_COLS} FROM payout_destinations "
            "WHERE org_id = %s AND user_id = %s AND status <> 'removed' "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [pc.destination_json(r, full=True) for r in rows]

    @router.post("/investor/payout-destinations", status_code=201, response_model=Dict[str, Any])
    async def add_destination(body: DestinationBody,
                              ctx: OrgContext = Depends(require_org_role("investor")),
                              conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        kind = (body.kind or "").strip().lower()
        if kind not in ("bank", "crypto"):
            raise HTTPException(status_code=400, detail="kind must be bank or crypto")
        try:
            nickname = pc.clean_text(body.nickname, "nickname", max_len=64)
            details = clean_destination_details(kind, body.details)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if body.proof_file_id is not None and not file_belongs(
                conn, ctx.org_id, ctx.user_id, body.proof_file_id, "payout_proof"):
            raise HTTPException(status_code=400, detail="proof file not found")
        if hourly.is_limited(f"portal-destination:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        row = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, "
            "proof_file_id) VALUES (%s, %s, %s, %s, %s, %s) "
            f"RETURNING {pc.DESTINATION_COLS}",
            (ctx.org_id, ctx.user_id, kind, nickname, Jsonb(details),
             body.proof_file_id)).fetchone()
        out = pc.destination_json(row, full=True)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_destination_added",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            destination_id=out["id"], kind=kind, destination=out["summary"],
            summary=f"Payout account added: {out['summary']} by {ctx.user_email}")
        return out

    @router.post("/investor/payout-destinations/{dest_id}/remove", response_model=Dict[str, Any])
    async def remove_destination(dest_id: int,
                                 ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status FROM payout_destinations WHERE id = %s AND org_id = %s AND user_id = %s",
            (dest_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Payout account not found")
        if not pc.can_transition("payout_destinations", current[0], "removed"):
            raise HTTPException(status_code=409,
                                detail=f"payout account is already {current[0]}")
        in_use = conn.execute(
            "SELECT 1 FROM withdrawals WHERE destination_id = %s "
            "AND status IN ('requested', 'approved') LIMIT 1", (dest_id,)).fetchone()
        if in_use:
            raise HTTPException(status_code=409,
                                detail="a withdrawal is still using this payout account")
        row = conn.execute(
            "UPDATE payout_destinations SET status = 'removed', decided_by = %s, "
            "decided_at = now() WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {pc.DESTINATION_COLS}",
            (ctx.user_id, dest_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.destination_json(row, full=True)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_destination_removed",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            destination_id=dest_id, destination=out["summary"])
        return out

    # ------------------------------------------------------------ withdrawals

    @router.get("/investor/withdrawals", response_model=List[Dict[str, Any]])
    async def my_withdrawals(ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.WITHDRAWAL_COLS} FROM withdrawals "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [pc.withdrawal_json(r) for r in rows]

    @router.post("/investor/withdrawals", status_code=201, response_model=Dict[str, Any])
    async def request_withdrawal(body: WithdrawalRequest,
                                 ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        try:
            amount = pc.parse_amount(body.amount)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        dest = conn.execute(
            "SELECT kind, details FROM payout_destinations "
            "WHERE id = %s AND org_id = %s AND user_id = %s AND status = 'approved'",
            (body.destination_id, ctx.org_id, ctx.user_id)).fetchone()
        if not dest:
            raise HTTPException(status_code=404, detail="Payout account not found")
        settings = pc.portal_settings(conn, ctx.org_id)
        if amount < settings["withdrawal_min"]:
            raise HTTPException(
                status_code=400,
                detail=f"minimum withdrawal is {settings['withdrawal_min']:.2f}")
        # Everything from the available read through the INSERT runs inside
        # ONE transaction, serialised by the per-investor advisory lock:
        # otherwise two concurrent requests could both read the same
        # available figure, both pass the cap and over-commit the wallet.
        # The cap itself is the same floored figure the summary reports, so
        # the dashboard's "Use max" can never be refused for rounding.
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, ctx.user_id)
            available = pc.wallet_figures(conn, ctx.org_id, ctx.user_id)["main"]["available"]
            if amount > available:
                raise HTTPException(
                    status_code=400,
                    detail=f"amount exceeds what is available ({available:.2f})")
            fee = pc.fee_for(amount, settings["withdrawal_fee_pct"])
            net = amount - fee
            if net <= 0:
                raise HTTPException(status_code=400, detail="amount is too small to cover the fee")
            if hourly.is_limited(f"portal-withdrawal:{ctx.org_id}:{ctx.user_id}"):
                raise HTTPException(status_code=429, detail=RATE_LIMITED)
            summary = pc.destination_summary(dest[0], dest[1])
            row = conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {pc.WITHDRAWAL_COLS}",
                (ctx.org_id, ctx.user_id, body.destination_id, dest[0], summary, amount, fee,
                 net)).fetchone()
        out = pc.withdrawal_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_requested",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            withdrawal_id=out["id"], amount=out["amount"], fee=out["fee"],
            net_amount=out["net_amount"], destination=summary,
            summary=f"Withdrawal request: {amount:.2f} USD to {summary} from {ctx.user_email}")
        return out

    @router.post("/investor/withdrawals/{wd_id}/cancel", response_model=Dict[str, Any])
    async def cancel_withdrawal(wd_id: int,
                                ctx: OrgContext = Depends(require_org_role("investor")),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status, amount FROM withdrawals WHERE id = %s AND org_id = %s AND user_id = %s",
            (wd_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if not pc.can_transition("withdrawals", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"withdrawal is already {current[0]}")
        row = conn.execute(
            "UPDATE withdrawals SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {pc.WITHDRAWAL_COLS}",
            (ctx.user_id, wd_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.withdrawal_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            withdrawal_id=wd_id, amount=out["amount"])
        return out

    # -------------------------------------------------------------- transfers

    @router.get("/investor/transfers", response_model=List[Dict[str, Any]])
    async def my_transfers(ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.TRANSFER_COLS} FROM transfers "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [pc.transfer_json(r) for r in rows]

    @router.post("/investor/transfers", status_code=201, response_model=Dict[str, Any])
    async def request_transfer(body: TransferRequest, http_request: Request,
                               ctx: OrgContext = Depends(require_org_role("investor")),
                               conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        try:
            amount = pc.parse_amount(body.amount)
            source_kind, target_kind = pc.transfer_pair(body.source.model_dump(),
                                                         body.target.model_dump())
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        account_id: Optional[int] = None
        if "account" in (source_kind, target_kind):
            account_id = pc.linked_account(conn, ctx.org_id, ctx.user_id)
            if account_id is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            named = body.source.account_id if source_kind == "account" else body.target.account_id
            if named != account_id:
                raise HTTPException(status_code=404, detail="Account not found")
        # The equity lookup is a network round trip to the copier and must
        # run BEFORE the lock is taken: nothing may be awaited while it is
        # held (lock_investor_ledger's docstring). The cap check itself
        # moves inside the locked transaction below so it reads the same
        # snapshot the INSERT commits against.
        equity: Optional[Decimal] = None
        equity_source = "unknown"
        if source_kind == "account":
            equity, equity_source, _positions = await pc.equity_for(
                http_request, conn, ctx.org_id, account_id)
        instant = source_kind != "account" and target_kind != "account"
        source_wallet = None if source_kind == "account" else source_kind
        target_wallet = None if target_kind == "account" else target_kind
        # Wallet-to-wallet moves need no admin: inserted as done and settled
        # in the same transaction. Account moves wait for the admin. The
        # per-investor ledger lock is taken FIRST inside the transaction, as
        # every writer of wallet_entries must: otherwise a concurrent
        # request could read the same available/equity figure, both pass
        # their cap and over-commit the wallet or the account.
        with conn.transaction():
            pc.lock_investor_ledger(conn, ctx.org_id, ctx.user_id)
            if source_kind == "account":
                if equity is not None:
                    account_available = pc.floor_cents(
                        equity - pc.open_account_transfers_out(
                            conn, ctx.org_id, ctx.user_id, account_id))
                    if amount > account_available:
                        raise HTTPException(
                            status_code=400,
                            detail="amount exceeds the account's available equity "
                                   f"({account_available:.2f})")
            else:
                available = pc.wallet_figures(conn, ctx.org_id, ctx.user_id)[source_kind]["available"]
                if amount > available:
                    raise HTTPException(
                        status_code=400,
                        detail=f"amount exceeds what is available ({available:.2f})")
            if hourly.is_limited(f"portal-transfer:{ctx.org_id}:{ctx.user_id}"):
                raise HTTPException(status_code=429, detail=RATE_LIMITED)
            row = conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                "source_account_id, target_kind, target_wallet, target_account_id, amount, "
                "status, equity_at_request, equity_verified, done_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "CASE WHEN %s THEN now() END) "
                f"RETURNING {pc.TRANSFER_COLS}",
                (ctx.org_id, ctx.user_id,
                 "wallet" if source_wallet else "account", source_wallet,
                 account_id if source_kind == "account" else None,
                 "wallet" if target_wallet else "account", target_wallet,
                 account_id if target_kind == "account" else None,
                 amount, "done" if instant else "requested", equity,
                 equity_source == "live", instant)).fetchone()
            out = pc.transfer_json(row)
            if instant:
                pc.settle(conn, org_id=ctx.org_id, user_id=ctx.user_id, wallet=source_wallet,
                         amount=-amount, kind="transfer", ref_table="transfers",
                         ref_id=out["id"])
                pc.settle(conn, org_id=ctx.org_id, user_id=ctx.user_id, wallet=target_wallet,
                         amount=amount, kind="transfer", ref_table="transfers",
                         ref_id=out["id"])
        from_label = money_ref_label(source_kind, account_id)
        to_label = money_ref_label(target_kind, account_id)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_transfer_requested",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            severity="info" if instant else "warning",
            account_id=account_id, transfer_id=out["id"], amount=out["amount"],
            source=from_label, target=to_label, instant=instant,
            equity_verified=out["equity_verified"],
            summary=f"Transfer request: {amount:.2f} USD from {from_label} to {to_label} "
                    f"from {ctx.user_email}")
        return out

    @router.post("/investor/transfers/{tr_id}/cancel", response_model=Dict[str, Any])
    async def cancel_transfer(tr_id: int,
                              ctx: OrgContext = Depends(require_org_role("investor")),
                              conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status FROM transfers WHERE id = %s AND org_id = %s AND user_id = %s",
            (tr_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Transfer not found")
        if not pc.can_transition("transfers", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"transfer is already {current[0]}")
        row = conn.execute(
            "UPDATE transfers SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {pc.TRANSFER_COLS}",
            (ctx.user_id, tr_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.transfer_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_transfer_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            transfer_id=tr_id, amount=out["amount"])
        return out

    return router
