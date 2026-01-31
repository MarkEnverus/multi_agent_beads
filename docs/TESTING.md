# Testing Strategy for Multi-Agent Beads

## Decision: No Claude/Anthropic API in CI/CD

### Summary

This project deliberately **does NOT run automated tests that call the Claude/Anthropic API** in CI/CD pipelines. This is a conscious architectural decision, not an oversight.

### Rationale

1. **Cost**: Each API call incurs usage costs. Running tests on every PR, commit, or nightly build would accumulate significant expenses.

2. **Flakiness**: LLM responses are non-deterministic. Tests that depend on specific Claude outputs are inherently flaky and can fail randomly, blocking legitimate PRs.

3. **Rate Limits**: Hitting API rate limits during test runs could block critical CI pipelines or affect production workloads.

4. **Latency**: API calls add significant latency to test runs (seconds to minutes per call), making feedback loops slow.

5. **External Dependency**: CI pipelines should not depend on external service availability. API outages should not block deployments.

### What We Test Instead

- **Unit tests** with mocking for Claude-dependent code paths
- **Integration tests** that verify internal system behavior without API calls
- **E2E tests** for browser automation (run locally, skipped in CI)
- **Static analysis** (ruff, mypy) to catch issues before runtime

---

## Local Testing Guide

### Prerequisites

```bash
# Install dependencies
uv sync

# Verify installation
uv run pytest --version
uv run ruff --version
uv run mypy --version
```

### Running Tests

#### Quick Unit Tests (No External Dependencies)

```bash
# Run all unit tests (fast, no external deps)
uv run pytest tests/test_*.py -v

# Run specific test file
uv run pytest tests/test_dashboard.py -v

# Run with coverage
uv run pytest tests/test_*.py --cov=dashboard --cov-report=html
```

#### Integration Tests (Requires `bd` CLI)

```bash
# These tests require beads CLI to be installed and configured
uv run pytest tests/test_integration.py -v
uv run pytest tests/integration/ -v
```

#### E2E Tests (Requires Dashboard Running)

```bash
# Terminal 1: Start the dashboard
uv run python -m dashboard.app

# Terminal 2: Run E2E tests
uv run pytest tests/e2e/ -v
```

#### Full Workflow Tests (Requires Spawn Infrastructure)

These tests actually spawn Claude Code workers and interact with the Anthropic API.
**Run locally only, with caution due to API costs.**

```bash
# Ensure dashboard and daemon are running
mab start --daemon

# Spawn a test worker (this calls Claude API)
mab spawn --role dev --count 1

# Monitor activity
mab status
tail -f claude.log
```

---

## Test File Reference

| File | Requires API | Requires bd CLI | Requires Dashboard | Notes |
|------|-------------|-----------------|-------------------|-------|
| `tests/test_spawn.py` | No | No | No | Uses mocking |
| `tests/test_dashboard.py` | No | No | No | FastAPI TestClient with mocks |
| `tests/test_dashboard_agents.py` | No | No | No | Agent display tests |
| `tests/test_dashboard_beads.py` | No | No | No | Bead display tests |
| `tests/test_dashboard_logs.py` | No | No | No | Log viewer tests |
| `tests/test_mab_cli.py` | No | No | No | CLI unit tests with mocking |
| `tests/test_rpc.py` | No | No | No | Internal RPC tests |
| `tests/test_workers.py` | No | No | No | Worker unit tests |
| `tests/test_workers_api.py` | No | No | No | Worker API tests |
| `tests/test_pr_validation.py` | No | No | No | PR validation logic |
| `tests/test_daemon.py` | No | No | No | Daemon unit tests |
| `tests/test_towns.py` | No | No | No | Towns feature tests |
| `tests/test_log_validation.py` | No | No | No | Log format validation |
| `tests/test_integration.py` | No | Yes | No | Bead lifecycle tests |
| `tests/integration/test_multi_agent.py` | No | Yes | No | Agent coordination tests |
| `tests/integration/test_agent_conflicts.py` | No | Yes | No | Conflict handling tests |
| `tests/integration/test_concurrent_access.py` | No | Yes | No | Concurrency tests |
| `tests/integration/test_mab_system.py` | No | Yes | No | MAB system tests |
| `tests/integration/test_db_sync_recovery.py` | No | Yes | No | DB sync tests |
| `tests/integration/test_api_fullstack.py` | No | Yes | Yes | Full API stack tests |
| `tests/e2e/test_chrome_mcp_e2e.py` | No | No | Yes | Chrome MCP tests (skipped in CI) |
| `tests/e2e/test_interactive_e2e.py` | No | No | Yes | Playwright E2E (skipped in CI) |
| `tests/e2e/test_dashboard_e2e.py` | No | No | Yes | Dashboard E2E tests |
| `tests/e2e/test_admin_e2e.py` | No | No | Yes | Admin page E2E tests |
| `tests/mcp/test_mcp_reports.py` | No | No | No | MCP report formatting |

