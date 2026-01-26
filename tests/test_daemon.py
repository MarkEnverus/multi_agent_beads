"""Tests for MAB daemon process architecture."""

import os
import signal
from pathlib import Path

import pytest

from mab.daemon import (
    MAB_HOME,
    Daemon,
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    DaemonState,
    DaemonStatus,
    get_default_daemon,
    status_to_json,
)


class TestDaemonStatus:
    """Tests for DaemonStatus dataclass."""

    def test_status_to_dict(self) -> None:
        """Test DaemonStatus converts to dict correctly."""
        status = DaemonStatus(
            state=DaemonState.RUNNING,
            pid=12345,
            uptime_seconds=3600.5,
            started_at="2026-01-26 10:00:00",
            workers_count=3,
        )
        result = status.to_dict()

        assert result["state"] == "running"
        assert result["pid"] == 12345
        assert result["uptime_seconds"] == 3600.5
        assert result["started_at"] == "2026-01-26 10:00:00"
        assert result["workers_count"] == 3

    def test_status_stopped_defaults(self) -> None:
        """Test DaemonStatus with stopped state has correct defaults."""
        status = DaemonStatus(state=DaemonState.STOPPED)
        result = status.to_dict()

        assert result["state"] == "stopped"
        assert result["pid"] is None
        assert result["uptime_seconds"] is None
        assert result["started_at"] is None
        assert result["workers_count"] == 0

    def test_status_to_json(self) -> None:
        """Test status_to_json produces valid JSON."""
        status = DaemonStatus(
            state=DaemonState.RUNNING,
            pid=12345,
        )
        json_str = status_to_json(status)

        assert '"state": "running"' in json_str
        assert '"pid": 12345' in json_str


class TestDaemonPidFile:
    """Tests for PID file management."""

    def test_read_pid_missing_file(self, tmp_path: Path) -> None:
        """Test reading PID when file doesn't exist."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        assert daemon._read_pid() is None

    def test_read_pid_valid(self, tmp_path: Path) -> None:
        """Test reading valid PID from file."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("12345")

        daemon = Daemon(mab_dir=mab_dir)
        assert daemon._read_pid() == 12345

    def test_read_pid_invalid_content(self, tmp_path: Path) -> None:
        """Test reading PID with invalid content."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("not-a-number")

        daemon = Daemon(mab_dir=mab_dir)
        assert daemon._read_pid() is None

    def test_read_pid_empty_file(self, tmp_path: Path) -> None:
        """Test reading PID from empty file."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("")

        daemon = Daemon(mab_dir=mab_dir)
        assert daemon._read_pid() is None

    def test_write_and_read_pid(self, tmp_path: Path) -> None:
        """Test writing and reading PID."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()

        daemon = Daemon(mab_dir=mab_dir)
        daemon._write_pid()

        assert daemon._read_pid() == os.getpid()

    def test_remove_pid(self, tmp_path: Path) -> None:
        """Test removing PID file."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("12345")

        daemon = Daemon(mab_dir=mab_dir)
        daemon._remove_pid()

        assert not pid_file.exists()

    def test_remove_pid_missing_file(self, tmp_path: Path) -> None:
        """Test removing PID file when it doesn't exist (no error)."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()

        daemon = Daemon(mab_dir=mab_dir)
        daemon._remove_pid()  # Should not raise


class TestDaemonLock:
    """Tests for lock file management."""

    def test_acquire_lock_success(self, tmp_path: Path) -> None:
        """Test acquiring lock successfully."""
        mab_dir = tmp_path / ".mab"
        daemon = Daemon(mab_dir=mab_dir)

        assert daemon._acquire_lock() is True
        assert daemon._lock_fd is not None

        # Cleanup
        daemon._release_lock()

    def test_acquire_lock_creates_directory(self, tmp_path: Path) -> None:
        """Test acquiring lock creates .mab directory."""
        mab_dir = tmp_path / ".mab"
        assert not mab_dir.exists()

        daemon = Daemon(mab_dir=mab_dir)
        daemon._acquire_lock()

        assert mab_dir.exists()

        # Cleanup
        daemon._release_lock()

    def test_acquire_lock_blocked(self, tmp_path: Path) -> None:
        """Test that second daemon can't acquire lock."""
        mab_dir = tmp_path / ".mab"

        daemon1 = Daemon(mab_dir=mab_dir)
        daemon2 = Daemon(mab_dir=mab_dir)

        assert daemon1._acquire_lock() is True
        assert daemon2._acquire_lock() is False

        # Cleanup
        daemon1._release_lock()

    def test_release_lock(self, tmp_path: Path) -> None:
        """Test releasing lock."""
        mab_dir = tmp_path / ".mab"
        daemon = Daemon(mab_dir=mab_dir)

        daemon._acquire_lock()
        daemon._release_lock()

        assert daemon._lock_fd is None

        # Another daemon should be able to acquire now
        daemon2 = Daemon(mab_dir=mab_dir)
        assert daemon2._acquire_lock() is True
        daemon2._release_lock()


