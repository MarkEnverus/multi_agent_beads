"""REST API endpoints for log streaming."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dashboard.config import LOG_FILE
from dashboard.exceptions import LogFileError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogEntry(BaseModel):
    """Response model for a log entry."""

    timestamp: str = Field(..., description="Log entry timestamp")
    pid: int = Field(..., description="Process ID of the agent")
    event: str = Field(..., description="Event type (SESSION_START, CLAIM, etc.)")
    message: str | None = Field(None, description="Event message/details")
    role: str | None = Field(None, description="Inferred agent role")
    bead_id: str | None = Field(None, description="Associated bead ID if any")


# Log line pattern: [TIMESTAMP] [PID] EVENT_TYPE: details
LOG_PATTERN = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\d+)\] (.+)"
)

# Bead ID pattern for extraction
BEAD_ID_PATTERN = re.compile(r"(multi_agent_beads-\w+)")

# SSE retry interval in milliseconds (sent to client for reconnection)
SSE_RETRY_MS = 3000


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

    match = LOG_PATTERN.match(line)
    if not match:
        return None

    timestamp_str, pid_str, content = match.groups()

    try:
        pid = int(pid_str)
    except ValueError:
        logger.warning("Invalid PID in log line: %s", pid_str)
        return None

    # Parse event type and message
    if ":" in content:
        event, message = content.split(":", 1)
        event = event.strip()
        message = message.strip()
    else:
        event = content.strip()
        message = None

    # Extract bead ID if present
    bead_id = None
    if message:
        bead_match = BEAD_ID_PATTERN.search(message)
        if bead_match:
            bead_id = bead_match.group(1)

    # Infer role from event content
    role = _infer_role_from_content(event, message)

    return {
        "timestamp": timestamp_str,
        "pid": pid,
        "event": event,
        "message": message,
        "role": role,
        "bead_id": bead_id,
    }


def _infer_role_from_content(event: str, message: str | None) -> str | None:
    """Infer agent role from log content."""
    if not message:
        return None

    message_lower = message.lower()

    # Infer from content keywords
    if any(kw in message_lower for kw in ["test", "qa", "verify"]):
        return "qa"
    if any(kw in message_lower for kw in ["review", "pr"]):
        return "reviewer"
    if any(kw in message_lower for kw in ["arch", "design", "tech lead"]):
        return "tech_lead"
    if any(kw in message_lower for kw in ["epic", "priorit", "manage"]):
        return "manager"
    if any(kw in message_lower for kw in ["implement", "fix", "add", "create", "dashboard"]):
        return "developer"

    return None


def _read_recent_logs(
    limit: int = 100,
    role: str | None = None,
    bead_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent log entries from file.

    Args:
        limit: Maximum number of entries to return.
        role: Filter by agent role.
        bead_id: Filter by bead ID.

    Returns:
        List of log entries, most recent first.

    Raises:
        LogFileError: If the log file cannot be read.
    """
    log_path = Path(LOG_FILE)

    if not log_path.exists():
        logger.debug("Log file does not exist: %s", log_path)
        return []

    entries = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                entry = _parse_log_line(line)
                if entry:
                    # Apply filters
                    if role and entry.get("role") != role:
                        continue
                    if bead_id and entry.get("bead_id") != bead_id:
                        continue
                    entries.append(entry)

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

    # Return most recent entries first
    entries.reverse()
    return entries[:limit]


def _format_sse_event(
    data: dict[str, Any],
    event_type: str = "message",
) -> str:
    """Format data as a Server-Sent Event.

    Args:
        data: Data to serialize as JSON.
        event_type: SSE event type (default: "message").

    Returns:
        Formatted SSE event string.
    """
    json_data = json.dumps(data)
    if event_type == "message":
        return f"data: {json_data}\n\n"
    return f"event: {event_type}\ndata: {json_data}\n\n"


def _format_sse_error(message: str, error_type: str = "error") -> str:
    """Format an error as a Server-Sent Event.

    Args:
        message: Error message.
        error_type: Type of error for client handling.

    Returns:
        Formatted SSE error event string.
    """
    return _format_sse_event(
        {"type": error_type, "message": message},
        event_type="error",
    )


