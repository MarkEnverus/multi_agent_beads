"""Tests for dispatch prompt type selection in SubprocessSpawner.spawn().

Tests that spawn() correctly selects between _build_worker_prompt (polling mode)
and _build_single_task_prompt (dispatch mode) based on the bead_id parameter.

This tests the actual branching logic inside spawn() by creating a non-test-mode
spawner and mocking the subprocess/PTY layer to capture which prompt was built
and what CLI arguments were generated.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mab.spawner import SubprocessSpawner


@pytest.fixture
def prod_spawner(tmp_path: Path) -> SubprocessSpawner:
    """Create a non-test-mode SubprocessSpawner for prompt selection tests."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return SubprocessSpawner(
        logs_dir=logs_dir,
        test_mode=False,
        claude_path="/usr/bin/claude",
        use_worktrees=False,
    )


@pytest.fixture
def project_with_prompts(tmp_path: Path) -> Path:
    """Create a project dir with role prompt files."""
    project = tmp_path / "project"
    project.mkdir()
    prompts_dir = project / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "DEVELOPER.md").write_text("# Dev role instructions")
    (prompts_dir / "QA.md").write_text("# QA role instructions")
    return project


def _make_mock_popen() -> MagicMock:
    """Create a properly configured mock Popen that simulates a running process."""
    mock = MagicMock()
    mock.return_value.pid = 12345
    mock.return_value.poll.return_value = None  # Process still running
    return mock


