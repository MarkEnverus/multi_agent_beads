"""Tests for mab CLI framework."""

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

    def test_status_help(self) -> None:
        """Test status subcommand help."""
        result = self.runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--watch" in result.output
        assert "--json" in result.output

    def test_init_runs(self) -> None:
        """Test init command executes."""
        result = self.runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Initializing MAB project" in result.output

    def test_start_runs(self) -> None:
        """Test start command executes."""
        result = self.runner.invoke(cli, ["start"])
        assert result.exit_code == 0
        assert "Starting" in result.output
        assert "worker" in result.output

    def test_stop_requires_argument_or_flag(self) -> None:
        """Test stop command requires worker_id or --all."""
        result = self.runner.invoke(cli, ["stop"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_stop_with_all_flag(self) -> None:
        """Test stop --all command executes."""
        result = self.runner.invoke(cli, ["stop", "--all"])
        assert result.exit_code == 0
        assert "Stopping all workers" in result.output

    def test_status_runs(self) -> None:
        """Test status command executes."""
        result = self.runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "MAB Status" in result.output

    def test_status_json_output(self) -> None:
        """Test status --json outputs JSON."""
        result = self.runner.invoke(cli, ["status", "--json"])
        assert result.exit_code == 0
        assert '"workers"' in result.output
        assert '"queue"' in result.output
