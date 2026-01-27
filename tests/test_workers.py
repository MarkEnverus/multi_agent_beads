"""Tests for MAB workers module."""

import asyncio
import os
from datetime import datetime
from pathlib import Path

import pytest

from mab.workers import (
    VALID_ROLES,
    HealthConfig,
    HealthStatus,
    Worker,
    WorkerDatabase,
    WorkerManager,
    WorkerNotFoundError,
    WorkerSpawnError,
    WorkerStatus,
    get_default_worker_manager,
)


class TestWorkerStatus:
    """Tests for WorkerStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Test that all expected statuses exist."""
        assert WorkerStatus.PENDING.value == "pending"
        assert WorkerStatus.STARTING.value == "starting"
        assert WorkerStatus.RUNNING.value == "running"
        assert WorkerStatus.STOPPING.value == "stopping"
        assert WorkerStatus.STOPPED.value == "stopped"
        assert WorkerStatus.CRASHED.value == "crashed"
        assert WorkerStatus.FAILED.value == "failed"


class TestWorkerDataclass:
    """Tests for Worker dataclass."""

    def test_worker_defaults(self) -> None:
        """Test Worker has correct defaults."""
        worker = Worker(
            id="test-worker-1",
            role="dev",
            project_path="/tmp/project",
        )

        assert worker.id == "test-worker-1"
        assert worker.role == "dev"
        assert worker.project_path == "/tmp/project"
        assert worker.status == WorkerStatus.PENDING
        assert worker.pid is None
        assert worker.crash_count == 0
        assert worker.created_at is not None

    def test_worker_to_dict(self) -> None:
        """Test Worker converts to dict correctly."""
        worker = Worker(
            id="test-worker-2",
            role="qa",
            project_path="/tmp/project",
            status=WorkerStatus.RUNNING,
            pid=12345,
            crash_count=1,
        )

        d = worker.to_dict()

        assert d["id"] == "test-worker-2"
        assert d["role"] == "qa"
        assert d["status"] == "running"
        assert d["pid"] == 12345
        assert d["crash_count"] == 1


class TestValidRoles:
    """Tests for valid roles."""

    def test_all_roles_valid(self) -> None:
        """Test that expected roles are valid."""
        assert "dev" in VALID_ROLES
        assert "qa" in VALID_ROLES
        assert "tech_lead" in VALID_ROLES
        assert "manager" in VALID_ROLES
        assert "reviewer" in VALID_ROLES

    def test_invalid_roles_rejected(self) -> None:
        """Test that arbitrary strings are not valid roles."""
        assert "admin" not in VALID_ROLES
        assert "developer" not in VALID_ROLES  # Should be 'dev'
        assert "tester" not in VALID_ROLES


