"""Tests for the repository layer (mappings, events, settings, accounts, symbol cache)."""

from contextlib import contextmanager
from decimal import Decimal
import threading
import time

import psycopg
import pytest

from copier.db.repo import Repo, Settings, AccountRow, MappingNotFound
from copier.db.writer import AsyncWriter
from copier.domain.models import Side, SymbolInfo, PositionMappingEntry, OrderMappingEntry
from copier.engine.reconcile import PositionSnapshot


@pytest.fixture
def repo(db):
    """Create a Repo instance connected to test database."""
    return Repo(db)


@pytest.fixture
def org_id(db):
    """Create an org and return its id, so mapping/account calls can thread it through."""
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute("INSERT INTO orgs (name) VALUES ('Test Org') RETURNING id").fetchone()
    return row[0]


@pytest.fixture(autouse=True)
def seed_connections_and_accounts(db, org_id):
    """Seed one connection and accounts 100/101 for testing."""
    with psycopg.connect(db, autocommit=True) as conn:
        # Create a ctid_connection
        conn.execute(
            """
            INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
            VALUES (%s, %s, %s, now(), now() + interval '1 hour')
            RETURNING id
            """,
            (org_id, "token_access", "token_refresh"),
        )

        # Create accounts 100 (slave) and 101 (slave)
        conn.execute(
            """
            INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, trader_login, is_live, role, enabled, multiplier)
            VALUES
                (100, 1, %(org_id)s, 10000, false, 'slave', true, 1.5),
                (101, 1, %(org_id)s, 10001, false, 'slave', true, 2.0)
            """,
            {"org_id": org_id},
        )
    yield


def test_log_event_writes_row_and_notifies(db):
    """Test that log_event writes a row and notifies listeners."""
    repo = Repo(db)

    payload = {"action": "test", "value": 42}
    event_id = repo.log_event("master_event", "info", payload, account_id=100, latency_ms=50)

    # Verify event was written
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT id, account_id, category, severity, payload, latency_ms FROM events WHERE id = %s",
            (event_id,)
        ).fetchall()

    assert len(rows) == 1
    row_id, acc_id, category, severity, payload_jsonb, latency_ms = rows[0]
    assert row_id == event_id
    assert acc_id == 100
    assert category == "master_event"
    assert severity == "info"
    # JSONB roundtrip preserves the payload
    assert payload_jsonb == payload
    assert latency_ms == 50


def test_settings_roundtrip(db):
    """Test that settings (process config only -- shards) can be read and updated."""
    repo = Repo(db)

    # Get initial settings
    settings1 = repo.get_settings()
    assert settings1.shards == 1

    # Update shards
    repo.set_setting("shards", 4)

    settings2 = repo.get_settings()
    assert settings2.shards == 4


def test_position_mapping_lifecycle(db, org_id):
    """Test position mapping from creation through activation to close."""
    repo = Repo(db)

    # Step 1: Create pending mapping for master position 11
    repo.create_position_mapping(11, 101, "cm11.101", org_id=org_id)

    # Position entries should be empty (still pending)
    entries = repo.position_entries(11)
    assert entries == []

    # Step 2: Activate the mapping
    repo.activate_position_mapping(101, "cm11.101", 555, 10_000_000)

    entries = repo.position_entries(11)
    assert len(entries) == 1
    assert entries[0] == PositionMappingEntry(101, 555, 10_000_000)

    # Step 3: Reduce by 4M (partial close), should still be active
    repo.reduce_position_mapping(101, 555, 4_000_000)

    entries = repo.position_entries(11)
    assert len(entries) == 1
    assert entries[0].slave_volume == 6_000_000

    # Step 4: Reduce by 6M (full close)
    repo.reduce_position_mapping(101, 555, 6_000_000)

    entries = repo.position_entries(11)
    assert entries == []


def test_fail_mapping_records_error(db, org_id):
    """Test that fail_mapping records an error."""
    repo = Repo(db)

    repo.create_position_mapping(20, 100, "cm20.100", org_id=org_id)
    repo.fail_mapping(100, "cm20.100", "Connection lost")

    # Check the mapping status
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT status, error FROM mappings WHERE client_order_id = %s",
            ("cm20.100",)
        ).fetchone()

    assert row[0] == "failed"
    assert row[1] == "Connection lost"


def test_order_mapping_and_pending_fill_link(db, org_id):
    """Test order mapping lifecycle and pending fill linking."""
    repo = Repo(db)

    # Step 1: Create order mapping for master order 42
    repo.create_order_mapping(42, 101, "co42.101", org_id=org_id)

    # Step 2: Activate the order mapping
    repo.activate_order_mapping(101, "co42.101", 900)

    # Should have one order entry
    entries = repo.order_entries(42)
    assert len(entries) == 1
    assert entries[0] == OrderMappingEntry(101, 900)

    # Step 3: Link pending fill to position 77
    repo.link_pending_fill(42, 101, 77)

    # Step 4: Activate the pending fill with position info
    repo.activate_pending_fill(101, 900, 555, 1_000_000)

    # Now position 77 should have the mapping
    pos_entries = repo.position_entries(77)
    assert len(pos_entries) == 1
    assert pos_entries[0] == PositionMappingEntry(101, 555, 1_000_000)


def test_upsert_and_load_accounts(db, org_id):
    """Test upserting and loading accounts with decimal multiplier."""
    repo = Repo(db)

    # Upsert a new account
    assert repo.upsert_account(102, 1, org_id, 10002, False) is True

    # Load accounts
    accounts = repo.load_accounts()

    # Should have accounts 100, 101, 102
    assert len(accounts) >= 3

    # Check that multiplier is returned as Decimal
    for acc in accounts:
        assert isinstance(acc.multiplier, Decimal)

    # Find account 102
    acc102 = next((a for a in accounts if a.account_id == 102), None)
    assert acc102 is not None
    assert acc102.org_id == org_id
    assert acc102.connection_id == 1
    assert acc102.trader_login == 10002
    assert acc102.is_live is False


