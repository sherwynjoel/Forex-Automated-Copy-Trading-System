"""Reconciliation and drift detection engine.

Reconciles broker reality (from ProtoOAReconcileReq responses) against the mappings
table, emitting drift events and surfacing one-click remedies as data
(close_orphan, adopt, dismiss). Drift is REPORTED, never auto-traded.

Interfaces:
- PositionSnapshot / OrderSnapshot: immutable snapshots from broker reconcile response
- compute_drift: pure function, no side effects
- DriftItem: immutable drift detection result
- Reconciler: stateful orchestrator; run() sends ProtoOAReconcileReq per enabled account,
  computes drift, logs events, stores in self.current; remedies (close_orphan, adopt,
  dismiss) provide data for manual action, never auto-trade
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Callable

from twisted.internet import defer

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from ctrader_open_api import Protobuf

from copier.domain.models import Side
from copier.ctrader.client import CTraderClient
from copier.db.repo import Repo
from copier.engine.dispatch import Dispatcher
from copier.domain.models import ClosePosition

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSnapshot:
    """Immutable snapshot of a broker position from reconcile response."""
    position_id: int
    symbol_id: int
    side: Side
    volume: int
    price: float
    label: str


@dataclass(frozen=True)
class OrderSnapshot:
    """Immutable snapshot of a broker pending order from reconcile response."""
    order_id: int
    symbol_id: int
    volume: int
    label: str


@dataclass(frozen=True)
class DriftItem:
    """Immutable drift detection result."""
    id: str  # Stable hash of (kind, account_id, position_id, order_id)
    kind: str  # 'orphan_slave_position', 'missing_slave_copy', 'unmapped_master_position', 'unfilled_slave_order'
    account_id: int | None
    position_id: int | None
    order_id: int | None
    detail: str


def _extract_master_id_from_label(label: str) -> int | None:
    """Extract master position ID from label like 'copy:m123'."""
    if label.startswith("copy:m"):
        try:
            return int(label[6:])
        except ValueError:
            pass
    return None


def _stable_id(*parts) -> str:
    """Generate stable hash ID from parts."""
    content = "|".join(str(p) for p in parts)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def compute_drift(
    master_positions: list[PositionSnapshot],
    master_orders: list[OrderSnapshot],
    slave_positions: dict[int, list[PositionSnapshot]],
    slave_orders: dict[int, list[OrderSnapshot]],
    mappings: list[dict],
    enabled_slave_ids: set[int],
) -> list[DriftItem]:
    """Compute drift between broker reality and mappings (pure, no side effects).

    Detects four drift categories:
    1. orphan_slave_position: slave position labeled copy:* with no active mapping,
       or mapping whose master position is gone
    2. missing_slave_copy: active mapping exists but slave position vanished
    3. unmapped_master_position: master position with zero mapping rows (missed while down)
    4. unfilled_slave_order: order mapping linked to master fill but slave position never materialized

    Args:
        master_positions: List of PositionSnapshot from master account
        master_orders: List of OrderSnapshot from master account (unused for now)
        slave_positions: Dict[account_id, list[PositionSnapshot]] from slave accounts
        slave_orders: Dict[account_id, list[OrderSnapshot]] from slave accounts
        mappings: List[dict] of mapping rows from database
        enabled_slave_ids: Set[int] of enabled slave account IDs

    Returns:
        list[DriftItem] sorted by id for stable output
    """
    drift_items = []

    # Index master positions by ID for quick lookup
    master_pos_by_id = {p.position_id: p for p in master_positions}

    # Index mappings by (account_id, slave_position_id) for quick lookup
    mappings_by_slave_pos = {}
    mappings_by_master_pos = {}
    for m in mappings:
        if m.get('status') == 'active' and m.get('slave_position_id'):
            key = (m['slave_account_id'], m['slave_position_id'])
            mappings_by_slave_pos[key] = m
        if m.get('status') == 'active' and m.get('master_position_id'):
            mappings_by_master_pos.setdefault(m['master_position_id'], []).append(m)

    # Build slave position index by account
    slave_pos_by_account = {}
    for account_id, positions in slave_positions.items():
        slave_pos_by_account[account_id] = {p.position_id: p for p in positions}

    # 1. Check for orphan slave positions (labeled copy:* without active mapping, or mapping whose master is gone)
    for account_id, positions in slave_positions.items():
        if account_id not in enabled_slave_ids:
            continue

        for pos in positions:
            # Check if position is labeled copy:*
            if pos.label.startswith("copy:"):
                # Look for active mapping for this slave position
                mapping = mappings_by_slave_pos.get((account_id, pos.position_id))

                if mapping:
                    # Mapping exists; check if master position still exists
                    master_id = mapping.get('master_position_id')
                    if master_id not in master_pos_by_id:
                        # Master position is gone -> orphan
                        item = DriftItem(
                            id=_stable_id('orphan_slave_position', account_id, pos.position_id),
                            kind='orphan_slave_position',
                            account_id=account_id,
                            position_id=pos.position_id,
                            order_id=None,
                            detail=f"Mapping exists but master position {master_id} is gone"
                        )
                        drift_items.append(item)
                else:
                    # Labeled but no active mapping -> orphan
                    item = DriftItem(
                        id=_stable_id('orphan_slave_position', account_id, pos.position_id),
                        kind='orphan_slave_position',
                        account_id=account_id,
                        position_id=pos.position_id,
                        order_id=None,
                        detail=f"Labeled copy:* but no active mapping"
                    )
                    drift_items.append(item)

    # 2. Check for missing slave copies (active mapping, master position still open,
    #    but slave position vanished). If the master position is also gone, the
    #    slave closing in step is consistent with the master and is NOT drift.
    for m in mappings:
        if m.get('status') != 'active':
            continue

        account_id = m.get('slave_account_id')
        slave_pos_id = m.get('slave_position_id')
        master_pos_id = m.get('master_position_id')

        if not account_id or not slave_pos_id:
            continue

        if account_id not in enabled_slave_ids:
            continue

        # Only flag as missing if the master position this mapping tracks is
        # still open. If the master closed too, there is nothing to reconcile.
        if master_pos_id not in master_pos_by_id:
            continue

        # Check if slave position still exists on broker
        slave_pos_dict = slave_pos_by_account.get(account_id, {})
        if slave_pos_id not in slave_pos_dict:
            # Slave position is gone but mapping is active -> missing copy
            item = DriftItem(
                id=_stable_id('missing_slave_copy', account_id, slave_pos_id),
                kind='missing_slave_copy',
                account_id=account_id,
                position_id=slave_pos_id,
                order_id=None,
                detail=f"Active mapping but slave position {slave_pos_id} vanished"
            )
            drift_items.append(item)

    # 3. Check for unmapped master positions (master with zero mappings = missed while down, never replayed)
    for master_pos in master_positions:
        if master_pos.position_id not in mappings_by_master_pos:
            # Master position has no mappings -> unmapped
            item = DriftItem(
                id=_stable_id('unmapped_master_position', master_pos.position_id),
                kind='unmapped_master_position',
                account_id=None,
                position_id=master_pos.position_id,
                order_id=None,
                detail=f"Master position {master_pos.position_id} opened while copier was down"
            )
            drift_items.append(item)

    # 4. Check for unfilled slave orders (order mapping with master_position_id but no slave position)
    for m in mappings:
        if m.get('status') != 'active':
            continue

        account_id = m.get('slave_account_id')
        slave_order_id = m.get('slave_order_id')
        master_pos_id = m.get('master_position_id')

        if not account_id or not slave_order_id or not master_pos_id:
            continue

        if account_id not in enabled_slave_ids:
            continue

        # This is an order mapping linked to a master fill
        # Check if a slave position exists for this fill
        slave_pos_dict = slave_pos_by_account.get(account_id, {})
        slave_pos_ids = set(slave_pos_dict.keys())

        # If there's no slave position linked to this fill, it's unfilled
        # (Note: this is a simplified check; in practice, we'd need to track
        # which positions correspond to which order fills)
        has_corresponding_position = False
        for pos in slave_positions.get(account_id, []):
            # Check if this position matches the order mapping
            # (For now, we assume any position with this label is the corresponding position)
            if f"copy:o{m.get('master_order_id')}" in pos.label:
                has_corresponding_position = True
                break

        if not has_corresponding_position and slave_order_id:
            item = DriftItem(
                id=_stable_id('unfilled_slave_order', account_id, slave_order_id),
                kind='unfilled_slave_order',
                account_id=account_id,
                position_id=None,
                order_id=slave_order_id,
                detail=f"Order mapping linked to master fill but no slave position materialized"
            )
            drift_items.append(item)

    # Sort for stable output
    drift_items.sort(key=lambda item: item.id)
    return drift_items


class Reconciler:
    """Orchestrator for reconciliation: sends ProtoOAReconcileReq, detects drift, logs events.

    Drift is REPORTED, never auto-traded:
    - run() never calls dispatcher to trade
    - close_orphan() provides data for manual close; manual action calls dispatcher
    - adopt() provides data for manual adoption; manual action calls repo
    - dismiss() provides data for manual dismissal; manual action calls repo
    """

    def __init__(
        self,
        clients_by_account: Callable[[int], CTraderClient],
        repo: Repo,
        dispatcher: Dispatcher,
        master_account_id: int,
    ):
        """Initialize Reconciler.

        Args:
            clients_by_account: Callable that returns CTraderClient for an account_id
            repo: Repository for mappings and events
            dispatcher: Dispatcher for remedies (only used by close_orphan for user-initiated close)
            master_account_id: The master account ID
        """
        self.clients_by_account = clients_by_account
        self.repo = repo
        self.dispatcher = dispatcher
        self.master_account_id = master_account_id
        self.current: list[DriftItem] = []
        # Snapshot of the most recent slave positions per account, captured by
        # run(). Used by close_orphan()/adopt() to determine the real live
        # volume of a slave position without re-querying the broker.
        self._slave_positions: dict[int, dict[int, PositionSnapshot]] = {}

    def _fetch_snapshot(self, account_id: int):
        """Send ProtoOAReconcileReq for one account and extract snapshots.

        Returns a Deferred[(list[PositionSnapshot], list[OrderSnapshot])].
        """
        client = self.clients_by_account(account_id)
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = account_id

        def _extract(res):
            reconcile_res = Protobuf.extract(res)
            positions = [
                PositionSnapshot(
                    position_id=p.positionId,
                    symbol_id=p.tradeData.symbolId,
                    side=Side.BUY if p.tradeData.tradeSide == ProtoOATradeSide.BUY else Side.SELL,
                    volume=p.tradeData.volume,
                    price=p.price,
                    label=p.tradeData.label,
                )
                for p in reconcile_res.position
            ]
            orders = [
                OrderSnapshot(
                    order_id=o.orderId,
                    symbol_id=o.tradeData.symbolId,
                    volume=o.tradeData.volume,
                    label=o.tradeData.label,
                )
                for o in reconcile_res.order
            ]
            return positions, orders

        d = client.send(req)
        d.addCallback(_extract)
        return d

    @defer.inlineCallbacks
    def run(self):
        """Run reconciliation: send ProtoOAReconcileReq per enabled account, compute drift, log events.

        This method:
        1. Sends ProtoOAReconcileReq to the master account for open positions/orders
        2. Sends ProtoOAReconcileReq to each enabled slave account
        3. Computes drift using compute_drift()
        4. Logs one 'drift' event per DriftItem
        5. Stores results in self.current

        Never issues orders itself, and never calls the dispatcher; drift is
        reported, not auto-traded.

        Returns:
            Deferred[list[DriftItem]] - the computed drift items
        """
        accounts = self.repo.load_accounts()
        enabled_slave_ids = {a.account_id for a in accounts if a.role == 'slave' and a.enabled}

        master_positions, master_orders = yield self._fetch_snapshot(self.master_account_id)

        slave_positions: dict[int, list[PositionSnapshot]] = {}
        slave_orders: dict[int, list[OrderSnapshot]] = {}
        for slave_id in enabled_slave_ids:
            positions, orders = yield self._fetch_snapshot(slave_id)
            slave_positions[slave_id] = positions
            slave_orders[slave_id] = orders

        # Cache live slave position snapshots for close_orphan()/adopt() volume lookups.
        self._slave_positions = {
            account_id: {p.position_id: p for p in positions}
            for account_id, positions in slave_positions.items()
        }

        mappings = self.repo.mapping_rows()

        items = compute_drift(
            master_positions, master_orders, slave_positions, slave_orders,
            mappings, enabled_slave_ids,
        )

        for item in items:
            self.repo.log_event(
                'drift',
                'warning',
                {
                    'drift_kind': item.kind,
                    'position_id': item.position_id,
                    'order_id': item.order_id,
                    'detail': item.detail,
                },
                account_id=item.account_id,
            )

        self.current = items
        return items

    def _find_current_item(self, item_id: str) -> DriftItem | None:
        for drift_item in self.current:
            if drift_item.id == item_id:
                return drift_item
        return None

    def _lookup_slave_volume(self, account_id: int, position_id: int) -> int | None:
        """Determine the real, live volume of a slave position.

        Prefers the broker snapshot captured by the most recent run() (accurate
        even for orphan positions that have no mapping row at all); falls back
        to the mapping row's recorded slave_volume when no snapshot is available.
        """
        snapshot = self._slave_positions.get(account_id, {}).get(position_id)
        if snapshot is not None:
            return snapshot.volume

        for m in self.repo.mapping_rows():
            if (m.get('slave_account_id') == account_id and
                m.get('slave_position_id') == position_id and
                m.get('status') == 'active'):
                return m.get('slave_volume')
        return None

    def close_orphan(self, item_id: str) -> defer.Deferred:
        """Close an orphan slave position (user-initiated action).

        Finds the DriftItem by id, extracts slave_position_id and account_id,
        dispatches a full-volume ClosePosition intent.

        This is the ONLY drift action that actually trades, and only on explicit user click.

        Args:
            item_id: The DriftItem.id

        Returns:
            Deferred[] - resolves when close has been dispatched
        """
        d = defer.Deferred()

        item = self._find_current_item(item_id)

        if not item:
            d.errback(ValueError(f"Drift item {item_id} not found"))
            return d

        if item.kind != 'orphan_slave_position':
            d.errback(ValueError(f"Item {item_id} is not an orphan_slave_position"))
            return d

        if not item.account_id or not item.position_id:
            d.errback(ValueError(f"Item {item_id} missing account_id or position_id"))
            return d

        volume = self._lookup_slave_volume(item.account_id, item.position_id)

        if not volume:
            d.errback(ValueError(f"Could not determine volume for position {item.position_id}"))
            return d

        # Create ClosePosition intent
        intent = ClosePosition(
            slave_account_id=item.account_id,
            position_id=item.position_id,
            volume=volume
        )

        # Dispatch it
        try:
            self.dispatcher.dispatch([intent])
            d.callback(None)
        except Exception as e:
            d.errback(e)

        return d

    def adopt(self, item_id: str, master_position_id: int) -> defer.Deferred:
        """Adopt an orphan slave position (user-initiated action).

        Creates a new active mapping linking the slave position to a master position.
        Uses repo.adopt_position_mapping to insert the mapping.

        Args:
            item_id: The DriftItem.id
            master_position_id: The master position ID to link

        Returns:
            Deferred[] - resolves when adoption is complete
        """
        d = defer.Deferred()

        item = self._find_current_item(item_id)

        if not item:
            d.errback(ValueError(f"Drift item {item_id} not found"))
            return d

        if item.kind != 'orphan_slave_position':
            d.errback(ValueError(f"Item {item_id} is not an orphan_slave_position"))
            return d

        if not item.account_id or not item.position_id:
            d.errback(ValueError(f"Item {item_id} missing account_id or position_id"))
            return d

        # Look up the real, live slave position volume (from the snapshot
        # captured during run(), falling back to a mapping row if present)
        # so the adopted mapping records the actual position size rather
        # than a fabricated 0.
        slave_volume = self._lookup_slave_volume(item.account_id, item.position_id)
        if not slave_volume:
            d.errback(ValueError(f"Could not determine volume for position {item.position_id}"))
            return d

        try:
            self.repo.adopt_position_mapping(
                master_position_id=master_position_id,
                slave_account_id=item.account_id,
                slave_position_id=item.position_id,
                slave_volume=slave_volume,
            )
            d.callback(None)
        except Exception as e:
            d.errback(e)

        return d

    def dismiss(self, item_id: str) -> defer.Deferred:
        """Dismiss a drift item (user-initiated action).

        For orphan positions, logs the dismissal as an event.
        For other drift types, logs the dismissal.

        Args:
            item_id: The DriftItem.id

        Returns:
            Deferred[] - resolves when dismissal is logged
        """
        d = defer.Deferred()

        # Find the drift item
        item = None
        for drift_item in self.current:
            if drift_item.id == item_id:
                item = drift_item
                break

        if not item:
            d.errback(ValueError(f"Drift item {item_id} not found"))
            return d

        try:
            self.repo.log_event(
                'drift',
                'info',
                {
                    'action': 'dismissed',
                    'drift_kind': item.kind,
                    'position_id': item.position_id,
                    'order_id': item.order_id,
                    'detail': item.detail,
                },
                account_id=item.account_id,
            )
            d.callback(None)
        except Exception as e:
            d.errback(e)

        return d
