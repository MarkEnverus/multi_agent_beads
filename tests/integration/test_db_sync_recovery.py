"""Integration tests for dashboard database out-of-sync auto-recovery.

This module tests the auto-recovery mechanism in BeadService that handles
database synchronization errors. When the JSONL file is newer than the SQLite
database (e.g., after a git pull from another machine), the bd CLI returns
sync errors. The dashboard should automatically recover by running
`bd sync --import-only` and retrying the command.

Test scenarios:
1. Mocked subprocess to verify recovery logic path
2. Real file manipulation to test actual recovery flow

These tests verify the fix from PR #16 (multi_agent_beads-v4yaw).
"""

import json
import os
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.services import BeadService
from dashboard.services.beads import DB_SYNC_ERROR_PATTERNS


class TestDbSyncErrorDetection:
    """Test the sync error detection logic."""

    def test_is_sync_error_detects_database_out_of_sync(self) -> None:
        """Should detect 'database out of sync' error message."""
        error = "Error: Database out of sync with JSONL"
        assert BeadService._is_sync_error(error) is True

    def test_is_sync_error_detects_jsonl_newer(self) -> None:
        """Should detect 'jsonl newer than db' error message."""
        error = "Warning: JSONL newer than db, run 'bd sync --import'"
        assert BeadService._is_sync_error(error) is True

    def test_is_sync_error_detects_stale_database(self) -> None:
        """Should detect 'stale database' error message."""
        error = "Error: Stale database detected"
        assert BeadService._is_sync_error(error) is True

    def test_is_sync_error_detects_sync_required(self) -> None:
        """Should detect 'sync required' error message."""
        error = "Sync required before operation"
        assert BeadService._is_sync_error(error) is True

    def test_is_sync_error_case_insensitive(self) -> None:
        """Should be case insensitive."""
        error = "DATABASE OUT OF SYNC"
        assert BeadService._is_sync_error(error) is True

    def test_is_sync_error_returns_false_for_other_errors(self) -> None:
        """Should return False for non-sync errors."""
        error = "Error: Bead not found"
        assert BeadService._is_sync_error(error) is False

    def test_is_sync_error_returns_false_for_empty(self) -> None:
        """Should return False for empty string."""
        assert BeadService._is_sync_error("") is False

    def test_is_sync_error_returns_false_for_none(self) -> None:
        """Should handle None gracefully."""
        # Type checking would catch this, but test runtime behavior
        assert BeadService._is_sync_error(None) is False  # type: ignore[arg-type]


class TestDbSyncRecoveryMocked:
    """Test auto-recovery using mocked subprocess calls."""

    @patch("dashboard.services.beads.subprocess.run")
    def test_recovery_triggered_on_sync_error(self, mock_run: MagicMock) -> None:
        """Should attempt recovery when sync error detected."""
        # First call: return sync error
        # Second call (recovery): success
        # Third call (retry): success
        mock_run.side_effect = [
            # First bd list call - fails with sync error
            MagicMock(returncode=1, stdout="", stderr="Database out of sync"),
            # bd sync --import-only - succeeds
            MagicMock(returncode=0, stdout="Sync complete", stderr=""),
            # Retry bd list - succeeds
            MagicMock(returncode=0, stdout="[]", stderr=""),
        ]

        # Should not raise - recovery should succeed
        result = BeadService.run_command(["list", "--json"])

        assert result == "[]"
        assert mock_run.call_count == 3

        # Verify the calls
        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["bd", "list", "--json"]
        assert calls[1][0][0] == ["bd", "sync", "--import-only"]
        assert calls[2][0][0] == ["bd", "list", "--json"]

    @patch("dashboard.services.beads.subprocess.run")
    def test_recovery_not_triggered_on_other_errors(self, mock_run: MagicMock) -> None:
        """Should not attempt recovery for non-sync errors."""
        # Return a different error (not found)
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Bead not found: test-123",
        )

        from dashboard.exceptions import BeadCommandError

        with pytest.raises(BeadCommandError):
            BeadService.run_command(["show", "test-123", "--json"])

        # Should only be called once - no recovery attempted
        assert mock_run.call_count == 1

    @patch("dashboard.services.beads.subprocess.run")
    def test_recovery_retries_only_once(self, mock_run: MagicMock) -> None:
        """Should not retry infinitely if recovery fails."""
        # All calls return sync error
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Database out of sync",
        )

        from dashboard.exceptions import BeadCommandError

        with pytest.raises(BeadCommandError):
            BeadService.run_command(["list", "--json"])

        # Should be: original call + recovery attempt (which fails)
        # When recovery fails, it falls through to raise the original error
        # without retrying the command
        assert mock_run.call_count == 2

    @patch("dashboard.services.beads.subprocess.run")
    def test_recovery_fails_gracefully(self, mock_run: MagicMock) -> None:
        """Should raise original error if recovery fails."""
        mock_run.side_effect = [
            # Original command fails with sync error
            MagicMock(returncode=1, stdout="", stderr="Database out of sync"),
            # Recovery command fails
            MagicMock(returncode=1, stdout="", stderr="Recovery failed"),
        ]

        from dashboard.exceptions import BeadCommandError

        with pytest.raises(BeadCommandError) as exc_info:
            BeadService.run_command(["list", "--json"])

        assert "Database out of sync" in str(exc_info.value)
        assert mock_run.call_count == 2


