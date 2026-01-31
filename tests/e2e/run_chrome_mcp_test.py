#!/usr/bin/env python3
"""Interactive Chrome MCP dashboard test runner.

This script starts the dashboard and provides test scenarios for
Claude to execute using Chrome MCP tools (navigate_page, take_snapshot,
click, fill, etc.).

Usage:
    # Start dashboard and wait for Claude to run tests:
    uv run python tests/e2e/run_chrome_mcp_test.py

    # Run automated HTTP-based tests (no Chrome MCP needed):
    uv run python tests/e2e/run_chrome_mcp_test.py --http-only

    # Just print test scenarios for Claude:
    uv run python tests/e2e/run_chrome_mcp_test.py --print-scenarios
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


def find_free_port() -> int:
    """Find an available port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait for server to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                if sock.connect_ex((host, port)) == 0:
                    # Also verify HTTP health
                    resp = requests.get(f"http://{host}:{port}/health", timeout=2)
                    if resp.status_code == 200:
                        return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


@dataclass
class TestScenario:
    """A test scenario for Chrome MCP testing."""

    name: str
    description: str
    steps: list[str]
    verification: str
    chrome_mcp_tools: list[str] = field(default_factory=list)


# Define comprehensive test scenarios for Chrome MCP
TEST_SCENARIOS: list[TestScenario] = [
    TestScenario(
        name="1. Dashboard Home Page",
        description="Verify the main dashboard page loads correctly",
        steps=[
            "Navigate to http://127.0.0.1:{port}/",
            "Take a snapshot to see the page structure",
            "Verify 'Kanban Board' heading exists",
            "Verify columns: Ready, In Progress, Done exist",
            "Check for any console errors",
        ],
        verification="Page loads with kanban board showing three columns",
        chrome_mcp_tools=["navigate_page", "take_snapshot", "list_console_messages"],
    ),
    TestScenario(
        name="2. Navigation Links",
        description="Test all navigation links work correctly",
        steps=[
            "From the dashboard, take a snapshot to find nav links",
            "Click on 'Admin' link",
            "Verify Admin page loads (has 'Daemon Status' section)",
            "Click on 'Agents' link",
            "Verify Agents page loads",
            "Click on 'Beads' link",
            "Verify Beads page loads",
            "Click on 'Logs' link",
            "Verify Logs page loads",
            "Click on 'Help' link",
            "Verify Help page loads with role documentation",
            "Return to Dashboard using logo/home link",
        ],
        verification="All navigation links work and pages render correctly",
        chrome_mcp_tools=["take_snapshot", "click", "wait_for"],
    ),
    TestScenario(
        name="3. Admin Page - Daemon Controls",
        description="Test the admin page daemon management UI",
        steps=[
            "Navigate to http://127.0.0.1:{port}/admin",
            "Take a snapshot to see daemon status",
            "Identify the daemon control buttons (Start/Stop/Restart)",
            "Note the current daemon status (running/stopped)",
            "If daemon is stopped, click 'Start Daemon' button",
            "Wait for status to update",
            "Verify status shows 'Running'",
            "Check health statistics section",
        ],
        verification="Daemon controls work and status updates correctly",
        chrome_mcp_tools=["navigate_page", "take_snapshot", "click", "wait_for"],
    ),
    TestScenario(
        name="4. Admin Page - Worker Spawn Form",
        description="Test spawning different agent types via the UI",
        steps=[
            "Navigate to http://127.0.0.1:{port}/admin",
            "Take a snapshot to find the worker spawn form",
            "Identify the role dropdown/selector",
            "Select 'dev' role",
            "Verify project path is pre-filled or fill it",
            "Click 'Spawn Worker' button",
            "Wait for worker to start",
            "Check the workers list shows the new worker",
            "Repeat for 'qa' role",
            "Repeat for 'reviewer' role",
        ],
        verification="Workers can be spawned for each role type",
        chrome_mcp_tools=["take_snapshot", "fill", "click", "wait_for"],
    ),
    TestScenario(
        name="5. Agents Page - Monitor Running Agents",
        description="Test the agents monitoring page",
        steps=[
            "Navigate to http://127.0.0.1:{port}/agents",
            "Take a snapshot to see agent list",
            "Verify agent cards show status indicators",
            "Check for role filter controls",
            "Use role filter to show only 'dev' agents",
            "Click on an agent card to see details",
            "Check agent session logs are displayed",
        ],
        verification="Agent monitoring shows real-time status and logs",
        chrome_mcp_tools=["navigate_page", "take_snapshot", "click", "fill"],
    ),
    TestScenario(
        name="6. Beads Page - View and Filter",
        description="Test the beads management page",
        steps=[
            "Navigate to http://127.0.0.1:{port}/beads",
            "Take a snapshot to see bead list",
            "Identify filter controls (status, priority, type)",
            "Apply status filter to show only 'open' beads",
            "Apply priority filter",
            "Clear filters",
            "Click on a bead to view details modal",
            "Verify modal shows bead title, description, status",
            "Close the modal",
            "Check dependency graph section",
        ],
        verification="Bead filtering and detail view work correctly",
        chrome_mcp_tools=["navigate_page", "take_snapshot", "click", "fill"],
    ),
    TestScenario(
        name="7. Logs Page - Live Streaming",
        description="Test the logs page with live streaming",
        steps=[
            "Navigate to http://127.0.0.1:{port}/logs",
            "Take a snapshot to see log viewer",
            "Verify log entries are displayed",
            "Check level filter (info, warning, error)",
            "Apply error level filter",
            "Check time range selector",
            "Test search/filter functionality",
            "Verify log stats are shown",
        ],
        verification="Logs display and filter correctly with streaming",
        chrome_mcp_tools=["navigate_page", "take_snapshot", "click", "fill"],
    ),
    TestScenario(
        name="8. Help Page - Documentation",
        description="Verify help page documents all worker roles",
        steps=[
            "Navigate to http://127.0.0.1:{port}/help",
            "Take a snapshot to see help content",
            "Verify Developer role documentation exists",
            "Verify QA role documentation exists",
            "Verify Reviewer role documentation exists",
            "Verify Tech Lead role documentation exists",
            "Verify Manager role documentation exists",
            "Check priority level documentation (P0-P4)",
        ],
        verification="All worker roles and priorities are documented",
        chrome_mcp_tools=["navigate_page", "take_snapshot"],
    ),
    TestScenario(
        name="9. Kanban Board Interactions",
        description="Test kanban board card interactions",
        steps=[
            "Navigate to http://127.0.0.1:{port}/",
            "Take a snapshot of kanban board",
            "Click on a bead card in 'Ready' column",
            "Verify detail modal opens",
            "Close the modal",
            "Check if drag indicators exist on cards",
            "Verify bead counts in column headers",
        ],
        verification="Kanban cards are clickable and show details",
        chrome_mcp_tools=["navigate_page", "take_snapshot", "click"],
    ),
    TestScenario(
        name="10. Full E2E Workflow",
        description="Complete workflow: spawn agent, watch it work on beads",
        steps=[
            "Navigate to Admin page",
            "Ensure daemon is running (start if needed)",
            "Spawn a 'dev' worker",
            "Navigate to Agents page",
            "Verify the new worker appears and is 'running'",
            "Navigate to Logs page",
            "Watch for worker activity in logs",
            "Navigate to Dashboard (Kanban)",
            "Watch for beads moving between columns",
            "After some time, check if any beads moved to 'Done'",
        ],
        verification="Worker spawns, picks up work, and completes beads",
        chrome_mcp_tools=[
            "navigate_page",
            "take_snapshot",
            "click",
            "fill",
            "wait_for",
        ],
    ),
]