class TestWorkerDatabase:
    """Tests for WorkerDatabase."""

    def test_database_creation(self, tmp_path: Path) -> None:
        """Test database is created correctly."""
        db_path = tmp_path / ".mab" / "workers.db"
        WorkerDatabase(db_path)  # Creates the database

        assert db_path.exists()

    def test_insert_and_get_worker(self, tmp_path: Path) -> None:
        """Test inserting and retrieving a worker."""
        db = WorkerDatabase(tmp_path / "workers.db")

        worker = Worker(
            id="test-1",
            role="dev",
            project_path="/tmp/test",
            status=WorkerStatus.RUNNING,
            pid=12345,
        )

        db.insert_worker(worker)
        retrieved = db.get_worker("test-1")

        assert retrieved is not None
        assert retrieved.id == "test-1"
        assert retrieved.role == "dev"
        assert retrieved.status == WorkerStatus.RUNNING
        assert retrieved.pid == 12345

    def test_get_nonexistent_worker(self, tmp_path: Path) -> None:
        """Test getting a worker that doesn't exist."""
        db = WorkerDatabase(tmp_path / "workers.db")
        retrieved = db.get_worker("nonexistent")
        assert retrieved is None

    def test_update_worker(self, tmp_path: Path) -> None:
        """Test updating a worker."""
        db = WorkerDatabase(tmp_path / "workers.db")

        worker = Worker(
            id="test-2",
            role="qa",
            project_path="/tmp/test",
        )
        db.insert_worker(worker)

        # Update worker
        worker.status = WorkerStatus.STOPPED
        worker.pid = 99999
        db.update_worker(worker)

        retrieved = db.get_worker("test-2")
        assert retrieved is not None
        assert retrieved.status == WorkerStatus.STOPPED
        assert retrieved.pid == 99999

    def test_list_workers(self, tmp_path: Path) -> None:
        """Test listing workers."""
        db = WorkerDatabase(tmp_path / "workers.db")

        # Insert multiple workers
        for i in range(3):
            worker = Worker(
                id=f"worker-{i}",
                role="dev",
                project_path="/tmp/test",
            )
            db.insert_worker(worker)

        workers = db.list_workers()
        assert len(workers) == 3

    def test_list_workers_with_status_filter(self, tmp_path: Path) -> None:
        """Test listing workers filtered by status."""
        db = WorkerDatabase(tmp_path / "workers.db")

        # Insert workers with different statuses
        db.insert_worker(Worker(id="w1", role="dev", project_path="/tmp", status=WorkerStatus.RUNNING))
        db.insert_worker(Worker(id="w2", role="dev", project_path="/tmp", status=WorkerStatus.RUNNING))
        db.insert_worker(Worker(id="w3", role="dev", project_path="/tmp", status=WorkerStatus.STOPPED))

        running = db.list_workers(status=WorkerStatus.RUNNING)
        assert len(running) == 2

        stopped = db.list_workers(status=WorkerStatus.STOPPED)
        assert len(stopped) == 1

    def test_list_workers_with_role_filter(self, tmp_path: Path) -> None:
        """Test listing workers filtered by role."""
        db = WorkerDatabase(tmp_path / "workers.db")

        db.insert_worker(Worker(id="w1", role="dev", project_path="/tmp"))
        db.insert_worker(Worker(id="w2", role="qa", project_path="/tmp"))
        db.insert_worker(Worker(id="w3", role="dev", project_path="/tmp"))

        devs = db.list_workers(role="dev")
        assert len(devs) == 2

        qas = db.list_workers(role="qa")
        assert len(qas) == 1

    def test_delete_worker(self, tmp_path: Path) -> None:
        """Test deleting a worker."""
        db = WorkerDatabase(tmp_path / "workers.db")

        worker = Worker(id="to-delete", role="dev", project_path="/tmp")
        db.insert_worker(worker)

        assert db.get_worker("to-delete") is not None

        deleted = db.delete_worker("to-delete")
        assert deleted is True

        assert db.get_worker("to-delete") is None

    def test_delete_nonexistent_worker(self, tmp_path: Path) -> None:
        """Test deleting a worker that doesn't exist."""
        db = WorkerDatabase(tmp_path / "workers.db")
        deleted = db.delete_worker("nonexistent")
        assert deleted is False

    def test_count_workers(self, tmp_path: Path) -> None:
        """Test counting workers."""
        db = WorkerDatabase(tmp_path / "workers.db")

        assert db.count_workers() == 0

        db.insert_worker(Worker(id="w1", role="dev", project_path="/tmp", status=WorkerStatus.RUNNING))
        db.insert_worker(Worker(id="w2", role="dev", project_path="/tmp", status=WorkerStatus.RUNNING))
        db.insert_worker(Worker(id="w3", role="dev", project_path="/tmp", status=WorkerStatus.STOPPED))

        assert db.count_workers() == 3
        assert db.count_workers(status=WorkerStatus.RUNNING) == 2
        assert db.count_workers(status=WorkerStatus.STOPPED) == 1


