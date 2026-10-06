# api/src/api/portal_common.py
"""Shared plumbing for the client-portal routers (phase 1).

Everything the investor and admin routers need in common lives here so the
two route files stay about their routes: the ledger figures read from the
database, the ONE idempotent settlement writer, the account ownership and
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
from .portal_ledger import (WALLETS, available, balance, clean_text, deposit_bonus,
                            floor_cents, holds, money)
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


TOPICS = ("money", "identity", "support", "bonus")
TITLE_MAX = 120
BODY_MAX = 500


def clip(text: str, limit: int) -> str:
    """At most `limit` characters; an ellipsis marks a cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def investor_link(org_id: int, page: str) -> str:
    """The in-app path of an investor page, e.g. investor_link(7, "deposit")."""
    return f"/org/{org_id}/invest/{page}"


def email_wanted(conn: psycopg.Connection, org_id: int, user_id: int, topic: str) -> bool:
    """The user's email switch for `topic` in this org; no prefs row means
    every switch is on. `topic` names a column, so it is checked first."""
    if topic not in TOPICS:
        raise ValueError(f"unknown notification topic {topic!r}")
    row = conn.execute(f"SELECT {topic} FROM notification_prefs WHERE org_id = %s AND user_id = %s",
                       (org_id, user_id)).fetchone()
    return row is None or bool(row[0])


async def notify(conn: psycopg.Connection, request: Request, org_id: int, user_id: int,
                 topic: str, title: str, body: str, link: Optional[str] = None) -> None:
    """One in-app notification for `user_id` in `org_id`, then the same words
    by email (subject = title, text = body) unless their switch for `topic`
    is off. Title and body are clipped to the column limits for both.

    Best effort: the WHOLE body -- checking the switch, the INSERT, finding
    the alerter and the user's address, and the send -- is one try/except,
    so a failure anywhere is logged and never fails the request that
    triggered it. Callers run it AFTER their transaction, never while
    holding the ledger lock."""
    try:
        title, body = clip(title, TITLE_MAX), clip(body, BODY_MAX)
        conn.execute(
            "INSERT INTO notifications (org_id, user_id, topic, title, body, link) "
            "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, user_id, topic, title, body, link))
        if not email_wanted(conn, org_id, user_id, topic):
            return
        alerter = getattr(broadcaster, "alerter", None)
        if alerter is None:
            alerter = getattr(request.app.state, "alerter", None)
        if alerter is None:
            return
        row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
        if not row:
            return
        await alerter.send_to(row[0], title, body)
    except Exception:
        logger.exception("notify failed for user %s in org %s (topic %s)", user_id, org_id, topic)


async def notify_admins(conn: psycopg.Connection, request: Request, org_id: int, topic: str,
                        title: str, body: str, link: Optional[str] = None) -> None:
    """notify() every admin member of the org (the support desk). Best
    effort, like notify(): the whole body is one try/except, so a failure
    (listing the admins, or notifying one of them) is logged and never
    fails the request that triggered it.

    ponytail: the admins are emailed sequentially, inside this request --
    move this to a background task if an org ever has many admins."""
    try:
        for (admin_id,) in conn.execute(
                "SELECT user_id FROM org_memberships WHERE org_id = %s AND role = 'admin' "
                "ORDER BY user_id", (org_id,)).fetchall():
            await notify(conn, request, org_id, admin_id, topic, title, body, link)
    except Exception:
        logger.exception("failed to notify admins for org %s", org_id)


# ------------------------------------------------------------ accounts + equity


def linked_accounts(conn: psycopg.Connection, org_id: int, user_id: int) -> list[int]:
    """Every trading account linked to this investor in this workspace
    (accounts.investor_user_id), oldest id first; [] while none."""
    rows = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts WHERE org_id = %s AND investor_user_id = %s "
        "ORDER BY ctid_trader_account_id", (org_id, user_id)).fetchall()
    return [int(r[0]) for r in rows]


