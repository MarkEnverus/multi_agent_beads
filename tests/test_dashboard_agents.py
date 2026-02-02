"""Tests for the dashboard agents API endpoints.

Tests the new database-backed implementation that reads from workers.db
instead of parsing claude.log.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import app
from dashboard.routes.agents import (
    ROLE_MAP,
    VALID_ROLES,
    _extract_instance_from_worker_id,
    _format_db_timestamp,
    _get_active_agents,
    _get_workers_from_db,
    _map_db_status_to_api,
)

client = TestClient(app)


def create_test_db(db_path: Path) -> sqlite3.Connection:
    """Create a test database with the workers.db schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Create workers table (workers.db schema from WorkerDatabase)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            project_path TEXT NOT NULL,
            status TEXT NOT NULL,
            pid INTEGER,
            created_at TEXT NOT NULL,
            started_at TEXT,
            stopped_at TEXT,
            crash_count INTEGER DEFAULT 0,
            last_heartbeat TEXT,
            exit_code INTEGER,
            error_message TEXT,
            town_name TEXT DEFAULT 'default',
            worktree_path TEXT,
            worktree_branch TEXT,
            last_restart_at TEXT,
            auto_restart_enabled INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    return conn


def insert_test_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    role: str = "dev",
    status: str = "running",
    pid: int = 1000,
    started_at: datetime | None = None,
    stopped_at: datetime | None = None,
    project_path: str = "/test/project",
) -> None:
    """Insert a test worker record."""
    now = datetime.now()
    if started_at is None:
        started_at = now

    conn.execute(
        """
        INSERT INTO workers (id, role, project_path, status, pid, created_at, started_at, stopped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            worker_id,
            role,
            project_path,
            status,
            pid,
            now.isoformat(),
            started_at.isoformat(),
            stopped_at.isoformat() if stopped_at else None,
        ),
    )
    conn.commit()


def insert_test_event(
    conn: sqlite3.Connection,
    worker_id: str,
    event_type: str,
    bead_id: str | None = None,
    message: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Insert a test worker event.

    Note: workers.db doesn't have worker_events table, so this is a no-op.
    The worker_events table only exists in mab.db (legacy).
    """
    pass  # workers.db doesn't track events


class TestAgentsEndpoints:
    """Tests for /api/agents endpoints."""

    def test_list_agents_no_database(self, tmp_path: Path) -> None:
        """Test listing agents when database doesn't exist."""
        # Patch both PROJECT_ROOT and Path.home to prevent fallback to global db
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with (
            patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path),
            patch("dashboard.routes.agents.Path.home", return_value=fake_home),
        ):
            response = client.get("/api/agents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_agents_empty_database(self, tmp_path: Path) -> None:
        """Test listing agents with empty database."""
        db_path = tmp_path / ".mab" / "workers.db"
        create_test_db(db_path)

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            # workers.db exists in tmp_path, so no fallback triggered
            response = client.get("/api/agents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_agents_with_running_workers(self, tmp_path: Path) -> None:
        """Test listing agents with running workers."""
        db_path = tmp_path / ".mab" / "workers.db"
        conn = create_test_db(db_path)

        # Insert running worker
        insert_test_worker(conn, "worker-dev-abc123", role="dev", status="running", pid=1001, project_path=str(tmp_path))
        # Note: workers.db doesn't track bead claims (no worker_events table)

        conn.close()

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            # workers.db exists in tmp_path so no fallback triggered
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["pid"] == 1001
            assert data[0]["role"] == "developer"
            # workers.db doesn't track bead claims, so current_bead is None
            assert data[0]["current_bead"] is None
            assert data[0]["current_bead_title"] is None
            # Without a current bead, status is "idle" not "working"
            assert data[0]["status"] == "idle"

    def test_list_agents_excludes_old_stopped_workers(self, tmp_path: Path) -> None:
        """Test that workers stopped more than 1 hour ago are excluded."""
        db_path = tmp_path / ".mab" / "workers.db"
        conn = create_test_db(db_path)

        # Worker stopped 2 hours ago - should be excluded
        old_stop = datetime.now() - timedelta(hours=2)
        insert_test_worker(
            conn,
            "worker-old",
            status="stopped",
            started_at=datetime.now() - timedelta(hours=3),
            stopped_at=old_stop,
            project_path=str(tmp_path),
        )

        # Worker stopped 30 minutes ago - should be included
        recent_stop = datetime.now() - timedelta(minutes=30)
        insert_test_worker(
            conn,
            "worker-recent",
            status="stopped",
            started_at=datetime.now() - timedelta(hours=1),
            stopped_at=recent_stop,
            project_path=str(tmp_path),
        )

        conn.close()

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            # Only recent worker should be included
            assert len(data) == 1
            assert "worker-recent" in data[0]["worker_id"]

    def test_list_agents_includes_spawning_workers(self, tmp_path: Path) -> None:
        """Test that spawning workers are included."""
        db_path = tmp_path / ".mab" / "workers.db"
        conn = create_test_db(db_path)

        insert_test_worker(conn, "worker-spawning", status="spawning", project_path=str(tmp_path))
        conn.close()

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            response = client.get("/api/agents")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["status"] == "idle"

    def test_list_agents_by_role(self, tmp_path: Path) -> None:
        """Test filtering agents by role."""
        db_path = tmp_path / ".mab" / "workers.db"
        conn = create_test_db(db_path)

        insert_test_worker(conn, "worker-dev-1", role="dev", status="running", project_path=str(tmp_path))
        insert_test_worker(conn, "worker-qa-1", role="qa", status="running", project_path=str(tmp_path))
        conn.close()

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            # Filter by developer
            response = client.get("/api/agents/developer")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "developer"

            # Filter by qa
            response = client.get("/api/agents/qa")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["role"] == "qa"

    def test_list_agents_invalid_role(self) -> None:
        """Test that invalid role returns 400 error."""
        response = client.get("/api/agents/invalid_role")
        assert response.status_code == 400
        assert "Invalid role" in response.json()["detail"]


class TestDatabaseFunctions:
    """Tests for database query functions."""

    def test_get_workers_from_db_empty(self, tmp_path: Path) -> None:
        """Test getting workers when DB is empty."""
        db_path = tmp_path / ".mab" / "workers.db"
        create_test_db(db_path)

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            workers = _get_workers_from_db()
            assert workers == []

    def test_get_workers_from_db_with_workers(self, tmp_path: Path) -> None:
        """Test getting workers from database."""
        db_path = tmp_path / ".mab" / "workers.db"
        conn = create_test_db(db_path)

        insert_test_worker(conn, "worker-1", status="running", pid=1001, project_path=str(tmp_path))
        insert_test_worker(conn, "worker-2", status="spawning", pid=1002, project_path=str(tmp_path))
        conn.close()

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            workers = _get_workers_from_db()
            assert len(workers) == 2

    def test_get_workers_from_db_no_database(self, tmp_path: Path) -> None:
        """Test getting workers when database doesn't exist."""
        # Patch both PROJECT_ROOT and Path.home to prevent fallback to global db
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with (
            patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path),
            patch("dashboard.routes.agents.Path.home", return_value=fake_home),
        ):
            workers = _get_workers_from_db()
            assert workers == []


class TestStatusMapping:
    """Tests for status mapping functions."""

    def test_map_db_status_running_with_bead(self) -> None:
        """Test running status with current bead -> working."""
        assert _map_db_status_to_api("running", "mab-task") == "working"

    def test_map_db_status_running_no_bead(self) -> None:
        """Test running status without bead -> idle."""
        assert _map_db_status_to_api("running", None) == "idle"

    def test_map_db_status_spawning(self) -> None:
        """Test spawning status -> idle."""
        assert _map_db_status_to_api("spawning", None) == "idle"
        assert _map_db_status_to_api("spawning", "mab-task") == "idle"

    def test_map_db_status_stopped(self) -> None:
        """Test stopped status -> ended."""
        assert _map_db_status_to_api("stopped", None) == "ended"
        assert _map_db_status_to_api("stopped", "mab-task") == "ended"

    def test_map_db_status_crashed(self) -> None:
        """Test crashed status -> ended."""
        assert _map_db_status_to_api("crashed", None) == "ended"

    def test_map_db_status_failed(self) -> None:
        """Test failed status -> ended."""
        assert _map_db_status_to_api("failed", None) == "ended"
        assert _map_db_status_to_api("failed", "mab-task") == "ended"

    def test_map_db_status_stopping(self) -> None:
        """Test stopping status -> ended."""
        assert _map_db_status_to_api("stopping", None) == "ended"
        assert _map_db_status_to_api("stopping", "mab-task") == "ended"


class TestRoleMapping:
    """Tests for role mapping."""

    def test_role_map_values(self) -> None:
        """Test that role map contains expected mappings."""
        assert ROLE_MAP["dev"] == "developer"
        assert ROLE_MAP["qa"] == "qa"
        assert ROLE_MAP["tech_lead"] == "tech_lead"
        assert ROLE_MAP["manager"] == "manager"
        assert ROLE_MAP["reviewer"] == "reviewer"

    def test_valid_roles(self) -> None:
        """Test valid roles set."""
        expected_roles = {"developer", "qa", "reviewer", "tech_lead", "manager", "unknown"}
        assert VALID_ROLES == expected_roles


class TestInstanceExtraction:
    """Tests for extracting instance numbers from worker IDs."""

    def test_extract_instance_with_number(self) -> None:
        """Test extracting instance from ID with number."""
        assert _extract_instance_from_worker_id("worker-dev-1-abc123") == 1
        assert _extract_instance_from_worker_id("worker-qa-5-xyz") == 5

    def test_extract_instance_no_number(self) -> None:
        """Test default instance when no number in ID."""
        assert _extract_instance_from_worker_id("worker-dev-abc123") == 1
        assert _extract_instance_from_worker_id("some-worker-id") == 1


class TestTimestampFormatting:
    """Tests for timestamp formatting."""

    def test_format_iso_timestamp(self) -> None:
        """Test formatting ISO timestamp."""
        assert _format_db_timestamp("2026-01-24T14:30:00") == "2026-01-24T14:30:00Z"
        assert _format_db_timestamp("2026-01-24T14:30:00Z") == "2026-01-24T14:30:00Z"

    def test_format_space_separated_timestamp(self) -> None:
        """Test formatting space-separated timestamp."""
        assert _format_db_timestamp("2026-01-24 14:30:00") == "2026-01-24T14:30:00Z"

    def test_format_empty_timestamp(self) -> None:
        """Test formatting empty/None timestamp."""
        assert _format_db_timestamp(None) == ""
        assert _format_db_timestamp("") == ""


class TestGetActiveAgents:
    """Integration tests for _get_active_agents function."""

    def test_get_active_agents_full_workflow(self, tmp_path: Path) -> None:
        """Test full workflow of getting active agents from DB."""
        db_path = tmp_path / ".mab" / "workers.db"
        conn = create_test_db(db_path)

        # Create workers with various states
        insert_test_worker(conn, "worker-dev-1-abc", role="dev", status="running", pid=1001, project_path=str(tmp_path))
        # Note: workers.db doesn't track bead claims (no worker_events table)

        insert_test_worker(conn, "worker-qa-2-xyz", role="qa", status="spawning", pid=1002, project_path=str(tmp_path))

        # Stopped recently - should be included
        insert_test_worker(
            conn,
            "worker-dev-3-def",
            role="dev",
            status="stopped",
            pid=1003,
            stopped_at=datetime.now() - timedelta(minutes=30),
            project_path=str(tmp_path),
        )

        conn.close()

        with patch("dashboard.routes.agents.PROJECT_ROOT", tmp_path):
            agents = _get_active_agents()

            assert len(agents) == 3

            # Find specific agents
            dev1 = next(a for a in agents if "dev-1" in a["worker_id"])
            # workers.db doesn't track bead claims, so status is "idle" not "working"
            assert dev1["status"] == "idle"
            assert dev1["current_bead"] is None
            assert dev1["role"] == "developer"

            qa = next(a for a in agents if "qa-2" in a["worker_id"])
            assert qa["status"] == "idle"
            assert qa["role"] == "qa"

            stopped = next(a for a in agents if "dev-3" in a["worker_id"])
            assert stopped["status"] == "ended"
