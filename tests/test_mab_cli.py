"""Tests for mab CLI framework."""

from unittest.mock import patch

from click.testing import CliRunner

from mab.cli import cli
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
