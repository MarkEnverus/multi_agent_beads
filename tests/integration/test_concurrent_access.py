"""Concurrent access integration tests.

This module tests the system's behavior under concurrent access scenarios:
- Multiple simultaneous API requests
- Race conditions in bead operations
- Cache behavior under load
- Thread safety of the service layer
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient

from dashboard.services import BeadService

from .conftest import (
    TEST_PREFIX,
    create_test_bead,
    delete_test_bead,
    run_bd,
    update_bead_status,
)


class TestConcurrentReads:
    """Test concurrent read operations."""

    def test_concurrent_list_beads(self, test_client: TestClient) -> None:
        """Multiple concurrent list requests should all succeed."""
        results: list[tuple[int, int]] = []
        errors: list[Exception] = []

        def fetch_beads() -> tuple[int, int]:
            response = test_client.get("/api/beads")
            return response.status_code, len(response.json())

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_beads) for _ in range(20)]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    errors.append(e)

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert all(status == 200 for status, _ in results)
        # All responses should have the same count (data consistency)
        counts = [count for _, count in results]
        assert len(set(counts)) == 1, "Inconsistent counts from concurrent reads"

    def test_concurrent_kanban_partials(self, test_client: TestClient) -> None:
        """Multiple concurrent kanban requests should all succeed."""
        results: list[int] = []

        def fetch_kanban() -> int:
            response = test_client.get("/partials/kanban")
            return response.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_kanban) for _ in range(15)]
            for future in as_completed(futures):
                results.append(future.result())

        assert all(status == 200 for status in results)

    def test_concurrent_bead_detail_same_bead(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Multiple concurrent requests for same bead should all succeed."""
        results: list[tuple[int, str]] = []

        def fetch_bead() -> tuple[int, str]:
            response = test_client.get(f"/api/beads/{test_bead}")
            bead_id = response.json().get("id", "") if response.status_code == 200 else ""
            return response.status_code, bead_id

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_bead) for _ in range(10)]
            for future in as_completed(futures):
                results.append(future.result())

        assert all(status == 200 for status, _ in results)
        assert all(bead_id == test_bead for _, bead_id in results)

    def test_concurrent_different_endpoints(self, test_client: TestClient) -> None:
        """Concurrent requests to different endpoints should all succeed."""
        results: dict[str, list[int]] = {
            "beads": [],
            "ready": [],
            "agents": [],
            "health": [],
            "kanban": [],
        }

        def fetch_beads() -> tuple[str, int]:
            response = test_client.get("/api/beads")
            return "beads", response.status_code

        def fetch_ready() -> tuple[str, int]:
            response = test_client.get("/api/beads/ready")
            return "ready", response.status_code

        def fetch_agents() -> tuple[str, int]:
            response = test_client.get("/api/agents")
            return "agents", response.status_code

        def fetch_health() -> tuple[str, int]:
            response = test_client.get("/health")
            return "health", response.status_code

        def fetch_kanban() -> tuple[str, int]:
            response = test_client.get("/partials/kanban")
            return "kanban", response.status_code

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for _ in range(4):
                futures.extend(
                    [
                        executor.submit(fetch_beads),
                        executor.submit(fetch_ready),
                        executor.submit(fetch_agents),
                        executor.submit(fetch_health),
                        executor.submit(fetch_kanban),
                    ]
                )

            for future in as_completed(futures):
                endpoint, status = future.result()
                results[endpoint].append(status)

        for endpoint, statuses in results.items():
            assert all(s == 200 for s in statuses), f"{endpoint} had non-200 responses"


