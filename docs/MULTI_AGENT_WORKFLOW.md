# Multi-Agent Workflow Design

This document defines the multi-agent workflow, team configurations, and coordination mechanisms for the Multi-Agent Beads System.

## Table of Contents

1. [Agent Roles & Responsibilities](#agent-roles--responsibilities)
2. [Complete Bead Lifecycle](#complete-bead-lifecycle)
3. [Team Configurations](#team-configurations)
4. [Town vs Individual Worker](#town-vs-individual-worker)
5. [Coordination Mechanisms](#coordination-mechanisms)
6. [Worktree Strategy](#worktree-strategy)
7. [Implementation Priority](#implementation-priority)

---

## Agent Roles & Responsibilities

### Manager
- Creates and prioritizes beads from user requests
- Breaks down epics into smaller tasks
- Assigns labels to route work (dev, qa, review)
- Monitors progress, unblocks stuck work
- **Finds work:** User requests, epic breakdowns
- **Hands off to:** Tech Lead (design), Dev (implementation)

### Tech Lead
- Designs architecture for complex features
- Creates technical specifications
- Reviews design decisions
- Sets coding standards
- **Finds work:** `bd ready -l design` or complex features from Manager
- **Hands off to:** Dev (implementation specs)

### Developer
- Implements features and fixes bugs
- Creates PRs (does NOT merge)
- Addresses review feedback
- **Finds work:** `bd ready -l dev`
- **Hands off to:** QA (testing), sets status=ready_for_qa

### QA
- Tests open PRs (checkouts PR branch)
- Verifies acceptance criteria
- Creates bug beads when issues found
- Approves PRs that pass testing
- **Finds work:** `bd ready --status=ready_for_qa` or `-l qa`
- **Hands off to:** Reviewer (if passes), Dev (if bugs found)

### Code Reviewer
- Reviews code quality, patterns, security
- Approves or requests changes on PRs
- Merges approved PRs
- Closes beads after merge
- **Finds work:** `bd ready --status=ready_for_review` or `-l review`
- **Hands off to:** Dev (if changes needed), Done (if merged)

---

## Complete Bead Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                         BEAD LIFECYCLE                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  User Request                                                        │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐                                                         │
│  │ MANAGER │ Creates bead, sets priority, assigns label             │
│  └────┬────┘                                                         │
│       │                                                              │
│       ▼ (complex feature?)                                           │
│  ┌───────────┐                                                       │
│  │ TECH LEAD │ Creates design doc, breaks into tasks                │
│  └─────┬─────┘                                                       │
│        │                                                             │
│        ▼                                                             │
│  ┌───────────┐                                                       │
│  │ DEVELOPER │ Implements, creates PR                               │
│  └─────┬─────┘                                                       │
│        │ status = ready_for_qa                                       │
│        ▼                                                             │
│  ┌─────────┐        ┌─────────────┐                                  │
│  │   QA    │──bug──▶│  DEVELOPER  │ (fix bug, back to QA)           │
│  └────┬────┘        └─────────────┘                                  │
│       │ passes                                                       │
│       │ status = ready_for_review                                    │
│       ▼                                                              │
│  ┌──────────┐       ┌─────────────┐                                  │
│  │ REVIEWER │─fix──▶│  DEVELOPER  │ (address feedback, back to QA)  │
│  └────┬─────┘       └─────────────┘                                  │
│       │ approved                                                     │
│       │ merge PR                                                     │
│       ▼                                                              │
│    [DONE]                                                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Status Flow

```
open → in_progress → ready_for_qa → qa_in_progress → ready_for_review → review_in_progress → done
```

---

## Team Configurations

### Option 1: Full Team (Recommended for Production)
```bash
mab town create prod --roles manager,tech_lead,dev,qa,reviewer
```
- All roles present
- Full automation
- Proper handoffs
- Cost: 5 Claude instances

### Option 2: Core Team (Balanced)
```bash
mab town create core --roles dev,qa,reviewer
```
- Human acts as Manager (creates beads manually)
- Human acts as Tech Lead (provides design guidance)
- Automated: code → test → review → merge
- Cost: 3 Claude instances

### Option 3: Dev Pair (Minimum Viable)
```bash
mab town create minimal --roles dev,qa
```
- Human acts as Manager, Tech Lead, Reviewer
- Automated: code → test
- Human merges PRs
- Cost: 2 Claude instances

### Option 4: Solo Dev (Cheapest)
```bash
mab worker spawn dev  # or: mab town create solo --roles dev
```
- Human does everything except coding
- Just automated implementation
- Cost: 1 Claude instance

---

## Town vs Individual Worker

### Require Town? NO - Allow Both

**Individual Worker Use Cases:**
- Quick one-off task
- Human wants to be in the loop
- Testing/debugging
- Cost-conscious

**Town Use Cases:**
- Ongoing project work
- Full automation desired
- Multiple parallel tasks
- Team coordination needed

### CLI Design
```bash
# Individual worker (no town required)
mab worker spawn dev --project .

# Create town with specific roles
mab town create myproject --roles dev,qa --project .

# Start town (spins up all configured roles)
mab town start myproject

# Quick start with defaults (dev + qa)
mab start  # Auto-creates town from current directory
```

---

## Coordination Mechanisms

### 1. Bead Status Flow
```
open → in_progress → ready_for_qa → qa_in_progress → ready_for_review → review_in_progress → done
```

### 2. Label Routing
- `-l dev` - Developer work
- `-l qa` - QA work
- `-l review` - Review work
- `-l design` - Tech Lead work
- `-l blocked` - Needs human intervention

### 3. Dependency Blocking
```bash
bd dep add <bug-id> <feature-id>  # Feature blocked by bug
```

### 4. PR-Based Handoff
- Dev creates PR → triggers QA
- QA approves PR → triggers Reviewer
- Reviewer merges → closes bead

---

## Worktree Strategy

### All Workers Get Worktrees
Each worker (regardless of role) gets isolated worktree:
```
.worktrees/
├── worker-dev-abc123/      # Dev's workspace
├── worker-qa-def456/       # QA's workspace
├── worker-reviewer-ghi789/ # Reviewer's workspace
```

### Branch Sharing via PRs
```bash
# Dev creates PR from their branch
git push -u origin worker/worker-dev-abc123
gh pr create

# QA checks out the PR
gh pr checkout 42

# Reviewer reviews same PR
gh pr review 42
```

### Shared Beads Database
All worktrees symlink to main `.beads/`:
```bash
.worktrees/worker-dev-abc123/.beads → ../../.beads
```

---

## Implementation Priority

| Priority | Task | Description |
|----------|------|-------------|
| P0 | Define status flow | Add ready_for_qa, ready_for_review statuses to beads |
| P0 | Update prompts | Update agent prompts for proper handoffs |
| P1 | Town creation | Implement `mab town create` with role selection |
| P1 | Auto-start roles | Start configured roles when town starts |
| P2 | Dashboard town management | Add town management UI to dashboard |
| P2 | Agent availability | Check agent availability before spawning |

---

## Related Documentation

- [ARCHITECTURE.md](../ARCHITECTURE.md) - System architecture
- [WORKER_WORKFLOW.md](WORKER_WORKFLOW.md) - Worker session protocol
- [prompts/](../prompts/) - Role-specific agent prompts
