import os
import pathlib
import sys

import psycopg
import pytest
from fastapi.testclient import TestClient

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

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS copytrader_test WITH (FORCE)")
        conn.execute("CREATE DATABASE copytrader_test")
    apply_migrations(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def db(database):
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE events, mappings, symbol_cache, accounts, ctid_connections, admin "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE settings SET copying_enabled = true, dry_run = false, shards = 1")
    return database


@pytest.fixture
def app_client(db):
    """Provide a TestClient with test environment variables."""
    # Set up test environment variables
    os.environ["POSTGRES_DSN"] = db
    os.environ["SESSION_SECRET"] = "test-secret"
    os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "hunter2!"
    os.environ["COPIER_CONTROL_URL"] = "http://copier.test"

    # Import after setting env vars
    from api.main import create_app
    from api.auth import ensure_admin

    # Explicitly bootstrap admin since TestClient doesn't run lifespan
    ensure_admin(db, "hunter2!")

    app = create_app()
    return TestClient(app)
