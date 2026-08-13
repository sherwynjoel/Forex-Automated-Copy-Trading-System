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
    ctrader_client_id: str
    ctrader_client_secret: str
    ctrader_redirect_uri: str
    ctrader_auth_url: str
    ctrader_token_url: str
    fernet_key: str

    @classmethod
    def from_env(cls) -> "ApiConfig":
        """Load configuration from environment variables."""
        session_secret = os.environ.get("SESSION_SECRET", "")
        admin_bootstrap_password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
        fernet_key = os.environ.get("FERNET_KEY", "")

        if not session_secret:
            raise ValueError("SESSION_SECRET must be set and non-empty")
        if not admin_bootstrap_password:
            raise ValueError("ADMIN_BOOTSTRAP_PASSWORD must be set and non-empty")
        if not fernet_key:
            raise ValueError("FERNET_KEY must be set and non-empty")

        return cls(
            postgres_dsn=os.environ["POSTGRES_DSN"],
            session_secret=session_secret,
            admin_bootstrap_password=admin_bootstrap_password,
            copier_control_url=os.environ["COPIER_CONTROL_URL"],
            cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() in ("true", "1", "yes"),
            ctrader_client_id=os.environ["CTRADER_CLIENT_ID"],
            ctrader_client_secret=os.environ["CTRADER_CLIENT_SECRET"],
            ctrader_redirect_uri=os.environ["CTRADER_REDIRECT_URI"],
            ctrader_auth_url=os.environ["CTRADER_AUTH_URL"],
            ctrader_token_url=os.environ["CTRADER_TOKEN_URL"],
            fernet_key=fernet_key,
        )
