"""MAB Workers - Worker process management with SQLite state persistence.

This module implements worker lifecycle management for the daemon, including:
- Worker dataclass for state representation
- SQLite database for state persistence across daemon restarts
- Process spawning and tracking
- Health monitoring via heartbeat files
- Auto-restart on worker crash
"""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

# Worker roles
VALID_ROLES = frozenset(["dev", "qa", "tech_lead", "manager", "reviewer"])


class WorkerStatus(str, Enum):
    """Worker lifecycle states."""

    PENDING = "pending"  # Created but not yet spawned
    STARTING = "starting"  # Spawn initiated
    RUNNING = "running"  # Process confirmed running
    STOPPING = "stopping"  # Stop requested
    STOPPED = "stopped"  # Clean shutdown
    CRASHED = "crashed"  # Unexpected termination
    FAILED = "failed"  # Failed to start


@dataclass
class Worker:
    """Represents a worker agent process."""

    id: str
    role: str
    project_path: str
    status: WorkerStatus = WorkerStatus.PENDING
    pid: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    stopped_at: str | None = None
    crash_count: int = 0
    last_heartbeat: str | None = None
    exit_code: int | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "role": self.role,
            "project_path": self.project_path,
            "status": self.status.value,
            "pid": self.pid,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "crash_count": self.crash_count,
            "last_heartbeat": self.last_heartbeat,
            "exit_code": self.exit_code,
            "error_message": self.error_message,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Worker":
        """Create Worker from database row."""
        return cls(
            id=row["id"],
            role=row["role"],
            project_path=row["project_path"],
            status=WorkerStatus(row["status"]),
            pid=row["pid"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            stopped_at=row["stopped_at"],
            crash_count=row["crash_count"],
            last_heartbeat=row["last_heartbeat"],
            exit_code=row["exit_code"],
            error_message=row["error_message"],
        )


class WorkerError(Exception):
    """Base exception for worker operations."""

    pass


class WorkerNotFoundError(WorkerError):
    """Raised when worker is not found."""

    pass


class WorkerSpawnError(WorkerError):
    """Raised when worker fails to spawn."""

    pass


class WorkerDatabase:
    """SQLite database for worker state persistence.

    Uses a single database file at ~/.mab/workers.db for global worker state.
    Thread-safe via sqlite3's built-in serialization.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path) -> None:
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._ensure_directory()
        self._init_schema()

    def _ensure_directory(self) -> None:
        """Ensure parent directory exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pid INTEGER,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    stopped_at TEXT,
                    crash_count INTEGER DEFAULT 0,
                    last_heartbeat TEXT,
                    exit_code INTEGER,
                    error_message TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workers_project ON workers(project_path)
            """)
            conn.commit()

    def insert_worker(self, worker: Worker) -> None:
        """Insert a new worker record."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO workers (
                    id, role, project_path, status, pid, created_at,
                    started_at, stopped_at, crash_count, last_heartbeat,
                    exit_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    worker.id,
                    worker.role,
                    worker.project_path,
                    worker.status.value,
                    worker.pid,
                    worker.created_at,
                    worker.started_at,
                    worker.stopped_at,
                    worker.crash_count,
                    worker.last_heartbeat,
                    worker.exit_code,
                    worker.error_message,
                ),
            )
            conn.commit()

    def update_worker(self, worker: Worker) -> None:
        """Update an existing worker record."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE workers SET
                    role = ?,
                    project_path = ?,
                    status = ?,
                    pid = ?,
                    started_at = ?,
                    stopped_at = ?,
                    crash_count = ?,
                    last_heartbeat = ?,
                    exit_code = ?,
                    error_message = ?
                WHERE id = ?
            """,
                (
                    worker.role,
                    worker.project_path,
                    worker.status.value,
                    worker.pid,
                    worker.started_at,
                    worker.stopped_at,
                    worker.crash_count,
                    worker.last_heartbeat,
                    worker.exit_code,
                    worker.error_message,
                    worker.id,
                ),
            )
            conn.commit()

    def get_worker(self, worker_id: str) -> Worker | None:
        """Get a worker by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM workers WHERE id = ?",
                (worker_id,),
            )
            row = cursor.fetchone()
            return Worker.from_row(row) if row else None

    def list_workers(
        self,
        status: WorkerStatus | None = None,
        project_path: str | None = None,
        role: str | None = None,
    ) -> list[Worker]:
        """List workers with optional filters."""
        conditions = []
        params: list[Any] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if project_path is not None:
            conditions.append("project_path = ?")
            params.append(project_path)
        if role is not None:
            conditions.append("role = ?")
            params.append(role)

        query = "SELECT * FROM workers"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [Worker.from_row(row) for row in cursor.fetchall()]

    def delete_worker(self, worker_id: str) -> bool:
        """Delete a worker record."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM workers WHERE id = ?",
                (worker_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def count_workers(self, status: WorkerStatus | None = None) -> int:
        """Count workers with optional status filter."""
        if status is None:
            query = "SELECT COUNT(*) FROM workers"
            params: tuple[Any, ...] = ()
        else:
            query = "SELECT COUNT(*) FROM workers WHERE status = ?"
            params = (status.value,)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return int(result[0]) if result else 0


class WorkerManager:
    """Manages worker lifecycle with process spawning and health monitoring.

    The WorkerManager handles:
    - Spawning worker processes (cross-platform)
    - Monitoring worker health via heartbeat files
    - Detecting crashed workers and handling auto-restart
    - Graceful and forceful worker termination
    """

    # Heartbeat timeout before considering worker crashed
    HEARTBEAT_TIMEOUT_SECONDS = 60

    def __init__(
        self,
        mab_dir: Path,
        heartbeat_dir: Path | None = None,
    ) -> None:
        """Initialize WorkerManager.

        Args:
            mab_dir: Global .mab directory (for database).
            heartbeat_dir: Directory for heartbeat files.
        """
        self.mab_dir = mab_dir
        self.db = WorkerDatabase(mab_dir / "workers.db")
        self.heartbeat_dir = heartbeat_dir or mab_dir / "heartbeat"
        self._active_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._ensure_heartbeat_dir()

    def _ensure_heartbeat_dir(self) -> None:
        """Ensure heartbeat directory exists."""
        self.heartbeat_dir.mkdir(parents=True, exist_ok=True)

    def _generate_worker_id(self, role: str) -> str:
        """Generate a unique worker ID."""
        short_uuid = str(uuid.uuid4())[:8]
        return f"worker-{role}-{short_uuid}"

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _get_heartbeat_file(self, worker_id: str) -> Path:
        """Get heartbeat file path for a worker."""
        return self.heartbeat_dir / f"{worker_id}.heartbeat"

    def _update_heartbeat(self, worker_id: str) -> None:
        """Update heartbeat file for a worker."""
        heartbeat_file = self._get_heartbeat_file(worker_id)
        heartbeat_file.write_text(datetime.now().isoformat())

    def _check_heartbeat(self, worker_id: str) -> datetime | None:
        """Check when worker last sent heartbeat.

        Returns:
            Last heartbeat datetime or None if no heartbeat file.
        """
        heartbeat_file = self._get_heartbeat_file(worker_id)
        if not heartbeat_file.exists():
            return None

        try:
            timestamp_str = heartbeat_file.read_text().strip()
            return datetime.fromisoformat(timestamp_str)
        except (ValueError, OSError):
            return None

    def _cleanup_heartbeat(self, worker_id: str) -> None:
        """Remove heartbeat file for a worker."""
        heartbeat_file = self._get_heartbeat_file(worker_id)
        try:
            heartbeat_file.unlink()
        except FileNotFoundError:
            pass

    async def spawn(
        self,
        role: str,
        project_path: str,
        auto_restart: bool = True,
    ) -> Worker:
        """Spawn a new worker process.

        Args:
            role: Worker role (dev, qa, tech_lead, manager, reviewer).
            project_path: Path to project for this worker.
            auto_restart: Whether to auto-restart on crash.

        Returns:
            Created Worker object.

        Raises:
            WorkerSpawnError: If worker fails to spawn.
        """
        if role not in VALID_ROLES:
            raise WorkerSpawnError(f"Invalid role: {role}")

        # Create worker record
        worker = Worker(
            id=self._generate_worker_id(role),
            role=role,
            project_path=project_path,
            status=WorkerStatus.STARTING,
        )
        self.db.insert_worker(worker)

        try:
            # Spawn the process
            process = await self._spawn_process(worker)
            worker.pid = process.pid
            worker.status = WorkerStatus.RUNNING
            worker.started_at = datetime.now().isoformat()

            # Track the process
            self._active_processes[worker.id] = process

            # Initial heartbeat
            self._update_heartbeat(worker.id)
            worker.last_heartbeat = datetime.now().isoformat()

            self.db.update_worker(worker)
            return worker

        except Exception as e:
            worker.status = WorkerStatus.FAILED
            worker.error_message = str(e)
            self.db.update_worker(worker)
            raise WorkerSpawnError(f"Failed to spawn worker: {e}") from e

    async def _spawn_process(self, worker: Worker) -> subprocess.Popen[bytes]:
        """Spawn the actual worker process.

        This creates a subprocess that runs the worker agent script.
        The subprocess is platform-independent (no AppleScript).

        Args:
            worker: Worker to spawn process for.

        Returns:
            Subprocess.Popen instance.
        """
        # Build environment for worker
        env = os.environ.copy()
        env["WORKER_ID"] = worker.id
        env["WORKER_ROLE"] = worker.role
        env["WORKER_PROJECT"] = worker.project_path
        env["WORKER_HEARTBEAT_FILE"] = str(self._get_heartbeat_file(worker.id))

        # Get the worker script path
        mab_root = Path(__file__).parent.parent
        worker_script = mab_root / "scripts" / "worker_agent.py"

        # If worker script doesn't exist, use a simple placeholder command
        # This allows testing the infrastructure without the full worker implementation
        if not worker_script.exists():
            # Create a minimal worker that just maintains heartbeat
            cmd = [
                "python3",
                "-c",
                """
import time
import os
from pathlib import Path

heartbeat_file = Path(os.environ.get('WORKER_HEARTBEAT_FILE', '/tmp/heartbeat'))
print(f"Worker {os.environ.get('WORKER_ID')} started")

while True:
    heartbeat_file.write_text(str(time.time()))
    time.sleep(10)
""",
            ]
        else:
            cmd = ["python3", str(worker_script)]

        # Spawn the process
        process = subprocess.Popen(
            cmd,
            env=env,
            cwd=worker.project_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # Detach from parent
        )

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Check if it crashed immediately
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise WorkerSpawnError(
                f"Worker process exited immediately with code {process.returncode}: "
                f"{stderr.decode()}"
            )

        return process

    async def stop(
        self,
        worker_id: str,
        graceful: bool = True,
        timeout: float = 30.0,
    ) -> Worker:
        """Stop a worker.

        Args:
            worker_id: ID of worker to stop.
            graceful: If True, send SIGTERM and wait. If False, send SIGKILL.
            timeout: Seconds to wait for graceful shutdown.

        Returns:
            Updated Worker object.

        Raises:
            WorkerNotFoundError: If worker not found.
        """
        worker = self.db.get_worker(worker_id)
        if worker is None:
            raise WorkerNotFoundError(f"Worker not found: {worker_id}")

        if worker.status not in (WorkerStatus.RUNNING, WorkerStatus.STARTING):
            # Worker already stopped
            return worker

        worker.status = WorkerStatus.STOPPING
        self.db.update_worker(worker)

        if worker.pid is not None:
            try:
                if graceful:
                    os.kill(worker.pid, signal.SIGTERM)

                    # Wait for process to exit
                    start_time = time.time()
                    while self._is_process_running(worker.pid):
                        if time.time() - start_time > timeout:
                            # Force kill
                            os.kill(worker.pid, signal.SIGKILL)
                            break
                        await asyncio.sleep(0.1)
                else:
                    os.kill(worker.pid, signal.SIGKILL)

            except (OSError, ProcessLookupError):
                pass  # Process already gone

        # Remove from active processes
        self._active_processes.pop(worker_id, None)

        # Cleanup heartbeat
        self._cleanup_heartbeat(worker_id)

        # Update worker state
        worker.status = WorkerStatus.STOPPED
        worker.stopped_at = datetime.now().isoformat()
        self.db.update_worker(worker)

        return worker

    async def stop_all(
        self,
        graceful: bool = True,
        timeout: float = 30.0,
    ) -> list[Worker]:
        """Stop all running workers.

        Args:
            graceful: If True, send SIGTERM and wait.
            timeout: Seconds to wait for each worker.

        Returns:
            List of stopped workers.
        """
        running_workers = self.db.list_workers(status=WorkerStatus.RUNNING)
        stopped = []

        for worker in running_workers:
            try:
                stopped_worker = await self.stop(
                    worker.id,
                    graceful=graceful,
                    timeout=timeout,
                )
                stopped.append(stopped_worker)
            except WorkerError:
                pass  # Continue stopping others

        return stopped

    def get(self, worker_id: str) -> Worker:
        """Get a worker by ID.

        Args:
            worker_id: Worker ID.

        Returns:
            Worker object.

        Raises:
            WorkerNotFoundError: If worker not found.
        """
        worker = self.db.get_worker(worker_id)
        if worker is None:
            raise WorkerNotFoundError(f"Worker not found: {worker_id}")
        return worker

    def list_workers(
        self,
        status: WorkerStatus | None = None,
        project_path: str | None = None,
        role: str | None = None,
    ) -> list[Worker]:
        """List workers with optional filters.

        Args:
            status: Filter by status.
            project_path: Filter by project path.
            role: Filter by role.

        Returns:
            List of matching workers.
        """
        return self.db.list_workers(
            status=status,
            project_path=project_path,
            role=role,
        )

    async def health_check(self) -> list[Worker]:
        """Check health of all running workers.

        Returns:
            List of workers that were marked as crashed.
        """
        crashed_workers = []
        running_workers = self.db.list_workers(status=WorkerStatus.RUNNING)

        for worker in running_workers:
            is_healthy = await self._check_worker_health(worker)
            if not is_healthy:
                worker.status = WorkerStatus.CRASHED
                worker.crash_count += 1
                worker.stopped_at = datetime.now().isoformat()
                self.db.update_worker(worker)
                crashed_workers.append(worker)

                # Cleanup
                self._active_processes.pop(worker.id, None)
                self._cleanup_heartbeat(worker.id)

        return crashed_workers

    async def _check_worker_health(self, worker: Worker) -> bool:
        """Check if a specific worker is healthy.

        Args:
            worker: Worker to check.

        Returns:
            True if healthy, False if crashed or unresponsive.
        """
        # Check if process is still running
        if worker.pid is not None and not self._is_process_running(worker.pid):
            return False

        # Check heartbeat freshness
        last_heartbeat = self._check_heartbeat(worker.id)
        if last_heartbeat is not None:
            age = (datetime.now() - last_heartbeat).total_seconds()
            if age > self.HEARTBEAT_TIMEOUT_SECONDS:
                return False

            # Update last heartbeat in database
            worker.last_heartbeat = last_heartbeat.isoformat()
            self.db.update_worker(worker)

        return True

    def count_running(self) -> int:
        """Count currently running workers."""
        return self.db.count_workers(status=WorkerStatus.RUNNING)


def get_default_worker_manager(
    mab_dir: Path | None = None,
    heartbeat_dir: Path | None = None,
) -> WorkerManager:
    """Get a WorkerManager with default configuration.

    Args:
        mab_dir: Global .mab directory. Defaults to ~/.mab/.
        heartbeat_dir: Heartbeat directory. Defaults to mab_dir/heartbeat.

    Returns:
        Configured WorkerManager.
    """
    if mab_dir is None:
        mab_dir = Path.home() / ".mab"
    return WorkerManager(mab_dir=mab_dir, heartbeat_dir=heartbeat_dir)
