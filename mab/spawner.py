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
    ) -> str:
        """Build the full worker prompt with role context.

        Args:
            role: Worker role.
            prompt_content: Content from role-specific prompt file.
            worker_id: Worker identifier.

        Returns:
            Complete prompt string for Claude CLI.
        """
        label = ROLE_TO_LABEL.get(role)
        label_filter = f"-l {label}" if label else ""

        return f"""# Autonomous Beads Worker - {role.upper()} Agent

## Worker ID: {worker_id}

You are a {role} agent in the multi-agent beads system. Follow the role-specific prompt below, then find work using:
    bd ready {label_filter}

## Session Protocol

1. Log session start
2. Find work with: bd ready {label_filter}
3. Claim highest priority unblocked issue
4. Do the work following your role guidelines
5. Create PR if code changes
6. Wait for CI, merge PR
7. Close bead
8. Exit cleanly

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
    ) -> None:
        """Initialize the subprocess spawner.

        Args:
            logs_dir: Directory for worker log files.
            claude_path: Path to claude CLI. Auto-detected if None.
            test_mode: If True, use placeholder script instead of Claude CLI.
        """
        self.logs_dir = logs_dir
        self.test_mode = test_mode
        self._claude_path = claude_path
        self._active_fds: dict[str, int] = {}

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
    ) -> ProcessInfo:
        """Spawn a worker using subprocess with PTY.

        Creates a pseudo-terminal for the Claude CLI process, allowing
        proper interactive behavior while running headless.
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

        # Build environment
        env = os.environ.copy()
        env["WORKER_ID"] = worker_id
        env["WORKER_ROLE"] = role
        env["WORKER_PROJECT"] = str(project)
        env["TERM"] = "xterm-256color"  # For PTY compatibility

        if env_vars:
            env.update(env_vars)

        # Build command based on mode
        if self.test_mode:
            # Test mode: use placeholder script that maintains heartbeat
            heartbeat_file = env_vars.get("WORKER_HEARTBEAT_FILE", "/tmp/heartbeat") if env_vars else "/tmp/heartbeat"
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

            # Using --print flag to pass initial prompt
            cmd = [
                self.claude_path,
                "--print",
                full_prompt,
            ]

        logger.info(f"Spawning worker {worker_id} (role={role}) at {project}")
        logger.debug(f"Command: {' '.join(cmd[:2])}...")
        logger.debug(f"Log file: {log_file}")

        try:
            # Open log file for writing
            log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)

            # Create pseudo-terminal
            master_fd, slave_fd = pty.openpty()

            # Fork the process
            process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(project),
                env=env,
                start_new_session=True,
            )

            # Close slave fd in parent (worker process uses it)
            os.close(slave_fd)

            # Start async task to copy PTY output to log file
            asyncio.create_task(
                self._copy_pty_to_log(master_fd, log_fd, worker_id)
            )

            # Give process a moment to start
            await asyncio.sleep(0.2)

            # Check if it crashed immediately
            if process.poll() is not None:
                # Process already exited
                os.close(master_fd)
                os.close(log_fd)
                raise SpawnerError(
                    message=f"Worker process exited immediately (code {process.returncode})",
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
            )

        except OSError as e:
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

        try:
            while True:
                # Read from PTY (non-blocking via asyncio)
                try:
                    data = await loop.run_in_executor(
                        None,
                        lambda: os.read(master_fd, 4096),
                    )
                except OSError:
                    break

                if not data:
                    break

                # Write to log file
                try:
                    os.write(log_fd, data)
                except OSError:
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error copying PTY output for {worker_id}: {e}")
        finally:
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
            f'{self.claude_path} --print "{escaped_prompt}" 2>&1 | tee -a "{log_file}"'
        )

        logger.info(f"Creating tmux session {session_name} for worker {worker_id}")

        try:
            # Create new tmux session
            result = subprocess.run(
                [
                    self.tmux_path,
                    "new-session",
                    "-d",  # Detached
                    "-s", session_name,
                    "-x", "200",  # Width
                    "-y", "50",   # Height
                    "bash", "-c", tmux_cmd,
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
                    "-t", session_name,
                    "-F", "#{pane_pid}",
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
