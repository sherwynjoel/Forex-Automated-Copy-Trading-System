from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from cryptography.fernet import Fernet

REFRESH_THRESHOLD = timedelta(days=25)


class UnknownConnection(KeyError):
    """Raised when rotate() or mark() is called with a non-existent connection_id."""
    pass


@dataclass(frozen=True)
class TokenPair:
    connection_id: int
    access_token: str
    refresh_token: str
    expires_at: datetime
    status: str


class TokenStore:
    def __init__(self, dsn: str, fernet_key: str):
        self._dsn = dsn
        self._fernet = Fernet(fernet_key.encode())

    def _enc(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def _dec(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()

    def save_grant(self, access_token: str, refresh_token: str, expires_at: datetime,
                   org_id: int) -> int:
        """Persist a new OAuth grant for one org.

        `org_id` is not optional: ctid_connections.org_id is NOT NULL (every
        connection belongs to exactly one tenant, and connection_org() is what
        discovery resolves an account's owner from), so a grant with no org
        could never be stored at all.
        """
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                "INSERT INTO ctid_connections"
                " (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)"
                " VALUES (%s, %s, %s, now(), %s) RETURNING id",
                (org_id, self._enc(access_token), self._enc(refresh_token), expires_at),
            ).fetchone()
            conn.commit()
            return row[0]

    def get(self, connection_id: int) -> TokenPair:
        with psycopg.connect(self._dsn) as conn:
            a, r, exp, status = conn.execute(
                "SELECT access_token_enc, refresh_token_enc, expires_at, status"
                " FROM ctid_connections WHERE id=%s", (connection_id,)).fetchone()
        return TokenPair(connection_id, self._dec(a), self._dec(r), exp, status)

    def rotate(self, connection_id: int, access_token: str, refresh_token: str,
               expires_at: datetime) -> None:
        # single transaction: rotated refresh token is never lost (spec §5)
        with psycopg.connect(self._dsn) as conn:
            cur = conn.execute(
                "UPDATE ctid_connections SET access_token_enc=%s, refresh_token_enc=%s,"
                " expires_at=%s, status='active' WHERE id=%s",
                (self._enc(access_token), self._enc(refresh_token), expires_at, connection_id))
            conn.commit()
            if cur.rowcount == 0:
                raise UnknownConnection(connection_id)

    def due_for_refresh(self, now: datetime) -> list[int]:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT id FROM ctid_connections"
                " WHERE status='active' AND expires_at - %s < %s ORDER BY id",
                (now, REFRESH_THRESHOLD)).fetchall()
        return [r[0] for r in rows]

    def mark(self, connection_id: int, status: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            cur = conn.execute("UPDATE ctid_connections SET status=%s WHERE id=%s",
                               (status, connection_id))
            conn.commit()
            if cur.rowcount == 0:
                raise UnknownConnection(connection_id)
