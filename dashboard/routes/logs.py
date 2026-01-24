"""REST API endpoints for log streaming."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dashboard.config import LOG_FILE

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


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a single log line into a structured dict."""
    line = line.strip()
    if not line:
        return None

    match = LOG_PATTERN.match(line)
    if not match:
        return None

    timestamp_str, pid_str, content = match.groups()

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
        # Pattern: bead-id - title or just bead-id
        bead_match = re.match(r"(multi_agent_beads-\w+)", message)
        if bead_match:
            bead_id = bead_match.group(1)

    # Infer role from event content
    role = _infer_role_from_content(event, message)

    return {
        "timestamp": timestamp_str,
        "pid": int(pid_str),
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


def _read_recent_logs(limit: int = 100, role: str | None = None, bead_id: str | None = None) -> list[dict[str, Any]]:
    """Read recent log entries from file.

    Args:
        limit: Maximum number of entries to return.
        role: Filter by agent role.
        bead_id: Filter by bead ID.

    Returns:
        List of log entries, most recent first.
    """
    log_path = Path(LOG_FILE)
    if not log_path.exists():
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
    except OSError:
        return []

    # Return most recent entries first
    entries.reverse()
    return entries[:limit]


async def _tail_log_file(role: str | None = None, bead_id: str | None = None) -> AsyncIterator[str]:
    """Async generator that yields new log entries as SSE events.

    Uses polling approach for cross-platform compatibility.
    Handles log file rotation gracefully by reopening if file is truncated.
    """
    log_path = Path(LOG_FILE)
    last_position = 0
    last_inode: int | None = None

    # If file exists, start from end (don't replay old entries)
    if log_path.exists():
        last_position = log_path.stat().st_size
        last_inode = log_path.stat().st_ino

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
            if last_inode is not None and (current_inode != last_inode or current_size < last_position):
                # File was rotated or truncated, start from beginning
                last_position = 0
                last_inode = current_inode

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
                        yield f"data: {json.dumps(entry)}\n\n"

            # Small delay between polls
            await asyncio.sleep(0.5)

        except OSError:
            # File access error, retry after delay
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            # Client disconnected
            break


@router.get("/recent", response_model=list[LogEntry])
async def get_recent_logs(
    limit: int = Query(100, ge=1, le=1000, description="Maximum entries to return"),
    role: str | None = Query(None, description="Filter by agent role"),
    bead_id: str | None = Query(None, description="Filter by bead ID"),
) -> list[dict[str, Any]]:
    """Get recent log entries.

    Returns log entries from the claude.log file, most recent first.
    Supports filtering by role and/or bead ID.
    """
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

    Handles log file rotation gracefully - if the log file is truncated
    or rotated, streaming continues from the beginning of the new file.
    """
    return StreamingResponse(
        _tail_log_file(role=role, bead_id=bead_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
