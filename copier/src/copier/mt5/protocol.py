"""The wire format between the MirrorFleet EA and the copier.

Shared by the copier (server side) and the Python fake EA used in tests,
and mirrored -- by contract, not by import -- by the MQL5 EA. Requests
(EA -> server) are JSON; responses (server -> EA) are tab-separated lines,
because MQL5 has no JSON parser but splits strings trivially. Every
conversion between the EA's lots and the copier's centilots happens here
(report -> centilots) or in outbox.py (payload lots <- centilots); nothing
else in the copier ever sees a lot as a float.
"""

from dataclasses import dataclass

MT5_KEY_PREFIX = "mt5_"
PROTOCOL_VERSION = 1
CENTILOTS = 100                       # protocol units per 1.00 lot for every MT5 symbol
DEFAULT_POLL_MS = 250
RETRY_POLL_MS = 2000
MAX_DEALS_PER_SYNC = 200

SIDES = ("BUY", "SELL")
PENDING_TYPES = ("BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP")
DEAL_TYPES = ("BUY", "SELL", "BALANCE", "CREDIT", "OTHER")
DEAL_ENTRIES = ("IN", "OUT", "INOUT", "OUT_BY", "")
COMMAND_KINDS = ("open", "close", "amend", "place_pending", "amend_pending", "cancel_pending")


class ProtocolError(ValueError):
    """The other side sent something this version of the protocol cannot read."""


@dataclass(frozen=True)
class HelloSymbol:
    name: str
    digits: int
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    trade_mode: int


@dataclass(frozen=True)
class HelloReport:
    ea_version: str
    ea_build: int
    login: int
    broker: str
    server: str
    currency: str
    hedging: bool
    trade_mode: str
    leverage: int
    symbols: list[HelloSymbol]
    chunk: int
    chunks: int


@dataclass(frozen=True)
class ReportPosition:
    ticket: int
    symbol: str
    side: str                                    # "BUY" | "SELL"
    volume: int                                  # centilots
    open_price: float
    stop_loss: float | None
    take_profit: float | None
    current_price: float | None
    pnl: float
    swap: float
    comment: str
    magic: int
    opened_at_ms: int


@dataclass(frozen=True)
class ReportOrder:
    ticket: int
    symbol: str
    order_type: str                              # "BUY_LIMIT" | "SELL_LIMIT" | "BUY_STOP" | "SELL_STOP"
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None
    comment: str
    magic: int


@dataclass(frozen=True)
class ReportDeal:
    ticket: int
    position: int
    order: int
    symbol: str
    deal_type: str                               # "BUY" | "SELL" | "BALANCE" | "CREDIT" | "OTHER"
    entry: str                                   # "IN" | "OUT" | "INOUT" | "OUT_BY" | "" (balance ops)
    volume: int
    price: float
    profit: float
    swap: float
    commission: float
    time_ms: int
    comment: str
    magic: int


@dataclass(frozen=True)
class Ack:
    command_id: int
    ok: bool
    retcode: int
    message: str
    position: int | None
    deal: int | None
    order: int | None
    price: float | None
    volume: int | None                           # centilots


@dataclass(frozen=True)
class SyncReport:
    seq: int
    ts_ms: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    positions: list[ReportPosition]
    orders: list[ReportOrder]
    deals: list[ReportDeal]
    acks: list[Ack]


@dataclass(frozen=True)
class Command:
    id: int
    kind: str
    payload: dict                                # payload per the contract's table (section 1)
    client_order_id: str | None = None


def centilots(lots_value) -> int:
    """Lots (a float on the wire) -> centilots (the copier's integer units)."""
    return int(round(float(lots_value) * CENTILOTS))


def lots(volume: int) -> float:
    """Centilots -> lots, as the EA wants them."""
    return round(int(volume) / CENTILOTS, 2)


# ---------- request parsing (EA -> server) ----------

def _require(body: dict, key: str, kind, what: str):
    if key not in body or body[key] is None:
        raise ProtocolError(f"{what}: missing field {key!r}")
    try:
        return kind(body[key])
    except (TypeError, ValueError):
        raise ProtocolError(f"{what}: field {key!r} is not a {kind.__name__}")


def _optional(body: dict, key: str, kind, default):
    value = body.get(key)
    if value is None:
        return default
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"field {key!r} is not a {kind.__name__}")


