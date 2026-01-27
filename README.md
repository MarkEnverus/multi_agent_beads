# Multi-Agent Beads (MAB)

Multi-agent SDLC orchestration system where Developer, QA, Tech Lead, Manager, and Code Reviewer agents work concurrently on shared codebases with proper task handoffs.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Command Reference](#cli-command-reference)
- [Configuration](#configuration)
- [Web Dashboard](#web-dashboard)
- [Architecture](#architecture)
- [Agent Roles](#agent-roles)
- [Troubleshooting](#troubleshooting)

---

## Overview

MAB coordinates multiple Claude Code agents to work on software development tasks collaboratively. It uses [Beads](https://github.com/steveyegge/beads) as a task coordination layer, enabling agents to:

- **Pick up work autonomously** from a shared queue
- **Respect dependencies** - tasks block/unblock automatically
- **Work in parallel** - multiple agents on independent tasks
- **Hand off properly** - QA validates developer work, reviewers approve PRs
- **Monitor progress** - real-time dashboard shows agent activity

### Key Features

| Feature | Description |
|---------|-------------|
| Role-based agents | Developer, QA, Tech Lead, Manager, Code Reviewer |
| Dependency workflows | Tasks automatically block/unblock based on relationships |
| Multi-town support | Run isolated environments on different ports |
| Health monitoring | Auto-restart crashed workers |
| Web Dashboard | Kanban board, dependency graph, log streaming |
| Cross-platform | Works on macOS and Linux |

---

## Installation

### Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** - Fast Python package manager
- **[Beads](https://github.com/steveyegge/beads)** CLI (`bd` command)
- **[Claude Code](https://github.com/anthropics/claude-code)** CLI (`claude` command)
- **Git** (for version control)

### Option 1: Install with uv (Recommended)

```bash
# Clone the repository
git clone https://github.com/your-org/multi_agent_beads.git
cd multi_agent_beads

# Install dependencies and create virtual environment
uv sync

# Install mab CLI globally (optional)
uv tool install .
```

### Option 2: Install with pipx

```bash
# Install from local directory
pipx install .

# Or install from git URL
pipx install git+https://github.com/your-org/multi_agent_beads.git
```

### Option 3: Development Install

```bash
# Clone and install with dev dependencies
git clone https://github.com/your-org/multi_agent_beads.git
cd multi_agent_beads
uv sync --all-extras

# Verify installation
uv run mab --version
```

### Verify Installation

```bash
# Check mab CLI
mab --version

# Check beads CLI
bd --version

# Check claude CLI
claude --version
```

---

## Quick Start

### 1. Initialize a Project

```bash
cd your-project-directory

# Initialize MAB in your project
mab init

# This creates:
#   .mab/config.yaml  - Project configuration
#   .mab/logs/        - Worker log files
#   .mab/heartbeat/   - Health monitoring files
```

### 2. Start the Dashboard

```bash
# Start the web dashboard
uv run python -m dashboard.app

# Opens at http://127.0.0.1:8000
```

### 3. Start Workers

```bash
# Start the daemon with workers
mab start --daemon --workers 2 --role dev

# Or run a single worker in foreground
mab start --workers 1 --role qa
```

### 4. Monitor Progress

```bash
# Check daemon status
mab status

# Watch status continuously
mab status --watch

# View logs
tail -f claude.log
```

### 5. Stop Workers

```bash
# Graceful shutdown
mab stop --all

# Force shutdown
mab stop --all --force
```

---

## CLI Command Reference

### Global Commands

#### `mab --version`
Show the current MAB version.

#### `mab --help`
Show help for all commands.

### Project Initialization

#### `mab init [DIRECTORY]`

Initialize a MAB project in the specified directory (defaults to current).

```bash
# Initialize with default config
mab init

# Initialize with full configuration template
mab init --template full

# Initialize in a specific directory
mab init /path/to/project

# Force reinitialize (overwrites existing config)
mab init --force
```

**Options:**
| Option | Description |
|--------|-------------|
| `--template, -t` | Config template: `default`, `minimal`, `full` |
| `--force, -f` | Overwrite existing configuration |

### Daemon Control

#### `mab start`

Start agent workers.

```bash
# Start daemon with default settings
mab start --daemon

# Start 3 developer workers
mab start --daemon --workers 3 --role dev

# Run in foreground (for debugging)
mab start --workers 1 --role qa
```

**Options:**
| Option | Description |
|--------|-------------|
| `--daemon, -d` | Run as background daemon |
| `--workers, -w` | Number of workers to spawn (default: 1) |
| `--role, -r` | Agent role: `dev`, `qa`, `tech-lead`, `manager`, `reviewer`, `all` |

#### `mab stop [WORKER_ID]`

Stop workers or the entire daemon.

```bash
# Stop all workers and daemon
mab stop --all

# Graceful shutdown with timeout
mab stop --all --graceful --timeout 120

# Force immediate shutdown
mab stop --all --force
```

**Options:**
| Option | Description |
|--------|-------------|
| `--all, -a` | Stop all workers and daemon |
| `--graceful/-g, --force/-f` | Shutdown mode (default: graceful) |
| `--timeout, -t` | Graceful shutdown timeout in seconds (default: 60) |

#### `mab status`

Show daemon and worker status.

```bash
# Show current status
mab status

# Continuously update status
mab status --watch

# Output as JSON
mab status --json
```

**Options:**
| Option | Description |
|--------|-------------|
| `--watch, -w` | Continuously update display |
| `--json` | Output as JSON |

#### `mab restart`

Restart the daemon.

```bash
# Restart as daemon
mab restart

# Restart in foreground
mab restart --no-daemon
```

### Town Management

Towns are isolated orchestration contexts, each with their own dashboard port, worker pool, and configuration.

#### `mab town create NAME`

Create a new town.

```bash
# Create a staging town on port 8001
mab town create staging --port 8001

# Create a town for a specific project
mab town create myproject --project /path/to/project --roles dev --roles qa

# Create with custom worker limit
mab town create prod --max-workers 5
```

**Options:**
| Option | Description |
|--------|-------------|
| `--port, -p` | Dashboard port (auto-allocated if not specified) |
| `--project, -P` | Project directory path |
| `--max-workers, -w` | Maximum concurrent workers (default: 3) |
| `--roles, -r` | Default roles (repeatable) |
| `--description, -d` | Human-readable description |

#### `mab town list`

List all towns.

```bash
# List all towns
mab town list

# Filter by status
mab town list --status running

# Output as JSON
mab town list --json
```

#### `mab town show NAME`

Show details of a specific town.

```bash
mab town show staging
```

#### `mab town update NAME`

Update town configuration.

```bash
# Change port
mab town update staging --port 8002

# Change max workers
mab town update staging --max-workers 4
```

#### `mab town delete NAME`

Delete a town.

```bash
# Delete with confirmation
mab town delete staging

# Force delete (even if running)
mab town delete staging --force --yes
```

---

## Configuration

### Project Configuration (`.mab/config.yaml`)

Created by `mab init`, this file controls project-specific settings.

#### Minimal Configuration

```yaml
# .mab/config.yaml
project:
  name: "my-project"

workers:
  max_workers: 2
```

#### Full Configuration

```yaml
# .mab/config.yaml

# Project identification
project:
  name: "my-project"
  description: "Project description"
  issue_prefix: ""  # Overrides beads prefix if set

# Worker settings
workers:
  max_workers: 5
  default_roles:
    - dev
    - qa
    - reviewer
  restart_policy: always  # always, on-failure, never
  heartbeat_interval: 30
  max_failures: 3

# Role-specific configuration
roles:
  dev:
    labels:
      - dev
      - feature
      - bug
    max_priority: 3  # 0=P0 only, 4=all

  qa:
    labels:
      - qa
      - test
    max_priority: 2

  reviewer:
    labels:
      - review
    max_priority: 2

# Beads integration
beads:
  enabled: true
  path: ".beads"

# Logging
logging:
  level: info  # debug, info, warning, error
  retention_days: 7

# Hooks (scripts to run at various points)
hooks:
  pre_claim: ""
  post_complete: ""
  on_error: ""
```

### Global Configuration (`~/.mab/`)

The global daemon state lives at `~/.mab/`:

```
~/.mab/
├── daemon.pid      # Daemon process ID
├── daemon.lock     # Exclusive lock file
├── daemon.log      # Daemon logs
├── mab.sock        # Unix socket for RPC
├── config.yaml     # Global defaults
└── towns/          # Town configurations
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHBOARD_HOST` | `127.0.0.1` | Dashboard bind address |
| `DASHBOARD_PORT` | `8000` | Dashboard port |
| `DASHBOARD_TOWN` | `default` | Town name for dashboard |
| `DASHBOARD_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `DASHBOARD_CACHE_TTL` | `5.0` | Cache TTL in seconds |

---

## Web Dashboard

The web dashboard provides real-time monitoring of agent activity.

### Starting the Dashboard

```bash
# Default (port 8000)
uv run python -m dashboard.app

# Custom port
DASHBOARD_PORT=8001 uv run python -m dashboard.app

# With debug logging
DASHBOARD_LOG_LEVEL=DEBUG uv run python -m dashboard.app
```

### Dashboard Views

#### Main Dashboard (`/`)

The Kanban board view shows beads organized by status:

- **Ready** - Work available for agents to claim
- **In Progress** - Currently being worked on
- **Done** - Completed work

Features:
- Click any bead card to view details
- Real-time updates via WebSocket
- Color-coded by priority (P0=red, P1=orange, P2=blue, P3=gray)
- Blocked beads shown with red border

#### Admin Page (`/admin`)

System administration page showing:

- **Daemon Status** - Running state, PID, uptime
- **Worker List** - Active workers with role, status, project
- **Health Monitoring** - Crash counts, restart status
- **Town Switcher** - Switch between orchestration contexts

#### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/beads` | List all beads |
| `GET /api/beads/{id}` | Get bead details |
| `GET /api/agents` | List active agents |
| `GET /api/logs` | Stream agent logs |
| `GET /api/logs/level` | Get/set log level |
| `GET /api/workers` | List workers |
| `GET /api/towns` | List towns |
| `WS /ws/logs` | WebSocket log streaming |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                         │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   MAB CLI    │    │   Dashboard  │    │    Towns     │       │
│  │  (mab ...)   │    │  (FastAPI)   │    │  (isolated)  │       │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘       │
│         │                   │                   │                │
│         └───────────────────┼───────────────────┘                │
│                             │                                    │
│                      ┌──────▼──────┐                             │
│                      │   Daemon    │                             │
│                      │  (~/.mab/)  │                             │
│                      └──────┬──────┘                             │
│                             │                                    │
│              ┌──────────────┼──────────────┐                     │
│              │              │              │                     │
│        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐              │
│        │  Worker   │  │  Worker   │  │  Worker   │              │
│        │ Manager   │  │  Spawner  │  │  Health   │              │
│        └───────────┘  └───────────┘  └───────────┘              │
└─────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                     AGENT LAYER                                  │
│                             │                                    │
│              ┌──────────────┼──────────────┐                     │
│              │              │              │                     │
│        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐              │
│        │ Developer │  │    QA     │  │ Reviewer  │              │
│        │  Agent    │  │   Agent   │  │   Agent   │              │
│        └─────┬─────┘  └─────┬─────┘  └─────┬─────┘              │
│              │              │              │                     │
│              └──────────────┼──────────────┘                     │
│                             │                                    │
│                      ┌──────▼──────┐                             │
│                      │   Beads     │                             │
│                      │    (bd)     │                             │
│                      └─────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

### Component Overview

| Component | Purpose |
|-----------|---------|
| **MAB CLI** | Command-line interface for managing daemon and workers |
| **Daemon** | Background process managing worker lifecycle |
| **Worker Manager** | Tracks worker state, handles spawn/stop |
| **Spawner** | Cross-platform process spawning (PTY, tmux) |
| **Health Monitor** | Detects crashes, triggers auto-restart |
| **Dashboard** | Web UI for monitoring and control |
| **Towns** | Isolated orchestration contexts |
| **Agents** | Claude Code instances with role-specific prompts |
| **Beads** | Task tracking and dependency management |

### File Layout

```
project/
├── .mab/                    # Per-project MAB config
│   ├── config.yaml          # Project settings
│   ├── logs/                # Worker logs
│   └── heartbeat/           # Health check files
├── .beads/                  # Beads issue tracking
├── prompts/                 # Role-specific agent prompts
│   ├── DEVELOPER.md
│   ├── QA.md
│   ├── TECH_LEAD.md
│   ├── MANAGER.md
│   └── CODE_REVIEWER.md
├── dashboard/               # Web dashboard (FastAPI)
├── mab/                     # MAB CLI package
└── claude.log               # Agent activity log
```

---

## Agent Roles

Each agent role has specific responsibilities and sees only relevant work.

| Role | Focus | Label Filter | Responsibilities |
|------|-------|--------------|------------------|
| **Developer** | Implementation | `bd ready -l dev` | Write code, fix bugs, create PRs |
| **QA** | Testing | `bd ready -l qa` | Run tests, verify acceptance criteria, report bugs |
| **Tech Lead** | Architecture | `bd ready -l architecture` | Design decisions, task breakdowns, unblock technical issues |
| **Manager** | Coordination | `bd ready` (all) | Create epics, set priorities, track progress |
| **Reviewer** | Quality | `bd ready -l review` | Review PRs, check code quality, approve merges |

### Workflow Example

```
Manager creates epic
       ↓
Tech Lead breaks into tasks, sets dependencies
       ↓
Developer claims task, implements feature
       ↓
Developer creates PR
       ↓
Reviewer approves PR
       ↓
Developer merges PR
       ↓
QA verifies feature works
       ↓
Manager closes epic
```

---

## Troubleshooting

### Common Issues

#### Daemon won't start

**Symptom:** `mab start` fails with "Daemon already running"

```bash
# Check for stale PID file
cat ~/.mab/daemon.pid

# Check if process is actually running
ps -p $(cat ~/.mab/daemon.pid)

# Force cleanup if process doesn't exist
rm ~/.mab/daemon.pid ~/.mab/daemon.lock
mab start
```

#### Workers keep crashing

**Symptom:** Workers spawn but immediately exit

```bash
# Check worker logs
ls -la ~/.mab/logs/
tail -100 ~/.mab/logs/worker-dev-*.log

# Check if Claude CLI is available
which claude
claude --version

# Check prompt files exist
ls -la prompts/
```

#### Dashboard won't start

**Symptom:** Port already in use

```bash
# Find process using port
lsof -i :8000

# Kill it or use different port
DASHBOARD_PORT=8001 uv run python -m dashboard.app
```

#### Beads commands are slow

**Symptom:** Dashboard shows "Daemon took too long to start"

```bash
# Check beads daemon
bd doctor

# Restart beads daemon
bd daemon stop
bd daemon start
```

### Debug Mode

Enable debug logging for more information:

```bash
# Dashboard debug logging
DASHBOARD_LOG_LEVEL=DEBUG uv run python -m dashboard.app

# Check daemon logs
tail -f ~/.mab/daemon.log

# Check agent activity
tail -f claude.log
```

### Health Checks

```bash
# Check MAB daemon health
mab status --json

# Check beads health
bd stats
bd doctor

# Check dashboard health
curl http://127.0.0.1:8000/health
```

### Reset Everything

If all else fails:

```bash
# Stop all MAB processes
mab stop --all --force

# Clean up MAB state
rm -rf ~/.mab/daemon.pid ~/.mab/daemon.lock ~/.mab/mab.sock

# Restart
mab start --daemon
```

---

## Development

### Running Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov=dashboard --cov=mab

# Specific test file
uv run pytest tests/test_dashboard.py -v
```

### Code Quality

```bash
# Linting
uv run ruff check .

# Type checking
uv run mypy dashboard/ mab/ --ignore-missing-imports

# Format code
uv run ruff format .
```

### Project Structure

```
multi_agent_beads/
├── dashboard/           # FastAPI web dashboard
│   ├── app.py          # Main application
│   ├── config.py       # Configuration
│   ├── routes/         # API endpoints
│   ├── services/       # Business logic
│   ├── templates/      # Jinja2 templates
│   └── static/         # CSS, JS assets
├── mab/                # MAB CLI package
│   ├── cli.py          # Click CLI commands
│   ├── daemon.py       # Background daemon
│   ├── workers.py      # Worker management
│   ├── spawner.py      # Process spawning
│   ├── towns.py        # Town management
│   └── rpc.py          # RPC communication
├── prompts/            # Agent role prompts
├── scripts/            # Utility scripts
├── tests/              # Test suite
└── docs/               # Additional documentation
```

---

## License

MIT

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `uv run pytest`
5. Run linting: `uv run ruff check .`
6. Submit a pull request

For questions or issues, please open a GitHub issue.
