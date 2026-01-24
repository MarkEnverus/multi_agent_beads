# Multi-Agent SDLC System with Beads

## Executive Summary

This document outlines a production-ready architecture for orchestrating multiple AI agents across the software development lifecycle (SDLC), using Beads as the central coordination mechanism. The system enables Developer, QA, Tech Lead, and Manager agents to work concurrently on shared codebases while maintaining proper task handoffs and quality gates.

---

## Part 1: Foundation Research

### 1.1 Beads Framework Analysis

**Source**: https://github.com/steveyegge/beads

Beads provides several critical features that enable multi-agent orchestration:

| Feature | How It Enables Multi-Agent Work |
|---------|--------------------------------|
| **Hash-based IDs** | Prevents collisions when multiple agents create tasks concurrently. IDs like `bd-a1b2` are derived from content hashes, not sequences. |
| **Dependency Graph** | `blocks` relationships control execution order. Agents only see unblocked work via `bd ready`. |
| **Per-workspace Daemon** | Each project gets isolated coordination. Background sync handles concurrent writes safely. |
| **Molecules** | Work graphs (epics with child tasks) model entire workflows. Dependencies determine parallelism vs sequence. |
| **Wisps** | Ephemeral local-only tasks that never sync. Perfect for agent internal work that shouldn't pollute the shared graph. |
| **Labels** | Categorical tagging (`--label qa`, `--label dev`) enables role-based filtering. |
| **State Dimensions** | Key-value pairs (`--state role:qa`) provide metadata without changing status. |

**Key Constraint**: Beads is fundamentally a *coordination protocol*, not an execution engine. It tracks who should do what and when, but agents must be spawned and managed externally.

### 1.2 SDLC Phase Mapping

Traditional SDLC phases and their agent equivalents:

| SDLC Phase | Agent Role | Beads Representation |
|------------|------------|---------------------|
| **Planning** | Manager Agent | Epic creation, priority assignment, dependency planning |
| **Design** | Tech Lead Agent | Architecture beads, blocking relationships for implementation |
| **Implementation** | Developer Agent | Feature/bug beads, PR creation, code changes |
| **Testing** | QA Agent | Test beads, validation, bug discovery |
| **Review** | Code Review Agent | PR review beads, blocking merge until approved |
| **Deployment** | DevOps Agent | Release beads, deployment verification |

### 1.3 Multi-Agent Collaboration Patterns

From research on AutoGen, LangGraph, and CrewAI, three patterns emerge:

1. **Sequential Handoff**: Task A must complete before Task B starts
   - Beads: `bd dep add task-b task-a` (task-a blocks task-b)

2. **Parallel Execution**: Independent tasks run simultaneously
   - Beads: Tasks with no dependency relationships appear in `bd ready` together

3. **Fanout/Fanin**: One task spawns many, then aggregates
   - Beads: Parent epic with multiple child tasks, aggregation task blocked by all children

---

## Part 2: System Architecture

### 2.1 Component Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATION LAYER                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │   Spawner    │  │   Monitor    │  │   Dashboard  │              │
│  │  (Python)    │  │  (Python)    │  │   (Web UI)   │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                 │                 │                       │
│         └─────────────────┴─────────────────┘                       │
│                           │                                         │
│                    ┌──────▼──────┐                                  │
│                    │    Beads    │                                  │
│                    │   (bd CLI)  │                                  │
│                    └──────┬──────┘                                  │
│                           │                                         │
└───────────────────────────┼─────────────────────────────────────────┘
                            │
┌───────────────────────────┼─────────────────────────────────────────┐
│                    AGENT LAYER                                      │
│         ┌─────────────────┴─────────────────┐                       │
│         │                                   │                       │
│  ┌──────▼──────┐  ┌──────────────┐  ┌──────▼──────┐                │
│  │  Developer  │  │     QA       │  │  Tech Lead  │                │
│  │   Agent     │  │   Agent      │  │   Agent     │                │
│  └─────────────┘  └──────────────┘  └─────────────┘                │
│                                                                     │
│  Each agent runs in separate terminal via ralph-loop                │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Agent Role Definitions

#### Developer Agent
**Purpose**: Implement features, fix bugs, write code

**Prompt File**: `prompts/DEVELOPER.md`

