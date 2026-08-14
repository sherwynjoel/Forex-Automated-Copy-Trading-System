"""OAuth connect flow for cTrader IDs."""
import secrets
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from cryptography.fernet import Fernet
from itsdangerous import BadData
import httpx

from .config import ApiConfig
from .auth import require_admin, get_session_serializer
from .db import get_conn

logger = logging.getLogger(__name__)

# How long an unconsumed OAuth state stays usable. Previously enforced by
# the signed state's own max_age; now enforced against oauth_states.created_at,
# since the state parameter carries no self-describing timestamp any more.
STATE_TTL_SECONDS = 3600


def _digest(value: str) -> str:
    """SHA-256 hex digest.

    N4 -- used for BOTH columns of oauth_states, and neither is reversible:

    - `state_hash`: the stored lookup key for the opaque nonce that travels
      to cTrader. Storing the digest rather than the nonce means a read of
      this table does not hand anyone a usable, still-unconsumed state.
    - `session`: a BINDING for the admin session that started the flow, not
      the session itself. The state parameter used to be
      `URLSafeTimedSerializer(...).dumps({"state": ..., "session": session})`
      -- SIGNED, NOT ENCRYPTED, so its payload was plain base64 JSON
      containing the full, currently-valid admin session cookie. That value
      travelled in a query string to openapi.ctrader.com (their request
      logs), sat in browser history, and came back in the callback URL;
      anyone who read it anywhere along that path could decode it and replay
      the admin session for its remaining 12 h. A digest binds the flow to
      the session just as tightly (an attacker still cannot produce a
      matching cookie) while carrying nothing worth stealing.
    """
    return hashlib.sha256(value.encode()).hexdigest()


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

        # N4: the state parameter is now an OPAQUE RANDOM NONCE and nothing
        # else. Everything the callback needs to validate it -- that it was
        # issued by us, which admin session it belongs to, when it was
        # issued, and whether it has already been used -- lives server-side
        # in oauth_states, keyed by the nonce's digest. The nonce itself
        # carries no payload, so there is nothing in the URL that reaches
        # cTrader's logs or the browser's history worth stealing.
        state = secrets.token_urlsafe(32)

        # Store state in database as consumed=False for single-use enforcement
        try:
            conn.execute(
                """
                INSERT INTO oauth_states (state_hash, session, consumed_at)
                VALUES (%s, %s, NULL)
                ON CONFLICT (state_hash) DO NOTHING
                """,
                (_digest(state), _digest(session)),
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

        # N4: validate and consume the state in ONE atomic statement.
        #
        # Every condition the old code checked separately is now a predicate
        # in this UPDATE's WHERE clause, so there is no window between
        # "checked" and "consumed", and -- because session binding is part
        # of the same predicate -- a request presenting a valid nonce under
        # the WRONG session does not consume (and therefore cannot burn) a
        # legitimate flow's state:
        #   - the nonce was issued by us and is not forged  (state_hash match)
        #   - it has not already been used                  (consumed_at IS NULL)
        #   - it belongs to THIS admin session              (session digest match)
        #   - it has not expired                            (created_at within TTL)
        # `session` is guaranteed non-empty here: require_admin already
        # rejected the request otherwise.
        try:
            result = conn.execute(
                """
                UPDATE oauth_states SET consumed_at = %s
                WHERE state_hash = %s
                  AND consumed_at IS NULL
                  AND session = %s
                  AND created_at > %s - make_interval(secs => %s)
                RETURNING state_hash
                """,
                (
                    datetime.now(timezone.utc),
                    _digest(state),
                    _digest(session or ""),
                    datetime.now(timezone.utc),
                    STATE_TTL_SECONDS,
                ),
            ).fetchone()

            if not result:
                # Unknown/forged nonce, already consumed, expired, or bound
                # to a different session -- all indistinguishable to the
                # caller on purpose.
                logger.warning(
                    "OAuth state rejected (unknown, already consumed, expired, or "
                    "bound to a different session) - possible replay/forgery"
                )
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
