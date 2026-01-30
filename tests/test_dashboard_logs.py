"""Tests for the dashboard logs API endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.routes.logs import (
    _infer_role_from_content,
    _parse_log_line,
    _read_recent_logs,
)

client = TestClient(app)


class TestRecentLogsEndpoint:
    """Tests for /api/logs/recent endpoint."""

    def test_recent_logs_empty_log(self) -> None:
        """Test getting recent logs when log file is missing."""
        with patch("dashboard.routes.logs.LOG_FILE", "/nonexistent/path"):
            response = client.get("/api/logs/recent")
            assert response.status_code == 200
            assert response.json() == []

    def test_recent_logs_with_entries(self, tmp_path: Path) -> None:
        """Test getting recent logs with entries."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: multi_agent_beads-abc - Test feature
[2026-01-24 14:01:00] [1001] WORK_START: implementing feature
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.logs.LOG_FILE", str(log_file)):
            response = client.get("/api/logs/recent")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 3
            # Most recent first
            assert data[0]["event"] == "WORK_START"
            assert data[1]["event"] == "CLAIM"
            assert data[2]["event"] == "SESSION_START"

    def test_recent_logs_limit(self, tmp_path: Path) -> None:
        """Test limit parameter."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: multi_agent_beads-abc - Test
[2026-01-24 14:01:00] [1001] WORK_START: working
[2026-01-24 14:02:00] [1001] TESTS: running
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.logs.LOG_FILE", str(log_file)):
            response = client.get("/api/logs/recent?limit=2")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2

    def test_recent_logs_filter_by_role(self, tmp_path: Path) -> None:
        """Test filtering by role."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: multi_agent_beads-abc - QA: Test coverage
[2026-01-24 14:01:00] [1002] CLAIM: multi_agent_beads-def - Dashboard implementation
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.logs.LOG_FILE", str(log_file)):
            response = client.get("/api/logs/recent?role=qa")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "qa"

    def test_recent_logs_filter_by_bead_id(self, tmp_path: Path) -> None:
        """Test filtering by bead ID."""
        log_content = """[2026-01-24 14:00:00] [1001] CLAIM: multi_agent_beads-abc - Task one
[2026-01-24 14:01:00] [1002] CLAIM: multi_agent_beads-def - Task two
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.logs.LOG_FILE", str(log_file)):
            response = client.get("/api/logs/recent?bead_id=multi_agent_beads-abc")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["bead_id"] == "multi_agent_beads-abc"


class TestStreamLogsEndpoint:
    """Tests for /api/logs/stream endpoint.

    Note: Full streaming tests require manual testing as the SSE endpoint
    runs indefinitely. These tests verify the response format and setup.
    """

    def test_stream_logs_endpoint_exists(self) -> None:
        """Test that the streaming endpoint is registered correctly."""
        # Get OpenAPI schema to verify endpoint exists
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        assert "/api/logs/stream" in openapi["paths"]
        stream_endpoint = openapi["paths"]["/api/logs/stream"]["get"]
        assert stream_endpoint["tags"] == ["logs"]

    def test_stream_logs_query_params(self) -> None:
        """Test that streaming endpoint accepts filter parameters."""
        response = client.get("/openapi.json")
        openapi = response.json()
        stream_endpoint = openapi["paths"]["/api/logs/stream"]["get"]
        param_names = [p["name"] for p in stream_endpoint.get("parameters", [])]
        assert "role" in param_names
        assert "bead_id" in param_names


class TestLogParsing:
    """Tests for log parsing functions."""

    def test_parse_valid_log_line(self) -> None:
        """Test parsing a valid log line."""
        line = "[2026-01-24 14:00:05] [1001] CLAIM: multi_agent_beads-abc - Test feature"
        result = _parse_log_line(line)

        assert result is not None
        assert result["timestamp"] == "2026-01-24 14:00:05"
        assert result["pid"] == 1001
        assert result["event"] == "CLAIM"
        assert result["message"] == "multi_agent_beads-abc - Test feature"
        assert result["bead_id"] == "multi_agent_beads-abc"

    def test_parse_log_line_without_message(self) -> None:
        """Test parsing a log line without message."""
        line = "[2026-01-24 14:00:00] [1001] SESSION_START"
        result = _parse_log_line(line)

        assert result is not None
        assert result["event"] == "SESSION_START"
        assert result["message"] is None
        assert result["bead_id"] is None

    def test_parse_invalid_log_line(self) -> None:
        """Test parsing an invalid log line returns None."""
        assert _parse_log_line("") is None
        assert _parse_log_line("invalid line") is None
        assert _parse_log_line("   ") is None

    def test_parse_log_line_extracts_bead_id(self) -> None:
        """Test that bead IDs are extracted correctly."""
        line = "[2026-01-24 14:00:00] [1001] CLOSE: multi_agent_beads-xyz - Done"
        result = _parse_log_line(line)

        assert result is not None
        assert result["bead_id"] == "multi_agent_beads-xyz"


class TestRoleInference:
    """Tests for role inference from content."""

    @pytest.mark.parametrize(
        "event,message,expected_role",
        [
            ("CLAIM", "multi_agent_beads-abc - QA: Test coverage", "qa"),
            ("CLAIM", "multi_agent_beads-abc - Test verification", "qa"),
            ("CLAIM", "multi_agent_beads-abc - PR review task", "reviewer"),
            ("CLAIM", "multi_agent_beads-abc - Architecture design", "tech_lead"),
            ("CLAIM", "multi_agent_beads-abc - Epic management", "manager"),
            ("CLAIM", "multi_agent_beads-abc - Dashboard implementation", "developer"),
            ("CLAIM", "multi_agent_beads-abc - Add new feature", "developer"),
            ("CLAIM", "multi_agent_beads-abc - Fix bug", "developer"),
            ("WORK_START", None, None),
            ("SESSION_START", None, None),
        ],
    )
    def test_infer_role(self, event: str, message: str | None, expected_role: str | None) -> None:
        """Test role inference from event and message."""
        assert _infer_role_from_content(event, message) == expected_role


class TestReadRecentLogs:
    """Tests for _read_recent_logs function."""

    def test_read_recent_logs_missing_file(self) -> None:
        """Test reading logs when file doesn't exist."""
        with patch("dashboard.routes.logs.LOG_FILE", "/nonexistent/path"):
            result = _read_recent_logs()
            assert result == []

    def test_read_recent_logs_respects_limit(self, tmp_path: Path) -> None:
        """Test that limit is respected."""
        log_content = "\n".join([f"[2026-01-24 14:00:{i:02d}] [1001] EVENT{i}" for i in range(50)])
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.logs.LOG_FILE", str(log_file)):
            result = _read_recent_logs(limit=10)
            assert len(result) == 10
            # Most recent should be first (EVENT49)
            assert result[0]["event"] == "EVENT49"
