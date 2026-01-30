"""MAB Daemon - Background process for worker lifecycle management.

This module implements the daemon process architecture for managing Claude Code
worker agents, including:
- PID file management for daemon tracking
- Lock file for single-instance enforcement
- Signal handling (SIGTERM, SIGINT) for clean shutdown
- Start/stop/status functionality
- File-based logging
- RPC server for CLI communication

The daemon uses a hybrid global/local architecture:
- Global daemon state at ~/.mab/ (daemon.pid, mab.sock, workers.db)
- Per-project config at <project>/.mab/config.yaml
- Per-project worker logs at <project>/.mab/logs/
"""

import asyncio
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

from mab.rpc import RPCError, RPCErrorCode, RPCServer
from mab.workers import (
    HealthConfig,
    WorkerError,
    WorkerManager,
    WorkerNotFoundError,
    WorkerSpawnError,
    WorkerStatus,
)

# Global daemon location - one daemon per user manages all towns/projects
MAB_HOME = Path.home() / ".mab"


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

    File layout (hybrid global/local):
        ~/.mab/                   # GLOBAL daemon home (one per user)
        ├── daemon.pid            # Daemon process ID
        ├── daemon.lock           # Exclusive lock (flock)
        ├── daemon.log            # Daemon structured logs
        ├── mab.sock              # Unix socket for RPC
        ├── workers.db            # SQLite state (ALL workers)
        └── config.yaml           # Global defaults

        <project>/.mab/           # PER-PROJECT town config
        ├── config.yaml           # Town-specific overrides
        ├── logs/                 # Worker logs for this town
        └── heartbeat/            # Heartbeat files for workers
    """

    def __init__(
        self,
        mab_dir: Path | None = None,
        town_path: Path | None = None,
        health_config: HealthConfig | None = None,
    ) -> None:
        """Initialize daemon with configuration.

        Args:
            mab_dir: Path to global .mab directory. Defaults to ~/.mab/.
            town_path: Path to project directory for per-project config/logs.
                      If not specified, some per-project features won't work.
            health_config: Health monitoring configuration. Defaults to HealthConfig().
        """
        # Global daemon state lives at ~/.mab/ by default
        self.mab_dir = mab_dir or MAB_HOME
        self.pid_file = self.mab_dir / "daemon.pid"
        self.lock_file = self.mab_dir / "daemon.lock"
        self.log_file = self.mab_dir / "daemon.log"
        self.socket_path = self.mab_dir / "mab.sock"

        # Health configuration
        self.health_config = health_config or HealthConfig()

        # Per-project state (optional)
        self.town_path: Path | None = town_path
        self.town_mab_dir: Path | None = None
        self.town_logs_dir: Path | None = None
        self.town_heartbeat_dir: Path | None = None
        self.town_config_file: Path | None = None

        if town_path:
            self.town_mab_dir = town_path / ".mab"
            self.town_logs_dir = self.town_mab_dir / "logs"
            self.town_heartbeat_dir = self.town_mab_dir / "heartbeat"
            self.town_config_file = self.town_mab_dir / "config.yaml"

        self._lock_fd: int | None = None
        self._shutting_down = False
        self._started_at: datetime | None = None
        self._rpc_server: RPCServer | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._worker_manager: WorkerManager | None = None
        self._health_check_task: asyncio.Task[None] | None = None

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
        - Handles RPC requests
        - Manages auto-restart (future)
        """
        self.logger.info("Entering main event loop")

        # Run the async event loop
        try:
            asyncio.run(self._async_run())
        except Exception as e:
            self.logger.error(f"Event loop error: {e}")

        self.logger.info("Exiting main event loop")

    async def _async_run(self) -> None:
        """Async main event loop with RPC server."""
        self._event_loop = asyncio.get_running_loop()

        # Initialize worker manager with health config
        heartbeat_dir = (
            self.town_heartbeat_dir if self.town_heartbeat_dir else self.mab_dir / "heartbeat"
        )
        self._worker_manager = WorkerManager(
            mab_dir=self.mab_dir,
            heartbeat_dir=heartbeat_dir,
            health_config=self.health_config,
        )
        self.logger.info(
            f"Worker manager initialized (health_check_interval={self.health_config.health_check_interval_seconds}s, "
            f"max_restarts={self.health_config.max_restart_count})"
        )

        # Create and start RPC server
        self._rpc_server = RPCServer(mab_dir=self.mab_dir)
        self._register_rpc_handlers()

        await self._rpc_server.start()
        self.logger.info(f"RPC server listening on {self.socket_path}")

        # Start health check loop
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        self.logger.info("Health check loop started")

        try:
            # Main loop - check shutdown flag periodically
            while not self._shutting_down:
                await asyncio.sleep(1)
        finally:
            # Cancel health check task
            if self._health_check_task is not None:
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass
                self.logger.info("Health check loop stopped")

            # Cancel pending restarts
            if self._worker_manager is not None:
                cancelled = self._worker_manager.cancel_pending_restarts()
                if cancelled > 0:
                    self.logger.info(f"Cancelled {cancelled} pending restarts")

            # Stop all workers gracefully
            if self._worker_manager is not None:
                stopped = await self._worker_manager.stop_all(graceful=True)
                self.logger.info(f"Stopped {len(stopped)} workers on shutdown")

            # Stop RPC server
            if self._rpc_server is not None:
                await self._rpc_server.stop(graceful=True)
                self.logger.info("RPC server stopped")

    async def _health_check_loop(self) -> None:
        """Periodic health check for workers with auto-restart."""
        while not self._shutting_down:
            try:
                # Use configurable health check interval
                await asyncio.sleep(self.health_config.health_check_interval_seconds)

                if self._worker_manager is not None:
                    # Use health_check_and_restart for crash detection + auto-restart
                    (
                        crashed,
                        restart_scheduled,
                    ) = await self._worker_manager.health_check_and_restart()

                    for worker in crashed:
                        self.logger.warning(
                            f"Worker {worker.id} crashed (count: {worker.crash_count})"
                        )

                    for worker in restart_scheduled:
                        backoff = self.health_config.calculate_backoff(worker.crash_count)
                        self.logger.info(
                            f"Auto-restart scheduled for {worker.id} in {backoff:.1f}s"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Health check error: {e}")

    def _register_rpc_handlers(self) -> None:
        """Register RPC method handlers."""
        if self._rpc_server is None:
            return

        # Daemon control methods
        self._rpc_server.register("daemon.status", self._handle_status)
        self._rpc_server.register("daemon.shutdown", self._handle_shutdown)

        # Worker management methods
        self._rpc_server.register("worker.list", self._handle_worker_list)
        self._rpc_server.register("worker.spawn", self._handle_worker_spawn)
        self._rpc_server.register("worker.stop", self._handle_worker_stop)
        self._rpc_server.register("worker.get", self._handle_worker_get)

        # Health monitoring methods
        self._rpc_server.register("health.status", self._handle_health_status)

    async def _handle_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle daemon.status RPC request.

        Returns current daemon status including uptime and worker count.
        """
        uptime_seconds = None
        if self._started_at is not None:
            uptime_seconds = (datetime.now() - self._started_at).total_seconds()

        workers_count = 0
        if self._worker_manager is not None:
            workers_count = self._worker_manager.count_running()

        return {
            "state": DaemonState.RUNNING.value,
            "pid": os.getpid(),
            "uptime_seconds": uptime_seconds,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "workers_count": workers_count,
        }

    async def _handle_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle daemon.shutdown RPC request.

        Initiates graceful daemon shutdown.
        """
        graceful = params.get("graceful", True)
        self.logger.info(f"Shutdown requested via RPC (graceful={graceful})")

        # Schedule shutdown (don't block the response)
        if self._event_loop is not None:
            self._event_loop.call_soon(self._initiate_shutdown)

        return {"success": True, "message": "Shutdown initiated"}

    def _initiate_shutdown(self) -> None:
        """Initiate daemon shutdown from RPC handler."""
        self._shutting_down = True

    async def _handle_worker_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle worker.list RPC request.

        Returns list of all workers, optionally filtered by town/role/status.
        """
        if self._worker_manager is None:
            return {"workers": []}

        # Parse filters
        status_str = params.get("status")
        status = WorkerStatus(status_str) if status_str else None
        project_path = params.get("project_path")
        role = params.get("role")

        workers = self._worker_manager.list_workers(
            status=status,
            project_path=project_path,
            role=role,
        )

        return {"workers": [w.to_dict() for w in workers]}

    async def _handle_worker_spawn(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle worker.spawn RPC request.

        Spawns a new worker with the specified role.
        """
        if self._worker_manager is None:
            raise RPCError(
                RPCErrorCode.INTERNAL_ERROR,
                "Worker manager not initialized",
            )

        role = params.get("role")
        if not role:
            raise RPCError(
                RPCErrorCode.INVALID_PARAMS,
                "Missing required parameter: role",
            )

        project_path = params.get("project_path")
        if not project_path:
            raise RPCError(
                RPCErrorCode.INVALID_PARAMS,
                "Missing required parameter: project_path",
            )

        self.logger.info(f"Worker spawn requested: role={role}, project={project_path}")

        try:
            worker = await self._worker_manager.spawn(
                role=role,
                project_path=project_path,
                auto_restart=params.get("auto_restart", True),
            )

            self.logger.info(f"Worker spawned: {worker.id} (PID {worker.pid})")

            return {
                "worker_id": worker.id,
                "pid": worker.pid,
                "status": worker.status.value,
                "role": worker.role,
                "project_path": worker.project_path,
            }

        except WorkerSpawnError as e:
            self.logger.error(f"Worker spawn failed: {e}")
            raise RPCError(
                RPCErrorCode.INTERNAL_ERROR,
                f"Failed to spawn worker: {e}",
            )

    async def _handle_worker_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle worker.stop RPC request.

        Stops a worker by ID.
        """
        if self._worker_manager is None:
            raise RPCError(
                RPCErrorCode.INTERNAL_ERROR,
                "Worker manager not initialized",
            )

        worker_id = params.get("worker_id")
        if not worker_id:
            raise RPCError(
                RPCErrorCode.INVALID_PARAMS,
                "Missing required parameter: worker_id",
            )

        graceful = params.get("graceful", True)
        timeout = params.get("timeout", 30.0)

        self.logger.info(f"Worker stop requested: worker_id={worker_id}")

        try:
            worker = await self._worker_manager.stop(
                worker_id=worker_id,
                graceful=graceful,
                timeout=timeout,
            )

            self.logger.info(f"Worker stopped: {worker_id}")

            return {
                "success": True,
                "worker": worker.to_dict(),
            }

        except WorkerNotFoundError:
            raise RPCError(
                RPCErrorCode.INVALID_PARAMS,
                f"Worker not found: {worker_id}",
            )
        except WorkerError as e:
            self.logger.error(f"Worker stop failed: {e}")
            raise RPCError(
                RPCErrorCode.INTERNAL_ERROR,
                f"Failed to stop worker: {e}",
            )

    async def _handle_worker_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle worker.get RPC request.

        Returns details of a specific worker.
        """
        if self._worker_manager is None:
            raise RPCError(
                RPCErrorCode.INTERNAL_ERROR,
                "Worker manager not initialized",
            )

        worker_id = params.get("worker_id")
        if not worker_id:
            raise RPCError(
                RPCErrorCode.INVALID_PARAMS,
                "Missing required parameter: worker_id",
            )

        try:
            worker = self._worker_manager.get(worker_id)
            return {"worker": worker.to_dict()}

        except WorkerNotFoundError:
            raise RPCError(
                RPCErrorCode.INVALID_PARAMS,
                f"Worker not found: {worker_id}",
            )

    async def _handle_health_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle health.status RPC request.

        Returns current health status including worker counts and configuration.
        """
        if self._worker_manager is None:
            raise RPCError(
                RPCErrorCode.INTERNAL_ERROR,
                "Worker manager not initialized",
            )

        health_status = self._worker_manager.get_health_status()
        return health_status.to_dict()

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


def get_default_daemon(town_path: Path | None = None) -> Daemon:
    """Get a Daemon instance with default global configuration.

    The daemon always uses ~/.mab/ for global state. Optionally,
    a town_path can be specified for per-project config and logs.

    Args:
        town_path: Optional project directory for per-project state.
                   Defaults to current working directory if you need
                   project-specific features.

    Returns:
        Daemon configured with global ~/.mab/ location.
    """
    return Daemon(mab_dir=MAB_HOME, town_path=town_path)


def status_to_json(status: DaemonStatus) -> str:
    """Convert DaemonStatus to JSON string.

    Args:
        status: DaemonStatus to convert.

    Returns:
        JSON string representation.
    """
    return json.dumps(status.to_dict(), indent=2)
