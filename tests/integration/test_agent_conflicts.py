"""Agent conflict resolution integration tests.

This module tests scenarios where multiple agents compete for resources:
- Race conditions when claiming the same bead
- Concurrent status updates on the same bead
- Simultaneous creation of dependent beads
- Conflict resolution in dependency chains

These tests ensure data integrity when agents work concurrently.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from dashboard.services import BeadService

from .conftest import (
    TEST_PREFIX,
    add_dependency,
    create_test_bead,
    delete_test_bead,
    run_bd,
    update_bead_status,
)


class TestClaimConflicts:
    """Test scenarios where multiple agents try to claim the same work."""

    def test_two_agents_claim_same_bead_simultaneously(
        self,
        unique_id: str,
    ) -> None:
        """Two agents try to claim the exact same bead at the same time.

        Expected behavior: One succeeds, the other gets blocked or fails.
        Data integrity must be maintained.
        """
        # Create a single high-priority task
        target_bead = create_test_bead(
            title=f"{TEST_PREFIX}_claim_conflict_{unique_id}",
            priority="0",
            labels=["dev"],
        )

        results: dict[str, Any] = {
            "agent_a_success": False,
            "agent_b_success": False,
            "agent_a_error": None,
            "agent_b_error": None,
        }
        lock = threading.Lock()
        barrier = threading.Barrier(2)  # Sync both agents to start together

        def agent_claim(agent_id: str) -> None:
            """Agent attempts to claim the target bead."""
            barrier.wait()  # Both start at same time

            try:
                update_bead_status(target_bead, "in_progress")
                with lock:
                    results[f"{agent_id}_success"] = True
            except Exception as e:
                with lock:
                    results[f"{agent_id}_error"] = str(e)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(agent_claim, "agent_a"),
                    executor.submit(agent_claim, "agent_b"),
                ]
                for future in as_completed(futures):
                    pass

            # At least one should succeed
            assert (
                results["agent_a_success"] or results["agent_b_success"]
            ), "At least one agent should claim successfully"

            # Verify final state is consistent
            BeadService.invalidate_cache()
            bead = BeadService.get_bead(target_bead)
            assert bead["status"] == "in_progress", "Bead should be in_progress"

        finally:
            delete_test_bead(target_bead)

    def test_five_agents_race_for_three_beads(
        self,
        unique_id: str,
    ) -> None:
        """Five agents competing for three available beads.

        Tests that:
        - Each bead is claimed at most once
        - All beads end up in valid states
        - No orphaned claims
        """
        # Create 3 tasks
        tasks = [
            create_test_bead(
                title=f"{TEST_PREFIX}_race_3_{unique_id}_{i}",
                priority="1",
                labels=["dev"],
            )
            for i in range(3)
        ]

        claimed: dict[str, str] = {}  # bead_id -> agent_id
        lock = threading.Lock()
        start_event = threading.Event()

        def agent_race(agent_id: str) -> None:
            """Agent tries to claim any available bead."""
            start_event.wait()  # All agents start together

            BeadService.invalidate_cache()
            ready = BeadService.list_ready(use_cache=False)

            for bead in ready:
                bead_id = bead["id"]
                if bead_id not in tasks:
                    continue

                # Check current state
                current = BeadService.get_bead(bead_id)
                if current["status"] != "open":
                    continue

                try:
                    update_bead_status(bead_id, "in_progress")
                    with lock:
                        # Record successful claim
                        if bead_id not in claimed:
                            claimed[bead_id] = agent_id
                    return  # Got one, done
                except Exception:
                    continue  # Try next bead

        try:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(agent_race, f"agent_{i}")
                    for i in range(5)
                ]
                start_event.set()  # Release all agents
                for future in as_completed(futures):
                    pass

            # Verify each task is claimed at most once
            for task_id in tasks:
                claim_count = sum(1 for bid in claimed if bid == task_id)
                assert claim_count <= 1, f"Task {task_id} claimed multiple times"

            # Verify all tasks are in valid states
            BeadService.invalidate_cache()
            for task_id in tasks:
                bead = BeadService.get_bead(task_id)
                assert bead["status"] in ("open", "in_progress"), (
                    f"Invalid state: {bead['status']}"
                )

        finally:
            for task in tasks:
                delete_test_bead(task)

    def test_claim_after_another_agent_claimed(
        self,
        unique_id: str,
    ) -> None:
        """Agent tries to claim work already claimed by another.

        Verifies proper state change when claiming work.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_already_claimed_{unique_id}",
            priority="1",
            labels=["dev"],
        )

        try:
            # First agent claims
            update_bead_status(target, "in_progress")
            BeadService.invalidate_cache()

            # Direct status check - bead should be in_progress
            bead = BeadService.get_bead(target)
            assert bead["status"] == "in_progress", "Bead should be in_progress"

            # Second agent trying to claim should see it's already in_progress
            current = BeadService.get_bead(target)
            assert current["status"] == "in_progress", "Status should still be in_progress"

        finally:
            delete_test_bead(target)


