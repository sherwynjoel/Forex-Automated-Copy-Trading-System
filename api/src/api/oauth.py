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
from .auth import require_user, get_session_serializer
from .db import get_conn
from .rbac import OrgContext, require_org_role

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
    """Create router with the org-scoped OAuth connect endpoint."""
    router = APIRouter(prefix="/api/orgs/{org_id}/oauth", tags=["oauth"])

    @router.get("/connect")
    async def connect(
        ctx: OrgContext = Depends(require_org_role("admin")),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        session: Optional[str] = Cookie(None),
        conn: psycopg.Connection = Depends(get_conn),
        request: Request = None,
    ):
        """Initiate OAuth connect flow to cTrader."""
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Extract session value (already validated by require_org_role/require_user)
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

        # Sweep expired states before adding one. Nothing else ever deleted
        # them, so every click of Connect left a row behind for good -- 71
        # had piled up in production, and once more than one was still
        # inside STATE_TTL_SECONDS the callback could no longer tell which
        # org was meant and refused the connect outright. Cheap, bounded,
        # and it keeps the single-use guard honest.
        try:
            conn.execute(
                "DELETE FROM oauth_states WHERE created_at < now() - make_interval(secs => %s)",
                (STATE_TTL_SECONDS,),
            )
        except Exception as e:
            # Housekeeping must never block a connect.
            logger.warning(f"Could not sweep expired OAuth states: {e}")

        # Store state in database as consumed=False for single-use enforcement
        try:
            conn.execute(
                """
                INSERT INTO oauth_states (state_hash, session, org_id, consumed_at)
                VALUES (%s, %s, %s, NULL)
                ON CONFLICT (state_hash) DO NOTHING
                """,
                (_digest(state), _digest(session), ctx.org_id),
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

    return router


def create_oauth_callback_router() -> APIRouter:
    """Create router with the global OAuth callback endpoint.

    This path is NOT org-scoped: it must match the redirect URI registered
    with cTrader exactly (`/api/oauth/callback`), so the org is resolved
    from the consumed `oauth_states` row rather than from the path.
    """
    router = APIRouter(prefix="/api/oauth", tags=["oauth"])

    @router.get("/callback")
    async def callback(
        code: Optional[str] = None,
        state: Optional[str] = None,
        user_id: int = Depends(require_user),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
        conn: psycopg.Connection = Depends(get_conn),
        session: Optional[str] = Cookie(None),
        request: Request = None,
    ):
        """Handle OAuth callback from cTrader."""
        from fastapi.responses import RedirectResponse

        # Validate the code parameter. `state` is deliberately NOT required:
        # cTrader's authorize endpoint does not echo the state parameter back
        # on redirect -- the callback arrives as `?code=...` and nothing else
        # (their docs describe only `code` being appended). When state IS
        # present it is validated strictly; when absent, the consume below
        # falls back to the pending nonce bound to this session.
        if not code:
            logger.warning("OAuth callback missing authorization code")
            raise HTTPException(status_code=400, detail="Missing authorization code")

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
        #   - it belongs to THIS session                    (session digest match)
        #   - it has not expired                            (created_at within TTL)
        # `session` is guaranteed non-empty here: require_user already
        # rejected the request otherwise. The org is resolved from the
        # consumed row, not the caller's membership: a logged-in user can
        # only complete a callback for a state bound to their own session
        # cookie, which in practice means they initiated the connect flow.
        try:
            now = datetime.now(timezone.utc)
            if state:
                result = conn.execute(
                    """
                    UPDATE oauth_states SET consumed_at = %s
                    WHERE state_hash = %s
                      AND consumed_at IS NULL
                      AND session = %s
                      AND created_at > %s - make_interval(secs => %s)
                    RETURNING state_hash, org_id
                    """,
                    (
                        now,
                        _digest(state),
                        _digest(session or ""),
                        now,
                        STATE_TTL_SECONDS,
                    ),
                ).fetchone()
            else:
                # cTrader never echoes `state`, so the callback cannot present
                # the nonce. Consume the pending nonce bound to THIS session
                # instead: still single-use, still TTL-bounded, still
                # session-bound. The outer `consumed_at IS NULL` re-checks on
                # the row the subquery picked, so concurrent callbacks cannot
                # both consume it.
                #
                # BUT: if this session has started MORE THAN ONE connect flow
                # that's still pending (e.g. an admin opened connect for org A,
                # then org B, and only now completes consent), "most recent
                # pending nonce" is a guess -- and a wrong one silently lands
                # the grant in the wrong org. Count first, using the exact
                # same predicate the consume below uses, and refuse to guess
                # when there's more than one candidate.
                (pending_count,) = conn.execute(
                    """
                    SELECT COUNT(*) FROM oauth_states
                    WHERE consumed_at IS NULL
                      AND session = %s
                      AND created_at > %s - make_interval(secs => %s)
                    """,
                    (
                        _digest(session or ""),
                        now,
                        STATE_TTL_SECONDS,
                    ),
                ).fetchone()
                if pending_count > 1:
                    raise HTTPException(
                        status_code=409,
                        detail="Multiple pending connect flows for this session; "
                               "restart the connect flow for the organization you want",
                    )
                result = conn.execute(
                    """
                    UPDATE oauth_states SET consumed_at = %s
                    WHERE consumed_at IS NULL
                      AND state_hash = (
                        SELECT state_hash FROM oauth_states
                        WHERE consumed_at IS NULL
                          AND session = %s
                          AND created_at > %s - make_interval(secs => %s)
                        ORDER BY created_at DESC
                        LIMIT 1
                      )
                    RETURNING state_hash, org_id
                    """,
                    (
                        now,
                        _digest(session or ""),
                        now,
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

            org_id = result[1]
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
                INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at, scope, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (org_id, access_token_enc, refresh_token_enc, now, expires_at, "trading", "active"),
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

        # Redirect to the starting org's accounts page
        redirect_url = f"/org/{org_id}/accounts?connected=1"
        if discover_failed:
            redirect_url += "&warning=discover_failed"

        return RedirectResponse(url=redirect_url, status_code=307)

    return router
