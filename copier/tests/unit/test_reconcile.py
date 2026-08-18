"""Tests for reconciliation and drift detection (reconcile.py)."""

import pytest
import pytest_twisted
import psycopg
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, MagicMock, AsyncMock

from ctrader_open_api import Client, TcpProtocol
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
from twisted.internet import defer, reactor

from copier.ctrader.client import CTraderClient
from copier.domain.models import ClosePosition, Side
from copier.engine.reconcile import (
    STALE_PENDING_AFTER_S, PositionSnapshot, OrderSnapshot, DriftItem,
    compute_drift, Reconciler
)
from copier.testing.fake_server import FakeCTraderServer


@pytest.fixture
def repo(db):
    """Create a Repo instance connected to test database."""
    from copier.db.repo import Repo
    return Repo(db)


@pytest.fixture(autouse=True)
def seed_accounts_and_mappings(db):
    """Seed one org (id=1, thanks to RESTART IDENTITY in the db fixture) with
    accounts for testing."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Test Org') RETURNING id"
        ).fetchone()

        # Create a ctid_connection
        conn.execute(
            """
            INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, %s, now(), now() + interval '1 hour')
            RETURNING id
            """,
            (org_id, "token_access", "token_refresh"),
        )

        # Create accounts: 1001 (master), 2001/2002 (slaves)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (1001, %(org_id)s, 1, 10001, false, 'master', true, 1.0),
                (2001, %(org_id)s, 1, 20001, false, 'slave', true, 1.0),
                (2002, %(org_id)s, 1, 20002, false, 'slave', false, 1.0)
            """,
            {"org_id": org_id},
        )
    yield org_id


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
ORG_ID = 1                 # per seed_accounts_and_mappings (first org after RESTART IDENTITY)


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
            org_id=ORG_ID,
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
        repo.create_position_mapping(master_position_id=2, slave_account_id=SLAVE_ID, client_order_id="cm2.2001", org_id=ORG_ID)
        repo.activate_position_mapping(2001, "cm2.2001", slave_position_id=5002, slave_volume=100_000)

        # Seed the unfilled_slave_order mapping: order placed & linked to a master
        # fill, but no corresponding slave position ever appeared.
        repo.create_order_mapping(master_order_id=100, slave_account_id=SLAVE_ID, client_order_id="co100.2001", org_id=ORG_ID)
        repo.activate_order_mapping(2001, "co100.2001", slave_order_id=9000)
        repo.link_pending_fill(master_order_id=100, slave_account_id=SLAVE_ID, master_position_id=55)

        reconciler = Reconciler(
            clients_by_account=lambda account_id: client,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
            org_id=ORG_ID,
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
            org_id=ORG_ID,
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
            org_id=ORG_ID,
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
            org_id=ORG_ID,
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
        """dismiss() logs an event, hides the item immediately, and never
        dispatches."""
        reconciler = Reconciler(
            clients_by_account=Mock(),
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
            org_id=ORG_ID,
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
        # The dismissal takes effect immediately, not on the next resync.
        assert reconciler.current == []
        dispatcher.dispatch.assert_not_called()

    @pytest_twisted.inlineCallbacks
    def test_periodic_reruns_log_once_and_dismissals_stick(self, repo, dispatcher):
        """run() now also fires from the periodic resync loop, so: a
        persisting item is logged as a 'drift' warning exactly once (not
        once per minute); a dismissed item stays suppressed across reruns;
        and a dismissal whose condition clears is pruned, so the same
        condition re-alerts if it ever comes back."""
        reconciler = Reconciler(
            clients_by_account=Mock(),
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=MASTER_ID,
            org_id=ORG_ID,
        )
        pos = PositionSnapshot(position_id=999, symbol_id=1, side=Side.BUY,
                               volume=100_000, price=1.10, label="")
        snapshots = {'master': ([pos], [])}
        reconciler._fetch_snapshot = lambda account_id: defer.succeed(
            snapshots['master'] if account_id == MASTER_ID else ([], []))

        items = yield reconciler.run()
        assert [i.kind for i in items] == ['unmapped_master_position']
        item_id = items[0].id

        def warning_count():
            return len([r for r in self._drift_events(repo)
                        if r['severity'] == 'warning'])

        assert warning_count() == 1

        # Rerun with unchanged broker state: item persists, no second log.
        items = yield reconciler.run()
        assert [i.id for i in items] == [item_id]
        assert warning_count() == 1

        # Dismiss: suppressed on every subsequent rerun.
        yield reconciler.dismiss(item_id)
        items = yield reconciler.run()
        assert items == []
        assert warning_count() == 1

        # Condition clears -> dismissal pruned; reappearance re-alerts.
        snapshots['master'] = ([], [])
        items = yield reconciler.run()
        assert items == []
        snapshots['master'] = ([pos], [])
        items = yield reconciler.run()
        assert [i.id for i in items] == [item_id]
        assert warning_count() == 2

    @pytest_twisted.inlineCallbacks
    def test_run_is_org_scoped(self, repo, dispatcher, fake_server, make_client, db):
        """Org A's reconciler must not fetch snapshots for, or report drift
        against, org B's accounts or mappings."""
        srv, port = fake_server

        with psycopg.connect(db, autocommit=True) as conn:
            (org_a,) = conn.execute(
                "INSERT INTO orgs (name) VALUES ('Org A') RETURNING id").fetchone()
            (org_b,) = conn.execute(
                "INSERT INTO orgs (name) VALUES ('Org B') RETURNING id").fetchone()
            (conn_a,) = conn.execute(
                """INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,
                       granted_at, expires_at)
                   VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
                   RETURNING id""", (org_a,)).fetchone()
            (conn_b,) = conn.execute(
                """INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,
                       granted_at, expires_at)
                   VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
                   RETURNING id""", (org_b,)).fetchone()
            conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                       trader_login, is_live, role, enabled, multiplier)
                VALUES
                    (100, %(org_a)s, %(conn_a)s, 100, false, 'master', true, 1.0),
                    (101, %(org_a)s, %(conn_a)s, 101, false, 'slave', true, 1.0),
                    (200, %(org_b)s, %(conn_b)s, 200, false, 'master', true, 1.0),
                    (201, %(org_b)s, %(conn_b)s, 201, false, 'slave', true, 1.0)
                """,
                {"org_a": org_a, "conn_a": conn_a, "org_b": org_b, "conn_b": conn_b},
            )

        # One active mapping per org, each matching what the broker reports
        # (so it produces no drift on its own).
        repo.create_position_mapping(master_position_id=1, slave_account_id=101,
                                      client_order_id="cm1.101", org_id=org_a)
        repo.activate_position_mapping(101, "cm1.101", slave_position_id=5002, slave_volume=100_000)
        repo.create_position_mapping(master_position_id=2, slave_account_id=201,
                                      client_order_id="cm2.201", org_id=org_b)
        repo.activate_position_mapping(201, "cm2.201", slave_position_id=9002, slave_volume=100_000)

        srv.accounts = {100: "tok-100", 101: "tok-101", 200: "tok-200", 201: "tok-201"}
        srv.open_positions = {
            100: [{"position_id": 1, "symbol_id": 1, "volume": 100_000,
                   "trade_side": ProtoOATradeSide.BUY, "price": 1.10}],
            101: [
                {"position_id": 5002, "symbol_id": 1, "volume": 100_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.10, "label": "copy:m1"},
                # Labeled, unmapped -- a genuine orphan for org A.
                {"position_id": 5001, "symbol_id": 1, "volume": 50_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.20, "label": "copy:m999"},
            ],
            200: [{"position_id": 2, "symbol_id": 1, "volume": 100_000,
                   "trade_side": ProtoOATradeSide.BUY, "price": 1.15}],
            201: [
                {"position_id": 9002, "symbol_id": 1, "volume": 100_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.15, "label": "copy:m2"},
                # Same shape of orphan as org A's -- must NEVER be seen by
                # org A's Reconciler.
                {"position_id": 9000, "symbol_id": 1, "volume": 50_000,
                 "trade_side": ProtoOATradeSide.BUY, "price": 1.20, "label": "copy:m999"},
            ],
        }

        client = yield make_client([100, 101, 200, 201])
        requested_accounts = []

        def _clients_by_account(account_id):
            requested_accounts.append(account_id)
            return client

        reconciler = Reconciler(
            clients_by_account=_clients_by_account,
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=100,
            org_id=org_a,
        )

        items = yield reconciler.run()

        # Snapshot requests confined to org A's accounts -- org B's master/slave
        # (200/201) must never be queried, even though 201 is scripted with an
        # orphan of the exact same shape as org A's.
        assert set(requested_accounts) <= {100, 101}
        assert 200 not in requested_accounts
        assert 201 not in requested_accounts

        # Only org A's own orphan is reported; org B's identically-shaped
        # orphan (account 201) never leaks in.
        assert [(i.kind, i.account_id, i.position_id) for i in items] == [
            ('orphan_slave_position', 101, 5001),
        ]
        assert reconciler.current is items
        assert all(i.account_id in (100, 101) for i in reconciler.current)

        # 'drift' events are logged for org A only; org B's org_id never appears.
        rows = self._drift_events(repo)
        assert len(rows) == 1
        assert rows[0]['org_id'] == org_a
        assert all(r['org_id'] != org_b for r in rows)

    @pytest_twisted.inlineCallbacks
    def test_dry_run_org_stale_pending_is_not_reported_as_drift(self, repo, dispatcher, db):
        """Regression pin: run() must read dry_run from repo.get_org(self.org_id),
        not the removed Settings.dry_run. Before the fix, that read raised
        AttributeError, was swallowed, and silently fell back to dry_run=False
        -- so a dry-run org's stale-pending mappings (which STAY pending by
        design, see compute_drift) got reported as drift on every tick."""
        with psycopg.connect(db, autocommit=True) as conn:
            (org_id,) = conn.execute(
                "INSERT INTO orgs (name, dry_run) VALUES ('Dry Run Org', true) RETURNING id"
            ).fetchone()
            (conn_id,) = conn.execute(
                """INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,
                       granted_at, expires_at)
                   VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
                   RETURNING id""", (org_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,
                                       trader_login, is_live, role, enabled, multiplier)
                VALUES
                    (300, %(org_id)s, %(conn_id)s, 300, false, 'master', true, 1.0),
                    (301, %(org_id)s, %(conn_id)s, 301, false, 'slave', true, 1.0)
                """,
                {"org_id": org_id, "conn_id": conn_id},
            )

        repo.create_position_mapping(master_position_id=1, slave_account_id=301,
                                      client_order_id="cm1.301", org_id=org_id)
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute(
                "UPDATE mappings SET created_at = now() - interval '10 minutes' "
                "WHERE client_order_id = %s",
                ("cm1.301",),
            )

        reconciler = Reconciler(
            clients_by_account=Mock(),
            repo=repo,
            dispatcher=dispatcher,
            master_account_id=300,
            org_id=org_id,
        )
        reconciler._fetch_snapshot = lambda account_id: defer.succeed(([], []))

        items = yield reconciler.run()

        assert [i for i in items if i.kind == 'stale_pending_copy'] == []
        assert self._drift_events(repo) == []