class TestWorkerManager:
    """Tests for WorkerManager."""

    def test_manager_creation(self, tmp_path: Path) -> None:
        """Test WorkerManager is created correctly."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        assert manager.mab_dir == tmp_path / ".mab"
        assert manager.heartbeat_dir.exists()

    def test_generate_worker_id(self, tmp_path: Path) -> None:
        """Test worker ID generation."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        id1 = manager._generate_worker_id("dev")
        id2 = manager._generate_worker_id("dev")

        assert id1.startswith("worker-dev-")
        assert id2.startswith("worker-dev-")
        assert id1 != id2  # Should be unique

    def test_heartbeat_file_path(self, tmp_path: Path) -> None:
        """Test heartbeat file path generation."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        path = manager._get_heartbeat_file("worker-1")
        assert path.parent == manager.heartbeat_dir
        assert path.name == "worker-1.heartbeat"

    def test_update_and_check_heartbeat(self, tmp_path: Path) -> None:
        """Test heartbeat update and check."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        worker_id = "test-worker"

        # No heartbeat initially
        assert manager._check_heartbeat(worker_id) is None

        # Update heartbeat
        manager._update_heartbeat(worker_id)

        # Should have recent heartbeat
        hb = manager._check_heartbeat(worker_id)
        assert hb is not None
        assert (datetime.now() - hb).total_seconds() < 2

    def test_cleanup_heartbeat(self, tmp_path: Path) -> None:
        """Test heartbeat cleanup."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        worker_id = "test-worker"
        manager._update_heartbeat(worker_id)

        assert manager._check_heartbeat(worker_id) is not None

        manager._cleanup_heartbeat(worker_id)

        assert manager._check_heartbeat(worker_id) is None

    def test_list_workers_empty(self, tmp_path: Path) -> None:
        """Test listing workers when empty."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")
        workers = manager.list_workers()
        assert workers == []

    def test_count_running_empty(self, tmp_path: Path) -> None:
        """Test counting running workers when empty."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")
        count = manager.count_running()
        assert count == 0

    def test_get_nonexistent_worker(self, tmp_path: Path) -> None:
        """Test getting a worker that doesn't exist raises error."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        with pytest.raises(WorkerNotFoundError):
            manager.get("nonexistent")


class TestWorkerManagerAsync:
    """Async tests for WorkerManager."""

    @pytest.mark.asyncio
    async def test_spawn_invalid_role(self, tmp_path: Path) -> None:
        """Test spawning worker with invalid role raises error."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        with pytest.raises(WorkerSpawnError) as exc_info:
            await manager.spawn(
                role="invalid_role",
                project_path=str(tmp_path),
            )

        assert "Invalid role" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_spawn_worker_creates_record(self, tmp_path: Path) -> None:
        """Test spawning worker creates database record."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        # Spawn a worker (uses placeholder process in test mode)
        worker = await manager.spawn(
            role="dev",
            project_path=str(tmp_path),
        )

        assert worker.id.startswith("worker-dev-")
        assert worker.role == "dev"
        assert worker.project_path == str(tmp_path)
        assert worker.status == WorkerStatus.RUNNING
        assert worker.pid is not None

        # Worker should be in database
        retrieved = manager.get(worker.id)
        assert retrieved.id == worker.id

        # Cleanup
        await manager.stop(worker.id)

    @pytest.mark.asyncio
    async def test_stop_worker(self, tmp_path: Path) -> None:
        """Test stopping a worker."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        # Spawn and stop
        worker = await manager.spawn(
            role="qa",
            project_path=str(tmp_path),
        )

        stopped = await manager.stop(worker.id)

        assert stopped.status == WorkerStatus.STOPPED
        assert stopped.stopped_at is not None

    @pytest.mark.asyncio
    async def test_stop_nonexistent_worker(self, tmp_path: Path) -> None:
        """Test stopping a nonexistent worker raises error."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        with pytest.raises(WorkerNotFoundError):
            await manager.stop("nonexistent")

    @pytest.mark.asyncio
    async def test_stop_all_workers(self, tmp_path: Path) -> None:
        """Test stopping all workers."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        # Spawn multiple workers
        await manager.spawn(role="dev", project_path=str(tmp_path))
        await manager.spawn(role="qa", project_path=str(tmp_path))

        assert manager.count_running() == 2

        # Stop all
        stopped = await manager.stop_all()

        assert len(stopped) == 2
        assert manager.count_running() == 0

    @pytest.mark.asyncio
    async def test_health_check_detects_crashed_worker(self, tmp_path: Path) -> None:
        """Test health check detects crashed workers."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        # Spawn worker
        worker = await manager.spawn(
            role="dev",
            project_path=str(tmp_path),
        )

        # Kill the process to simulate crash
        if worker.pid is not None:
            os.kill(worker.pid, 9)  # SIGKILL

        # Reap the zombie process by waiting on it
        if worker.id in manager._active_processes:
            proc = manager._active_processes[worker.id]
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

        # Give it time to die
        await asyncio.sleep(0.5)

        # Health check should detect crash
        crashed = await manager.health_check()

        assert len(crashed) == 1
        assert crashed[0].id == worker.id
        assert crashed[0].status == WorkerStatus.CRASHED


