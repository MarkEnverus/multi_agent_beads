"""Cross-platform worker spawner implementations.

This module provides platform-independent worker spawning that replaces
the macOS-only AppleScript approach. It supports:
- Headless subprocess spawning with PTY allocation
- Optional tmux/screen session management for isolation
- Log capture and streaming
- Proper Claude CLI interaction

Usage:
    from mab.spawner import SubprocessSpawner

    spawner = SubprocessSpawner(logs_dir=Path("/path/to/logs"))
    process_info = await spawner.spawn(
        role="developer",
        project_path="/path/to/repo",
        worker_id="worker-dev-abc123",
    )
"""

from __future__ import annotations

import asyncio
import fcntl
import json as json_module
import logging
import os
import pty
import re
import shutil
import subprocess
import termios
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mab import db

logger = logging.getLogger("mab.spawner")

# Role to prompt file mapping
ROLE_TO_PROMPT = {
    "dev": "DEVELOPER.md",
    "developer": "DEVELOPER.md",
    "qa": "QA.md",
    "tech_lead": "TECH_LEAD.md",
    "manager": "MANAGER.md",
    "reviewer": "CODE_REVIEWER.md",
}

# Role to label mapping for bd ready filtering
ROLE_TO_LABEL = {
    "dev": "dev",
    "developer": "dev",
    "qa": "qa",
    "tech_lead": "architecture",
    "manager": None,  # Manager sees all
    "reviewer": "review",
}

# Default worktrees directory name
WORKTREES_DIR = ".worktrees"

# Regex pattern for valid worker_id (alphanumeric, hyphens, underscores only)
WORKER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def validate_worker_id(worker_id: str) -> None:
    """Validate worker_id to prevent shell injection.

    Args:
        worker_id: Worker identifier to validate.

    Raises:
        ValueError: If worker_id contains invalid characters.
    """
    if not worker_id:
        raise ValueError("worker_id cannot be empty")
    if len(worker_id) > 128:
        raise ValueError(f"worker_id too long: {len(worker_id)} chars (max 128)")
    if not WORKER_ID_PATTERN.match(worker_id):
        raise ValueError(
            f"Invalid worker_id '{worker_id}': must contain only "
            "alphanumeric characters, hyphens, and underscores, "
            "and must start with an alphanumeric character"
        )


def is_git_repo(path: Path) -> bool:
    """Check if path is inside a git repository.

    Args:
        path: Directory path to check.

    Returns:
        True if path is a git repo or inside one.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_git_root(path: Path) -> Path | None:
    """Get the root directory of the git repository.

    Args:
        path: Directory path inside the repo.

    Returns:
        Path to git root, or None if not a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def create_worktree(
    project_path: Path,
    worker_id: str,
    bead_id: str | None = None,
) -> tuple[Path, str]:
    """Create an isolated git worktree for a worker.

    Args:
        project_path: Path to the main project (git repo root).
        worker_id: Unique identifier for the worker.
        bead_id: Optional bead ID for branch naming.

    Returns:
        Tuple of (worktree_path, branch_name).

    Raises:
        SpawnerError: If worktree creation fails.
        ValueError: If worker_id or bead_id contains invalid characters.
    """
    # Validate inputs to prevent shell injection
    validate_worker_id(worker_id)
    if bead_id is not None:
        validate_worker_id(bead_id)

    git_root = get_git_root(project_path)
    if git_root is None:
        raise SpawnerError(
            message=f"Not a git repository: {project_path}",
            detail="Git worktree isolation requires a git repository",
        )

    # Create worktrees directory if needed
    worktrees_dir = git_root / WORKTREES_DIR
    worktrees_dir.mkdir(parents=True, exist_ok=True)

    # Generate branch name based on worker/bead
    branch_name = f"worker/{worker_id}"
    if bead_id:
        branch_name = f"bead/{bead_id}"

    # Worktree path
    worktree_path = worktrees_dir / worker_id

    # Check if worktree already exists
    if worktree_path.exists():
        logger.warning(f"Worktree already exists at {worktree_path}, removing first")
        if not remove_worktree(git_root, worktree_path):
            # Removal failed - use unique suffix to avoid collision
            import uuid

            suffix = uuid.uuid4().hex[:8]
            original_path = worktree_path
            worktree_path = worktrees_dir / f"{worker_id}-{suffix}"
            logger.warning(
                f"Failed to remove existing worktree at {original_path}, "
                f"using unique path: {worktree_path}"
            )

    # Create the worktree with a new branch from HEAD
    try:
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Branch might already exist, try without -b
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=str(git_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise SpawnerError(
                    message=f"Failed to create worktree: {result.stderr}",
                    worker_id=worker_id,
                    detail="git worktree add failed",
                )

        logger.info(f"Created worktree at {worktree_path} on branch {branch_name}")

        # Symlink .beads directory from main project to worktree
        # This ensures workers use the live beads database, not a stale copy
        main_beads = git_root / ".beads"
        worktree_beads = worktree_path / ".beads"

        if main_beads.exists():
            symlink_created = False
            last_error: Exception | None = None
            max_retries = 3

            for attempt in range(1, max_retries + 1):
                try:
                    # Remove the stale .beads directory in the worktree
                    # Git worktree add copies tracked files, including .beads contents
                    if worktree_beads.exists() and not worktree_beads.is_symlink():
                        logger.info(
                            f"Removing stale .beads directory in worktree: {worktree_beads} "
                            f"(attempt {attempt}/{max_retries})"
                        )
                        shutil.rmtree(worktree_beads)
                    elif worktree_beads.is_symlink():
                        logger.debug(f"Removing existing .beads symlink: {worktree_beads}")
                        worktree_beads.unlink()

                    # Create symlink to main project's .beads
                    worktree_beads.symlink_to(main_beads, target_is_directory=True)

                    # Verify the symlink was created correctly
                    if worktree_beads.is_symlink():
                        resolved = worktree_beads.resolve()
                        if resolved == main_beads.resolve():
                            logger.info(f"Symlinked .beads from {main_beads} to {worktree_beads}")
                            symlink_created = True
                            break
                        else:
                            last_error = OSError(
                                f"Symlink created but points to wrong target: {resolved} "
                                f"(expected {main_beads.resolve()})"
                            )
                            logger.error(str(last_error))
                    else:
                        last_error = OSError(f"Failed to create symlink at {worktree_beads}")
                        logger.error(str(last_error))

                except OSError as e:
                    last_error = e
                    logger.error(
                        f"Failed to create .beads symlink in worktree {worktree_path} "
                        f"(attempt {attempt}/{max_retries}): {e}"
                    )

                # Small delay between retries to handle transient issues
                if attempt < max_retries:
                    import time

                    time.sleep(0.1)

            if not symlink_created:
                # Remove the worktree since it's unusable without the beads symlink
                logger.error(
                    f"Failed to create .beads symlink after {max_retries} attempts, "
                    "removing worktree"
                )
                if not remove_worktree(git_root, worktree_path):
                    logger.warning(
                        f"Failed to cleanup worktree at {worktree_path} after symlink failure. "
                        "Manual cleanup may be required."
                    )
                raise SpawnerError(
                    message="Failed to create .beads symlink in worktree",
                    worker_id=worker_id,
                    detail=f"Workers cannot operate without access to live beads database. "
                    f"Last error: {last_error}",
                )
        else:
            logger.debug(f"No .beads directory found at {main_beads}, skipping symlink")

        return worktree_path, branch_name

    except subprocess.TimeoutExpired:
        raise SpawnerError(
            message="Timeout creating git worktree",
            worker_id=worker_id,
        )
    except FileNotFoundError:
        raise SpawnerError(
            message="git command not found",
            detail="Ensure git is installed and in PATH",
        )


def remove_worktree(git_root: Path, worktree_path: Path) -> bool:
    """Remove a git worktree and optionally its branch.

    Args:
        git_root: Path to the git repository root.
        worktree_path: Path to the worktree to remove.

    Returns:
        True if removal succeeded, False otherwise.
    """
    if not worktree_path.exists():
        return True

    try:
        # First try normal removal
        result = subprocess.run(
            ["git", "worktree", "remove", str(worktree_path)],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            # Force removal if normal fails (e.g., uncommitted changes)
            logger.warning(f"Normal worktree removal failed, forcing: {result.stderr}")
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=str(git_root),
                capture_output=True,
                text=True,
                timeout=30,
            )

        if result.returncode == 0:
            logger.info(f"Removed worktree at {worktree_path}")
            return True
        else:
            logger.error(f"Failed to remove worktree: {result.stderr}")
            return False

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"Error removing worktree: {e}")
        return False


