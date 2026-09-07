"""An MT5 master's report -> the MasterEvents the decision core consumes.

This is normalize.py's counterpart for a terminal that reports state
rather than pushing execution events: deals say what was traded, and the
difference between this report's book and the previous one says what was
amended, placed, replaced or cancelled. Symbol names are translated from
the broker's to the canonical names the followers know before anything
reaches decide().

Netting masters (spec "Netting accounts"): the terminal holds ONE position
per symbol, so its deals cannot be copied as positions one-to-one. The
NetLedger expands the net position into VIRTUAL master positions: an IN
deal opens one (its id is the deal ticket), an OUT deal consumes them
oldest-first, an INOUT closes them all and opens the remainder the other
way, and a stop/target change on the net position reaches every virtual
position of the symbol. Followers see MasterPositionOpened/Closed/
SLTPAmended by virtual id exactly as they would a hedging master's, and
the ledger's rows are persisted by the caller (mt5_net_ledger) so a
restart keeps them.
"""

from dataclasses import dataclass

from copier.domain.models import (
    MasterEvent, MasterPendingCancelled, MasterPendingFilled, MasterPendingPlaced,
    MasterPendingReplaced, MasterPositionClosed, MasterPositionOpened,
    MasterPositionSLTPAmended, PendingType, Side, SymbolInfo)
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.protocol import CENTILOTS, ReportDeal, SyncReport

CLOSE_ENTRIES = ("OUT", "OUT_BY", "INOUT")
TRADE_TYPES = ("BUY", "SELL")


@dataclass
class VirtualPosition:
    """One row of mt5_net_ledger: a slice of a netting master's net position
    that followers copy as a position of its own. Its id is the IN deal's
    ticket, which is what the followers' mapping rows carry as
    master_position_id."""
    virtual_id: int
    symbol: str                       # broker name
    side: str                         # "BUY" | "SELL"
    volume_open: int                  # centilots at open
    volume_left: int                  # centilots still open
    stop_loss: float | None
    take_profit: float | None
    opened_at_ms: int

    def row(self) -> dict:
        """The repo's key shape (Repo.upsert_net_ledger)."""
        return {"virtual_id": self.virtual_id, "symbol": self.symbol, "side": self.side,
                "volume_open": self.volume_open, "volume_left": self.volume_left,
                "stop_loss": self.stop_loss, "take_profit": self.take_profit,
                "opened_at_ms": self.opened_at_ms}

    @classmethod
    def from_row(cls, row: dict) -> "VirtualPosition":
        return cls(virtual_id=int(row["virtual_id"]), symbol=row["symbol"], side=row["side"],
                   volume_open=int(row["volume_open"]), volume_left=int(row["volume_left"]),
                   stop_loss=row.get("stop_loss"), take_profit=row.get("take_profit"),
                   opened_at_ms=int(row["opened_at_ms"]))


def _order_key(v: VirtualPosition) -> tuple[int, int]:
    return (v.opened_at_ms, v.virtual_id)