class TestConcurrentWrites:
    """Test concurrent write operations."""

    def test_concurrent_bead_creation(
        self,
        test_client: TestClient,
        cleanup_created_beads: list[str],
    ) -> None:
        """Multiple concurrent create requests should all succeed."""
        created_ids: list[str] = []
        errors: list[str] = []
        lock = threading.Lock()

        def create_bead(index: int) -> None:
            unique_id = uuid.uuid4().hex[:8]
            data = {
                "title": f"{TEST_PREFIX}_concurrent_{unique_id}_{index}",
                "description": f"Concurrent create test {index}",
                "priority": 3,
            }
            try:
                response = test_client.post("/api/beads", json=data)
                if response.status_code == 201:
                    bead_id = response.json()["id"]
                    with lock:
                        created_ids.append(bead_id)
                else:
                    with lock:
                        errors.append(f"Create failed: {response.status_code}")
            except Exception as e:
                with lock:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_bead, i) for i in range(10)]
            for future in as_completed(futures):
                pass  # Just wait for completion

        # Track for cleanup
        cleanup_created_beads.extend(created_ids)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(created_ids) == 10, f"Only {len(created_ids)} beads created"

        # All created beads should be visible
        BeadService.invalidate_cache()
        response = test_client.get("/api/beads")
        all_ids = [b["id"] for b in response.json()]
        for bead_id in created_ids:
            assert bead_id in all_ids


class TestConcurrentReadWrite:
    """Test concurrent read and write operations."""

    def test_reads_during_create(
        self,
        test_client: TestClient,
        cleanup_created_beads: list[str],
    ) -> None:
        """Reads should succeed even during write operations."""
        created_ids: list[str] = []
        read_results: list[int] = []
        lock = threading.Lock()

        def create_bead(index: int) -> None:
            unique_id = uuid.uuid4().hex[:8]
            data = {"title": f"{TEST_PREFIX}_rw_{unique_id}_{index}"}
            response = test_client.post("/api/beads", json=data)
            if response.status_code == 201:
                with lock:
                    created_ids.append(response.json()["id"])

        def read_beads() -> None:
            response = test_client.get("/api/beads")
            with lock:
                read_results.append(response.status_code)

        with ThreadPoolExecutor(max_workers=10) as executor:
            # Mix of reads and writes
            futures = []
            for i in range(5):
                futures.append(executor.submit(create_bead, i))
                futures.extend([executor.submit(read_beads) for _ in range(3)])

            for future in as_completed(futures):
                pass

        cleanup_created_beads.extend(created_ids)

        # All reads should succeed
        assert all(status == 200 for status in read_results)

    def test_reads_during_status_update(
        self,
        test_client: TestClient,
        multiple_test_beads: list[str],
    ) -> None:
        """Reads should return consistent data during updates."""
        read_results: list[tuple[int, int]] = []
        lock = threading.Lock()

        def update_beads() -> None:
            for bead_id in multiple_test_beads[:3]:
                try:
                    update_bead_status(bead_id, "in_progress")
                except Exception:
                    pass  # Ignore update errors

        def read_beads() -> None:
            response = test_client.get("/api/beads")
            with lock:
                read_results.append((response.status_code, len(response.json())))

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(update_beads),
                *[executor.submit(read_beads) for _ in range(10)],
            ]
            for future in as_completed(futures):
                pass

        # All reads should succeed
        assert all(status == 200 for status, _ in read_results)


