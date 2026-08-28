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