---

## CI/CD Recommendations

### What to Run in CI

```yaml
# Example GitHub Actions workflow
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv sync

      # Static analysis
      - name: Lint with ruff
        run: uv run ruff check .

      - name: Type check with mypy
        run: uv run mypy dashboard/ --ignore-missing-imports

      # Unit tests only (no external deps)
      - name: Run unit tests
        run: uv run pytest tests/test_*.py -v --ignore=tests/test_integration.py
```

### What to Skip in CI

- `tests/test_integration.py` - Requires `bd` CLI
- `tests/integration/` - Requires `bd` CLI and real bead database
- `tests/e2e/` - Requires browser automation (already has CI skip markers)
- Any manual worker spawn tests

### Pre-commit Hooks (Optional)

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff
        entry: uv run ruff check
        language: system
        types: [python]

      - id: mypy
        name: mypy
        entry: uv run mypy
        language: system
        types: [python]
        args: [--ignore-missing-imports]

      - id: pytest-quick
        name: pytest-quick
        entry: uv run pytest tests/test_dashboard.py tests/test_spawn.py -q
        language: system
        pass_filenames: false
        always_run: true
```

---

## Writing New Tests

### Guidelines

1. **Prefer mocking over real API calls**
   ```python
   from unittest.mock import patch

   def test_spawn_worker():
       with patch("mab.daemon.spawn_claude_process") as mock_spawn:
           mock_spawn.return_value = {"pid": 12345}
           result = spawn_worker("dev")
           assert result["pid"] == 12345
   ```

2. **Use skip markers for tests requiring infrastructure**
   ```python
   import pytest
   import os

   pytestmark = pytest.mark.skipif(
       bool(os.environ.get("CI")),
       reason="Requires local infrastructure"
   )
   ```

3. **Isolate filesystem operations**
   ```python
   def test_config_creation(tmp_path):
       config_file = tmp_path / "config.yaml"
       create_config(config_file)
       assert config_file.exists()
   ```

4. **Test behavior, not implementation**
   - Good: "Does this function return the right result?"
   - Bad: "Does this function call that other function?"

---

## Troubleshooting

### Tests Hang or Timeout

- Check if dashboard is running (for E2E tests)
- Check if `bd` daemon is running (for integration tests)
- Look for deadlocks in concurrent tests

### Tests Fail with "bd not found"

```bash
# Ensure beads CLI is installed
which bd
# Should return: /path/to/bd

# If not installed, integration tests will fail
# This is expected in CI - skip these tests
```

### Flaky E2E Tests

- E2E tests can be flaky due to timing issues
- Increase timeouts if needed
- Run in isolation: `pytest tests/e2e/test_file.py::TestClass::test_method -v`

### Mock Not Working

```python
# Common issue: patching the wrong location
# Patch where the function is USED, not where it's DEFINED

# Wrong:
with patch("some_module.function"):  # where it's defined
    ...

# Right:
with patch("my_module.function"):  # where it's imported/used
    ...
```

---

## Summary

| Test Type | Run in CI | Run Locally | API Calls | Notes |
|-----------|-----------|-------------|-----------|-------|
| Unit tests | Yes | Yes | No | Fast, isolated |
| Static analysis | Yes | Yes | No | ruff, mypy |
| Integration tests | No | Yes | No | Require bd CLI |
| E2E tests | No | Yes | No | Require browser |
| Worker spawn tests | No | Yes | **Yes** | Cost money |
| Full workflow tests | No | Yes | **Yes** | Cost money |

The key principle: **CI tests must be free, fast, and deterministic.**