def fix_worktree_beads_symlinks(project_path: Path) -> tuple[int, int]:
    """Fix .beads directories in worktrees by replacing them with symlinks to main project.

    This repairs worktrees that were created before the symlink code was added,
    or where symlink creation failed.

    Args:
        project_path: Path to the main project (git repo root).

    Returns:
        Tuple of (fixed_count, error_count).
    """
    git_root = get_git_root(project_path)
    if git_root is None:
        logger.warning(f"Not a git repository: {project_path}")
        return 0, 0

    main_beads = git_root / ".beads"
    if not main_beads.exists():
        logger.info("No .beads directory in main project, nothing to fix")
        return 0, 0

    worktrees_dir = git_root / WORKTREES_DIR
    if not worktrees_dir.exists():
        logger.info("No worktrees directory found")
        return 0, 0

    fixed = 0
    errors = 0

    for worktree_path in worktrees_dir.iterdir():
        if not worktree_path.is_dir():
            continue

        worktree_beads = worktree_path / ".beads"

        # Skip if already a symlink pointing to the right place
        if worktree_beads.is_symlink():
            target = worktree_beads.resolve()
            if target == main_beads.resolve():
                logger.debug(f"Worktree {worktree_path.name} already has correct symlink")
                continue
            else:
                logger.info(f"Worktree {worktree_path.name} has symlink to wrong target, fixing")

        try:
            # Remove existing .beads (directory or bad symlink)
            if worktree_beads.exists() or worktree_beads.is_symlink():
                if worktree_beads.is_symlink():
                    worktree_beads.unlink()
                else:
                    shutil.rmtree(worktree_beads)

            # Create symlink to main project's .beads
            worktree_beads.symlink_to(main_beads, target_is_directory=True)
            logger.info(f"Fixed .beads symlink in worktree: {worktree_path.name}")
            fixed += 1

        except OSError as e:
            logger.error(f"Failed to fix .beads in worktree {worktree_path.name}: {e}")
            errors += 1

    return fixed, errors


def cleanup_stale_worktrees(project_path: Path, active_worker_ids: set[str] | None = None) -> int:
    """Clean up stale worktrees that don't belong to active workers.

    Args:
        project_path: Path to the project (git repo root).
        active_worker_ids: Set of currently active worker IDs. If None, cleans all.

    Returns:
        Number of worktrees removed.
    """
    git_root = get_git_root(project_path)
    if git_root is None:
        return 0

    worktrees_dir = git_root / WORKTREES_DIR
    if not worktrees_dir.exists():
        return 0

    removed = 0
    for worktree_path in worktrees_dir.iterdir():
        if not worktree_path.is_dir():
            continue

        worker_id = worktree_path.name
        if active_worker_ids is None or worker_id not in active_worker_ids:
            if remove_worktree(git_root, worktree_path):
                removed += 1

    # Prune any dangling worktree references
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(git_root),
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return removed


def list_worktrees(project_path: Path) -> list[dict[str, str]]:
    """List all worktrees in a project.

    Args:
        project_path: Path to the project (git repo root).

    Returns:
        List of dicts with 'path', 'branch', 'commit' keys.
    """
    git_root = get_git_root(project_path)
    if git_root is None:
        return []

    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        worktrees = []
        current: dict[str, str] = {}
        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["commit"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]

        if current:
            worktrees.append(current)

        return worktrees

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


