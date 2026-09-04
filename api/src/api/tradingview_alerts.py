"""Turning a TradingView alert into something the copier will accept.

Pure functions, no I/O: this is the part of the webhook that can be reasoned
about and tested without a server, and it is where a template typo turns
into either a clear rejection or a wrong trade. Everything here fails LOUD
with a message the operator can act on -- "lots exceeds the cap" beats
"400 Bad Request" when the person reading it is not an engineer.

Two problems live here:

TICKERS. TradingView names instruments as EXCHANGE:SYMBOL with occasional
suffixes -- "OANDA:XAUUSD", "FX:EURUSD", "BINANCE:BTCUSDT.P", "ES1!". The
copier's symbol cache holds the broker's plain names: "XAUUSD", "EURUSD".
The mapping strips the exchange and the decorations and nothing else; it
never guesses a different instrument, so an unmapped ticker is rejected at
the door rather than traded as its nearest neighbour.

VOLUME. A strategy alert sends {{strategy.order.contracts}}, which is in
whatever unit the strategy was written in -- lots for most forex scripts,
but nothing enforces that. So the alert carries an explicit `lots` field the
operator writes into the template themselves, and it is capped per org. A
cap in the wrong place would let a template that meant 0.10 send 10.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

ACTIONS = ("buy", "sell", "close")

# Exchange prefix and the decorations TradingView appends to some tickers:
# a continuous-contract "1!" / "2!", a perpetual ".P", or a settlement
# suffix like ".CFD". None of them survive to the broker's name.
_EXCHANGE_PREFIX = re.compile(r"^[A-Z0-9_]+:")
_SUFFIXES = re.compile(r"(\d*!|\.[A-Z]+)+$")
_ALLOWED = re.compile(r"^[A-Z0-9]{3,20}$")

# Absolute ceiling regardless of the per-org cap. No copy-trading alert
# should ever ask for this; it exists so a corrupted or malicious cap cannot
# unlock a hundred-lot order.
HARD_MAX_LOTS = 50.0


class AlertError(ValueError):
    """The alert was understood and refused. The message is for the operator."""


@dataclass(frozen=True)
class Alert:
    action: str            # buy | sell | close
    symbol: str            # broker-style, e.g. XAUUSD
    lots: float | None     # None for close (closes the whole position)
    stop_loss: float | None
    take_profit: float | None
    # Whatever the alert carried as its own id, for the audit trail and for
    # duplicate suppression. Never trusted for anything else.
    alert_id: str | None


def normalise_ticker(raw: object) -> str:
    """"OANDA:XAUUSD" -> "XAUUSD". Refuses anything that is not a plain name.

    Uppercases, strips one exchange prefix and any trailing decorations.
    Does NOT translate between instruments -- "BTCUSDT" stays "BTCUSDT" and
    is rejected later if the broker calls it "BTCUSD", because trading a
    lookalike is the one thing worse than trading nothing.
    """
    if not isinstance(raw, str):
        raise AlertError("symbol must be text, e.g. \"XAUUSD\" or \"OANDA:XAUUSD\"")
    ticker = raw.strip().upper()
    ticker = _EXCHANGE_PREFIX.sub("", ticker, count=1)
    ticker = _SUFFIXES.sub("", ticker)
    if not _ALLOWED.match(ticker):
        raise AlertError(
            f"could not read a symbol from {raw!r}; expected something like "
            f"\"XAUUSD\" or \"OANDA:XAUUSD\"")
    return ticker


def _price(body: dict, key: str) -> float | None:
    raw = body.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise AlertError(f"{key} must be a number, got {raw!r}")
    if not math.isfinite(value) or value <= 0:
        raise AlertError(f"{key} must be a positive price, got {raw!r}")
    return value


def parse_alert(body: object, max_lots: float) -> Alert:
    """Validate an alert body against the org's limits.

    `max_lots` is the org's own cap, set by an admin. It is applied on top of
    HARD_MAX_LOTS, never instead of it.
    """
    if not isinstance(body, dict):
        raise AlertError(
            "the alert message must be JSON, e.g. "
            '{"action":"buy","symbol":"XAUUSD","lots":0.01}')

    action = str(body.get("action", "")).strip().lower()
    if action not in ACTIONS:
        raise AlertError(
            f"action must be one of {', '.join(ACTIONS)}; got {body.get('action')!r}")

    symbol = normalise_ticker(body.get("symbol", body.get("ticker")))

    alert_id = body.get("id")
    if alert_id is not None:
        alert_id = str(alert_id).strip()[:128] or None

    if action == "close":
        # A close needs no size, stop or target -- it closes what is there.
        # Anything the template sent alongside is ignored rather than
        # rejected, so a shared template can carry a lots field harmlessly.
        return Alert(action, symbol, None, None, None, alert_id)

    raw_lots = body.get("lots")
    if raw_lots is None or raw_lots == "":
        raise AlertError("lots is required for buy and sell, e.g. \"lots\": 0.01")
    try:
        lots = float(raw_lots)
    except (TypeError, ValueError):
        raise AlertError(f"lots must be a number, got {raw_lots!r}")
    if not math.isfinite(lots) or lots <= 0:
        raise AlertError(f"lots must be greater than 0, got {raw_lots!r}")

    cap = min(float(max_lots), HARD_MAX_LOTS) if max_lots else HARD_MAX_LOTS
    if lots > cap:
        raise AlertError(
            f"lots {lots:g} is above this workspace's cap of {cap:g}. "
            f"Raise the cap in Automation settings if that size is intended.")

    stop_loss = _price(body, "stop_loss")
    take_profit = _price(body, "take_profit")

    return Alert(action, symbol, lots, stop_loss, take_profit, alert_id)


def find_master_positions(state: object, symbol: str) -> list[dict]:
    """The master's open positions on `symbol`, from the copier's /state.

    A "close" alert closes what the master holds on that symbol -- all of
    it, if the master has scaled in more than once. Returns [] when there is
    nothing to close, which the caller reports as success-with-nothing-done
    rather than an error: a strategy that fires "close" on every exit
    signal must not be told it is broken because the position already went.
    """
    if not isinstance(state, dict):
        return []
    positions = state.get("master_positions") or []
    wanted = symbol.upper()
    out = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        name = pos.get("symbol")
        if isinstance(name, str) and name.upper() == wanted and pos.get("position_id") is not None:
            out.append(pos)
    return out
