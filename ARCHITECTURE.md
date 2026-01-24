# Architecture

Technical architecture documentation for the Multi-Agent Beads System.

## Table of Contents

1. [System Overview](#system-overview)
2. [Beads Integration](#beads-integration)
3. [Agent Roles](#agent-roles)
4. [Dashboard](#dashboard)
5. [Workflow](#workflow)
6. [Scaling](#scaling)

---

## System Overview

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATION LAYER                               │
│                                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐             │
│  │  spawn_agent   │  │    monitor     │  │   Dashboard    │             │
│  │     .py        │  │      .py       │  │   (FastAPI)    │             │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘             │
│          │                   │                   │                       │
│          └───────────────────┼───────────────────┘                       │
│                              │                                           │
│                     ┌────────▼────────┐                                  │
│                     │   Beads (bd)    │                                  │
│                     │   CLI + SQLite  │                                  │
│                     └────────┬────────┘                                  │
│                              │                                           │
└──────────────────────────────┼───────────────────────────────────────────┘
                               │
                               │ subprocess calls
                               │
┌──────────────────────────────┼───────────────────────────────────────────┐
│                       AGENT LAYER                                         │
│                              │                                           │
│    ┌─────────────────────────┼─────────────────────────────┐            │
│    │                         │                             │            │
│    ▼                         ▼                             ▼            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │Developer │  │    QA    │  │Tech Lead │  │ Manager  │  │ Reviewer │  │
│  │  Agent   │  │  Agent   │  │  Agent   │  │  Agent   │  │  Agent   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │             │             │             │             │         │
│       └─────────────┴─────────────┴─────────────┴─────────────┘         │
│                              │                                           │
│                     ┌────────▼────────┐                                  │
│                     │   claude.log    │                                  │
│                     │  (shared log)   │                                  │
│                     └─────────────────┘                                  │
│                                                                          │
│    Each agent runs in its own terminal session with Claude Code CLI      │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Summary

| Component | Technology | Purpose |
|-----------|------------|---------|
| Dashboard | FastAPI + Jinja2 + HTMX | Real-time web UI for monitoring |
| Beads (bd) | CLI + SQLite | Issue tracking and dependencies |
| Agents | Claude Code CLI | Autonomous work execution |
| Spawner | Python + AppleScript | Launch agents in terminal windows |
| Monitor | Python | Terminal-based activity viewer |

### Data Flow

```
┌────────────┐     ┌────────────┐     ┌────────────┐
│   Agent    │────▶│  bd CLI    │────▶│  SQLite    │
│  (Claude)  │     │            │     │  (beads.db)│
└────────────┘     └────────────┘     └────────────┘
      │                                     │
      │                                     │
      ▼                                     ▼
┌────────────┐     ┌────────────┐     ┌────────────┐
│ claude.log │◀────│  Dashboard │◀────│ bd --json  │
│            │     │  (FastAPI) │     │  commands  │
└────────────┘     └────────────┘     └────────────┘
```

1. **Agents** execute `bd` commands to claim/update/close issues
2. **Beads CLI** persists state to SQLite database
3. **Dashboard** polls `bd` commands with `--json` flag for data
4. **Agents** write structured logs to shared `claude.log`
5. **Dashboard** parses logs for real-time agent status

---

## Beads Integration

### How Beads Works

[Beads](https://github.com/steveyegge/beads) is a git-native issue tracking system. It stores issues in a SQLite database (`.beads/beads.db`) and syncs to JSONL files for git portability.

```
.beads/
├── beads.db          # Primary SQLite database
├── beads.db-wal      # Write-ahead log
├── issues.jsonl      # JSONL export for git
├── config.yaml       # Repository configuration
├── daemon.pid        # Background daemon process
└── bd.sock           # Unix socket for daemon communication
```

### Key Commands Used

```bash
# Finding work
bd ready              # List issues with no blockers
bd ready -l dev       # Filter by label

# Issue lifecycle
bd update <id> --status=in_progress   # Claim work
bd show <id>                          # View details
bd close <id> --reason="..."          # Complete work

# Dependencies
bd dep add <child> <parent>    # Child depends on parent
bd blocked                     # Show blocked issues

# Sync
bd sync --flush-only           # Export to JSONL
```

### Dependency Patterns

Beads supports blocking dependencies that control work order:

```
┌───────────────┐      depends on      ┌───────────────┐
│    Feature    │ ──────────────────▶  │    Design     │
│   (blocked)   │                      │  (must first) │
└───────────────┘                      └───────────────┘

# Created via:
bd dep add <feature-id> <design-id>
```

**Dependency Rules:**
- A blocked issue cannot be claimed until blockers are closed
- `bd ready` only shows issues with no open blockers
- Closing an issue automatically unblocks dependents

### Dashboard Integration

The dashboard reads bead state via subprocess calls:

```python
def _run_bd_command(args: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        ["bd", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0, result.stdout
```

JSON output is parsed for display:

```python
success, output = _run_bd_command(["list", "--json", "--limit", "0"])
beads = json.loads(output) if success else []
```

---

## Agent Roles

### Role Architecture

```
                    ┌───────────────┐
                    │    Manager    │
                    │  (Prioritize) │
                    └───────┬───────┘
                            │ creates epics & priorities
                            ▼
                    ┌───────────────┐
                    │  Tech Lead    │
                    │  (Architect)  │
                    └───────┬───────┘
                            │ designs & breaks down
                            ▼
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
      ┌───────────────┐           ┌───────────────┐
      │   Developer   │           │      QA       │
      │  (Implement)  │           │   (Verify)    │
      └───────┬───────┘           └───────┬───────┘
              │ creates PRs               │ creates bugs
              ▼                           │
      ┌───────────────┐                   │
      │ Code Reviewer │◀──────────────────┘
      │   (Approve)   │
      └───────────────┘
```

### Role Definitions

| Role | Label Filter | Primary Responsibility | Does Not |
|------|--------------|------------------------|----------|
| **Developer** | `-l dev` | Write production code, create PRs | Write tests, make architecture decisions |
| **QA** | `-l qa` | Verify acceptance criteria, create bug beads | Write production code |
| **Tech Lead** | `-l architecture` | Design systems, break down tasks | Prioritize work, manage epics |
| **Manager** | (all) | Create epics, set priorities, assign labels | Write code, make technical decisions |
| **Code Reviewer** | `-l review` | Review PRs, approve/request changes | Write production code |

### Agent Prompt Structure

Each agent type has a dedicated prompt file in `prompts/`:

```
prompts/
├── _COMMON.md        # Shared rules and logging protocol
├── DEVELOPER.md      # Developer-specific instructions
├── QA.md             # QA-specific instructions
├── TECH_LEAD.md      # Tech Lead instructions
├── MANAGER.md        # Manager instructions
└── CODE_REVIEWER.md  # Code Reviewer instructions
```

### Agent Session Protocol

Every agent follows the same session structure:

```
1. SESSION_START     → Log start, initialize
2. bd ready -l <X>   → Find work for role
3. CLAIM             → Update status to in_progress
4. READ              → Understand requirements
5. WORK_START        → Begin implementation
6. [role-specific work]
7. TESTS/CI          → Quality verification
8. PR_CREATE/MERGE   → Code delivery (if applicable)
9. CLOSE             → Mark bead complete
10. SESSION_END      → Clean exit
```

### Inter-Agent Communication

Agents communicate via beads (not directly):

```
Developer creates PR  →  Code Reviewer reviews  →  Developer merges
     │                         │                        │
     ▼                         ▼                        ▼
  [bead: PR ready]      [bead: needs review]    [bead: closed]
```

---

## Dashboard

### Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Backend | FastAPI | REST API and HTML endpoints |
| Templates | Jinja2 | Server-side HTML rendering |
| Frontend | HTMX | Dynamic partial updates |
| Styling | TailwindCSS | Utility-first CSS |
| Graphs | Mermaid.js | Dependency visualization |

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       FastAPI Application                    │
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │   Main Routes   │  │   API Routes    │                  │
│  │                 │  │                 │                  │
│  │  GET /          │  │  /api/beads/*   │                  │
│  │  GET /partials/*│  │  /api/agents/*  │                  │
│  │                 │  │  /api/logs/*    │                  │
│  └────────┬────────┘  └────────┬────────┘                  │
│           │                    │                            │
│           └────────┬───────────┘                            │
│                    │                                        │
│           ┌────────▼────────┐                               │
│           │  bd subprocess  │                               │
│           │    (--json)     │                               │
│           └────────┬────────┘                               │
│                    │                                        │
└────────────────────┼────────────────────────────────────────┘
                     │
              ┌──────▼──────┐
              │ .beads/     │
              │ beads.db    │
              └─────────────┘
```

### API Endpoints

**Beads API (`/api/beads/`)**
```
GET  /api/beads                    # List all beads (filterable)
GET  /api/beads/ready              # List ready beads (no blockers)
GET  /api/beads/in-progress        # List in-progress beads
GET  /api/beads/{bead_id}          # Get single bead
POST /api/beads                    # Create new bead
```

**Agents API (`/api/agents/`)**
```
GET  /api/agents                   # List active agents
GET  /api/agents/{role}            # Filter by role
```

**Logs API (`/api/logs/`)**
```
GET  /api/logs/recent              # Recent log entries
GET  /api/logs/stream              # SSE live stream
```

### HTMX Partials

The dashboard uses HTMX for dynamic updates without full page reloads:

```
GET /partials/kanban     → Kanban board columns
GET /partials/agents     → Agent sidebar
GET /partials/depgraph   → Dependency graph (Mermaid)
GET /partials/beads/{id} → Bead detail modal
```

### Data Flow

```
┌─────────────┐   HTMX polling   ┌─────────────┐   subprocess   ┌─────────────┐
│   Browser   │ ──────────────▶  │   FastAPI   │ ────────────▶  │     bd      │
│   (HTMX)    │                  │   Server    │                │    CLI      │
└─────────────┘                  └─────────────┘                └─────────────┘
      ▲                                │                              │
      │                                │                              │
      │    SSE stream                  │  parse JSON                  │
      │◀───────────────────────────────┤                              ▼
      │                                │                        ┌─────────────┐
      │                                └───────────────────────▶│  beads.db   │
      │                                                         └─────────────┘
      │
      │    Log entries
      │◀───────────────────────────────────────────────────────┐
                                                               │
                                                         ┌─────────────┐
                                                         │ claude.log  │
                                                         └─────────────┘
```

---

## Workflow

### Task Lifecycle

```
┌─────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────┐
│  OPEN   │────▶│ IN_PROGRESS │────▶│     PR      │────▶│ CLOSED  │
│         │     │             │     │   REVIEW    │     │         │
└─────────┘     └─────────────┘     └─────────────┘     └─────────┘
     │                │                    │                 │
     │                │                    │                 │
  bd create      bd update            gh pr create      bd close
                 --status=             + merge
                 in_progress
```

### Handoff Patterns

**Feature Development Flow:**

```
Manager                Tech Lead              Developer           QA
   │                      │                      │                │
   │  1. Create epic      │                      │                │
   ├─────────────────────▶│                      │                │
   │                      │  2. Break into tasks │                │
   │                      ├─────────────────────▶│                │
   │                      │                      │  3. Implement  │
   │                      │                      ├───────────────▶│
   │                      │                      │                │
   │                      │                      │  4. Create bug │
   │                      │                      │◀───────────────┤
   │                      │                      │                │
   │                      │                      │  5. Fix & close│
   │                      │                      ├───────────────▶│
   │                      │                      │                │
```

**Code Review Flow:**

```
Developer              Code Reviewer            Git
    │                       │                    │
    │  1. Create PR         │                    │
    ├───────────────────────┼───────────────────▶│
    │                       │                    │
    │  2. Review PR         │                    │
    │◀──────────────────────┤                    │
    │                       │                    │
    │  3. Address comments  │                    │
    ├───────────────────────┼───────────────────▶│
    │                       │                    │
    │  4. Approve           │                    │
    │◀──────────────────────┤                    │
    │                       │                    │
    │  5. Merge             │                    │
    ├───────────────────────┼───────────────────▶│
    │                       │                    │
```

### Logging Protocol

All agents write to a shared `claude.log` file:

```
Format: [TIMESTAMP] [PID] EVENT_TYPE: details

Events:
  SESSION_START          Session begins
  SESSION_END: <id>      Session ends
  CLAIM: <id> - <title>  Bead claimed
  READ: <id>             Bead details read
  WORK_START: <desc>     Work begins
  TESTS: <desc>          Tests running
  TESTS_PASSED           Tests succeeded
  TESTS_FAILED: <n>      Tests failed
  PR_CREATE: <title>     PR being created
  PR_CREATED: #<n>       PR created
  PR_MERGED: #<n>        PR merged
  CI: PASSED/FAILED      CI status
  CLOSE: <id> - <reason> Bead closed
  BLOCKED: <id> - <why>  Agent blocked
  ERROR: <desc>          Error occurred
```

Example log output:
```
[2024-01-24 15:32:01] [12345] SESSION_START
[2024-01-24 15:32:05] [12345] CLAIM: multi_agent_beads-abc - Add login feature
[2024-01-24 15:32:06] [12345] READ: multi_agent_beads-abc
[2024-01-24 15:32:10] [12345] WORK_START: Implementing OAuth integration
[2024-01-24 15:45:30] [12345] TESTS: running pytest
[2024-01-24 15:45:45] [12345] TESTS_PASSED
[2024-01-24 15:46:00] [12345] PR_CREATE: feat(auth): add OAuth login
[2024-01-24 15:46:15] [12345] PR_CREATED: #42
[2024-01-24 15:50:00] [12345] CI: PASSED
[2024-01-24 15:52:00] [12345] PR_MERGED: #42
[2024-01-24 15:52:05] [12345] CLOSE: multi_agent_beads-abc - PR #42 merged
[2024-01-24 15:52:10] [12345] SESSION_END: multi_agent_beads-abc
```

---

## Scaling

### Multiple Agents

The system supports running multiple agents concurrently:

```
┌─────────────────────────────────────────────────────────┐
│                    Terminal Windows                      │
│                                                         │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐           │
│  │ Developer │  │ Developer │  │    QA     │           │
│  │ Instance 1│  │ Instance 2│  │ Instance 1│           │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘           │
│        │              │              │                  │
│        └──────────────┼──────────────┘                  │
│                       │                                 │
│               ┌───────▼───────┐                         │
│               │    Beads      │                         │
│               │   (shared)    │                         │
│               └───────────────┘                         │
└─────────────────────────────────────────────────────────┘
```

**Spawning multiple agents:**
```bash
# Different roles
python scripts/spawn_agent.py developer
python scripts/spawn_agent.py qa
python scripts/spawn_agent.py tech_lead

# Multiple instances of same role
python scripts/spawn_agent.py developer --instance 1
python scripts/spawn_agent.py developer --instance 2
python scripts/spawn_agent.py developer --instance 3
```

### Concurrency Control

Beads handles concurrency via SQLite:
- **Claim-based locking**: Setting `status=in_progress` claims a bead
- **No double-claiming**: Agents skip beads already in progress
- **Write-ahead logging**: SQLite WAL mode for concurrent reads

### Multiple Repositories

For multiple repositories, each repo maintains its own:
- `.beads/` directory with independent database
- `claude.log` file
- Agent sessions

**Cross-repo coordination** (future consideration):
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Repo A    │     │   Repo B    │     │   Repo C    │
│  .beads/    │     │  .beads/    │     │  .beads/    │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────▼──────┐
                    │ Aggregator  │
                    │  Dashboard  │
                    └─────────────┘
```

### Resource Considerations

**Per-agent resources:**
- Each Claude Code CLI session uses ~500MB-1GB RAM
- Each maintains its own conversation context
- API rate limits apply per session

**Recommended limits:**
- 3-5 concurrent agents per developer machine
- 1 agent per role for small projects
- Scale horizontally with more machines for larger projects

---

## Future Considerations

### Planned Improvements

1. **Linux/Windows support** for spawn_agent.py (currently macOS only)
2. **Cross-repo aggregation** dashboard
3. **Agent health monitoring** with automatic restart
4. **Configurable agent prompts** via YAML
5. **Integration with CI/CD** pipelines

### Extension Points

| Extension | Description |
|-----------|-------------|
| Custom roles | Add new prompts in `prompts/` directory |
| Dashboard views | Add HTMX partials and routes |
| Beads hooks | Use bd's hook system for automation |
| Log analysis | Parse claude.log for metrics/alerts |

---

## References

- [Beads CLI](https://github.com/steveyegge/beads) - Issue tracking system
- [Claude Code](https://github.com/anthropics/claude-code) - CLI interface
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [HTMX](https://htmx.org/) - HTML-first approach to dynamic content
- [Mermaid](https://mermaid.js.org/) - Diagram generation
