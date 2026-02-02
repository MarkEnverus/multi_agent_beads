"""MAB Database - SQLite database for worker tracking.

This module provides the database schema and utilities for tracking worker
state and events. It's designed to replace log-parsing based monitoring
with persistent, queryable state.

Database location: .mab/mab.db (per-project)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the database schema.

    Creates tables if they don't exist. This function is idempotent and safe
    to call multiple times.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Open database connection with the schema initialized.
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Enable foreign keys and WAL mode for better concurrency
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")

    # Create workers table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY,
            pid INTEGER,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            project_path TEXT NOT NULL,
            worktree_path TEXT,
            worktree_branch TEXT,
            log_file TEXT,
            started_at TIMESTAMP NOT NULL,
            stopped_at TIMESTAMP,
            exit_code INTEGER,
            error_message TEXT
        )
    """)

    # Create worker_events table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS worker_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            bead_id TEXT,
            message TEXT,
            timestamp TIMESTAMP NOT NULL,
            FOREIGN KEY (worker_id) REFERENCES workers(id)
        )
    """)

    # Create indexes for common queries
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_workers_project ON workers(project_path)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_worker ON worker_events(worker_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON worker_events(timestamp)
    """)

    # Store schema version for future migrations
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_info (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
        ("version", str(SCHEMA_VERSION)),
    )

    conn.commit()
    return conn


def get_db(project_path: Path | str) -> sqlite3.Connection:
    """Get a database connection for a project.

    Opens (and initializes if needed) the database at .mab/mab.db within
    the given project path.

    Args:
        project_path: Path to the project root directory.

    Returns:
        Open database connection.
    """
    project_path = Path(project_path)
    db_path = project_path / ".mab" / "mab.db"
    return init_db(db_path)


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database.

    Args:
        conn: Database connection.

    Returns:
        Schema version number, or 0 if not set.
    """
    try:
        cursor = conn.execute("SELECT value FROM schema_info WHERE key = ?", ("version",))
        row = cursor.fetchone()
        return int(row["value"]) if row else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def migrate_db(conn: sqlite3.Connection) -> bool:
    """Run any pending database migrations.

    Args:
        conn: Database connection.

    Returns:
        True if migrations were applied, False if already up to date.
    """
    current_version = get_schema_version(conn)

    if current_version >= SCHEMA_VERSION:
        return False

    # Future migrations go here
    # if current_version < 2:
    #     _migrate_v1_to_v2(conn)

    # Update version
    conn.execute(
        "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
        ("version", str(SCHEMA_VERSION)),
    )
    conn.commit()

    return current_version < SCHEMA_VERSION


# CRUD helpers for workers table


def insert_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    role: str,
    status: str,
    project_path: str,
    started_at: datetime | None = None,
    pid: int | None = None,
    worktree_path: str | None = None,
    worktree_branch: str | None = None,
    log_file: str | None = None,
) -> None:
    """Insert a new worker record.

    Args:
        conn: Database connection.
        worker_id: Unique worker identifier.
        role: Worker role (dev, qa, tech_lead, manager, reviewer).
        status: Worker status (spawning, running, stopped, crashed).
        project_path: Path to the project.
        started_at: When the worker started. Defaults to now.
        pid: Process ID.
        worktree_path: Path to git worktree.
        worktree_branch: Branch name in worktree.
        log_file: Path to worker log file.
    """
    if started_at is None:
        started_at = datetime.now()

    conn.execute(
        """
        INSERT INTO workers (
            id, pid, role, status, project_path,
            worktree_path, worktree_branch, log_file, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            worker_id,
            pid,
            role,
            status,
            project_path,
            worktree_path,
            worktree_branch,
            log_file,
            started_at.isoformat(),
        ),
    )
    conn.commit()


def update_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    **kwargs: Any,
) -> bool:
    """Update a worker record.

    Args:
        conn: Database connection.
        worker_id: Worker ID to update.
        **kwargs: Fields to update (status, pid, stopped_at, exit_code, error_message, etc.)

    Returns:
        True if a row was updated, False if worker not found.
    """
    if not kwargs:
        return False

    # Handle datetime objects
    for key, value in kwargs.items():
        if isinstance(value, datetime):
            kwargs[key] = value.isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [worker_id]

    cursor = conn.execute(
        f"UPDATE workers SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    return cursor.rowcount > 0


def get_worker(conn: sqlite3.Connection, worker_id: str) -> Any:
    """Get a worker by ID.

    Args:
        conn: Database connection.
        worker_id: Worker ID.

    Returns:
        Worker row (sqlite3.Row) or None if not found.
    """
    cursor = conn.execute("SELECT * FROM workers WHERE id = ?", (worker_id,))
    return cursor.fetchone()


def list_workers(
    conn: sqlite3.Connection,
    status: str | None = None,
    role: str | None = None,
    project_path: str | None = None,
) -> list[Any]:
    """List workers with optional filters.

    Args:
        conn: Database connection.
        status: Filter by status.
        role: Filter by role.
        project_path: Filter by project path.

    Returns:
        List of worker rows (sqlite3.Row objects).
    """
    conditions = []
    params: list[Any] = []

    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if role is not None:
        conditions.append("role = ?")
        params.append(role)
    if project_path is not None:
        conditions.append("project_path = ?")
        params.append(project_path)

    query = "SELECT * FROM workers"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC"

    cursor = conn.execute(query, params)
    return cursor.fetchall()


def delete_worker(conn: sqlite3.Connection, worker_id: str) -> bool:
    """Delete a worker and its events.

    Args:
        conn: Database connection.
        worker_id: Worker ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    # Delete events first (due to foreign key)
    conn.execute("DELETE FROM worker_events WHERE worker_id = ?", (worker_id,))
    cursor = conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
    conn.commit()
    return cursor.rowcount > 0


# CRUD helpers for worker_events table


def insert_event(
    conn: sqlite3.Connection,
    worker_id: str,
    event_type: str,
    bead_id: str | None = None,
    message: str | None = None,
    timestamp: datetime | None = None,
) -> int:
    """Insert a worker event.

    Args:
        conn: Database connection.
        worker_id: Worker that generated the event.
        event_type: Type of event (spawn, claim, close, error, terminate).
        bead_id: Associated bead ID (if any).
        message: Event message.
        timestamp: When the event occurred. Defaults to now.

    Returns:
        ID of the inserted event.
    """
    if timestamp is None:
        timestamp = datetime.now()

    cursor = conn.execute(
        """
        INSERT INTO worker_events (worker_id, event_type, bead_id, message, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (worker_id, event_type, bead_id, message, timestamp.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid or 0


def list_events(
    conn: sqlite3.Connection,
    worker_id: str | None = None,
    event_type: str | None = None,
    bead_id: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """List worker events with optional filters.

    Args:
        conn: Database connection.
        worker_id: Filter by worker.
        event_type: Filter by event type.
        bead_id: Filter by bead.
        limit: Maximum number of events to return.

    Returns:
        List of event rows (sqlite3.Row objects), ordered by timestamp descending.
    """
    conditions = []
    params: list[Any] = []

    if worker_id is not None:
        conditions.append("worker_id = ?")
        params.append(worker_id)
    if event_type is not None:
        conditions.append("event_type = ?")
        params.append(event_type)
    if bead_id is not None:
        conditions.append("bead_id = ?")
        params.append(bead_id)

    query = "SELECT * FROM worker_events"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp DESC"

    if limit is not None:
        query += f" LIMIT {limit}"

    cursor = conn.execute(query, params)
    return cursor.fetchall()
