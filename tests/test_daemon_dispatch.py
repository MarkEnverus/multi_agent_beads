"""Tests for daemon worker dispatch loop."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mab.daemon import Daemon
from mab.workers import Worker, WorkerNotFoundError, WorkerSpawnError, WorkerStatus


class TestDispatchLoopState:
    """Tests for dispatch loop initialization and state management."""

    def test_dispatch_state_defaults(self, tmp_path: Path) -> None:
        """Test dispatch state has correct defaults."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        assert daemon._dispatch_enabled is False
        assert daemon._dispatch_roles == []
        assert daemon._dispatch_project_path is None
        assert daemon._dispatch_interval_seconds == 5.0
        assert daemon._dispatch_task is None

    def test_start_dispatch_configures_state(self, tmp_path: Path) -> None:
        """Test start_dispatch sets up configuration correctly."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon.start_dispatch(
            project_path=project_path,
            roles=["dev", "qa"],
            interval_seconds=10.0,
        )

        assert daemon._dispatch_enabled is True
        assert daemon._dispatch_project_path == project_path
        assert daemon._dispatch_roles == ["dev", "qa"]
        assert daemon._dispatch_interval_seconds == 10.0

    def test_start_dispatch_default_roles(self, tmp_path: Path) -> None:
        """Test start_dispatch uses all roles when none specified."""
        from mab.spawner import ROLE_TO_LABEL

        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon.start_dispatch(project_path=project_path)

        assert daemon._dispatch_roles == list(ROLE_TO_LABEL.keys())

    def test_stop_dispatch_disables(self, tmp_path: Path) -> None:
        """Test stop_dispatch disables the dispatch loop."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = "/some/path"

        daemon.stop_dispatch()

        assert daemon._dispatch_enabled is False


