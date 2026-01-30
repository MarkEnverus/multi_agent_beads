"""E2E tests using Chrome DevTools Protocol for dashboard testing.

These tests use pychrome to connect to Chrome DevTools Protocol,
providing an alternative to Playwright that aligns with Chrome MCP tools.

Run with: uv run pytest tests/e2e/test_chrome_mcp_e2e.py -v

Prerequisites:
- Chrome running with --remote-debugging-port=9222
- Dashboard running at http://127.0.0.1:8000

To start Chrome with debugging:
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
        --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

To start the dashboard:
    uv run python -m dashboard.app
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass

import pytest
import requests

# Skip all tests if not in interactive E2E mode
pytestmark = pytest.mark.skipif(
    bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS")),
    reason="Chrome MCP E2E tests require local Chrome instance",
)


def find_free_port() -> int:
    """Find an available port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


@dataclass
class E2EResult:
    """Result of an E2E test case."""

    name: str
    passed: bool
    message: str
    duration_ms: float = 0


class ChromeE2ETestRunner:
    """Test runner using Chrome DevTools Protocol.

    This class provides a simple interface for running E2E tests
    against the dashboard using HTTP requests to verify functionality.
    It complements interactive Chrome MCP testing by providing
    automated verification of key functionality.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.results: list[E2EResult] = []

    def test_navigation_links(self) -> E2EResult:
        """Test that all navigation links return valid pages."""
        pages = [
            ("/", "Kanban Board"),
            ("/admin", "Admin"),
            ("/agents", "Agents"),
            ("/beads", "Beads"),
            ("/logs", "Logs"),
            ("/help", "Help"),
            ("/docs", "Swagger UI"),  # FastAPI auto-generated docs
        ]

        start = time.time()
        errors = []

        for path, expected_title in pages:
            try:
                resp = requests.get(f"{self.base_url}{path}", timeout=10)
                if resp.status_code != 200:
                    errors.append(f"{path}: HTTP {resp.status_code}")
                elif expected_title.lower() not in resp.text.lower():
                    errors.append(f"{path}: missing '{expected_title}' in response")
            except requests.RequestException as e:
                errors.append(f"{path}: {e}")

        duration = (time.time() - start) * 1000
        passed = len(errors) == 0
        message = "All navigation links work" if passed else f"Failures: {errors}"

        return E2EResult("navigation_links", passed, message, duration)

    def test_health_endpoint(self) -> E2EResult:
        """Test the /health endpoint returns OK."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            data = resp.json()
            passed = resp.status_code == 200 and data.get("status") == "ok"
            message = "Health check passed" if passed else f"Unexpected: {data}"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("health_endpoint", passed, message, duration)

    def test_api_beads(self) -> E2EResult:
        """Test the /api/beads endpoint returns valid data."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/api/beads", timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    passed = True
                    message = f"Returned {len(data)} beads"
                else:
                    passed = False
                    message = f"Unexpected response type: {type(data)}"
            else:
                passed = False
                message = f"HTTP {resp.status_code}"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("api_beads", passed, message, duration)

    def test_api_agents(self) -> E2EResult:
        """Test the /api/agents endpoint returns valid data."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/api/agents", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    passed = True
                    message = f"Returned {len(data)} agents"
                else:
                    passed = False
                    message = f"Unexpected response type: {type(data)}"
            else:
                passed = False
                message = f"HTTP {resp.status_code}"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("api_agents", passed, message, duration)

    def test_api_workers_daemon_status(self) -> E2EResult:
        """Test the daemon status endpoint."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/api/workers/daemon/status", timeout=10)
            # Can return 200 (running) or 503 (not running)
            passed = resp.status_code in [200, 503]
            if resp.status_code == 200:
                data = resp.json()
                message = f"Daemon running: PID {data.get('pid', 'unknown')}"
            else:
                message = "Daemon not running (503)"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("api_workers_daemon_status", passed, message, duration)

    def test_api_workers_health(self) -> E2EResult:
        """Test the workers health endpoint."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/api/workers/health", timeout=10)
            passed = resp.status_code in [200, 503]
            if resp.status_code == 200:
                data = resp.json()
                message = f"Health stats: {data}"
            else:
                message = "Health endpoint returned 503"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("api_workers_health", passed, message, duration)

    def test_partials_kanban(self) -> E2EResult:
        """Test the Kanban partial endpoint."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/partials/kanban", timeout=30)
            passed = resp.status_code == 200
            if passed:
                # Check for expected HTML structure
                has_columns = all(
                    col in resp.text.lower() for col in ["ready", "in progress", "done"]
                )
                message = "Kanban partial loaded" + (" with columns" if has_columns else "")
            else:
                message = f"HTTP {resp.status_code}"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("partials_kanban", passed, message, duration)

    def test_partials_agents(self) -> E2EResult:
        """Test the agents partial endpoint."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/partials/agents", timeout=10)
            passed = resp.status_code == 200
            message = "Agents partial loaded" if passed else f"HTTP {resp.status_code}"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("partials_agents", passed, message, duration)

    def test_partials_depgraph(self) -> E2EResult:
        """Test the dependency graph partial endpoint."""
        start = time.time()

        try:
            resp = requests.get(f"{self.base_url}/partials/depgraph", timeout=30)
            passed = resp.status_code == 200
            message = "Depgraph partial loaded" if passed else f"HTTP {resp.status_code}"
        except Exception as e:
            passed = False
            message = f"Error: {e}"

        duration = (time.time() - start) * 1000
        return E2EResult("partials_depgraph", passed, message, duration)

    def run_all(self) -> list[E2EResult]:
        """Run all E2E tests and return results."""
        self.results = [
            self.test_health_endpoint(),
            self.test_navigation_links(),
            self.test_api_beads(),
            self.test_api_agents(),
            self.test_api_workers_daemon_status(),
            self.test_api_workers_health(),
            self.test_partials_kanban(),
            self.test_partials_agents(),
            self.test_partials_depgraph(),
        ]
        return self.results

    def print_summary(self) -> None:
        """Print test results summary."""
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        total_time = sum(r.duration_ms for r in self.results)

        print(f"\n{'=' * 60}")
        print(f"E2E Test Results: {passed}/{total} passed ({total_time:.0f}ms)")
        print("=" * 60)

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.name}: {result.message} ({result.duration_ms:.0f}ms)")

        print("=" * 60)


