# Multi-Agent Beads Installation Guide

This guide covers various ways to install and set up Multi-Agent Beads (MAB).

## Quick Install (Recommended)

The fastest way to get started is using `uv tool install`:

```bash
# Install globally with uv from GitHub
uv tool install git+https://github.com/USER/multi_agent_beads.git

# Or from a local clone
git clone https://github.com/USER/multi_agent_beads.git
cd multi_agent_beads
uv tool install .
```

After installation, verify it works:

```bash
mab --version
mab --help
mab dashboard --help
```

## pip Install

For users without `uv`, standard pip installation works:

```bash
# Install from GitHub
pip install git+https://github.com/USER/multi_agent_beads.git

# Or install from local clone in editable mode
git clone https://github.com/USER/multi_agent_beads.git
cd multi_agent_beads
pip install -e .
```

Verify installation:

```bash
mab --version
```

## Development Install

For contributing or local development:

```bash
# Clone the repository
git clone https://github.com/USER/multi_agent_beads.git
cd multi_agent_beads

# Install dependencies with uv
uv sync

# Run commands with uv run prefix
uv run mab --help
uv run mab dashboard

# Or activate the virtual environment
source .venv/bin/activate
mab --help
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=mab --cov=dashboard

# Run specific test file
uv run pytest tests/test_cli.py
```

### Linting and Type Checking

```bash
# Lint with ruff
uv run ruff check .

# Type check with mypy
uv run mypy mab/ dashboard/ --ignore-missing-imports
```

## Verifying Installation

After installing, run these commands to verify everything works:

```bash
# Check version
mab --version

# View help
mab --help

# Initialize a project (in a git repo)
cd /path/to/your/project
mab init

# Start the dashboard
mab dashboard

# Check dashboard status
mab dashboard --status
```

## Shell Completion Setup

MAB uses Click, which supports shell completion for bash, zsh, and fish.

### Bash

Add to `~/.bashrc`:

```bash
eval "$(_MAB_COMPLETE=bash_source mab)"
```

### Zsh

Add to `~/.zshrc`:

```bash
eval "$(_MAB_COMPLETE=zsh_source mab)"
```

### Fish

Add to `~/.config/fish/completions/mab.fish`:

```fish
_MAB_COMPLETE=fish_source mab | source
```

After adding the completion script, restart your shell or source the config file.

## Configuration

MAB stores configuration in two locations:

### Global Configuration

Global settings and daemon state are stored in `~/.mab/`:

```
~/.mab/
├── daemon.pid          # Daemon process ID
├── daemon.log          # Daemon log file
├── mab.sock            # Unix socket for RPC
├── dashboards.json     # Running dashboard registry
└── logs/               # Dashboard logs
    └── dashboard-*.log
```

### Project Configuration

Per-project settings are stored in `<project>/.mab/`:

```
<project>/.mab/
├── config.yaml         # Project settings (tracked in git)
├── logs/               # Local logs (not tracked)
└── heartbeat/          # Worker heartbeats
```

Initialize a project with:

```bash
cd /path/to/your/project
mab init
```

## Using the Dashboard

The dashboard provides a web interface for monitoring your agents and beads.

### Starting the Dashboard

```bash
# Start for current project (auto-assigns port)
mab dashboard

# Start on a specific port
mab dashboard --port 8001

# Start in background (default)
mab dashboard

# Open in browser
open http://127.0.0.1:8000
```

### Managing Dashboards

```bash
# Check running dashboards
mab dashboard --status

# Stop dashboard for current project
mab dashboard --stop
```

### Multi-Project Support

Each project gets its own dashboard on a unique port:

```bash
# Project A gets port 8000
cd /path/to/project-a
mab dashboard

# Project B gets port 8001
cd /path/to/project-b
mab dashboard

# See all running dashboards
mab dashboard --status
```

## Uninstall

### uv tool uninstall

```bash
uv tool uninstall multi-agent-beads
```

### pip uninstall

```bash
pip uninstall multi-agent-beads
```

### Clean up configuration

```bash
# Remove global config (optional)
rm -rf ~/.mab

# Remove project config (in each project)
rm -rf .mab
```

## Troubleshooting

### Dashboard won't start

1. Check if another process is using the port:
   ```bash
   lsof -i :8000
   ```

2. Check the dashboard log:
   ```bash
   cat ~/.mab/logs/dashboard-*.log
   ```

3. Try a different port:
   ```bash
   mab dashboard --port 8001
   ```

### Command not found

If `mab` command is not found after installation:

1. For `uv tool install`: Ensure `~/.local/bin` is in your PATH
2. For pip: Ensure pip's script directory is in your PATH
3. For development: Use `uv run mab` or activate the virtualenv

### Daemon not responding

```bash
# Check daemon status
mab status

# Restart daemon
mab restart

# Or stop and start
mab stop --all
mab start -d
```

## Requirements

- Python 3.11 or later
- Git (for version control integration)
- A modern web browser (for dashboard)

### Optional

- `uv` (recommended for fast installs)
- `gh` CLI (for GitHub integration)
- `bd` CLI (for beads integration)
