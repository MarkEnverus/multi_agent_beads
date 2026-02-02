"""REST API endpoints for agent status monitoring.

All file I/O operations are wrapped with asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard.config import AGENT_STALE_MINUTES, LOG_FILE
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


# Log line pattern: [TIMESTAMP] [ID] EVENT_TYPE: details
# ID can be either a numeric PID or an alphanumeric worker ID (e.g., "worker-dev-1")
# Worker IDs fix the tracking bug where PIDs change per bash subshell command
LOG_PATTERN = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[([a-zA-Z0-9_-]+)\] (.+)")

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
                    timestamp_str, id_str, content = match.groups()
                    # ID can be numeric PID or alphanumeric worker_id
                    # For backward compatibility, try to parse as int first
                    try:
                        pid = int(id_str)
                        worker_id = id_str  # Use string version for tracking
                    except ValueError:
                        # Alphanumeric worker ID - use 0 as placeholder PID
                        pid = 0
                        worker_id = id_str

                    entries.append(
                        {
                            "timestamp": timestamp_str,
                            "pid": pid,  # For backward compatibility
                            "worker_id": worker_id,  # Primary tracking key
                            "content": content,
                        }
                    )

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


def _extract_agents_from_logs(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract agent sessions from log entries.

    Returns a dict of worker_id -> agent info for active sessions.
    Uses worker_id for tracking to handle the PID instability bug where
    each bash command runs in a different subshell with a different PID.
    """
    agents: dict[str, dict[str, Any]] = {}

    for entry in entries:
        # Use worker_id as primary tracking key (handles both PIDs and alphanumeric IDs)
        worker_id = entry.get("worker_id", str(entry["pid"]))
        pid = entry["pid"]
        content = entry["content"]
        timestamp = entry["timestamp"]

        if content == "SESSION_START":
            # New session started
            agents[worker_id] = {
                "pid": pid,  # Keep for API compatibility
                "worker_id": worker_id,  # Primary identifier
                "status": "idle",
                "current_bead": None,
                "current_bead_title": None,
                "last_activity": timestamp,
                "role": "unknown",
                "instance": 1,
            }
        elif content.startswith("SESSION_END:"):
            # Session ended - mark as ended
            if worker_id in agents:
                agents[worker_id]["status"] = "ended"
                agents[worker_id]["last_activity"] = timestamp
        elif content.startswith("CLAIM:"):
            # Agent claimed a bead
            if worker_id in agents:
                # Parse: CLAIM: bead-id - title
                parts = content[6:].strip().split(" - ", 1)
                if parts:
                    agents[worker_id]["current_bead"] = parts[0].strip()
                    if len(parts) > 1:
                        agents[worker_id]["current_bead_title"] = parts[1].strip()
                    agents[worker_id]["status"] = "working"
                    agents[worker_id]["last_activity"] = timestamp
        elif content.startswith("CLOSE:"):
            # Bead closed - still active until SESSION_END
            if worker_id in agents:
                agents[worker_id]["current_bead"] = None
                agents[worker_id]["current_bead_title"] = None
                agents[worker_id]["status"] = "idle"
                agents[worker_id]["last_activity"] = timestamp
        elif worker_id in agents:
            # Only meaningful work events update the activity timestamp
            # NO_WORK polls should NOT count as activity
            meaningful_prefixes = (
                "WORK_START:",
                "READ:",
                "TESTS",
                "BEAD_CREATE:",
                "PR_CREATE:",
                "PR_CREATED:",
                "PR_MERGED:",
                "CI:",
                "BLOCKED:",
                "ERROR:",
            )
            if content.startswith(meaningful_prefixes):
                agents[worker_id]["last_activity"] = timestamp

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


def _is_agent_stale(timestamp_str: str, stale_minutes: int) -> bool:
    """Check if an agent's last activity is older than the staleness threshold.

    Args:
        timestamp_str: The agent's last activity timestamp (YYYY-MM-DD HH:MM:SS).
        stale_minutes: Threshold in minutes after which an agent is considered stale.

    Returns:
        True if the agent is stale (no activity for longer than threshold).
    """
    try:
        last_activity = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
        now = datetime.now()  # noqa: DTZ005
        age_minutes = (now - last_activity).total_seconds() / 60
        return age_minutes > stale_minutes
    except ValueError:
        logger.warning("Invalid timestamp format for staleness check: %s", timestamp_str)
        return True  # Treat unparseable timestamps as stale


def _is_pid_running(pid: int) -> bool:
    """Check if a process with the given PID is currently running.

    Uses os.kill with signal 0 which doesn't actually send a signal,
    but checks if the process exists and we have permission to signal it.

    Args:
        pid: Process ID to check.

    Returns:
        True if the process is running, False otherwise.
    """
    # PIDs must be positive integers
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # Process does not exist
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        # This means it's running (owned by another user)
        return True
    except OSError:
        # Other OS error - assume not running
        return False


def _is_claude_agent_process(pid: int) -> bool:
    """Check if the process is actually a Claude agent, not a recycled PID.

    Uses the `ps` command to inspect the process command line. A Claude agent
    process should have 'claude' in its command line. This prevents false
    positives when a PID has been recycled by the OS for a completely
    different process.

    Args:
        pid: Process ID to check.

    Returns:
        True if the process appears to be a Claude agent, False otherwise.
    """
    import subprocess

    try:
        # Get the command line of the process
        # Works on macOS and Linux
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            # Process doesn't exist or ps failed
            return False

        command = result.stdout.strip().lower()
        if not command:
            return False

        # Check if this looks like a Claude agent process
        # Claude Code runs as a Node.js process with "claude" in the command
        # or as a spawned subprocess from the dashboard
        claude_indicators = ["claude", "anthropic", "mab", "multi_agent"]

        return any(indicator in command for indicator in claude_indicators)

    except subprocess.TimeoutExpired:
        logger.warning("Timeout checking process %d command line", pid)
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Error checking process %d: %s", pid, e)
        return False


def _get_active_agents() -> list[dict[str, Any]]:
    """Get list of currently active (non-ended, non-stale) agents.

    Returns:
        List of active agent dictionaries.

    Raises:
        LogFileError: If the log file cannot be read.
    """
    entries = _parse_log_file()
    agents = _extract_agents_from_logs(entries)

    # Filter to only active agents (not ended, not stale, and process verification)
    active = []
    for agent in agents.values():
        if agent["status"] == "ended":
            continue

        worker_id = agent.get("worker_id", str(agent["pid"]))
        pid = agent["pid"]

        # Check freshness - skip agents that haven't had activity recently
        if _is_agent_stale(agent["last_activity"], AGENT_STALE_MINUTES):
            logger.debug(
                "Skipping stale agent %s (last activity: %s)",
                worker_id,
                agent["last_activity"],
            )
            continue

        # PID verification: only for numeric worker_ids (actual PIDs)
        # For alphanumeric worker_ids, we can't verify the process - rely on staleness
        is_numeric_pid = pid > 0 and worker_id.isdigit()
        if is_numeric_pid:
            # Verify process is actually running - skip phantom agents
            if not _is_pid_running(pid):
                logger.debug(
                    "Skipping phantom agent PID %d (process not running)",
                    pid,
                )
                continue

            # Verify the process is actually a Claude agent, not a recycled PID
            if not _is_claude_agent_process(pid):
                logger.debug(
                    "Skipping recycled PID %d (not a Claude agent process)",
                    pid,
                )
                continue

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
