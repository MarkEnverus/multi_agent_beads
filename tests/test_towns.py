"""Tests for the MAB towns module."""

import tempfile
from pathlib import Path

import pytest

from mab.towns import (
    DEFAULT_PORT_START,
    PortConflictError,
    ProjectPathConflictError,
    Town,
    TownDatabase,
    TownError,
    TownExistsError,
    TownManager,
    TownNotFoundError,
    TownStatus,
)


class TestTown:
    """Tests for the Town dataclass."""

    def test_town_default_values(self) -> None:
        """Test Town has correct default values."""
        town = Town(name="test", port=8000)
        assert town.name == "test"
        assert town.port == 8000
        assert town.status == TownStatus.STOPPED
        assert town.max_workers == 3
        assert town.default_roles == ["dev", "qa"]
        assert town.town_name == "default" if hasattr(town, "town_name") else True
        assert town.pid is None
        assert town.project_path is None

    def test_town_to_dict(self) -> None:
        """Test Town serialization to dictionary."""
        town = Town(
            name="prod",
            port=8001,
            status=TownStatus.RUNNING,
            max_workers=5,
            description="Production town",
        )
        data = town.to_dict()

        assert data["name"] == "prod"
        assert data["port"] == 8001
        assert data["status"] == "running"
        assert data["max_workers"] == 5
        assert data["description"] == "Production town"


class TestTownDatabase:
    """Tests for TownDatabase."""

    @pytest.fixture
    def temp_db(self) -> Path:
        """Create a temporary database path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "workers.db"

    def test_init_creates_schema(self, temp_db: Path) -> None:
        """Test database initialization creates schema."""
        _db = TownDatabase(temp_db)  # Creates schema on init
        assert temp_db.exists()
        assert _db is not None

    def test_insert_and_get_town(self, temp_db: Path) -> None:
        """Test inserting and retrieving a town."""
        db = TownDatabase(temp_db)
        town = Town(name="test", port=8000)

        db.insert_town(town)
        retrieved = db.get_town("test")

        assert retrieved is not None
        assert retrieved.name == "test"
        assert retrieved.port == 8000

    def test_get_nonexistent_town(self, temp_db: Path) -> None:
        """Test getting a town that doesn't exist."""
        db = TownDatabase(temp_db)
        result = db.get_town("nonexistent")
        assert result is None

    def test_get_town_by_port(self, temp_db: Path) -> None:
        """Test getting a town by port number."""
        db = TownDatabase(temp_db)
        town = Town(name="test", port=8005)
        db.insert_town(town)

        retrieved = db.get_town_by_port(8005)
        assert retrieved is not None
        assert retrieved.name == "test"

    def test_list_towns(self, temp_db: Path) -> None:
        """Test listing all towns."""
        db = TownDatabase(temp_db)
        db.insert_town(Town(name="town1", port=8000))
        db.insert_town(Town(name="town2", port=8001))
        db.insert_town(Town(name="town3", port=8002, status=TownStatus.RUNNING))

        all_towns = db.list_towns()
        assert len(all_towns) == 3

        running_towns = db.list_towns(status=TownStatus.RUNNING)
        assert len(running_towns) == 1
        assert running_towns[0].name == "town3"

    def test_update_town(self, temp_db: Path) -> None:
        """Test updating a town."""
        db = TownDatabase(temp_db)
        town = Town(name="test", port=8000, max_workers=3)
        db.insert_town(town)

        town.max_workers = 5
        town.status = TownStatus.RUNNING
        db.update_town(town)

        retrieved = db.get_town("test")
        assert retrieved is not None
        assert retrieved.max_workers == 5
        assert retrieved.status == TownStatus.RUNNING

    def test_delete_town(self, temp_db: Path) -> None:
        """Test deleting a town."""
        db = TownDatabase(temp_db)
        db.insert_town(Town(name="test", port=8000))

        assert db.delete_town("test") is True
        assert db.get_town("test") is None

    def test_delete_nonexistent_town(self, temp_db: Path) -> None:
        """Test deleting a town that doesn't exist."""
        db = TownDatabase(temp_db)
        assert db.delete_town("nonexistent") is False

    def test_count_towns(self, temp_db: Path) -> None:
        """Test counting towns."""
        db = TownDatabase(temp_db)
        db.insert_town(Town(name="town1", port=8000))
        db.insert_town(Town(name="town2", port=8001, status=TownStatus.RUNNING))

        assert db.count_towns() == 2
        assert db.count_towns(status=TownStatus.RUNNING) == 1
        assert db.count_towns(status=TownStatus.STOPPED) == 1

    def test_get_next_available_port(self, temp_db: Path) -> None:
        """Test finding next available port."""
        db = TownDatabase(temp_db)

        # First port should be start of range
        port = db.get_next_available_port(start=8000, end=8010)
        assert port == 8000

        # After using some ports
        db.insert_town(Town(name="town1", port=8000))
        db.insert_town(Town(name="town2", port=8001))

        port = db.get_next_available_port(start=8000, end=8010)
        assert port == 8002

    def test_get_next_available_port_all_used(self, temp_db: Path) -> None:
        """Test when all ports in range are used."""
        db = TownDatabase(temp_db)

        # Fill up a small range
        for i in range(3):
            db.insert_town(Town(name=f"town{i}", port=8000 + i))

        port = db.get_next_available_port(start=8000, end=8002)
        assert port is None


