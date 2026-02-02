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
            {"timestamp": "2026-01-24 14:00:00", "pid": 1001, "worker_id": "1001", "content": "SESSION_START"},
            {
                "timestamp": "2026-01-24 14:00:05",
                "pid": 1001,
                "worker_id": "1001",
                "content": "CLAIM: mab-abc - Task one",
            },
            {
                "timestamp": "2026-01-24 14:01:00",
                "pid": 1001,
                "worker_id": "1001",
                "content": "WORK_START: implementing",
            },
            {"timestamp": "2026-01-24 14:02:00", "pid": 1001, "worker_id": "1001", "content": "CLOSE: mab-abc"},
            {"timestamp": "2026-01-24 14:02:05", "pid": 1001, "worker_id": "1001", "content": "SESSION_END: mab-abc"},
        ]

        agents = _extract_agents_from_logs(entries)
        assert len(agents) == 1
        # Now keyed by worker_id (string)
        assert agents["1001"]["status"] == "ended"
        assert agents["1001"]["current_bead"] is None


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


class TestWorkerIdTracking:
    """Tests for worker ID-based tracking (fixes PID instability bug).

    The bug: Claude Code runs each bash command in a new subshell, so PIDs change
    between SESSION_START, CLAIM, and SESSION_END. This breaks agent tracking.

    The fix: Use WORKER_ID (or SESSION_ID) for tracking instead of PIDs.
    """

    def test_pid_changes_break_session_tracking_without_worker_id(self) -> None:
        """Document the bug: varying PIDs without worker_id cause tracking to fail.

        When PIDs change per command AND there's no worker_id field,
        _extract_agents_from_logs sees each log entry as a separate session.

        The fix uses worker_id for tracking, so when logs include consistent
        worker_ids, tracking works correctly even with varying PIDs.
        """
        # This is what happens with OLD log format - each command gets a different PID
        # and no worker_id field (legacy entries)
        entries = [
            {
                "timestamp": "2026-02-02 10:00:00",
                "pid": 64632,
                "worker_id": "64632",  # Parsed from log, same as PID string
                "content": "SESSION_START",
            },
            {
                "timestamp": "2026-02-02 10:00:10",
                "pid": 67444,
                "worker_id": "67444",  # Different worker_id because different PID
                "content": "CLAIM: mab-test - Test task",
            },
            {
                "timestamp": "2026-02-02 10:00:20",
                "pid": 69988,
                "worker_id": "69988",  # Yet another worker_id
                "content": "WORK_START: doing work",
            },
            {
                "timestamp": "2026-02-02 10:00:30",
                "pid": 73180,
                "worker_id": "73180",  # Still different
                "content": "SESSION_END: mab-test",
            },
        ]

        agents = _extract_agents_from_logs(entries)

        # With different worker_ids (derived from PIDs), we get 4 separate "agents"
        # SESSION_START creates agent "64632", status="idle"
        # CLAIM has worker_id "67444" which doesn't exist - ignored
        # etc.
        assert len(agents) == 1, "Only SESSION_START entry creates an agent"
        assert agents["64632"]["status"] == "idle"  # Never updated to "working"!
        assert agents["64632"]["current_bead"] is None  # Never sees the CLAIM!

    def test_worker_id_based_tracking(self) -> None:
        """Test that worker ID-based log format enables correct tracking.

        When logs use a consistent worker ID instead of PIDs, session tracking
        works correctly even though the underlying PIDs change.
        """
        # With fix: worker_id stays consistent across all entries
        entries_with_worker_id = [
            {
                "timestamp": "2026-02-02 10:00:00",
                "pid": 0,  # Placeholder for non-numeric ID
                "worker_id": "worker-dev-1",  # Consistent worker ID
                "content": "SESSION_START",
            },
            {
                "timestamp": "2026-02-02 10:00:10",
                "pid": 0,
                "worker_id": "worker-dev-1",  # Same worker ID
                "content": "CLAIM: mab-test - Test task",
            },
            {
                "timestamp": "2026-02-02 10:00:20",
                "pid": 0,
                "worker_id": "worker-dev-1",  # Same worker ID
                "content": "SESSION_END: mab-test",
            },
        ]

        agents = _extract_agents_from_logs(entries_with_worker_id)

        # With consistent worker_id, all events are tracked to same agent
        assert len(agents) == 1
        assert "worker-dev-1" in agents
        agent = agents["worker-dev-1"]
        assert agent["status"] == "ended"
        assert agent["worker_id"] == "worker-dev-1"

    def test_parse_log_file_with_worker_id(self, tmp_path: Path) -> None:
        """Test parsing log entries that use worker IDs instead of PIDs.

        LOG_PATTERN now accepts alphanumeric worker IDs like "worker-dev-1".
        """
        log_content = """[2026-02-02 10:00:00] [worker-dev-1] SESSION_START
[2026-02-02 10:00:10] [worker-dev-1] CLAIM: mab-test - Test task
[2026-02-02 10:00:30] [worker-dev-1] SESSION_END: mab-test
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.agents.LOG_FILE", str(log_file)):
            entries = _parse_log_file()

            # Should parse all 3 entries with worker_id field
            assert len(entries) == 3, "Worker ID format should be parseable"
            assert entries[0]["worker_id"] == "worker-dev-1"
            assert entries[0]["pid"] == 0  # Placeholder for non-numeric
            assert entries[0]["content"] == "SESSION_START"
            assert entries[1]["worker_id"] == "worker-dev-1"
            assert entries[2]["worker_id"] == "worker-dev-1"

    def test_full_session_with_worker_id_tracking(self, tmp_path: Path) -> None:
        """End-to-end test: worker ID tracking through full session lifecycle."""
        log_content = """[2026-02-02 10:00:00] [worker-dev-1] SESSION_START
[2026-02-02 10:00:05] [worker-dev-1] CLAIM: mab-abc - Fix tracking bug
[2026-02-02 10:00:10] [worker-dev-1] WORK_START: investigating
[2026-02-02 10:00:20] [worker-dev-1] TESTS: running pytest
[2026-02-02 10:00:25] [worker-dev-1] CLOSE: mab-abc - PR merged
[2026-02-02 10:00:30] [worker-dev-1] SESSION_END: mab-abc
"""
        log_file = tmp_path / "test.log"
        log_file.write_text(log_content)

        with patch("dashboard.routes.agents.LOG_FILE", str(log_file)):
            entries = _parse_log_file()

            # Should have 6 entries, all with same worker_id
            assert len(entries) == 6
            for entry in entries:
                assert entry["worker_id"] == "worker-dev-1"

            # Extract agents using worker_id-aware function
            agents = _extract_agents_from_logs(entries)

            # Single agent tracked through full lifecycle
            assert len(agents) == 1
            agent = agents["worker-dev-1"]
            assert agent["status"] == "ended"
            assert agent["worker_id"] == "worker-dev-1"
            # CLOSE should have cleared the current_bead
            assert agent["current_bead"] is None
