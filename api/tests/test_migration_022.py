# api/tests/test_migration_022.py
"""Migration 022: the client portal's money tables -- files, payment
methods, portal settings, payout destinations, the wallet ledger, deposits,
withdrawals and transfers -- and the copy-then-drop of the three
2026-09-23 tables. conftest applies EVERY migration, so the `db` tests
assert the post-migration shape; the copy is exercised on a scratch
database stopped at 021, like test_migration_020's upgrade test."""
import pathlib
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from conftest import ADMIN_DSN

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
UPGRADE_DB = "copytrader_mig022"
UPGRADE_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{UPGRADE_DB}"

AUDIT = ["decided_by", "decided_at", "decision_note"]
COLUMNS = {
    "files": ["id", "org_id", "user_id", "purpose", "content_type", "size_bytes", "sha256",
              "storage_key", "created_at"],
    "payment_methods": ["id", "org_id", "kind", "label", "enabled", "currency", "details",
                        "min_amount", "fee_pct", "instructions", "sort_order", "created_by",
                        "created_at", "updated_at"],
    "portal_settings": ["org_id", "withdrawal_min", "withdrawal_fee_pct", "updated_by",
                        "updated_at"],
    "payout_destinations": ["id", "org_id", "user_id", "kind", "nickname", "details",
                            "proof_file_id", "status", *AUDIT, "created_at"],
    "wallet_entries": ["id", "org_id", "user_id", "wallet", "amount", "kind", "ref_table",
                       "ref_id", "note", "created_by", "created_at"],
    "deposits": ["id", "org_id", "user_id", "method_id", "method_kind", "method_label", "amount",
                 "fee", "credited_amount", "reference", "receipt_file_id", "target",
                 "target_account_id", "note", "status", *AUDIT, "created_at"],
    "withdrawals": ["id", "org_id", "user_id", "destination_id", "destination_kind",
                    "destination_summary", "amount", "fee", "net_amount", "status", *AUDIT,
                    "paid_by", "paid_at", "txid", "created_at"],
    "transfers": ["id", "org_id", "user_id", "source_kind", "source_wallet", "source_account_id",
                  "target_kind", "target_wallet", "target_account_id", "amount", "status",
                  "equity_at_request", "equity_verified", *AUDIT, "done_by", "done_at", "note",
                  "created_at"],
}
INDEXES = ["files_by_user", "payment_methods_by_org", "payout_destinations_by_user",
           "payout_destinations_queue", "wallet_entries_by_user", "wallet_entries_by_org_time",
           "wallet_entries_one_per_ref", "deposits_queue", "deposits_by_user",
           "deposits_one_live_reference", "withdrawals_queue", "withdrawals_by_user",
           "transfers_queue", "transfers_by_user"]


def _people(make_user, make_org):
    admin = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(admin, "admin"), (investor, "investor")])
    return org_id, admin, investor


def test_migration_022_is_recorded_right_after_021(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "022_client_wallets.sql" in names
    assert names.index("022_client_wallets.sql") == names.index("021_mpin.sql") + 1


@pytest.mark.parametrize("table", list(COLUMNS))
def test_each_table_has_exactly_the_spec_columns_in_order(db, table):
    with psycopg.connect(db, autocommit=True) as conn:
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,)).fetchall()]
    assert cols == COLUMNS[table]


def test_the_three_2026_09_23_tables_are_gone(db):
    with psycopg.connect(db, autocommit=True) as conn:
        for old in ("investor_withdrawals", "investor_deposits", "org_investor_wallets"):
            (reg,) = conn.execute("SELECT to_regclass(%s)", (old,)).fetchone()
            assert reg is None, old


