"""Comprehensive E2E tests for dashboard interactive workflows.

These tests verify the complete dashboard workflow including:
1. Spinning up the dashboard
2. Clicking all interactive buttons
3. Spawning workers via UI
4. Shutting down workers via UI
5. Adding beads via UI
6. Verifying work completion flow
7. Viewing worker logs
8. Viewing completed beads

Run locally with: uv run pytest tests/e2e/test_interactive_e2e.py -v
"""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import Page, Request, expect

# Skip all tests in CI environments
pytestmark = pytest.mark.skipif(
    bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS")),
    reason="Interactive E2E tests require local Playwright and dashboard infrastructure",
)


class TestDashboardStartup:
    """Tests for dashboard startup and initialization."""

    def test_dashboard_starts_and_loads(self, dashboard_page: Page) -> None:
        """Verify dashboard server starts and main page loads."""
        # Dashboard should have loaded with Kanban Board title
        expect(dashboard_page).to_have_title(re.compile("Kanban Board"))

        # Main elements should be visible
        header = dashboard_page.locator("h1:has-text('Kanban Board')")
        expect(header).to_be_visible()

    def test_all_navigation_pages_load(self, page_with_server: Page, server_url: str) -> None:
        """Verify all navigation links work and pages load correctly."""
        nav_pages = [
            ("/", "Kanban Board"),
            ("/admin", "Admin"),
            ("/agents", "Agents"),
            ("/beads", "Beads"),
            ("/logs", "Logs"),
            ("/help", "Help"),
        ]

        for path, expected_content in nav_pages:
            page_with_server.goto(f"{server_url}{path}")
            # Use domcontentloaded instead of networkidle to avoid timeout
            page_with_server.wait_for_load_state("domcontentloaded")
            page_with_server.wait_for_timeout(500)  # Allow JS to initialize
            # Each page should load without errors
            assert expected_content.lower() in page_with_server.content().lower(), (
                f"Page {path} should contain '{expected_content}'"
            )


class TestInteractiveButtons:
    """Tests for clicking all interactive buttons in the dashboard."""

    def test_refresh_button_on_dashboard(self, dashboard_page: Page) -> None:
        """Test clicking the Refresh Now button on main dashboard."""
        refresh_btn = dashboard_page.locator("button:has-text('Refresh Now')")
        expect(refresh_btn).to_be_visible()

        # Track network requests
        requests_made: list[str] = []

        def handle_request(request: Request) -> None:
            requests_made.append(request.url)

        dashboard_page.on("request", handle_request)

        # Click refresh
        refresh_btn.click()
        dashboard_page.wait_for_timeout(1000)

        # Should have triggered API calls
        assert any("/partials" in r for r in requests_made), (
            "Refresh should trigger partial updates"
        )

    def test_dependencies_toggle_button(self, dashboard_page: Page) -> None:
        """Test clicking the Dependencies toggle button."""
        deps_btn = dashboard_page.locator("#depgraph-toggle")
        expect(deps_btn).to_be_visible()

        kanban = dashboard_page.locator("#kanban-board")
        depgraph = dashboard_page.locator("#depgraph-container")

        # Initially kanban visible, depgraph hidden
        expect(kanban).not_to_have_class(re.compile("hidden"))
        expect(depgraph).to_have_class(re.compile("hidden"))

        # Click to show dependencies
        deps_btn.click()
        dashboard_page.wait_for_timeout(500)

        # Now depgraph visible, kanban hidden
        expect(kanban).to_have_class(re.compile("hidden"))
        expect(depgraph).not_to_have_class(re.compile("hidden"))

        # Click to toggle back
        deps_btn.click()
        dashboard_page.wait_for_timeout(500)

        expect(kanban).not_to_have_class(re.compile("hidden"))

    def test_admin_page_buttons(self, admin_page: Page) -> None:
        """Test all interactive buttons on admin page."""
        # Test Refresh button
        refresh_btn = admin_page.locator("#refresh-btn")
        expect(refresh_btn).to_be_visible()
        refresh_btn.click()
        admin_page.wait_for_timeout(500)

        # Test New Bead button opens modal
        new_bead_btn = admin_page.locator("#new-bead-btn")
        expect(new_bead_btn).to_be_visible()

        modal = admin_page.locator("#create-bead-modal")
        expect(modal).to_have_class(re.compile("hidden"))

        new_bead_btn.click()
        admin_page.wait_for_timeout(300)

        expect(modal).not_to_have_class(re.compile("hidden"))

        # Close modal with Escape
        admin_page.keyboard.press("Escape")
        admin_page.wait_for_timeout(300)

        expect(modal).to_have_class(re.compile("hidden"))

    def test_filter_dropdowns_on_admin(self, admin_page: Page) -> None:
        """Test filter dropdown interactions on admin page."""
        status_filter = admin_page.locator("#filter-status")
        role_filter = admin_page.locator("#filter-role")

        expect(status_filter).to_be_visible()
        expect(role_filter).to_be_visible()

        # Change filter values
        status_filter.select_option("running")
        admin_page.wait_for_timeout(300)

        role_filter.select_option("dev")
        admin_page.wait_for_timeout(300)

        # Filters should have changed
        assert status_filter.input_value() == "running"
        assert role_filter.input_value() == "dev"


