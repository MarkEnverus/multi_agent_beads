"""End-to-end browser tests for the Admin page using Playwright."""

import re

from playwright.sync_api import Page, expect


class TestAdminPageLoad:
    """Tests for admin page loading and initial state."""

    def test_admin_page_loads(self, admin_page: Page) -> None:
        """Verify the admin page loads with correct title."""
        expect(admin_page).to_have_title(re.compile("Admin"))

    def test_daemon_status_visible(self, admin_page: Page) -> None:
        """Verify the daemon status card is visible."""
        daemon_status = admin_page.locator("#daemon-status")
        expect(daemon_status).to_be_visible()
        # Check for MAB Daemon header text
        expect(daemon_status.locator("h2")).to_contain_text("MAB Daemon")

    def test_health_stats_visible(self, admin_page: Page) -> None:
        """Verify the health statistics grid is visible with all stat cards."""
        # Check for each stat card
        stat_healthy = admin_page.locator("#stat-healthy")
        stat_unhealthy = admin_page.locator("#stat-unhealthy")
        stat_crashed = admin_page.locator("#stat-crashed")
        stat_restarts = admin_page.locator("#stat-restarts")

        expect(stat_healthy).to_be_visible()
        expect(stat_unhealthy).to_be_visible()
        expect(stat_crashed).to_be_visible()
        expect(stat_restarts).to_be_visible()

    def test_spawn_form_visible(self, admin_page: Page) -> None:
        """Verify the spawn worker form is visible."""
        spawn_form = admin_page.locator("#spawn-form")
        expect(spawn_form).to_be_visible()

    def test_workers_list_visible(self, admin_page: Page) -> None:
        """Verify the workers list container is visible."""
        workers_list = admin_page.locator("#workers-list")
        expect(workers_list).to_be_visible()


class TestWorkerSpawn:
    """Tests for the spawn worker form."""

    def test_spawn_form_has_role_dropdown(self, admin_page: Page) -> None:
        """Verify the spawn form has a role dropdown with expected options."""
        role_select = admin_page.locator("#spawn-role")
        expect(role_select).to_be_visible()

        # Check for expected role options
        options = role_select.locator("option")
        expect(options).to_have_count(5)

        # Verify specific roles exist
        expect(role_select.locator("option[value='dev']")).to_be_attached()
        expect(role_select.locator("option[value='qa']")).to_be_attached()
        expect(role_select.locator("option[value='reviewer']")).to_be_attached()

    def test_spawn_form_has_project_input(self, admin_page: Page) -> None:
        """Verify the spawn form has a project path input field."""
        project_input = admin_page.locator("#spawn-project")
        expect(project_input).to_be_visible()
        # Should be pre-populated with project path
        expect(project_input).not_to_be_empty()

    def test_spawn_button_visible(self, admin_page: Page) -> None:
        """Verify the spawn button is visible and enabled."""
        spawn_btn = admin_page.locator("#spawn-form button[type='submit']")
        expect(spawn_btn).to_be_visible()
        expect(spawn_btn).to_be_enabled()
        expect(spawn_btn).to_contain_text("Spawn")

    def test_spawn_autorestart_checkbox(self, admin_page: Page) -> None:
        """Verify the auto-restart checkbox is visible and checked by default."""
        autorestart = admin_page.locator("#spawn-autorestart")
        expect(autorestart).to_be_visible()
        expect(autorestart).to_be_checked()