**Workflow**:
1. `bd ready --label dev` - find development work
2. Claim bead, implement solution
3. Create PR, run tests
4. Close bead, unblocking downstream QA tasks

**Beads Commands**:
```bash
bd ready --label dev --priority-max 2
bd update <id> --status=in_progress --state role:developer
bd close <id> --reason="PR #123 merged"
```

#### QA Agent
**Purpose**: Test implementations, discover bugs, validate acceptance criteria

**Prompt File**: `prompts/QA.md`

**Workflow**:
1. `bd ready --label qa` - find work needing QA
2. Pull latest code (PR must be merged)
3. Run test suites, manual verification
4. If pass: close bead. If fail: create bug bead blocking original

**Beads Commands**:
```bash
bd ready --label qa
bd create "Bug: <description>" --priority 1 --label bug --label dev
bd dep add <original-feature> <new-bug>  # Bug blocks feature completion
```

#### Tech Lead Agent
**Purpose**: Review architecture, approve designs, unblock complex decisions

**Prompt File**: `prompts/TECH_LEAD.md`

**Workflow**:
1. `bd ready --label architecture` - find design work
2. Review proposed approaches
3. Create implementation beads with dependencies
4. Mentor/unblock developer agents via comments

**Beads Commands**:
```bash
bd create "Implement X" --label dev --parent <epic-id>
bd dep add <impl-task> <design-task>  # Design must complete first
bd comment <id> "Approved approach: use strategy pattern"
```

#### Manager Agent
**Purpose**: Plan sprints, prioritize work, track velocity

**Prompt File**: `prompts/MANAGER.md`

**Workflow**:
1. Review all open beads: `bd list --status=open`
2. Create epics for new initiatives
3. Assign priorities, set dependencies
4. Generate status reports

**Beads Commands**:
```bash
bd create "Epic: Feature X" --priority 0 --type epic
bd update <id> --priority 1  # Reprioritize
bd list --status=in_progress --json | jq  # Status report data
```

#### Code Review Agent
**Purpose**: Review PRs for quality, security, maintainability

**Prompt File**: `prompts/CODE_REVIEWER.md`

**Workflow**:
1. `bd ready --label review` - find PRs needing review
2. Fetch PR diff via `gh pr diff`
3. Analyze code quality, suggest improvements
4. Approve or request changes

### 2.3 Task Flow Model

Standard feature flow through agents:

```
Manager creates Epic
        │
        ▼
Tech Lead creates Design bead (blocked by Epic refinement)
        │
        ▼
Design approved → Developer beads unblocked
        │
        ▼
Developer implements → creates PR
        │
        ▼
Code Review bead unblocked → Review agent reviews
        │
        ▼
Review approved → QA bead unblocked
        │
        ▼
QA validates → closes or creates bug beads
        │
        ▼
All QA passed → Epic closeable
```

**Beads Dependency Graph**:
```bash
# Manager creates epic
bd create "Epic: User Authentication" --priority 1 --type epic

# Tech Lead creates design task, blocked until epic is refined
bd create "Design: Auth flow architecture" --label architecture --parent bd-abc123

# Developer tasks blocked by design
bd create "Implement login endpoint" --label dev --parent bd-abc123
bd dep add bd-def456 bd-design123  # impl blocked by design

# QA tasks blocked by implementation
bd create "QA: Login flow" --label qa --parent bd-abc123
bd dep add bd-qa789 bd-def456  # qa blocked by impl
```

---

## Part 3: Implementation Plan

### Phase 1: Core Infrastructure (Week 1-2)

#### 1.1 Role-Based Prompt System

Create directory structure:
```
multi_agent_beads/
├── prompts/
│   ├── DEVELOPER.md       # Developer agent system prompt
│   ├── QA.md              # QA agent system prompt
│   ├── TECH_LEAD.md       # Tech lead agent system prompt
│   ├── MANAGER.md         # Manager agent system prompt
│   ├── CODE_REVIEWER.md   # Code review agent system prompt
│   └── _COMMON.md         # Shared rules (imported by all)
├── scripts/
│   ├── spawn_agent.py     # Spawn agent with role
│   ├── monitor.py         # Watch all agent activity
│   └── dashboard.py       # Web dashboard (future)
├── config/
│   └── roles.yaml         # Role definitions and permissions
└── PLAN.md                # This document
```

#### 1.2 Common Prompt Template

