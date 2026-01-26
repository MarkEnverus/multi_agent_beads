"""Tests for MAB RPC communication layer."""

import asyncio
import json
import struct
import tempfile
from pathlib import Path

import pytest

from mab.rpc import (
    ConnectionTimeoutError,
    DaemonNotRunningError,
    RequestTimeoutError,
    RPCClient,
    RPCError,
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
    RPCServer,
)


@pytest.fixture
def short_tmp_path():
    """Create a short temporary directory for Unix socket tests.

    macOS has a ~104 byte limit on Unix socket paths. pytest's tmp_path
    can exceed this, so we create a shorter path in /tmp.
    """
    with tempfile.TemporaryDirectory(prefix="mab_", dir="/tmp") as tmpdir:
        yield Path(tmpdir)


class TestRPCError:
    """Tests for RPCError class."""

    def test_error_to_dict(self) -> None:
        """Test RPCError converts to dict correctly."""
        error = RPCError(RPCErrorCode.INVALID_PARAMS, "Missing param", {"param": "role"})
        result = error.to_dict()

        assert result["code"] == -32602
        assert result["message"] == "Missing param"
        assert result["data"] == {"param": "role"}

    def test_error_to_dict_no_data(self) -> None:
        """Test RPCError without data converts to dict correctly."""
        error = RPCError(RPCErrorCode.INTERNAL_ERROR, "Something failed")
        result = error.to_dict()

        assert result["code"] == -32603
        assert result["message"] == "Something failed"
        assert "data" not in result

    def test_error_from_dict(self) -> None:
        """Test RPCError from dict."""
        data = {"code": -32601, "message": "Method not found", "data": {"method": "foo"}}
        error = RPCError.from_dict(data)

        assert error.code == RPCErrorCode.METHOD_NOT_FOUND
        assert error.message == "Method not found"
        assert error.data == {"method": "foo"}

    def test_daemon_not_running_error(self) -> None:
        """Test DaemonNotRunningError."""
        error = DaemonNotRunningError()
        assert error.code == RPCErrorCode.DAEMON_NOT_RUNNING
        assert "not running" in error.message

    def test_connection_timeout_error(self) -> None:
        """Test ConnectionTimeoutError."""
        error = ConnectionTimeoutError(5.0)
        assert error.code == RPCErrorCode.CONNECTION_TIMEOUT
        assert "5.0s" in error.message

    def test_request_timeout_error(self) -> None:
        """Test RequestTimeoutError."""
        error = RequestTimeoutError(30.0, "daemon.status")
        assert error.code == RPCErrorCode.REQUEST_TIMEOUT
        assert "30.0s" in error.message
        assert "daemon.status" in error.message


class TestRPCRequest:
    """Tests for RPCRequest class."""

    def test_request_to_dict(self) -> None:
        """Test RPCRequest converts to dict correctly."""
        request = RPCRequest(
            id="test-123",
            method="daemon.status",
            params={"verbose": True},
        )
        result = request.to_dict()

        assert result["id"] == "test-123"
        assert result["method"] == "daemon.status"
        assert result["params"] == {"verbose": True}

    def test_request_default_id(self) -> None:
        """Test RPCRequest generates UUID for id."""
        request = RPCRequest(method="worker.list")
        assert request.id is not None
        assert len(request.id) == 36  # UUID format

    def test_request_default_params(self) -> None:
        """Test RPCRequest has empty params by default."""
        request = RPCRequest(method="worker.list")
        assert request.params == {}

    def test_request_from_dict(self) -> None:
        """Test RPCRequest from dict."""
        data = {
            "id": "abc-123",
            "method": "worker.spawn",
            "params": {"role": "dev"},
        }
        request = RPCRequest.from_dict(data)

        assert request.id == "abc-123"
        assert request.method == "worker.spawn"
        assert request.params == {"role": "dev"}

    def test_request_to_bytes(self) -> None:
        """Test RPCRequest serializes to length-prefixed bytes."""
        request = RPCRequest(id="test", method="foo", params={})
        data = request.to_bytes()

        # First 4 bytes are length (big-endian)
        length = struct.unpack(">I", data[:4])[0]
        payload = data[4:]

        assert len(payload) == length
        parsed = json.loads(payload)
        assert parsed["id"] == "test"
        assert parsed["method"] == "foo"