def owns_account(conn: psycopg.Connection, org_id: int, user_id: int,
                 account_id: Optional[int]) -> bool:
    """True when account_id is one of this investor's accounts here."""
    return conn.execute(
        "SELECT 1 FROM accounts WHERE org_id = %s AND investor_user_id = %s "
        "AND ctid_trader_account_id = %s", (org_id, user_id, account_id)).fetchone() is not None


def pick_account(conn: psycopg.Connection, org_id: int, user_id: int,
                 account_id: Optional[int], *, field: str = "account_id") -> int:
    """The account a read route works on: the named one (404 unless the
    investor owns it -- no hint whether it exists), else the only one; 409
    while there is none and 400 when there are several to choose from.
    `field` names the body/query field in the 400 message (deposits pass
    "target_account_id" so their refusal names the field they sent)."""
    if account_id is not None:
        if not owns_account(conn, org_id, user_id, account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        return account_id
    owned = linked_accounts(conn, org_id, user_id)
    if not owned:
        raise HTTPException(status_code=409, detail="no account linked yet")
    if len(owned) > 1:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    return owned[0]


def accounts_used(conn: psycopg.Connection, org_id: int, user_id: int) -> int:
    """What counts against portal_settings.max_live_accounts: the accounts
    the investor owns plus their open ('requested') account requests."""
    (used,) = conn.execute(
        "SELECT (SELECT count(*) FROM accounts "
        "        WHERE org_id = %(o)s AND investor_user_id = %(u)s) "
        "     + (SELECT count(*) FROM account_requests "
        "        WHERE org_id = %(o)s AND user_id = %(u)s AND status = 'requested')",
        {"o": org_id, "u": user_id}).fetchone()
    return int(used)


def account_limit_text(limit: int) -> str:
    return f"you have reached the limit of {limit} live accounts"


def link_account(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int,
                 *, mt5_only: bool = False) -> None:
    """Link one more account to an investor, inside the caller's
    transaction (moved from routes/portal_identity._link_account; the admin
    link route and fulfil both use it): never the master, MT5 only when
    mt5_only (fulfil of an MT5 account request), never an
    account linked to someone else, and at most max_live_accounts per
    investor. Linking an account the investor already owns is a no-op."""
    if owns_account(conn, org_id, user_id, account_id):
        return
    role_row = conn.execute(
        "SELECT role, platform FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
        (account_id, org_id)).fetchone()
    # Existence (and the master/mt5_only checks, which need the row) are
    # settled BEFORE the cap: an unknown id is 404 even while the investor
    # is already at the limit, never the limit's 409.
    if not role_row:
        raise HTTPException(status_code=404,
                            detail="Account not found in this workspace, or already linked")
    if role_row[0] == "master":
        raise HTTPException(status_code=400,
                            detail="The master account cannot be linked to an investor")
    if mt5_only and role_row[1] != "mt5":
        raise HTTPException(status_code=400, detail="Only an MT5 account can be linked here")
    limit = portal_settings(conn, org_id)["max_live_accounts"]
    # ponytail: count then update, no lock -- two admins linking to the same
    # investor at the same instant could pass the cap by one; take
    # lock_investor_ledger here first if that ever matters.
    if len(linked_accounts(conn, org_id, user_id)) >= limit:
        raise HTTPException(status_code=409, detail=account_limit_text(limit))
    updated = conn.execute(
        "UPDATE accounts SET investor_user_id = %s "
        "WHERE ctid_trader_account_id = %s AND org_id = %s AND investor_user_id IS NULL "
        "RETURNING ctid_trader_account_id", (user_id, account_id, org_id)).fetchone()
    if not updated:
        raise HTTPException(status_code=404,
                            detail="Account not found in this workspace, or already linked")


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


def credit_transfer_open(conn: psycopg.Connection, org_id: int, user_id: int,
                         account_id: int) -> bool:
    """True while a requested/approved credit->account transfer is still
    moving money into this account. Mutual exclusion with an outflow out of
    the same account (request_transfer, ruling on the Task 11 fix round,
    Finding 1): while either direction is open the other is refused, so a
    credit transfer can never land `done` in between two outflows that each
    looked capped alone and together moved more bonus principal out than
    the account ever held clear of it."""
    return conn.execute(
        "SELECT 1 FROM transfers WHERE org_id = %s AND user_id = %s AND status = ANY(%s) "
        "AND source_kind = 'wallet' AND source_wallet = 'credit' "
        "AND target_kind = 'account' AND target_account_id = %s LIMIT 1",
        (org_id, user_id, list(OPEN_TRANSFER_STATUSES), account_id)).fetchone() is not None


def credit_funded(conn: psycopg.Connection, org_id: int, user_id: int,
                  account_id: int) -> Decimal:
    """Bonus credit this investor moved into this account: done transfers
    from the Credit wallet. The desk funds them as broker credit; that
    principal never moves back out to a wallet (profit made on it may).

    ponytail: keyed on (user_id, account_id) over `transfers` rows, so
    deleting and re-adding the account (its transfers' account_id is ON
    DELETE SET NULL) or relinking it to a different investor resets this
    floor to 0 for whoever holds it next. Both are admin-only actions;
    accepted rather than guarded against."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transfers WHERE org_id = %s AND user_id = %s "
        "AND status = 'done' AND source_kind = 'wallet' AND source_wallet = 'credit' "
        "AND target_kind = 'account' AND target_account_id = %s",
        (org_id, user_id, account_id)).fetchone()
    return Decimal(total)


def account_movable(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int,
                    equity: Decimal) -> Decimal:
    """What may still move from the account to a wallet: equity less open
    account->wallet transfers less the bonus credit funded into it, floored
    to the cent and never below zero."""
    movable = floor_cents(equity - open_account_transfers_out(conn, org_id, user_id, account_id)
                          - credit_funded(conn, org_id, user_id, account_id))
    return max(movable, Decimal("0.00"))


# ------------------------------------------------------------ locking


LEDGER_LOCK_NAMESPACE = 220229


def lock_investor_ledger(conn: psycopg.Connection, org_id: int, user_id: int) -> None:
    """Serialise every read-check-write on one investor's wallets.

    Takes a transaction-scoped advisory lock in the two-key space
    (namespace, hashtext("<org_id>:<user_id>")), which never overlaps the
    single-key org locks the webhook routes take. Must be called inside
    ``with conn.transaction():`` -- in autocommit the lock would be
    released when the SELECT ends and protect nothing, so that is refused.

    Every transaction that writes wallet_entries -- every settle() call,
    and any direct ledger INSERT -- must take this lock as the FIRST
    statement inside its `with conn.transaction():` block, not just the
    request that is doing the capping. Otherwise the SIDE THAT COMMITS
    LATER can still read a stale balance/hold snapshot (wallet_figures
    reads balances and holds as two separate statements; at READ
    COMMITTED each sees whatever was committed at the moment it runs) and
    let a second request pass its cap against money this transaction is
    about to move.

    While the lock is held, the caller must not `await` anything --
    every route here is `async def` on top of a synchronous psycopg
    connection, so an `await` between taking the lock and the transaction
    ending would suspend this worker mid-transaction. That freezes every
    OTHER request this worker could otherwise serve, and a second
    connection blocked on the SAME (org_id, user_id) lock in the database
    has no way to make progress until this worker is rescheduled and
    finishes -- the one situation this helper exists to prevent would
    then just move from "unlocked" to "deadlocked"."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError("lock_investor_ledger must run inside a transaction")
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                 (LEDGER_LOCK_NAMESPACE, f"{org_id}:{user_id}"))


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