class TestGetDefaultWorkerManager:
    """Tests for get_default_worker_manager function."""

    def test_default_manager_uses_home_dir(self) -> None:
        """Test default manager uses ~/.mab/."""
        manager = get_default_worker_manager()
        assert manager.mab_dir == Path.home() / ".mab"

    def test_custom_mab_dir(self, tmp_path: Path) -> None:
        """Test manager with custom mab_dir."""
        manager = get_default_worker_manager(mab_dir=tmp_path / "custom")
        assert manager.mab_dir == tmp_path / "custom"

    def test_custom_heartbeat_dir(self, tmp_path: Path) -> None:
        """Test manager with custom heartbeat_dir."""
        manager = get_default_worker_manager(
            mab_dir=tmp_path / ".mab",
            heartbeat_dir=tmp_path / "heartbeat",
        )
        assert manager.heartbeat_dir == tmp_path / "heartbeat"


class TestHealthConfig:
    """Tests for HealthConfig."""

    def test_default_values(self) -> None:
        """Test HealthConfig has correct defaults."""
        config = HealthConfig()

        assert config.health_check_interval_seconds == 30.0
        assert config.heartbeat_timeout_seconds == 60.0
        assert config.max_restart_count == 5
        assert config.restart_backoff_base_seconds == 5.0
        assert config.restart_backoff_max_seconds == 300.0
        assert config.auto_restart_enabled is True

    def test_custom_values(self) -> None:
        """Test HealthConfig with custom values."""
        config = HealthConfig(
            health_check_interval_seconds=10.0,
            max_restart_count=3,
            restart_backoff_base_seconds=2.0,
        )

        assert config.health_check_interval_seconds == 10.0
        assert config.max_restart_count == 3
        assert config.restart_backoff_base_seconds == 2.0

    def test_calculate_backoff_first_crash(self) -> None:
        """Test backoff calculation for first crash."""
        config = HealthConfig(restart_backoff_base_seconds=5.0)

        # First crash: 5 * 2^0 = 5 seconds
        assert config.calculate_backoff(1) == 5.0

    def test_calculate_backoff_exponential(self) -> None:
        """Test exponential backoff calculation."""
        config = HealthConfig(
            restart_backoff_base_seconds=5.0,
            restart_backoff_max_seconds=300.0,
        )

        # crash 1: 5 * 2^0 = 5
        assert config.calculate_backoff(1) == 5.0
        # crash 2: 5 * 2^1 = 10
        assert config.calculate_backoff(2) == 10.0
        # crash 3: 5 * 2^2 = 20
        assert config.calculate_backoff(3) == 20.0
        # crash 4: 5 * 2^3 = 40
        assert config.calculate_backoff(4) == 40.0
        # crash 5: 5 * 2^4 = 80
        assert config.calculate_backoff(5) == 80.0

    def test_calculate_backoff_capped_at_max(self) -> None:
        """Test backoff is capped at max value."""
        config = HealthConfig(
            restart_backoff_base_seconds=5.0,
            restart_backoff_max_seconds=50.0,
        )

        # crash 5: 5 * 2^4 = 80, but capped at 50
        assert config.calculate_backoff(5) == 50.0

    def test_calculate_backoff_zero_crash(self) -> None:
        """Test backoff for zero crashes returns zero."""
        config = HealthConfig()
        assert config.calculate_backoff(0) == 0.0

    def test_to_dict(self) -> None:
        """Test HealthConfig to_dict."""
        config = HealthConfig(
            health_check_interval_seconds=15.0,
            max_restart_count=3,
        )
        d = config.to_dict()

        assert d["health_check_interval_seconds"] == 15.0
        assert d["max_restart_count"] == 3


