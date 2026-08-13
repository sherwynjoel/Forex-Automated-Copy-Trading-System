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
    cookie_secure: bool

    @classmethod
    def from_env(cls) -> "ApiConfig":
        """Load configuration from environment variables."""
        session_secret = os.environ.get("SESSION_SECRET", "")
        admin_bootstrap_password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")

        if not session_secret:
            raise ValueError("SESSION_SECRET must be set and non-empty")
        if not admin_bootstrap_password:
            raise ValueError("ADMIN_BOOTSTRAP_PASSWORD must be set and non-empty")

        return cls(
            postgres_dsn=os.environ["POSTGRES_DSN"],
            session_secret=session_secret,
            admin_bootstrap_password=admin_bootstrap_password,
            copier_control_url=os.environ["COPIER_CONTROL_URL"],
            cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() in ("true", "1", "yes"),
        )
