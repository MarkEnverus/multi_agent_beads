"""Tests for PR validation module."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from mab.bd_close import main as bd_close_main
from mab.pr_validation import (
    PRInfo,
    PRStatus,
    ValidationResult,
    get_pr_by_number,
    get_pr_for_bead,
    has_git_remote,
    is_code_bead,
    validate_close,
)


class TestHasGitRemote:
    """Tests for has_git_remote function."""

    def test_returns_true_when_remote_exists(self) -> None:
        """Test returns True when git remote output is non-empty."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="origin\thttps://github.com/user/repo.git (fetch)\n"
            )
            assert has_git_remote() is True

    def test_returns_false_when_no_remote(self) -> None:
        """Test returns False when git remote output is empty."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            assert has_git_remote() is False

    def test_returns_false_on_timeout(self) -> None:
        """Test returns False when command times out."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
            assert has_git_remote() is False


class TestGetPRForBead:
    """Tests for get_pr_for_bead function."""

    def test_returns_none_when_no_remote(self) -> None:
        """Test returns None when no git remote exists."""
        with patch("mab.pr_validation.has_git_remote", return_value=False):
            result = get_pr_for_bead("test-bead-123")
            assert result is None

    def test_returns_merged_pr_info(self) -> None:
        """Test returns PRInfo for merged PR."""
        pr_data = [
            {
                "number": 42,
                "title": "Fix test-bead-123",
                "state": "MERGED",
                "url": "https://github.com/user/repo/pull/42",
                "mergedAt": "2024-01-15T10:30:00Z",
            }
        ]

        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps(pr_data),
                )
                result = get_pr_for_bead("test-bead-123")

                assert result is not None
                assert result.number == 42
                assert result.status == PRStatus.MERGED
                assert result.title == "Fix test-bead-123"

    def test_returns_open_pr_info(self) -> None:
        """Test returns PRInfo for open PR."""
        pr_data = [
            {
                "number": 43,
                "title": "WIP: test-bead-456",
                "state": "OPEN",
                "url": "https://github.com/user/repo/pull/43",
            }
        ]

        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps(pr_data),
                )
                result = get_pr_for_bead("test-bead-456")

                assert result is not None
                assert result.number == 43
                assert result.status == PRStatus.OPEN

    def test_returns_none_when_no_prs_found(self) -> None:
        """Test returns None when no PRs reference the bead."""
        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="[]",
                )
                result = get_pr_for_bead("unknown-bead-999")
                assert result is None


class TestGetPRByNumber:
    """Tests for get_pr_by_number function."""

    def test_returns_pr_info_by_number(self) -> None:
        """Test returns PRInfo for specific PR number."""
        pr_data = {
            "number": 100,
            "title": "Feature implementation",
            "state": "MERGED",
            "url": "https://github.com/user/repo/pull/100",
            "mergedAt": "2024-01-20T15:00:00Z",
        }

        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps(pr_data),
                )
                result = get_pr_by_number(100)

                assert result is not None
                assert result.number == 100
                assert result.status == PRStatus.MERGED

    def test_returns_none_when_pr_not_found(self) -> None:
        """Test returns None when PR doesn't exist."""
        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                result = get_pr_by_number(9999)
                assert result is None


