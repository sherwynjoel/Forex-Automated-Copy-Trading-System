from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from cryptography.fernet import Fernet

from copier.ctrader.tokens import REFRESH_THRESHOLD, TokenStore, UnknownConnection

KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def make_store(db):
    return TokenStore(db, KEY)


def test_save_grant_encrypts_tokens_at_rest(db):
    store = make_store(db)
    cid = store.save_grant("access-1", "refresh-1", NOW + timedelta(days=30))
    with psycopg.connect(db) as conn:
        enc_a, enc_r = conn.execute(
            "SELECT access_token_enc, refresh_token_enc FROM ctid_connections WHERE id=%s",
            (cid,)).fetchone()
    assert "access-1" not in enc_a and "refresh-1" not in enc_r     # never plaintext
    assert Fernet(KEY.encode()).decrypt(enc_a.encode()).decode() == "access-1"


def test_get_roundtrips(db):
    store = make_store(db)
    cid = store.save_grant("a", "r", NOW + timedelta(days=30))
    pair = store.get(cid)
    assert (pair.access_token, pair.refresh_token, pair.status) == ("a", "r", "active")


def test_rotate_persists_new_refresh_token(db):
    # spec: refresh token rotates on every refresh; new one MUST be persisted
    store = make_store(db)
    cid = store.save_grant("a1", "r1", NOW + timedelta(days=30))
    store.rotate(cid, "a2", "r2", NOW + timedelta(days=60))
    pair = store.get(cid)
    assert (pair.access_token, pair.refresh_token) == ("a2", "r2")
    assert pair.expires_at == NOW + timedelta(days=60)
    assert pair.status == "active"          # rotation revives invalid connections


def test_due_for_refresh_under_25_days_remaining(db):
    store = make_store(db)
    due = store.save_grant("a", "r", NOW + timedelta(days=24))
    fresh = store.save_grant("a", "r", NOW + timedelta(days=26))
    assert store.due_for_refresh(NOW) == [due]
    assert fresh not in store.due_for_refresh(NOW)


def test_mark_status(db):
    store = make_store(db)
    cid = store.save_grant("a", "r", NOW + timedelta(days=30))
    store.mark(cid, "refresh_failed")
    assert store.get(cid).status == "refresh_failed"


def test_threshold_constant():
    assert REFRESH_THRESHOLD == timedelta(days=25)


def test_rotate_raises_on_unknown_connection_id(db):
    store = make_store(db)
    with pytest.raises(UnknownConnection):
        store.rotate(99999, "a", "r", NOW + timedelta(days=30))


def test_rotate_succeeds_with_valid_connection_id(db):
    store = make_store(db)
    cid = store.save_grant("a1", "r1", NOW + timedelta(days=30))
    store.rotate(cid, "a2", "r2", NOW + timedelta(days=60))  # should not raise
    pair = store.get(cid)
    assert pair.access_token == "a2"


def test_mark_raises_on_unknown_connection_id(db):
    store = make_store(db)
    with pytest.raises(UnknownConnection):
        store.mark(99999, "refresh_failed")


def test_mark_succeeds_with_valid_connection_id(db):
    store = make_store(db)
    cid = store.save_grant("a", "r", NOW + timedelta(days=30))
    store.mark(cid, "refresh_failed")  # should not raise
    assert store.get(cid).status == "refresh_failed"


def test_due_for_refresh_at_exactly_threshold_not_due(db):
    # Spec: expires_at - now < REFRESH_THRESHOLD (strict <, not <=)
    # So at exactly 25 days, NOT due
    store = make_store(db)
    exactly_threshold = store.save_grant("a", "r", NOW + timedelta(days=25))
    assert store.due_for_refresh(NOW) == []
    assert exactly_threshold not in store.due_for_refresh(NOW)


def test_due_for_refresh_excludes_invalid_status(db):
    store = make_store(db)
    # Connection that would be due except for status
    due_id = store.save_grant("a", "r", NOW + timedelta(days=24))
    store.mark(due_id, "invalid")
    # Even though expiry qualifies (< 25 days), status='invalid' excludes it
    assert store.due_for_refresh(NOW) == []


def test_due_for_refresh_excludes_refresh_failed_status(db):
    store = make_store(db)
    # Connection that would be due except for status
    due_id = store.save_grant("a", "r", NOW + timedelta(days=24))
    store.mark(due_id, "refresh_failed")
    # Even though expiry qualifies (< 25 days), status='refresh_failed' excludes it
    assert store.due_for_refresh(NOW) == []