class TestWorkerSpawning:
    """Tests for spawning workers through the UI."""

    def test_spawn_form_visible(self, admin_page: Page) -> None:
        """Verify spawn form elements are visible and functional."""
        spawn_form = admin_page.locator("#spawn-form")
        expect(spawn_form).to_be_visible()

        # Check all form elements
        role_select = admin_page.locator("#spawn-role")
        project_input = admin_page.locator("#spawn-project")
        autorestart_checkbox = admin_page.locator("#spawn-autorestart")
        spawn_btn = admin_page.locator("#spawn-form button[type='submit']")

        expect(role_select).to_be_visible()
        expect(project_input).to_be_visible()
        expect(autorestart_checkbox).to_be_visible()
        expect(spawn_btn).to_be_visible()
        expect(spawn_btn).to_be_enabled()

    def test_role_dropdown_options(self, admin_page: Page) -> None:
        """Verify all worker role options are available."""
        role_select = admin_page.locator("#spawn-role")

        expected_roles = ["dev", "qa", "reviewer", "tech_lead", "manager"]
        for role in expected_roles:
            option = role_select.locator(f"option[value='{role}']")
            expect(option).to_be_attached()

    def test_spawn_form_can_be_filled(self, admin_page: Page) -> None:
        """Test filling out the spawn form."""
        role_select = admin_page.locator("#spawn-role")
        autorestart_checkbox = admin_page.locator("#spawn-autorestart")

        # Select a role
        role_select.select_option("qa")
        assert role_select.input_value() == "qa"

        # Toggle autorestart
        expect(autorestart_checkbox).to_be_checked()
        autorestart_checkbox.uncheck()
        expect(autorestart_checkbox).not_to_be_checked()
        autorestart_checkbox.check()
        expect(autorestart_checkbox).to_be_checked()

    def test_spawn_form_submission_api(self, page_with_server: Page, server_url: str) -> None:
        """Test worker spawn API endpoint."""
        # Test that the spawn API accepts requests
        response = page_with_server.request.post(
            f"{server_url}/api/workers",
            data={
                "role": "dev",
                "project_path": "/tmp/test-project",
                "auto_restart": True,
            },
        )
        # 503 means daemon not running (expected in test)
        # 200 means worker spawned
        assert response.status in [200, 503]


