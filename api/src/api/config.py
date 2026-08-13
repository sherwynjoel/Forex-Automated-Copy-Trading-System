"""API configuration from environment variables."""
import os
from dataclasses import dataclass


@dataclass
class ApiConfig:
    """Configuration for the API."""

    postgres_dsn: str
    session_secret: str
    admin_bootstrap_password: str
    copier_control_url: str

    @classmethod
    def from_env(cls) -> "ApiConfig":
        """Load configuration from environment variables."""
        return cls(
            postgres_dsn=os.environ["POSTGRES_DSN"],
            session_secret=os.environ["SESSION_SECRET"],
            admin_bootstrap_password=os.environ["ADMIN_BOOTSTRAP_PASSWORD"],
            copier_control_url=os.environ["COPIER_CONTROL_URL"],
        )
