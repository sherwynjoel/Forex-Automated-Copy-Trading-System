# api/src/api/portal_common.py
"""Shared plumbing for the client-portal routers (phase 1).

Everything the investor and admin routers need in common lives here so the
two route files stay about their routes: the ledger figures read from the
database, the ONE idempotent settlement writer, the linked-account and
equity resolvers moved from routes/investor.py, audit and email, the
per-org portal settings row, and the row serialisers. Pure money rules
(parsing, rounding, fees, state machines) live in portal_ledger and are
re-exported from here so a router imports a single module.

The app never holds keys and never moves money: every transfer of value
happens outside it and is recorded, approved and reconciled here.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

import psycopg
from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .config import ApiConfig
from .portal_ledger import *  # noqa: F401,F403  -- re-exported for the routers
from .portal_ledger import WALLETS, available, balance, clean_text, holds, money
from .routes.mt5 import MT5_OFFLINE_AFTER_S
from .routes.settings_control import _proxy_to_copier
from .ws import broadcaster

logger = logging.getLogger(__name__)

CURRENCY = "USD"

OPEN_WITHDRAWAL_STATUSES = ("requested", "approved")
OPEN_TRANSFER_STATUSES = ("requested", "approved")


# ------------------------------------------------------------ audit + email


async def audit_control(conn: psycopg.Connection, *, org_id: int, action: str,
                        actor_email: str, user_id: int, severity: str = "info",
                        account_id: Optional[int] = None, **detail: Any) -> None:
    """One `events` row per state change (category control). Warnings reach
    the admin email and Telegram channels through ALERT_RULES and
    TELEGRAM_RULES; info rows only drive the live refresh. `user_id` is the
    investor the row is ABOUT so the alerters cool down per investor.
    Best effort: a failed audit write is logged, never surfaced as a failed
    request."""
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, payload, actor_email) "
            "VALUES (%s, %s, 'control', %s, %s, %s)",
            (org_id, account_id, severity,
             Jsonb({"action": action, "user_id": user_id, **detail}), actor_email))
    except Exception:
        logger.exception("failed to write portal audit event %s", action)


async def notify_investor(conn: psycopg.Connection, request: Request, user_id: int,
                          subject: str, text: str) -> None:
    """Best-effort email to the investor's own address. The alerter lives on
    the event broadcaster (main.py wires it at startup); with none
    configured this is a no-op, and a failing send never fails the
    request that triggered it."""
    alerter = getattr(broadcaster, "alerter", None)
    if alerter is None:
        alerter = getattr(request.app.state, "alerter", None)
    if alerter is None:
        return
    row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return
    try:
        await alerter.send_to(row[0], subject, text)
    except Exception:
        logger.exception("investor email failed for user %s", user_id)


# ------------------------------------------------------------ accounts + equity


def linked_account(conn: psycopg.Connection, org_id: int, user_id: int) -> Optional[int]:
    """The ONE trading account linked to this investor in this workspace
    (accounts.investor_user_id), or None while unlinked."""
    row = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts "
        "WHERE org_id = %s AND investor_user_id = %s", (org_id, user_id)).fetchone()
    return int(row[0]) if row else None


def account_card(conn: psycopg.Connection, org_id: int, account_id: int) -> dict:
    """The account card the investor summary shows (moved from
    routes/investor.py). 404 when the account is not this org's."""
    row = conn.execute(
        """SELECT a.nickname, a.platform, a.status, a.last_error,
                  COALESCE(l.last_seen_at > now() - make_interval(secs => %s), false),
                  c.status
           FROM accounts a
           LEFT JOIN mt5_links l ON l.account_id = a.ctid_trader_account_id
           LEFT JOIN ctid_connections c ON a.ctid_connection_id = c.id
           WHERE a.ctid_trader_account_id = %s AND a.org_id = %s""",
        (MT5_OFFLINE_AFTER_S, account_id, org_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    connected = bool(row[4]) if row[1] == "mt5" else row[5] == "active"
    return {"account_id": account_id, "nickname": row[0], "platform": row[1],
            "status": row[2], "last_error": row[3], "connected": connected}


async def org_state(client, cfg: ApiConfig, org_id: int) -> Optional[dict]:
    """One /state round trip for the whole org. None when the copier is
    down or answers with something that isn't a JSON object -- callers
    then fall back to last-known equity for every account, not just one."""
    try:
        state = await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/state?org_id={org_id}", method="GET")
    except HTTPException:
        return None
    return state if isinstance(state, dict) else None


def equity_from(state: Optional[dict], conn: psycopg.Connection,
                account_id: int) -> tuple[Optional[Decimal], str, list]:
    """Live equity from an already-fetched /state snapshot, else the last
    equity the MT5 terminal reported, else unknown."""
    accounts = state.get("accounts") if isinstance(state, dict) else None
    entry = accounts.get(str(account_id)) if isinstance(accounts, dict) else None
    if isinstance(entry, dict) and entry.get("equity") is not None:
        return Decimal(str(entry["equity"])), "live", list(entry.get("positions") or [])
    row = conn.execute("SELECT equity FROM mt5_links WHERE account_id = %s",
                       (account_id,)).fetchone()
    if row and row[0] is not None:
        return Decimal(str(row[0])), "last known", []
    return None, "unknown", []


async def equity_for(request: Request, conn: psycopg.Connection, org_id: int,
                     account_id: Optional[int]) -> tuple[Optional[Decimal], str, list]:
    """(equity, source, positions) for one account: 'live' from the copier,
    'last known' from mt5_links, 'unknown' otherwise. None account_id (no
    link) is simply unknown. Never raises for an unreachable copier."""
    if account_id is None:
        return None, "unknown", []
    state = await org_state(request.app.state.http, ApiConfig.from_env(), org_id)
    return equity_from(state, conn, account_id)


# ------------------------------------------------------------ wallet figures


def wallet_balances(conn: psycopg.Connection, org_id: int, user_id: int) -> dict[str, Decimal]:
    """Sum of every wallet's entries; every WALLETS key present."""
    rows = conn.execute(
        "SELECT wallet, SUM(amount) FROM wallet_entries "
        "WHERE org_id = %s AND user_id = %s GROUP BY wallet", (org_id, user_id)).fetchall()
    summed = balance([(w, Decimal(a)) for w, a in rows])
    return {w: summed.get(w, Decimal("0")) for w in WALLETS}


def wallet_holds(conn: psycopg.Connection, org_id: int, user_id: int) -> dict[str, Decimal]:
    """Money spoken for by open requests: withdrawals in requested/approved
    hold on main; transfers in requested/approved hold on their source
    wallet."""
    withdrawals = [Decimal(a) for (a,) in conn.execute(
        "SELECT amount FROM withdrawals WHERE org_id = %s AND user_id = %s "
        "AND status = ANY(%s)", (org_id, user_id, list(OPEN_WITHDRAWAL_STATUSES))).fetchall()]
    transfers = [(w, Decimal(a)) for w, a in conn.execute(
        "SELECT source_wallet, amount FROM transfers WHERE org_id = %s AND user_id = %s "
        "AND source_kind = 'wallet' AND status = ANY(%s)",
        (org_id, user_id, list(OPEN_TRANSFER_STATUSES))).fetchall()]
    held = holds(withdrawals, transfers)
    return {w: held.get(w, Decimal("0")) for w in WALLETS}


def wallet_figures(conn: psycopg.Connection, org_id: int,
                   user_id: int) -> dict[str, dict[str, Decimal]]:
    """{wallet: {balance, on_hold, available}} with available FLOORED to
    cents -- the same Decimal every cap check compares against."""
    balances = wallet_balances(conn, org_id, user_id)
    held = wallet_holds(conn, org_id, user_id)
    return {w: {"balance": balances[w], "on_hold": held[w],
                "available": available(balances[w], held[w])} for w in WALLETS}


def open_account_transfers_out(conn: psycopg.Connection, org_id: int, user_id: int,
                               account_id: int) -> Decimal:
    """Requested + approved account->wallet transfers: equity already spoken for."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transfers WHERE org_id = %s AND user_id = %s "
        "AND source_kind = 'account' AND source_account_id = %s AND status = ANY(%s)",
        (org_id, user_id, account_id, list(OPEN_TRANSFER_STATUSES))).fetchone()
    return Decimal(total)


def net_funded(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int) -> Decimal:
    """Done wallet->account minus done account->wallet for the linked
    account: what the investor has put into the broker account, net."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN target_kind = 'account' AND target_account_id = %s THEN amount "
        "                        WHEN source_kind = 'account' AND source_account_id = %s THEN -amount "
        "                        ELSE 0 END), 0) "
        "FROM transfers WHERE org_id = %s AND user_id = %s AND status = 'done'",
        (account_id, account_id, org_id, user_id)).fetchone()
    return Decimal(total)


# ------------------------------------------------------------ settlement


def settle(conn: psycopg.Connection, *, org_id: int, user_id: int, wallet: str, amount: Decimal,
           kind: str, ref_table: Optional[str], ref_id: Optional[int],
           note: Optional[str] = None, created_by: Optional[int] = None) -> bool:
    """The only writer of wallet_entries. Called inside the transaction that
    changes a request's status. The partial unique index
    wallet_entries_one_per_ref (ref_table, ref_id, wallet) makes a retried
    settlement a no-op, so a double click or a replayed request cannot
    double-credit. Returns True when a row was written. Adjustments carry
    no reference and are never deduplicated."""
    row = conn.execute(
        "INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id, "
        "note, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ref_table, ref_id, wallet) WHERE ref_table IS NOT NULL DO NOTHING "
        "RETURNING id",
        (org_id, user_id, wallet, amount, kind, ref_table, ref_id, note, created_by)).fetchone()
    return row is not None


# ------------------------------------------------------------ settings


def portal_settings(conn: psycopg.Connection, org_id: int) -> dict:
    """{withdrawal_min, withdrawal_fee_pct} as Decimals; the row is created
    with the zero defaults on first read."""
    conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s) ON CONFLICT (org_id) DO NOTHING",
                 (org_id,))
    row = conn.execute(
        "SELECT withdrawal_min, withdrawal_fee_pct FROM portal_settings WHERE org_id = %s",
        (org_id,)).fetchone()
    return {"withdrawal_min": Decimal(row[0]), "withdrawal_fee_pct": Decimal(row[1])}