@dataclass
class HTTPTestResult:
    """Result of an HTTP-based test."""

    name: str
    passed: bool
    message: str
    duration_ms: float = 0


class HTTPTestRunner:
    """Run HTTP-based tests that don't require Chrome MCP."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.results: list[HTTPTestResult] = []

    def test_health(self) -> HTTPTestResult:
        """Test health endpoint."""
        start = time.time()
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            passed = resp.status_code == 200 and resp.json().get("status") == "ok"
            message = "Health OK" if passed else f"Unexpected: {resp.text[:100]}"
        except Exception as e:
            passed = False
            message = str(e)
        return HTTPTestResult("health", passed, message, (time.time() - start) * 1000)

    def test_pages_load(self) -> HTTPTestResult:
        """Test all pages return 200."""
        pages = ["/", "/admin", "/agents", "/beads", "/logs", "/help", "/docs"]
        start = time.time()
        errors = []
        for page in pages:
            try:
                resp = requests.get(f"{self.base_url}{page}", timeout=10)
                if resp.status_code != 200:
                    errors.append(f"{page}: {resp.status_code}")
            except Exception as e:
                errors.append(f"{page}: {e}")
        passed = len(errors) == 0
        message = "All pages load" if passed else f"Errors: {errors}"
        return HTTPTestResult("pages_load", passed, message, (time.time() - start) * 1000)

    def test_api_beads(self) -> HTTPTestResult:
        """Test beads API returns valid data."""
        start = time.time()
        try:
            resp = requests.get(f"{self.base_url}/api/beads", timeout=30)
            passed = resp.status_code == 200 and isinstance(resp.json(), list)
            count = len(resp.json()) if passed else 0
            message = f"Returned {count} beads" if passed else resp.text[:100]
        except Exception as e:
            passed = False
            message = str(e)
        return HTTPTestResult("api_beads", passed, message, (time.time() - start) * 1000)

    def test_api_agents(self) -> HTTPTestResult:
        """Test agents API returns valid data."""
        start = time.time()
        try:
            resp = requests.get(f"{self.base_url}/api/agents", timeout=10)
            passed = resp.status_code == 200 and isinstance(resp.json(), list)
            count = len(resp.json()) if passed else 0
            message = f"Returned {count} agents" if passed else resp.text[:100]
        except Exception as e:
            passed = False
            message = str(e)
        return HTTPTestResult("api_agents", passed, message, (time.time() - start) * 1000)

    def test_partials(self) -> HTTPTestResult:
        """Test HTMX partial endpoints."""
        partials = ["/partials/kanban", "/partials/agents", "/partials/depgraph"]
        start = time.time()
        errors = []
        for partial in partials:
            try:
                resp = requests.get(f"{self.base_url}{partial}", timeout=30)
                if resp.status_code != 200:
                    errors.append(f"{partial}: {resp.status_code}")
            except Exception as e:
                errors.append(f"{partial}: {e}")
        passed = len(errors) == 0
        message = "All partials load" if passed else f"Errors: {errors}"
        return HTTPTestResult("partials", passed, message, (time.time() - start) * 1000)

    def test_daemon_status(self) -> HTTPTestResult:
        """Test daemon status endpoint."""
        start = time.time()
        try:
            resp = requests.get(f"{self.base_url}/api/workers/daemon/status", timeout=10)
            # 200 (running) or 503 (not running) are both valid
            passed = resp.status_code in [200, 503]
            if resp.status_code == 200:
                message = f"Daemon running: PID {resp.json().get('pid')}"
            else:
                message = "Daemon not running (503)"
        except Exception as e:
            passed = False
            message = str(e)
        return HTTPTestResult("daemon_status", passed, message, (time.time() - start) * 1000)

    def run_all(self) -> list[HTTPTestResult]:
        """Run all HTTP tests."""
        self.results = [
            self.test_health(),
            self.test_pages_load(),
            self.test_api_beads(),
            self.test_api_agents(),
            self.test_partials(),
            self.test_daemon_status(),
        ]
        return self.results

    def print_summary(self) -> None:
        """Print test summary."""
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        total_time = sum(r.duration_ms for r in self.results)

        print(f"\n{'=' * 60}")
        print(f"HTTP Test Results: {passed}/{total} passed ({total_time:.0f}ms)")
        print("=" * 60)

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.name}: {result.message} ({result.duration_ms:.0f}ms)")

        print("=" * 60)


def print_chrome_mcp_scenarios(port: int) -> None:
    """Print test scenarios formatted for Claude to execute with Chrome MCP tools."""
    print("\n" + "=" * 70)
    print("CHROME MCP TEST SCENARIOS")
    print("=" * 70)
    print(f"\nDashboard URL: http://127.0.0.1:{port}")
    print("\nClaude should execute these scenarios using Chrome MCP tools:")
    print("- navigate_page: Navigate to URLs")
    print("- take_snapshot: Capture page state as accessible tree")
    print("- click: Click on elements by uid")
    print("- fill: Fill form inputs")
    print("- wait_for: Wait for text to appear")
    print("- list_console_messages: Check for errors")
    print("-" * 70)

    for scenario in TEST_SCENARIOS:
        print(f"\n## {scenario.name}")
        print(f"Description: {scenario.description}")
        print(f"Tools: {', '.join(scenario.chrome_mcp_tools)}")
        print("\nSteps:")
        for i, step in enumerate(scenario.steps, 1):
            step_with_port = step.format(port=port)
            print(f"  {i}. {step_with_port}")
        print(f"\nVerification: {scenario.verification}")
        print("-" * 50)

    print("\n" + "=" * 70)
    print("END OF TEST SCENARIOS")
    print("=" * 70)


def generate_claude_prompt(port: int) -> str:
    """Generate a prompt for Claude to execute Chrome MCP tests."""
    scenarios_json = []
    for s in TEST_SCENARIOS:
        scenarios_json.append(
            {
                "name": s.name,
                "description": s.description,
                "steps": [step.format(port=port) for step in s.steps],
                "verification": s.verification,
                "tools": s.chrome_mcp_tools,
            }
        )

    prompt = f"""