class TestRunBdReady:
    """Tests for _run_bd_ready subprocess execution."""

    @pytest.mark.asyncio
    async def test_bd_not_found(self, tmp_path: Path) -> None:
        """Test _run_bd_ready returns empty when bd not on PATH."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        with patch("mab.daemon.shutil.which", return_value=None):
            result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_missing_beads_db(self, tmp_path: Path) -> None:
        """Test _run_bd_ready returns empty when beads.db doesn't exist."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_successful_bd_ready(self, tmp_path: Path) -> None:
        """Test _run_bd_ready parses JSON output correctly."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        # Create fake beads.db
        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        beads_json = json.dumps(
            [
                {"id": "bead-001", "title": "Fix bug", "priority": 1},
                {"id": "bead-002", "title": "Add feature", "priority": 2},
            ]
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(beads_json.encode(), b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert len(result) == 2
        assert result[0]["id"] == "bead-001"
        assert result[1]["id"] == "bead-002"

    @pytest.mark.asyncio
    async def test_bd_ready_with_label_filter(self, tmp_path: Path) -> None:
        """Test _run_bd_ready passes label filter for roles with labels."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"[]", b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await daemon._run_bd_ready("dev", str(tmp_path))

        # Check that -l dev was included in the command
        call_args = mock_exec.call_args[0]
        assert "-l" in call_args
        assert "dev" in call_args

    @pytest.mark.asyncio
    async def test_bd_ready_manager_no_label(self, tmp_path: Path) -> None:
        """Test _run_bd_ready does NOT pass label for manager role."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"[]", b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await daemon._run_bd_ready("manager", str(tmp_path))

        # Manager has no label filter
        call_args = mock_exec.call_args[0]
        assert "-l" not in call_args

    @pytest.mark.asyncio
    async def test_bd_ready_nonzero_exit(self, tmp_path: Path) -> None:
        """Test _run_bd_ready returns empty on non-zero exit code."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.returncode = 1

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_bd_ready_invalid_json(self, tmp_path: Path) -> None:
        """Test _run_bd_ready handles invalid JSON gracefully."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"not json", b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_bd_ready_empty_output(self, tmp_path: Path) -> None:
        """Test _run_bd_ready handles empty output."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_bd_ready_timeout(self, tmp_path: Path) -> None:
        """Test _run_bd_ready handles subprocess timeout."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []


class TestDispatchForRole:
    """Tests for _dispatch_for_role method."""

    @pytest.mark.asyncio
    async def test_skips_when_active_worker_exists(self, tmp_path: Path) -> None:
        """Test _dispatch_for_role skips when role already has active worker."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        # Simulate an active worker
        daemon._active_workers_by_role[project_path] = {"dev": {"worker-123"}}

        # Mock _is_worker_still_running to return True
        with patch.object(daemon, "_is_worker_still_running", return_value=True):
            with patch.object(daemon, "_run_bd_ready") as mock_bd:
                await daemon._dispatch_for_role("dev", project_path)

        # Should not even check for beads
        mock_bd.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_beads_available(self, tmp_path: Path) -> None:
        """Test _dispatch_for_role does nothing when no beads available."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        with patch.object(daemon, "_run_bd_ready", return_value=[]):
            with patch.object(daemon, "_get_project_manager") as mock_mgr:
                await daemon._dispatch_for_role("dev", project_path)

        mock_mgr.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawns_worker_for_available_bead(self, tmp_path: Path) -> None:
        """Test _dispatch_for_role spawns worker when bead available."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        beads = [{"id": "bead-001", "title": "Fix bug", "priority": 1}]
        mock_worker = Worker(
            id="worker-abc",
            role="dev",
            project_path=project_path,
            status=WorkerStatus.RUNNING,
            pid=12345,
        )
        mock_manager = AsyncMock()
        mock_manager.spawn = AsyncMock(return_value=mock_worker)

        with patch.object(daemon, "_run_bd_ready", return_value=beads):
            with patch.object(daemon, "_get_project_manager", return_value=mock_manager):
                await daemon._dispatch_for_role("dev", project_path)

        # Verify spawn was called with bead_id and auto_restart=False
        mock_manager.spawn.assert_called_once_with(
            role="dev",
            project_path=project_path,
            auto_restart=False,
            bead_id="bead-001",
        )

        # Verify worker was registered
        assert "worker-abc" in daemon._active_workers_by_role[project_path]["dev"]

    @pytest.mark.asyncio
    async def test_picks_first_bead(self, tmp_path: Path) -> None:
        """Test _dispatch_for_role picks the first (highest priority) bead."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        beads = [
            {"id": "bead-p1", "title": "High priority", "priority": 1},
            {"id": "bead-p2", "title": "Lower priority", "priority": 2},
        ]
        mock_worker = Worker(
            id="worker-abc",
            role="dev",
            project_path=project_path,
            status=WorkerStatus.RUNNING,
            pid=12345,
        )
        mock_manager = AsyncMock()
        mock_manager.spawn = AsyncMock(return_value=mock_worker)

        with patch.object(daemon, "_run_bd_ready", return_value=beads):
            with patch.object(daemon, "_get_project_manager", return_value=mock_manager):
                await daemon._dispatch_for_role("dev", project_path)

        # Should pick bead-p1 (first in list)
        call_kwargs = mock_manager.spawn.call_args[1]
        assert call_kwargs["bead_id"] == "bead-p1"

    @pytest.mark.asyncio
    async def test_handles_spawn_failure(self, tmp_path: Path) -> None:
        """Test _dispatch_for_role handles spawn failure gracefully."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        beads = [{"id": "bead-001", "title": "Fix bug"}]
        mock_manager = AsyncMock()
        mock_manager.spawn = AsyncMock(side_effect=WorkerSpawnError("spawn failed"))

        with patch.object(daemon, "_run_bd_ready", return_value=beads):
            with patch.object(daemon, "_get_project_manager", return_value=mock_manager):
                # Should not raise
                await daemon._dispatch_for_role("dev", project_path)

        # Worker should NOT be registered since spawn failed
        assert project_path not in daemon._active_workers_by_role


