# QA Verification Report: Workers Claim Beads After PR #27 Fix

**Bead ID**: multi_agent_beads-hti5l
**Date**: 2026-01-31
**Result**: **FAIL**

## Test Summary

PR #27 was supposed to fix workers claiming beads by making them use the main project beads database. However, testing reveals a **different, more fundamental bug** that prevents workers from claiming beads.

## Test Steps Performed

1. **Environment Check**
   - MAB daemon: RUNNING (PID 96934)
   - No workers active initially

2. **Created Test Bead**
   - ID: `multi_agent_beads-wt9f9`
   - Title: "[Test] Verify worker claims this bead"
   - Priority: P1
   - Labels: dev
   - Verified visible in `bd ready -l dev`

3. **Spawned Developer Worker**
   - Worker ID: `worker-dev-1452e0ac`
   - PID: 86613
   - Worktree created: `.worktrees/worker-dev-1452e0ac/`

4. **Observed Worker Behavior**
   - Worker process started but became **defunct (zombie)** almost immediately
   - No `claude.log` file created in worktree
   - Test bead remained in OPEN status
   - **No CLAIM event logged**

## Root Cause Analysis

**The spawner is using `claude --print` flag** (mab/spawner.py:704-708), which causes Claude CLI to:
1. Print the prompt to stdout
2. Exit immediately without running interactively

This means workers never actually execute - they just print their prompt and terminate.

### Evidence from spawner.py

```python
# Line 704-708:
# Using --print flag to pass initial prompt
cmd = [
    self.claude_path,
    "--print",
    full_prompt,
]
```

### Process Evidence

```
PID 86613: <defunct>  # Zombie process - already exited
```

## Conclusion

**PR #27 fix is irrelevant** because workers never run long enough to query any database. The `--print` flag is the root cause preventing all worker functionality.

## Recommended Action

Create a P0 bug to fix the spawner:
- Remove `--print` flag from spawn command
- Use proper interactive mode or `-p` (prompt) flag instead
- This affects ALL worker types, not just dev workers

## Test Artifacts

- Test bead: `multi_agent_beads-wt9f9` (remains OPEN)
- Worker worktree: `.worktrees/worker-dev-1452e0ac/`
- No screenshots possible (worker never ran)
