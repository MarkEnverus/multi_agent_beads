"""Integration test fixtures for API testing.

This module provides shared fixtures for integration tests including:
- Test client setup
- Temporary bead management with cleanup
- Utility functions for bd CLI interaction
"""

import subprocess
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.services import BeadService

# Test prefix for beads created during tests - easy to identify and cleanup
TEST_PREFIX = "test_api_fullstack"

# Global test client
client = TestClient(app)


def run_bd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a bd command and return the result.

    Args:
        args: Command arguments to pass to bd.
        check: If True, raise on non-zero exit code.

    Returns:
        CompletedProcess with stdout/stderr.

    Raises:
        RuntimeError: If check=True and command fails.
    """
    result = subprocess.run(
        ["bd", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"bd command failed: {result.stderr or result.stdout}")
    return result


def create_test_bead(
    title: str,
    priority: str = "4",
    description: str | None = None,
    labels: list[str] | None = None,
    issue_type: str = "task",
) -> str:
    """Create a test bead via CLI and return its ID.

    Args:
        title: Bead title.
        priority: Priority level (0-4).
        description: Optional description.
        labels: Optional list of labels.
        issue_type: Issue type (task, bug, feature).

    Returns:
        The created bead ID.

    Raises:
        RuntimeError: If bead creation fails.
    """
    args = [
        "create",
        "--title",
        title,
        "--description",
        description or "Integration test bead - safe to delete",
        "--priority",
        priority,
        "-t",
        issue_type,
        "--silent",
    ]
    if labels:
        args.extend(["-l", ",".join(labels)])
    else:
        args.extend(["-l", "test"])

    result = run_bd(args)
    bead_id = result.stdout.strip()
    if not bead_id:
        raise RuntimeError("Failed to create test bead - no ID returned")
    return bead_id


def delete_test_bead(bead_id: str) -> None:
    """Close a test bead to clean it up.

    Args:
        bead_id: The bead ID to close.
    """
    run_bd(["close", bead_id, "--reason", "Test cleanup"], check=False)


def update_bead_status(bead_id: str, status: str) -> None:
    """Update a bead's status via CLI.

    Args:
        bead_id: The bead ID to update.
        status: New status (open, in_progress).
    """
    run_bd(["update", bead_id, "--status", status])


def add_dependency(blocked_id: str, blocker_id: str) -> None:
    """Add a dependency between beads.

    Args:
        blocked_id: The bead that will be blocked.
        blocker_id: The bead that blocks the other.
    """
    run_bd(["dep", "add", blocked_id, blocker_id])


@pytest.fixture
def test_client() -> TestClient:
    """Provide a FastAPI test client."""
    return client


@pytest.fixture
def unique_id() -> str:
    """Generate a unique ID for test isolation."""
    return uuid.uuid4().hex[:8]


@pytest.fixture
def test_bead(unique_id: str) -> Generator[str, None, None]:
    """Create a single test bead and clean up after test.

    Yields:
        The created bead ID.
    """
    title = f"{TEST_PREFIX}_{unique_id}"
    bead_id = create_test_bead(title)
    # Invalidate cache after creation
    BeadService.invalidate_cache()
    yield bead_id
    delete_test_bead(bead_id)


@pytest.fixture
def test_bead_in_progress(unique_id: str) -> Generator[str, None, None]:
    """Create a test bead in in_progress status.

    Yields:
        The created bead ID.
    """
    title = f"{TEST_PREFIX}_in_progress_{unique_id}"
    bead_id = create_test_bead(title)
    update_bead_status(bead_id, "in_progress")
    BeadService.invalidate_cache()
    yield bead_id
    delete_test_bead(bead_id)


@pytest.fixture
def multiple_test_beads(unique_id: str) -> Generator[list[str], None, None]:
    """Create multiple test beads for batch testing.

    Creates 5 beads with varying priorities.

    Yields:
        List of created bead IDs.
    """
    bead_ids: list[str] = []
    try:
        for i in range(5):
            title = f"{TEST_PREFIX}_batch_{unique_id}_{i}"
            priority = str(i % 5)  # Priorities 0-4
            bead_id = create_test_bead(title, priority=priority)
            bead_ids.append(bead_id)
        BeadService.invalidate_cache()
        yield bead_ids
    finally:
        for bead_id in bead_ids:
            delete_test_bead(bead_id)


@pytest.fixture
def dependent_beads(unique_id: str) -> Generator[tuple[str, str], None, None]:
    """Create two beads with a dependency relationship.

    Yields:
        Tuple of (blocked_bead_id, blocker_bead_id).
    """
    blocker_id = create_test_bead(f"{TEST_PREFIX}_blocker_{unique_id}", priority="1")
    blocked_id = create_test_bead(f"{TEST_PREFIX}_blocked_{unique_id}", priority="2")
    add_dependency(blocked_id, blocker_id)
    BeadService.invalidate_cache()
    yield (blocked_id, blocker_id)
    delete_test_bead(blocked_id)
    delete_test_bead(blocker_id)


@pytest.fixture
def bead_creation_data() -> dict[str, Any]:
    """Provide valid data for bead creation API."""
    return {
        "title": f"{TEST_PREFIX}_api_create_{uuid.uuid4().hex[:8]}",
        "description": "API integration test bead",
        "priority": 3,
        "issue_type": "task",
        "labels": ["test", "api"],
    }


@pytest.fixture
def cleanup_created_beads() -> Generator[list[str], None, None]:
    """Fixture that tracks and cleans up beads created during tests.

    Use this when the test itself creates beads via the API.

    Yields:
        A list to append created bead IDs for cleanup.
    """
    created_ids: list[str] = []
    yield created_ids
    for bead_id in created_ids:
        delete_test_bead(bead_id)
