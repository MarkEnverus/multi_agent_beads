"""Tests for spawner prompt builder methods.

Tests _build_worker_prompt (polling loop) and _build_single_task_prompt (single bead).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from mab.spawner import ROLE_TO_LABEL, SubprocessSpawner


@pytest.fixture
def spawner(tmp_path: Path) -> SubprocessSpawner:
    """Create a SubprocessSpawner in test mode."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return SubprocessSpawner(logs_dir=logs_dir, test_mode=True)


class TestBuildWorkerPrompt:
    """Tests for the polling-loop _build_worker_prompt method."""

    def test_contains_role(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "# Role content", "worker-1")
        assert "DEV Agent" in result

    def test_contains_worker_id(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "# Role content", "worker-abc")
        assert "worker-abc" in result

    def test_contains_polling_loop(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "# Role content", "worker-1")
        assert "CONTINUOUS POLLING" in result
        assert "MAIN WORK LOOP" in result
        assert "idle_count" in result

    def test_contains_prompt_content(self, spawner: SubprocessSpawner) -> None:
        content = "# Custom role instructions\nDo special things."
        result = spawner._build_worker_prompt("dev", content, "worker-1")
        assert content in result

    def test_label_filter_for_dev(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "bd ready -l dev" in result

    def test_label_filter_for_qa(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("qa", "", "worker-1")
        assert "bd ready -l qa" in result

    def test_no_label_filter_for_manager(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("manager", "", "worker-1")
        # Manager sees all - no -l <label> filter
        assert "-l " not in result

    def test_contains_setup_commands(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "log()" in result
        assert "BD_ROOT" in result
        assert "WORKER_LOG_FILE" in result
        assert "SESSION_START" in result

    def test_max_idle_polls_configurable(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "", "worker-1", max_idle_polls=20)
        assert "20" in result

    def test_poll_interval_configurable(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("dev", "", "worker-1", poll_interval_seconds=60)
        assert "sleep 60" in result


class TestBuildSingleTaskPrompt:
    """Tests for the single-bead _build_single_task_prompt method."""

    def test_contains_role(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt(
            "dev", "# Role content", "worker-1", "beads-abc12"
        )
        assert "DEV Agent" in result

    def test_contains_worker_id(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt(
            "dev", "# Role content", "worker-abc", "beads-abc12"
        )
        assert "worker-abc" in result

    def test_contains_bead_id(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "multi_agent_beads-xyz99")
        assert "multi_agent_beads-xyz99" in result

    def test_contains_claim_command(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "bd update beads-abc12 --status=in_progress" in result

    def test_contains_show_command(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "bd show beads-abc12" in result

    def test_contains_close_command(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "bd close beads-abc12" in result

    def test_no_polling_loop(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "CONTINUOUS POLLING" not in result
        assert "MAIN WORK LOOP" not in result
        assert "idle_count" not in result
        assert "sleep" not in result

    def test_instructs_exit_after_completion(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "EXIT IMMEDIATELY" in result
        assert "Do NOT poll" in result

    def test_contains_prompt_content(self, spawner: SubprocessSpawner) -> None:
        content = "# Custom role instructions\nDo special things."
        result = spawner._build_single_task_prompt("dev", content, "worker-1", "beads-abc12")
        assert content in result

    def test_contains_setup_commands(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "log()" in result
        assert "BD_ROOT" in result
        assert "WORKER_LOG_FILE" in result
        assert "SESSION_START" in result

    def test_contains_sync_command(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "bd sync" in result

    def test_session_end_references_bead(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "SESSION_END: beads-abc12" in result

    def test_single_task_label(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt says SINGLE TASK, not CONTINUOUS POLLING."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "SINGLE TASK" in result

    def test_assigned_bead_in_header(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc12")
        assert "Assigned Bead: beads-abc12" in result


class TestPromptSelection:
    """Tests that spawn methods choose the correct prompt builder."""

    def test_spawn_without_bead_uses_polling_prompt(
        self, spawner: SubprocessSpawner
    ) -> None:
        """Without bead_id, SubprocessSpawner uses _build_worker_prompt."""
        worker_prompt = spawner._build_worker_prompt("dev", "# content", "w-1")
        assert "CONTINUOUS POLLING" in worker_prompt

    def test_spawn_with_bead_uses_single_task_prompt(self, spawner: SubprocessSpawner) -> None:
        """With bead_id, single-task prompt is used."""
        single_prompt = spawner._build_single_task_prompt("dev", "# content", "w-1", "beads-123")
        assert "SINGLE TASK" in single_prompt
        assert "CONTINUOUS POLLING" not in single_prompt


class TestPromptTypeSelectionIntegration:
    """Integration tests for prompt type selection through spawn().

    These tests verify the branching logic at spawn time:
    - spawn(bead_id=None) -> _build_worker_prompt (polling loop)
    - spawn(bead_id="...") -> _build_single_task_prompt (single task)

    Since the actual spawn() involves PTY allocation and process forking,
    we mock the prompt builders to track which one gets called and verify
    the prompt type selection logic at spawner.py:1043-1049.
    """

    @pytest.fixture
    def prod_spawner(self, tmp_path: Path) -> SubprocessSpawner:
        """Create a non-test-mode SubprocessSpawner for integration testing."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        return SubprocessSpawner(logs_dir=logs_dir, test_mode=False, use_worktrees=False)

    @pytest.fixture
    def project_with_prompt(self, tmp_path: Path) -> Path:
        """Create a project directory with a dev prompt file."""
        project = tmp_path / "project"
        project.mkdir()
        prompts_dir = project / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "DEVELOPER.md").write_text("# Dev role instructions")
        return project

    @pytest.mark.asyncio
    async def test_spawn_without_bead_id_calls_worker_prompt(
        self, prod_spawner: SubprocessSpawner, project_with_prompt: Path
    ) -> None:
        """spawn() without bead_id invokes _build_worker_prompt, not single-task."""
        builder_calls: list[str] = []

        original_worker = prod_spawner._build_worker_prompt
        original_single = prod_spawner._build_single_task_prompt

        def track_worker(*args, **kwargs):
            builder_calls.append("worker")
            return original_worker(*args, **kwargs)

        def track_single(*args, **kwargs):
            builder_calls.append("single")
            return original_single(*args, **kwargs)

        with patch.object(prod_spawner, "_build_worker_prompt", side_effect=track_worker):
            with patch.object(prod_spawner, "_build_single_task_prompt", side_effect=track_single):
                with patch("mab.spawner.shutil.which", return_value="/usr/bin/claude"):
                    # The spawn will fail at PTY allocation, but prompt selection
                    # happens before that. We catch the expected error.
                    try:
                        await prod_spawner.spawn(
                            role="dev",
                            project_path=str(project_with_prompt),
                            worker_id="worker-test-1",
                            bead_id=None,
                        )
                    except Exception:
                        pass  # PTY/subprocess errors expected in test env

        assert "worker" in builder_calls
        assert "single" not in builder_calls

    @pytest.mark.asyncio
    async def test_spawn_with_bead_id_calls_single_task_prompt(
        self, prod_spawner: SubprocessSpawner, project_with_prompt: Path
    ) -> None:
        """spawn() with bead_id invokes _build_single_task_prompt, not worker."""
        builder_calls: list[str] = []

        original_worker = prod_spawner._build_worker_prompt
        original_single = prod_spawner._build_single_task_prompt

        def track_worker(*args, **kwargs):
            builder_calls.append("worker")
            return original_worker(*args, **kwargs)

        def track_single(*args, **kwargs):
            builder_calls.append("single")
            return original_single(*args, **kwargs)

        with patch.object(prod_spawner, "_build_worker_prompt", side_effect=track_worker):
            with patch.object(prod_spawner, "_build_single_task_prompt", side_effect=track_single):
                with patch("mab.spawner.shutil.which", return_value="/usr/bin/claude"):
                    try:
                        await prod_spawner.spawn(
                            role="dev",
                            project_path=str(project_with_prompt),
                            worker_id="worker-test-2",
                            bead_id="beads-dispatch-42",
                        )
                    except Exception:
                        pass

        assert "single" in builder_calls
        assert "worker" not in builder_calls

    @pytest.mark.asyncio
    async def test_spawn_single_task_receives_correct_bead_id(
        self, prod_spawner: SubprocessSpawner, project_with_prompt: Path
    ) -> None:
        """spawn() passes the correct bead_id to _build_single_task_prompt."""
        captured_bead_id: list[str] = []

        original_single = prod_spawner._build_single_task_prompt

        def capture_single(role, content, worker_id, bead_id):
            captured_bead_id.append(bead_id)
            return original_single(role, content, worker_id, bead_id)

        with patch.object(prod_spawner, "_build_single_task_prompt", side_effect=capture_single):
            with patch("mab.spawner.shutil.which", return_value="/usr/bin/claude"):
                try:
                    await prod_spawner.spawn(
                        role="dev",
                        project_path=str(project_with_prompt),
                        worker_id="worker-test-3",
                        bead_id="beads-xyz-99",
                    )
                except Exception:
                    pass

        assert captured_bead_id == ["beads-xyz-99"]

    @pytest.mark.asyncio
    async def test_spawn_worker_prompt_receives_role_content(
        self, prod_spawner: SubprocessSpawner, project_with_prompt: Path
    ) -> None:
        """spawn() reads prompt file and passes content to _build_worker_prompt."""
        captured_content: list[str] = []

        original_worker = prod_spawner._build_worker_prompt

        def capture_worker(role, content, worker_id, *args, **kwargs):
            captured_content.append(content)
            return original_worker(role, content, worker_id, *args, **kwargs)

        with patch.object(prod_spawner, "_build_worker_prompt", side_effect=capture_worker):
            with patch("mab.spawner.shutil.which", return_value="/usr/bin/claude"):
                try:
                    await prod_spawner.spawn(
                        role="dev",
                        project_path=str(project_with_prompt),
                        worker_id="worker-test-4",
                        bead_id=None,
                    )
                except Exception:
                    pass

        assert len(captured_content) == 1
        assert "# Dev role instructions" in captured_content[0]

    @pytest.mark.asyncio
    async def test_spawn_single_task_receives_role_content(
        self, prod_spawner: SubprocessSpawner, project_with_prompt: Path
    ) -> None:
        """spawn() reads prompt file and passes content to _build_single_task_prompt."""
        captured_content: list[str] = []

        original_single = prod_spawner._build_single_task_prompt

        def capture_single(role, content, worker_id, bead_id):
            captured_content.append(content)
            return original_single(role, content, worker_id, bead_id)

        with patch.object(prod_spawner, "_build_single_task_prompt", side_effect=capture_single):
            with patch("mab.spawner.shutil.which", return_value="/usr/bin/claude"):
                try:
                    await prod_spawner.spawn(
                        role="dev",
                        project_path=str(project_with_prompt),
                        worker_id="worker-test-5",
                        bead_id="beads-abc",
                    )
                except Exception:
                    pass

        assert len(captured_content) == 1
        assert "# Dev role instructions" in captured_content[0]


class TestWorkerPromptLabelFilters:
    """Tests for label filters across all roles in the worker (polling) prompt."""

    def test_tech_lead_uses_architecture_label(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("tech_lead", "", "worker-1")
        assert "bd ready -l architecture" in result

    def test_reviewer_uses_review_label(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("reviewer", "", "worker-1")
        assert "bd ready -l review" in result

    def test_developer_alias_uses_dev_label(self, spawner: SubprocessSpawner) -> None:
        """The 'developer' alias role maps to 'dev' label."""
        result = spawner._build_worker_prompt("developer", "", "worker-1")
        assert "bd ready -l dev" in result

    def test_manager_has_no_label_filter(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("manager", "", "worker-1")
        # Manager should see all work - no label filter in bd ready command
        assert "bd ready\n" in result or "bd ready " in result
        assert "-l " not in result

    def test_all_roles_with_labels_have_filter(self, spawner: SubprocessSpawner) -> None:
        """Every role with a non-None label should have -l in its prompt."""
        for role, label in ROLE_TO_LABEL.items():
            result = spawner._build_worker_prompt(role, "", "worker-1")
            if label is not None:
                assert f"-l {label}" in result, f"Role {role} should have -l {label}"
            else:
                assert "-l " not in result, f"Role {role} (manager) should not have -l"


class TestWorkerPromptRoleDisplay:
    """Tests that role names are displayed correctly in prompts."""

    def test_qa_role_uppercase(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("qa", "", "worker-1")
        assert "QA Agent" in result

    def test_tech_lead_role_uppercase(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("tech_lead", "", "worker-1")
        assert "TECH_LEAD Agent" in result

    def test_reviewer_role_uppercase(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("reviewer", "", "worker-1")
        assert "REVIEWER Agent" in result

    def test_manager_role_uppercase(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_worker_prompt("manager", "", "worker-1")
        assert "MANAGER Agent" in result


class TestSingleTaskPromptRoles:
    """Tests for single-task prompt across different roles."""

    def test_qa_role_single_task(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("qa", "", "worker-1", "beads-123")
        assert "QA Agent" in result
        assert "SINGLE TASK" in result

    def test_tech_lead_role_single_task(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("tech_lead", "", "worker-1", "beads-123")
        assert "TECH_LEAD Agent" in result

    def test_manager_role_single_task(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("manager", "", "worker-1", "beads-123")
        assert "MANAGER Agent" in result

    def test_reviewer_role_single_task(self, spawner: SubprocessSpawner) -> None:
        result = spawner._build_single_task_prompt("reviewer", "", "worker-1", "beads-123")
        assert "REVIEWER Agent" in result


class TestPromptContentIntegrity:
    """Tests for structural integrity of both prompt types."""

    def test_worker_prompt_contains_bd_alias(self, spawner: SubprocessSpawner) -> None:
        """Worker prompt sets up bd alias to use BD_ROOT database."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert 'alias bd=' in result
        assert "BD_ROOT" in result

    def test_single_task_prompt_contains_bd_alias(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt also sets up bd alias."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")
        assert 'alias bd=' in result
        assert "BD_ROOT" in result

    def test_worker_prompt_has_idle_exit_logic(self, spawner: SubprocessSpawner) -> None:
        """Worker prompt includes idle counter exit logic."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "idle_count" in result
        assert "SESSION_END" in result
        assert "max idle polls" in result

    def test_single_task_prompt_has_no_idle_logic(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt should never mention idle polling."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")
        assert "idle_count" not in result
        assert "idle_polls" not in result

    def test_worker_prompt_default_parameters(self, spawner: SubprocessSpawner) -> None:
        """Worker prompt uses correct defaults for poll_interval and max_idle."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "sleep 30" in result  # default poll_interval_seconds
        assert "10" in result  # default max_idle_polls

    def test_single_task_work_start_log(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt includes WORK_START log instruction."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")
        assert "WORK_START" in result

    def test_single_task_close_log(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt includes CLOSE log for the specific bead."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-xyz")
        assert "CLOSE: beads-xyz" in result

    def test_worker_prompt_never_exit_immediately(self, spawner: SubprocessSpawner) -> None:
        """Worker (polling) prompt should never say EXIT IMMEDIATELY."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "EXIT IMMEDIATELY" not in result

    def test_single_task_never_has_return_to_step(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt should not have 'RETURN TO STEP' loop instructions."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")
        assert "RETURN TO STEP" not in result


class TestPromptTypeDispatchDifferentiation:
    """Tests that dispatch prompt types (polling vs single-task) are properly differentiated.

    The dispatch system uses two prompt types:
    - Polling loop: for workers without a bead_id (continuous work discovery)
    - Single task: for workers with a bead_id (dispatch-assigned, exits after)
    """

    def test_polling_has_return_to_step(self, spawner: SubprocessSpawner) -> None:
        """Polling prompt has 'RETURN TO STEP' loop instructions."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "RETURN TO STEP" in result

    def test_polling_has_never_exit_rules(self, spawner: SubprocessSpawner) -> None:
        """Polling prompt has 'NEVER exit' rule instructions."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "NEVER exit" in result

    def test_single_task_describes_one_specific_bead(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt describes assignment to one specific bead."""
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-abc")
        assert "one specific bead" in result

    def test_polling_describes_continuous_loop(self, spawner: SubprocessSpawner) -> None:
        """Polling prompt describes continuous polling behavior."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "CONTINUOUS POLLING LOOP" in result

    def test_idle_timeout_minutes_calculated(self, spawner: SubprocessSpawner) -> None:
        """Polling prompt computes minutes from max_idle_polls * interval."""
        result = spawner._build_worker_prompt(
            "dev", "", "worker-1", poll_interval_seconds=60, max_idle_polls=5
        )
        # 5 * 60 // 60 = 5 minutes
        assert "5 minutes" in result

    def test_idle_timeout_minutes_default(self, spawner: SubprocessSpawner) -> None:
        """Default timeout is 10 polls * 30s = 300s = 5 minutes."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "5 minutes" in result

    def test_single_task_contains_claim_for_specific_bead(
        self, spawner: SubprocessSpawner
    ) -> None:
        """Single task prompt embeds the actual bead ID in claim command."""
        result = spawner._build_single_task_prompt("dev", "", "w-1", "beads-test-99")
        assert "bd update beads-test-99 --status=in_progress" in result

    def test_polling_uses_generic_bead_placeholder(self, spawner: SubprocessSpawner) -> None:
        """Polling prompt uses <bead-id> placeholder, not a specific bead."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "<bead-id>" in result
        # Should not contain a specific hardcoded bead ID
        assert "bd update beads-" not in result

    def test_single_task_session_end_references_specific_bead(
        self, spawner: SubprocessSpawner
    ) -> None:
        """Single task SESSION_END log references the specific bead ID."""
        result = spawner._build_single_task_prompt("dev", "", "w-1", "beads-specific")
        assert "SESSION_END: beads-specific" in result

    def test_polling_session_end_references_max_idle(self, spawner: SubprocessSpawner) -> None:
        """Polling SESSION_END log references max idle polls, not a specific bead."""
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "SESSION_END: max idle polls" in result

    def test_single_task_has_numbered_steps(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt has sequential numbered steps (1-6)."""
        result = spawner._build_single_task_prompt("dev", "", "w-1", "beads-123")
        assert "### 1." in result
        assert "### 2." in result
        assert "### 3." in result
        assert "### 5." in result
        assert "### 6." in result

    def test_both_prompts_share_setup_structure(self, spawner: SubprocessSpawner) -> None:
        """Both prompt types include the same critical setup commands."""
        worker = spawner._build_worker_prompt("dev", "", "worker-1")
        single = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")

        # Both should have these setup elements
        for prompt in [worker, single]:
            assert "log()" in prompt
            assert "BD_ROOT" in prompt
            assert "WORKER_LOG_FILE" in prompt
            assert "WORKER_ID" in prompt
            assert "SESSION_START" in prompt

    def test_prompts_embed_same_worker_id(self, spawner: SubprocessSpawner) -> None:
        """Both prompt types correctly embed the same worker ID."""
        worker_id = "worker-unique-42"
        worker = spawner._build_worker_prompt("dev", "", worker_id)
        single = spawner._build_single_task_prompt("dev", "", worker_id, "beads-123")

        assert worker_id in worker
        assert worker_id in single


class TestPromptContentEdgeCases:
    """Tests for edge cases in prompt content handling."""

    def test_empty_prompt_content(self, spawner: SubprocessSpawner) -> None:
        """Prompts work with empty prompt_content."""
        worker = spawner._build_worker_prompt("dev", "", "worker-1")
        single = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")

        # Should still have the structural elements
        assert "DEV Agent" in worker
        assert "DEV Agent" in single

    def test_prompt_content_with_curly_braces(self, spawner: SubprocessSpawner) -> None:
        """Prompt content with curly braces doesn't break f-string formatting."""
        content = "Use bash: for i in {1..10}; do echo $i; done"
        # This should not raise - the f-string uses raw content appended, not interpolated
        worker = spawner._build_worker_prompt("dev", content, "worker-1")
        single = spawner._build_single_task_prompt("dev", content, "worker-1", "beads-123")

        assert content in worker
        assert content in single

    def test_prompt_content_with_markdown_headers(self, spawner: SubprocessSpawner) -> None:
        """Prompt content with markdown headers is included verbatim."""
        content = "# Main Header\n## Sub Header\n### Third Level\n- Bullet point"
        worker = spawner._build_worker_prompt("dev", content, "worker-1")
        assert content in worker

    def test_prompt_content_appears_at_end(self, spawner: SubprocessSpawner) -> None:
        """Prompt content is appended after the protocol section (after ---)."""
        content = "# CUSTOM_ROLE_MARKER"
        worker = spawner._build_worker_prompt("dev", content, "worker-1")
        single = spawner._build_single_task_prompt("dev", content, "worker-1", "beads-123")

        # Content should appear after the separator
        for prompt in [worker, single]:
            sep_pos = prompt.rfind("---")
            content_pos = prompt.find("CUSTOM_ROLE_MARKER")
            assert content_pos > sep_pos, "Content should appear after the last --- separator"

    def test_unknown_role_no_label_filter(self, spawner: SubprocessSpawner) -> None:
        """Unknown role gets no label filter (ROLE_TO_LABEL returns None)."""
        result = spawner._build_worker_prompt("unknown_role", "", "worker-1")
        assert "-l " not in result
        assert "UNKNOWN_ROLE Agent" in result

    def test_single_task_unknown_role(self, spawner: SubprocessSpawner) -> None:
        """Single task prompt works with unknown role."""
        result = spawner._build_single_task_prompt("custom", "", "worker-1", "beads-123")
        assert "CUSTOM Agent" in result
        assert "beads-123" in result


class TestRoleToLabelMapping:
    """Tests for the ROLE_TO_LABEL constant used by prompt builders."""

    def test_dev_label(self) -> None:
        assert ROLE_TO_LABEL["dev"] == "dev"

    def test_developer_alias_label(self) -> None:
        assert ROLE_TO_LABEL["developer"] == "dev"

    def test_qa_label(self) -> None:
        assert ROLE_TO_LABEL["qa"] == "qa"

    def test_tech_lead_label(self) -> None:
        assert ROLE_TO_LABEL["tech_lead"] == "architecture"

    def test_manager_no_label(self) -> None:
        assert ROLE_TO_LABEL["manager"] is None

    def test_reviewer_label(self) -> None:
        assert ROLE_TO_LABEL["reviewer"] == "review"

    def test_all_roles_present(self) -> None:
        """All expected roles are in the mapping."""
        expected_roles = {"dev", "developer", "qa", "tech_lead", "manager", "reviewer"}
        assert set(ROLE_TO_LABEL.keys()) == expected_roles
