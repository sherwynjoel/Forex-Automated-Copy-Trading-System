"""Tests for persisted drift dismissals (migration 008).

Reconciler._dismissed was in-memory only, so every deploy/restart
resurfaced every dismissed drift item -- exactly what happened on prod
on 2026-08-20, when two copier restarts brought back 11 dismissals the
operator had already cleared. Dismissals now live in drift_dismissals:
written by dismiss(), loaded at Reconciler construction, and pruned when
the underlying condition clears so a RETURNING condition alerts again.
"""
from unittest.mock import Mock

import psycopg
import pytest

from copier.db.repo import Repo
from copier.engine.reconcile import DriftItem, Reconciler

MASTER_ID = 100


@pytest.fixture
def org_id(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (org,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Desk') RETURNING id").fetchone()
    return org


def _reconciler(db, org):
    return Reconciler(
        clients_by_account=Mock(),
        repo=Repo(db),
        dispatcher=Mock(),
        master_account_id=MASTER_ID,
        org_id=org,
    )


def _item(item_id="deadbeef0001", position_id=555):
    return DriftItem(
        id=item_id, kind="orphan_slave_position", account_id=101,
        position_id=position_id, order_id=None,
        detail="Labeled copy:* but no active mapping")


# ---------- migration 008: schema ----------

def test_drift_dismissals_table_has_org_scoped_pk(db):
    with psycopg.connect(db) as conn:
        cols = conn.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = 'drift_dismissals'""").fetchall()
        pk = conn.execute(
            """SELECT a.attname FROM pg_index i
               JOIN pg_attribute a ON a.attrelid = i.indrelid
                AND a.attnum = ANY(i.indkey)
               WHERE i.indrelid = 'drift_dismissals'::regclass
                 AND i.indisprimary""").fetchall()
    assert {c[0] for c in cols} >= {"org_id", "drift_id", "dismissed_at"}
    assert {p[0] for p in pk} == {"org_id", "drift_id"}


# ---------- repo: save / load / prune ----------

def test_save_load_prune_roundtrip(db, org_id):
    repo = Repo(db)

    repo.save_drift_dismissal(org_id, "aaa")
    repo.save_drift_dismissal(org_id, "bbb")
    repo.save_drift_dismissal(org_id, "aaa")  # idempotent re-dismiss

    assert repo.load_drift_dismissals(org_id) == {"aaa", "bbb"}

    repo.prune_drift_dismissals(org_id, keep={"aaa"})
    assert repo.load_drift_dismissals(org_id) == {"aaa"}


def test_dismissals_are_org_scoped(db, org_id):
    repo = Repo(db)
    with psycopg.connect(db, autocommit=True) as conn:
        (other_org,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('Other') RETURNING id").fetchone()

    repo.save_drift_dismissal(org_id, "aaa")
    repo.save_drift_dismissal(other_org, "zzz")

    assert repo.load_drift_dismissals(org_id) == {"aaa"}
    # Pruning one org must never touch another org's dismissals.
    repo.prune_drift_dismissals(org_id, keep=set())
    assert repo.load_drift_dismissals(other_org) == {"zzz"}


# ---------- reconciler: dismissals survive a restart ----------

def test_dismissal_survives_a_new_reconciler_instance(db, org_id):
    """The prod failure mode: dismiss, restart the copier (here: build a
    fresh Reconciler), and the dismissal must still suppress the item."""
    item = _item()
    first = _reconciler(db, org_id)
    first.current = [item]

    first.dismiss(item.id)

    reborn = _reconciler(db, org_id)
    assert reborn._dismissed == {item.id}
    assert reborn._apply_dismissals([item, _item("cafecafe0002", 777)]) == [
        _item("cafecafe0002", 777)]


def test_cleared_condition_prunes_the_stored_dismissal(db, org_id):
    """When the dismissed condition no longer computes, the stored row is
    pruned, so the same condition RETURNING later alerts again."""
    item = _item()
    reconciler = _reconciler(db, org_id)
    reconciler.current = [item]
    reconciler.dismiss(item.id)

    # Next scan: the orphan is gone from the broker.
    assert reconciler._apply_dismissals([]) == []

    assert Repo(db).load_drift_dismissals(org_id) == set()
    assert _reconciler(db, org_id)._dismissed == set()
