"""Database connection management."""
import psycopg
from fastapi import Depends
from typing import Generator

from .config import ApiConfig


def get_cfg() -> ApiConfig:
    """Get configuration."""
    return ApiConfig.from_env()


def get_conn(cfg: ApiConfig = Depends(get_cfg)) -> Generator[psycopg.Connection, None, None]:
    """Get a database connection in autocommit mode with automatic cleanup."""
    conn = psycopg.connect(cfg.postgres_dsn, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()
