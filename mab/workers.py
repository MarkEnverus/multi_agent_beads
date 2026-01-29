"""MAB Workers - Worker process management with SQLite state persistence.

This module implements worker lifecycle management for the daemon, including:
- Worker dataclass for state representation
- SQLite database for state persistence across daemon restarts
- Process spawning and tracking via cross-platform spawner
- Health monitoring via heartbeat files
- Auto-restart on worker crash with exponential backoff
"""

from __future__ import annotations

import asyncio
import logging
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
from typing import TYPE_CHECKING, Any

from mab.spawner import (
    ProcessInfo,
    SpawnerError,
    get_git_root,
    get_spawner,
    remove_worktree,
)

if TYPE_CHECKING:
    from mab.spawner import Spawner

logger = logging.getLogger("mab.workers")

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
class HealthConfig:
    """Configuration for health monitoring and auto-restart.

    Attributes:
        health_check_interval_seconds: How often to check worker health (default: 30).
        heartbeat_timeout_seconds: Max age of heartbeat before considering crashed (default: 60).
        max_restart_count: Maximum auto-restarts before giving up (default: 5).
        restart_backoff_base_seconds: Base delay for exponential backoff (default: 5).
        restart_backoff_max_seconds: Maximum backoff delay (default: 300 = 5 min).
        auto_restart_enabled: Whether auto-restart is enabled (default: True).
    """

    health_check_interval_seconds: float = 30.0
    heartbeat_timeout_seconds: float = 60.0
    max_restart_count: int = 5
    restart_backoff_base_seconds: float = 5.0
    restart_backoff_max_seconds: float = 300.0
    auto_restart_enabled: bool = True

    def calculate_backoff(self, crash_count: int) -> float:
        """Calculate backoff delay using exponential backoff.

        Args:
            crash_count: Number of times the worker has crashed.

        Returns:
            Backoff delay in seconds.
        """
        # Exponential backoff: base * 2^(crash_count-1), capped at max
        if crash_count <= 0:
            return 0.0
        delay = self.restart_backoff_base_seconds * (2 ** (crash_count - 1))
        return min(delay, self.restart_backoff_max_seconds)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "health_check_interval_seconds": self.health_check_interval_seconds,
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "max_restart_count": self.max_restart_count,
            "restart_backoff_base_seconds": self.restart_backoff_base_seconds,
            "restart_backoff_max_seconds": self.restart_backoff_max_seconds,
            "auto_restart_enabled": self.auto_restart_enabled,
        }


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
    last_restart_at: str | None = None
    auto_restart_enabled: bool = True
    town_name: str = "default"  # Town this worker belongs to
    worktree_path: str | None = None  # Path to isolated git worktree
    worktree_branch: str | None = None  # Branch name for the worktree

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
            "last_restart_at": self.last_restart_at,
            "auto_restart_enabled": self.auto_restart_enabled,
            "town_name": self.town_name,
            "worktree_path": self.worktree_path,
            "worktree_branch": self.worktree_branch,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Worker":
        """Create Worker from database row."""
        # Handle legacy rows without town_name column
        town_name = "default"
        try:
            town_name = row["town_name"] or "default"
        except (KeyError, IndexError):
            pass

        # Handle legacy rows without worktree columns
        worktree_path = None
        worktree_branch = None
        try:
            worktree_path = row["worktree_path"]
            worktree_branch = row["worktree_branch"]
        except (KeyError, IndexError):
            pass

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
            last_restart_at=row["last_restart_at"],
            auto_restart_enabled=bool(row["auto_restart_enabled"]),
            town_name=town_name,
            worktree_path=worktree_path,
            worktree_branch=worktree_branch,
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
                    error_message TEXT,
                    last_restart_at TEXT,
                    auto_restart_enabled INTEGER DEFAULT 1,
                    town_name TEXT DEFAULT 'default',
                    worktree_path TEXT,
                    worktree_branch TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workers_project ON workers(project_path)
            """)

            # Migration: Add columns if they don't exist (for existing DBs)
            cursor = conn.execute("PRAGMA table_info(workers)")
            columns = {row[1] for row in cursor.fetchall()}

            if "town_name" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN town_name TEXT DEFAULT 'default'"
                )

            if "worktree_path" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN worktree_path TEXT"
                )

            if "worktree_branch" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN worktree_branch TEXT"
                )

            if "last_restart_at" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN last_restart_at TEXT"
                )

            if "exit_code" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN exit_code INTEGER"
                )

            if "error_message" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN error_message TEXT"
                )

            if "auto_restart_enabled" not in columns:
                conn.execute(
                    "ALTER TABLE workers ADD COLUMN auto_restart_enabled INTEGER DEFAULT 1"
                )

            # Create town index (must be after migration in case column was just added)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workers_town ON workers(town_name)
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
                    exit_code, error_message, last_restart_at, auto_restart_enabled,
                    town_name, worktree_path, worktree_branch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    worker.last_restart_at,
                    1 if worker.auto_restart_enabled else 0,
                    worker.town_name,
                    worker.worktree_path,
                    worker.worktree_branch,
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
                    error_message = ?,
                    last_restart_at = ?,
                    auto_restart_enabled = ?,
                    town_name = ?,
                    worktree_path = ?,
                    worktree_branch = ?
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
                    worker.last_restart_at,
                    1 if worker.auto_restart_enabled else 0,
                    worker.town_name,
                    worker.worktree_path,
                    worker.worktree_branch,
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
        town_name: str | None = None,
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
        if town_name is not None:
            conditions.append("town_name = ?")
            params.append(town_name)

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

    def count_workers(
        self,
        status: WorkerStatus | None = None,
        town_name: str | None = None,
    ) -> int:
        """Count workers with optional status and town filters."""
        conditions = []
        params: list[Any] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if town_name is not None:
            conditions.append("town_name = ?")
            params.append(town_name)

        query = "SELECT COUNT(*) FROM workers"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        with self._get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            result = cursor.fetchone()
            return int(result[0]) if result else 0


@dataclass
class HealthStatus:
    """Health status summary for the worker system.

    Attributes:
        healthy_workers: Number of healthy running workers.
        unhealthy_workers: Number of workers needing attention.
        crashed_workers: Number of currently crashed workers.
        total_restarts: Total restart count across all workers.
        workers_at_max_restarts: Workers that have hit max restart limit.
        config: Current health configuration.
    """

    healthy_workers: int = 0
    unhealthy_workers: int = 0
    crashed_workers: int = 0
    total_restarts: int = 0
    workers_at_max_restarts: int = 0
    config: HealthConfig = field(default_factory=HealthConfig)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "healthy_workers": self.healthy_workers,
            "unhealthy_workers": self.unhealthy_workers,
            "crashed_workers": self.crashed_workers,
            "total_restarts": self.total_restarts,
            "workers_at_max_restarts": self.workers_at_max_restarts,
            "config": self.config.to_dict(),
        }


class WorkerManager:
    """Manages worker lifecycle with process spawning and health monitoring.

    The WorkerManager handles:
    - Spawning worker processes (cross-platform via spawner module)
    - Monitoring worker health via heartbeat files
    - Detecting crashed workers and auto-restart with exponential backoff
    - Graceful and forceful worker termination
    """

    def __init__(
        self,
        mab_dir: Path,
        heartbeat_dir: Path | None = None,
        health_config: HealthConfig | None = None,
        spawner_type: str = "subprocess",
        test_mode: bool = False,
    ) -> None:
        """Initialize WorkerManager.

        Args:
            mab_dir: Global .mab directory (for database).
            heartbeat_dir: Directory for heartbeat files.
            health_config: Health monitoring configuration.
            spawner_type: Type of spawner to use ("subprocess" or "tmux").
            test_mode: If True, use placeholder scripts instead of Claude CLI.
        """
        self.mab_dir = mab_dir
        self.db = WorkerDatabase(mab_dir / "workers.db")
        self.heartbeat_dir = heartbeat_dir or mab_dir / "heartbeat"
        self.health_config = health_config or HealthConfig()
        self._active_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._active_process_info: dict[str, ProcessInfo] = {}
        self._pending_restarts: dict[str, asyncio.Task[None]] = {}
        self._test_mode = test_mode

        # Initialize cross-platform spawner
        logs_dir = mab_dir / "logs"
        self._spawner: Spawner = get_spawner(spawner_type, logs_dir=logs_dir, test_mode=test_mode)
        self._spawner_type = spawner_type

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
        town_name: str = "default",
    ) -> Worker:
        """Spawn a new worker process.

        Args:
            role: Worker role (dev, qa, tech_lead, manager, reviewer).
            project_path: Path to project for this worker.
            auto_restart: Whether to auto-restart on crash.
            town_name: Town this worker belongs to.

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
            town_name=town_name,
        )
        self.db.insert_worker(worker)

        try:
            # Spawn the process via cross-platform spawner
            process_info = await self._spawn_process(worker)
            worker.pid = process_info.pid
            worker.status = WorkerStatus.RUNNING
            worker.started_at = process_info.started_at

            # Store worktree info if created
            if process_info.worktree_path:
                worker.worktree_path = str(process_info.worktree_path)
                worker.worktree_branch = process_info.worktree_branch

            # Track the process info
            self._active_process_info[worker.id] = process_info
            if process_info.process:
                self._active_processes[worker.id] = process_info.process

            # Initial heartbeat
            self._update_heartbeat(worker.id)
            worker.last_heartbeat = datetime.now().isoformat()

            worktree_info = ""
            if worker.worktree_path:
                worktree_info = f", worktree: {worker.worktree_path}"

            logger.info(
                f"Worker {worker.id} spawned successfully (PID {worker.pid}, "
                f"log: {process_info.log_file}{worktree_info})"
            )

            self.db.update_worker(worker)
            return worker

        except Exception as e:
            worker.status = WorkerStatus.FAILED
            worker.error_message = str(e)
            self.db.update_worker(worker)
            raise WorkerSpawnError(f"Failed to spawn worker: {e}") from e

    async def _spawn_process(self, worker: Worker) -> ProcessInfo:
        """Spawn the actual worker process using cross-platform spawner.

        This uses the configured spawner (subprocess or tmux) to create
        a Claude CLI process with the appropriate role-specific prompt.

        Args:
            worker: Worker to spawn process for.

        Returns:
            ProcessInfo from the spawner.
        """
        # Additional environment variables for heartbeat
        env_vars = {
            "WORKER_HEARTBEAT_FILE": str(self._get_heartbeat_file(worker.id)),
            "WORKER_TOWN": worker.town_name,
        }

        try:
            process_info = await self._spawner.spawn(
                role=worker.role,
                project_path=worker.project_path,
                worker_id=worker.id,
                env_vars=env_vars,
            )
            return process_info

        except SpawnerError as e:
            raise WorkerSpawnError(
                f"Spawner failed for {worker.id}: {e.message}"
            ) from e

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

        # Use spawner's terminate if we have process info
        process_info = self._active_process_info.get(worker_id)
        if process_info:
            exit_code = await self._spawner.terminate(
                process_info, graceful=graceful, timeout=timeout
            )
            worker.exit_code = exit_code
        elif worker.pid is not None:
            # Fallback to direct signal handling
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

        # Remove from active tracking
        self._active_processes.pop(worker_id, None)
        self._active_process_info.pop(worker_id, None)

        # Cleanup heartbeat
        self._cleanup_heartbeat(worker_id)

        # Cleanup worktree if one exists (fallback in case spawner didn't clean it)
        if worker.worktree_path:
            worktree_path = Path(worker.worktree_path)
            if worktree_path.exists():
                git_root = get_git_root(Path(worker.project_path))
                if git_root:
                    logger.info(f"Cleaning up worktree at {worktree_path}")
                    remove_worktree(git_root, worktree_path)
            # Clear worktree info from worker
            worker.worktree_path = None
            worker.worktree_branch = None

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
        town_name: str | None = None,
    ) -> list[Worker]:
        """List workers with optional filters.

        Args:
            status: Filter by status.
            project_path: Filter by project path.
            role: Filter by role.
            town_name: Filter by town name.

        Returns:
            List of matching workers.
        """
        return self.db.list_workers(
            status=status,
            project_path=project_path,
            role=role,
            town_name=town_name,
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
                self._active_process_info.pop(worker.id, None)
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
            if age > self.health_config.heartbeat_timeout_seconds:
                return False

            # Update last heartbeat in database
            worker.last_heartbeat = last_heartbeat.isoformat()
            self.db.update_worker(worker)

        return True

    def count_running(self, town_name: str | None = None) -> int:
        """Count currently running workers.

        Args:
            town_name: Optional filter by town name.

        Returns:
            Number of running workers.
        """
        return self.db.count_workers(status=WorkerStatus.RUNNING, town_name=town_name)

    async def auto_restart(self, worker: Worker) -> Worker | None:
        """Attempt to auto-restart a crashed worker with exponential backoff.

        Args:
            worker: The crashed worker to restart.

        Returns:
            The restarted Worker if successful, None if restart was skipped
            (disabled, at max restarts, or already pending).
        """
        # Check if auto-restart is enabled globally and for this worker
        if not self.health_config.auto_restart_enabled:
            logger.info(f"Auto-restart disabled globally, not restarting {worker.id}")
            return None

        if not worker.auto_restart_enabled:
            logger.info(f"Auto-restart disabled for {worker.id}")
            return None

        # Check if we've hit max restart count
        if worker.crash_count >= self.health_config.max_restart_count:
            logger.warning(
                f"Worker {worker.id} has crashed {worker.crash_count} times, "
                f"exceeds max {self.health_config.max_restart_count}. "
                "Disabling auto-restart."
            )
            worker.auto_restart_enabled = False
            worker.error_message = f"Exceeded max restart count ({self.health_config.max_restart_count})"
            self.db.update_worker(worker)
            return None

        # Check if already pending restart
        if worker.id in self._pending_restarts:
            task = self._pending_restarts[worker.id]
            if not task.done():
                logger.debug(f"Worker {worker.id} already has pending restart")
                return None
            else:
                # Clean up completed task
                del self._pending_restarts[worker.id]

        # Calculate backoff delay
        backoff_delay = self.health_config.calculate_backoff(worker.crash_count)

        logger.info(
            f"Scheduling auto-restart for {worker.id} in {backoff_delay:.1f}s "
            f"(crash #{worker.crash_count})"
        )

        # Schedule the restart with backoff
        task = asyncio.create_task(
            self._delayed_restart(worker, backoff_delay)
        )
        self._pending_restarts[worker.id] = task

        return None  # Return None immediately, restart will happen later

    async def _delayed_restart(
        self,
        worker: Worker,
        delay: float,
    ) -> None:
        """Perform a delayed restart of a worker.

        Args:
            worker: Worker to restart.
            delay: Delay in seconds before restarting.
        """
        try:
            # Wait for backoff period
            await asyncio.sleep(delay)

            # Refresh worker state from database
            current_worker = self.db.get_worker(worker.id)
            if current_worker is None:
                logger.warning(f"Worker {worker.id} no longer exists, skipping restart")
                return

            # Check if status changed while we were waiting
            if current_worker.status not in (WorkerStatus.CRASHED, WorkerStatus.FAILED):
                logger.info(
                    f"Worker {worker.id} status changed to {current_worker.status}, "
                    "skipping restart"
                )
                return

            # Check if auto-restart was disabled while waiting
            if not current_worker.auto_restart_enabled:
                logger.info(f"Auto-restart disabled for {worker.id} while waiting")
                return

            # Perform the restart
            logger.info(f"Restarting worker {worker.id}")

            try:
                restarted = await self._restart_worker(current_worker)
                logger.info(
                    f"Worker {worker.id} restarted successfully as PID {restarted.pid}"
                )
            except WorkerSpawnError as e:
                logger.error(f"Failed to restart worker {worker.id}: {e}")
                # Update error message
                current_worker.error_message = f"Restart failed: {e}"
                current_worker.status = WorkerStatus.FAILED
                self.db.update_worker(current_worker)

        except asyncio.CancelledError:
            logger.debug(f"Restart cancelled for {worker.id}")
            raise
        finally:
            # Clean up from pending restarts
            self._pending_restarts.pop(worker.id, None)

    async def _restart_worker(self, worker: Worker) -> Worker:
        """Restart a worker by spawning a new process with the same configuration.

        Uses the cross-platform spawner for consistent behavior.

        Args:
            worker: Worker to restart.

        Returns:
            Updated Worker with new process.
        """
        # Mark restart time
        worker.last_restart_at = datetime.now().isoformat()

        # Additional environment variables for heartbeat
        env_vars = {
            "WORKER_HEARTBEAT_FILE": str(self._get_heartbeat_file(worker.id)),
            "WORKER_TOWN": worker.town_name,
        }

        try:
            process_info = await self._spawner.spawn(
                role=worker.role,
                project_path=worker.project_path,
                worker_id=worker.id,
                env_vars=env_vars,
            )
        except SpawnerError as e:
            raise WorkerSpawnError(
                f"Restart failed for {worker.id}: {e.message}"
            ) from e

        # Update worker state
        worker.pid = process_info.pid
        worker.status = WorkerStatus.RUNNING
        worker.started_at = process_info.started_at
        worker.stopped_at = None
        worker.exit_code = None
        worker.error_message = None

        # Track the process info
        self._active_process_info[worker.id] = process_info
        if process_info.process:
            self._active_processes[worker.id] = process_info.process

        # Initial heartbeat
        self._update_heartbeat(worker.id)
        worker.last_heartbeat = datetime.now().isoformat()

        logger.info(
            f"Worker {worker.id} restarted successfully (PID {worker.pid}, "
            f"log: {process_info.log_file})"
        )

        self.db.update_worker(worker)
        return worker

    def get_health_status(self) -> HealthStatus:
        """Get current health status of the worker system.

        Returns:
            HealthStatus with current metrics.
        """
        all_workers = self.db.list_workers()
        running_workers = [w for w in all_workers if w.status == WorkerStatus.RUNNING]
        crashed_workers = [w for w in all_workers if w.status == WorkerStatus.CRASHED]

        # Count healthy vs unhealthy running workers
        healthy_count = 0
        unhealthy_count = 0

        for worker in running_workers:
            last_hb = self._check_heartbeat(worker.id)
            if last_hb is not None:
                age = (datetime.now() - last_hb).total_seconds()
                if age <= self.health_config.heartbeat_timeout_seconds:
                    healthy_count += 1
                else:
                    unhealthy_count += 1
            else:
                # No heartbeat file - check if process is running
                if worker.pid is not None and self._is_process_running(worker.pid):
                    unhealthy_count += 1  # Running but no heartbeat
                else:
                    unhealthy_count += 1  # May have crashed

        # Calculate totals
        total_restarts = sum(w.crash_count for w in all_workers)
        at_max_restarts = sum(
            1 for w in all_workers
            if w.crash_count >= self.health_config.max_restart_count
        )

        return HealthStatus(
            healthy_workers=healthy_count,
            unhealthy_workers=unhealthy_count,
            crashed_workers=len(crashed_workers),
            total_restarts=total_restarts,
            workers_at_max_restarts=at_max_restarts,
            config=self.health_config,
        )

    async def health_check_and_restart(self) -> tuple[list[Worker], list[Worker]]:
        """Check health of all running workers and auto-restart crashed ones.

        This is the main health check method that should be called periodically.
        It combines crash detection with auto-restart logic.

        Returns:
            Tuple of (crashed_workers, restart_scheduled_workers).
        """
        crashed = await self.health_check()
        restart_scheduled: list[Worker] = []

        for worker in crashed:
            # Attempt auto-restart
            if self.health_config.auto_restart_enabled and worker.auto_restart_enabled:
                result = await self.auto_restart(worker)
                if result is None:
                    # Restart was scheduled (not immediate)
                    if worker.id in self._pending_restarts:
                        restart_scheduled.append(worker)

        return crashed, restart_scheduled

    def cancel_pending_restarts(self) -> int:
        """Cancel all pending restart tasks.

        Returns:
            Number of restart tasks cancelled.
        """
        cancelled = 0
        for worker_id, task in list(self._pending_restarts.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
            del self._pending_restarts[worker_id]
        return cancelled


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
