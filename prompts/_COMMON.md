# Common Agent Rules

> **Import this file in all role-specific prompts**

---

## Project Context

**Multi-Agent Beads System** - A multi-agent SDLC orchestration system where Developer, QA, Tech Lead, Manager, and Code Reviewer agents work concurrently on shared codebases with proper task handoffs.

---

## Logging (REQUIRED)

**Log all major actions to `claude.log`** so progress can be monitored.

### Log Function

```bash
# Use WORKER_ID if set (for spawned workers), otherwise fall back to $$ (for manual runs)
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${WORKER_ID:-$$}] $1" >> claude.log; }
```

### Required Log Events

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

### Critical Logging Rules

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
grep "<bead-id>" claude.log
```

---

## Beads Protocol

### Session Lifecycle

```bash
# 1. Start
log "SESSION_START"

# 2. Find work
bd ready                    # All available work
bd ready -l <role-label>    # Filter by role

# 3. Claim
bd update <bead-id> --status=in_progress
log "CLAIM: <bead-id> - <title>"

# 4. Read
bd show <bead-id>
log "READ: <bead-id>"

# 5. Work
log "WORK_START: <description>"
# ... do the work ...

# 6. Close (after PR merged for code changes)
bd close <bead-id> --reason="<reason>"
log "CLOSE: <bead-id> - <reason>"

# 7. Sync
bd sync --flush-only

# 8. Exit
log "SESSION_END: <bead-id>"
```

### Essential bd Commands

| Action            | Command                                 |
| ----------------- | --------------------------------------- |
| Find work         | `bd ready`                              |
| Find by label     | `bd ready -l <label>`                   |
| Claim bead        | `bd update <id> --status=in_progress`   |
| View bead         | `bd show <id>`                          |
| Add comment       | `bd comment <id> "message"`             |
| Close bead        | `bd close <id> --reason="..."`          |
| Sync changes      | `bd sync --flush-only`                  |
| Check blocked     | `bd blocked`                            |
| List all open     | `bd list --status=open`                 |
| Show dependencies | `bd dep show <id>`                      |
| Create bead       | `bd create --title="..." -p <0-4> -l <label>` |
| Add dependency    | `bd dep add <issue> <depends-on>`       |

### Sync Requirements

- Run `bd sync --flush-only` before ending session
- Sync exports beads to JSONL for persistence
- Always sync after closing beads

---

## Rules

### Session Discipline (STRICT)

- Work on exactly ONE bead per session
- Claim bead BEFORE starting work
- Don't mark bead closed unless ALL acceptance criteria met
- If blocked, add comment and exit (don't force close)

### Evidence Requirements (STRICT)

No claims without proof:
- File:line citations for code references
- Command output for verifications
- Test results for quality assertions
- Read files BEFORE modifying them

### Anti-Hallucination Policy (STRICT)

- NEVER invent URLs, endpoints, or data
- NEVER claim files exist without reading them
- NEVER claim tests pass without running them
- ALWAYS cite sources (file:line, command output)

### PR Requirements (STRICT)

- Every code change MUST have a PR
- PR MUST pass CI before bead can be closed
- NEVER close a bead without a merged PR (for code changes)
- Non-code beads (docs only, config) can close without PR

### Code Quality (STRICT)

- Run tests before every commit
- Run linting before every commit
- Follow existing patterns in codebase

---

## Git Safety (STRICT)

### Never Do

- Never force push (`git push --force`)
- Never amend without explicit request
- Never skip hooks (`--no-verify`)
- Never push to main directly (use PRs)

### Always Do

- Stage specific files: `git add <file1> <file2>`
- NOT: `git add -A` or `git add .`
- Use HEREDOC for commit messages
- Include Co-Authored-By line

### Commit Format

```bash
git add <specific-files>
git commit -m "$(cat <<'EOF'
<type>: <description>

<body explaining what and why>

Fixes #<bead-id>

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

### PR Workflow

```bash
# Create PR
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
<what this PR does>

## Test Plan
- [ ] Unit tests pass
- [ ] Linting passes

Fixes #<bead-id>
EOF
)"

# Wait for CI to pass (REQUIRED)

# Merge after CI passes
gh pr merge <number> --squash --delete-branch

# Verify merge
gh pr view <number> --json state
```

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

Never force close a bead when blocked. Document the issue and exit for human review.

---

## Protected Files

- **NEVER modify PROMPT.md** - human-controlled only
- If PROMPT.md needs updates, create a bead instead
- Only humans may edit PROMPT.md

---

## Project Commands

```bash
# Start dashboard
uv run python -m dashboard.app

# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/<file>.py

# Linting
uv run ruff check .

# Type checking
uv run mypy dashboard/ --ignore-missing-imports
```
