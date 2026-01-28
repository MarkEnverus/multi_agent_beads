"""REST API endpoints for agent status monitoring.

All file I/O operations are wrapped with asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard.config import LOG_FILE
from dashboard.exceptions import LogFileError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentStatus(BaseModel):
    """Response model for an agent's current status."""

    role: str = Field(..., description="Agent role (developer, qa, etc.)")
    instance: int = Field(..., description="Agent instance number")
    current_bead: str | None = Field(None, description="Currently claimed bead ID")
    current_bead_title: str | None = Field(None, description="Title of current bead")
    status: str = Field(..., description="Agent status (working, idle, ended)")
    last_activity: str = Field(..., description="ISO timestamp of last activity")
    pid: int = Field(..., description="Process ID of the agent")


# Log line pattern: [TIMESTAMP] [PID] EVENT_TYPE: details
LOG_PATTERN = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\d+)\] (.+)"
)

# Valid agent roles
VALID_ROLES = {"developer", "qa", "reviewer", "tech_lead", "manager", "unknown"}


def _parse_log_file() -> list[dict[str, Any]]:
    """Parse claude.log and return list of log entries.

    Returns:
        List of parsed log entry dictionaries.

    Raises:
        LogFileError: If the log file cannot be read or parsed.
    """
    log_path = Path(LOG_FILE)

    if not log_path.exists():
        logger.debug("Log file does not exist: %s", log_path)
        return []

    entries = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                match = LOG_PATTERN.match(line)
                if match:
                    timestamp_str, pid_str, content = match.groups()
                    try:
                        entries.append({
                            "timestamp": timestamp_str,
                            "pid": int(pid_str),
                            "content": content,
                        })
                    except ValueError as e:
                        logger.warning("Invalid PID on line %d: %s", line_num, e)
                        continue

    except PermissionError:
        logger.error("Permission denied reading log file: %s", log_path)
        raise LogFileError(
            message="Permission denied when reading log file",
            file_path=str(log_path),
        ) from None

    except OSError as e:
        logger.error("Error reading log file: %s - %s", log_path, e)
        raise LogFileError(
            message=f"Failed to read log file: {e}",
            file_path=str(log_path),
        ) from None

    logger.debug("Parsed %d log entries from %s", len(entries), log_path)
    return entries


def _extract_agents_from_logs(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Extract agent sessions from log entries.

    Returns a dict of PID -> agent info for active sessions.
    """
    agents: dict[int, dict[str, Any]] = {}

    for entry in entries:
        pid = entry["pid"]
        content = entry["content"]
        timestamp = entry["timestamp"]

        if content == "SESSION_START":
            # New session started
            agents[pid] = {
                "pid": pid,
                "status": "idle",
                "current_bead": None,
                "current_bead_title": None,
                "last_activity": timestamp,
                "role": "unknown",
                "instance": 1,
            }
        elif content.startswith("SESSION_END:"):
            # Session ended - mark as ended
            if pid in agents:
                agents[pid]["status"] = "ended"
                agents[pid]["last_activity"] = timestamp
        elif content.startswith("CLAIM:"):
            # Agent claimed a bead
            if pid in agents:
                # Parse: CLAIM: bead-id - title
                parts = content[6:].strip().split(" - ", 1)
                if parts:
                    agents[pid]["current_bead"] = parts[0].strip()
                    if len(parts) > 1:
                        agents[pid]["current_bead_title"] = parts[1].strip()
                    agents[pid]["status"] = "working"
                    agents[pid]["last_activity"] = timestamp
        elif content.startswith("CLOSE:"):
            # Bead closed - still active until SESSION_END
            if pid in agents:
                agents[pid]["current_bead"] = None
                agents[pid]["current_bead_title"] = None
                agents[pid]["status"] = "idle"
                agents[pid]["last_activity"] = timestamp
        elif pid in agents:
            # Any other activity updates timestamp
            agents[pid]["last_activity"] = timestamp

    return agents


def _infer_role_from_bead_title(title: str | None) -> str:
    """Infer agent role from bead title or labels."""
    if not title:
        return "unknown"

    title_lower = title.lower()

    # Simple heuristics based on common patterns
    if any(kw in title_lower for kw in ["test", "qa", "verify"]):
        return "qa"
    if any(kw in title_lower for kw in ["review", "pr review"]):
        return "reviewer"
    if any(kw in title_lower for kw in ["arch", "design", "tech lead"]):
        return "tech_lead"
    if any(kw in title_lower for kw in ["epic", "priorit", "manage"]):
        return "manager"

    return "developer"


def _format_iso_timestamp(timestamp_str: str) -> str:
    """Convert log timestamp to ISO format."""
    try:
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
        return dt.isoformat() + "Z"
    except ValueError:
        logger.warning("Invalid timestamp format: %s", timestamp_str)
        return timestamp_str


def _get_active_agents() -> list[dict[str, Any]]:
    """Get list of currently active (non-ended) agents.

    Returns:
        List of active agent dictionaries.

    Raises:
        LogFileError: If the log file cannot be read.
    """
    entries = _parse_log_file()
    agents = _extract_agents_from_logs(entries)

    # Filter to only active agents (not ended)
    active = []
    for agent in agents.values():
        if agent["status"] != "ended":
            # Infer role from current bead title
            agent["role"] = _infer_role_from_bead_title(agent.get("current_bead_title"))
            agent["last_activity"] = _format_iso_timestamp(agent["last_activity"])
            active.append(agent)

    # Sort by last activity (most recent first)
    active.sort(key=lambda a: a["last_activity"], reverse=True)

    logger.debug("Found %d active agents", len(active))
    return active


@router.get("", response_model=list[AgentStatus])
async def list_agents() -> list[dict[str, Any]]:
    """List all active agent sessions.

    Returns agents that have started but not yet ended their session.
    Each agent shows their current bead (if claimed) and status.

    Raises:
        LogFileError: If the log file cannot be read.
    """
    # Run blocking file I/O in thread pool to avoid blocking event loop
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
        LogFileError: If the log file cannot be read.
    """
    role_normalized = role.lower().replace("-", "_")

    if role_normalized not in VALID_ROLES:
        logger.warning("Invalid role requested: %s", role)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: {role}. Valid roles: {', '.join(sorted(VALID_ROLES))}",
        )

    # Run blocking file I/O in thread pool to avoid blocking event loop
    agents = await asyncio.to_thread(_get_active_agents)
    filtered = [a for a in agents if a["role"] == role_normalized]
    logger.debug("Found %d agents with role %s", len(filtered), role_normalized)
    return filtered
