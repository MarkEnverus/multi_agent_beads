"""Configuration for the Multi-Agent Dashboard."""

import logging
import os
import sys
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Beads configuration
BEADS_DIR = PROJECT_ROOT / ".beads"

# Log file paths
LOG_FILE = PROJECT_ROOT / "claude.log"  # Agent session log (read by dashboard)
DASHBOARD_LOG_FILE = PROJECT_ROOT / "dashboard.log"  # Dashboard application log

# Dashboard directories
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"

# Server configuration
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "8000"))
TOWN_NAME = os.environ.get("DASHBOARD_TOWN", "default")

# CORS allowed origins (localhost only for development)
# Dynamically include configured port plus common development ports
CORS_ORIGINS = [
    f"http://localhost:{PORT}",
    f"http://127.0.0.1:{PORT}",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
# Add default port range for multi-town support (8000-8010)
for p in range(8000, 8011):
    if f"http://localhost:{p}" not in CORS_ORIGINS:
        CORS_ORIGINS.append(f"http://localhost:{p}")
    if f"http://127.0.0.1:{p}" not in CORS_ORIGINS:
        CORS_ORIGINS.append(f"http://127.0.0.1:{p}")

# Logging configuration
# Log level can be set via DASHBOARD_LOG_LEVEL environment variable
# Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL_STR = os.environ.get("DASHBOARD_LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)

# Performance configuration
# Cache TTL in seconds for bd list results (reduces subprocess overhead)
CACHE_TTL_SECONDS = float(os.environ.get("DASHBOARD_CACHE_TTL", "5.0"))

# Log format for file and console handlers
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> logging.Logger:
    """Configure logging for the dashboard application.

    Sets up both console and file handlers with appropriate formatters.
    The log level is configurable via DASHBOARD_LOG_LEVEL environment variable.

    Returns:
        The root logger for the dashboard package.
    """
    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Get the dashboard package logger
    logger = logging.getLogger("dashboard")
    logger.setLevel(LOG_LEVEL)

    # Avoid duplicate handlers if setup is called multiple times
    if logger.handlers:
        return logger

    # Console handler - logs to stderr
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(LOG_LEVEL)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler - logs to dashboard.log
    try:
        file_handler = logging.FileHandler(DASHBOARD_LOG_FILE, encoding="utf-8")
        file_handler.setLevel(LOG_LEVEL)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except (OSError, PermissionError) as e:
        # If file logging fails, log warning to console and continue
        logger.warning("Could not set up file logging to %s: %s", DASHBOARD_LOG_FILE, e)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    return logger
