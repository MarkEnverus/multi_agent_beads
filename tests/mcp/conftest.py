"""Pytest configuration for MCP browser tests.

These fixtures provide utilities for integrating MCP test results
with the pytest framework. The actual MCP tool calls are made by
Claude agents, but results can be validated through pytest.
"""

import json
from pathlib import Path
from typing import Any

import pytest

# Directories
MCP_TEST_DIR = Path(__file__).parent
REPORTS_DIR = MCP_TEST_DIR / "reports"
SCREENSHOTS_DIR = MCP_TEST_DIR / "screenshots"


@pytest.fixture
def mcp_reports_dir() -> Path:
    """Provide path to MCP test reports directory."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


@pytest.fixture
def mcp_screenshots_dir() -> Path:
    """Provide path to MCP screenshots directory."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS_DIR


@pytest.fixture
def latest_mcp_report(mcp_reports_dir: Path) -> dict[str, Any] | None:
    """Load the most recent MCP test report.

    Returns None if no reports exist.
    """
    reports = sorted(mcp_reports_dir.glob("*.json"), reverse=True)
    if not reports:
        return None

    with open(reports[0]) as f:
        data: dict[str, Any] = json.load(f)
        return data


def get_mcp_report(run_id: str) -> dict[str, Any] | None:
    """Load a specific MCP test report by run_id.

    Args:
        run_id: The test run identifier.

    Returns:
        The report as a dictionary, or None if not found.
    """
    report_path = REPORTS_DIR / f"{run_id}.json"
    if not report_path.exists():
        return None

    with open(report_path) as f:
        data: dict[str, Any] = json.load(f)
        return data
