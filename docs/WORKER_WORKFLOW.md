# Worker Workflow Guide

This guide documents how workers pick up and process beads in the Multi-Agent Beads system.

## Overview

The MAB system uses a daemon-worker architecture where:

1. The **daemon** manages worker lifecycle (spawning, monitoring, restarting)
2. **Workers** are autonomous Claude Code agents that pick up beads and complete tasks
3. The **dashboard** provides a UI for monitoring and spawning workers

## Prerequisites

1. MAB daemon running (`mab start`)
2. Dashboard running (`mab dashboard`)
3. Beads available in the project (`.beads/beads.db`)

## Step-by-Step Workflow

### 1. Create a Bead

```bash
# Create a dev-labeled bead for workers to pick up
bd create --title="Your task description" --type=task --priority=1 -l dev
```

Available labels:
- `dev` - Development work (code implementation)
- `qa` - Quality assurance (testing)
- `review` - Code review tasks
- `architecture` - Technical design decisions

### 2. Verify Bead is Ready

```bash
# Check for available work with specific label
bd ready -l dev

# Example output:
# 1. [P1] [task] multi_agent_beads-abc12: Your task description
```

### 3. Start the Dashboard (if not running)

```bash
mab dashboard
# Dashboard available at http://127.0.0.1:8000
```

### 4. Spawn a Worker via Dashboard UI

Navigate to the Admin page (`http://127.0.0.1:8000/admin`):

1. Select the **role** (dev, qa, tech_lead, manager, reviewer)
2. Set the **project path** (defaults to current project)
3. Toggle **auto_restart** if desired
4. Click **Spawn Worker**

### 5. Spawn a Worker via API

```bash
curl -X POST http://127.0.0.1:8000/api/workers \
  -H "Content-Type: application/json" \
  -d '{
    "role": "dev",
    "project_path": "/path/to/project",
    "auto_restart": false
  }'
```

Response:
```json
{
  "id": "worker-dev-abc12345",
  "pid": 12345,
  "status": "running",
  "role": "dev",
  "project_path": "/path/to/project",
  "started_at": "2026-02-01T16:21:53.202389",
  "crash_count": 0
}
```

### 6. Monitor Worker Progress

#### Via API

```bash
# Get worker status
curl http://127.0.0.1:8000/api/workers/{worker-id}

# List all workers
curl http://127.0.0.1:8000/api/workers
```

#### Via Dashboard

- Navigate to **Admin** page to see all workers
- Navigate to **Agents** page for detailed monitoring
- Navigate to **Logs** page for real-time log streaming

#### Via File System

```bash
# Worker logs directory
ls -la ~/.mab/logs/

# View specific worker log
cat ~/.mab/logs/worker-dev-{id}_{timestamp}.log

# Worker's internal log (in worktree)
cat .worktrees/worker-dev-{id}/claude.log
```

### 7. Observe Worker Claiming Bead

When a worker starts, it will:

1. Initialize logging: `SESSION_START`
2. Run `bd ready -l {role}` to find work
3. Claim the highest priority bead: `bd update {bead-id} --status=in_progress`
4. Log the claim: `CLAIM: {bead-id} - {title}`
5. Process the bead according to its role

### 8. Verify Bead Status

```bash
# Check if bead was claimed
bd show {bead-id}

# Expected: status should be IN_PROGRESS
```

## Architecture Details

### Worktrees

Each worker operates in an isolated git worktree:
- Location: `.worktrees/worker-{role}-{id}/`
- Prevents conflicts between concurrent workers
- Shares the same git history as main repository

### Daemon Communication

Workers communicate with the daemon via:
- Unix socket: `~/.mab/mab.sock`
- JSON-RPC protocol

### Worker Database

Worker metadata stored in:
- `~/.mab/workers.db` (SQLite)

### Dashboard API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/workers` | GET | List all workers |
| `/api/workers` | POST | Spawn new worker |
| `/api/workers/{id}` | GET | Get worker details |
| `/api/workers/{id}` | DELETE | Stop worker |

## Troubleshooting

### Worker Not Picking Up Beads

1. Check bead has correct label: `bd show {bead-id}`
2. Check bead is not blocked: `bd blocked`
3. Verify worker role matches bead label

### Worker Crashes Immediately

1. Check daemon logs: `~/.mab/daemon.log`
2. Check worker logs: `~/.mab/logs/worker-*.log`
3. Ensure project path is valid

### Dashboard Not Showing Workers

1. Verify daemon is running: `mab status`
2. Check RPC socket: `ls -la ~/.mab/mab.sock`
3. Restart dashboard if needed

## Quick Reference

```bash
# Start system
mab start         # Start daemon
mab dashboard     # Start dashboard

# Create work
bd create --title="Task" --type=task --priority=1 -l dev

# Spawn worker
curl -X POST http://127.0.0.1:8000/api/workers \
  -H "Content-Type: application/json" \
  -d '{"role": "dev", "project_path": "...", "auto_restart": false}'

# Monitor
curl http://127.0.0.1:8000/api/workers
tail -f ~/.mab/logs/worker-*.log
```