class TestWorkerDispatchLoop:
    """Tests for the _worker_dispatch_loop method."""

    @pytest.mark.asyncio
    async def test_exits_when_no_project_path(self, tmp_path: Path) -> None:
        """Test dispatch loop exits early when no project_path configured."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = None

        # Should return immediately
        await daemon._worker_dispatch_loop()

    @pytest.mark.asyncio
    async def test_exits_when_disabled(self, tmp_path: Path) -> None:
        """Test dispatch loop exits when disabled."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = False
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev"]

        # Should return immediately since not enabled
        await daemon._worker_dispatch_loop()

    @pytest.mark.asyncio
    async def test_dispatches_for_each_role(self, tmp_path: Path) -> None:
        """Test dispatch loop calls _dispatch_for_role for each configured role."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev", "qa"]
        daemon._dispatch_interval_seconds = 0.01

        call_count = 0

        async def mock_dispatch(role, project_path):
            nonlocal call_count
            call_count += 1
            # Stop after one full cycle
            if call_count >= 2:
                daemon._dispatch_enabled = False

        with patch.object(daemon, "_dispatch_for_role", side_effect=mock_dispatch):
            await daemon._worker_dispatch_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_stops_on_shutdown(self, tmp_path: Path) -> None:
        """Test dispatch loop stops when _shutting_down is set."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev"]
        daemon._dispatch_interval_seconds = 0.01

        async def set_shutdown(*args, **kwargs):
            daemon._shutting_down = True

        with patch.object(daemon, "_dispatch_for_role", side_effect=set_shutdown):
            await daemon._worker_dispatch_loop()

        assert daemon._shutting_down is True

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self, tmp_path: Path) -> None:
        """Test dispatch loop continues after errors."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev"]
        daemon._dispatch_interval_seconds = 0.01

        call_count = 0

        async def mock_dispatch(role, project_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("temporary error")
            # Stop on second call
            daemon._dispatch_enabled = False

        with patch.object(daemon, "_dispatch_for_role", side_effect=mock_dispatch):
            await daemon._worker_dispatch_loop()

        # Should have continued past the error
        assert call_count == 2


class TestDispatchRPCHandlers:
    """Tests for dispatch-related RPC handlers."""

    @pytest.mark.asyncio
    async def test_handle_dispatch_start(self, tmp_path: Path) -> None:
        """Test dispatch.start RPC handler configures and enables dispatch."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        result = await daemon._handle_dispatch_start(
            {
                "project_path": project_path,
                "roles": ["dev", "qa"],
                "interval_seconds": 10.0,
            }
        )

        assert result["success"] is True
        assert result["project_path"] == project_path
        assert result["roles"] == ["dev", "qa"]
        assert result["interval_seconds"] == 10.0
        assert daemon._dispatch_enabled is True

    @pytest.mark.asyncio
    async def test_handle_dispatch_start_missing_project(self, tmp_path: Path) -> None:
        """Test dispatch.start fails without project_path."""
        from mab.rpc import RPCError

        daemon = Daemon(mab_dir=tmp_path / ".mab")

        with pytest.raises(RPCError):
            await daemon._handle_dispatch_start({})

    @pytest.mark.asyncio
    async def test_handle_dispatch_stop(self, tmp_path: Path) -> None:
        """Test dispatch.stop RPC handler disables dispatch."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True

        result = await daemon._handle_dispatch_stop({})

        assert result["success"] is True
        assert daemon._dispatch_enabled is False

    @pytest.mark.asyncio
    async def test_handle_dispatch_status(self, tmp_path: Path) -> None:
        """Test dispatch.status RPC handler returns correct state."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = "/some/project"
        daemon._dispatch_roles = ["dev"]
        daemon._dispatch_interval_seconds = 5.0

        result = await daemon._handle_dispatch_status({})

        assert result["enabled"] is True
        assert result["project_path"] == "/some/project"
        assert result["roles"] == ["dev"]
        assert result["interval_seconds"] == 5.0
        assert result["task_running"] is False


