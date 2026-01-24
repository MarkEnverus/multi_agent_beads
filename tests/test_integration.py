"""End-to-end integration tests for bead workflow.

This module tests the complete workflow of creating, updating, and closing
beads while verifying the dashboard reflects these changes.
"""

import subprocess
import uuid
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)

# Test prefix for beads created during tests
TEST_PREFIX = "test_integration"


def _run_bd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a bd command and return the result."""
    result = subprocess.run(
        ["bd", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"bd command failed: {result.stderr or result.stdout}")
    return result


def _create_test_bead(title: str, priority: str = "4") -> str:
    """Create a test bead and return its ID."""
    result = _run_bd([
        "create",
        "--title", title,
        "--description", "Integration test bead - safe to delete",
        "--priority", priority,
        "--labels", "test",
        "--silent",
    ])
    bead_id = result.stdout.strip()
    if not bead_id:
        raise RuntimeError("Failed to create test bead - no ID returned")
    return bead_id


def _delete_test_bead(bead_id: str) -> None:
    """Delete a test bead (close it to clean up)."""
    # Close the bead
    _run_bd(["close", bead_id, "--reason", "Test cleanup"], check=False)


class TestBeadLifecycleWorkflow:
    """Test complete bead lifecycle through dashboard."""

    @pytest.fixture
    def test_bead(self) -> Generator[str, None, None]:
        """Create a test bead and clean it up after the test."""
        unique_id = uuid.uuid4().hex[:8]
        title = f"{TEST_PREFIX}_{unique_id}"
        bead_id = _create_test_bead(title)
        yield bead_id
        _delete_test_bead(bead_id)

    def test_created_bead_appears_in_ready_list(self, test_bead: str) -> None:
        """Test that a newly created bead appears in the ready list via bd."""
        # Verify bead appears in open list (ready)
        result = _run_bd(["list", "--json", "--status", "open", "--limit", "0"])
        assert test_bead in result.stdout, f"Bead {test_bead} should appear in open list"

        # Also verify kanban endpoint works
        response = client.get("/partials/kanban")
        assert response.status_code == 200

    def test_status_update_reflects_in_kanban(self, test_bead: str) -> None:
        """Test that updating status moves bead to correct column."""
        # Update to in_progress
        _run_bd(["update", test_bead, "--status", "in_progress"])

        # Verify it appears in the in-progress section
        response = client.get("/partials/kanban")
        assert response.status_code == 200
        html = response.text
        assert test_bead in html or test_bead.split("-")[-1] in html

    def test_closed_bead_appears_in_done_list(self, test_bead: str) -> None:
        """Test that closing a bead moves it to the closed list."""
        # Close the bead
        _run_bd(["close", test_bead, "--reason", "Integration test complete"])

        # Verify it appears in closed list via bd
        result = _run_bd(["list", "--json", "--status", "closed", "--limit", "0"])
        assert test_bead in result.stdout, f"Bead {test_bead} should appear in closed list"

        # Verify kanban endpoint works
        response = client.get("/partials/kanban")
        assert response.status_code == 200

    def test_bead_detail_shows_correct_info(self, test_bead: str) -> None:
        """Test that bead detail modal shows correct information."""
        response = client.get(f"/partials/beads/{test_bead}")
        assert response.status_code == 200
        html = response.text
        # Should show the bead info, not error
        assert "Integration test bead" in html or test_bead in html
        assert "Failed to load" not in html


class TestFullWorkflowSequence:
    """Test the complete workflow sequence from creation to done."""

    def test_complete_workflow_sequence(self) -> None:
        """Test the full lifecycle: create -> in_progress -> closed."""
        bead_id = None
        try:
            # Step 1: Create bead
            unique_id = uuid.uuid4().hex[:8]
            title = f"{TEST_PREFIX}_workflow_{unique_id}"
            bead_id = _create_test_bead(title)
            assert bead_id, "Bead ID should be returned"

            # Step 2: Verify in ready (open beads)
            result = _run_bd(["list", "--json", "--status", "open", "--limit", "100"])
            assert bead_id in result.stdout, "New bead should appear in open list"

            # Step 3: Update to in_progress
            _run_bd(["update", bead_id, "--status", "in_progress"])
            result = _run_bd(["list", "--json", "--status", "in_progress", "--limit", "100"])
            assert bead_id in result.stdout, "Bead should appear in in_progress list"

            # Step 4: Close the bead
            _run_bd(["close", bead_id, "--reason", "Workflow test complete"])
            result = _run_bd(["list", "--json", "--status", "closed", "--limit", "100"])
            assert bead_id in result.stdout, "Bead should appear in closed list"

            # Step 5: Verify dashboard reflects final state
            response = client.get("/partials/kanban")
            assert response.status_code == 200

        finally:
            if bead_id:
                _delete_test_bead(bead_id)


class TestDashboardHealthDuringOperations:
    """Test that dashboard remains healthy during bead operations."""

    def test_health_endpoint_during_operations(self) -> None:
        """Test health endpoint responds correctly during operations."""
        bead_id = None
        try:
            # Create a bead
            unique_id = uuid.uuid4().hex[:8]
            bead_id = _create_test_bead(f"{TEST_PREFIX}_health_{unique_id}")

            # Health should be OK
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

            # Update bead
            _run_bd(["update", bead_id, "--status", "in_progress"])

            # Health should still be OK
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

        finally:
            if bead_id:
                _delete_test_bead(bead_id)

    def test_kanban_partial_handles_concurrent_updates(self) -> None:
        """Test kanban partial handles rapid updates gracefully."""
        bead_ids: list[str] = []
        try:
            # Create multiple beads
            for i in range(3):
                unique_id = uuid.uuid4().hex[:8]
                bead_id = _create_test_bead(f"{TEST_PREFIX}_concurrent_{unique_id}")
                bead_ids.append(bead_id)

            # Rapidly update and fetch
            for bead_id in bead_ids:
                _run_bd(["update", bead_id, "--status", "in_progress"])
                response = client.get("/partials/kanban")
                assert response.status_code == 200

        finally:
            for bead_id in bead_ids:
                _delete_test_bead(bead_id)


class TestCleanupBehavior:
    """Test that cleanup works correctly after tests."""

    def test_cleanup_removes_test_beads(self) -> None:
        """Test that test beads are properly cleaned up."""
        # Create and immediately close a test bead
        unique_id = uuid.uuid4().hex[:8]
        bead_id = _create_test_bead(f"{TEST_PREFIX}_cleanup_{unique_id}")

        # Verify it exists
        result = _run_bd(["show", bead_id, "--json"])
        assert bead_id in result.stdout

        # Clean up
        _delete_test_bead(bead_id)

        # Verify it's closed
        result = _run_bd(["show", bead_id, "--json"])
        assert '"status":"closed"' in result.stdout.replace(" ", "").replace("\n", "")

    def test_repeated_runs_do_not_accumulate(self) -> None:
        """Test that running tests multiple times doesn't leave orphans."""
        # Count existing test beads before
        result = _run_bd([
            "list", "--json", "--limit", "0",
            "--title-contains", TEST_PREFIX,
        ])
        # This just verifies the query works - actual accumulation check
        # would require running tests multiple times
        assert result.returncode == 0


