#!/usr/bin/env python3
"""Interactive Chrome MCP Dashboard Test Runner.

This script provides an interactive testing framework for the Multi-Agent Dashboard
using Chrome DevTools MCP tools. It starts the dashboard server and guides Claude
through executing Chrome MCP commands to test all functionality.

Usage:
    # Start the test environment (dashboard server)
    python tests/mcp/run_chrome_mcp_test.py start

    # Check if server is ready
    python tests/mcp/run_chrome_mcp_test.py status

    # Stop the test environment
    python tests/mcp/run_chrome_mcp_test.py stop

    # Run with custom port
    python tests/mcp/run_chrome_mcp_test.py start --port 8888

Prerequisites:
    - Chrome MCP server connected to Claude Code
    - Python 3.10+ with uvicorn installed

After starting, Claude can use Chrome MCP tools to test:
    1. Navigate to pages: mcp__chrome-devtools__navigate_page
    2. Take snapshots: mcp__chrome-devtools__take_snapshot
    3. Click elements: mcp__chrome-devtools__click
    4. Fill forms: mcp__chrome-devtools__fill
    5. Check console: mcp__chrome-devtools__list_console_messages
    6. Verify network: mcp__chrome-devtools__list_network_requests
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
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

# Constants
DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"
PID_FILE = Path(__file__).parent / ".dashboard_test_server.pid"
REPORTS_DIR = Path(__file__).parent / "reports"
SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"


@dataclass
class TestResult:
    """Result of a single test step."""

    name: str
    action: str
    passed: bool
    message: str
    duration_ms: float = 0
    screenshot_path: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class TestReport:
    """Complete test run report."""

    run_id: str
    scenario_name: str
    started_at: str
    completed_at: str | None = None
    results: list[TestResult] = field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    total_duration_ms: float = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "scenario_name": self.scenario_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "total_duration_ms": self.total_duration_ms,
            "results": [
                {
                    "name": r.name,
                    "action": r.action,
                    "passed": r.passed,
                    "message": r.message,
                    "duration_ms": r.duration_ms,
                    "screenshot_path": r.screenshot_path,
                    "errors": r.errors,
                }
                for r in self.results
            ],
        }


def find_free_port() -> int:
    """Find an available port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((DEFAULT_HOST, 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def is_port_in_use(port: int, host: str = DEFAULT_HOST) -> bool:
    """Check if a port is already in use."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        return sock.connect_ex((host, port)) == 0


def wait_for_server(port: int, host: str = DEFAULT_HOST, timeout: int = 30) -> bool:
    """Wait for server to be ready, return True if ready within timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://{host}:{port}/health", timeout=2)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def start_dashboard_server(port: int = DEFAULT_PORT) -> int | None:
    """Start the dashboard server and return its PID.

    Returns None if server is already running or failed to start.
    """
    # Check if already running
    if is_port_in_use(port):
        print(f"Port {port} is already in use. Server may already be running.")
        try:
            resp = requests.get(f"http://{DEFAULT_HOST}:{port}/health", timeout=2)
            if resp.status_code == 200:
                print(f"Dashboard is healthy at http://{DEFAULT_HOST}:{port}")
                return None
        except requests.RequestException:
            print(f"Port {port} in use but not responding to health check.")
            return None

    # Start the server
    project_root = Path(__file__).parent.parent.parent
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dashboard.app:app",
            "--host",
            DEFAULT_HOST,
            "--port",
            str(port),
        ],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    print(f"Starting dashboard server (PID: {process.pid})...")

    # Wait for server to be ready
    if wait_for_server(port):
        # Save PID for later cleanup
        PID_FILE.write_text(f"{process.pid}\n{port}")
        print(f"\n✓ Dashboard server running at http://{DEFAULT_HOST}:{port}")
        print(f"  PID file: {PID_FILE}")
        return process.pid
    else:
        print("\n✗ Dashboard server failed to start within timeout")
        process.terminate()
        return None


def stop_dashboard_server() -> bool:
    """Stop the dashboard server if running."""
    if not PID_FILE.exists():
        print("No PID file found. Server may not be running.")
        return False

    content = PID_FILE.read_text().strip().split("\n")
    pid = int(content[0])
    port = int(content[1]) if len(content) > 1 else DEFAULT_PORT

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to process {pid}")

        # Wait for process to die
        for _ in range(10):
            try:
                os.kill(pid, 0)  # Check if process exists
                time.sleep(0.5)
            except ProcessLookupError:
                break

        PID_FILE.unlink()
        print(f"✓ Dashboard server stopped (was at http://{DEFAULT_HOST}:{port})")
        return True
    except ProcessLookupError:
        print(f"Process {pid} not found. Cleaning up PID file.")
        PID_FILE.unlink()
        return False
    except PermissionError:
        print(f"Permission denied to kill process {pid}")
        return False


def get_server_status() -> dict[str, Any]:
    """Get the current status of the test server."""
    status = {
        "running": False,
        "pid": None,
        "port": None,
        "url": None,
        "health": None,
    }

    if PID_FILE.exists():
        content = PID_FILE.read_text().strip().split("\n")
        pid = int(content[0])
        port = int(content[1]) if len(content) > 1 else DEFAULT_PORT

        # Check if process is still running
        try:
            os.kill(pid, 0)
            status["pid"] = pid
            status["port"] = port
            status["url"] = f"http://{DEFAULT_HOST}:{port}"

            # Check health
            try:
                resp = requests.get(f"{status['url']}/health", timeout=2)
                status["running"] = resp.status_code == 200
                status["health"] = resp.json() if resp.status_code == 200 else None
            except requests.RequestException:
                pass
        except ProcessLookupError:
            pass

    return status


def print_test_instructions(port: int = DEFAULT_PORT) -> None:
    """Print instructions for running Chrome MCP tests."""
    base_url = f"http://{DEFAULT_HOST}:{port}"

    instructions = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     Chrome MCP Dashboard Test Runner                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Dashboard running at: {base_url:<55} ║
╚══════════════════════════════════════════════════════════════════════════════╝

QUICK TEST SEQUENCE (copy and execute each step):

1. Navigate to Dashboard
   mcp__chrome-devtools__navigate_page(url="{base_url}")

2. Take Snapshot (see page structure)
   mcp__chrome-devtools__take_snapshot()

3. Click Navigation Links
   - Find 'Admin' link uid in snapshot and click it
   - mcp__chrome-devtools__click(uid="<admin-link-uid>")

4. Verify No Console Errors
   mcp__chrome-devtools__list_console_messages(types=["error", "warn"])

5. Test Worker Spawn Form (on /admin page)
   - Find spawn-role select uid
   - mcp__chrome-devtools__fill(uid="<spawn-role-uid>", value="dev")
   - Find spawn button uid
   - mcp__chrome-devtools__click(uid="<spawn-button-uid>")

6. Check Network Requests
   mcp__chrome-devtools__list_network_requests()

═══════════════════════════════════════════════════════════════════════════════

FULL TEST CHECKLIST:

□ Navigation Tests
  □ Dashboard (/) - Kanban board visible
  □ Admin (/admin) - Daemon status, spawn form visible
  □ Agents (/agents) - Agent list visible
  □ Beads (/beads) - Bead management visible
  □ Logs (/logs) - Log viewer visible
  □ Help (/help) - Documentation visible

□ Interactive Tests
  □ Click "Refresh Now" button on dashboard
  □ Toggle Dependencies view
  □ Open New Bead modal
  □ Fill spawn worker form
  □ Submit spawn worker form

□ Worker Spawn Tests (one of each type)
  □ Spawn Developer worker
  □ Spawn QA worker
  □ Spawn Reviewer worker
  □ Spawn Tech Lead worker
  □ Spawn Manager worker

□ Verification
  □ No JavaScript errors in console
  □ All API requests return 200/503
  □ Workers appear in list after spawn
  □ Toast notifications show for actions

═══════════════════════════════════════════════════════════════════════════════

SCREENSHOTS (save test evidence):
  mcp__chrome-devtools__take_screenshot(filePath="tests/mcp/screenshots/<name>.png")

To stop the server:
  python tests/mcp/run_chrome_mcp_test.py stop
"""
    print(instructions)


def save_test_report(report: TestReport) -> Path:
    """Save test report to JSON file."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{report.run_id}.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2))
    return report_path


def create_test_report(scenario_name: str) -> TestReport:
    """Create a new test report."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return TestReport(
        run_id=run_id,
        scenario_name=scenario_name,
        started_at=datetime.now().isoformat(),
    )


def print_status() -> None:
    """Print current server status."""
    status = get_server_status()

    if status["running"]:
        print("\n✓ Dashboard Test Server: RUNNING")
        print(f"  URL: {status['url']}")
        print(f"  PID: {status['pid']}")
        print(f"  Health: {status['health']}")
    else:
        print("\n✗ Dashboard Test Server: NOT RUNNING")
        if status["pid"]:
            print(f"  Stale PID file exists (PID: {status['pid']})")
            print("  Run 'stop' to clean up.")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Interactive Chrome MCP Dashboard Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s start          # Start dashboard server for testing
  %(prog)s status         # Check if server is running
  %(prog)s stop           # Stop the test server
  %(prog)s start -p 8888  # Start on custom port
        """,
    )

    parser.add_argument(
        "command",
        choices=["start", "stop", "status"],
        help="Command to run",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to run dashboard on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress test instructions output",
    )

    args = parser.parse_args()

    if args.command == "start":
        pid = start_dashboard_server(args.port)
        if pid is not None or is_port_in_use(args.port):
            if not args.quiet:
                print_test_instructions(args.port)
            return 0
        return 1

    elif args.command == "stop":
        return 0 if stop_dashboard_server() else 1

    elif args.command == "status":
        print_status()
        status = get_server_status()
        return 0 if status["running"] else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
