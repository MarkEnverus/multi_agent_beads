"""REST API endpoints for worker management.

This module provides API endpoints for managing workers through the MAB daemon,
including listing, spawning, stopping, monitoring, and log streaming for worker agents.
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dashboard.config import LOG_FILE
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


class HealthConfigResponse(BaseModel):
    """Response model for health configuration."""

    health_check_interval_seconds: float = Field(
        ..., description="Health check interval in seconds"
    )
    heartbeat_timeout_seconds: float = Field(
        ..., description="Heartbeat timeout in seconds"
    )
    max_restart_count: int = Field(
        ..., description="Max allowed restarts before giving up"
    )
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
    workers_at_max_restarts: int = Field(
        ..., description="Workers that have hit max restart limit"
    )
    config: HealthConfigResponse = Field(..., description="Health configuration")


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


# Log streaming functionality

# Log line pattern: [TIMESTAMP] [PID] EVENT_TYPE: details
_LOG_PATTERN = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\d+)\] (.+)")

# SSE retry interval in milliseconds
_SSE_RETRY_MS = 3000


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a single log line into a structured dict.

    Args:
        line: Raw log line to parse.

    Returns:
        Parsed log entry dict, or None if line is invalid.
    """
    line = line.strip()
    if not line:
        return None

    match = _LOG_PATTERN.match(line)
    if not match:
        return None

    timestamp_str, pid_str, content = match.groups()

    try:
        pid = int(pid_str)
    except ValueError:
        return None

    # Parse event type and message
    if ":" in content:
        event, message = content.split(":", 1)
        event = event.strip()
        message = message.strip()
    else:
        event = content.strip()
        message = None

    return {
        "timestamp": timestamp_str,
        "pid": pid,
        "event": event,
        "message": message,
        "raw": line,
    }


def _format_sse_event(data: dict[str, Any], event_type: str = "message") -> str:
    """Format data as a Server-Sent Event."""
    json_data = json.dumps(data)
    if event_type == "message":
        return f"data: {json_data}\n\n"
    return f"event: {event_type}\ndata: {json_data}\n\n"


async def _stream_worker_logs(
    worker_pid: int,
    log_path: Path,
    tail_lines: int = 50,
) -> AsyncIterator[str]:
    """Stream log entries for a specific worker PID via SSE.

    Args:
        worker_pid: The PID of the worker to filter logs for.
        log_path: Path to the log file.
        tail_lines: Number of recent lines to include initially.

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
                if entry and entry["pid"] == worker_pid:
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
                    if entry and entry["pid"] == worker_pid:
                        yield _format_sse_event(entry)

            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.debug("Worker log stream cancelled for PID %d", worker_pid)
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

    Opens a persistent SSE connection that streams log entries filtered
    by the worker's process ID.

    Args:
        worker_id: Worker unique identifier
        tail: Number of recent log lines to include at start (default: 50)

    Returns:
        StreamingResponse with SSE content.

    Raises:
        HTTPException: If worker not found or daemon not running.
    """
    try:
        client = _get_rpc_client()
        result = client.call("worker.get", {"worker_id": worker_id})
        worker = result.get("worker")

        if not worker:
            raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")

        worker_pid = worker.get("pid")
        if not worker_pid:
            raise HTTPException(
                status_code=400,
                detail=f"Worker {worker_id} has no PID (may not be running)",
            )

        # Determine log file path based on worker's project
        project_path = worker.get("project_path")
        if project_path:
            log_path = Path(project_path) / "claude.log"
        else:
            log_path = Path(LOG_FILE)

        logger.info(
            "Starting log stream for worker %s (PID %d) from %s",
            worker_id,
            worker_pid,
            log_path,
        )

        return StreamingResponse(
            _stream_worker_logs(worker_pid, log_path, tail_lines=tail),
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


class LogEntry(BaseModel):
    """Response model for a log entry."""

    timestamp: str = Field(..., description="Log entry timestamp")
    pid: int = Field(..., description="Process ID")
    event: str = Field(..., description="Event type")
    message: str | None = Field(None, description="Event message/details")


@router.get("/{worker_id}/logs/recent", response_model=list[LogEntry])
async def get_worker_recent_logs(
    worker_id: str,
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
) -> list[dict[str, Any]]:
    """Get recent log entries for a specific worker.

    Returns log entries filtered by the worker's process ID.

    Args:
        worker_id: Worker unique identifier
        limit: Maximum number of entries to return

    Returns:
        List of log entries, most recent first.

    Raises:
        HTTPException: If worker not found or daemon not running.
    """
    try:
        client = _get_rpc_client()
        result = client.call("worker.get", {"worker_id": worker_id})
        worker = result.get("worker")

        if not worker:
            raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")

        worker_pid = worker.get("pid")
        if not worker_pid:
            raise HTTPException(
                status_code=400,
                detail=f"Worker {worker_id} has no PID (may not be running)",
            )

        # Determine log file path
        project_path = worker.get("project_path")
        if project_path:
            log_path = Path(project_path) / "claude.log"
        else:
            log_path = Path(LOG_FILE)

        if not log_path.exists():
            return []

        # Read and filter logs
        entries = []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                entry = _parse_log_line(line)
                if entry and entry["pid"] == worker_pid:
                    entries.append(entry)
        except OSError as e:
            logger.warning("Failed to read log file %s: %s", log_path, e)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read log file: {e}",
            )

        # Return most recent first, limited
        entries.reverse()
        return entries[:limit]

    except HTTPException:
        raise
    except Exception as e:
        _handle_rpc_error(e, f"get_worker_recent_logs({worker_id})")
        raise