class TestHealthStatus:
    """Tests for HealthStatus."""

    def test_default_values(self) -> None:
        """Test HealthStatus has correct defaults."""
        status = HealthStatus()

        assert status.healthy_workers == 0
        assert status.unhealthy_workers == 0
        assert status.crashed_workers == 0
        assert status.total_restarts == 0
        assert status.workers_at_max_restarts == 0

    def test_to_dict(self) -> None:
        """Test HealthStatus to_dict."""
        status = HealthStatus(
            healthy_workers=5,
            unhealthy_workers=1,
            crashed_workers=2,
            total_restarts=10,
        )
        d = status.to_dict()

        assert d["healthy_workers"] == 5
        assert d["unhealthy_workers"] == 1
        assert d["crashed_workers"] == 2
        assert d["total_restarts"] == 10
        assert "config" in d


class TestWorkerWithAutoRestart:
    """Tests for Worker with auto-restart fields."""

    def test_worker_auto_restart_defaults(self) -> None:
        """Test Worker has auto-restart defaults."""
        worker = Worker(
            id="test-1",
            role="dev",
            project_path="/tmp/test",
        )

        assert worker.auto_restart_enabled is True
        assert worker.last_restart_at is None

    def test_worker_to_dict_includes_restart_fields(self) -> None:
        """Test Worker to_dict includes restart fields."""
        worker = Worker(
            id="test-1",
            role="dev",
            project_path="/tmp/test",
            auto_restart_enabled=False,
            last_restart_at="2024-01-01T12:00:00",
        )
        d = worker.to_dict()

        assert d["auto_restart_enabled"] is False
        assert d["last_restart_at"] == "2024-01-01T12:00:00"


class TestWorkerManagerHealthConfig:
    """Tests for WorkerManager with HealthConfig."""

    def test_manager_accepts_health_config(self, tmp_path: Path) -> None:
        """Test WorkerManager accepts health config."""
        config = HealthConfig(
            health_check_interval_seconds=10.0,
            max_restart_count=3,
        )
        manager = WorkerManager(
            mab_dir=tmp_path / ".mab",
            health_config=config,
        )

        assert manager.health_config.health_check_interval_seconds == 10.0
        assert manager.health_config.max_restart_count == 3

    def test_manager_uses_default_health_config(self, tmp_path: Path) -> None:
        """Test WorkerManager uses default config if not specified."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        assert manager.health_config.health_check_interval_seconds == 30.0
        assert manager.health_config.max_restart_count == 5

    def test_get_health_status_empty(self, tmp_path: Path) -> None:
        """Test get_health_status with no workers."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")
        status = manager.get_health_status()

        assert status.healthy_workers == 0
        assert status.unhealthy_workers == 0
        assert status.crashed_workers == 0
        assert status.total_restarts == 0


