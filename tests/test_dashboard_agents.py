"""Tests for the dashboard agents API endpoints.

Tests the RPC-backed implementation that uses the daemon as the
single source of truth for worker/agent data.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.routes.agents import (
    ROLE_MAP,
    VALID_ROLES,
    _extract_instance_from_worker_id,
    _format_timestamp,
    _get_active_agents,
    _map_status_to_api,
)

client = TestClient(app)


def _make_worker(
    worker_id: str = "worker-dev-abc123",
    role: str = "dev",
    status: str = "running",
    pid: int = 1001,
    project_path: str = "/test/project",
    started_at: str | None = None,
    stopped_at: str | None = None,
    bead_id: str | None = None,
) -> dict:
    """Create a worker dict as returned by the RPC daemon."""
    if started_at is None:
        started_at = datetime.now().isoformat()
    return {
        "id": worker_id,
        "role": role,
        "project_path": project_path,
        "status": status,
        "pid": pid,
        "created_at": started_at,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "crash_count": 0,
        "last_heartbeat": None,
        "exit_code": None,
        "error_message": None,
        "last_restart_at": None,
        "auto_restart_enabled": True,
        "town_name": "default",
        "worktree_path": None,
        "worktree_branch": None,
        "bead_id": bead_id,
    }


def _mock_rpc_workers(workers: list[dict]) -> MagicMock:
    """Create a mock RPC client that returns the given workers."""
    mock_client = MagicMock()
    mock_client.call.return_value = {"workers": workers}
    return mock_client


class TestAgentsEndpoints:
    """Tests for /api/agents endpoints."""

    def test_list_agents_daemon_not_running(self) -> None:
        """Test listing agents when daemon is not running."""
        from mab.rpc import DaemonNotRunningError

        mock_client = MagicMock()
        mock_client.call.side_effect = DaemonNotRunningError("not running")

        with patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client):
            response = client.get("/api/agents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_agents_empty(self) -> None:
        """Test listing agents with no workers running."""
        mock_client = _mock_rpc_workers([])
        with patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client):
            response = client.get("/api/agents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_agents_with_running_workers(self) -> None:
        """Test listing agents with running workers."""
        workers = [_make_worker("worker-dev-abc123", role="dev", status="running", pid=1001)]
        mock_client = _mock_rpc_workers(workers)

        with (
            patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client),
            patch(
                "dashboard.routes.agents._enrich_workers_with_bead_info",
                side_effect=lambda ws: ws,
            ),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["pid"] == 1001
            assert data[0]["role"] == "developer"
            assert data[0]["status"] == "idle"

    def test_list_agents_with_bead(self) -> None:
        """Test that agents with a claimed bead show as working."""
        workers = [_make_worker("worker-dev-abc123", role="dev", status="running", pid=1001)]
        mock_client = _mock_rpc_workers(workers)

        def enrich(ws):
            for w in ws:
                w["current_bead"] = "mab-task-123"
                w["current_bead_title"] = "Fix the bug"
            return ws

        with (
            patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client),
            patch(
                "dashboard.routes.agents._enrich_workers_with_bead_info",
                side_effect=enrich,
            ),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["status"] == "working"
            assert data[0]["current_bead"] == "mab-task-123"
            assert data[0]["current_bead_title"] == "Fix the bug"

    def test_list_agents_excludes_old_stopped_workers(self) -> None:
        """Test that workers stopped more than 1 hour ago are excluded."""
        old_stop = (datetime.now() - timedelta(hours=2)).isoformat()
        recent_stop = (datetime.now() - timedelta(minutes=30)).isoformat()

        workers = [
            _make_worker(
                "worker-old",
                status="stopped",
                started_at=(datetime.now() - timedelta(hours=3)).isoformat(),
                stopped_at=old_stop,
            ),
            _make_worker(
                "worker-recent",
                status="stopped",
                started_at=(datetime.now() - timedelta(hours=1)).isoformat(),
                stopped_at=recent_stop,
            ),
        ]
        mock_client = _mock_rpc_workers(workers)

        with (
            patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client),
            patch(
                "dashboard.routes.agents._enrich_workers_with_bead_info",
                side_effect=lambda ws: ws,
            ),
        ):
            # Active-only endpoint filters to running/spawning/starting only
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 0  # Both are stopped, not active

            # Recent endpoint shows non-active workers
            response = client.get("/api/agents/recent")
            assert response.status_code == 200
            data = response.json()
            # Old worker filtered out by _is_worker_recent, recent one included
            assert len(data) == 1
            assert "worker-recent" in data[0]["worker_id"]

    def test_list_agents_includes_spawning_workers(self) -> None:
        """Test that spawning workers are included as idle."""
        workers = [_make_worker("worker-spawning", status="spawning")]
        mock_client = _mock_rpc_workers(workers)

        with (
            patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client),
            patch(
                "dashboard.routes.agents._enrich_workers_with_bead_info",
                side_effect=lambda ws: ws,
            ),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["status"] == "idle"

    def test_list_agents_by_role(self) -> None:
        """Test filtering agents by role."""
        workers = [
            _make_worker("worker-dev-1", role="dev", status="running"),
            _make_worker("worker-qa-1", role="qa", status="running"),
        ]
        mock_client = _mock_rpc_workers(workers)

        with (
            patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client),
            patch(
                "dashboard.routes.agents._enrich_workers_with_bead_info",
                side_effect=lambda ws: ws,
            ),
        ):
            # Filter by developer
            response = client.get("/api/agents/developer")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "developer"

            # Filter by qa
            response = client.get("/api/agents/qa")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "qa"

    def test_list_agents_invalid_role(self) -> None:
        """Test that invalid role returns 400 error."""
        response = client.get("/api/agents/invalid_role")
        assert response.status_code == 400
        assert "Invalid role" in response.json()["detail"]


class TestStatusMapping:
    """Tests for status mapping functions."""

    def test_map_status_running_with_bead(self) -> None:
        """Test running status with current bead -> working."""
        assert _map_status_to_api("running", "mab-task") == "working"

    def test_map_status_running_no_bead(self) -> None:
        """Test running status without bead -> idle."""
        assert _map_status_to_api("running", None) == "idle"

    def test_map_status_spawning(self) -> None:
        """Test spawning status -> idle."""
        assert _map_status_to_api("spawning", None) == "idle"
        assert _map_status_to_api("spawning", "mab-task") == "idle"

    def test_map_status_stopped(self) -> None:
        """Test stopped status -> ended."""
        assert _map_status_to_api("stopped", None) == "ended"
        assert _map_status_to_api("stopped", "mab-task") == "ended"

    def test_map_status_crashed(self) -> None:
        """Test crashed status -> ended."""
        assert _map_status_to_api("crashed", None) == "ended"

    def test_map_status_failed(self) -> None:
        """Test failed status -> ended."""
        assert _map_status_to_api("failed", None) == "ended"
        assert _map_status_to_api("failed", "mab-task") == "ended"

    def test_map_status_stopping(self) -> None:
        """Test stopping status -> ended."""
        assert _map_status_to_api("stopping", None) == "ended"
        assert _map_status_to_api("stopping", "mab-task") == "ended"


class TestRoleMapping:
    """Tests for role mapping."""

    def test_role_map_values(self) -> None:
        """Test that role map contains expected mappings."""
        assert ROLE_MAP["dev"] == "developer"
        assert ROLE_MAP["qa"] == "qa"
        assert ROLE_MAP["tech_lead"] == "tech_lead"
        assert ROLE_MAP["manager"] == "manager"
        assert ROLE_MAP["reviewer"] == "reviewer"

    def test_valid_roles(self) -> None:
        """Test valid roles set."""
        expected_roles = {"developer", "qa", "reviewer", "tech_lead", "manager", "unknown"}
        assert VALID_ROLES == expected_roles


class TestInstanceExtraction:
    """Tests for extracting instance numbers from worker IDs."""

    def test_extract_instance_with_number(self) -> None:
        """Test extracting instance from ID with number."""
        assert _extract_instance_from_worker_id("worker-dev-1-abc123") == 1
        assert _extract_instance_from_worker_id("worker-qa-5-xyz") == 5

    def test_extract_instance_no_number(self) -> None:
        """Test default instance when no number in ID."""
        assert _extract_instance_from_worker_id("worker-dev-abc123") == 1
        assert _extract_instance_from_worker_id("some-worker-id") == 1


class TestTimestampFormatting:
    """Tests for timestamp formatting."""

    def test_format_iso_timestamp(self) -> None:
        """Test formatting ISO timestamp."""
        assert _format_timestamp("2026-01-24T14:30:00") == "2026-01-24T14:30:00Z"
        assert _format_timestamp("2026-01-24T14:30:00Z") == "2026-01-24T14:30:00Z"

    def test_format_space_separated_timestamp(self) -> None:
        """Test formatting space-separated timestamp."""
        assert _format_timestamp("2026-01-24 14:30:00") == "2026-01-24T14:30:00Z"

    def test_format_empty_timestamp(self) -> None:
        """Test formatting empty/None timestamp."""
        assert _format_timestamp(None) == ""
        assert _format_timestamp("") == ""


class TestGetActiveAgents:
    """Integration tests for _get_active_agents function."""

    def test_get_active_agents_full_workflow(self) -> None:
        """Test full workflow of getting active agents from RPC daemon."""
        now = datetime.now()
        workers = [
            _make_worker(
                "worker-dev-1-abc",
                role="dev",
                status="running",
                pid=1001,
            ),
            _make_worker(
                "worker-qa-2-xyz",
                role="qa",
                status="spawning",
                pid=1002,
            ),
            _make_worker(
                "worker-dev-3-def",
                role="dev",
                status="stopped",
                pid=1003,
                stopped_at=(now - timedelta(minutes=30)).isoformat(),
            ),
        ]
        mock_client = _mock_rpc_workers(workers)

        with (
            patch("dashboard.routes.agents._get_rpc_client", return_value=mock_client),
            patch(
                "dashboard.routes.agents._enrich_workers_with_bead_info",
                side_effect=lambda ws: ws,
            ),
        ):
            agents = _get_active_agents()

            # Only running and spawning are "active"
            assert len(agents) == 2

            # Find specific agents
            dev1 = next(a for a in agents if "dev-1" in a["worker_id"])
            assert dev1["status"] == "idle"
            assert dev1["role"] == "developer"

            qa = next(a for a in agents if "qa-2" in a["worker_id"])
            assert qa["status"] == "idle"
            assert qa["role"] == "qa"
