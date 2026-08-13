"""Tests for reconciliation and drift detection (reconcile.py)."""

import pytest
import pytest_twisted
import psycopg
from decimal import Decimal
from unittest.mock import Mock, MagicMock, AsyncMock

from ctrader_open_api import Client, TcpProtocol
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from twisted.internet import reactor

from copier.ctrader.client import CTraderClient
from copier.domain.models import ClosePosition, Side
from copier.engine.reconcile import (
    PositionSnapshot, OrderSnapshot, DriftItem, compute_drift, Reconciler
)
from copier.testing.fake_server import FakeCTraderServer


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
            PositionSnapshot(3, 1, Side.BUY, 100000, 1.1, ""),  # Still open on master; slave copy vanished
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
                'master_position_id': 3,  # Master position 3 still open, but no slave position
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

    def test_missing_slave_copy_not_flagged_when_master_also_closed(self):
        """No drift: mapping is active but BOTH master and slave positions are gone.

        This is the copier having correctly closed the slave in step with the
        master; it must NOT be reported as missing_slave_copy (which is only
        for master-still-open-but-slave-vanished).
        """
        master_pos = []  # Master position 1 is also closed
        master_orders = []

        slave_pos = {2001: []}  # Slave position vanished too
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

    def test_unfilled_slave_order_no_drift_when_slave_position_present(self):
        """No drift: the linked order DID fill (matching slave position present).

        Mirrors what Repo.activate_pending_fill() actually does in production:
        once the slave order fills, slave_position_id/slave_volume are stamped
        onto the SAME mapping row alongside slave_order_id/master_position_id.
        """
        master_pos = [
            PositionSnapshot(123, 1, Side.BUY, 100000, 1.1, ""),  # still open on master too
        ]
        master_orders = []

        slave_pos = {
            2001: [
                PositionSnapshot(5000, 1, Side.BUY, 100000, 1.1, "copy:o100"),
            ]
        }
        slave_orders = {2001: []}

        mappings = [
            {
                'id': 1,
                'master_order_id': 100,
                'slave_account_id': 2001,
                'slave_order_id': 9000,
                'slave_position_id': 5000,
                'slave_volume': 100000,
                'master_position_id': 123,
                'status': 'active'
            }
        ]

        drift_items = compute_drift(
            master_pos, master_orders, slave_pos, slave_orders, mappings, {2001}
        )

        assert drift_items == []


MASTER_ID = 1001
SLAVE_ID = 2001            # enabled, per seed_accounts_and_mappings
DISABLED_SLAVE_ID = 2002   # disabled, per seed_accounts_and_mappings