def _price(value) -> float | None:
    """A price field: 0 or absent means none."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"price {value!r} is not a number")
    return price if price != 0 else None


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"ticket {value!r} is not an integer")
    return number if number != 0 else None


def _enum(value, allowed: tuple, what: str) -> str:
    text = str(value).upper()
    if text not in allowed:
        raise ProtocolError(f"{what}: {value!r} is not one of {allowed}")
    return text


def _list(body: dict, key: str) -> list:
    value = body.get(key) or []
    if not isinstance(value, list):
        raise ProtocolError(f"field {key!r} must be a list")
    for item in value:
        if not isinstance(item, dict):
            raise ProtocolError(f"every entry of {key!r} must be an object")
    return value


def parse_hello(body: dict) -> HelloReport:
    if not isinstance(body, dict):
        raise ProtocolError("hello body must be a JSON object")
    symbols = [
        HelloSymbol(
            name=str(_require(raw, "n", str, "hello symbol")),
            digits=_require(raw, "d", int, "hello symbol"),
            contract_size=_optional(raw, "cs", float, 0.0),
            volume_min=_require(raw, "vmin", float, "hello symbol"),
            volume_step=_require(raw, "vstep", float, "hello symbol"),
            volume_max=_optional(raw, "vmax", float, 0.0),
            trade_mode=_optional(raw, "tm", int, 0),
        )
        for raw in _list(body, "symbols")
    ]
    return HelloReport(
        ea_version=str(_optional(body, "ea", str, "")),
        ea_build=_optional(body, "build", int, 0),
        login=_require(body, "login", int, "hello"),
        broker=str(_require(body, "broker", str, "hello")),
        server=str(_optional(body, "server", str, "")),
        currency=str(_optional(body, "currency", str, "")),
        hedging=bool(_require(body, "hedging", bool, "hello")),
        trade_mode=str(_optional(body, "trade_mode", str, "")),
        leverage=_optional(body, "leverage", int, 0),
        symbols=symbols,
        chunk=_optional(body, "chunk", int, 1),
        chunks=_optional(body, "chunks", int, 1),
    )


def parse_sync(body: dict) -> SyncReport:
    """Lots become centilots here and nowhere else; missing optional fields
    become None (prices, tickets) or 0 (money, counters)."""
    if not isinstance(body, dict):
        raise ProtocolError("sync body must be a JSON object")
    positions = [
        ReportPosition(
            ticket=_require(raw, "t", int, "position"),
            symbol=str(_require(raw, "s", str, "position")),
            side=_enum(_require(raw, "side", str, "position"), SIDES, "position side"),
            volume=centilots(_require(raw, "lots", float, "position")),
            open_price=_require(raw, "open", float, "position"),
            stop_loss=_price(raw.get("sl")),
            take_profit=_price(raw.get("tp")),
            current_price=_price(raw.get("price")),
            pnl=_optional(raw, "pnl", float, 0.0),
            swap=_optional(raw, "swap", float, 0.0),
            comment=str(raw.get("comment") or ""),
            magic=_optional(raw, "magic", int, 0),
            # POSITION_TIME is seconds on the terminal (deals carry DEAL_TIME_MSC).
            opened_at_ms=_optional(raw, "time", int, 0) * 1000,
        )
        for raw in _list(body, "positions")
    ]
    orders = [
        ReportOrder(
            ticket=_require(raw, "t", int, "order"),
            symbol=str(_require(raw, "s", str, "order")),
            order_type=_enum(_require(raw, "type", str, "order"), PENDING_TYPES, "order type"),
            volume=centilots(_require(raw, "lots", float, "order")),
            price=_require(raw, "price", float, "order"),
            stop_loss=_price(raw.get("sl")),
            take_profit=_price(raw.get("tp")),
            comment=str(raw.get("comment") or ""),
            magic=_optional(raw, "magic", int, 0),
        )
        for raw in _list(body, "orders")
    ]
    deals = [
        ReportDeal(
            ticket=_require(raw, "t", int, "deal"),
            position=_optional(raw, "pos", int, 0),
            order=_optional(raw, "order", int, 0),
            symbol=str(raw.get("s") or ""),
            deal_type=_enum(_optional(raw, "type", str, "OTHER"), DEAL_TYPES, "deal type"),
            entry=_enum(_optional(raw, "entry", str, ""), DEAL_ENTRIES, "deal entry"),
            volume=centilots(_optional(raw, "lots", float, 0.0)),
            price=_optional(raw, "price", float, 0.0),
            profit=_optional(raw, "profit", float, 0.0),
            swap=_optional(raw, "swap", float, 0.0),
            commission=_optional(raw, "commission", float, 0.0),
            time_ms=_require(raw, "time", int, "deal"),
            comment=str(raw.get("comment") or ""),
            magic=_optional(raw, "magic", int, 0),
        )
        for raw in _list(body, "deals")
    ]
    acks = []
    for raw in _list(body, "acks"):
        volume = raw.get("lots")
        acks.append(Ack(
            command_id=_require(raw, "id", int, "ack"),
            ok=bool(_require(raw, "ok", bool, "ack")),
            retcode=_optional(raw, "retcode", int, 0),
            message=str(raw.get("msg") or ""),
            position=_int_or_none(raw.get("pos")),
            deal=_int_or_none(raw.get("deal")),
            order=_int_or_none(raw.get("order")),
            price=_price(raw.get("price")),
            volume=centilots(volume) if volume is not None else None,
        ))
    return SyncReport(
        seq=_require(body, "seq", int, "sync"),
        ts_ms=_require(body, "ts", int, "sync"),
        balance=_require(body, "balance", float, "sync"),
        equity=_require(body, "equity", float, "sync"),
        margin=_optional(body, "margin", float, 0.0),
        margin_free=_optional(body, "margin_free", float, 0.0),
        positions=positions, orders=orders, deals=deals, acks=acks,
    )


# ---------- response encoding (server -> EA) ----------

def _field(value) -> str:
    """One positional field. The format has no escaping, so a tab or a line
    break inside a value would corrupt every field after it."""
    text = str(value)
    if "\t" in text or "\n" in text or "\r" in text:
        raise ProtocolError(f"field {text!r} would break the line format")
    return text


def _num(value) -> str:
    """A price or lot size: None/0 -> "0" (no protection, full close);
    otherwise the shortest repr that round-trips through StringToDouble."""
    if value is None or float(value) == 0:
        return "0"
    return repr(float(value))


def command_line(cmd: Command) -> str:
    p = cmd.payload
    if cmd.kind == "open":
        fields = [p["symbol"], p["side"], _num(p["lots"]), _num(p.get("sl")), _num(p.get("tp")),
                  p.get("comment", ""), cmd.client_order_id or ""]
    elif cmd.kind == "close":
        fields = [int(p["position"]), _num(p.get("lots"))]
    elif cmd.kind == "amend":
        fields = [int(p["position"]), _num(p.get("sl")), _num(p.get("tp"))]
    elif cmd.kind == "place_pending":
        fields = [p["symbol"], p["type"], _num(p["lots"]), _num(p["price"]), _num(p.get("sl")),
                  _num(p.get("tp")), int(p.get("expiry_ms") or 0), p.get("comment", ""),
                  cmd.client_order_id or ""]
    elif cmd.kind == "amend_pending":
        fields = [int(p["order"]), _num(p["lots"]), _num(p["price"]), _num(p.get("sl")),
                  _num(p.get("tp"))]
    elif cmd.kind == "cancel_pending":
        fields = [int(p["order"])]
    else:
        raise ProtocolError(f"unknown command kind {cmd.kind!r}")
    return "\t".join(["CMD", str(int(cmd.id)), cmd.kind] + [_field(f) for f in fields])


def encode_response(status: str, server_ms: int, next_poll_ms: int, commands: list[Command],
                    last_deal_ticket: int | None = None, reason: str | None = None) -> str:
    """The whole response: one status line, then one CMD line per command.

    status: "OK" | "RETRY" | "STOP". For STOP the third field is the reason
    text (the EA shows it and stops polling). The hello response passes
    last_deal_ticket so a restarted EA resumes its deal watermark.
    """
    if status == "STOP":
        head = ["STOP", str(int(server_ms)), _field(reason or "stopped")]
    elif status in ("OK", "RETRY"):
        head = [status, str(int(server_ms)), str(int(next_poll_ms))]
        if last_deal_ticket is not None:
            head.append(str(int(last_deal_ticket)))
    else:
        raise ProtocolError(f"unknown status {status!r}")
    lines = ["\t".join(head)] + [command_line(c) for c in commands]
    return "\n".join(lines) + "\n"


def parse_response(text: str) -> tuple[list[str], list[Command]]:
    """The EA's side of the format, kept here so the fake EA and the tests
    prove the encoder against the same rules the real EA follows.

    Returns (status line fields, commands). Prices and lots come back as
    floats (a "0" is 0.0 -- "none"), tickets and expiry as ints."""
    lines = [line for line in text.split("\n") if line != ""]
    if not lines:
        raise ProtocolError("empty response")
    status = lines[0].split("\t")
    commands: list[Command] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if parts[0] != "CMD" or len(parts) < 3:
            raise ProtocolError(f"bad command line {line!r}")
        kind, rest = parts[2], parts[3:]
        coid: str | None = None
        try:
            command_id = int(parts[1])
            if kind == "open":
                symbol, side, lots_, sl, tp, comment, coid = rest
                payload = {"symbol": symbol, "side": side, "lots": float(lots_),
                           "sl": float(sl), "tp": float(tp), "comment": comment}
            elif kind == "close":
                position, lots_ = rest
                payload = {"position": int(position), "lots": float(lots_)}
            elif kind == "amend":
                position, sl, tp = rest
                payload = {"position": int(position), "sl": float(sl), "tp": float(tp)}
            elif kind == "place_pending":
                symbol, type_, lots_, price, sl, tp, expiry, comment, coid = rest
                payload = {"symbol": symbol, "type": type_, "lots": float(lots_),
                           "price": float(price), "sl": float(sl), "tp": float(tp),
                           "expiry_ms": int(expiry), "comment": comment}
            elif kind == "amend_pending":
                order, lots_, price, sl, tp = rest
                payload = {"order": int(order), "lots": float(lots_), "price": float(price),
                           "sl": float(sl), "tp": float(tp)}
            elif kind == "cancel_pending":
                (order,) = rest
                payload = {"order": int(order)}
            else:
                raise ProtocolError(f"unknown command kind {kind!r}")
        except ValueError as e:
            # A wrong field count unpacks to ValueError, as does a non-number.
            raise ProtocolError(f"bad command line {line!r}: {e}")
        commands.append(Command(id=command_id, kind=kind, payload=payload,
                                client_order_id=coid or None))
    return status, commands
