"""Custom exception classes for the Multi-Agent Dashboard.

This module defines a hierarchy of exceptions for consistent error handling
across the dashboard application. All exceptions inherit from DashboardError
to allow catch-all handling when needed.

Exception Hierarchy:
    DashboardError (base)
    ├── BeadError (bead-related errors)
    │   ├── BeadCommandError (bd subprocess failures)
    │   ├── BeadNotFoundError (invalid/missing bead)
    │   └── BeadParseError (JSON parsing failures)
    ├── LogFileError (log file access/parse errors)
    └── AgentSpawnError (agent spawn failures)
"""

from typing import Any


class DashboardError(Exception):
    """Base exception for all dashboard errors.

    Attributes:
        message: Human-readable error description.
        detail: Technical details for debugging (optional).
        status_code: Suggested HTTP status code for API responses.
    """

    def __init__(
        self,
        message: str,
        detail: str | None = None,
        status_code: int = 500,
    ) -> None:
        self.message = message
        self.detail = detail
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to a dictionary for JSON responses."""
        result: dict[str, Any] = {
            "error": self.__class__.__name__,
            "message": self.message,
        }
        if self.detail:
            result["detail"] = self.detail
        return result


class BeadError(DashboardError):
    """Base exception for bead-related errors."""

    def __init__(
        self,
        message: str,
        bead_id: str | None = None,
        detail: str | None = None,
        status_code: int = 500,
    ) -> None:
        self.bead_id = bead_id
        super().__init__(message, detail, status_code)

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        if self.bead_id:
            result["bead_id"] = self.bead_id
        return result


class BeadCommandError(BeadError):
    """Raised when a bd subprocess command fails.

    This includes timeouts, command not found, non-zero exit codes,
    and other subprocess-related failures.
    """

    def __init__(
        self,
        message: str,
        command: list[str] | None = None,
        stderr: str | None = None,
        return_code: int | None = None,
        bead_id: str | None = None,
    ) -> None:
        detail_parts = []
        if command:
            detail_parts.append(f"command: bd {' '.join(command)}")
        if stderr:
            detail_parts.append(f"stderr: {stderr}")
        if return_code is not None:
            detail_parts.append(f"exit_code: {return_code}")

        detail = "; ".join(detail_parts) if detail_parts else None
        super().__init__(message, bead_id=bead_id, detail=detail, status_code=500)
        self.command = command
        self.stderr = stderr
        self.return_code = return_code


class BeadNotFoundError(BeadError):
    """Raised when a bead ID doesn't exist or is invalid."""

    def __init__(self, bead_id: str, message: str | None = None) -> None:
        msg = message or f"Bead not found: {bead_id}"
        super().__init__(msg, bead_id=bead_id, status_code=404)


class BeadParseError(BeadError):
    """Raised when bead JSON output cannot be parsed."""

    def __init__(
        self,
        message: str = "Failed to parse bead data",
        raw_output: str | None = None,
        bead_id: str | None = None,
    ) -> None:
        detail: str | None
        if raw_output and len(raw_output) > 200:
            detail = f"raw_output: {raw_output[:200]}..."
        else:
            detail = raw_output
        super().__init__(message, bead_id=bead_id, detail=detail, status_code=500)
        self.raw_output = raw_output


class BeadValidationError(BeadError):
    """Raised when bead input validation fails."""

    def __init__(self, message: str, field: str | None = None, bead_id: str | None = None) -> None:
        detail = f"field: {field}" if field else None
        super().__init__(message, bead_id=bead_id, detail=detail, status_code=400)
        self.field = field


class LogFileError(DashboardError):
    """Raised when there are issues with the log file.

    This includes file not found, permission denied, read errors,
    and parse failures.
    """

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        detail: str | None = None,
    ) -> None:
        if file_path and not detail:
            detail = f"file: {file_path}"
        super().__init__(message, detail=detail, status_code=500)
        self.file_path = file_path


class AgentSpawnError(DashboardError):
    """Raised when agent spawning fails.

    This includes invalid roles, missing prompt files, subprocess failures,
    and platform-specific issues.
    """

    def __init__(
        self,
        message: str,
        role: str | None = None,
        instance: int | None = None,
        detail: str | None = None,
    ) -> None:
        detail_parts = []
        if role:
            detail_parts.append(f"role: {role}")
        if instance is not None:
            detail_parts.append(f"instance: {instance}")
        if detail:
            detail_parts.append(detail)

        combined_detail = "; ".join(detail_parts) if detail_parts else None
        super().__init__(message, detail=combined_detail, status_code=500)
        self.role = role
        self.instance = instance


class SSEConnectionError(DashboardError):
    """Raised when SSE streaming encounters an error."""

    def __init__(self, message: str = "SSE connection error", detail: str | None = None) -> None:
        super().__init__(message, detail=detail, status_code=500)
