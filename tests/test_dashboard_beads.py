"""Tests for the dashboard beads API endpoints."""

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.routes.beads import (
    BeadCreate,
    _parse_beads_json,
    _run_bd_command,
)

client = TestClient(app)


# Sample bead data for mocking bd command responses
SAMPLE_BEADS = [
    {
        "id": "multi_agent_beads-abc",
        "title": "Test task one",
        "description": "First test task",
        "status": "open",
        "priority": 2,
        "issue_type": "task",
        "owner": "mark.johnson",
        "created_at": "2026-01-24",
        "updated_at": "2026-01-24",
        "labels": ["dev"],
    },
    {
        "id": "multi_agent_beads-def",
        "title": "Test task two",
        "description": "Second test task",
        "status": "in_progress",
        "priority": 1,
        "issue_type": "feature",
        "owner": "mark.johnson",
        "created_at": "2026-01-24",
        "updated_at": "2026-01-24",
        "labels": ["qa"],
    },
    {
        "id": "multi_agent_beads-ghi",
        "title": "Test task three",
        "description": "Third test task",
        "status": "closed",
        "priority": 3,
        "issue_type": "bug",
        "owner": None,
        "created_at": "2026-01-24",
        "updated_at": "2026-01-24",
        "labels": ["dev", "urgent"],
    },
]


class TestListBeadsEndpoint:
    """Tests for GET /api/beads endpoint."""

    def test_list_beads_returns_valid_json(self) -> None:
        """Test that GET /api/beads returns valid JSON array."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(SAMPLE_BEADS))

            response = client.get("/api/beads")

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 3
            # Verify structure of first bead
            assert "id" in data[0]
            assert "title" in data[0]
            assert "status" in data[0]
            assert "priority" in data[0]

    def test_list_beads_filter_by_status_open(self) -> None:
        """Test GET /api/beads?status=open filters correctly."""
        open_beads = [b for b in SAMPLE_BEADS if b["status"] == "open"]

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(open_beads))

            response = client.get("/api/beads?status=open")

            assert response.status_code == 200
            data = response.json()
            # All returned beads should have open status
            for bead in data:
                assert bead["status"] == "open"
            # Verify the command was called with --status flag
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "--status" in args
            assert "open" in args

    def test_list_beads_filter_by_status_in_progress(self) -> None:
        """Test GET /api/beads?status=in_progress filters correctly."""
        in_progress_beads = [b for b in SAMPLE_BEADS if b["status"] == "in_progress"]

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(in_progress_beads))

            response = client.get("/api/beads?status=in_progress")

            assert response.status_code == 200
            data = response.json()
            for bead in data:
                assert bead["status"] == "in_progress"

    def test_list_beads_filter_by_label_dev(self) -> None:
        """Test GET /api/beads?label=dev filters correctly."""
        dev_beads = [b for b in SAMPLE_BEADS if "dev" in b["labels"]]

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(dev_beads))

            response = client.get("/api/beads?label=dev")

            assert response.status_code == 200
            data = response.json()
            # All returned beads should have dev label
            for bead in data:
                assert "dev" in bead["labels"]
            # Verify the command was called with -l flag
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "-l" in args
            assert "dev" in args

    def test_list_beads_filter_by_priority(self) -> None:
        """Test GET /api/beads?priority=2 filters correctly."""
        p2_beads = [b for b in SAMPLE_BEADS if b["priority"] == 2]

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(p2_beads))

            response = client.get("/api/beads?priority=2")

            assert response.status_code == 200
            data = response.json()
            for bead in data:
                assert bead["priority"] == 2
            # Verify the command was called with -p flag
            args = mock_run.call_args[0][0]
            assert "-p" in args
            assert "2" in args

    def test_list_beads_combined_filters(self) -> None:
        """Test multiple filters can be combined."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps([SAMPLE_BEADS[0]]))

            response = client.get("/api/beads?status=open&label=dev&priority=2")

            assert response.status_code == 200
            args = mock_run.call_args[0][0]
            # All filters should be present
            assert "--status" in args
            assert "-l" in args
            assert "-p" in args

    def test_list_beads_empty_result(self) -> None:
        """Test that empty result returns empty JSON array."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps([]))

            response = client.get("/api/beads")

            assert response.status_code == 200
            assert response.json() == []

    def test_list_beads_bd_command_failure(self) -> None:
        """Test error handling when bd command fails."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (False, "bd command not found")

            response = client.get("/api/beads")

            assert response.status_code == 500
            assert "Failed to list beads" in response.json()["detail"]