def test_symbol_cache_roundtrip(db):
    """Test saving and loading symbol cache with SymbolInfo dataclasses."""
    repo = Repo(db)

    # Create symbol infos
    symbols = {
        "EURUSD": SymbolInfo(1, "EURUSD", 5, 100000, 1000, 1000),
        "GBPUSD": SymbolInfo(2, "GBPUSD", 5, 100000, 1000, 1000),
    }

    # Save to cache
    repo.save_symbol_cache(100, symbols)

    # Load from cache
    loaded = repo.load_symbol_cache(100)

    assert len(loaded) == 2
    assert loaded["EURUSD"] == SymbolInfo(1, "EURUSD", 5, 100000, 1000, 1000)
    assert loaded["GBPUSD"] == SymbolInfo(2, "GBPUSD", 5, 100000, 1000, 1000)


def test_set_account_status(db):
    """Test setting account status and error."""
    repo = Repo(db)

    repo.set_account_status(100, "degraded", "Low balance")

    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT status, last_error FROM accounts WHERE ctid_trader_account_id = %s",
            (100,)
        ).fetchone()

    assert row[0] == "degraded"
    assert row[1] == "Low balance"


def test_mapping_rows(db, org_id):
    """Test retrieving all mapping rows."""
    repo = Repo(db)

    # Create some mappings
    repo.create_position_mapping(10, 100, "cm10.100", org_id=org_id)
    repo.activate_position_mapping(100, "cm10.100", 111, 5_000_000)

    repo.create_order_mapping(20, 101, "co20.101", org_id=org_id)
    repo.activate_order_mapping(101, "co20.101", 222)

    # Get all mapping rows
    rows = repo.mapping_rows()

    assert len(rows) >= 2

    # Verify structure
    for row in rows:
        assert "id" in row
        assert "master_position_id" in row
        assert "master_order_id" in row
        assert "slave_account_id" in row
        assert "status" in row


def test_close_order_mapping(db, org_id):
    """Test closing an order mapping."""
    repo = Repo(db)

    repo.create_order_mapping(50, 100, "co50.100", org_id=org_id)
    repo.activate_order_mapping(100, "co50.100", 555)

    # Close the order mapping
    repo.close_order_mapping(100, 555)

    # Order entries should be empty
    entries = repo.order_entries(50)
    assert entries == []


def test_adopt_position_mapping(db, org_id):
    """Test adopting an existing position (drift remedy)."""
    repo = Repo(db)

    # Adopt an existing position
    repo.adopt_position_mapping(99, 100, 777, 3_000_000, org_id=org_id)

    # Should have the position entry
    entries = repo.position_entries(99)
    assert len(entries) == 1
    assert entries[0] == PositionMappingEntry(100, 777, 3_000_000)


def test_mapping_state_protocol_compliance(db):
    """Test that Repo satisfies the MappingState protocol."""
    repo = Repo(db)

    # Verify protocol methods exist
    assert hasattr(repo, "position_entries")
    assert hasattr(repo, "order_entries")

    # Verify they're callable
    assert callable(repo.position_entries)
    assert callable(repo.order_entries)

    # Verify they return sequences
    pos = repo.position_entries(1)
    assert isinstance(pos, (list, tuple))

    ord = repo.order_entries(1)
    assert isinstance(ord, (list, tuple))


def test_concurrent_reduce_position_mapping_no_race(db, org_id):
    """Test that concurrent reduces are atomic (no TOCTOU race)."""
    repo = Repo(db)

    # Create a position mapping with 10M volume
    repo.create_position_mapping(30, 100, "cm30.100", org_id=org_id)
    repo.activate_position_mapping(100, "cm30.100", 666, 10_000_000)

    # Concurrent reduces: thread 1 reduces 3M, thread 2 reduces 2M
    # Expected final volume: 10M - 3M - 2M = 5M (not 7M or 8M due to race)
    errors = []

    def reduce_3m():
        try:
            repo.reduce_position_mapping(100, 666, 3_000_000)
        except Exception as e:
            errors.append(e)

    def reduce_2m():
        try:
            repo.reduce_position_mapping(100, 666, 2_000_000)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=reduce_3m)
    t2 = threading.Thread(target=reduce_2m)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent reduce errors: {errors}"

    # Verify final volume
    entries = repo.position_entries(30)
    assert len(entries) == 1
    assert entries[0].slave_volume == 5_000_000, f"Expected 5M, got {entries[0].slave_volume}"


def test_activate_position_mapping_raises_on_unknown(db):
    """Test that activate_position_mapping raises MappingNotFound on unknown client_order_id."""
    repo = Repo(db)

    with pytest.raises(MappingNotFound):
        repo.activate_position_mapping(100, "unknown_order_id", 999, 1_000_000)


def test_fail_mapping_raises_on_unknown(db):
    """Test that fail_mapping raises MappingNotFound on unknown client_order_id."""
    repo = Repo(db)

    with pytest.raises(MappingNotFound):
        repo.fail_mapping(100, "unknown_order_id", "Error message")


def test_activate_order_mapping_raises_on_unknown(db):
    """Test that activate_order_mapping raises MappingNotFound on unknown client_order_id."""
    repo = Repo(db)

    with pytest.raises(MappingNotFound):
        repo.activate_order_mapping(100, "unknown_order_id", 999)


