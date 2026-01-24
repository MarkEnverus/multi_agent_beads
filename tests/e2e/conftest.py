"""Playwright E2E test fixtures for the Multi-Agent Dashboard."""

import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import closing
from typing import cast

import pytest
from playwright.sync_api import Browser, Page


def find_free_port() -> int:
    """Find an available port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return cast(int, s.getsockname()[1])


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
    """Start the dashboard server for E2E tests.

    This fixture starts uvicorn as a subprocess and waits for it to be ready.
    The server is shut down after all tests complete.
    """
    # Start uvicorn with the test port
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

    # Wait for server to be ready (up to 10 seconds)
    max_retries = 20
    for i in range(max_retries):
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                result = sock.connect_ex(("127.0.0.1", server_port))
                if result == 0:
                    break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        process.kill()
        raise RuntimeError(f"Dashboard server failed to start on port {server_port}")

    yield process

    # Cleanup: terminate the server
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="session")
def browser_context_args() -> dict:
    """Configure browser context arguments."""
    return {
        "viewport": {"width": 1280, "height": 720},
        "ignore_https_errors": True,
    }


@pytest.fixture
def page_with_server(
    dashboard_server: subprocess.Popen,
    page: Page,
    server_url: str,
) -> Page:
    """Provide a page connected to the running dashboard server.

    This fixture ensures the server is running before providing the page.
    """
    return page


@pytest.fixture
def dashboard_page(page_with_server: Page, server_url: str) -> Page:
    """Navigate to the dashboard and wait for initial load."""
    page_with_server.goto(server_url)
    # Wait for HTMX to load initial content
    page_with_server.wait_for_selector("#kanban-board", state="attached")
    # Give HTMX time to fetch partials
    page_with_server.wait_for_timeout(1000)
    return page_with_server


@pytest.fixture
def mobile_page(
    dashboard_server: subprocess.Popen,
    browser: Browser,
    server_url: str,
) -> Generator[Page, None, None]:
    """Provide a page with mobile viewport for responsive testing."""
    context = browser.new_context(
        viewport={"width": 375, "height": 667},  # iPhone SE dimensions
    )
    page = context.new_page()
    page.goto(server_url)
    page.wait_for_selector("#kanban-board", state="attached")
    page.wait_for_timeout(1000)
    yield page
    context.close()


@pytest.fixture
def tablet_page(
    dashboard_server: subprocess.Popen,
    browser: Browser,
    server_url: str,
) -> Generator[Page, None, None]:
    """Provide a page with tablet viewport for responsive testing."""
    context = browser.new_context(
        viewport={"width": 768, "height": 1024},  # iPad dimensions
    )
    page = context.new_page()
    page.goto(server_url)
    page.wait_for_selector("#kanban-board", state="attached")
    page.wait_for_timeout(1000)
    yield page
    context.close()