**`prompts/_COMMON.md`** (imported by all role prompts):
```markdown
## Beads Protocol (ALL AGENTS)

### Session Start
1. `source .env`
2. Log: `log "SESSION_START: <role>"`
3. Find work for your role

### Session End
1. `bd sync`
2. Log: `log "SESSION_END: <bead-id>"`

### Rules
- ONE bead per session
- Never modify files outside your role's scope
- Always cite evidence (file:line, trace IDs)
- Never close bead without meeting acceptance criteria
```

#### 1.3 Agent Spawner Script

**`scripts/spawn_agent.py`**:
```python
#!/usr/bin/env python3
"""Spawn a role-based agent in a new terminal."""

import argparse
import subprocess
import os
from pathlib import Path

ROLES = {
    "developer": {
        "prompt": "prompts/DEVELOPER.md",
        "filter": "--label dev",
        "color": "green",
    },
    "qa": {
        "prompt": "prompts/QA.md",
        "filter": "--label qa",
        "color": "blue",
    },
    "tech_lead": {
        "prompt": "prompts/TECH_LEAD.md",
        "filter": "--label architecture",
        "color": "yellow",
    },
    "manager": {
        "prompt": "prompts/MANAGER.md",
        "filter": "",  # Sees all work
        "color": "purple",
    },
    "reviewer": {
        "prompt": "prompts/CODE_REVIEWER.md",
        "filter": "--label review",
        "color": "cyan",
    },
}

def spawn_agent(role: str, repo_path: str, instance_id: int = 1):
    """Spawn an agent with the specified role."""
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}. Valid: {list(ROLES.keys())}")

    config = ROLES[role]
    prompt_path = Path(__file__).parent.parent / config["prompt"]

    # Build the ralph command with role-specific prompt
    cmd = f'''
    cd {repo_path} && \\
    export AGENT_ROLE={role} && \\
    export AGENT_INSTANCE={instance_id} && \\
    ralph-claude --prompt {prompt_path}
    '''

    # Open in new terminal (macOS)
    subprocess.run([
        "osascript", "-e",
        f'tell app "Terminal" to do script "{cmd}"'
    ])

    print(f"Spawned {role} agent #{instance_id}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=ROLES.keys())
    parser.add_argument("--repo", default=".")
    parser.add_argument("--instance", type=int, default=1)
    args = parser.parse_args()

    spawn_agent(args.role, args.repo, args.instance)
```

### Phase 2: Role Prompts (Week 2-3)

#### 2.1 Developer Agent Prompt

**`prompts/DEVELOPER.md`**:
```markdown
# Developer Agent

## Role
You are a Developer agent. Your job is to implement features, fix bugs, and write clean, tested code.

## Scope
- Implement code changes
- Write unit tests
- Create PRs
- Respond to code review feedback

## NOT Your Scope
- QA testing (that's the QA agent)
- Architecture decisions (that's the Tech Lead)
- Priority decisions (that's the Manager)

## Finding Work
```bash
bd ready --label dev --priority-max 2
```

## Acceptance Criteria
Before closing ANY bead:
1. [ ] Code compiles/passes linting
2. [ ] Unit tests pass
3. [ ] PR created and CI green
4. [ ] PR merged to main

## Handoff Protocol
After completing work:
1. Close your dev bead
2. If bead has downstream QA tasks, they auto-unblock via dependencies
3. Add comment noting PR number for QA agent reference

## Output Artifacts
Every completed bead must produce:
- Git commit(s) with meaningful messages
- PR with description and test plan
- Comment on bead with PR link
```

#### 2.2 QA Agent Prompt

**`prompts/QA.md`**:
```markdown
# QA Agent

## Role
You are a QA agent. Your job is to verify implementations meet acceptance criteria, discover bugs, and ensure quality.

## Scope
- Run test suites
- Verify acceptance criteria
- Create bug reports
- Validate fixes

## NOT Your Scope
- Writing production code (that's Developer)
- Deciding what to build (that's Manager)
- Approving architecture (that's Tech Lead)

## Finding Work
```bash
bd ready --label qa
```

## Verification Protocol
For each QA bead:
1. Pull latest main (PR should already be merged)
2. Run full test suite
3. Manual verification of acceptance criteria
4. Check for edge cases

## Bug Discovery
When you find a bug:
```bash
# Create bug bead
bd create "Bug: <clear description>" --priority 1 --label bug --label dev