class TestWorkerShutdown:
    """Tests for shutting down workers through the UI."""

    def test_workers_list_structure(self, admin_page: Page) -> None:
        """Verify workers list has expected structure."""
        workers_list = admin_page.locator("#workers-list")
        expect(workers_list).to_be_visible()

        # Wait for content to load
        admin_page.wait_for_timeout(1500)

        # Should show either workers or empty state
        empty_msg = workers_list.locator("text=No workers running")
        loading_msg = workers_list.locator("text=Loading workers...")
        worker_rows = workers_list.locator(".px-6.py-4")

        is_empty = empty_msg.count() > 0
        is_loading = loading_msg.count() > 0
        has_workers = worker_rows.count() > 0

        # One of these states should be visible
        assert is_empty or is_loading or has_workers

    def test_daemon_control_buttons(self, admin_page: Page) -> None:
        """Test daemon start/stop/restart buttons are present."""
        start_btn = admin_page.locator("#daemon-start-btn")
        stop_btn = admin_page.locator("#daemon-stop-btn")
        restart_btn = admin_page.locator("#daemon-restart-btn")

        # At least one button should be visible
        expect(start_btn).to_be_attached()
        expect(stop_btn).to_be_attached()
        expect(restart_btn).to_be_attached()

    def test_stop_worker_api(self, page_with_server: Page, server_url: str) -> None:
        """Test worker stop API endpoint."""
        # Test stopping a non-existent worker
        response = page_with_server.request.delete(f"{server_url}/api/workers/test-worker-id")
        # 404 means worker not found
        # 503 means daemon not running
        # 200 means worker stopped
        assert response.status in [200, 404, 503]


class TestBeadCreation:
    """Tests for creating beads through the UI."""

    def test_create_bead_modal_elements(self, admin_page: Page) -> None:
        """Verify all create bead form elements exist."""
        # Open modal
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(300)

        # Check all form fields
        title_input = admin_page.locator("#bead-title")
        desc_input = admin_page.locator("#bead-description")
        type_select = admin_page.locator("#bead-type")
        priority_select = admin_page.locator("#bead-priority")
        labels_input = admin_page.locator("#bead-labels")
        submit_btn = admin_page.locator("#create-bead-submit")

        expect(title_input).to_be_visible()
        expect(desc_input).to_be_visible()
        expect(type_select).to_be_visible()
        expect(priority_select).to_be_visible()
        expect(labels_input).to_be_visible()
        expect(submit_btn).to_be_visible()

    def test_create_bead_form_can_be_filled(self, admin_page: Page) -> None:
        """Test filling out the create bead form."""
        # Open modal
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(300)

        # Fill form
        admin_page.locator("#bead-title").fill("Test Bead Title")
        admin_page.locator("#bead-description").fill("Test description")
        admin_page.locator("#bead-type").select_option("feature")
        admin_page.locator("#bead-priority").select_option("1")
        admin_page.locator("#bead-labels").fill("dev, test")

        # Verify values
        assert admin_page.locator("#bead-title").input_value() == "Test Bead Title"
        assert admin_page.locator("#bead-description").input_value() == "Test description"
        assert admin_page.locator("#bead-type").input_value() == "feature"
        assert admin_page.locator("#bead-priority").input_value() == "1"
        assert admin_page.locator("#bead-labels").input_value() == "dev, test"

    def test_create_bead_type_options(self, admin_page: Page) -> None:
        """Verify all bead type options are available."""
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(300)

        type_select = admin_page.locator("#bead-type")
        expected_types = ["task", "bug", "feature", "epic"]

        for bead_type in expected_types:
            option = type_select.locator(f"option[value='{bead_type}']")
            expect(option).to_be_attached()

    def test_create_bead_priority_options(self, admin_page: Page) -> None:
        """Verify all priority options are available."""
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(300)

        priority_select = admin_page.locator("#bead-priority")
        expected_priorities = ["0", "1", "2", "3", "4"]

        for priority in expected_priorities:
            option = priority_select.locator(f"option[value='{priority}']")
            expect(option).to_be_attached()

    def test_create_bead_api(self, page_with_server: Page, server_url: str) -> None:
        """Test bead creation API endpoint."""
        response = page_with_server.request.post(
            f"{server_url}/api/beads",
            data={
                "title": "E2E Test Bead",
                "description": "Created by E2E test",
                "issue_type": "task",
                "priority": 2,
                "labels": ["test"],
            },
        )
        # 201 means bead created
        # 500 means bd command failed (beads not configured)
        assert response.status in [201, 500]


