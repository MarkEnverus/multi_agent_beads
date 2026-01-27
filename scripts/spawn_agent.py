#!/usr/bin/env python3
"""Spawn role-based Claude agents using cross-platform spawner.

This script replaces the macOS-only AppleScript implementation with
a cross-platform solution that works on both macOS and Linux.

Usage:
    # Headless mode (default) - works on macOS and Linux
    python scripts/spawn_agent.py developer --instance 1 --repo /path/to/repo

    # With tmux (if installed)
    python scripts/spawn_agent.py qa --instance 2 --spawner tmux

    # Legacy Terminal mode (macOS only, for development)
    python scripts/spawn_agent.py tech_lead --mode terminal
"""

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mab.spawner import (
    ROLE_TO_LABEL,
    ROLE_TO_PROMPT,
    SpawnerError,
    get_spawner,
    is_claude_available,
    is_tmux_available,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Supported roles
VALID_ROLES = ["developer", "dev", "qa", "tech_lead", "manager", "reviewer"]

# Map CLI role names to internal names
ROLE_ALIASES = {
    "developer": "dev",
    "dev": "dev",
    "qa": "qa",
    "tech_lead": "tech_lead",
    "manager": "manager",
    "reviewer": "reviewer",
}


class AgentSpawnError(Exception):
    """Raised when agent spawning fails."""

    def __init__(
        self,
        message: str,
        role: str | None = None,
        instance: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.message = message
        self.role = role
        self.instance = instance
        self.detail = detail
        super().__init__(message)


def validate_repo_path(repo_path: Path) -> None:
    """Validate the repository path exists and is a directory."""
    if not repo_path.exists():
        raise AgentSpawnError(
            message=f"Repository path not found: {repo_path}",
            detail="Please provide a valid path to the repository",
        )

    if not repo_path.is_dir():
        raise AgentSpawnError(
            message=f"Repository path is not a directory: {repo_path}",
        )


def get_prompt_path(role: str, repo_path: Path) -> Path:
    """Get the path to the role-specific prompt file."""
    if role not in ROLE_TO_PROMPT:
        raise AgentSpawnError(
            message=f"Invalid role: {role}",
            role=role,
            detail=f"Valid roles: {', '.join(VALID_ROLES)}",
        )

    prompt_file = ROLE_TO_PROMPT[role]
    return repo_path / "prompts" / prompt_file


def validate_prompt_exists(prompt_path: Path, role: str) -> None:
    """Ensure the prompt file exists."""
    if not prompt_path.exists():
        raise AgentSpawnError(
            message=f"Prompt file not found: {prompt_path}",
            role=role,
            detail="Ensure prompts/ directory contains role-specific prompts",
        )


def generate_worker_id(role: str, instance: int) -> str:
    """Generate a worker ID from role and instance."""
    import uuid
    short_uuid = str(uuid.uuid4())[:8]
    return f"worker-{role}-{instance}-{short_uuid}"


async def spawn_headless(
    role: str,
    instance: int,
    repo_path: Path,
    spawner_type: str = "subprocess",
) -> None:
    """Spawn agent using cross-platform headless spawner.

    This works on both macOS and Linux without requiring a GUI.
    """
    # Validate inputs
    validate_repo_path(repo_path)

    internal_role = ROLE_ALIASES.get(role, role)
    prompt_path = get_prompt_path(internal_role, repo_path)
    validate_prompt_exists(prompt_path, role)

    # Set up logs directory
    logs_dir = repo_path / ".mab" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Generate worker ID
    worker_id = generate_worker_id(internal_role, instance)

    logger.info(f"Spawning {role} agent (instance {instance}) headlessly")
    logger.debug(f"Repo: {repo_path}")
    logger.debug(f"Prompt: {prompt_path}")
    logger.debug(f"Spawner: {spawner_type}")

    try:
        spawner = get_spawner(spawner_type, logs_dir=logs_dir)
        process_info = await spawner.spawn(
            role=internal_role,
            project_path=str(repo_path),
            worker_id=worker_id,
            env_vars={
                "AGENT_INSTANCE": str(instance),
            },
        )

        print(f"Spawned {role} agent (instance {instance})")
        print(f"  Worker ID: {worker_id}")
        print(f"  PID: {process_info.pid}")
        print(f"  Log: {process_info.log_file}")
        print(f"  Repo: {repo_path}")

        if spawner_type == "tmux":
            print(f"\nTo attach: tmux attach -t mab-{worker_id}")

    except SpawnerError as e:
        raise AgentSpawnError(
            message=e.message,
            role=role,
            instance=instance,
            detail=e.detail,
        ) from e


def spawn_terminal_macos(
    role: str,
    instance: int,
    repo_path: Path,
) -> None:
    """Legacy: Spawn agent in a new macOS Terminal window using AppleScript.

    This is kept for development/debugging purposes where you want
    to see the agent in a visible Terminal window.
    """
    if sys.platform != "darwin":
        raise AgentSpawnError(
            message="Terminal mode only supported on macOS",
            role=role,
            instance=instance,
            detail="Use headless mode (--mode headless) on other platforms",
        )

    validate_repo_path(repo_path)

    internal_role = ROLE_ALIASES.get(role, role)
    prompt_path = get_prompt_path(internal_role, repo_path)
    validate_prompt_exists(prompt_path, role)

    # Create log directory
    log_file = repo_path / "logs" / f"{role}_{instance}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Read prompt content
    prompt_content = prompt_path.read_text(encoding="utf-8")

    # Build worker prompt
    label = ROLE_TO_LABEL.get(internal_role)
    label_filter = f"-l {label}" if label else ""
    worker_id = generate_worker_id(internal_role, instance)

    agent_prompt = f"""# Autonomous Beads Worker - {role.upper()} Agent (Instance {instance})

## Worker ID: {worker_id}

You are a {role} agent. Follow the role-specific prompt below, then find work using:
    bd ready {label_filter}

## Session Protocol

1. Log session start
2. Find work with: bd ready {label_filter}
3. Claim highest priority unblocked issue
4. Do the work following your role guidelines
5. Create PR if code changes
6. Wait for CI, merge PR
7. Close bead
8. Exit

---

{prompt_content}
"""

    # Escape for shell
    escaped_prompt = (
        agent_prompt.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )

    env_exports = f"""
export AGENT_ROLE="{role}"
export AGENT_INSTANCE="{instance}"
export AGENT_LOG_FILE="{log_file}"
export WORKER_ID="{worker_id}"
"""

    terminal_command = (
        f'cd "{repo_path}" && {env_exports.strip()} && '
        f'claude --print "{escaped_prompt}"'
    )

    applescript = f'''
tell application "Terminal"
    activate
    set newWindow to do script "{terminal_command}"
    set custom title of front window to "{role.upper()} Agent #{instance}"
end tell
'''

    logger.info(f"Spawning {role} agent (instance {instance}) in Terminal")

    result = subprocess.run(
        ["osascript", "-e", applescript],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise AgentSpawnError(
            message=f"Failed to spawn agent: {result.stderr.strip()}",
            role=role,
            instance=instance,
            detail="AppleScript execution failed",
        )

    print(f"Spawned {role} agent (instance {instance}) in new Terminal window")
    print(f"  Worker ID: {worker_id}")
    print(f"  Repo: {repo_path}")
    print(f"  Prompt: {prompt_path}")
    print(f"  Log: {log_file}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Spawn role-based Claude agents (cross-platform).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Headless (default, works on macOS and Linux)
    python scripts/spawn_agent.py developer
    python scripts/spawn_agent.py qa --instance 2
    python scripts/spawn_agent.py tech_lead --repo /path/to/repo

    # Using tmux for session management
    python scripts/spawn_agent.py developer --spawner tmux

    # Legacy Terminal mode (macOS only)
    python scripts/spawn_agent.py manager --mode terminal

Spawner types:
    subprocess  - Headless using PTY (default, cross-platform)
    tmux        - Uses tmux sessions for isolation (requires tmux)
        """,
    )

    parser.add_argument(
        "role",
        choices=VALID_ROLES,
        help="Agent role to spawn",
    )
    parser.add_argument(
        "--instance",
        type=int,
        default=1,
        help="Instance number for this agent (default: 1)",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=".",
        help="Path to repository (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        choices=["headless", "terminal"],
        default="headless",
        help="Spawn mode: headless (default) or terminal (macOS only)",
    )
    parser.add_argument(
        "--spawner",
        choices=["subprocess", "tmux"],
        default="subprocess",
        help="Spawner type for headless mode (default: subprocess)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check prerequisites
    if not is_claude_available():
        print("Error: Claude CLI not found", file=sys.stderr)
        print("Ensure 'claude' is installed and in PATH", file=sys.stderr)
        return 1

    if args.spawner == "tmux" and not is_tmux_available():
        print("Error: tmux not found", file=sys.stderr)
        print("Install tmux or use --spawner subprocess", file=sys.stderr)
        return 1

    try:
        repo_path = Path(args.repo).resolve()

        if args.mode == "terminal":
            spawn_terminal_macos(args.role, args.instance, repo_path)
        else:
            asyncio.run(
                spawn_headless(
                    args.role,
                    args.instance,
                    repo_path,
                    spawner_type=args.spawner,
                )
            )

        return 0

    except AgentSpawnError as e:
        logger.error("Agent spawn failed: %s", e.message)
        error_parts = [f"Error: {e.message}"]
        if e.role:
            error_parts.append(f"Role: {e.role}")
        if e.instance is not None:
            error_parts.append(f"Instance: {e.instance}")
        if e.detail:
            error_parts.append(f"Detail: {e.detail}")
        print("\n".join(error_parts), file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        print("\nInterrupted", file=sys.stderr)
        return 130

    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