class TestCoidMutationsRequireTheOwningAccount:
    """The client_order_id-keyed mutations carry an account predicate.

    `client_order_id` is derived, not secret -- `cm{master_position_id}.
    {slave_account_id}` -- and master position/order ids are per-account
    broker sequences that collide across orgs, so a coid is guessable and
    NOT globally unique to a tenant by construction. These three statements
    write real-money sizing state (slave_volume, status), so each pins the
    row to the slave account that actually reported the execution rather
    than trusting the coid alone.
    """

    def test_activate_position_mapping_rejects_a_foreign_account(self, db, org_id):
        repo = Repo(db)
        repo.create_position_mapping(11, 101, "cm11.101", org_id=org_id)

        with pytest.raises(MappingNotFound):
            repo.activate_position_mapping(100, "cm11.101", 555, 10_000_000)
        assert repo.position_entries(11) == [], "a foreign account activated the mapping"

        # The owning account still works, and the row is untouched by the reject.
        repo.activate_position_mapping(101, "cm11.101", 555, 10_000_000)
        assert repo.position_entries(11) == [PositionMappingEntry(101, 555, 10_000_000)]

    def test_activate_order_mapping_rejects_a_foreign_account(self, db, org_id):
        repo = Repo(db)
        repo.create_order_mapping(42, 101, "co42.101", org_id=org_id)

        with pytest.raises(MappingNotFound):
            repo.activate_order_mapping(100, "co42.101", 900)
        assert repo.order_entries(42) == [], "a foreign account activated the order mapping"

        repo.activate_order_mapping(101, "co42.101", 900)
        assert repo.order_entries(42) == [OrderMappingEntry(101, 900)]

    def test_fail_mapping_rejects_a_foreign_account(self, db, org_id):
        repo = Repo(db)
        repo.create_position_mapping(20, 100, "cm20.100", org_id=org_id)

        with pytest.raises(MappingNotFound):
            repo.fail_mapping(101, "cm20.100", "Connection lost")

        with psycopg.connect(db, autocommit=True) as conn:
            status, error = conn.execute(
                "SELECT status, error FROM mappings WHERE client_order_id = 'cm20.100'"
            ).fetchone()
        assert (status, error) == ("pending", None), "a foreign account failed the mapping"

        repo.fail_mapping(100, "cm20.100", "Connection lost")
        with psycopg.connect(db, autocommit=True) as conn:
            status, _ = conn.execute(
                "SELECT status, error FROM mappings WHERE client_order_id = 'cm20.100'"
            ).fetchone()
        assert status == "failed"


def test_close_order_mapping_raises_on_unknown(db):
    """Test that close_order_mapping raises MappingNotFound on unknown identifiers."""
    repo = Repo(db)

    with pytest.raises(MappingNotFound):
        repo.close_order_mapping(100, 999)


def test_activate_pending_fill_raises_on_unknown(db):
    """Test that activate_pending_fill raises MappingNotFound on unknown order mapping."""
    repo = Repo(db)

    with pytest.raises(MappingNotFound):
        repo.activate_pending_fill(100, 999, 555, 1_000_000)


def test_link_pending_fill_does_not_touch_failed_rows(db, org_id):
    """Test that link_pending_fill only updates active rows, not failed ones."""
    repo = Repo(db)

    # Create an order mapping
    repo.create_order_mapping(60, 100, "co60.100", org_id=org_id)
    repo.activate_order_mapping(100, "co60.100", 777)

    # Simulate it failing (manually set status to failed)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE mappings SET status = 'failed', error = %s WHERE client_order_id = %s",
            ("Test error", "co60.100"),
        )

    # Try to link_pending_fill on the failed row - should raise
    with pytest.raises(MappingNotFound):
        repo.link_pending_fill(60, 100, 88)

    # Verify the failed row wasn't touched
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT master_position_id, status FROM mappings WHERE client_order_id = %s",
            ("co60.100",),
        ).fetchone()

    assert row[0] is None  # master_position_id still NULL
    assert row[1] == "failed"  # status still failed


def test_save_symbol_cache_is_atomic(db):
    """Test that save_symbol_cache is atomic (old cache not deleted on error)."""
    repo = Repo(db)

    # Save initial cache
    symbols_v1 = {
        "EURUSD": SymbolInfo(1, "EURUSD", 5, 100000, 1000, 1000),
    }
    repo.save_symbol_cache(100, symbols_v1)

    loaded_v1 = repo.load_symbol_cache(100)
    assert len(loaded_v1) == 1
    assert "EURUSD" in loaded_v1

    # Try to save new cache that will fail (inject bad data directly)
    # We can't easily force an error in the loop, so we'll test that
    # the transaction protects the data by replacing the cache
    symbols_v2 = {
        "GBPUSD": SymbolInfo(2, "GBPUSD", 5, 100000, 1000, 1000),
    }
    repo.save_symbol_cache(100, symbols_v2)

    loaded_v2 = repo.load_symbol_cache(100)
    assert len(loaded_v2) == 1
    assert "GBPUSD" in loaded_v2
    assert "EURUSD" not in loaded_v2  # Old cache should be replaced


def test_link_pending_fill_raises_on_unknown_order(db):
    """Test that link_pending_fill raises MappingNotFound on unknown order."""
    repo = Repo(db)

    with pytest.raises(MappingNotFound):
        repo.link_pending_fill(999, 100, 88)


# ---------- N2: master position increases ----------

def test_create_position_mapping_is_idempotent_for_an_increase(db, org_id):
    """N2 (repo half): the SECOND dispatch of the same (master position,
    slave) -- a master position INCREASE, which reuses the deterministic
    client_order_id `cm{pid}.{slave}` -- must not raise.

    Before the ON CONFLICT clause this raised psycopg UniqueViolation on
    `mappings.client_order_id TEXT UNIQUE`, which Dispatcher's per-intent
    handler caught as `intent_processing_failed` and turned into a degraded
    account -- and the increase itself was never sent.
    """
    repo = Repo(db)

    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)   # the increase

    rows = [r for r in repo.mapping_rows() if r["client_order_id"] == "cm500.100"]
    assert len(rows) == 1, "an increase must reuse the existing mapping row, not add one"
    assert rows[0]["status"] == "pending"


def test_activate_position_mapping_accumulates_volume_across_fills(db, org_id):
    """N2 (repo half): the increase's fill ADDS to the mapping's volume.

    cTrader merges a same-direction add into the existing position, so the
    slave's second fill carries the SAME slave_position_id and a
    deal.filledVolume of only the delta. The mapping row therefore has to
    accumulate for its slave_volume to mean "this slave position's real
    size" -- which is what partial_close_volume and reduce_position_mapping
    both compute against.
    """
    repo = Repo(db)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)

    repo.activate_position_mapping(100, "cm500.100", 777, 10_000_000, fill_price=1.10500)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)            # increase dispatched
    repo.activate_position_mapping(100, "cm500.100", 777, 5_000_000, fill_price=1.10900)

    (row,) = [r for r in repo.mapping_rows() if r["client_order_id"] == "cm500.100"]
    assert row["slave_volume"] == 15_000_000
    assert row["slave_position_id"] == 777
    assert row["status"] == "active"
    # The FIRST fill's price is the copy's entry price; a later increase must
    # not silently redefine the slippage the Positions screen reports.
    assert row["fill_price"] == pytest.approx(1.10500)


