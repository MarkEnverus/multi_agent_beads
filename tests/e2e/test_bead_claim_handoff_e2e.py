"""E2E tests for bead claim and handoff workflow using Playwright.

This module tests the core workflow of:
1. Dev worker claiming a bead (status changes to in_progress)
2. Dev completes and hands off to QA
3. QA worker picks up the bead

These tests verify the full workflow visible in the dashboard UI.

Run with: uv run pytest tests/e2e/test_bead_claim_handoff_e2e.py -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import requests
from playwright.sync_api import Page, expect

# Skip all tests in CI environments
pytestmark = pytest.mark.skipif(
    bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS")),
    reason="E2E tests require Playwright browsers and local dashboard",
)


class TestBeadClaimWorkflow:
    """Tests for bead claiming workflow via dashboard UI."""

    def _create_test_bead(
        self,
        base_url: str,
        title: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Helper to create a test bead via API.

        Args:
            base_url: The dashboard base URL.
            title: Optional custom title (default: auto-generated).
            labels: Optional labels to apply.

        Returns:
            The created bead data.
        """
        if title is None:
            title = f"Test Bead {uuid.uuid4().hex[:8]}"

        payload = {
            "title": title,
            "description": "E2E test bead for claim workflow",
            "priority": 2,
            "issue_type": "task",
            "labels": labels or ["dev"],
        }

        resp = requests.post(
            f"{base_url}/api/beads",
            json=payload,
            timeout=30,
        )
        assert resp.status_code == 201, f"Failed to create bead: {resp.text}"
        return dict(resp.json())

    def _update_bead_status(
        self,
        base_url: str,
        bead_id: str,
        status: str,
    ) -> None:
        """Helper to update bead status via bd CLI (simulating worker claim).

        Since the API doesn't expose status updates, we use subprocess.
        In a real scenario, this would be done by the worker via bd CLI.
        """
        import subprocess

        result = subprocess.run(
            ["bd", "update", bead_id, f"--status={status}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Failed to update bead status: {result.stderr}"

    def test_kanban_board_shows_all_columns(
        self,
        dashboard_page: Page,
        server_url: str,
    ) -> None:
        """Verify kanban board displays Ready, In Progress, and Done columns."""
        # Wait for kanban board to load
        kanban = dashboard_page.locator("#kanban-board")
        expect(kanban).to_be_visible()

        # Check for all three column headers (use h2 selector to avoid
        # matching the stats row labels which also contain these texts)
        ready_col = dashboard_page.locator("h2", has_text="Ready")
        in_progress_col = dashboard_page.locator("h2", has_text="In Progress")
        done_col = dashboard_page.locator("h2", has_text="Done")

        expect(ready_col).to_be_visible()
        expect(in_progress_col).to_be_visible()
        expect(done_col).to_be_visible()

    def test_new_bead_appears_in_ready_column(
        self,
        dashboard_page: Page,
        server_url: str,
    ) -> None:
        """Verify that a newly created bead appears in the Ready column."""
        # Create a test bead
        bead = self._create_test_bead(server_url)
        bead_id = bead["id"]
        short_id = bead_id.split("-")[-1][:8]

        # Refresh the kanban board
        dashboard_page.reload()
        dashboard_page.wait_for_selector("#kanban-board", state="attached")
        dashboard_page.wait_for_timeout(1500)

        # Look for the bead card in the Ready column
        bead_card = dashboard_page.locator(f'[data-bead-id="{bead_id}"]')
        expect(bead_card).to_be_visible()

        # Verify it shows the short ID
        expect(bead_card.locator(f"text={short_id}")).to_be_visible()

    def test_claimed_bead_moves_to_in_progress(
        self,
        dashboard_page: Page,
        server_url: str,
    ) -> None:
        """Verify that claiming a bead moves it from Ready to In Progress column."""
        # Create a test bead
        bead = self._create_test_bead(server_url)
        bead_id = bead["id"]

        # Refresh to see the bead in Ready
        dashboard_page.reload()
        dashboard_page.wait_for_selector("#kanban-board", state="attached")
        dashboard_page.wait_for_timeout(1500)

        # Verify bead is in Ready column initially
        bead_card = dashboard_page.locator(f'[data-bead-id="{bead_id}"]')
        expect(bead_card).to_be_visible()

        # Claim the bead (simulating worker action)
        self._update_bead_status(server_url, bead_id, "in_progress")

        # Refresh and verify it moved to In Progress
        dashboard_page.reload()
        dashboard_page.wait_for_selector("#kanban-board", state="attached")
        dashboard_page.wait_for_timeout(1500)

        # The bead should now be visible in In Progress column
        # In Progress column is the second one
        in_progress_col = dashboard_page.locator(".kanban-column").nth(1)
        bead_in_progress = in_progress_col.locator(f'[data-bead-id="{bead_id}"]')
        expect(bead_in_progress).to_be_visible()

    def test_bead_shows_owner_when_claimed(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify that a claimed bead displays the owner/assignee."""
        import subprocess

        # Create a test bead
        bead = self._create_test_bead(server_url)
        bead_id = bead["id"]

        # Claim the bead and set an assignee
        result = subprocess.run(
            ["bd", "update", bead_id, "--status=in_progress", "--assignee=test-worker"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Failed to update bead: {result.stderr}"

        # Navigate to dashboard and verify
        page_with_server.goto(server_url)
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        # Check the bead card shows the owner
        bead_card = page_with_server.locator(f'[data-bead-id="{bead_id}"]')
        expect(bead_card).to_be_visible()

        # Owner should be displayed on the card
        owner_text = bead_card.locator("text=test-worker")
        expect(owner_text).to_be_visible()


class TestBeadHandoffWorkflow:
    """Tests for Dev to QA handoff workflow."""

    def _create_test_bead(
        self,
        base_url: str,
        title: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Helper to create a test bead via API."""
        if title is None:
            title = f"Test Bead {uuid.uuid4().hex[:8]}"

        payload = {
            "title": title,
            "description": "E2E test bead for handoff workflow",
            "priority": 2,
            "issue_type": "task",
            "labels": labels or ["dev"],
        }

        resp = requests.post(
            f"{base_url}/api/beads",
            json=payload,
            timeout=30,
        )
        assert resp.status_code == 201, f"Failed to create bead: {resp.text}"
        return dict(resp.json())

    def _close_bead(self, bead_id: str, reason: str = "completed") -> None:
        """Helper to close a bead via bd CLI."""
        import subprocess

        result = subprocess.run(
            ["bd", "close", bead_id, f"--reason={reason}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Failed to close bead: {result.stderr}"

    def test_completed_bead_moves_to_done(
        self,
        dashboard_page: Page,
        server_url: str,
    ) -> None:
        """Verify that completing a bead moves it to the Done column."""
        import subprocess

        # Create a test bead and claim it
        bead = self._create_test_bead(server_url)
        bead_id = bead["id"]

        # First claim it
        result = subprocess.run(
            ["bd", "update", bead_id, "--status=in_progress"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

        # Now complete it
        self._close_bead(bead_id, "test completed")

        # Refresh and verify it's in Done column
        dashboard_page.reload()
        dashboard_page.wait_for_selector("#kanban-board", state="attached")
        dashboard_page.wait_for_timeout(1500)

        # Done column is the third one
        done_col = dashboard_page.locator(".kanban-column").nth(2)
        bead_done = done_col.locator(f'[data-bead-id="{bead_id}"]')
        expect(bead_done).to_be_visible()

    def test_dev_to_qa_handoff_creates_qa_bead(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify that dev completing work can trigger QA work.

        This test simulates the handoff pattern where:
        1. Dev creates a task with 'dev' label
        2. Dev completes and closes the bead
        3. A new QA bead is created (or the same bead transitions)

        Note: The actual handoff logic depends on project workflow.
        This test verifies the UI correctly reflects bead transitions.
        """
        import subprocess

        # Create a dev task
        title = f"Dev Task for QA Handoff {uuid.uuid4().hex[:8]}"
        bead = self._create_test_bead(server_url, title=title, labels=["dev"])
        dev_bead_id = bead["id"]

        # Claim it as dev
        subprocess.run(
            ["bd", "update", dev_bead_id, "--status=in_progress", "--assignee=dev-worker"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Create corresponding QA task
        qa_payload = {
            "title": f"QA: Verify {title}",
            "description": f"QA verification for {dev_bead_id}",
            "priority": 2,
            "issue_type": "task",
            "labels": ["qa"],
        }
        qa_resp = requests.post(
            f"{server_url}/api/beads",
            json=qa_payload,
            timeout=30,
        )
        assert qa_resp.status_code == 201
        qa_bead_id = qa_resp.json()["id"]

        # Complete the dev task
        subprocess.run(
            ["bd", "close", dev_bead_id, "--reason=ready for QA"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Navigate to dashboard
        page_with_server.goto(server_url)
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        # Verify dev bead is in Done
        done_col = page_with_server.locator(".kanban-column").nth(2)
        dev_bead_done = done_col.locator(f'[data-bead-id="{dev_bead_id}"]')
        expect(dev_bead_done).to_be_visible()

        # Verify QA bead is in Ready
        ready_col = page_with_server.locator(".kanban-column").nth(0)
        qa_bead_ready = ready_col.locator(f'[data-bead-id="{qa_bead_id}"]')
        expect(qa_bead_ready).to_be_visible()

    def test_qa_worker_claims_qa_bead(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify QA worker can claim a QA-labeled bead."""
        import subprocess

        # Create a QA task
        title = f"QA Task {uuid.uuid4().hex[:8]}"
        qa_payload = {
            "title": title,
            "description": "QA verification task",
            "priority": 2,
            "issue_type": "task",
            "labels": ["qa"],
        }
        resp = requests.post(
            f"{server_url}/api/beads",
            json=qa_payload,
            timeout=30,
        )
        assert resp.status_code == 201
        qa_bead_id = resp.json()["id"]

        # Claim it as QA worker
        result = subprocess.run(
            ["bd", "update", qa_bead_id, "--status=in_progress", "--assignee=qa-worker"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Failed to claim bead: {result.stderr}"

        # Verify in dashboard
        page_with_server.goto(server_url)
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        # QA bead should be in In Progress
        in_progress_col = page_with_server.locator(".kanban-column").nth(1)
        qa_bead_in_progress = in_progress_col.locator(f'[data-bead-id="{qa_bead_id}"]')
        expect(qa_bead_in_progress).to_be_visible()

        # Should show qa-worker as owner
        owner_text = qa_bead_in_progress.locator("text=qa-worker")
        expect(owner_text).to_be_visible()


class TestFullClaimHandoffCycle:
    """End-to-end test for the complete Dev -> QA cycle."""

    def test_full_dev_qa_cycle(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Test the complete workflow:

        1. Create a dev bead (appears in Ready)
        2. Dev worker claims it (moves to In Progress)
        3. Dev completes and creates QA bead (dev bead to Done)
        4. QA worker claims QA bead (QA bead to In Progress)
        5. QA completes (QA bead to Done)

        This tests the full workflow visible in the dashboard UI.
        """
        import subprocess

        # Step 1: Create dev bead
        dev_title = f"Dev Feature {uuid.uuid4().hex[:8]}"
        dev_payload = {
            "title": dev_title,
            "description": "Feature implementation for E2E test",
            "priority": 2,
            "issue_type": "feature",
            "labels": ["dev"],
        }
        resp = requests.post(
            f"{server_url}/api/beads",
            json=dev_payload,
            timeout=30,
        )
        assert resp.status_code == 201
        dev_bead_id = resp.json()["id"]

        # Navigate and verify bead is in Ready
        page_with_server.goto(server_url)
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        ready_col = page_with_server.locator(".kanban-column").nth(0)
        in_progress_col = page_with_server.locator(".kanban-column").nth(1)
        done_col = page_with_server.locator(".kanban-column").nth(2)

        dev_card_ready = ready_col.locator(f'[data-bead-id="{dev_bead_id}"]')
        expect(dev_card_ready).to_be_visible()

        # Step 2: Dev worker claims the bead
        subprocess.run(
            ["bd", "update", dev_bead_id, "--status=in_progress", "--assignee=developer-1"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Refresh and verify it moved
        page_with_server.reload()
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        ready_col = page_with_server.locator(".kanban-column").nth(0)
        in_progress_col = page_with_server.locator(".kanban-column").nth(1)

        dev_card_progress = in_progress_col.locator(f'[data-bead-id="{dev_bead_id}"]')
        expect(dev_card_progress).to_be_visible()

        # Should no longer be in Ready
        dev_card_ready = ready_col.locator(f'[data-bead-id="{dev_bead_id}"]')
        expect(dev_card_ready).not_to_be_visible()

        # Step 3: Dev completes and creates QA bead
        subprocess.run(
            ["bd", "close", dev_bead_id, "--reason=Feature implemented, ready for QA"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Create QA verification bead
        qa_title = f"QA: Verify {dev_title}"
        qa_payload = {
            "title": qa_title,
            "description": f"Verify implementation of {dev_bead_id}",
            "priority": 2,
            "issue_type": "task",
            "labels": ["qa"],
        }
        resp = requests.post(
            f"{server_url}/api/beads",
            json=qa_payload,
            timeout=30,
        )
        assert resp.status_code == 201
        qa_bead_id = resp.json()["id"]

        # Refresh and verify states
        page_with_server.reload()
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        ready_col = page_with_server.locator(".kanban-column").nth(0)
        done_col = page_with_server.locator(".kanban-column").nth(2)

        # Dev bead should be in Done
        dev_card_done = done_col.locator(f'[data-bead-id="{dev_bead_id}"]')
        expect(dev_card_done).to_be_visible()

        # QA bead should be in Ready
        qa_card_ready = ready_col.locator(f'[data-bead-id="{qa_bead_id}"]')
        expect(qa_card_ready).to_be_visible()

        # Step 4: QA worker claims the bead
        subprocess.run(
            ["bd", "update", qa_bead_id, "--status=in_progress", "--assignee=qa-tester-1"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Refresh and verify
        page_with_server.reload()
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        in_progress_col = page_with_server.locator(".kanban-column").nth(1)

        qa_card_progress = in_progress_col.locator(f'[data-bead-id="{qa_bead_id}"]')
        expect(qa_card_progress).to_be_visible()

        # Step 5: QA completes
        subprocess.run(
            ["bd", "close", qa_bead_id, "--reason=QA passed, feature verified"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Final refresh and verify both beads in Done
        page_with_server.reload()
        page_with_server.wait_for_selector("#kanban-board", state="attached")
        page_with_server.wait_for_timeout(1500)

        done_col = page_with_server.locator(".kanban-column").nth(2)

        # Both beads should be in Done
        dev_card_done = done_col.locator(f'[data-bead-id="{dev_bead_id}"]')
        qa_card_done = done_col.locator(f'[data-bead-id="{qa_bead_id}"]')

        expect(dev_card_done).to_be_visible()
        expect(qa_card_done).to_be_visible()


class TestKanbanAPIIntegration:
    """Tests for kanban board API integration."""

    def test_api_beads_endpoint_returns_data(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify /api/beads returns a list of beads."""
        response = page_with_server.request.get(f"{server_url}/api/beads")
        assert response.status == 200

        data = response.json()
        assert isinstance(data, list)

    def test_api_beads_in_progress_filter(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify /api/beads/in-progress returns only in-progress beads."""
        response = page_with_server.request.get(f"{server_url}/api/beads/in-progress")
        assert response.status == 200

        data = response.json()
        assert isinstance(data, list)
        # All returned beads should have in_progress status
        for bead in data:
            assert bead.get("status") == "in_progress"

    def test_api_beads_ready_filter(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify /api/beads/ready returns ready beads."""
        response = page_with_server.request.get(f"{server_url}/api/beads/ready")
        assert response.status == 200

        data = response.json()
        assert isinstance(data, list)

    def test_partials_kanban_returns_html(
        self,
        page_with_server: Page,
        server_url: str,
    ) -> None:
        """Verify /partials/kanban returns HTML content."""
        response = page_with_server.request.get(f"{server_url}/partials/kanban")
        assert response.status == 200

        # Should contain kanban column structure
        html = response.text()
        assert "Ready" in html
        assert "In Progress" in html
        assert "Done" in html
