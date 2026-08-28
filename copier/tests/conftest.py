import os
import pathlib
import sys

import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # makes the top-level `db` package importable

ADMIN_DSN = os.environ.get(
    "TEST_POSTGRES_ADMIN_DSN",
    "postgresql://copytrader:copytrader@localhost:5433/copytrader",
)
TEST_DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql://copytrader:copytrader@localhost:5433/copytrader_test",
)
TEST_DB_NAME = TEST_DSN.rsplit("/", 1)[1]


@pytest.fixture(scope="session")
def database():
    from db.migrate import apply_migrations

    # The scratch database NAME comes from TEST_POSTGRES_DSN (TEST_DB_NAME)
    # rather than being hardcoded: parallel sessions (e.g. two agents on
    # different branches) can then isolate by exporting DSNs with distinct
    # names instead of silently dropping each other's scratch DB mid-run.
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    apply_migrations(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, portfolio_snapshots, mappings, symbol_cache, "
            "symbol_commission, "
            "executions, positions, deals, deal_backfill_state, balance_samples, "
            "accounts, ctid_connections, "
            "org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET shards = 1")
    return database
