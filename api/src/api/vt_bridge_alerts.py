"""Turning a VT HTF->LTF bridge alert into something the copier will accept.

Pure functions, no I/O -- same discipline as tradingview_alerts.py, and for
the same reason: this is where a relay-script mistake becomes either a
clear rejection or a wrong trade.

Two alert kinds share one contract, told apart by `role`:

HTF ("htf"). Permission only, never traded. Every valid reading overwrites
the previous one for that (org, symbol) -- there is no history.

LTF ("ltf"). The trade itself. Only acted on when `trigger` is true; a
`trigger: false` alert is informational (VT Screener relaying its current
levels without price having reached them yet). When it does trigger, it is
checked against the org's most recent HTF snapshot for that symbol:

  0. its own `tf` is on the workspace's allowed-timeframes list
  1. its own `valid` is true (stop/target are on the correct side of entry)
  2. a snapshot exists at all
  3. the snapshot is not stale (older than STALE_MULTIPLIER times its own tf)
  4. its bias matches the snapshot's bias
  5. its entry falls inside the snapshot's stop-target band, padded by
     TOL_PCT of the band's own width (never a fixed price number -- a gold
     band and a EURUSD band differ by orders of magnitude)

First failure wins; `check_gate` is pure and returns which one, so the
caller can log and refuse without a single I/O call inside this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .tradingview_alerts import AlertError, HARD_MAX_LOTS, normalise_ticker

TOL_PCT = 0.02
STALE_MULTIPLIER = 2

ROLES = ("htf", "ltf")
BIASES = ("long", "short")


@dataclass(frozen=True)
class VTAlert:
    role: str               # htf | ltf
    symbol: str              # broker-style, e.g. XAUUSD
    tf: str                  # TradingView's own timeframe string, e.g. "60"
    bias: str                # long | short
    entry: float
    stop: float
    target: float
    price: float             # chart price at relay time, informational
    valid: bool
    trigger: bool | None     # ltf only; None on htf
    lots: float | None       # ltf only (required there); None on htf
    bar_ms: int


@dataclass(frozen=True)
class VTSnapshot:
    """A stored vt_htf_snapshots row, as check_gate needs it."""
    bias: str
    stop: float
    target: float
    tf: str
    received_at: datetime    # timezone-aware


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None


def _number(raw: object, key: str) -> float:
    """float() that refuses the things float() accepts and a trade should not."""
    if isinstance(raw, bool):
        raise AlertError(f"{key} must be a number, got {raw!r}")
    if isinstance(raw, str) and len(raw) > 32:
        raise AlertError(f"{key} is not a valid number")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise AlertError(f"{key} must be a number, got {raw!r}")
    return value


def _finite_positive(body: dict, key: str) -> float:
    raw = body.get(key)
    if raw is None or raw == "":
        raise AlertError(f'{key} is required, e.g. "{key}": 4385.34')
    value = _number(raw, key)
    if not math.isfinite(value) or value <= 0:
        raise AlertError(f"{key} must be a positive price, got {raw!r}")
    return value


def _bool_field(body: dict, key: str) -> bool:
    raw = body.get(key)
    if not isinstance(raw, bool):
        raise AlertError(f'{key} must be true or false, got {raw!r}')
    return raw


def parse_vt_alert(body: object, max_lots: float) -> VTAlert:
    """Validate a VT bridge alert against the org's limits.

    `max_lots` is the org's own cap, applied to `lots` on an ltf alert the
    same way parse_alert applies it -- on top of HARD_MAX_LOTS, never
    instead of it.
    """
    if not isinstance(body, dict):
        raise AlertError(
            "the alert message must be JSON, e.g. "
            '{"role":"ltf","symbol":"XAUUSD","bias":"long",...}')

    role = str(body.get("role", ""))
    if role not in ROLES:
        raise AlertError(f'role must be one of {", ".join(ROLES)}; got {body.get("role")!r}')

    symbol = normalise_ticker(body.get("symbol", body.get("ticker")))

    tf = str(body.get("tf", ""))
    if not tf:
        raise AlertError('tf is required, e.g. "tf": "60"')

    bias = str(body.get("bias", ""))
    if bias not in BIASES:
        raise AlertError(f'bias must be one of {", ".join(BIASES)}; got {body.get("bias")!r}')

    entry = _finite_positive(body, "entry")
    stop = _finite_positive(body, "stop")
    target = _finite_positive(body, "target")
    price = _finite_positive(body, "price")
    valid = _bool_field(body, "valid")

    raw_bar_ms = body.get("bar_ms")
    try:
        bar_ms = int(raw_bar_ms)
    except (TypeError, ValueError):
        raise AlertError('bar_ms is required and must be a whole number of milliseconds')

    if role == "htf":
        return VTAlert(role, symbol, tf, bias, entry, stop, target, price,
                       valid, None, None, bar_ms)

    trigger = _bool_field(body, "trigger")

    raw_lots = body.get("lots")
    if raw_lots is None or raw_lots == "":
        raise AlertError('lots is required for role "ltf", e.g. "lots": 0.01')
    lots = _number(raw_lots, "lots")
    if not math.isfinite(lots) or lots <= 0:
        raise AlertError(f"lots must be greater than 0, got {raw_lots!r}")
    if max_lots is None or not math.isfinite(float(max_lots)) or float(max_lots) <= 0:
        raise AlertError("this workspace has no lot cap configured; set one in Automation")
    cap = min(float(max_lots), HARD_MAX_LOTS)
    if lots > cap:
        raise AlertError(
            f"lots {lots:g} is above this workspace's cap of {cap:g}. "
            f"Raise the cap in Automation settings if that size is intended.")

    return VTAlert(role, symbol, tf, bias, entry, stop, target, price,
                   valid, trigger, lots, bar_ms)


def check_gate(htf: VTSnapshot | None, ltf: VTAlert, allowed_timeframes: set[str],
               now: datetime) -> GateResult:
    """First failure wins. Gate 0 (allowed timeframes) is cheapest and
    org-scoped rather than a function of (htf, ltf), so it runs before even
    checking whether a snapshot exists."""
    if ltf.tf not in allowed_timeframes:
        return GateResult(False, f"entry timeframe {ltf.tf} is not enabled for this workspace")

    if not ltf.valid:
        return GateResult(False, "LTF setup is not valid (stop/target on the wrong side of entry)")

    if htf is None:
        return GateResult(False, f"no HTF snapshot for {ltf.symbol} yet")

    try:
        htf_tf_minutes = int(htf.tf)
    except ValueError:
        htf_tf_minutes = 0
    max_age_s = STALE_MULTIPLIER * htf_tf_minutes * 60
    age_s = (now - htf.received_at).total_seconds()
    if max_age_s <= 0 or age_s > max_age_s:
        return GateResult(
            False, f"HTF snapshot for {ltf.symbol} is {int(age_s)}s old (max {max_age_s}s)")

    if ltf.bias != htf.bias:
        return GateResult(
            False, f"LTF bias ({ltf.bias}) does not match HTF bias ({htf.bias})")

    lo = min(htf.stop, htf.target)
    hi = max(htf.stop, htf.target)
    tol = TOL_PCT * (hi - lo)
    if not (lo - tol <= ltf.entry <= hi + tol):
        return GateResult(
            False,
            f"LTF entry {ltf.entry:g} is outside the HTF band [{lo:g}, {hi:g}] (±{tol:g})")

    return GateResult(True, None)