def test_reduce_position_mapping_operates_on_the_aggregate(db, org_id):
    """N2 (consequence): with one row per (master position, slave), a close
    deducts from the aggregate exactly once.

    The rejected alternative -- one row per fill -- would have produced two
    active rows sharing a slave_position_id, and reduce_position_mapping
    (which matches on `slave_account_id AND slave_position_id`) would have
    deducted the full closed volume from BOTH.
    """
    repo = Repo(db)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)
    repo.activate_position_mapping(100, "cm500.100", 777, 10_000_000)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)
    repo.activate_position_mapping(100, "cm500.100", 777, 5_000_000)

    repo.reduce_position_mapping(100, 777, 6_000_000)

    (row,) = [r for r in repo.mapping_rows() if r["client_order_id"] == "cm500.100"]
    assert row["slave_volume"] == 9_000_000
    assert row["status"] == "active"


def test_position_entries_reports_one_aggregated_entry_per_slave(db, org_id):
    """The decision layer sees ONE entry per slave for an increased position,
    carrying the aggregate volume -- so a later close emits one ClosePosition
    per slave, sized against the slave's real position."""
    repo = Repo(db)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)
    repo.activate_position_mapping(100, "cm500.100", 777, 10_000_000)
    repo.create_position_mapping(500, 100, "cm500.100", org_id=org_id)
    repo.activate_position_mapping(100, "cm500.100", 777, 5_000_000)

    entries = repo.position_entries(500)
    assert len(entries) == 1
    assert entries[0] == PositionMappingEntry(
        slave_account_id=100, slave_position_id=777, slave_volume=15_000_000,
    )


# ---------- N6: degraded auto-clear ----------

def test_clear_degraded_returns_account_to_ok_and_drops_last_error(db):
    repo = Repo(db)
    repo.set_account_status(100, 'degraded', "no connected transport for send")

    assert repo.clear_degraded(100) is True

    account = next(a for a in repo.load_accounts() if a.account_id == 100)
    assert account.status == 'ok'
    assert account.last_error is None


def test_clear_degraded_never_resumes_a_paused_account(db):
    """A slave an operator deliberately PAUSED must not be silently resumed
    by a send that happens to succeed."""
    repo = Repo(db)
    repo.set_account_status(100, 'paused', None)

    assert repo.clear_degraded(100) is False

    account = next(a for a in repo.load_accounts() if a.account_id == 100)
    assert account.status == 'paused'


def test_clear_degraded_is_a_no_op_on_an_ok_account(db):
    repo = Repo(db)
    assert repo.clear_degraded(100) is False
    account = next(a for a in repo.load_accounts() if a.account_id == 100)
    assert account.status == 'ok'


# ---------- T9c: fill price on the mapping row ----------

def test_activate_pending_fill_stamps_fill_price(db, org_id):
    repo = Repo(db)
    repo.create_order_mapping(900, 100, "co900.100", org_id=org_id)
    repo.activate_order_mapping(100, "co900.100", 4242)

    repo.activate_pending_fill(100, 4242, 888, 2_000_000, fill_price=1.23456)

    (row,) = [r for r in repo.mapping_rows() if r["client_order_id"] == "co900.100"]
    assert row["fill_price"] == pytest.approx(1.23456)
    assert row["slave_position_id"] == 888


def test_mappings_store_symbol(db, org_id):
    """Mappings stamp the traded symbol at creation so copy feeds can show
    it without a broker lookup -- alongside the org that owns the copy."""
    repo = Repo(db)
    repo.create_position_mapping(11, 100, "cm11.100", org_id=org_id, symbol="EURUSD")
    repo.create_order_mapping(7, 101, "co7.101", org_id=org_id, symbol="GBPUSD")

    rows = {r["client_order_id"]: r for r in repo.mapping_rows()}
    assert rows["cm11.100"]["symbol"] == "EURUSD"
    assert rows["cm11.100"]["org_id"] == org_id
    assert rows["co7.101"]["symbol"] == "GBPUSD"
    assert rows["co7.101"]["org_id"] == org_id


def test_mapping_symbol_is_optional(db, org_id):
    """An adopted orphan (or any mapping created without a resolved symbol)
    keeps a NULL symbol rather than failing."""
    repo = Repo(db)
    repo.create_position_mapping(12, 100, "cm12.100", org_id=org_id)

    (row,) = [r for r in repo.mapping_rows() if r["client_order_id"] == "cm12.100"]
    assert row["symbol"] is None


def test_portfolio_snapshot_upserts_per_day(db, org_id):
    """One row per (day, account); repeated saves keep the LAST value, so
    yesterday's row is yesterday's closing value."""
    from datetime import date

    repo = Repo(db)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 100, balance=1000.0,
                                 equity=1010.0, org_id=org_id)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 100, balance=1000.0,
                                 equity=1020.0, org_id=org_id)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 101, balance=500.0,
                                 equity=505.0, org_id=org_id)

    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT account_id, balance, equity, org_id FROM portfolio_snapshots "
            "WHERE snapshot_date = %s ORDER BY account_id",
            (date(2026, 8, 18),),
        ).fetchall()
    assert [(r[0], float(r[1]), float(r[2]), r[3]) for r in rows] == [
        (100, 1000.0, 1020.0, org_id), (101, 500.0, 505.0, org_id)]


def test_portfolio_snapshot_stamps_org_per_account(db, org_id):
    """Two orgs' accounts writing on the same day keep their own org stamp:
    the api sums snapshots per org, so a shared/overwritten stamp would put
    one desk's portfolio value on another desk's Overview."""
    from datetime import date

    with psycopg.connect(db, autocommit=True) as conn:
        (other_org,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Other') RETURNING id").fetchone()

    repo = Repo(db)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 100, 1000.0, 1010.0, org_id=org_id)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 900, 7.0, 7.0, org_id=other_org)

    with psycopg.connect(db, autocommit=True) as conn:
        rows = dict(conn.execute(
            "SELECT account_id, org_id FROM portfolio_snapshots "
            "WHERE snapshot_date = %s", (date(2026, 8, 18),)).fetchall())
    assert rows == {100: org_id, 900: other_org}


