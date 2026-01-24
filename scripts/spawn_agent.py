#!/usr/bin/env python3
"""Spawn role-based Claude agents in new terminal windows.

Usage:
    python scripts/spawn_agent.py developer --instance 1 --repo /path/to/repo
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

VALID_ROLES = ["developer", "qa", "tech_lead", "manager", "reviewer"]

ROLE_TO_PROMPT = {
    "developer": "DEVELOPER.md",
    "qa": "QA.md",
    "tech_lead": "TECH_LEAD.md",
    "manager": "MANAGER.md",
    "reviewer": "CODE_REVIEWER.md",
}

ROLE_TO_LABEL = {
    "developer": "dev",
    "qa": "qa",
    "tech_lead": "architecture",
    "manager": None,
    "reviewer": "review",
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


def get_prompt_path(role: str, repo_path: Path) -> Path:
    """Get the path to the role-specific prompt file.

    Args:
        role: Agent role.
        repo_path: Path to the repository.

    Returns:
        Path to the prompt file.

    Raises:
        AgentSpawnError: If the role is invalid.
    """
    if role not in ROLE_TO_PROMPT:
        raise AgentSpawnError(
            message=f"Invalid role: {role}. Valid roles: {', '.join(VALID_ROLES)}",
            role=role,
        )
    prompt_file = ROLE_TO_PROMPT[role]
    return repo_path / "prompts" / prompt_file


def validate_prompt_exists(prompt_path: Path, role: str) -> None:
    """Ensure the prompt file exists.

    Args:
        prompt_path: Path to check.
        role: Role for error context.

    Raises:
        AgentSpawnError: If the prompt file doesn't exist.
    """
    if not prompt_path.exists():
        logger.error("Prompt file not found: %s", prompt_path)
        raise AgentSpawnError(
            message=f"Prompt file not found: {prompt_path}",
            role=role,
            detail=f"Expected file at: {prompt_path}",
        )

    if not prompt_path.is_file():
        logger.error("Prompt path is not a file: %s", prompt_path)
        raise AgentSpawnError(
            message=f"Prompt path is not a file: {prompt_path}",
            role=role,
        )


def validate_repo_path(repo_path: Path) -> None:
    """Validate the repository path exists and is a directory.

    Args:
        repo_path: Path to validate.

    Raises:
        AgentSpawnError: If the path is invalid.
    """
    if not repo_path.exists():
        logger.error("Repository path not found: %s", repo_path)
        raise AgentSpawnError(
            message=f"Repository path not found: {repo_path}",
            detail="Please provide a valid path to the repository",
        )

    if not repo_path.is_dir():
        logger.error("Repository path is not a directory: %s", repo_path)
        raise AgentSpawnError(
            message=f"Repository path is not a directory: {repo_path}",
        )


def spawn_agent_macos(
    role: str,
    instance: int,
    repo_path: Path,
    prompt_path: Path,
) -> None:
    """Spawn agent in a new macOS Terminal window.

    Args:
        role: Agent role.
        instance: Instance number.
        repo_path: Path to the repository.
        prompt_path: Path to the prompt file.

    Raises:
        AgentSpawnError: If spawning fails.
    """
    log_file = repo_path / "logs" / f"{role}_{instance}.log"

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        logger.error("Permission denied creating log directory: %s", log_file.parent)
        raise AgentSpawnError(
            message="Permission denied creating log directory",
            role=role,
            instance=instance,
            detail=f"Could not create: {log_file.parent}",
        ) from None
    except OSError as e:
        logger.error("Error creating log directory: %s", e)
        raise AgentSpawnError(
            message=f"Error creating log directory: {e}",
            role=role,
            instance=instance,
        ) from None

    env_exports = f"""
export AGENT_ROLE="{role}"
export AGENT_INSTANCE="{instance}"
export AGENT_LOG_FILE="{log_file}"
"""

    label = ROLE_TO_LABEL.get(role)
    label_filter = f"-l {label}" if label else ""

    try:
        prompt_content = prompt_path.read_text(encoding="utf-8")
    except PermissionError:
        logger.error("Permission denied reading prompt file: %s", prompt_path)
        raise AgentSpawnError(
            message="Permission denied reading prompt file",
            role=role,
            instance=instance,
            detail=f"Could not read: {prompt_path}",
        ) from None
    except OSError as e:
        logger.error("Error reading prompt file: %s", e)
        raise AgentSpawnError(
            message=f"Error reading prompt file: {e}",
            role=role,
            instance=instance,
        ) from None

    agent_prompt = f"""# Autonomous Beads Worker - {role.upper()} Agent (Instance {instance})

## Your Role: {role.upper()}

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

    escaped_prompt = (
        agent_prompt.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )

    terminal_command = (
        f'cd "{repo_path}" && {env_exports.strip()} && '
        f'claude --print-system-prompt "{escaped_prompt}" '
    )

    applescript = f'''
tell application "Terminal"
    activate
    set newWindow to do script "{terminal_command}"
    set custom title of front window to "{role.upper()} Agent #{instance}"
end tell
'''

    logger.info("Spawning %s agent (instance %d)", role, instance)
    logger.debug("Repo: %s", repo_path)
    logger.debug("Prompt: %s", prompt_path)
    logger.debug("Log: %s", log_file)

    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.error("AppleScript error: %s", stderr)
            raise AgentSpawnError(
                message=f"Failed to spawn agent: {stderr}",
                role=role,
                instance=instance,
                detail="AppleScript execution failed",
            )

        logger.info(
            "Successfully spawned %s agent (instance %d) in new Terminal window",
            role,
            instance,
        )
        print(f"Spawned {role} agent (instance {instance}) in new Terminal window")
        print(f"  Repo: {repo_path}")
        print(f"  Prompt: {prompt_path}")
        print(f"  Log: {log_file}")

    except subprocess.TimeoutExpired:
        logger.error("Timeout spawning agent")
        raise AgentSpawnError(
            message="Timeout spawning agent - Terminal may not be responding",
            role=role,
            instance=instance,
        ) from None

    except FileNotFoundError:
        logger.error("osascript command not found")
        raise AgentSpawnError(
            message="osascript command not found - is this macOS?",
            role=role,
            instance=instance,
        ) from None

    except OSError as e:
        logger.error("Error executing osascript: %s", e)
        raise AgentSpawnError(
            message=f"Error executing osascript: {e}",
            role=role,
            instance=instance,
        ) from None


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        description="Spawn role-based Claude agents in new terminal windows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/spawn_agent.py developer
    python scripts/spawn_agent.py qa --instance 2
    python scripts/spawn_agent.py tech_lead --repo /path/to/repo
    python scripts/spawn_agent.py manager --instance 1 --repo .
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
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        repo_path = Path(args.repo).resolve()
        validate_repo_path(repo_path)

        prompt_path = get_prompt_path(args.role, repo_path)
        validate_prompt_exists(prompt_path, args.role)

        if sys.platform == "darwin":
            spawn_agent_macos(args.role, args.instance, repo_path, prompt_path)
        else:
            logger.error("Platform '%s' not supported", sys.platform)
            print(
                f"Error: Platform '{sys.platform}' not supported. "
                "Only macOS is implemented.",
                file=sys.stderr,
            )
            print("For Linux/Windows, please spawn agents manually.", file=sys.stderr)
            return 1

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