# ------------------------------------------------------------ text helpers


def short_address(a: str) -> str:
    return a if len(a) <= 8 else f"{a[0]}…{a[-2:]}"


def destination_summary(kind: str, details: dict) -> str:
    """bank -> "<bank_name> ••<last 4>", crypto -> "<network> <shortAddress>"."""
    details = details or {}
    if kind == "bank":
        number = str(details.get("account_number") or "")
        return f"{details.get('bank_name') or ''} ••{number[-4:]}".strip()
    return f"{details.get('network') or ''} {short_address(str(details.get('address') or ''))}".strip()


def qualify(cols: str, alias: str) -> str:
    """'id, user_id' -> 'd.id, d.user_id' for the admin joins."""
    return ", ".join(f"{alias}.{c.strip()}" for c in cols.split(","))


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ------------------------------------------------------------ serialisers


DEPOSIT_COLS = ("id, user_id, method_id, method_kind, method_label, amount, fee, credited_amount, "
                "reference, receipt_file_id, target, target_account_id, note, status, decided_by, "
                "decided_at, decision_note, created_at")
WITHDRAWAL_COLS = ("id, user_id, destination_id, destination_kind, destination_summary, amount, "
                   "fee, net_amount, status, decided_by, decided_at, decision_note, paid_by, "
                   "paid_at, txid, created_at")