class TestHandleWorkerSpawnBeadId:
    """Tests for bead_id passthrough in _handle_worker_spawn."""

    @pytest.mark.asyncio
    async def test_spawn_passes_bead_id(self, tmp_path: Path) -> None:
        """Test _handle_worker_spawn passes bead_id to manager.spawn."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        mock_worker = Worker(
            id="worker-abc",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.RUNNING,
            pid=12345,
            bead_id="bead-001",
        )
        mock_manager = AsyncMock()
        mock_manager.spawn = AsyncMock(return_value=mock_worker)

        with patch.object(daemon, "_get_project_manager", return_value=mock_manager):
            result = await daemon._handle_worker_spawn(
                {
                    "role": "dev",
                    "project_path": str(tmp_path),
                    "bead_id": "bead-001",
                }
            )

        mock_manager.spawn.assert_called_once_with(
            role="dev",
            project_path=str(tmp_path),
            auto_restart=True,
            bead_id="bead-001",
        )
        assert result["worker_id"] == "worker-abc"

    @pytest.mark.asyncio
    async def test_spawn_without_bead_id(self, tmp_path: Path) -> None:
        """Test _handle_worker_spawn passes None bead_id when not provided."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        mock_worker = Worker(
            id="worker-abc",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.RUNNING,
            pid=12345,
        )
        mock_manager = AsyncMock()
        mock_manager.spawn = AsyncMock(return_value=mock_worker)

        with patch.object(daemon, "_get_project_manager", return_value=mock_manager):
            await daemon._handle_worker_spawn(
                {
                    "role": "dev",
                    "project_path": str(tmp_path),
                }
            )

        mock_manager.spawn.assert_called_once_with(
            role="dev",
            project_path=str(tmp_path),
            auto_restart=True,
            bead_id=None,
        )


class TestHasActiveWorker:
    """Tests for _has_active_worker method."""

    def test_no_active_workers_returns_false(self, tmp_path: Path) -> None:
        """Returns False when no workers tracked at all."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        assert daemon._has_active_worker("dev", str(tmp_path)) is False

    def test_active_running_worker_returns_true(self, tmp_path: Path) -> None:
        """Returns True when a running worker exists for the role."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._active_workers_by_role[project_path] = {"dev": {"worker-1"}}

        with patch.object(daemon, "_is_worker_still_running", return_value=True):
            assert daemon._has_active_worker("dev", project_path) is True

    def test_stale_worker_cleaned_up(self, tmp_path: Path) -> None:
        """Stale workers (no longer running) are cleaned up and return False."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._active_workers_by_role[project_path] = {"dev": {"worker-stale"}}

        with patch.object(daemon, "_is_worker_still_running", return_value=False):
            assert daemon._has_active_worker("dev", project_path) is False

        # The stale worker should have been removed
        assert "worker-stale" not in daemon._active_workers_by_role[project_path].get("dev", set())

    def test_different_role_not_counted(self, tmp_path: Path) -> None:
        """Worker for a different role doesn't count."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._active_workers_by_role[project_path] = {"qa": {"worker-qa"}}

        with patch.object(daemon, "_is_worker_still_running", return_value=True):
            assert daemon._has_active_worker("dev", project_path) is False

    def test_no_project_path_checks_all(self, tmp_path: Path) -> None:
        """Without project_path, checks across all projects."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_a = str(tmp_path / "project-a")
        project_b = str(tmp_path / "project-b")

        daemon._active_workers_by_role[project_a] = {"dev": set()}
        daemon._active_workers_by_role[project_b] = {"dev": {"worker-b"}}

        with patch.object(daemon, "_is_worker_still_running", return_value=True):
            assert daemon._has_active_worker("dev") is True

    def test_no_project_path_all_stale(self, tmp_path: Path) -> None:
        """Without project_path, returns False if all workers are stale."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_a = str(tmp_path / "project-a")

        daemon._active_workers_by_role[project_a] = {"dev": {"worker-dead"}}

        with patch.object(daemon, "_is_worker_still_running", return_value=False):
            assert daemon._has_active_worker("dev") is False


