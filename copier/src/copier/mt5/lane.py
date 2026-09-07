"""Operator actions and read models for ONE MT5 account.

The cTrader paths in main.py talk to a client: reconcile for the live
book, DealList for history, a trade request for an action. An MT5 account
has no client -- it has the registry (the terminal's last report), the
outbox (what the terminal will do next) and the deals table (what it has
done). This lane answers the same calls from those three, in the same
shapes, so the control routes and the dashboard see one kind of account.
"""

import logging
import math

from twisted.internet import defer, task

from copier.domain.models import MANUAL_ORDER_LABEL
from copier.engine.queries import _lots
from copier.mt5.protocol import CENTILOTS, lots

log = logging.getLogger(__name__)

TRADE_SIDES = ("BUY", "SELL")


def validated_price(name: str, raw) -> float | None:
    """A positive, finite price; None for absent/empty; ValueError otherwise.
    Shared with CopierApp.amend_position_sltp."""
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive, finite price")
    return value


class MT5Lane:
    def __init__(self, app):
        """app: CopierApp -- read for repo, mt5_registry, mt5_outbox, clock
        and _org_for_account, so the lane sees exactly the app's wiring."""
        self._app = app

    @property
    def _repo(self):
        return self._app.repo

    @property
    def _registry(self):
        return self._app.mt5_registry

    @property
    def _outbox(self):
        return self._app.mt5_outbox

    def _clock(self):
        clock = self._app.clock
        if clock is None:
            from twisted.internet import reactor as clock
        return clock

    def _org(self, account_id: int) -> int:
        org_id = self._app._org_for_account(account_id)
        if org_id is None:
            raise ValueError(f"account {account_id} not found")
        return org_id

    def _position(self, account_id: int, position_id: int):
        pos = self._registry.position(account_id, position_id)
        if pos is None:
            raise ValueError(f"position {position_id} not found on account {account_id}")
        return pos

    def _digits(self, account_id: int, symbol: str) -> int:
        info = self._registry.symbol_by_name(account_id, symbol)
        return info.digits if info is not None else 5

    @staticmethod
    def _rounded(price, digits: int) -> float:
        """Payload prices are already at the symbol's digits (contract §1);
        0.0 means none."""
        return round(float(price), digits) if price is not None else 0.0

    # ---------- operator actions ----------

    def place_order(self, account_id, org_id, symbol, side, order_type, volume_lots,
                    limit_price, stop_price, stop_loss, take_profit, actor) -> dict:
        info = self._registry.symbol_by_name(account_id, symbol)
        if info is None:
            raise ValueError(f"unknown symbol {symbol!r} for account {account_id}")
        volume = int(round(float(volume_lots) * CENTILOTS))
        if info.step_volume:
            volume -= volume % info.step_volume
        if volume <= 0 or volume < info.min_volume:
            raise ValueError(f"volume {volume_lots} lots is below the minimum for {symbol}")
        sl = self._rounded(stop_loss, info.digits)
        tp = self._rounded(take_profit, info.digits)
        if order_type == "MARKET":
            kind = "open"
            payload = {"symbol": symbol, "side": side, "lots": lots(volume), "sl": sl, "tp": tp,
                       "comment": MANUAL_ORDER_LABEL}
        else:
            price = limit_price if order_type == "LIMIT" else stop_price
            kind = "place_pending"
            payload = {"symbol": symbol, "type": f"{side}_{order_type}", "lots": lots(volume),
                       "price": self._rounded(price, info.digits), "sl": sl, "tp": tp,
                       "expiry_ms": 0, "comment": MANUAL_ORDER_LABEL}
        command_id = self._outbox.enqueue(account_id, org_id, kind, payload, None)
        summary = {"status": "submitted", "account_id": account_id, "symbol": symbol,
                   "side": side, "order_type": order_type, "volume": volume,
                   "volume_lots": f"{volume / CENTILOTS:.2f}", "command_id": command_id}
        protection = ({'stop_loss': sl or None, 'take_profit': tp or None}
                      if (sl or tp) else None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'manual_order',
             **{k: v for k, v in summary.items() if k != 'status'},
             **({'protection': protection} if protection else {})},
            account_id=account_id, org_id=org_id, actor=actor)
        return summary

    def close_position(self, account_id, position_id, volume_lots, actor) -> dict:
        org_id = self._org(account_id)
        pos = self._position(account_id, int(position_id))
        volume = pos.volume
        if volume_lots is not None:
            volume = min(int(round(float(volume_lots) * CENTILOTS)), pos.volume)
            if volume <= 0:
                raise ValueError("volume_lots must be greater than 0")
        payload = {"position": pos.ticket,
                   "lots": 0.0 if volume >= pos.volume else lots(volume)}
        command_id = self._outbox.enqueue(account_id, org_id, "close", payload, None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'manual_close', 'position_id': pos.ticket, 'volume': volume,
             'command_id': command_id},
            account_id=account_id, org_id=org_id, actor=actor)
        return {"status": "submitted", "account_id": account_id, "position_id": pos.ticket,
                "volume": volume, "command_id": command_id}

    def amend_position_sltp(self, account_id, position_id, stop_loss, take_profit, actor) -> dict:
        org_id = self._org(account_id)
        sl = validated_price("stop_loss", stop_loss)
        tp = validated_price("take_profit", take_profit)
        pos = self._position(account_id, int(position_id))
        digits = self._digits(account_id, pos.symbol)
        sl = round(sl, digits) if sl is not None else None
        tp = round(tp, digits) if tp is not None else None
        payload = {"position": pos.ticket, "sl": sl if sl is not None else 0.0,
                   "tp": tp if tp is not None else 0.0}
        command_id = self._outbox.enqueue(account_id, org_id, "amend", payload, None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'amend_sltp', 'position_id': pos.ticket, 'stop_loss': sl,
             'take_profit': tp, 'command_id': command_id},
            account_id=account_id, org_id=org_id, actor=actor)
        return {"status": "submitted", "account_id": account_id, "position_id": pos.ticket,
                "stop_loss": sl, "take_profit": tp, "command_id": command_id}

    def cancel_order(self, account_id, order_id, actor) -> dict:
        org_id = self._org(account_id)
        command_id = self._outbox.enqueue(account_id, org_id, "cancel_pending",
                                          {"order": int(order_id)}, None)
        self._repo.log_event(
            'control', 'info',
            {'action': 'manual_cancel', 'order_id': int(order_id), 'command_id': command_id},
            account_id=account_id, org_id=org_id, actor=actor)
        return {"status": "submitted", "account_id": account_id, "order_id": int(order_id),
                "command_id": command_id}

    @defer.inlineCallbacks
    def flatten(self, account_id: int):
        """Close everything the terminal reports, and VERIFY from its later
        reports -- the same rounds/settle/verified-count contract as
        CopierApp._flatten_account, with the terminal's own book as the
        only evidence. A close already queued for a ticket is not queued
        again: the terminal would refuse the second one."""
        from copier import main as app_main   # constants live with the cTrader flatten
        org_id = self._org(account_id)
        if self._registry.report(account_id) is None:
            raise ValueError(
                f"MT5 account {account_id} has not reported yet: is the terminal connected?")
        clock = self._clock()

        first_positions: set[int] | None = None
        first_orders: set[int] | None = None
        open_positions: list[int] = []
        open_orders: list[int] = []
        rounds_used = 0

        for attempt in range(app_main.FLATTEN_ROUNDS):
            report = self._registry.report(account_id)
            positions, orders = list(report.positions), list(report.orders)
            if first_positions is None:
                first_positions = {p.ticket for p in positions}
                first_orders = {o.ticket for o in orders}
            open_positions = [p.ticket for p in positions]
            open_orders = [o.ticket for o in orders]
            if not positions and not orders:
                break

            rounds_used = attempt + 1
            queued = self._repo.mt5_commands_open(account_id)
            closing = {r["payload"].get("position") for r in queued if r["kind"] == "close"}
            cancelling = {r["payload"].get("order") for r in queued
                          if r["kind"] == "cancel_pending"}
            for p in positions:
                if p.ticket not in closing:
                    self._outbox.enqueue(account_id, org_id, "close",
                                         {"position": p.ticket, "lots": 0.0}, None)
            for o in orders:
                if o.ticket not in cancelling:
                    self._outbox.enqueue(account_id, org_id, "cancel_pending",
                                         {"order": o.ticket}, None)

            yield task.deferLater(
                clock, app_main.FLATTEN_SETTLE_S * (2 ** attempt), lambda: None)
        else:
            report = self._registry.report(account_id)
            open_positions = [p.ticket for p in report.positions]
            open_orders = [o.ticket for o in report.orders]

        first_positions = first_positions or set()
        first_orders = first_orders or set()
        still_open = first_positions & set(open_positions)
        still_working = first_orders & set(open_orders)
        positions_closed = len(first_positions) - len(still_open)
        orders_cancelled = len(first_orders) - len(still_working)

        summary = {
            "account_id": account_id,
            "positions_closed": positions_closed,
            "orders_cancelled": orders_cancelled,
            "positions_remaining": open_positions,
            "orders_remaining": open_orders,
            "rounds": rounds_used,
            "error": None,
        }
        if open_positions or open_orders:
            summary["error"] = (
                f"{len(open_positions)} position(s) and {len(open_orders)} "
                f"order(s) still open after {rounds_used} attempt(s)")

        self._repo.log_event(
            'control', 'error' if summary["error"] else 'warning',
            {'action': 'kill_switch_flatten',
             'positions_closed': positions_closed,
             'orders_cancelled': orders_cancelled,
             'positions_remaining': open_positions,
             'orders_remaining': open_orders,
             'rounds': rounds_used},
            account_id=account_id, org_id=org_id,
        )
        return summary

    # ---------- read models ----------

    def _deal(self, row: dict) -> dict:
        deal = dict(row)
        deal["volume_lots"] = _lots(deal.get("filled_volume"), CENTILOTS)
        if deal.get("close"):
            deal["close"] = {**deal["close"],
                             "closed_volume_lots": _lots(deal["close"].get("closed_volume"),
                                                         CENTILOTS)}
        deal["balance_after_estimated"] = True
        return deal

    def deal_history(self, account_id, from_ms, to_ms) -> dict:
        rows = self._repo.load_deals(account_id, since_ms=from_ms, until_ms=to_ms)
        return {"deals": [self._deal(r) for r in rows if r.get("side") in TRADE_SIDES],
                "has_more": False}

    def order_history(self, account_id, from_ms, to_ms) -> dict:
        """One FILLED order per trade deal: MT5 keeps order history, but a
        deal is the fact the History page shows, and the contract derives."""
        orders = []
        for r in self._repo.load_deals(account_id, since_ms=from_ms, until_ms=to_ms):
            if r.get("side") not in TRADE_SIDES:
                continue
            orders.append({
                "order_id": r["order_id"], "symbol_id": r["symbol_id"], "symbol": r["symbol"],
                "side": r["side"], "volume": r["volume"],
                "volume_lots": _lots(r["volume"], CENTILOTS), "order_type": "MARKET",
                "status": "FILLED", "limit_price": None, "stop_price": None,
                "execution_price": r["execution_price"], "executed_volume": r["filled_volume"],
                "position_id": r["position_id"], "label": "",
                "open_timestamp": r["execution_timestamp"],
                "update_timestamp": r["execution_timestamp"],
                "stop_loss": None, "take_profit": None,
            })
        return {"orders": orders, "has_more": False}

    def cash_flow(self, account_id, from_ms, to_ms) -> dict:
        entries = []
        for r in self._repo.load_mt5_cash_flow(account_id, from_ms, to_ms):
            if r["side"] == "CREDIT":
                kind = "CREDIT"
            else:
                kind = "DEPOSIT" if r["amount"] >= 0 else "WITHDRAW"
            entries.append({"id": r["deal_id"], "type": kind, "amount": r["amount"],
                            "balance_after": r["balance_after"], "timestamp": r["timestamp"],
                            "note": None})
        return {"entries": entries}

    def position_deals(self, account_id, position_id, from_ms, to_ms) -> dict:
        rows = self._repo.load_deals(account_id, since_ms=from_ms, until_ms=to_ms,
                                     position_id=position_id)
        return {"deals": [self._deal(r) for r in rows], "has_more": False}

    def details(self, account_id) -> dict:
        """queries.account_details' shape from the link row and the last
        report, plus "platform" and the "mt5" block the api merges."""
        link = self._repo.load_mt5_link(account_id) or {}
        report = self._registry.report(account_id)
        symbols = self._registry.symbols_by_name(account_id)

        def symbol_id(name):
            info = symbols.get(name)
            return info.symbol_id if info is not None else None

        open_positions = [
            {"position_id": p.ticket, "symbol_id": symbol_id(p.symbol), "symbol": p.symbol,
             "side": p.side, "volume": p.volume, "volume_lots": _lots(p.volume, CENTILOTS),
             "price": p.open_price, "label": p.comment, "stop_loss": p.stop_loss,
             "take_profit": p.take_profit, "swap": p.swap,
             "open_timestamp": p.opened_at_ms or None}
            for p in (report.positions if report is not None else [])
        ]
        pending_orders = [
            {"order_id": o.ticket, "symbol_id": symbol_id(o.symbol), "symbol": o.symbol,
             "side": o.order_type.split("_", 1)[0], "volume": o.volume,
             "volume_lots": _lots(o.volume, CENTILOTS),
             "order_type": o.order_type.split("_", 1)[1],
             "limit_price": o.price if o.order_type.endswith("LIMIT") else None,
             "stop_price": o.price if o.order_type.endswith("STOP") else None,
             "label": o.comment}
            for o in (report.orders if report is not None else [])
        ]
        hedging = link.get("hedging")
        last_seen = link.get("last_seen_at")
        return {
            "account_id": account_id,
            "trader_login": link.get("login"),
            "balance": report.balance if report is not None else link.get("balance"),
            "money_digits": 2,
            "deposit_currency": link.get("currency"),
            "leverage": link.get("leverage"),
            "max_leverage": None,
            "broker_name": link.get("broker"),
            "registration_timestamp": None,
            "account_type": "HEDGED" if hedging else ("NETTED" if hedging is False else "UNKNOWN"),
            "access_rights": "FULL_ACCESS",
            "swap_free": None,
            "is_limited_risk": False,
            "open_positions": open_positions,
            "pending_orders": pending_orders,
            "platform": "mt5",
            "mt5": {
                "login": link.get("login"), "broker": link.get("broker"),
                "server": link.get("server"), "currency": link.get("currency"),
                "hedging": hedging, "trade_mode": link.get("trade_mode"),
                "ea_version": link.get("ea_version"), "ea_build": link.get("ea_build"),
                "last_seen_at": last_seen.isoformat() if last_seen else None,
                "connected": self._registry.is_online(account_id, self._clock().seconds()),
            },
        }