class TestDbSyncRecoveryApiIntegration:
    """Test auto-recovery through API endpoints using mocked subprocess."""

    @patch("dashboard.services.beads.subprocess.run")
    def test_api_beads_recovers_from_sync_error(self, mock_run: MagicMock) -> None:
        """API /api/beads should recover from sync error and return data."""
        # Clear any cached data
        BeadService.invalidate_cache()

        # Complete bead data with all required fields
        complete_bead = json.dumps(
            [
                {
                    "id": "test-123",
                    "title": "Test Bead",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "task",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        )

        # Mock responses
        mock_run.side_effect = [
            # First call (list with --all): sync error
            MagicMock(returncode=1, stdout="", stderr="JSONL newer than db"),
            # Recovery call
            MagicMock(returncode=0, stdout="", stderr=""),
            # Retry list with --all: success
            MagicMock(returncode=0, stdout=complete_bead, stderr=""),
            # blocked call: success
            MagicMock(returncode=0, stdout="[]", stderr=""),
        ]

        client = TestClient(app)
        response = client.get("/api/beads")

        # Should succeed after recovery
        assert response.status_code == 200
        # Response should be a list of beads
        data = response.json()
        assert isinstance(data, list)
        # Should have the test bead we mocked
        assert len(data) >= 1
        assert data[0]["id"] == "test-123"

    @patch("dashboard.services.beads.subprocess.run")
    def test_api_beads_ready_recovers_from_sync_error(self, mock_run: MagicMock) -> None:
        """API /api/beads/ready should recover from sync error."""
        BeadService.invalidate_cache()

        # Complete bead data with all required fields
        complete_bead = json.dumps(
            [
                {
                    "id": "ready-1",
                    "title": "Ready task",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "task",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        )

        mock_run.side_effect = [
            # First call: sync error
            MagicMock(returncode=1, stdout="", stderr="stale database"),
            # Recovery
            MagicMock(returncode=0, stdout="", stderr=""),
            # Retry: success
            MagicMock(returncode=0, stdout=complete_bead, stderr=""),
        ]

        client = TestClient(app)
        response = client.get("/api/beads/ready")

        assert response.status_code == 200


class TestDbSyncRecoveryRealFiles:
    """Test auto-recovery with actual file manipulation.

    These tests manipulate the real .beads directory and should only run
    in development environments with proper safeguards.

    IMPORTANT: These tests create backup and restore state, but should
    NOT be run in CI or production environments.
    """

    @pytest.fixture
    def beads_dir(self) -> Path:
        """Get the .beads directory path."""
        # Look for .beads in current directory or parent
        current = Path.cwd()
        beads_path = current / ".beads"
        if beads_path.exists():
            return beads_path
        # Check parent (in case running from tests dir)
        parent_beads = current.parent / ".beads"
        if parent_beads.exists():
            return parent_beads
        pytest.skip(".beads directory not found")

    @pytest.fixture
    def backup_jsonl(self, beads_dir: Path) -> Generator[tuple[Path, bytes], None, None]:
        """Backup the JSONL file before test and restore after."""
        jsonl_path = beads_dir / "issues.jsonl"
        if not jsonl_path.exists():
            pytest.skip("issues.jsonl not found")

        # Read original content
        original_content = jsonl_path.read_bytes()
        original_mtime = jsonl_path.stat().st_mtime

        yield jsonl_path, original_content

        # Restore original content
        jsonl_path.write_bytes(original_content)
        # Restore original mtime
        os.utime(jsonl_path, (original_mtime, original_mtime))

    @pytest.mark.skip(reason="Modifies real .beads directory - run manually")
    def test_real_recovery_with_corrupted_jsonl(
        self,
        beads_dir: Path,
        backup_jsonl: tuple[Path, bytes],
    ) -> None:
        """Test recovery by actually corrupting the JSONL file.

        This test:
        1. Appends a fake entry to issues.jsonl
        2. Updates the mtime to be newer than db
        3. Makes an API call that triggers sync error
        4. Verifies auto-recovery succeeds
        """
        jsonl_path, _ = backup_jsonl

        # Create a fake entry that won't exist in the database
        fake_entry = {
            "id": "test_recovery_fake_12345",
            "title": "Fake entry for recovery test",
            "status": "open",
            "priority": 4,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        # Append fake entry to JSONL
        with open(jsonl_path, "a") as f:
            f.write("\n" + json.dumps(fake_entry))

        # Touch JSONL to make it newer than the database
        future_time = time.time() + 60  # 1 minute in future
        os.utime(jsonl_path, (future_time, future_time))

        # Clear cache to force fresh fetch
        BeadService.invalidate_cache()

        # Make API call - should trigger recovery
        client = TestClient(app)
        response = client.get("/api/beads")

        # Should succeed (recovery worked)
        assert response.status_code == 200

        # The fake entry should now be in the database after recovery
        # (bd sync --import-only would have imported it)

    @pytest.mark.skip(reason="Modifies real .beads directory - run manually")
    def test_real_recovery_with_touched_jsonl(
        self,
        beads_dir: Path,
        backup_jsonl: tuple[Path, bytes],
    ) -> None:
        """Test recovery by only touching the JSONL file timestamp.

        A simpler test that just makes JSONL appear newer without
        modifying its content.
        """
        jsonl_path, _ = backup_jsonl

        # Just touch the file to make it newer
        future_time = time.time() + 60
        os.utime(jsonl_path, (future_time, future_time))

        BeadService.invalidate_cache()

        client = TestClient(app)
        response = client.get("/api/beads")

        # Even if bd detects the timestamp difference, recovery should work
        assert response.status_code == 200


class TestDbSyncErrorPatterns:
    """Test that error patterns match expected bd CLI messages."""

    def test_all_patterns_are_lowercase(self) -> None:
        """All patterns should be lowercase for case-insensitive matching."""
        for pattern in DB_SYNC_ERROR_PATTERNS:
            assert pattern == pattern.lower(), f"Pattern not lowercase: {pattern}"

    def test_patterns_cover_known_errors(self) -> None:
        """Verify patterns cover all known bd CLI sync error messages."""
        known_errors = [
            "Error: Database out of sync",
            "Warning: JSONL newer than db",
            "Auto-import failed - manual sync required",
            "Error: Stale database",
            "Sync required before write",
        ]

        for error in known_errors:
            assert BeadService._is_sync_error(error), f"Pattern should match: {error}"
