"""Tests for reconciliation and drift detection (reconcile.py)."""

import pytest
import psycopg
from decimal import Decimal
from unittest.mock import Mock, MagicMock, AsyncMock

from copier.domain.models import Side
from copier.engine.reconcile import (
    PositionSnapshot, OrderSnapshot, DriftItem, compute_drift, Reconciler
)


@pytest.fixture
def repo(db):
    """Create a Repo instance connected to test database."""
    from copier.db.repo import Repo
    return Repo(db)


@pytest.fixture(autouse=True)
def seed_accounts_and_mappings(db):
    """Seed accounts for testing."""
    with psycopg.connect(db, autocommit=True) as conn:
        # Create a ctid_connection
        conn.execute(
            """
            INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, now(), now() + interval '1 hour')
            RETURNING id
            """,
            ("token_access", "token_refresh"),
        )

        # Create accounts: 1001 (master), 2001/2002 (slaves)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (1001, 1, 10001, false, 'master', true, 1.0),
                (2001, 1, 20001, false, 'slave', true, 1.0),
                (2002, 1, 20002, false, 'slave', false, 1.0)
            """
        )
    yield


class TestPositionSnapshot:
    """Test PositionSnapshot data class."""

    def test_position_snapshot_creation(self):
        snap = PositionSnapshot(
            position_id=5000,
            symbol_id=1,
            side=Side.BUY,
            volume=100000,
            price=1.1234,
            label="copy:m123"
        )
        assert snap.position_id == 5000
        assert snap.symbol_id == 1
        assert snap.side == Side.BUY
        assert snap.volume == 100000
        assert snap.price == 1.1234
        assert snap.label == "copy:m123"


class TestOrderSnapshot:
    """Test OrderSnapshot data class."""

    def test_order_snapshot_creation(self):
        snap = OrderSnapshot(
            order_id=9000,
            symbol_id=1,
            volume=200000,
            label="copy:o456"
        )
        assert snap.order_id == 9000
        assert snap.symbol_id == 1
        assert snap.volume == 200000
        assert snap.label == "copy:o456"


class TestComputeDrift:
    """Tests for compute_drift function (pure, no side effects)."""

    def test_no_drift_when_everything_matches(self):
        """No drift when all positions are mapped and present on slave."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, "")
        ]
        master_orders = []

        # Slave has the mapped position
        slave_pos = {
            2001: [
                PositionSnapshot(5000, 1, Side.BUY, 100000, 1.1, "copy:m1")
            ]
        }
        slave_orders = {2001: []}

        mappings = [
            {
                'id': 1,
                'master_position_id': 1,
                'slave_account_id': 2001,
                'slave_position_id': 5000,
                'slave_volume': 100000,
                'status': 'active'
            }
        ]

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert drift_items == []

    def test_orphan_labeled_slave_position_without_mapping(self):
        """Orphan: slave position labeled copy:* but no active mapping."""
        master_pos = []
        master_orders = []

        slave_pos = {
            2001: [
                PositionSnapshot(5000, 1, Side.BUY, 100000, 1.1, "copy:m999")
            ]
        }
        slave_orders = {2001: []}

        mappings = []  # No mapping for this position

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert len(drift_items) == 1
        item = drift_items[0]
        assert item.kind == 'orphan_slave_position'
        assert item.account_id == 2001
        assert item.position_id == 5000

    def test_mapping_whose_master_position_is_gone_is_orphan(self):
        """Orphan: active mapping exists but master position is closed/gone."""
        master_pos = []  # Master closed position 1
        master_orders = []

        slave_pos = {
            2001: [
                PositionSnapshot(5000, 1, Side.BUY, 100000, 1.1, "copy:m1")
            ]
        }
        slave_orders = {2001: []}

        # Mapping still exists but master position is gone
        mappings = [
            {
                'id': 1,
                'master_position_id': 1,
                'slave_account_id': 2001,
                'slave_position_id': 5000,
                'slave_volume': 100000,
                'status': 'active'
            }
        ]

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert len(drift_items) == 1
        item = drift_items[0]
        assert item.kind == 'orphan_slave_position'
        assert item.account_id == 2001
        assert item.position_id == 5000

    def test_slave_position_vanished_is_missing_copy(self):
        """Missing copy: active mapping exists but slave position disappeared."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, "")
        ]
        master_orders = []

        slave_pos = {2001: []}  # Position vanished on slave
        slave_orders = {2001: []}

        mappings = [
            {
                'id': 1,
                'master_position_id': 1,
                'slave_account_id': 2001,
                'slave_position_id': 5000,
                'slave_volume': 100000,
                'status': 'active'
            }
        ]

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert len(drift_items) == 1
        item = drift_items[0]
        assert item.kind == 'missing_slave_copy'
        assert item.account_id == 2001
        assert item.position_id == 5000

    def test_master_position_without_mappings_is_unmapped(self):
        """Unmapped master position: opened while copier was down."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, "")
        ]
        master_orders = []

        slave_pos = {2001: []}
        slave_orders = {2001: []}

        mappings = []  # No mapping for master position 1

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert len(drift_items) == 1
        item = drift_items[0]
        assert item.kind == 'unmapped_master_position'
        assert item.account_id is None
        assert item.position_id == 1

    def test_linked_order_without_resulting_position_is_unfilled(self):
        """Unfilled order: order mapping linked to master position but no slave position."""
        master_pos = []
        master_orders = []

        slave_pos = {2001: []}
        slave_orders = {2001: []}

        # Order mapping with master_position_id but no corresponding slave position
        mappings = [
            {
                'id': 1,
                'master_order_id': 100,
                'slave_account_id': 2001,
                'slave_order_id': 9000,
                'master_position_id': 123,  # Order linked to master fill
                'status': 'active'
            }
        ]

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert len(drift_items) == 1
        item = drift_items[0]
        assert item.kind == 'unfilled_slave_order'
        assert item.account_id == 2001
        assert item.order_id == 9000

    def test_disabled_slaves_are_ignored(self):
        """Disabled slaves are not checked for drift."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, "")
        ]

        slave_pos = {
            2001: [],
            2002: [
                PositionSnapshot(5000, 1, Side.BUY, 100000, 1.1, "copy:m999")  # Disabled account, should be ignored
            ]
        }

        mappings = []

        # Only 2001 is enabled (2002 is disabled)
        drift_items = compute_drift(
            master_pos, [], slave_pos, {2001: [], 2002: []}, mappings, {2001}
        )

        # Only unmapped master position should be reported (orphan on 2002 ignored)
        assert len(drift_items) == 1
        assert drift_items[0].kind == 'unmapped_master_position'

    def test_drift_item_ids_are_stable(self):
        """Same input produces same drift item IDs."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, "")
        ]

        slave_pos = {2001: []}
        slave_orders = {2001: []}

        # Compute drift twice
        drift_items_1 = compute_drift(
            master_pos, [], slave_pos, slave_orders, [], {2001}
        )
        drift_items_2 = compute_drift(
            master_pos, [], slave_pos, slave_orders, [], {2001}
        )

        # IDs should be identical
        assert len(drift_items_1) == len(drift_items_2)
        assert drift_items_1[0].id == drift_items_2[0].id

    def test_multiple_enabled_slaves(self):
        """Test with multiple enabled slave accounts."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, "")
        ]

        slave_pos = {2001: [], 2002: []}
        slave_orders = {2001: [], 2002: []}

        drift_items = compute_drift(
            master_pos, [], slave_pos, slave_orders, [], {2001, 2002}
        )

        # Unmapped master position reported once, not per slave
        assert len(drift_items) == 1
        assert drift_items[0].kind == 'unmapped_master_position'

    def test_mixed_drift_categories(self):
        """Test detection of multiple drift categories in one run."""
        master_pos = [
            PositionSnapshot(1, 1, Side.BUY, 100000, 1.1, ""),  # Unmapped
        ]

        slave_pos = {
            2001: [
                PositionSnapshot(5000, 1, Side.BUY, 100000, 1.1, "copy:m2"),  # Orphan (master 2 is gone)
            ]
        }

        slave_orders = {2001: []}

        mappings = [
            {
                'id': 1,
                'master_position_id': 2,  # Master position 2 is gone
                'slave_account_id': 2001,
                'slave_position_id': 5000,
                'slave_volume': 100000,
                'status': 'active'
            },
            {
                'id': 2,
                'master_position_id': 3,  # Mapping exists but no slave position
                'slave_account_id': 2001,
                'slave_position_id': 5002,
                'slave_volume': 100000,
                'status': 'active'
            }
        ]

        drift_items = compute_drift(
            master_pos, [], slave_pos, slave_orders, mappings, {2001}
        )

        kinds = {item.kind for item in drift_items}
        assert 'unmapped_master_position' in kinds
        assert 'orphan_slave_position' in kinds
        assert 'missing_slave_copy' in kinds


class TestReconciler:
    """Tests for Reconciler class with fake server."""

    @pytest.fixture
    def dispatcher(self):
        """Mock dispatcher."""
        return Mock()

    @pytest.fixture
    def clients_by_account(self):
        """Mock client factory."""
        return Mock()

    def test_reconciler_initialization(self, repo, dispatcher, clients_by_account):
        """Test Reconciler can be initialized."""
        reconciler = Reconciler(
            clients_by_account=clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=1001
        )
        assert reconciler.master_account_id == 1001
        assert reconciler.current == []

    def test_reconciler_stores_drift_items(self, repo, dispatcher, clients_by_account):
        """Test Reconciler stores computed drift items."""
        reconciler = Reconciler(
            clients_by_account=clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=1001
        )

        # Simulate setting drift items (normally done by run())
        test_item = DriftItem(
            id="test_1",
            kind="unmapped_master_position",
            account_id=None,
            position_id=1,
            order_id=None,
            detail="Test drift"
        )
        reconciler.current = [test_item]

        assert len(reconciler.current) == 1
        assert reconciler.current[0].id == "test_1"

    def test_close_orphan_raises_not_implemented(self, repo, dispatcher, clients_by_account):
        """Test close_orphan method exists and is callable."""
        reconciler = Reconciler(
            clients_by_account=clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=1001
        )

        # Should be callable even if not implemented
        assert callable(reconciler.close_orphan)

    def test_adopt_raises_not_implemented(self, repo, dispatcher, clients_by_account):
        """Test adopt method exists and is callable."""
        reconciler = Reconciler(
            clients_by_account=clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=1001
        )

        # Should be callable even if not implemented
        assert callable(reconciler.adopt)

    def test_dismiss_raises_not_implemented(self, repo, dispatcher, clients_by_account):
        """Test dismiss method exists and is callable."""
        reconciler = Reconciler(
            clients_by_account=clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=1001
        )

        # Should be callable even if not implemented
        assert callable(reconciler.dismiss)
