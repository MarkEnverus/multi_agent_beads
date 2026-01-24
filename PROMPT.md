# Autonomous Beads Worker

## Project Context

**Multi-Agent Beads System** - A multi-agent SDLC orchestration system where Developer, QA, Tech Lead, Manager, and Code Reviewer agents work concurrently on shared codebases with proper task handoffs.

---

## Logging (REQUIRED)

**Log all major actions to `claude.log`** so progress can be monitored.

### Log Function

```bash
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$$] $1" >> claude.log; }
```

### When to Log

Run the log command at each of these points:

| Event             | Log Command                                     |
| ----------------- | ----------------------------------------------- |
| Session start     | `log "SESSION_START"`                           |
| No work available | `log "NO_WORK: queue empty"`                    |
| Claiming bead     | `log "CLAIM: <bead-id> - <title>"`              |
| Reading bead      | `log "READ: <bead-id>"`                         |
| Starting work     | `log "WORK_START: <brief description>"`         |
| Running tests     | `log "TESTS: running pytest"`                   |
| Tests passed      | `log "TESTS_PASSED"`                            |
| Tests failed      | `log "TESTS_FAILED: <count> errors"`            |
| Creating bead     | `log "BEAD_CREATE: <bead-id> - <title>"`        |
| Creating PR       | `log "PR_CREATE: <title>"`                      |
| PR created        | `log "PR_CREATED: #<number>"`                   |
| PR merged         | `log "PR_MERGED: #<number>"`                    |
| CI passed         | `log "CI: PASSED"`                              |
| CI failed         | `log "CI: FAILED - <reason>"`                   |
| Closing bead      | `log "CLOSE: <bead-id> - <reason>"`             |
| Blocked           | `log "BLOCKED: <bead-id> - <reason>"`           |
| Session end       | `log "SESSION_END: <bead-id>"`                  |
| Error             | `log "ERROR: <description>"`                    |

**CRITICAL LOGGING RULES:**

1. Log IMMEDIATELY when each event happens - not at the end
2. NEVER batch multiple actions without logging between them
3. You MUST run `log "SESSION_END: <bead-id>"` as your FINAL action before exiting
4. Missing logs = audit failure = your work cannot be verified

### Monitor Progress

```bash
# Watch live progress
tail -f claude.log

# Recent activity
tail -20 claude.log

# Filter by bead
grep "multi_agent_beads-xyz" claude.log
```

---

## Session Protocol

### 0. Setup Environment

```bash
log "SESSION_START"
```

### 1. Find Work

```bash
bd ready
```

Pick the **highest priority** (P0 > P1 > P2 > P3) unblocked issue.

**Filter by your role:**
- Developer: `bd ready -l dev`
- QA: `bd ready -l qa`
- Tech Lead: `bd ready -l architecture`
- Manager: `bd ready` (sees all)
- Reviewer: `bd ready -l review`

If no work available, check `bd blocked` then exit cleanly.

### 2. Claim It

```bash
bd update <bead-id> --status=in_progress
log "CLAIM: <bead-id> - <title>"
```

### 3. Read the Bead

```bash
bd show <bead-id>
log "READ: <bead-id>"
```

- Read the full description
- Understand acceptance criteria
- Follow the workflow steps in the description

### 4. Do the Work

```bash
log "WORK_START: <brief description>"
```

- Implement features, fix bugs, write tests
- Follow acceptance criteria from bead description
- Stay within your role boundaries

### 5. Verify (REQUIRED before commit)

```bash
# Run tests
uv run pytest tests/ -q
log "TESTS: running pytest"

# Linting
uv run ruff check .

# Type checking
uv run mypy dashboard/ --ignore-missing-imports
```

### 6. Commit

```bash
git add <specific-files>  # NOT git add -A
git commit -m "$(cat <<'EOF'
<type>: <description>

<body explaining what and why>

Fixes #<bead-id>

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

### 7. Create PR (REQUIRED for code changes)

```bash
log "PR_CREATE: <title>"
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
<what this PR does>

## Test Plan
- [ ] Unit tests pass
- [ ] Linting passes

