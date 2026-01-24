"""REST API endpoints for bead operations."""

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


@router.get("", response_model=list[BeadResponse])
async def list_beads(
    status: str | None = Query(None, description="Filter by status (open, in_progress, closed)"),
    label: str | None = Query(None, description="Filter by label"),
    priority: int | None = Query(None, ge=0, le=4, description="Filter by priority (0-4)"),
) -> list[dict[str, Any]]:
    """List all beads with optional filters.

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Listing beads: status=%s, label=%s, priority=%s", status, label, priority)
    return BeadService.list_beads(status=status, label=label, priority=priority)


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
    return BeadService.list_ready(label=label)


@router.get("/in-progress", response_model=list[BeadResponse])
async def list_in_progress_beads() -> list[dict[str, Any]]:
    """List beads that are currently in progress.

    Raises:
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.debug("Listing in-progress beads")
    return BeadService.list_beads(status="in_progress")


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
    return BeadService.get_bead(bead_id)


@router.post("", response_model=BeadResponse, status_code=201)
async def create_bead(bead: BeadCreate) -> dict[str, Any]:
    """Create a new bead.

    Raises:
        BeadValidationError: If input validation fails.
        BeadCommandError: If the bd command fails.
        BeadParseError: If output parsing fails.
    """
    logger.info("Creating bead: %s", bead.title)
    return BeadService.create_bead(
        title=bead.title,
        description=bead.description,
        priority=bead.priority,
        issue_type=bead.issue_type,
        labels=bead.labels,
    )
