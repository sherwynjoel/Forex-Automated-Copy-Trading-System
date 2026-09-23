"""Ledger rules for the investor portal, with no I/O.

Three things live here so they can be tested without a database and reused
by every endpoint identically: what an amount may look like, which status
changes are legal, and how an investor's summary is computed from rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Optional

MAX_TEXT = 128
MAX_INTEGER_DIGITS = 12
_CENT = Decimal("0.01")


class LedgerError(ValueError):
    """The input was understood and refused. The message is for the person."""


def parse_amount(raw: object, field: str = "amount") -> Decimal:
    """A positive money amount with at most two decimals. Strings are
    accepted because forms send strings; bools are refused because
    Decimal(True) is 1."""
    if isinstance(raw, bool) or raw is None or raw == "":
        raise LedgerError(f"{field} is required, e.g. 250.00")
    if isinstance(raw, float) and raw != raw:  # NaN
        raise LedgerError(f"{field} must be a number")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise LedgerError(f"{field} must be a number, got {raw!r}")
    if not value.is_finite() or value <= 0:
        raise LedgerError(f"{field} must be greater than 0")
    try:
        quantized = value.quantize(_CENT)
    except InvalidOperation:
        raise LedgerError(f"{field} is too large")
    if value != quantized:
        raise LedgerError(f"{field} may have at most two decimals")
    if value >= Decimal(10) ** MAX_INTEGER_DIGITS:
        raise LedgerError(f"{field} is too large")
    return value


def clean_text(raw: object, field: str, max_len: int = MAX_TEXT,
               required: bool = True) -> Optional[str]:
    text = "" if raw is None else str(raw).strip()
    if not text:
        if required:
            raise LedgerError(f"{field} is required")
        return None
    if len(text) > max_len:
        raise LedgerError(f"{field} must be at most {max_len} characters")
    return text


DEPOSIT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"confirmed", "rejected"}),
}
WITHDRAWAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "requested": frozenset({"approved", "rejected"}),
    "approved": frozenset({"paid", "rejected"}),
}


def can_transition(transitions: dict[str, frozenset[str]], current: str, new: str) -> bool:
    return new in transitions.get(current, frozenset())


@dataclass(frozen=True)
class Summary:
    total_deposited: Decimal
    total_withdrawn: Decimal
    net_deposits: Decimal
    pending_withdrawn: Decimal
    equity: Optional[Decimal]
    profit: Optional[Decimal]
    available: Optional[Decimal]


def summarise(deposits: Iterable[tuple[str, Decimal]],
              withdrawals: Iterable[tuple[str, Decimal]],
              equity: Optional[Decimal]) -> Summary:
    """`deposits`/`withdrawals` are (status, amount) pairs. Only confirmed
    deposits and paid withdrawals are money that moved; requested and
    approved withdrawals are money spoken for."""
    total_dep = sum((a for s, a in deposits if s == "confirmed"), Decimal(0))
    paid = Decimal(0)
    pending = Decimal(0)
    for status, amount in withdrawals:
        if status == "paid":
            paid += amount
        elif status in ("requested", "approved"):
            pending += amount
    net = total_dep - paid
    profit = None if equity is None else equity - net
    available = None if equity is None else equity - pending
    return Summary(total_deposited=total_dep, total_withdrawn=paid, net_deposits=net,
                   pending_withdrawn=pending, equity=equity, profit=profit,
                   available=available)


def _money(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def summary_json(s: Summary) -> dict:
    return {
        "total_deposited": _money(s.total_deposited),
        "total_withdrawn": _money(s.total_withdrawn),
        "net_deposits": _money(s.net_deposits),
        "pending_withdrawn": _money(s.pending_withdrawn),
        "equity": _money(s.equity),
        "profit": _money(s.profit),
        "available": _money(s.available),
    }
