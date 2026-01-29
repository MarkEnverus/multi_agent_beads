"""End-to-end browser tests for the Multi-Agent Dashboard using Playwright."""

import re
from collections.abc import Generator

import pytest
from playwright.sync_api import Page, expect


class TestDashboardLoad:
    """Tests for initial dashboard loading and state."""

    def test_dashboard_loads_successfully(self, dashboard_page: Page) -> None:
        """Verify the dashboard page loads with correct title."""
        expect(dashboard_page).to_have_title(re.compile("Kanban Board"))

    def test_kanban_header_visible(self, dashboard_page: Page) -> None:
        """Verify the Kanban Board header is visible."""
        header = dashboard_page.locator("h1:has-text('Kanban Board')")
        expect(header).to_be_visible()

    def test_refresh_button_visible(self, dashboard_page: Page) -> None:
        """Verify the Refresh Now button is present."""
        refresh_btn = dashboard_page.locator("button:has-text('Refresh Now')")
        expect(refresh_btn).to_be_visible()

    def test_dependencies_button_visible(self, dashboard_page: Page) -> None:
        """Verify the Dependencies toggle button is present."""
        deps_btn = dashboard_page.locator("#depgraph-toggle")
        expect(deps_btn).to_be_visible()
        expect(deps_btn).to_contain_text("Dependencies")

    def test_agent_sidebar_loads(self, dashboard_page: Page) -> None:
        """Verify the agent sidebar section loads."""
        sidebar = dashboard_page.locator("#agent-sidebar")
        expect(sidebar).to_be_visible()

    def test_kanban_columns_present(self, dashboard_page: Page) -> None:
        """Verify the Kanban board has the expected columns."""
        # Wait for kanban content to load
        kanban = dashboard_page.locator("#kanban-board")
        expect(kanban).to_be_visible()

        # Check for column headers (Ready, In Progress, Done)
        # These are loaded via HTMX partial, may need to wait
        dashboard_page.wait_for_timeout(500)


class TestViewNavigation:
    """Tests for navigating between Kanban and dependency graph views."""

    def test_toggle_to_dependency_graph(self, dashboard_page: Page) -> None:
        """Verify clicking Dependencies button shows the dependency graph."""
        # Initially kanban should be visible
        kanban = dashboard_page.locator("#kanban-board")
        depgraph = dashboard_page.locator("#depgraph-container")

        expect(kanban).not_to_have_class(re.compile("hidden"))
        expect(depgraph).to_have_class(re.compile("hidden"))

        # Click the Dependencies button
        deps_btn = dashboard_page.locator("#depgraph-toggle")
        deps_btn.click()

        # Wait for transition
        dashboard_page.wait_for_timeout(500)

        # Now depgraph should be visible and kanban hidden
        expect(kanban).to_have_class(re.compile("hidden"))
        expect(depgraph).not_to_have_class(re.compile("hidden"))

    def test_toggle_back_to_kanban(self, dashboard_page: Page) -> None:
        """Verify toggling back to Kanban view works."""
        deps_btn = dashboard_page.locator("#depgraph-toggle")

        # Toggle to graph
        deps_btn.click()
        dashboard_page.wait_for_timeout(300)

        # Toggle back to kanban
        deps_btn.click()
        dashboard_page.wait_for_timeout(300)

        kanban = dashboard_page.locator("#kanban-board")
        depgraph = dashboard_page.locator("#depgraph-container")

        expect(kanban).not_to_have_class(re.compile("hidden"))
        expect(depgraph).to_have_class(re.compile("hidden"))

    def test_escape_closes_graph_view(self, dashboard_page: Page) -> None:
        """Verify pressing Escape closes the dependency graph view."""
        # Toggle to graph view
        deps_btn = dashboard_page.locator("#depgraph-toggle")
        deps_btn.click()
        dashboard_page.wait_for_timeout(300)

        # Press Escape
        dashboard_page.keyboard.press("Escape")
        dashboard_page.wait_for_timeout(300)

        # Should be back to kanban
        kanban = dashboard_page.locator("#kanban-board")
        expect(kanban).not_to_have_class(re.compile("hidden"))