TRANSFER_COLS = ("id, user_id, source_kind, source_wallet, source_account_id, target_kind, "
                 "target_wallet, target_account_id, amount, status, equity_at_request, "
                 "equity_verified, decided_by, decided_at, decision_note, done_by, done_at, "
                 "note, created_at")
DESTINATION_COLS = ("id, user_id, kind, nickname, details, proof_file_id, status, decided_by, "
                    "decided_at, decision_note, created_at")
ENTRY_COLS = "id, wallet, amount, kind, ref_table, ref_id, note, created_at"
METHOD_COLS = ("id, kind, label, enabled, currency, details, min_amount, fee_pct, instructions, "
               "sort_order")


def _with_person(out: dict, row, width: int) -> dict:
    """Admin queries append u.email, u.display_name after the row columns."""
    if len(row) > width:
        out["email"], out["display_name"] = row[width], row[width + 1]
    return out


def deposit_json(row) -> dict:
    (dep_id, user_id, method_id, method_kind, method_label, amount, fee, credited, reference,
     receipt_file_id, target, target_account_id, note, status, decided_by, decided_at,
     decision_note, created_at) = row[:18]
    out = {"id": dep_id, "user_id": user_id, "method_id": method_id,
           "method_kind": method_kind, "method_label": method_label,
           "amount": money(amount), "fee": money(fee), "credited_amount": money(credited),
           "reference": reference, "receipt_file_id": receipt_file_id, "target": target,
           "target_account_id": int(target_account_id) if target_account_id is not None else None,
           "note": note, "status": status, "decided_by": decided_by,
           "decided_at": _iso(decided_at), "decision_note": decision_note,
           "created_at": _iso(created_at), "currency": CURRENCY}
    return _with_person(out, row, 18)


