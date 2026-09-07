import pytest

from copier.db.repo import Repo
from copier.engine.routing import OrgRouting


@pytest.fixture
def make_routing():
    """OrgRouting for a single-org test world:
    make_routing(master=100, slaves=[SlaveConfig...], org_id=1)"""
    def _make(master, slaves, org_id=1):
        org_by_account = {master: org_id, **{s.account_id: org_id for s in slaves}}
        return OrgRouting(
            org_by_account=org_by_account,
            master_by_org={org_id: master},
            slaves_by_org={org_id: list(slaves)},
        )
    return _make


@pytest.fixture(autouse=True)
def _release_repo_connections(monkeypatch):
    """Close the connections each test's Repos opened.

    Repo caches one connection for the life of the object and deliberately
    has no close(): in production there is exactly one Repo per process, so
    nothing leaks. A test session is the opposite -- files here build a Repo
    per test, each holding a server connection nothing ever releases, and
    Postgres's default max_connections is 100. Left alone a full run walks
    into "sorry, too many clients already", and every LATER test fails,
    including tests in other files that did nothing wrong. That failure
    reads as unrelated breakage and costs hours to trace, so the guard
    lives here rather than in whichever file last tipped the count over.

    Closing is safe: Repo._connect() reopens any connection it finds closed,
    so a Repo outliving its test still works.
    """
    opened = []
    real_open = Repo._open

    def tracking_open(self):
        conn = real_open(self)
        opened.append(conn)
        return conn

    monkeypatch.setattr(Repo, "_open", tracking_open)
    yield
    for conn in opened:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture
def seed_mt5_account(db):
    """seed_mt5_account(org_id, role='slave', multiplier='1.0', enabled=True)
    -> the synthetic account id of a fresh MT5 account in that org, with the
    mt5_links row the api would have created (a dummy key hash; the copier
    never reads it)."""
    import psycopg

    def _seed(org_id, role="slave", multiplier="1.0", enabled=True):
        with psycopg.connect(db, autocommit=True) as conn:
            (account_id,) = conn.execute(
                """
                INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id,
                                      trader_login, is_live, role, enabled, multiplier, platform)
                VALUES (nextval('mt5_account_id_seq'), NULL, %s, 0, false, %s, %s, %s, 'mt5')
                RETURNING ctid_trader_account_id
                """,
                (org_id, role, enabled, multiplier),
            ).fetchone()
            conn.execute(
                "INSERT INTO mt5_links (account_id, key_hash) VALUES (%s, %s)",
                (account_id, f"hash-{account_id}"),
            )
        return account_id

    return _seed