# Add reproduction steps in description
bd update <bug-id> --description "$(cat <<'EOF'
## Steps to Reproduce
1. ...
2. ...

## Expected
...

## Actual
...

## Evidence
<file:line citations or screenshots>
EOF
)"

# Block the feature bead on the bug
bd dep add <original-feature-id> <bug-id>
```

## Closing Protocol
Only close QA bead when:
1. [ ] All acceptance criteria verified
2. [ ] Test suite passes
3. [ ] No critical bugs found (or bugs are tracked in separate beads)
```

#### 2.3 Tech Lead Agent Prompt

**`prompts/TECH_LEAD.md`**:
```markdown
# Tech Lead Agent

## Role
You are a Tech Lead agent. Your job is to make architectural decisions, review designs, and unblock technical complexity.

## Scope
- Review and approve architecture
- Create implementation breakdown
- Unblock complex technical decisions
- Mentor via bead comments

## Finding Work
```bash
bd ready --label architecture
```

## Design Approval Protocol
1. Read the proposed design/epic
2. Evaluate against:
   - Existing patterns in codebase
   - Scalability concerns
   - Maintainability
   - Security implications
3. Either approve or request changes via comment

## Creating Implementation Plan
When design is approved:
```bash
# Create child implementation beads
bd create "Implement: Component A" --label dev --parent <epic-id>
bd create "Implement: Component B" --label dev --parent <epic-id>

# Set dependencies if order matters
bd dep add <component-b> <component-a>

# Add QA beads blocked by implementation
bd create "QA: Component A" --label qa --parent <epic-id>
bd dep add <qa-a> <impl-a>
```

## Evidence Requirements
All design decisions must cite:
- Existing patterns: `<file>:<line>` references
- External best practices: links to documentation
- Trade-off analysis: pros/cons of alternatives
```

### Phase 3: Orchestration Layer (Week 3-4)

#### 3.1 Multi-Agent Monitor

**`scripts/monitor.py`**:
```python
#!/usr/bin/env python3
"""Monitor all agent activity across the system."""

import subprocess
import json
import time
from datetime import datetime
from pathlib import Path

def get_active_beads():
    """Get all in-progress beads."""
    result = subprocess.run(
        ["bd", "list", "--status=in_progress", "--json"],
        capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.stdout else []

def get_agent_logs(log_file: Path, lines: int = 20):
    """Get recent log entries."""
    result = subprocess.run(
        ["tail", f"-{lines}", str(log_file)],
        capture_output=True, text=True
    )
    return result.stdout.splitlines()

def display_status():
    """Display current system status."""
    print(f"\n{'='*60}")
    print(f"MULTI-AGENT STATUS - {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    beads = get_active_beads()

    # Group by role
    by_role = {}
    for bead in beads:
        role = bead.get("state", {}).get("role", "unknown")
        by_role.setdefault(role, []).append(bead)

    for role, role_beads in by_role.items():
        print(f"\n[{role.upper()}]")
        for bead in role_beads:
            print(f"  {bead['id']}: {bead['title'][:50]}")

    # Show recent activity
    print(f"\n{'─'*60}")
    print("RECENT ACTIVITY (claude.log)")
    print(f"{'─'*60}")

    log_file = Path("claude.log")
    if log_file.exists():
        for line in get_agent_logs(log_file, 10):
            print(f"  {line}")

def main():
    """Run monitor loop."""
    while True:
        display_status()
        time.sleep(10)

if __name__ == "__main__":
    main()
```

#### 3.2 Workflow Templates (Molecules)

Create reusable workflow templates:

