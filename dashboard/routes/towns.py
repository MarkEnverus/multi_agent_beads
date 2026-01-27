"""Towns API routes for the Multi-Agent Dashboard.

Provides endpoints for listing and managing orchestration towns.
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from dashboard.config import TOWN_NAME

# Try to import town management - gracefully handle if mab package not installed
try:
    from mab.towns import TownManager, TownNotFoundError, TownStatus

    MAB_HOME = Path.home() / ".mab"
    TOWNS_AVAILABLE = True
except ImportError:
    TOWNS_AVAILABLE = False
    TownManager = None  # type: ignore
    TownNotFoundError = Exception  # type: ignore
    TownStatus = None  # type: ignore
    MAB_HOME = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/towns", tags=["towns"])


def _get_town_manager() -> Any:
    """Get a TownManager instance."""
    if not TOWNS_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="Town management not available (mab package not installed)",
        )
    return TownManager(MAB_HOME)


@router.get("")
async def list_towns() -> dict[str, Any]:
    """List all configured towns.

    Returns:
        Dictionary with towns list and current town info.
    """
    manager = _get_town_manager()
    towns = manager.list_towns()

    return {
        "towns": [t.to_dict() for t in towns],
        "current_town": TOWN_NAME,
        "count": len(towns),
    }


@router.get("/current")
async def get_current_town() -> dict[str, Any]:
    """Get the current town configuration.

    Returns:
        Current town details or default if not found.
    """
    manager = _get_town_manager()

    try:
        town = manager.get(TOWN_NAME)
        return {
            "town": town.to_dict(),
            "name": TOWN_NAME,
        }
    except TownNotFoundError:
        # Return default info if town not in database
        return {
            "town": None,
            "name": TOWN_NAME,
            "message": f"Town '{TOWN_NAME}' not found in database (using defaults)",
        }


@router.get("/{town_name}")
async def get_town(town_name: str) -> dict[str, Any]:
    """Get details of a specific town.

    Args:
        town_name: Name of the town.

    Returns:
        Town details.
    """
    manager = _get_town_manager()

    try:
        town = manager.get(town_name)
        return {"town": town.to_dict()}
    except TownNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Town '{town_name}' not found",
        )


@router.get("/status/summary")
async def towns_status_summary() -> dict[str, Any]:
    """Get summary of all towns status.

    Returns:
        Summary with counts by status.
    """
    manager = _get_town_manager()
    towns = manager.list_towns()

    running = sum(1 for t in towns if t.status == TownStatus.RUNNING)
    stopped = sum(1 for t in towns if t.status == TownStatus.STOPPED)

    return {
        "total": len(towns),
        "running": running,
        "stopped": stopped,
        "towns": [
            {
                "name": t.name,
                "port": t.port,
                "status": t.status.value,
            }
            for t in towns
        ],
    }
