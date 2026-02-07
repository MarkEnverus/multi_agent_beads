"""Tests for the dashboard towns API routes.

This module tests the /api/towns endpoints including the current town
endpoint that clearly indicates when no town is configured.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


class TestCurrentTownEndpoint:
    """Tests for /api/towns/current endpoint."""

    def test_current_town_exists_returns_exists_true(self) -> None:
        """Test that existing town returns exists: true with full details."""
        mock_town = MagicMock()
        mock_town.to_dict.return_value = {
            "name": "default",
            "template": "pair",
            "port": 8000,
        }
        mock_town.template = "pair"
        mock_town.workflow = ["dev", "qa", "human_merge"]
        mock_town.get_effective_roles.return_value = {"dev": 1, "qa": 1}

        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_town

        with patch("dashboard.routes.towns._get_town_manager", return_value=mock_manager):
            with patch("dashboard.routes.towns._get_active_worker_counts", return_value={}):
                with patch("dashboard.routes.towns.TOWN_NAME", "default"):
                    response = client.get("/api/towns/current")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert data["name"] == "default"
        assert data["template"] == "pair"
        assert data["workflow"] == ["dev", "qa", "human_merge"]
        assert data["worker_counts"] == {"dev": 1, "qa": 1}

    def test_current_town_not_found_returns_exists_false(self) -> None:
        """Test that missing town returns exists: false with helpful message."""
        # Import the exception from the actual module
        from mab.towns import TownNotFoundError

        mock_manager = MagicMock()
        mock_manager.get.side_effect = TownNotFoundError("default")

        with patch("dashboard.routes.towns._get_town_manager", return_value=mock_manager):
            with patch("dashboard.routes.towns._get_active_worker_counts", return_value={}):
                response = client.get("/api/towns/current")

        assert response.status_code == 200
        data = response.json()

        # Key assertions: clearly indicates town doesn't exist
        assert data["exists"] is False
        assert data["town"] is None
        assert data["template"] is None
        assert data["workflow"] is None
        assert data["worker_counts"] == {}

        # Message should tell user how to create the town
        assert "does not exist" in data["message"]
        assert "mab town create" in data["message"]

    def test_current_town_not_found_does_not_fake_defaults(self) -> None:
        """Test that missing town doesn't pretend to have pair template defaults."""
        from mab.towns import TownNotFoundError

        mock_manager = MagicMock()
        mock_manager.get.side_effect = TownNotFoundError("mytown")

        with patch("dashboard.routes.towns._get_town_manager", return_value=mock_manager):
            with patch("dashboard.routes.towns._get_active_worker_counts", return_value={}):
                with patch("dashboard.routes.towns.TOWN_NAME", "mytown"):
                    response = client.get("/api/towns/current")

        data = response.json()

        # Should NOT fake pair template defaults
        assert data["template"] != "pair"
        assert data["worker_counts"] != {"dev": 1, "qa": 1}

        # Should clearly show no configuration
        assert data["template"] is None
        assert data["worker_counts"] == {}

    def test_current_town_preserves_active_workers_even_without_town(self) -> None:
        """Test that active workers are still reported even if town doesn't exist."""
        from mab.towns import TownNotFoundError

        mock_manager = MagicMock()
        mock_manager.get.side_effect = TownNotFoundError("default")

        # Simulate orphaned workers running without a configured town
        active_workers = {"dev": 1}

        with patch("dashboard.routes.towns._get_town_manager", return_value=mock_manager):
            with patch(
                "dashboard.routes.towns._get_active_worker_counts", return_value=active_workers
            ):
                response = client.get("/api/towns/current")

        data = response.json()
        assert data["exists"] is False
        assert data["active_workers"] == {"dev": 1}


class TestListTownsEndpoint:
    """Tests for /api/towns endpoint."""

    def test_list_towns_returns_count(self) -> None:
        """Test that list towns includes count."""
        mock_manager = MagicMock()
        mock_manager.list_towns.return_value = []

        with patch("dashboard.routes.towns._get_town_manager", return_value=mock_manager):
            response = client.get("/api/towns")

        assert response.status_code == 200
        data = response.json()
        assert "towns" in data
        assert "count" in data
        assert data["count"] == 0

    def test_list_towns_includes_current_town(self) -> None:
        """Test that list towns indicates which is current."""
        mock_manager = MagicMock()
        mock_manager.list_towns.return_value = []

        with patch("dashboard.routes.towns._get_town_manager", return_value=mock_manager):
            with patch("dashboard.routes.towns.TOWN_NAME", "mytown"):
                response = client.get("/api/towns")

        data = response.json()
        assert data["current_town"] == "mytown"
