"""MAB Towns - Multi-town orchestration support.

This module implements the multi-town architecture for running multiple isolated
orchestration contexts simultaneously, including:
- Town dataclass for configuration and state
- TownManager for CRUD operations
- SQLite persistence for town metadata
- Port allocation and conflict detection

Each town provides:
- Isolated worker pool (workers tagged with town name)
- Dedicated dashboard port
- Independent configuration
- Resource isolation
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from mab.templates import TEMPLATES, TeamTemplate, get_template

logger = logging.getLogger("mab.towns")

# Default port range for towns
DEFAULT_PORT_START = 8000
DEFAULT_PORT_END = 8099


class TownStatus(str, Enum):
    """Town lifecycle states."""

    STOPPED = "stopped"  # Town exists but dashboard not running
    STARTING = "starting"  # Dashboard being started
    RUNNING = "running"  # Dashboard active and serving
    STOPPING = "stopping"  # Dashboard shutting down


@dataclass
class Town:
    """Represents an orchestration town - an isolated context for agents.

    Attributes:
        name: Unique town identifier (alphanumeric + underscores).
        port: Dashboard port for this town.
        project_path: Path to the project directory (optional).
        status: Current town status.
        max_workers: Maximum concurrent workers for this town.
        default_roles: Roles to spawn on town start.
        description: Human-readable description.
        created_at: ISO timestamp of creation.
        started_at: ISO timestamp of last start (if running).
        pid: Dashboard process ID (if running).
        template: Team template name (solo, pair, full).
        workflow: JSON array of workflow steps.
        worker_counts: JSON dict of role -> count for custom configurations.
    """

    name: str
    port: int
    project_path: str | None = None
    status: TownStatus = TownStatus.STOPPED
    max_workers: int = 3
    default_roles: list[str] = field(default_factory=lambda: ["dev", "qa"])
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    pid: int | None = None
    template: str = "pair"
    workflow: list[str] = field(default_factory=list)
    worker_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "port": self.port,
            "project_path": self.project_path,
            "status": self.status.value,
            "max_workers": self.max_workers,
            "default_roles": self.default_roles,
            "description": self.description,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "pid": self.pid,
            "template": self.template,
            "workflow": self.workflow,
            "worker_counts": self.worker_counts,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Town":
        """Create Town from database row."""
        roles = row["default_roles"]
        if isinstance(roles, str):
            roles = json.loads(roles)

        # Handle optional new columns (may be None in older databases)
        workflow_data = row["workflow"] if "workflow" in row.keys() else None
        workflow = json.loads(workflow_data) if workflow_data else []

        worker_counts_data = row["worker_counts"] if "worker_counts" in row.keys() else None
        worker_counts = json.loads(worker_counts_data) if worker_counts_data else {}

        template = row["template"] if "template" in row.keys() else "pair"

        return cls(
            name=row["name"],
            port=row["port"],
            project_path=row["project_path"],
            status=TownStatus(row["status"]),
            max_workers=row["max_workers"],
            default_roles=roles,
            description=row["description"] or "",
            created_at=row["created_at"],
            started_at=row["started_at"],
            pid=row["pid"],
            template=template or "pair",
            workflow=workflow,
            worker_counts=worker_counts,
        )

    def get_template_config(self) -> TeamTemplate | None:
        """Get the TeamTemplate configuration for this town."""
        return get_template(self.template)

    def get_effective_roles(self) -> dict[str, int]:
        """Get the effective role counts for this town.

        Returns worker_counts if set, otherwise falls back to template defaults.
        """
        if self.worker_counts:
            return self.worker_counts

        template = self.get_template_config()
        if template:
            return template.roles

        # Fallback to default_roles with count of 1 each
        return {role: 1 for role in self.default_roles}


class TownError(Exception):
    """Base exception for town operations."""

    pass


class TownNotFoundError(TownError):
    """Raised when town is not found."""

    pass


class TownExistsError(TownError):
    """Raised when trying to create a town that already exists."""

    pass


class PortConflictError(TownError):
    """Raised when port is already in use by another town."""

    pass


class ProjectPathConflictError(TownError):
    """Raised when project path is already in use by another town."""

    pass


class TownDatabase:
    """SQLite database for town metadata persistence.

    Uses the global workers.db database at ~/.mab/workers.db for town
    coordination. Towns remain global since they coordinate across projects.

    Uses WAL mode (Write-Ahead Logging) for better concurrent performance.
    """

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
        """Get a database connection.

        Enables WAL mode for better concurrent read/write performance.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode = WAL")
        # Set busy timeout to wait for locks instead of failing immediately
        conn.execute("PRAGMA busy_timeout = 5000")  # 5 second timeout
        return conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS towns (
                    name TEXT PRIMARY KEY,
                    port INTEGER NOT NULL UNIQUE,
                    project_path TEXT,
                    status TEXT NOT NULL DEFAULT 'stopped',
                    max_workers INTEGER NOT NULL DEFAULT 3,
                    default_roles TEXT NOT NULL DEFAULT '["dev", "qa"]',
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    pid INTEGER,
                    template TEXT DEFAULT 'pair',
                    workflow TEXT,
                    worker_counts TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_towns_port ON towns(port)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_towns_status ON towns(status)
            """)
            # Migration: add new columns if they don't exist
            self._migrate_schema(conn)
            conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Run schema migrations for new columns."""
        # Check existing columns
        cursor = conn.execute("PRAGMA table_info(towns)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        # Add template column if missing
        if "template" not in existing_columns:
            conn.execute("ALTER TABLE towns ADD COLUMN template TEXT DEFAULT 'pair'")
            logger.debug("Added 'template' column to towns table")

        # Add workflow column if missing
        if "workflow" not in existing_columns:
            conn.execute("ALTER TABLE towns ADD COLUMN workflow TEXT")
            logger.debug("Added 'workflow' column to towns table")

        # Add worker_counts column if missing
        if "worker_counts" not in existing_columns:
            conn.execute("ALTER TABLE towns ADD COLUMN worker_counts TEXT")
            logger.debug("Added 'worker_counts' column to towns table")

    def insert_town(self, town: Town) -> None:
        """Insert a new town record."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO towns (
                    name, port, project_path, status, max_workers,
                    default_roles, description, created_at, started_at, pid,
                    template, workflow, worker_counts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    town.name,
                    town.port,
                    town.project_path,
                    town.status.value,
                    town.max_workers,
                    json.dumps(town.default_roles),
                    town.description,
                    town.created_at,
                    town.started_at,
                    town.pid,
                    town.template,
                    json.dumps(town.workflow) if town.workflow else None,
                    json.dumps(town.worker_counts) if town.worker_counts else None,
                ),
            )
            conn.commit()

    def update_town(self, town: Town) -> None:
        """Update an existing town record."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE towns SET
                    port = ?,
                    project_path = ?,
                    status = ?,
                    max_workers = ?,
                    default_roles = ?,
                    description = ?,
                    started_at = ?,
                    pid = ?,
                    template = ?,
                    workflow = ?,
                    worker_counts = ?
                WHERE name = ?
            """,
                (
                    town.port,
                    town.project_path,
                    town.status.value,
                    town.max_workers,
                    json.dumps(town.default_roles),
                    town.description,
                    town.started_at,
                    town.pid,
                    town.template,
                    json.dumps(town.workflow) if town.workflow else None,
                    json.dumps(town.worker_counts) if town.worker_counts else None,
                    town.name,
                ),
            )
            conn.commit()

    def get_town(self, name: str) -> Town | None:
        """Get a town by name."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM towns WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()
            return Town.from_row(row) if row else None

    def get_town_by_port(self, port: int) -> Town | None:
        """Get a town by port number."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM towns WHERE port = ?",
                (port,),
            )
            row = cursor.fetchone()
            return Town.from_row(row) if row else None

    def list_towns(
        self,
        status: TownStatus | None = None,
        project_path: str | None = None,
    ) -> list[Town]:
        """List towns with optional filters."""
        conditions = []
        params: list[Any] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if project_path is not None:
            conditions.append("project_path = ?")
            params.append(project_path)

        query = "SELECT * FROM towns"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY name"

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [Town.from_row(row) for row in cursor.fetchall()]

    def delete_town(self, name: str) -> bool:
        """Delete a town record."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM towns WHERE name = ?",
                (name,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def count_towns(self, status: TownStatus | None = None) -> int:
        """Count towns with optional status filter."""
        if status is None:
            query = "SELECT COUNT(*) FROM towns"
            params: tuple[Any, ...] = ()
        else:
            query = "SELECT COUNT(*) FROM towns WHERE status = ?"
            params = (status.value,)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return int(result[0]) if result else 0

    def get_next_available_port(
        self,
        start: int = DEFAULT_PORT_START,
        end: int = DEFAULT_PORT_END,
    ) -> int | None:
        """Find the next available port in the range.

        Args:
            start: Start of port range (inclusive).
            end: End of port range (inclusive).

        Returns:
            Available port number or None if all ports used.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT port FROM towns WHERE port BETWEEN ? AND ? ORDER BY port",
                (start, end),
            )
            used_ports = {row[0] for row in cursor.fetchall()}

        for port in range(start, end + 1):
            if port not in used_ports:
                return port

        return None


class TownManager:
    """Manages town lifecycle and operations.

    The TownManager handles:
    - Creating and deleting towns
    - Starting and stopping town dashboards
    - Port allocation and validation
    - Town configuration management
    """

    def __init__(self, mab_dir: Path) -> None:
        """Initialize TownManager.

        Args:
            mab_dir: Global .mab directory.
        """
        self.mab_dir = mab_dir
        self.db = TownDatabase(mab_dir / "workers.db")

    def create(
        self,
        name: str,
        port: int | None = None,
        project_path: str | None = None,
        max_workers: int = 3,
        default_roles: list[str] | None = None,
        description: str = "",
        template: str = "pair",
        worker_counts: dict[str, int] | None = None,
    ) -> Town:
        """Create a new town.

        Args:
            name: Unique town name (alphanumeric + underscores).
            port: Dashboard port (auto-allocated if None).
            project_path: Path to project directory.
            max_workers: Maximum workers for this town.
            default_roles: Roles to spawn on start.
            description: Human-readable description.
            template: Team template name (solo, pair, full).
            worker_counts: Custom role counts (overrides template).

        Returns:
            Created Town object.

        Raises:
            TownExistsError: If town with name already exists.
            PortConflictError: If port is already in use.
            TownError: If no ports available or invalid name/template.
        """
        # Validate name
        if not name or not name.replace("_", "").isalnum():
            raise TownError(f"Invalid town name '{name}': must be alphanumeric with underscores")

        # Validate template
        template_config = get_template(template)
        if template_config is None:
            valid_templates = ", ".join(TEMPLATES.keys())
            raise TownError(f"Invalid template '{template}'. Valid templates: {valid_templates}")

        # Check if town exists
        existing = self.db.get_town(name)
        if existing is not None:
            raise TownExistsError(f"Town '{name}' already exists")

        # Allocate port
        if port is None:
            port = self.db.get_next_available_port()
            if port is None:
                raise TownError("No available ports in range")
        else:
            # Check port conflict
            existing_by_port = self.db.get_town_by_port(port)
            if existing_by_port is not None:
                raise PortConflictError(
                    f"Port {port} already in use by town '{existing_by_port.name}'"
                )

        # Check project path conflict (only if project_path is provided)
        if project_path is not None:
            existing_by_path = self.db.list_towns(project_path=project_path)
            if existing_by_path:
                raise ProjectPathConflictError(
                    f"Project path '{project_path}' already in use by town "
                    f"'{existing_by_path[0].name}'"
                )

        # Derive roles and workflow from template if not overridden
        effective_roles = default_roles
        if effective_roles is None:
            effective_roles = list(template_config.roles.keys())

        workflow = [step.value for step in template_config.workflow]

        # Use worker_counts if provided, otherwise derive from template
        effective_worker_counts = worker_counts or template_config.roles.copy()

        # Create town
        town = Town(
            name=name,
            port=port,
            project_path=project_path,
            max_workers=max_workers,
            default_roles=effective_roles,
            description=description or template_config.description,
            template=template,
            workflow=workflow,
            worker_counts=effective_worker_counts,
        )

        self.db.insert_town(town)
        logger.info(f"Created town '{name}' on port {port} with template '{template}'")

        return town

    def get(self, name: str) -> Town:
        """Get a town by name.

        Args:
            name: Town name.

        Returns:
            Town object.

        Raises:
            TownNotFoundError: If town not found.
        """
        town = self.db.get_town(name)
        if town is None:
            raise TownNotFoundError(f"Town '{name}' not found")
        return town

    def list_towns(
        self,
        status: TownStatus | None = None,
        project_path: str | None = None,
    ) -> list[Town]:
        """List towns with optional filters.

        Args:
            status: Filter by status.
            project_path: Filter by project path.

        Returns:
            List of matching towns.
        """
        return self.db.list_towns(status=status, project_path=project_path)

    def delete(self, name: str, force: bool = False) -> bool:
        """Delete a town.

        Args:
            name: Town name.
            force: If True, delete even if running.

        Returns:
            True if deleted.

        Raises:
            TownNotFoundError: If town not found.
            TownError: If town is running and force=False.
        """
        town = self.get(name)

        if town.status == TownStatus.RUNNING and not force:
            raise TownError(f"Town '{name}' is running. Stop it first or use --force.")

        # Stop town dashboard if running
        if town.status == TownStatus.RUNNING and town.pid:
            try:
                os.kill(town.pid, signal.SIGTERM)
                logger.info(f"Stopped dashboard process {town.pid} for town '{name}'")
            except (OSError, ProcessLookupError):
                logger.debug(f"Dashboard process {town.pid} already terminated")

        deleted = self.db.delete_town(name)
        if deleted:
            logger.info(f"Deleted town '{name}'")

        return deleted

    def update(
        self,
        name: str,
        port: int | None = None,
        max_workers: int | None = None,
        default_roles: list[str] | None = None,
        description: str | None = None,
        project_path: str | None = None,
    ) -> Town:
        """Update town configuration.

        Args:
            name: Town name.
            port: New port (if changing).
            max_workers: New max workers.
            default_roles: New default roles.
            description: New description.
            project_path: New project path.

        Returns:
            Updated Town object.

        Raises:
            TownNotFoundError: If town not found.
            PortConflictError: If new port is in use.
            ProjectPathConflictError: If new project path is in use.
        """
        town = self.get(name)

        if port is not None and port != town.port:
            # Check port conflict
            existing = self.db.get_town_by_port(port)
            if existing is not None and existing.name != name:
                raise PortConflictError(f"Port {port} already in use by town '{existing.name}'")
            town.port = port

        if max_workers is not None:
            town.max_workers = max_workers

        if default_roles is not None:
            town.default_roles = default_roles

        if description is not None:
            town.description = description

        if project_path is not None and project_path != town.project_path:
            # Check project path conflict
            existing_by_path = self.db.list_towns(project_path=project_path)
            if existing_by_path and existing_by_path[0].name != name:
                raise ProjectPathConflictError(
                    f"Project path '{project_path}' already in use by town "
                    f"'{existing_by_path[0].name}'"
                )
            town.project_path = project_path

        self.db.update_town(town)
        logger.info(f"Updated town '{name}'")

        return town

    def set_status(
        self,
        name: str,
        status: TownStatus,
        pid: int | None = None,
    ) -> Town:
        """Update town status.

        Args:
            name: Town name.
            status: New status.
            pid: Dashboard PID (if starting).

        Returns:
            Updated Town object.
        """
        town = self.get(name)
        town.status = status
        town.pid = pid

        if status == TownStatus.RUNNING:
            town.started_at = datetime.now().isoformat()
        elif status == TownStatus.STOPPED:
            town.pid = None

        self.db.update_town(town)
        return town

    def get_or_create_default(self, project_path: str | None = None) -> Town:
        """Get the default town, creating it if needed.

        Args:
            project_path: Project path for the default town.

        Returns:
            Default Town object.
        """
        default = self.db.get_town("default")
        if default is not None:
            return default

        return self.create(
            name="default",
            port=DEFAULT_PORT_START,
            project_path=project_path,
            description="Default orchestration town",
        )

    def count_running(self) -> int:
        """Count running towns."""
        return self.db.count_towns(status=TownStatus.RUNNING)


def get_default_town_manager(mab_dir: Path | None = None) -> TownManager:
    """Get a TownManager with default configuration.

    Args:
        mab_dir: Global .mab directory. Defaults to ~/.mab/.

    Returns:
        Configured TownManager.
    """
    if mab_dir is None:
        mab_dir = Path.home() / ".mab"
    return TownManager(mab_dir=mab_dir)


def get_next_handoff(current_role: str, workflow: list[str]) -> str | None:
    """Get the next handoff target for a role in a workflow.

    Given a current role and a workflow sequence, returns the next step
    that work should be handed off to.

    Args:
        current_role: The current role completing work (e.g., "dev", "qa").
        workflow: List of workflow step strings defining the handoff chain.

    Returns:
        Next role/step in the workflow (e.g., "qa", "human_merge", "done"),
        or None if current role is not in the workflow or is the last step.

    Examples:
        >>> get_next_handoff("dev", ["dev", "qa", "human_merge"])
        "qa"
        >>> get_next_handoff("qa", ["dev", "qa", "human_merge"])
        "human_merge"
        >>> get_next_handoff("dev", ["dev", "human_merge"])
        "human_merge"
        >>> get_next_handoff("reviewer", ["dev", "qa"])  # Not in workflow
        None
    """
    try:
        current_index = workflow.index(current_role)
    except ValueError:
        return None

    if current_index + 1 < len(workflow):
        return workflow[current_index + 1]

    return None
