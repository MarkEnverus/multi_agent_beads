#!/usr/bin/env python3
"""MCP Test Runner Utility.

This script provides utilities for running and reporting Chrome MCP-based
browser tests. It's designed to be used by Claude agents during QA testing.

Usage by Claude Agent:
    # Import and use the utilities
    from scripts.mcp_test_runner import MCPTestRunner

    runner = MCPTestRunner("smoke_test")
    runner.log_step("Navigate to Dashboard", "PASS", "Page loaded successfully")
    runner.log_step("Verify Header", "PASS", "Kanban Board header visible")
    runner.save_screenshot("step_1.png", screenshot_data)
    runner.complete(passed=True)

    # Or use the command-line interface to view test reports
    python scripts/mcp_test_runner.py --list
    python scripts/mcp_test_runner.py --report smoke_test_20240128_123456
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
SCREENSHOTS_DIR = PROJECT_ROOT / "tests" / "mcp" / "screenshots"
REPORTS_DIR = PROJECT_ROOT / "tests" / "mcp" / "reports"
SCENARIOS_DIR = PROJECT_ROOT / "tests" / "mcp" / "scenarios"


class MCPTestRunner:
    """Utility class for running and logging MCP-based browser tests.

    This class provides structured logging, screenshot management, and
    test result reporting for Chrome MCP-based testing.
    """

    def __init__(self, test_name: str) -> None:
        """Initialize a test run.

        Args:
            test_name: Name of the test scenario being run.
        """
        self.test_name = test_name
        self.start_time = datetime.now()
        self.run_id = f"{test_name}_{self.start_time.strftime('%Y%m%d_%H%M%S')}"
        self.steps: list[dict[str, Any]] = []
        self.screenshots: list[dict[str, str]] = []
        self.console_errors: list[str] = []
        self.network_errors: list[dict[str, Any]] = []

        # Ensure directories exist
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        # Create run-specific screenshot directory
        self.screenshot_dir = SCREENSHOTS_DIR / self.run_id
        self.screenshot_dir.mkdir(exist_ok=True)

    def log_step(
        self,
        step_name: str,
        status: str,
        details: str = "",
        snapshot_excerpt: str = "",
    ) -> None:
        """Log a test step result.

        Args:
            step_name: Name of the test step.
            status: Status (PASS, FAIL, SKIP, ERROR).
            details: Additional details about the step result.
            snapshot_excerpt: Relevant excerpt from page snapshot.
        """
        step = {
            "name": step_name,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        }
        if snapshot_excerpt:
            step["snapshot_excerpt"] = snapshot_excerpt

        self.steps.append(step)

        # Also print to stdout for real-time feedback
        status_emoji = {
            "PASS": "\u2705",
            "FAIL": "\u274c",
            "SKIP": "\u23ed\ufe0f",
            "ERROR": "\u26a0\ufe0f",
        }.get(status, "\u2753")
        print(f"{status_emoji} {step_name}: {status}")
        if details:
            print(f"   {details}")

    def log_console_error(self, error: str) -> None:
        """Log a console error found during testing.

        Args:
            error: The console error message.
        """
        self.console_errors.append(error)

    def log_network_error(self, url: str, status: int, error: str = "") -> None:
        """Log a network error found during testing.

        Args:
            url: The URL that failed.
            status: HTTP status code.
            error: Additional error details.
        """
        self.network_errors.append({"url": url, "status": status, "error": error})

    def save_screenshot(self, filename: str, note: str = "") -> str:
        """Record a screenshot file.

        Args:
            filename: Name of the screenshot file.
            note: Note about what the screenshot shows.

        Returns:
            Full path to where screenshot should be saved.
        """
        screenshot_path = self.screenshot_dir / filename
        self.screenshots.append({"filename": filename, "path": str(screenshot_path), "note": note})
        return str(screenshot_path)

    def complete(self, passed: bool, summary: str = "") -> dict[str, Any]:
        """Complete the test run and generate report.

        Args:
            passed: Whether the test passed overall.
            summary: Summary of the test results.

        Returns:
            The complete test report as a dictionary.
        """
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        # Calculate step statistics
        passed_steps = sum(1 for s in self.steps if s["status"] == "PASS")
        failed_steps = sum(1 for s in self.steps if s["status"] == "FAIL")
        skipped_steps = sum(1 for s in self.steps if s["status"] == "SKIP")

        report = {
            "run_id": self.run_id,
            "test_name": self.test_name,
            "status": "PASSED" if passed else "FAILED",
            "start_time": self.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_seconds": duration,
            "summary": summary,
            "steps": {
                "total": len(self.steps),
                "passed": passed_steps,
                "failed": failed_steps,
                "skipped": skipped_steps,
            },
            "step_details": self.steps,
            "screenshots": self.screenshots,
            "console_errors": self.console_errors,
            "network_errors": self.network_errors,
        }

        # Save report
        report_path = REPORTS_DIR / f"{self.run_id}.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        # Print summary
        print("\n" + "=" * 60)
        print(f"TEST COMPLETE: {self.test_name}")
        print(f"Status: {'PASSED' if passed else 'FAILED'}")
        print(f"Duration: {duration:.2f}s")
        print(f"Steps: {passed_steps}/{len(self.steps)} passed")
        if self.console_errors:
            print(f"Console Errors: {len(self.console_errors)}")
        if self.network_errors:
            print(f"Network Errors: {len(self.network_errors)}")
        print(f"Report saved to: {report_path}")
        print("=" * 60)

        return report


def list_scenarios() -> None:
    """List available test scenarios."""
    print("\nAvailable MCP Test Scenarios:")
    print("-" * 40)

    if not SCENARIOS_DIR.exists():
        print("No scenarios directory found.")
        return

    for scenario_file in sorted(SCENARIOS_DIR.glob("*.yaml")):
        # Read first few lines to get name/description
        with open(scenario_file) as f:
            lines = f.readlines()[:10]

        name = scenario_file.stem
        desc = ""
        for line in lines:
            if line.strip().startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                break

        print(f"  {name}")
        if desc:
            print(f"    {desc}")

    print(f"\nScenarios directory: {SCENARIOS_DIR}")


def list_reports() -> None:
    """List available test reports."""
    print("\nRecent MCP Test Reports:")
    print("-" * 60)

    if not REPORTS_DIR.exists():
        print("No reports directory found.")
        return

    reports = sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:10]

    if not reports:
        print("No reports found.")
        return

    for report_file in reports:
        with open(report_file) as f:
            report = json.load(f)

        status = report.get("status", "UNKNOWN")
        status_emoji = "\u2705" if status == "PASSED" else "\u274c"
        duration = report.get("duration_seconds", 0)
        steps = report.get("steps", {})

        print(f"{status_emoji} {report_file.stem}")
        print(
            f"   Status: {status} | Duration: {duration:.2f}s | "
            f"Steps: {steps.get('passed', 0)}/{steps.get('total', 0)}"
        )

    print(f"\nReports directory: {REPORTS_DIR}")


def show_report(run_id: str) -> None:
    """Show details of a specific test report."""
    report_path = REPORTS_DIR / f"{run_id}.json"

    if not report_path.exists():
        print(f"Report not found: {run_id}")
        return

    with open(report_path) as f:
        report = json.load(f)

    print(f"\n{'=' * 60}")
    print(f"TEST REPORT: {report['test_name']}")
    print(f"{'=' * 60}")
    print(f"Run ID: {report['run_id']}")
    print(f"Status: {report['status']}")
    print(f"Start: {report['start_time']}")
    print(f"Duration: {report['duration_seconds']:.2f}s")

    if report.get("summary"):
        print(f"\nSummary: {report['summary']}")

    print(f"\nStep Results ({report['steps']['passed']}/{report['steps']['total']} passed):")
    print("-" * 40)

    for step in report.get("step_details", []):
        status_emoji = {
            "PASS": "\u2705",
            "FAIL": "\u274c",
            "SKIP": "\u23ed\ufe0f",
            "ERROR": "\u26a0\ufe0f",
        }.get(step["status"], "\u2753")
        print(f"  {status_emoji} {step['name']}: {step['status']}")
        if step.get("details"):
            print(f"     {step['details']}")

    if report.get("console_errors"):
        print(f"\nConsole Errors ({len(report['console_errors'])}):")
        for error in report["console_errors"][:5]:
            print(f"  - {error[:100]}")

    if report.get("network_errors"):
        print(f"\nNetwork Errors ({len(report['network_errors'])}):")
        for error in report["network_errors"][:5]:
            print(f"  - {error['url']}: {error['status']}")

    if report.get("screenshots"):
        print(f"\nScreenshots ({len(report['screenshots'])}):")
        for screenshot in report["screenshots"]:
            print(f"  - {screenshot['filename']}: {screenshot.get('note', '')}")


def main() -> None:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="MCP Test Runner - View and manage Chrome MCP-based browser tests"
    )
    parser.add_argument("--list", action="store_true", help="List available test scenarios")
    parser.add_argument("--reports", action="store_true", help="List recent test reports")
    parser.add_argument("--report", type=str, help="Show details of a specific report by run_id")

    args = parser.parse_args()

    if args.list:
        list_scenarios()
    elif args.reports:
        list_reports()
    elif args.report:
        show_report(args.report)
    else:
        # Default: show both scenarios and recent reports
        list_scenarios()
        print()
        list_reports()


if __name__ == "__main__":
    main()
