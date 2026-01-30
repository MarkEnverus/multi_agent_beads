"""Tests for log validation script."""

from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.validate_logs import LogEntry, ValidationIssue, parse_log_line, validate_logs


class TestParseLogLine:
    """Tests for parse_log_line function."""

    def test_parse_valid_log_line(self) -> None:
        """Test parsing a valid log line."""
        line = "[2026-01-30 17:15:00] [12345] SESSION_START"
        entry = parse_log_line(line, 1)

        assert entry is not None
        assert entry.line_num == 1
        assert entry.timestamp == "2026-01-30 17:15:00"
        assert entry.pid == "12345"
        assert entry.message == "SESSION_START"

    def test_parse_log_with_fake_pid(self) -> None:
        """Test parsing a log line with literal $$."""
        line = "[2026-01-30 17:15:00] [$$] TESTS_PASSED: Chrome MCP testing"
        entry = parse_log_line(line, 42)

        assert entry is not None
        assert entry.pid == "$$"
        assert "TESTS_PASSED" in entry.message

    def test_parse_invalid_line(self) -> None:
        """Test parsing invalid log line returns None."""
        assert parse_log_line("not a log line", 1) is None
        assert parse_log_line("", 1) is None
        assert parse_log_line("[incomplete", 1) is None


class TestValidateLogs:
    """Tests for validate_logs function."""

    def test_detect_literal_dollar_dollar(self) -> None:
        """Test detection of literal [$$] in logs."""
        log_content = """[2026-01-30 17:15:00] [12345] SESSION_START
[2026-01-30 17:15:01] [$$] TESTS_PASSED: fake results
[2026-01-30 17:15:02] [12345] SESSION_END
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = Path(f.name)

        try:
            issues = validate_logs(log_path)
            fake_pid_issues = [i for i in issues if i.category == "fake_pid"]

            assert len(fake_pid_issues) == 1
            assert fake_pid_issues[0].severity == "ERROR"
            assert "shell variable not expanded" in fake_pid_issues[0].description
        finally:
            log_path.unlink()

    def test_detect_multiple_fake_pids(self) -> None:
        """Test detection of multiple fake PID entries."""
        log_content = """[2026-01-30 17:15:00] [$$] TC1: PASS
[2026-01-30 17:15:00] [$$] TC2: PASS
[2026-01-30 17:15:00] [$$] TC3: PASS
[2026-01-30 17:15:00] [$$] TESTS_PASSED: all tests
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = Path(f.name)

        try:
            issues = validate_logs(log_path)
            fake_pid_issues = [i for i in issues if i.category == "fake_pid"]

            assert len(fake_pid_issues) == 4
        finally:
            log_path.unlink()

    def test_detect_timestamp_batching(self) -> None:
        """Test detection of suspicious timestamp batching."""
        log_content = """[2026-01-30 17:15:00] [12345] TC1: PASS
[2026-01-30 17:15:00] [12345] TC2: PASS
[2026-01-30 17:15:00] [12345] TC3: PASS
[2026-01-30 17:15:00] [12345] TC4: PASS
[2026-01-30 17:15:00] [12345] TC5: PASS
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = Path(f.name)

        try:
            issues = validate_logs(log_path)
            batch_issues = [i for i in issues if i.category == "timestamp_batch"]

            assert len(batch_issues) == 1
            assert batch_issues[0].severity == "WARNING"
            assert "5 log entries" in batch_issues[0].description
        finally:
            log_path.unlink()

    def test_detect_tests_without_evidence(self) -> None:
        """Test detection of TESTS_PASSED without prior TESTS: entry."""
        log_content = """[2026-01-30 17:15:00] [12345] SESSION_START
[2026-01-30 17:15:01] [12345] WORK_START: doing work
[2026-01-30 17:15:02] [12345] TESTS_PASSED
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = Path(f.name)

        try:
            issues = validate_logs(log_path)
            no_evidence = [i for i in issues if i.category == "tests_no_evidence"]

            assert len(no_evidence) == 1
            assert no_evidence[0].severity == "WARNING"
        finally:
            log_path.unlink()

    def test_valid_log_passes(self) -> None:
        """Test that properly formatted logs pass validation."""
        log_content = """[2026-01-30 17:15:00] [12345] SESSION_START
[2026-01-30 17:15:01] [12346] CLAIM: beads-abc123 - Task
[2026-01-30 17:15:02] [12347] READ: beads-abc123
[2026-01-30 17:15:03] [12348] WORK_START: implementing feature
[2026-01-30 17:15:10] [12349] TESTS: running pytest
[2026-01-30 17:15:30] [12350] TESTS_PASSED
[2026-01-30 17:15:31] [12351] CLOSE: beads-abc123 - done
[2026-01-30 17:15:32] [12352] SESSION_END: beads-abc123
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = Path(f.name)

        try:
            issues = validate_logs(log_path)
            errors = [i for i in issues if i.severity == "ERROR"]
            # No errors for properly formatted logs
            assert len(errors) == 0
        finally:
            log_path.unlink()

    def test_missing_log_file(self) -> None:
        """Test handling of missing log file."""
        issues = validate_logs(Path("/nonexistent/path/to/log.log"))

        assert len(issues) == 1
        assert issues[0].severity == "ERROR"
        assert issues[0].category == "file_missing"

    def test_detect_large_pid_jump(self) -> None:
        """Test detection of large PID jumps indicating crash."""
        log_content = """[2026-01-30 17:15:00] [95413] WORK_START: testing
[2026-01-30 17:15:01] [36189] CLOSE: beads-xyz - done
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            log_path = Path(f.name)

        try:
            issues = validate_logs(log_path)
            pid_jump_issues = [i for i in issues if i.category == "pid_jump"]

            assert len(pid_jump_issues) == 1
            assert pid_jump_issues[0].severity == "WARNING"
            assert "95413" in pid_jump_issues[0].description
            assert "36189" in pid_jump_issues[0].description
        finally:
            log_path.unlink()


class TestValidationIssue:
    """Tests for ValidationIssue dataclass."""

    def test_issue_creation(self) -> None:
        """Test creating a validation issue."""
        issue = ValidationIssue(
            severity="ERROR",
            category="fake_pid",
            description="Test description",
            line_num=42,
            evidence="[$$] in log",
        )

        assert issue.severity == "ERROR"
        assert issue.category == "fake_pid"
        assert issue.line_num == 42


class TestLogEntry:
    """Tests for LogEntry dataclass."""

    def test_entry_creation(self) -> None:
        """Test creating a log entry."""
        entry = LogEntry(
            line_num=1,
            timestamp="2026-01-30 17:15:00",
            pid="12345",
            message="SESSION_START",
            raw="[2026-01-30 17:15:00] [12345] SESSION_START",
        )

        assert entry.line_num == 1
        assert entry.pid == "12345"
