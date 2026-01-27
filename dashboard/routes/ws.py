"""WebSocket endpoint for real-time dashboard updates.

This module provides WebSocket support for streaming real-time updates
to connected clients, including worker status changes, bead updates,
and log entries.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from weakref import WeakSet

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class MessageType(str, Enum):
    """WebSocket message types."""

    # System messages
    CONNECTED = "connected"
    HEARTBEAT = "heartbeat"
    ERROR = "error"

    # Worker updates
    WORKER_SPAWNED = "worker.spawned"
    WORKER_STOPPED = "worker.stopped"
    WORKER_CRASHED = "worker.crashed"
    WORKER_STATUS = "worker.status"

    # Bead updates
    BEAD_CREATED = "bead.created"
    BEAD_UPDATED = "bead.updated"
    BEAD_CLOSED = "bead.closed"

    # Agent session updates
    AGENT_SESSION_START = "agent.session_start"
    AGENT_SESSION_END = "agent.session_end"
    AGENT_CLAIM = "agent.claim"

    # Daemon updates
    DAEMON_STATUS = "daemon.status"


@dataclass
class WebSocketMessage:
    """Message sent over WebSocket."""

    type: MessageType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp,
        })


class ConnectionManager:
    """Manages WebSocket connections and broadcasting.

    Thread-safe connection manager using WeakSet to prevent memory leaks
    from disconnected clients.
    """

    def __init__(self) -> None:
        self._connections: WeakSet[WebSocket] = WeakSet()
        self._active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self._active.add(websocket)
        logger.info("WebSocket connected: %s", id(websocket))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            self._active.discard(websocket)
        logger.info("WebSocket disconnected: %s", id(websocket))

    async def broadcast(self, message: WebSocketMessage) -> None:
        """Send a message to all connected clients.

        Failed sends are silently ignored and connections cleaned up.
        """
        async with self._lock:
            dead_connections: set[WebSocket] = set()

            for connection in self._active:
                try:
                    await connection.send_text(message.to_json())
                except Exception as e:
                    logger.debug("Failed to send to WebSocket: %s", e)
                    dead_connections.add(connection)

            # Clean up dead connections
            self._active -= dead_connections

    async def send_personal(self, websocket: WebSocket, message: WebSocketMessage) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send_text(message.to_json())
        except Exception as e:
            logger.debug("Failed to send personal message: %s", e)

    @property
    def connection_count(self) -> int:
        """Return the number of active connections."""
        return len(self._active)


# Global connection manager instance
manager = ConnectionManager()


@asynccontextmanager
async def websocket_connection(websocket: WebSocket) -> AsyncGenerator[ConnectionManager, None]:
    """Context manager for WebSocket connection lifecycle."""
    await manager.connect(websocket)
    try:
        yield manager
    finally:
        await manager.disconnect(websocket)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time updates.

    Clients connect to this endpoint to receive real-time updates about:
    - Worker status changes (spawned, stopped, crashed)
    - Bead updates (created, updated, closed)
    - Agent session events (start, end, claims)
    - Daemon status changes

    The connection starts with a 'connected' message and maintains
    heartbeats every 30 seconds.

    Message format:
    {
        "type": "worker.spawned",
        "data": {...},
        "timestamp": "2024-01-15T10:30:00Z"
    }
    """
    async with websocket_connection(websocket) as conn_manager:
        # Send connected message
        await conn_manager.send_personal(
            websocket,
            WebSocketMessage(
                type=MessageType.CONNECTED,
                data={"message": "Connected to MAB dashboard"},
            ),
        )

        # Start heartbeat task
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket))

        try:
            # Main receive loop - handle incoming messages
            while True:
                try:
                    # Wait for client messages (subscriptions, commands, etc.)
                    data = await websocket.receive_text()
                    await _handle_client_message(websocket, data, conn_manager)
                except WebSocketDisconnect:
                    logger.info("WebSocket client disconnected normally")
                    break
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


async def _heartbeat_loop(websocket: WebSocket) -> None:
    """Send periodic heartbeats to keep connection alive."""
    while True:
        try:
            await asyncio.sleep(30)  # Heartbeat every 30 seconds
            await manager.send_personal(
                websocket,
                WebSocketMessage(
                    type=MessageType.HEARTBEAT,
                    data={"connections": manager.connection_count},
                ),
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Heartbeat error: %s", e)
            break


async def _handle_client_message(
    websocket: WebSocket,
    data: str,
    conn_manager: ConnectionManager,
) -> None:
    """Handle incoming messages from WebSocket clients.

    Supported commands:
    - {"type": "ping"} - Returns a pong
    - {"type": "subscribe", "channels": [...]} - Subscribe to channels
    - {"type": "unsubscribe", "channels": [...]} - Unsubscribe from channels
    """
    try:
        message = json.loads(data)
        msg_type = message.get("type")

        if msg_type == "ping":
            await conn_manager.send_personal(
                websocket,
                WebSocketMessage(
                    type=MessageType.HEARTBEAT,
                    data={"pong": True},
                ),
            )
        elif msg_type == "subscribe":
            channels = message.get("channels", [])
            logger.debug("Client subscribing to: %s", channels)
            # Subscription logic could be extended here
        elif msg_type == "unsubscribe":
            channels = message.get("channels", [])
            logger.debug("Client unsubscribing from: %s", channels)
        else:
            logger.debug("Unknown message type: %s", msg_type)

    except json.JSONDecodeError:
        await conn_manager.send_personal(
            websocket,
            WebSocketMessage(
                type=MessageType.ERROR,
                data={"message": "Invalid JSON message"},
            ),
        )


# Broadcast helper functions for use by other parts of the application


async def broadcast_worker_spawned(worker_data: dict[str, Any]) -> None:
    """Broadcast worker spawned event to all clients."""
    await manager.broadcast(
        WebSocketMessage(type=MessageType.WORKER_SPAWNED, data=worker_data)
    )


async def broadcast_worker_stopped(worker_id: str, reason: str = "") -> None:
    """Broadcast worker stopped event to all clients."""
    await manager.broadcast(
        WebSocketMessage(
            type=MessageType.WORKER_STOPPED,
            data={"worker_id": worker_id, "reason": reason},
        )
    )


async def broadcast_worker_crashed(worker_id: str, crash_count: int) -> None:
    """Broadcast worker crashed event to all clients."""
    await manager.broadcast(
        WebSocketMessage(
            type=MessageType.WORKER_CRASHED,
            data={"worker_id": worker_id, "crash_count": crash_count},
        )
    )


async def broadcast_bead_updated(bead_data: dict[str, Any]) -> None:
    """Broadcast bead update event to all clients."""
    await manager.broadcast(
        WebSocketMessage(type=MessageType.BEAD_UPDATED, data=bead_data)
    )


async def broadcast_daemon_status(status_data: dict[str, Any]) -> None:
    """Broadcast daemon status update to all clients."""
    await manager.broadcast(
        WebSocketMessage(type=MessageType.DAEMON_STATUS, data=status_data)
    )