class TestDaemonProcessCheck:
    """Tests for process existence checking."""

    def test_is_process_running_current(self, tmp_path: Path) -> None:
        """Test checking if current process is running."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        assert daemon._is_process_running(os.getpid()) is True

    def test_is_process_running_nonexistent(self, tmp_path: Path) -> None:
        """Test checking if nonexistent process is running."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        # Use a very high PID that's unlikely to exist
        assert daemon._is_process_running(999999999) is False


class TestDaemonIsRunning:
    """Tests for is_running() method."""

    def test_is_running_no_pid_file(self, tmp_path: Path) -> None:
        """Test is_running when no PID file exists."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        assert daemon.is_running() is False

    def test_is_running_stale_pid(self, tmp_path: Path) -> None:
        """Test is_running with stale PID (process not running)."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("999999999")  # Nonexistent PID

        daemon = Daemon(mab_dir=mab_dir)
        assert daemon.is_running() is False

    def test_is_running_current_process(self, tmp_path: Path) -> None:
        """Test is_running when PID file points to current process."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text(str(os.getpid()))

        daemon = Daemon(mab_dir=mab_dir)
        assert daemon.is_running() is True


class TestDaemonGetStatus:
    """Tests for get_status() method."""

    def test_get_status_stopped(self, tmp_path: Path) -> None:
        """Test get_status when daemon is stopped."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        status = daemon.get_status()

        assert status.state == DaemonState.STOPPED
        assert status.pid is None

    def test_get_status_stale_pid(self, tmp_path: Path) -> None:
        """Test get_status with stale PID file."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("999999999")

        daemon = Daemon(mab_dir=mab_dir)
        status = daemon.get_status()

        assert status.state == DaemonState.STOPPED

    def test_get_status_running(self, tmp_path: Path) -> None:
        """Test get_status when daemon is running."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text(str(os.getpid()))

        daemon = Daemon(mab_dir=mab_dir)
        status = daemon.get_status()

        assert status.state == DaemonState.RUNNING
        assert status.pid == os.getpid()


class TestDaemonStart:
    """Tests for start() method."""

    def test_start_already_running_error(self, tmp_path: Path) -> None:
        """Test start raises error when daemon already running."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text(str(os.getpid()))

        daemon = Daemon(mab_dir=mab_dir)

        with pytest.raises(DaemonAlreadyRunningError):
            daemon.start(foreground=True)


class TestDaemonStop:
    """Tests for stop() method."""

    def test_stop_not_running_error(self, tmp_path: Path) -> None:
        """Test stop raises error when daemon not running."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        with pytest.raises(DaemonNotRunningError):
            daemon.stop()

    def test_stop_stale_pid_error(self, tmp_path: Path) -> None:
        """Test stop raises error with stale PID."""
        mab_dir = tmp_path / ".mab"
        mab_dir.mkdir()
        pid_file = mab_dir / "daemon.pid"
        pid_file.write_text("999999999")

        daemon = Daemon(mab_dir=mab_dir)

        with pytest.raises(DaemonNotRunningError):
            daemon.stop()

        # Stale PID file should be cleaned up
        assert not pid_file.exists()


