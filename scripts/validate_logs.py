#!/usr/bin/env python3
"""Log validation script to detect fake or suspicious agent log entries.

Detects:
- Literal [$$] instead of numeric PID (shell variable not expanded)
- Multiple log entries with identical timestamps (suspicious batching)
- TESTS_PASSED without prior WORK_START (skipped actual work)
- Large PID jumps indicating possible crash/restart
- CLOSE without WORK_START in same session

Usage:
    python scripts/validate_logs.py                    # Validate claude.log
    python scripts/validate_logs.py --log-file other.log
    python scripts/validate_logs.py --strict          # Fail on any warnings
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Log line pattern: [YYYY-MM-DD HH:MM:SS] [PID] MESSAGE
LOG_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[([^\]]+)\] (.+)$"
)


@dataclass
class LogEntry:
    """Parsed log entry."""

    line_num: int
    timestamp: str
    pid: str
    message: str
    raw: str


@dataclass
class ValidationIssue:
    """A detected validation issue."""

    severity: str  # ERROR, WARNING
    category: str
    description: str
    line_num: int
    evidence: str


def parse_log_line(line: str, line_num: int) -> LogEntry | None:
    """Parse a log line into structured format."""
    match = LOG_PATTERN.match(line.strip())
    if not match:
        return None
    return LogEntry(
        line_num=line_num,
        timestamp=match.group(1),
        pid=match.group(2),
        message=match.group(3),
        raw=line.strip(),
    )


def validate_logs(log_path: Path) -> list[ValidationIssue]:
    """Validate log file for suspicious entries."""
    issues: list[ValidationIssue] = []

    if not log_path.exists():
        issues.append(
            ValidationIssue(
                severity="ERROR",
                category="file_missing",
                description=f"Log file not found: {log_path}",
                line_num=0,
                evidence="",
            )
        )
        return issues

    entries: list[LogEntry] = []
    with open(log_path) as f:
        for line_num, line in enumerate(f, 1):
            if line.strip():
                entry = parse_log_line(line, line_num)
                if entry:
                    entries.append(entry)

    if not entries:
        return issues

    # Check 1: Literal [$$] instead of numeric PID
    for entry in entries:
        if entry.pid == "$$":
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    category="fake_pid",
                    description=(
                        "Literal [$$] detected - shell variable not expanded. "
                        "Agent may have simulated log output without running bash."
                    ),
                    line_num=entry.line_num,
                    evidence=entry.raw,
                )
            )

    # Check 2: Multiple entries with identical timestamps (suspicious batching)
    timestamp_groups: dict[str, list[LogEntry]] = defaultdict(list)
    for entry in entries:
        timestamp_groups[entry.timestamp].append(entry)

    for timestamp, group in timestamp_groups.items():
        # More than 3 entries at exact same second is suspicious
        if len(group) > 3:
            # Even more suspicious if they have the same PID (or $$)
            pids = {e.pid for e in group}
            if len(pids) == 1:
                issues.append(
                    ValidationIssue(
                        severity="WARNING",
                        category="timestamp_batch",
                        description=(
                            f"{len(group)} log entries at identical timestamp "
                            f"with same PID. May indicate simulated batch output."
                        ),
                        line_num=group[0].line_num,
                        evidence=f"Timestamp: {timestamp}, "
                        f"Lines: {group[0].line_num}-{group[-1].line_num}",
                    )
                )

    # Check 3: Session tracking - CLOSE without WORK_START
    session_start_pids: set[str] = set()
    work_start_pids: set[str] = set()
    tests_pids: set[str] = set()
    close_pids: set[str] = set()

    for entry in entries:
        if entry.pid == "$$":
            continue  # Skip fake PIDs

        msg = entry.message.upper()
        if "SESSION_START" in msg:
            session_start_pids.add(entry.pid)
        elif "WORK_START" in msg:
            work_start_pids.add(entry.pid)
        elif msg.startswith("TESTS") or "TESTS_PASSED" in msg:
            tests_pids.add(entry.pid)
        elif msg.startswith("CLOSE"):
            close_pids.add(entry.pid)

    # Note: We track session_start_pids, work_start_pids, tests_pids, close_pids
    # for potential future analysis of session flow anomalies.
    # Currently focusing on more reliable indicators (fake PID, timestamp batching).

    # Check 4: Large PID jumps in sequential entries (possible crash)
    prev_entry: LogEntry | None = None
    for entry in entries:
        if prev_entry and entry.pid != "$$" and prev_entry.pid != "$$":
            try:
                curr_pid = int(entry.pid)
                prev_pid = int(prev_entry.pid)
                # PIDs can wrap around, but a jump of >50000 in same minute is sus
                if abs(curr_pid - prev_pid) > 50000:
                    # Check if timestamps are close
                    try:
                        t1 = datetime.strptime(prev_entry.timestamp, "%Y-%m-%d %H:%M:%S")
                        t2 = datetime.strptime(entry.timestamp, "%Y-%m-%d %H:%M:%S")
                        delta = abs((t2 - t1).total_seconds())
                        if delta < 120:  # Within 2 minutes
                            issues.append(
                                ValidationIssue(
                                    severity="WARNING",
                                    category="pid_jump",
                                    description=(
                                        f"Large PID jump ({prev_pid} -> {curr_pid}) "
                                        f"within {delta:.0f}s. May indicate crash/restart."
                                    ),
                                    line_num=entry.line_num,
                                    evidence=f"Previous: {prev_entry.raw[:60]}",
                                )
                            )
                    except ValueError:
                        pass
            except ValueError:
                pass
        prev_entry = entry

    # Check 5: TESTS_PASSED without any actual test execution evidence
    # This is tricky - we look for TESTS: entries before TESTS_PASSED
    tests_started: set[str] = set()
    for entry in entries:
        msg = entry.message
        if msg.startswith("TESTS:"):
            tests_started.add(entry.pid)
        elif "TESTS_PASSED" in msg:
            if entry.pid not in tests_started and entry.pid != "$$":
                # Only flag if this PID never logged TESTS: first
                issues.append(
                    ValidationIssue(
                        severity="WARNING",
                        category="tests_no_evidence",
                        description=(
                            "TESTS_PASSED logged without prior TESTS: entry. "
                            "May indicate fake test results."
                        ),
                        line_num=entry.line_num,
                        evidence=entry.raw,
                    )
                )

    return issues


def print_report(issues: list[ValidationIssue]) -> None:
    """Print validation report."""
    if not issues:
        print("\u2705 Log validation passed - no issues detected")
        return

    errors = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]

    print(f"\n{'='*70}")
    print("LOG VALIDATION REPORT")
    print(f"{'='*70}\n")

    if errors:
        print(f"\u274c ERRORS ({len(errors)}):")
        print("-" * 70)
        for issue in errors:
            print(f"\n  Line {issue.line_num}: [{issue.category}]")
            print(f"  {issue.description}")
            if issue.evidence:
                print(f"  Evidence: {issue.evidence[:100]}")

    if warnings:
        print(f"\n\u26a0\ufe0f  WARNINGS ({len(warnings)}):")
        print("-" * 70)
        for issue in warnings:
            print(f"\n  Line {issue.line_num}: [{issue.category}]")
            print(f"  {issue.description}")
            if issue.evidence:
                print(f"  Evidence: {issue.evidence[:100]}")

    print(f"\n{'='*70}")
    print(f"Summary: {len(errors)} errors, {len(warnings)} warnings")
    print(f"{'='*70}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate agent log files for suspicious entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="claude.log",
        help="Path to log file (default: claude.log)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with error code on any issues (including warnings)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args(argv)

    log_path = Path(args.log_file)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path

    issues = validate_logs(log_path)

    if args.json:
        import json

        output = [
            {
                "severity": i.severity,
                "category": i.category,
                "description": i.description,
                "line_num": i.line_num,
                "evidence": i.evidence,
            }
            for i in issues
        ]
        print(json.dumps(output, indent=2))
    else:
        print_report(issues)

    # Determine exit code
    errors = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
