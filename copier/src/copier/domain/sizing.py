"""Lots <-> protocol-volume conversion. protocol volume = lots * ProtoOASymbol.lotSize."""
from decimal import ROUND_HALF_UP, Decimal


def protocol_volume_to_lots(volume: int, lot_size: int) -> Decimal:
    return (Decimal(volume) / Decimal(lot_size)).normalize()


def lots_to_protocol_volume(lots: Decimal, lot_size: int) -> int:
    return int((lots * lot_size).to_integral_value(rounding=ROUND_HALF_UP))


def floor_to_step(volume: int, step_volume: int) -> int:
    if step_volume <= 0:
        return volume
    return (volume // step_volume) * step_volume


def mirror_volume(master_volume: int, master_lot_size: int, multiplier: Decimal,
                  slave_lot_size: int, slave_step_volume: int) -> int:
    lots = protocol_volume_to_lots(master_volume, master_lot_size) * multiplier
    return floor_to_step(lots_to_protocol_volume(lots, slave_lot_size), slave_step_volume)


def partial_close_volume(slave_volume: int, closed_volume: int, remaining_volume: int,
                         step_volume: int) -> int:
    if remaining_volume == 0:
        return slave_volume
    total = closed_volume + remaining_volume
    exact = Decimal(slave_volume) * Decimal(closed_volume) / Decimal(total)
    return min(floor_to_step(int(exact), step_volume), slave_volume)
