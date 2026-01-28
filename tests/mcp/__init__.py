"""Chrome MCP-based interactive testing for the Multi-Agent Dashboard.

This module provides test scenarios and utilities for running interactive
browser tests using the Chrome DevTools MCP tools.

Usage:
    # Start dashboard
    uv run python -m dashboard.app

    # In Claude Code with Chrome MCP available:
    # 1. Navigate to dashboard: mcp__chrome-devtools__navigate_page(url="http://localhost:8000")
    # 2. Take snapshot: mcp__chrome-devtools__take_snapshot()
    # 3. Click elements: mcp__chrome-devtools__click(uid="<uid from snapshot>")
    # 4. Check console: mcp__chrome-devtools__list_console_messages()

Available Test Scenarios (in tests/mcp/scenarios/):
    - smoke_test.yaml: Basic dashboard smoke test
    - admin_test.yaml: Admin page functionality test
    - spawn_worker_test.yaml: Worker spawn flow test
    - create_bead_test.yaml: Bead creation flow test

Screenshot Directory:
    tests/mcp/screenshots/ - Screenshots captured during test runs
"""
