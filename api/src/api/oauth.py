"""OAuth connect flow for cTrader IDs."""
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer, BadSignature
import httpx

from .config import ApiConfig
from .auth import require_admin
from .db import get_conn

logger = logging.getLogger(__name__)


def get_state_serializer(cfg: ApiConfig = Depends(ApiConfig.from_env)):
    """Get a state serializer for OAuth state parameter."""
    return URLSafeTimedSerializer(cfg.session_secret, salt="oauth-state")


def create_oauth_router() -> APIRouter:
    """Create router with OAuth endpoints."""
    router = APIRouter(prefix="/api/oauth", tags=["oauth"])

    @router.get("/connect")
    async def connect(
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        request: Request = None,
    ):
        """Initiate OAuth connect flow to cTrader."""
        # Generate random state and sign it
        serializer = get_state_serializer(cfg)
        state_value = secrets.token_urlsafe(32)
        state = serializer.dumps({"state": state_value})

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
        code: str,
        state: str,
        _: bool = Depends(require_admin),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
        request: Request = None,
    ):
        """Handle OAuth callback from cTrader."""
        from fastapi.responses import RedirectResponse

        # Verify state parameter
        serializer = get_state_serializer(cfg)
        try:
            state_data = serializer.loads(state, max_age=3600)  # 1 hour
        except BadSignature:
            raise HTTPException(status_code=403, detail="Invalid or expired state")

        # Exchange authorization code for tokens
        try:
            # Use app.state.http if available (injected in tests), otherwise create a client
            if hasattr(request.app.state, 'http'):
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
                )
            else:
                async with httpx.AsyncClient() as client:
                    token_response = await client.post(
                        cfg.ctrader_token_url,
                        data={
                            "grant_type": "authorization_code",
                            "code": code,
                            "redirect_uri": cfg.ctrader_redirect_uri,
                            "client_id": cfg.ctrader_client_id,
                            "client_secret": cfg.ctrader_client_secret,
                        },
                    )

            if token_response.status_code != 200:
                # Handle error responses from cTrader
                logger.error(f"Token exchange failed: {token_response.status_code}")
                raise HTTPException(
                    status_code=400,
                    detail="Failed to obtain tokens from cTrader",
                )

            token_data = token_response.json()
        except httpx.RequestError as e:
            logger.error(f"Token exchange request failed: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to connect to cTrader token endpoint",
            )

        # Extract tokens (handle both camelCase and snake_case)
        access_token = token_data.get("accessToken") or token_data.get("access_token")
        refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token")
        expires_in = token_data.get("expiresIn") or token_data.get("expires_in")

        if not access_token or not refresh_token or not expires_in:
            logger.error(f"Invalid token response: missing required fields")
            raise HTTPException(
                status_code=400,
                detail="Invalid token response from cTrader",
            )

        # Encrypt tokens
        cipher_suite = Fernet(cfg.fernet_key.encode() if isinstance(cfg.fernet_key, str) else cfg.fernet_key)
        access_token_enc = cipher_suite.encrypt(access_token.encode()).decode()
        refresh_token_enc = cipher_suite.encrypt(refresh_token.encode()).decode()

        # Store in database
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in)

        result = conn.execute(
            """
            INSERT INTO ctid_connections (access_token_enc, refresh_token_enc, granted_at, expires_at, scope, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (access_token_enc, refresh_token_enc, now, expires_at, "trading", "active"),
        ).fetchone()

        connection_id = result[0] if result else None

        # Trigger discovery (best-effort)
        discover_failed = False
        if connection_id:
            try:
                if hasattr(request.app.state, 'http'):
                    http_client = request.app.state.http
                    discover_response = await http_client.post(
                        f"{cfg.copier_control_url}/discover",
                        json={"connection_id": connection_id},
                        timeout=5,
                    )
                    if discover_response.status_code != 200:
                        logger.warning(f"Discovery failed with status {discover_response.status_code}")
                        discover_failed = True
                else:
                    async with httpx.AsyncClient() as client:
                        discover_response = await client.post(
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
