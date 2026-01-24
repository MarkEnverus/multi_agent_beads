#!/usr/bin/env python3
"""Terminal monitor for multi-agent beads system.

Usage:
    python scripts/monitor.py
    python scripts/monitor.py --interval 5
    python scripts/monitor.py --log-lines 20
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def clear_screen() -> None:
    """Clear terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def get_beads_by_status() -> dict[str, list[dict]]:
    """Get beads grouped by status."""
    result: dict[str, list[dict]] = {
        "in_progress": [],
        "open": [],
        "blocked": [],
    }

    try:
        # Get in-progress beads
        output = subprocess.run(
            ["bd", "list", "--status=in_progress"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if output.returncode == 0 and output.stdout.strip():
            for line in output.stdout.strip().split("\n"):
                if line.strip():
                    result["in_progress"].append(parse_bead_line(line))

        # Get open beads (ready)
        output = subprocess.run(
            ["bd", "ready"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if output.returncode == 0 and output.stdout.strip():
            for line in output.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("📋"):
                    result["open"].append(parse_bead_line(line))

        # Get blocked beads
        output = subprocess.run(
            ["bd", "blocked"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if output.returncode == 0 and output.stdout.strip():
            for line in output.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("🚧"):
                    result["blocked"].append(parse_bead_line(line))

    except subprocess.TimeoutExpired:
        pass
    except FileNotFoundError:
        print("Error: 'bd' command not found", file=sys.stderr)
        sys.exit(1)

    return result


def parse_bead_line(line: str) -> dict:
    """Parse a bead line from bd output."""
    # Format: ◐ multi_agent_beads-2gr [● P1] [task] [dev scripts] - Title
    # Or: 1. [● P0] [epic] multi_agent_beads-t35: Title
    bead = {
        "id": "",
        "priority": "",
        "type": "",
        "labels": [],
        "title": "",
        "raw": line,
    }

    # Try to extract bead ID
    import re

    id_match = re.search(r"(multi_agent_beads-\w+)", line)
    if id_match:
        bead["id"] = id_match.group(1)

    # Extract priority
    priority_match = re.search(r"\[● (P\d)\]", line)
    if priority_match:
        bead["priority"] = priority_match.group(1)

    # Extract type
    type_match = re.search(r"\[(task|bug|feature|epic)\]", line)
    if type_match:
        bead["type"] = type_match.group(1)

    # Extract title (after the last ] - or :)
    title_match = re.search(r"(?:\] - |: )(.+)$", line)
    if title_match:
        bead["title"] = title_match.group(1).strip()

    # Extract labels
    label_match = re.search(r"\[([a-z\s,]+)\](?=\s*-)", line)
    if label_match:
        bead["labels"] = [label.strip() for label in label_match.group(1).split()]

    return bead


def get_recent_logs(log_path: Path, num_lines: int = 10) -> list[str]:
    """Get recent log entries."""
    if not log_path.exists():
        return []

    try:
        output = subprocess.run(
            ["tail", f"-{num_lines}", str(log_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if output.returncode == 0:
            return output.stdout.strip().split("\n")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return []


def format_bead_display(bead: dict, show_time: bool = True) -> str:
    """Format a bead for display."""
    parts = []

    if bead["id"]:
        # Shorten ID for display
        short_id = bead["id"].replace("multi_agent_beads-", "")
        parts.append(f"[{short_id}]")

    if bead["priority"]:
        parts.append(f"[{bead['priority']}]")

    if bead["title"]:
        parts.append(bead["title"])
    elif bead["raw"]:
        parts.append(bead["raw"][:60])

    return " ".join(parts)


def get_agent_assignments() -> dict[str, list[str]]:
    """Group in-progress work by agent role based on labels."""
    beads = get_beads_by_status()
    assignments: dict[str, list[str]] = {
        "DEVELOPER": [],
        "QA": [],
        "TECH_LEAD": [],
        "MANAGER": [],
        "REVIEWER": [],
        "UNASSIGNED": [],
    }

    label_to_role = {
        "dev": "DEVELOPER",
        "qa": "QA",
        "architecture": "TECH_LEAD",
        "review": "REVIEWER",
    }

    for bead in beads["in_progress"]:
        assigned = False
        for label in bead.get("labels", []):
            if label in label_to_role:
                role = label_to_role[label]
                assignments[role].append(format_bead_display(bead))
                assigned = True
                break
        if not assigned:
            assignments["UNASSIGNED"].append(format_bead_display(bead))

    return assignments


def render_display(
    beads: dict[str, list[dict]],
    logs: list[str],
    log_lines: int,
) -> None:
    """Render the monitor display."""
    clear_screen()

    now = datetime.now().strftime("%H:%M:%S")
    width = 70

    print("=" * width)
    print(f"MULTI-AGENT STATUS - {now}".center(width))
    print("=" * width)
    print()

    # Get agent assignments
    assignments = get_agent_assignments()

    # Show active work by role
    has_active = False
    for role in ["DEVELOPER", "QA", "TECH_LEAD", "MANAGER", "REVIEWER"]:
        if assignments[role]:
            has_active = True
            print(f"[{role}]")
            for item in assignments[role]:
                print(f"  {item}")
            print()

    if assignments["UNASSIGNED"]:
        has_active = True
        print("[UNASSIGNED]")
        for item in assignments["UNASSIGNED"]:
            print(f"  {item}")
        print()

    if not has_active:
        print("  No active work in progress")
        print()

    # Show queue summary
    print("-" * width)
    print("QUEUE SUMMARY")
    print("-" * width)
    ready_count = len(beads["open"])
    blocked_count = len(beads["blocked"])
    in_progress_count = len(beads["in_progress"])

    print(f"  In Progress: {in_progress_count}")
    print(f"  Ready:       {ready_count}")
    print(f"  Blocked:     {blocked_count}")
    print()

    # Show recent activity
    print("-" * width)
    print("RECENT ACTIVITY")
    print("-" * width)

    if logs:
        for log_line in logs[-log_lines:]:
            # Parse log line: [timestamp] [pid] MESSAGE
            if log_line.strip():
                # Extract just the time and message
                parts = log_line.split("] ", 2)
                if len(parts) >= 3:
                    timestamp = parts[0].replace("[", "").split(" ")[-1]
                    message = parts[2]
                    print(f"  {timestamp} {message[:55]}")
                else:
                    print(f"  {log_line[:60]}")
    else:
        print("  No recent activity")

    print()
    print("-" * width)
    print("Press Ctrl+C to exit")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Terminal monitor for multi-agent beads system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Refresh interval in seconds (default: 10)",
    )
    parser.add_argument(
        "--log-lines",
        type=int,
        default=10,
        help="Number of log lines to show (default: 10)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="claude.log",
        help="Path to log file (default: claude.log)",
    )

    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path

    print(f"Starting monitor (refresh every {args.interval}s)...")
    print("Press Ctrl+C to exit")
    time.sleep(1)

    try:
        while True:
            beads = get_beads_by_status()
            logs = get_recent_logs(log_path, args.log_lines * 2)
            render_display(beads, logs, args.log_lines)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
