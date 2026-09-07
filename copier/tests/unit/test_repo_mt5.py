"""Repo additions for the MT5 bridge (copier/src/copier/db/repo.py): links,
the command queue, symbol aliases, the deal watermark and MT5 deals."""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from copier.db.repo import Repo


@pytest.fixture
def world(db, seed_mt5_account):
    """One org with a cTrader master (100) and one MT5 slave -> (repo, org_id, mt5_id)."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
        conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
            " granted_at, expires_at) VALUES (%s, 'x', 'y', now(), now() + interval '30 days')",
            (org_id,))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role) VALUES (100, %s, 1, 111, false, 'master')",
            (org_id,))
    mt5_id = seed_mt5_account(org_id)
    return Repo(db), org_id, mt5_id


def test_load_accounts_reports_the_platform(world):
    repo, _org_id, mt5_id = world
    by_id = {a.account_id: a for a in repo.load_accounts()}
    assert by_id[100].platform == "ctrader" and by_id[100].connection_id == 1
    assert by_id[mt5_id].platform == "mt5" and by_id[mt5_id].connection_id is None
    assert mt5_id >= 1_000_000_000_000


def test_hello_updates_the_link_and_copies_the_login_onto_the_account(world):
    repo, _org_id, mt5_id = world
    repo.upsert_mt5_link_hello(
        mt5_id, login=12345678, broker="XYZ Ltd", server="XYZ-Live3", currency="USD",
        hedging=True, trade_mode="real", leverage=500, ea_version="1.0.0", ea_build=4400)
    link = repo.load_mt5_link(mt5_id)
    assert (link["login"], link["broker"], link["server"], link["currency"],
            link["hedging"]) == (12345678, "XYZ Ltd", "XYZ-Live3", "USD", True)
    assert (link["trade_mode"], link["leverage"], link["ea_version"],
            link["ea_build"]) == ("real", 500, "1.0.0", 4400)
    assert link["last_seen_at"] is None and "key_hash" not in link
    account = next(a for a in repo.load_accounts() if a.account_id == mt5_id)
    assert account.trader_login == 12345678


def test_load_mt5_link_is_none_for_a_ctrader_account(world):
    repo, _org_id, _mt5_id = world
    assert repo.load_mt5_link(100) is None


def test_touch_records_balance_equity_and_last_seen(world):
    repo, _org_id, mt5_id = world
    seen = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
    repo.touch_mt5_link(mt5_id, balance=9784.04, equity=9790.10, seen_at=seen)
    link = repo.load_mt5_link(mt5_id)
    assert (link["balance"], link["equity"], link["last_seen_at"]) == (9784.04, 9790.10, seen)


class TestCommandQueue:
    def test_enqueue_then_open_in_id_order(self, world):
        repo, org_id, mt5_id = world
        first = repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 0.01, "sl": 0, "tp": 0,
             "comment": "copy:m1"}, f"cm1.{mt5_id}")
        second = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        rows = repo.mt5_commands_open(mt5_id)
        assert [r["id"] for r in rows] == [first, second]
        assert rows[0]["status"] == "queued" and rows[0]["attempts"] == 0
        assert rows[0]["sent_at"] is None and rows[0]["created_at"] is not None
        assert rows[0]["payload"]["symbol"] == "EURUSD.r"
        assert rows[0]["client_order_id"] == f"cm1.{mt5_id}" and rows[0]["org_id"] == org_id
        assert rows[1]["kind"] == "close" and rows[1]["client_order_id"] is None
        assert repo.mt5_commands_open(100) == []

    def test_mark_sent_counts_attempts_and_keeps_the_row_open(self, world):
        repo, org_id, mt5_id = world
        cid = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        sent = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        repo.mark_mt5_commands_sent([cid], sent)
        repo.mark_mt5_commands_sent([cid], sent + timedelta(seconds=10))
        (row,) = repo.mt5_commands_open(mt5_id)
        assert (row["status"], row["attempts"], row["sent_at"]) == (
            "sent", 2, sent + timedelta(seconds=10))
        repo.mark_mt5_commands_sent([], sent)   # nothing to do, no error

    def test_complete_returns_the_row_once_and_settles_it(self, world):
        repo, org_id, mt5_id = world
        cid = repo.enqueue_mt5_command(
            mt5_id, org_id, "open", {"symbol": "EURUSD.r"}, f"cm1.{mt5_id}")
        done = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        result = {"ok": True, "retcode": 10009, "message": "done", "position": 7, "deal": 8,
                  "order": 9, "price": 1.1, "lots": 0.01}
        row = repo.complete_mt5_command(cid, True, result, done, account_id=mt5_id)
        assert (row["id"], row["kind"], row["client_order_id"], row["account_id"],
                row["org_id"]) == (cid, "open", f"cm1.{mt5_id}", mt5_id, org_id)
        assert row["payload"] == {"symbol": "EURUSD.r"}
        # A duplicate ack changes nothing and reports nothing.
        assert repo.complete_mt5_command(cid, True, result, done, account_id=mt5_id) is None
        assert repo.mt5_commands_open(mt5_id) == []
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            status, stored, done_at = conn.execute(
                "SELECT status, result, done_at FROM mt5_commands WHERE id = %s", (cid,)
            ).fetchone()
        assert (status, stored, done_at) == ("done", result, done)

    def test_a_failed_ack_marks_the_row_failed(self, world):
        repo, org_id, mt5_id = world
        cid = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        row = repo.complete_mt5_command(
            cid, False, {"ok": False, "retcode": 10036, "message": "position closed"},
            datetime.now(timezone.utc))
        assert row["id"] == cid
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (status,) = conn.execute(
                "SELECT status FROM mt5_commands WHERE id = %s", (cid,)).fetchone()
        assert status == "failed"

    def test_complete_ignores_another_accounts_command_and_unknown_ids(self, world, seed_mt5_account):
        repo, org_id, mt5_id = world
        other = seed_mt5_account(org_id)
        cid = repo.enqueue_mt5_command(mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        assert repo.complete_mt5_command(
            cid, True, {"ok": True}, datetime.now(timezone.utc), account_id=other) is None
        assert [r["id"] for r in repo.mt5_commands_open(mt5_id)] == [cid]
        assert repo.complete_mt5_command(
            999_999, True, {"ok": True}, datetime.now(timezone.utc)) is None

    def test_fail_stale_opens_expires_only_old_opens_and_fails_their_mappings(self, world):
        repo, org_id, mt5_id = world
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        repo.create_order_mapping(43, mt5_id, f"co43.{mt5_id}", org_id=org_id)
        old_open = repo.enqueue_mt5_command(
            mt5_id, org_id, "open", {"symbol": "EURUSD.r"}, f"cm42.{mt5_id}")
        old_pending = repo.enqueue_mt5_command(
            mt5_id, org_id, "place_pending", {"symbol": "EURUSD.r"}, f"co43.{mt5_id}")
        old_close = repo.enqueue_mt5_command(
            mt5_id, org_id, "close", {"position": 5, "lots": 0}, None)
        # A netting follower's close is an opposite-side open named after its
        # mapping with the ':close' suffix (Task 7): a close, so it never expires.
        old_netting_close = repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "SELL", "lots": 1.0, "sl": 0, "tp": 0,
             "comment": "close:m42"}, f"cm42.{mt5_id}:close")
        fresh_open = repo.enqueue_mt5_command(
            mt5_id, org_id, "open", {"symbol": "EURUSD.r"}, f"cm44.{mt5_id}")
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE mt5_commands SET created_at = now() - interval '60 seconds'"
                " WHERE id = ANY(%s)",
                ([old_open, old_pending, old_close, old_netting_close],))

        older_than = datetime.now(timezone.utc) - timedelta(seconds=30)
        expired = repo.fail_stale_mt5_opens(mt5_id, older_than)

        assert sorted(expired) == sorted([old_open, old_pending])
        assert [r["id"] for r in repo.mt5_commands_open(mt5_id)] == [
            old_close, old_netting_close, fresh_open]
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            (message,) = conn.execute(
                "SELECT result->>'message' FROM mt5_commands WHERE id = %s", (old_open,)
            ).fetchone()
        assert message == "terminal offline"
        by_coid = {m["client_order_id"]: m for m in repo.mapping_rows(org_id=org_id)}
        assert by_coid[f"cm42.{mt5_id}"]["status"] == "failed"
        assert by_coid[f"cm42.{mt5_id}"]["error"] == "terminal offline"
        assert by_coid[f"co43.{mt5_id}"]["status"] == "failed"
        assert repo.fail_stale_mt5_opens(mt5_id, older_than) == []


class TestSymbolAliases:
    def test_roundtrip_and_manual_beats_auto(self, world):
        repo, _org_id, mt5_id = world
        assert repo.load_symbol_aliases(mt5_id) == {}
        repo.save_symbol_aliases(mt5_id, {"EURUSD": "EURUSD.r", "XAUUSD": "GOLD.r"}, "auto")
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSD.r", "XAUUSD": "GOLD.r"}
        repo.save_symbol_aliases(mt5_id, {"XAUUSD": "XAUUSD.pro"}, "manual")
        # An auto pass never overwrites what the operator set by hand.
        repo.save_symbol_aliases(mt5_id, {"XAUUSD": "GOLDm", "EURUSD": "EURUSDm"}, "auto")
        assert repo.load_symbol_aliases(mt5_id) == {"EURUSD": "EURUSDm", "XAUUSD": "XAUUSD.pro"}
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            sources = dict(conn.execute(
                "SELECT canonical, source FROM symbol_aliases WHERE account_id = %s",
                (mt5_id,)).fetchall())
        assert sources == {"EURUSD": "auto", "XAUUSD": "manual"}

    def test_an_empty_broker_name_removes_the_alias(self, world):
        repo, _org_id, mt5_id = world
        repo.save_symbol_aliases(mt5_id, {"EURUSD": "EURUSD.r"}, "manual")
        repo.save_symbol_aliases(mt5_id, {"EURUSD": ""}, "manual")
        assert repo.load_symbol_aliases(mt5_id) == {}


class TestWatermark:
    def test_defaults_to_zero_and_only_moves_forward(self, world):
        repo, _org_id, mt5_id = world
        assert repo.mt5_watermark(mt5_id) == (0, 0)
        repo.set_mt5_watermark(mt5_id, 700005, 1_757_203_100_456)
        repo.set_mt5_watermark(mt5_id, 700003, 1_757_203_000_000)   # an older batch cannot rewind it
        assert repo.mt5_watermark(mt5_id) == (700005, 1_757_203_100_456)


def _deal(deal_id, ts, close=None, side="BUY", **extra):
    row = {"deal_id": deal_id, "order_id": deal_id + 1, "position_id": 9, "symbol_id": 77,
           "symbol": "EURUSD.r", "side": side, "volume": 100, "filled_volume": 100,
           "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
           "create_timestamp": ts, "execution_timestamp": ts, "close": close}
    row.update(extra)
    return row


class TestMt5Deals:
    def test_upsert_counts_new_rows_only(self, world):
        repo, org_id, mt5_id = world
        rows = [
            _deal(1, 1000, balance_after=9990.0),
            _deal(2, 2000, close={"entry_price": 1.1, "gross_profit": 5.0, "swap": 0.0,
                                  "commission": -0.03, "balance": 10000.0, "closed_volume": 100}),
        ]
        assert repo.upsert_mt5_deals(mt5_id, org_id, rows) == 2
        assert repo.upsert_mt5_deals(mt5_id, org_id, rows) == 0
        assert repo.upsert_mt5_deals(mt5_id, org_id, rows + [_deal(3, 3000)]) == 1
        closes = [d for d in repo.load_deals(mt5_id) if d["close"]]
        assert len(closes) == 1 and closes[0]["close"]["balance"] == 10000.0

    def test_estimated_balance_after_is_stored_for_deals_that_are_not_closes(self, world):
        repo, org_id, mt5_id = world
        repo.upsert_mt5_deals(mt5_id, org_id, [_deal(1, 1000, balance_after=9990.0)])
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT balance_after, is_close, gross_profit FROM deals"
                " WHERE account_id = %s AND deal_id = 1", (mt5_id,)).fetchone()
        assert (float(row[0]), row[1], row[2]) == (9990.0, False, None)

    def test_load_deals_filters_by_until_and_position(self, world):
        repo, org_id, mt5_id = world
        repo.upsert_mt5_deals(
            mt5_id, org_id, [_deal(1, 1000), _deal(2, 2000, position_id=10), _deal(3, 3000)])
        assert [d["deal_id"] for d in repo.load_deals(mt5_id, since_ms=1000, until_ms=2000)] == [1, 2]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id, position_id=10)] == [2]
        assert [d["deal_id"] for d in repo.load_deals(mt5_id)] == [1, 2, 3]

    def test_cash_flow_reads_balance_operations_only(self, world):
        repo, org_id, mt5_id = world
        repo.upsert_mt5_deals(mt5_id, org_id, [
            _deal(1, 1000, side="BALANCE", volume=0, filled_volume=0, execution_price=None,
                  commission=None, gross_profit=500.0, balance_after=10500.0),
            _deal(2, 2000),
            _deal(3, 3000, side="CREDIT", volume=0, filled_volume=0, execution_price=None,
                  commission=None, gross_profit=-100.0, balance_after=10400.0),
        ])
        entries = repo.load_mt5_cash_flow(mt5_id, 0, 5000)
        assert entries == [
            {"deal_id": 1, "side": "BALANCE", "amount": 500.0, "balance_after": 10500.0,
             "timestamp": 1000},
            {"deal_id": 3, "side": "CREDIT", "amount": -100.0, "balance_after": 10400.0,
             "timestamp": 3000},
        ]
        assert repo.load_mt5_cash_flow(mt5_id, 1500, 5000) == entries[1:]


class TestNetting:
    """A netting follower's copies share one net position: several mapping
    rows with the same slave_position_id (ticket 9001 here)."""

    def _three_copies(self, repo, org_id, mt5_id):
        # Oldest first: 42 (100), 43 (50), 44 (30). created_at is what
        # "oldest" means, so it is made unambiguous rather than left to the
        # millisecond the rows happened to land in.
        for n, (master_id, volume) in enumerate(((42, 100), (43, 50), (44, 30))):
            coid = f"cm{master_id}.{mt5_id}"
            repo.create_position_mapping(master_id, mt5_id, coid, org_id=org_id)
            repo.activate_position_mapping(mt5_id, coid, 9001, volume)
            with psycopg.connect(repo.dsn, autocommit=True) as conn:
                conn.execute(
                    "UPDATE mappings SET created_at = now() - interval '10 seconds'"
                    " + %s * interval '1 second' WHERE client_order_id = %s",
                    (n, coid))

    def _by_coid(self, repo, org_id):
        return {m["client_order_id"]: (m["status"], m["slave_volume"])
                for m in repo.mapping_rows(org_id=org_id)}

    def test_mapping_side_reads_the_open_commands_payload(self, world):
        repo, org_id, mt5_id = world
        repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "SELL", "lots": 0.5, "sl": 0, "tp": 0,
             "comment": "copy:m42"}, f"cm42.{mt5_id}")
        # The mapping's later close is an opposite-side open under the
        # ':close' suffix; it never answers for the mapping's own side.
        repo.enqueue_mt5_command(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 0.5, "sl": 0, "tp": 0,
             "comment": "close:m42"}, f"cm42.{mt5_id}:close")
        assert repo.mapping_side(f"cm42.{mt5_id}") == "SELL"
        assert repo.mt5_open_payload(f"cm42.{mt5_id}")["symbol"] == "EURUSD.r"
        assert repo.mapping_side(f"cm43.{mt5_id}") is None
        assert repo.mt5_open_payload(f"cm43.{mt5_id}") is None

    def test_fifo_reduction_consumes_the_oldest_mapping_first(self, world):
        repo, org_id, mt5_id = world
        self._three_copies(repo, org_id, mt5_id)
        touched = repo.reduce_position_mappings_fifo(mt5_id, 9001, 120)
        assert [(t["client_order_id"], t["master_position_id"], t["closed_volume"],
                 t["slave_volume"], t["status"]) for t in touched] == [
            (f"cm42.{mt5_id}", 42, 100, 0, "closed"), (f"cm43.{mt5_id}", 43, 20, 30, "active")]
        assert self._by_coid(repo, org_id) == {
            f"cm42.{mt5_id}": ("closed", 0), f"cm43.{mt5_id}": ("active", 30),
            f"cm44.{mt5_id}": ("active", 30)}

    def test_fifo_reduction_closes_everything_and_then_finds_nothing(self, world):
        repo, org_id, mt5_id = world
        self._three_copies(repo, org_id, mt5_id)
        touched = repo.reduce_position_mappings_fifo(mt5_id, 9001, 500)   # more than the copies hold
        assert [t["closed_volume"] for t in touched] == [100, 50, 30]
        assert {status for status, _v in self._by_coid(repo, org_id).values()} == {"closed"}
        assert repo.reduce_position_mappings_fifo(mt5_id, 9001, 10) == []
        assert repo.reduce_position_mappings_fifo(mt5_id, 9002, 10) == []
        assert repo.reduce_position_mappings_fifo(mt5_id, 9001, 0) == []

    def test_reduce_by_client_order_id_touches_only_that_mapping(self, world):
        repo, org_id, mt5_id = world
        self._three_copies(repo, org_id, mt5_id)
        repo.reduce_position_mapping(mt5_id, 9001, 20, client_order_id=f"cm43.{mt5_id}")
        assert self._by_coid(repo, org_id)[f"cm43.{mt5_id}"] == ("active", 30)
        # The coid alone names the mapping: a netting close that EMPTIES the
        # net position is acked with ticket 0 (plan 04 DoOpen reports the
        # position the terminal shows after the fill -- none), and the
        # ticket the ack carries must not decide whether the reduction lands.
        repo.reduce_position_mapping(mt5_id, 0, 30, client_order_id=f"cm43.{mt5_id}")
        assert self._by_coid(repo, org_id) == {
            f"cm42.{mt5_id}": ("active", 100), f"cm43.{mt5_id}": ("closed", 0),
            f"cm44.{mt5_id}": ("active", 30)}
        # Without the predicate the old behaviour stands: every active row on
        # the ticket is reduced (one mapping per position on a hedging account).
        repo.reduce_position_mapping(mt5_id, 9001, 30)
        assert self._by_coid(repo, org_id) == {
            f"cm42.{mt5_id}": ("active", 70), f"cm43.{mt5_id}": ("closed", 0),
            f"cm44.{mt5_id}": ("closed", 0)}


class TestNetLedger:
    """The netting master's virtual positions, persisted so a restart keeps them."""

    def _row(self, virtual_id, **extra):
        row = {"virtual_id": virtual_id, "symbol": "XAUUSD.r", "side": "BUY", "volume_open": 50,
               "volume_left": 50, "stop_loss": None, "take_profit": None,
               "opened_at_ms": 1_757_203_100_000 + virtual_id}
        row.update(extra)
        return row

    def test_roundtrip_in_open_order(self, world):
        repo, _org_id, mt5_id = world
        assert repo.load_net_ledger(mt5_id) == []
        repo.upsert_net_ledger(mt5_id, [self._row(700003), self._row(700001, stop_loss=2390.0)])
        assert repo.load_net_ledger(mt5_id) == [
            self._row(700001, stop_loss=2390.0), self._row(700003)]

    def test_upsert_updates_volume_and_protection_in_place(self, world):
        repo, _org_id, mt5_id = world
        repo.upsert_net_ledger(mt5_id, [self._row(700001)])
        repo.upsert_net_ledger(mt5_id, [self._row(700001, volume_left=20, take_profit=2420.0)])
        repo.upsert_net_ledger(mt5_id, [])                        # nothing to do, no error
        (row,) = repo.load_net_ledger(mt5_id)
        assert (row["volume_open"], row["volume_left"], row["take_profit"]) == (50, 20, 2420.0)

    def test_delete_removes_only_the_named_rows_of_that_account(self, world, seed_mt5_account):
        repo, org_id, mt5_id = world
        other = seed_mt5_account(org_id)
        repo.upsert_net_ledger(mt5_id, [self._row(700001), self._row(700002)])
        repo.upsert_net_ledger(other, [self._row(700001)])
        repo.delete_net_ledger(mt5_id, [700001, 424242])
        repo.delete_net_ledger(mt5_id, [])
        assert [r["virtual_id"] for r in repo.load_net_ledger(mt5_id)] == [700002]
        assert [r["virtual_id"] for r in repo.load_net_ledger(other)] == [700001]