class TestReadyBeadsEndpoint:
    """Tests for GET /api/beads/ready endpoint."""

    def test_ready_beads_returns_unblocked(self) -> None:
        """Test GET /api/beads/ready returns only unblocked beads."""
        ready_beads = [SAMPLE_BEADS[0]]  # Only first bead is ready

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(ready_beads))

            response = client.get("/api/beads/ready")

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            # Verify bd ready command was called
            args = mock_run.call_args[0][0]
            assert args[0] == "ready"
            assert "--json" in args

    def test_ready_beads_filter_by_label(self) -> None:
        """Test GET /api/beads/ready?label=dev filters by label."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps([SAMPLE_BEADS[0]]))

            response = client.get("/api/beads/ready?label=dev")

            assert response.status_code == 200
            args = mock_run.call_args[0][0]
            assert args[0] == "ready"
            assert "-l" in args
            assert "dev" in args

    def test_ready_beads_empty_when_all_blocked(self) -> None:
        """Test empty result when all beads are blocked."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps([]))

            response = client.get("/api/beads/ready")

            assert response.status_code == 200
            assert response.json() == []


class TestInProgressBeadsEndpoint:
    """Tests for GET /api/beads/in-progress endpoint."""

    def test_in_progress_beads(self) -> None:
        """Test GET /api/beads/in-progress returns only in_progress beads."""
        in_progress = [b for b in SAMPLE_BEADS if b["status"] == "in_progress"]

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(in_progress))

            response = client.get("/api/beads/in-progress")

            assert response.status_code == 200
            data = response.json()
            for bead in data:
                assert bead["status"] == "in_progress"


class TestGetBeadEndpoint:
    """Tests for GET /api/beads/{bead_id} endpoint."""

    def test_get_bead_returns_correct_bead(self) -> None:
        """Test GET /api/beads/{id} returns the correct bead."""
        target_bead = SAMPLE_BEADS[0]

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps([target_bead]))

            response = client.get(f"/api/beads/{target_bead['id']}")

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == target_bead["id"]
            assert data["title"] == target_bead["title"]
            assert data["status"] == target_bead["status"]
            # Verify bd show command was called with correct ID
            args = mock_run.call_args[0][0]
            assert args[0] == "show"
            assert target_bead["id"] in args

    def test_get_bead_invalid_id_returns_404(self) -> None:
        """Test GET /api/beads/{invalid_id} returns 404."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (False, "Bead not found: invalid-bead-id")

            response = client.get("/api/beads/invalid-bead-id")

            assert response.status_code == 404
            assert "Bead not found" in response.json()["detail"]

    def test_get_bead_empty_result_returns_404(self) -> None:
        """Test 404 when bd returns empty result."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps([]))

            response = client.get("/api/beads/nonexistent-id")

            assert response.status_code == 404

    def test_get_bead_server_error(self) -> None:
        """Test 500 on general bd command failure."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (False, "Connection timeout")

            response = client.get("/api/beads/some-id")

            assert response.status_code == 500


class TestCreateBeadEndpoint:
    """Tests for POST /api/beads endpoint."""

    def test_create_bead_minimal(self) -> None:
        """Test creating a bead with minimal required fields."""
        new_bead_id = "multi_agent_beads-new"
        created_bead = {
            "id": new_bead_id,
            "title": "New task",
            "description": None,
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "owner": None,
            "created_at": "2026-01-24",
            "updated_at": "2026-01-24",
            "labels": [],
        }

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            # First call: create returns ID
            # Second call: show returns full bead
            mock_run.side_effect = [
                (True, new_bead_id),
                (True, json.dumps([created_bead])),
            ]

            response = client.post(
                "/api/beads",
                json={"title": "New task"},
            )

            assert response.status_code == 201
            data = response.json()
            assert data["id"] == new_bead_id
            assert data["title"] == "New task"

    def test_create_bead_with_all_fields(self) -> None:
        """Test creating a bead with all optional fields."""
        new_bead_id = "multi_agent_beads-full"
        created_bead = {
            "id": new_bead_id,
            "title": "Full task",
            "description": "Task with all fields",
            "status": "open",
            "priority": 1,
            "issue_type": "feature",
            "owner": None,
            "created_at": "2026-01-24",
            "updated_at": "2026-01-24",
            "labels": ["dev", "urgent"],
        }

        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.side_effect = [
                (True, new_bead_id),
                (True, json.dumps([created_bead])),
            ]

            response = client.post(
                "/api/beads",
                json={
                    "title": "Full task",
                    "description": "Task with all fields",
                    "priority": 1,
                    "issue_type": "feature",
                    "labels": ["dev", "urgent"],
                },
            )

            assert response.status_code == 201
            data = response.json()
            assert data["priority"] == 1
            assert data["issue_type"] == "feature"
            assert "dev" in data["labels"]

    def test_create_bead_validates_title_required(self) -> None:
        """Test that title is required."""
        response = client.post("/api/beads", json={})

        assert response.status_code == 422  # Validation error

    def test_create_bead_validates_title_not_empty(self) -> None:
        """Test that empty title is rejected."""
        response = client.post("/api/beads", json={"title": ""})

        assert response.status_code == 422

    def test_create_bead_validates_priority_range(self) -> None:
        """Test priority validation (0-4)."""
        # Invalid: priority too high
        response = client.post("/api/beads", json={"title": "Test", "priority": 5})
        assert response.status_code == 422

        # Invalid: priority negative
        response = client.post("/api/beads", json={"title": "Test", "priority": -1})
        assert response.status_code == 422

    def test_create_bead_bd_failure(self) -> None:
        """Test error handling when bd create fails."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (False, "Failed to create bead")

            response = client.post("/api/beads", json={"title": "Test"})

            assert response.status_code == 500
            assert "Failed to create bead" in response.json()["detail"]


