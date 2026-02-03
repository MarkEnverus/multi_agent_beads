"""Towns API routes for the Multi-Agent Dashboard.

Provides endpoints for listing and managing orchestration towns.
"""

import logging
import sqlite3
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


def _get_active_worker_counts(town_name: str) -> dict[str, int]:
    """Get counts of currently active workers per role.

    Args:
        town_name: Name of the town to filter workers by.

    Returns:
        Dictionary mapping role names to active worker counts.
    """
    counts: dict[str, int] = {}

    # Try project-local database first, then global
    db_paths = [
        Path.cwd() / ".mab" / "workers.db",
        MAB_HOME / "workers.db" if MAB_HOME else None,
    ]

    for db_path in db_paths:
        if db_path and db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT role, COUNT(*) as count
                    FROM workers
                    WHERE status IN ('running', 'spawning')
                    AND (town = ? OR town IS NULL)
                    GROUP BY role
                    """,
                    (town_name,),
                )
                for row in cursor.fetchall():
                    role = row["role"]
                    counts[role] = counts.get(role, 0) + row["count"]
                conn.close()
                break
            except (sqlite3.Error, OSError) as e:
                logger.debug("Could not read workers from %s: %s", db_path, e)
                continue

    return counts


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
    """Get the current town configuration with template and worker info.

    Returns:
        Current town details including:
        - town: Full town configuration
        - name: Town name
        - template: Team template name (solo, pair, full)
        - workflow: List of workflow steps
        - worker_counts: Configured worker counts per role
        - active_workers: Currently running workers per role
    """
    manager = _get_town_manager()
    active_workers = _get_active_worker_counts(TOWN_NAME)

    try:
        town = manager.get(TOWN_NAME)
        return {
            "town": town.to_dict(),
            "name": TOWN_NAME,
            "template": town.template,
            "workflow": town.workflow,
            "worker_counts": town.get_effective_roles(),
            "active_workers": active_workers,
        }
    except TownNotFoundError:
        # Return default info if town not in database
        return {
            "town": None,
            "name": TOWN_NAME,
            "template": "pair",
            "workflow": ["dev", "qa", "human_merge"],
            "worker_counts": {"dev": 1, "qa": 1},
            "active_workers": active_workers,
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
