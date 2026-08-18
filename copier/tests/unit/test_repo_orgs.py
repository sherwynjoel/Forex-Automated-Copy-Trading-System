"""Org-aware repo layer."""
import psycopg
import pytest

from copier.db.repo import Repo


@pytest.fixture
def seeded(db):
    """Two orgs, one connection each. Returns (repo, org_a, org_b, conn_a, conn_b)."""
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        org_a = conn.execute(
            "INSERT INTO orgs (name) VALUES ('A') RETURNING id").fetchone()[0]
        org_b = conn.execute(
            "INSERT INTO orgs (name, copying_enabled, dry_run) "
            "VALUES ('B', false, true) RETURNING id").fetchone()[0]
        conn_a = conn.execute(
            """INSERT INTO ctid_connections (org_id, access_token_enc,
                   refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
               RETURNING id""", (org_a,)).fetchone()[0]
        conn_b = conn.execute(
            """INSERT INTO ctid_connections (org_id, access_token_enc,
                   refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'x', 'x', now(), now() + interval '30 days')
               RETURNING id""", (org_b,)).fetchone()[0]
    return repo, org_a, org_b, conn_a, conn_b


def test_load_orgs_and_get_org(seeded):
    repo, org_a, org_b, *_ = seeded
    orgs = {o.org_id: o for o in repo.load_orgs()}
    assert orgs[org_a].copying_enabled is True and orgs[org_a].dry_run is False
    assert orgs[org_b].copying_enabled is False and orgs[org_b].dry_run is True
    assert repo.get_org(org_b).name == "B"


def test_set_org_setting_isolated(seeded):
    repo, org_a, org_b, *_ = seeded
    repo.set_org_setting(org_a, "dry_run", True)
    assert repo.get_org(org_a).dry_run is True
    assert repo.get_org(org_b).dry_run is True  # was already true; untouched
    repo.set_org_setting(org_b, "dry_run", False)
    assert repo.get_org(org_a).dry_run is True


def test_settings_is_shards_only(seeded):
    repo, *_ = seeded
    assert repo.get_settings().shards == 1
    with pytest.raises(ValueError):
        repo.set_setting("copying_enabled", False)


def test_upsert_account_respects_org_ownership(seeded):
    repo, org_a, org_b, conn_a, conn_b = seeded
    assert repo.upsert_account(100, conn_a, org_a, 100, False) is True
    # same org re-upsert: fine
    assert repo.upsert_account(100, conn_a, org_a, 100, False) is True
    # ANOTHER org discovering the same broker account: refused, row unchanged
    assert repo.upsert_account(100, conn_b, org_b, 100, False) is False
    rows = repo.load_accounts()
    assert len(rows) == 1 and rows[0].org_id == org_a


def test_connection_org(seeded):
    repo, org_a, org_b, conn_a, conn_b = seeded
    assert repo.connection_org(conn_a) == org_a
    assert repo.connection_org(conn_b) == org_b


def test_log_event_carries_org(seeded, db):
    repo, org_a, *_ = seeded
    repo.log_event("control", "info", {"x": 1}, org_id=org_a)
    repo.log_event("connection", "info", {"x": 2})
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT org_id, payload->>'x' FROM events ORDER BY id").fetchall()
    assert rows == [(org_a, "1"), (None, "2")]


def test_mapping_rows_org_filter(seeded):
    repo, org_a, org_b, conn_a, conn_b = seeded
    repo.upsert_account(100, conn_a, org_a, 100, False)
    repo.upsert_account(201, conn_b, org_b, 201, False)
    repo.create_position_mapping(1, 100, "cm1.100", org_id=org_a)
    repo.create_position_mapping(2, 201, "cm2.201", org_id=org_b)
    assert {m["org_id"] for m in repo.mapping_rows()} == {org_a, org_b}
    only_a = repo.mapping_rows(org_id=org_a)
    assert len(only_a) == 1 and only_a[0]["client_order_id"] == "cm1.100"