class TestBdCommandRunner:
    """Tests for _run_bd_command helper function."""

    def test_run_bd_command_success(self) -> None:
        """Test successful bd command execution."""
        with patch("dashboard.routes.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = MagicMock(
                returncode=0,
                stdout="output",
                stderr="",
            )

            success, output = _run_bd_command(["list", "--json"])

            assert success is True
            assert output == "output"
            mock_subprocess.assert_called_once()

    def test_run_bd_command_failure(self) -> None:
        """Test bd command failure handling."""
        with patch("dashboard.routes.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error message",
            )

            success, output = _run_bd_command(["invalid"])

            assert success is False
            assert output == "Error message"

    def test_run_bd_command_timeout(self) -> None:
        """Test timeout handling."""
        import subprocess

        with patch("dashboard.routes.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="bd", timeout=30)

            success, output = _run_bd_command(["slow-command"])

            assert success is False
            assert "timed out" in output.lower()

    def test_run_bd_command_not_found(self) -> None:
        """Test handling when bd is not installed."""
        with patch("dashboard.routes.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = FileNotFoundError()

            success, output = _run_bd_command(["list"])

            assert success is False
            assert "not found" in output.lower()


class TestParseBeadsJson:
    """Tests for _parse_beads_json helper function."""

    def test_parse_valid_json_array(self) -> None:
        """Test parsing valid JSON array."""
        result = _parse_beads_json(json.dumps(SAMPLE_BEADS))
        assert len(result) == 3
        assert result[0]["id"] == "multi_agent_beads-abc"

    def test_parse_empty_array(self) -> None:
        """Test parsing empty JSON array."""
        result = _parse_beads_json("[]")
        assert result == []

    def test_parse_invalid_json(self) -> None:
        """Test handling invalid JSON."""
        result = _parse_beads_json("not valid json")
        assert result == []

    def test_parse_non_array_json(self) -> None:
        """Test handling non-array JSON."""
        result = _parse_beads_json('{"single": "object"}')
        assert result == []


class TestBeadCreateModel:
    """Tests for BeadCreate Pydantic model."""

    def test_bead_create_defaults(self) -> None:
        """Test default values for BeadCreate."""
        bead = BeadCreate(title="Test")
        assert bead.title == "Test"
        assert bead.description is None
        assert bead.priority == 2
        assert bead.issue_type == "task"
        assert bead.labels == []

    def test_bead_create_with_all_fields(self) -> None:
        """Test BeadCreate with all fields."""
        bead = BeadCreate(
            title="Full test",
            description="Description",
            priority=0,
            issue_type="feature",
            labels=["dev", "urgent"],
        )
        assert bead.priority == 0
        assert bead.issue_type == "feature"
        assert len(bead.labels) == 2


class TestResponseConsistency:
    """Tests for consistent error responses."""

    def test_error_response_format(self) -> None:
        """Test that error responses follow consistent format."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (False, "Bead not found")

            response = client.get("/api/beads/invalid")

            assert response.status_code == 404
            data = response.json()
            # FastAPI error format
            assert "detail" in data

    def test_404_contains_bead_id(self) -> None:
        """Test that 404 error contains the requested bead ID."""
        with patch("dashboard.routes.beads._run_bd_command") as mock_run:
            mock_run.return_value = (False, "Bead not found: test-id-123")

            response = client.get("/api/beads/test-id-123")

            assert "test-id-123" in response.json()["detail"]
