# MAB Daemon Architecture

Technical design document for the Multi-Agent Beads worker lifecycle management daemon.

**Status:** Draft
**Author:** Claude Opus 4.5
**Bead:** multi_agent_beads-z7xo
**Date:** 2026-01-26

---

## Table of Contents

1. [Overview](#overview)
2. [Daemon Process Design](#daemon-process-design)
3. [RPC Protocol Specification](#rpc-protocol-specification)
4. [State Management](#state-management)
5. [Health Check and Auto-Restart](#health-check-and-auto-restart)
6. [Graceful Shutdown](#graceful-shutdown)
7. [Multi-Town Isolation](#multi-town-isolation)
8. [Sequence Diagrams](#sequence-diagrams)
9. [Implementation Plan](#implementation-plan)

---

## Overview

### Purpose

The MAB daemon manages the lifecycle of Claude Code worker agents, providing:

- **Centralized orchestration** of multiple concurrent workers
- **Health monitoring** with automatic restart on failure
- **Graceful shutdown** allowing in-progress work to complete
- **Multi-town isolation** for running separate agent pools per project

### Design Principles

1. **Follow proven patterns** - Mirror beads daemon architecture (Unix socket, PID file, SQLite)
2. **Fail-safe defaults** - Workers continue even if daemon dies
3. **Minimal coordination** - Workers are largely autonomous; daemon provides scaffolding
4. **Observable state** - All state queryable via RPC for dashboard integration

### Comparison with Beads Daemon

| Aspect | Beads Daemon | MAB Daemon |
|--------|--------------|------------|
| Purpose | Database sync & RPC | Worker lifecycle management |
| Scope | Per-project (`.beads/`) | **Global per-user** (`~/.mab/`) |
| Socket | `.beads/bd.sock` | `~/.mab/mab.sock` |
| PID File | `.beads/daemon.pid` | `~/.mab/daemon.pid` |
| State Store | SQLite (beads.db) | SQLite (`~/.mab/workers.db`) |
| Event Model | Mutation-driven export | Worker state changes |

### Key Architectural Decisions

1. **Global Daemon**: One daemon process per user manages all towns/projects
2. **Project Identification**: Towns are identified by their filesystem path
3. **Local Logs**: Worker logs stay in `<project>/.mab/logs/` for easy debugging
4. **Config Inheritance**: Global defaults in `~/.mab/config.yaml` → Project overrides in `<project>/.mab/config.yaml`
5. **Single-Machine Design**: MAB is designed for single-machine deployments (see [Known Limitations](#known-limitations))

---

## Daemon Process Design

### Process Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MAB Daemon Process                              │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         Main Event Loop                               │   │
│  │                                                                       │   │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │   │
│  │   │ RPC Handler │  │Health Check │  │ Signal      │                 │   │
│  │   │  (socket)   │  │  (timer)    │  │ Handler     │                 │   │
│  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                 │   │
│  │          │                │                │                         │   │
│  │          └────────────────┼────────────────┘                         │   │
│  │                           │                                          │   │
│  │                    ┌──────▼──────┐                                   │   │
│  │                    │   Worker    │                                   │   │
│  │                    │  Registry   │                                   │   │
│  │                    └──────┬──────┘                                   │   │
│  │                           │                                          │   │
│  └───────────────────────────┼──────────────────────────────────────────┘   │
│                              │                                              │
│                       ┌──────▼──────┐                                       │
│                       │  SQLite DB  │                                       │
│                       │ workers.db  │                                       │
│                       └─────────────┘                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ subprocess spawn/monitor
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐          ┌───────────────┐          ┌───────────────┐
│    Worker 1   │          │    Worker 2   │          │    Worker 3   │
│  (Developer)  │          │     (QA)      │          │  (Developer)  │
│   PID: 1234   │          │   PID: 1235   │          │   PID: 1236   │
└───────────────┘          └───────────────┘          └───────────────┘
```

### Daemon Lifecycle

```
┌─────────────┐
│   STOPPED   │
└──────┬──────┘
       │ mab start --daemon
       ▼
┌─────────────┐
│  STARTING   │ → Acquire lock, create socket, initialize DB
└──────┬──────┘
       │ socket ready
       ▼
┌─────────────┐
│   RUNNING   │ → Accept RPC, spawn workers, health checks
└──────┬──────┘
       │ SIGTERM/SIGINT
       ▼
┌─────────────┐
│  STOPPING   │ → Signal workers, wait for graceful exit
└──────┬──────┘
       │ all workers stopped
       ▼
┌─────────────┐
│   STOPPED   │ → Cleanup socket, release lock
└─────────────┘
```

### File Layout (Hybrid Global/Local)

The MAB daemon uses a **hybrid approach**: global daemon state in `~/.mab/` with per-project worker logs and configuration overrides.

```
~/.mab/                       # GLOBAL daemon home (one per user)
├── daemon.pid                # Daemon process ID
├── daemon.lock               # Exclusive lock (flock)
├── daemon.log                # Daemon structured logs
├── mab.sock                  # Unix socket for RPC
├── workers.db                # SQLite state (ALL workers across ALL towns)
├── workers.db-wal            # Write-ahead log
├── workers.db-shm            # Shared memory
└── config.yaml               # Global defaults

<project>/.mab/               # PER-PROJECT town config
├── config.yaml               # Town-specific configuration overrides
├── logs/                     # Worker logs for this town
│   ├── worker-abc123.log     # Individual worker log
│   └── worker-xyz789.log
└── heartbeat/                # Heartbeat files for this town's workers
    ├── mab-worker-abc123     # Heartbeat touch file
    └── mab-worker-xyz789
```

#### Configuration Inheritance

Configuration is resolved with the following precedence (highest to lowest):

1. **Command-line arguments** (e.g., `mab spawn --max-workers=3`)
2. **Project config** (`<project>/.mab/config.yaml`)
3. **Global config** (`~/.mab/config.yaml`)
4. **Built-in defaults**

```yaml
# Example: ~/.mab/config.yaml (global)
defaults:
  max_workers_per_town: 5
  auto_restart: true
  health_check_interval: 10

# Example: <project>/.mab/config.yaml (overrides)
max_workers: 3  # Override: this project only needs 3 workers
roles:
  - developer
  - qa
```

### Startup Sequence

```python
import os
from pathlib import Path

MAB_HOME = Path.home() / ".mab"

def daemon_main():
    # 0. Ensure global directory exists
    MAB_HOME.mkdir(exist_ok=True)

    # 1. Acquire exclusive lock
    lock_file = open(MAB_HOME / "daemon.lock", "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # 2. Write PID file
    with open(MAB_HOME / "daemon.pid", "w") as f:
        f.write(str(os.getpid()))

    # 3. Initialize database (stores ALL workers across ALL towns)
    db = sqlite3.connect(MAB_HOME / "workers.db")
    init_schema(db)

    # 4. Create Unix socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(MAB_HOME / "mab.sock"))
    sock.listen(10)

    # 5. Install signal handlers
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    # 6. Start event loop
    asyncio.run(event_loop(sock, db))
```

---

## RPC Protocol Specification

### Transport Layer

- **Protocol:** Unix domain socket (SOCK_STREAM)
- **Location:** `~/.mab/mab.sock` (global, user-level)
- **Framing:** Length-prefixed JSON messages
- **Timeout:** 30 seconds default, configurable per command

### Message Format

**Request:**
```json
{
  "id": "uuid-v4",
  "method": "worker.spawn",
  "params": {
    "role": "developer",
    "town": "default"
  }
}
```

**Response (success):**
```json
{
  "id": "uuid-v4",
  "result": {
    "worker_id": "mab-worker-abc123",
    "pid": 12345,
    "status": "starting"
  }
}
```

**Response (error):**
```json
{
  "id": "uuid-v4",
  "error": {
    "code": -32600,
    "message": "Invalid role: 'invalid'"
  }
}
```

### RPC Methods

#### Worker Management

| Method | Description | Params | Returns |
|--------|-------------|--------|---------|
| `worker.spawn` | Start a new worker | `role`, `town?`, `instance?` | `worker_id`, `pid` |
| `worker.stop` | Stop a worker | `worker_id`, `graceful?` | `success`, `reason` |
| `worker.list` | List all workers | `town?`, `role?`, `status?` | `workers[]` |
| `worker.get` | Get worker details | `worker_id` | `worker` |
| `worker.restart` | Restart a worker | `worker_id` | `new_worker_id`, `pid` |

#### Town Management

| Method | Description | Params | Returns |
|--------|-------------|--------|---------|
| `town.create` | Create a new town | `name`, `path` | `town_id` |
| `town.list` | List all towns | | `towns[]` |
| `town.get` | Get town details | `town_id` | `town` |
| `town.delete` | Delete a town | `town_id` | `success` |

#### Daemon Control

| Method | Description | Params | Returns |
|--------|-------------|--------|---------|
| `daemon.status` | Get daemon status | | `status`, `uptime`, `stats` |
| `daemon.shutdown` | Initiate shutdown | `graceful?` | `success` |
| `daemon.config` | Get/set config | `key?`, `value?` | `config` |

### Wire Protocol

```
┌──────────────────────────────────────────────────────────────┐
│                        Message Frame                          │
├──────────────┬───────────────────────────────────────────────┤
│  Length (4B) │            JSON Payload (N bytes)              │
│  big-endian  │                                                │
└──────────────┴───────────────────────────────────────────────┘
```

**Example client implementation:**
```python
import socket
import struct
import json
from pathlib import Path

MAB_HOME = Path.home() / ".mab"

def rpc_call(method: str, params: dict) -> dict:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(MAB_HOME / "mab.sock"))  # Global socket

    request = json.dumps({
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params
    }).encode()

    # Send length-prefixed message
    sock.sendall(struct.pack(">I", len(request)))
    sock.sendall(request)

    # Receive response
    length_data = sock.recv(4)
    length = struct.unpack(">I", length_data)[0]
    response_data = sock.recv(length)

    sock.close()
    return json.loads(response_data)
```

---

## State Management

### Decision: SQLite with WAL Mode

**Chosen approach:** SQLite database with Write-Ahead Logging (WAL) mode.

**Rationale:**
- Consistent with beads daemon (proven pattern)
- ACID guarantees for worker state
- Concurrent read access for dashboard
- Simple backup/restore (single file)
- No external dependencies

**Alternatives considered:**
- In-memory with JSON persistence: Simpler but no concurrent access
- Redis: Overkill for single-machine scenario
- File-per-worker: Race conditions, no transactions

### Database Schema

```sql
-- Workers table: tracks all worker processes
CREATE TABLE workers (
    id TEXT PRIMARY KEY,           -- mab-worker-abc123
    town_id TEXT NOT NULL,         -- town this worker belongs to
    role TEXT NOT NULL,            -- developer, qa, tech_lead, etc.
    instance INTEGER DEFAULT 1,    -- instance number for same role
    pid INTEGER,                   -- OS process ID
    status TEXT NOT NULL,          -- starting, running, stopping, stopped, failed
    current_bead TEXT,             -- bead currently being worked on
    started_at TEXT,               -- ISO 8601 timestamp
    stopped_at TEXT,               -- ISO 8601 timestamp
    last_heartbeat TEXT,           -- last health check timestamp
    exit_code INTEGER,             -- process exit code if stopped
    error_message TEXT,            -- error if failed
    restart_count INTEGER DEFAULT 0,
    FOREIGN KEY (town_id) REFERENCES towns(id)
);

-- Towns table: isolated worker pools
CREATE TABLE towns (
    id TEXT PRIMARY KEY,           -- town-xyz123
    name TEXT NOT NULL UNIQUE,     -- human-readable name
    path TEXT NOT NULL,            -- filesystem path to project
    created_at TEXT NOT NULL,
    config TEXT                    -- JSON configuration overrides
);

-- Worker logs: recent activity for debugging
CREATE TABLE worker_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,           -- DEBUG, INFO, WARN, ERROR
    message TEXT NOT NULL,
    FOREIGN KEY (worker_id) REFERENCES workers(id)
);

-- Create indexes
CREATE INDEX idx_workers_town ON workers(town_id);
CREATE INDEX idx_workers_status ON workers(status);
CREATE INDEX idx_worker_logs_worker ON worker_logs(worker_id);
CREATE INDEX idx_worker_logs_timestamp ON worker_logs(timestamp);
```

### Worker States

```
                                    ┌─────────────┐
                    spawn           │  STARTING   │
               ┌───────────────────▶│             │
               │                    └──────┬──────┘
               │                           │ process alive
               │                           ▼
               │                    ┌─────────────┐
               │                    │   RUNNING   │◀──┐
               │                    │             │   │ restart
               │                    └──────┬──────┘   │
               │                           │          │
               │         ┌─────────────────┼──────────┼─────────────────┐
               │         │                 │          │                 │
               │         │ SIGTERM         │ crash    │ health fail     │
               │         ▼                 ▼          │                 │
               │  ┌─────────────┐   ┌─────────────┐   │                 │
               │  │  STOPPING   │   │   FAILED    │───┘                 │
               │  │             │   │             │     auto-restart    │
               │  └──────┬──────┘   └─────────────┘     (if enabled)    │
               │         │                                              │
               │         │ exit                                         │
               │         ▼                                              │
               │  ┌─────────────┐                                       │
               └──│   STOPPED   │◀──────────────────────────────────────┘
                  │             │     manual restart
                  └─────────────┘
```

### State Transitions

| From | To | Trigger | Action |
|------|-----|---------|--------|
| - | STARTING | `worker.spawn` | Fork subprocess |
| STARTING | RUNNING | Heartbeat received | Update DB |
| STARTING | FAILED | Timeout (30s) | Log error, cleanup |
| RUNNING | STOPPING | `worker.stop` | Send SIGTERM |
| RUNNING | FAILED | Heartbeat timeout | Log, maybe restart |
| STOPPING | STOPPED | Process exits | Cleanup |
| STOPPING | FAILED | Force timeout (60s) | SIGKILL, cleanup |
| FAILED | STARTING | Auto-restart | Spawn new process |
| STOPPED | STARTING | `worker.restart` | Spawn new process |

---

## Health Check and Auto-Restart

### Health Check Mechanism

Workers report health via two mechanisms:

1. **Heartbeat file** (primary): Worker touches `<project>/.mab/heartbeat/{worker_id}` (local to project)
2. **Process check** (fallback): Daemon checks if PID is alive

Heartbeat files are stored **per-project** for easy debugging and to keep worker state close to the code it's working on.

```python
async def health_check_loop(interval: int = 10):
    while running:
        await asyncio.sleep(interval)

        for worker in get_running_workers():
            # Heartbeat file is in the project directory (town path)
            town = get_town(worker.town_id)
            heartbeat_file = Path(town.path) / ".mab" / "heartbeat" / worker.id

            # Check heartbeat file freshness
            if heartbeat_file.exists():
                mtime = heartbeat_file.stat().st_mtime
                age = time.time() - mtime

                if age > HEARTBEAT_TIMEOUT:
                    mark_worker_unhealthy(worker)
                else:
                    update_last_heartbeat(worker)
            else:
                # Fallback: check if process is alive
                if not process_exists(worker.pid):
                    mark_worker_failed(worker)
```

### Heartbeat Protocol

**Worker side:**
```python
def worker_heartbeat_loop():
    # Heartbeat file is local to the project
    project_path = Path.cwd()  # Worker runs in project directory
    heartbeat_dir = project_path / ".mab" / "heartbeat"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_file = heartbeat_dir / WORKER_ID

    while running:
        # Touch the file
        heartbeat_file.touch()
        time.sleep(5)
```

**Configuration:**
```yaml
health_check:
  interval: 10          # seconds between checks
  heartbeat_timeout: 30 # seconds before marking unhealthy
  unhealthy_threshold: 3 # consecutive failures before restart
```

### Auto-Restart Strategy

```python
async def handle_worker_failure(worker: Worker):
    worker.restart_count += 1

    # Exponential backoff
    delay = min(300, 5 * (2 ** worker.restart_count))

    # Max restarts check
    if worker.restart_count > MAX_RESTARTS:
        log.error(f"Worker {worker.id} exceeded max restarts, giving up")
        worker.status = "failed"
        return

    log.info(f"Restarting worker {worker.id} in {delay}s (attempt {worker.restart_count})")
    await asyncio.sleep(delay)

    # Spawn replacement
    new_worker = await spawn_worker(
        role=worker.role,
        town=worker.town_id,
        instance=worker.instance
    )
```

**Restart policy configuration:**
```yaml
restart_policy:
  enabled: true
  max_restarts: 5        # per hour
  backoff_base: 5        # seconds
  backoff_max: 300       # 5 minutes max
  cooldown_period: 3600  # reset restart count after 1 hour
```

---

## Graceful Shutdown

### Shutdown Sequence

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Graceful Shutdown Flow                              │
│                                                                             │
│   SIGTERM                                                                   │
│      │                                                                      │
│      ▼                                                                      │
│  ┌────────────────┐                                                         │
│  │ Stop accepting │                                                         │
│  │ new RPC calls  │                                                         │
│  └───────┬────────┘                                                         │
│          │                                                                   │
│          ▼                                                                   │
│  ┌────────────────┐    SIGTERM    ┌─────────────┐                          │
│  │ Signal workers │──────────────▶│  Worker 1   │─┐                        │
│  │  (parallel)    │──────────────▶│  Worker 2   │ │ finish current        │
│  │                │──────────────▶│  Worker 3   │ │ bead if possible      │
│  └───────┬────────┘               └─────────────┘ │                        │
│          │                              │         │                        │
│          │                              ▼         │                        │
│          │                        ┌──────────────┐│                        │
│          │◀───────────────────────│ Workers exit ││                        │
│          │        (wait)          └──────────────┘│                        │
│          │                                        │                        │
│          ▼                     timeout (60s)      │                        │
│  ┌────────────────┐               │               │                        │
│  │ Force kill any │◀──────────────┘               │                        │
│  │  stuck workers │               SIGKILL         │                        │
│  └───────┬────────┘──────────────────────────────▶│                        │
│          │                                                                  │
│          ▼                                                                  │
│  ┌────────────────┐                                                         │
│  │ Cleanup:       │                                                         │
│  │ - Close socket │                                                         │
│  │ - Release lock │                                                         │
│  │ - Update DB    │                                                         │
│  └───────┬────────┘                                                         │
│          │                                                                   │
│          ▼                                                                   │
│      [EXIT 0]                                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Worker Graceful Shutdown

Workers receive SIGTERM and should:
1. Stop claiming new beads
2. Complete current bead if possible (with timeout)
3. Log `SESSION_END`
4. Exit cleanly

**Worker signal handler:**
```python
def worker_signal_handler(signum, frame):
    global SHUTTING_DOWN
    SHUTTING_DOWN = True

    log("SHUTDOWN: received signal, finishing current work")

    # Don't claim new beads
    # Current work continues until natural completion or timeout
```

### Shutdown Configuration

```yaml
shutdown:
  worker_grace_period: 60     # seconds to wait for workers
  force_kill_timeout: 10      # seconds after SIGKILL before giving up
  drain_connections: true     # wait for pending RPC responses
```

---

## Multi-Town Isolation

### Town Concept

A "town" is an isolated environment for running agents against a specific project. The **global daemon** (`~/.mab/`) orchestrates workers across **multiple towns**, while each project maintains its own local state:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    GLOBAL MAB Daemon (~/.mab/)                               │
│                    One daemon per user manages ALL towns                     │
│                                                                             │
│  ┌────────────────────────────────────────┐                                 │
│  │  ~/.mab/workers.db (GLOBAL STATE)      │                                 │
│  │  Tracks ALL workers across ALL towns   │                                 │
│  └────────────────────────────────────────┘                                 │
│                              │                                              │
│           ┌──────────────────┼──────────────────┐                           │
│           │                  │                  │                           │
│           ▼                  ▼                  ▼                           │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐               │
│  │  Town: frontend │ │  Town: backend  │ │  Town: shared   │               │
│  │  (by path)      │ │  (by path)      │ │  (by path)      │               │
│  └────────┬────────┘ └────────┬────────┘ └────────┬────────┘               │
│           │                   │                   │                         │
└───────────┼───────────────────┼───────────────────┼─────────────────────────┘
            │                   │                   │
            ▼                   ▼                   ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│ /projects/frontend  │ │ /projects/backend   │ │ /projects/shared    │
│ ├── .mab/           │ │ ├── .mab/           │ │ ├── .mab/           │
│ │   ├── config.yaml │ │ │   ├── config.yaml │ │ │   ├── config.yaml │
│ │   ├── logs/       │ │ │   ├── logs/       │ │ │   ├── logs/       │
│ │   └── heartbeat/  │ │ │   └── heartbeat/  │ │ │   └── heartbeat/  │
│ ├── .beads/         │ │ ├── .beads/         │ │ ├── .beads/         │
│ │   └── beads.db    │ │ │   └── beads.db    │ │ │   └── beads.db    │
│ └── src/            │ │ └── src/            │ │ └── lib/            │
│                     │ │                     │ │                     │
│  Workers:           │ │  Workers:           │ │  Workers:           │
│  ┌────────┐         │ │  ┌────────┐         │ │  ┌────────┐         │
│  │  Dev   │         │ │  │ Dev x2 │         │ │  │  Lead  │         │
│  │   QA   │         │ │  │  Lead  │         │ │  └────────┘         │
│  └────────┘         │ │  └────────┘         │ │                     │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
```

### Isolation Guarantees

| Aspect | Isolation Level | Location | Implementation |
|--------|-----------------|----------|----------------|
| Beads database | Full | `<project>/.beads/` | Each project has own beads |
| Worker state | Shared | `~/.mab/workers.db` | Global daemon tracks all |
| Worker processes | Full | N/A | Separate PIDs, working directories |
| Log files | Full | `<project>/.mab/logs/` | Logs stay local to project |
| Heartbeat files | Full | `<project>/.mab/heartbeat/` | Per-project for debugging |
| Configuration | Inherited | Both | Global → Project overrides |
| Resource limits | Configurable | Both | Per-town worker limits |

### Town Configuration

Towns can be configured at the global level (`~/.mab/config.yaml`) or overridden at the project level (`<project>/.mab/config.yaml`):

```yaml
# ~/.mab/config.yaml (GLOBAL defaults)
defaults:
  max_workers_per_town: 5
  auto_create_town: true  # create town when spawning in new path
  default_roles:
    - developer
    - qa

# Town-specific settings can also be declared globally
towns:
  # Towns are keyed by path for uniqueness
  "/home/user/projects/frontend":
    max_workers: 3
    roles:
      - developer
      - qa

  "/home/user/projects/backend":
    max_workers: 5
    roles:
      - developer
      - developer
      - qa
      - tech_lead
      - reviewer
```

```yaml
# /home/user/projects/frontend/.mab/config.yaml (PROJECT override)
# This overrides global settings for this specific project
max_workers: 2  # Override: fewer workers than global default
roles:
  - developer
# Note: Other settings inherit from ~/.mab/config.yaml
```

### Town Routing

When spawning a worker, the daemon resolves the town by project path and sets up the local directory structure:

```python
async def spawn_worker(role: str, project_path: str = None):
    """Spawn a worker for a project (town identified by path)."""
    project_path = Path(project_path or os.getcwd()).resolve()

    # Resolve town by path (create if auto_create enabled)
    town_config = get_town_by_path(project_path)
    if not town_config:
        if config.auto_create_town:
            town_config = create_town(project_path)
        else:
            raise ValueError(f"Unknown project (no town): {project_path}")

    # Merge configuration: global defaults → project overrides
    effective_config = merge_configs(
        global_config=load_config(MAB_HOME / "config.yaml"),
        project_config=load_config(project_path / ".mab" / "config.yaml")
    )

    # Check limits
    current_count = count_workers_in_town(project_path)
    if current_count >= effective_config.max_workers:
        raise ResourceError(f"Project {project_path} at capacity ({current_count}/{effective_config.max_workers})")

    # Ensure local directories exist
    local_mab = project_path / ".mab"
    (local_mab / "logs").mkdir(parents=True, exist_ok=True)
    (local_mab / "heartbeat").mkdir(parents=True, exist_ok=True)

    # Create worker record (stored in global ~/.mab/workers.db)
    worker = Worker(
        id=generate_worker_id(),
        town_path=str(project_path),  # Town identified by path
        role=role,
    )

    # Start process with correct working directory
    # Logs go to project-local directory
    log_file = local_mab / "logs" / f"{worker.id}.log"
    process = await spawn_claude_code(
        cwd=project_path,
        prompt_file=f"prompts/{role.upper()}.md",
        log_file=log_file
    )

    worker.pid = process.pid
    save_worker(worker)  # Saved to ~/.mab/workers.db

    return worker
```

---

## Sequence Diagrams

The following diagrams illustrate the hybrid global/local architecture where:
- **Daemon** runs globally at `~/.mab/`
- **Workers** run in project directories with local logs/heartbeats at `<project>/.mab/`

### Worker Spawn

```
┌───────┐          ┌─────────────┐       ┌─────────────┐     ┌────────────────┐
│  CLI  │          │   Daemon    │       │ ~/.mab/     │     │    Worker      │
│       │          │ (~/.mab/)   │       │ workers.db  │     │ (in project)   │
└───┬───┘          └──────┬──────┘       └──────┬──────┘     └───────┬────────┘
    │                     │                     │                    │
    │  worker.spawn       │                     │                    │
    │  (developer,        │                     │                    │
    │   /proj/frontend)   │                     │                    │
    │────────────────────▶│                     │                    │
    │                     │                     │                    │
    │                     │ INSERT worker       │                    │
    │                     │ town=/proj/frontend │                    │
    │                     │ status=starting     │                    │
    │                     │────────────────────▶│                    │
    │                     │                     │                    │
    │                     │ mkdir /proj/frontend/.mab/{logs,heartbeat}
    │                     │                     │                    │
    │                     │ fork process (cwd=/proj/frontend)       │
    │                     │─────────────────────────────────────────▶│
    │                     │                     │                    │
    │  {worker_id,        │                     │     logs →         │
    │   pid, status}      │                     │  /proj/.mab/logs/  │
    │◀────────────────────│                     │                    │
    │                     │                     │                    │
    │                     │ heartbeat file at   │                    │
    │                     │ /proj/.mab/heartbeat/worker-id           │
    │                     │◀────────────────────────────────────────│
    │                     │                     │                    │
    │                     │ UPDATE worker       │                    │
    │                     │ status=running      │                    │
    │                     │────────────────────▶│                    │
    │                     │                     │                    │
```

### Health Check Failure

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐   ┌──────────────────┐
│   Daemon    │     │ ~/.mab/     │     │ Worker 1         │   │ Worker 2         │
│  (~/.mab/)  │     │ workers.db  │     │ (/proj/frontend) │   │ (/proj/backend)  │
└──────┬──────┘     └──────┬──────┘     └────────┬─────────┘   └────────┬─────────┘
       │                   │                     │                      │
       │  health check     │                     │                      │
       │  tick             │                     │                      │
       │───────┐           │                     │                      │
       │       │           │                     │                      │
       │◀──────┘           │                     │                      │
       │                   │                     │                      │
       │ check heartbeat   │                     │                      │
       │ /proj/frontend/.mab/heartbeat/worker1   │                      │
       │ → STALE (no update)                (crashed)                   │
       │                   │                     X                      │
       │                   │                                            │
       │ check heartbeat   │                                            │
       │ /proj/backend/.mab/heartbeat/worker2                           │
       │ → FRESH           │                                            │
       │◀───────────────────────────────────────────────────────────────│
       │                   │                                            │
       │ UPDATE Worker 1   │                                            │
       │ status=failed     │                                            │
       │──────────────────▶│                                            │
       │                   │                                            │
       │ schedule restart  │                                            │
       │ (backoff: 5s)     │                                            │
       │───────┐           │                                            │
       │       │           │                                            │
       │◀──────┘           │                     ┌─────────────────┐    │
       │                   │                     │ Worker 1 (new)  │    │
       │ spawn replacement │                     │ /proj/frontend  │    │
       │ in /proj/frontend │                     │                 │    │
       │─────────────────────────────────────────▶                 │    │
       │                   │                     └────────┬────────┘    │
       │                   │                              │             │
```

### Graceful Shutdown

```
┌───────┐        ┌─────────────┐      ┌──────────────────┐   ┌──────────────────┐
│  OS   │        │   Daemon    │      │ Worker 1         │   │ Worker 2         │
│       │        │  (~/.mab/)  │      │ (/proj/frontend) │   │ (/proj/backend)  │
└───┬───┘        └──────┬──────┘      └────────┬─────────┘   └────────┬─────────┘
    │                   │                      │                      │
    │  SIGTERM          │                      │                      │
    │──────────────────▶│                      │                      │
    │                   │                      │                      │
    │                   │ stop accepting       │                      │
    │                   │ new RPC connections  │                      │
    │                   │───────┐              │                      │
    │                   │       │              │                      │
    │                   │◀──────┘              │                      │
    │                   │                      │                      │
    │                   │     SIGTERM (to all workers across all towns)
    │                   │─────────────────────▶│                      │
    │                   │                      │                      │
    │                   │─────────────────────────────────────────────▶│
    │                   │                      │                      │
    │                   │ finish current bead  │                      │
    │                   │                      │───────┐              │
    │                   │                      │       │              │
    │                   │     exit(0)          │◀──────┘              │
    │                   │◀─────────────────────│                      │
    │                   │                      │                      │
    │                   │                      │    finish current bead
    │                   │                      │                      │───────┐
    │                   │                      │                      │       │
    │                   │     exit(0)          │                      │◀──────┘
    │                   │◀────────────────────────────────────────────│
    │                   │                      │                      │
    │                   │ cleanup:             │                      │
    │                   │ - close ~/.mab/mab.sock                     │
    │                   │ - release ~/.mab/daemon.lock                │
    │                   │ - update ~/.mab/workers.db                  │
    │                   │───────┐              │                      │
    │                   │       │              │                      │
    │   exit(0)         │◀──────┘              │                      │
    │◀──────────────────│                      │                      │
    │                   │                      │                      │
```

---

## Implementation Plan

### Phase 1: Core Daemon (multi_agent_beads-7txe)

1. **Basic process management**
   - PID file handling
   - Lock file for single instance
   - Signal handlers (SIGTERM, SIGINT)

2. **SQLite state store**
   - Schema creation
   - Worker CRUD operations
   - WAL mode configuration

3. **Unix socket RPC**
   - Socket creation and binding
   - Message framing (length-prefix)
   - Basic error handling

### Phase 2: Worker Lifecycle

4. **Worker spawning**
   - Claude Code subprocess launch
   - Environment setup
   - Initial state tracking

5. **Health monitoring**
   - Heartbeat file mechanism
   - Process liveness checks
   - State transitions

6. **Auto-restart**
   - Failure detection
   - Exponential backoff
   - Restart limits

### Phase 3: Multi-Town Support

7. **Town management**
   - Town CRUD operations
   - Configuration per town
   - Worker routing

8. **Isolation enforcement**
   - Working directory isolation
   - Resource limits
   - Log separation

### Phase 4: CLI Integration

9. **mab CLI commands**
   - `mab start --daemon`
   - `mab stop [--all] [--graceful]`
   - `mab status [--watch] [--json]`

10. **Dashboard integration**
    - Worker status API
    - Town overview
    - Health metrics

---

## Known Limitations

### Single-Machine Deployment Only

MAB is designed for **single-machine deployments** and does not support running multiple daemon instances across different machines against the same `~/.mab` directory.

**Technical Reason:** The daemon uses `fcntl.flock()` for single-instance enforcement:

```python
fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
```

This POSIX file locking mechanism only works reliably on local filesystems. On network filesystems (NFS, CIFS, SMB, etc.), `flock()` behavior is:

- **NFS**: Advisory locks may not be respected across different machines
- **CIFS/SMB**: Lock semantics vary by implementation
- **Other network FS**: Generally unreliable or unsupported

**Impact of Running on Network Filesystem:**
- Multiple daemons could start on different machines
- Race conditions in worker state management
- Corrupted SQLite database (workers.db)
- Undefined behavior in dashboard operations

**Runtime Detection:** MAB detects network filesystems at startup and logs a warning:

```
WARNING: MAB daemon: Directory /home/user/.mab appears to be on a network filesystem (nfs4).
File locking (flock) may not work reliably. Running multiple MAB instances on different
machines against the same directory could cause undefined behavior and state corruption.
```

**Recommendations:**
1. Ensure `~/.mab` is on a local filesystem (ext4, xfs, apfs, etc.)
2. For multi-machine deployments, run separate daemon instances with separate `~/.mab` directories
3. If you need distributed coordination, consider using external tools (etcd, consul) or a database-backed solution

**Future Consideration:** A future version could optionally use database-backed locking (e.g., PostgreSQL advisory locks) for multi-machine deployments, but this is not currently planned.

---

## Appendix: Configuration Reference

### Global Configuration (`~/.mab/config.yaml`)

The global configuration applies to all projects unless overridden:

```yaml
# ~/.mab/config.yaml - Full configuration reference

# Daemon process settings (global only)
daemon:
  socket: mab.sock          # Relative to ~/.mab/
  pid_file: daemon.pid      # Relative to ~/.mab/
  lock_file: daemon.lock    # Relative to ~/.mab/
  log_file: daemon.log      # Relative to ~/.mab/
  log_level: INFO

# Database settings (global only - single DB for all workers)
database:
  path: workers.db          # Relative to ~/.mab/
  wal_mode: true
  busy_timeout: 5000

# Health check settings (can override per-project)
health_check:
  enabled: true
  interval: 10
  heartbeat_timeout: 30
  unhealthy_threshold: 3

# Restart policy (can override per-project)
restart_policy:
  enabled: true
  max_restarts: 5
  backoff_base: 5
  backoff_max: 300
  cooldown_period: 3600

# Shutdown settings (global only)
shutdown:
  worker_grace_period: 60
  force_kill_timeout: 10
  drain_connections: true

# Default settings for all projects
defaults:
  max_workers_per_town: 5
  auto_create_town: true
  default_roles:
    - developer
    - qa

# Optional: pre-declare town-specific settings
towns:
  "/path/to/project":
    max_workers: 3
    roles:
      - developer
```

### Project Configuration (`<project>/.mab/config.yaml`)

Project-level overrides (inherits from global):

```yaml
# <project>/.mab/config.yaml - Project-specific overrides

# Override max workers for this project
max_workers: 3

# Override roles for this project
roles:
  - developer
  - developer  # 2 developer instances
  - qa
  - tech_lead

# Override health check for this project
health_check:
  heartbeat_timeout: 60  # Longer timeout for slow workers

# Override restart policy for this project
restart_policy:
  max_restarts: 3  # Fewer restarts for this project
```

### Configuration Inheritance Example

```
~/.mab/config.yaml              <project>/.mab/config.yaml       Effective
─────────────────────────────   ────────────────────────────     ─────────
max_workers_per_town: 5         max_workers: 3                 → max_workers: 3
health_check.interval: 10       (not set)                      → health_check.interval: 10
restart_policy.enabled: true    (not set)                      → restart_policy.enabled: true
default_roles: [dev, qa]        roles: [dev, dev, qa, lead]    → roles: [dev, dev, qa, lead]
```
