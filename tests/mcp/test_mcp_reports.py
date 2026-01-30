"""Tests for validating MCP test reports.

These tests verify that MCP test reports are properly structured
and that test results meet expected criteria.
"""

import json
from pathlib import Path

import pytest

from .conftest import REPORTS_DIR


class TestMCPReportStructure:
    """Tests for MCP report structure validation."""

    def test_reports_directory_exists(self, mcp_reports_dir: Path) -> None:
        """Verify the reports directory exists."""
        assert mcp_reports_dir.exists()
        assert mcp_reports_dir.is_dir()

    def test_screenshots_directory_exists(self, mcp_screenshots_dir: Path) -> None:
        """Verify the screenshots directory exists."""
        assert mcp_screenshots_dir.exists()
        assert mcp_screenshots_dir.is_dir()

    @pytest.mark.skipif(
        not any(REPORTS_DIR.glob("*.json")) if REPORTS_DIR.exists() else True,
        reason="No MCP reports available to validate",
    )
    def test_latest_report_structure(self, latest_mcp_report: dict | None) -> None:
        """Verify the latest MCP report has correct structure."""
        if latest_mcp_report is None:
            pytest.skip("No reports available")

        # Required fields
        assert "run_id" in latest_mcp_report
        assert "test_name" in latest_mcp_report
        assert "status" in latest_mcp_report
        assert "start_time" in latest_mcp_report
        assert "end_time" in latest_mcp_report
        assert "duration_seconds" in latest_mcp_report
        assert "steps" in latest_mcp_report
        assert "step_details" in latest_mcp_report

        # Status must be valid
        assert latest_mcp_report["status"] in ("PASSED", "FAILED")

        # Steps summary must have expected fields
        steps = latest_mcp_report["steps"]
        assert "total" in steps
        assert "passed" in steps
        assert "failed" in steps

    @pytest.mark.skipif(
        not any(REPORTS_DIR.glob("*.json")) if REPORTS_DIR.exists() else True,
        reason="No MCP reports available to validate",
    )
    def test_latest_report_step_details(self, latest_mcp_report: dict | None) -> None:
        """Verify step details in the latest report are properly structured."""
        if latest_mcp_report is None:
            pytest.skip("No reports available")

        for step in latest_mcp_report.get("step_details", []):
            assert "name" in step
            assert "status" in step
            assert "timestamp" in step
            assert step["status"] in ("PASS", "FAIL", "SKIP", "ERROR")


class TestMCPReportContent:
    """Tests for validating MCP report content after test runs."""

    @pytest.mark.skipif(
        not any(REPORTS_DIR.glob("smoke_test_*.json")) if REPORTS_DIR.exists() else True,
        reason="No smoke test reports available",
    )
    def test_smoke_test_passed(self) -> None:
        """Verify the most recent smoke test passed."""
        reports = sorted(REPORTS_DIR.glob("smoke_test_*.json"), reverse=True)
        if not reports:
            pytest.skip("No smoke test reports")

        with open(reports[0]) as f:
            report = json.load(f)

        assert report["status"] == "PASSED", (
            f"Smoke test failed: {report.get('summary', 'No summary')}"
        )

    @pytest.mark.skipif(
        not any(REPORTS_DIR.glob("admin_test_*.json")) if REPORTS_DIR.exists() else True,
        reason="No admin test reports available",
    )
    def test_admin_test_no_console_errors(self) -> None:
        """Verify the most recent admin test had no console errors."""
        reports = sorted(REPORTS_DIR.glob("admin_test_*.json"), reverse=True)
        if not reports:
            pytest.skip("No admin test reports")

        with open(reports[0]) as f:
            report = json.load(f)

        console_errors = report.get("console_errors", [])
        assert len(console_errors) == 0, f"Console errors found: {console_errors}"

    @pytest.mark.skipif(
        not any(REPORTS_DIR.glob("*.json")) if REPORTS_DIR.exists() else True,
        reason="No MCP reports available",
    )
    def test_no_network_errors_in_latest(self, latest_mcp_report: dict | None) -> None:
        """Verify the latest test had no network errors."""
        if latest_mcp_report is None:
            pytest.skip("No reports available")

        network_errors = latest_mcp_report.get("network_errors", [])
        # Filter out expected errors (like 503 when daemon not running)
        unexpected_errors = [e for e in network_errors if e.get("status") not in (503,)]
        assert len(unexpected_errors) == 0, f"Unexpected network errors: {unexpected_errors}"
