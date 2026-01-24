"""Service layer for bead operations.

This module provides a centralized interface for interacting with the bd CLI.
All bd subprocess calls should go through this service to ensure consistent
error handling and logging.
"""

import json
import logging
import re
import subprocess
from typing import Any

from dashboard.exceptions import (
    BeadCommandError,
    BeadNotFoundError,
    BeadParseError,
    BeadValidationError,
)

logger = logging.getLogger(__name__)

# Valid bead ID pattern: prefix-shortid (e.g., multi_agent_beads-abc123)
BEAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_]+-[a-zA-Z0-9]+$")

# Command timeout in seconds
DEFAULT_TIMEOUT = 30


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
    ) -> list[dict[str, Any]]:
        """List beads with optional filters.

        Args:
            status: Filter by status (open, in_progress, closed).
            label: Filter by label.
            priority: Filter by priority (0-4).
            limit: Maximum number of beads (0 = unlimited).

        Returns:
            List of bead dictionaries.

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        args = ["list", "--json", "--limit", str(limit)]

        if status:
            args.extend(["--status", status])
        if label:
            args.extend(["-l", label])
        if priority is not None:
            args.extend(["-p", str(priority)])

        output = cls.run_command(args)
        return cls.parse_json_output(output)

    @classmethod
    def list_ready(cls, *, label: str | None = None) -> list[dict[str, Any]]:
        """List beads ready to work on (no blockers).

        Args:
            label: Filter by label.

        Returns:
            List of ready bead dictionaries.

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        args = ["ready", "--json"]

        if label:
            args.extend(["-l", label])

        output = cls.run_command(args)
        return cls.parse_json_output(output)

    @classmethod
    def list_blocked(cls) -> list[dict[str, Any]]:
        """List blocked beads.

        Returns:
            List of blocked bead dictionaries.

        Raises:
            BeadCommandError: If the bd command fails.
            BeadParseError: If output parsing fails.
        """
        output = cls.run_command(["blocked", "--json"])
        return cls.parse_json_output(output)

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