# ------------------------------------------------------------ bonuses


BONUS_SOURCES = ("signup", "kyc", "deposit", "manual")
BONUS_NAMES = {"signup": "welcome bonus", "kyc": "verification bonus",
               "deposit": "deposit bonus", "manual": "bonus"}


def bonus_rules(conn: psycopg.Connection, org_id: int) -> dict:
    """The org's bonus rules; the row is created with every rule off on first
    read. Flags as bools, amounts and pct as Decimals, deposit_cap Decimal or None."""
    conn.execute("INSERT INTO bonus_rules (org_id) VALUES (%s) ON CONFLICT (org_id) DO NOTHING",
                 (org_id,))
    row = conn.execute(
        "SELECT signup_enabled, signup_amount, kyc_enabled, kyc_amount, deposit_enabled, "
        "deposit_pct, deposit_cap, updated_at FROM bonus_rules WHERE org_id = %s",
        (org_id,)).fetchone()
    return {"signup_enabled": bool(row[0]), "signup_amount": Decimal(row[1]),
            "kyc_enabled": bool(row[2]), "kyc_amount": Decimal(row[3]),
            "deposit_enabled": bool(row[4]), "deposit_pct": Decimal(row[5]),
            "deposit_cap": Decimal(row[6]) if row[6] is not None else None,
            "updated_at": row[7]}


