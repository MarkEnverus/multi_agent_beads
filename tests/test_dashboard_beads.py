"""Tests for the dashboard beads API endpoints."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.exceptions import BeadCommandError, BeadNotFoundError, BeadParseError
from dashboard.routes.beads import BeadCreate
from dashboard.services.beads import BeadService

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
        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = SAMPLE_BEADS

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

        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = open_beads

            response = client.get("/api/beads?status=open")

            assert response.status_code == 200
            data = response.json()
            # All returned beads should have open status
            for bead in data:
                assert bead["status"] == "open"
            # Verify the service was called with status filter
            mock_list.assert_called_once()
            assert mock_list.call_args.kwargs.get("status") == "open"

    def test_list_beads_filter_by_status_in_progress(self) -> None:
        """Test GET /api/beads?status=in_progress filters correctly."""
        in_progress_beads = [b for b in SAMPLE_BEADS if b["status"] == "in_progress"]

        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = in_progress_beads

            response = client.get("/api/beads?status=in_progress")

            assert response.status_code == 200
            data = response.json()
            for bead in data:
                assert bead["status"] == "in_progress"

    def test_list_beads_filter_by_label_dev(self) -> None:
        """Test GET /api/beads?label=dev filters correctly."""
        dev_beads = [b for b in SAMPLE_BEADS if "dev" in b["labels"]]

        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = dev_beads

            response = client.get("/api/beads?label=dev")

            assert response.status_code == 200
            data = response.json()
            # All returned beads should have dev label
            for bead in data:
                assert "dev" in bead["labels"]
            # Verify the service was called with label filter
            mock_list.assert_called_once()
            assert mock_list.call_args.kwargs.get("label") == "dev"

    def test_list_beads_filter_by_priority(self) -> None:
        """Test GET /api/beads?priority=2 filters correctly."""
        p2_beads = [b for b in SAMPLE_BEADS if b["priority"] == 2]

        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = p2_beads

            response = client.get("/api/beads?priority=2")

            assert response.status_code == 200
            data = response.json()
            for bead in data:
                assert bead["priority"] == 2
            # Verify priority filter
            assert mock_list.call_args.kwargs.get("priority") == 2

    def test_list_beads_combined_filters(self) -> None:
        """Test multiple filters can be combined."""
        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = [SAMPLE_BEADS[0]]

            response = client.get("/api/beads?status=open&label=dev&priority=2")

            assert response.status_code == 200
            # All filters should be passed
            kwargs = mock_list.call_args.kwargs
            assert kwargs.get("status") == "open"
            assert kwargs.get("label") == "dev"
            assert kwargs.get("priority") == 2

    def test_list_beads_empty_result(self) -> None:
        """Test that empty result returns empty JSON array."""
        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = []

            response = client.get("/api/beads")

            assert response.status_code == 200
            assert response.json() == []

    def test_list_beads_bd_command_failure(self) -> None:
        """Test error handling when bd command fails."""
        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.side_effect = BeadCommandError(
                message="bd command not found",
                command=["list"],
            )

            response = client.get("/api/beads")

            assert response.status_code == 500
            assert "bd command not found" in response.json()["message"]


class TestReadyBeadsEndpoint:
    """Tests for GET /api/beads/ready endpoint."""

    def test_ready_beads_returns_unblocked(self) -> None:
        """Test GET /api/beads/ready returns only unblocked beads."""
        ready_beads = [SAMPLE_BEADS[0]]  # Only first bead is ready

        with patch.object(BeadService, "list_ready") as mock_ready:
            mock_ready.return_value = ready_beads

            response = client.get("/api/beads/ready")

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            mock_ready.assert_called_once()

    def test_ready_beads_filter_by_label(self) -> None:
        """Test GET /api/beads/ready?label=dev filters by label."""
        with patch.object(BeadService, "list_ready") as mock_ready:
            mock_ready.return_value = [SAMPLE_BEADS[0]]

            response = client.get("/api/beads/ready?label=dev")

            assert response.status_code == 200
            assert mock_ready.call_args.kwargs.get("label") == "dev"

    def test_ready_beads_empty_when_all_blocked(self) -> None:
        """Test empty result when all beads are blocked."""
        with patch.object(BeadService, "list_ready") as mock_ready:
            mock_ready.return_value = []

            response = client.get("/api/beads/ready")

            assert response.status_code == 200
            assert response.json() == []


class TestInProgressBeadsEndpoint:
    """Tests for GET /api/beads/in-progress endpoint."""

    def test_in_progress_beads(self) -> None:
        """Test GET /api/beads/in-progress returns only in_progress beads."""
        in_progress = [b for b in SAMPLE_BEADS if b["status"] == "in_progress"]

        with patch.object(BeadService, "list_beads") as mock_list:
            mock_list.return_value = in_progress

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

        with patch.object(BeadService, "get_bead") as mock_get:
            mock_get.return_value = target_bead

            response = client.get(f"/api/beads/{target_bead['id']}")

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == target_bead["id"]
            assert data["title"] == target_bead["title"]
            assert data["status"] == target_bead["status"]
            mock_get.assert_called_once_with(target_bead["id"])

    def test_get_bead_invalid_id_returns_404(self) -> None:
        """Test GET /api/beads/{invalid_id} returns 404."""
        with patch.object(BeadService, "get_bead") as mock_get:
            mock_get.side_effect = BeadNotFoundError("invalid-bead-id")

            response = client.get("/api/beads/invalid-bead-id")

            assert response.status_code == 404
            assert "invalid-bead-id" in response.json()["message"]

    def test_get_bead_server_error(self) -> None:
        """Test 500 on general bd command failure."""
        with patch.object(BeadService, "get_bead") as mock_get:
            mock_get.side_effect = BeadCommandError(
                message="Connection timeout",
                command=["show"],
            )

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

        with patch.object(BeadService, "create_bead") as mock_create:
            mock_create.return_value = created_bead

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

        with patch.object(BeadService, "create_bead") as mock_create:
            mock_create.return_value = created_bead

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
        with patch.object(BeadService, "create_bead") as mock_create:
            mock_create.side_effect = BeadCommandError(
                message="Failed to create bead",
                command=["create"],
            )

            response = client.post("/api/beads", json={"title": "Test"})

            assert response.status_code == 500
            assert "Failed to create bead" in response.json()["message"]


class TestBeadServiceRunCommand:
    """Tests for BeadService.run_command method."""

    def test_run_command_success(self) -> None:
        """Test successful bd command execution."""
        with patch("dashboard.services.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = MagicMock(
                returncode=0,
                stdout="output",
                stderr="",
            )

            output = BeadService.run_command(["list", "--json"])

            assert output == "output"
            mock_subprocess.assert_called_once()

    def test_run_command_failure(self) -> None:
        """Test bd command failure handling."""
        with patch("dashboard.services.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error message",
            )

            with pytest.raises(BeadCommandError) as exc_info:
                BeadService.run_command(["invalid"])

            assert "Error message" in str(exc_info.value.message)

    def test_run_command_timeout(self) -> None:
        """Test timeout handling."""
        with patch("dashboard.services.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="bd", timeout=30)

            with pytest.raises(BeadCommandError) as exc_info:
                BeadService.run_command(["slow-command"])

            assert "timed out" in exc_info.value.message.lower()

    def test_run_command_not_found(self) -> None:
        """Test handling when bd is not installed."""
        with patch("dashboard.services.beads.subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = FileNotFoundError()

            with pytest.raises(BeadCommandError) as exc_info:
                BeadService.run_command(["list"])

            assert "not installed" in exc_info.value.message.lower()


class TestBeadServiceParseJson:
    """Tests for BeadService.parse_json_output method."""

    def test_parse_valid_json_array(self) -> None:
        """Test parsing valid JSON array."""
        result = BeadService.parse_json_output(json.dumps(SAMPLE_BEADS))
        assert len(result) == 3
        assert result[0]["id"] == "multi_agent_beads-abc"

    def test_parse_empty_array(self) -> None:
        """Test parsing empty JSON array."""
        result = BeadService.parse_json_output("[]")
        assert result == []

    def test_parse_invalid_json(self) -> None:
        """Test handling invalid JSON."""
        with pytest.raises(BeadParseError):
            BeadService.parse_json_output("not valid json")

    def test_parse_single_object(self) -> None:
        """Test parsing single JSON object (wrapped in list)."""
        result = BeadService.parse_json_output(json.dumps(SAMPLE_BEADS[0]))
        assert len(result) == 1
        assert result[0]["id"] == "multi_agent_beads-abc"

    def test_parse_empty_string(self) -> None:
        """Test parsing empty string returns empty list."""
        result = BeadService.parse_json_output("")
        assert result == []


class TestBeadServiceValidation:
    """Tests for BeadService validation methods."""

    def test_validate_bead_id_valid(self) -> None:
        """Test valid bead ID passes validation."""
        # Should not raise
        BeadService.validate_bead_id("multi_agent_beads-abc123")
        BeadService.validate_bead_id("project-xyz")

    def test_validate_bead_id_empty(self) -> None:
        """Test empty bead ID fails validation."""
        from dashboard.exceptions import BeadValidationError

        with pytest.raises(BeadValidationError):
            BeadService.validate_bead_id("")

    def test_validate_bead_id_invalid_format(self) -> None:
        """Test invalid bead ID format fails validation."""
        from dashboard.exceptions import BeadValidationError

        with pytest.raises(BeadValidationError):
            BeadService.validate_bead_id("no-dash-at-end-")


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
        with patch.object(BeadService, "get_bead") as mock_get:
            mock_get.side_effect = BeadNotFoundError("invalid")

            response = client.get("/api/beads/invalid")

            assert response.status_code == 404
            data = response.json()
            # Custom exception format
            assert "error" in data
            assert "message" in data

    def test_404_contains_bead_id(self) -> None:
        """Test that 404 error contains the requested bead ID."""
        with patch.object(BeadService, "get_bead") as mock_get:
            mock_get.side_effect = BeadNotFoundError("test-id-123")

            response = client.get("/api/beads/test-id-123")

            assert "test-id-123" in response.json()["message"]
