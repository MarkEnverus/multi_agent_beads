"""Tests for MAB database module."""

from datetime import datetime
from pathlib import Path

import pytest

from mab.db import (
    SCHEMA_VERSION,
    delete_worker,
    get_db,
    get_schema_version,
    get_worker,
    init_db,
    insert_event,
    insert_worker,
    list_events,
    list_workers,
    migrate_db,
    update_worker,
)


class TestInitDb:
    """Tests for init_db function."""

    def test_creates_database_file(self, tmp_path: Path) -> None:
        """Test that init_db creates the database file."""
        db_path = tmp_path / "test.db"
        assert not db_path.exists()

        conn = init_db(db_path)
        conn.close()

        assert db_path.exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Test that init_db creates parent directories."""
        db_path = tmp_path / "nested" / "dir" / "test.db"
        assert not db_path.parent.exists()

        conn = init_db(db_path)
        conn.close()

        assert db_path.exists()

    def test_creates_workers_table(self, tmp_path: Path) -> None:
        """Test that workers table is created."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workers'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_worker_events_table(self, tmp_path: Path) -> None:
        """Test that worker_events table is created."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_events'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_indexes(self, tmp_path: Path) -> None:
        """Test that indexes are created."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}

        assert "idx_workers_status" in indexes
        assert "idx_workers_project" in indexes
        assert "idx_events_worker" in indexes
        assert "idx_events_timestamp" in indexes
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        """Test that init_db can be called multiple times safely."""
        db_path = tmp_path / "test.db"

        # Call init_db multiple times
        conn1 = init_db(db_path)
        conn1.close()

        conn2 = init_db(db_path)
        conn2.close()

        conn3 = init_db(db_path)

        # Verify tables still exist
        cursor = conn3.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workers'"
        )
        assert cursor.fetchone() is not None
        conn3.close()

    def test_sets_schema_version(self, tmp_path: Path) -> None:
        """Test that schema version is set."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION
        conn.close()


class TestGetDb:
    """Tests for get_db function."""

    def test_creates_db_in_mab_directory(self, tmp_path: Path) -> None:
        """Test that get_db creates database in .mab directory."""
        project_path = tmp_path / "my_project"
        project_path.mkdir()

        conn = get_db(project_path)
        conn.close()

        expected_db = project_path / ".mab" / "mab.db"
        assert expected_db.exists()

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """Test that get_db accepts string paths."""
        project_path = tmp_path / "my_project"
        project_path.mkdir()

        conn = get_db(str(project_path))
        conn.close()

        expected_db = project_path / ".mab" / "mab.db"
        assert expected_db.exists()


class TestMigration:
    """Tests for database migration."""

    def test_get_schema_version_empty_db(self, tmp_path: Path) -> None:
        """Test get_schema_version returns 0 for empty database."""
        import sqlite3

        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        version = get_schema_version(conn)
        assert version == 0
        conn.close()

    def test_migrate_already_current(self, tmp_path: Path) -> None:
        """Test migrate_db returns False when already current."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        result = migrate_db(conn)
        assert result is False
        conn.close()