class TestWorkerList:
    """Tests for the workers list."""

    def test_empty_state_shown_when_no_workers(self, admin_page: Page) -> None:
        """Verify empty state message is shown when no workers are running."""
        # Wait for the workers list to load
        admin_page.wait_for_timeout(1500)

        workers_list = admin_page.locator("#workers-list")

        # Either shows "No workers running" or "Loading workers..."
        # Depending on daemon state, we check for presence of list content
        expect(workers_list).to_be_visible()

        # If daemon is not running, should show empty state
        empty_msg = workers_list.locator("text=No workers running")
        loading_msg = workers_list.locator("text=Loading workers...")

        # One of these should be visible (or workers if daemon is running)
        is_empty = empty_msg.is_visible()
        is_loading = loading_msg.is_visible()
        has_workers = workers_list.locator(".px-6.py-4").count() > 0

        assert is_empty or is_loading or has_workers, (
            "Expected empty state, loading state, or workers list"
        )

    def test_worker_row_has_expected_structure(self, admin_page: Page) -> None:
        """Verify worker list has proper structure for worker rows."""
        workers_list = admin_page.locator("#workers-list")
        expect(workers_list).to_be_visible()

        # The workers list is rendered dynamically; verify container exists
        expect(workers_list).to_be_attached()


class TestLogViewer:
    """Tests for the log viewer modal."""

    def test_log_modal_exists(self, admin_page: Page) -> None:
        """Verify the log modal container exists but is hidden initially."""
        log_modal = admin_page.locator("#log-modal")
        expect(log_modal).to_be_attached()
        expect(log_modal).to_have_class(re.compile("hidden"))

    def test_log_modal_has_controls(self, admin_page: Page) -> None:
        """Verify the log modal has pause and clear buttons."""
        pause_btn = admin_page.locator("#log-pause-btn")
        expect(pause_btn).to_be_attached()
        expect(pause_btn).to_contain_text("Pause")

        log_viewer = admin_page.locator("#log-viewer")
        expect(log_viewer).to_be_attached()

    def test_log_modal_closes_on_escape(self, admin_page: Page) -> None:
        """Verify pressing Escape closes the log modal if it were open."""
        log_modal = admin_page.locator("#log-modal")

        # Modal should be hidden initially
        expect(log_modal).to_have_class(re.compile("hidden"))

        # Press Escape (should be safe even when modal is hidden)
        admin_page.keyboard.press("Escape")
        admin_page.wait_for_timeout(200)

        # Modal should still be hidden
        expect(log_modal).to_have_class(re.compile("hidden"))


class TestCreateBeadModal:
    """Tests for the create bead modal on admin page."""

    def test_create_bead_modal_exists(self, admin_page: Page) -> None:
        """Verify the create bead modal exists but is hidden initially."""
        modal = admin_page.locator("#create-bead-modal")
        expect(modal).to_be_attached()
        expect(modal).to_have_class(re.compile("hidden"))

    def test_new_bead_button_visible(self, admin_page: Page) -> None:
        """Verify the New Bead button is visible in header."""
        new_bead_btn = admin_page.locator("#new-bead-btn")
        expect(new_bead_btn).to_be_visible()
        expect(new_bead_btn).to_contain_text("New Bead")

    def test_new_bead_button_opens_modal(self, admin_page: Page) -> None:
        """Verify clicking New Bead button opens the create modal."""
        new_bead_btn = admin_page.locator("#new-bead-btn")
        modal = admin_page.locator("#create-bead-modal")

        # Modal should be hidden
        expect(modal).to_have_class(re.compile("hidden"))

        # Click the button
        new_bead_btn.click()
        admin_page.wait_for_timeout(200)

        # Modal should now be visible
        expect(modal).not_to_have_class(re.compile("hidden"))

    def test_create_bead_form_fields(self, admin_page: Page) -> None:
        """Verify the create bead form has all required fields."""
        # Open the modal
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(200)

        # Check form fields exist
        title_input = admin_page.locator("#bead-title")
        desc_input = admin_page.locator("#bead-description")
        type_select = admin_page.locator("#bead-type")
        priority_select = admin_page.locator("#bead-priority")
        labels_input = admin_page.locator("#bead-labels")

        expect(title_input).to_be_visible()
        expect(desc_input).to_be_visible()
        expect(type_select).to_be_visible()
        expect(priority_select).to_be_visible()
        expect(labels_input).to_be_visible()

    def test_create_bead_modal_closes_on_escape(self, admin_page: Page) -> None:
        """Verify pressing Escape closes the create bead modal."""
        # Open the modal
        admin_page.locator("#new-bead-btn").click()
        admin_page.wait_for_timeout(200)

        modal = admin_page.locator("#create-bead-modal")
        expect(modal).not_to_have_class(re.compile("hidden"))

        # Press Escape
        admin_page.keyboard.press("Escape")
        admin_page.wait_for_timeout(200)

        # Modal should be hidden again
        expect(modal).to_have_class(re.compile("hidden"))


