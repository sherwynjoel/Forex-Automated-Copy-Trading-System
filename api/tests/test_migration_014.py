"""Migration 014: the MT5 bridge schema (contract section 1). conftest builds
the scratch database by applying EVERY migration, so these assert the
post-migration shape rather than the migration's own SQL."""
import psycopg
import pytest


def test_mt5_tables_and_the_synthetic_id_sequence_exist(db):
    with psycopg.connect(db, autocommit=True) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'")}
        assert {"mt5_links", "mt5_commands", "symbol_aliases",
                "mt5_deal_watermark"} <= tables
        (first,) = conn.execute("SELECT nextval('mt5_account_id_seq')").fetchone()
        assert first >= 1_000_000_000_000


def test_an_mt5_account_has_no_grant_and_a_ctrader_account_must(db, make_user, make_org):
    """The platform decides whether ctid_connection_id may be NULL: an MT5
    account never has a cTrader grant; a cTrader account always does."""
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (account_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', false) "
            "RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        (platform,) = conn.execute(
            "SELECT platform FROM accounts WHERE ctid_trader_account_id = %s",
            (account_id,)).fetchone()
        assert platform == "mt5"

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                "trader_login, is_live) VALUES (77, NULL, %s, 77, false)", (org_id,))

        # The link row goes with the account.
        conn.execute("INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, 'h')",
                     (account_id,))
        conn.execute("DELETE FROM accounts WHERE ctid_trader_account_id = %s", (account_id,))
        (links,) = conn.execute("SELECT count(*) FROM mt5_links").fetchone()
        assert links == 0
