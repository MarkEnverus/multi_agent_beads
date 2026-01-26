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
| Socket | `.beads/bd.sock` | `.mab/mab.sock` |
| PID File | `.beads/daemon.pid` | `.mab/daemon.pid` |
| State Store | SQLite (beads.db) | SQLite (workers.db) |
| Event Model | Mutation-driven export | Worker state changes |

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

### File Layout

```
.mab/
├── daemon.pid        # Daemon process ID
├── daemon.lock       # Exclusive lock (flock)
├── daemon.log        # Daemon structured logs
├── mab.sock          # Unix socket for RPC
├── workers.db        # SQLite state database
├── workers.db-wal    # Write-ahead log
├── workers.db-shm    # Shared memory
└── config.yaml       # Daemon configuration
```

### Startup Sequence

```python
def daemon_main():
    # 1. Acquire exclusive lock
    lock_file = open(".mab/daemon.lock", "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # 2. Write PID file
    with open(".mab/daemon.pid", "w") as f:
        f.write(str(os.getpid()))

    # 3. Initialize database
    db = sqlite3.connect(".mab/workers.db")
    init_schema(db)

    # 4. Create Unix socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(".mab/mab.sock")
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
- **Location:** `.mab/mab.sock`
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

def rpc_call(method: str, params: dict) -> dict:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(".mab/mab.sock")

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

1. **Heartbeat file** (primary): Worker touches `.mab/heartbeat/{worker_id}`
2. **Process check** (fallback): Daemon checks if PID is alive

```python
async def health_check_loop(interval: int = 10):
    while running:
        await asyncio.sleep(interval)

        for worker in get_running_workers():
            heartbeat_file = f".mab/heartbeat/{worker.id}"

            # Check heartbeat file freshness
            if os.path.exists(heartbeat_file):
                mtime = os.path.getmtime(heartbeat_file)
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
    heartbeat_file = f".mab/heartbeat/{WORKER_ID}"
    while running:
        # Touch the file
        Path(heartbeat_file).touch()
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

A "town" is an isolated environment for running agents against a specific project:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MAB Daemon                                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         Town: "frontend"                             │   │
│  │  path: /home/user/projects/frontend                                  │   │
│  │                                                                       │   │
│  │  ┌─────────────┐  ┌─────────────┐                                   │   │
│  │  │  Developer  │  │     QA      │                                   │   │
│  │  └─────────────┘  └─────────────┘                                   │   │
│  │         │                │                                           │   │
│  │         └────────┬───────┘                                           │   │
│  │                  ▼                                                   │   │
│  │         .beads/beads.db (frontend)                                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         Town: "backend"                               │   │
│  │  path: /home/user/projects/backend                                    │   │
│  │                                                                       │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                   │   │
│  │  │  Developer  │  │  Developer  │  │  Tech Lead  │                   │   │
│  │  │  (inst 1)   │  │  (inst 2)   │  │             │                   │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                   │   │
│  │         │                │                │                           │   │
│  │         └────────────────┼────────────────┘                           │   │
│  │                          ▼                                            │   │
│  │               .beads/beads.db (backend)                               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Isolation Guarantees

| Aspect | Isolation Level | Implementation |
|--------|-----------------|----------------|
| Beads database | Full | Each town has own `.beads/` |
| Worker processes | Full | Separate PIDs, working directories |
| Log files | Full | `{town_path}/claude.log` |
| Configuration | Partial | Town can override daemon defaults |
| Resource limits | Configurable | Per-town worker limits |

### Town Configuration

```yaml
# .mab/config.yaml
towns:
  frontend:
    path: /home/user/projects/frontend
    max_workers: 3
    default_roles:
      - developer
      - qa

  backend:
    path: /home/user/projects/backend
    max_workers: 5
    default_roles:
      - developer
      - developer
      - qa
      - tech_lead
      - reviewer

defaults:
  max_workers_per_town: 5
  auto_create_town: true  # create town when spawning in new path
```

### Town Routing

When spawning a worker:
```python
async def spawn_worker(role: str, town: str = "default"):
    # Resolve town
    town_config = get_town(town)
    if not town_config:
        if config.auto_create_town:
            town_config = create_town(town, os.getcwd())
        else:
            raise ValueError(f"Unknown town: {town}")

    # Check limits
    current_count = count_workers(town)
    if current_count >= town_config.max_workers:
        raise ResourceError(f"Town {town} at capacity ({current_count}/{town_config.max_workers})")

    # Spawn in town's directory
    worker = Worker(
        id=generate_worker_id(),
        town_id=town_config.id,
        role=role,
        cwd=town_config.path
    )

    # Start process with correct working directory
    process = await spawn_claude_code(
        cwd=town_config.path,
        prompt_file=f"prompts/{role.upper()}.md"
    )

    worker.pid = process.pid
    save_worker(worker)

    return worker
```

---

## Sequence Diagrams

### Worker Spawn

