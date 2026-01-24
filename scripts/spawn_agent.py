#!/usr/bin/env python3
"""Spawn role-based Claude agents in new terminal windows.

Usage:
    python scripts/spawn_agent.py developer --instance 1 --repo /path/to/repo
"""

import argparse
import subprocess
import sys
from pathlib import Path

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


def get_prompt_path(role: str, repo_path: Path) -> Path:
    """Get the path to the role-specific prompt file."""
    prompt_file = ROLE_TO_PROMPT[role]
    return repo_path / "prompts" / prompt_file


def validate_prompt_exists(prompt_path: Path) -> None:
    """Ensure the prompt file exists."""
    if not prompt_path.exists():
        print(f"Error: Prompt file not found: {prompt_path}", file=sys.stderr)
        sys.exit(1)


def spawn_agent_macos(
    role: str,
    instance: int,
    repo_path: Path,
    prompt_path: Path,
) -> None:
    """Spawn agent in a new macOS Terminal window."""
    log_file = repo_path / "logs" / f"{role}_{instance}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    env_exports = f"""
export AGENT_ROLE="{role}"
export AGENT_INSTANCE="{instance}"
export AGENT_LOG_FILE="{log_file}"
"""

    label = ROLE_TO_LABEL.get(role)
    label_filter = f"-l {label}" if label else ""

    prompt_content = prompt_path.read_text()

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

    escaped_prompt = agent_prompt.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")

    terminal_command = f"""cd "{repo_path}" && {env_exports.strip()} && claude --print-system-prompt "{escaped_prompt}" """

    applescript = f'''
tell application "Terminal"
    activate
    set newWindow to do script "{terminal_command}"
    set custom title of front window to "{role.upper()} Agent #{instance}"
end tell
'''

    try:
        subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True)
        print(f"Spawned {role} agent (instance {instance}) in new Terminal window")
        print(f"  Repo: {repo_path}")
        print(f"  Prompt: {prompt_path}")
        print(f"  Log: {log_file}")
    except subprocess.CalledProcessError as e:
        print(f"Error spawning agent: {e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
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

    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()

    if not repo_path.exists():
        print(f"Error: Repository path not found: {repo_path}", file=sys.stderr)
        sys.exit(1)

    prompt_path = get_prompt_path(args.role, repo_path)
    validate_prompt_exists(prompt_path)

    if sys.platform == "darwin":
        spawn_agent_macos(args.role, args.instance, repo_path, prompt_path)
    else:
        print(f"Error: Platform '{sys.platform}' not supported. Only macOS is implemented.", file=sys.stderr)
        print("For Linux/Windows, please spawn agents manually.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