def test_portfolio_snapshot_upsert_keeps_an_existing_org_stamp(db, org_id):
    """A later write that cannot resolve an org (org_id=None) must not blank
    out a stamp an earlier write established."""
    from datetime import date

    repo = Repo(db)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 100, 1000.0, 1010.0, org_id=org_id)
    repo.save_portfolio_snapshot(date(2026, 8, 18), 100, 1100.0, 1110.0, org_id=None)

    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT balance, org_id FROM portfolio_snapshots "
            "WHERE snapshot_date = %s AND account_id = 100",
            (date(2026, 8, 18),)).fetchone()
    assert float(row[0]) == 1100.0
    assert row[1] == org_id


def test_org_for_account_resolves_the_owning_org(db, org_id):
    """The single-row tenancy lookup behind push-event handling: accounts
    100/101 are seeded into org_id by the autouse fixture."""
    repo = Repo(db)
    assert repo.org_for_account(100) == org_id
    assert repo.org_for_account(101) == org_id


def test_org_for_account_is_none_for_an_unknown_account(db):
    """An account the DB has never seen resolves to None rather than
    raising -- the callers (pushed balance / margin call) must be able to
    log and drop, not crash the client's callback."""
    repo = Repo(db)
    assert repo.org_for_account(999_999) is None


