"""Database connection management."""
import psycopg
from fastapi import Depends

from .config import ApiConfig


def get_cfg() -> ApiConfig:
    """Get configuration."""
    return ApiConfig.from_env()


def get_conn(cfg: ApiConfig = Depends(get_cfg)) -> psycopg.Connection:
    """Get a database connection in autocommit mode."""
    return psycopg.connect(cfg.postgres_dsn, autocommit=True)
