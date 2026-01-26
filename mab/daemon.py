"""MAB Daemon - Background process for worker lifecycle management.

This module implements the daemon process architecture for managing Claude Code
worker agents, including:
- PID file management for daemon tracking
- Lock file for single-instance enforcement
- Signal handling (SIGTERM, SIGINT) for clean shutdown
- Start/stop/status functionality
- File-based logging
"""

import fcntl
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class DaemonState(str, Enum):
    """Daemon lifecycle states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


@dataclass
class DaemonStatus:
    """Status information for the daemon."""

    state: DaemonState
    pid: int | None = None
    uptime_seconds: float | None = None
    started_at: str | None = None
    workers_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "state": self.state.value,
            "pid": self.pid,
            "uptime_seconds": self.uptime_seconds,
            "started_at": self.started_at,
            "workers_count": self.workers_count,
        }


class DaemonError(Exception):
    """Base exception for daemon operations."""

    pass


class DaemonAlreadyRunningError(DaemonError):
    """Raised when trying to start a daemon that's already running."""

    pass


class DaemonNotRunningError(DaemonError):
    """Raised when trying to stop a daemon that's not running."""

    pass


class Daemon:
    """MAB Daemon for managing worker lifecycle.

    The daemon runs as a background process and manages:
    - Worker spawning and monitoring
    - Health checks and auto-restart
    - Graceful shutdown coordination

    File layout:
        .mab/
        ├── daemon.pid        # Daemon process ID
        ├── daemon.lock       # Exclusive lock (flock)
        ├── daemon.log        # Daemon structured logs
        ├── mab.sock          # Unix socket for RPC (future)
        └── workers.db        # SQLite state database (future)
    """

    def __init__(self, mab_dir: Path | None = None) -> None:
        """Initialize daemon with configuration.

        Args:
            mab_dir: Path to .mab directory. Defaults to .mab in current dir.
        """
        self.mab_dir = mab_dir or Path(".mab")
        self.pid_file = self.mab_dir / "daemon.pid"
        self.lock_file = self.mab_dir / "daemon.lock"
        self.log_file = self.mab_dir / "daemon.log"

        self._lock_fd: int | None = None
        self._shutting_down = False
        self._started_at: datetime | None = None

        # Set up logging
        self._setup_logging()

    def _setup_logging(self) -> None:
        """Configure daemon logging to file."""
        self.logger = logging.getLogger("mab.daemon")
        self.logger.setLevel(logging.INFO)

        # Only add handler if not already present
        if not self.logger.handlers:
            # Ensure .mab directory exists for log file
            self.mab_dir.mkdir(parents=True, exist_ok=True)

            handler = logging.FileHandler(self.log_file)
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def _ensure_mab_dir(self) -> None:
        """Ensure .mab directory exists."""
        self.mab_dir.mkdir(parents=True, exist_ok=True)

    def _read_pid(self) -> int | None:
        """Read PID from pid file.

        Returns:
            Process ID if file exists and is valid, None otherwise.
        """
        if not self.pid_file.exists():
            return None

        try:
            pid_str = self.pid_file.read_text().strip()
            return int(pid_str) if pid_str else None
        except (ValueError, OSError):
            return None

    def _write_pid(self) -> None:
        """Write current process PID to pid file."""
        self.pid_file.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        """Remove pid file."""
        try:
            self.pid_file.unlink()
        except FileNotFoundError:
            pass

    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock to ensure single daemon instance.

        Returns:
            True if lock acquired, False if daemon already running.
        """
        self._ensure_mab_dir()

        try:
            self._lock_fd = os.open(
                str(self.lock_file),
                os.O_RDWR | os.O_CREAT,
                0o644,
            )
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            return False

    def _release_lock(self) -> None:
        """Release exclusive lock."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            finally:
                self._lock_fd = None

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running.

        Args:
            pid: Process ID to check.

        Returns:
            True if process exists and is running.
        """
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _install_signal_handlers(self) -> None:
        """Install signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle termination signals for graceful shutdown.

        Args:
            signum: Signal number received.
            frame: Current stack frame.
        """
        sig_name = signal.Signals(signum).name
        self.logger.info(f"Received {sig_name}, initiating graceful shutdown")
        self._shutting_down = True

    def _daemonize(self) -> None:
        """Fork process to run as a background daemon.

        Uses double-fork technique to completely detach from terminal.
        """
        # First fork
        pid = os.fork()
        if pid > 0:
            # Parent exits
            sys.exit(0)

        # Create new session and process group
        os.setsid()

        # Second fork to prevent acquiring a controlling terminal
        pid = os.fork()
        if pid > 0:
            # First child exits
            sys.exit(0)

        # Now we're the daemon process
        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()

        # Close standard file descriptors
        os.close(0)
        os.close(1)
        os.close(2)

        # Reopen as /dev/null
        os.open("/dev/null", os.O_RDONLY)  # stdin
        os.open("/dev/null", os.O_WRONLY)  # stdout
        os.open("/dev/null", os.O_WRONLY)  # stderr

    def is_running(self) -> bool:
        """Check if daemon is currently running.

        Returns:
            True if daemon is running.
        """
        pid = self._read_pid()
        if pid is None:
            return False
        return self._is_process_running(pid)

    def get_status(self) -> DaemonStatus:
        """Get current daemon status.

        Returns:
            DaemonStatus with current state information.
        """
        pid = self._read_pid()

        if pid is None:
            return DaemonStatus(state=DaemonState.STOPPED)

        if not self._is_process_running(pid):
            # Stale PID file - daemon crashed
            return DaemonStatus(state=DaemonState.STOPPED)

        # Read started_at from log if available
        started_at = None
        uptime_seconds = None

        try:
            # Look for most recent startup log entry
            if self.log_file.exists():
                with open(self.log_file) as f:
                    for line in f:
                        if "Daemon started" in line:
                            # Extract timestamp from log line
                            timestamp_str = line.split("[")[0].strip()
                            started_at = timestamp_str
        except OSError:
            pass

        if started_at:
            try:
                start_time = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                uptime_seconds = (datetime.now() - start_time).total_seconds()
            except ValueError:
                pass

        return DaemonStatus(
            state=DaemonState.RUNNING,
            pid=pid,
            uptime_seconds=uptime_seconds,
            started_at=started_at,
            workers_count=0,  # TODO: Read from workers.db
        )

    def start(self, foreground: bool = False) -> None:
        """Start the daemon.

        Args:
            foreground: If True, run in foreground (don't daemonize).

        Raises:
            DaemonAlreadyRunningError: If daemon is already running.
        """
        if self.is_running():
            pid = self._read_pid()
            raise DaemonAlreadyRunningError(f"Daemon already running (PID {pid})")

        self._ensure_mab_dir()

        if not foreground:
            self._daemonize()

        # Acquire lock (must be after daemonize since we're a new process)
        if not self._acquire_lock():
            raise DaemonAlreadyRunningError("Could not acquire lock - daemon may be starting")

        # Write PID file
        self._write_pid()

        # Install signal handlers
        self._install_signal_handlers()

        # Record start time
        self._started_at = datetime.now()

        self.logger.info(f"Daemon started (PID {os.getpid()})")

        # Run main event loop
        try:
            self._run()
        finally:
            self._cleanup()

    def _run(self) -> None:
        """Main daemon event loop.

        This is the core loop that:
        - Monitors worker health
        - Handles RPC requests (future)
        - Manages auto-restart (future)
        """
        self.logger.info("Entering main event loop")

        while not self._shutting_down:
            # For now, just sleep and check shutdown flag
            # Future: asyncio event loop with RPC handler
            time.sleep(1)

        self.logger.info("Exiting main event loop")

    def _cleanup(self) -> None:
        """Clean up daemon resources on shutdown."""
        self.logger.info("Cleaning up daemon resources")

        # Remove PID file
        self._remove_pid()

        # Release lock
        self._release_lock()

        self.logger.info("Daemon stopped")

    def stop(self, graceful: bool = True, timeout: float = 60.0) -> None:
        """Stop the daemon.

        Args:
            graceful: If True, send SIGTERM and wait. If False, send SIGKILL.
            timeout: Seconds to wait for graceful shutdown before force kill.

        Raises:
            DaemonNotRunningError: If daemon is not running.
        """
        pid = self._read_pid()

        if pid is None or not self._is_process_running(pid):
            # Clean up stale PID file if present
            self._remove_pid()
            raise DaemonNotRunningError("Daemon is not running")

        if graceful:
            # Send SIGTERM for graceful shutdown
            os.kill(pid, signal.SIGTERM)

            # Wait for process to exit
            start_time = time.time()
            while self._is_process_running(pid):
                if time.time() - start_time > timeout:
                    # Timeout - force kill
                    os.kill(pid, signal.SIGKILL)
                    break
                time.sleep(0.1)
        else:
            # Force kill immediately
            os.kill(pid, signal.SIGKILL)

        # Wait a bit for process to fully exit
        time.sleep(0.5)

        # Clean up PID file if daemon didn't
        if self.pid_file.exists():
            self._remove_pid()

    def restart(self, foreground: bool = False) -> None:
        """Restart the daemon.

        Args:
            foreground: If True, run in foreground after restart.
        """
        try:
            self.stop()
        except DaemonNotRunningError:
            pass  # OK if not running

        # Give time for cleanup
        time.sleep(0.5)

        self.start(foreground=foreground)


def get_default_daemon() -> Daemon:
    """Get a Daemon instance with default configuration.

    Returns:
        Daemon configured for current directory.
    """
    return Daemon()


def status_to_json(status: DaemonStatus) -> str:
    """Convert DaemonStatus to JSON string.

    Args:
        status: DaemonStatus to convert.

    Returns:
        JSON string representation.
    """
    return json.dumps(status.to_dict(), indent=2)