**`templates/feature_workflow.yaml`**:
```yaml
# Standard Feature Workflow
# Usage: bd mol pour feature_workflow --title "My Feature"

name: feature_workflow
description: Standard feature implementation with design, dev, review, and QA

tasks:
  - id: design
    title: "Design: ${title}"
    labels: [architecture]
    description: |
      ## Objective
      Design the architecture for: ${title}

      ## Deliverables
      - Architecture decision record
      - Component breakdown
      - API contracts (if applicable)

  - id: implement
    title: "Implement: ${title}"
    labels: [dev]
    blocked_by: [design]
    description: |
      ## Objective
      Implement the feature as designed.

      ## Acceptance Criteria
      - [ ] Code passes linting
      - [ ] Unit tests written and passing
      - [ ] PR created

  - id: review
    title: "Review: ${title}"
    labels: [review]
    blocked_by: [implement]
    description: |
      ## Objective
      Code review the implementation PR.

      ## Checklist
      - [ ] Code quality acceptable
      - [ ] Tests adequate
      - [ ] No security issues

  - id: qa
    title: "QA: ${title}"
    labels: [qa]
    blocked_by: [review]
    description: |
      ## Objective
      Validate the feature works as specified.

      ## Verification
      - [ ] Acceptance criteria met
      - [ ] Edge cases tested
      - [ ] No regressions
```

### Phase 4: Dashboard & Visibility (Week 4-5)

#### 4.1 Web Dashboard (FastAPI + HTMX)

**`scripts/dashboard.py`**:
```python
#!/usr/bin/env python3
"""Web dashboard for multi-agent monitoring."""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import subprocess
import json

app = FastAPI(title="Multi-Agent Dashboard")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard view."""
    # Get beads data
    result = subprocess.run(
        ["bd", "list", "--json"],
        capture_output=True, text=True
    )
    beads = json.loads(result.stdout) if result.stdout else []

    # Categorize
    by_status = {"open": [], "in_progress": [], "closed": []}
    for bead in beads:
        status = bead.get("status", "open")
        by_status.setdefault(status, []).append(bead)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "beads": by_status,
            "total": len(beads),
        }
    )

@app.get("/api/beads")
async def get_beads():
    """API endpoint for beads data."""
    result = subprocess.run(
        ["bd", "list", "--json"],
        capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.stdout else []

@app.get("/api/logs")
async def get_logs(lines: int = 50):
    """Get recent log entries."""
    result = subprocess.run(
        ["tail", f"-{lines}", "claude.log"],
        capture_output=True, text=True
    )
    return {"logs": result.stdout.splitlines()}
```

#### 4.2 Dashboard HTML Template

**`templates/dashboard.html`**:
```html
<!DOCTYPE html>
<html>
<head>
    <title>Multi-Agent Dashboard</title>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        body { font-family: monospace; background: #1a1a1a; color: #eee; padding: 20px; }
        .board { display: flex; gap: 20px; }
        .column { flex: 1; background: #2a2a2a; padding: 15px; border-radius: 8px; }
        .column h2 { margin-top: 0; border-bottom: 2px solid #444; padding-bottom: 10px; }
        .bead { background: #333; padding: 10px; margin: 10px 0; border-radius: 4px; }
        .bead-id { color: #888; font-size: 0.8em; }
        .label { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; margin: 2px; }
        .label-dev { background: #2d5a27; }
        .label-qa { background: #1e4a6d; }
        .label-architecture { background: #6d4c1e; }
        .label-review { background: #4c1e6d; }
        .logs { background: #111; padding: 15px; margin-top: 20px; border-radius: 8px; max-height: 300px; overflow-y: auto; }
        .log-line { font-size: 0.85em; padding: 2px 0; }
    </style>
</head>
<body>
    <h1>Multi-Agent Dashboard</h1>
    <p>Total beads: {{ total }}</p>

    <div class="board">
        <div class="column">
            <h2>Ready ({{ beads.open|length }})</h2>
            {% for bead in beads.open %}
            <div class="bead">
                <div class="bead-id">{{ bead.id }}</div>
                <div>{{ bead.title }}</div>
                {% for label in bead.labels %}
                <span class="label label-{{ label }}">{{ label }}</span>
                {% endfor %}
            </div>
            {% endfor %}
        </div>

        <div class="column">
            <h2>In Progress ({{ beads.in_progress|length }})</h2>
            {% for bead in beads.in_progress %}
            <div class="bead">
                <div class="bead-id">{{ bead.id }}</div>
                <div>{{ bead.title }}</div>
                {% for label in bead.labels %}
                <span class="label label-{{ label }}">{{ label }}</span>
                {% endfor %}
            </div>
            {% endfor %}
        </div>

        <div class="column">
            <h2>Done ({{ beads.closed|length }})</h2>
            {% for bead in beads.closed[:10] %}
            <div class="bead">
                <div class="bead-id">{{ bead.id }}</div>
                <div>{{ bead.title }}</div>
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="logs" hx-get="/api/logs" hx-trigger="every 5s" hx-swap="innerHTML">
        <h3>Live Logs</h3>
        <!-- Logs will be loaded here -->
    </div>
</body>
</html>
```

