# Usage Guide

Comprehensive guide to using the Multi-Agent Beads System.

## Table of Contents

- [Installation](#installation)
- [Configuration](#configuration)
- [Running Agents](#running-agents)
- [Creating Work](#creating-work)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)
- [Examples](#examples)

---

## Installation

### Prerequisites

Before setting up the system, ensure you have:

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python | 3.11+ | Runtime for dashboard and scripts |
| [uv](https://docs.astral.sh/uv/) | Latest | Python package manager |
| [Beads](https://github.com/steveyegge/beads) | Latest | Work tracking CLI (`bd` command) |
| [Claude Code](https://github.com/anthropics/claude-code) | Latest | Agent runtime (`claude` command) |

### Installation Commands

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Claude Code (if not already installed)
npm install -g @anthropic-ai/claude-code

# Install Beads CLI
pip install beads-cli  # or follow beads installation guide
```

### Setup Steps

1. **Clone the repository**

   ```bash
   git clone <repo-url>
   cd multi_agent_beads
   ```

2. **Run the setup script**

   ```bash
   ./scripts/setup.sh
   ```

   This script will:
   - Verify all prerequisites are installed
   - Create a Python virtual environment
   - Install dependencies via `uv sync`
   - Initialize beads (creates `.beads/` directory)
   - Verify installation

3. **Alternatively, manual setup**

   ```bash
   # Create virtual environment and install dependencies
   uv sync

   # Initialize beads (if not already done)
   bd init
   ```

### Verification

After setup, verify everything works:

```bash
# Check beads is working
bd stats

# Run tests
uv run pytest

# Start dashboard (should open at http://127.0.0.1:8000)
uv run python -m dashboard.app

# Verify agent spawning (opens a new terminal)
python scripts/spawn_agent.py developer
```

---

## Configuration

### roles.yaml

Located at `config/roles.yaml`, this file defines agent roles and their properties:

```yaml
roles:
  developer:
    prompt: prompts/DEVELOPER.md    # Role-specific instructions
    label_filter: dev               # Only sees 'dev' labeled work
    color: green                    # Terminal color for this role
    max_instances: 3                # Max concurrent agents of this role

  qa:
    prompt: prompts/QA.md
    label_filter: qa
    color: blue
    max_instances: 2

  tech_lead:
    prompt: prompts/TECH_LEAD.md
    label_filter: architecture
    color: yellow
    max_instances: 1

  manager:
    prompt: prompts/MANAGER.md
    label_filter: null              # Sees ALL work items
    color: purple
    max_instances: 1

  reviewer:
    prompt: prompts/CODE_REVIEWER.md
    label_filter: review
    color: cyan
    max_instances: 2
```

**Key concepts:**

- `label_filter` determines what work an agent sees when running `bd ready -l <filter>`
- `max_instances` limits how many agents of each role can run concurrently
- `prompt` points to the role-specific behavior instructions

### labels.yaml

Located at `config/labels.yaml`, this file defines available labels:

```yaml
labels:
  # Role labels (used for filtering work)
  dev: "Development work"
  qa: "QA/testing work"
  architecture: "Design/architecture"
  review: "Code review"

  # Type labels (categorization)
  bug: "Bug fix"
  feature: "New feature"
  infra: "Infrastructure"
  docs: "Documentation"

  # Component labels (what area)
  dashboard: "Web dashboard"
  prompts: "Agent prompts"
  scripts: "Orchestration scripts"
  config: "Configuration"
```

**Usage:**

```bash
# Create a task with labels
bd create --title="Fix login bug" --type=bug --labels="dev,bug,dashboard" --priority=1

# Filter by label
bd ready -l dev          # Show only dev-labeled work
bd list -l dashboard     # List dashboard-related issues
```

### Environment Variables

Agents use these environment variables (set automatically by `spawn_agent.py`):

| Variable | Description | Example |
|----------|-------------|---------|
| `AGENT_ROLE` | The agent's role | `developer` |
| `AGENT_INSTANCE` | Instance number | `1` |
| `AGENT_LOG_FILE` | Path to agent's log file | `logs/developer_1.log` |

---

## Running Agents

### Starting a Single Agent

```bash
# Basic usage (defaults to instance 1)
python scripts/spawn_agent.py developer

# Specify instance number
python scripts/spawn_agent.py developer --instance 2

# Use a specific repository path
python scripts/spawn_agent.py developer --repo /path/to/repo
```

This opens a new Terminal window with Claude Code running the agent prompt.

### Starting Multiple Agents

Run multiple agents to work in parallel:

```bash
# Start a development team
python scripts/spawn_agent.py developer --instance 1
python scripts/spawn_agent.py developer --instance 2
python scripts/spawn_agent.py qa --instance 1
python scripts/spawn_agent.py reviewer --instance 1

# Full team
python scripts/spawn_agent.py manager
python scripts/spawn_agent.py tech_lead
python scripts/spawn_agent.py developer --instance 1
python scripts/spawn_agent.py developer --instance 2
python scripts/spawn_agent.py qa --instance 1
python scripts/spawn_agent.py reviewer --instance 1
```

### Role-Specific Notes

| Role | Command | Behavior |
|------|---------|----------|
| **Developer** | `python scripts/spawn_agent.py developer` | Writes code, fixes bugs, creates PRs. Uses `bd ready -l dev`. |
| **QA** | `python scripts/spawn_agent.py qa` | Tests features, validates acceptance criteria, creates bug beads. Uses `bd ready -l qa`. |
| **Tech Lead** | `python scripts/spawn_agent.py tech_lead` | Reviews designs, creates task breakdowns, sets dependencies. Uses `bd ready -l architecture`. |
| **Manager** | `python scripts/spawn_agent.py manager` | Creates epics, sets priorities, manages workflow. Sees all work (`bd ready`). |
| **Reviewer** | `python scripts/spawn_agent.py reviewer` | Reviews PRs, checks code quality. Uses `bd ready -l review`. |

### Manual Agent Mode

For testing or development, run agent behavior manually:

```bash
# Start Claude Code with a specific prompt
claude --print-system-prompt "$(cat prompts/DEVELOPER.md)"

# Or just use claude code and follow the prompt manually
claude
```

---

## Creating Work

### Manager Creates Epics

Epics are high-level features that contain multiple tasks:

```bash
# Create an epic
bd create --title="User Authentication System" \
  --type=epic \
  --priority=0 \
  --description="Implement complete user auth with login, logout, and session management"
```

### Tech Lead Creates Tasks

Break down epics into actionable tasks:

```bash
# Create implementation tasks
bd create --title="Design auth database schema" \
  --type=task \
  --priority=1 \
  --labels="architecture"

bd create --title="Implement login endpoint" \
  --type=task \
  --priority=1 \
  --labels="dev"

bd create --title="Write login integration tests" \
  --type=task \
  --priority=2 \
  --labels="qa"
```

### Setting Dependencies

Use dependencies to enforce ordering:

```bash
# Get the bead IDs
bd list --status=open

# Tests depend on implementation (implementation blocks tests)
bd dep add <test-bead-id> <impl-bead-id>

# Review depends on tests
bd dep add <review-bead-id> <test-bead-id>

# View dependency chain
bd show <bead-id>
bd dep show <bead-id>
```

**Dependency Concepts:**

- `bd dep add A B` means "A depends on B" (B must complete before A can start)
- Blocked beads won't appear in `bd ready`
- Use `bd blocked` to see all blocked beads

### Priority Levels

| Priority | When to Use |
|----------|-------------|
| P0 | Critical - security issues, production outages |
| P1 | High - major features, blocking bugs |
| P2 | Medium - standard development work |
| P3 | Low - nice-to-have, polish |

```bash
# Set priority when creating
bd create --title="Fix critical bug" --priority=0

# Update priority
bd update <bead-id> --priority=1
```

---

## Monitoring

### Dashboard (Web UI)

Start the dashboard for visual monitoring:

```bash
uv run python -m dashboard.app
```

Open http://127.0.0.1:8000 in your browser.

**Dashboard Features:**

- **Kanban Board** - Visual task board with Ready, In Progress, Done columns
- **Agent Sidebar** - Shows active agents and their current work
- **Dependency Graph** - Mermaid-based visualization of task dependencies
- **Bead Details** - Click any bead for full details modal
- **Auto-refresh** - Dashboard updates automatically via HTMX

### Terminal Monitor

For terminal-based monitoring:

```bash
# Basic usage (updates every 10 seconds)
python scripts/monitor.py

# Custom refresh interval
python scripts/monitor.py --interval 5

# Show more log lines
python scripts/monitor.py --log-lines 20

# Use custom log file
python scripts/monitor.py --log-file logs/developer_1.log
```

**Monitor Display:**

```
========================================
     MULTI-AGENT STATUS - 14:32:15
========================================

[DEVELOPER]
  [wod] [P2] Docs: Create USAGE.md detailed guide

[QA]
  No active work

----------------------------------------------------------------------
QUEUE SUMMARY
----------------------------------------------------------------------
  In Progress: 1
  Ready:       5
  Blocked:     3

----------------------------------------------------------------------
RECENT ACTIVITY
----------------------------------------------------------------------
  14:32:10 CLAIM: multi_agent_beads-wod
  14:31:45 SESSION_START
----------------------------------------------------------------------
Press Ctrl+C to exit
```

### Log Watching

Watch agent activity in real-time:

```bash
# Watch main log
tail -f claude.log

# Watch specific agent
tail -f logs/developer_1.log

# Filter for specific bead
grep "multi_agent_beads-xyz" claude.log

# Watch for errors
tail -f claude.log | grep "ERROR\|BLOCKED"
```

---

## Troubleshooting

### Common Issues

#### 1. "bd command not found"

**Problem:** Beads CLI not installed or not in PATH.

**Solution:**
```bash
# Check if installed
which bd

# Install beads
pip install beads-cli

# Or add to PATH if installed elsewhere
export PATH="$PATH:/path/to/beads/bin"
```

#### 2. "Beads not initialized"

**Problem:** No `.beads/` directory in project root.

**Solution:**
```bash
cd /path/to/multi_agent_beads
bd init
```

#### 3. Dashboard won't start

**Problem:** Port already in use or missing dependencies.

**Solution:**
```bash
# Check if port 8000 is in use
lsof -i :8000

# Kill existing process
kill -9 <PID>

# Or use different port
PORT=8080 uv run python -m dashboard.app
```

#### 4. Agent spawn fails (macOS)

**Problem:** Terminal permission issues or AppleScript errors.

**Solution:**
```bash
# Grant Terminal full disk access in System Preferences
# Settings > Privacy & Security > Full Disk Access > Terminal

# Or run manually
cd /path/to/repo
claude --print-system-prompt "$(cat prompts/DEVELOPER.md)"
```

### Agent Stuck

When an agent appears stuck or unresponsive:

1. **Check the log:**
   ```bash
   tail -20 claude.log
   grep "ERROR\|BLOCKED" claude.log
   ```

2. **Check bead status:**
   ```bash
   bd list --status=in_progress
   bd show <stuck-bead-id>
   ```

3. **Force release the bead:**
   ```bash
   # Reset to open so another agent can pick it up
   bd update <bead-id> --status=open
   ```

4. **Kill and restart agent:**
   - Close the stuck Terminal window
   - Check for orphaned processes: `ps aux | grep claude`
   - Spawn a new agent: `python scripts/spawn_agent.py developer`

### Beads Out of Sync

When local beads don't match expected state:

1. **Check sync status:**
   ```bash
   bd doctor
   ```

2. **Force sync:**
   ```bash
   bd sync --flush-only
   ```

3. **Verify JSONL export:**
   ```bash
   cat .beads/issues.jsonl | head -10
   ```

4. **Reset from JSONL (if corrupted):**
   ```bash
   # Backup first
   cp -r .beads .beads.backup

   # Reimport
   bd import .beads/issues.jsonl
   ```

### Dependency Cycles

Circular dependencies block all involved beads.

```bash
# Find cycles
bd blocked

# Remove problematic dependency
bd dep remove <bead-a> <bead-b>

# View full dependency tree
bd dep show <bead-id>
```

---

## Examples

### Full Workflow Walkthrough

This example shows a complete feature development cycle.

**1. Manager creates epic:**

```bash
bd create --title="Add user profile page" \
  --type=epic \
  --priority=1 \
  --description="Users need to view and edit their profile information"
```

**2. Tech Lead breaks down into tasks:**

```bash
# Design task
bd create --title="Design profile API endpoints" \
  --type=task --priority=1 --labels="architecture"

# Implementation tasks
bd create --title="Implement GET /profile endpoint" \
  --type=task --priority=1 --labels="dev"

bd create --title="Implement PUT /profile endpoint" \
  --type=task --priority=1 --labels="dev"

# QA task
bd create --title="Test profile endpoints" \
  --type=task --priority=2 --labels="qa"

# Review task
bd create --title="Review profile implementation" \
  --type=task --priority=2 --labels="review"
```

**3. Tech Lead sets dependencies:**

```bash
# Implementation depends on design
bd dep add <get-endpoint-id> <design-id>
bd dep add <put-endpoint-id> <design-id>

# Testing depends on implementation
bd dep add <test-id> <get-endpoint-id>
bd dep add <test-id> <put-endpoint-id>

# Review depends on testing
bd dep add <review-id> <test-id>
```

**4. Agents work through the queue:**

```bash
# Tech Lead sees design task in bd ready -l architecture
# Developer sees implementation tasks after design is closed
# QA sees test task after implementation is closed
# Reviewer sees review task after testing is closed
```

### Bug Discovery Flow

When QA finds a bug during testing:

**1. QA discovers issue:**

```bash
# QA adds comment to existing bead
bd comment <test-bead-id> "Found bug: profile update fails for empty fields"

# QA creates bug bead
bd create --title="Fix: profile update fails for empty fields" \
  --type=bug --priority=1 --labels="dev,bug"
```

**2. QA blocks the test on the bug:**

```bash
bd dep add <test-bead-id> <bug-bead-id>
```

**3. Developer fixes bug:**

The developer sees the bug in `bd ready -l dev`, fixes it, creates PR, and closes the bead.

**4. QA continues testing:**

After bug bead is closed, the test bead becomes unblocked and QA can continue.

### Code Review Flow

When code is ready for review:

**1. Developer creates PR:**

```bash
# After implementation is complete
gh pr create --title "Add profile endpoints" --body "Implements GET/PUT profile API"
```

**2. Create review bead (if not exists):**

```bash
bd create --title="Review PR #123: Profile endpoints" \
  --type=task --priority=1 --labels="review"
```

**3. Reviewer picks up the bead:**

```bash
# Reviewer sees in bd ready -l review
bd update <review-bead-id> --status=in_progress
```

**4. Reviewer examines PR:**

```bash
# View PR diff
gh pr diff 123

# View PR status
gh pr checks 123

# Approve or request changes
gh pr review 123 --approve
# OR
gh pr review 123 --request-changes --body "Please fix X"
```

**5. On approval:**

```bash
# Merge PR
gh pr merge 123 --squash --delete-branch

# Close review bead
bd close <review-bead-id> --reason="PR #123 approved and merged"
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Find work | `bd ready` |
| Find work by role | `bd ready -l dev` |
| View bead | `bd show <id>` |
| Claim bead | `bd update <id> --status=in_progress` |
| Close bead | `bd close <id> --reason="..."` |
| Create task | `bd create --title="..." --type=task` |
| Add dependency | `bd dep add <blocked> <blocker>` |
| View blocked | `bd blocked` |
| Project stats | `bd stats` |
| Sync to JSONL | `bd sync --flush-only` |
| Start dashboard | `uv run python -m dashboard.app` |
| Start monitor | `python scripts/monitor.py` |
| Spawn agent | `python scripts/spawn_agent.py <role>` |
| Run tests | `uv run pytest` |