class TestIsCodeBead:
    """Tests for is_code_bead function."""

    def test_returns_false_for_docs_type(self) -> None:
        """Test returns False for documentation type beads."""
        bead_data = {"type": "docs", "labels": []}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(bead_data),
            )
            assert is_code_bead("docs-bead-123") is False

    def test_returns_false_for_config_type(self) -> None:
        """Test returns False for config type beads."""
        bead_data = {"type": "config", "labels": []}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(bead_data),
            )
            assert is_code_bead("config-bead-456") is False

    def test_returns_true_for_feature_type(self) -> None:
        """Test returns True for feature type beads."""
        bead_data = {"type": "feature", "labels": ["dev"]}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(bead_data),
            )
            assert is_code_bead("feature-bead-789") is True

    def test_returns_true_for_bug_type(self) -> None:
        """Test returns True for bug type beads."""
        bead_data = {"type": "bug", "labels": ["fix"]}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(bead_data),
            )
            assert is_code_bead("bug-bead-101") is True

    def test_returns_true_on_error(self) -> None:
        """Test defaults to True when bead info can't be fetched."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            # Should default to True (assume code bead) when uncertain
            assert is_code_bead("unknown-bead-xyz") is True


class TestValidateClose:
    """Tests for validate_close function."""

    def test_allows_force_close(self) -> None:
        """Test force flag bypasses all validation."""
        result = validate_close("any-bead-123", force=True)
        assert result.allowed is True
        assert "force" in result.reason.lower()

    def test_allows_no_pr_flag(self) -> None:
        """Test no-pr flag bypasses PR validation."""
        result = validate_close("docs-bead-456", no_pr=True)
        assert result.allowed is True
        assert "non-code" in result.reason.lower()

    def test_allows_when_no_remote(self) -> None:
        """Test allows close when no git remote configured."""
        with patch("mab.pr_validation.has_git_remote", return_value=False):
            result = validate_close("any-bead-789")
            assert result.allowed is True
            assert "no git remote" in result.reason.lower()

    def test_allows_non_code_bead(self) -> None:
        """Test allows close for non-code beads."""
        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("mab.pr_validation.is_code_bead", return_value=False):
                result = validate_close("docs-bead-101")
                assert result.allowed is True
                assert "non-code" in result.reason.lower()

    def test_allows_when_pr_merged(self) -> None:
        """Test allows close when PR is merged."""
        merged_pr = PRInfo(
            number=42,
            title="Fix bead",
            status=PRStatus.MERGED,
            url="https://github.com/user/repo/pull/42",
        )

        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("mab.pr_validation.is_code_bead", return_value=True):
                with patch("mab.pr_validation.get_pr_for_bead", return_value=merged_pr):
                    result = validate_close("code-bead-111")
                    assert result.allowed is True
                    assert result.pr_info is not None
                    assert result.pr_info.number == 42

    def test_denies_when_pr_open(self) -> None:
        """Test denies close when PR is still open."""
        open_pr = PRInfo(
            number=43,
            title="WIP bead",
            status=PRStatus.OPEN,
            url="https://github.com/user/repo/pull/43",
        )

        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("mab.pr_validation.is_code_bead", return_value=True):
                with patch("mab.pr_validation.get_pr_for_bead", return_value=open_pr):
                    result = validate_close("code-bead-222")
                    assert result.allowed is False
                    assert "still open" in result.reason.lower()
                    assert result.suggestions is not None

    def test_denies_when_no_pr_found(self) -> None:
        """Test denies close when no PR references the bead."""
        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("mab.pr_validation.is_code_bead", return_value=True):
                with patch("mab.pr_validation.get_pr_for_bead", return_value=None):
                    result = validate_close("code-bead-333")
                    assert result.allowed is False
                    assert "no pr found" in result.reason.lower()

    def test_uses_specific_pr_number(self) -> None:
        """Test validates specific PR number when provided."""
        merged_pr = PRInfo(
            number=100,
            title="Specific PR",
            status=PRStatus.MERGED,
        )

        with patch("mab.pr_validation.has_git_remote", return_value=True):
            with patch("mab.pr_validation.is_code_bead", return_value=True):
                with patch("mab.pr_validation.get_pr_by_number", return_value=merged_pr):
                    result = validate_close("code-bead-444", pr_number=100)
                    assert result.allowed is True


class TestBdCloseCli:
    """Tests for bd-close CLI command."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_help_shows_options(self) -> None:
        """Test that --help shows available options."""
        result = self.runner.invoke(bd_close_main, ["--help"])
        assert result.exit_code == 0
        assert "--pr" in result.output
        assert "--reason" in result.output
        assert "--no-pr" in result.output
        assert "--force" in result.output
        assert "--dry-run" in result.output

    def test_requires_bead_id(self) -> None:
        """Test command requires at least one bead ID."""
        result = self.runner.invoke(bd_close_main, [])
        assert result.exit_code != 0

    def test_dry_run_does_not_close(self) -> None:
        """Test dry-run validates but doesn't close."""
        with patch("mab.bd_close.validate_close") as mock_validate:
            mock_validate.return_value = ValidationResult(
                allowed=True, reason="Test allowed"
            )
            result = self.runner.invoke(
                bd_close_main, ["test-bead-123", "--dry-run", "--force"]
            )
            assert result.exit_code == 0
            assert "dry run" in result.output.lower()

    def test_force_bypasses_validation(self) -> None:
        """Test force flag allows close without PR."""
        with patch("mab.bd_close.validate_close") as mock_validate:
            mock_validate.return_value = ValidationResult(
                allowed=True, reason="Forced close"
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = self.runner.invoke(
                    bd_close_main, ["test-bead-123", "--force"]
                )
                assert result.exit_code == 0

    def test_fails_when_validation_fails(self) -> None:
        """Test exits with error when validation fails."""
        with patch("mab.bd_close.validate_close") as mock_validate:
            mock_validate.return_value = ValidationResult(
                allowed=False,
                reason="PR not merged",
                suggestions=["Merge the PR first"],
            )
            result = self.runner.invoke(bd_close_main, ["test-bead-123"])
            assert result.exit_code == 1
            assert "validation failed" in result.output.lower()

    def test_passes_reason_to_bd_close(self) -> None:
        """Test reason argument is passed to bd close."""
        with patch("mab.bd_close.validate_close") as mock_validate:
            mock_validate.return_value = ValidationResult(
                allowed=True, reason="Allowed"
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = self.runner.invoke(
                    bd_close_main,
                    ["test-bead-123", "--force", "--reason", "Test complete"],
                )

                assert result.exit_code == 0
                # Check that bd close was called with reason
                call_args = mock_run.call_args[0][0]
                assert "--reason" in call_args
                assert "Test complete" in call_args