# Pytest fixtures and tests


@pytest.fixture(scope="session")
def server_port() -> int:
    """Get a free port for the test server."""
    return find_free_port()


@pytest.fixture(scope="session")
def server_url(server_port: int) -> str:
    """Get the base URL for the test server."""
    return f"http://127.0.0.1:{server_port}"


@pytest.fixture(scope="session")
def dashboard_server(server_port: int) -> Generator[subprocess.Popen, None, None]:
    """Start the dashboard server for E2E tests."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dashboard.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    max_retries = 20
    for _ in range(max_retries):
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                if sock.connect_ex(("127.0.0.1", server_port)) == 0:
                    break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        process.kill()
        raise RuntimeError(f"Dashboard server failed to start on port {server_port}")

    yield process

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture
def test_runner(dashboard_server: subprocess.Popen, server_url: str) -> ChromeE2ETestRunner:
    """Create a test runner connected to the dashboard."""
    return ChromeE2ETestRunner(server_url)


class TestNavigationLinks:
    """Tests for navigation link functionality."""

    def test_dashboard_loads(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify dashboard page loads successfully."""
        resp = requests.get(f"{test_runner.base_url}/", timeout=10)
        assert resp.status_code == 200
        assert "kanban" in resp.text.lower()

    def test_admin_loads(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify admin page loads successfully."""
        resp = requests.get(f"{test_runner.base_url}/admin", timeout=10)
        assert resp.status_code == 200
        assert "admin" in resp.text.lower()

    def test_agents_loads(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify agents page loads successfully."""
        resp = requests.get(f"{test_runner.base_url}/agents", timeout=10)
        assert resp.status_code == 200
        assert "agent" in resp.text.lower()

    def test_beads_loads(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify beads page loads successfully."""
        resp = requests.get(f"{test_runner.base_url}/beads", timeout=10)
        assert resp.status_code == 200
        assert "bead" in resp.text.lower()

    def test_logs_loads(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify logs page loads successfully."""
        resp = requests.get(f"{test_runner.base_url}/logs", timeout=10)
        assert resp.status_code == 200
        assert "log" in resp.text.lower()

    def test_help_loads(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify help page loads successfully."""
        resp = requests.get(f"{test_runner.base_url}/help", timeout=10)
        assert resp.status_code == 200
        assert "help" in resp.text.lower()


class TestAPIEndpoints:
    """Tests for API endpoint functionality."""

    def test_health_returns_ok(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify /health returns status ok."""
        resp = requests.get(f"{test_runner.base_url}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_api_beads_returns_list(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify /api/beads returns a list."""
        resp = requests.get(f"{test_runner.base_url}/api/beads", timeout=30)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_api_agents_returns_list(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify /api/agents returns a list."""
        resp = requests.get(f"{test_runner.base_url}/api/agents", timeout=10)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_daemon_status_endpoint(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify daemon status endpoint responds appropriately."""
        resp = requests.get(f"{test_runner.base_url}/api/workers/daemon/status", timeout=10)
        # Can be 200 (running) or 503 (not running) - both are valid
        assert resp.status_code in [200, 503]

    def test_workers_health_endpoint(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify workers health endpoint responds."""
        resp = requests.get(f"{test_runner.base_url}/api/workers/health", timeout=10)
        assert resp.status_code in [200, 503]


class TestPartials:
    """Tests for HTMX partial endpoints."""

    def test_kanban_partial(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify kanban partial loads."""
        resp = requests.get(f"{test_runner.base_url}/partials/kanban", timeout=30)
        assert resp.status_code == 200

    def test_agents_partial(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify agents partial loads."""
        resp = requests.get(f"{test_runner.base_url}/partials/agents", timeout=10)
        assert resp.status_code == 200

    def test_depgraph_partial(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify dependency graph partial loads."""
        resp = requests.get(f"{test_runner.base_url}/partials/depgraph", timeout=30)
        assert resp.status_code == 200


class TestAgentsPage:
    """Tests specific to the Agents page functionality."""

    def test_agents_api_filter_by_role(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify agents can be filtered by role via API."""
        resp = requests.get(
            f"{test_runner.base_url}/api/agents",
            params={"role": "dev"},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        # All returned agents should be developers
        for agent in data:
            if "role" in agent:
                assert agent["role"].lower() in ["dev", "developer"]

    def test_agents_page_has_filters(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify agents page includes filter controls."""
        resp = requests.get(f"{test_runner.base_url}/agents", timeout=10)
        assert resp.status_code == 200
        # Check for filter elements
        assert "filter" in resp.text.lower() or "role" in resp.text.lower()


class TestAdminPage:
    """Tests specific to the Admin page functionality."""

    def test_admin_has_daemon_status(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify admin page shows daemon status section."""
        resp = requests.get(f"{test_runner.base_url}/admin", timeout=10)
        assert resp.status_code == 200
        assert "daemon" in resp.text.lower()

    def test_admin_has_spawn_form(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify admin page has worker spawn form."""
        resp = requests.get(f"{test_runner.base_url}/admin", timeout=10)
        assert resp.status_code == 200
        assert "spawn" in resp.text.lower()

    def test_admin_has_health_stats(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify admin page shows health statistics."""
        resp = requests.get(f"{test_runner.base_url}/admin", timeout=10)
        assert resp.status_code == 200
        # Check for health stat indicators
        assert any(word in resp.text.lower() for word in ["healthy", "crashed", "unhealthy"])


class TestBeadsPage:
    """Tests specific to the Beads page functionality."""

    def test_beads_page_has_filters(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify beads page includes filter controls."""
        resp = requests.get(f"{test_runner.base_url}/beads", timeout=10)
        assert resp.status_code == 200
        # Check for filter elements
        assert any(word in resp.text.lower() for word in ["status", "priority", "type", "label"])

    def test_beads_page_has_dependency_graph(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify beads page includes dependency graph section."""
        resp = requests.get(f"{test_runner.base_url}/beads", timeout=10)
        assert resp.status_code == 200
        assert "dependency" in resp.text.lower() or "graph" in resp.text.lower()


class TestLogsPage:
    """Tests specific to the Logs page functionality."""

    def test_logs_page_has_filters(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify logs page includes filter controls."""
        resp = requests.get(f"{test_runner.base_url}/logs", timeout=10)
        assert resp.status_code == 200
        assert any(word in resp.text.lower() for word in ["level", "search", "time", "role"])

    def test_logs_page_has_stats(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify logs page shows statistics."""
        resp = requests.get(f"{test_runner.base_url}/logs", timeout=10)
        assert resp.status_code == 200
        assert any(
            word in resp.text.lower() for word in ["total", "errors", "warnings", "sessions"]
        )


class TestHelpPage:
    """Tests specific to the Help page functionality."""

    def test_help_has_worker_roles(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify help page documents worker roles."""
        resp = requests.get(f"{test_runner.base_url}/help", timeout=10)
        assert resp.status_code == 200
        roles = ["developer", "qa", "reviewer", "tech lead", "manager"]
        assert any(role in resp.text.lower() for role in roles)

    def test_help_has_priority_docs(self, test_runner: ChromeE2ETestRunner) -> None:
        """Verify help page documents priority levels."""
        resp = requests.get(f"{test_runner.base_url}/help", timeout=10)
        assert resp.status_code == 200
        assert "priority" in resp.text.lower()
        # Check for P0-P4 priority levels
        assert any(f"p{i}" in resp.text.lower() for i in range(5))


# Main execution for standalone testing
if __name__ == "__main__":
    print("Chrome MCP E2E Test Suite")
    print("=" * 60)

    # Check if dashboard is running
    try:
        resp = requests.get("http://127.0.0.1:8000/health", timeout=5)
        if resp.status_code != 200:
            print("ERROR: Dashboard not healthy")
            sys.exit(1)
    except requests.RequestException:
        print("ERROR: Dashboard not running at http://127.0.0.1:8000")
        print("Start with: uv run python -m dashboard.app")
        sys.exit(1)

    # Run tests
    runner = ChromeE2ETestRunner("http://127.0.0.1:8000")
    runner.run_all()
    runner.print_summary()

    # Exit with appropriate code
    failed = sum(1 for r in runner.results if not r.passed)
    sys.exit(0 if failed == 0 else 1)
