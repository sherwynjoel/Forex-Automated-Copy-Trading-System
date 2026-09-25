# api/tests/test_migration_019.py
"""Migration 019: the investor portal -- investor role, the account->investor
link, the workspace wallet card, deposit notices and withdrawal requests.
conftest applies EVERY migration, so these assert the post-migration shape."""
import psycopg
import pytest


def test_migration_019_is_recorded_right_after_018(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "019_investor_portal.sql" in names
    assert names.index("019_investor_portal.sql") == names.index("018_risk_engine.sql") + 1


def test_investor_is_a_valid_membership_and_invite_role(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (role,) = conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
            (org_id, investor["id"])).fetchone()
        assert role == "investor"
        conn.execute(
            "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
            "VALUES (%s, 'investor', 'h019', %s, now() + interval '1 day')",
            (org_id, owner["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'guest')",
                (org_id, owner["id"]))


def test_one_account_per_investor_per_org(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (cid,) = conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, "
            "granted_at, expires_at) VALUES (%s, 'e', 'e', now(), now() + interval '1 day') "
            "RETURNING id", (org_id,)).fetchone()
        for aid in (901, 902):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                "trader_login, is_live, role, investor_user_id) "
                "VALUES (%s, %s, %s, %s, false, 'slave', %s)",
                (aid, cid, org_id, aid, investor["id"] if aid == 901 else None))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "UPDATE accounts SET investor_user_id = %s WHERE ctid_trader_account_id = 902",
                (investor["id"],))


def test_amounts_must_be_positive_and_statuses_are_checked(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                "VALUES (%s, %s, 0, 'USDT', 'tx')", (org_id, investor["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid, status) "
                "VALUES (%s, %s, 10, 'USDT', 'tx', 'done')", (org_id, investor["id"]))
        (status,) = conn.execute(
            "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
            "VALUES (%s, %s, 10.50, 'USDT', 'tx') RETURNING status",
            (org_id, investor["id"])).fetchone()
        assert status == "pending"


def test_one_live_notice_per_transaction_id(db, make_user, make_org):
    """R16: the same chain transaction cannot sit in the queue twice --
    confirming both would count the same money twice. Rejected rows are out
    of the index, so a mistake can be re-filed."""
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (indexdef,) = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = "
            "'investor_deposits_one_live_txid'").fetchone()
        assert "UNIQUE" in indexdef and "org_id" in indexdef and "txid" in indexdef
        assert "rejected" in indexdef

        conn.execute("INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                     "VALUES (%s, %s, 10, 'USDT', 'same-tx')", (org_id, investor["id"]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                         "VALUES (%s, %s, 20, 'USDT', 'same-tx')", (org_id, investor["id"]))
        # A rejected row leaves the index, so the txid is free again.
        conn.execute("UPDATE investor_deposits SET status = 'rejected' WHERE txid = 'same-tx'")
        conn.execute("INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                     "VALUES (%s, %s, 20, 'USDT', 'same-tx')", (org_id, investor["id"]))
        # ...and the same txid in ANOTHER workspace was never in the way.
        other = make_org(name="Other", members=[(owner, "admin")])
        conn.execute("INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                     "VALUES (%s, %s, 30, 'USDT', 'same-tx')", (other, owner["id"]))


def test_wallet_card_is_one_row_per_org(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "admin")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
            "VALUES (%s, 'USDT', 'TRC20', 'TAddr1')", (org_id,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
                "VALUES (%s, 'USDT', 'TRC20', 'TAddr2')", (org_id,))
