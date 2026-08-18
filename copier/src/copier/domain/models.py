from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Mapping, Protocol, Sequence


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


# Label stamped on operator-placed orders (dashboard order ticket).  Fills
# carrying it are expected, not drift: the copier logs them as info instead
# of unmatched-fill warnings, and reconcile's orphan check (which only ever
# flags copy:* labels) never picks them up.
MANUAL_ORDER_LABEL = "manual"


class PendingType(Enum):
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass(frozen=True)
class SymbolInfo:
    symbol_id: int
    name: str
    digits: int
    lot_size: int      # ProtoOASymbol.lotSize (protocol units per 1.00 lot)
    min_volume: int
    step_volume: int


@dataclass(frozen=True)
class SlaveConfig:
    account_id: int
    enabled: bool
    multiplier: Decimal
    symbols: Mapping[str, SymbolInfo]


# ---------- master events (normalized from ProtoOAExecutionEvent) ----------

@dataclass(frozen=True)
class MasterPositionOpened:
    position_id: int
    symbol_name: str
    side: Side
    volume: int          # protocol units filled on the master
    lot_size: int        # master's lotSize for the symbol
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class MasterPositionClosed:
    position_id: int
    symbol_name: str
    closed_volume: int
    remaining_volume: int   # 0 => full close


@dataclass(frozen=True)
class MasterPositionSLTPAmended:
    position_id: int
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class MasterPendingPlaced:
    order_id: int
    symbol_name: str
    side: Side
    order_type: PendingType
    volume: int
    lot_size: int
    price: float
    stop_loss: float | None
    take_profit: float | None
    expiry_ts_ms: int | None


@dataclass(frozen=True)
class MasterPendingReplaced:
    order_id: int
    symbol_name: str
    lot_size: int
    order_type: PendingType
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class MasterPendingCancelled:
    order_id: int


@dataclass(frozen=True)
class MasterPendingFilled:
    order_id: int
    position_id: int


@dataclass(frozen=True)
class MasterRejected:
    reason: str


MasterEvent = (MasterPositionOpened | MasterPositionClosed | MasterPositionSLTPAmended
               | MasterPendingPlaced | MasterPendingReplaced | MasterPendingCancelled
               | MasterPendingFilled | MasterRejected)


# ---------- mapping state (implemented by copier.db.repo.Repo) ----------

@dataclass(frozen=True)
class PositionMappingEntry:
    slave_account_id: int
    slave_position_id: int
    slave_volume: int


@dataclass(frozen=True)
class OrderMappingEntry:
    slave_account_id: int
    slave_order_id: int


class MappingState(Protocol):
    def position_entries(self, master_position_id: int) -> Sequence[PositionMappingEntry]: ...
    def order_entries(self, master_order_id: int) -> Sequence[OrderMappingEntry]: ...


# ---------- slave intents (decision core output) ----------

@dataclass(frozen=True)
class OpenMarket:
    slave_account_id: int
    master_position_id: int
    symbol_id: int
    side: Side
    volume: int
    stop_loss: float | None
    take_profit: float | None
    label: str
    # Slave-resolved symbol NAME, stamped onto the mapping row for copy
    # feeds. Defaulted so pre-existing positional constructions stay valid.
    symbol_name: str = ""


@dataclass(frozen=True)
class ClosePosition:
    slave_account_id: int
    position_id: int
    volume: int


@dataclass(frozen=True)
class AmendPositionSLTP:
    slave_account_id: int
    position_id: int
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class PlacePending:
    slave_account_id: int
    master_order_id: int
    symbol_id: int
    side: Side
    order_type: PendingType
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None
    expiry_ts_ms: int | None
    label: str
    symbol_name: str = ""


@dataclass(frozen=True)
class AmendPending:
    slave_account_id: int
    order_id: int
    order_type: PendingType
    volume: int
    price: float
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True)
class CancelPending:
    slave_account_id: int
    order_id: int


@dataclass(frozen=True)
class LinkPendingFill:
    slave_account_id: int
    master_order_id: int
    master_position_id: int


@dataclass(frozen=True)
class Alert:
    slave_account_id: int | None
    message: str


SlaveIntent = (OpenMarket | ClosePosition | AmendPositionSLTP | PlacePending
               | AmendPending | CancelPending | LinkPendingFill | Alert)