# ---------- N8: a copy that never activates ----------

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def _pending_mapping(created_at, *, account_id=101, client_order_id="cm500.101",
                     master_position_id=500, status='pending'):
    return {
        'id': 1,
        'master_position_id': master_position_id,
        'master_order_id': None,
        'slave_account_id': account_id,
        'slave_position_id': None,
        'slave_order_id': None,
        'slave_volume': None,
        'client_order_id': client_order_id,
        'status': status,
        'error': None,
        'fill_price': None,
        'created_at': created_at,
        'updated_at': created_at,
    }


def _master_position(position_id=500):
    return PositionSnapshot(position_id=position_id, symbol_id=1, side=Side.BUY,
                            volume=10_000_000, price=1.1, label=f"copy:m{position_id}")


class TestStalePendingDrift:
    """N8: a mapping stuck in 'pending' is invisible to every other check."""

    def test_stale_pending_copy_is_reported(self):
        """RED before the fix: compute_drift returned []. The row has no
        slave_position_id (so missing_slave_copy skips it), the master
        position DOES have other active mapping rows (so
        unmapped_master_position never fires), and no broker-side label
        exists for an order that never reached the broker (so the orphan
        checks see nothing). One slave silently carried no exposure at all
        between the master's fill and its next action on that position.
        """
        stale = _pending_mapping(NOW - timedelta(seconds=STALE_PENDING_AFTER_S + 1))
        # Another slave's copy DID activate -- which is exactly what keeps
        # unmapped_master_position quiet about this master position.
        healthy = {
            **_pending_mapping(NOW, account_id=102, client_order_id="cm500.102"),
            'status': 'active', 'slave_position_id': 777, 'slave_volume': 10_000_000,
        }

        items = compute_drift(
            [_master_position()], [],
            {101: [], 102: [PositionSnapshot(777, 1, Side.BUY, 10_000_000, 1.1, "copy:m500")]},
            {101: [], 102: []},
            [stale, healthy], {101, 102}, now=NOW,
        )

        stale_items = [i for i in items if i.kind == 'stale_pending_copy']
        assert len(stale_items) == 1, items
        assert stale_items[0].account_id == 101
        assert stale_items[0].position_id == 500
        assert "pending" in stale_items[0].detail
        assert "cm500.101" in stale_items[0].detail

    def test_a_freshly_pending_copy_is_not_drift(self):
        """A copy dispatched moments ago is mid-flight, not drift."""
        fresh = _pending_mapping(NOW - timedelta(seconds=STALE_PENDING_AFTER_S - 1))

        items = compute_drift([_master_position()], [], {101: []}, {101: []},
                              [fresh], {101}, now=NOW)

        assert [i for i in items if i.kind == 'stale_pending_copy'] == []

    def test_stale_pending_for_a_disabled_slave_is_ignored(self):
        stale = _pending_mapping(NOW - timedelta(hours=1))

        items = compute_drift([_master_position()], [], {}, {},
                              [stale], set(), now=NOW)

        assert [i for i in items if i.kind == 'stale_pending_copy'] == []

    def test_dry_run_pending_mappings_are_not_drift(self):
        """Dry-run creates mappings that STAY pending by design
        (engine/dispatch.py:_handle_dry_run) -- reporting every one of them
        would bury Stage 1's drift list in noise."""
        stale = _pending_mapping(NOW - timedelta(hours=1))

        assert [i for i in compute_drift([_master_position()], [], {101: []}, {101: []},
                                         [stale], {101}, now=NOW, dry_run=True)
                if i.kind == 'stale_pending_copy'] == []
        # ... and the SAME rows are reported again once dry-run is off,
        # because at that point they are genuine leftovers.
        assert [i for i in compute_drift([_master_position()], [], {101: []}, {101: []},
                                         [stale], {101}, now=NOW, dry_run=False)
                if i.kind == 'stale_pending_copy'] != []

    def test_active_and_closed_mappings_are_never_stale_pending(self):
        old = NOW - timedelta(days=7)
        rows = [
            {**_pending_mapping(old, client_order_id="a"), 'status': 'active',
             'slave_position_id': 777, 'slave_volume': 1},
            {**_pending_mapping(old, client_order_id="b"), 'status': 'closed'},
            {**_pending_mapping(old, client_order_id="c"), 'status': 'failed'},
        ]

        items = compute_drift(
            [_master_position()], [],
            {101: [PositionSnapshot(777, 1, Side.BUY, 1, 1.1, "copy:m500")]}, {101: []},
            rows, {101}, now=NOW,
        )

        assert [i for i in items if i.kind == 'stale_pending_copy'] == []

    def test_stale_pending_id_is_stable_across_runs(self):
        stale = _pending_mapping(NOW - timedelta(hours=1))
        first = compute_drift([_master_position()], [], {101: []}, {101: []},
                              [stale], {101}, now=NOW)
        second = compute_drift([_master_position()], [], {101: []}, {101: []},
                               [stale], {101}, now=NOW + timedelta(minutes=5))
        stale_ids = lambda items: [i.id for i in items if i.kind == 'stale_pending_copy']
        assert stale_ids(first) == stale_ids(second) != []

    def test_stale_pending_order_copy_names_the_master_order(self):
        stale = {
            **_pending_mapping(NOW - timedelta(hours=1), client_order_id="co900.101"),
            'master_position_id': None, 'master_order_id': 900,
        }

        items = compute_drift([], [], {101: []}, {101: []}, [stale], {101}, now=NOW)

        stale_items = [i for i in items if i.kind == 'stale_pending_copy']
        assert len(stale_items) == 1
        assert stale_items[0].order_id == 900
        assert "order 900" in stale_items[0].detail
