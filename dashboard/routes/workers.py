"""REST API endpoints for worker management.

This module provides API endpoints for managing workers through the MAB daemon,
including listing, spawning, stopping, and monitoring worker agents.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mab.rpc import DaemonNotRunningError, RPCClient, RPCError, get_default_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workers", tags=["workers"])


class WorkerSpawnRequest(BaseModel):
    """Request model for spawning a new worker."""

    role: str = Field(..., description="Worker role (dev, qa, reviewer, tech-lead, manager)")
    project_path: str = Field(..., description="Project path for the worker")
    auto_restart: bool = Field(default=True, description="Enable auto-restart on crash")


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


class HealthStatusResponse(BaseModel):
    """Response model for health status."""

    healthy_count: int = Field(..., description="Number of healthy workers")
    unhealthy_count: int = Field(..., description="Number of unhealthy workers")
    crashed_count: int = Field(..., description="Number of crashed workers")
    total_restarts: int = Field(..., description="Total restart count across all workers")
    health_check_interval: float = Field(..., description="Health check interval in seconds")
    max_restart_count: int = Field(..., description="Max allowed restarts before giving up")


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
        result = client.call("daemon.status")
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
        result = client.call("health.status")
        logger.debug("Health status: %s", result)
        return result
    except Exception as e:
        _handle_rpc_error(e, "get_health_status")
        raise


@router.get("", response_model=WorkerListResponse)
async def list_workers(
    status: str | None = None,
    project_path: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """List all workers with optional filtering.

    Args:
        status: Filter by worker status (running, stopped, crashed)
        project_path: Filter by project path
        role: Filter by worker role
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

        result = client.call("worker.list", params)
        workers = result.get("workers", [])
        logger.debug("Listed %d workers", len(workers))
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
        result = client.call("worker.get", {"worker_id": worker_id})
        worker = result.get("worker")
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
        result = client.call(
            "worker.spawn",
            {
                "role": request.role,
                "project_path": request.project_path,
                "auto_restart": request.auto_restart,
            },
            timeout=60.0,  # Spawning can take time
        )
        logger.info(
            "Spawned worker: %s (role=%s, project=%s)",
            result.get("worker_id"),
            request.role,
            request.project_path,
        )
        return result
    except Exception as e:
        _handle_rpc_error(e, f"spawn_worker({request.role})")
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
        result = client.call(
            "worker.stop",
            {
                "worker_id": worker_id,
                "graceful": graceful,
                "timeout": timeout,
            },
            timeout=timeout + 5.0,  # Allow time for operation plus buffer
        )
        worker = result.get("worker", {})
        logger.info("Stopped worker: %s", worker_id)
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
        get_result = client.call("worker.get", {"worker_id": worker_id})
        worker = get_result.get("worker")
        if not worker:
            raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")

        # Stop the worker
        client.call(
            "worker.stop",
            {"worker_id": worker_id, "graceful": graceful, "timeout": 30.0},
            timeout=35.0,
        )

        # Spawn a new worker with same config
        result = client.call(
            "worker.spawn",
            {
                "role": worker["role"],
                "project_path": worker["project_path"],
                "auto_restart": worker.get("auto_restart", True),
            },
            timeout=60.0,
        )
        logger.info("Restarted worker: %s -> %s", worker_id, result.get("worker_id"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        _handle_rpc_error(e, f"restart_worker({worker_id})")
        raise
