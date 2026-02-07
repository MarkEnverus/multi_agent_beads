"""REST API endpoints for worker management.

This module provides API endpoints for managing workers through the MAB daemon,
including listing, spawning, stopping, monitoring, and log streaming for worker agents.

All RPC calls are wrapped with asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dashboard.config import PROJECT_ROOT
from dashboard.routes.ws import broadcast_worker_spawned, broadcast_worker_stopped
from mab.daemon import (
    MAB_HOME,
    Daemon,
    DaemonAlreadyRunningError,
)
from mab.daemon import (
    DaemonNotRunningError as DaemonNotRunning,
)
from mab.db import get_db
from mab.rpc import DaemonNotRunningError, RPCClient, RPCError, get_default_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workers", tags=["workers"])


class WorkerSpawnRequest(BaseModel):
    """Request model for spawning a new worker."""

    role: str = Field(..., description="Worker role (dev, qa, reviewer, tech-lead, manager)")
    project_path: str = Field(..., description="Project path for the worker")
    auto_restart: bool = Field(default=True, description="Enable auto-restart on crash")


class WorkerAddRequest(BaseModel):
    """Simplified request model for adding workers via the UI."""

    role: str = Field(..., description="Worker role (dev, qa, reviewer, tech_lead, manager)")
    count: int = Field(default=1, ge=1, le=5, description="Number of workers to spawn")


class WorkerStopRequest(BaseModel):
    """Request model for stopping a worker."""

    graceful: bool = Field(default=True, description="Wait for current work to complete")
    timeout: float = Field(default=30.0, description="Timeout in seconds for graceful shutdown")


class WorkerResponse(BaseModel):
    """Response model for a single worker."""

    id: str = Field(..., description="Worker unique identifier")
    pid: int | None = Field(None, description="Process ID")
    status: str = Field(..., description="Worker status (running, stopped, crashed)")
    role: str = Field(..., description="Worker role")
    project_path: str = Field(..., description="Project directory")
    started_at: str | None = Field(None, description="ISO timestamp of start time")
    crash_count: int = Field(default=0, description="Number of crashes")


class WorkerListResponse(BaseModel):
    """Response model for listing workers."""

    workers: list[WorkerResponse] = Field(default_factory=list, description="List of workers")
    total: int = Field(..., description="Total number of workers")


class DaemonStatusResponse(BaseModel):
    """Response model for daemon status."""

    state: str = Field(..., description="Daemon state (running, stopped, etc)")
    pid: int | None = Field(None, description="Daemon process ID")
    uptime_seconds: float | None = Field(None, description="Daemon uptime in seconds")
    started_at: str | None = Field(None, description="ISO timestamp of daemon start")
    workers_count: int = Field(default=0, description="Number of running workers")


class HealthConfigResponse(BaseModel):
    """Response model for health configuration."""

    health_check_interval_seconds: float = Field(
        ..., description="Health check interval in seconds"
    )
    heartbeat_timeout_seconds: float = Field(..., description="Heartbeat timeout in seconds")
    max_restart_count: int = Field(..., description="Max allowed restarts before giving up")
    restart_backoff_base_seconds: float = Field(
        ..., description="Base restart backoff delay in seconds"
    )
    restart_backoff_max_seconds: float = Field(
        ..., description="Maximum restart backoff delay in seconds"
    )
    auto_restart_enabled: bool = Field(..., description="Whether auto-restart is enabled")


class HealthStatusResponse(BaseModel):
    """Response model for health status."""

    healthy_workers: int = Field(..., description="Number of healthy workers")
    unhealthy_workers: int = Field(..., description="Number of unhealthy workers")
    crashed_workers: int = Field(..., description="Number of crashed workers")
    total_restarts: int = Field(..., description="Total restart count across all workers")
    workers_at_max_restarts: int = Field(..., description="Workers that have hit max restart limit")
    config: HealthConfigResponse = Field(..., description="Health configuration")


# Shorter timeout for dashboard operations to prevent long waits
DASHBOARD_RPC_TIMEOUT = 5.0


def _get_worker_log_file(project_path: str, worker_id: str) -> Path | None:
    """Get the log file path for a worker from mab.db.

    Args:
        project_path: Path to the project directory.
        worker_id: Worker unique identifier.

    Returns:
        Path to the worker's log file, or None if not found.
    """
    try:
        conn = get_db(project_path)
        cursor = conn.execute(
            "SELECT log_file FROM workers WHERE id = ?",
            (worker_id,),
        )
        row = cursor.fetchone()
        conn.close()

        if row and row["log_file"]:
            return Path(row["log_file"])
        return None
    except Exception as e:
        logger.warning("Failed to get log file for worker %s: %s", worker_id, e)
        return None


def _get_worker_from_db(project_path: str, worker_id: str) -> dict[str, Any] | None:
    """Get worker info from mab.db database.

    This is used as a fallback when the daemon doesn't have the worker in memory
    (e.g., stopped/historical workers).

    Args:
        project_path: Path to the project directory.
        worker_id: Worker unique identifier.

    Returns:
        Dictionary with worker info (project_path, log_file), or None if not found.
    """
    try:
        conn = get_db(project_path)
        cursor = conn.execute(
            "SELECT id, project_path, log_file FROM workers WHERE id = ?",
            (worker_id,),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "id": row["id"],
                "project_path": row["project_path"],
                "log_file": row["log_file"],
            }
        return None
    except Exception as e:
        logger.warning("Failed to get worker %s from database: %s", worker_id, e)
        return None


_CLAIM_PATTERN = re.compile(r"CLAIM:\s*(\S+)\s*-\s*(.+)$")


def _parse_claim_from_log(log_file: str | None) -> tuple[str | None, str | None]:
    """Parse the most recent CLAIM line from a worker's log file.

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
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        for line in reversed(lines):
            match = _CLAIM_PATTERN.search(line)
            if match:
                return match.group(1), match.group(2).strip()

        return None, None
    except (OSError, IOError):
        return None, None