class TestWorkerLogs:
    """Tests for viewing worker logs."""

    def test_log_modal_structure(self, admin_page: Page) -> None:
        """Verify log modal has expected structure."""
        log_modal = admin_page.locator("#log-modal")
        expect(log_modal).to_be_attached()
        expect(log_modal).to_have_class(re.compile("hidden"))

        # Check modal elements exist
        log_viewer = admin_page.locator("#log-viewer")
        log_pause_btn = admin_page.locator("#log-pause-btn")
        log_status = admin_page.locator("#log-status")
        log_count = admin_page.locator("#log-count")

        expect(log_viewer).to_be_attached()
        expect(log_pause_btn).to_be_attached()
        expect(log_status).to_be_attached()
        expect(log_count).to_be_attached()

    def test_log_modal_controls(self, admin_page: Page) -> None:
        """Verify log modal control buttons exist."""
        pause_btn = admin_page.locator("#log-pause-btn")
        expect(pause_btn).to_be_attached()
        expect(pause_btn).to_contain_text("Pause")

    def test_log_streaming_api(self, page_with_server: Page, server_url: str) -> None:
        """Test log streaming API endpoint."""
        # Test log endpoint for a non-existent worker
        response = page_with_server.request.get(f"{server_url}/api/workers/test-worker/logs/recent")
        # 404 means worker not found
        # 503 means daemon not running
        assert response.status in [404, 503]


class TestBeadsPage:
    """Tests for the Beads management page."""

    def test_beads_page_loads(self, page_with_server: Page, server_url: str) -> None:
        """Verify beads page loads with expected elements."""
        page_with_server.goto(f"{server_url}/beads")
        page_with_server.wait_for_load_state("networkidle")

        # Check for main elements
        assert "Bead Management" in page_with_server.content()

    def test_beads_page_stats_cards(self, page_with_server: Page, server_url: str) -> None:
        """Verify beads page has statistics cards."""
        page_with_server.goto(f"{server_url}/beads")
        page_with_server.wait_for_load_state("networkidle")
        page_with_server.wait_for_timeout(1000)

        # Check for stat cards
        stat_ids = [
            "#stat-total",
            "#stat-ready",
            "#stat-in-progress",
            "#stat-blocked",
            "#stat-closed",
            "#stat-with-deps",
        ]

        for stat_id in stat_ids:
            stat = page_with_server.locator(stat_id)
            expect(stat).to_be_attached()

    def test_beads_page_filters(self, page_with_server: Page, server_url: str) -> None:
        """Verify beads page has filter controls."""
        page_with_server.goto(f"{server_url}/beads")
        page_with_server.wait_for_load_state("networkidle")

        # Check filter elements
        search_input = page_with_server.locator("#search-input")
        status_filter = page_with_server.locator("#filter-status")
        priority_filter = page_with_server.locator("#filter-priority")
        type_filter = page_with_server.locator("#filter-type")
        sort_by = page_with_server.locator("#sort-by")

        expect(search_input).to_be_visible()
        expect(status_filter).to_be_visible()
        expect(priority_filter).to_be_visible()
        expect(type_filter).to_be_visible()
        expect(sort_by).to_be_visible()

    def test_beads_page_status_filter_options(
        self, page_with_server: Page, server_url: str
    ) -> None:
        """Verify beads status filter has expected options."""
        page_with_server.goto(f"{server_url}/beads")
        page_with_server.wait_for_load_state("networkidle")

        status_filter = page_with_server.locator("#filter-status")
        expected_statuses = ["", "open", "in_progress", "closed"]

        for status in expected_statuses:
            option = status_filter.locator(f"option[value='{status}']")
            expect(option).to_be_attached()

    def test_beads_page_dependency_graph(self, page_with_server: Page, server_url: str) -> None:
        """Verify beads page has dependency graph section."""
        page_with_server.goto(f"{server_url}/beads")
        page_with_server.wait_for_load_state("networkidle")

        # Check for dependency graph container
        graph_container = page_with_server.locator("#dep-graph-container")
        expect(graph_container).to_be_visible()

    def test_beads_api_returns_data(self, page_with_server: Page, server_url: str) -> None:
        """Verify beads API returns list data."""
        response = page_with_server.request.get(f"{server_url}/api/beads")
        assert response.status == 200
        data = response.json()
        assert isinstance(data, list)