class NetLedger:
    """Netting MASTER bookkeeping. Pure: nothing here touches the database;
    the caller persists dirty_rows() after every report."""

    def __init__(self, rows):
        self._positions: list[VirtualPosition] = sorted(
            (r if isinstance(r, VirtualPosition) else VirtualPosition.from_row(r) for r in rows),
            key=_order_key)
        self._dirty: dict[int, VirtualPosition] = {}
        self._deleted: list[int] = []

    def positions(self) -> list[VirtualPosition]:
        """Oldest first: the order OUT deals consume them in."""
        return list(self._positions)

    def _of_symbol(self, symbol: str) -> list[VirtualPosition]:
        return [v for v in self._positions if v.symbol == symbol]

    def _open(self, deal: ReportDeal, volume: int, stop_loss, take_profit) -> VirtualPosition:
        vp = VirtualPosition(
            virtual_id=deal.ticket, symbol=deal.symbol, side=deal.deal_type, volume_open=volume,
            volume_left=volume, stop_loss=stop_loss, take_profit=take_profit,
            opened_at_ms=deal.time_ms)
        self._positions.append(vp)
        self._positions.sort(key=_order_key)
        self._dirty[vp.virtual_id] = vp
        self._deleted = [d for d in self._deleted if d != vp.virtual_id]
        return vp

    def _consume(self, symbol: str, canonical: str, volume: int) -> list[MasterEvent]:
        """Take `volume` out of the symbol's virtual positions oldest-first.
        A close beyond what the ledger holds (a position opened while the
        copier was down) closes what it has and stops."""
        events: list[MasterEvent] = []
        remaining = volume
        for vp in self._of_symbol(symbol):
            if remaining <= 0:
                break
            take = min(vp.volume_left, remaining)
            if take <= 0:
                continue
            vp.volume_left -= take
            remaining -= take
            events.append(MasterPositionClosed(
                position_id=vp.virtual_id, symbol_name=canonical, closed_volume=take,
                remaining_volume=vp.volume_left))
            if vp.volume_left <= 0:
                self._positions.remove(vp)
                self._dirty.pop(vp.virtual_id, None)
                self._deleted.append(vp.virtual_id)
            else:
                self._dirty[vp.virtual_id] = vp
        return events

    def apply_deal(self, deal: ReportDeal, canonical_symbol: str,
                   stop_loss: float | None = None, take_profit: float | None = None,
                   ) -> list[MasterEvent]:
        """One trade deal of the net position -> the events its followers act
        on. `stop_loss`/`take_profit` are the net position's current levels
        (from the report), stamped on a virtual position this deal opens."""
        if deal.deal_type not in TRADE_TYPES or deal.volume <= 0:
            return []
        if deal.entry == "IN":
            vp = self._open(deal, deal.volume, stop_loss, take_profit)
            return [MasterPositionOpened(
                position_id=vp.virtual_id, symbol_name=canonical_symbol, side=Side(vp.side),
                volume=vp.volume_open, lot_size=CENTILOTS, stop_loss=stop_loss,
                take_profit=take_profit, entry_price=deal.price)]
        if deal.entry in ("OUT", "OUT_BY"):
            return self._consume(deal.symbol, canonical_symbol, deal.volume)
        if deal.entry == "INOUT":
            held = sum(v.volume_left for v in self._of_symbol(deal.symbol))
            events = self._consume(deal.symbol, canonical_symbol, held)
            remainder = deal.volume - held
            if remainder > 0:
                vp = self._open(deal, remainder, stop_loss, take_profit)
                events.append(MasterPositionOpened(
                    position_id=vp.virtual_id, symbol_name=canonical_symbol, side=Side(vp.side),
                    volume=remainder, lot_size=CENTILOTS, stop_loss=stop_loss,
                    take_profit=take_profit, entry_price=deal.price))
            return events
        return []

    def apply_protection(self, symbol: str, stop_loss: float | None,
                         take_profit: float | None) -> list[MasterEvent]:
        """The net position's stop/target changed: every virtual position of
        the (broker) symbol takes the new levels, and each one's followers
        are told."""
        events: list[MasterEvent] = []
        for vp in self._of_symbol(symbol):
            if (vp.stop_loss, vp.take_profit) == (stop_loss, take_profit):
                continue
            vp.stop_loss, vp.take_profit = stop_loss, take_profit
            self._dirty[vp.virtual_id] = vp
            events.append(MasterPositionSLTPAmended(
                position_id=vp.virtual_id, stop_loss=stop_loss, take_profit=take_profit))
        return events

    def dirty_rows(self) -> tuple[list[VirtualPosition], list[int]]:
        """(upserts, deleted virtual ids) since the last call; clears both."""
        upserts, deleted = list(self._dirty.values()), list(self._deleted)
        self._dirty, self._deleted = {}, []
        return upserts, deleted


def _side_of(order_type: str) -> Side:
    return Side.BUY if order_type.startswith("BUY") else Side.SELL


def _pending_type_of(order_type: str) -> PendingType:
    return PendingType.LIMIT if order_type.endswith("LIMIT") else PendingType.STOP