@dataclass
class ProcessInfo:
    """Information about a spawned worker process."""

    pid: int
    worker_id: str
    role: str
    project_path: str
    log_file: Path
    started_at: str
    master_fd: int | None = None  # PTY master fd for I/O
    process: subprocess.Popen[bytes] | None = None
    worktree_path: Path | None = None  # Isolated worktree for this worker
    worktree_branch: str | None = None  # Branch name for the worktree


class SpawnerError(Exception):
    """Base exception for spawner errors."""

    def __init__(
        self,
        message: str,
        role: str | None = None,
        worker_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.message = message
        self.role = role
        self.worker_id = worker_id
        self.detail = detail
        super().__init__(message)


class Spawner(ABC):
    """Abstract base class for worker spawners."""

    @abstractmethod
    async def spawn(
        self,
        role: str,
        project_path: str,
        worker_id: str,
        env_vars: dict[str, str] | None = None,
        bead_id: str | None = None,
    ) -> ProcessInfo:
        """Spawn a new worker process.

        Args:
            role: Worker role (dev, qa, tech_lead, manager, reviewer).
            project_path: Path to the project directory.
            worker_id: Unique identifier for this worker.
            env_vars: Additional environment variables.
            bead_id: Optional specific bead ID for this worker to work on.

        Returns:
            ProcessInfo with spawned process details.

        Raises:
            SpawnerError: If spawning fails.
        """
        pass

    @abstractmethod
    async def terminate(
        self,
        process_info: ProcessInfo,
        graceful: bool = True,
        timeout: float = 30.0,
    ) -> int | None:
        """Terminate a worker process.

        Args:
            process_info: ProcessInfo from spawn().
            graceful: If True, send SIGTERM first.
            timeout: Seconds to wait before SIGKILL.

        Returns:
            Exit code if process terminated, None if timeout.
        """
        pass

    def _get_prompt_path(self, role: str, project_path: Path) -> Path:
        """Get the path to the role-specific prompt file.

        Args:
            role: Worker role.
            project_path: Project directory path.

        Returns:
            Path to prompt file.

        Raises:
            SpawnerError: If role is invalid or prompt not found.
        """
        if role not in ROLE_TO_PROMPT:
            raise SpawnerError(
                message=f"Invalid role: {role}",
                role=role,
                detail=f"Valid roles: {', '.join(ROLE_TO_PROMPT.keys())}",
            )

        prompt_file = ROLE_TO_PROMPT[role]
        prompt_path = project_path / "prompts" / prompt_file

        if not prompt_path.exists():
            raise SpawnerError(
                message=f"Prompt file not found: {prompt_path}",
                role=role,
                detail="Ensure the prompts/ directory contains role-specific prompts",
            )

        return prompt_path

    def _build_worker_prompt(
        self,
        role: str,
        prompt_content: str,
        worker_id: str,
        poll_interval_seconds: int = 30,
        max_idle_polls: int = 10,
    ) -> str:
        """Build the full worker prompt with role context and polling loop.

        This builds a prompt for workers that continuously poll for work.
        For workers assigned a specific bead, use _build_single_task_prompt instead.

        Args:
            role: Worker role.
            prompt_content: Content from role-specific prompt file.
            worker_id: Worker identifier.
            poll_interval_seconds: Seconds to wait between polls when no work found.
            max_idle_polls: Max consecutive polls without finding work before exiting.

        Returns:
            Complete prompt string for Claude CLI.
        """
        label = ROLE_TO_LABEL.get(role)
        label_filter = f"-l {label}" if label else ""

        return f"""# Autonomous Beads Worker - {role.upper()} Agent

## Worker ID: {worker_id}

You are a {role} agent in the multi-agent beads system. You operate in a CONTINUOUS POLLING LOOP - do NOT exit after completing one task.

## CRITICAL: Setup Commands (RUN IMMEDIATELY)

**STOP! Run these commands NOW before reading further:**

```bash
# 1. Define log function (uses WORKER_LOG_FILE and WORKER_ID env vars)
log() {{ echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$WORKER_ID] $1" >> "$WORKER_LOG_FILE"; }}

# 2. Define bd alias to use main project database (uses BD_ROOT env var)
# Workers run in isolated git worktrees - you MUST use the main project's beads database
alias bd='bd --db "$BD_ROOT/.beads/beads.db"'

# 3. Verify setup worked
log "SESSION_START"
bd ready {label_filter}
```

If `bd ready` shows "No .beads found" or errors, check that BD_ROOT is set: `echo $BD_ROOT`

## Session Protocol (CONTINUOUS POLLING)

After running setup commands above:

1. Initialize idle counter: `idle_count=0`

### MAIN WORK LOOP (repeat until max idle reached)

2. Check for work:
   ```bash
   bd ready {label_filter}
   ```

3. **If work is available:**
   - Reset idle counter: `idle_count=0`
   - Claim highest priority unblocked issue: `bd update <bead-id> --status=in_progress`
   - Log claim: `log "CLAIM: <bead-id> - <title>"`
   - Do the work following your role guidelines
   - Create PR if code changes, wait for CI, merge PR
   - Close bead: `bd close <bead-id> --reason="..."`
   - Log completion: `log "CLOSE: <bead-id>"`
   - **RETURN TO STEP 2** (check for more work)

4. **If NO work available:**
   - Increment idle counter
   - Log idle: `log "NO_WORK: poll $idle_count/{max_idle_polls}"`
   - If `idle_count < {max_idle_polls}`:
     - Wait {poll_interval_seconds} seconds: `sleep {poll_interval_seconds}`
     - **RETURN TO STEP 2**
   - If `idle_count >= {max_idle_polls}`:
     - Log exit: `log "SESSION_END: max idle polls reached"`
     - Exit cleanly

### Key Rules
- **NEVER exit immediately after "NO_WORK"** - always poll up to {max_idle_polls} times first
- **NEVER exit after completing a bead** - always check for more work
- Only exit after {max_idle_polls} consecutive polls ({max_idle_polls * poll_interval_seconds // 60} minutes) with no work
- Reset idle counter to 0 every time you successfully claim work

---

{prompt_content}
"""

    def _build_single_task_prompt(
        self,
        role: str,
        prompt_content: str,
        worker_id: str,
        bead_id: str,
    ) -> str:
        """Build a prompt for a worker assigned to a single specific bead.

        Unlike _build_worker_prompt which creates a polling loop, this builds
        a focused prompt that works on exactly one bead and exits. This is
        simpler and cheaper (less Claude usage) since the daemon handles
        dispatch.

        Args:
            role: Worker role.
            prompt_content: Content from role-specific prompt file.
            worker_id: Worker identifier.
            bead_id: The specific bead ID this worker must work on.

        Returns:
            Complete prompt string for Claude CLI.
        """
        return f"""# Autonomous Beads Worker - {role.upper()} Agent

## Worker ID: {worker_id}
## Assigned Bead: {bead_id}

You are a {role} agent in the multi-agent beads system. You have been assigned **one specific bead** to work on. Complete it and exit.

## CRITICAL: Setup Commands (RUN IMMEDIATELY)

**STOP! Run these commands NOW before reading further:**

```bash
# 1. Define log function (uses WORKER_LOG_FILE and WORKER_ID env vars)
log() {{ echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$WORKER_ID] $1" >> "$WORKER_LOG_FILE"; }}

# 2. Define bd alias to use main project database (uses BD_ROOT env var)
# Workers run in isolated git worktrees - you MUST use the main project's beads database
alias bd='bd --db "$BD_ROOT/.beads/beads.db"'

# 3. Log session start
log "SESSION_START"
```

If `bd` commands show "No .beads found" or errors, check that BD_ROOT is set: `echo $BD_ROOT`

## Session Protocol (SINGLE TASK)

After running setup commands above, work on your assigned bead:

### 1. Claim the bead

```bash
bd update {bead_id} --status=in_progress
log "CLAIM: {bead_id}"
```

### 2. Read the bead

```bash
bd show {bead_id}
log "READ: {bead_id}"
```

Read the full description and understand the acceptance criteria.

### 3. Do the work

```bash
log "WORK_START: <brief description of what you're doing>"
```

Follow your role guidelines below to complete the work.

### 4. Verify, commit, create PR (if code changes)

Follow the standard PR workflow from your role guidelines.

### 5. Close the bead (or hand off)

After work is complete and verified:

```bash
bd close {bead_id} --reason="<what was done>"
log "CLOSE: {bead_id}"
```

Or hand off to the next agent if your workflow requires it.

### 6. Sync and exit

```bash
bd sync --flush-only
log "SESSION_END: {bead_id}"
```

**EXIT IMMEDIATELY after completing this bead. Do NOT poll for more work.**

---

{prompt_content}
"""


class SubprocessSpawner(Spawner):
    """Cross-platform spawner using subprocess with PTY.

    This spawner works on macOS and Linux without requiring any GUI.
    It allocates a pseudo-terminal for Claude CLI interaction and
    captures output to log files.
    """

    def __init__(
        self,
        logs_dir: Path,
        claude_path: str | None = None,
        test_mode: bool = False,
        use_worktrees: bool = True,
    ) -> None:
        """Initialize the subprocess spawner.

        Args:
            logs_dir: Directory for worker log files.
            claude_path: Path to claude CLI. Auto-detected if None.
            test_mode: If True, use placeholder script instead of Claude CLI.
            use_worktrees: If True, create isolated git worktrees for each worker.
        """
        self.logs_dir = logs_dir
        self.test_mode = test_mode
        self.use_worktrees = use_worktrees
        self._claude_path = claude_path
        self._active_fds: dict[str, int] = {}
        self._worktrees: dict[str, tuple[Path, str]] = {}  # worker_id -> (path, branch)

    @property
    def claude_path(self) -> str:
        """Get claude path, finding it if not in test mode."""
        if self._claude_path:
            return self._claude_path
        if self.test_mode:
            return "python3"  # Use python for test mode
        self._claude_path = self._find_claude()
        return self._claude_path

    def _find_claude(self) -> str:
        """Find the claude CLI executable.

        Returns:
            Path to claude executable.

        Raises:
            SpawnerError: If claude not found.
        """
        claude_path = shutil.which("claude")
        if claude_path:
            return claude_path

        # Check common locations
        common_paths = [
            Path.home() / ".claude" / "local" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
        ]

        for path in common_paths:
            if path.exists() and os.access(path, os.X_OK):
                return str(path)

        raise SpawnerError(
            message="Claude CLI not found",
            detail="Ensure 'claude' is installed and in PATH",
        )

    def _ensure_logs_dir(self) -> None:
        """Ensure logs directory exists."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    async def spawn(
        self,
        role: str,
        project_path: str,
        worker_id: str,
        env_vars: dict[str, str] | None = None,
        bead_id: str | None = None,
    ) -> ProcessInfo:
        """Spawn a worker using subprocess with PTY.

        Creates a pseudo-terminal for the Claude CLI process, allowing
        proper interactive behavior while running headless.

        If use_worktrees is enabled, creates an isolated git worktree for
        the worker to operate in, preventing conflicts between concurrent workers.
        """
        project = Path(project_path).resolve()
        if not project.is_dir():
            raise SpawnerError(
                message=f"Project path not found: {project}",
                role=role,
                worker_id=worker_id,
            )

        # Validate worker_id to prevent shell injection
        validate_worker_id(worker_id)
        if bead_id is not None:
            validate_worker_id(bead_id)

        self._ensure_logs_dir()

        # Set up log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.logs_dir / f"{worker_id}_{timestamp}.log"

        # Create isolated worktree if enabled and in a git repo
        worktree_path: Path | None = None
        worktree_branch: str | None = None
        working_dir = project  # Default to project directory

        if self.use_worktrees and is_git_repo(project):
            try:
                worktree_path, worktree_branch = create_worktree(project, worker_id, bead_id)
                self._worktrees[worker_id] = (worktree_path, worktree_branch)
                working_dir = worktree_path
                logger.info(f"Worker {worker_id} will use worktree at {worktree_path}")
            except SpawnerError as e:
                logger.warning(
                    f"Failed to create worktree for {worker_id}, "
                    f"falling back to shared directory: {e.message}"
                )

        # Build environment
        env = os.environ.copy()
        env["WORKER_ID"] = worker_id
        # SESSION_ID is used by PROMPT.md's log function: ${SESSION_ID:-$$}
        # Without this, log entries would use different PIDs per subshell command,
        # breaking agent tracking in the dashboard
        env["SESSION_ID"] = worker_id
        env["WORKER_ROLE"] = role
        env["WORKER_PROJECT"] = str(project)
        env["WORKER_WORKING_DIR"] = str(working_dir)
        env["TERM"] = "xterm-256color"  # For PTY compatibility
        # Set per-worker log file path for isolated worker logging
        # Each worker logs to its own file: .mab/logs/{worker_id}_{timestamp}.log
        # Dashboard reads this from the database to display worker-specific logs
        env["WORKER_LOG_FILE"] = str(log_file)
        # BD_ROOT points to main project - workers use this to find the live beads database
        # This is the fallback when .beads symlink doesn't exist or is broken
        env["BD_ROOT"] = str(project)

        # Inject workflow configuration from town if available
        # This enables agents to know their handoff chain
        try:
            from mab.towns import TownManager, get_next_handoff

            town_manager = TownManager(Path.home() / ".mab")
            # Try to find town by project path
            towns = town_manager.list_towns(project_path=str(project))
            if towns:
                town = towns[0]  # Use first matching town
                env["TOWN_TEMPLATE"] = town.template
                if town.workflow:
                    env["TOWN_WORKFLOW"] = json_module.dumps(town.workflow)
                    next_handoff = get_next_handoff(role, town.workflow)
                    if next_handoff:
                        env["NEXT_HANDOFF"] = next_handoff
                    else:
                        env["NEXT_HANDOFF"] = ""  # Role not in workflow or last step
                else:
                    env["TOWN_WORKFLOW"] = "[]"
                    env["NEXT_HANDOFF"] = ""
                logger.debug(
                    f"Injected workflow config for {worker_id}: "
                    f"template={town.template}, next_handoff={env.get('NEXT_HANDOFF', '')}"
                )
        except Exception as e:
            # Don't fail spawn if town lookup fails
            logger.debug(f"Could not inject workflow config for {worker_id}: {e}")

        if worktree_path:
            env["WORKER_WORKTREE"] = str(worktree_path)
            env["WORKER_BRANCH"] = worktree_branch or ""

        if env_vars:
            env.update(env_vars)

        # Build command based on mode
        if self.test_mode:
            # Test mode: use placeholder script that maintains heartbeat
            heartbeat_file = (
                env_vars.get("WORKER_HEARTBEAT_FILE", "/tmp/heartbeat")
                if env_vars
                else "/tmp/heartbeat"
            )
            cmd = [
                "python3",
                "-c",
                f"""
import time
import os
from pathlib import Path

heartbeat_file = Path("{heartbeat_file}")
worker_id = os.environ.get('WORKER_ID', 'unknown')
print(f"Test worker {{worker_id}} started")

while True:
    heartbeat_file.write_text(str(time.time()))
    time.sleep(10)
""",
            ]
        else:
            # Production mode: use Claude CLI with prompt
            # Get prompt content
            prompt_path = self._get_prompt_path(role, project)
            try:
                prompt_content = prompt_path.read_text(encoding="utf-8")
            except OSError as e:
                raise SpawnerError(
                    message=f"Failed to read prompt file: {e}",
                    role=role,
                    worker_id=worker_id,
                ) from e

            # Build full prompt - use single-task prompt when bead_id assigned
            if bead_id:
                full_prompt = self._build_single_task_prompt(
                    role, prompt_content, worker_id, bead_id
                )
            else:
                full_prompt = self._build_worker_prompt(role, prompt_content, worker_id)

            # Using -p flag to pass initial prompt
            # --dangerously-skip-permissions allows workers to run bash commands
            # autonomously without interactive approval prompts
            cmd = [
                self.claude_path,
                "--dangerously-skip-permissions",
                "-p",
                full_prompt,
            ]

        logger.info(f"Spawning worker {worker_id} (role={role}) at {working_dir}")
        logger.debug(f"Command: {' '.join(cmd[:2])}...")
        logger.debug(f"Log file: {log_file}")

        # Initialize fd variables for cleanup tracking
        log_fd = -1
        master_fd = -1
        slave_fd = -1

        try:
            # Open log file for writing
            log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)

            # Write startup header to log file immediately
            startup_msg = (
                f"=== Worker Spawn Log ===\n"
                f"Worker ID: {worker_id}\n"
                f"Role: {role}\n"
                f"Project: {project}\n"
                f"Working Directory: {working_dir}\n"
                f"Started: {datetime.now().isoformat()}\n"
                f"{'=' * 40}\n\n"
            ).encode("utf-8")
            os.write(log_fd, startup_msg)

            # Create pseudo-terminal
            master_fd, slave_fd = pty.openpty()

            # Get slave device name for reopening in child
            slave_name = os.ttyname(slave_fd)

            def setup_pty_child() -> None:
                """Set up PTY in child process before exec.

                This ensures the PTY slave becomes the controlling terminal,
                which is required for proper terminal I/O from Claude CLI.
                """
                # Create new session (makes this process session leader)
                os.setsid()

                # Open the slave PTY to make it the controlling terminal
                # This must be done AFTER setsid() and the fd must be opened fresh
                child_slave = os.open(slave_name, os.O_RDWR)

                # Set the slave as controlling terminal (TIOCSCTTY)
                # The 0 argument means don't steal from other sessions
                try:
                    fcntl.ioctl(child_slave, termios.TIOCSCTTY, 0)
                except OSError:
                    # May fail on some systems, but usually not fatal
                    pass

                # Redirect standard streams to the PTY
                os.dup2(child_slave, 0)  # stdin
                os.dup2(child_slave, 1)  # stdout
                os.dup2(child_slave, 2)  # stderr

                # Close the extra fd (0,1,2 now point to the pty)
                if child_slave > 2:
                    os.close(child_slave)

            # Fork the process - use working_dir (worktree) instead of project
            # Note: preexec_fn handles session creation and PTY setup
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  # Will be overridden by preexec_fn
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(working_dir),
                env=env,
                preexec_fn=setup_pty_child,
            )

            # Close slave fd in parent (child has its own copy from preexec_fn)
            os.close(slave_fd)
            slave_fd = -1  # Mark as closed for cleanup tracking

            # Start async task to copy PTY output to log file
            asyncio.create_task(self._copy_pty_to_log(master_fd, log_fd, worker_id))

            # Give process a moment to start
            await asyncio.sleep(0.2)

            # Check if it crashed immediately
            if process.poll() is not None:
                # Process already exited - capture any output from PTY first
                exit_code = process.returncode
                captured_output = b""

                # Try to read any remaining output from PTY (non-blocking)
                try:
                    import select

                    while True:
                        readable, _, _ = select.select([master_fd], [], [], 0.1)
                        if not readable:
                            break
                        chunk = os.read(master_fd, 4096)
                        if not chunk:
                            break
                        captured_output += chunk
                except OSError:
                    pass

                # Write any captured output to log
                if captured_output:
                    os.write(log_fd, captured_output)

                # Write crash information to log file
                crash_msg = (
                    f"\n{'=' * 40}\n"
                    f"=== PROCESS CRASHED ===\n"
                    f"Exit Code: {exit_code}\n"
                    f"Crashed At: {datetime.now().isoformat()}\n"
                    f"{'=' * 40}\n"
                ).encode("utf-8")
                os.write(log_fd, crash_msg)

                os.close(master_fd)
                master_fd = -1  # Mark as closed for cleanup tracking
                os.close(log_fd)
                log_fd = -1  # Mark as closed for cleanup tracking
                raise SpawnerError(
                    message=f"Worker process exited immediately (code {exit_code})",
                    role=role,
                    worker_id=worker_id,
                    detail=f"Check log file: {log_file}",
                )

            # Track the master fd
            self._active_fds[worker_id] = master_fd

            # Record worker in database
            started_at = datetime.now()
            try:
                conn = db.get_db(project)
                db.insert_worker(
                    conn,
                    worker_id=worker_id,
                    role=role,
                    status="running",
                    project_path=str(project),
                    started_at=started_at,
                    pid=process.pid,
                    worktree_path=str(worktree_path) if worktree_path else None,
                    worktree_branch=worktree_branch,
                    log_file=str(log_file),
                )
                db.insert_event(conn, worker_id, "spawn")
                conn.close()
            except Exception as e:
                # Log but don't fail spawn if DB write fails
                logger.warning(f"Failed to record worker {worker_id} in database: {e}")

            return ProcessInfo(
                pid=process.pid,
                worker_id=worker_id,
                role=role,
                project_path=str(project),
                log_file=log_file,
                started_at=started_at.isoformat(),
                master_fd=master_fd,
                process=process,
                worktree_path=worktree_path,
                worktree_branch=worktree_branch,
            )

        except OSError as e:
            # Clean up file descriptors to prevent leaks
            for fd in (slave_fd, master_fd, log_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass  # Already closed or invalid
            # Clean up worktree if spawn failed
            if worktree_path and is_git_repo(project):
                git_root = get_git_root(project)
                if git_root:
                    if not remove_worktree(git_root, worktree_path):
                        logger.warning(
                            f"Failed to cleanup worktree at {worktree_path} after spawn failure. "
                            "Manual cleanup may be required."
                        )
                self._worktrees.pop(worker_id, None)
            raise SpawnerError(
                message=f"Failed to spawn process: {e}",
                role=role,
                worker_id=worker_id,
            ) from e

    @staticmethod
    def _read_pty(fd: int) -> bytes:
        """Read from PTY fd - used in executor to avoid lambda closure issues."""
        return os.read(fd, 4096)

    async def _copy_pty_to_log(
        self,
        master_fd: int,
        log_fd: int,
        worker_id: str,
    ) -> None:
        """Copy PTY output to log file asynchronously.

        Args:
            master_fd: PTY master file descriptor.
            log_fd: Log file descriptor.
            worker_id: Worker ID for logging.
        """
        # Use get_running_loop() to ensure we get the correct event loop
        # (get_event_loop() behavior is inconsistent in Python 3.10+)
        loop = asyncio.get_running_loop()
        bytes_written = 0
        read_errors = 0

        logger.debug(f"Starting PTY copy for {worker_id}, master_fd={master_fd}")

        try:
            while True:
                # Read from PTY (non-blocking via asyncio)
                # Use functools.partial or explicit function to avoid lambda closure issues
                try:
                    data = await loop.run_in_executor(
                        None,
                        self._read_pty,
                        master_fd,
                    )
                except OSError as e:
                    # PTY closed or error - common when process exits
                    read_errors += 1
                    if e.errno == 5:  # EIO - normal when slave closes
                        logger.debug(f"PTY closed for {worker_id} (process likely exited)")
                    else:
                        logger.debug(f"PTY read error for {worker_id}: {e}")
                    break

                if not data:
                    logger.debug(f"PTY EOF for {worker_id}")
                    break

                # Write to log file
                try:
                    written = os.write(log_fd, data)
                    bytes_written += written
                except OSError as e:
                    logger.error(f"Log write error for {worker_id}: {e}")
                    break

        except asyncio.CancelledError:
            # Write cancellation notice to log
            try:
                cancel_msg = (
                    f"\n{'=' * 40}\n"
                    f"=== LOG STREAM CANCELLED ===\n"
                    f"Time: {datetime.now().isoformat()}\n"
                    f"Bytes logged: {bytes_written}\n"
                    f"{'=' * 40}\n"
                ).encode("utf-8")
                os.write(log_fd, cancel_msg)
            except OSError:
                pass
        except Exception as e:
            logger.error(f"Error copying PTY output for {worker_id}: {e}")
            # Write error to log file
            try:
                error_msg = (
                    f"\n{'=' * 40}\n"
                    f"=== LOG STREAM ERROR ===\n"
                    f"Error: {e}\n"
                    f"Time: {datetime.now().isoformat()}\n"
                    f"{'=' * 40}\n"
                ).encode("utf-8")
                os.write(log_fd, error_msg)
            except OSError:
                pass
        finally:
            # Always write session end marker (helps debugging even with 0 bytes)
            try:
                end_msg = (
                    f"\n{'=' * 40}\n"
                    f"=== SESSION ENDED ===\n"
                    f"Time: {datetime.now().isoformat()}\n"
                    f"Total bytes logged: {bytes_written}\n"
                    f"Read errors: {read_errors}\n"
                    f"{'=' * 40}\n"
                ).encode("utf-8")
                os.write(log_fd, end_msg)
            except OSError:
                pass
            logger.debug(
                f"PTY copy ended for {worker_id}: {bytes_written} bytes, {read_errors} errors"
            )
            try:
                os.close(log_fd)
            except OSError:
                pass

    async def terminate(
        self,
        process_info: ProcessInfo,
        graceful: bool = True,
        timeout: float = 30.0,
    ) -> int | None:
        """Terminate a worker process."""
        import signal

        pid = process_info.pid
        worker_id = process_info.worker_id

        logger.info(f"Terminating worker {worker_id} (PID {pid})")

        try:
            if graceful:
                os.kill(pid, signal.SIGTERM)
                # Wait for process to exit
                start_time = asyncio.get_event_loop().time()
                while True:
                    try:
                        os.kill(pid, 0)  # Check if still running
                    except ProcessLookupError:
                        break

                    if asyncio.get_event_loop().time() - start_time > timeout:
                        logger.warning(f"Worker {worker_id} didn't exit, sending SIGKILL")
                        os.kill(pid, signal.SIGKILL)
                        break

                    await asyncio.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)

        except ProcessLookupError:
            pass  # Already dead

        # Clean up PTY fd
        master_fd = self._active_fds.pop(worker_id, None)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass

        # Clean up worktree if one was created
        worktree_info = self._worktrees.pop(worker_id, None)
        if worktree_info:
            worktree_path, _ = worktree_info
            project_path = Path(process_info.project_path)
            git_root = get_git_root(project_path)
            if git_root:
                logger.info(f"Cleaning up worktree at {worktree_path}")
                if not remove_worktree(git_root, worktree_path):
                    logger.warning(
                        f"Failed to cleanup worktree at {worktree_path} during termination. "
                        "Manual cleanup may be required."
                    )

        # Also check process_info for worktree (in case terminate called externally)
        if process_info.worktree_path and process_info.worktree_path.exists():
            project_path = Path(process_info.project_path)
            git_root = get_git_root(project_path)
            if git_root and worker_id not in self._worktrees:
                logger.info(
                    f"Cleaning up worktree from process_info at {process_info.worktree_path}"
                )
                if not remove_worktree(git_root, process_info.worktree_path):
                    logger.warning(
                        f"Failed to cleanup worktree at {process_info.worktree_path} "
                        "during termination. Manual cleanup may be required."
                    )

        # Get exit code
        exit_code: int | None = None
        if process_info.process:
            try:
                process_info.process.wait(timeout=1.0)
                exit_code = process_info.process.returncode
            except subprocess.TimeoutExpired:
                pass

        # Update worker status in database
        try:
            conn = db.get_db(Path(process_info.project_path))
            status = "stopped" if exit_code == 0 else "crashed"
            db.update_worker(
                conn,
                worker_id,
                status=status,
                stopped_at=datetime.now(),
                exit_code=exit_code,
            )
            db.insert_event(
                conn,
                worker_id,
                "terminate",
                message=f"Exit code: {exit_code}",
            )
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to update worker {worker_id} in database: {e}")

        return exit_code


class TmuxSpawner(Spawner):
    """Spawner using tmux sessions for worker isolation.

    This spawner creates a tmux session for each worker, providing:
    - Session persistence (survives daemon restart)
    - Easy manual inspection (tmux attach)
    - Named sessions for identification
    - Output capture to log files

    Requires tmux to be installed.
    """

    def __init__(
        self,
        logs_dir: Path,
        claude_path: str | None = None,
        tmux_path: str | None = None,
    ) -> None:
        """Initialize the tmux spawner.

        Args:
            logs_dir: Directory for worker log files.
            claude_path: Path to claude CLI. Auto-detected if None.
            tmux_path: Path to tmux. Auto-detected if None.
        """
        self.logs_dir = logs_dir
        self._claude_path = claude_path
        self._tmux_path = tmux_path

    @property
    def claude_path(self) -> str:
        """Get claude CLI path, finding it if needed."""
        if self._claude_path is None:
            self._claude_path = shutil.which("claude")
            if not self._claude_path:
                raise SpawnerError(
                    message="Claude CLI not found",
                    detail="Ensure 'claude' is installed and in PATH",
                )
        return self._claude_path

    @property
    def tmux_path(self) -> str:
        """Get tmux path, finding it if needed."""
        if self._tmux_path is None:
            self._tmux_path = shutil.which("tmux")
            if not self._tmux_path:
                raise SpawnerError(
                    message="tmux not found",
                    detail="Install tmux: brew install tmux (macOS) or apt install tmux (Linux)",
                )
        return self._tmux_path

    def _ensure_logs_dir(self) -> None:
        """Ensure logs directory exists."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _session_name(self, worker_id: str) -> str:
        """Get tmux session name for worker."""
        return f"mab-{worker_id}"

    async def spawn(
        self,
        role: str,
        project_path: str,
        worker_id: str,
        env_vars: dict[str, str] | None = None,
        bead_id: str | None = None,
    ) -> ProcessInfo:
        """Spawn a worker in a tmux session."""
        project = Path(project_path).resolve()
        if not project.is_dir():
            raise SpawnerError(
                message=f"Project path not found: {project}",
                role=role,
                worker_id=worker_id,
            )

        # Validate worker_id to prevent shell injection
        validate_worker_id(worker_id)

        self._ensure_logs_dir()

        # Get prompt content
        prompt_path = self._get_prompt_path(role, project)
        try:
            prompt_content = prompt_path.read_text(encoding="utf-8")
        except OSError as e:
            raise SpawnerError(
                message=f"Failed to read prompt file: {e}",
                role=role,
                worker_id=worker_id,
            ) from e

        # Build full prompt - use single-task prompt when bead_id assigned
        if bead_id:
            full_prompt = self._build_single_task_prompt(role, prompt_content, worker_id, bead_id)
        else:
            full_prompt = self._build_worker_prompt(role, prompt_content, worker_id)

        # Set up log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.logs_dir / f"{worker_id}_{timestamp}.log"

        session_name = self._session_name(worker_id)

        # Build environment exports
        # SESSION_ID is used by PROMPT.md's log function: ${SESSION_ID:-$$}
        # Without this, log entries would use different PIDs per subshell command
        env_exports = f"""
export WORKER_ID="{worker_id}"
export SESSION_ID="{worker_id}"
export WORKER_ROLE="{role}"
export WORKER_PROJECT="{project}"
"""
        if env_vars:
            for key, value in env_vars.items():
                env_exports += f'export {key}="{value}"\n'

        # Escape prompt for shell
        escaped_prompt = (
            full_prompt.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )

        # Build command to run in tmux
        # --dangerously-skip-permissions allows workers to run bash commands
        # autonomously without interactive approval prompts
        tmux_cmd = (
            f'cd "{project}" && {env_exports.strip()} && '
            f'{self.claude_path} --dangerously-skip-permissions -p "{escaped_prompt}" 2>&1 | tee -a "{log_file}"'
        )

        logger.info(f"Creating tmux session {session_name} for worker {worker_id}")

        try:
            # Create new tmux session
            result = subprocess.run(
                [
                    self.tmux_path,
                    "new-session",
                    "-d",  # Detached
                    "-s",
                    session_name,
                    "-x",
                    "200",  # Width
                    "-y",
                    "50",  # Height
                    "bash",
                    "-c",
                    tmux_cmd,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                raise SpawnerError(
                    message=f"Failed to create tmux session: {result.stderr}",
                    role=role,
                    worker_id=worker_id,
                )

            # Get the PID of the tmux session
            pid_result = subprocess.run(
                [
                    self.tmux_path,
                    "list-panes",
                    "-t",
                    session_name,
                    "-F",
                    "#{pane_pid}",
                ],
                capture_output=True,
                text=True,
            )

            if pid_result.returncode == 0 and pid_result.stdout.strip():
                pid = int(pid_result.stdout.strip().split("\n")[0])
            else:
                pid = 0  # Unknown PID

            # Record worker in database
            started_at = datetime.now()
            try:
                conn = db.get_db(project)
                db.insert_worker(
                    conn,
                    worker_id=worker_id,
                    role=role,
                    status="running",
                    project_path=str(project),
                    started_at=started_at,
                    pid=pid if pid > 0 else None,
                    log_file=str(log_file),
                )
                db.insert_event(conn, worker_id, "spawn")
                conn.close()
            except Exception as e:
                # Log but don't fail spawn if DB write fails
                logger.warning(f"Failed to record worker {worker_id} in database: {e}")

            return ProcessInfo(
                pid=pid,
                worker_id=worker_id,
                role=role,
                project_path=str(project),
                log_file=log_file,
                started_at=started_at.isoformat(),
            )

        except subprocess.TimeoutExpired:
            raise SpawnerError(
                message="Timeout creating tmux session",
                role=role,
                worker_id=worker_id,
            )
        except OSError as e:
            raise SpawnerError(
                message=f"Failed to run tmux: {e}",
                role=role,
                worker_id=worker_id,
            ) from e

    async def terminate(
        self,
        process_info: ProcessInfo,
        graceful: bool = True,
        timeout: float = 30.0,
    ) -> int | None:
        """Terminate a worker's tmux session."""
        session_name = self._session_name(process_info.worker_id)
        worker_id = process_info.worker_id

        logger.info(f"Killing tmux session {session_name}")

        exit_code: int | None = None
        try:
            result = subprocess.run(
                [self.tmux_path, "kill-session", "-t", session_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            exit_code = 0 if result.returncode == 0 else None

        except (subprocess.TimeoutExpired, OSError):
            pass

        # Update worker status in database
        try:
            conn = db.get_db(Path(process_info.project_path))
            status = "stopped" if exit_code == 0 else "crashed"
            db.update_worker(
                conn,
                worker_id,
                status=status,
                stopped_at=datetime.now(),
                exit_code=exit_code,
            )
            db.insert_event(
                conn,
                worker_id,
                "terminate",
                message=f"Exit code: {exit_code}",
            )
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to update worker {worker_id} in database: {e}")

        return exit_code


def get_spawner(
    spawner_type: str = "subprocess",
    logs_dir: Path | None = None,
    test_mode: bool = False,
    **kwargs: Any,
) -> Spawner:
    """Get a spawner instance by type.

    Args:
        spawner_type: Type of spawner ("subprocess" or "tmux").
        logs_dir: Directory for log files.
        test_mode: If True, use placeholder scripts instead of Claude CLI.
        **kwargs: Additional arguments for spawner.

    Returns:
        Spawner instance.

    Raises:
        SpawnerError: If spawner type is invalid.
    """
    if logs_dir is None:
        logs_dir = Path.home() / ".mab" / "logs"

    if spawner_type == "subprocess":
        return SubprocessSpawner(logs_dir=logs_dir, test_mode=test_mode, **kwargs)
    elif spawner_type == "tmux":
        return TmuxSpawner(logs_dir=logs_dir, **kwargs)
    else:
        raise SpawnerError(
            message=f"Invalid spawner type: {spawner_type}",
            detail="Valid types: subprocess, tmux",
        )


def is_tmux_available() -> bool:
    """Check if tmux is available on this system."""
    return shutil.which("tmux") is not None


def is_claude_available() -> bool:
    """Check if claude CLI is available on this system."""
    if shutil.which("claude"):
        return True

    common_paths = [
        Path.home() / ".claude" / "local" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    ]

    for path in common_paths:
        if path.exists() and os.access(path, os.X_OK):
            return True

    return False