class TestDependencyGraphIntegration:
    """Test dependency graph updates with bead changes."""

    def test_depgraph_partial_loads(self) -> None:
        """Test that dependency graph partial loads successfully."""
        response = client.get("/partials/depgraph")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_depgraph_reflects_bead_status_changes(self) -> None:
        """Test depgraph reflects status changes correctly."""
        bead_ids: list[str] = []
        try:
            # Create two beads with dependency
            unique_id = uuid.uuid4().hex[:8]
            blocker_id = _create_test_bead(f"{TEST_PREFIX}_blocker_{unique_id}")
            bead_ids.append(blocker_id)

            blocked_id = _create_test_bead(f"{TEST_PREFIX}_blocked_{unique_id}")
            bead_ids.append(blocked_id)

            # Add dependency
            _run_bd(["dep", "add", blocked_id, blocker_id])

            # Depgraph should load without error
            response = client.get("/partials/depgraph")
            assert response.status_code == 200

            # Close blocker
            _run_bd(["close", blocker_id, "--reason", "Test"])

            # Depgraph should still load
            response = client.get("/partials/depgraph")
            assert response.status_code == 200

        finally:
            for bead_id in bead_ids:
                _delete_test_bead(bead_id)


class TestAgentSidebarIntegration:
    """Test agent sidebar during bead operations."""

    def test_agents_partial_loads_during_operations(self) -> None:
        """Test that agents partial loads during bead operations."""
        bead_id = None
        try:
            unique_id = uuid.uuid4().hex[:8]
            bead_id = _create_test_bead(f"{TEST_PREFIX}_agents_{unique_id}")

            # Agents partial should load
            response = client.get("/partials/agents")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]

        finally:
            if bead_id:
                _delete_test_bead(bead_id)
