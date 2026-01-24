"""Tests for spawn_agent.py script.

This module tests the agent spawning functionality including role validation,
prompt path resolution, and environment variable configuration.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from spawn_agent import (
    ROLE_TO_LABEL,
    ROLE_TO_PROMPT,
    VALID_ROLES,
    get_prompt_path,
    validate_prompt_exists,
)


class TestValidRoles:
    """Tests for role validation."""

    def test_valid_roles_contains_developer(self) -> None:
        """Test that developer role is valid."""
        assert "developer" in VALID_ROLES

    def test_valid_roles_contains_qa(self) -> None:
        """Test that qa role is valid."""
        assert "qa" in VALID_ROLES

    def test_valid_roles_contains_tech_lead(self) -> None:
        """Test that tech_lead role is valid."""
        assert "tech_lead" in VALID_ROLES

    def test_valid_roles_contains_manager(self) -> None:
        """Test that manager role is valid."""
        assert "manager" in VALID_ROLES

    def test_valid_roles_contains_reviewer(self) -> None:
        """Test that reviewer role is valid."""
        assert "reviewer" in VALID_ROLES

    def test_valid_roles_has_five_entries(self) -> None:
        """Test that there are exactly five valid roles."""
        assert len(VALID_ROLES) == 5

    def test_all_roles_have_prompt_mapping(self) -> None:
        """Test that all valid roles have a corresponding prompt file."""
        for role in VALID_ROLES:
            assert role in ROLE_TO_PROMPT

    def test_all_roles_have_label_mapping(self) -> None:
        """Test that all valid roles have a label mapping (even if None)."""
        for role in VALID_ROLES:
            assert role in ROLE_TO_LABEL


class TestInvalidRoleRejected:
    """Tests that invalid roles are rejected."""

    def test_invalid_role_not_in_valid_roles(self) -> None:
        """Test that arbitrary strings are not valid roles."""
        assert "invalid_role" not in VALID_ROLES
        assert "admin" not in VALID_ROLES
        assert "tester" not in VALID_ROLES

    def test_argparse_rejects_invalid_role(self) -> None:
        """Test that argparse rejects invalid role via CLI."""
        result = subprocess.run(
            [sys.executable, "scripts/spawn_agent.py", "invalid_role"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "invalid choice" in result.stderr.lower()

    def test_argparse_shows_valid_choices_on_error(self) -> None:
        """Test that error message shows valid role choices."""
        result = subprocess.run(
            [sys.executable, "scripts/spawn_agent.py", "badguy"],
            capture_output=True,
            text=True,
        )
        # Error should mention at least one valid role
        assert "developer" in result.stderr or "qa" in result.stderr


class TestPromptPathExists:
    """Tests for prompt path resolution and validation."""

    def test_get_prompt_path_developer(self, tmp_path: Path) -> None:
        """Test get_prompt_path returns correct path for developer."""
        expected = tmp_path / "prompts" / "DEVELOPER.md"
        result = get_prompt_path("developer", tmp_path)
        assert result == expected

    def test_get_prompt_path_qa(self, tmp_path: Path) -> None:
        """Test get_prompt_path returns correct path for qa."""
        expected = tmp_path / "prompts" / "QA.md"
        result = get_prompt_path("qa", tmp_path)
        assert result == expected

    def test_get_prompt_path_tech_lead(self, tmp_path: Path) -> None:
        """Test get_prompt_path returns correct path for tech_lead."""
        expected = tmp_path / "prompts" / "TECH_LEAD.md"
        result = get_prompt_path("tech_lead", tmp_path)
        assert result == expected

    def test_get_prompt_path_manager(self, tmp_path: Path) -> None:
        """Test get_prompt_path returns correct path for manager."""
        expected = tmp_path / "prompts" / "MANAGER.md"
        result = get_prompt_path("manager", tmp_path)
        assert result == expected

    def test_get_prompt_path_reviewer(self, tmp_path: Path) -> None:
        """Test get_prompt_path returns correct path for reviewer."""
        expected = tmp_path / "prompts" / "CODE_REVIEWER.md"
        result = get_prompt_path("reviewer", tmp_path)
        assert result == expected

    def test_validate_prompt_exists_succeeds_when_file_exists(
        self, tmp_path: Path
    ) -> None:
        """Test validate_prompt_exists passes when file exists."""
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text("# Test Prompt")
        # Should not raise
        validate_prompt_exists(prompt_file)

    def test_validate_prompt_exists_exits_when_file_missing(
        self, tmp_path: Path
    ) -> None:
        """Test validate_prompt_exists exits when file is missing."""
        missing_file = tmp_path / "nonexistent.md"
        with pytest.raises(SystemExit) as exc_info:
            validate_prompt_exists(missing_file)
        assert exc_info.value.code == 1


class TestEnvironmentVariablesSet:
    """Tests for environment variable configuration in spawned agents."""

    def test_spawn_agent_sets_agent_role_env(self, tmp_path: Path) -> None:
        """Test that spawned agent command includes AGENT_ROLE env var."""
        # Create required prompt file
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "DEVELOPER.md"
        prompt_file.write_text("# Developer Prompt")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Import and call spawn function
            from spawn_agent import spawn_agent_macos

            spawn_agent_macos("developer", 1, tmp_path, prompt_file)

            # Verify subprocess.run was called
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            applescript = call_args[0][0][2]  # osascript -e <script>

            assert 'AGENT_ROLE="developer"' in applescript

    def test_spawn_agent_sets_agent_instance_env(self, tmp_path: Path) -> None:
        """Test that spawned agent command includes AGENT_INSTANCE env var."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "QA.md"
        prompt_file.write_text("# QA Prompt")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            from spawn_agent import spawn_agent_macos

            spawn_agent_macos("qa", 3, tmp_path, prompt_file)

            call_args = mock_run.call_args
            applescript = call_args[0][0][2]

            assert 'AGENT_INSTANCE="3"' in applescript

    def test_spawn_agent_sets_log_file_env(self, tmp_path: Path) -> None:
        """Test that spawned agent command includes AGENT_LOG_FILE env var."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "MANAGER.md"
        prompt_file.write_text("# Manager Prompt")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            from spawn_agent import spawn_agent_macos

            spawn_agent_macos("manager", 2, tmp_path, prompt_file)

            call_args = mock_run.call_args
            applescript = call_args[0][0][2]

            assert "AGENT_LOG_FILE=" in applescript
            assert "manager_2.log" in applescript


class TestOsascriptCommand:
    """Tests for osascript command building."""

    def test_osascript_called_with_correct_args(self, tmp_path: Path) -> None:
        """Test that subprocess.run is called with osascript command."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "DEVELOPER.md"
        prompt_file.write_text("# Developer Prompt")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            from spawn_agent import spawn_agent_macos

            spawn_agent_macos("developer", 1, tmp_path, prompt_file)

            call_args = mock_run.call_args
            command = call_args[0][0]

            assert command[0] == "osascript"
            assert command[1] == "-e"

    def test_applescript_contains_terminal_commands(self, tmp_path: Path) -> None:
        """Test that AppleScript includes Terminal application commands."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "TECH_LEAD.md"
        prompt_file.write_text("# Tech Lead Prompt")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            from spawn_agent import spawn_agent_macos

            spawn_agent_macos("tech_lead", 1, tmp_path, prompt_file)

            call_args = mock_run.call_args
            applescript = call_args[0][0][2]

            assert 'tell application "Terminal"' in applescript
            assert "activate" in applescript
            assert "do script" in applescript

    def test_subprocess_error_causes_exit(self, tmp_path: Path) -> None:
        """Test that subprocess error causes sys.exit."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "REVIEWER.md"
        prompt_file.write_text("# Reviewer Prompt")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "osascript", stderr=b"AppleScript error"
            )

            from spawn_agent import spawn_agent_macos

            with pytest.raises(SystemExit) as exc_info:
                spawn_agent_macos("reviewer", 1, tmp_path, prompt_file)

            assert exc_info.value.code == 1


class TestRoleLabels:
    """Tests for role-to-label mapping."""

    def test_developer_label_is_dev(self) -> None:
        """Test that developer maps to 'dev' label."""
        assert ROLE_TO_LABEL["developer"] == "dev"

    def test_qa_label_is_qa(self) -> None:
        """Test that qa maps to 'qa' label."""
        assert ROLE_TO_LABEL["qa"] == "qa"

    def test_tech_lead_label_is_architecture(self) -> None:
        """Test that tech_lead maps to 'architecture' label."""
        assert ROLE_TO_LABEL["tech_lead"] == "architecture"

    def test_manager_label_is_none(self) -> None:
        """Test that manager has no label filter (sees all)."""
        assert ROLE_TO_LABEL["manager"] is None

    def test_reviewer_label_is_review(self) -> None:
        """Test that reviewer maps to 'review' label."""
        assert ROLE_TO_LABEL["reviewer"] == "review"
