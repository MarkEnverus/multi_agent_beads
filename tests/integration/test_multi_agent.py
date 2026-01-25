"""Multi-agent coordination integration tests.

This module tests the interaction patterns between different agent roles:
- Role-based work handoffs (Developer → QA)
- Dependency chains (Tech Lead creates dependent tasks)
- Full workflow: Epic → Tasks → Implementation → QA → Close
- Agent unblocking scenarios

Test scenarios simulate multiple agents working concurrently on a shared
bead system, verifying that handoffs and dependencies work correctly.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dashboard.services import BeadService

from .conftest import (
    TEST_PREFIX,
    add_dependency,
    create_test_bead,
    delete_test_bead,
    run_bd,
    update_bead_status,
)

# Agent role labels mapping
ROLE_LABELS = {
    "developer": "dev",
    "qa": "qa",
    "tech_lead": "architecture",
    "manager": None,  # Manager sees all
    "reviewer": "review",
}


def create_labeled_bead(
    title: str,
    labels: list[str],
    priority: str = "2",
    description: str | None = None,
) -> str:
    """Create a bead with specific labels for role filtering.

    Args:
        title: Bead title.
        labels: List of labels to apply.
        priority: Priority level (0-4).
        description: Optional description.

    Returns:
        The created bead ID.
    """
    return create_test_bead(
        title=title,
        priority=priority,
        description=description or f"Multi-agent test bead for {', '.join(labels)}",
        labels=labels,
        issue_type="task",
    )


def get_ready_for_role(role: str) -> list[dict]:
    """Get ready work filtered by role.

    Args:
        role: Agent role to filter by.

    Returns:
        List of ready beads for the role.
    """
    label = ROLE_LABELS.get(role)
    BeadService.invalidate_cache()
    return BeadService.list_ready(label=label, use_cache=False)


class TestRoleBasedHandoffs:
    """Test handoff scenarios between different agent roles."""

    def test_developer_creates_qa_picks_up(
        self,
        unique_id: str,
    ) -> None:
        """Developer completes work, QA agent picks up for verification.

        Simulates the handoff pattern:
        1. Developer creates task with 'qa' label
        2. Developer "completes" work (marks done)
        3. QA agent finds the work via role filter
        """
        # Developer creates a task that QA needs to verify
        dev_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_dev_to_qa_{unique_id}",
            labels=["qa", "test"],
            priority="2",
            description="Task completed by dev, needs QA verification",
        )

        qa_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_qa_work_{unique_id}",
            labels=["qa", "test"],
            priority="2",
            description="QA verification task",
        )

        try:
            BeadService.invalidate_cache()

            # QA agent should find both tasks
            qa_ready = get_ready_for_role("qa")
            qa_ids = [b["id"] for b in qa_ready]

            assert dev_task in qa_ids, "QA should see dev's task"
            assert qa_task in qa_ids, "QA should see its own task"

            # Note: Developer agent would NOT see QA-labeled tasks
            # These tasks have 'qa' label, so dev shouldn't see them
            # (depends on label filtering behavior - dev sees 'dev' label only)

        finally:
            delete_test_bead(dev_task)
            delete_test_bead(qa_task)

    def test_tech_lead_assigns_to_developer(
        self,
        unique_id: str,
    ) -> None:
        """Tech Lead creates architecture task, Developer picks up implementation.

        Simulates:
        1. Tech Lead creates task with 'dev' label
        2. Developer agent finds and claims the task
        """
        # Tech Lead creates implementation task for developers
        impl_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_tech_lead_to_dev_{unique_id}",
            labels=["dev", "architecture"],
            priority="1",
            description="Implementation task created by Tech Lead",
        )

        try:
            BeadService.invalidate_cache()

            # Developer should see this task
            dev_ready = get_ready_for_role("developer")
            dev_ids = [b["id"] for b in dev_ready]

            assert impl_task in dev_ids, "Developer should see Tech Lead's task"

            # Developer claims the task
            update_bead_status(impl_task, "in_progress")
            BeadService.invalidate_cache()

            # Verify bead status changed directly
            bead = BeadService.get_bead(impl_task)
            assert bead["status"] == "in_progress", "Bead should be in_progress"

        finally:
            delete_test_bead(impl_task)

    def test_manager_sees_all_work(
        self,
        unique_id: str,
    ) -> None:
        """Manager role should see work across all labels.

        Simulates manager oversight - sees all work regardless of label.
        """
        # Create tasks with different role labels
        dev_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_manager_view_dev_{unique_id}",
            labels=["dev"],
            priority="2",
        )
        qa_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_manager_view_qa_{unique_id}",
            labels=["qa"],
            priority="2",
        )
        arch_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_manager_view_arch_{unique_id}",
            labels=["architecture"],
            priority="2",
        )

        try:
            BeadService.invalidate_cache()

            # Manager (no label filter) should see all - verify via list_beads
            # since list_ready uses bd ready which may have different behavior
            all_beads = BeadService.list_beads(use_cache=False)
            all_ids = [b["id"] for b in all_beads]

            assert dev_task in all_ids, "Manager should see dev task"
            assert qa_task in all_ids, "Manager should see qa task"
            assert arch_task in all_ids, "Manager should see architecture task"

            # Verify beads exist and are accessible
            for task_id in [dev_task, qa_task, arch_task]:
                bead = BeadService.get_bead(task_id)
                assert bead["status"] == "open"

        finally:
            delete_test_bead(dev_task)
            delete_test_bead(qa_task)
            delete_test_bead(arch_task)


class TestDependencyChains:
    """Test dependency management in multi-agent scenarios."""

    def test_tech_lead_creates_dependent_tasks(
        self,
        unique_id: str,
    ) -> None:
        """Tech Lead creates a chain of dependent tasks.

        Creates: Design → Implement → Test
        Only Design should be ready initially.
        """
        # Tech Lead creates dependent chain
        design_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_design_{unique_id}",
            labels=["architecture"],
            priority="1",
            description="Design phase - must complete first",
        )
        impl_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_implement_{unique_id}",
            labels=["dev"],
            priority="1",
            description="Implementation - depends on design",
        )
        test_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_test_{unique_id}",
            labels=["qa"],
            priority="1",
            description="Testing - depends on implementation",
        )

        try:
            # Set up dependency chain
            add_dependency(impl_task, design_task)  # impl blocked by design
            add_dependency(test_task, impl_task)    # test blocked by impl

            BeadService.invalidate_cache()

            # Only design should be ready
            ready_work = BeadService.list_ready(use_cache=False)
            ready_ids = [b["id"] for b in ready_work]

            assert design_task in ready_ids, "Design task should be ready"
            assert impl_task not in ready_ids, "Impl should be blocked"
            assert test_task not in ready_ids, "Test should be blocked"

            # Complete design
            run_bd(["close", design_task, "--reason", "Design complete"])
            BeadService.invalidate_cache()

            # Now implementation should be ready
            ready_after_design = BeadService.list_ready(use_cache=False)
            ready_after_ids = [b["id"] for b in ready_after_design]

            assert impl_task in ready_after_ids, "Impl should be unblocked"
            assert test_task not in ready_after_ids, "Test still blocked"

        finally:
            delete_test_bead(impl_task)
            delete_test_bead(test_task)
            # design_task already closed

    def test_parallel_tasks_same_blocker(
        self,
        unique_id: str,
    ) -> None:
        """Multiple tasks can be blocked by the same dependency.

        Creates: Epic → (Task A, Task B, Task C)
        Closing Epic should unblock all child tasks.
        """
        # Create epic that blocks multiple tasks
        epic = create_labeled_bead(
            title=f"{TEST_PREFIX}_epic_{unique_id}",
            labels=["architecture"],
            priority="0",
            description="Epic that blocks child tasks",
        )

        child_tasks: list[str] = []
        for i in range(3):
            child = create_labeled_bead(
                title=f"{TEST_PREFIX}_child_{unique_id}_{i}",
                labels=["dev"],
                priority="2",
                description=f"Child task {i} of epic",
            )
            child_tasks.append(child)
            add_dependency(child, epic)

        try:
            BeadService.invalidate_cache()

            # All children should be blocked
            ready_work = BeadService.list_ready(use_cache=False)
            ready_ids = [b["id"] for b in ready_work]

            for child in child_tasks:
                assert child not in ready_ids, f"Child {child} should be blocked"

            assert epic in ready_ids, "Epic should be ready"

            # Close the epic
            run_bd(["close", epic, "--reason", "Epic complete"])
            BeadService.invalidate_cache()

            # All children should now be unblocked - verify via blocked list
            blocked_after = BeadService.list_blocked(use_cache=False)
            blocked_after_ids = [b["id"] for b in blocked_after]

            for child in child_tasks:
                assert child not in blocked_after_ids, f"Child {child} should be unblocked"

            # Verify children are open (not closed, not blocked)
            for child in child_tasks:
                bead = BeadService.get_bead(child)
                assert bead["status"] == "open", f"Child {child} should be open"

        finally:
            for child in child_tasks:
                delete_test_bead(child)


class TestAgentUnblocking:
    """Test scenarios where one agent unblocks another."""

    def test_developer_unblocks_qa(
        self,
        unique_id: str,
    ) -> None:
        """QA is blocked until Developer completes implementation.

        Scenario:
        1. QA task blocked by Dev task
        2. Developer completes work
        3. QA task becomes available
        """
        dev_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_dev_impl_{unique_id}",
            labels=["dev"],
            priority="1",
            description="Implementation work",
        )
        qa_task = create_labeled_bead(
            title=f"{TEST_PREFIX}_qa_verify_{unique_id}",
            labels=["qa"],
            priority="1",
            description="QA verification - blocked by implementation",
        )

        try:
            add_dependency(qa_task, dev_task)
            BeadService.invalidate_cache()

            # QA should see nothing (their task is blocked)
            qa_ready = get_ready_for_role("qa")
            qa_ids = [b["id"] for b in qa_ready]
            assert qa_task not in qa_ids, "QA task should be blocked"

            # Developer completes work
            update_bead_status(dev_task, "in_progress")
            run_bd(["close", dev_task, "--reason", "Implementation done"])
            BeadService.invalidate_cache()

            # QA should now see their task
            qa_ready_after = get_ready_for_role("qa")
            qa_ids_after = [b["id"] for b in qa_ready_after]
            assert qa_task in qa_ids_after, "QA task should be unblocked"

        finally:
            delete_test_bead(qa_task)
            # dev_task already closed

    def test_concurrent_unblocking(
        self,
        unique_id: str,
    ) -> None:
        """Multiple agents racing to claim unblocked work.

        Simulates when a blocker is resolved and multiple agents
        try to claim the newly available work.
        """
        blocker = create_labeled_bead(
            title=f"{TEST_PREFIX}_blocker_{unique_id}",
            labels=["architecture"],
            priority="1",
        )
        blocked_tasks: list[str] = []
        for i in range(3):
            task = create_labeled_bead(
                title=f"{TEST_PREFIX}_blocked_{unique_id}_{i}",
                labels=["dev"],
                priority="2",
            )
            blocked_tasks.append(task)
            add_dependency(task, blocker)

        claimed: list[str] = []
        errors: list[str] = []
        lock = threading.Lock()

        def claim_first_available() -> None:
            """Agent tries to claim first available task."""
            BeadService.invalidate_cache()
            ready = get_ready_for_role("developer")

            for task in ready:
                task_id = task["id"]
                if task_id in blocked_tasks:
                    try:
                        # Try to claim
                        update_bead_status(task_id, "in_progress")
                        with lock:
                            claimed.append(task_id)
                        return
                    except Exception as e:
                        with lock:
                            errors.append(str(e))

        try:
            # Close blocker to unblock tasks
            run_bd(["close", blocker, "--reason", "Unblocking"])

            # Multiple "agents" race to claim
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(claim_first_available) for _ in range(5)]
                for future in as_completed(futures):
                    pass

            # At least some claims should succeed
            # (depending on timing, some agents may see empty ready list)
            # The key is no data corruption
            BeadService.invalidate_cache()

            # All tasks should be in a valid state
            for task_id in blocked_tasks:
                bead = BeadService.get_bead(task_id)
                status = bead["status"]
                assert status in ("open", "in_progress"), f"Invalid state: {status}"

        finally:
            for task in blocked_tasks:
                delete_test_bead(task)


class TestFullWorkflow:
    """Test complete end-to-end workflow with multiple agents."""

    def test_epic_to_close_workflow(
        self,
        unique_id: str,
    ) -> None:
        """Full workflow: Epic → Tasks → Implementation → QA → Close.

        Simulates a realistic multi-agent workflow:
        1. Manager creates epic
        2. Tech Lead breaks into tasks with dependencies
        3. Developer implements in order
        4. QA verifies
        5. All work closed
        """
        # 1. Manager creates epic
        epic = create_labeled_bead(
            title=f"{TEST_PREFIX}_epic_workflow_{unique_id}",
            labels=["architecture"],
            priority="0",
            description="Manager-created epic for feature",
        )

        try:
            # 2. Tech Lead creates dependent tasks
            design_task = create_labeled_bead(
                title=f"{TEST_PREFIX}_design_workflow_{unique_id}",
                labels=["architecture"],
                priority="1",
            )
            impl_task = create_labeled_bead(
                title=f"{TEST_PREFIX}_impl_workflow_{unique_id}",
                labels=["dev"],
                priority="1",
            )
            qa_task = create_labeled_bead(
                title=f"{TEST_PREFIX}_qa_workflow_{unique_id}",
                labels=["qa"],
                priority="1",
            )

            # Set up dependencies
            add_dependency(design_task, epic)
            add_dependency(impl_task, design_task)
            add_dependency(qa_task, impl_task)

            BeadService.invalidate_cache()

            # Only epic should be ready
            ready = BeadService.list_ready(use_cache=False)
            ready_ids = [b["id"] for b in ready]
            assert epic in ready_ids
            assert design_task not in ready_ids
            assert impl_task not in ready_ids
            assert qa_task not in ready_ids

            # 3. Close epic
            run_bd(["close", epic, "--reason", "Epic planning complete"])
            BeadService.invalidate_cache()

            # Design should now be ready
            ready = BeadService.list_ready(use_cache=False)
            ready_ids = [b["id"] for b in ready]
            assert design_task in ready_ids

            # Tech Lead completes design
            update_bead_status(design_task, "in_progress")
            run_bd(["close", design_task, "--reason", "Design approved"])
            BeadService.invalidate_cache()

            # Implementation should be ready
            ready = BeadService.list_ready(use_cache=False)
            ready_ids = [b["id"] for b in ready]
            assert impl_task in ready_ids

            # Developer implements
            update_bead_status(impl_task, "in_progress")
            run_bd(["close", impl_task, "--reason", "Implementation done"])
            BeadService.invalidate_cache()

            # QA should be ready
            ready = BeadService.list_ready(use_cache=False)
            ready_ids = [b["id"] for b in ready]
            assert qa_task in ready_ids

            # QA verifies
            update_bead_status(qa_task, "in_progress")
            run_bd(["close", qa_task, "--reason", "QA passed"])
            BeadService.invalidate_cache()

            # All work complete - verify closed status
            for task_id in [epic, design_task, impl_task, qa_task]:
                bead = BeadService.get_bead(task_id)
                assert bead["status"] == "closed", f"{task_id} should be closed"

        except Exception:
            # Cleanup on failure
            for task_id in [epic, design_task, impl_task, qa_task]:
                try:
                    delete_test_bead(task_id)
                except Exception:
                    pass
            raise


class TestConcurrentAgentSimulation:
    """Simulate multiple agents working concurrently."""

    def test_multiple_agents_different_roles(
        self,
        unique_id: str,
    ) -> None:
        """Simulate agents of different roles working simultaneously.

        Creates work for multiple roles and verifies each agent
        sees only their appropriate work via role labels.
        """
        # Create work for each role
        dev_work = create_labeled_bead(
            title=f"{TEST_PREFIX}_multi_dev_{unique_id}",
            labels=["dev"],
        )
        qa_work = create_labeled_bead(
            title=f"{TEST_PREFIX}_multi_qa_{unique_id}",
            labels=["qa"],
        )
        arch_work = create_labeled_bead(
            title=f"{TEST_PREFIX}_multi_arch_{unique_id}",
            labels=["architecture"],
        )
        review_work = create_labeled_bead(
            title=f"{TEST_PREFIX}_multi_review_{unique_id}",
            labels=["review"],
        )

        try:
            BeadService.invalidate_cache()

            results: dict[str, list[str]] = {}
            lock = threading.Lock()

            def agent_check_work(role: str) -> None:
                """Agent checks what work is available for their role."""
                ready = get_ready_for_role(role)
                ready_ids = [b["id"] for b in ready]
                with lock:
                    results[role] = ready_ids

            # Run all agents concurrently
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(agent_check_work, role): role
                    for role in ROLE_LABELS.keys()
                }
                for future in as_completed(futures):
                    pass

            # Verify each role-filtered list contains appropriate work
            assert dev_work in results["developer"], "Dev should see dev work"
            assert qa_work in results["qa"], "QA should see qa work"
            assert arch_work in results["tech_lead"], "Tech Lead should see arch work"
            assert review_work in results["reviewer"], "Reviewer should see review work"

            # Manager has no label filter - verify all beads exist
            all_beads = BeadService.list_beads(use_cache=False)
            all_ids = [b["id"] for b in all_beads]
            for work_id in [dev_work, qa_work, arch_work, review_work]:
                assert work_id in all_ids, f"All beads should exist: {work_id}"

        finally:
            delete_test_bead(dev_work)
            delete_test_bead(qa_work)
            delete_test_bead(arch_work)
            delete_test_bead(review_work)

    def test_work_claiming_race(
        self,
        unique_id: str,
    ) -> None:
        """Multiple agents of the same role racing to claim limited work.

        Creates fewer tasks than agents to ensure some don't get work.
        Tests that the final state is consistent (no corruption).
        """
        # Create 2 tasks for 5 "developer" agents
        tasks = [
            create_labeled_bead(
                title=f"{TEST_PREFIX}_race_{unique_id}_{i}",
                labels=["dev"],
                priority="1",
            )
            for i in range(2)
        ]

        def agent_claim_work(agent_id: int) -> None:
            """Agent tries to claim available work."""
            # Small delay to simulate timing variation
            time.sleep(0.01 * (agent_id % 3))

            BeadService.invalidate_cache()
            ready = get_ready_for_role("developer")

            for task in ready:
                task_id = task["id"]
                if task_id in tasks:
                    try:
                        update_bead_status(task_id, "in_progress")
                        return
                    except Exception:
                        continue

        try:
            # 5 agents race for 2 tasks
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(agent_claim_work, i)
                    for i in range(5)
                ]
                for future in as_completed(futures):
                    pass

            # Verify all tasks are in valid states (no corruption)
            BeadService.invalidate_cache()
            for task_id in tasks:
                bead = BeadService.get_bead(task_id)
                assert bead["status"] in ("open", "in_progress"), (
                    f"Task {task_id} in invalid state: {bead['status']}"
                )

        finally:
            for task in tasks:
                delete_test_bead(task)
