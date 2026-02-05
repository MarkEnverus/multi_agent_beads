"""Tests for daemon worker dispatch loop."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mab.daemon import Daemon
from mab.workers import Worker, WorkerSpawnError, WorkerStatus


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

        beads_json = json.dumps([
            {"id": "bead-001", "title": "Fix bug", "priority": 1},
            {"id": "bead-002", "title": "Add feature", "priority": 2},
        ])

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

        result = await daemon._handle_dispatch_start({
            "project_path": project_path,
            "roles": ["dev", "qa"],
            "interval_seconds": 10.0,
        })

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
            result = await daemon._handle_worker_spawn({
                "role": "dev",
                "project_path": str(tmp_path),
                "bead_id": "bead-001",
            })

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
            await daemon._handle_worker_spawn({
                "role": "dev",
                "project_path": str(tmp_path),
            })

        mock_manager.spawn.assert_called_once_with(
            role="dev",
            project_path=str(tmp_path),
            auto_restart=True,
            bead_id=None,
        )