def _get_current_bead_for_worker(
    worker_id: str, log_file: str | None = None
) -> tuple[str | None, str | None]:
    """Get the current bead for a worker.

    First tries the worker_events table in mab.db.
    Falls back to parsing the worker's log file for CLAIM entries.

    Args:
        worker_id: The worker's unique identifier.
        log_file: Optional path to the worker's log file for fallback parsing.

    Returns:
        Tuple of (bead_id, bead_title), or (None, None) if no claim found.
    """
    db_path = PROJECT_ROOT / ".mab" / "mab.db"
    if not db_path.exists():
        db_path = Path.home() / ".mab" / "mab.db"

    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            has_events = (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_events'"
                ).fetchone()
                is not None
            )

            if has_events:
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

    return _parse_claim_from_log(log_file)


def _enrich_workers_with_bead_info(workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add current_bead and current_bead_title to each worker dict.

    For each worker, checks the spawn-time bead_id first, then looks up
    claimed beads from worker_events or log files.
    """
    for worker in workers:
        worker_id = worker.get("id", "")
        bead_id = worker.get("bead_id")
        bead_title = None

        if not bead_id:
            log_file = None
            project_path = worker.get("project_path")
            if project_path:
                log_file_path = _get_worker_log_file(project_path, worker_id)
                if log_file_path:
                    log_file = str(log_file_path)

            bead_id, bead_title = _get_current_bead_for_worker(worker_id, log_file)

        worker["current_bead"] = bead_id
        worker["current_bead_title"] = bead_title

    return workers


def _get_rpc_client() -> RPCClient:
    """Get RPC client for daemon communication."""
    return get_default_client()


def _handle_rpc_error(e: Exception, operation: str) -> None:
    """Handle RPC errors with appropriate HTTP responses."""
    if isinstance(e, DaemonNotRunningError):
        logger.warning("Daemon not running for %s: %s", operation, e)
        raise HTTPException(
            status_code=503,
            detail="MAB daemon is not running. Start it with 'mab start -d'",
        )
    elif isinstance(e, RPCError):
        logger.error("RPC error during %s: %s", operation, e)
        raise HTTPException(
            status_code=500,
            detail=f"RPC error: {e.message}",
        )
    else:
        logger.exception("Unexpected error during %s: %s", operation, e)
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}",
        )


@router.get("/daemon/status", response_model=DaemonStatusResponse)
async def get_daemon_status() -> dict[str, Any]:
    """Get current daemon status.

    Returns the daemon's current state, PID, uptime, and worker count.
    """
    try:
        client = _get_rpc_client()
        # Run blocking RPC call in thread pool to avoid blocking event loop
        result: dict[str, Any] = await asyncio.to_thread(
            client.call, "daemon.status", None, DASHBOARD_RPC_TIMEOUT
        )
        logger.debug("Daemon status: %s", result)
        return result
    except Exception as e:
        _handle_rpc_error(e, "get_daemon_status")
        raise  # Make type checker happy


@router.get("/health", response_model=HealthStatusResponse)
async def get_health_status() -> dict[str, Any]:
    """Get worker health status.

    Returns aggregate health information including healthy/unhealthy counts
    and restart statistics.
    """
    try:
        client = _get_rpc_client()
        # Run blocking RPC call in thread pool to avoid blocking event loop
        result: dict[str, Any] = await asyncio.to_thread(
            client.call, "health.status", None, DASHBOARD_RPC_TIMEOUT
        )
        logger.debug("Health status: %s", result)
        return result
    except Exception as e:
        _handle_rpc_error(e, "get_health_status")
        raise


class DispatchStatusResponse(BaseModel):
    """Response model for dispatch loop status."""

    enabled: bool = Field(..., description="Whether dispatch loop is active")
    project_path: str | None = Field(None, description="Project being monitored")
    roles: list[str] = Field(default_factory=list, description="Roles being dispatched")
    interval_seconds: float = Field(..., description="Poll interval in seconds")
    task_running: bool = Field(..., description="Whether the async dispatch task is running")


class DispatchStartRequest(BaseModel):
    """Request model for starting the dispatch loop."""

    project_path: str = Field(..., description="Project path to monitor for work")
    roles: list[str] | None = Field(None, description="Roles to dispatch (all if omitted)")
    interval_seconds: float = Field(default=5.0, description="Poll interval in seconds")


@router.get("/dispatch/status", response_model=DispatchStatusResponse)
async def get_dispatch_status() -> dict[str, Any]:
    """Get current dispatch loop status.

    Returns whether the dispatch loop is active, which project and roles
    it monitors, and the polling interval.
    """
    try:
        client = _get_rpc_client()
        result: dict[str, Any] = await asyncio.to_thread(
            client.call, "dispatch.status", None, DASHBOARD_RPC_TIMEOUT
        )
        logger.debug("Dispatch status: %s", result)
        return result
    except Exception as e:
        _handle_rpc_error(e, "get_dispatch_status")
        raise


@router.post("/dispatch/start")
async def start_dispatch(request: DispatchStartRequest) -> dict[str, Any]:
    """Start the dispatch loop.

    Begins polling for available work and spawning workers as needed.
    """
    try:
        client = _get_rpc_client()
        params: dict[str, Any] = {
            "project_path": request.project_path,
            "interval_seconds": request.interval_seconds,
        }
        if request.roles is not None:
            params["roles"] = request.roles

        result: dict[str, Any] = await asyncio.to_thread(
            client.call, "dispatch.start", params, DASHBOARD_RPC_TIMEOUT
        )
        logger.info("Dispatch started: %s", result)
        return result
    except Exception as e:
        _handle_rpc_error(e, "start_dispatch")
        raise


@router.post("/dispatch/stop")
async def stop_dispatch() -> dict[str, Any]:
    """Stop the dispatch loop.

    Stops polling for work. Running workers are not affected.
    """
    try:
        client = _get_rpc_client()
        result: dict[str, Any] = await asyncio.to_thread(
            client.call, "dispatch.stop", None, DASHBOARD_RPC_TIMEOUT
        )
        logger.info("Dispatch stopped")
        return result
    except Exception as e:
        _handle_rpc_error(e, "stop_dispatch")
        raise


class DaemonStartRequest(BaseModel):
    """Request model for starting the daemon."""

    foreground: bool = Field(default=False, description="Run in foreground mode")


class DaemonStopRequest(BaseModel):
    """Request model for stopping the daemon."""

    graceful: bool = Field(default=True, description="Wait for workers to complete")
    timeout: float = Field(default=60.0, description="Timeout in seconds")


class ProjectInitRequest(BaseModel):
    """Request model for initializing a project."""

    project_path: str = Field(..., description="Path to project directory")
    template: str = Field(
        default="default",
        description="Config template (default, minimal, full)",
    )
    force: bool = Field(default=False, description="Overwrite existing config")


class ProjectInitResponse(BaseModel):
    """Response model for project initialization."""

    success: bool = Field(..., description="Whether initialization succeeded")
    project_path: str = Field(..., description="Path to initialized project")
    mab_dir: str = Field(..., description="Path to .mab directory")
    message: str = Field(..., description="Status message")


def _get_daemon() -> Daemon:
    """Get a Daemon instance for the current project."""
    return Daemon(mab_dir=MAB_HOME)


@router.post("/daemon/start")
async def start_daemon(request: DaemonStartRequest | None = None) -> dict[str, Any]:
    """Start the MAB daemon.

    Starts the daemon in background mode. The daemon process will fork
    and this endpoint returns immediately after the fork succeeds.
    """
    daemon = _get_daemon()
    foreground = request.foreground if request else False

    # Check if already running
    if daemon.is_running():
        status = daemon.get_status()
        return {
            "success": False,
            "message": f"Daemon already running (PID {status.pid})",
            "already_running": True,
            "status": status.to_dict(),
        }

    try:
        # Run daemon start in thread pool since it involves I/O
        # Note: start() with foreground=False will fork - parent exits with SystemExit(0)
        await asyncio.to_thread(daemon.start, foreground)

        # Give the daemon a moment to fully initialize
        await asyncio.sleep(0.5)

        # Get updated status
        status = daemon.get_status()
        logger.info("Daemon started (PID %s)", status.pid)

        return {
            "success": True,
            "message": "Daemon started successfully",
            "status": status.to_dict(),
        }
    except DaemonAlreadyRunningError as e:
        logger.warning("Daemon already running: %s", e)
        return {
            "success": False,
            "message": str(e),
            "already_running": True,
        }
    except SystemExit as e:
        # When daemon.start() forks with foreground=False, the parent process
        # calls sys.exit(0) which raises SystemExit. This is expected behavior
        # indicating the daemon forked successfully.
        if e.code == 0:
            # Give the daemon a moment to initialize
            await asyncio.sleep(0.5)

            # Get updated status
            status = daemon.get_status()
            logger.info("Daemon forked successfully (PID %s)", status.pid)

            return {
                "success": True,
                "message": "Daemon started successfully",
                "status": status.to_dict(),
            }
        else:
            logger.exception("Daemon exited with code %s", e.code)
            raise HTTPException(
                status_code=500,
                detail=f"Daemon exited with code {e.code}",
            )
    except Exception as e:
        logger.exception("Failed to start daemon: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start daemon: {e}",
        )


@router.post("/daemon/stop")
async def stop_daemon(request: DaemonStopRequest | None = None) -> dict[str, Any]:
    """Stop the MAB daemon.

    Gracefully stops the daemon and all running workers.
    """
    daemon = _get_daemon()
    graceful = request.graceful if request else True
    timeout = request.timeout if request else 60.0

    # Check if running
    if not daemon.is_running():
        return {
            "success": False,
            "message": "Daemon is not running",
            "already_stopped": True,
        }

    try:
        # Run stop in thread pool since it involves I/O and waiting
        await asyncio.to_thread(daemon.stop, graceful, timeout)
        logger.info("Daemon stopped (graceful=%s)", graceful)

        return {
            "success": True,
            "message": "Daemon stopped successfully",
        }
    except DaemonNotRunning:
        return {
            "success": False,
            "message": "Daemon is not running",
            "already_stopped": True,
        }
    except Exception as e:
        logger.exception("Failed to stop daemon: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop daemon: {e}",
        )


@router.post("/daemon/restart")
async def restart_daemon() -> dict[str, Any]:
    """Restart the MAB daemon.

    Stops the daemon if running, then starts it again.
    """
    daemon = _get_daemon()

    try:
        # Check current state
        was_running = daemon.is_running()

        if was_running:
            # Stop first
            await asyncio.to_thread(daemon.stop, True, 60.0)
            logger.info("Daemon stopped for restart")

        # Give time for cleanup
        await asyncio.sleep(0.5)

        # Start daemon - will fork, causing SystemExit(0) in parent
        await asyncio.to_thread(daemon.start, False)

        # Give the daemon a moment to initialize
        await asyncio.sleep(0.5)

        status = daemon.get_status()
        logger.info("Daemon restarted (PID %s)", status.pid)

        return {
            "success": True,
            "message": "Daemon restarted successfully",
            "was_running": was_running,
            "status": status.to_dict(),
        }
    except SystemExit as e:
        # When daemon.start() forks, the parent process calls sys.exit(0)
        # which raises SystemExit. This is expected behavior.
        if e.code == 0:
            # Give the daemon a moment to initialize
            await asyncio.sleep(0.5)

            status = daemon.get_status()
            logger.info("Daemon restarted via fork (PID %s)", status.pid)

            return {
                "success": True,
                "message": "Daemon restarted successfully",
                "was_running": daemon.is_running(),  # Refresh state
                "status": status.to_dict(),
            }
        else:
            logger.exception("Daemon exited with code %s during restart", e.code)
            raise HTTPException(
                status_code=500,
                detail=f"Daemon exited with code {e.code}",
            )
    except Exception as e:
        logger.exception("Failed to restart daemon: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to restart daemon: {e}",
        )


@router.post("/project/init", response_model=ProjectInitResponse)
async def init_project(request: ProjectInitRequest) -> dict[str, Any]:
    """Initialize a MAB project.

    Creates the .mab directory structure and configuration files
    for the specified project path.
    """
    project_path = Path(request.project_path).resolve()

    # Validate project path exists
    if not project_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Project path does not exist: {project_path}",
        )

    if not project_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Project path is not a directory: {project_path}",
        )

    mab_dir = project_path / ".mab"
    config_file = mab_dir / "config.yaml"
    logs_dir = mab_dir / "logs"
    heartbeat_dir = mab_dir / "heartbeat"

    # Check if already initialized
    if mab_dir.exists() and config_file.exists() and not request.force:
        return {
            "success": False,
            "project_path": str(project_path),
            "mab_dir": str(mab_dir),
            "message": "Project already initialized. Use force=true to reinitialize.",
        }

    def do_init() -> dict[str, Any]:
        """Perform the initialization (runs in thread pool)."""
        # Create directory structure
        mab_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(exist_ok=True)
        heartbeat_dir.mkdir(exist_ok=True)

        # Generate config
        project_name = project_path.name
        has_beads = (project_path / ".beads").exists()

        if request.template == "minimal":
            config_content = f'''# MAB Configuration File (Minimal)
project:
  name: "{project_name}"

workers:
  max_workers: 2
'''
        elif request.template == "full":
            config_content = f'''# MAB Configuration File (Full)
project:
  name: "{project_name}"
  description: ""
  issue_prefix: ""

workers:
  max_workers: 5
  default_roles:
    - dev
    - qa
    - reviewer
  restart_policy: always
  heartbeat_interval: 30
  max_failures: 3

roles:
  dev:
    labels: [dev, feature, bug]
    max_priority: 3
  qa:
    labels: [qa, test]
    max_priority: 2
  reviewer:
    labels: [review]
    max_priority: 2

beads:
  enabled: {str(has_beads).lower()}
  path: ".beads"

logging:
  level: info
  retention_days: 7
'''
        else:
            config_content = f'''# MAB Configuration File
project:
  name: "{project_name}"
  description: ""

workers:
  max_workers: 3
  default_roles:
    - dev
    - qa
'''

        if has_beads:
            config_content += "\n# Note: Existing beads setup detected at .beads/\n"

        config_file.write_text(config_content)

        # Create .gitignore
        gitignore_file = mab_dir / ".gitignore"
        gitignore_content = """# MAB local files
!config.yaml
logs/
*.log
heartbeat/
*.pid
*.lock
*.sock
"""
        gitignore_file.write_text(gitignore_content)

        return {
            "success": True,
            "project_path": str(project_path),
            "mab_dir": str(mab_dir),
            "message": f"Initialized MAB project at {mab_dir}",
        }

    try:
        result = await asyncio.to_thread(do_init)
        logger.info("Initialized project at %s", project_path)
        return result
    except Exception as e:
        logger.exception("Failed to initialize project: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize project: {e}",
        )


@router.get("/project/status")
async def get_project_status(project_path: str) -> dict[str, Any]:
    """Get initialization status for a project.

    Returns whether the project has a .mab configuration.
    """
    path = Path(project_path).resolve()

    if not path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Project path does not exist: {path}",
        )

    mab_dir = path / ".mab"
    config_file = mab_dir / "config.yaml"
    beads_dir = path / ".beads"

    return {
        "project_path": str(path),
        "initialized": mab_dir.exists() and config_file.exists(),
        "has_beads": beads_dir.exists(),
        "mab_dir": str(mab_dir) if mab_dir.exists() else None,
    }


def _is_worker_recent(worker: dict[str, Any], max_age_hours: float = 1.0) -> bool:
    """Check if a worker is recent (active or stopped within max_age_hours).

    Args:
        worker: Worker dict from RPC response.
        max_age_hours: Maximum age in hours for stopped workers to be considered recent.

    Returns:
        True if worker is running/spawning/starting or stopped within max_age_hours.
    """
    from datetime import datetime, timedelta

    status = worker.get("status", "")

    # Always show active workers
    if status in ("running", "spawning", "starting"):
        return True

    # For stopped/crashed workers, check how recently they stopped
    stopped_at = worker.get("stopped_at")
    if not stopped_at:
        # No stop time recorded - use started_at as fallback
        stopped_at = worker.get("started_at")

    if not stopped_at:
        return False

    try:
        # Parse timestamp (handle both 'T' and space separators)
        stopped_at = stopped_at.replace("T", " ").rstrip("Z")
        if "." in stopped_at:
            stopped_time = datetime.strptime(stopped_at, "%Y-%m-%d %H:%M:%S.%f")
        else:
            stopped_time = datetime.strptime(stopped_at, "%Y-%m-%d %H:%M:%S")

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        return stopped_time > cutoff
    except (ValueError, TypeError):
        return False


@router.get("", response_model=WorkerListResponse)
async def list_workers(
    status: str | None = None,
    project_path: str | None = None,
    role: str | None = None,
    active_only: bool = Query(True, description="Only show active or recently stopped workers"),
    max_age_hours: float = Query(
        1.0, description="Max age in hours for stopped workers (when active_only=True)"
    ),
) -> dict[str, Any]:
    """List all workers with optional filtering.

    Args:
        status: Filter by worker status (running, stopped, crashed)
        project_path: Filter by project path
        role: Filter by worker role
        active_only: Only show active or recently stopped workers (default True)
        max_age_hours: Max age in hours for stopped workers when active_only=True
    """
    try:
        client = _get_rpc_client()
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if project_path:
            params["project_path"] = project_path
        if role:
            params["role"] = role

        # Run blocking RPC call in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(client.call, "worker.list", params, DASHBOARD_RPC_TIMEOUT)
        workers = result.get("workers", [])

        # Filter to only recent workers unless active_only=False
        if active_only:
            workers = [w for w in workers if _is_worker_recent(w, max_age_hours)]

        # Enrich with bead info (current_bead, current_bead_title)
        workers = await asyncio.to_thread(_enrich_workers_with_bead_info, workers)

        logger.debug("Listed %d workers (active_only=%s)", len(workers), active_only)
        return {"workers": workers, "total": len(workers)}
    except Exception as e:
        _handle_rpc_error(e, "list_workers")
        raise


@router.get("/{worker_id}", response_model=WorkerResponse)
async def get_worker(worker_id: str) -> dict[str, Any]:
    """Get details of a specific worker.

    Args:
        worker_id: Worker unique identifier
    """
    try:
        client = _get_rpc_client()
        # Run blocking RPC call in thread pool to avoid blocking event loop
        result: dict[str, Any] = await asyncio.to_thread(
            client.call, "worker.get", {"worker_id": worker_id}, DASHBOARD_RPC_TIMEOUT
        )
        worker: dict[str, Any] | None = result.get("worker")
        if not worker:
            raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")
        logger.debug("Got worker: %s", worker_id)
        return worker
    except HTTPException:
        raise
    except Exception as e:
        _handle_rpc_error(e, f"get_worker({worker_id})")
        raise


@router.post("", response_model=WorkerResponse)
async def spawn_worker(request: WorkerSpawnRequest) -> dict[str, Any]:
    """Spawn a new worker.

    Creates a new worker with the specified role for the given project.
    The worker will be managed by the daemon and auto-restarted on crash
    if enabled.
    """
    try:
        client = _get_rpc_client()
        # Run blocking RPC call in thread pool to avoid blocking event loop
        # Use longer timeout for spawn operations
        result: dict[str, Any] = await asyncio.to_thread(
            client.call,
            "worker.spawn",
            {
                "role": request.role,
                "project_path": request.project_path,
                "auto_restart": request.auto_restart,
            },
            60.0,  # Spawning can take time
        )
        logger.info(
            "Spawned worker: %s (role=%s, project=%s)",
            result.get("worker_id"),
            request.role,
            request.project_path,
        )
        # Transform worker_id to id for WorkerResponse model compatibility
        if "worker_id" in result and "id" not in result:
            result["id"] = result.pop("worker_id")
        # Broadcast to WebSocket clients
        await broadcast_worker_spawned({"role": request.role, **result})
        return result
    except Exception as e:
        _handle_rpc_error(e, f"spawn_worker({request.role})")
        raise


class WorkerAddResponse(BaseModel):
    """Response model for the simplified add workers endpoint."""

    success: bool = Field(..., description="Whether all workers were spawned successfully")
    spawned: int = Field(..., description="Number of workers successfully spawned")
    workers: list[WorkerResponse] = Field(
        default_factory=list, description="List of spawned workers"
    )
    errors: list[str] = Field(default_factory=list, description="Error messages for failed spawns")


@router.post("/add", response_model=WorkerAddResponse)
async def add_workers(request: WorkerAddRequest) -> dict[str, Any]:
    """Add workers with a simplified API for the UI.

    This endpoint uses the current project path automatically and allows
    spawning multiple workers of the same role at once.

    Args:
        request: WorkerAddRequest with role and count

    Returns:
        WorkerAddResponse with success status and spawned workers.
    """
    project_path = str(PROJECT_ROOT)
    workers_spawned: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        client = _get_rpc_client()

        for i in range(request.count):
            try:
                result: dict[str, Any] = await asyncio.to_thread(
                    client.call,
                    "worker.spawn",
                    {
                        "role": request.role,
                        "project_path": project_path,
                        "auto_restart": True,
                    },
                    60.0,
                )
                # Transform worker_id to id for response model compatibility
                if "worker_id" in result and "id" not in result:
                    result["id"] = result.pop("worker_id")
                workers_spawned.append(result)
                logger.info(
                    "Added worker %d/%d: %s (role=%s)",
                    i + 1,
                    request.count,
                    result.get("id"),
                    request.role,
                )
                # Broadcast to WebSocket clients
                await broadcast_worker_spawned({"role": request.role, **result})
            except Exception as e:
                error_msg = f"Failed to spawn worker {i + 1}: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)

        return {
            "success": len(errors) == 0,
            "spawned": len(workers_spawned),
            "workers": workers_spawned,
            "errors": errors,
        }
    except Exception as e:
        _handle_rpc_error(e, f"add_workers({request.role}, count={request.count})")
        raise


@router.delete("/{worker_id}", response_model=WorkerResponse)
async def stop_worker(
    worker_id: str,
    graceful: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Stop a running worker.

    Args:
        worker_id: Worker unique identifier
        graceful: If True, wait for current work to complete
        timeout: Timeout in seconds for graceful shutdown
    """
    try:
        client = _get_rpc_client()
        # Run blocking RPC call in thread pool to avoid blocking event loop
        result: dict[str, Any] = await asyncio.to_thread(
            client.call,
            "worker.stop",
            {
                "worker_id": worker_id,
                "graceful": graceful,
                "timeout": timeout,
            },
            timeout + 5.0,  # Allow time for operation plus buffer
        )
        worker: dict[str, Any] = result.get("worker", {})
        logger.info("Stopped worker: %s", worker_id)
        # Broadcast to WebSocket clients
        await broadcast_worker_stopped(worker_id, reason="stopped via API")
        return worker
    except Exception as e:
        _handle_rpc_error(e, f"stop_worker({worker_id})")
        raise


@router.post("/{worker_id}/restart", response_model=WorkerResponse)
async def restart_worker(
    worker_id: str,
    graceful: bool = True,
) -> dict[str, Any]:
    """Restart a worker.

    Stops the worker and spawns a new one with the same configuration.

    Args:
        worker_id: Worker unique identifier
        graceful: If True, wait for current work to complete before stopping
    """
    try:
        client = _get_rpc_client()

        # First get the worker details
        # Run blocking RPC call in thread pool to avoid blocking event loop
        get_result: dict[str, Any] = await asyncio.to_thread(
            client.call, "worker.get", {"worker_id": worker_id}, DASHBOARD_RPC_TIMEOUT
        )
        worker: dict[str, Any] | None = get_result.get("worker")
        if not worker:
            raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")

        # Stop the worker
        await asyncio.to_thread(
            client.call,
            "worker.stop",
            {"worker_id": worker_id, "graceful": graceful, "timeout": 30.0},
            35.0,
        )

        # Spawn a new worker with same config
        result: dict[str, Any] = await asyncio.to_thread(
            client.call,
            "worker.spawn",
            {
                "role": worker["role"],
                "project_path": worker["project_path"],
                "auto_restart": worker.get("auto_restart", True),
            },
            60.0,
        )
        logger.info("Restarted worker: %s -> %s", worker_id, result.get("worker_id"))
        # Transform worker_id to id for WorkerResponse model compatibility
        if "worker_id" in result and "id" not in result:
            result["id"] = result.pop("worker_id")
        return result
    except HTTPException:
        raise
    except Exception as e:
        _handle_rpc_error(e, f"restart_worker({worker_id})")
        raise


# Log streaming functionality

# Log line pattern: [TIMESTAMP] [IDENTIFIER] EVENT_TYPE: details
# IDENTIFIER can be either a PID (digits only) or a worker_id (alphanumeric with hyphens)
_LOG_PATTERN = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[([\w-]+)\] (.+)")

# SSE retry interval in milliseconds
_SSE_RETRY_MS = 3000


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a single log line into a structured dict.

    Args:
        line: Raw log line to parse.

    Returns:
        Parsed log entry dict, or None if line is invalid.
        Contains 'worker_id' if identifier is alphanumeric, 'pid' if numeric.
    """
    line = line.strip()
    if not line:
        return None

    match = _LOG_PATTERN.match(line)
    if not match:
        return None

    timestamp_str, identifier, content = match.groups()

    # Parse event type and message
    if ":" in content:
        event, message = content.split(":", 1)
        event = event.strip()
        message = message.strip()
    else:
        event = content.strip()
        message = None

    result: dict[str, Any] = {
        "timestamp": timestamp_str,
        "event": event,
        "message": message,
        "raw": line,
    }

    # Determine if identifier is a PID (all digits) or worker_id
    if identifier.isdigit():
        result["pid"] = int(identifier)
    else:
        result["worker_id"] = identifier

    return result


def _format_sse_event(data: dict[str, Any], event_type: str = "message") -> str:
    """Format data as a Server-Sent Event."""
    json_data = json.dumps(data)
    if event_type == "message":
        return f"data: {json_data}\n\n"
    return f"event: {event_type}\ndata: {json_data}\n\n"


async def _stream_worker_logs(
    worker_id: str,
    log_path: Path,
    tail_lines: int = 50,
    filter_by_worker_id: bool = False,
) -> AsyncIterator[str]:
    """Stream log entries from a log file via SSE.

    When streaming from a per-worker log file (default), no filtering is needed
    since the entire file belongs to one worker.

    When streaming from a shared log file (legacy mode), set filter_by_worker_id=True
    to filter entries by worker_id.

    Args:
        worker_id: The worker ID (used for logging and optional filtering).
        log_path: Path to the log file.
        tail_lines: Number of recent lines to include initially.
        filter_by_worker_id: If True, filter log entries by worker_id.

    Yields:
        SSE-formatted event strings.
    """
    last_position = 0
    last_inode: int | None = None

    # Send initial retry interval
    yield f"retry: {_SSE_RETRY_MS}\n\n"

    # Read initial tail lines
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
            # Get last N lines
            recent_lines = lines[-tail_lines:] if len(lines) > tail_lines else lines

            for line in recent_lines:
                entry = _parse_log_line(line)
                if entry:
                    # For per-worker files, include all entries; for shared files, filter
                    if not filter_by_worker_id or entry.get("worker_id") == worker_id:
                        yield _format_sse_event(entry)

            stat = log_path.stat()
            last_position = stat.st_size
            last_inode = stat.st_ino
        except OSError as e:
            logger.warning("Could not read initial log content: %s", e)
            yield _format_sse_event(
                {"type": "warning", "message": f"Could not read log file: {e}"},
                event_type="error",
            )

    # Stream new entries
    while True:
        try:
            if not log_path.exists():
                await asyncio.sleep(0.5)
                continue

            current_stat = log_path.stat()
            current_inode = current_stat.st_ino
            current_size = current_stat.st_size

            # Check for log rotation
            if last_inode is not None and (
                current_inode != last_inode or current_size < last_position
            ):
                last_position = 0
                last_inode = current_inode
                yield _format_sse_event(
                    {"type": "rotation", "message": "Log file rotated"},
                    event_type="info",
                )

            # Read new content
            if current_size > last_position:
                with open(log_path, encoding="utf-8") as f:
                    f.seek(last_position)
                    new_content = f.read()
                    last_position = f.tell()

                for line in new_content.splitlines():
                    entry = _parse_log_line(line)
                    if entry:
                        # For per-worker files, include all entries; for shared files, filter
                        if not filter_by_worker_id or entry.get("worker_id") == worker_id:
                            yield _format_sse_event(entry)

            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.debug("Worker log stream cancelled for worker %s", worker_id)
            break
        except Exception as e:
            logger.warning("Error streaming worker logs: %s", e)
            yield _format_sse_event(
                {"type": "error", "message": str(e)},
                event_type="error",
            )
            await asyncio.sleep(1.0)


@router.get("/{worker_id}/logs/stream")
async def stream_worker_logs(
    worker_id: str,
    tail: int = Query(50, ge=0, le=500, description="Number of recent lines to include"),
) -> StreamingResponse:
    """Stream live log entries for a specific worker via Server-Sent Events.

    Opens a persistent SSE connection that streams log entries from the
    worker's dedicated log file.

    Args:
        worker_id: Worker unique identifier
        tail: Number of recent log lines to include at start (default: 50)

    Returns:
        StreamingResponse with SSE content.

    Raises:
        HTTPException: If worker not found, log file not found, or daemon not running.
    """
    try:
        client = _get_rpc_client()
        # Run blocking RPC call in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(
            client.call, "worker.get", {"worker_id": worker_id}, DASHBOARD_RPC_TIMEOUT
        )
        worker = result.get("worker")

        if not worker:
            raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")

        project_path = worker.get("project_path")
        if not project_path:
            raise HTTPException(
                status_code=400,
                detail=f"Worker {worker_id} has no project_path",
            )

        # Look up log file from mab.db
        log_path = await asyncio.to_thread(_get_worker_log_file, project_path, worker_id)

        # Fall back to project claude.log if no per-worker log file (legacy workers)
        filter_by_worker_id = False
        if log_path is None or not log_path.exists():
            log_path = Path(project_path) / "claude.log"
            filter_by_worker_id = True  # Need to filter shared log file
            logger.debug(
                "No per-worker log file for %s, falling back to %s",
                worker_id,
                log_path,
            )

        if not log_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Log file not found for worker {worker_id}",
            )

        logger.info(
            "Starting log stream for worker %s from %s (filter=%s)",
            worker_id,
            log_path,
            filter_by_worker_id,
        )

        return StreamingResponse(
            _stream_worker_logs(
                worker_id, log_path, tail_lines=tail, filter_by_worker_id=filter_by_worker_id
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        _handle_rpc_error(e, f"stream_worker_logs({worker_id})")
        raise


class WorkerLogEntry(BaseModel):
    """Response model for a worker log entry."""

    timestamp: str = Field(..., description="Log entry timestamp")
    pid: int | None = Field(None, description="Process ID (for legacy logs)")
    worker_id: str | None = Field(None, description="Worker unique identifier")
    event: str = Field(..., description="Event type")
    message: str | None = Field(None, description="Event message/details")


class CleanupRequest(BaseModel):
    """Request model for cleaning up old workers."""

    all_non_running: bool = Field(
        default=False, description="Remove all non-running workers (stopped, crashed, failed)"
    )
    older_than_seconds: int | None = Field(
        default=None,
        description="Remove workers older than specified seconds (e.g., 604800 for 7 days)",
    )
    status: str | None = Field(
        default=None,
        description="Remove workers with specific status only (stopped/crashed/failed)",
    )


class CleanupResponse(BaseModel):
    """Response model for cleanup operation."""

    success: bool = Field(..., description="Whether the operation succeeded")
    removed_count: int = Field(..., description="Number of workers removed")
    dry_run: bool = Field(default=False, description="Whether this was a dry run")
    workers_found: list[dict[str, Any]] = Field(
        default_factory=list, description="Workers that were/would be removed"
    )


@router.get("/{worker_id}/logs/recent", response_model=list[WorkerLogEntry])
async def get_worker_recent_logs(
    worker_id: str,
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
) -> list[dict[str, Any]]:
    """Get recent log entries for a specific worker.

    Reads log entries from the worker's dedicated log file. Falls back to
    filtering from the shared claude.log for legacy workers without a
    per-worker log file.

    For historical/stopped workers not in daemon memory, falls back to
    database lookup to find the worker's log file.

    Args:
        worker_id: Worker unique identifier
        limit: Maximum number of entries to return

    Returns:
        List of log entries, most recent first.

    Raises:
        HTTPException: If worker not found in daemon or database.
    """
    project_path: str | None = None

    # Try to get worker from daemon first (for running workers)
    try:
        client = _get_rpc_client()
        result = await asyncio.to_thread(
            client.call, "worker.get", {"worker_id": worker_id}, DASHBOARD_RPC_TIMEOUT
        )
        worker = result.get("worker")
        if worker:
            project_path = worker.get("project_path")
    except (DaemonNotRunningError, RPCError) as e:
        # Daemon not running or RPC error - will try database fallback
        logger.debug("RPC lookup failed for worker %s, trying database: %s", worker_id, e)
    except Exception as e:
        # Unexpected error - log but try database fallback
        logger.warning("Unexpected error in RPC lookup for %s: %s", worker_id, e)

    # If not found in daemon, try database lookup (for historical/stopped workers)
    if not project_path:
        # Use PROJECT_ROOT as the default project path for database lookup
        db_worker = await asyncio.to_thread(_get_worker_from_db, str(PROJECT_ROOT), worker_id)
        if db_worker:
            project_path = db_worker.get("project_path")
            logger.debug(
                "Found worker %s in database with project_path: %s", worker_id, project_path
            )

    # If still not found, return 404
    if not project_path:
        raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")

    try:
        # Look up log file from mab.db
        log_path = await asyncio.to_thread(_get_worker_log_file, project_path, worker_id)

        # Fall back to project claude.log if no per-worker log file (legacy workers)
        filter_by_worker_id = False
        if log_path is None or not log_path.exists():
            log_path = Path(project_path) / "claude.log"
            filter_by_worker_id = True  # Need to filter shared log file

        if not log_path.exists():
            return []

        # Read logs (run file I/O in thread pool)
        def read_logs() -> list[dict[str, Any]]:
            entries = []
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    entry = _parse_log_line(line)
                    if entry:
                        # For per-worker files, include all entries; for shared files, filter
                        if not filter_by_worker_id or entry.get("worker_id") == worker_id:
                            entries.append(entry)
            except OSError as e:
                logger.warning("Failed to read log file %s: %s", log_path, e)
                raise
            return entries

        entries = await asyncio.to_thread(read_logs)

        # Return most recent first, limited
        entries.reverse()
        return entries[:limit]

    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read log file: {e}",
        )


@router.post("/cleanup", response_model=CleanupResponse)
async def cleanup_workers(
    request: CleanupRequest,
    dry_run: bool = Query(False, description="Preview what would be removed without deleting"),
) -> dict[str, Any]:
    """Clean up old workers from the database.

    Removes stopped, crashed, or failed workers. Running workers are never removed.

    Args:
        request: CleanupRequest with filtering options
        dry_run: If True, only preview what would be removed

    Returns:
        CleanupResponse with results of the operation.
    """
    from datetime import datetime, timedelta
    from pathlib import Path

    from mab.workers import WorkerDatabase, WorkerStatus

    # Validate options
    if not request.all_non_running and not request.older_than_seconds and not request.status:
        raise HTTPException(
            status_code=400,
            detail="Must specify all_non_running, older_than_seconds, or status",
        )

    # Determine which statuses to clean up
    if request.status:
        try:
            target_statuses = [WorkerStatus(request.status)]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {request.status}. Use stopped, crashed, or failed.",
            )
    elif request.all_non_running:
        target_statuses = [
            WorkerStatus.STOPPED,
            WorkerStatus.CRASHED,
            WorkerStatus.FAILED,
        ]
    else:
        # If only older_than_seconds specified, default to all non-running statuses
        target_statuses = [
            WorkerStatus.STOPPED,
            WorkerStatus.CRASHED,
            WorkerStatus.FAILED,
        ]

    # Calculate cutoff time
    cutoff_time = (
        datetime.now() - timedelta(seconds=request.older_than_seconds)
        if request.older_than_seconds
        else None
    )

    # Find database locations
    databases_to_check: list[Path] = []

    # Check project-specific database
    project_db = PROJECT_ROOT / ".mab" / "workers.db"
    if project_db.exists():
        databases_to_check.append(project_db)

    # Check global database
    global_db = MAB_HOME / "workers.db"
    if global_db.exists():
        databases_to_check.append(global_db)

    if not databases_to_check:
        return {
            "success": True,
            "removed_count": 0,
            "dry_run": dry_run,
            "workers_found": [],
        }

    # Collect workers to remove
    workers_to_remove: list[tuple[Path, str, dict[str, Any]]] = []

    def collect_workers() -> None:
        for db_path in databases_to_check:
            try:
                db = WorkerDatabase(db_path)
                for target_status in target_statuses:
                    workers = db.list_workers(status=target_status)
                    for worker in workers:
                        # Skip running workers (should never happen but be safe)
                        if worker.status == WorkerStatus.RUNNING:
                            continue

                        # Check age if older_than_seconds specified
                        if cutoff_time:
                            timestamp_str = worker.stopped_at or worker.created_at
                            try:
                                worker_time = datetime.fromisoformat(
                                    timestamp_str.replace("Z", "+00:00")
                                )
                                if worker_time.tzinfo:
                                    worker_time = worker_time.replace(tzinfo=None)
                                if worker_time > cutoff_time:
                                    continue  # Worker is too recent
                            except (ValueError, TypeError):
                                continue

                        workers_to_remove.append(
                            (
                                db_path,
                                worker.id,
                                {
                                    "id": worker.id,
                                    "role": worker.role,
                                    "status": worker.status.value,
                                    "stopped_at": worker.stopped_at,
                                    "created_at": worker.created_at,
                                },
                            )
                        )
            except Exception as e:
                logger.warning("Could not read database %s: %s", db_path, e)

    # Run collection in thread pool
    await asyncio.to_thread(collect_workers)

    if not workers_to_remove:
        return {
            "success": True,
            "removed_count": 0,
            "dry_run": dry_run,
            "workers_found": [],
        }

    # Extract worker info for response
    workers_found = [info for _, _, info in workers_to_remove]

    if dry_run:
        return {
            "success": True,
            "removed_count": 0,
            "dry_run": True,
            "workers_found": workers_found,
        }

    # Actually remove workers
    removed_count = 0
    errors = 0

    def do_cleanup() -> tuple[int, int]:
        nonlocal removed_count, errors
        for db_path, worker_id, _ in workers_to_remove:
            try:
                db = WorkerDatabase(db_path)
                if db.delete_worker(worker_id):
                    removed_count += 1
                else:
                    errors += 1
            except Exception as e:
                logger.error("Error removing %s: %s", worker_id, e)
                errors += 1
        return removed_count, errors

    removed_count, errors = await asyncio.to_thread(do_cleanup)

    if errors > 0:
        logger.warning("Cleanup completed with %d errors", errors)

    return {
        "success": errors == 0,
        "removed_count": removed_count,
        "dry_run": False,
        "workers_found": workers_found,
    }
