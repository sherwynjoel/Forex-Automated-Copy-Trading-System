"""WebSocket handlers and event broadcaster."""
import asyncio
import logging

import psycopg
from psycopg import AsyncConnection
import psycopg.errors
from fastapi import APIRouter
from fastapi.websockets import WebSocket
from starlette.websockets import WebSocketState

from .auth import user_id_from_session_cookie
from .config import ApiConfig

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Manages WebSocket connections and broadcasts events from Postgres LISTEN/NOTIFY.

    Connections are keyed by org_id so each org's sockets only ever receive
    that org's events.
    """

    def __init__(self):
        # ws -> user_id, per org. Tracking the user per socket lets us close
        # a specific member's sockets on membership revocation (see
        # close_for) without touching the rest of the org's connections.
        self.connections: dict[int, dict[WebSocket, int]] = {}
        self.listener_task = None
        self.listener_connection: AsyncConnection = None
        self.query_connection: AsyncConnection = None
        self.dsn = None
        # Set True once LISTEN events has actually been registered with Postgres.
        # Lets callers (e.g. tests) know it's safe to INSERT without racing the
        # listener's startup (asyncio.create_task only *schedules* the coroutine;
        # it does not wait for LISTEN to be issued).
        self.listening = False

    async def connect(self, ws: WebSocket, org_id: int, user_id: int):
        """Register a WebSocket connection under its org, accepting it first
        if the caller hasn't already done so (idempotent: the endpoint may
        need to accept early -- see websocket_endpoint -- so a real WS close
        frame, rather than a pre-accept HTTP rejection, carries a custom
        close code back to the client)."""
        if ws.application_state != WebSocketState.CONNECTED:
            await ws.accept()
        self.connections.setdefault(org_id, {})[ws] = user_id

    def disconnect(self, ws: WebSocket, org_id: int):
        """Remove a WebSocket connection from its org."""
        self.connections.get(org_id, {}).pop(ws, None)

    async def broadcast(self, org_id: int | None, message: dict):
        """Send to the org's sockets only. org_id None (infrastructure
        events) is delivered to no one."""
        if org_id is None:
            return
        for ws in list(self.connections.get(org_id, {}).keys()):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                self.disconnect(ws, org_id)

    async def close_for(self, org_id: int, user_id: int):
        """Close and discard every socket this user has open in this org
        (e.g. their membership was just revoked). Other members' sockets in
        the same org are untouched."""
        targets = [ws for ws, uid in self.connections.get(org_id, {}).items()
                   if uid == user_id]
        for ws in targets:
            try:
                await ws.close(code=4404, reason="Membership revoked")
            except Exception as e:
                logger.warning(f"Failed to close socket for revoked member: {e}")
            self.connections.get(org_id, {}).pop(ws, None)

    async def close_org(self, org_id: int):
        """Close and discard every socket open for this org (e.g. the org
        was just deleted)."""
        targets = list(self.connections.get(org_id, {}).keys())
        for ws in targets:
            try:
                await ws.close(code=4404, reason="Organization deleted")
            except Exception as e:
                logger.warning(f"Failed to close socket for deleted org: {e}")
        self.connections.pop(org_id, None)

    async def start_listener(self, dsn: str):
        """Start listening for Postgres events and broadcast them."""
        try:
            # Create dedicated async connection for listening
            self.listener_connection = await AsyncConnection.connect(dsn, autocommit=True)
            logger.info("Event listener connection established")

            # Start listening on the events channel
            async with self.listener_connection.cursor() as cur:
                await cur.execute("LISTEN events")

            # A SEPARATE connection for fetching event rows. `notifies()` below
            # holds the listener connection's internal lock for the lifetime of
            # the generator (including while our loop body runs between yields),
            # so issuing a query on that same connection from inside the loop
            # would deadlock waiting for a lock the generator itself is holding.
            self.query_connection = await AsyncConnection.connect(dsn, autocommit=True)

            self.listening = True
            logger.info("Started listening on events channel")

            # Listen for notifications
            async for notify in self.listener_connection.notifies():
                try:
                    event_id = int(notify.payload)
                    logger.debug(f"Received event notification: {event_id}")

                    # Fetch the event details on the separate query connection.
                    async with self.query_connection.cursor() as cur:
                        await cur.execute(
                            """SELECT id, ts, account_id, category, severity, latency_ms,
                                      payload, org_id
                               FROM events WHERE id = %s""",
                            (event_id,)
                        )
                        row = await cur.fetchone()

                    if row:
                        event = {
                            "id": row[0],
                            "ts": row[1].isoformat() if hasattr(row[1], 'isoformat') else str(row[1]),
                            "account_id": row[2],
                            "category": row[3],
                            "severity": row[4],
                            "latency_ms": row[5],
                            "payload": row[6],
                            "org_id": row[7],
                        }
                        logger.debug(f"Broadcasting event: {event['id']}")
                        await self.broadcast(row[7], event)
                except Exception as e:
                    logger.error(f"Error processing event: {e}")

        except psycopg.errors.OperationalError as e:
            logger.error(f"Listener connection error: {e}")
        except asyncio.CancelledError:
            logger.info("Listener task cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in listener: {e}")
        finally:
            # Clean up the listener and query connections
            self.listening = False
            if self.query_connection:
                await self.query_connection.close()
                self.query_connection = None
            if self.listener_connection:
                await self.listener_connection.close()
                self.listener_connection = None
            self.listener_task = None

    async def stop_listener(self):
        """Stop the listener task and clean up."""
        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
            self.listener_task = None


# Global broadcaster instance
broadcaster = EventBroadcaster()


def create_ws_router() -> APIRouter:
    """Create router for WebSocket endpoint."""
    router = APIRouter(tags=["websocket"])

    @router.websocket("/api/ws")
    async def websocket_endpoint(ws: WebSocket):
        """Org-scoped event stream. Auth: session cookie; membership in the
        org_id query parameter is required.
        """
        cfg = ApiConfig.from_env()
        session = ws.cookies.get("session")
        user_id = user_id_from_session_cookie(session, cfg) if session else None
        if user_id is None:
            await ws.close(code=4401, reason="Unauthorized")
            return

        raw_org = ws.query_params.get("org_id")
        try:
            org_id = int(raw_org)
        except (TypeError, ValueError):
            await ws.close(code=4400, reason="org_id required")
            return

        with psycopg.connect(cfg.postgres_dsn, autocommit=True) as conn:
            member = conn.execute(
                "SELECT 1 FROM org_memberships WHERE org_id = %s AND user_id = %s",
                (org_id, user_id),
            ).fetchone()

        # Accept before the membership check fails closed: uvicorn collapses
        # any "websocket.close" sent *before* "websocket.accept" into a bare
        # HTTP-level rejection (403), discarding the ASGI close code -- so a
        # pre-accept close(4404) would reach real clients as an opaque
        # handshake failure, not a WS close frame carrying 4404. Auth
        # (4401) and org_id parsing (4400) are cheap, pre-DB checks where
        # that HTTP-level rejection is fine; membership is denied post-accept
        # so the close code is preserved end to end.
        if not member:
            await ws.accept()
            await ws.close(code=4404, reason="Not found")
            return

        await broadcaster.connect(ws, org_id, user_id)
        try:
            while True:
                await ws.receive_text()
        except Exception as e:
            logger.debug(f"WebSocket error: {e}")
        finally:
            broadcaster.disconnect(ws, org_id)

    return router
