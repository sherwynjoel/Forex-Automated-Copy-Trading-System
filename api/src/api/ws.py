"""WebSocket handlers and event broadcaster."""
import asyncio
import logging
from typing import Set

from psycopg import AsyncConnection
import psycopg.errors
from fastapi import APIRouter
from fastapi.websockets import WebSocket
from itsdangerous import SignatureExpired, BadSignature

from .auth import get_session_serializer
from .config import ApiConfig

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Manages WebSocket connections and broadcasts events from Postgres LISTEN/NOTIFY."""

    def __init__(self):
        self.connections: Set[WebSocket] = set()
        self.listener_task = None
        self.listener_connection: AsyncConnection = None
        self.dsn = None

    async def connect(self, ws: WebSocket):
        """Add a WebSocket connection."""
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket):
        """Remove a WebSocket connection."""
        self.connections.discard(ws)

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        for ws in list(self.connections):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                self.disconnect(ws)

    async def start_listener(self, dsn: str):
        """Start listening for Postgres events and broadcast them."""
        try:
            # Create dedicated async connection for listening
            self.listener_connection = await AsyncConnection.connect(dsn, autocommit=True)
            logger.info("Event listener connection established")

            # Start listening on the events channel
            async with self.listener_connection.cursor() as cur:
                await cur.execute("LISTEN events")

            logger.info("Started listening on events channel")

            # Listen for notifications
            async for notify in self.listener_connection.notifies():
                try:
                    event_id = int(notify.payload)
                    logger.debug(f"Received event notification: {event_id}")

                    # Fetch the event details
                    async with self.listener_connection.cursor() as cur:
                        await cur.execute(
                            """SELECT id, ts, account_id, category, severity, latency_ms, payload
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
                        }
                        logger.debug(f"Broadcasting event: {event['id']}")
                        await self.broadcast(event)
                except Exception as e:
                    logger.error(f"Error processing event: {e}")

        except psycopg.errors.OperationalError as e:
            logger.error(f"Listener connection error: {e}")
        except asyncio.CancelledError:
            logger.info("Listener task cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in listener: {e}")
        finally:
            # Clean up the listener connection
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
        """WebSocket endpoint for real-time event streaming.

        Requires authentication via session cookie.
        """
        # Check authentication
        cfg = ApiConfig.from_env()
        authenticated = False

        # Extract session cookie from headers
        cookies = ws.cookies
        session = cookies.get("session")

        if session:
            serializer = get_session_serializer(cfg)
            try:
                # max_age=12h = 43200 seconds
                data = serializer.loads(session, max_age=43200)
                # Verify the session data contains authenticated flag
                if data.get("authenticated"):
                    authenticated = True
            except (SignatureExpired, BadSignature):
                pass

        # Reject before accepting the connection
        if not authenticated:
            await ws.close(code=4401, reason="Unauthorized")
            return

        # Accept the connection
        await broadcaster.connect(ws)

        try:
            # Keep connection open and listen for client messages (if any)
            while True:
                # Just receive to detect disconnection
                await ws.receive_text()
        except Exception as e:
            logger.debug(f"WebSocket error: {e}")
        finally:
            broadcaster.disconnect(ws)

    return router
