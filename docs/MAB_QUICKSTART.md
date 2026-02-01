# MAB Quickstart Guide

Get Multi-Agent Beads (MAB) running in your project in 5 minutes.

## What is MAB?

MAB orchestrates concurrent Claude Code agents working on shared codebases. Think of it as a team of AI developers with specialized roles:

| Role | Purpose |
|------|---------|
| **dev** | Implements features, fixes bugs |
| **qa** | Runs tests, verifies quality |
| **tech-lead** | Makes architecture decisions |
| **manager** | Prioritizes work, manages scope |
| **reviewer** | Reviews code, approves PRs |

Tasks are tracked as "beads" - issues with dependency tracking that agents claim and complete autonomously.

## Quick Start

### 1. Install

```bash
# Clone and install (development mode)
git clone https://github.com/USER/multi_agent_beads.git
cd multi_agent_beads
uv sync

# Or install globally
uv tool install .
```

### 2. Initialize Your Project

```bash
cd /path/to/your/project

# Initialize beads (task tracking)
bd init

# Initialize MAB (agent orchestration)
uv run mab init
```

This creates:
- `.beads/` - Task database (git-tracked)
- `.mab/` - Project config and logs

### 3. Start the Daemon

```bash
# Start MAB daemon in background
uv run mab start -d
```

### 4. Start the Dashboard

```bash
# Launch web dashboard
uv run mab dashboard
```

Open http://127.0.0.1:8000 to see the kanban board.

### 5. Spawn Workers

```bash
# Spawn a developer agent
uv run mab spawn --role dev

# Spawn a QA agent
uv run mab spawn --role qa
```

## Creating Work

### Via CLI (bd)

```bash
# Create a task
bd create --title="Add user login endpoint" --type=feature --priority=2

# Create a bug
bd create --title="Fix null pointer in parser" --type=bug --priority=1

# Create with description
bd create --title="Refactor auth module" --type=task \
  --description="Split auth.py into separate files for OAuth and JWT"
```

### Via Dashboard

1. Open http://127.0.0.1:8000
2. Click "Create Bead"
3. Fill in title, type, priority
4. Save

### Managing Dependencies

```bash
# Issue Y depends on X (X blocks Y)
bd dep add <child-id> <parent-id>

# Example: tests depend on feature implementation
bd dep add beads-002 beads-001

# See blocked issues
bd blocked

# See what's ready to work on
bd ready
```

## Watching It Work

### Dashboard

The dashboard at http://127.0.0.1:8000 shows:
- Kanban board with task states
- Live worker status
- WebSocket log streaming

### CLI Status

```bash
# Daemon and worker status
uv run mab status

# List running workers
uv run mab list

# View logs
uv run mab logs -f
```

### Beads Status

```bash
# Ready work (unblocked)
bd ready

# All open issues
bd list --status=open

# Project overview
bd status
```

## Directory Structure

```
~/.mab/                    # Global daemon state
├── daemon.pid             # Daemon process ID
├── daemon.log             # Daemon logs
├── mab.sock               # Unix socket for RPC
└── workers.db             # SQLite state for all workers

your-project/
├── .beads/                # Task database (git-tracked)
│   ├── issues.jsonl       # Issue data
│   └── beads.db           # SQLite cache
├── .mab/                  # Project config (partially tracked)
│   ├── config.yaml        # Project settings
│   ├── logs/              # Worker logs
│   └── heartbeat/         # Worker health files
└── .worktrees/            # Isolated git worktrees per worker
    ├── worker-dev-abc123/
    └── worker-qa-xyz789/
```

## Common Commands

### Daemon Management

```bash
uv run mab start -d       # Start daemon in background
uv run mab stop --all     # Stop daemon and all workers
uv run mab restart        # Restart daemon
uv run mab status         # Check daemon status
```

### Worker Management

```bash
uv run mab spawn -r dev           # Spawn dev worker
uv run mab spawn -r qa -c 2       # Spawn 2 QA workers
uv run mab list                   # List workers
uv run mab logs -f                # Follow logs
```

### Dashboard

```bash
uv run mab dashboard              # Start dashboard
uv run mab dashboard --status     # Check dashboard status
uv run mab dashboard --stop       # Stop dashboard
uv run mab dashboard --port 8001  # Use specific port
```

### Task Management (bd)

```bash
bd ready                  # Show unblocked work
bd create --title="..."   # Create task
bd update <id> --status=in_progress  # Claim work
bd close <id>             # Complete task
bd show <id>              # View task details
bd doctor                 # Health check
```

## Troubleshooting

### Daemon Won't Start

```bash
# Check if already running
uv run mab status

# Check for stale PID file
ls -la ~/.mab/

# Force stop and restart
uv run mab stop --all --force
uv run mab start -d
```

### Dashboard Port In Use

```bash
# Check what's using port 8000
lsof -i :8000

# Use different port
uv run mab dashboard --port 8001
```

### Workers Not Claiming Work

```bash
# Check for ready work
bd ready

# Check worker logs
uv run mab logs -f

# Health check
bd doctor
```

### Worktree Issues

```bash
# Fix .beads symlinks in worktrees
uv run mab fix-worktrees
```

## Next Steps

- **Full Installation Guide**: [INSTALLATION.md](./INSTALLATION.md)
- **Daemon Architecture**: [daemon-architecture.md](./daemon-architecture.md)
- **Testing Guide**: [TESTING.md](./TESTING.md)

## Tips

1. **Start small**: Begin with 1 dev worker before scaling up
2. **Use dependencies**: Block work properly to avoid conflicts
3. **Watch the logs**: `mab logs -f` shows what agents are doing
4. **Check bd doctor**: Run periodically to catch issues early
5. **Git-track .beads**: Share task state with your team