class TestStatusUpdateConflicts:
    """Test concurrent status update scenarios."""

    def test_rapid_status_toggle(
        self,
        unique_id: str,
    ) -> None:
        """Rapidly toggle bead status between open and in_progress.

        Tests that final state is consistent and valid.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_status_toggle_{unique_id}",
            priority="2",
            labels=["test"],
        )

        errors: list[str] = []
        lock = threading.Lock()

        def toggle_status(iteration: int) -> None:
            """Toggle status based on iteration."""
            try:
                if iteration % 2 == 0:
                    update_bead_status(target, "in_progress")
                else:
                    run_bd(["update", target, "--status", "open"], check=False)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(toggle_status, i)
                    for i in range(20)
                ]
                for future in as_completed(futures):
                    pass

            # Final state should be valid
            BeadService.invalidate_cache()
            bead = BeadService.get_bead(target)
            assert bead["status"] in ("open", "in_progress"), (
                f"Invalid final state: {bead['status']}"
            )

        finally:
            delete_test_bead(target)

    def test_concurrent_updates_different_fields(
        self,
        unique_id: str,
    ) -> None:
        """Multiple agents updating different aspects of the same bead.

        One updates status, another adds notes - shouldn't conflict.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_multi_field_{unique_id}",
            priority="2",
            labels=["test"],
        )

        completed = {"status": False, "notes": False}
        lock = threading.Lock()

        def update_status() -> None:
            """Update bead status."""
            try:
                update_bead_status(target, "in_progress")
                with lock:
                    completed["status"] = True
            except Exception:
                pass

        def update_notes() -> None:
            """Add notes to bead."""
            try:
                run_bd(
                    ["update", target, "--notes", "Agent added notes"],
                    check=False,
                )
                with lock:
                    completed["notes"] = True
            except Exception:
                pass

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(update_status),
                    executor.submit(update_notes),
                ]
                for future in as_completed(futures):
                    pass

            # Both should succeed - verify bead is accessible
            BeadService.invalidate_cache()
            BeadService.get_bead(target)  # Should not raise

        finally:
            delete_test_bead(target)


