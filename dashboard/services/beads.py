"""Service layer for bead operations.

This module provides a centralized interface for interacting with the bd CLI.
All bd subprocess calls should go through this service to ensure consistent
error handling and logging.

Performance optimizations:
- Cache layer with 5-second TTL for list operations
- Batch fetching to reduce subprocess calls
"""

import json
import logging
import re
import subprocess
import time
from typing import Any, cast

from dashboard.config import CACHE_STALE_TTL_SECONDS, CACHE_TTL_SECONDS
from dashboard.exceptions import (
    BeadCommandError,
    BeadNotFoundError,
    BeadParseError,
    BeadValidationError,
)

logger = logging.getLogger(__name__)

# Valid bead ID pattern: prefix-shortid (e.g., multi_agent_beads-abc123)
BEAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_]+-[a-zA-Z0-9]+$")

# Command timeout in seconds - allow enough time for slow bd CLI operations
# The bd CLI can be slow with large JSONL files (10+ seconds for 1000+ beads)
DEFAULT_TIMEOUT = 30


class _BeadCache:
    """Time-based cache with stale-while-revalidate support for bead list operations.

    Thread-safe for FastAPI's async context since we use simple dict operations.

    Implements two-tier caching:
    - Fresh TTL: Data within this age is served directly (default 30s)
    - Stale TTL: Data within this age is served while triggering background refresh (default 120s)
    - Beyond stale TTL: Cache miss, must block on fresh data
    """

    def __init__(
        self,
        ttl: float = CACHE_TTL_SECONDS,
        stale_ttl: float = CACHE_STALE_TTL_SECONDS,
    ) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl
        self._stale_ttl = stale_ttl
        # Track which keys are currently being refreshed to prevent stampede
        self._refreshing: set[str] = set()

    def get(self, key: str) -> Any | None:
        """Get cached value if not expired (beyond stale TTL)."""
        if key not in self._cache:
            return None
        timestamp, value = self._cache[key]
        age = time.monotonic() - timestamp
        # Only delete if beyond stale TTL
        if age > self._stale_ttl:
            del self._cache[key]
            return None
        return value

    def get_with_stale_info(self, key: str) -> tuple[Any | None, bool, bool]:
        """Get cached value with staleness information.

        Returns:
            Tuple of (value, is_fresh, needs_refresh):
            - value: The cached value, or None if not found or too stale
            - is_fresh: True if data is within fresh TTL
            - needs_refresh: True if data should be refreshed in background
        """
        if key not in self._cache:
            return None, False, False

        timestamp, value = self._cache[key]
        age = time.monotonic() - timestamp

        # Beyond stale TTL - treat as cache miss
        if age > self._stale_ttl:
            del self._cache[key]
            return None, False, False

        is_fresh = age <= self._ttl
        # Need refresh if stale and not already being refreshed
        needs_refresh = not is_fresh and key not in self._refreshing

        return value, is_fresh, needs_refresh

    def mark_refreshing(self, key: str) -> bool:
        """Mark a key as being refreshed.

        Returns True if successfully marked (wasn't already refreshing).
        This prevents multiple concurrent refreshes for the same key.
        """
        if key in self._refreshing:
            return False
        self._refreshing.add(key)
        return True

    def mark_refresh_complete(self, key: str) -> None:
        """Mark a key's refresh as complete."""
        self._refreshing.discard(key)

    def set(self, key: str, value: Any) -> None:
        """Cache a value with current timestamp."""
        self._cache[key] = (time.monotonic(), value)
        self._refreshing.discard(key)

    def invalidate(self, key: str | None = None) -> None:
        """Invalidate a specific key or all keys."""
        if key is None:
            self._cache.clear()
            self._refreshing.clear()
        elif key in self._cache:
            del self._cache[key]
            self._refreshing.discard(key)

    def make_key(self, *args: Any) -> str:
        """Create a cache key from arguments."""
        return ":".join(str(a) for a in args)


# Global cache instance
_cache = _BeadCache()