async def _tail_log_file(
    role: str | None = None,
    bead_id: str | None = None,
) -> AsyncIterator[str]:
    """Async generator that yields new log entries as SSE events.

    Uses polling approach for cross-platform compatibility.
    Handles log file rotation gracefully by reopening if file is truncated.
    Sends error events to clients when issues occur.

    Args:
        role: Filter by agent role.
        bead_id: Filter by bead ID.

    Yields:
        SSE-formatted event strings.
    """
    log_path = Path(LOG_FILE)
    last_position = 0
    last_inode: int | None = None
    error_count = 0
    max_consecutive_errors = 5

    # Send initial retry interval to client
    yield f"retry: {SSE_RETRY_MS}\n\n"

    # If file exists, start from end (don't replay old entries)
    if log_path.exists():
        try:
            stat = log_path.stat()
            last_position = stat.st_size
            last_inode = stat.st_ino
            logger.debug("Starting SSE stream from position %d", last_position)
        except OSError as e:
            logger.warning("Could not stat log file: %s", e)
            yield _format_sse_error(f"Could not access log file: {e}", "warning")

    while True:
        try:
            if not log_path.exists():
                # File doesn't exist yet, wait
                await asyncio.sleep(0.5)
                continue

            current_stat = log_path.stat()
            current_inode = current_stat.st_ino
            current_size = current_stat.st_size

            # Check for log rotation (inode changed or file truncated)
            if last_inode is not None and (
                current_inode != last_inode or current_size < last_position
            ):
                # File was rotated or truncated, start from beginning
                logger.info("Log file rotated, restarting from beginning")
                last_position = 0
                last_inode = current_inode
                yield _format_sse_event(
                    {"type": "rotation", "message": "Log file rotated"},
                    event_type="info",
                )

            # Check if new content available
            if current_size > last_position:
                with open(log_path, encoding="utf-8") as f:
                    f.seek(last_position)
                    new_content = f.read()
                    last_position = f.tell()

                # Process new lines
                for line in new_content.splitlines():
                    entry = _parse_log_line(line)
                    if entry:
                        # Apply filters
                        if role and entry.get("role") != role:
                            continue
                        if bead_id and entry.get("bead_id") != bead_id:
                            continue

                        # Yield as SSE event
                        yield _format_sse_event(entry)

                # Reset error count on successful read
                error_count = 0

            # Small delay between polls
            await asyncio.sleep(0.5)

        except PermissionError:
            error_count += 1
            logger.warning("Permission denied reading log file (attempt %d)", error_count)
            if error_count >= max_consecutive_errors:
                yield _format_sse_error("Permission denied - stream stopping", "fatal")
                break
            yield _format_sse_error("Permission denied reading log file", "warning")
            await asyncio.sleep(2.0)

        except OSError as e:
            error_count += 1
            logger.warning("OS error reading log file (attempt %d): %s", error_count, e)
            if error_count >= max_consecutive_errors:
                yield _format_sse_error(f"Persistent error - stream stopping: {e}", "fatal")
                break
            yield _format_sse_error(f"Error reading log file: {e}", "warning")
            await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            # Client disconnected
            logger.debug("SSE client disconnected")
            break

        except Exception as e:
            error_count += 1
            logger.exception("Unexpected error in SSE stream: %s", e)
            if error_count >= max_consecutive_errors:
                yield _format_sse_error("Unexpected error - stream stopping", "fatal")
                break
            yield _format_sse_error("An unexpected error occurred", "warning")
            await asyncio.sleep(1.0)


@router.get("/recent", response_model=list[LogEntry])
async def get_recent_logs(
    limit: int = Query(100, ge=1, le=1000, description="Maximum entries to return"),
    role: str | None = Query(None, description="Filter by agent role"),
    bead_id: str | None = Query(None, description="Filter by bead ID"),
) -> list[dict[str, Any]]:
    """Get recent log entries.

    Returns log entries from the claude.log file, most recent first.
    Supports filtering by role and/or bead ID.

    Raises:
        LogFileError: If the log file cannot be read.
    """
    logger.debug("Fetching recent logs: limit=%d, role=%s, bead_id=%s", limit, role, bead_id)
    return _read_recent_logs(limit=limit, role=role, bead_id=bead_id)


@router.get("/stream")
async def stream_logs(
    role: str | None = Query(None, description="Filter by agent role"),
    bead_id: str | None = Query(None, description="Filter by bead ID"),
) -> StreamingResponse:
    """Stream live log entries via Server-Sent Events.

    Opens a persistent SSE connection that streams new log entries
    as they are written to claude.log.

    SSE Format:
        data: {"timestamp": "...", "pid": 123, "event": "CLAIM", ...}

    Error events are sent as:
        event: error
        data: {"type": "warning|fatal", "message": "..."}

    Info events are sent as:
        event: info
        data: {"type": "rotation", "message": "Log file rotated"}

    Handles log file rotation gracefully - if the log file is truncated
    or rotated, streaming continues from the beginning of the new file.
    """
    logger.info("Starting SSE log stream: role=%s, bead_id=%s", role, bead_id)
    return StreamingResponse(
        _tail_log_file(role=role, bead_id=bead_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