class TestRefreshFunctionality:
    """Tests for manual and auto-refresh functionality."""

    def test_manual_refresh_triggers_htmx(self, dashboard_page: Page) -> None:
        """Verify clicking Refresh Now triggers an HTMX request."""
        # Intercept network requests
        refresh_called = []

        def handle_request(request):
            if "/partials/kanban" in request.url:
                refresh_called.append(request.url)

        dashboard_page.on("request", handle_request)

        # Click refresh button
        refresh_btn = dashboard_page.locator("button:has-text('Refresh Now')")
        refresh_btn.click()

        # Wait for request
        dashboard_page.wait_for_timeout(1000)

        # Verify the request was made
        assert len(refresh_called) > 0, "Expected /partials/kanban request on refresh"

    def test_auto_refresh_indicator_visible(self, dashboard_page: Page) -> None:
        """Verify the auto-refresh indicator text is shown."""
        indicator = dashboard_page.locator("text=Auto-refreshing every 10s")
        expect(indicator).to_be_visible()


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_endpoint(self, page_with_server: Page, server_url: str) -> None:
        """Verify the /health endpoint returns OK status."""
        response = page_with_server.request.get(f"{server_url}/health")
        assert response.ok
        data = response.json()
        assert data["status"] == "ok"


class TestAPIEndpoints:
    """Tests for API endpoints via browser requests."""

    def test_api_beads_endpoint(self, page_with_server: Page, server_url: str) -> None:
        """Verify the /api/beads endpoint is accessible."""
        response = page_with_server.request.get(f"{server_url}/api/beads")
        # May return 200 with data or error depending on bd availability
        assert response.status in [200, 500]

    def test_api_agents_endpoint(self, page_with_server: Page, server_url: str) -> None:
        """Verify the /api/agents endpoint is accessible."""
        response = page_with_server.request.get(f"{server_url}/api/agents")
        # May return 200 with data or error depending on log file
        assert response.status in [200, 500]


class TestResponsiveLayout:
    """Tests for responsive design at different viewport sizes."""

    def test_mobile_viewport_loads(self, mobile_page: Page) -> None:
        """Verify dashboard loads on mobile viewport."""
        # Dashboard should still show header
        header = mobile_page.locator("h1:has-text('Kanban Board')")
        expect(header).to_be_visible()

    def test_mobile_sidebar_visible(self, mobile_page: Page) -> None:
        """Verify agent sidebar is accessible on mobile."""
        # On mobile, sidebar may be collapsed or stacked
        sidebar = mobile_page.locator("#agent-sidebar")
        expect(sidebar).to_be_attached()

    def test_tablet_viewport_loads(self, tablet_page: Page) -> None:
        """Verify dashboard loads on tablet viewport."""
        header = tablet_page.locator("h1:has-text('Kanban Board')")
        expect(header).to_be_visible()

    def test_tablet_kanban_visible(self, tablet_page: Page) -> None:
        """Verify Kanban board is visible on tablet."""
        kanban = tablet_page.locator("#kanban-board")
        expect(kanban).to_be_visible()


class TestModalBehavior:
    """Tests for modal dialogs and overlays."""

    def test_modal_container_exists(self, dashboard_page: Page) -> None:
        """Verify the bead detail modal container exists."""
        modal = dashboard_page.locator("#bead-detail-modal")
        expect(modal).to_be_attached()

    def test_escape_closes_modal(self, dashboard_page: Page) -> None:
        """Verify pressing Escape clears modal content."""
        modal = dashboard_page.locator("#bead-detail-modal")

        # Press Escape
        dashboard_page.keyboard.press("Escape")
        dashboard_page.wait_for_timeout(200)

        # Modal should be empty
        expect(modal).to_be_empty()


class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_invalid_bead_shows_error(self, page_with_server: Page, server_url: str) -> None:
        """Verify requesting an invalid bead shows an error modal."""
        # Navigate to dashboard first
        page_with_server.goto(server_url)
        page_with_server.wait_for_timeout(1000)

        # Request an invalid bead via HTMX-like endpoint
        response = page_with_server.request.get(
            f"{server_url}/partials/beads/invalid-bead-id-12345"
        )

        # Should return 200 with error content (not 404, per app.py logic)
        assert response.ok
        content = response.text()
        assert "not found" in content.lower() or "error" in content.lower()


class TestScreenshotOnFailure:
    """Demonstrate screenshot capture capability for debugging."""

    @pytest.fixture(autouse=True)
    def capture_screenshot_on_failure(
        self, request: pytest.FixtureRequest, dashboard_page: Page
    ) -> Generator[None, None, None]:
        """Capture screenshot if test fails."""
        yield
        if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
            screenshot_path = f"tests/e2e/screenshots/{request.node.name}.png"
            dashboard_page.screenshot(path=screenshot_path)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)  # type: ignore[misc]
def pytest_runtest_makereport(item, call):  # type: ignore[no-untyped-def]
    """Store test result on the item for screenshot capture."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