---

## Part 4: Operations Guide

### 4.1 Starting a Multi-Agent Session

```bash
# 1. Navigate to your repo
cd /path/to/your/repo

# 2. Ensure Beads is initialized
bd info || bd init

# 3. Start the dashboard (optional, in background)
python scripts/dashboard.py &

# 4. Spawn agents for your workload
python scripts/spawn_agent.py developer --instance 1
python scripts/spawn_agent.py developer --instance 2  # Multiple devs
python scripts/spawn_agent.py qa --instance 1
python scripts/spawn_agent.py tech_lead --instance 1

# 5. Monitor activity
python scripts/monitor.py
# OR
tail -f claude.log | grep --line-buffered "SESSION\|CLAIM\|CLOSE"
```

### 4.2 Creating Work for Agents

```bash
# Manager creates an epic
bd create "Epic: Implement User Authentication" --priority 1 --type epic

# Tech Lead creates design task
bd create "Design: Auth architecture" --label architecture --parent <epic-id>

# After design is approved, create dev tasks
bd create "Implement: Login endpoint" --label dev --parent <epic-id>
bd dep add <impl-id> <design-id>

# Create QA tasks blocked by implementation
bd create "QA: Login flow" --label qa --parent <epic-id>
bd dep add <qa-id> <impl-id>
```

### 4.3 Monitoring & Troubleshooting

```bash
# See all active work
bd list --status=in_progress

# Check what each role can work on
bd ready --label dev
bd ready --label qa
bd ready --label architecture

# Check blocked work and why
bd blocked

# See dependency graph
bd deps <bead-id>

# Force sync (if agents seem out of sync)
bd sync --force
```

### 4.4 Handling Conflicts

When multiple agents try to modify the same bead:

1. **Beads handles it**: Hash-based IDs and SQLite transactions prevent data corruption
2. **Last write wins**: For status changes, the latest update takes effect
3. **Comments are safe**: Multiple agents can comment without conflict
4. **Dependencies are additive**: Adding deps is safe from multiple sources

---

## Part 5: Scaling Considerations

### 5.1 Scaling Agents

| Scale Level | Configuration |
|-------------|---------------|
| **Small** (1-3 agents) | Single terminal per agent, shared log file |
| **Medium** (4-10 agents) | Separate log files per role, dashboard monitoring |
| **Large** (10+ agents) | Multiple repos with `bd` routing, centralized logging |

### 5.2 Performance Limits

From Beads documentation:
- SQLite handles thousands of beads efficiently
- Daemon batches writes with 5-second debounce
- `bd ready` with blocked-issues cache: ~29ms on 10K issues
- Git sync: depends on repo size, but JSONL diffs are efficient

### 5.3 Future Enhancements

1. **Agent Pools**: Pre-spawn agent pools for instant work assignment
2. **Priority Queues**: Sophisticated work distribution based on agent velocity
3. **Metrics Collection**: Track cycle time, throughput, bug rates
4. **Slack/Teams Integration**: Notifications for blocked work or completed epics
5. **AI Manager**: Let an AI agent do sprint planning and priority assignment

---

## Part 6: Risk Mitigation

### 6.1 Known Risks

| Risk | Mitigation |
|------|------------|
| **Agents conflict on same file** | Use granular beads - one component per task |
| **Agent hallucinates completion** | Strict acceptance criteria with evidence requirements |
| **Deadlock (circular deps)** | Beads detects cycles on `bd dep add` |
| **Agent runs too long** | Set session time limits in ralph-loop |
| **Lost work (agent crashes)** | Beads tracks partial state; `bd sync` on startup |

### 6.2 Rollback Strategy

If multi-agent work goes wrong:
```bash
# 1. Kill all agent terminals
pkill -f ralph-claude

# 2. Check beads state
bd list --status=in_progress

# 3. Reset stuck beads
bd update <id> --status=open

# 4. Git reset if needed
git status
git reset --hard origin/main  # CAREFUL: loses local changes

# 5. Restart fresh
bd sync
```

---

## Appendix A: File Manifest