def test_the_named_indexes_exist_and_the_unique_ones_are_partial(db):
    with psycopg.connect(db, autocommit=True) as conn:
        defs = dict(conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(%s)",
            (INDEXES,)).fetchall())
    assert sorted(defs) == sorted(INDEXES)
    live = defs["deposits_one_live_reference"]
    assert "UNIQUE" in live and "(org_id, reference)" in live
    assert "pending" in live and "confirmed" in live
    once = defs["wallet_entries_one_per_ref"]
    assert "UNIQUE" in once and "(ref_table, ref_id, wallet)" in once and "IS NOT NULL" in once
    for queue in ("deposits_queue", "withdrawals_queue", "transfers_queue",
                  "payout_destinations_queue"):
        assert "(org_id, status, created_at)" in defs[queue], queue
    assert "(org_id, user_id, wallet, created_at DESC, id DESC)" in defs["wallet_entries_by_user"]
    assert "(org_id, sort_order, id)" in defs["payment_methods_by_org"]
    assert "(org_id, user_id, created_at DESC)" in defs["files_by_user"]


def test_wallet_entries_refuse_zero_and_settle_once_per_reference(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    entry = ("INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id) "
             "VALUES (%s, %s, %s, %s, %s, %s, %s)")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(entry, (org_id, investor["id"], "main", 100, "deposit", "deposits", 1))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(entry, (org_id, investor["id"], "main", 100, "deposit", "deposits", 1))
        # The settlement idiom every route uses: a replay is a no-op, not an error.
        conn.execute(entry + " ON CONFLICT (ref_table, ref_id, wallet) WHERE ref_table IS NOT NULL "
                     "DO NOTHING", (org_id, investor["id"], "main", 100, "deposit", "deposits", 1))
        # The two legs of a wallet->wallet transfer are different wallets, so both fit.
        conn.execute(entry, (org_id, investor["id"], "pamm", -100, "transfer", "transfers", 1))
        conn.execute(entry, (org_id, investor["id"], "main", 100, "transfer", "transfers", 1))
        # Adjustments carry no reference and may repeat.
        conn.execute(entry, (org_id, investor["id"], "main", 5, "adjustment", None, None))
        conn.execute(entry, (org_id, investor["id"], "main", 5, "adjustment", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(entry, (org_id, investor["id"], "main", 0, "adjustment", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(entry, (org_id, investor["id"], "savings", 1, "adjustment", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(entry, (org_id, investor["id"], "main", 1, "refund", None, None))
        (total,) = conn.execute(
            "SELECT sum(amount) FROM wallet_entries WHERE wallet = 'main'").fetchone()
    assert total == Decimal("210.00")


def test_deposits_keep_one_live_reference_per_org(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    dep = ("INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference) "
           "VALUES (%s, %s, 'crypto', 'USDT on TRC20', %s, %s) RETURNING id, status, target, fee")
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(dep, (org_id, investor["id"], 10, "same-tx")).fetchone()
        assert row[1:] == ("pending", "wallet", Decimal("0"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(dep, (org_id, investor["id"], 20, "same-tx"))
        # A cancelled (or rejected) row leaves the index, so the reference is free again.
        conn.execute("UPDATE deposits SET status = 'cancelled' WHERE id = %s", (row[0],))
        conn.execute(dep, (org_id, investor["id"], 20, "same-tx"))
        # ...and the same reference in ANOTHER workspace was never in the way.
        other = make_org(name="Other", members=[(admin, "admin")])
        conn.execute(dep, (other, admin["id"], 30, "same-tx"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(dep, (org_id, investor["id"], 0, "zero"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, "
                "reference, status) VALUES (%s, %s, 'crypto', 'x', 1, 'st', 'done')",
                (org_id, investor["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, "
                "reference, target) VALUES (%s, %s, 'crypto', 'x', 1, 'tg', 'bank')",
                (org_id, investor["id"]))


def test_transfers_check_both_ends(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    tr = ("INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, source_account_id, "
          "target_kind, target_wallet, target_account_id, amount) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING status, equity_verified")
    with psycopg.connect(db, autocommit=True) as conn:
        (aid,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, platform, "
            "trader_login, is_live, role, enabled) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', true) "
            "RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        row = conn.execute(tr, (org_id, investor["id"], "wallet", "main", None,
                                "account", None, aid, 50)).fetchone()
        assert row == ("requested", False)
        conn.execute(tr, (org_id, investor["id"], "account", None, aid, "wallet", "main", None, 50))
        conn.execute(tr, (org_id, investor["id"], "wallet", "pamm", None, "wallet", "main", None, 50))
        for bad in [
            ("account", None, aid, "account", None, aid),       # account -> account
            ("wallet", None, None, "wallet", "main", None),     # wallet kind without a wallet
            ("wallet", "main", aid, "wallet", "pamm", None),    # wallet kind carrying an account
            ("account", None, None, "wallet", "main", None),    # account kind without an account
            ("wallet", "savings", None, "wallet", "main", None),
        ]:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(tr, (org_id, investor["id"], *bad, 50))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(tr, (org_id, investor["id"], "wallet", "main", None,
                              "wallet", "pamm", None, 0))


def test_a_destination_with_history_cannot_be_deleted(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    with psycopg.connect(db, autocommit=True) as conn:
        (dest,) = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, status) "
            "VALUES (%s, %s, 'crypto', 'Main', %s, 'approved') RETURNING id",
            (org_id, investor["id"],
             Jsonb({"coin": "USDT", "network": "TRC20", "address": "TDest0987654321"}))).fetchone()
        wd = ("INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
              "destination_summary, amount, fee, net_amount) "
              "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…21', 100, 1.5, 98.5) RETURNING status")
        (status,) = conn.execute(wd, (org_id, investor["id"], dest)).fetchone()
        assert status == "requested"
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute("DELETE FROM payout_destinations WHERE id = %s", (dest,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE withdrawals SET status = 'done'")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE payout_destinations SET status = 'deleted' WHERE id = %s", (dest,))


def test_files_methods_and_settings_constraints(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    f = ("INSERT INTO files (org_id, user_id, purpose, content_type, size_bytes, sha256, storage_key) "
         "VALUES (%s, %s, %s, 'image/png', %s, 'abc', %s)")
    pm = ("INSERT INTO payment_methods (org_id, kind, label, details, fee_pct) "
          "VALUES (%s, %s, 'x', '{}', %s) RETURNING enabled, currency, min_amount, sort_order")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(f, (org_id, investor["id"], "deposit_receipt", 10, f"{org_id}/1.png"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(f, (org_id, investor["id"], "payout_proof", 10, f"{org_id}/1.png"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(f, (org_id, investor["id"], "deposit_receipt", 0, f"{org_id}/2.png"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(f, (org_id, investor["id"], "selfie", 10, f"{org_id}/3.png"))
        assert conn.execute(pm, (org_id, "bank", 0)).fetchone() == (True, "USD", Decimal("0"), 0)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(pm, (org_id, "crypto", 100))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(pm, (org_id, "cash", 0))
        conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s)", (org_id,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s)", (org_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE portal_settings SET withdrawal_fee_pct = 100 WHERE org_id = %s",
                         (org_id,))


def test_upgrade_copies_the_three_old_tables_then_drops_them(database):
    """A developer database has a wallet card, a confirmed and a pending
    notice, and a paid withdrawal when 022 arrives. Each becomes a row of
    the new shape, the settled ones get their ledger entries, the ids are
    kept so the sequences continue, and the old tables are gone."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {UPGRADE_DB}")
    try:
        with psycopg.connect(UPGRADE_DSN) as conn:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name.startswith("022_"):
                    continue
                conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES "
                "(1, 'admin@x.com', 'h', 'A'), (2, 'inv@x.com', 'h', 'I')")
            conn.execute("INSERT INTO orgs (id, name) VALUES (1, 'Desk')")
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                "platform, trader_login, is_live, role, enabled) "
                "VALUES (900, NULL, 1, 'mt5', 0, false, 'slave', true)")
            conn.execute(
                "INSERT INTO org_investor_wallets (org_id, coin, network, address, memo, updated_by) "
                "VALUES (1, 'USDT', 'TRC20', 'TAddr123', NULL, 1)")
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, account_id, amount, coin, txid, "
                "note, status, decided_by, decided_at, decision_note) VALUES "
                "(1, 2, 900, 250.00, 'USDT', 'tx-1', 'first', 'confirmed', 1, now(), 'seen on chain')")
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                "VALUES (1, 2, 75.00, 'USDT', 'tx-2')")
            conn.execute(
                "INSERT INTO investor_withdrawals (org_id, user_id, account_id, amount, destination, "
                "status, decided_by, decided_at, paid_by, paid_at, txid) VALUES "
                "(1, 2, 900, 40.00, 'TDest999', 'paid', 1, now(), 1, now(), 'out-1')")
            conn.execute((MIGRATIONS_DIR / "022_client_wallets.sql").read_text())
            conn.commit()

        with psycopg.connect(UPGRADE_DSN, autocommit=True) as conn:
            methods = conn.execute(
                "SELECT org_id, kind, label, enabled, currency, details, min_amount, fee_pct, "
                "created_by FROM payment_methods").fetchall()
            assert methods == [(1, "crypto", "USDT on TRC20", True, "USD",
                                {"coin": "USDT", "network": "TRC20", "address": "TAddr123"},
                                Decimal("0"), Decimal("0"), 1)]
            (method_id,) = conn.execute("SELECT id FROM payment_methods").fetchone()
            deposits = conn.execute(
                "SELECT id, user_id, method_id, method_kind, method_label, amount, fee, "
                "credited_amount, reference, target, target_account_id, note, status, decided_by, "
                "decision_note FROM deposits ORDER BY id").fetchall()
            assert deposits == [
                (1, 2, method_id, "crypto", "USDT", Decimal("250.00"), Decimal("0"),
                 Decimal("250.00"), "tx-1", "wallet", None, "first", "confirmed", 1, "seen on chain"),
                (2, 2, method_id, "crypto", "USDT", Decimal("75.00"), Decimal("0"),
                 None, "tx-2", "wallet", None, None, "pending", None, None)]
            dests = conn.execute(
                "SELECT id, org_id, user_id, kind, nickname, details, status "
                "FROM payout_destinations").fetchall()
            assert dests == [(1, 1, 2, "crypto", "Imported",
                              {"coin": "", "network": "", "address": "TDest999"}, "approved")]
            withdrawals = conn.execute(
                "SELECT id, user_id, destination_id, destination_kind, destination_summary, amount, "
                "fee, net_amount, status, decided_by, paid_by, txid FROM withdrawals").fetchall()
            assert withdrawals == [(1, 2, 1, "crypto", "TDest999", Decimal("40.00"), Decimal("0"),
                                    Decimal("40.00"), "paid", 1, 1, "out-1")]
            entries = conn.execute(
                "SELECT wallet, amount, kind, ref_table, ref_id, created_by "
                "FROM wallet_entries ORDER BY id").fetchall()
            assert entries == [("main", Decimal("250.00"), "deposit", "deposits", 1, 1),
                               ("main", Decimal("-40.00"), "withdrawal", "withdrawals", 1, 1)]
            for old in ("investor_withdrawals", "investor_deposits", "org_investor_wallets"):
                (reg,) = conn.execute("SELECT to_regclass(%s)", (old,)).fetchone()
                assert reg is None, old
            (next_dep,) = conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference) "
                "VALUES (1, 2, 'bank', 'ICICI', 5, 'tx-3') RETURNING id").fetchone()
            assert next_dep == 3
            (next_wd,) = conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount) "
                "VALUES (1, 2, 1, 'crypto', 'TDest999', 1, 0, 1) RETURNING id").fetchone()
            assert next_wd == 2
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