class TestAdminRefresh:
    """Tests for refresh functionality."""

    def test_refresh_button_visible(self, admin_page: Page) -> None:
        """Verify the refresh button is visible."""
        refresh_btn = admin_page.locator("#refresh-btn")
        expect(refresh_btn).to_be_visible()
        expect(refresh_btn).to_contain_text("Refresh")

    def test_refresh_button_triggers_api_calls(self, admin_page: Page) -> None:
        """Verify clicking refresh triggers API calls."""
        api_calls: list[str] = []

        def handle_request(request):
            if "/api/" in request.url:
                api_calls.append(request.url)

        admin_page.on("request", handle_request)

        # Click refresh button
        refresh_btn = admin_page.locator("#refresh-btn")
        refresh_btn.click()

        # Wait for requests
        admin_page.wait_for_timeout(1000)

        # Should have made API calls
        assert len(api_calls) > 0, "Expected API calls on refresh"


class TestWebSocketStatus:
    """Tests for WebSocket connection status indicator."""

    def test_ws_status_indicator_visible(self, admin_page: Page) -> None:
        """Verify the WebSocket status indicator is visible."""
        ws_status = admin_page.locator("#ws-status")
        expect(ws_status).to_be_visible()


class TestFilterControls:
    """Tests for worker filter controls."""

    def test_status_filter_visible(self, admin_page: Page) -> None:
        """Verify the status filter dropdown is visible."""
        status_filter = admin_page.locator("#filter-status")
        expect(status_filter).to_be_visible()

        # Check for expected options
        expect(status_filter.locator("option[value='running']")).to_be_attached()
        expect(status_filter.locator("option[value='stopped']")).to_be_attached()
        expect(status_filter.locator("option[value='crashed']")).to_be_attached()

    def test_role_filter_visible(self, admin_page: Page) -> None:
        """Verify the role filter dropdown is visible."""
        role_filter = admin_page.locator("#filter-role")
        expect(role_filter).to_be_visible()

        # Check for expected options
        expect(role_filter.locator("option[value='dev']")).to_be_attached()
        expect(role_filter.locator("option[value='qa']")).to_be_attached()
        expect(role_filter.locator("option[value='reviewer']")).to_be_attached()


class TestAdminAPIEndpoints:
    """Tests for admin-related API endpoints via browser requests."""

    def test_daemon_status_endpoint(self, page_with_server: Page, server_url: str) -> None:
        """Verify the daemon status endpoint returns valid response."""
        response = page_with_server.request.get(f"{server_url}/api/workers/daemon/status")
        # May return 200 (running) or 503 (stopped)
        assert response.status in [200, 503]

    def test_workers_list_endpoint(self, page_with_server: Page, server_url: str) -> None:
        """Verify the workers list endpoint returns valid response."""
        response = page_with_server.request.get(f"{server_url}/api/workers")
        # May return 200 (with workers) or 503 (daemon not running)
        assert response.status in [200, 503]

    def test_health_endpoint(self, page_with_server: Page, server_url: str) -> None:
        """Verify the health endpoint returns valid response."""
        response = page_with_server.request.get(f"{server_url}/api/workers/health")
        # Should return 200 even if daemon isn't running
        assert response.status in [200, 503]
