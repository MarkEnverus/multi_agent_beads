"""Integration tests for the complete MAB orchestration system.

This module tests the full integration of:
- Daemon process with RPC server
- Worker management through RPC
- Multi-town orchestration
- Health monitoring loop
- CLI to daemon communication
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from mab.daemon import Daemon, DaemonState
from mab.rpc import RPCClient, RPCServer
from mab.towns import TownManager, TownStatus
from mab.workers import HealthConfig, WorkerManager, WorkerStatus


@pytest.fixture
def short_tmp_path():
    """Create a short temporary directory for Unix socket tests.

    macOS has a ~104 byte limit on Unix socket paths. pytest's tmp_path
    can exceed this, so we create a shorter path in /tmp.
    """
    with tempfile.TemporaryDirectory(prefix="mab_", dir="/tmp") as tmpdir:
        yield Path(tmpdir)


class TestDaemonRPCIntegration:
    """Integration tests for daemon + RPC server."""

    @pytest.fixture
    async def daemon_server_pair(self, short_tmp_path: Path):
        """Create a daemon with RPC server for testing."""
        mab_dir = short_tmp_path / ".mab"
        daemon = Daemon(mab_dir=mab_dir)
        server = RPCServer(mab_dir=mab_dir)

        # Register basic handlers
        async def status_handler(params: dict) -> dict:
            return {
                "state": "running",
                "pid": os.getpid(),
                "workers_count": 0,
            }

        async def echo_handler(params: dict) -> dict:
            return {"echo": params}

        server.register("daemon.status", status_handler)
        server.register("test.echo", echo_handler)

        await server.start()
        yield daemon, server, mab_dir
        await server.stop()

    @pytest.mark.asyncio
    async def test_client_communicates_with_server(self, daemon_server_pair) -> None:
        """Test RPC client can communicate with server."""
        daemon, server, mab_dir = daemon_server_pair
        client = RPCClient(mab_dir=mab_dir)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: client.call("test.echo", {"message": "hello"})
        )

        assert result == {"echo": {"message": "hello"}}
        client.close()

    @pytest.mark.asyncio
    async def test_daemon_status_via_rpc(self, daemon_server_pair) -> None:
        """Test getting daemon status through RPC."""
        daemon, server, mab_dir = daemon_server_pair
        client = RPCClient(mab_dir=mab_dir)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: client.call("daemon.status", {}))

        assert result["state"] == "running"
        assert result["pid"] == os.getpid()
        client.close()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_rpc_calls(self, daemon_server_pair) -> None:
        """Test multiple concurrent RPC calls work correctly."""
        daemon, server, mab_dir = daemon_server_pair
        client = RPCClient(mab_dir=mab_dir)

        loop = asyncio.get_event_loop()

        async def make_call(i: int):
            return await loop.run_in_executor(None, lambda: client.call("test.echo", {"count": i}))

        tasks = [make_call(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        for i, result in enumerate(results):
            assert result == {"echo": {"count": i}}

        client.close()


class TestWorkerManagerIntegration:
    """Integration tests for worker management system."""

    def test_worker_manager_creates_heartbeat_dir(self, short_tmp_path: Path) -> None:
        """Test WorkerManager creates necessary directories."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir)

        assert manager.heartbeat_dir.exists()
        assert (mab_dir / "workers.db").exists()

    @pytest.mark.asyncio
    async def test_spawn_and_stop_worker_lifecycle(self, short_tmp_path: Path) -> None:
        """Test complete worker spawn and stop lifecycle."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir, test_mode=True)

        # Spawn worker
        worker = await manager.spawn(
            role="dev",
            project_path=str(short_tmp_path),
        )

        assert worker.status == WorkerStatus.RUNNING
        assert worker.pid is not None
        assert manager.count_running() == 1

        # Verify can get worker
        retrieved = manager.get(worker.id)
        assert retrieved.id == worker.id
        assert retrieved.role == "dev"

        # Stop worker
        stopped = await manager.stop(worker.id)
        assert stopped.status == WorkerStatus.STOPPED
        assert manager.count_running() == 0

    @pytest.mark.asyncio
    async def test_spawn_multiple_workers_different_roles(self, short_tmp_path: Path) -> None:
        """Test spawning workers with different roles."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir, test_mode=True)

        workers = []
        roles = ["dev", "qa", "tech_lead"]

        try:
            for role in roles:
                worker = await manager.spawn(
                    role=role,
                    project_path=str(short_tmp_path),
                )
                workers.append(worker)

            assert manager.count_running() == 3

            # Check each worker has correct role
            for worker in workers:
                retrieved = manager.get(worker.id)
                assert retrieved.role == worker.role

            # Filter by role
            devs = manager.list_workers(role="dev")
            assert len(devs) == 1
            assert devs[0].role == "dev"

        finally:
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_worker_crash_detection_and_status_update(self, short_tmp_path: Path) -> None:
        """Test that crashed workers are detected and status updated."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir, test_mode=True)

        worker = await manager.spawn(
            role="dev",
            project_path=str(short_tmp_path),
        )

        # Kill the process to simulate crash
        if worker.pid is not None:
            os.kill(worker.pid, 9)  # SIGKILL

            # Reap zombie process
            if worker.id in manager._active_processes:
                proc = manager._active_processes[worker.id]
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass

        # Give time for process to die
        await asyncio.sleep(0.5)

        # Health check should detect crash
        crashed = await manager.health_check()

        assert len(crashed) >= 1
        # Find our worker in crashed list
        our_worker = next((w for w in crashed if w.id == worker.id), None)
        assert our_worker is not None
        assert our_worker.status == WorkerStatus.CRASHED


class TestTownManagerIntegration:
    """Integration tests for multi-town management."""

    def test_town_creation_and_retrieval(self, short_tmp_path: Path) -> None:
        """Test creating and retrieving towns."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()
        manager = TownManager(mab_dir=mab_dir)

        # Create town
        town = manager.create(
            name="test_town",
            port=8100,
            max_workers=5,
            description="Integration test town",
        )

        assert town.name == "test_town"
        assert town.port == 8100
        assert town.max_workers == 5

        # Retrieve town
        retrieved = manager.get("test_town")
        assert retrieved.name == town.name
        assert retrieved.port == town.port

    def test_multiple_towns_different_ports(self, short_tmp_path: Path) -> None:
        """Test creating multiple towns with different ports."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()
        manager = TownManager(mab_dir=mab_dir)

        towns = []
        for i in range(3):
            town = manager.create(
                name=f"town_{i}",
                port=8100 + i,
            )
            towns.append(town)

        all_towns = manager.list_towns()
        assert len(all_towns) == 3

        # Each should have unique port
        ports = {t.port for t in all_towns}
        assert len(ports) == 3

    def test_town_status_transitions(self, short_tmp_path: Path) -> None:
        """Test town status transitions (stopped -> running -> stopped)."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()
        manager = TownManager(mab_dir=mab_dir)

        town = manager.create(name="status_test", port=8200)
        assert town.status == TownStatus.STOPPED

        # Start town
        started = manager.set_status(
            "status_test",
            TownStatus.RUNNING,
            pid=12345,
        )
        assert started.status == TownStatus.RUNNING
        assert started.pid == 12345
        assert started.started_at is not None

        # Stop town
        stopped = manager.set_status("status_test", TownStatus.STOPPED)
        assert stopped.status == TownStatus.STOPPED
        assert stopped.pid is None

    def test_get_or_create_default_town(self, short_tmp_path: Path) -> None:
        """Test get_or_create_default ensures default town exists."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()
        manager = TownManager(mab_dir=mab_dir)

        # First call creates
        town1 = manager.get_or_create_default()
        assert town1.name == "default"

        # Second call returns same
        town2 = manager.get_or_create_default()
        assert town2.name == "default"
        assert town2.created_at == town1.created_at


class TestHealthMonitoringIntegration:
    """Integration tests for health monitoring system."""

    def test_health_config_backoff_calculation(self) -> None:
        """Test health config calculates backoff correctly."""
        config = HealthConfig(
            restart_backoff_base_seconds=2.0,
            restart_backoff_max_seconds=60.0,
        )

        # Exponential backoff: base * 2^(crash_count-1)
        assert config.calculate_backoff(1) == 2.0  # 2 * 2^0 = 2
        assert config.calculate_backoff(2) == 4.0  # 2 * 2^1 = 4
        assert config.calculate_backoff(3) == 8.0  # 2 * 2^2 = 8

        # Should cap at max
        assert config.calculate_backoff(10) == 60.0

    @pytest.mark.asyncio
    async def test_health_check_marks_crashed_workers(self, short_tmp_path: Path) -> None:
        """Test health check updates crashed worker status."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir, test_mode=True)

        worker = await manager.spawn(
            role="qa",
            project_path=str(short_tmp_path),
        )

        # Simulate crash
        if worker.pid is not None:
            os.kill(worker.pid, 9)
            if worker.id in manager._active_processes:
                proc = manager._active_processes[worker.id]
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass

        await asyncio.sleep(0.5)

        # Run health check
        await manager.health_check()

        # Verify status in database is updated
        db_worker = manager.get(worker.id)
        assert db_worker.status == WorkerStatus.CRASHED

    @pytest.mark.asyncio
    async def test_heartbeat_updates_tracked(self, short_tmp_path: Path) -> None:
        """Test heartbeat updates are properly tracked."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir, test_mode=True)

        worker_id = "heartbeat-test-worker"

        # No heartbeat initially
        assert manager._check_heartbeat(worker_id) is None

        # Update heartbeat
        manager._update_heartbeat(worker_id)

        # Should have recent heartbeat
        hb = manager._check_heartbeat(worker_id)
        assert hb is not None

        # Cleanup removes heartbeat
        manager._cleanup_heartbeat(worker_id)
        assert manager._check_heartbeat(worker_id) is None


class TestDaemonLifecycleWithWorkers:
    """Integration tests for daemon + worker lifecycle."""

    def test_daemon_pid_file_management(self, short_tmp_path: Path) -> None:
        """Test daemon PID file is properly managed."""
        mab_dir = short_tmp_path / ".mab"
        daemon = Daemon(mab_dir=mab_dir)

        # Initially no PID
        assert daemon._read_pid() is None

        # Acquire lock and write PID
        assert daemon._acquire_lock() is True
        daemon._write_pid()

        assert daemon._read_pid() == os.getpid()

        # Remove PID
        daemon._remove_pid()
        assert daemon._read_pid() is None

        # Release lock
        daemon._release_lock()

    def test_daemon_status_reflects_state(self, short_tmp_path: Path) -> None:
        """Test daemon status accurately reflects running state."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()

        daemon = Daemon(mab_dir=mab_dir)

        # Initially stopped
        status = daemon.get_status()
        assert status.state == DaemonState.STOPPED

        # Write PID to simulate running
        (mab_dir / "daemon.pid").write_text(str(os.getpid()))

        status = daemon.get_status()
        assert status.state == DaemonState.RUNNING
        assert status.pid == os.getpid()


