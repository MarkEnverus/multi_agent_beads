"""Tests for mab CLI framework."""

from unittest.mock import patch

from click.testing import CliRunner

from mab.cli import (
    _get_town_for_project,
    _normalize_role_name,
    _validate_role_for_town,
    cli,
)
from mab.towns import Town
from mab.version import __version__


class TestMabCli:
    """Tests for mab CLI commands."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_help_shows_commands(self) -> None:
        """Test that --help shows available commands."""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Commands:" in result.output
        assert "init" in result.output
        assert "start" in result.output
        assert "stop" in result.output
        assert "status" in result.output
        assert "restart" in result.output

    def test_version_shows_version(self) -> None:
        """Test that --version shows version."""
        result = self.runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output
        assert "mab" in result.output

    def test_init_help(self) -> None:
        """Test init subcommand help."""
        result = self.runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "--template" in result.output
        assert "--force" in result.output

    def test_start_help(self) -> None:
        """Test start subcommand help."""
        result = self.runner.invoke(cli, ["start", "--help"])
        assert result.exit_code == 0
        assert "--daemon" in result.output
        assert "--workers" in result.output
        assert "--role" in result.output

    def test_stop_help(self) -> None:
        """Test stop subcommand help."""
        result = self.runner.invoke(cli, ["stop", "--help"])
        assert result.exit_code == 0
        assert "--all" in result.output
        assert "--graceful" in result.output
        assert "--timeout" in result.output

    def test_status_help(self) -> None:
        """Test status subcommand help."""
        result = self.runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--watch" in result.output
        assert "--json" in result.output

    def test_init_runs(self) -> None:
        """Test init command executes and creates config."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert "Initialized MAB project" in result.output
            assert ".mab" in result.output

    def test_init_creates_directory_structure(self) -> None:
        """Test init creates .mab directory structure."""
        import os

        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert os.path.isdir(".mab")
            assert os.path.isdir(".mab/logs")
            assert os.path.isdir(".mab/heartbeat")
            assert os.path.isfile(".mab/config.yaml")
            assert os.path.isfile(".mab/.gitignore")

    def test_init_config_content(self) -> None:
        """Test init creates valid config file."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 0

            with open(".mab/config.yaml") as f:
                config = f.read()
            assert "project:" in config
            assert "workers:" in config
            assert "max_workers:" in config

    def test_init_with_template_minimal(self) -> None:
        """Test init with minimal template."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init", "--template", "minimal"])
            assert result.exit_code == 0

            with open(".mab/config.yaml") as f:
                config = f.read()
            assert "Minimal" in config
            assert "max_workers: 2" in config

    def test_init_with_template_full(self) -> None:
        """Test init with full template."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init", "--template", "full"])
            assert result.exit_code == 0

            with open(".mab/config.yaml") as f:
                config = f.read()
            assert "Full" in config
            assert "roles:" in config
            assert "hooks:" in config

    def test_init_warns_not_git_repo(self) -> None:
        """Test init warns when not in git repo."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert "Warning" in result.output
            assert "git" in result.output.lower()

    def test_init_detects_beads(self) -> None:
        """Test init detects existing beads setup."""
        import os

        with self.runner.isolated_filesystem():
            os.makedirs(".beads")
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert "beads" in result.output.lower()

    def test_init_fails_if_exists(self) -> None:
        """Test init fails if already initialized."""
        with self.runner.isolated_filesystem():
            # First init
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 0

            # Second init should fail
            result = self.runner.invoke(cli, ["init"])
            assert result.exit_code == 1
            assert "already initialized" in result.output

    def test_init_force_overwrites(self) -> None:
        """Test init --force overwrites existing config."""
        with self.runner.isolated_filesystem():
            # First init
            self.runner.invoke(cli, ["init"])

            # Force init should succeed
            result = self.runner.invoke(cli, ["init", "--force"])
            assert result.exit_code == 0
            assert "Initialized MAB project" in result.output

    def test_init_creates_target_directory(self) -> None:
        """Test init creates target directory if it doesn't exist."""
        import os

        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["init", "new-project"])
            assert result.exit_code == 0
            assert os.path.isdir("new-project/.mab")

    def test_start_daemon_mode(self) -> None:
        """Test start command with --daemon flag calls daemon.start()."""
        with patch("mab.cli.Daemon") as mock_daemon_class:
            mock_daemon = mock_daemon_class.return_value
            mock_daemon.start.return_value = None

            result = self.runner.invoke(cli, ["start", "--daemon"])

            assert result.exit_code == 0
            assert "Starting MAB daemon" in result.output
            mock_daemon.start.assert_called_once_with(foreground=False)

    def test_stop_requires_argument_or_flag(self) -> None:
        """Test stop command requires worker_id or --all."""
        result = self.runner.invoke(cli, ["stop"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_stop_with_all_flag_not_running(self) -> None:
        """Test stop --all fails when daemon not running."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["stop", "--all"])
            assert result.exit_code == 1
            assert "not running" in result.output.lower()

    def test_stop_with_all_flag_success(self) -> None:
        """Test stop --all succeeds when daemon running."""
        with patch("mab.cli.Daemon") as mock_daemon_class:
            mock_daemon = mock_daemon_class.return_value
            mock_daemon.stop.return_value = None

            result = self.runner.invoke(cli, ["stop", "--all"])

            assert result.exit_code == 0
            assert "stopped successfully" in result.output.lower()
            mock_daemon.stop.assert_called_once()

    def test_status_runs(self) -> None:
        """Test status command executes."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["status"])
            assert result.exit_code == 0
            assert "MAB Status" in result.output
            assert "Daemon:" in result.output

    def test_status_json_output(self) -> None:
        """Test status --json outputs JSON."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["status", "--json"])
            assert result.exit_code == 0
            assert '"state"' in result.output
            assert '"pid"' in result.output


class TestMabSpawnCommand:
    """Tests for mab spawn command."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_spawn_help(self) -> None:
        """Test spawn subcommand help."""
        result = self.runner.invoke(cli, ["spawn", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output
        assert "--count" in result.output

    def test_spawn_requires_role(self) -> None:
        """Test spawn requires a role."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["spawn"])
            # Should fail without role
            assert result.exit_code != 0

    def test_spawn_invalid_role_rejected(self) -> None:
        """Test spawn rejects invalid role."""
        # Invalid role is rejected at CLI validation layer (exit code 2)
        result = self.runner.invoke(cli, ["spawn", "--role", "invalid"])
        assert result.exit_code == 2
        assert "invalid" in result.output.lower() or "invalid choice" in result.output.lower()


class TestMabListCommand:
    """Tests for mab list command."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_list_help(self) -> None:
        """Test list subcommand help."""
        result = self.runner.invoke(cli, ["list", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output or "--status" in result.output

    def test_list_when_daemon_not_running(self) -> None:
        """Test list fails gracefully when daemon not running."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["list"])
            # Should show error or empty list
            assert result.exit_code == 0 or "not running" in result.output.lower()


class TestMabTownCommands:
    """Tests for mab town management commands."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_town_list_help(self) -> None:
        """Test town list subcommand help."""
        result = self.runner.invoke(cli, ["town", "--help"])
        if result.exit_code == 0:
            assert "list" in result.output or "show" in result.output

    def test_town_create_help(self) -> None:
        """Test town create command shows help."""
        result = self.runner.invoke(cli, ["town", "create", "--help"])
        if result.exit_code == 0:
            assert "--port" in result.output or "--name" in result.output


class TestMabRestartCommand:
    """Tests for mab restart command."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_restart_help(self) -> None:
        """Test restart subcommand help."""
        result = self.runner.invoke(cli, ["restart", "--help"])
        assert result.exit_code == 0
        assert "--daemon" in result.output

    def test_restart_daemon_not_running(self) -> None:
        """Test restart fails when daemon not running."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["restart", "--daemon"])
            assert result.exit_code == 1
            assert "not running" in result.output.lower()


class TestMabConfigCommand:
    """Tests for mab config commands."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_config_show_help(self) -> None:
        """Test config show command help."""
        result = self.runner.invoke(cli, ["config", "--help"])
        if result.exit_code == 0:
            assert "show" in result.output or "get" in result.output

    def test_config_show_no_init(self) -> None:
        """Test config show when not initialized."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["config", "show"])
            if result.exit_code != 0:
                assert (
                    "not initialized" in result.output.lower()
                    or "not found" in result.output.lower()
                )


class TestMabLogsCommand:
    """Tests for mab logs command."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_logs_help(self) -> None:
        """Test logs subcommand help."""
        result = self.runner.invoke(cli, ["logs", "--help"])
        assert result.exit_code == 0
        assert "--follow" in result.output or "-f" in result.output

    def test_logs_when_no_workers(self) -> None:
        """Test logs command when no workers running."""
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["logs"])
            # Should either show empty or daemon not running message
            assert result.exit_code == 0 or result.exit_code == 1


class TestRoleNormalization:
    """Tests for role name normalization."""

    def test_normalize_role_name_with_hyphen(self) -> None:
        """Test normalizing role names with hyphens."""
        assert _normalize_role_name("tech-lead") == "tech_lead"

    def test_normalize_role_name_without_hyphen(self) -> None:
        """Test normalizing role names without hyphens."""
        assert _normalize_role_name("dev") == "dev"
        assert _normalize_role_name("qa") == "qa"
        assert _normalize_role_name("manager") == "manager"
        assert _normalize_role_name("reviewer") == "reviewer"

    def test_normalize_multiple_hyphens(self) -> None:
        """Test normalizing role names with multiple hyphens."""
        assert _normalize_role_name("tech-lead-senior") == "tech_lead_senior"


class TestRoleValidation:
    """Tests for role validation against town templates."""

    def test_validate_dev_role_pair_template(self) -> None:
        """Test dev role is valid for pair template."""
        town = Town(
            name="test-town",
            port=8000,
            template="pair",
            worker_counts={"dev": 1, "qa": 1},
        )
        is_valid, error_msg = _validate_role_for_town("dev", town)
        assert is_valid is True
        assert error_msg == ""

    def test_validate_qa_role_pair_template(self) -> None:
        """Test qa role is valid for pair template."""
        town = Town(
            name="test-town",
            port=8000,
            template="pair",
            worker_counts={"dev": 1, "qa": 1},
        )
        is_valid, error_msg = _validate_role_for_town("qa", town)
        assert is_valid is True
        assert error_msg == ""

    def test_validate_manager_role_pair_template_fails(self) -> None:
        """Test manager role is NOT valid for pair template."""
        town = Town(
            name="test-town",
            port=8000,
            template="pair",
            worker_counts={"dev": 1, "qa": 1},
        )
        is_valid, error_msg = _validate_role_for_town("manager", town)
        assert is_valid is False
        assert "manager" in error_msg
        assert "pair" in error_msg
        assert "dev" in error_msg
        assert "qa" in error_msg

    def test_validate_tech_lead_role_pair_template_fails(self) -> None:
        """Test tech-lead role is NOT valid for pair template."""
        town = Town(
            name="test-town",
            port=8000,
            template="pair",
            worker_counts={"dev": 1, "qa": 1},
        )
        is_valid, error_msg = _validate_role_for_town("tech-lead", town)
        assert is_valid is False
        assert "tech-lead" in error_msg
        assert "pair" in error_msg

    def test_validate_reviewer_role_pair_template_fails(self) -> None:
        """Test reviewer role is NOT valid for pair template."""
        town = Town(
            name="test-town",
            port=8000,
            template="pair",
            worker_counts={"dev": 1, "qa": 1},
        )
        is_valid, error_msg = _validate_role_for_town("reviewer", town)
        assert is_valid is False
        assert "reviewer" in error_msg

    def test_validate_dev_role_solo_template(self) -> None:
        """Test dev role is valid for solo template."""
        town = Town(
            name="test-town",
            port=8000,
            template="solo",
            worker_counts={"dev": 1},
        )
        is_valid, error_msg = _validate_role_for_town("dev", town)
        assert is_valid is True
        assert error_msg == ""

    def test_validate_qa_role_solo_template_fails(self) -> None:
        """Test qa role is NOT valid for solo template."""
        town = Town(
            name="test-town",
            port=8000,
            template="solo",
            worker_counts={"dev": 1},
        )
        is_valid, error_msg = _validate_role_for_town("qa", town)
        assert is_valid is False
        assert "qa" in error_msg
        assert "solo" in error_msg

    def test_validate_all_roles_full_template(self) -> None:
        """Test all roles are valid for full template."""
        town = Town(
            name="test-town",
            port=8000,
            template="full",
            worker_counts={
                "manager": 1,
                "tech_lead": 1,
                "dev": 1,
                "qa": 1,
                "reviewer": 1,
            },
        )
        for role in ["dev", "qa", "manager", "reviewer", "tech-lead"]:
            is_valid, error_msg = _validate_role_for_town(role, town)
            assert is_valid is True, f"Role {role} should be valid for full template"
            assert error_msg == ""

    def test_validate_role_uses_template_roles(self) -> None:
        """Test validation always uses template roles, ignoring worker_counts."""
        # Even if worker_counts is set differently, template roles determine validity
        town = Town(
            name="test-town",
            port=8000,
            template="pair",  # pair template has dev and qa
            worker_counts={"dev": 2, "reviewer": 1},  # Stale/legacy data
        )
        # QA should be valid because pair template includes it
        is_valid, error_msg = _validate_role_for_town("qa", town)
        assert is_valid is True

        # Reviewer should NOT be valid because pair template doesn't include it
        is_valid, error_msg = _validate_role_for_town("reviewer", town)
        assert is_valid is False


class TestGetTownForProject:
    """Tests for getting town by project path."""

    def test_get_town_for_nonexistent_project_returns_none(self) -> None:
        """Test getting town for non-existent project returns None."""
        # Using a path that definitely doesn't have a town
        result = _get_town_for_project("/nonexistent/path/12345")
        assert result is None


class TestSpawnRoleValidation:
    """Integration tests for spawn command role validation."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_spawn_validates_role_against_town_template(self) -> None:
        """Test spawn validates role against town template."""
        # Create a mock town that only allows dev
        mock_town = Town(
            name="test-town",
            port=8000,
            template="solo",
            worker_counts={"dev": 1},
        )

        with patch("mab.cli._get_town_for_project", return_value=mock_town):
            # Spawning manager (not in solo template) should fail
            result = self.runner.invoke(cli, ["spawn", "--role", "manager"])
            assert result.exit_code == 1
            assert "manager" in result.output.lower()
            assert "solo" in result.output.lower()

    def test_spawn_allows_valid_role(self) -> None:
        """Test spawn allows valid role for town template."""
        mock_town = Town(
            name="test-town",
            port=8000,
            template="pair",
            worker_counts={"dev": 1, "qa": 1},
        )

        with patch("mab.cli._get_town_for_project", return_value=mock_town):
            with patch("mab.cli.get_default_client") as mock_client:
                mock_client.return_value.call.return_value = {
                    "worker_id": "test-123",
                    "pid": 12345,
                }
                result = self.runner.invoke(cli, ["spawn", "--role", "dev"])
                assert result.exit_code == 0
                assert "Spawned dev worker" in result.output

    def test_spawn_works_without_town(self) -> None:
        """Test spawn works when no town is associated with project."""
        # When no town exists, validation is skipped
        with patch("mab.cli._get_town_for_project", return_value=None):
            with patch("mab.cli.get_default_client") as mock_client:
                mock_client.return_value.call.return_value = {
                    "worker_id": "test-123",
                    "pid": 12345,
                }
                result = self.runner.invoke(cli, ["spawn", "--role", "manager"])
                assert result.exit_code == 0


class TestWorkerAddRoleValidation:
    """Integration tests for worker add command role validation."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_worker_add_validates_role_against_town_template(self) -> None:
        """Test worker add validates role against town template."""
        mock_town = Town(
            name="test-town",
            port=8000,
            template="solo",
            worker_counts={"dev": 1},
        )

        with patch("mab.cli._get_town_for_project", return_value=mock_town):
            # Adding qa (not in solo template) should fail
            result = self.runner.invoke(cli, ["worker", "add", "qa"])
            assert result.exit_code == 1
            assert "qa" in result.output.lower()
            assert "solo" in result.output.lower()

    def test_worker_add_allows_valid_role(self) -> None:
        """Test worker add allows valid role for town template."""
        mock_town = Town(
            name="test-town",
            port=8000,
            template="full",
            worker_counts={
                "manager": 1,
                "tech_lead": 1,
                "dev": 1,
                "qa": 1,
                "reviewer": 1,
            },
        )

        with patch("mab.cli._get_town_for_project", return_value=mock_town):
            with patch("mab.cli.get_default_client") as mock_client:
                mock_client.return_value.call.return_value = {
                    "worker_id": "test-123",
                    "pid": 12345,
                }
                result = self.runner.invoke(cli, ["worker", "add", "manager"])
                assert result.exit_code == 0
                assert "Added manager worker" in result.output
