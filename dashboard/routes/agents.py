"""REST API endpoints for agent status monitoring.

Uses the RPC daemon as the single source of truth for worker data,
enriched with bead information from worker_events/log files.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard.routes.workers import (
    DASHBOARD_RPC_TIMEOUT,
    _enrich_workers_with_bead_info,
    _get_rpc_client,
    _is_worker_recent,
)

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


def _map_status_to_api(worker_status: str, current_bead: str | None) -> str:
    """Map worker status to agent API status.

    Worker statuses: spawning, running, stopped, crashed, failed, stopping
    Agent API statuses: working, idle, ended
    """
    if worker_status in ("stopped", "crashed", "failed", "stopping"):
        return "ended"
    if worker_status == "spawning":
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


def _format_timestamp(timestamp_str: str | None) -> str:
    """Convert timestamp to ISO format for API response."""
    if not timestamp_str:
        return ""
    if "T" in timestamp_str:
        return timestamp_str if timestamp_str.endswith("Z") else timestamp_str + "Z"
    return timestamp_str.replace(" ", "T") + "Z"


def _worker_to_agent(worker: dict[str, Any]) -> dict[str, Any]:
    """Convert an RPC worker dict (enriched with bead info) to an agent dict."""
    worker_id = worker.get("id", "")
    current_bead = worker.get("current_bead")
    bead_title = worker.get("current_bead_title")

    db_role = worker.get("role", "unknown")
    role = ROLE_MAP.get(db_role, db_role)
    if role not in VALID_ROLES:
        role = "unknown"

    worker_status = worker.get("status", "unknown")
    api_status = _map_status_to_api(worker_status, current_bead)
    last_activity = worker.get("stopped_at") or worker.get("started_at", "")

    return {
        "pid": worker.get("pid") or None,
        "worker_id": worker_id,
        "role": role,
        "instance": _extract_instance_from_worker_id(worker_id),
        "current_bead": current_bead,
        "current_bead_title": bead_title,
        "status": api_status,
        "last_activity": _format_timestamp(last_activity),
    }


def _get_active_agents() -> list[dict[str, Any]]:
    """Get list of currently active agents.

    Convenience wrapper used by app.py for the dashboard page.
    """
    return _get_agents_from_rpc(active_only=True)


def _get_recent_agents() -> list[dict[str, Any]]:
    """Get recently stopped/crashed agents.

    Convenience wrapper used by app.py for the dashboard page.
    """
    return _get_agents_from_rpc(active_only=False)


def _get_agents_from_rpc(active_only: bool = True) -> list[dict[str, Any]]:
    """Get agent data from the RPC daemon.

    Uses the same data source as /api/workers for consistency.
    """
    try:
        client = _get_rpc_client()
        result = client.call("worker.list", {}, DASHBOARD_RPC_TIMEOUT)
        workers = result.get("workers", [])

        # Filter to recent workers
        workers = [w for w in workers if _is_worker_recent(w)]

        # Enrich with bead info
        workers = _enrich_workers_with_bead_info(workers)

        # Convert to agent format
        agents = []
        for w in workers:
            status = w.get("status", "unknown")
            is_active = status in ("running", "spawning", "starting")

            if active_only and not is_active:
                continue
            if not active_only and is_active:
                continue

            agents.append(_worker_to_agent(w))

        agents.sort(key=lambda a: a["last_activity"], reverse=True)
        return agents

    except Exception as e:
        logger.warning("RPC call failed for agents, returning empty list: %s", e)
        return []


@router.get("", response_model=list[AgentStatus])
async def list_agents() -> list[dict[str, Any]]:
    """List all active agent sessions.

    Returns only workers that are currently running/spawning/starting.
    Data is sourced from the RPC daemon for consistency with /api/workers.
    """
    return await asyncio.to_thread(_get_agents_from_rpc, True)


@router.get("/recent", response_model=list[AgentStatus])
async def list_recent_agents() -> list[dict[str, Any]]:
    """List recently stopped/crashed agent sessions (last hour)."""
    return await asyncio.to_thread(_get_agents_from_rpc, False)


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

    agents = await asyncio.to_thread(_get_agents_from_rpc, True)
    filtered = [a for a in agents if a["role"] == role_normalized]
    logger.debug("Found %d agents with role %s", len(filtered), role_normalized)
    return filtered