def master_events_from_report(
    report: SyncReport,
    previous: tuple[list[PositionSnapshot], list[OrderSnapshot]] | None,
    aliases_reverse: dict[str, str],
    symbols: dict[str, SymbolInfo],
    ledger: NetLedger | None = None,
) -> list[MasterEvent]:
    """
    deals (in time order; the caller passes only deals not yet ingested):
      entry IN  -> MasterPositionOpened (SL/TP from the matching position in
                   this report, entry_price = the deal's), unless the deal's
                   order is a pending order known from `previous` -- then
                   MasterPendingFilled, as cTrader's LIMIT/STOP fill would be
      entry OUT / OUT_BY / INOUT -> MasterPositionClosed with remaining_volume
                   = what this report still shows for the position, else 0
      with `ledger` (a netting master): every trade deal goes through
      ledger.apply_deal instead -- virtual positions, ids = deal tickets; a
      pending order's fill is MasterPendingFilled(position_id=deal ticket)
    positions vs previous: SL/TP changed -> MasterPositionSLTPAmended; with
      `ledger`, through ledger.apply_protection (one amend per virtual
      position of the symbol), AFTER the deals so a reversal in the same
      report does not amend positions it just closed
    orders vs previous: new -> MasterPendingPlaced; volume/price/SL/TP changed
      -> MasterPendingReplaced; gone without a deal -> MasterPendingCancelled
      (gone WITH its deal was emitted as MasterPendingFilled above)
    previous is None (the first report after a copier restart): no diff
      events -- every pending order would otherwise look new and be copied
      twice. `previous` is always the terminal's own book (net positions),
      never the virtual one.
    """
    events: list[MasterEvent] = []
    prev_positions = {p.position_id: p for p in previous[0]} if previous is not None else {}
    prev_orders = {o.order_id: o for o in previous[1]} if previous is not None else {}
    cur_positions = {p.ticket: p for p in report.positions}
    cur_orders = {o.ticket: o for o in report.orders}
    filled_orders = {d.order for d in report.deals if d.order and d.entry == "IN"}

    def canonical(broker_name: str) -> str:
        return aliases_reverse.get(broker_name, broker_name)

    def lot_size(broker_name: str) -> int:
        info = symbols.get(broker_name)
        return info.lot_size if info is not None else CENTILOTS

    for d in sorted(report.deals, key=lambda d: (d.time_ms, d.ticket)):
        if d.deal_type not in TRADE_TYPES:
            continue
        pos = cur_positions.get(d.position)
        if ledger is not None:
            produced = ledger.apply_deal(
                d, canonical(d.symbol),
                stop_loss=pos.stop_loss if pos is not None else None,
                take_profit=pos.take_profit if pos is not None else None)
            if d.entry == "IN" and d.order in prev_orders:
                events.append(MasterPendingFilled(order_id=d.order, position_id=d.ticket))
                continue
            events.extend(produced)
            continue
        if d.entry == "IN":
            if d.order in prev_orders:
                events.append(MasterPendingFilled(order_id=d.order, position_id=d.position))
                continue
            events.append(MasterPositionOpened(
                position_id=d.position, symbol_name=canonical(d.symbol), side=Side(d.deal_type),
                volume=d.volume, lot_size=lot_size(d.symbol),
                stop_loss=pos.stop_loss if pos is not None else None,
                take_profit=pos.take_profit if pos is not None else None,
                entry_price=d.price,
            ))
        elif d.entry in CLOSE_ENTRIES:
            events.append(MasterPositionClosed(
                position_id=d.position, symbol_name=canonical(d.symbol), closed_volume=d.volume,
                remaining_volume=pos.volume if pos is not None else 0,
            ))

    if previous is None:
        return events

    for ticket, pos in cur_positions.items():
        prev = prev_positions.get(ticket)
        if prev is None:
            continue
        if (prev.stop_loss, prev.take_profit) != (pos.stop_loss, pos.take_profit):
            if ledger is not None:
                events.extend(ledger.apply_protection(pos.symbol, pos.stop_loss, pos.take_profit))
            else:
                events.append(MasterPositionSLTPAmended(
                    position_id=ticket, stop_loss=pos.stop_loss, take_profit=pos.take_profit))

    for ticket, o in cur_orders.items():
        prev = prev_orders.get(ticket)
        if prev is None:
            events.append(MasterPendingPlaced(
                order_id=ticket, symbol_name=canonical(o.symbol), side=_side_of(o.order_type),
                order_type=_pending_type_of(o.order_type), volume=o.volume,
                lot_size=lot_size(o.symbol), price=o.price, stop_loss=o.stop_loss,
                take_profit=o.take_profit, expiry_ts_ms=None,
            ))
        elif (prev.volume, prev.price, prev.stop_loss, prev.take_profit) != (
                o.volume, o.price, o.stop_loss, o.take_profit):
            events.append(MasterPendingReplaced(
                order_id=ticket, symbol_name=canonical(o.symbol), lot_size=lot_size(o.symbol),
                order_type=_pending_type_of(o.order_type), volume=o.volume, price=o.price,
                stop_loss=o.stop_loss, take_profit=o.take_profit,
            ))

    for ticket in prev_orders:
        if ticket in cur_orders or ticket in filled_orders:
            continue
        events.append(MasterPendingCancelled(order_id=ticket))

    return events