def rule_bonus(conn: psycopg.Connection, org_id: int, source: str,
               base: Optional[Decimal] = None) -> Decimal:
    """What the org's rule pays for this event NOW (rules are never applied
    backwards): 0 while the rule is off; the deposit rule takes `base`, the
    confirmed amount.

    There is no rule for a hand-posted bonus and no base-less deposit
    bonus, so both raise ValueError -- even while the matching rule is
    off -- rather than silently answering 0 for a caller that passed the
    wrong source or forgot `base`."""
    if source == "manual":
        raise ValueError("rule_bonus does not pay the 'manual' source; use pay_bonus directly")
    if source == "deposit" and base is None:
        raise ValueError("rule_bonus requires base for source 'deposit'")
    rules = bonus_rules(conn, org_id)
    if not rules[f"{source}_enabled"]:
        return Decimal("0")
    if source == "deposit":
        return deposit_bonus(base, rules["deposit_pct"], rules["deposit_cap"])
    return rules[f"{source}_amount"]


def pay_bonus(conn: psycopg.Connection, org_id: int, user_id: int, source: str, amount: Decimal,
              source_id: Optional[int] = None, note: Optional[str] = None,
              created_by: Optional[int] = None) -> Optional[int]:
    """Record one bonus and credit it, inside the caller's transaction (which
    took lock_investor_ledger first). The bonuses unique indexes make each
    rule pay at most once: a second signup or kyc bonus for the investor, or
    a second one for the same deposit, inserts nothing and returns None. A
    zero amount pays nothing. Returns the bonuses id when paid."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError("pay_bonus must run inside the caller's transaction")
    if amount == 0:
        return None
    row = conn.execute(
        "INSERT INTO bonuses (org_id, user_id, source, source_id, amount, note, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
        (org_id, user_id, source, source_id, amount, note, created_by)).fetchone()
    if row is None:
        return None
    bonus_id = int(row[0])
    wrote = settle(conn, org_id=org_id, user_id=user_id, wallet="credit", amount=amount,
                   kind="bonus", ref_table="bonuses", ref_id=bonus_id, note=note,
                   created_by=created_by)
    if not wrote:
        # The bonuses row is new (just inserted above), so its ref_id can
        # never already be settled -- settle() reporting nothing written
        # means the writer and the row it is meant to back have drifted
        # apart. Keep that explicit rather than leaving an orphan bonuses
        # row with no matching credit entry.
        raise RuntimeError(f"bonus {bonus_id} was recorded but settle() wrote no ledger entry")
    return bonus_id


async def announce_bonus(conn: psycopg.Connection, request: Request, *, org_id: int,
                         user_id: int, bonus_id: int, source: str, amount: Decimal,
                         actor_email: str, note: Optional[str] = None) -> None:
    """Audit one paid bonus and tell the investor (topic bonus). A hand-posted
    bonus is a warning, so it reaches the alerters like a ledger adjustment;
    a rule's bonus is info. Run after the paying transaction.

    Best effort, like notify: the whole body -- the email lookup, the audit
    row and the notification -- is one try/except. By the time this runs
    the bonus is already committed, so a failure here is logged as "paid
    but not announced", never as a failed payment, and never turns into a
    500 for a request that already landed."""
    try:
        name = BONUS_NAMES[source]
        row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
        who = row[0] if row else f"user {user_id}"
        await audit_control(
            conn, org_id=org_id, action="investor_bonus_paid", actor_email=actor_email,
            user_id=user_id, severity="warning" if source == "manual" else "info",
            bonus_id=bonus_id, source=source, amount=money(amount), note=note,
            summary=f"Bonus {amount:+.2f} USD ({name}) for {who} by {actor_email}")
        noted = f"\nNote: {note}" if note else ""
        if amount > 0:
            title = f"You received a {amount:.2f} USD {name}"
            body = (f"{amount:.2f} USD was paid into your Credit wallet.{noted}\n"
                    "Bonus credit can be moved to one of your trading accounts from Transfer.")
        else:
            title = f"A bonus of {-amount:.2f} USD was taken back"
            body = f"{-amount:.2f} USD was taken from your Credit wallet.{noted}"
        await notify(conn, request, org_id, user_id, "bonus", title, body,
                     investor_link(org_id, "bonus"))
    except Exception:
        logger.exception("bonus %s paid but not announced", bonus_id)


async def award_rule_bonus(conn: psycopg.Connection, request: Request, org_id: int,
                           user_id: int, source: str, *,
                           actor_email: Optional[str] = None) -> Optional[int]:
    """The signup or kyc rule's bonus, in its own transaction (ledger lock
    first), then audit + notify. Paid at most once per investor; nothing
    while the rule is off. Returns the bonuses id when paid.

    `actor_email` is the admin who caused the triggering change (a role
    change, a KYC approval); omitted (None) by the one caller where the
    actor IS the investor themselves (a self-service join) and no
    OrgContext carries their email -- it is then resolved from `user_id`
    inside this same best-effort try, so a lookup failure cannot 500 a
    join that already committed.

    Best effort, like notify: it runs after the join, role change or KYC
    approval has committed, so a failure here is logged and returns None
    rather than answering 500 for a change that already landed. A missed
    bonus is visible in the log and can be granted by hand.

    The announce_bonus call is inside this same try (ruling P8): once the
    paying transaction above has committed, a failure in the audit/notify
    step is just as much a best-effort failure as one in the payment
    itself -- it must never turn into a 500 for a join, role change or KYC
    approval that already landed. The bonus stays paid either way; only
    the return value (None) tells the caller the announcement is missing
    from the log. The log message says so explicitly ("paid but not
    announced") rather than the generic "bonus failed", which would be
    wrong once the money has already moved."""
    bonus_id: Optional[int] = None
    try:
        with conn.transaction():
            lock_investor_ledger(conn, org_id, user_id)
            amount = rule_bonus(conn, org_id, source)
            bonus_id = pay_bonus(conn, org_id, user_id, source, amount)
        if bonus_id is not None:
            email = actor_email
            if email is None:
                row = conn.execute("SELECT email FROM users WHERE id = %s",
                                   (user_id,)).fetchone()
                email = row[0] if row else f"user {user_id}"
            await announce_bonus(conn, request, org_id=org_id, user_id=user_id,
                                 bonus_id=bonus_id, source=source, amount=amount,
                                 actor_email=email)
    except Exception:
        if bonus_id is not None:
            logger.exception("bonus %s paid but not announced", bonus_id)
        else:
            logger.exception("%s bonus failed for user %s in org %s", source, user_id, org_id)
        return None
    return bonus_id


# ------------------------------------------------------------ settings


def portal_settings(conn: psycopg.Connection, org_id: int) -> dict:
    """{withdrawal_min, withdrawal_fee_pct} as Decimals and max_live_accounts
    as an int; the row is created with the defaults on first read."""
    conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s) ON CONFLICT (org_id) DO NOTHING",
                 (org_id,))
    row = conn.execute(
        "SELECT withdrawal_min, withdrawal_fee_pct, max_live_accounts FROM portal_settings "
        "WHERE org_id = %s", (org_id,)).fetchone()
    return {"withdrawal_min": Decimal(row[0]), "withdrawal_fee_pct": Decimal(row[1]),
            "max_live_accounts": int(row[2])}


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