class TestRPCResponse:
    """Tests for RPCResponse class."""

    def test_response_success_to_dict(self) -> None:
        """Test successful RPCResponse converts to dict."""
        response = RPCResponse(id="test-123", result={"status": "ok"})
        result = response.to_dict()

        assert result["id"] == "test-123"
        assert result["result"] == {"status": "ok"}
        assert "error" not in result

    def test_response_error_to_dict(self) -> None:
        """Test error RPCResponse converts to dict."""
        error = RPCError(RPCErrorCode.INTERNAL_ERROR, "Failed")
        response = RPCResponse(id="test-123", error=error)
        result = response.to_dict()

        assert result["id"] == "test-123"
        assert result["error"]["code"] == -32603
        assert result["error"]["message"] == "Failed"
        assert "result" not in result

    def test_response_success_factory(self) -> None:
        """Test RPCResponse.success factory method."""
        response = RPCResponse.success("req-1", {"workers": []})

        assert response.id == "req-1"
        assert response.result == {"workers": []}
        assert response.error is None

    def test_response_failure_factory(self) -> None:
        """Test RPCResponse.failure factory method."""
        error = RPCError(RPCErrorCode.METHOD_NOT_FOUND, "Not found")
        response = RPCResponse.failure("req-1", error)

        assert response.id == "req-1"
        assert response.result is None
        assert response.error is not None
        assert response.error.code == RPCErrorCode.METHOD_NOT_FOUND

    def test_response_from_dict_success(self) -> None:
        """Test RPCResponse from dict with success."""
        data = {"id": "xyz", "result": {"count": 5}}
        response = RPCResponse.from_dict(data)

        assert response.id == "xyz"
        assert response.result == {"count": 5}
        assert response.error is None

    def test_response_from_dict_error(self) -> None:
        """Test RPCResponse from dict with error."""
        data = {
            "id": "xyz",
            "error": {"code": -32600, "message": "Bad request"},
        }
        response = RPCResponse.from_dict(data)

        assert response.id == "xyz"
        assert response.result is None
        assert response.error is not None
        assert response.error.code == RPCErrorCode.INVALID_REQUEST

    def test_response_to_bytes(self) -> None:
        """Test RPCResponse serializes to length-prefixed bytes."""
        response = RPCResponse.success("test", {"ok": True})
        data = response.to_bytes()

        # First 4 bytes are length (big-endian)
        length = struct.unpack(">I", data[:4])[0]
        payload = data[4:]

        assert len(payload) == length
        parsed = json.loads(payload)
        assert parsed["id"] == "test"
        assert parsed["result"] == {"ok": True}


class TestRPCClient:
    """Tests for RPCClient class."""

    def test_client_init_default_dir(self) -> None:
        """Test RPCClient uses default .mab directory."""
        client = RPCClient()
        assert client.mab_dir == Path(".mab")
        assert client.socket_path == Path(".mab/mab.sock")

    def test_client_init_custom_dir(self, tmp_path: Path) -> None:
        """Test RPCClient with custom directory."""
        mab_dir = tmp_path / ".mab"
        client = RPCClient(mab_dir=mab_dir)
        assert client.mab_dir == mab_dir
        assert client.socket_path == mab_dir / "mab.sock"

    def test_client_raises_when_socket_missing(self, tmp_path: Path) -> None:
        """Test RPCClient raises DaemonNotRunningError when socket doesn't exist."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        client = RPCClient(mab_dir=mab_dir)

        with pytest.raises(DaemonNotRunningError) as exc_info:
            client.call("daemon.status")

        assert "not found" in str(exc_info.value).lower()

    def test_client_context_manager(self, tmp_path: Path) -> None:
        """Test RPCClient as context manager."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()

        with RPCClient(mab_dir=mab_dir) as client:
            assert client is not None

        # Pool should be cleared after exit
        assert len(client._connection_pool) == 0


class TestRPCServer:
    """Tests for RPCServer class."""

    def test_server_init_default_dir(self) -> None:
        """Test RPCServer uses default .mab directory."""
        server = RPCServer()
        assert server.mab_dir == Path(".mab")
        assert server.socket_path == Path(".mab/mab.sock")

    def test_server_init_custom_dir(self, tmp_path: Path) -> None:
        """Test RPCServer with custom directory."""
        mab_dir = tmp_path / ".mab"
        server = RPCServer(mab_dir=mab_dir)
        assert server.mab_dir == mab_dir
        assert server.socket_path == mab_dir / "mab.sock"

    def test_server_register_handler(self) -> None:
        """Test registering an RPC handler."""
        server = RPCServer()

        async def handler(params: dict) -> dict:
            return {"ok": True}

        server.register("test.method", handler)
        assert "test.method" in server._handlers