def withdrawal_json(row) -> dict:
    (wd_id, user_id, destination_id, destination_kind, destination_summary_, amount, fee,
     net_amount, status, decided_by, decided_at, decision_note, paid_by, paid_at, txid,
     created_at) = row[:16]
    out = {"id": wd_id, "user_id": user_id, "destination_id": destination_id,
           "destination_kind": destination_kind, "destination_summary": destination_summary_,
           "amount": money(amount), "fee": money(fee), "net_amount": money(net_amount),
           "status": status, "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "paid_by": paid_by, "paid_at": _iso(paid_at),
           "txid": txid, "created_at": _iso(created_at), "currency": CURRENCY}
    return _with_person(out, row, 16)


def _money_ref(kind: str, wallet: Optional[str], account_id: Optional[int]) -> dict:
    if kind == "wallet":
        return {"kind": "wallet", "wallet": wallet}
    return {"kind": "account", "account_id": int(account_id) if account_id is not None else None}


def transfer_json(row) -> dict:
    (tr_id, user_id, source_kind, source_wallet, source_account_id, target_kind, target_wallet,
     target_account_id, amount, status, equity_at_request, equity_verified, decided_by,
     decided_at, decision_note, done_by, done_at, note, created_at) = row[:19]
    out = {"id": tr_id, "user_id": user_id,
           "source": _money_ref(source_kind, source_wallet, source_account_id),
           "target": _money_ref(target_kind, target_wallet, target_account_id),
           "amount": money(amount), "status": status,
           "equity_at_request": money(equity_at_request),
           "equity_verified": bool(equity_verified), "decided_by": decided_by,
           "decided_at": _iso(decided_at), "decision_note": decision_note, "done_by": done_by,
           "done_at": _iso(done_at), "note": note, "created_at": _iso(created_at),
           "currency": CURRENCY}
    return _with_person(out, row, 19)


def destination_json(row, *, full: bool) -> dict:
    """full=False masks a bank account number to its last four digits: the
    owner and admins see it whole, nobody else ever sees the row at all."""
    (dest_id, user_id, kind, nickname, details, proof_file_id, status, decided_by, decided_at,
     decision_note, created_at) = row[:11]
    details = dict(details or {})
    summary = destination_summary(kind, details)
    if not full and kind == "bank" and details.get("account_number"):
        details["account_number"] = "••" + str(details["account_number"])[-4:]
    out = {"id": dest_id, "user_id": user_id, "kind": kind, "nickname": nickname,
           "details": details, "proof_file_id": proof_file_id, "status": status,
           "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "created_at": _iso(created_at), "summary": summary}
    return _with_person(out, row, 11)


def entry_json(row) -> dict:
    entry_id, wallet, amount, kind, ref_table, ref_id, note, created_at = row[:8]
    return {"id": entry_id, "wallet": wallet, "amount": money(amount), "kind": kind,
            "ref_table": ref_table, "ref_id": ref_id, "note": note,
            "created_at": _iso(created_at), "currency": CURRENCY}


def method_json(row, *, public: bool) -> dict:
    """One shape for admins and investors (the interfaces doc: public omits
    nothing; what protects investors is that disabled rows are never
    listed to them). `public` documents the caller's intent."""
    (method_id, kind, label, enabled, currency, details, min_amount, fee_pct, instructions,
     sort_order) = row[:10]
    return {"id": method_id, "kind": kind, "label": label, "enabled": bool(enabled),
            "currency": currency, "details": dict(details or {}),
            "min_amount": money(min_amount), "fee_pct": float(fee_pct),
            "instructions": instructions, "sort_order": sort_order}


# ------------------------------------------------------------ decisions


class Decision(BaseModel):
    status: str
    note: Optional[str] = None


def require_note_on_reject(status: str, note: Optional[str]) -> Optional[str]:
    """A rejection must say why; any other decision may carry a note."""
    return clean_text(note, "note", max_len=500, required=(status == "rejected"))