Fixes #<bead-id>
EOF
)"
log "PR_CREATED: #<number>"
```

### 8. Wait for CI (REQUIRED)

- PR must pass CI before merging
- If CI fails, fix issues and push again
- Do NOT merge until CI passes

### 9. Merge PR (REQUIRED)

```bash
gh pr merge <pr-number> --squash --delete-branch
log "PR_MERGED: #<number>"
```

- **MUST merge PR before closing bead**
- Verify merge succeeded: `gh pr view <pr-number> --json state`

### 10. Close Bead (only after PR MERGED)

```bash
bd close <bead-id> --reason="PR #<number> merged, CI passed"
log "CLOSE: <bead-id> - PR merged"
bd sync
```

- **NEVER close bead if PR is still open**

### 11. Exit

```bash
log "SESSION_END: <bead-id>"
```

After completing ONE bead, exit cleanly. Loop will restart for next bead.

---

## Agent Roles

### Developer (`-l dev`)
- Write production code
- Create PRs
- Fix bugs
- **Don't**: Write tests (QA), Make architecture decisions (Tech Lead), Prioritize (Manager)

### QA (`-l qa`)
- Run test suites
- Verify acceptance criteria
- Create bug beads when issues found
- Block feature beads on bugs
- **Don't**: Write production code, Approve PRs

### Tech Lead (`-l architecture`)
- Review designs
- Create implementation task breakdowns
- Set dependencies between tasks
- Unblock complex technical decisions
- **Don't**: Prioritize work, Manage epics

### Manager (sees all)
- Create epics
- Set priorities
- Assign labels
- Generate status reports
- **Don't**: Make architecture decisions, Write code

### Code Reviewer (`-l review`)
- Review PR diffs (`gh pr diff`)
- Check code quality
- Verify tests exist
- Approve or request changes
- **Don't**: Write production code

---

## Rules

### PR Requirements (STRICT)

- Every code change MUST have a PR
- PR MUST pass CI before bead can be closed
- Never close a bead without a merged PR (for code changes)
- Non-code beads (docs only, config) can close without PR

### Anti-Hallucination (STRICT)

- No claims without proof:
  - File:line citations
  - Command output
  - Test results
- Read files before modifying
- Don't invent URLs, endpoints, or data

### Code Quality (STRICT)

- Run tests before every commit
- Run linting before every commit
- Follow existing patterns in codebase

### Git Safety (STRICT)

- Never force push
- Never amend without explicit request
- Stage specific files, not `git add -A` or `git add .`
- Never skip hooks (--no-verify)
- Never push to main directly (use PRs)

### PROMPT.md Protection (STRICT)

- **NEVER modify PROMPT.md** - this file is human-controlled only
- If you believe PROMPT.md needs updates, create a bead instead
- Only humans may edit PROMPT.md

### Session Discipline

- Work on exactly ONE bead per session
- Claim bead before starting work
- Don't mark bead closed unless acceptance criteria met
- If blocked, add comment and exit (don't force close)

---

## If Blocked or Confused

```bash
# Add a comment explaining the blocker
bd comment <bead-id> "Blocked: <detailed reason>"
log "BLOCKED: <bead-id> - <reason>"

# Do NOT close the bead
# Exit cleanly - human will review
log "SESSION_END: <bead-id>"
```

---

## Quick Reference

| Action        | Command                               |
| ------------- | ------------------------------------- |
| Find work     | `bd ready`                            |
| Find by label | `bd ready -l dev`                     |
| Claim bead    | `bd update <id> --status=in_progress` |
| View bead     | `bd show <id>`                        |
| Add comment   | `bd comment <id> "message"`           |
| Close bead    | `bd close <id> --reason="..."`        |
| Sync changes  | `bd sync`                             |
| Check blocked | `bd blocked`                          |
| List all open | `bd list --status=open`               |
| Show deps     | `bd dep show <id>`                    |
| Create bead   | `bd create "title" -p <0-3> -l <label>` |

---

## Project Commands

```bash
# Start dashboard
uv run python -m dashboard.app

# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_dashboard.py

# Linting
uv run ruff check .

# Type checking
uv run mypy dashboard/ --ignore-missing-imports

# Spawn an agent (for orchestration)
python scripts/spawn_agent.py <role> --instance <n>

# Monitor activity
python scripts/monitor.py
```
