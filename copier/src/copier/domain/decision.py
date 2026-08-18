"""Pure replication decision core: (master_event, mapping_state, slave_configs) -> [SlaveIntent].

No I/O. No clocks. No randomness. Everything here must stay exhaustively unit-tested.
"""
from typing import Sequence

from copier.domain import models as m
from copier.domain.sizing import mirror_volume, partial_close_volume


def decide(event: m.MasterEvent, mappings: m.MappingState,
           slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    match event:
        case m.MasterPositionOpened():
            return _position_opened(event, slaves)
        case m.MasterPositionClosed():
            return _position_closed(event, mappings, slaves)
        case m.MasterPositionSLTPAmended():
            return _position_sltp(event, mappings, slaves)
        case m.MasterRejected():
            return []  # spec §5: master rejections replicate as no-ops (logged by caller)
        case _:
            return _decide_pending(event, mappings, slaves)  # Task 6


def _enabled(slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveConfig]:
    return [s for s in slaves if s.enabled]


def _by_id(slaves: Sequence[m.SlaveConfig]) -> dict[int, m.SlaveConfig]:
    return {s.account_id: s for s in _enabled(slaves)}


def _position_opened(e: m.MasterPositionOpened,
                     slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    for s in _enabled(slaves):
        sym = s.symbols.get(e.symbol_name)
        if sym is None:
            out.append(m.Alert(s.account_id,
                       f"cannot copy position {e.position_id}: symbol {e.symbol_name!r} not available"))
            continue
        vol = mirror_volume(e.volume, e.lot_size, s.multiplier, sym.lot_size, sym.step_volume)
        if vol == 0:
            out.append(m.Alert(s.account_id,
                       f"cannot copy position {e.position_id}: mirrored volume rounds to 0"))
            continue
        out.append(m.OpenMarket(s.account_id, e.position_id, sym.symbol_id, e.side, vol,
                                e.stop_loss, e.take_profit, f"copy:m{e.position_id}",
                                symbol_name=e.symbol_name))
    return out


def _position_closed(e: m.MasterPositionClosed, mappings: m.MappingState,
                     slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    enabled = _by_id(slaves)
    entries = mappings.position_entries(e.position_id)
    covered: set[int] = set()
    for entry in entries:
        s = enabled.get(entry.slave_account_id)
        if s is None:
            continue
        covered.add(entry.slave_account_id)
        sym = s.symbols.get(e.symbol_name)
        step = sym.step_volume if sym is not None else 1
        vol = partial_close_volume(entry.slave_volume, e.closed_volume,
                                   e.remaining_volume, step)
        if vol == 0:
            out.append(m.Alert(s.account_id,
                       f"partial close of position {e.position_id} rounds to 0 on slave"))
            continue
        out.append(m.ClosePosition(s.account_id, entry.slave_position_id, vol))
    for account_id in enabled.keys() - covered:
        out.append(m.Alert(account_id,
                   f"master closed position {e.position_id} but slave has no mapped copy"))
    return out


def _position_sltp(e: m.MasterPositionSLTPAmended, mappings: m.MappingState,
                   slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    enabled = _by_id(slaves)
    entries = mappings.position_entries(e.position_id)
    covered: set[int] = set()
    for entry in entries:
        if entry.slave_account_id not in enabled:
            continue
        covered.add(entry.slave_account_id)
        out.append(m.AmendPositionSLTP(entry.slave_account_id, entry.slave_position_id,
                                       e.stop_loss, e.take_profit))
    for account_id in enabled.keys() - covered:
        out.append(m.Alert(account_id,
                   f"SL/TP change on master position {e.position_id} but slave has no mapped copy"))
    return out


def _decide_pending(event, mappings: m.MappingState,
                    slaves: Sequence[m.SlaveConfig]) -> list[m.SlaveIntent]:
    out: list[m.SlaveIntent] = []
    enabled = _by_id(slaves)
    match event:
        case m.MasterPendingPlaced() as e:
            for s in _enabled(slaves):
                sym = s.symbols.get(e.symbol_name)
                if sym is None:
                    out.append(m.Alert(s.account_id,
                               f"cannot copy order {e.order_id}: symbol {e.symbol_name!r} not available"))
                    continue
                vol = mirror_volume(e.volume, e.lot_size, s.multiplier,
                                    sym.lot_size, sym.step_volume)
                if vol == 0:
                    out.append(m.Alert(s.account_id,
                               f"cannot copy order {e.order_id}: mirrored volume rounds to 0"))
                    continue
                out.append(m.PlacePending(s.account_id, e.order_id, sym.symbol_id, e.side,
                                          e.order_type, vol, e.price, e.stop_loss,
                                          e.take_profit, e.expiry_ts_ms, f"copy:o{e.order_id}",
                                          symbol_name=e.symbol_name))
        case m.MasterPendingReplaced() as e:
            covered: set[int] = set()
            for entry in mappings.order_entries(e.order_id):
                s = enabled.get(entry.slave_account_id)
                if s is None:
                    continue
                covered.add(entry.slave_account_id)
                sym = s.symbols.get(e.symbol_name)
                if sym is None:
                    out.append(m.Alert(s.account_id,
                               f"cannot amend order copy of {e.order_id}: symbol missing"))
                    continue
                vol = mirror_volume(e.volume, e.lot_size, s.multiplier,
                                    sym.lot_size, sym.step_volume)
                if vol == 0:
                    out.append(m.Alert(s.account_id,
                               f"cannot amend order {e.order_id}: mirrored volume rounds to 0"))
                    continue
                out.append(m.AmendPending(s.account_id, entry.slave_order_id, e.order_type,
                                          vol, e.price, e.stop_loss, e.take_profit))
            for account_id in enabled.keys() - covered:
                out.append(m.Alert(account_id,
                           f"master replaced order {e.order_id} but slave has no mapped order"))
        case m.MasterPendingCancelled() as e:
            covered = set()
            for entry in mappings.order_entries(e.order_id):
                if entry.slave_account_id not in enabled:
                    continue
                covered.add(entry.slave_account_id)
                out.append(m.CancelPending(entry.slave_account_id, entry.slave_order_id))
            for account_id in enabled.keys() - covered:
                out.append(m.Alert(account_id,
                           f"master cancelled order {e.order_id} but slave has no mapped order"))
        case m.MasterPendingFilled() as e:
            covered = set()
            for entry in mappings.order_entries(e.order_id):
                if entry.slave_account_id not in enabled:
                    continue
                covered.add(entry.slave_account_id)
                out.append(m.LinkPendingFill(entry.slave_account_id, e.order_id, e.position_id))
            for account_id in enabled.keys() - covered:
                out.append(m.Alert(account_id,
                           f"master order {e.order_id} filled but slave has no mapped order"))
    return out
