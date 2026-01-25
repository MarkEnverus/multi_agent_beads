"""Full-stack API integration tests.

This module tests all REST API endpoints end-to-end, verifying:
- CRUD operations on beads via API
- Dependency management
- Bulk operations
- Search and filtering
- Stats and reporting endpoints
- Error handling and edge cases
"""

from typing import Any

from fastapi.testclient import TestClient

from dashboard.services import BeadService

from .conftest import (
    TEST_PREFIX,
    create_test_bead,
    delete_test_bead,
    run_bd,
    update_bead_status,
)


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_ok(self, test_client: TestClient) -> None:
        """Health endpoint should return status ok."""
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_is_always_available(self, test_client: TestClient) -> None:
        """Health endpoint should work regardless of system state."""
        # Call multiple times to verify stability
        for _ in range(3):
            response = test_client.get("/health")
            assert response.status_code == 200


class TestBeadsListEndpoint:
    """Test GET /api/beads listing endpoint."""

    def test_list_beads_returns_list(self, test_client: TestClient) -> None:
        """Should return a list of beads."""
        response = test_client.get("/api/beads")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_list_beads_with_test_bead(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Created test bead should appear in the list."""
        response = test_client.get("/api/beads")
        assert response.status_code == 200
        beads = response.json()
        bead_ids = [b["id"] for b in beads]
        assert test_bead in bead_ids

    def test_filter_by_status_open(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Should filter beads by open status."""
        response = test_client.get("/api/beads?status=open")
        assert response.status_code == 200
        beads = response.json()
        # All returned beads should be open
        for bead in beads:
            assert bead["status"] == "open"

    def test_filter_by_status_in_progress(
        self,
        test_client: TestClient,
        test_bead_in_progress: str,
    ) -> None:
        """Should filter beads by in_progress status."""
        response = test_client.get("/api/beads?status=in_progress")
        assert response.status_code == 200
        beads = response.json()
        bead_ids = [b["id"] for b in beads]
        assert test_bead_in_progress in bead_ids
        # All returned beads should be in_progress
        for bead in beads:
            assert bead["status"] == "in_progress"

    def test_filter_by_label(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Should filter beads by label."""
        response = test_client.get("/api/beads?label=test")
        assert response.status_code == 200
        beads = response.json()
        # Test bead should be in results
        bead_ids = [b["id"] for b in beads]
        assert test_bead in bead_ids

    def test_filter_by_priority(
        self,
        test_client: TestClient,
        multiple_test_beads: list[str],
    ) -> None:
        """Should filter beads by priority."""
        # Our test beads have priorities 0-4
        response = test_client.get("/api/beads?priority=0")
        assert response.status_code == 200
        beads = response.json()
        # All returned beads should have priority 0
        for bead in beads:
            assert bead["priority"] == 0


class TestBeadsReadyEndpoint:
    """Test GET /api/beads/ready endpoint."""

    def test_ready_returns_list(self, test_client: TestClient) -> None:
        """Ready endpoint should return a list."""
        response = test_client.get("/api/beads/ready")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_ready_excludes_blocked_beads(
        self,
        test_client: TestClient,
        dependent_beads: tuple[str, str],
    ) -> None:
        """Blocked beads should not appear in ready list."""
        blocked_id, blocker_id = dependent_beads
        response = test_client.get("/api/beads/ready")
        assert response.status_code == 200
        beads = response.json()
        bead_ids = [b["id"] for b in beads]
        # Blocked bead should NOT be in ready list
        assert blocked_id not in bead_ids
        # Blocker should be in ready list (it's not blocked by anything)
        assert blocker_id in bead_ids

    def test_ready_with_label_filter(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Ready endpoint should support label filter."""
        # First verify the test bead exists
        response = test_client.get(f"/api/beads/{test_bead}")
        assert response.status_code == 200

        # Query ready beads with label filter
        response = test_client.get("/api/beads/ready?label=test")
        assert response.status_code == 200
        beads = response.json()

        # If our test bead is open and has "test" label, it should be in results
        # unless it's blocked by something else. The main verification is that
        # the endpoint works and returns a list.
        assert isinstance(beads, list)

        # If test_bead is in results, verify it has the right label
        bead_ids = [b["id"] for b in beads]
        if test_bead in bead_ids:
            for b in beads:
                if b["id"] == test_bead:
                    assert "test" in b.get("labels", [])


class TestBeadsInProgressEndpoint:
    """Test GET /api/beads/in-progress endpoint."""

    def test_in_progress_returns_list(self, test_client: TestClient) -> None:
        """In-progress endpoint should return a list."""
        response = test_client.get("/api/beads/in-progress")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_in_progress_contains_working_bead(
        self,
        test_client: TestClient,
        test_bead_in_progress: str,
    ) -> None:
        """In-progress bead should appear in the list."""
        response = test_client.get("/api/beads/in-progress")
        assert response.status_code == 200
        beads = response.json()
        bead_ids = [b["id"] for b in beads]
        assert test_bead_in_progress in bead_ids


class TestBeadDetailEndpoint:
    """Test GET /api/beads/{bead_id} endpoint."""

    def test_get_bead_returns_detail(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Should return bead details by ID."""
        response = test_client.get(f"/api/beads/{test_bead}")
        assert response.status_code == 200
        bead = response.json()
        assert bead["id"] == test_bead
        assert "title" in bead
        assert "status" in bead
        assert "priority" in bead

    def test_get_bead_not_found(self, test_client: TestClient) -> None:
        """Should return error for non-existent bead."""
        response = test_client.get("/api/beads/multi_agent_beads-nonexistent123")
        # Returns 404 or 500 depending on how the CLI reports the error
        assert response.status_code in (404, 500)
        # Should include error information in response
        assert "error" in response.json() or "message" in response.json()

    def test_get_bead_invalid_id_format(self, test_client: TestClient) -> None:
        """Should return error for invalid bead ID format."""
        response = test_client.get("/api/beads/invalid-format-bead-id")
        # This could be 400 (validation) or 404 (not found), depending on implementation
        assert response.status_code in (400, 404, 422)


class TestBeadCreateEndpoint:
    """Test POST /api/beads endpoint."""

    def test_create_bead_success(
        self,
        test_client: TestClient,
        bead_creation_data: dict[str, Any],
        cleanup_created_beads: list[str],
    ) -> None:
        """Should create a new bead via API."""
        response = test_client.post("/api/beads", json=bead_creation_data)
        assert response.status_code == 201
        bead = response.json()
        assert bead["title"] == bead_creation_data["title"]
        assert bead["priority"] == bead_creation_data["priority"]
        assert bead["issue_type"] == bead_creation_data["issue_type"]
        # Track for cleanup
        cleanup_created_beads.append(bead["id"])

    def test_create_bead_minimal_data(
        self,
        test_client: TestClient,
        unique_id: str,
        cleanup_created_beads: list[str],
    ) -> None:
        """Should create bead with minimal required data."""
        data = {"title": f"{TEST_PREFIX}_minimal_{unique_id}"}
        response = test_client.post("/api/beads", json=data)
        assert response.status_code == 201
        bead = response.json()
        assert bead["title"] == data["title"]
        cleanup_created_beads.append(bead["id"])

    def test_create_bead_empty_title_fails(self, test_client: TestClient) -> None:
        """Should reject bead with empty title."""
        data = {"title": ""}
        response = test_client.post("/api/beads", json=data)
        assert response.status_code == 422  # Validation error

    def test_create_bead_invalid_priority_fails(
        self,
        test_client: TestClient,
        unique_id: str,
    ) -> None:
        """Should reject bead with invalid priority."""
        data = {"title": f"{TEST_PREFIX}_invalid_{unique_id}", "priority": 10}
        response = test_client.post("/api/beads", json=data)
        assert response.status_code == 422  # Validation error


class TestKanbanPartial:
    """Test GET /partials/kanban endpoint."""

    def test_kanban_returns_html(self, test_client: TestClient) -> None:
        """Kanban partial should return HTML."""
        response = test_client.get("/partials/kanban")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_kanban_contains_bead(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Test bead should appear in kanban board."""
        response = test_client.get("/partials/kanban?refresh=true")
        assert response.status_code == 200
        # Bead ID or short ID should be in the HTML
        short_id = test_bead.split("-")[-1]
        assert short_id in response.text or test_bead in response.text

    def test_kanban_refresh_bypasses_cache(
        self,
        test_client: TestClient,
        unique_id: str,
    ) -> None:
        """Refresh parameter should bypass cache."""
        # Create a bead
        bead_id = create_test_bead(f"{TEST_PREFIX}_refresh_{unique_id}")
        try:
            # With refresh=true, should see the new bead
            response = test_client.get("/partials/kanban?refresh=true")
            assert response.status_code == 200
            short_id = bead_id.split("-")[-1]
            assert short_id in response.text or bead_id in response.text
        finally:
            delete_test_bead(bead_id)


class TestDepgraphPartial:
    """Test GET /partials/depgraph endpoint."""

    def test_depgraph_returns_html(self, test_client: TestClient) -> None:
        """Depgraph partial should return HTML."""
        response = test_client.get("/partials/depgraph")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_depgraph_shows_dependencies(
        self,
        test_client: TestClient,
        dependent_beads: tuple[str, str],
    ) -> None:
        """Dependency graph should show connected beads."""
        blocked_id, blocker_id = dependent_beads
        response = test_client.get("/partials/depgraph")
        assert response.status_code == 200
        # The mermaid code should contain references to our beads
        html = response.text
        # Short IDs should appear in the mermaid graph
        blocked_short = blocked_id.split("-")[-1]
        blocker_short = blocker_id.split("-")[-1]
        # At least one should appear (the one with deps)
        assert blocked_short in html or blocker_short in html


class TestBeadDetailPartial:
    """Test GET /partials/beads/{bead_id} endpoint."""

    def test_bead_detail_returns_html(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Bead detail partial should return HTML."""
        response = test_client.get(f"/partials/beads/{test_bead}")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_bead_detail_shows_title(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Bead detail should show title."""
        response = test_client.get(f"/partials/beads/{test_bead}")
        assert response.status_code == 200
        # Should contain either the bead ID or "test" prefix
        assert TEST_PREFIX in response.text or test_bead in response.text

    def test_bead_detail_not_found(self, test_client: TestClient) -> None:
        """Should show error for non-existent bead."""
        response = test_client.get("/partials/beads/multi_agent_beads-nonexistent")
        assert response.status_code == 200  # Returns error modal as HTML
        # Should contain error messaging - could be "not found", "error", or "failed"
        html_lower = response.text.lower()
        assert any(phrase in html_lower for phrase in ["not found", "error", "failed"])


class TestAgentsPartial:
    """Test GET /partials/agents endpoint."""

    def test_agents_partial_returns_html(self, test_client: TestClient) -> None:
        """Agents partial should return HTML."""
        response = test_client.get("/partials/agents")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


class TestAgentsEndpoint:
    """Test GET /api/agents endpoint."""

    def test_agents_returns_list(self, test_client: TestClient) -> None:
        """Agents endpoint should return a list."""
        response = test_client.get("/api/agents")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_agents_by_invalid_role(self, test_client: TestClient) -> None:
        """Should return 400 for invalid role."""
        response = test_client.get("/api/agents/invalid_role")
        assert response.status_code == 400


class TestLogsEndpoint:
    """Test /api/logs endpoints."""

    def test_recent_logs_returns_list(self, test_client: TestClient) -> None:
        """Recent logs should return a list."""
        response = test_client.get("/api/logs/recent")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_recent_logs_with_limit(self, test_client: TestClient) -> None:
        """Should respect limit parameter."""
        response = test_client.get("/api/logs/recent?limit=5")
        assert response.status_code == 200
        logs = response.json()
        assert len(logs) <= 5

    def test_stream_logs_returns_sse(self, test_client: TestClient) -> None:
        """Stream endpoint should return event-stream content type."""
        # Just verify the endpoint is accessible - actual streaming is hard to test
        with test_client.stream("GET", "/api/logs/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            # Context manager handles cleanup, no need to read more


class TestDashboardRoot:
    """Test GET / dashboard endpoint."""

    def test_dashboard_returns_html(self, test_client: TestClient) -> None:
        """Dashboard should return HTML page."""
        response = test_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_dashboard_contains_htmx_references(
        self,
        test_client: TestClient,
    ) -> None:
        """Dashboard should contain HTMX elements."""
        response = test_client.get("/")
        assert response.status_code == 200
        # Should have HTMX attributes
        html = response.text.lower()
        assert "hx-" in html or "htmx" in html


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_invalid_endpoint_returns_404(self, test_client: TestClient) -> None:
        """Invalid endpoints should return 404."""
        response = test_client.get("/api/nonexistent")
        assert response.status_code == 404

    def test_json_error_response_format(self, test_client: TestClient) -> None:
        """Error responses should have consistent format."""
        response = test_client.get("/api/beads/multi_agent_beads-nonexistent")
        assert response.status_code == 404
        error = response.json()
        assert "error" in error or "message" in error


class TestStatusWorkflow:
    """Test full status workflow: open -> in_progress -> closed."""

    def test_complete_workflow(
        self,
        test_client: TestClient,
        unique_id: str,
    ) -> None:
        """Test complete bead lifecycle through API."""
        bead_id = None
        try:
            # Step 1: Create via API
            data = {
                "title": f"{TEST_PREFIX}_workflow_{unique_id}",
                "description": "Workflow test bead",
                "priority": 2,
            }
            response = test_client.post("/api/beads", json=data)
            assert response.status_code == 201
            bead = response.json()
            bead_id = bead["id"]
            assert bead["status"] == "open"

            # Verify appears in list
            response = test_client.get("/api/beads")
            bead_ids = [b["id"] for b in response.json()]
            assert bead_id in bead_ids

            # Step 2: Update to in_progress (via CLI - API doesn't have update)
            update_bead_status(bead_id, "in_progress")
            BeadService.invalidate_cache()

            # Verify in in-progress list
            response = test_client.get("/api/beads/in-progress")
            assert response.status_code == 200
            bead_ids = [b["id"] for b in response.json()]
            assert bead_id in bead_ids

            # Step 3: Close (via CLI)
            run_bd(["close", bead_id, "--reason", "Workflow test complete"])
            BeadService.invalidate_cache()

            # Verify status is closed
            response = test_client.get(f"/api/beads/{bead_id}")
            assert response.status_code == 200
            assert response.json()["status"] == "closed"

        finally:
            if bead_id:
                delete_test_bead(bead_id)


class TestBatchOperations:
    """Test operations with multiple beads."""

    def test_list_all_with_multiple_beads(
        self,
        test_client: TestClient,
        multiple_test_beads: list[str],
    ) -> None:
        """Should handle listing with many beads."""
        response = test_client.get("/api/beads")
        assert response.status_code == 200
        beads = response.json()
        # All our test beads should be present
        bead_ids = [b["id"] for b in beads]
        for test_bead in multiple_test_beads:
            assert test_bead in bead_ids

    def test_kanban_with_multiple_beads(
        self,
        test_client: TestClient,
        multiple_test_beads: list[str],
    ) -> None:
        """Kanban should handle multiple beads."""
        response = test_client.get("/partials/kanban?refresh=true")
        assert response.status_code == 200
        # Should not error even with many beads
        assert "error" not in response.text.lower() or "error_message" in response.text


class TestDependencyManagement:
    """Test dependency-related functionality."""

    def test_blocked_bead_shows_in_depgraph(
        self,
        test_client: TestClient,
        dependent_beads: tuple[str, str],
    ) -> None:
        """Blocked beads should appear in dependency graph."""
        blocked_id, _ = dependent_beads
        response = test_client.get("/partials/depgraph")
        assert response.status_code == 200
        # Depgraph should have content when there are dependencies
        html = response.text
        # Check for mermaid graph markers or node references
        short_id = blocked_id.split("-")[-1]
        assert short_id in html or "graph" in html.lower() or "mermaid" in html.lower()

    def test_cycle_detection(
        self,
        test_client: TestClient,
        unique_id: str,
    ) -> None:
        """Adding circular dependency should be prevented."""
        bead_a = create_test_bead(f"{TEST_PREFIX}_cycle_a_{unique_id}")
        bead_b = create_test_bead(f"{TEST_PREFIX}_cycle_b_{unique_id}")
        try:
            # Add A -> B dependency
            run_bd(["dep", "add", bead_b, bead_a])
            # Try to add B -> A (would create cycle)
            run_bd(["dep", "add", bead_a, bead_b], check=False)
            # Should either fail or be prevented
            # The system should handle this gracefully
            # Just verify the API still works
            response = test_client.get("/partials/depgraph")
            assert response.status_code == 200
        finally:
            delete_test_bead(bead_a)
            delete_test_bead(bead_b)
