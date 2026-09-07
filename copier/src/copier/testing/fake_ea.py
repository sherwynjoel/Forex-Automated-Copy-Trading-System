"""A Python stand-in for MirrorFleet.mq5.

Speaks the exact wire format (copier/mt5/protocol.py) against an in-memory
book in HEDGING or NETTING mode: every command kind executes, positions/
orders/deals are reported back, acks carry what the real EA's CTrade
results would. Used by the integration tests through the copier directly
(hello_fn/sync_fn, no HTTP) or -- with base_url -- over HTTP: the api's
machine door when `key` is given (header X-MirrorFleet-Key), else the
copier control port (JSON bodies carrying account_id).

What it models of the real EA's discipline:
- executed command ids live in memory AND in an ack "file"
  (MQL5/Files/MirrorFleet/<login>.acks, last 500); a re-delivered id is
  re-acked from the file, never re-executed -- restart() forgets memory
  but keeps the file;
- acks ride the next sync and are dropped only after an OK response;
- deals are sent with ticket > watermark, the watermark being the server's
  (hello response) and then the last ticket the server accepted;
- an amend that changes nothing acks ok (TRADE_RETCODE_NO_CHANGES is not
  a failure for the EA either), and every price is used as given -- the
  real EA normalises to the symbol's digits before calling CTrade.

The netting book (mode="netting", ACCOUNT_MARGIN_MODE_RETAIL_NETTING):
one position per symbol. An open on the same side adds to it (an IN deal,
volume-weighted open price); on the opposite side it reduces it (OUT),
closes it (OUT, the position is gone) or reverses it (one INOUT deal for
the order's whole volume; the position keeps its ticket and turns to the
new side with the excess). The ack of an open reports, as the real EA's
DoOpen does, the ticket of the net position the terminal shows AFTER the
fill -- pos 0 when the fill emptied it -- and the fill's deal ticket; the
copier pairs the two by the deal ticket and reads the deal's entry. A
non-zero SL/TP on the order replaces the position's, as
CTrade.PositionOpen does on a netting account; zero leaves them. The
hello says hedging:false.
"""

import itertools
import json
import time
import urllib.request

from copier.mt5 import protocol as p

