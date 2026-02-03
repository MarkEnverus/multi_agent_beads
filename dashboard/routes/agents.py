"""REST API endpoints for agent status monitoring.

All file I/O operations are wrapped with asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentStatus(BaseModel):
    """Response model for an agent's current status."""

    worker_id: str = Field(..., description="Unique worker identifier")
    role: str = Field(..., description="Agent role (developer, qa, etc.)")
    instance: int = Field(..., description="Agent instance number")
    current_bead: str | None = Field(None, description="Currently claimed bead ID")
    current_bead_title: str | None = Field(None, description="Title of current bead")
    status: str = Field(..., description="Agent status (working, idle, ended)")
    last_activity: str = Field(..., description="ISO timestamp of last activity")
    pid: int | None = Field(None, description="Process ID of the agent, null if not started")


# Valid agent roles - maps DB roles to API roles
ROLE_MAP = {
    "dev": "developer",
    "qa": "qa",
    "tech_lead": "tech_lead",
    "manager": "manager",
    "reviewer": "reviewer",
}
VALID_ROLES = {"developer", "qa", "reviewer", "tech_lead", "manager", "unknown"}


def _get_workers_from_db() -> list[dict[str, Any]]:
    """Get worker state from workers.db.

    Returns running/spawning workers and recently stopped workers (last hour).
    Checks both per-project database (.mab/workers.db) and falls back to
    global database (~/.mab/workers.db) if project-local doesn't exist.
    """
    # Try per-project database first
    db_path = PROJECT_ROOT / ".mab" / "workers.db"
    if not db_path.exists():
        # Fall back to global database
        db_path = Path.home() / ".mab" / "workers.db"
    if not db_path.exists():
        logger.debug("Database does not exist: %s", db_path)
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Note: Python ISO timestamps use 'T' separator while SQLite datetime uses space.
        # Use REPLACE to normalize the comparison.
        one_hour_ago = conn.execute("SELECT datetime('now', 'localtime', '-1 hour')").fetchone()[0]

        # Check if worker_events table exists (mab.db has it, workers.db doesn't)
        has_events_table = (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_events'"
            ).fetchone()
            is not None
        )

        # Filter to current project only
        project_path = str(PROJECT_ROOT)

        if has_events_table:
            workers = conn.execute(
                """
                SELECT w.*,
                       (SELECT COUNT(*) FROM worker_events e
                        WHERE e.worker_id = w.id AND e.event_type = 'claim') as beads_claimed
                FROM workers w
                WHERE w.project_path = ?
                  AND (w.status IN ('running', 'spawning', 'starting')
                       OR REPLACE(w.stopped_at, 'T', ' ') > ?)
                ORDER BY w.started_at DESC
            """,
                (project_path, one_hour_ago),
            ).fetchall()
        else:
            # workers.db schema - no worker_events table
            workers = conn.execute(
                """
                SELECT w.*, 0 as beads_claimed
                FROM workers w
                WHERE w.project_path = ?
                  AND (w.status IN ('running', 'spawning', 'starting')
                       OR REPLACE(COALESCE(w.stopped_at, ''), 'T', ' ') > ?)
                ORDER BY w.started_at DESC
            """,
                (project_path, one_hour_ago),
            ).fetchall()

        result = [dict(w) for w in workers]
        conn.close()

        logger.debug("Found %d workers in database", len(result))
        return result

    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return []


def _parse_claim_from_log(log_file: str | None) -> tuple[str | None, str | None]:
    """Parse the most recent CLAIM line from a worker's log file.

    Workers log claims as: CLAIM: <bead-id> - <title>
    This function reads the log file and extracts the most recent claim.

    Args:
        log_file: Path to the worker's log file.

    Returns:
        Tuple of (bead_id, bead_title), or (None, None) if no claim found.
    """
    if not log_file:
        return None, None

    log_path = Path(log_file)
    if not log_path.exists():
        return None, None

    try:
        # Read the log file and find CLAIM lines
        # Pattern: [timestamp] [worker_id] CLAIM: <bead-id> - <title>
        claim_pattern = re.compile(r"CLAIM:\s*(\S+)\s*-\s*(.+)$")

        # Read from the end to find the most recent claim
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Search from most recent to oldest
        for line in reversed(lines):
            match = claim_pattern.search(line)
            if match:
                bead_id = match.group(1)
                title = match.group(2).strip()
                return bead_id, title

        return None, None
    except (OSError, IOError) as e:
        logger.debug("Could not read log file %s: %s", log_file, e)
        return None, None


