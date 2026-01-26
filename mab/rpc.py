"""MAB RPC - Remote procedure call layer for daemon communication.

This module implements the RPC protocol for communication between CLI commands
and the background daemon, including:
- Unix socket transport
- Length-prefixed JSON message framing
- Request/response pattern with timeouts
- Connection pooling for performance
- Error handling for daemon not running
"""

import asyncio
import json
import os
import socket
import struct
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Coroutine


class RPCErrorCode(IntEnum):
    """Standard RPC error codes (JSON-RPC 2.0 compatible)."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Custom error codes (-32000 to -32099)
    DAEMON_NOT_RUNNING = -32000
    CONNECTION_TIMEOUT = -32001
    REQUEST_TIMEOUT = -32002
    DAEMON_SHUTTING_DOWN = -32003


class RPCError(Exception):
    """RPC error with code and message."""

    def __init__(self, code: RPCErrorCode, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"RPC Error {code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            result["data"] = self.data
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RPCError":
        """Create RPCError from dictionary."""
        return cls(
            code=RPCErrorCode(data["code"]),
            message=data["message"],
            data=data.get("data"),
        )


class DaemonNotRunningError(RPCError):
    """Raised when daemon is not running or socket is unavailable."""

    def __init__(self, message: str = "Daemon is not running") -> None:
        super().__init__(RPCErrorCode.DAEMON_NOT_RUNNING, message)


class ConnectionTimeoutError(RPCError):
    """Raised when connection to daemon times out."""

    def __init__(self, timeout: float) -> None:
        super().__init__(
            RPCErrorCode.CONNECTION_TIMEOUT,
            f"Connection to daemon timed out after {timeout}s",
        )


class RequestTimeoutError(RPCError):
    """Raised when request to daemon times out."""

    def __init__(self, timeout: float, method: str) -> None:
        super().__init__(
            RPCErrorCode.REQUEST_TIMEOUT,
            f"Request '{method}' timed out after {timeout}s",
        )


@dataclass
class RPCRequest:
    """RPC request message."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }

    def to_bytes(self) -> bytes:
        """Serialize to length-prefixed JSON bytes."""
        payload = json.dumps(self.to_dict()).encode("utf-8")
        return struct.pack(">I", len(payload)) + payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RPCRequest":
        """Create RPCRequest from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            method=data["method"],
            params=data.get("params", {}),
        )


@dataclass
class RPCResponse:
    """RPC response message."""

    id: str
    result: Any = None
    error: RPCError | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        response: dict[str, Any] = {"id": self.id}
        if self.error is not None:
            response["error"] = self.error.to_dict()
        else:
            response["result"] = self.result
        return response

    def to_bytes(self) -> bytes:
        """Serialize to length-prefixed JSON bytes."""
        payload = json.dumps(self.to_dict()).encode("utf-8")
        return struct.pack(">I", len(payload)) + payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RPCResponse":
        """Create RPCResponse from dictionary."""
        error = None
        if "error" in data:
            error = RPCError.from_dict(data["error"])
        return cls(
            id=data["id"],
            result=data.get("result"),
            error=error,
        )

    @classmethod
    def success(cls, request_id: str, result: Any) -> "RPCResponse":
        """Create a successful response."""
        return cls(id=request_id, result=result)

    @classmethod
    def failure(cls, request_id: str, error: RPCError) -> "RPCResponse":
        """Create an error response."""
        return cls(id=request_id, error=error)


# Type alias for RPC method handlers
RPCHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]


class RPCClient:
    """Client for making RPC calls to the daemon.

    Usage:
        client = RPCClient()

        # Synchronous call (blocking)
        result = client.call("daemon.status", {})

        # With custom timeout
        result = client.call("worker.spawn", {"role": "dev"}, timeout=60.0)
    """

    DEFAULT_TIMEOUT = 30.0

    def __init__(self, mab_dir: Path | None = None) -> None:
        """Initialize RPC client.

        Args:
            mab_dir: Path to .mab directory. Defaults to .mab in current dir.
        """
        self.mab_dir = mab_dir or Path(".mab")
        self.socket_path = self.mab_dir / "mab.sock"
        self._connection_pool: list[socket.socket] = []
        self._pool_size = 3

    def _get_connection(self, timeout: float) -> socket.socket:
        """Get a connection from the pool or create a new one.

        Args:
            timeout: Connection timeout in seconds.

        Returns:
            Connected socket.

        Raises:
            DaemonNotRunningError: If socket doesn't exist.
            ConnectionTimeoutError: If connection times out.
        """
        # Check if socket exists
        if not self.socket_path.exists():
            raise DaemonNotRunningError("Daemon socket not found")

        # Try to reuse from pool
        while self._connection_pool:
            sock = self._connection_pool.pop()
            try:
                # Test if connection is still alive
                sock.setblocking(False)
                try:
                    sock.recv(1, socket.MSG_PEEK)
                except BlockingIOError:
                    # No data available, connection still good
                    sock.setblocking(True)
                    sock.settimeout(timeout)
                    return sock
                except (ConnectionResetError, BrokenPipeError):
                    # Connection dead
                    sock.close()
                    continue
            except Exception:
                sock.close()
                continue

        # Create new connection
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        try:
            sock.connect(str(self.socket_path))
            return sock
        except FileNotFoundError:
            sock.close()
            raise DaemonNotRunningError("Daemon socket not found")
        except ConnectionRefusedError:
            sock.close()
            raise DaemonNotRunningError("Daemon refused connection")
        except socket.timeout:
            sock.close()
            raise ConnectionTimeoutError(timeout)

    def _return_connection(self, sock: socket.socket) -> None:
        """Return a connection to the pool.

        Args:
            sock: Socket to return.
        """
        if len(self._connection_pool) < self._pool_size:
            self._connection_pool.append(sock)
        else:
            sock.close()

    def _send_request(self, sock: socket.socket, request: RPCRequest) -> None:
        """Send a request over the socket.

        Args:
            sock: Connected socket.
            request: Request to send.
        """
        sock.sendall(request.to_bytes())

    def _receive_response(self, sock: socket.socket, timeout: float) -> RPCResponse:
        """Receive a response from the socket.

        Args:
            sock: Connected socket.
            timeout: Response timeout in seconds.

        Returns:
            Parsed response.

        Raises:
            RequestTimeoutError: If response times out.
        """
        sock.settimeout(timeout)

        try:
            # Read length prefix (4 bytes, big-endian)
            length_data = self._recv_exactly(sock, 4)
            if len(length_data) < 4:
                raise RPCError(
                    RPCErrorCode.INTERNAL_ERROR,
                    "Connection closed while reading response length",
                )

            length = struct.unpack(">I", length_data)[0]

            # Read payload
            payload = self._recv_exactly(sock, length)
            if len(payload) < length:
                raise RPCError(
                    RPCErrorCode.INTERNAL_ERROR,
                    "Connection closed while reading response",
                )

            # Parse response
            data = json.loads(payload.decode("utf-8"))
            return RPCResponse.from_dict(data)

        except socket.timeout:
            raise RequestTimeoutError(timeout, "unknown")
        except json.JSONDecodeError as e:
            raise RPCError(RPCErrorCode.PARSE_ERROR, f"Invalid JSON response: {e}")

    def _recv_exactly(self, sock: socket.socket, n: int) -> bytes:
        """Receive exactly n bytes from socket.

        Args:
            sock: Socket to read from.
            n: Number of bytes to read.

        Returns:
            Bytes read (may be less than n if connection closed).
        """
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Make a synchronous RPC call to the daemon.

        Args:
            method: RPC method name.
            params: Method parameters.
            timeout: Request timeout in seconds.

        Returns:
            Method result.

        Raises:
            RPCError: On any RPC error.
            DaemonNotRunningError: If daemon is not running.
        """
        if params is None:
            params = {}
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        request = RPCRequest(method=method, params=params)

        sock = self._get_connection(timeout)
        try:
            self._send_request(sock, request)
            response = self._receive_response(sock, timeout)

            if response.error is not None:
                raise response.error

            self._return_connection(sock)
            return response.result

        except (BrokenPipeError, ConnectionResetError):
            sock.close()
            raise DaemonNotRunningError("Connection to daemon lost")
        except RPCError:
            sock.close()
            raise
        except Exception as e:
            sock.close()
            raise RPCError(RPCErrorCode.INTERNAL_ERROR, str(e))

    def close(self) -> None:
        """Close all connections in the pool."""
        for sock in self._connection_pool:
            try:
                sock.close()
            except Exception:
                pass
        self._connection_pool.clear()

    def __enter__(self) -> "RPCClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class RPCServer:
    """Async RPC server for handling daemon requests.

    The server runs as part of the daemon's asyncio event loop and handles
    incoming requests on a Unix socket.

    Usage:
        server = RPCServer()

        # Register handlers
        server.register("daemon.status", handle_status)
        server.register("worker.spawn", handle_spawn)

        # Start server
        await server.start()

        # Server runs until stop() is called
        await server.wait_closed()
    """

    def __init__(self, mab_dir: Path | None = None) -> None:
        """Initialize RPC server.

        Args:
            mab_dir: Path to .mab directory. Defaults to .mab in current dir.
        """
        self.mab_dir = mab_dir or Path(".mab")
        self.socket_path = self.mab_dir / "mab.sock"
        self._handlers: dict[str, RPCHandler] = {}
        self._server: asyncio.Server | None = None
        self._shutting_down = False
        self._active_connections: set[asyncio.Task[None]] = set()

    def register(self, method: str, handler: RPCHandler) -> None:
        """Register an RPC method handler.

        Args:
            method: Method name (e.g., "daemon.status").
            handler: Async function to handle the method.
        """
        self._handlers[method] = handler

    async def start(self) -> None:
        """Start the RPC server.

        Creates the Unix socket and begins accepting connections.

        Raises:
            OSError: If socket cannot be created.
        """
        # Ensure directory exists
        self.mab_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Create Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
        )

        # Set socket permissions (owner read/write only)
        os.chmod(self.socket_path, 0o600)

    async def stop(self, graceful: bool = True, timeout: float = 5.0) -> None:
        """Stop the RPC server.

        Args:
            graceful: If True, wait for active requests to complete.
            timeout: Seconds to wait for graceful shutdown.
        """
        self._shutting_down = True

        if self._server is not None:
            # Stop accepting new connections
            self._server.close()

            if graceful and self._active_connections:
                # Wait for active connections to finish
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self._active_connections, return_exceptions=True),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    # Cancel remaining connections
                    for task in self._active_connections:
                        task.cancel()

            await self._server.wait_closed()

        # Remove socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

    async def wait_closed(self) -> None:
        """Wait until the server is closed."""
        if self._server is not None:
            await self._server.wait_closed()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a client connection.

        Args:
            reader: Stream reader for incoming data.
            writer: Stream writer for outgoing data.
        """
        task = asyncio.current_task()
        if task is not None:
            self._active_connections.add(task)

        try:
            while not self._shutting_down:
                try:
                    # Read length prefix
                    length_data = await asyncio.wait_for(
                        reader.readexactly(4),
                        timeout=60.0,  # Idle connection timeout
                    )
                except asyncio.IncompleteReadError:
                    # Client disconnected
                    break
                except asyncio.TimeoutError:
                    # Idle timeout
                    break

                length = struct.unpack(">I", length_data)[0]

                # Sanity check on message size (max 10MB)
                if length > 10 * 1024 * 1024:
                    await self._send_error(
                        writer,
                        "unknown",
                        RPCError(RPCErrorCode.INVALID_REQUEST, "Message too large"),
                    )
                    break

                # Read payload
                try:
                    payload = await asyncio.wait_for(
                        reader.readexactly(length),
                        timeout=30.0,
                    )
                except asyncio.IncompleteReadError:
                    break
                except asyncio.TimeoutError:
                    break

                # Parse and handle request
                await self._handle_request(payload, writer)

        except Exception:
            pass  # Connection handling errors are not propagated
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            if task is not None:
                self._active_connections.discard(task)

    async def _handle_request(
        self,
        payload: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single RPC request.

        Args:
            payload: JSON request payload.
            writer: Stream writer for response.
        """
        request_id = "unknown"

        try:
            # Parse request
            data = json.loads(payload.decode("utf-8"))
            request = RPCRequest.from_dict(data)
            request_id = request.id

            # Check if shutting down
            if self._shutting_down:
                await self._send_error(
                    writer,
                    request_id,
                    RPCError(RPCErrorCode.DAEMON_SHUTTING_DOWN, "Daemon is shutting down"),
                )
                return

            # Find handler
            handler = self._handlers.get(request.method)
            if handler is None:
                await self._send_error(
                    writer,
                    request_id,
                    RPCError(RPCErrorCode.METHOD_NOT_FOUND, f"Method not found: {request.method}"),
                )
                return

            # Execute handler
            try:
                result = await handler(request.params)
                await self._send_response(writer, request_id, result)
            except RPCError as e:
                await self._send_error(writer, request_id, e)
            except Exception as e:
                await self._send_error(
                    writer,
                    request_id,
                    RPCError(RPCErrorCode.INTERNAL_ERROR, str(e)),
                )

        except json.JSONDecodeError as e:
            await self._send_error(
                writer,
                request_id,
                RPCError(RPCErrorCode.PARSE_ERROR, f"Invalid JSON: {e}"),
            )
        except KeyError as e:
            await self._send_error(
                writer,
                request_id,
                RPCError(RPCErrorCode.INVALID_REQUEST, f"Missing required field: {e}"),
            )

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        request_id: str,
        result: Any,
    ) -> None:
        """Send a successful response.

        Args:
            writer: Stream writer.
            request_id: Request ID to match.
            result: Result data.
        """
        response = RPCResponse.success(request_id, result)
        writer.write(response.to_bytes())
        await writer.drain()

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: str,
        error: RPCError,
    ) -> None:
        """Send an error response.

        Args:
            writer: Stream writer.
            request_id: Request ID to match.
            error: Error to send.
        """
        response = RPCResponse.failure(request_id, error)
        writer.write(response.to_bytes())
        await writer.drain()


def get_default_client() -> RPCClient:
    """Get an RPC client with default configuration.

    Returns:
        RPCClient configured for current directory.
    """
    return RPCClient()