```
┌───────┐          ┌────────┐          ┌────────┐          ┌────────┐
│  CLI  │          │ Daemon │          │  DB    │          │ Worker │
└───┬───┘          └───┬────┘          └───┬────┘          └───┬────┘
    │                  │                   │                   │
    │  worker.spawn    │                   │                   │
    │  (developer)     │                   │                   │
    │─────────────────▶│                   │                   │
    │                  │                   │                   │
    │                  │ INSERT worker     │                   │
    │                  │ status=starting   │                   │
    │                  │──────────────────▶│                   │
    │                  │                   │                   │
    │                  │ fork process      │                   │
    │                  │──────────────────────────────────────▶│
    │                  │                   │                   │
    │                  │                   │        start      │
    │                  │                   │◀──────────────────│
    │                  │                   │                   │
    │  {worker_id,     │                   │                   │
    │   pid, status}   │                   │                   │
    │◀─────────────────│                   │                   │
    │                  │                   │                   │
    │                  │ heartbeat         │                   │
    │                  │◀──────────────────────────────────────│
    │                  │                   │                   │
    │                  │ UPDATE worker     │                   │
    │                  │ status=running    │                   │
    │                  │──────────────────▶│                   │
    │                  │                   │                   │
```

### Health Check Failure

```
┌────────┐          ┌────────┐          ┌────────┐          ┌────────┐
│ Daemon │          │  DB    │          │Worker 1│          │Worker 2│
└───┬────┘          └───┬────┘          └───┬────┘          └───┬────┘
    │                   │                   │                   │
    │  health check     │                   │                   │
    │  tick             │                   │                   │
    │───────┐           │                   │                   │
    │       │           │                   │                   │
    │◀──────┘           │                   │                   │
    │                   │                   │                   │
    │ check heartbeat   │                   │                   │
    │ Worker 1: stale   │                   │                   │
    │                   │              (crashed)                │
    │                   │                   X                   │
    │                   │                                       │
    │ check heartbeat   │                                       │
    │ Worker 2: fresh   │                                       │
    │◀──────────────────────────────────────────────────────────│
    │                   │                                       │
    │ UPDATE Worker 1   │                                       │
    │ status=failed     │                                       │
    │──────────────────▶│                                       │
    │                   │                                       │
    │ schedule restart  │                                       │
    │ (backoff: 5s)     │                                       │
    │───────┐           │                                       │
    │       │           │                                       │
    │◀──────┘           │                                       │
    │                   │                    ┌────────┐         │
    │ spawn replacement │                    │Worker 1│         │
    │────────────────────────────────────────▶  (new) │         │
    │                   │                    └───┬────┘         │
    │                   │                        │              │
```

### Graceful Shutdown

```
┌───────┐          ┌────────┐          ┌────────┐          ┌────────┐
│  OS   │          │ Daemon │          │Worker 1│          │Worker 2│
└───┬───┘          └───┬────┘          └───┬────┘          └───┬────┘
    │                  │                   │                   │
    │  SIGTERM         │                   │                   │
    │─────────────────▶│                   │                   │
    │                  │                   │                   │
    │                  │ stop accepting    │                   │
    │                  │ new connections   │                   │
    │                  │───────┐           │                   │
    │                  │       │           │                   │
    │                  │◀──────┘           │                   │
    │                  │                   │                   │
    │                  │     SIGTERM       │                   │
    │                  │──────────────────▶│                   │
    │                  │                   │                   │
    │                  │     SIGTERM       │                   │
    │                  │──────────────────────────────────────▶│
    │                  │                   │                   │
    │                  │ finish work       │                   │
    │                  │                   │───────┐           │
    │                  │                   │       │           │
    │                  │     exit(0)       │◀──────┘           │
    │                  │◀──────────────────│                   │
    │                  │                   │                   │
    │                  │                   │    finish work    │
    │                  │                   │                   │───────┐
    │                  │                   │                   │       │
    │                  │     exit(0)       │                   │◀──────┘
    │                  │◀──────────────────────────────────────│
    │                  │                   │                   │
    │                  │ cleanup socket,   │                   │
    │                  │ release lock      │                   │
    │                  │───────┐           │                   │
    │                  │       │           │                   │
    │   exit(0)        │◀──────┘           │                   │
    │◀─────────────────│                   │                   │
    │                  │                   │                   │
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

## Appendix: Configuration Reference

```yaml
# .mab/config.yaml - Full configuration reference

daemon:
  socket: .mab/mab.sock
  pid_file: .mab/daemon.pid
  lock_file: .mab/daemon.lock
  log_file: .mab/daemon.log
  log_level: INFO

database:
  path: .mab/workers.db
  wal_mode: true
  busy_timeout: 5000

health_check:
  enabled: true
  interval: 10
  heartbeat_timeout: 30
  unhealthy_threshold: 3

restart_policy:
  enabled: true
  max_restarts: 5
  backoff_base: 5
  backoff_max: 300
  cooldown_period: 3600

shutdown:
  worker_grace_period: 60
  force_kill_timeout: 10
  drain_connections: true

defaults:
  max_workers_per_town: 5
  auto_create_town: true
  default_roles:
    - developer
    - qa

towns:
  # Populated dynamically or via mab town create
```
