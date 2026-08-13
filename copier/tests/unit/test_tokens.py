from datetime import datetime, timedelta, timezone

import psycopg
from cryptography.fernet import Fernet

from copier.ctrader.tokens import REFRESH_THRESHOLD, TokenStore

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
