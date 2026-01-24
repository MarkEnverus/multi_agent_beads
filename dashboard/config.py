"""Configuration for the Multi-Agent Dashboard."""

from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Beads configuration
BEADS_DIR = PROJECT_ROOT / ".beads"

# Log file path
LOG_FILE = PROJECT_ROOT / "claude.log"

# Dashboard directories
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"

# Server configuration
HOST = "127.0.0.1"
PORT = 8000

# CORS allowed origins (localhost only for development)
CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
