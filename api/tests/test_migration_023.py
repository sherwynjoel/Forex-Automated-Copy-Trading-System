# api/tests/test_migration_023.py
"""Migration 023: the client portal's identity tables -- KYC profiles,
account packages, account requests and the sign-in history. conftest
applies every migration, so these tests assert the post-migration shape and
the rules the database itself enforces."""
import psycopg
import pytest

from portal_helpers import add_package, seed_file

COLUMNS = {
    "kyc_profiles": [
        "org_id", "user_id", "full_name", "gender", "date_of_birth", "phone", "address_line",
        "area", "landmark", "city", "state", "postal_code", "country_residence",
        "country_citizenship", "id_type", "id_number", "id_front_file_id", "id_back_file_id",
        "address_proof_file_id", "photo_file_id", "status", "submitted_at", "decided_by",
        "decided_at", "decision_note", "updated_at"],
    "account_packages": [
        "id", "org_id", "name", "min_deposit", "currency", "spread_label", "leverage_options",
        "enabled", "sort_order", "created_at", "updated_at"],
    "account_requests": [
        "id", "org_id", "user_id", "package_id", "package_name", "leverage", "main_password_enc",
        "investor_password_enc", "status", "mt5_login", "mt5_server", "account_id", "decided_by",
        "decided_at", "decision_note", "created_at"],
    "login_events": ["id", "user_id", "ip", "user_agent", "outcome", "created_at"],
}
INDEXES = ["kyc_profiles_queue", "account_packages_by_org", "account_requests_queue",
           "account_requests_one_open", "login_events_by_user"]


def _people(make_user, make_org):
    admin = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(admin, "admin"), (investor, "investor")])
    return org_id, admin, investor


def _request(conn, org_id, user_id, package_id, **cols):
    names = ["org_id", "user_id", "package_id", "package_name", "leverage", *cols]
    values = [org_id, user_id, package_id, "Standard", 100, *cols.values()]
    (req_id,) = conn.execute(
        f"INSERT INTO account_requests ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(values))}) RETURNING id", values).fetchone()
    return req_id


def test_migration_023_is_recorded_right_after_022(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "023_portal_identity.sql" in names
    assert names.index("023_portal_identity.sql") == names.index("022_client_wallets.sql") + 1


@pytest.mark.parametrize("table", list(COLUMNS))
def test_each_table_has_exactly_the_spec_columns_in_order(db, table):
    with psycopg.connect(db, autocommit=True) as conn:
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,)).fetchall()]
    assert cols == COLUMNS[table]


def test_the_named_indexes_exist(db):
    with psycopg.connect(db, autocommit=True) as conn:
        defs = dict(conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(%s)",
            (INDEXES,)).fetchall())
    assert sorted(defs) == sorted(INDEXES)
    one_open = defs["account_requests_one_open"]
    assert "UNIQUE" in one_open and "(org_id, user_id)" in one_open and "'requested'" in one_open
    assert "(org_id, status, submitted_at)" in defs["kyc_profiles_queue"]
    assert "(org_id, status, created_at)" in defs["account_requests_queue"]
    assert "(org_id, sort_order, id)" in defs["account_packages_by_org"]
    assert "(user_id, created_at DESC)" in defs["login_events_by_user"]


def test_a_profile_starts_as_a_draft_and_checks_its_choices(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO kyc_profiles (org_id, user_id) VALUES (%s, %s)",
                     (org_id, investor["id"]))
        (status,) = conn.execute("SELECT status FROM kyc_profiles WHERE user_id = %s",
                                 (investor["id"],)).fetchone()
        assert status == "draft"
        for column, bad in (("gender", "unknown"), ("id_type", "visa"), ("status", "pending"),
                            ("country_residence", "india"), ("country_citizenship", "in")):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(f"UPDATE kyc_profiles SET {column} = %s WHERE user_id = %s",
                             (bad, investor["id"]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO kyc_profiles (org_id, user_id) VALUES (%s, %s)",
                         (org_id, investor["id"]))


def test_a_deleted_file_empties_its_kyc_slot(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    file_id = seed_file(db, org_id, investor["id"], purpose="kyc_photo")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO kyc_profiles (org_id, user_id, photo_file_id) VALUES (%s, %s, %s)",
                     (org_id, investor["id"], file_id))
        conn.execute("DELETE FROM files WHERE id = %s", (file_id,))
        (photo,) = conn.execute("SELECT photo_file_id FROM kyc_profiles WHERE user_id = %s",
                                (investor["id"],)).fetchone()
    assert photo is None


def test_one_open_account_request_per_investor(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        first = _request(conn, org_id, investor["id"], package_id)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _request(conn, org_id, investor["id"], package_id)
        conn.execute("UPDATE account_requests SET status = 'cancelled' WHERE id = %s", (first,))
        _request(conn, org_id, investor["id"], package_id)


def test_passwords_live_only_while_a_request_is_open(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        req_id = _request(conn, org_id, investor["id"], package_id,
                          main_password_enc="sealed-main", investor_password_enc="sealed-inv")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE account_requests SET status = 'rejected' WHERE id = %s", (req_id,))
        conn.execute("UPDATE account_requests SET status = 'rejected', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (req_id,))


def test_a_fulfilled_request_carries_its_login_and_server(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        req_id = _request(conn, org_id, investor["id"], package_id)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE account_requests SET status = 'fulfilled' WHERE id = %s", (req_id,))
        conn.execute("UPDATE account_requests SET status = 'fulfilled', mt5_login = 5001, "
                     "mt5_server = 'Broker-Live' WHERE id = %s", (req_id,))


def test_packages_need_a_leverage_and_a_non_negative_minimum(db, make_user, make_org):
    org_id, _, investor = _people(make_user, make_org)
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO account_packages (org_id, name, leverage_options) "
                         "VALUES (%s, 'Empty', '{}')", (org_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO account_packages (org_id, name, min_deposit, leverage_options) "
                         "VALUES (%s, 'Negative', -1, '{100}')", (org_id,))
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        req_id = _request(conn, org_id, investor["id"], package_id)
        conn.execute("UPDATE account_requests SET status = 'cancelled' WHERE id = %s", (req_id,))
        conn.execute("DELETE FROM account_packages WHERE id = %s", (package_id,))
        row = conn.execute("SELECT package_id, package_name FROM account_requests WHERE id = %s",
                           (req_id,)).fetchone()
    assert row == (None, "Standard")


def test_login_events_check_the_outcome(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO login_events (user_id, ip, user_agent, outcome) "
                     "VALUES (%s, '10.0.0.1', 'ua', 'password_ok')", (user["id"],))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO login_events (user_id, ip, outcome) "
                         "VALUES (%s, '10.0.0.1', 'locked')", (user["id"],))
