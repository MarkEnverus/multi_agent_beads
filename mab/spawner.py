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
import logging
import os
import pty
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
    """
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
        remove_worktree(git_root, worktree_path)

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
    ) -> ProcessInfo:
        """Spawn a new worker process.

        Args:
            role: Worker role (dev, qa, tech_lead, manager, reviewer).
            project_path: Path to the project directory.
            worker_id: Unique identifier for this worker.
            env_vars: Additional environment variables.

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
        """Build the full worker prompt with role context.

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

## CRITICAL: Setup Commands (RUN FIRST)

**IMPORTANT**: You MUST run these setup commands FIRST before doing anything else.

### 1. Define log function with absolute path
```bash
log() {{ echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$$] $1" >> "$WORKER_LOG_FILE"; }}
```

### 2. Define bd alias to use main project database
Workers run in isolated git worktrees that have stale `.beads` data. You MUST use the main project's beads database for all bd commands.

```bash
alias bd='bd --db "$WORKER_PROJECT/.beads/beads.db"'
```

This ensures all `bd` commands query the live beads database, not the worktree's stale copy.

## Session Protocol (CONTINUOUS POLLING)

1. **FIRST**: Run the two setup commands above (log function AND bd alias)
2. Log session start: `log "SESSION_START"`
3. Initialize idle counter: `idle_count=0`

### MAIN WORK LOOP (repeat until max idle reached)

4. Check for work:
   ```bash
   bd ready {label_filter}
   ```

5. **If work is available:**
   - Reset idle counter: `idle_count=0`
   - Claim highest priority unblocked issue: `bd update <bead-id> --status=in_progress`
   - Log claim: `log "CLAIM: <bead-id> - <title>"`
   - Do the work following your role guidelines
   - Create PR if code changes, wait for CI, merge PR
   - Close bead: `bd close <bead-id> --reason="..."`
   - Log completion: `log "CLOSE: <bead-id>"`
   - **RETURN TO STEP 4** (check for more work)

6. **If NO work available:**
   - Increment idle counter
   - Log idle: `log "NO_WORK: poll $idle_count/{max_idle_polls}"`
   - If `idle_count < {max_idle_polls}`:
     - Wait {poll_interval_seconds} seconds: `sleep {poll_interval_seconds}`
     - **RETURN TO STEP 4**
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
        env["WORKER_ROLE"] = role
        env["WORKER_PROJECT"] = str(project)
        env["WORKER_WORKING_DIR"] = str(working_dir)
        env["TERM"] = "xterm-256color"  # For PTY compatibility
        # Set absolute path to main project's claude.log for centralized logging
        # This ensures workers in worktrees still log to the dashboard-monitored file
        env["WORKER_LOG_FILE"] = str(project / "claude.log")

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

            # Build full prompt
            full_prompt = self._build_worker_prompt(role, prompt_content, worker_id)

            # Using -p flag to pass initial prompt
            cmd = [
                self.claude_path,
                "-p",
                full_prompt,
            ]

        logger.info(f"Spawning worker {worker_id} (role={role}) at {working_dir}")
        logger.debug(f"Command: {' '.join(cmd[:2])}...")
        logger.debug(f"Log file: {log_file}")

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

            # Fork the process - use working_dir (worktree) instead of project
            process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(working_dir),
                env=env,
                start_new_session=True,
            )

            # Close slave fd in parent (worker process uses it)
            os.close(slave_fd)

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
                os.close(log_fd)
                raise SpawnerError(
                    message=f"Worker process exited immediately (code {exit_code})",
                    role=role,
                    worker_id=worker_id,
                    detail=f"Check log file: {log_file}",
                )

            # Track the master fd
            self._active_fds[worker_id] = master_fd

            return ProcessInfo(
                pid=process.pid,
                worker_id=worker_id,
                role=role,
                project_path=str(project),
                log_file=log_file,
                started_at=datetime.now().isoformat(),
                master_fd=master_fd,
                process=process,
                worktree_path=worktree_path,
                worktree_branch=worktree_branch,
            )

        except OSError as e:
            # Clean up worktree if spawn failed
            if worktree_path and is_git_repo(project):
                git_root = get_git_root(project)
                if git_root:
                    remove_worktree(git_root, worktree_path)
                self._worktrees.pop(worker_id, None)
            raise SpawnerError(
                message=f"Failed to spawn process: {e}",
                role=role,
                worker_id=worker_id,
            ) from e

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
        loop = asyncio.get_event_loop()
        bytes_written = 0
        read_errors = 0

        try:
            while True:
                # Read from PTY (non-blocking via asyncio)
                try:
                    data = await loop.run_in_executor(
                        None,
                        lambda: os.read(master_fd, 4096),
                    )
                except OSError as e:
                    # PTY closed - write final message
                    read_errors += 1
                    logger.debug(f"PTY read error for {worker_id}: {e}")
                    break

                if not data:
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
            # Write session end marker if we logged anything
            if bytes_written > 0:
                try:
                    end_msg = (
                        f"\n{'=' * 40}\n"
                        f"=== SESSION ENDED ===\n"
                        f"Time: {datetime.now().isoformat()}\n"
                        f"Total bytes logged: {bytes_written}\n"
                        f"{'=' * 40}\n"
                    ).encode("utf-8")
                    os.write(log_fd, end_msg)
                except OSError:
                    pass
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
                remove_worktree(git_root, worktree_path)

        # Also check process_info for worktree (in case terminate called externally)
        if process_info.worktree_path and process_info.worktree_path.exists():
            project_path = Path(process_info.project_path)
            git_root = get_git_root(project_path)
            if git_root and worker_id not in self._worktrees:
                logger.info(
                    f"Cleaning up worktree from process_info at {process_info.worktree_path}"
                )
                remove_worktree(git_root, process_info.worktree_path)

        # Get exit code
        if process_info.process:
            try:
                process_info.process.wait(timeout=1.0)
                return process_info.process.returncode
            except subprocess.TimeoutExpired:
                return None

        return None


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
    ) -> ProcessInfo:
        """Spawn a worker in a tmux session."""
        project = Path(project_path).resolve()
        if not project.is_dir():
            raise SpawnerError(
                message=f"Project path not found: {project}",
                role=role,
                worker_id=worker_id,
            )

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

        # Build full prompt
        full_prompt = self._build_worker_prompt(role, prompt_content, worker_id)

        # Set up log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.logs_dir / f"{worker_id}_{timestamp}.log"

        session_name = self._session_name(worker_id)

        # Build environment exports
        env_exports = f"""
export WORKER_ID="{worker_id}"
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
        tmux_cmd = (
            f'cd "{project}" && {env_exports.strip()} && '
            f'{self.claude_path} -p "{escaped_prompt}" 2>&1 | tee -a "{log_file}"'
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

            return ProcessInfo(
                pid=pid,
                worker_id=worker_id,
                role=role,
                project_path=str(project),
                log_file=log_file,
                started_at=datetime.now().isoformat(),
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

        logger.info(f"Killing tmux session {session_name}")

        try:
            result = subprocess.run(
                [self.tmux_path, "kill-session", "-t", session_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return 0 if result.returncode == 0 else None

        except (subprocess.TimeoutExpired, OSError):
            return None


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