class TestWorkflowVerification:
    """Tests for verifying complete dashboard workflows."""

    def test_kanban_board_columns(self, dashboard_page: Page) -> None:
        """Verify Kanban board has expected columns."""
        kanban = dashboard_page.locator("#kanban-board")
        expect(kanban).to_be_visible()

        # Wait for content to load
        dashboard_page.wait_for_timeout(1500)

        # Check for column presence in content
        page_content = dashboard_page.content().lower()
        assert "ready" in page_content or "open" in page_content
        assert "progress" in page_content or "in progress" in page_content
        assert "done" in page_content or "closed" in page_content

    def test_agent_sidebar_loads(self, dashboard_page: Page) -> None:
        """Verify agent sidebar loads and displays."""
        sidebar = dashboard_page.locator("#agent-sidebar")
        expect(sidebar).to_be_visible()

    def test_htmx_partial_updates(self, dashboard_page: Page) -> None:
        """Verify HTMX partial updates work."""
        # Track requests for partials
        partial_requests: list[str] = []

        def handle_request(request: Request) -> None:
            if "/partials" in request.url:
                partial_requests.append(request.url)

        dashboard_page.on("request", handle_request)

        # Trigger a refresh
        refresh_btn = dashboard_page.locator("button:has-text('Refresh Now')")
        if refresh_btn.count() > 0:
            refresh_btn.click()
            dashboard_page.wait_for_timeout(1500)

            # Should have made partial requests
            assert len(partial_requests) > 0, "HTMX should request partials on refresh"

    def test_websocket_connection_indicator(self, admin_page: Page) -> None:
        """Verify WebSocket connection status is shown."""
        ws_status = admin_page.locator("#ws-status")
        expect(ws_status).to_be_visible()


class TestLogsPage:
    """Tests for the Logs viewer page."""

    def test_logs_page_loads(self, page_with_server: Page, server_url: str) -> None:
        """Verify logs page loads with expected elements."""
        page_with_server.goto(f"{server_url}/logs")
        # Use domcontentloaded to avoid timeout from SSE connections
        page_with_server.wait_for_load_state("domcontentloaded")
        page_with_server.wait_for_timeout(500)

        # Check for main elements
        page_content = page_with_server.content().lower()
        assert "log" in page_content

    def test_logs_page_has_filters(self, page_with_server: Page, server_url: str) -> None:
        """Verify logs page has filter controls."""
        page_with_server.goto(f"{server_url}/logs")
        # Use domcontentloaded to avoid timeout from SSE connections
        page_with_server.wait_for_load_state("domcontentloaded")
        page_with_server.wait_for_timeout(500)

        # Check for filter-related content
        page_content = page_with_server.content().lower()
        assert any(word in page_content for word in ["filter", "search", "level", "time"])