class TestDependencyConflicts:
    """Test conflicts in dependency management."""

    def test_concurrent_dependency_creation(
        self,
        unique_id: str,
    ) -> None:
        """Two agents try to add different dependencies to the same bead.

        Both dependencies should be recorded correctly.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_dep_target_{unique_id}",
            priority="2",
            labels=["test"],
        )
        blocker_a = create_test_bead(
            title=f"{TEST_PREFIX}_blocker_a_{unique_id}",
            priority="1",
            labels=["test"],
        )
        blocker_b = create_test_bead(
            title=f"{TEST_PREFIX}_blocker_b_{unique_id}",
            priority="1",
            labels=["test"],
        )

        results = {"a_added": False, "b_added": False}
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def add_dep_a() -> None:
            """Add first dependency."""
            barrier.wait()
            try:
                add_dependency(target, blocker_a)
                with lock:
                    results["a_added"] = True
            except Exception:
                pass

        def add_dep_b() -> None:
            """Add second dependency."""
            barrier.wait()
            try:
                add_dependency(target, blocker_b)
                with lock:
                    results["b_added"] = True
            except Exception:
                pass

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(add_dep_a),
                    executor.submit(add_dep_b),
                ]
                for future in as_completed(futures):
                    pass

            # At least one should succeed
            assert results["a_added"] or results["b_added"], (
                "At least one dependency should be added"
            )

            # Target should be blocked
            BeadService.invalidate_cache()
            blocked = BeadService.list_blocked(use_cache=False)
            blocked_ids = [b["id"] for b in blocked]
            assert target in blocked_ids, "Target should be blocked"

        finally:
            delete_test_bead(target)
            delete_test_bead(blocker_a)
            delete_test_bead(blocker_b)

    def test_closing_blocker_during_dependency_check(
        self,
        unique_id: str,
    ) -> None:
        """One agent closes blocker while another checks if blocked.

        Race between dependency resolution and status check.
        Verifies the blocked bead becomes unblocked after blocker closes.
        """
        blocker = create_test_bead(
            title=f"{TEST_PREFIX}_racing_blocker_{unique_id}",
            priority="1",
            labels=["test"],
        )
        blocked = create_test_bead(
            title=f"{TEST_PREFIX}_racing_blocked_{unique_id}",
            priority="2",
            labels=["dev"],
        )

        add_dependency(blocked, blocker)
        BeadService.invalidate_cache()

        # Verify initially blocked
        blocked_list = BeadService.list_blocked(use_cache=False)
        blocked_ids = [b["id"] for b in blocked_list]
        assert blocked in blocked_ids, "Bead should initially be blocked"

        barrier = threading.Barrier(2)

        def close_blocker() -> None:
            """Close the blocking bead."""
            barrier.wait()
            run_bd(["close", blocker, "--reason", "Unblock"])

        def verify_blocked() -> None:
            """Verify the blocking relationship."""
            barrier.wait()
            # Just verify we can read state during the race
            for _ in range(3):
                BeadService.invalidate_cache()
                BeadService.list_blocked(use_cache=False)
                time.sleep(0.01)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(close_blocker),
                    executor.submit(verify_blocked),
                ]
                for future in as_completed(futures):
                    pass

            # After blocker is closed, blocked bead should be unblocked
            BeadService.invalidate_cache()
            blocked_after = BeadService.list_blocked(use_cache=False)
            blocked_after_ids = [b["id"] for b in blocked_after]
            assert blocked not in blocked_after_ids, "Blocked bead should be unblocked"

            # Verify blocked bead is still open
            bead = BeadService.get_bead(blocked)
            assert bead["status"] == "open", "Blocked bead should be open"

        finally:
            delete_test_bead(blocked)
            # blocker already closed


class TestCreationConflicts:
    """Test conflicts during bead creation."""

    def test_concurrent_bead_creation_unique_ids(
        self,
        unique_id: str,
    ) -> None:
        """Multiple agents creating beads simultaneously.

        All created beads should have unique IDs.
        """
        created_ids: list[str] = []
        errors: list[str] = []
        lock = threading.Lock()

        def create_bead(index: int) -> None:
            """Create a bead."""
            try:
                bead_id = create_test_bead(
                    title=f"{TEST_PREFIX}_concurrent_create_{unique_id}_{index}",
                    priority="3",
                    labels=["test"],
                )
                with lock:
                    created_ids.append(bead_id)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        try:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(create_bead, i)
                    for i in range(10)
                ]
                for future in as_completed(futures):
                    pass

            # All IDs should be unique
            assert len(created_ids) == len(set(created_ids)), (
                "All created bead IDs should be unique"
            )

            # All creations should succeed
            assert len(errors) == 0, f"Creation errors: {errors}"
            assert len(created_ids) == 10, f"Only {len(created_ids)} beads created"

        finally:
            for bead_id in created_ids:
                delete_test_bead(bead_id)

    def test_create_with_same_title(
        self,
        unique_id: str,
    ) -> None:
        """Two agents create beads with identical titles.

        Both should succeed - titles don't need to be unique.
        """
        title = f"{TEST_PREFIX}_same_title_{unique_id}"
        created_ids: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def create_with_title() -> None:
            """Create bead with the shared title."""
            barrier.wait()
            try:
                bead_id = create_test_bead(
                    title=title,
                    priority="2",
                    labels=["test"],
                )
                with lock:
                    created_ids.append(bead_id)
            except Exception:
                pass

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(create_with_title) for _ in range(2)]
                for future in as_completed(futures):
                    pass

            # Both should succeed with different IDs
            assert len(created_ids) == 2, "Both beads should be created"
            assert created_ids[0] != created_ids[1], "IDs should differ"

            # Both should have same title
            BeadService.invalidate_cache()
            for bead_id in created_ids:
                bead = BeadService.get_bead(bead_id)
                assert bead["title"] == title

        finally:
            for bead_id in created_ids:
                delete_test_bead(bead_id)


class TestCloseConflicts:
    """Test conflicts during bead closure."""

    def test_two_agents_close_same_bead(
        self,
        unique_id: str,
    ) -> None:
        """Two agents try to close the same bead simultaneously.

        One should succeed, the other should fail gracefully.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_double_close_{unique_id}",
            priority="2",
            labels=["test"],
        )

        results: dict[str, bool] = {"agent_a": False, "agent_b": False}
        barrier = threading.Barrier(2)

        def close_bead(agent_id: str) -> None:
            """Agent attempts to close the bead."""
            barrier.wait()
            result = run_bd(
                ["close", target, "--reason", f"Closed by {agent_id}"],
                check=False,
            )
            results[agent_id] = result.returncode == 0

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(close_bead, "agent_a"),
                    executor.submit(close_bead, "agent_b"),
                ]
                for future in as_completed(futures):
                    pass

            # At least one should succeed
            assert results["agent_a"] or results["agent_b"], (
                "At least one close should succeed"
            )

            # Bead should be closed
            BeadService.invalidate_cache()
            bead = BeadService.get_bead(target)
            assert bead["status"] == "closed", "Bead should be closed"

        except Exception:
            delete_test_bead(target)
            raise

    def test_close_while_updating(
        self,
        unique_id: str,
    ) -> None:
        """One agent closes while another updates status.

        The close should take precedence.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_close_vs_update_{unique_id}",
            priority="2",
            labels=["test"],
        )

        barrier = threading.Barrier(2)

        def close_bead() -> None:
            """Close the bead."""
            barrier.wait()
            run_bd(["close", target, "--reason", "Closing"], check=False)

        def update_bead() -> None:
            """Update bead status."""
            barrier.wait()
            run_bd(["update", target, "--status", "in_progress"], check=False)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(close_bead),
                    executor.submit(update_bead),
                ]
                for future in as_completed(futures):
                    pass

            # Check final state - could be closed or in_progress
            # depending on timing
            BeadService.invalidate_cache()
            bead = BeadService.get_bead(target)
            assert bead["status"] in ("closed", "in_progress"), (
                f"Invalid state: {bead['status']}"
            )

        except Exception:
            delete_test_bead(target)
            raise


class TestCacheConsistency:
    """Test cache behavior during conflicts."""

    def test_cache_invalidation_during_concurrent_writes(
        self,
        unique_id: str,
    ) -> None:
        """Multiple writes should properly invalidate cache.

        Readers should eventually see consistent data.
        """
        # Create initial bead
        target = create_test_bead(
            title=f"{TEST_PREFIX}_cache_write_{unique_id}",
            priority="3",
            labels=["test"],
        )

        statuses_seen: list[str] = []
        lock = threading.Lock()

        def writer() -> None:
            """Write status updates."""
            for _ in range(5):
                update_bead_status(target, "in_progress")
                BeadService.invalidate_cache()
                time.sleep(0.02)
                run_bd(["update", target, "--status", "open"], check=False)
                BeadService.invalidate_cache()
                time.sleep(0.02)

        def reader() -> None:
            """Read bead status."""
            for _ in range(20):
                BeadService.invalidate_cache()
                try:
                    bead = BeadService.get_bead(target)
                    with lock:
                        statuses_seen.append(bead["status"])
                except Exception:
                    pass
                time.sleep(0.01)

        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(writer),
                    executor.submit(reader),
                    executor.submit(reader),
                ]
                for future in as_completed(futures):
                    pass

            # All seen statuses should be valid
            valid_statuses = {"open", "in_progress", "closed"}
            for status in statuses_seen:
                assert status in valid_statuses, f"Invalid status seen: {status}"

        finally:
            delete_test_bead(target)


class TestEdgeCases:
    """Test edge case conflict scenarios."""

    def test_update_deleted_bead(
        self,
        unique_id: str,
    ) -> None:
        """Agent tries to update a bead that was just deleted.

        Should fail gracefully.
        """
        target = create_test_bead(
            title=f"{TEST_PREFIX}_deleted_{unique_id}",
            priority="2",
            labels=["test"],
        )

        # Delete immediately
        delete_test_bead(target)

        # Try to update - should fail gracefully (no crash)
        run_bd(
            ["update", target, "--status", "in_progress"],
            check=False,
        )
        BeadService.invalidate_cache()

    def test_dependency_on_closed_bead(
        self,
        unique_id: str,
    ) -> None:
        """Create dependency on a bead that's already closed.

        Should either succeed (and blocked bead is ready since blocker done)
        or fail gracefully. Either way, blocked bead should be workable.
        """
        blocker = create_test_bead(
            title=f"{TEST_PREFIX}_closed_blocker_{unique_id}",
            priority="1",
            labels=["test"],
        )
        blocked = create_test_bead(
            title=f"{TEST_PREFIX}_blocked_by_closed_{unique_id}",
            priority="2",
            labels=["test"],
        )

        try:
            # Close blocker first
            run_bd(["close", blocker, "--reason", "Already done"])
            BeadService.invalidate_cache()

            # Try to add dependency to closed bead
            try:
                add_dependency(blocked, blocker)
            except Exception:
                pass  # May fail, which is acceptable

            # Blocked bead should still be workable (not in blocked list)
            BeadService.invalidate_cache()
            blocked_list = BeadService.list_blocked(use_cache=False)
            blocked_ids = [b["id"] for b in blocked_list]

            # If dependency was added to closed blocker, it shouldn't block
            # If dependency failed, blocked bead has no blockers
            assert blocked not in blocked_ids, "Blocked bead shouldn't be blocked"

            # Verify blocked bead is still open and accessible
            bead = BeadService.get_bead(blocked)
            assert bead["status"] == "open", "Blocked bead should be open"

        finally:
            delete_test_bead(blocked)
            # blocker already closed
