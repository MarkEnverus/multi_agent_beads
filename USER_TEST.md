# Testing the Multi-Agent Beads System

## Prerequisites

```bash
# Ensure you're in the project directory
cd /Users/mark.johnson/Desktop/source/repos/mark.johnson/multi_agent_beads

# Install dependencies
uv sync
```

---

## Option 1: Web UI Testing (Recommended)

### 1. Start the Dashboard

```bash
uv run python -m dashboard.app
```

### 2. Open Admin Page

Navigate to: **http://localhost:8000/admin**

### 3. Test Worker Management

| Action | How |
|--------|-----|
| **Spawn Worker** | Select role from dropdown, enter project path, click "Spawn" |
| **Stop Worker** | Click stop button (square icon) on worker row |
| **Restart Worker** | Click restart button (circular arrows) on worker row |
| **View Logs** | Click logs button (document icon) to open live log stream |

### 4. Verify

- Daemon status shows "RUNNING" (green)
- Workers appear in the list after spawning
- Logs stream in real-time

---

## Option 2: CLI Testing

### 1. Start the Daemon

```bash
uv run mab start --daemon
```

### 2. Check Status

```bash
uv run mab status
```

Expected output:
```
MAB Status
========================================
Daemon: RUNNING
PID: <number>
Uptime: <time>
Workers: 0 running
```

### 3. Initialize Project (if not done)

```bash
uv run mab init
```

### 4. Spawn a Worker

```bash
uv run mab spawn --role developer
```

### 5. List Workers

```bash
uv run mab list
```

### 6. View Logs

```bash
uv run mab logs
```

### 7. Stop Everything

```bash
uv run mab stop --all
```

---

## Option 3: Run Automated Tests

```bash
# All mab-related tests
uv run pytest tests/test_mab_cli.py tests/test_daemon.py tests/test_towns.py tests/test_rpc.py -v

# Quick summary
uv run pytest tests/test_mab_cli.py tests/test_daemon.py tests/test_towns.py tests/test_rpc.py -q
```

---

## Troubleshooting

### Daemon won't start

```bash
# Check if already running
uv run mab status

# Force stop if stuck
uv run mab stop --all --force

# Check for stale PID file
rm -f ~/.mab/daemon.pid
```

### Workers not spawning

1. Ensure daemon is running: `uv run mab status`
2. Check daemon logs: `cat ~/.mab/daemon.log`
3. Verify project has `.beads/` directory

### Web UI not loading

1. Check dashboard is running on correct port
2. Try: `curl http://localhost:8000/admin`
3. Check for errors in terminal running dashboard

---

## Architecture Overview

```
~/.mab/                      # Global daemon (one per user)
├── daemon.pid               # Process ID
├── daemon.lock              # Single-instance lock
├── mab.sock                 # RPC socket
├── workers.db               # Worker state (SQLite)
└── daemon.log               # Daemon logs

<project>/.mab/              # Per-project config
├── config.yaml              # Town-specific settings
└── logs/                    # Worker logs for this project
```

The daemon manages all workers across all projects. Each project can have its own dashboard on a different port.
