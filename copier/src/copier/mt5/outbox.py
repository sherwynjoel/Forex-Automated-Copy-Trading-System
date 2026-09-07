"""The command outbox: what the copier wants an MT5 terminal to do.

Nothing can push to a terminal, so every decision the engine makes for an
MT5 slave -- and every operator action on an MT5 account -- becomes a row
in mt5_commands that the terminal collects on its next poll (queued ->
sent) and settles with an acknowledgement (done / failed). The rows are
Postgres, not memory: a copier restart loses nothing that was decided.

Delivery rules (spec, "Ordering guarantees"): commands go out in id order;
a `sent` command without an ack is re-delivered after REDELIVER_AFTER_S at
most MAX_ATTEMPTS times, then failed as "no ack from terminal". An `open`
or `place_pending` not delivered within OPEN_COMMAND_TTL_S is failed as
"terminal offline" and its mapping with it -- a market copy placed minutes
late is a different trade. close/amend/cancel never expire.

Netting followers (spec "Netting accounts"): every copy on a symbol lives
inside the symbol's single net position, so a mapping cannot be closed by
ticket -- the ticket is shared. Its close is an OPEN on the opposite side
for the mapping's volume (that is how a netting account reduces), queued
under the mapping's own client_order_id plus CLOSE_SUFFIX, so the ack
comes back naming exactly that mapping; apply_acks strips the suffix and
reports it as a close. A close without a master id (an operator's, Close
all's, the reconciler's) is a plain close on the net ticket, on both
modes. The ':close' opens never expire (repo.fail_stale_mt5_opens).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from copier.db.repo import MappingNotFound
from copier.domain.models import (
    AmendPending, AmendPositionSLTP, CancelPending, ClosePosition, OpenMarket, PendingType,
    PlacePending, SlaveIntent)
from copier.engine.dispatch import client_order_id_for
from copier.mt5.protocol import Ack, Command, lots

log = logging.getLogger(__name__)

OPEN_COMMAND_TTL_S = 30.0
REDELIVER_AFTER_S = 10.0
MAX_ATTEMPTS = 3
NO_ACK_MESSAGE = "no ack from terminal"
OFFLINE_MESSAGE = "terminal offline"
CLOSE_SUFFIX = ":close"


@dataclass(frozen=True)
class AckOutcome:
    command_id: int
    kind: str
    client_order_id: str | None
    ok: bool
    message: str
    position: int | None
    order: int | None
    price: float | None
    volume: int | None                # centilots
    # The terminal's deal ticket for the executed command, so the deal it
    # reports in the same sync is not booked a second time (main.py).
    deal: int | None = None


def _price(value) -> float:
    """Payload prices: 0.0 means none (the contract's `0`/absent)."""
    return float(value) if value is not None else 0.0


def command_for_intent(intent: SlaveIntent, broker_symbol: str) -> tuple[str, dict]:
    """(kind, payload) per the contract's table. Centilots become lots HERE
    and nowhere else on the way out; prices pass through unchanged (a
    master's price is already at its digits; the lane rounds operator
    prices before they get here)."""
    if isinstance(intent, OpenMarket):
        return "open", {
            "symbol": broker_symbol, "side": intent.side.value, "lots": lots(intent.volume),
            "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit),
            "comment": intent.label,
        }
    if isinstance(intent, ClosePosition):
        return "close", {"position": intent.position_id, "lots": lots(intent.volume)}
    if isinstance(intent, AmendPositionSLTP):
        return "amend", {"position": intent.position_id,
                         "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit)}
    if isinstance(intent, PlacePending):
        kind = "LIMIT" if intent.order_type == PendingType.LIMIT else "STOP"
        return "place_pending", {
            "symbol": broker_symbol, "type": f"{intent.side.value}_{kind}",
            "lots": lots(intent.volume), "price": float(intent.price),
            "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit),
            "expiry_ms": int(intent.expiry_ts_ms or 0), "comment": intent.label,
        }
    if isinstance(intent, AmendPending):
        return "amend_pending", {
            "order": intent.order_id, "lots": lots(intent.volume), "price": float(intent.price),
            "sl": _price(intent.stop_loss), "tp": _price(intent.take_profit),
        }
    if isinstance(intent, CancelPending):
        return "cancel_pending", {"order": intent.order_id}
    raise ValueError(f"no MT5 command for intent {type(intent).__name__}")


def _ts(now: float) -> datetime:
    return datetime.fromtimestamp(now, tz=timezone.utc)


class MT5Outbox:
    def __init__(self, repo, clock=None, margin_mode=None):
        """margin_mode: account_id -> "hedging" | "netting" | None (the
        registry's margin_mode). None means every account is hedging."""
        self._repo = repo
        self._clock = clock
        self._margin_mode = margin_mode

    def _now(self) -> float:
        clock = self._clock
        if clock is None:
            from twisted.internet import reactor as clock
        return clock.seconds()

    def _is_netting(self, account_id: int) -> bool:
        return self._margin_mode is not None and self._margin_mode(account_id) == "netting"

    def enqueue_intent(self, intent: SlaveIntent, org_id: int, broker_symbol: str) -> int:
        account_id = intent.slave_account_id
        if (isinstance(intent, ClosePosition) and intent.master_position_id is not None
                and self._is_netting(account_id)):
            queued = self._enqueue_netting_close(intent, org_id)
            if queued is not None:
                return queued
        kind, payload = command_for_intent(intent, broker_symbol)
        return self.enqueue(account_id, org_id, kind, payload, client_order_id_for(intent))

    def _enqueue_netting_close(self, intent: ClosePosition, org_id: int) -> int | None:
        """A netting follower's close of ONE copy: an open on the opposite
        side of the mapping's own open, for the closed volume, under the
        mapping's coid + CLOSE_SUFFIX. None when the mapping has no open
        command to read a side from (an adopted mapping): the caller falls
        back to a plain close on the net ticket and the operator is told."""
        account_id = intent.slave_account_id
        coid = f"cm{intent.master_position_id}.{account_id}"     # client_order_id_for's scheme
        side = self._repo.mapping_side(coid)
        payload = self._repo.mt5_open_payload(coid) or {}
        symbol = payload.get("symbol")
        if side is None or not symbol:
            self._repo.log_event(
                'slave_action', 'warning',
                {'action': 'mt5_netting_close_unsided', 'client_order_id': coid,
                 'position': intent.position_id, 'volume': intent.volume,
                 'detail': 'no open command records this copy\'s side; closing the net '
                           'position by volume instead, which reduces the copies oldest-first'},
                account_id=account_id, org_id=org_id)
            return None
        # SL/TP 0 leaves the net position's levels alone; the real EA passes
        # them as given (Task 17's fake does the same).
        return self.enqueue(account_id, org_id, "open", {
            "symbol": symbol, "side": "SELL" if side == "BUY" else "BUY",
            "lots": lots(intent.volume), "sl": 0.0, "tp": 0.0,
            "comment": f"close:m{intent.master_position_id}",
        }, coid + CLOSE_SUFFIX)

    def enqueue(self, account_id: int, org_id: int, kind: str, payload: dict,
                client_order_id: str | None = None) -> int:
        return self._repo.enqueue_mt5_command(account_id, org_id, kind, payload, client_order_id)

    def deliverable(self, account_id: int, now: float) -> list[Command]:
        """What the terminal gets on this poll: expires stale opens, gives up
        on commands re-delivered MAX_ATTEMPTS times without an ack,
        re-delivers `sent` rows older than REDELIVER_AFTER_S, and marks
        everything returned as sent. `now` is epoch seconds."""
        for command_id in self._repo.fail_stale_mt5_opens(
                account_id, _ts(now - OPEN_COMMAND_TTL_S)):
            self._repo.log_event(
                'slave_action', 'warning',
                {'action': 'mt5_command_expired', 'command_id': command_id,
                 'reason': OFFLINE_MESSAGE,
                 'detail': f'market copy not delivered within {OPEN_COMMAND_TTL_S:.0f}s; '
                           'a copy placed that late is a different trade'},
                account_id=account_id)
        out: list[Command] = []
        ids: list[int] = []
        for row in self._repo.mt5_commands_open(account_id):
            if row["status"] == "sent":
                sent_at = row["sent_at"].timestamp() if row["sent_at"] is not None else now
                if now - sent_at < REDELIVER_AFTER_S:
                    continue
                if row["attempts"] >= MAX_ATTEMPTS:
                    self._give_up(account_id, row, now)
                    continue
            out.append(Command(id=row["id"], kind=row["kind"], payload=row["payload"],
                               client_order_id=row["client_order_id"]))
            ids.append(row["id"])
        if ids:
            self._repo.mark_mt5_commands_sent(ids, _ts(now))
        return out

    def _give_up(self, account_id: int, row: dict, now: float) -> None:
        result = {"ok": False, "retcode": 0, "message": NO_ACK_MESSAGE, "position": None,
                  "deal": None, "order": None, "price": None, "lots": None}
        self._repo.complete_mt5_command(row["id"], False, result, _ts(now), account_id=account_id)
        if row["client_order_id"]:
            try:
                self._repo.fail_mapping(account_id, row["client_order_id"], NO_ACK_MESSAGE)
            except MappingNotFound:
                pass
        self._repo.log_event(
            'slave_action', 'error',
            {'action': 'mt5_command_unacked', 'command_id': row["id"], 'kind': row["kind"],
             'attempts': row["attempts"], 'error': NO_ACK_MESSAGE},
            account_id=account_id, org_id=row["org_id"])

    def apply_acks(self, account_id: int, acks: list[Ack]) -> list[AckOutcome]:
        """Settle acked commands. Unknown, duplicate and foreign ids change
        nothing (the terminal re-acks re-delivered ids by design). A ticket
        the ack omits falls back to the payload's, so a close/cancel ack
        always names what it settled; a volume it omits falls back to the
        payload's lots. A netting follower's ':close' open is reported as
        kind "close" under the mapping's own coid."""
        out: list[AckOutcome] = []
        for ack in acks:
            result = {"ok": ack.ok, "retcode": ack.retcode, "message": ack.message,
                      "position": ack.position, "deal": ack.deal, "order": ack.order,
                      "price": ack.price,
                      "lots": lots(ack.volume) if ack.volume is not None else None}
            row = self._repo.complete_mt5_command(
                ack.command_id, ack.ok, result, _ts(self._now()), account_id=account_id)
            if row is None:
                continue
            payload = row["payload"] or {}
            kind, coid = row["kind"], row["client_order_id"]
            if kind == "open" and coid and coid.endswith(CLOSE_SUFFIX):
                kind, coid = "close", coid[:-len(CLOSE_SUFFIX)]
            volume = ack.volume
            if volume is None and payload.get("lots"):
                volume = int(round(float(payload["lots"]) * 100))
            out.append(AckOutcome(
                command_id=row["id"], kind=kind, client_order_id=coid,
                ok=ack.ok, message=ack.message,
                position=ack.position if ack.position is not None else payload.get("position"),
                order=ack.order if ack.order is not None else payload.get("order"),
                price=ack.price, volume=volume, deal=ack.deal,
            ))
        return out

    def pending_count(self, account_id: int) -> int:
        return len(self._repo.mt5_commands_open(account_id))