I need you to test the Multi-Agent Dashboard running at http://127.0.0.1:{port}
using Chrome MCP tools.

## Available Chrome MCP Tools:
- mcp__chrome-devtools__navigate_page: Navigate to URLs
- mcp__chrome-devtools__take_snapshot: Get page accessibility tree
- mcp__chrome-devtools__click: Click elements by uid
- mcp__chrome-devtools__fill: Fill form inputs
- mcp__chrome-devtools__wait_for: Wait for text to appear
- mcp__chrome-devtools__list_console_messages: Check for JS errors
- mcp__chrome-devtools__take_screenshot: Capture visual state

## Test Scenarios:
{json.dumps(scenarios_json, indent=2)}

## Instructions:
1. Start with navigating to the dashboard
2. Take snapshots to understand page structure
3. Execute each scenario step by step
4. Report any failures or unexpected behavior
5. Check for console errors after each page load
6. Take screenshots of any failures

Please begin testing and report results for each scenario.
"""
    return prompt


class DashboardServer:
    """Manages the dashboard server process."""

    def __init__(self, port: int | None = None, project_root: Path | None = None):
        self.port = port or find_free_port()
        self.project_root = project_root or Path.cwd()
        self.process: subprocess.Popen | None = None
        self.base_url = f"http://127.0.0.1:{self.port}"

    def start(self) -> bool:
        """Start the dashboard server."""
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "dashboard.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]

        env = os.environ.copy()
        env["DASHBOARD_PORT"] = str(self.port)

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            print(f"Starting dashboard server on port {self.port}...")
            if wait_for_server("127.0.0.1", self.port):
                print(f"Dashboard ready at {self.base_url}")
                return True
            else:
                print("ERROR: Dashboard failed to start")
                self.stop()
                return False
        except Exception as e:
            print(f"ERROR: Failed to start dashboard: {e}")
            return False

    def stop(self) -> None:
        """Stop the dashboard server."""
        if self.process:
            print("Stopping dashboard server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Interactive Chrome MCP dashboard test runner")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for dashboard (default: auto-assign)",
    )
    parser.add_argument(
        "--http-only",
        action="store_true",
        help="Run HTTP-based tests only (no Chrome MCP needed)",
    )
    parser.add_argument(
        "--print-scenarios",
        action="store_true",
        help="Just print test scenarios without starting server",
    )
    parser.add_argument(
        "--generate-prompt",
        action="store_true",
        help="Generate Claude prompt for Chrome MCP testing",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep server running after tests for manual inspection",
    )

    args = parser.parse_args()

    # If just printing scenarios, don't need a server
    if args.print_scenarios:
        port = args.port or 8000
        print_chrome_mcp_scenarios(port)
        return 0

    if args.generate_prompt:
        port = args.port or 8000
        print(generate_claude_prompt(port))
        return 0

    # Start the dashboard server
    server = DashboardServer(port=args.port)

    # Handle SIGINT gracefully
    def signal_handler(sig: int, frame: Any) -> None:
        print("\nInterrupted, shutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not server.start():
        return 1

    try:
        # Run HTTP tests first
        print("\n" + "=" * 60)
        print("Running HTTP-based tests...")
        print("=" * 60)

        http_runner = HTTPTestRunner(server.base_url)
        http_runner.run_all()
        http_runner.print_summary()

        http_passed = all(r.passed for r in http_runner.results)

        if args.http_only:
            return 0 if http_passed else 1

        # Print Chrome MCP test scenarios
        print_chrome_mcp_scenarios(server.port)

        # Generate and print Claude prompt
        print("\n" + "=" * 70)
        print("CLAUDE PROMPT FOR CHROME MCP TESTING")
        print("=" * 70)
        print(generate_claude_prompt(server.port))

        if args.keep_running:
            print("\n" + "=" * 60)
            print(f"Server running at {server.base_url}")
            print("Press Ctrl+C to stop")
            print("=" * 60)

            # Keep running until interrupted
            while True:
                time.sleep(1)

        return 0 if http_passed else 1

    finally:
        if not args.keep_running:
            server.stop()


if __name__ == "__main__":
    sys.exit(main())