class TestAutoRestartAsync:
    """Async tests for auto-restart functionality."""

    @pytest.mark.asyncio
    async def test_auto_restart_disabled_globally(self, tmp_path: Path) -> None:
        """Test auto-restart respects global disable."""
        config = HealthConfig(auto_restart_enabled=False)
        manager = WorkerManager(
            mab_dir=tmp_path / ".mab",
            health_config=config,
        )

        worker = Worker(
            id="test-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.CRASHED,
            crash_count=1,
        )
        manager.db.insert_worker(worker)

        result = await manager.auto_restart(worker)
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_restart_disabled_per_worker(self, tmp_path: Path) -> None:
        """Test auto-restart respects per-worker disable."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab")

        worker = Worker(
            id="test-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.CRASHED,
            crash_count=1,
            auto_restart_enabled=False,
        )
        manager.db.insert_worker(worker)

        result = await manager.auto_restart(worker)
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_restart_max_count_exceeded(self, tmp_path: Path) -> None:
        """Test auto-restart stops at max restart count."""
        config = HealthConfig(max_restart_count=3)
        manager = WorkerManager(
            mab_dir=tmp_path / ".mab",
            health_config=config,
        )

        worker = Worker(
            id="test-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.CRASHED,
            crash_count=3,  # At max
        )
        manager.db.insert_worker(worker)

        result = await manager.auto_restart(worker)
        assert result is None

        # Worker should be marked as disabled
        updated = manager.db.get_worker("test-1")
        assert updated is not None
        assert updated.auto_restart_enabled is False

    @pytest.mark.asyncio
    async def test_health_check_and_restart_schedules_restarts(self, tmp_path: Path) -> None:
        """Test health_check_and_restart schedules restarts for crashed workers."""
        config = HealthConfig(
            restart_backoff_base_seconds=0.1,  # Fast for testing
        )
        manager = WorkerManager(
            mab_dir=tmp_path / ".mab",
            health_config=config,
            test_mode=True,
        )

        # Spawn a worker
        worker = await manager.spawn(
            role="dev",
            project_path=str(tmp_path),
        )

        # Kill it to simulate crash
        if worker.pid is not None:
            os.kill(worker.pid, 9)

        # Reap the zombie process by waiting on it
        if worker.id in manager._active_processes:
            proc = manager._active_processes[worker.id]
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

        # Wait for process to fully die
        await asyncio.sleep(0.5)

        # Health check should detect crash and schedule restart
        crashed, restart_scheduled = await manager.health_check_and_restart()

        assert len(crashed) == 1
        assert crashed[0].id == worker.id
        # Restart should be scheduled (pending)
        assert worker.id in manager._pending_restarts

        # Cancel pending restarts for cleanup
        manager.cancel_pending_restarts()

    @pytest.mark.asyncio
    async def test_cancel_pending_restarts(self, tmp_path: Path) -> None:
        """Test cancelling pending restarts."""
        config = HealthConfig(
            restart_backoff_base_seconds=10.0,  # Long delay so it stays pending
        )
        manager = WorkerManager(
            mab_dir=tmp_path / ".mab",
            health_config=config,
        )

        worker = Worker(
            id="test-1",
            role="dev",
            project_path=str(tmp_path),
            status=WorkerStatus.CRASHED,
            crash_count=1,
        )
        manager.db.insert_worker(worker)

        # Schedule restart (will be pending due to backoff)
        await manager.auto_restart(worker)

        assert "test-1" in manager._pending_restarts

        # Cancel all
        cancelled = manager.cancel_pending_restarts()

        assert cancelled == 1
        assert "test-1" not in manager._pending_restarts

    @pytest.mark.asyncio
    async def test_get_health_status_with_workers(self, tmp_path: Path) -> None:
        """Test get_health_status with running workers."""
        manager = WorkerManager(mab_dir=tmp_path / ".mab", test_mode=True)

        # Spawn a worker
        worker = await manager.spawn(
            role="dev",
            project_path=str(tmp_path),
        )

        status = manager.get_health_status()

        assert status.healthy_workers >= 0  # May or may not be healthy yet
        assert status.total_restarts == 0

        # Cleanup
        await manager.stop(worker.id)