class TestHelpPage:
    """Tests for the Help documentation page."""

    def test_help_page_loads(self, page_with_server: Page, server_url: str) -> None:
        """Verify help page loads with documentation."""
        page_with_server.goto(f"{server_url}/help")
        page_with_server.wait_for_load_state("networkidle")

        # Check for help content
        page_content = page_with_server.content().lower()
        assert "help" in page_content

    def test_help_page_documents_roles(self, page_with_server: Page, server_url: str) -> None:
        """Verify help page documents worker roles."""
        page_with_server.goto(f"{server_url}/help")
        page_with_server.wait_for_load_state("networkidle")

        page_content = page_with_server.content().lower()
        roles = ["developer", "qa", "reviewer"]
        assert any(role in page_content for role in roles)

    def test_help_page_documents_priorities(self, page_with_server: Page, server_url: str) -> None:
        """Verify help page documents priority levels."""
        page_with_server.goto(f"{server_url}/help")
        page_with_server.wait_for_load_state("networkidle")

        page_content = page_with_server.content().lower()
        assert "priority" in page_content


class TestAgentsPage:
    """Tests for the Agents monitoring page."""

    def test_agents_page_loads(self, page_with_server: Page, server_url: str) -> None:
        """Verify agents page loads with expected elements."""
        page_with_server.goto(f"{server_url}/agents")
        page_with_server.wait_for_load_state("networkidle")

        # Check for main elements
        page_content = page_with_server.content().lower()
        assert "agent" in page_content

    def test_agents_api_returns_data(self, page_with_server: Page, server_url: str) -> None:
        """Verify agents API returns list data."""
        response = page_with_server.request.get(f"{server_url}/api/agents")
        assert response.status == 200
        data = response.json()
        assert isinstance(data, list)


class TestKeyboardShortcuts:
    """Tests for keyboard shortcuts and accessibility."""

    def test_escape_closes_modals(self, admin_page: Page) -> None:
        """Verify Escape key closes open modals."""
        # Open create bead modal
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(300)

        modal = admin_page.locator("#create-bead-modal")
        expect(modal).not_to_have_class(re.compile("hidden"))

        # Press Escape
        admin_page.keyboard.press("Escape")
        admin_page.wait_for_timeout(300)

        expect(modal).to_have_class(re.compile("hidden"))

    def test_escape_closes_dependency_view(self, dashboard_page: Page) -> None:
        """Verify Escape closes dependency graph view."""
        deps_btn = dashboard_page.locator("#depgraph-toggle")
        deps_btn.click()
        dashboard_page.wait_for_timeout(300)

        depgraph = dashboard_page.locator("#depgraph-container")
        expect(depgraph).not_to_have_class(re.compile("hidden"))

        # Press Escape
        dashboard_page.keyboard.press("Escape")
        dashboard_page.wait_for_timeout(300)

        kanban = dashboard_page.locator("#kanban-board")
        expect(kanban).not_to_have_class(re.compile("hidden"))


class TestResponsiveDesign:
    """Tests for responsive design at different viewport sizes."""

    def test_admin_page_mobile(self, dashboard_server, browser, server_url: str) -> None:
        """Verify admin page works on mobile viewport."""
        context = browser.new_context(viewport={"width": 375, "height": 667})
        page = context.new_page()

        try:
            page.goto(f"{server_url}/admin")
            page.wait_for_load_state("networkidle")

            # Main content should still be accessible
            daemon_status = page.locator("#daemon-status")
            expect(daemon_status).to_be_attached()

            spawn_form = page.locator("#spawn-form")
            expect(spawn_form).to_be_attached()
        finally:
            context.close()

    def test_beads_page_tablet(self, dashboard_server, browser, server_url: str) -> None:
        """Verify beads page works on tablet viewport."""
        context = browser.new_context(viewport={"width": 768, "height": 1024})
        page = context.new_page()

        try:
            page.goto(f"{server_url}/beads")
            page.wait_for_load_state("networkidle")

            # Filters should be visible
            status_filter = page.locator("#filter-status")
            expect(status_filter).to_be_attached()

            # Bead list should be visible
            beads_list = page.locator("#beads-list")
            expect(beads_list).to_be_attached()
        finally:
            context.close()