class TestRegisterUnregisterWorker:
    """Tests for _register_active_worker and _unregister_active_worker."""

    def test_register_creates_nested_structure(self, tmp_path: Path) -> None:
        """Register creates project and role entries when missing."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._register_active_worker("worker-1", "dev", project_path)

        assert "worker-1" in daemon._active_workers_by_role[project_path]["dev"]

    def test_register_multiple_workers(self, tmp_path: Path) -> None:
        """Multiple workers can be registered for same role."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._register_active_worker("worker-1", "dev", project_path)
        daemon._register_active_worker("worker-2", "dev", project_path)

        assert daemon._active_workers_by_role[project_path]["dev"] == {"worker-1", "worker-2"}

    def test_unregister_removes_worker(self, tmp_path: Path) -> None:
        """Unregister removes the worker from tracking."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._register_active_worker("worker-1", "dev", project_path)
        daemon._unregister_active_worker("worker-1", "dev", project_path)

        assert "worker-1" not in daemon._active_workers_by_role[project_path]["dev"]

    def test_unregister_nonexistent_no_error(self, tmp_path: Path) -> None:
        """Unregistering a worker that doesn't exist doesn't raise."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        # No error when project doesn't exist
        daemon._unregister_active_worker("worker-ghost", "dev", project_path)

    def test_register_different_roles(self, tmp_path: Path) -> None:
        """Different roles are tracked independently."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        project_path = str(tmp_path / "project")

        daemon._register_active_worker("worker-dev", "dev", project_path)
        daemon._register_active_worker("worker-qa", "qa", project_path)

        assert "worker-dev" in daemon._active_workers_by_role[project_path]["dev"]
        assert "worker-qa" in daemon._active_workers_by_role[project_path]["qa"]


class TestIsWorkerStillRunning:
    """Tests for _is_worker_still_running method."""

    def test_running_worker_returns_true(self, tmp_path: Path) -> None:
        """Returns True for a running worker with active process."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        worker = Worker(
            id="worker-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.RUNNING,
            pid=12345,
        )
        mock_manager = MagicMock()
        mock_manager.get = MagicMock(return_value=worker)

        with patch.object(daemon, "_find_worker_manager", return_value=mock_manager):
            with patch.object(daemon, "_is_process_running", return_value=True):
                assert daemon._is_worker_still_running("worker-1") is True

    def test_no_manager_returns_false(self, tmp_path: Path) -> None:
        """Returns False when worker's manager can't be found."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        with patch.object(daemon, "_find_worker_manager", return_value=None):
            assert daemon._is_worker_still_running("worker-ghost") is False

    def test_stopped_worker_returns_false(self, tmp_path: Path) -> None:
        """Returns False for a worker that's not in RUNNING status."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        worker = Worker(
            id="worker-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.STOPPED,
            pid=12345,
        )
        mock_manager = MagicMock()
        mock_manager.get = MagicMock(return_value=worker)

        with patch.object(daemon, "_find_worker_manager", return_value=mock_manager):
            assert daemon._is_worker_still_running("worker-1") is False

    def test_no_pid_returns_false(self, tmp_path: Path) -> None:
        """Returns False when worker has no PID."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        worker = Worker(
            id="worker-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.RUNNING,
            pid=None,
        )
        mock_manager = MagicMock()
        mock_manager.get = MagicMock(return_value=worker)

        with patch.object(daemon, "_find_worker_manager", return_value=mock_manager):
            assert daemon._is_worker_still_running("worker-1") is False

    def test_worker_not_found_returns_false(self, tmp_path: Path) -> None:
        """Returns False when worker lookup raises WorkerNotFoundError."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        mock_manager = MagicMock()
        mock_manager.get = MagicMock(side_effect=WorkerNotFoundError("not found"))

        with patch.object(daemon, "_find_worker_manager", return_value=mock_manager):
            assert daemon._is_worker_still_running("worker-gone") is False

    def test_dead_process_returns_false(self, tmp_path: Path) -> None:
        """Returns False when process is no longer running."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        worker = Worker(
            id="worker-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.RUNNING,
            pid=99999,
        )
        mock_manager = MagicMock()
        mock_manager.get = MagicMock(return_value=worker)

        with patch.object(daemon, "_find_worker_manager", return_value=mock_manager):
            with patch.object(daemon, "_is_process_running", return_value=False):
                assert daemon._is_worker_still_running("worker-1") is False


class TestDispatchLoopCancellation:
    """Tests for dispatch loop cancellation and edge cases."""

    @pytest.mark.asyncio
    async def test_cancelled_error_stops_loop(self, tmp_path: Path) -> None:
        """CancelledError breaks out of the loop cleanly."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev"]
        daemon._dispatch_interval_seconds = 0.01

        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(daemon, "_dispatch_for_role", side_effect=raise_cancelled):
            # Should not raise - CancelledError is caught
            await daemon._worker_dispatch_loop()

    @pytest.mark.asyncio
    async def test_stop_dispatch_cancels_running_task(self, tmp_path: Path) -> None:
        """stop_dispatch cancels the running asyncio task."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        # Create a mock task that's not done
        mock_task = MagicMock()
        mock_task.done.return_value = False
        daemon._dispatch_task = mock_task

        daemon._dispatch_enabled = True
        daemon.stop_dispatch()

        assert daemon._dispatch_enabled is False
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_dispatch_skips_cancel_if_task_done(self, tmp_path: Path) -> None:
        """stop_dispatch doesn't cancel an already-finished task."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        mock_task = MagicMock()
        mock_task.done.return_value = True
        daemon._dispatch_task = mock_task

        daemon.stop_dispatch()

        mock_task.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_loop_checks_enabled_between_roles(self, tmp_path: Path) -> None:
        """Dispatch loop checks enabled flag between role iterations."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev", "qa", "tech_lead"]
        daemon._dispatch_interval_seconds = 0.01

        dispatched_roles: list[str] = []

        async def mock_dispatch(role, project_path):
            dispatched_roles.append(role)
            if role == "dev":
                # Disable dispatch after first role
                daemon._dispatch_enabled = False

        with patch.object(daemon, "_dispatch_for_role", side_effect=mock_dispatch):
            await daemon._worker_dispatch_loop()

        # Should have only dispatched for dev before breaking
        assert dispatched_roles == ["dev"]


class TestRunBdReadyEdgeCases:
    """Additional edge case tests for _run_bd_ready."""

    @pytest.mark.asyncio
    async def test_bd_ready_returns_dict_not_list(self, tmp_path: Path) -> None:
        """Test _run_bd_ready returns empty when JSON output is a dict not list."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        # bd might return a dict in some error case
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(json.dumps({"error": "unexpected"}).encode(), b"")
        )
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_bd_ready_generic_exception(self, tmp_path: Path) -> None:
        """Test _run_bd_ready handles generic exceptions from subprocess."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("Permission denied"),
            ):
                result = await daemon._run_bd_ready("dev", str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_bd_ready_tech_lead_label(self, tmp_path: Path) -> None:
        """Test _run_bd_ready uses 'architecture' label for tech_lead role."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"[]", b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await daemon._run_bd_ready("tech_lead", str(tmp_path))

        call_args = mock_exec.call_args[0]
        assert "-l" in call_args
        assert "architecture" in call_args

    @pytest.mark.asyncio
    async def test_bd_ready_reviewer_label(self, tmp_path: Path) -> None:
        """Test _run_bd_ready uses 'review' label for reviewer role."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        beads_dir = tmp_path / ".beads"
        beads_dir.mkdir()
        (beads_dir / "beads.db").touch()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"[]", b""))
        mock_proc.returncode = 0

        with patch("mab.daemon.shutil.which", return_value="/usr/bin/bd"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await daemon._run_bd_ready("reviewer", str(tmp_path))

        call_args = mock_exec.call_args[0]
        assert "-l" in call_args
        assert "review" in call_args


class TestDispatchStatusWithTask:
    """Tests for dispatch status reporting with task state."""

    @pytest.mark.asyncio
    async def test_status_shows_task_running(self, tmp_path: Path) -> None:
        """dispatch.status reports task_running=True when task is active."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev"]

        mock_task = MagicMock()
        mock_task.done.return_value = False
        daemon._dispatch_task = mock_task

        result = await daemon._handle_dispatch_status({})

        assert result["task_running"] is True

    @pytest.mark.asyncio
    async def test_status_shows_task_not_running_when_done(self, tmp_path: Path) -> None:
        """dispatch.status reports task_running=False when task is done."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")
        daemon._dispatch_enabled = True
        daemon._dispatch_project_path = str(tmp_path)
        daemon._dispatch_roles = ["dev"]

        mock_task = MagicMock()
        mock_task.done.return_value = True
        daemon._dispatch_task = mock_task

        result = await daemon._handle_dispatch_status({})

        assert result["task_running"] is False

    @pytest.mark.asyncio
    async def test_status_disabled_defaults(self, tmp_path: Path) -> None:
        """dispatch.status reports correct defaults when dispatch is disabled."""
        daemon = Daemon(mab_dir=tmp_path / ".mab")

        result = await daemon._handle_dispatch_status({})

        assert result["enabled"] is False
        assert result["project_path"] is None
        assert result["roles"] == []
        assert result["task_running"] is False
