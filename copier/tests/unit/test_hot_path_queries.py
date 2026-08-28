"""The copy hot path must issue exactly ONE synchronous database round trip
per slave.

Before the persistence work, one master trade with N slaves cost N+2 fresh
connections on the reactor thread -- measured median 243ms / p95 417ms, with
later slaves in the fan-out filling strictly worse than earlier ones. If a
future change puts a blocking query back on this path, this test fails
rather than production slippage quietly widening.

WHAT IS COUNTED. There is no connection pool in this codebase (porting spec
D1): `Repo` holds one cached connection and every statement leases it
through the `Repo._connect()` contextmanager. So a lease of `_connect()` is
the unit of blocking database work on the reactor thread, and that is what
this counts -- not connection creation, which the cached connection makes
almost free and therefore no longer a useful signal.

Reuses `test_dispatch.py`'s `repo` and `seed_accounts` fixtures (org
`ORG_ID` with slave accounts 101 and 102).
"""

from unittest.mock import Mock

import pytest
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.db.writer import AsyncWriter
from copier.domain.models import OpenMarket, Side
from copier.engine.dispatch import Dispatcher
from test_dispatch import ORG_ID, repo, seed_accounts  # noqa: F401


class _LeaseCounter:
    """Wraps `Repo._connect` and counts how many times it is leased."""

    def __init__(self, repo):
        self.repo = repo
        self.inner = repo._connect
        self.leases = 0

    def __enter__(self):
        self.repo._connect = self
        self.leases = 0
        return self

    def __call__(self):
        self.leases += 1
        return self.inner()

    def __exit__(self, *exc):
        # An instance attribute shadows the bound method; deleting it puts
        # the real contextmanager back.
        del self.repo._connect
        return False


@pytest.fixture
def writer(repo, db):  # noqa: F811
    """A writer attached to the repo, drained inline at teardown.

    Deliberately not start()ed: nothing here waits on a row landing, and
    flush_and_stop() writes whatever is queued on the calling thread, so
    the test never races a daemon thread.
    """
    w = AsyncWriter(db, batch_interval_s=10.0)
    repo.writer = w
    try:
        yield w
    finally:
        repo.writer = None
        w.flush_and_stop(timeout_s=2.0)


def test_dispatching_to_two_slaves_costs_exactly_two_mapping_inserts(
        seed_accounts, repo, writer):  # noqa: F811
    """One org read plus one mapping insert per slave. Nothing else may
    block the reactor between a master fill and the copies reaching the
    wire.

    Asserted as EQUALITY, not `<=`. With `<=` this passes just as happily
    if mapping creation is deleted altogether -- and a copy with no mapping
    row is a live position at the broker that the fill handler cannot
    activate, the reconciler cannot recognise and the close path cannot
    find.
    """
    def mock_send(account_id, msg):
        return defer.succeed(None)

    dispatcher = Dispatcher(mock_send, repo, Mock(), clock=Clock())
    intents = [
        OpenMarket(slave_account_id=acct, master_position_id=42,
                   symbol_id=1, side=Side.BUY, volume=1000,
                   stop_loss=None, take_profit=None, label="test")
        for acct in (101, 102)
    ]

    with _LeaseCounter(repo) as counter:
        dispatcher.dispatch(intents, org_id=ORG_ID)
        leases = counter.leases

    assert leases == 3, (
        f"hot path made {leases} blocking DB round trips for 2 slaves; "
        "expected exactly 3 (1 get_org + 1 create_position_mapping each)")


def test_log_event_with_writer_attached_makes_no_blocking_round_trips(
        seed_accounts, repo, writer):  # noqa: F811
    """Event logging must never block the reactor on a DB round trip.

    With an AsyncWriter attached, log_event() only enqueues; the actual
    INSERT happens later on the writer's own thread and its own connection,
    never on the one the copy path leases.
    """
    with _LeaseCounter(repo) as counter:
        for i in range(50):
            repo.log_event(
                'slave_action', 'info', {'i': i},
                account_id=101, org_id=ORG_ID,
            )
        leases = counter.leases

    assert leases == 0, (
        f"log_event made {leases} blocking DB round trips with a writer "
        "attached; expected 0 (rows should be queued, not written "
        "synchronously)")