class TestRPCHandlerRegistration:
    """Integration tests for RPC handler system."""

    @pytest.mark.asyncio
    async def test_multiple_handlers_registered(self, short_tmp_path: Path) -> None:
        """Test multiple handlers can be registered and called."""
        mab_dir = short_tmp_path / ".mab"
        server = RPCServer(mab_dir=mab_dir)

        async def handler_a(params: dict) -> dict:
            return {"handler": "a", "result": params.get("x", 0) * 2}

        async def handler_b(params: dict) -> dict:
            return {"handler": "b", "result": params.get("x", 0) + 10}

        server.register("math.double", handler_a)
        server.register("math.add10", handler_b)

        await server.start()

        try:
            client = RPCClient(mab_dir=mab_dir)
            loop = asyncio.get_event_loop()

            result_a = await loop.run_in_executor(
                None, lambda: client.call("math.double", {"x": 5})
            )
            assert result_a == {"handler": "a", "result": 10}

            result_b = await loop.run_in_executor(None, lambda: client.call("math.add10", {"x": 5}))
            assert result_b == {"handler": "b", "result": 15}

            client.close()
        finally:
            await server.stop()


class TestSystemWideIntegration:
    """End-to-end integration tests for the full system."""

    @pytest.mark.asyncio
    async def test_full_worker_lifecycle_via_manager(self, short_tmp_path: Path) -> None:
        """Test complete worker lifecycle from spawn to cleanup."""
        mab_dir = short_tmp_path / ".mab"
        manager = WorkerManager(mab_dir=mab_dir, test_mode=True)

        # Phase 1: Spawn workers
        dev_worker = await manager.spawn(role="dev", project_path=str(short_tmp_path))
        await manager.spawn(role="qa", project_path=str(short_tmp_path))

        assert manager.count_running() == 2

        # Phase 2: List and filter
        all_workers = manager.list_workers()
        assert len(all_workers) == 2

        running_only = manager.list_workers(status=WorkerStatus.RUNNING)
        assert len(running_only) == 2

        # Phase 3: Stop one worker
        stopped = await manager.stop(dev_worker.id)
        assert stopped.status == WorkerStatus.STOPPED
        assert manager.count_running() == 1

        # Phase 4: Stop all remaining
        remaining = await manager.stop_all()
        assert len(remaining) == 1
        assert manager.count_running() == 0

    def test_town_with_worker_counts(self, short_tmp_path: Path) -> None:
        """Test town tracks worker count configuration."""
        mab_dir = short_tmp_path / ".mab"
        mab_dir.mkdir()
        manager = TownManager(mab_dir=mab_dir)

        town = manager.create(
            name="worker_town",
            port=8300,
            max_workers=5,
        )

        assert town.max_workers == 5

        # Update max workers
        updated = manager.update("worker_town", max_workers=10)
        assert updated.max_workers == 10

    @pytest.mark.asyncio
    async def test_rpc_server_handles_rapid_requests(self, short_tmp_path: Path) -> None:
        """Test RPC server handles many rapid requests."""
        mab_dir = short_tmp_path / ".mab"
        server = RPCServer(mab_dir=mab_dir)

        call_count = 0

        async def counter_handler(params: dict) -> dict:
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        server.register("test.count", counter_handler)
        await server.start()

        try:
            client = RPCClient(mab_dir=mab_dir)
            loop = asyncio.get_event_loop()

            # Make 50 rapid requests
            for _ in range(50):
                await loop.run_in_executor(None, lambda: client.call("test.count", {}))

            assert call_count == 50
            client.close()
        finally:
            await server.stop()
