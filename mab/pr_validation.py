"""PR validation utilities for bead close operations.

This module provides functions to validate that PRs are merged before
allowing beads to be closed, enforcing the PR-first workflow for code changes.
"""

import json
import subprocess
from dataclasses import dataclass
from enum import Enum


class PRStatus(Enum):
    """Status of a PR relative to bead close requirements."""

    MERGED = "merged"
    OPEN = "open"
    CLOSED = "closed"  # closed without merge
    NOT_FOUND = "not_found"
    NO_REMOTE = "no_remote"


@dataclass
class PRInfo:
    """Information about a PR."""

    number: int
    title: str
    status: PRStatus
    url: str = ""
    merged_at: str = ""

    @classmethod
    def not_found(cls) -> "PRInfo":
        """Create a PRInfo for when no PR is found."""
        return cls(number=0, title="", status=PRStatus.NOT_FOUND)


@dataclass
class ValidationResult:
    """Result of PR validation for bead close."""

    allowed: bool
    reason: str
    pr_info: PRInfo | None = None
    suggestions: list[str] | None = None


def has_git_remote() -> bool:
    """Check if the repository has a git remote configured."""
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_pr_for_bead(bead_id: str) -> PRInfo | None:
    """Find a PR that references the given bead ID.

    Searches for PRs that mention the bead ID in title, body, or commits.

    Args:
        bead_id: The bead identifier (e.g., "multi_agent_beads-9zu7")

    Returns:
        PRInfo if found, None otherwise
    """
    if not has_git_remote():
        return None

    # Search for PRs mentioning this bead
    try:
        # First try to find merged PRs
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--search",
                bead_id,
                "--json",
                "number,title,state,url,mergedAt",
                "--limit",
                "10",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return None

        prs = json.loads(result.stdout) if result.stdout.strip() else []

        for pr in prs:
            status = PRStatus.NOT_FOUND
            if pr.get("state") == "MERGED":
                status = PRStatus.MERGED
            elif pr.get("state") == "OPEN":
                status = PRStatus.OPEN
            elif pr.get("state") == "CLOSED":
                status = PRStatus.CLOSED

            return PRInfo(
                number=pr.get("number", 0),
                title=pr.get("title", ""),
                status=status,
                url=pr.get("url", ""),
                merged_at=pr.get("mergedAt", ""),
            )

        return None

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def get_pr_by_number(pr_number: int) -> PRInfo | None:
    """Get PR info by number.

    Args:
        pr_number: The PR number to look up

    Returns:
        PRInfo if found, None otherwise
    """
    if not has_git_remote():
        return None

    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,title,state,url,mergedAt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return None

        pr = json.loads(result.stdout) if result.stdout.strip() else {}

        status = PRStatus.NOT_FOUND
        if pr.get("state") == "MERGED":
            status = PRStatus.MERGED
        elif pr.get("state") == "OPEN":
            status = PRStatus.OPEN
        elif pr.get("state") == "CLOSED":
            status = PRStatus.CLOSED

        return PRInfo(
            number=pr.get("number", 0),
            title=pr.get("title", ""),
            status=status,
            url=pr.get("url", ""),
            merged_at=pr.get("mergedAt", ""),
        )

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def is_code_bead(bead_id: str) -> bool:
    """Determine if a bead involves code changes.

    Checks bead labels and type to determine if it's a code change
    that requires a merged PR.

    Args:
        bead_id: The bead identifier

    Returns:
        True if this is a code bead requiring PR, False otherwise
    """
    try:
        result = subprocess.run(
            ["bd", "show", bead_id, "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            # Default to treating as code bead if we can't determine
            return True

        bead = json.loads(result.stdout) if result.stdout.strip() else {}

        # Non-code types
        non_code_types = {"docs", "documentation", "config", "meta", "planning", "epic"}
        bead_type = bead.get("type", "").lower()
        if bead_type in non_code_types:
            return False

        # Non-code labels
        non_code_labels = {"docs", "documentation", "config", "planning", "meta"}
        labels = {label.lower() for label in bead.get("labels", [])}
        if labels & non_code_labels and not (labels & {"dev", "feature", "bug", "fix"}):
            return False

        return True

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        # Default to treating as code bead if we can't determine
        return True


def validate_close(
    bead_id: str,
    pr_number: int | None = None,
    force: bool = False,
    no_pr: bool = False,
) -> ValidationResult:
    """Validate that a bead can be closed.

    Checks if the bead has a merged PR (required for code beads).

    Args:
        bead_id: The bead identifier to close
        pr_number: Optional specific PR number to verify
        force: If True, bypass validation
        no_pr: If True, explicitly mark as non-code bead (no PR required)

    Returns:
        ValidationResult indicating if close is allowed
    """
    # Force override bypasses all checks
    if force:
        return ValidationResult(
            allowed=True,
            reason="Forced close (--force flag)",
        )

    # Explicit no-PR flag for non-code beads
    if no_pr:
        return ValidationResult(
            allowed=True,
            reason="Non-code bead (--no-pr flag)",
        )

    # Check if git remote exists
    if not has_git_remote():
        return ValidationResult(
            allowed=True,
            reason="No git remote configured (local-only mode)",
        )

    # Check if this is a code bead
    if not is_code_bead(bead_id):
        return ValidationResult(
            allowed=True,
            reason="Non-code bead (type/labels indicate no code changes)",
        )

    # Look for PR by number or by bead reference
    pr_info = None
    if pr_number:
        pr_info = get_pr_by_number(pr_number)
    else:
        pr_info = get_pr_for_bead(bead_id)

    if pr_info is None:
        return ValidationResult(
            allowed=False,
            reason=f"No PR found referencing {bead_id}",
            suggestions=[
                f"Create a PR with '{bead_id}' in title or body",
                "Use --no-pr flag if this is a non-code bead",
                "Use --force to bypass validation",
            ],
        )

    if pr_info.status == PRStatus.MERGED:
        return ValidationResult(
            allowed=True,
            reason=f"PR #{pr_info.number} is merged",
            pr_info=pr_info,
        )

    if pr_info.status == PRStatus.OPEN:
        return ValidationResult(
            allowed=False,
            reason=f"PR #{pr_info.number} is still open (not merged)",
            pr_info=pr_info,
            suggestions=[
                f"Merge PR #{pr_info.number} first",
                f"Run: gh pr merge {pr_info.number} --squash --delete-branch",
                "Use --force to bypass validation",
            ],
        )

    if pr_info.status == PRStatus.CLOSED:
        return ValidationResult(
            allowed=False,
            reason=f"PR #{pr_info.number} was closed without merging",
            pr_info=pr_info,
            suggestions=[
                "Create a new PR with the changes",
                "Use --force to bypass validation",
            ],
        )

    return ValidationResult(
        allowed=False,
        reason="Could not verify PR status",
        suggestions=[
            "Specify PR number with --pr flag",
            "Use --force to bypass validation",
        ],
    )
