"""Ledger rules for the client portal, with no I/O.

Everything an endpoint must agree on lives here so it can be tested
without a database and reused identically: what an amount may look like,
how a wallet's balance, holds and available figure are computed, how a fee
is taken, which status changes are legal, and which transfer pairs exist.
Money is Decimal throughout; the API turns it into cents-rounded floats
only at the edge (`money`).

The one rule that matters most: `available` is FLOORED to cents, never
rounded half-up, and the API reports and checks caps against that same
floored figure, so "Use max" can never be refused for rounding.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional

WALLETS: tuple[str, ...] = ("main", "credit", "pamm", "social")
CENT = Decimal("0.01")
MAX_TEXT = 128
MAX_INTEGER_DIGITS = 12


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
        quantized = value.quantize(CENT)
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


# ------------------------------------------------------------ cents


def floor_cents(value: Decimal) -> Decimal:
    """Toward zero to the cent. What the investor is told is available."""
    return value.quantize(CENT, rounding=ROUND_DOWN)


def round_cents(value: Decimal) -> Decimal:
    """Half-up to the cent. For fees and for the JSON edge."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fee_for(amount: Decimal, fee_pct: Decimal) -> Decimal:
    return round_cents(amount * fee_pct / Decimal(100))


def deposit_bonus(amount: Decimal, pct: Decimal, cap: Optional[Decimal]) -> Decimal:
    """The deposit rule's bonus: pct of the confirmed amount, half-up to the
    cent, never above cap (None = no cap). Zero means nothing is paid."""
    bonus = round_cents(amount * pct / Decimal(100))
    return bonus if cap is None else min(bonus, cap)


def money(value: Optional[Decimal]) -> Optional[float]:
    """The JSON form: a float rounded to cents, or None."""
    return None if value is None else float(round_cents(value))


# ------------------------------------------------------------ figures


def balance(entries: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """`entries` are (wallet, amount) pairs; every wallet in WALLETS is a
    key of the result, Decimal("0") when it has no entries. The database
    CHECK guarantees the wallet names."""
    out = {w: Decimal("0") for w in WALLETS}
    for wallet, amount in entries:
        out[wallet] = out[wallet] + amount
    return out


def holds(open_withdrawals: list[Decimal],
          open_transfers: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Money spoken for by open requests. Withdrawals always hold on
    `main`; `open_transfers` are (source_wallet, amount) pairs of transfers
    in `requested` or `approved` whose source is a wallet."""
    out = {w: Decimal("0") for w in WALLETS}
    for amount in open_withdrawals:
        out["main"] = out["main"] + amount
    for wallet, amount in open_transfers:
        out[wallet] = out[wallet] + amount
    return out


def available(balance: Decimal, hold: Decimal) -> Decimal:
    """Floored, and allowed to be negative: an admin adjustment can take a
    wallet below what is on hold, and the figure must say so."""
    return floor_cents(balance - hold)


# ------------------------------------------------------------ transitions

DEPOSIT_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "rejected", "cancelled"},
}
WITHDRAWAL_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"approved", "rejected", "cancelled"},
    "approved": {"paid", "rejected"},
}
TRANSFER_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"approved", "done", "rejected", "cancelled"},
    "approved": {"done", "rejected"},
}
DESTINATION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected", "removed"},
    "approved": {"removed"},
}
_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "deposits": DEPOSIT_TRANSITIONS,
    "withdrawals": WITHDRAWAL_TRANSITIONS,
    "transfers": TRANSFER_TRANSITIONS,
    "payout_destinations": DESTINATION_TRANSITIONS,
}
# The owner's own moves; every other transition is an admin's.
INVESTOR_MOVES: set[str] = {"cancelled", "removed"}


def can_transition(table: str, current: str, new: str) -> bool:
    """`table` is deposits, withdrawals, transfers or payout_destinations."""
    return new in _TRANSITIONS[table].get(current, set())


# ------------------------------------------------------------ transfers

TRANSFER_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("main", "account"), ("account", "main"), ("pamm", "main"), ("social", "main"),
})
_NOT_ALLOWED = "that transfer is not allowed"


def _end(ref: dict) -> str:
    """A wallet name, or "account", from {"kind":"wallet","wallet":...} |
    {"kind":"account","account_id":...}; anything else is refused."""
    if not isinstance(ref, dict):
        raise LedgerError(_NOT_ALLOWED)
    kind = ref.get("kind")
    if kind == "wallet" and ref.get("wallet") in WALLETS:
        return str(ref["wallet"])
    if kind == "account" and ref.get("account_id") is not None:
        return "account"
    raise LedgerError(_NOT_ALLOWED)


def transfer_pair(source: dict, target: dict) -> tuple[str, str]:
    """("main", "account") and friends; raises LedgerError for every pair
    outside TRANSFER_PAIRS (credit never moves in phase 1)."""
    pair = (_end(source), _end(target))
    if pair not in TRANSFER_PAIRS:
        raise LedgerError(_NOT_ALLOWED)
    return pair