class TestCacheUnderConcurrency:
    """Test cache behavior under concurrent access."""

    def test_cache_consistency_during_rapid_reads(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Cache should return consistent data under rapid reads."""
        results: list[str] = []
        lock = threading.Lock()

        def fetch_bead_title() -> None:
            response = test_client.get(f"/api/beads/{test_bead}")
            if response.status_code == 200:
                with lock:
                    results.append(response.json()["title"])

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(fetch_bead_title) for _ in range(50)]
            for future in as_completed(futures):
                pass

        # All titles should be identical
        assert len(set(results)) == 1, "Inconsistent cache results"

    def test_cache_invalidation_visibility(
        self,
        test_client: TestClient,
        unique_id: str,
    ) -> None:
        """New data should be visible after cache invalidation."""
        # Get initial count
        response = test_client.get("/api/beads")
        initial_count = len(response.json())

        # Create new bead
        bead_id = create_test_bead(f"{TEST_PREFIX}_cache_{unique_id}")
        try:
            # Invalidate cache
            BeadService.invalidate_cache()

            # New bead should be visible
            response = test_client.get("/api/beads")
            new_count = len(response.json())
            assert new_count >= initial_count  # Could be equal if other beads were deleted
            bead_ids = [b["id"] for b in response.json()]
            assert bead_id in bead_ids
        finally:
            delete_test_bead(bead_id)


class TestRaceConditions:
    """Test potential race condition scenarios."""

    def test_rapid_create_delete_cycle(
        self,
        test_client: TestClient,
        unique_id: str,
    ) -> None:
        """Rapid create-delete cycles should not corrupt state."""
        for i in range(5):
            # Create
            bead_id = create_test_bead(f"{TEST_PREFIX}_rapid_{unique_id}_{i}")
            # Small delay
            time.sleep(0.05)
            # Delete
            delete_test_bead(bead_id)
            BeadService.invalidate_cache()

        # System should still be healthy
        response = test_client.get("/health")
        assert response.status_code == 200

        response = test_client.get("/api/beads")
        assert response.status_code == 200

    def test_concurrent_status_updates_same_bead(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """Concurrent status updates on same bead should not corrupt state."""
        errors: list[str] = []
        lock = threading.Lock()

        def toggle_status(iteration: int) -> None:
            try:
                if iteration % 2 == 0:
                    update_bead_status(test_bead, "in_progress")
                else:
                    run_bd(["update", test_bead, "--status", "open"], check=False)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(toggle_status, i) for i in range(6)]
            for future in as_completed(futures):
                pass

        # Bead should still be readable
        BeadService.invalidate_cache()
        response = test_client.get(f"/api/beads/{test_bead}")
        assert response.status_code == 200
        # Status should be one of the valid values
        status = response.json()["status"]
        assert status in ("open", "in_progress", "closed")


class TestHighLoad:
    """Test system under higher load conditions."""

    def test_burst_traffic(self, test_client: TestClient) -> None:
        """System should handle burst of requests."""
        results: list[int] = []
        lock = threading.Lock()

        def make_request() -> None:
            response = test_client.get("/api/beads")
            with lock:
                results.append(response.status_code)

        # Burst of 50 requests
        with ThreadPoolExecutor(max_workers=25) as executor:
            futures = [executor.submit(make_request) for _ in range(50)]
            for future in as_completed(futures):
                pass

        # Most requests should succeed
        success_count = sum(1 for s in results if s == 200)
        success_rate = success_count / len(results)
        assert success_rate >= 0.95, f"Success rate {success_rate} below 95%"

    def test_sustained_load(
        self,
        test_client: TestClient,
        test_bead: str,
    ) -> None:
        """System should handle sustained requests over time."""
        results: list[int] = []
        start_time = time.monotonic()
        duration = 2.0  # 2 seconds of sustained load

        def make_requests() -> None:
            while time.monotonic() - start_time < duration:
                response = test_client.get(f"/api/beads/{test_bead}")
                results.append(response.status_code)
                time.sleep(0.01)  # Small delay between requests

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_requests) for _ in range(5)]
            for future in as_completed(futures):
                pass

        # All requests should succeed
        success_rate = sum(1 for s in results if s == 200) / len(results)
        assert success_rate >= 0.99, f"Success rate {success_rate} below 99%"


class TestPartialIsolation:
    """Test that different users/sessions don't interfere."""

    def test_independent_sessions(
        self,
        unique_id: str,
    ) -> None:
        """Different clients should see consistent data."""
        from fastapi.testclient import TestClient as TC

        from dashboard.app import app

        # Create two independent clients
        client1 = TC(app)
        client2 = TC(app)

        bead_id = create_test_bead(f"{TEST_PREFIX}_isolation_{unique_id}")
        try:
            BeadService.invalidate_cache()

            # Both clients should see the same bead
            response1 = client1.get(f"/api/beads/{bead_id}")
            response2 = client2.get(f"/api/beads/{bead_id}")

            assert response1.status_code == 200
            assert response2.status_code == 200
            assert response1.json()["id"] == response2.json()["id"]
        finally:
            delete_test_bead(bead_id)