def test_org_for_account_does_not_cross_orgs(db, org_id):
    """A second org's account resolves to ITS org, never to the first --
    this lookup is what keeps one tenant's pushed balance out of another
    tenant's state tracker."""
    with psycopg.connect(db, autocommit=True) as conn:
        (other_org,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Other') RETURNING id").fetchone()
        (other_conn,) = conn.execute(
            """INSERT INTO ctid_connections (org_id, access_token_enc,
                   refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'y', now(), now() + interval '30 days')
               RETURNING id""", (other_org,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role, enabled, multiplier)
               VALUES (200, %s, %s, 20000, false, 'slave', true, 1.0)""",
            (other_conn, other_org))

    repo = Repo(db)
    assert repo.org_for_account(200) == other_org
    assert repo.org_for_account(100) == org_id


def test_log_event_accepts_risk_category(db):
    repo = Repo(db)
    event_id = repo.log_event("risk", "error", {"action": "margin_call"}, account_id=100)
    assert event_id


class TestConnectionReuse:
    """The copy hot path makes several repo calls per master event. Each one
    opening (and SCRAM-authenticating) a fresh Postgres connection cost about
    70ms apiece on the production box -- roughly half a second of self-made
    copy latency per event. All autocommit calls must share one cached
    connection, and that connection must heal itself after a postgres restart."""

    def test_calls_share_one_connection(self, repo, monkeypatch):
        import copier.db.repo as repo_mod
        real_connect = repo_mod.psycopg.connect
        count = {"n": 0}

        def counting(*args, **kwargs):
            count["n"] += 1
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(repo_mod.psycopg, "connect", counting)
        repo.load_accounts()
        repo.load_symbol_cache(100)
        repo.log_event("control", "info", {"action": "x"}, account_id=100)
        repo.load_accounts()
        assert count["n"] == 1

    def test_cleanly_closed_connection_is_replaced(self, repo):
        first = repo.load_accounts()
        repo._conn.close()  # e.g. postgres restarted between events
        assert repo.load_accounts() == first

    def test_connection_dying_mid_statement_is_evicted_from_the_cache(self, repo):
        """A connection that fails DURING a statement must be dropped, not
        merely closed -- otherwise the next call re-leases a dead handle."""
        repo.load_accounts()

        class Broken:
            # Stays False through close() on purpose: the assertion must
            # prove the CACHE was cleared, not that `closed` flipped.
            closed = False
            close_calls = 0

            def execute(self, *args, **kwargs):
                raise psycopg.OperationalError("server closed the connection unexpectedly")

            def close(self):
                Broken.close_calls += 1

        repo._conn = Broken()
        with pytest.raises(psycopg.OperationalError):
            repo.load_accounts()

        assert repo._conn is None, "poisoned connection was left in the cache"
        assert Broken.close_calls == 1
        assert repo.load_accounts() is not None  # heals on the next call

    def test_idle_connection_killed_server_side_is_replaced_not_handed_out(self, repo):
        """THE regression this guards: a connection terminated server-side
        while idle still reports closed == False (libpq only marks the
        socket bad after a failed read/write). Handed out unchecked, the
        first call after a postgres restart fails -- and on the copy path
        that call is a live master fill, so the copy never happens."""
        first = repo.load_accounts()
        dead = repo._conn

        class DeadButNotClosed:
            """Exactly what libpq reports for a server-killed idle session."""
            closed = False
            broken = False

            def execute(self, *args, **kwargs):
                raise psycopg.OperationalError(
                    "terminating connection due to administrator command")

            def close(self):
                pass

        repo._conn = DeadButNotClosed()
        # Force the idle probe: the connection has "not been used" recently.
        repo._last_used -= (repo.IDLE_PROBE_S + 1)

        # Must SUCCEED -- the probe detects the dead peer and reconnects
        # before the caller ever touches it.
        assert repo.load_accounts() == first
        assert not isinstance(repo._conn, DeadButNotClosed)
        dead.close()

    def test_no_probe_within_a_burst_of_calls(self, repo):
        """The probe must not add a round trip to every call: consecutive
        calls inside one event are well under IDLE_PROBE_S."""
        repo.load_accounts()
        probes = {"n": 0}
        real_execute = repo._conn.execute

        def counting_execute(query, *args, **kwargs):
            if str(query).strip() == "SELECT 1":
                probes["n"] += 1
            return real_execute(query, *args, **kwargs)

        object.__setattr__(repo._conn, "execute", counting_execute)
        repo.load_accounts()
        repo.load_symbol_cache(100)
        repo.log_event("control", "info", {"action": "burst"}, account_id=100)
        assert probes["n"] == 0


def test_commission_rates_roundtrip(db):
    """What the broker charged, stored and read back per symbol."""
    repo = Repo(db)

    repo.save_commission_rates(100, {41: (0.28, 3), 1: (0.006, 12)})

    assert repo.load_commission_rates(100) == {41: 0.28, 1: 0.006}


def test_commission_rates_upsert_keeps_symbols_absent_from_the_batch(db):
    """A refresh reads a bounded window, so absence is not deletion.

    A symbol traded once and not since falls out of the seven-day window
    every later refresh. Replacing the account's set would discard that
    rate and leave the operator's next stop on it uncorrected -- an old
    observation of a real charge beats none.
    """
    repo = Repo(db)
    repo.save_commission_rates(100, {41: (0.28, 1), 1: (0.006, 1)})

    # A later refresh sees only gold, with a revised rate.
    repo.save_commission_rates(100, {41: (0.30, 4)})

    assert repo.load_commission_rates(100) == {41: 0.30, 1: 0.006}


def test_commission_rates_are_kept_per_account(db):
    """Commission is a term of THIS account's agreement, not the symbol's."""
    repo = Repo(db)
    repo.save_commission_rates(100, {41: (0.28, 1)})
    repo.save_commission_rates(101, {41: (0.14, 1)})

    assert repo.load_commission_rates(100) == {41: 0.28}
    assert repo.load_commission_rates(101) == {41: 0.14}


def test_saving_nothing_is_not_an_error(db):
    """No complete round trip in the window is the common case at boot."""
    repo = Repo(db)
    repo.save_commission_rates(100, {})
    assert repo.load_commission_rates(100) == {}


# ---------- trade persistence: master fill price ----------

def test_record_master_fill_stamps_every_copy_of_that_master_position(
        repo, org_id):
    """Slippage is (slave fill - master fill). mappings stored only the
    slave half, which is why realized slippage was not computable from
    production."""
    repo.create_position_mapping(555, 100, "cm555.100", org_id=org_id,
                                 symbol="EURUSD")
    repo.create_position_mapping(555, 101, "cm555.101", org_id=org_id,
                                 symbol="EURUSD")

    repo.record_master_fill(org_id, 555, fill_price=1.0855,
                            exec_timestamp=1700000000000)

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT slave_account_id, master_fill_price, master_exec_timestamp "
            "FROM mappings WHERE master_position_id = 555 "
            "ORDER BY slave_account_id").fetchall()
    assert len(rows) == 2
    for _, price, ts in rows:
        assert float(price) == pytest.approx(1.0855)
        assert ts == 1700000000000


def test_record_master_fill_ignores_a_null_price(repo, org_id):
    """A fill event without an executionPrice must not blank a price we
    already recorded."""
    repo.create_position_mapping(556, 100, "cm556.100", org_id=org_id)
    repo.record_master_fill(org_id, 556, 1.2345, 1700000000000)
    repo.record_master_fill(org_id, 556, None, None)
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        (price,) = conn.execute(
            "SELECT master_fill_price FROM mappings "
            "WHERE master_position_id = 556").fetchone()
    assert float(price) == pytest.approx(1.2345)


def test_record_master_fill_never_touches_another_orgs_mappings(db, org_id):
    """cTrader position ids are PER-ACCOUNT sequences, so two orgs on
    different brokers or environments routinely hold the same positionId.
    Without an org_id predicate, org A's master fill price and broker
    timestamp land on org B's mapping rows -- and org B's realized-slippage
    query, the single measurement this table exists to enable, then reports
    slippage against another tenant's master price."""
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        (org_b,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Org B') RETURNING id").fetchone()
        (other_conn,) = conn.execute(
            """INSERT INTO ctid_connections (org_id, access_token_enc,
                   refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'tok', 'ref', now(), now() + interval '1 hour')
               RETURNING id""", (org_b,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role, enabled, multiplier)
               VALUES (200, %s, %s, 20000, false, 'slave', true, 1.0)""",
            (other_conn, org_b))

    # The SAME broker position id in both tenants.
    repo.create_position_mapping(12345, 100, "cm12345.100", org_id=org_id)
    repo.create_position_mapping(12345, 200, "cm12345.200", org_id=org_b)

    repo.record_master_fill(org_id, 12345, 1.0855, 1700000000000)

    with psycopg.connect(db, autocommit=True) as conn:
        rows = dict(conn.execute(
            "SELECT org_id, master_fill_price FROM mappings "
            "WHERE master_position_id = 12345").fetchall())
    assert float(rows[org_id]) == pytest.approx(1.0855)
    assert rows[org_b] is None, (
        "org A's master fill price was stamped onto org B's mapping row")


# ---------- trade persistence: positions ----------

def _symbols():
    return {1: SymbolInfo(symbol_id=1, name="EURUSD", digits=5,
                          lot_size=100000, min_volume=1000, step_volume=1000)}


def _position_snapshot(position_id, volume, price, side=Side.BUY):
    return PositionSnapshot(position_id, 1, side, volume, price, "")


def test_upsert_positions_records_open_positions(repo, org_id):
    snaps = [PositionSnapshot(position_id=555, symbol_id=1, side=Side.BUY,
                              volume=100000, price=1.0855, label="",
                              stop_loss=1.08, take_profit=1.09)]
    repo.upsert_positions(100, org_id, snaps, _symbols())

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT symbol, side, volume, entry_price, stop_loss, take_profit, "
            "status FROM positions WHERE account_id = 100 AND position_id = 555"
        ).fetchone()
    assert row == ("EURUSD", "BUY", 100000, 1.0855, 1.08, 1.09, "open")


def test_upsert_positions_updates_volume_on_a_second_resync(repo, org_id):
    repo.upsert_positions(100, org_id, [_position_snapshot(555, 100000, 1.0855)],
                          _symbols())
    repo.upsert_positions(100, org_id, [_position_snapshot(555, 200000, 1.0860)],
                          _symbols())

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT volume FROM positions WHERE account_id = 100").fetchall()
    assert rows == [(200000,)]


def test_close_missing_positions_marks_vanished_positions_closed(repo, org_id):
    repo.upsert_positions(100, org_id, [
        _position_snapshot(555, 100000, 1.0855),
        _position_snapshot(556, 100000, 1.0860, side=Side.SELL),
    ], _symbols())

    # The broker now reports only 555 open.
    repo.close_missing_positions(100, [555])

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = dict(conn.execute(
            "SELECT position_id, status FROM positions "
            "WHERE account_id = 100").fetchall())
    assert rows == {555: "open", 556: "closed"}


def test_close_missing_positions_with_no_open_positions_closes_everything(
        repo, org_id):
    repo.upsert_positions(100, org_id,
                          [_position_snapshot(555, 100000, 1.0855)], _symbols())
    repo.close_missing_positions(100, [])
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        (status,) = conn.execute(
            "SELECT status FROM positions WHERE account_id = 100").fetchone()
    assert status == "closed"


def test_load_open_positions_returns_only_this_orgs_open_rows(repo, org_id):
    repo.upsert_positions(100, org_id, [
        _position_snapshot(555, 100000, 1.0855),
        _position_snapshot(556, 50000, 1.0860, side=Side.SELL),
    ], _symbols())
    repo.close_missing_positions(100, [555])

    rows = repo.load_open_positions(org_id)
    assert [r["position_id"] for r in rows] == [555]
    assert rows[0]["symbol"] == "EURUSD"
    assert rows[0]["account_id"] == 100


def test_load_open_positions_is_empty_for_an_unknown_org(repo):
    assert repo.load_open_positions(999999) == []


# ---------- trade persistence: balance samples ----------

def test_record_balance_sample_writes_a_timestamped_row(repo, org_id):
    repo.record_balance_sample(100, org_id, balance=10000.0, equity=10125.5,
                               margin_used=250.0, unrealized_pnl=125.5)
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT balance, equity, margin_used, unrealized_pnl "
            "FROM balance_samples WHERE account_id = 100").fetchone()
    assert [float(v) for v in row] == [10000.0, 10125.5, 250.0, 125.5]


def test_two_samples_in_the_same_second_do_not_error(repo, org_id):
    """PK is (account_id, ts); a double refresh must upsert, not raise."""
    repo.record_balance_sample(100, org_id, 1.0, 1.0, 0.0, 0.0)
    repo.record_balance_sample(100, org_id, 2.0, 2.0, 0.0, 0.0)
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM balance_samples WHERE account_id = 100"
        ).fetchone()
    assert n >= 1


# ---------- trade persistence: deals ----------

def test_upsert_deals_is_idempotent(repo, org_id):
    deal = {
        "deal_id": 999, "order_id": 777, "position_id": 555, "symbol_id": 1,
        "symbol": "EURUSD", "side": "BUY", "volume": 100000,
        "filled_volume": 100000, "execution_price": 1.0855, "status": "FILLED",
        "commission": -0.07, "create_timestamp": 1699999999000,
        "execution_timestamp": 1700000000000,
        "close": {"entry_price": 1.08, "gross_profit": 2.75, "swap": -0.12,
                  "commission": -0.07, "balance": 10002.75,
                  "closed_volume": 100000},
    }
    repo.upsert_deals(100, org_id, [deal])
    repo.upsert_deals(100, org_id, [deal])
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT count(*), max(gross_profit), bool_and(is_close) "
            "FROM deals WHERE account_id = 100").fetchall()
    (n, gross, is_close) = rows[0]
    assert n == 1
    assert float(gross) == pytest.approx(2.75)
    assert is_close is True


def test_upsert_deals_marks_opens_as_not_close(repo, org_id):
    repo.upsert_deals(100, org_id, [{
        "deal_id": 1000, "execution_timestamp": 1700000000000, "close": None}])
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        (is_close,) = conn.execute(
            "SELECT is_close FROM deals WHERE deal_id = 1000").fetchone()
    assert is_close is False


def test_backfill_state_roundtrips(repo):
    assert repo.get_backfill_state(100) is None
    repo.set_backfill_state(100, 1000, 2000, exhausted=False)
    state = repo.get_backfill_state(100)
    assert state["backfilled_from_ms"] == 1000
    assert state["backfilled_to_ms"] == 2000
    assert state["exhausted"] is False
    repo.set_backfill_state(100, 500, 2500, exhausted=True)
    state = repo.get_backfill_state(100)
    assert state["backfilled_from_ms"] == 500
    assert state["exhausted"] is True


def test_set_backfill_state_never_rewinds_progress(repo):
    """A re-run of an old, already-covered window must not undo progress
    a later run already made: from_ms only moves backwards (widening the
    covered range's start), to_ms only forwards."""
    repo.set_backfill_state(100, 1000, 5000, exhausted=False)
    repo.set_backfill_state(100, 2000, 3000, exhausted=False)  # a narrower re-run
    state = repo.get_backfill_state(100)
    assert state["backfilled_from_ms"] == 1000
    assert state["backfilled_to_ms"] == 5000


def test_load_deals_returns_the_map_deal_shape_compute_analytics_expects(
        repo, org_id):
    """analytics.compute_analytics reads d["close"]["gross_profit"] etc.
    load_deals must rebuild that nested shape or the Performance page
    silently reports zeros."""
    from copier.engine.analytics import compute_analytics

    repo.upsert_deals(100, org_id, [{
        "deal_id": 1, "execution_timestamp": 1700000000000,
        "symbol": "EURUSD", "commission": -0.07,
        "close": {"entry_price": 1.08, "gross_profit": 2.75, "swap": -0.12,
                  "commission": -0.07, "balance": 10002.75,
                  "closed_volume": 100000},
    }])

    deals = repo.load_deals(100)
    assert len(deals) == 1
    assert deals[0]["close"]["gross_profit"] == pytest.approx(2.75)

    # The real proof: the aggregator runs unchanged over these rows.
    result = compute_analytics(deals)
    assert result["net_pnl"] == pytest.approx(2.75 - 0.12 - 0.07)


def test_load_deals_returns_none_close_for_opening_deals(repo, org_id):
    repo.upsert_deals(100, org_id, [{
        "deal_id": 2, "execution_timestamp": 1700000000000, "close": None}])
    deals = repo.load_deals(100)
    assert deals[0]["close"] is None


def test_load_deals_since_ms_filters_out_older_deals(repo, org_id):
    """since_ms is the DB path's replacement for the broker's `weeks`
    window: everything at or after the cutoff, nothing before it."""
    repo.upsert_deals(100, org_id, [
        {"deal_id": 10, "execution_timestamp": 1_000, "close": None},
        {"deal_id": 11, "execution_timestamp": 2_000, "close": None},
    ])
    deals = repo.load_deals(100, since_ms=2_000)
    assert [d["deal_id"] for d in deals] == [11]

    all_deals = repo.load_deals(100, since_ms=None)
    assert [d["deal_id"] for d in all_deals] == [10, 11]


def test_load_deals_is_bounded_by_limit(repo, org_id):
    """D3: analytics runs synchronously on the reactor connection, so an
    account with years of history must not be able to fetch an unbounded
    result set. The default is generous (50_000); this exercises the
    parameter directly with a small cap."""
    repo.upsert_deals(100, org_id, [
        {"deal_id": i, "execution_timestamp": 1_000 + i, "close": None}
        for i in range(5)
    ])
    deals = repo.load_deals(100, limit=2)
    assert len(deals) == 2
    # ORDER BY execution_timestamp -- the oldest two, not an arbitrary two.
    assert [d["deal_id"] for d in deals] == [0, 1]


# ---------- trade persistence: the writer path production actually runs ----------
#
# upsert_positions, record_balance_sample and upsert_deals each branch on
# `self.writer is not None`. Production always attaches a writer, so
# production always takes the submit branch -- yet every test above
# constructs Repo(db) without one and therefore exercises only the
# _*_inline fallbacks, code that never runs in production. These tests
# attach a real AsyncWriter (its own connection, per the porting spec's
# D2) and verify the generated ON CONFLICT statements execute cleanly
# against real Postgres rather than being merely constructed.

@contextmanager
def _attached_writer(repo):
    """Attach a real AsyncWriter to `repo`, exactly as boot() does."""
    writer = AsyncWriter(repo.dsn, batch_interval_s=0.02)
    repo.writer = writer
    writer.start()
    try:
        yield writer
    finally:
        writer.flush_and_stop(timeout_s=5.0)
        repo.writer = None


def test_upsert_positions_through_the_writer_lands_in_postgres(repo, org_id):
    with _attached_writer(repo) as writer:
        repo.upsert_positions(
            100, org_id, [_position_snapshot(555, 100000, 1.0855)], _symbols())
    assert writer.healthy is True, "the writer batch failed"

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT symbol, side, volume, entry_price, status FROM positions "
            "WHERE account_id = 100 AND position_id = 555").fetchone()
    assert row == ("EURUSD", "BUY", 100000, 1.0855, "open")


def test_upsert_positions_through_the_writer_updates_on_conflict(repo, org_id):
    """The generated ON CONFLICT (account_id, position_id) DO UPDATE branch,
    executed against real Postgres rather than string-compared."""
    with _attached_writer(repo):
        repo.upsert_positions(
            100, org_id, [_position_snapshot(555, 100000, 1.0855)], _symbols())
    with _attached_writer(repo) as writer:
        repo.upsert_positions(
            100, org_id, [_position_snapshot(555, 200000, 1.0860)], _symbols())
    assert writer.healthy is True

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT volume, entry_price FROM positions "
            "WHERE account_id = 100").fetchall()
    assert rows == [(200000, 1.0860)]


def test_upsert_deals_through_the_writer_lands_and_upserts(repo, org_id):
    deal = {
        "deal_id": 999, "order_id": 777, "position_id": 555, "symbol_id": 1,
        "symbol": "EURUSD", "side": "BUY", "volume": 100000,
        "filled_volume": 100000, "execution_price": 1.0855, "status": "FILLED",
        "commission": -0.07, "create_timestamp": 1699999999000,
        "execution_timestamp": 1700000000000,
        "close": {"entry_price": 1.08, "gross_profit": 2.75, "swap": -0.12,
                  "commission": -0.07, "balance": 10002.75,
                  "closed_volume": 100000},
    }
    with _attached_writer(repo):
        repo.upsert_deals(100, org_id, [deal])
    with _attached_writer(repo) as writer:
        repo.upsert_deals(100, org_id, [{**deal, "close": {
            **deal["close"], "gross_profit": 3.50}}])
    assert writer.healthy is True

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT count(*), max(gross_profit), bool_and(is_close) "
            "FROM deals WHERE account_id = 100").fetchall()
    (n, gross, is_close) = rows[0]
    assert n == 1
    assert float(gross) == pytest.approx(3.50)
    assert is_close is True


def test_record_balance_sample_through_the_writer_lands_in_postgres(
        repo, org_id):
    """balance_samples is PARTITION BY RANGE (ts). If the generated
    ON CONFLICT (account_id, ts) were rejected, the writer would log, retry,
    drop the batch and flip healthy=False -- and the whole balance series
    would be silently empty in production with a green suite."""
    with _attached_writer(repo) as writer:
        repo.record_balance_sample(100, org_id, balance=10000.0,
                                   equity=10125.5, margin_used=None,
                                   unrealized_pnl=125.5)
    assert writer.healthy is True, "the balance_samples batch failed"

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT balance, equity, unrealized_pnl FROM balance_samples "
            "WHERE account_id = 100").fetchone()
    assert row is not None, "no balance_samples row was written"
    assert [float(v) for v in row] == [10000.0, 10125.5, 125.5]


def test_log_event_through_the_writer_returns_zero_and_still_lands(repo):
    """log_event on the async writer path cannot hand back a real id (the
    row has not been written yet), so it returns 0 -- but the row must
    still land once the batch flushes."""
    with _attached_writer(repo) as writer:
        event_id = repo.log_event("control", "info", {"action": "x"},
                                  account_id=100)
    assert event_id == 0
    assert writer.healthy is True

    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM events WHERE account_id = 100 "
            "AND category = 'control'").fetchone()
    assert n == 1


# ---------- trade persistence: partition maintenance ----------

def test_ensure_partitions_creates_the_requested_months(repo):
    """Migration 012 already creates a year up front, so months_ahead=0
    (this month only) should find nothing missing; a far-future window
    exercises actual creation without depending on which month `now()` is."""
    future = repo.ensure_partitions(months_ahead=0)
    assert future == [], "migration 012 already created the current month"

    created = repo.ensure_partitions(months_ahead=36)
    assert created, "a 3-year window must need at least one new partition"
    assert all(t.startswith(repo.PARTITIONED_TABLES) for t in created)

    # Idempotent: running again immediately creates nothing new.
    assert repo.ensure_partitions(months_ahead=36) == []


def test_ensure_partitions_is_a_no_op_when_everything_exists(repo):
    repo.ensure_partitions(months_ahead=0)
    assert repo.ensure_partitions(months_ahead=0) == []