DEFAULT_SYMBOLS = [
    {"n": "EURUSD.r", "d": 5, "cs": 100000, "vmin": 0.01, "vstep": 0.01, "vmax": 100, "tm": 4},
    {"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01, "vmax": 50, "tm": 4},
]
DEFAULT_PRICES = {"EURUSD.r": (1.10000, 1.10010), "XAUUSD.r": (2400.00, 2400.30)}
ACK_FILE_LIMIT = 500
RETCODE_DONE = 10009
RETCODE_INVALID = 10013
RETCODE_POSITION_CLOSED = 10036
MODES = ("hedging", "netting")


class FakeEA:
    def __init__(self, base_url=None, key=None, account_id=None, *, hello_fn=None, sync_fn=None,
                 login=12345678, broker="Fake Broker Ltd", server="Fake-Demo", currency="USD",
                 mode="hedging", symbols=None, prices=None, balance=10000.0, magic=20260907):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self.base_url = base_url
        self.key = key
        self.account_id = account_id
        self._hello_fn = hello_fn
        self._sync_fn = sync_fn
        self.login, self.broker, self.server, self.currency = login, broker, server, currency
        self.mode = mode
        self.hedging = mode == "hedging"
        self.symbols = [dict(s) for s in (symbols if symbols is not None else DEFAULT_SYMBOLS)]
        self.prices = dict(prices or DEFAULT_PRICES)
        self.balance = float(balance)
        self.magic = magic
        # The terminal's book (the broker side: survives restart()).
        self.positions: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self.deals: list[dict] = []
        self._tickets = itertools.count(700001)
        # The EA's own state.
        self._pending_acks: list[dict] = []
        self._memory_ids: set[int] = set()
        self._file_acks: dict[int, dict] = {}
        self._reject_next: tuple[int, str] | None = None
        self.connected = True
        self.needs_hello = True
        self.seq = 0
        self.watermark = 0
        self.executed: list[p.Command] = []
        self.responses: list[str] = []

    # ---------- the book ----------

    def _contract_size(self, symbol: str) -> float:
        return float(next((s["cs"] for s in self.symbols if s["n"] == symbol), 1.0))

    def set_price(self, symbol: str, bid: float, ask: float) -> None:
        self.prices[symbol] = (bid, ask)

    def _mark(self) -> None:
        for pos in self.positions.values():
            bid, ask = self.prices[pos["s"]]
            cs = self._contract_size(pos["s"])
            if pos["side"] == "BUY":
                pos["price"] = bid
                pos["pnl"] = round((bid - pos["open"]) * pos["lots"] * cs, 2)
            else:
                pos["price"] = ask
                pos["pnl"] = round((pos["open"] - ask) * pos["lots"] * cs, 2)

    def equity(self) -> float:
        self._mark()
        return round(self.balance + sum(pos["pnl"] for pos in self.positions.values()), 2)

    def _deal(self, deal, position, order, symbol, side, entry, lots, price, profit=0.0,
              comment=""):
        self.deals.append({
            "t": deal, "pos": position, "order": order, "s": symbol, "type": side, "entry": entry,
            "lots": float(lots), "price": price, "profit": round(profit, 2), "swap": 0.0,
            "commission": 0.0, "time": int(time.time() * 1000), "comment": comment,
            "magic": self.magic})

    def _new_position(self, ticket, symbol, side, lots, price, sl, tp, comment) -> dict:
        self.positions[ticket] = {
            "t": ticket, "s": symbol, "side": side, "lots": float(lots), "open": price,
            "sl": float(sl or 0), "tp": float(tp or 0), "price": price, "pnl": 0.0, "swap": 0.0,
            "comment": comment, "magic": self.magic, "time": int(time.time())}
        return self.positions[ticket]

    def _profit(self, pos: dict, lots: float, price: float) -> float:
        cs = self._contract_size(pos["s"])
        if pos["side"] == "BUY":
            return round((price - pos["open"]) * lots * cs, 2)
        return round((pos["open"] - price) * lots * cs, 2)

    def _net_position(self, symbol: str) -> dict | None:
        return next((pos for pos in self.positions.values() if pos["s"] == symbol), None)

    def _open(self, symbol, side, lots, sl, tp, comment):
        """A market order. Hedging: a new position every time. Netting: the
        symbol's single position absorbs it -- see the module docstring."""
        bid, ask = self.prices[symbol]
        price = ask if side == "BUY" else bid
        lots = float(lots)
        order, deal = next(self._tickets), next(self._tickets)
        pos = self._net_position(symbol) if self.mode == "netting" else None
        if pos is None:
            ticket = next(self._tickets)
            self._new_position(ticket, symbol, side, lots, price, sl, tp, comment)
            self._deal(deal, ticket, order, symbol, side, "IN", lots, price, comment=comment)
            return ticket, order, deal, price
        ticket = pos["t"]
        if sl:
            pos["sl"] = float(sl)
        if tp:
            pos["tp"] = float(tp)
        if pos["side"] == side:
            total = round(pos["lots"] + lots, 2)
            pos["open"] = round((pos["open"] * pos["lots"] + price * lots) / total, 5)
            pos["lots"] = total
            self._deal(deal, ticket, order, symbol, side, "IN", lots, price, comment=comment)
            return ticket, order, deal, price
        closing = min(lots, pos["lots"])
        profit = self._profit(pos, closing, price)
        self.balance = round(self.balance + profit, 2)
        remainder = round(lots - pos["lots"], 2)
        if remainder > 0:
            # A reversal: the whole old volume closes and the excess opens the
            # other way -- one INOUT deal for the order's whole volume.
            self._deal(deal, ticket, order, symbol, side, "INOUT", lots, price, profit,
                       comment=comment)
            pos.update({"side": side, "lots": remainder, "open": price, "comment": comment,
                        "time": int(time.time())})
        else:
            self._deal(deal, ticket, order, symbol, side, "OUT", closing, price, profit,
                       comment=comment)
            pos["lots"] = round(pos["lots"] - closing, 2)
            if pos["lots"] <= 0:
                del self.positions[ticket]
                # The real EA acks the ticket the terminal shows AFTER the
                # fill (plan 04 DoOpen: PositionSelect(symbol)) -- 0 when the
                # fill emptied the net position. The deal still names it.
                ticket = 0
        return ticket, order, deal, price

    def _close(self, ticket, lots):
        """PositionClose / PositionClosePartial on a ticket, in both modes."""
        pos = self.positions[ticket]
        close_lots = pos["lots"] if not lots or float(lots) >= pos["lots"] else float(lots)
        bid, ask = self.prices[pos["s"]]
        price = bid if pos["side"] == "BUY" else ask
        profit = self._profit(pos, close_lots, price)
        self.balance = round(self.balance + profit, 2)
        deal, order = next(self._tickets), next(self._tickets)
        self._deal(deal, ticket, order, pos["s"], "SELL" if pos["side"] == "BUY" else "BUY",
                   "OUT", close_lots, price, profit, comment=pos["comment"])
        pos["lots"] = round(pos["lots"] - close_lots, 2)
        if pos["lots"] <= 0:
            del self.positions[ticket]
        return deal, order, price, close_lots

    # Terminal-side actions: the owner trading by hand, a stop being hit.

    def market_order(self, symbol, side, lots, sl=0.0, tp=0.0, comment="") -> int:
        """The owner trades by hand. Returns the position ticket -- 0 when a
        netting fill emptied the net position, as the EA's ack would say."""
        ticket, _order, _deal, _price = self._open(symbol, side, lots, sl, tp, comment)
        return ticket

    def close_by_terminal(self, ticket, lots=0.0) -> None:
        self._close(ticket, lots)

    def modify_by_terminal(self, ticket, sl=0.0, tp=0.0) -> None:
        pos = self.positions[ticket]
        pos["sl"], pos["tp"] = float(sl or 0), float(tp or 0)

    def place_pending_by_terminal(self, symbol, order_type, lots, price, sl=0.0, tp=0.0,
                                  comment="") -> int:
        ticket = next(self._tickets)
        self.orders[ticket] = {"t": ticket, "s": symbol, "type": order_type, "lots": float(lots),
                               "price": float(price), "sl": float(sl or 0), "tp": float(tp or 0),
                               "comment": comment, "magic": self.magic}
        return ticket

    def cancel_by_terminal(self, ticket) -> None:
        del self.orders[ticket]

    def fill_pending(self, ticket) -> int:
        """The market reached a pending order: it becomes a position whose
        opening deal names the order, as MT5 does. (Hedging book only: the
        netting scenarios drive pendings through the copier's commands.)"""
        o = self.orders.pop(ticket)
        side = o["type"].split("_", 1)[0]
        position, deal = next(self._tickets), next(self._tickets)
        self._new_position(position, o["s"], side, o["lots"], o["price"], o["sl"], o["tp"],
                           o["comment"])
        self._deal(deal, position, ticket, o["s"], side, "IN", o["lots"], o["price"],
                   comment=o["comment"])
        return position

    def deposit(self, amount: float) -> None:
        self.balance = round(self.balance + amount, 2)
        self._deal(next(self._tickets), 0, 0, "", "BALANCE", "", 0.0, 0.0, profit=amount)

    # ---------- the EA ----------

    def reject_next(self, retcode: int, message: str) -> None:
        self._reject_next = (retcode, message)

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    def restart(self) -> None:
        """The terminal restarts: EA memory (executed ids, unsent acks, seq,
        watermark) is gone; the ack file and the book survive; the next
        tick says hello again."""
        self._memory_ids.clear()
        self._pending_acks.clear()
        self.seq = 0
        self.watermark = 0
        self.needs_hello = True
        self.connected = True

    def hello_body(self) -> dict:
        return {"v": p.PROTOCOL_VERSION, "ea": "1.0.0", "build": 4400, "login": self.login,
                "broker": self.broker, "server": self.server, "currency": self.currency,
                "hedging": self.hedging, "trade_mode": "demo", "leverage": 500,
                "symbols": [dict(s) for s in self.symbols], "chunk": 1, "chunks": 1}

    def sync_body(self) -> dict:
        self.seq += 1
        self._mark()
        deals = [dict(d) for d in self.deals if d["t"] > self.watermark][:p.MAX_DEALS_PER_SYNC]
        return {"v": p.PROTOCOL_VERSION, "seq": self.seq, "ts": int(time.time() * 1000),
                "balance": round(self.balance, 2), "equity": self.equity(), "margin": 0.0,
                "margin_free": self.equity(),
                "positions": [dict(pos) for pos in self.positions.values()],
                "orders": [dict(o) for o in self.orders.values()],
                "deals": deals, "acks": [dict(a) for a in self._pending_acks]}

    def tick(self):
        """One EA timer tick: hello if needed, then one sync; executes every
        command in the response. Returns the status line fields, or None
        while disconnected."""
        if not self.connected:
            return None
        if self.needs_hello:
            reply = self._post("hello", self.hello_body())
            self.watermark = int(reply.get("last_deal_ticket") or 0)
            self.needs_hello = False
        body = self.sync_body()
        sent_acks = list(body["acks"])
        text = self._post("sync", body)
        self.responses.append(text)
        status, commands = p.parse_response(text)
        if status[0] == "OK":
            self._pending_acks = [a for a in self._pending_acks if a not in sent_acks]
            if body["deals"]:
                self.watermark = max(self.watermark, max(d["t"] for d in body["deals"]))
            for cmd in commands:
                self.execute(cmd)
        elif status[0] == "STOP":
            self.connected = False
        return status

    def _post(self, kind: str, body: dict):
        if kind == "hello" and self._hello_fn is not None:
            return self._hello_fn(body)
        if kind == "sync" and self._sync_fn is not None:
            return self._sync_fn(body)
        if self.key is not None:
            url = f"{self.base_url}/api/mt5/{kind}"
            headers = {"Content-Type": "application/json", "X-MirrorFleet-Key": self.key}
            payload = body
        else:
            url = f"{self.base_url}/mt5/{kind}"
            headers = {"Content-Type": "application/json"}
            payload = {"account_id": self.account_id, "report": body}
        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            text = response.read().decode()
        return json.loads(text) if kind == "hello" else text

    def execute(self, cmd: p.Command) -> None:
        if cmd.id in self._file_acks or cmd.id in self._memory_ids:
            self._pending_acks.append(dict(self._file_acks[cmd.id]))   # re-acked, never re-run
            return
        ack = self._run(cmd)
        self._memory_ids.add(cmd.id)
        self._file_acks[cmd.id] = ack
        while len(self._file_acks) > ACK_FILE_LIMIT:
            self._file_acks.pop(next(iter(self._file_acks)))
        self._pending_acks.append(dict(ack))
        self.executed.append(cmd)

    def _run(self, cmd: p.Command) -> dict:
        if self._reject_next is not None:
            retcode, message = self._reject_next
            self._reject_next = None
            return self._ack(cmd.id, False, retcode, message)
        payload = cmd.payload
        try:
            if cmd.kind == "open":
                ticket, order, deal, price = self._open(
                    payload["symbol"], payload["side"], payload["lots"], payload.get("sl"),
                    payload.get("tp"), payload.get("comment", ""))
                return self._ack(cmd.id, True, RETCODE_DONE, "done", pos=ticket, deal=deal,
                                 order=order, price=price, lots=float(payload["lots"]))
            if cmd.kind == "close":
                deal, order, price, lots = self._close(int(payload["position"]), payload.get("lots"))
                return self._ack(cmd.id, True, RETCODE_DONE, "done", pos=int(payload["position"]),
                                 deal=deal, order=order, price=price, lots=lots)
            if cmd.kind == "amend":
                pos = self.positions[int(payload["position"])]
                pos["sl"] = float(payload.get("sl") or 0)
                pos["tp"] = float(payload.get("tp") or 0)
                return self._ack(cmd.id, True, RETCODE_DONE, "done", pos=pos["t"])
            if cmd.kind == "place_pending":
                ticket = next(self._tickets)
                self.orders[ticket] = {
                    "t": ticket, "s": payload["symbol"], "type": payload["type"],
                    "lots": float(payload["lots"]), "price": float(payload["price"]),
                    "sl": float(payload.get("sl") or 0), "tp": float(payload.get("tp") or 0),
                    "comment": payload.get("comment", ""), "magic": self.magic}
                return self._ack(cmd.id, True, RETCODE_DONE, "done", order=ticket,
                                 price=float(payload["price"]), lots=float(payload["lots"]))
            if cmd.kind == "amend_pending":
                o = self.orders[int(payload["order"])]
                o["lots"] = float(payload["lots"])
                o["price"] = float(payload["price"])
                o["sl"] = float(payload.get("sl") or 0)
                o["tp"] = float(payload.get("tp") or 0)
                return self._ack(cmd.id, True, RETCODE_DONE, "done", order=o["t"])
            if cmd.kind == "cancel_pending":
                del self.orders[int(payload["order"])]
                return self._ack(cmd.id, True, RETCODE_DONE, "done", order=int(payload["order"]))
        except KeyError as missing:
            return self._ack(cmd.id, False, RETCODE_POSITION_CLOSED, f"no such ticket {missing}")
        return self._ack(cmd.id, False, RETCODE_INVALID, f"unknown command {cmd.kind}")

    @staticmethod
    def _ack(command_id, ok, retcode, message, pos=None, deal=None, order=None, price=None,
             lots=None) -> dict:
        return {"id": command_id, "ok": ok, "retcode": retcode, "msg": message, "pos": pos,
                "deal": deal, "order": order, "price": price, "lots": lots}