class TestDaemonSignalHandler:
    """Tests for signal handler."""

    def test_signal_handler_sets_shutdown_flag(self, tmp_path: Path) -> None:
        """Test signal handler sets shutting_down flag."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        assert daemon._shutting_down is False

        daemon._signal_handler(signal.SIGTERM, None)

        assert daemon._shutting_down is True


class TestDaemonCLIIntegration:
    """Integration tests for daemon with CLI."""

    def test_status_command_stopped(self, tmp_path: Path) -> None:
        """Test status shows stopped when daemon not running."""
        from click.testing import CliRunner

        from mab.cli import cli

        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["status"])

            assert result.exit_code == 0
            assert "STOPPED" in result.output or "stopped" in result.output.lower()

    def test_status_json_output(self, tmp_path: Path) -> None:
        """Test status --json outputs valid JSON."""
        import json

        from click.testing import CliRunner

        from mab.cli import cli

        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["status", "--json"])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "state" in data
            assert data["state"] == "stopped"

    def test_stop_not_running_error(self, tmp_path: Path) -> None:
        """Test stop --all fails when daemon not running."""
        from click.testing import CliRunner

        from mab.cli import cli

        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["stop", "--all"])

            assert result.exit_code == 1
            assert "not running" in result.output.lower()

    def test_restart_help(self) -> None:
        """Test restart command help."""
        from click.testing import CliRunner

        from mab.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["restart", "--help"])

        assert result.exit_code == 0
        assert "--daemon" in result.output

    def test_help_shows_restart(self) -> None:
        """Test main help shows restart command."""
        from click.testing import CliRunner

        from mab.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "restart" in result.output


class TestGlobalDaemonLocation:
    """Tests for global daemon at ~/.mab/ location."""

    def test_mab_home_is_user_home(self) -> None:
        """Test MAB_HOME is at ~/.mab/."""
        assert MAB_HOME == Path.home() / ".mab"

    def test_default_daemon_uses_global_location(self) -> None:
        """Test get_default_daemon() uses ~/.mab/."""
        daemon = get_default_daemon()
        assert daemon.mab_dir == MAB_HOME
        assert daemon.pid_file == MAB_HOME / "daemon.pid"
        assert daemon.lock_file == MAB_HOME / "daemon.lock"
        assert daemon.log_file == MAB_HOME / "daemon.log"
        assert daemon.socket_path == MAB_HOME / "mab.sock"

    def test_default_daemon_no_town_path(self) -> None:
        """Test get_default_daemon() without town_path has no per-project state."""
        daemon = get_default_daemon()
        assert daemon.town_path is None
        assert daemon.town_mab_dir is None
        assert daemon.town_logs_dir is None
        assert daemon.town_heartbeat_dir is None
        assert daemon.town_config_file is None

    def test_default_daemon_with_town_path(self, tmp_path: Path) -> None:
        """Test get_default_daemon() with town_path sets up per-project paths."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        daemon = get_default_daemon(town_path=project_path)

        # Global daemon location
        assert daemon.mab_dir == MAB_HOME
        assert daemon.socket_path == MAB_HOME / "mab.sock"

        # Per-project locations
        assert daemon.town_path == project_path
        assert daemon.town_mab_dir == project_path / ".mab"
        assert daemon.town_logs_dir == project_path / ".mab" / "logs"
        assert daemon.town_heartbeat_dir == project_path / ".mab" / "heartbeat"
        assert daemon.town_config_file == project_path / ".mab" / "config.yaml"


class TestDaemonWithTownPath:
    """Tests for Daemon with per-project town_path."""

    def test_daemon_with_custom_mab_dir_and_town_path(self, tmp_path: Path) -> None:
        """Test Daemon with both custom mab_dir and town_path."""
        mab_dir = tmp_path / "global-mab"
        town_path = tmp_path / "project"
        mab_dir.mkdir()
        town_path.mkdir()

        daemon = Daemon(mab_dir=mab_dir, town_path=town_path)

        # Global daemon files at mab_dir
        assert daemon.mab_dir == mab_dir
        assert daemon.pid_file == mab_dir / "daemon.pid"
        assert daemon.socket_path == mab_dir / "mab.sock"

        # Per-project files at town_path
        assert daemon.town_path == town_path
        assert daemon.town_mab_dir == town_path / ".mab"
        assert daemon.town_logs_dir == town_path / ".mab" / "logs"

    def test_daemon_without_town_path(self, tmp_path: Path) -> None:
        """Test Daemon without town_path has global state only."""
        mab_dir = tmp_path / ".mab"

        daemon = Daemon(mab_dir=mab_dir)

        assert daemon.mab_dir == mab_dir
        assert daemon.town_path is None
        assert daemon.town_mab_dir is None
