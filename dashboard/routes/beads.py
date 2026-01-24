"""REST API endpoints for bead operations."""

import json
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

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


def _run_bd_command(args: list[str]) -> tuple[bool, str]:
    """Run a bd command and return (success, output)."""
    try:
        result = subprocess.run(
            ["bd", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, "bd command not found"
    except Exception as e:
        return False, str(e)


def _parse_beads_json(output: str) -> list[dict[str, Any]]:
    """Parse JSON output from bd command."""
    try:
        result = json.loads(output)
        if isinstance(result, list):
            return result  # type: ignore[return-value]
        return []
    except json.JSONDecodeError:
        return []


@router.get("", response_model=list[BeadResponse])
async def list_beads(
    status: str | None = Query(None, description="Filter by status (open, in_progress, closed)"),
    label: str | None = Query(None, description="Filter by label"),
    priority: int | None = Query(None, ge=0, le=4, description="Filter by priority (0-4)"),
) -> list[dict[str, Any]]:
    """List all beads with optional filters."""
    args = ["list", "--json", "--limit", "0"]

    if status:
        args.extend(["--status", status])
    if label:
        args.extend(["-l", label])
    if priority is not None:
        args.extend(["-p", str(priority)])

    success, output = _run_bd_command(args)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to list beads: {output}")

    return _parse_beads_json(output)


@router.get("/ready", response_model=list[BeadResponse])
async def list_ready_beads(
    label: str | None = Query(None, description="Filter by label"),
) -> list[dict[str, Any]]:
    """List beads that are ready to work on (no blockers)."""
    args = ["ready", "--json"]

    if label:
        args.extend(["-l", label])

    success, output = _run_bd_command(args)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to list ready beads: {output}")

    return _parse_beads_json(output)


@router.get("/in-progress", response_model=list[BeadResponse])
async def list_in_progress_beads() -> list[dict[str, Any]]:
    """List beads that are currently in progress."""
    args = ["list", "--json", "--status", "in_progress", "--limit", "0"]

    success, output = _run_bd_command(args)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to list in-progress beads: {output}")

    return _parse_beads_json(output)


@router.get("/{bead_id}", response_model=BeadResponse)
async def get_bead(bead_id: str) -> dict[str, Any]:
    """Get details for a single bead."""
    args = ["show", bead_id, "--json"]

    success, output = _run_bd_command(args)
    if not success:
        if "not found" in output.lower():
            raise HTTPException(status_code=404, detail=f"Bead not found: {bead_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get bead: {output}")

    beads = _parse_beads_json(output)
    if not beads:
        raise HTTPException(status_code=404, detail=f"Bead not found: {bead_id}")

    return beads[0]


@router.post("", response_model=BeadResponse, status_code=201)
async def create_bead(bead: BeadCreate) -> dict[str, Any]:
    """Create a new bead."""
    args = [
        "create",
        "--title", bead.title,
        "-p", str(bead.priority),
        "-t", bead.issue_type,
        "--silent",
    ]

    if bead.description:
        args.extend(["-d", bead.description])
    if bead.labels:
        args.extend(["-l", ",".join(bead.labels)])

    success, output = _run_bd_command(args)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to create bead: {output}")

    # The --silent flag returns just the ID
    bead_id = output.strip()
    if not bead_id:
        raise HTTPException(status_code=500, detail="Failed to get created bead ID")

    # Fetch and return the created bead
    return await get_bead(bead_id)
