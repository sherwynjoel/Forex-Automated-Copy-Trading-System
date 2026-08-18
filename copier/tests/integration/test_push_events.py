"""End-to-end tests for broker push events the copier now consumes:
ProtoOATraderUpdatedEvent (instant balance updates), ProtoOAMarginCall-
TriggerEvent (risk events), and the daily portfolio snapshot written by the
balance refresh."""

import psycopg
import pytest_twisted

from integration.test_copier_e2e import (
    MASTER_ID, ORG_ID, SLAVE1_ID, _setup, _teardown, _wait_until,
)


@pytest_twisted.inlineCallbacks
def test_trader_update_pushes_balance_instantly(db):
    server, repo, app = _setup(db)
    try:
        yield app.startup()

        server.push_trader_update(MASTER_ID, balance=555_000, money_digits=2)

        def balance_updated():
            # Trackers are per-org: the push has to be routed to the tracker
            # of the org that OWNS the account, which is the only one whose
            # snapshot feeds that org's /state.
            snap = app.state_trackers[ORG_ID].snapshot().get(MASTER_ID) or {}
            return snap.get("balance") == 5550.0

        yield _wait_until(balance_updated)
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_margin_call_logs_risk_event(db):
    server, repo, app = _setup(db)
    try:
        yield app.startup()

        server.push_margin_call(SLAVE1_ID, threshold=50.0)

        def has_risk_event():
            with psycopg.connect(db, autocommit=True) as conn:
                row = conn.execute(
                    """SELECT payload FROM events
                       WHERE category = 'risk' AND severity = 'error'
                         AND account_id = %s""",
                    (SLAVE1_ID,),
                ).fetchone()
            return row is not None and row[0].get("action") == "margin_call"

        yield _wait_until(has_risk_event)

        # The margin call MUST carry the owning org. The api's events feed
        # hides NULL-org rows, so an unstamped risk event would mean the
        # dashboard's margin-call banner never fires for the desk whose
        # account is being margin-called.
        with psycopg.connect(db, autocommit=True) as conn:
            (org_id,) = conn.execute(
                """SELECT org_id FROM events
                   WHERE category = 'risk' AND account_id = %s""",
                (SLAVE1_ID,),
            ).fetchone()
        assert org_id == ORG_ID
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_refresh_balances_writes_daily_snapshot(db):
    server, repo, app = _setup(db)
    server.balances[MASTER_ID] = 1_000_000
    try:
        yield app.startup()  # startup() runs the first refresh_balances

        def has_snapshots():
            with psycopg.connect(db, autocommit=True) as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM portfolio_snapshots "
                    "WHERE snapshot_date = CURRENT_DATE"
                ).fetchone()[0]
            return n >= 3  # master + both slaves

        yield _wait_until(has_snapshots)

        with psycopg.connect(db, autocommit=True) as conn:
            row = conn.execute(
                "SELECT balance, org_id FROM portfolio_snapshots "
                "WHERE snapshot_date = CURRENT_DATE AND account_id = %s",
                (MASTER_ID,),
            ).fetchone()
        assert float(row[0]) == 10000.0
        # Stamped with the owning org (migration 006) so each desk's
        # Overview sums only its own accounts' snapshots.
        assert row[1] == ORG_ID
    finally:
        _teardown(app, server)
