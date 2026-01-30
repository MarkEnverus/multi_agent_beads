"""Tests for the dashboard agents API endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.routes.agents import (
    _extract_agents_from_logs,
    _format_iso_timestamp,
    _infer_role_from_bead_title,
    _is_pid_running,
    _parse_log_file,
)

client = TestClient(app)


class TestAgentsEndpoints:
    """Tests for /api/agents endpoints."""

    def test_list_agents_empty_log(self) -> None:
        """Test listing agents when log file is empty or missing."""
        with patch("dashboard.routes.agents.LOG_FILE", "/nonexistent/path"):
            response = client.get("/api/agents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_agents_with_active_sessions(self, tmp_path: Path) -> None:
        """Test listing agents with active sessions in log."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: mab-abc - Test feature implementation
[2026-01-24 14:01:00] [1001] WORK_START: implementing feature
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        # Disable staleness check and mock PID/process checks for test
        with (
            patch("dashboard.routes.agents.LOG_FILE", str(log_file)),
            patch("dashboard.routes.agents.AGENT_STALE_MINUTES", 999999),
            patch("dashboard.routes.agents._is_pid_running", return_value=True),
            patch("dashboard.routes.agents._is_claude_agent_process", return_value=True),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["pid"] == 1001
            assert data[0]["current_bead"] == "mab-abc"
            assert data[0]["current_bead_title"] == "Test feature implementation"
            assert data[0]["status"] == "working"

    def test_list_agents_excludes_ended_sessions(self, tmp_path: Path) -> None:
        """Test that ended sessions are excluded from listing."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: mab-abc - Test task
[2026-01-24 14:01:00] [1001] SESSION_END: mab-abc
[2026-01-24 14:02:00] [1002] SESSION_START
[2026-01-24 14:02:05] [1002] CLAIM: mab-def - Another task
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        # Disable staleness check and mock PID/process checks for test
        with (
            patch("dashboard.routes.agents.LOG_FILE", str(log_file)),
            patch("dashboard.routes.agents.AGENT_STALE_MINUTES", 999999),
            patch("dashboard.routes.agents._is_pid_running", return_value=True),
            patch("dashboard.routes.agents._is_claude_agent_process", return_value=True),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            # Only the second agent should be active
            assert len(data) == 1
            assert data[0]["pid"] == 1002
            assert data[0]["current_bead"] == "mab-def"

    def test_list_agents_by_role(self, tmp_path: Path) -> None:
        """Test filtering agents by role."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: mab-abc - QA: Test coverage
[2026-01-24 14:02:00] [1002] SESSION_START
[2026-01-24 14:02:05] [1002] CLAIM: mab-def - Dashboard feature
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        # Disable staleness check and mock PID/process checks for test
        with (
            patch("dashboard.routes.agents.LOG_FILE", str(log_file)),
            patch("dashboard.routes.agents.AGENT_STALE_MINUTES", 999999),
            patch("dashboard.routes.agents._is_pid_running", return_value=True),
            patch("dashboard.routes.agents._is_claude_agent_process", return_value=True),
        ):
            # Filter by qa role
            response = client.get("/api/agents/qa")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "qa"

            # Filter by developer role
            response = client.get("/api/agents/developer")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "developer"

    def test_list_agents_invalid_role(self) -> None:
        """Test that invalid role returns 400 error."""
        response = client.get("/api/agents/invalid_role")
        assert response.status_code == 400
        assert "Invalid role" in response.json()["detail"]

    def test_list_agents_filters_stale_agents(self, tmp_path: Path) -> None:
        """Test that stale agents (no recent activity) are filtered out."""
        # Use very old timestamps that will definitely be stale
        log_content = """[2020-01-01 14:00:00] [1001] SESSION_START
[2020-01-01 14:00:05] [1001] CLAIM: mab-old - Old stale task
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        # With default staleness threshold (30 min), these old agents should be filtered
        with (
            patch("dashboard.routes.agents.LOG_FILE", str(log_file)),
            patch("dashboard.routes.agents.AGENT_STALE_MINUTES", 30),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            # Agent with old timestamp should be filtered out
            assert len(data) == 0


class TestLogParsing:
    """Tests for log parsing functions."""

    def test_parse_log_file_valid_entries(self, tmp_path: Path) -> None:
        """Test parsing valid log entries."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
[2026-01-24 14:00:05] [1001] CLAIM: mab-abc - Test task
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.agents.LOG_FILE", str(log_file)):
            entries = _parse_log_file()
            assert len(entries) == 2
            assert entries[0]["timestamp"] == "2026-01-24 14:00:00"
            assert entries[0]["pid"] == 1001
            assert entries[0]["content"] == "SESSION_START"

    def test_parse_log_file_skips_invalid_lines(self, tmp_path: Path) -> None:
        """Test that invalid log lines are skipped."""
        log_content = """[2026-01-24 14:00:00] [1001] SESSION_START
invalid line without proper format
[2026-01-24 14:00:05] [1001] CLAIM: mab-abc - Test
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.agents.LOG_FILE", str(log_file)):
            entries = _parse_log_file()
            assert len(entries) == 2

    def test_extract_agents_session_lifecycle(self) -> None:
        """Test agent extraction through full session lifecycle."""
        entries = [
            {"timestamp": "2026-01-24 14:00:00", "pid": 1001, "content": "SESSION_START"},
            {
                "timestamp": "2026-01-24 14:00:05",
                "pid": 1001,
                "content": "CLAIM: mab-abc - Task one",
            },
            {
                "timestamp": "2026-01-24 14:01:00",
                "pid": 1001,
                "content": "WORK_START: implementing",
            },
            {"timestamp": "2026-01-24 14:02:00", "pid": 1001, "content": "CLOSE: mab-abc"},
            {"timestamp": "2026-01-24 14:02:05", "pid": 1001, "content": "SESSION_END: mab-abc"},
        ]

        agents = _extract_agents_from_logs(entries)
        assert len(agents) == 1
        assert agents[1001]["status"] == "ended"
        assert agents[1001]["current_bead"] is None


class TestRoleInference:
    """Tests for role inference from bead titles."""

    @pytest.mark.parametrize(
        "title,expected_role",
        [
            ("QA: Test coverage", "qa"),
            ("Test verification task", "qa"),
            ("Verify acceptance criteria", "qa"),
            ("PR review for feature", "reviewer"),
            ("Code review task", "reviewer"),
            ("Architecture design", "tech_lead"),
            ("Tech lead decision", "tech_lead"),
            ("Epic: Big project", "manager"),
            ("Prioritize backlog", "manager"),
            ("Implement feature X", "developer"),
            ("Dashboard: Add button", "developer"),
            (None, "unknown"),
            ("", "unknown"),
        ],
    )
    def test_infer_role_from_title(self, title: str | None, expected_role: str) -> None:
        """Test role inference from various bead titles."""
        assert _infer_role_from_bead_title(title) == expected_role


class TestTimestampFormatting:
    """Tests for timestamp formatting."""

    def test_format_valid_timestamp(self) -> None:
        """Test formatting valid timestamp to ISO."""
        result = _format_iso_timestamp("2026-01-24 14:30:00")
        assert result == "2026-01-24T14:30:00Z"

    def test_format_invalid_timestamp(self) -> None:
        """Test that invalid timestamp is returned as-is."""
        result = _format_iso_timestamp("invalid")
        assert result == "invalid"


class TestPidVerification:
    """Tests for PID running verification."""

    def test_is_pid_running_current_process(self) -> None:
        """Test that current process PID is detected as running."""
        import os

        current_pid = os.getpid()
        assert _is_pid_running(current_pid) is True

    def test_is_pid_running_nonexistent_pid(self) -> None:
        """Test that obviously invalid PID is detected as not running."""
        # PID 99999999 is extremely unlikely to exist
        assert _is_pid_running(99999999) is False

    def test_is_pid_running_invalid_pids(self) -> None:
        """Test that invalid PIDs (zero or negative) are detected as not running."""
        assert _is_pid_running(0) is False
        assert _is_pid_running(-1) is False
        assert _is_pid_running(-999) is False

    def test_list_agents_filters_phantom_agents(self, tmp_path: Path) -> None:
        """Test that agents with non-running PIDs are filtered out."""
        # Use a PID that definitely doesn't exist
        fake_pid = 99999999
        log_content = f"""[2026-01-29 14:00:00] [1] SESSION_START
[2026-01-29 14:00:05] [1] CLAIM: mab-init - Init process (always running)
[2026-01-29 14:00:00] [{fake_pid}] SESSION_START
[2026-01-29 14:00:05] [{fake_pid}] CLAIM: mab-phantom - Phantom agent task
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        # Disable staleness check and mock PID/process checks for PID 1
        with (
            patch("dashboard.routes.agents.LOG_FILE", str(log_file)),
            patch("dashboard.routes.agents.AGENT_STALE_MINUTES", 999999),
            patch("dashboard.routes.agents._is_pid_running") as mock_pid_check,
            patch("dashboard.routes.agents._is_claude_agent_process", return_value=True),
        ):
            # PID 1 is running, fake_pid is not
            mock_pid_check.side_effect = lambda pid: pid == 1

            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            # Only PID 1 should be returned (phantom agent filtered out)
            assert len(data) == 1
            assert data[0]["pid"] == 1
            assert data[0]["current_bead"] == "mab-init"
