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


@pytest.fixture(scope="session")
def database():
    from db.migrate import apply_migrations

    # The scratch database NAME comes from TEST_POSTGRES_DSN rather than
    # being hardcoded: parallel sessions (e.g. two agents on different
    # branches) can then isolate by exporting DSNs with distinct names
    # instead of silently dropping each other's scratch database mid-run.
    scratch_db = TEST_DSN.rsplit("/", 1)[1].split("?")[0]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{scratch_db}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{scratch_db}"')
    apply_migrations(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, portfolio_snapshots, mappings, symbol_cache, accounts, ctid_connections, admin "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET copying_enabled = true, dry_run = false, shards = 1")
    return database
