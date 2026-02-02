"""Dashboard manager for multi-project dashboard instances.

Manages dashboard processes across multiple projects with:
- Auto-port assignment (8000, 8001, 8002...)
- PID file tracking
- Project-specific isolation
"""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from mab.daemon import MAB_HOME
from mab.filesystem import warn_if_network_filesystem


@dataclass
class DashboardInfo:
    """Information about a running dashboard instance."""

    project_path: str
    port: int
    pid: int | None
    project_hash: str
    log_file: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "project_path": self.project_path,
            "port": self.port,
            "pid": self.pid,
            "project_hash": self.project_hash,
            "log_file": self.log_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DashboardInfo":
        """Create from dictionary."""
        return cls(
            project_path=data["project_path"],
            port=data["port"],
            pid=data.get("pid"),
            project_hash=data["project_hash"],
            log_file=data.get("log_file", ""),
        )


class DashboardManager:
    """Manages dashboard instances across multiple projects."""

    def __init__(self, mab_home: Path | None = None) -> None:
        """Initialize the dashboard manager.

        Args:
            mab_home: Path to MAB home directory. Defaults to ~/.mab/
        """
        self.mab_home = mab_home or MAB_HOME
        self.dashboards_file = self.mab_home / "dashboards.json"
        self.logs_dir = self.mab_home / "logs"

        # Ensure directories exist
        self.mab_home.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Warn if on network filesystem (flock doesn't work reliably on NFS/CIFS)
        warn_if_network_filesystem(self.mab_home, context="Dashboard manager")

        # Lock file for synchronizing dashboard operations
        self._lock_file = self.mab_home / "dashboard.lock"

    @contextmanager
    def _dashboard_lock(self) -> Iterator[None]:
        """Acquire an exclusive lock for dashboard operations.

        This prevents race conditions when multiple processes try to
        start dashboards simultaneously.
        """
        # Create lock file if it doesn't exist
        self._lock_file.touch(exist_ok=True)

        lock_fd = open(self._lock_file, "r")
        try:
            # Acquire exclusive lock (blocks until available)
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            # Release lock
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()

    def _get_project_hash(self, project_path: Path) -> str:
        """Generate a short hash for a project path."""
        path_str = str(project_path.resolve())
        return hashlib.sha256(path_str.encode()).hexdigest()[:12]

    def _get_pid_file(self, project_hash: str) -> Path:
        """Get PID file path for a project."""
        return self.mab_home / f"dashboard-{project_hash}.pid"

    def _get_log_file(self, project_name: str) -> Path:
        """Get log file path for a project."""
        # Sanitize project name for filename
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
        return self.logs_dir / f"dashboard-{safe_name}.log"

    def _load_dashboards(self) -> dict[str, DashboardInfo]:
        """Load dashboard registry from file."""
        if not self.dashboards_file.exists():
            return {}

        try:
            data = json.loads(self.dashboards_file.read_text())
            return {k: DashboardInfo.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, KeyError):
            return {}

    def _save_dashboards(self, dashboards: dict[str, DashboardInfo]) -> None:
        """Save dashboard registry to file."""
        data = {k: v.to_dict() for k, v in dashboards.items()}
        self.dashboards_file.write_text(json.dumps(data, indent=2))

    def _find_available_port(self, dashboards: dict[str, DashboardInfo]) -> int:
        """Find the next available port starting from 8000."""
        used_ports = {d.port for d in dashboards.values()}
        port = 8000
        while port in used_ports:
            port += 1
        return port

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with the given PID is running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _read_pid_file(self, pid_file: Path) -> int | None:
        """Read PID from a PID file."""
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def _write_pid_file(self, pid_file: Path, pid: int) -> None:
        """Write PID to a PID file."""
        pid_file.write_text(str(pid))

    def _remove_pid_file(self, pid_file: Path) -> None:
        """Remove a PID file if it exists."""
        if pid_file.exists():
            pid_file.unlink()

    def get_dashboard(self, project_path: Path) -> DashboardInfo | None:
        """Get dashboard info for a project if it exists and is running.

        Args:
            project_path: Path to the project directory.

        Returns:
            DashboardInfo if running, None otherwise.
        """
        project_hash = self._get_project_hash(project_path)
        dashboards = self._load_dashboards()

        if project_hash not in dashboards:
            return None

        dashboard = dashboards[project_hash]
        pid_file = self._get_pid_file(project_hash)
        pid = self._read_pid_file(pid_file)

        if pid and self._is_process_running(pid):
            dashboard.pid = pid
            return dashboard

        # Process not running, clean up
        self._remove_pid_file(pid_file)
        del dashboards[project_hash]
        self._save_dashboards(dashboards)
        return None

    def list_dashboards(self) -> list[DashboardInfo]:
        """List all registered dashboards, filtering out dead ones.

        Returns:
            List of running dashboard instances.
        """
        dashboards = self._load_dashboards()
        running: list[DashboardInfo] = []
        changed = False

        for project_hash, dashboard in list(dashboards.items()):
            pid_file = self._get_pid_file(project_hash)
            pid = self._read_pid_file(pid_file)

            if pid and self._is_process_running(pid):
                dashboard.pid = pid
                running.append(dashboard)
            else:
                # Clean up dead entry
                self._remove_pid_file(pid_file)
                del dashboards[project_hash]
                changed = True

        if changed:
            self._save_dashboards(dashboards)

        return running

    def start(
        self,
        project_path: Path,
        port: int | None = None,
    ) -> DashboardInfo:
        """Start a dashboard for a project.

        Args:
            project_path: Path to the project directory.
            port: Specific port to use, or None for auto-assignment.

        Returns:
            DashboardInfo for the started dashboard.

        Raises:
            DashboardAlreadyRunningError: If dashboard is already running.
            DashboardStartError: If dashboard fails to start.
        """
        project_path = project_path.resolve()
        project_hash = self._get_project_hash(project_path)

        # Use file locking to prevent race conditions when multiple processes
        # try to start dashboards simultaneously
        with self._dashboard_lock():
            # Check if already running (inside lock to prevent TOCTOU race)
            existing = self.get_dashboard(project_path)
            if existing:
                raise DashboardAlreadyRunningError(
                    f"Dashboard already running for {project_path} on port {existing.port}"
                )

            # Load dashboards and find port (inside lock for atomic port assignment)
            dashboards = self._load_dashboards()
            if port is None:
                port = self._find_available_port(dashboards)

            # Prepare paths
            project_name = project_path.name
            log_file = self._get_log_file(project_name)
            pid_file = self._get_pid_file(project_hash)

            # Build command to start dashboard
            # Use uvicorn directly for proper daemon support
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "dashboard.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]

            # Set environment variables
            env = os.environ.copy()
            env["DASHBOARD_PORT"] = str(port)
            env["DASHBOARD_TOWN"] = project_name

            # Start as background process
            with open(log_file, "a") as log_handle:
                try:
                    process = subprocess.Popen(
                        cmd,
                        cwd=str(project_path),
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                    )
                except Exception as e:
                    raise DashboardStartError(f"Failed to start dashboard: {e}") from e

            # Write PID file
            self._write_pid_file(pid_file, process.pid)

            # Register dashboard
            dashboard = DashboardInfo(
                project_path=str(project_path),
                port=port,
                pid=process.pid,
                project_hash=project_hash,
                log_file=str(log_file),
            )
            dashboards[project_hash] = dashboard
            self._save_dashboards(dashboards)

            return dashboard

    def stop(self, project_path: Path, force: bool = False) -> bool:
        """Stop a dashboard for a project.

        Args:
            project_path: Path to the project directory.
            force: If True, use SIGKILL instead of SIGTERM.

        Returns:
            True if dashboard was stopped, False if not running.
        """
        project_path = project_path.resolve()
        project_hash = self._get_project_hash(project_path)
        dashboards = self._load_dashboards()

        if project_hash not in dashboards:
            return False

        pid_file = self._get_pid_file(project_hash)
        pid = self._read_pid_file(pid_file)

        if pid:
            try:
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.kill(pid, sig)
            except (OSError, ProcessLookupError):
                pass  # Process already dead

        # Clean up
        self._remove_pid_file(pid_file)
        del dashboards[project_hash]
        self._save_dashboards(dashboards)

        return True


class DashboardError(Exception):
    """Base exception for dashboard operations."""

    pass


class DashboardAlreadyRunningError(DashboardError):
    """Dashboard is already running for this project."""

    pass


class DashboardStartError(DashboardError):
    """Failed to start dashboard."""

    pass


class DashboardNotRunningError(DashboardError):
    """Dashboard is not running for this project."""

    pass
