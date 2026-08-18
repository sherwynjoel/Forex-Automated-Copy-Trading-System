"""API configuration from environment variables."""
import os
from dataclasses import dataclass


@dataclass
class ApiConfig:
    """Configuration for the API."""

    postgres_dsn: str
    session_secret: str
    bootstrap_admin_email: str
    bootstrap_admin_password: str
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
        fernet_key = os.environ.get("FERNET_KEY", "")
        ctrader_client_id = os.environ.get("CTRADER_CLIENT_ID", "")
        ctrader_client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
        ctrader_redirect_uri = os.environ.get("CTRADER_REDIRECT_URI", "")
        ctrader_auth_url = os.environ.get("CTRADER_AUTH_URL", "")
        ctrader_token_url = os.environ.get("CTRADER_TOKEN_URL", "")

        if not session_secret:
            raise ValueError("SESSION_SECRET must be set and non-empty")
        if not fernet_key:
            raise ValueError("FERNET_KEY must be set and non-empty")
        if not ctrader_client_id:
            raise ValueError("CTRADER_CLIENT_ID must be set and non-empty")
        if not ctrader_client_secret:
            raise ValueError("CTRADER_CLIENT_SECRET must be set and non-empty")
        if not ctrader_redirect_uri:
            raise ValueError("CTRADER_REDIRECT_URI must be set and non-empty")
        if not ctrader_auth_url:
            raise ValueError("CTRADER_AUTH_URL must be set and non-empty")
        if not ctrader_token_url:
            raise ValueError("CTRADER_TOKEN_URL must be set and non-empty")

        return cls(
            postgres_dsn=os.environ["POSTGRES_DSN"],
            session_secret=session_secret,
            bootstrap_admin_email=os.environ.get("BOOTSTRAP_ADMIN_EMAIL", ""),
            bootstrap_admin_password=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""),
            copier_control_url=os.environ["COPIER_CONTROL_URL"],
            cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() in ("true", "1", "yes"),
            ctrader_client_id=ctrader_client_id,
            ctrader_client_secret=ctrader_client_secret,
            ctrader_redirect_uri=ctrader_redirect_uri,
            ctrader_auth_url=ctrader_auth_url,
            ctrader_token_url=ctrader_token_url,
            fernet_key=fernet_key,
        )