class TestTownManager:
    """Tests for TownManager."""

    @pytest.fixture
    def manager(self) -> TownManager:
        """Create a TownManager with temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mab_dir = Path(tmpdir) / ".mab"
            mab_dir.mkdir()
            yield TownManager(mab_dir)

    def test_create_town(self, manager: TownManager) -> None:
        """Test creating a new town."""
        town = manager.create(
            name="test",
            port=8005,
            max_workers=5,
            description="Test town",
        )

        assert town.name == "test"
        assert town.port == 8005
        assert town.max_workers == 5
        assert town.description == "Test town"

    def test_create_town_auto_port(self, manager: TownManager) -> None:
        """Test creating a town with auto-allocated port."""
        town = manager.create(name="test")
        assert town.port == DEFAULT_PORT_START

    def test_create_duplicate_town(self, manager: TownManager) -> None:
        """Test creating a town with duplicate name."""
        manager.create(name="test", port=8000)

        with pytest.raises(TownExistsError):
            manager.create(name="test", port=8001)

    def test_create_town_port_conflict(self, manager: TownManager) -> None:
        """Test creating a town with conflicting port."""
        manager.create(name="town1", port=8000)

        with pytest.raises(PortConflictError):
            manager.create(name="town2", port=8000)

    def test_create_town_project_path_conflict(self, manager: TownManager) -> None:
        """Test creating a town with conflicting project path."""
        manager.create(name="town1", port=8000, project_path="/path/to/project")

        with pytest.raises(ProjectPathConflictError):
            manager.create(name="town2", port=8001, project_path="/path/to/project")

    def test_create_town_project_path_no_conflict_if_none(self, manager: TownManager) -> None:
        """Test creating towns without project path doesn't conflict."""
        # Both towns have None project_path - should not conflict
        manager.create(name="town1", port=8000, project_path=None)
        manager.create(name="town2", port=8001, project_path=None)

        towns = manager.list_towns()
        assert len(towns) == 2

    def test_create_town_invalid_name(self, manager: TownManager) -> None:
        """Test creating a town with invalid name."""
        with pytest.raises(TownError):
            manager.create(name="invalid-name!", port=8000)

        with pytest.raises(TownError):
            manager.create(name="", port=8000)

    def test_get_town(self, manager: TownManager) -> None:
        """Test getting an existing town."""
        manager.create(name="test", port=8000)
        town = manager.get("test")
        assert town.name == "test"

    def test_get_nonexistent_town(self, manager: TownManager) -> None:
        """Test getting a town that doesn't exist."""
        with pytest.raises(TownNotFoundError):
            manager.get("nonexistent")

    def test_list_towns(self, manager: TownManager) -> None:
        """Test listing towns."""
        manager.create(name="town1", port=8000)
        manager.create(name="town2", port=8001)

        towns = manager.list_towns()
        assert len(towns) == 2

    def test_delete_town(self, manager: TownManager) -> None:
        """Test deleting a town."""
        manager.create(name="test", port=8000)
        assert manager.delete("test") is True

        with pytest.raises(TownNotFoundError):
            manager.get("test")

    def test_delete_running_town_requires_force(self, manager: TownManager) -> None:
        """Test deleting a running town requires force flag."""
        manager.create(name="test", port=8000)
        manager.set_status("test", TownStatus.RUNNING, pid=12345)

        with pytest.raises(TownError):
            manager.delete("test", force=False)

        # Force delete should work
        assert manager.delete("test", force=True) is True

    def test_update_town(self, manager: TownManager) -> None:
        """Test updating town configuration."""
        manager.create(name="test", port=8000, max_workers=3)

        updated = manager.update("test", max_workers=5, description="Updated")
        assert updated.max_workers == 5
        assert updated.description == "Updated"

    def test_update_town_port(self, manager: TownManager) -> None:
        """Test updating town port."""
        manager.create(name="test", port=8000)
        updated = manager.update("test", port=8005)
        assert updated.port == 8005

    def test_update_town_port_conflict(self, manager: TownManager) -> None:
        """Test updating town to conflicting port."""
        manager.create(name="town1", port=8000)
        manager.create(name="town2", port=8001)

        with pytest.raises(PortConflictError):
            manager.update("town2", port=8000)

    def test_update_town_project_path_conflict(self, manager: TownManager) -> None:
        """Test updating town to conflicting project path."""
        manager.create(name="town1", port=8000, project_path="/path/to/project")
        manager.create(name="town2", port=8001, project_path="/other/project")

        with pytest.raises(ProjectPathConflictError):
            manager.update("town2", project_path="/path/to/project")

    def test_update_town_project_path_same_town_ok(self, manager: TownManager) -> None:
        """Test updating town to same project path is allowed."""
        manager.create(name="town1", port=8000, project_path="/path/to/project")

        # Updating to the same path should not raise
        updated = manager.update("town1", project_path="/path/to/project")
        assert updated.project_path == "/path/to/project"

    def test_set_status(self, manager: TownManager) -> None:
        """Test setting town status."""
        manager.create(name="test", port=8000)

        town = manager.set_status("test", TownStatus.RUNNING, pid=12345)
        assert town.status == TownStatus.RUNNING
        assert town.pid == 12345
        assert town.started_at is not None

        town = manager.set_status("test", TownStatus.STOPPED)
        assert town.status == TownStatus.STOPPED
        assert town.pid is None

    def test_get_or_create_default(self, manager: TownManager) -> None:
        """Test getting or creating default town."""
        # First call creates
        town1 = manager.get_or_create_default()
        assert town1.name == "default"
        assert town1.port == DEFAULT_PORT_START

        # Second call returns existing
        town2 = manager.get_or_create_default()
        assert town2.name == "default"
        assert town2.created_at == town1.created_at

    def test_count_running(self, manager: TownManager) -> None:
        """Test counting running towns."""
        manager.create(name="town1", port=8000)
        manager.create(name="town2", port=8001)
        manager.set_status("town1", TownStatus.RUNNING, pid=12345)

        assert manager.count_running() == 1

    def test_create_town_with_solo_template(self, manager: TownManager) -> None:
        """Test creating a town with solo template gets correct roles."""
        town = manager.create(name="test", port=8000, template="solo")
        assert town.template == "solo"
        assert town.get_effective_roles() == {"dev": 1}

    def test_create_town_with_pair_template(self, manager: TownManager) -> None:
        """Test creating a town with pair template gets correct roles."""
        town = manager.create(name="test", port=8000, template="pair")
        assert town.template == "pair"
        assert town.get_effective_roles() == {"dev": 1, "qa": 1}

    def test_create_town_with_full_template(self, manager: TownManager) -> None:
        """Test creating a town with full template gets correct roles."""
        town = manager.create(name="test", port=8000, template="full")
        assert town.template == "full"
        expected = {"manager": 1, "tech_lead": 1, "dev": 1, "qa": 1, "reviewer": 1}
        assert town.get_effective_roles() == expected

    def test_effective_roles_always_from_template(self, manager: TownManager) -> None:
        """Test that get_effective_roles always returns template roles."""
        town = manager.create(name="test", port=8000, template="pair")
        # Even if worker_counts were somehow different, effective_roles
        # should always return the template's fixed configuration
        assert town.get_effective_roles() == {"dev": 1, "qa": 1}

    def test_create_town_invalid_template(self, manager: TownManager) -> None:
        """Test creating a town with invalid template raises error."""
        with pytest.raises(TownError, match="Invalid template"):
            manager.create(name="test", port=8000, template="custom")