class TestReconciler:
    """Tests for Reconciler against a real FakeCTraderServer (real SDK wire, real Postgres).

    These exercise run()'s actual ProtoOAReconcileReq round trip end-to-end,
    the no-auto-trade safety invariant, and the close_orphan/adopt remedies.
    """

    @pytest.fixture
    def dispatcher(self):
        """Mock dispatcher -- used to assert run() never trades."""
        return Mock()

    @pytest.fixture
    def fake_server(self):
        """A live FakeCTraderServer with scriptable open_positions/pending_orders."""
        srv = FakeCTraderServer(auto_fill=True)
        srv.accounts = {
            MASTER_ID: "tok-master",
            SLAVE_ID: "tok-slave",
            DISABLED_SLAVE_ID: "tok-slave2",
        }
        port = srv.listen(reactor)
        yield srv, port
        srv.shutdown()

    @pytest.fixture
    def make_client(self, fake_server):
        """Factory for authorized CTraderClients; all are stopped at teardown
        regardless of test outcome, so no heartbeat LoopingCall ever leaks."""
        srv, port = fake_server
        created = []

        @pytest_twisted.inlineCallbacks
        def _make(account_ids):
            sdk = Client("127.0.0.1", port, TcpProtocol)
            client = CTraderClient(sdk, "test-cid", "test-csecret")
            client.start()
            created.append(client)
            yield client.ready
            for account_id in account_ids:
                yield client.authorize_account(account_id, srv.accounts[account_id])
            return client

        yield _make

        for client in created:
            client.stop()

    def _drift_events(self, repo):
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            conn.row_factory = psycopg.rows.dict_row
            return conn.execute("SELECT * FROM events WHERE category = 'drift'").fetchall()

    def test_reconciler_initialization(self, repo, dispatcher):
        """Reconciler starts with an empty current list and no snapshots."""
        reconciler = Reconciler(
            clients_by_account=Mock(),
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
        )
        assert reconciler.master_account_id == MASTER_ID
        assert reconciler.current == []

    @pytest_twisted.inlineCallbacks
    def test_run_computes_drift_end_to_end_and_never_trades(self, repo, dispatcher, fake_server, make_client):
        """run() fills self.current with one item per drift category (built via
        scriptable open_positions + real mapping rows), logs exactly one 'drift'
        event per item, and NEVER calls the dispatcher (binding no-auto-trade guard)."""
        srv, port = fake_server

        # Master: position 1 has no mapping at all (-> unmapped_master_position);
        # position 2 is actively mapped to a slave position that never shows up
        # on the slave broker (-> missing_slave_copy, since master is still open).
        srv.open_positions = {
            MASTER_ID: [
                {"position_id": 1, "symbol_id": 1, "volume": 100_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.10},
                {"position_id": 2, "symbol_id": 1, "volume": 100_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.15},
            ],
            SLAVE_ID: [
                # Labeled copy:* but no mapping row references it -> orphan_slave_position.
                {"position_id": 5000, "symbol_id": 1, "volume": 250_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.10, "label": "copy:m999"},
                # NOTE: position 5002 (mapped to master position 2) is intentionally absent.
            ],
        }

        client = yield make_client([MASTER_ID, SLAVE_ID])

        # Seed the missing_slave_copy mapping: master position 2 -> slave position
        # 5002, which never materializes on the slave broker.
        repo.create_position_mapping(master_position_id=2, slave_account_id=SLAVE_ID, client_order_id="cm2.2001")
        repo.activate_position_mapping("cm2.2001", slave_position_id=5002, slave_volume=100_000)

        # Seed the unfilled_slave_order mapping: order placed & linked to a master
        # fill, but no corresponding slave position ever appeared.
        repo.create_order_mapping(master_order_id=100, slave_account_id=SLAVE_ID, client_order_id="co100.2001")
        repo.activate_order_mapping("co100.2001", slave_order_id=9000)
        repo.link_pending_fill(master_order_id=100, slave_account_id=SLAVE_ID, master_position_id=55)

        reconciler = Reconciler(
            clients_by_account=lambda account_id: client,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
        )

        items = yield reconciler.run()

        assert items is reconciler.current
        kinds = {item.kind for item in items}
        assert kinds == {
            'unmapped_master_position', 'missing_slave_copy',
            'orphan_slave_position', 'unfilled_slave_order',
        }

        unmapped = next(i for i in items if i.kind == 'unmapped_master_position')
        assert unmapped.position_id == 1

        missing = next(i for i in items if i.kind == 'missing_slave_copy')
        assert missing.account_id == SLAVE_ID and missing.position_id == 5002

        orphan = next(i for i in items if i.kind == 'orphan_slave_position')
        assert orphan.account_id == SLAVE_ID and orphan.position_id == 5000

        unfilled = next(i for i in items if i.kind == 'unfilled_slave_order')
        assert unfilled.account_id == SLAVE_ID and unfilled.order_id == 9000

        # Exactly one 'drift' event logged per item.
        rows = self._drift_events(repo)
        assert len(rows) == len(items)
        assert {r['severity'] for r in rows} == {'warning'}

        # THE binding safety invariant: run() must never call the dispatcher.
        dispatcher.dispatch.assert_not_called()

    @pytest_twisted.inlineCallbacks
    def test_run_ignores_disabled_slaves(self, repo, dispatcher, fake_server, make_client):
        """A disabled slave's positions are never fetched/considered by run(),
        even though it is scripted with a labeled (would-be-orphan) position."""
        srv, port = fake_server
        srv.open_positions = {
            MASTER_ID: [],
            DISABLED_SLAVE_ID: [
                {"position_id": 6000, "symbol_id": 1, "volume": 100_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.10, "label": "copy:m1"},
            ],
        }

        client = yield make_client([MASTER_ID, SLAVE_ID, DISABLED_SLAVE_ID])
        requested_accounts = []

        def _clients_by_account(account_id):
            requested_accounts.append(account_id)
            return client

        reconciler = Reconciler(
            clients_by_account=_clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
        )

        items = yield reconciler.run()

        assert MASTER_ID in requested_accounts
        assert SLAVE_ID in requested_accounts        # enabled -> reconciled
        assert DISABLED_SLAVE_ID not in requested_accounts  # disabled -> skipped entirely
        assert items == []  # nothing open on master; disabled slave's position ignored
        dispatcher.dispatch.assert_not_called()

    @pytest_twisted.inlineCallbacks
    def test_close_orphan_dispatches_single_full_volume_close(self, repo, dispatcher, fake_server, make_client):
        """close_orphan() dispatches exactly ONE full-volume ClosePosition for the
        orphan, and only when called explicitly (run() itself dispatches nothing)."""
        srv, port = fake_server
        srv.open_positions = {
            MASTER_ID: [],
            SLAVE_ID: [
                {"position_id": 5000, "symbol_id": 1, "volume": 250_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.10, "label": "copy:m999"},
            ],
        }

        client = yield make_client([MASTER_ID, SLAVE_ID])

        reconciler = Reconciler(
            clients_by_account=lambda account_id: client,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
        )

        items = yield reconciler.run()
        orphan = next(i for i in items if i.kind == 'orphan_slave_position')

        # run() itself must not have dispatched anything.
        dispatcher.dispatch.assert_not_called()

        yield reconciler.close_orphan(orphan.id)

        dispatcher.dispatch.assert_called_once()
        (dispatched_intents,), _ = dispatcher.dispatch.call_args
        assert len(dispatched_intents) == 1
        intent = dispatched_intents[0]
        assert isinstance(intent, ClosePosition)
        assert intent.slave_account_id == SLAVE_ID
        assert intent.position_id == 5000
        assert intent.volume == 250_000  # full volume, matching the live snapshot

    @pytest_twisted.inlineCallbacks
    def test_adopt_persists_real_slave_volume(self, repo, dispatcher, fake_server, make_client):
        """adopt() must persist the REAL live slave position volume, not a
        hardcoded 0 (the mapping row is otherwise unusable for future reduces)."""
        srv, port = fake_server
        srv.open_positions = {
            MASTER_ID: [],
            SLAVE_ID: [
                {"position_id": 5000, "symbol_id": 1, "volume": 175_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.10, "label": "copy:m999"},
            ],
        }

        client = yield make_client([MASTER_ID, SLAVE_ID])

        reconciler = Reconciler(
            clients_by_account=lambda account_id: client,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
        )

        items = yield reconciler.run()
        orphan = next(i for i in items if i.kind == 'orphan_slave_position')

        yield reconciler.adopt(orphan.id, master_position_id=999)

        rows = repo.mapping_rows()
        adopted = next(
            r for r in rows
            if r['slave_account_id'] == SLAVE_ID and r['slave_position_id'] == 5000
        )
        assert adopted['master_position_id'] == 999
        assert adopted['slave_volume'] == 175_000  # real volume, not the old hardcoded 0
        assert adopted['status'] == 'active'

        dispatcher.dispatch.assert_not_called()  # adopt() never trades

    def test_dismiss_logs_event_without_trading(self, repo, dispatcher):
        """dismiss() is a log-only no-op: it logs an event and never dispatches."""
        reconciler = Reconciler(
            clients_by_account=Mock(),
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
        )
        item = DriftItem(
            id="test_1",
            kind="unmapped_master_position",
            account_id=None,
            position_id=1,
            order_id=None,
            detail="Test drift",
        )
        reconciler.current = [item]

        d = reconciler.dismiss("test_1")
        results = []
        d.addCallback(results.append)
        assert results == [None]

        rows = self._drift_events(repo)
        info_rows = [r for r in rows if r['severity'] == 'info']
        assert len(info_rows) == 1
        assert info_rows[0]['payload']['action'] == 'dismissed'
        dispatcher.dispatch.assert_not_called()
