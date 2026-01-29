"""Tests for worker management API endpoints."""

from unittest.mock import ANY, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app


@pytest.fixture
def client() -> TestClient:
    """Create test client."""
    return TestClient(app)


class TestDaemonStatusEndpoint:
    """Tests for /api/workers/daemon/status endpoint."""

    def test_daemon_status_when_running(self, client: TestClient) -> None:
        """Test getting daemon status when daemon is running."""
        mock_result = {
            "state": "running",
            "pid": 12345,
            "uptime_seconds": 3600.0,
            "started_at": "2024-01-15T10:00:00",
            "workers_count": 3,
        }

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.return_value = mock_result
            mock_get_client.return_value = mock_client

            response = client.get("/api/workers/daemon/status")

            assert response.status_code == 200
            data = response.json()
            assert data["state"] == "running"
            assert data["pid"] == 12345
            assert data["workers_count"] == 3

    def test_daemon_status_when_not_running(self, client: TestClient) -> None:
        """Test getting daemon status when daemon is not running."""
        from mab.rpc import DaemonNotRunningError

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.side_effect = DaemonNotRunningError()
            mock_get_client.return_value = mock_client

            response = client.get("/api/workers/daemon/status")

            assert response.status_code == 503
            assert "daemon is not running" in response.json()["detail"].lower()


class TestHealthStatusEndpoint:
    """Tests for /api/workers/health endpoint."""

    def test_health_status(self, client: TestClient) -> None:
        """Test getting health status."""
        mock_result = {
            "healthy_workers": 2,
            "unhealthy_workers": 1,
            "crashed_workers": 0,
            "total_restarts": 5,
            "workers_at_max_restarts": 0,
            "config": {
                "health_check_interval_seconds": 30.0,
                "heartbeat_timeout_seconds": 60.0,
                "max_restart_count": 3,
                "restart_backoff_base_seconds": 5.0,
                "restart_backoff_max_seconds": 300.0,
                "auto_restart_enabled": True,
            },
        }

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.return_value = mock_result
            mock_get_client.return_value = mock_client

            response = client.get("/api/workers/health")

            assert response.status_code == 200
            data = response.json()
            assert data["healthy_workers"] == 2
            assert data["unhealthy_workers"] == 1
            assert data["total_restarts"] == 5
            assert data["config"]["max_restart_count"] == 3


class TestListWorkersEndpoint:
    """Tests for /api/workers endpoint."""

    def test_list_workers_empty(self, client: TestClient) -> None:
        """Test listing workers when none exist."""
        mock_result = {"workers": []}

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.return_value = mock_result
            mock_get_client.return_value = mock_client

            response = client.get("/api/workers")

            assert response.status_code == 200
            data = response.json()
            assert data["workers"] == []
            assert data["total"] == 0

    def test_list_workers_with_filter(self, client: TestClient) -> None:
        """Test listing workers with status filter."""
        mock_result = {
            "workers": [
                {
                    "id": "worker-1",
                    "pid": 1234,
                    "status": "running",
                    "role": "dev",
                    "project_path": "/test/project",
                }
            ]
        }

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.return_value = mock_result
            mock_get_client.return_value = mock_client

            response = client.get("/api/workers?status=running")

            assert response.status_code == 200
            data = response.json()
            assert len(data["workers"]) == 1
            assert data["workers"][0]["status"] == "running"

            # Verify correct params were passed (ANY for timeout parameter)
            mock_client.call.assert_called_once_with("worker.list", {"status": "running"}, ANY)


class TestSpawnWorkerEndpoint:
    """Tests for POST /api/workers endpoint."""

    def test_spawn_worker_success(self, client: TestClient) -> None:
        """Test spawning a new worker."""
        mock_result = {
            "id": "worker-abc123",
            "worker_id": "worker-abc123",
            "pid": 5678,
            "status": "running",
            "role": "dev",
            "project_path": "/test/project",
        }

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.return_value = mock_result
            mock_get_client.return_value = mock_client

            response = client.post(
                "/api/workers",
                json={
                    "role": "dev",
                    "project_path": "/test/project",
                    "auto_restart": True,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "worker-abc123"
            assert data["role"] == "dev"

    def test_spawn_worker_missing_role(self, client: TestClient) -> None:
        """Test spawning worker without required role."""
        response = client.post(
            "/api/workers",
            json={
                "project_path": "/test/project",
            },
        )

        assert response.status_code == 422  # Validation error


class TestStopWorkerEndpoint:
    """Tests for DELETE /api/workers/{worker_id} endpoint."""

    def test_stop_worker_success(self, client: TestClient) -> None:
        """Test stopping a worker."""
        mock_result = {
            "success": True,
            "worker": {
                "id": "worker-1",
                "pid": None,
                "status": "stopped",
                "role": "dev",
                "project_path": "/test/project",
            },
        }

        with patch("dashboard.routes.workers.get_default_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.call.return_value = mock_result
            mock_get_client.return_value = mock_client

            response = client.delete("/api/workers/worker-1")

            assert response.status_code == 200
            # Verify RPC call was made
            mock_client.call.assert_called()


class TestAdminPage:
    """Tests for admin page."""

    def test_admin_page_returns_html(self, client: TestClient) -> None:
        """Test admin page returns HTML."""
        response = client.get("/admin")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "System Administration" in response.text

    def test_admin_page_contains_worker_management(self, client: TestClient) -> None:
        """Test admin page contains worker management elements."""
        response = client.get("/admin")
        assert response.status_code == 200
        assert "Spawn New Worker" in response.text
        assert "MAB Daemon" in response.text
        assert "Workers" in response.text


class TestWebSocketEndpoint:
    """Tests for WebSocket endpoint."""

    def test_websocket_connection(self, client: TestClient) -> None:
        """Test WebSocket connection and initial message."""
        with client.websocket_connect("/ws") as websocket:
            # Should receive connected message
            data = websocket.receive_json()
            assert data["type"] == "connected"
            assert "data" in data
            assert "timestamp" in data

    def test_websocket_ping_pong(self, client: TestClient) -> None:
        """Test WebSocket ping/pong."""
        with client.websocket_connect("/ws") as websocket:
            # Receive initial connected message
            websocket.receive_json()

            # Send ping
            websocket.send_json({"type": "ping"})

            # Should receive heartbeat/pong response
            data = websocket.receive_json()
            assert data["type"] == "heartbeat"
            assert data["data"].get("pong") is True


class TestAPIDocumentation:
    """Tests for API documentation."""

    def test_openapi_schema_available(self, client: TestClient) -> None:
        """Test OpenAPI schema is available."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "paths" in schema

    def test_docs_page_available(self, client: TestClient) -> None:
        """Test docs page is available."""
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_worker_endpoints_documented(self, client: TestClient) -> None:
        """Test worker endpoints are in OpenAPI schema."""
        response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        assert "/api/workers" in paths
        assert "/api/workers/daemon/status" in paths
        assert "/api/workers/health" in paths