class TestRPCIntegration:
    """Integration tests for RPC client and server."""

    @pytest.fixture
    async def server_with_dir(self, short_tmp_path: Path):
        """Create and start an RPC server, returning both server and mab_dir."""
        mab_dir = short_tmp_path / ".mab"
        server = RPCServer(mab_dir=mab_dir)

        # Register test handlers
        async def echo_handler(params: dict) -> dict:
            return {"echo": params}

        async def error_handler(params: dict) -> dict:
            raise RPCError(RPCErrorCode.INTERNAL_ERROR, "Test error")

        async def slow_handler(params: dict) -> dict:
            await asyncio.sleep(params.get("delay", 5))
            return {"done": True}

        server.register("test.echo", echo_handler)
        server.register("test.error", error_handler)
        server.register("test.slow", slow_handler)

        await server.start()
        yield server, mab_dir
        await server.stop()

    @pytest.mark.asyncio
    async def test_client_server_echo(self, server_with_dir) -> None:
        """Test basic request/response flow."""
        server, mab_dir = server_with_dir
        client = RPCClient(mab_dir=mab_dir)

        # Run sync client in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: client.call("test.echo", {"message": "hello"})
        )
        assert result == {"echo": {"message": "hello"}}

        client.close()

    @pytest.mark.asyncio
    async def test_client_server_error(self, server_with_dir) -> None:
        """Test error handling."""
        server, mab_dir = server_with_dir
        client = RPCClient(mab_dir=mab_dir)

        # Run sync client in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()

        with pytest.raises(RPCError) as exc_info:
            await loop.run_in_executor(None, lambda: client.call("test.error"))

        assert exc_info.value.code == RPCErrorCode.INTERNAL_ERROR
        assert "Test error" in exc_info.value.message

        client.close()

    @pytest.mark.asyncio
    async def test_client_method_not_found(self, server_with_dir) -> None:
        """Test calling non-existent method."""
        server, mab_dir = server_with_dir
        client = RPCClient(mab_dir=mab_dir)

        # Run sync client in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()

        with pytest.raises(RPCError) as exc_info:
            await loop.run_in_executor(
                None, lambda: client.call("nonexistent.method")
            )

        assert exc_info.value.code == RPCErrorCode.METHOD_NOT_FOUND

        client.close()

    @pytest.mark.asyncio
    async def test_client_multiple_calls(self, server_with_dir) -> None:
        """Test multiple sequential calls."""
        server, mab_dir = server_with_dir
        client = RPCClient(mab_dir=mab_dir)

        # Run sync client in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()

        for i in range(5):
            result = await loop.run_in_executor(
                None, lambda i=i: client.call("test.echo", {"count": i})
            )
            assert result == {"echo": {"count": i}}

        client.close()

    @pytest.mark.asyncio
    async def test_server_socket_permissions(self, server_with_dir) -> None:
        """Test socket file has correct permissions."""
        server, mab_dir = server_with_dir
        socket_path = mab_dir / "mab.sock"

        assert socket_path.exists()
        # Check permissions (owner read/write only = 0o600)
        mode = socket_path.stat().st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.asyncio
    async def test_server_cleanup_on_stop(self, short_tmp_path: Path) -> None:
        """Test server removes socket file on stop."""
        mab_dir = short_tmp_path / ".mab"
        server = RPCServer(mab_dir=mab_dir)

        await server.start()
        assert (mab_dir / "mab.sock").exists()

        await server.stop()
        assert not (mab_dir / "mab.sock").exists()

    @pytest.mark.asyncio
    async def test_server_removes_stale_socket(self, short_tmp_path: Path) -> None:
        """Test server removes stale socket file on start."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()
        socket_path = mab_dir / "mab.sock"
        socket_path.touch()

        server = RPCServer(mab_dir=mab_dir)
        await server.start()

        # Socket should be recreated
        assert socket_path.exists()

        await server.stop()


class TestRPCProtocol:
    """Tests for RPC wire protocol details."""

    def test_length_prefix_format(self) -> None:
        """Test length prefix is 4 bytes big-endian."""
        request = RPCRequest(method="test", params={"a": 1})
        data = request.to_bytes()

        # Parse length prefix
        length = struct.unpack(">I", data[:4])[0]

        # Verify payload matches
        payload = data[4:]
        assert len(payload) == length

        # Verify JSON is valid
        parsed = json.loads(payload)
        assert parsed["method"] == "test"

    def test_large_message(self) -> None:
        """Test handling of larger messages."""
        large_data = {"data": "x" * 100000}
        request = RPCRequest(method="test", params=large_data)
        data = request.to_bytes()

        # Parse back (skip 4-byte length prefix)
        payload = json.loads(data[4:])

        assert len(payload["params"]["data"]) == 100000

    def test_unicode_handling(self) -> None:
        """Test handling of Unicode characters."""
        # Use actual Unicode characters (Chinese "world" and earth emoji)
        test_message = "Hello 世界 🌍"
        request = RPCRequest(
            method="test",
            params={"message": test_message},
        )
        data = request.to_bytes()

        # Parse back
        payload = json.loads(data[4:])
        assert payload["params"]["message"] == test_message