class BeadService:
    """Service for interacting with the bd CLI.

    This service provides methods for all bead operations, handling
    subprocess execution, JSON parsing, and error translation into
    appropriate exceptions.

    All methods are static since no state is maintained.
    """

    @staticmethod
    def validate_bead_id(bead_id: str) -> None:
        """Validate a bead ID format.

        Args:
            bead_id: The bead ID to validate.

        Raises:
            BeadValidationError: If the bead ID format is invalid.
        """
        if not bead_id:
            raise BeadValidationError("Bead ID cannot be empty", field="bead_id")
        if not BEAD_ID_PATTERN.match(bead_id):
            raise BeadValidationError(
                f"Invalid bead ID format: {bead_id}. Expected format: prefix-shortid",
                field="bead_id",
                bead_id=bead_id,
            )

    @staticmethod
    def run_command(
        args: list[str],
        *,
        timeout: int = DEFAULT_TIMEOUT,
        bead_id: str | None = None,
    ) -> str:
        """Execute a bd command and return its output.

        Args:
            args: Arguments to pass to the bd command.
            timeout: Command timeout in seconds.
            bead_id: Associated bead ID for error context.

        Returns:
            The stdout output from the command.

        Raises:
            BeadCommandError: If the command fails for any reason.
        """
        cmd_str = f"bd {' '.join(args)}"
        logger.debug("Executing command: %s", cmd_str)

        try:
            result = subprocess.run(
                ["bd", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip() or result.stdout.strip()
                logger.warning(
                    "bd command failed: %s (exit=%d, stderr=%s)",
                    cmd_str,
                    result.returncode,
                    stderr,
                )
                raise BeadCommandError(
                    message=f"bd command failed: {stderr or 'Unknown error'}",
                    command=args,
                    stderr=stderr,
                    return_code=result.returncode,
                    bead_id=bead_id,
                )

            logger.debug("Command succeeded: %s", cmd_str)
            return result.stdout

        except subprocess.TimeoutExpired:
            logger.error("bd command timed out after %ds: %s", timeout, cmd_str)
            raise BeadCommandError(
                message=f"Command timed out after {timeout} seconds",
                command=args,
                bead_id=bead_id,
            ) from None

        except FileNotFoundError:
            logger.error("bd command not found in PATH")
            raise BeadCommandError(
                message="The 'bd' command is not installed or not in PATH. "
                "Please ensure the beads CLI is installed.",
                command=args,
                bead_id=bead_id,
            ) from None

        except PermissionError:
            logger.error("Permission denied executing bd command")
            raise BeadCommandError(
                message="Permission denied when executing 'bd' command",
                command=args,
                bead_id=bead_id,
            ) from None

        except OSError as e:
            logger.error("OS error executing bd command: %s", e)
            raise BeadCommandError(
                message=f"System error executing command: {e}",
                command=args,
                bead_id=bead_id,
            ) from None

    @staticmethod
    def parse_json_output(output: str, *, bead_id: str | None = None) -> list[dict[str, Any]]:
        """Parse JSON output from a bd command.

        Args:
            output: The raw stdout output from bd.
            bead_id: Associated bead ID for error context.

        Returns:
            Parsed list of bead dictionaries.

        Raises:
            BeadParseError: If JSON parsing fails.
        """
        if not output or not output.strip():
            logger.debug("Empty output from bd command, returning empty list")
            return []

        try:
            result = json.loads(output)
            if isinstance(result, list):
                return result  # type: ignore[return-value]
            if isinstance(result, dict):
                # Single bead returned, wrap in list
                return [result]
            logger.warning("Unexpected JSON type: %s", type(result).__name__)
            return []

        except json.JSONDecodeError as e:
            logger.error("Failed to parse bd JSON output: %s", e)
            raise BeadParseError(
                message="Failed to parse bead data from command output",
                raw_output=output,
                bead_id=bead_id,
            ) from None

    @classmethod
    def list_beads(
        cls,
        *,
        status: str | None = None,
        label: str | None = None,
        priority: int | None = None,
        limit: int = 0,
        use_cache: bool = True,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        """List beads with optional filters.

        Args:
            status: Filter by status (open, in_progress, closed).
            label: Filter by label.
            priority: Filter by priority (0-4).
            limit: Maximum number of beads (0 = unlimited).
            use_cache: Whether to use cached results (default True).
            include_all: Include closed beads (adds --all flag). Required to
                get a complete count of all beads in the system.

        Returns:
            List of bead dictionaries.

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        cache_key = _cache.make_key("list", status, label, priority, limit, include_all)

        if use_cache:
            cached = _cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for list_beads: %s", cache_key)
                return cast(list[dict[str, Any]], cached)

        args = ["list", "--json", "--limit", str(limit)]

        if include_all:
            args.append("--all")
        if status:
            args.extend(["--status", status])
        if label:
            args.extend(["-l", label])
        if priority is not None:
            args.extend(["-p", str(priority)])

        output = cls.run_command(args)
        result = cls.parse_json_output(output)

        _cache.set(cache_key, result)
        return result

    @classmethod
    def list_ready(
        cls,
        *,
        label: str | None = None,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """List beads ready to work on (no blockers).

        Args:
            label: Filter by label.
            use_cache: Whether to use cached results (default True).

        Returns:
            List of ready bead dictionaries.

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        cache_key = _cache.make_key("ready", label)

        if use_cache:
            cached = _cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for list_ready: %s", cache_key)
                return cast(list[dict[str, Any]], cached)

        args = ["ready", "--json"]

        if label:
            args.extend(["-l", label])

        output = cls.run_command(args)
        result = cls.parse_json_output(output)

        _cache.set(cache_key, result)
        return result

    @classmethod
    def list_blocked(cls, *, use_cache: bool = True) -> list[dict[str, Any]]:
        """List blocked beads.

        Args:
            use_cache: Whether to use cached results (default True).

        Returns:
            List of blocked bead dictionaries.

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        cache_key = "blocked"

        if use_cache:
            cached = _cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for list_blocked")
                return cast(list[dict[str, Any]], cached)

        output = cls.run_command(["blocked", "--json"])
        result = cls.parse_json_output(output)

        _cache.set(cache_key, result)
        return result

    @classmethod
    def get_bead(cls, bead_id: str) -> dict[str, Any]:
        """Get details for a single bead.

        Args:
            bead_id: The bead ID to retrieve.

        Returns:
            Bead dictionary.

        Raises:
            BeadValidationError: If bead_id format is invalid.
            BeadNotFoundError: If the bead doesn't exist.
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        cls.validate_bead_id(bead_id)

        try:
            output = cls.run_command(["show", bead_id, "--json"], bead_id=bead_id)
        except BeadCommandError as e:
            # Check if this is a "not found" error
            if e.stderr and "not found" in e.stderr.lower():
                raise BeadNotFoundError(bead_id) from None
            raise

        beads = cls.parse_json_output(output, bead_id=bead_id)
        if not beads:
            raise BeadNotFoundError(bead_id)

        return beads[0]

    @classmethod
    def create_bead(
        cls,
        *,
        title: str,
        description: str | None = None,
        priority: int = 2,
        issue_type: str = "task",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new bead.

        Args:
            title: Bead title.
            description: Bead description.
            priority: Priority level (0-4).
            issue_type: Issue type (task, bug, feature, epic).
            labels: List of labels to apply.

        Returns:
            The created bead dictionary.

        Raises:
            BeadValidationError: If input validation fails.
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        if not title or not title.strip():
            raise BeadValidationError("Bead title cannot be empty", field="title")

        args = [
            "create",
            "--title", title.strip(),
            "-p", str(priority),
            "-t", issue_type,
            "--silent",
        ]

        if description:
            args.extend(["-d", description])
        if labels:
            args.extend(["-l", ",".join(labels)])

        output = cls.run_command(args)

        # The --silent flag returns just the ID
        bead_id = output.strip()
        if not bead_id:
            raise BeadCommandError(
                message="Failed to get created bead ID from output",
                command=args,
            )

        # Invalidate cache after create
        _cache.invalidate()

        # Fetch and return the created bead
        return cls.get_bead(bead_id)

    @classmethod
    def sort_by_priority(cls, beads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort beads by priority (P0 first).

        Args:
            beads: List of bead dictionaries.

        Returns:
            Sorted list (does not modify original).
        """
        return sorted(beads, key=lambda b: b.get("priority", 4))

    @classmethod
    def get_kanban_data(
        cls,
        *,
        done_limit: int = 20,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Get all data needed for the kanban board in a single call.

        This batch method fetches all beads once and partitions them locally,
        reducing subprocess overhead from 4 calls to 1.

        Uses stale-while-revalidate caching: returns stale data immediately
        if available, rather than blocking for potentially slow bd CLI calls.
        Data within stale TTL (default 120s) is returned without blocking.

        Args:
            done_limit: Maximum number of closed beads to return.
            use_cache: Whether to use cached results (default True).

        Returns:
            Dictionary with keys:
                - ready_beads: Ready to work (open, no blockers), sorted by priority
                - in_progress_beads: Currently being worked on, sorted by priority
                - done_beads: Closed beads (limited), sorted by updated_at desc
                - total_count: Total number of beads
                - _cached: True if data was served from cache (for debugging)
                - _stale: True if cached data was stale (for debugging)

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        cache_key = _cache.make_key("kanban", done_limit)

        if use_cache:
            cached, is_fresh, needs_refresh = _cache.get_with_stale_info(cache_key)
            if cached is not None:
                if is_fresh:
                    logger.debug("Cache hit (fresh) for get_kanban_data")
                    result = cast(dict[str, Any], cached)
                    result["_cached"] = True
                    result["_stale"] = False
                    return result
                else:
                    # Stale but usable - return immediately, log that we're stale
                    logger.debug(
                        "Cache hit (stale) for get_kanban_data - "
                        "serving stale data to avoid blocking"
                    )
                    result = cast(dict[str, Any], cached)
                    result["_cached"] = True
                    result["_stale"] = True
                    return result

        # No cache or beyond stale TTL - must fetch fresh data
        logger.debug("Cache miss for get_kanban_data - fetching fresh data")
        result = cls._fetch_kanban_data(done_limit)
        result["_cached"] = False
        result["_stale"] = False
        _cache.set(cache_key, result)
        return result

    @classmethod
    def _fetch_kanban_data(cls, done_limit: int) -> dict[str, Any]:
        """Internal method to fetch and partition kanban data.

        Args:
            done_limit: Maximum number of closed beads to return.

        Returns:
            Dictionary with ready, in_progress, and done beads.
        """
        # Fetch all beads in a single call (include_all=True to include closed)
        all_beads = cls.list_beads(use_cache=False, include_all=True)

        # Also fetch blocked info to determine what's ready
        blocked_beads = cls.list_blocked(use_cache=False)
        blocked_ids = {b["id"] for b in blocked_beads}

        # Partition beads by status
        ready_beads: list[dict[str, Any]] = []
        in_progress_beads: list[dict[str, Any]] = []
        done_beads: list[dict[str, Any]] = []

        for bead in all_beads:
            status = bead.get("status", "").lower()
            bead_id = bead.get("id", "")

            if status == "closed":
                done_beads.append(bead)
            elif status == "in_progress":
                in_progress_beads.append(bead)
            elif status == "open" and bead_id not in blocked_ids:
                # Open and not blocked = ready
                ready_beads.append(bead)

        # Sort by priority (P0 first)
        ready_beads = cls.sort_by_priority(ready_beads)
        in_progress_beads = cls.sort_by_priority(in_progress_beads)

        # Sort done by updated_at desc, limit results
        done_beads.sort(
            key=lambda b: b.get("updated_at", ""),
            reverse=True,
        )
        done_beads = done_beads[:done_limit]

        return {
            "ready_beads": ready_beads,
            "in_progress_beads": in_progress_beads,
            "done_beads": done_beads,
            "total_count": len(all_beads),
        }

    @classmethod
    def invalidate_cache(cls) -> None:
        """Invalidate all cached data.

        Call this after any mutation operations (create, update, close).
        """
        _cache.invalidate()
        logger.debug("Bead cache invalidated")
