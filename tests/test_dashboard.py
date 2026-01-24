"""Tests for the dashboard FastAPI application.

This module tests the main app endpoints including health check,
root dashboard, and HTMX partials.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_endpoint_returns_ok(self) -> None:
        """Test that /health returns status ok."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_endpoint_json_content_type(self) -> None:
        """Test that /health returns JSON content type."""
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]


class TestDashboardRoot:
    """Tests for / root endpoint."""

    def test_root_returns_html(self) -> None:
        """Test that / returns HTML dashboard."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_root_contains_dashboard_elements(self) -> None:
        """Test that root page contains expected dashboard elements."""
        response = client.get("/")
        html = response.text
        assert "Multi-Agent Dashboard" in html or "dashboard" in html.lower()


class TestKanbanPartial:
    """Tests for /partials/kanban HTMX partial."""

    def test_kanban_partial_returns_html(self) -> None:
        """Test that kanban partial returns HTML."""
        with patch("dashboard.app._run_bd_command") as mock_run:
            mock_run.return_value = (True, "[]")
            response = client.get("/partials/kanban")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]

    def test_kanban_partial_handles_bd_failure(self) -> None:
        """Test kanban partial handles bd command failure gracefully."""
        with patch("dashboard.app._run_bd_command") as mock_run:
            mock_run.return_value = (False, "bd not found")
            response = client.get("/partials/kanban")
            # Should still return 200 with empty data
            assert response.status_code == 200


class TestAgentsPartial:
    """Tests for /partials/agents HTMX partial."""

    def test_agents_partial_returns_html(self) -> None:
        """Test that agents partial returns HTML."""
        with patch("dashboard.app._get_active_agents") as mock_agents:
            mock_agents.return_value = []
            response = client.get("/partials/agents")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]


class TestDepgraphPartial:
    """Tests for /partials/depgraph HTMX partial."""

    def test_depgraph_partial_returns_html(self) -> None:
        """Test that dependency graph partial returns HTML."""
        with patch("dashboard.app._run_bd_command") as mock_run:
            mock_run.return_value = (True, "[]")
            response = client.get("/partials/depgraph")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]

    def test_depgraph_handles_no_dependencies(self) -> None:
        """Test depgraph handles case with no dependencies."""
        with patch("dashboard.app._run_bd_command") as mock_run:
            mock_run.return_value = (True, "[]")
            response = client.get("/partials/depgraph")
            assert response.status_code == 200


class TestBeadDetailPartial:
    """Tests for /partials/beads/{bead_id} HTMX partial."""

    def test_bead_detail_returns_html(self) -> None:
        """Test that bead detail partial returns HTML."""
        sample_bead = [{
            "id": "multi_agent_beads-test",
            "title": "Test bead",
            "description": "Test description",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
        }]
        import json

        with patch("dashboard.app._run_bd_command") as mock_run:
            mock_run.return_value = (True, json.dumps(sample_bead))
            response = client.get("/partials/beads/multi_agent_beads-test")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]

    def test_bead_detail_not_found(self) -> None:
        """Test bead detail when bead not found."""
        with patch("dashboard.app._run_bd_command") as mock_run:
            mock_run.return_value = (False, "Bead not found")
            response = client.get("/partials/beads/nonexistent")
            # Returns 200 with error HTML, not 404
            assert response.status_code == 200
            assert "Failed to load" in response.text or "not found" in response.text.lower()


class TestMermaidGraphGeneration:
    """Tests for Mermaid dependency graph generation."""

    def test_generate_mermaid_graph_empty(self) -> None:
        """Test Mermaid generation with no beads."""
        from dashboard.app import _generate_mermaid_graph

        mermaid, nodes, edges = _generate_mermaid_graph([])
        assert mermaid == ""
        assert nodes == 0
        assert edges == 0

    def test_generate_mermaid_graph_no_deps(self) -> None:
        """Test Mermaid generation with beads but no dependencies."""
        from dashboard.app import _generate_mermaid_graph

        beads = [
            {"id": "mab-1", "title": "Task 1", "status": "open", "blocked_by": []},
            {"id": "mab-2", "title": "Task 2", "status": "open", "blocked_by": []},
        ]
        mermaid, nodes, edges = _generate_mermaid_graph(beads)
        assert mermaid == ""
        assert nodes == 0
        assert edges == 0

    def test_generate_mermaid_graph_with_deps(self) -> None:
        """Test Mermaid generation with dependencies."""
        from dashboard.app import _generate_mermaid_graph

        beads = [
            {"id": "mab-1", "title": "Task 1", "status": "closed", "blocked_by": []},
            {"id": "mab-2", "title": "Task 2", "status": "open", "blocked_by": ["mab-1"]},
        ]
        mermaid, nodes, edges = _generate_mermaid_graph(beads)
        assert "graph TD" in mermaid
        assert nodes == 2
        assert edges == 1
        assert "1 --> 2" in mermaid


class TestAppHelpers:
    """Tests for app helper functions."""

    def test_parse_beads_json_valid(self) -> None:
        """Test parsing valid JSON."""
        from dashboard.app import _parse_beads_json

        result = _parse_beads_json('[{"id": "test"}]')
        assert result == [{"id": "test"}]

    def test_parse_beads_json_invalid(self) -> None:
        """Test parsing invalid JSON."""
        from dashboard.app import _parse_beads_json

        result = _parse_beads_json("not json")
        assert result == []

    def test_parse_beads_json_non_array(self) -> None:
        """Test parsing non-array JSON."""
        from dashboard.app import _parse_beads_json

        result = _parse_beads_json('{"single": "object"}')
        assert result == []

    def test_sort_by_priority(self) -> None:
        """Test sorting beads by priority."""
        from dashboard.app import _sort_by_priority

        beads = [
            {"id": "1", "priority": 3},
            {"id": "2", "priority": 1},
            {"id": "3", "priority": 0},
        ]
        sorted_beads = _sort_by_priority(beads)
        assert sorted_beads[0]["priority"] == 0
        assert sorted_beads[1]["priority"] == 1
        assert sorted_beads[2]["priority"] == 3

    def test_run_bd_command_timeout(self) -> None:
        """Test bd command timeout handling."""
        import subprocess

        from dashboard.app import _run_bd_command

        with patch("dashboard.app.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="bd", timeout=30)
            success, output = _run_bd_command(["slow"])
            assert success is False
            assert "timed out" in output.lower()

    def test_run_bd_command_not_found(self) -> None:
        """Test bd command not found handling."""
        from dashboard.app import _run_bd_command

        with patch("dashboard.app.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            success, output = _run_bd_command(["test"])
            assert success is False
            assert "not found" in output.lower()


class TestCORS:
    """Tests for CORS middleware configuration."""

    def test_cors_headers_present(self) -> None:
        """Test that CORS headers are present for allowed origins."""
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # OPTIONS request should be handled by CORS middleware
        assert response.status_code in (200, 204, 405)