class TestWorkerCrud:
    """Tests for worker CRUD operations."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path):
        """Create a test database connection."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()

    def test_insert_worker(self, db_conn) -> None:
        """Test inserting a worker."""
        insert_worker(
            db_conn,
            worker_id="worker-dev-abc123",
            role="dev",
            status="running",
            project_path="/tmp/project",
            pid=12345,
        )

        worker = get_worker(db_conn, "worker-dev-abc123")
        assert worker is not None
        assert worker["id"] == "worker-dev-abc123"
        assert worker["role"] == "dev"
        assert worker["status"] == "running"
        assert worker["pid"] == 12345

    def test_insert_worker_defaults_started_at(self, db_conn) -> None:
        """Test that started_at defaults to now."""
        before = datetime.now()

        insert_worker(
            db_conn,
            worker_id="worker-qa-xyz789",
            role="qa",
            status="spawning",
            project_path="/tmp/project",
        )

        worker = get_worker(db_conn, "worker-qa-xyz789")
        started_at = datetime.fromisoformat(worker["started_at"])
        assert started_at >= before

    def test_update_worker(self, db_conn) -> None:
        """Test updating a worker."""
        insert_worker(
            db_conn,
            worker_id="worker-dev-abc123",
            role="dev",
            status="spawning",
            project_path="/tmp/project",
        )

        result = update_worker(
            db_conn,
            "worker-dev-abc123",
            status="running",
            pid=99999,
        )
        assert result is True

        worker = get_worker(db_conn, "worker-dev-abc123")
        assert worker["status"] == "running"
        assert worker["pid"] == 99999

    def test_update_worker_not_found(self, db_conn) -> None:
        """Test updating non-existent worker returns False."""
        result = update_worker(
            db_conn,
            "nonexistent-worker",
            status="stopped",
        )
        assert result is False

    def test_update_worker_with_datetime(self, db_conn) -> None:
        """Test updating worker with datetime values."""
        insert_worker(
            db_conn,
            worker_id="worker-dev-abc123",
            role="dev",
            status="running",
            project_path="/tmp/project",
        )

        stopped_time = datetime.now()
        update_worker(
            db_conn,
            "worker-dev-abc123",
            status="stopped",
            stopped_at=stopped_time,
        )

        worker = get_worker(db_conn, "worker-dev-abc123")
        assert worker["stopped_at"] == stopped_time.isoformat()

    def test_get_worker_not_found(self, db_conn) -> None:
        """Test get_worker returns None for non-existent worker."""
        worker = get_worker(db_conn, "nonexistent-worker")
        assert worker is None

    def test_list_workers_all(self, db_conn) -> None:
        """Test listing all workers."""
        insert_worker(db_conn, "worker-1", "dev", "running", "/tmp/p1")
        insert_worker(db_conn, "worker-2", "qa", "stopped", "/tmp/p2")

        workers = list_workers(db_conn)
        assert len(workers) == 2

    def test_list_workers_by_status(self, db_conn) -> None:
        """Test filtering workers by status."""
        insert_worker(db_conn, "worker-1", "dev", "running", "/tmp/p1")
        insert_worker(db_conn, "worker-2", "qa", "stopped", "/tmp/p2")
        insert_worker(db_conn, "worker-3", "dev", "running", "/tmp/p3")

        running = list_workers(db_conn, status="running")
        assert len(running) == 2

        stopped = list_workers(db_conn, status="stopped")
        assert len(stopped) == 1

    def test_list_workers_by_role(self, db_conn) -> None:
        """Test filtering workers by role."""
        insert_worker(db_conn, "worker-1", "dev", "running", "/tmp/p1")
        insert_worker(db_conn, "worker-2", "qa", "running", "/tmp/p2")

        devs = list_workers(db_conn, role="dev")
        assert len(devs) == 1
        assert devs[0]["role"] == "dev"

    def test_list_workers_by_project(self, db_conn) -> None:
        """Test filtering workers by project path."""
        insert_worker(db_conn, "worker-1", "dev", "running", "/tmp/p1")
        insert_worker(db_conn, "worker-2", "qa", "running", "/tmp/p2")

        workers = list_workers(db_conn, project_path="/tmp/p1")
        assert len(workers) == 1
        assert workers[0]["project_path"] == "/tmp/p1"

    def test_delete_worker(self, db_conn) -> None:
        """Test deleting a worker."""
        insert_worker(db_conn, "worker-1", "dev", "running", "/tmp/p1")

        result = delete_worker(db_conn, "worker-1")
        assert result is True

        worker = get_worker(db_conn, "worker-1")
        assert worker is None

    def test_delete_worker_not_found(self, db_conn) -> None:
        """Test deleting non-existent worker returns False."""
        result = delete_worker(db_conn, "nonexistent-worker")
        assert result is False

    def test_delete_worker_cascades_events(self, db_conn) -> None:
        """Test that deleting worker also deletes its events."""
        insert_worker(db_conn, "worker-1", "dev", "running", "/tmp/p1")
        insert_event(db_conn, "worker-1", "spawn", message="Worker started")
        insert_event(db_conn, "worker-1", "claim", bead_id="bead-123")

        events_before = list_events(db_conn, worker_id="worker-1")
        assert len(events_before) == 2

        delete_worker(db_conn, "worker-1")

        events_after = list_events(db_conn, worker_id="worker-1")
        assert len(events_after) == 0


class TestEventCrud:
    """Tests for worker event CRUD operations."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path):
        """Create a test database connection with a worker."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        insert_worker(conn, "worker-1", "dev", "running", "/tmp/p1")
        yield conn
        conn.close()

    def test_insert_event(self, db_conn) -> None:
        """Test inserting an event."""
        event_id = insert_event(
            db_conn,
            worker_id="worker-1",
            event_type="spawn",
            message="Worker spawned successfully",
        )

        assert event_id > 0

        events = list_events(db_conn, worker_id="worker-1")
        assert len(events) == 1
        assert events[0]["event_type"] == "spawn"
        assert events[0]["message"] == "Worker spawned successfully"

    def test_insert_event_with_bead_id(self, db_conn) -> None:
        """Test inserting event with bead reference."""
        insert_event(
            db_conn,
            worker_id="worker-1",
            event_type="claim",
            bead_id="multi_agent_beads-abc123",
            message="Claimed bead",
        )

        events = list_events(db_conn, bead_id="multi_agent_beads-abc123")
        assert len(events) == 1
        assert events[0]["bead_id"] == "multi_agent_beads-abc123"

    def test_insert_event_defaults_timestamp(self, db_conn) -> None:
        """Test that timestamp defaults to now."""
        before = datetime.now()

        insert_event(
            db_conn,
            worker_id="worker-1",
            event_type="spawn",
        )

        events = list_events(db_conn, worker_id="worker-1")
        timestamp = datetime.fromisoformat(events[0]["timestamp"])
        assert timestamp >= before

    def test_list_events_by_type(self, db_conn) -> None:
        """Test filtering events by type."""
        insert_event(db_conn, "worker-1", "spawn")
        insert_event(db_conn, "worker-1", "claim")
        insert_event(db_conn, "worker-1", "close")

        claims = list_events(db_conn, event_type="claim")
        assert len(claims) == 1
        assert claims[0]["event_type"] == "claim"

    def test_list_events_with_limit(self, db_conn) -> None:
        """Test limiting event results."""
        for i in range(10):
            insert_event(db_conn, "worker-1", f"event-{i}")

        events = list_events(db_conn, limit=5)
        assert len(events) == 5

    def test_list_events_ordered_by_timestamp(self, db_conn) -> None:
        """Test that events are ordered by timestamp descending."""
        insert_event(
            db_conn,
            "worker-1",
            "first",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
        )
        insert_event(
            db_conn,
            "worker-1",
            "second",
            timestamp=datetime(2024, 1, 1, 11, 0, 0),
        )
        insert_event(
            db_conn,
            "worker-1",
            "third",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
        )

        events = list_events(db_conn, worker_id="worker-1")

        # Most recent first
        assert events[0]["event_type"] == "third"
        assert events[1]["event_type"] == "second"
        assert events[2]["event_type"] == "first"
