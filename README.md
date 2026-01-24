# Multi-Agent Beads

Multi-agent SDLC orchestration system where Developer, QA, Tech Lead, Manager, and Code Reviewer agents work concurrently on shared codebases with proper task handoffs.

## What is this?

This system uses [Beads](https://github.com/steveyegge/beads) as a coordination layer to enable multiple Claude agents to collaborate on software development tasks. Each agent has a specialized role and operates autonomously, picking up work from a shared queue and handing off to other agents via dependencies.

**Key Features:**
- **Role-based agents** - Developer, QA, Tech Lead, Manager, Code Reviewer
- **Dependency-driven workflows** - Tasks block/unblock automatically
- **Concurrent execution** - Multiple agents work in parallel on independent tasks
- **Quality gates** - Tests and reviews must pass before work can proceed
- **Dashboard** - Web UI for monitoring agent activity and task status

## Quick Start

```bash
# Clone and setup
git clone <repo-url> && cd multi_agent_beads
./scripts/setup.sh

# Spawn a developer agent
python scripts/spawn_agent.py developer
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATION LAYER                       │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐                  │
│  │ Spawner  │  │ Monitor  │  │ Dashboard │                  │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘                  │
│       └─────────────┴──────────────┘                        │
│                     │                                        │
│              ┌──────▼──────┐                                 │
│              │  Beads (bd) │                                 │
│              └──────┬──────┘                                 │
└─────────────────────┼───────────────────────────────────────┘
                      │
┌─────────────────────┼───────────────────────────────────────┐
│               AGENT LAYER                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │Developer │ │    QA    │ │Tech Lead │ │ Manager  │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│  Each agent runs in its own terminal session                │
└─────────────────────────────────────────────────────────────┘
```

**Coordination Flow:**
1. Manager creates epics and tasks with priorities
2. Tech Lead designs architecture, sets dependencies
3. Developers pick up unblocked dev tasks
4. QA validates completed work, creates bug reports
5. Code Reviewer approves PRs before merge

## Agent Roles

| Role | Focus | Label Filter |
|------|-------|--------------|
| **Developer** | Write code, fix bugs, create PRs | `bd ready -l dev` |
| **QA** | Test features, verify acceptance criteria | `bd ready -l qa` |
| **Tech Lead** | Architecture decisions, task breakdowns | `bd ready -l architecture` |
| **Manager** | Prioritization, epic management | `bd ready` |
| **Code Reviewer** | PR reviews, code quality | `bd ready -l review` |

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Beads](https://github.com/steveyegge/beads) CLI (`bd`)
- [Claude Code](https://github.com/anthropics/claude-code) CLI

## Project Structure

```
multi_agent_beads/
├── dashboard/          # FastAPI web dashboard
├── prompts/            # Role-specific agent prompts
│   ├── DEVELOPER.md
│   ├── QA.md
│   ├── TECH_LEAD.md
│   ├── MANAGER.md
│   └── CODE_REVIEWER.md
├── scripts/            # Orchestration tools
│   ├── spawn_agent.py  # Spawn agents in terminal windows
│   └── monitor.py      # Terminal monitor for activity
├── config/             # Configuration files
└── tests/              # Test suite
```

## Commands

```bash
# Start dashboard
uv run python -m dashboard.app

# Spawn agents
python scripts/spawn_agent.py developer
python scripts/spawn_agent.py qa --instance 2
python scripts/spawn_agent.py tech_lead

# Monitor activity
python scripts/monitor.py

# Run tests
uv run pytest

# Beads commands
bd ready              # Find available work
bd list --status=open # List all open issues
bd show <id>          # View issue details
bd stats              # Project statistics
```

## Documentation

- [PLAN.md](PLAN.md) - Detailed architecture and design decisions
- [AGENTS.md](AGENTS.md) - Agent workflow instructions
- [prompts/](prompts/) - Role-specific agent prompts

## License

MIT
