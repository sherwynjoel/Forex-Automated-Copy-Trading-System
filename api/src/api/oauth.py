"""OAuth connect flow for cTrader IDs."""
import secrets
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer, BadData
import httpx

from .config import ApiConfig
from .auth import require_admin, get_session_serializer
from .db import get_conn

logger = logging.getLogger(__name__)


def get_state_serializer(cfg: ApiConfig = Depends(ApiConfig.from_env)):
    """Get a state serializer for OAuth state parameter."""
    return URLSafeTimedSerializer(cfg.session_secret, salt="oauth-state")


def _create_state_hash(state: str) -> str:
    """Create a hash of the state for database tracking."""
    return hashlib.sha256(state.encode()).hexdigest()


def create_oauth_router() -> APIRouter:
    """Create router with OAuth endpoints."""
    router = APIRouter(prefix="/api/oauth", tags=["oauth"])

    @router.get("/connect")
    async def connect(
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        session: Optional[str] = Cookie(None),
        conn: psycopg.Connection = Depends(get_conn),
        request: Request = None,
    ):
        """Initiate OAuth connect flow to cTrader."""
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Extract session value (already validated by require_admin)
        session_serializer = get_session_serializer(cfg)
        try:
            session_serializer.loads(session, max_age=43200)
        except BadData:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Generate random state and bind it to the session cookie value
        serializer = get_state_serializer(cfg)
        state_value = secrets.token_urlsafe(32)
        # State includes random value and session cookie binding for verification
        state = serializer.dumps({
            "state": state_value,
            "session": session,  # Bind state to the current admin session
        })

        # Store state in database as consumed=False for single-use enforcement
        state_hash = _create_state_hash(state)
        try:
            conn.execute(
                """
                INSERT INTO oauth_states (state_hash, session, consumed_at)
                VALUES (%s, %s, NULL)
                ON CONFLICT (state_hash) DO NOTHING
                """,
                (state_hash, session),
            )
        except Exception as e:
            logger.error(f"Failed to store OAuth state: {e}")
            raise HTTPException(status_code=500, detail="Failed to initiate OAuth flow")

        # Build cTrader authorize URL
        from fastapi.responses import RedirectResponse
        authorize_url = (
            f"{cfg.ctrader_auth_url}"
            f"?client_id={cfg.ctrader_client_id}"
            f"&redirect_uri={cfg.ctrader_redirect_uri}"
            f"&scope=trading"
            f"&state={state}"
        )

        return RedirectResponse(url=authorize_url, status_code=307)

    @router.get("/callback")
    async def callback(
        code: Optional[str] = None,
        state: Optional[str] = None,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
        session: Optional[str] = Cookie(None),
        request: Request = None,
    ):
        """Handle OAuth callback from cTrader."""
        from fastapi.responses import RedirectResponse

        # Validate code and state parameters
        if not code or not state:
            logger.warning("OAuth callback missing code or state")
            raise HTTPException(status_code=400, detail="Missing authorization code or state")

        # Verify state signature and structure
        serializer = get_state_serializer(cfg)
        try:
            state_data = serializer.loads(state, max_age=3600)  # 1 hour TTL
        except BadData:
            logger.warning("OAuth state verification failed")
            raise HTTPException(status_code=403, detail="Invalid or expired state")

        # Verify state is bound to the current session
        if state_data.get("session") != session:
            logger.warning("OAuth state session mismatch - possible CSRF attempt")
            raise HTTPException(status_code=403, detail="State does not match session")

        # Atomically consume state (single-use enforcement)
        # UPDATE with WHERE consumed_at IS NULL and RETURNING ensures state was unconsumed
        state_hash = _create_state_hash(state)
        try:
            result = conn.execute(
                "UPDATE oauth_states SET consumed_at = %s WHERE state_hash = %s AND consumed_at IS NULL RETURNING state_hash",
                (datetime.now(timezone.utc), state_hash),
            ).fetchone()

            if not result:
                # Either state_hash doesn't exist or it was already consumed (race-safe check)
                logger.warning("OAuth state not found or already consumed - possible replay/forgery")
                raise HTTPException(status_code=403, detail="Invalid or already-used state")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"State consumption database error: {e}")
            raise HTTPException(status_code=500, detail="State validation failed")

        # Exchange authorization code for tokens
        try:
            # ALWAYS use the injected http_client (never fallback to live calls)
            http_client = request.app.state.http
            token_response = await http_client.post(
                cfg.ctrader_token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": cfg.ctrader_redirect_uri,
                    "client_id": cfg.ctrader_client_id,
                    "client_secret": cfg.ctrader_client_secret,
                },
                timeout=5,
            )

            if token_response.status_code != 200:
                logger.error(f"Token exchange failed: {token_response.status_code}")
                raise HTTPException(
                    status_code=400,
                    detail="Failed to obtain tokens from cTrader",
                )

            # Safely parse and validate token response
            try:
                token_data = token_response.json()
                if not isinstance(token_data, dict):
                    raise TypeError("Token response is not a JSON object")
            except Exception as e:
                logger.error(f"Invalid token response format: {e}")
                raise HTTPException(status_code=400, detail="Invalid response from token endpoint")

            # Extract tokens (handle both camelCase and snake_case)
            access_token = token_data.get("accessToken") or token_data.get("access_token")
            refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token")
            expires_in = token_data.get("expiresIn") or token_data.get("expires_in")

            # Validate token fields and types
            if not access_token or not isinstance(access_token, str):
                logger.error("Missing or invalid access token in response")
                raise HTTPException(status_code=400, detail="Invalid token response")
            if not refresh_token or not isinstance(refresh_token, str):
                logger.error("Missing or invalid refresh token in response")
                raise HTTPException(status_code=400, detail="Invalid token response")
            # Validate expires_in: must be numeric, positive, and finite
            if not isinstance(expires_in, (int, float)):
                logger.error(f"Missing or invalid expiresIn type: {type(expires_in)}")
                raise HTTPException(status_code=400, detail="Invalid token response")
            import math
            if not math.isfinite(expires_in) or expires_in <= 0:
                logger.error(f"Invalid expiresIn value (not finite or <= 0): {expires_in}")
                raise HTTPException(status_code=400, detail="Invalid token response")

        except httpx.RequestError as e:
            logger.error(f"Token exchange request failed: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to connect to token endpoint",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error during token exchange: {e}")
            raise HTTPException(status_code=500, detail="Token exchange failed")

        # Encrypt tokens
        try:
            cipher_suite = Fernet(cfg.fernet_key.encode() if isinstance(cfg.fernet_key, str) else cfg.fernet_key)
            access_token_enc = cipher_suite.encrypt(access_token.encode()).decode()
            refresh_token_enc = cipher_suite.encrypt(refresh_token.encode()).decode()
        except Exception as e:
            logger.error(f"Token encryption failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to encrypt tokens")

        # Store in database
        try:
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=int(expires_in))

            result = conn.execute(
                """
                INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at, scope, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (access_token_enc, refresh_token_enc, now, expires_at, "trading", "active"),
            ).fetchone()

            connection_id = result[0] if result else None
        except Exception as e:
            logger.error(f"Failed to store connection: {e}")
            raise HTTPException(status_code=500, detail="Failed to store connection")

        # Trigger discovery (best-effort)
        discover_failed = False
        if connection_id:
            try:
                http_client = request.app.state.http
                discover_response = await http_client.post(
                    f"{cfg.copier_control_url}/discover",
                    json={"connection_id": connection_id},
                    timeout=5,
                )
                if discover_response.status_code != 200:
                    logger.warning(f"Discovery failed with status {discover_response.status_code}")
                    discover_failed = True
            except Exception as e:
                logger.warning(f"Discovery request failed: {e}")
                discover_failed = True

        # Redirect to accounts page
        redirect_url = "/accounts?connected=1"
        if discover_failed:
            redirect_url += "&warning=discover_failed"

        return RedirectResponse(url=redirect_url, status_code=307)

    return router
