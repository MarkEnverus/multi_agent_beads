"""REST API endpoints for bead operations.

All BeadService calls are wrapped with asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from dashboard.services import BeadService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/beads", tags=["beads"])


class BeadCreate(BaseModel):
    """Request model for creating a new bead."""

    title: str = Field(..., min_length=1, description="Bead title")
    description: str | None = Field(None, description="Bead description")
    priority: int = Field(2, ge=0, le=4, description="Priority (0=critical, 4=backlog)")
    issue_type: str = Field("task", description="Issue type (task, bug, feature, epic)")
    labels: list[str] = Field(default_factory=list, description="Labels to apply")


class BeadResponse(BaseModel):
    """Response model for a bead."""

    id: str
    title: str
    description: str | None = None
    status: str
    priority: int
    issue_type: str
    owner: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    labels: list[str] = Field(default_factory=list)


class BeadListResponse(BaseModel):
    """Response model for paginated bead list."""

    beads: list[BeadResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=list[BeadResponse])
async def list_beads(
    status: str | None = Query(None, description="Filter by status (open, in_progress, closed)"),
    label: str | None = Query(None, description="Filter by label"),
    priority: int | None = Query(None, ge=0, le=4, description="Filter by priority (0-4)"),
    include_all: bool = Query(False, description="Include closed beads (adds --all flag)"),
    limit: int = Query(0, ge=0, description="Maximum number of beads to return (0 = unlimited)"),
    offset: int = Query(0, ge=0, description="Number of beads to skip (for pagination)"),
) -> list[dict[str, Any]]:
    """List all beads with optional filters.

    Supports pagination via limit and offset parameters. For large datasets,
    use pagination to avoid loading thousands of beads at once.

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug(
        "Listing beads: status=%s, label=%s, priority=%s, include_all=%s, limit=%d, offset=%d",
        status,
        label,
        priority,
        include_all,
        limit,
        offset,
    )
    # Run blocking subprocess call in thread pool to avoid blocking event loop
    beads = await asyncio.to_thread(
        BeadService.list_beads,
        status=status,
        label=label,
        priority=priority,
        include_all=include_all,
    )

    # Apply pagination in memory (bd CLI doesn't support offset)
    if offset > 0:
        beads = beads[offset:]
    if limit > 0:
        beads = beads[:limit]

    return beads


@router.get("/ready", response_model=list[BeadResponse])
async def list_ready_beads(
    label: str | None = Query(None, description="Filter by label"),
) -> list[dict[str, Any]]:
    """List beads that are ready to work on (no blockers).

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Listing ready beads: label=%s", label)
    # Run blocking subprocess call in thread pool to avoid blocking event loop
    return await asyncio.to_thread(BeadService.list_ready, label=label)


@router.get("/queue-depth")
async def queue_depth_by_role() -> dict[str, int]:
    """Get the count of ready beads per role label.

    Returns a dictionary mapping role labels (e.g., dev, qa, review) to the
    number of ready beads waiting for that role. Useful for understanding
    workload distribution across roles.

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Getting queue depth by role")
    return await asyncio.to_thread(BeadService.queue_depth_by_role)


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get project statistics.

    Returns summary counts (total, open, closed, in-progress, blocked) and
    recent activity metrics (commits, issues created/closed in last 24h).

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Getting project stats")
    return await asyncio.to_thread(BeadService.get_stats)


@router.get("/cache-health")
async def get_cache_health() -> dict[str, Any]:
    """Get cache health information for monitoring.

    Returns failure counts per cache key, useful for diagnosing stale data.
    Keys with failure counts above the alert threshold indicate potential issues
    with the bd CLI or database.
    """
    logger.debug("Getting cache health")
    return BeadService.get_cache_health()


@router.get("/in-progress", response_model=list[BeadResponse])
async def list_in_progress_beads() -> list[dict[str, Any]]:
    """List beads that are currently in progress.

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Listing in-progress beads")
    # Run blocking subprocess call in thread pool to avoid blocking event loop
    return await asyncio.to_thread(BeadService.list_beads, status="in_progress")


@router.get("/{bead_id}", response_model=BeadResponse)
async def get_bead(bead_id: str) -> dict[str, Any]:
    """Get details for a single bead.

    Raises:
        BeadValidationError: If the bead ID format is invalid.
        BeadNotFoundError: If the bead doesn't exist.
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Getting bead: %s", bead_id)
    # Run blocking subprocess call in thread pool to avoid blocking event loop
    return await asyncio.to_thread(BeadService.get_bead, bead_id)


@router.post("", response_model=BeadResponse, status_code=201)
async def create_bead(bead: BeadCreate) -> dict[str, Any]:
    """Create a new bead.

    Raises:
        BeadValidationError: If input validation fails.
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.info("Creating bead: %s", bead.title)
    # Run blocking subprocess call in thread pool to avoid blocking event loop
    return await asyncio.to_thread(
        BeadService.create_bead,
        title=bead.title,
        description=bead.description,
        priority=bead.priority,
        issue_type=bead.issue_type,
        labels=bead.labels,
    )