class TestDispatchPromptTypeSelection:
    """Tests that spawn() selects the correct prompt builder based on bead_id."""

    @pytest.mark.asyncio
    async def test_spawn_without_bead_calls_worker_prompt(
        self,
        prod_spawner: SubprocessSpawner,
        project_with_prompts: Path,
    ) -> None:
        """spawn() without bead_id calls _build_worker_prompt (polling mode)."""
        calls: list[str] = []

        original_worker = prod_spawner._build_worker_prompt
        original_single = prod_spawner._build_single_task_prompt

        def track_worker(*args, **kwargs):
            calls.append("worker")
            return original_worker(*args, **kwargs)

        def track_single(*args, **kwargs):
            calls.append("single")
            return original_single(*args, **kwargs)

        prod_spawner._build_worker_prompt = track_worker  # type: ignore[assignment]
        prod_spawner._build_single_task_prompt = track_single  # type: ignore[assignment]

        mock_popen = _make_mock_popen()

        with (
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.ttyname", return_value="/dev/pts/0"),
            patch("os.open", return_value=9),
            patch("os.write"),
            patch("os.close"),
            patch("subprocess.Popen", mock_popen),
            patch("fcntl.fcntl"),
            patch("asyncio.create_task"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("mab.spawner.is_git_repo", return_value=False),
        ):
            await prod_spawner.spawn(
                role="dev",
                project_path=str(project_with_prompts),
                worker_id="worker-test-1",
                bead_id=None,
            )

        assert calls == ["worker"], f"Expected worker prompt, got: {calls}"

    @pytest.mark.asyncio
    async def test_spawn_with_bead_calls_single_task_prompt(
        self,
        prod_spawner: SubprocessSpawner,
        project_with_prompts: Path,
    ) -> None:
        """spawn() with bead_id calls _build_single_task_prompt (dispatch mode)."""
        calls: list[str] = []

        original_worker = prod_spawner._build_worker_prompt
        original_single = prod_spawner._build_single_task_prompt

        def track_worker(*args, **kwargs):
            calls.append("worker")
            return original_worker(*args, **kwargs)

        def track_single(*args, **kwargs):
            calls.append("single")
            return original_single(*args, **kwargs)

        prod_spawner._build_worker_prompt = track_worker  # type: ignore[assignment]
        prod_spawner._build_single_task_prompt = track_single  # type: ignore[assignment]

        mock_popen = _make_mock_popen()

        with (
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.ttyname", return_value="/dev/pts/0"),
            patch("os.open", return_value=9),
            patch("os.write"),
            patch("os.close"),
            patch("subprocess.Popen", mock_popen),
            patch("fcntl.fcntl"),
            patch("asyncio.create_task"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("mab.spawner.is_git_repo", return_value=False),
        ):
            await prod_spawner.spawn(
                role="dev",
                project_path=str(project_with_prompts),
                worker_id="worker-test-2",
                bead_id="beads-abc123",
            )

        assert calls == ["single"], f"Expected single-task prompt, got: {calls}"


class TestDispatchPromptCliArgs:
    """Tests that the correct prompt content reaches the Claude CLI command."""

    @pytest.mark.asyncio
    async def test_dispatch_prompt_passed_to_claude_cli(
        self,
        prod_spawner: SubprocessSpawner,
        project_with_prompts: Path,
    ) -> None:
        """spawn() with bead_id passes single-task prompt to Claude CLI via -p flag."""
        mock_popen = _make_mock_popen()

        with (
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.ttyname", return_value="/dev/pts/0"),
            patch("os.open", return_value=9),
            patch("os.write"),
            patch("os.close"),
            patch("subprocess.Popen", mock_popen),
            patch("fcntl.fcntl"),
            patch("asyncio.create_task"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("mab.spawner.is_git_repo", return_value=False),
        ):
            await prod_spawner.spawn(
                role="dev",
                project_path=str(project_with_prompts),
                worker_id="worker-test-3",
                bead_id="beads-xyz",
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        prompt_idx = cmd.index("-p") + 1
        prompt_text = cmd[prompt_idx]
        assert "beads-xyz" in prompt_text
        assert "SINGLE TASK" in prompt_text

    @pytest.mark.asyncio
    async def test_polling_prompt_passed_to_claude_cli(
        self,
        prod_spawner: SubprocessSpawner,
        project_with_prompts: Path,
    ) -> None:
        """spawn() without bead_id passes polling prompt to Claude CLI via -p flag."""
        mock_popen = _make_mock_popen()

        with (
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.ttyname", return_value="/dev/pts/0"),
            patch("os.open", return_value=9),
            patch("os.write"),
            patch("os.close"),
            patch("subprocess.Popen", mock_popen),
            patch("fcntl.fcntl"),
            patch("asyncio.create_task"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("mab.spawner.is_git_repo", return_value=False),
        ):
            await prod_spawner.spawn(
                role="dev",
                project_path=str(project_with_prompts),
                worker_id="worker-test-4",
                bead_id=None,
            )

        cmd = mock_popen.call_args[0][0]
        prompt_idx = cmd.index("-p") + 1
        prompt_text = cmd[prompt_idx]
        assert "CONTINUOUS POLLING" in prompt_text
        assert "SINGLE TASK" not in prompt_text

    @pytest.mark.asyncio
    async def test_dispatch_prompt_includes_role_content(
        self,
        prod_spawner: SubprocessSpawner,
        project_with_prompts: Path,
    ) -> None:
        """spawn() embeds the role-specific prompt file content into the final prompt."""
        mock_popen = _make_mock_popen()

        with (
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.ttyname", return_value="/dev/pts/0"),
            patch("os.open", return_value=9),
            patch("os.write"),
            patch("os.close"),
            patch("subprocess.Popen", mock_popen),
            patch("fcntl.fcntl"),
            patch("asyncio.create_task"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("mab.spawner.is_git_repo", return_value=False),
        ):
            await prod_spawner.spawn(
                role="dev",
                project_path=str(project_with_prompts),
                worker_id="worker-test-5",
                bead_id="beads-content-test",
            )

        cmd = mock_popen.call_args[0][0]
        prompt_idx = cmd.index("-p") + 1
        prompt_text = cmd[prompt_idx]
        assert "# Dev role instructions" in prompt_text

    @pytest.mark.asyncio
    async def test_different_roles_use_correct_prompt_files(
        self,
        prod_spawner: SubprocessSpawner,
        project_with_prompts: Path,
    ) -> None:
        """spawn() reads the correct prompt file for each role."""
        mock_popen = _make_mock_popen()

        with (
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.ttyname", return_value="/dev/pts/0"),
            patch("os.open", return_value=9),
            patch("os.write"),
            patch("os.close"),
            patch("subprocess.Popen", mock_popen),
            patch("fcntl.fcntl"),
            patch("asyncio.create_task"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("mab.spawner.is_git_repo", return_value=False),
        ):
            await prod_spawner.spawn(
                role="qa",
                project_path=str(project_with_prompts),
                worker_id="worker-test-6",
                bead_id="beads-qa-test",
            )

        cmd = mock_popen.call_args[0][0]
        prompt_idx = cmd.index("-p") + 1
        prompt_text = cmd[prompt_idx]
        assert "# QA role instructions" in prompt_text
        assert "QA Agent" in prompt_text


class TestPromptTypesMutuallyExclusive:
    """Tests that the two prompt types are structurally distinct."""

    def test_worker_prompt_has_no_assigned_bead(self) -> None:
        """Worker prompt (polling mode) should not contain 'Assigned Bead' header."""
        spawner = SubprocessSpawner(logs_dir=Path("/tmp"), test_mode=True)
        result = spawner._build_worker_prompt("dev", "", "worker-1")
        assert "Assigned Bead:" not in result

    def test_single_task_prompt_has_no_polling(self) -> None:
        """Single-task prompt (dispatch mode) has no polling loop constructs."""
        spawner = SubprocessSpawner(logs_dir=Path("/tmp"), test_mode=True)
        result = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")
        assert "CONTINUOUS POLLING" not in result
        assert "MAIN WORK LOOP" not in result
        assert "idle_count" not in result

    def test_prompts_share_common_setup(self) -> None:
        """Both prompt types include the same setup commands."""
        spawner = SubprocessSpawner(logs_dir=Path("/tmp"), test_mode=True)
        worker = spawner._build_worker_prompt("dev", "", "worker-1")
        single = spawner._build_single_task_prompt("dev", "", "worker-1", "beads-123")

        for required in ["log()", "BD_ROOT", "WORKER_LOG_FILE", "SESSION_START"]:
            assert required in worker, f"Worker prompt missing: {required}"
            assert required in single, f"Single task prompt missing: {required}"

    def test_prompts_both_include_role_content(self) -> None:
        """Both prompt types embed the role-specific content."""
        spawner = SubprocessSpawner(logs_dir=Path("/tmp"), test_mode=True)
        content = "# Custom role-specific instructions"
        worker = spawner._build_worker_prompt("dev", content, "worker-1")
        single = spawner._build_single_task_prompt("dev", content, "worker-1", "beads-123")

        assert content in worker
        assert content in single