```
multi_agent_beads/
├── PLAN.md                    # This document
├── prompts/
│   ├── _COMMON.md            # Shared rules for all agents
│   ├── DEVELOPER.md          # Developer agent prompt
│   ├── QA.md                 # QA agent prompt
│   ├── TECH_LEAD.md          # Tech Lead agent prompt
│   ├── MANAGER.md            # Manager agent prompt
│   └── CODE_REVIEWER.md      # Code Review agent prompt
├── scripts/
│   ├── spawn_agent.py        # Spawn agent with role
│   ├── monitor.py            # Terminal-based monitoring
│   └── dashboard.py          # Web dashboard
├── templates/
│   ├── dashboard.html        # Dashboard UI
│   └── feature_workflow.yaml # Workflow template
└── config/
    └── roles.yaml            # Role definitions
```

## Appendix B: References

1. **Beads Framework**: https://github.com/steveyegge/beads
   - Architecture: Hash-based IDs, SQLite + JSONL + Git
   - CLI Reference: `bd ready`, `bd dep`, `bd list`, `bd sync`
   - Molecules: Work graphs with dependency-driven execution

2. **Multi-Agent Research**:
   - AutoGen (Microsoft): Conversational multi-agent framework
   - LangGraph (LangChain): Graph-based orchestration with persistence
   - CrewAI: Role-playing agents with Flows + Crews pattern

3. **SDLC Mapping**:
   - Planning → Manager Agent
   - Design → Tech Lead Agent
   - Implementation → Developer Agent
   - Testing → QA Agent
   - Review → Code Review Agent

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-01-24 | Claude Opus 4.5 | Initial creation after deep research |
| 1.1 | 2025-01-24 | QA Review Agent | Added QA review findings and action items |

---

## Appendix C: QA Review Findings

**Review Date**: 2025-01-24
**Verdict**: APPROVED WITH CHANGES

### Strengths Identified

- Clear separation of concerns between agent roles
- Dependency-driven workflow is architecturally sound
- Evidence-based acceptance criteria reduces hallucination risk
- Phased 5-week implementation is realistic
- Comprehensive monitoring (terminal + web dashboard)
- Correct use of Beads' hash-based IDs for concurrent work
- Rollback strategy included

### Critical Issues (Must Address)

#### C.1 CLI Syntax Corrections Needed

Some Beads commands in this document use placeholder syntax. Before implementation, validate exact syntax against `bd --help` for each command:

| Area | Action Required |
|------|-----------------|
| Label filtering | Verify `bd ready` label filter syntax or use `bd list` with filters |
| State setting | Confirm state dimension syntax on `bd update` |
| Type flags | Verify short vs long flag format (`-t` vs `--type`) |

#### C.2 Agent Spawning Infrastructure

The `ralph-loop` / `ralph-claude` references are specific to your environment. For implementation:
- Document your `ralph-claude` fish function
- Or replace with standard Claude Code invocation: `claude --prompt-file <path>`

#### C.3 Workflow Template Format

The YAML workflow template is conceptual. For actual Beads molecules:
- Review Beads MOLECULES.md for exact proto/formula syntax
- May need JSON format instead of YAML

### Recommendations

1. **Add command validation wrapper** - catch syntax errors before agent execution
2. **Define explicit state machine** - IDLE → FINDING_WORK → CLAIMED → WORKING → COMPLETED/BLOCKED/FAILED
3. **Add heartbeat monitoring** - detect stuck agents via inactivity timeout
4. **Specify conflict resolution rules** - what happens with simultaneous claims?
5. **Add agent session ID** to all bd operations for audit trail
6. **Create handoff protocol document** - what info must be in bead for next role?
7. **Add rate limiting** - prevent tight-loop polling of `bd ready`
8. **Consider single-bead-per-agent enforcement** - track active assignments

### Open Questions for Implementation

1. How does your `ralph-loop` fish function work? (needed for spawn script)
2. What happens when agent session terminates unexpectedly?
3. How to prevent two developers working on same file simultaneously?
4. Should Manager agent run continuously or on-demand?
5. Is there a test environment to validate agent behavior safely?

### Final Assessment

The plan is **~70% production-ready**. The remaining 30% consists of:
- CLI command validation against actual Beads behavior
- Agent spawning infrastructure documentation
- Error handling and recovery procedures

These are addressable without major architectural changes.