def _get_current_bead_for_worker(
    worker_id: str, log_file: str | None = None
) -> tuple[str | None, str | None]:
    """Get the current bead for a worker.

    First tries to find claims in the worker_events table (mab.db).
    Falls back to parsing the worker's log file for CLAIM entries.

    Args:
        worker_id: The worker's unique identifier.
        log_file: Optional path to the worker's log file for fallback parsing.

    Returns:
        Tuple of (bead_id, bead_title), or (None, None) if no claim found.
    """
    # Try mab.db first (has worker_events table)
    db_path = PROJECT_ROOT / ".mab" / "mab.db"
    if not db_path.exists():
        # Try global mab.db
        db_path = Path.home() / ".mab" / "mab.db"

    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Check if worker_events table exists
            has_events = (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_events'"
                ).fetchone()
                is not None
            )

            if has_events:
                # Get the most recent claim event for this worker
                event = conn.execute(
                    """
                    SELECT bead_id, message FROM worker_events
                    WHERE worker_id = ? AND event_type = 'claim'
                    ORDER BY timestamp DESC
                    LIMIT 1
                """,
                    (worker_id,),
                ).fetchone()

                conn.close()

                if event:
                    return event["bead_id"], event["message"]
            else:
                conn.close()

        except sqlite3.Error:
            pass

    # Fall back to parsing the worker's log file
    return _parse_claim_from_log(log_file)


def _map_db_status_to_api(db_status: str, current_bead: str | None) -> str:
    """Map database status to API status.

    DB statuses: spawning, running, stopped, crashed, failed, stopping
    API statuses: working, idle, ended
    """
    if db_status in ("stopped", "crashed", "failed", "stopping"):
        return "ended"
    if db_status == "spawning":
        return "idle"
    # running status - check if working on a bead
    if current_bead:
        return "working"
    return "idle"


def _extract_instance_from_worker_id(worker_id: str) -> int:
    """Extract instance number from worker ID.

    Worker IDs are formatted like: worker-dev-abc123 or worker-qa-1-xyz789
    Returns 1 as default if no instance found.
    """
    parts = worker_id.split("-")
    for part in parts:
        if part.isdigit():
            return int(part)
    return 1


def _format_db_timestamp(timestamp_str: str | None) -> str:
    """Convert database timestamp to ISO format for API response."""
    if not timestamp_str:
        return ""
    # DB timestamps are already in ISO format
    if "T" in timestamp_str:
        return timestamp_str if timestamp_str.endswith("Z") else timestamp_str + "Z"
    # Convert space-separated format
    return timestamp_str.replace(" ", "T") + "Z"


def _get_active_agents() -> list[dict[str, Any]]:
    """Get list of currently active agents from mab.db.

    Returns:
        List of active agent dictionaries.
    """
    workers = _get_workers_from_db()

    agents = []
    for worker in workers:
        worker_id = worker["id"]
        log_file = worker.get("log_file")
        current_bead, bead_title = _get_current_bead_for_worker(worker_id, log_file)

        # Map DB role to API role
        db_role = worker.get("role", "unknown")
        role = ROLE_MAP.get(db_role, db_role)
        if role not in VALID_ROLES:
            role = "unknown"

        db_status = worker.get("status", "unknown")
        api_status = _map_db_status_to_api(db_status, current_bead)

        # Use stopped_at if available, otherwise started_at
        last_activity = worker.get("stopped_at") or worker.get("started_at", "")

        agents.append(
            {
                "pid": worker.get("pid") or None,
                "worker_id": worker_id,
                "role": role,
                "instance": _extract_instance_from_worker_id(worker_id),
                "current_bead": current_bead,
                "current_bead_title": bead_title,
                "status": api_status,
                "last_activity": _format_db_timestamp(last_activity),
            }
        )

    # Sort by last activity (most recent first)
    agents.sort(key=lambda a: a["last_activity"], reverse=True)

    logger.debug("Found %d active agents from database", len(agents))
    return agents


@router.get("", response_model=list[AgentStatus])
async def list_agents() -> list[dict[str, Any]]:
    """List all active agent sessions.

    Returns workers from mab.db that are currently running/spawning,
    plus recently stopped workers (last hour).
    Each agent shows their current bead (if claimed) and status.
    """
    # Run blocking DB I/O in thread pool to avoid blocking event loop
    return await asyncio.to_thread(_get_active_agents)


@router.get("/{role}", response_model=list[AgentStatus])
async def list_agents_by_role(role: str) -> list[dict[str, Any]]:
    """List active agents filtered by role.

    Args:
        role: Agent role to filter by (developer, qa, reviewer, tech_lead, manager)

    Returns:
        List of agents matching the specified role.

    Raises:
        HTTPException: If the role is invalid.
    """
    role_normalized = role.lower().replace("-", "_")

    if role_normalized not in VALID_ROLES:
        logger.warning("Invalid role requested: %s", role)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: {role}. Valid roles: {', '.join(sorted(VALID_ROLES))}",
        )

    # Run blocking DB I/O in thread pool to avoid blocking event loop
    agents = await asyncio.to_thread(_get_active_agents)
    filtered = [a for a in agents if a["role"] == role_normalized]
    logger.debug("Found %d agents with role %s", len(filtered), role_normalized)
    return filtered
