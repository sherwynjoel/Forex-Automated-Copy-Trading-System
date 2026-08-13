"""Apply SQL migrations in db/migrations, in filename order, exactly once each."""
import os
import pathlib

import psycopg

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def apply_migrations(dsn: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied


if __name__ == "__main__":
    names = apply_migrations(os.environ["POSTGRES_DSN"])
    print(f"applied: {names or 'nothing (up to date)'}")
