"""bd-close CLI - Wrapper for bd close with PR validation.

This module enforces that PRs must be merged before beads can be closed.
It wraps the bd close command to add hard validation.

Usage:
    bd-close <bead-id> [--pr <number>] [--reason <text>] [--no-pr] [--force]

Examples:
    # Close bead with automatic PR detection
    bd-close multi_agent_beads-xyz

    # Close bead with specific PR number
    bd-close multi_agent_beads-xyz --pr 123

    # Close non-code bead (docs/config)
    bd-close multi_agent_beads-xyz --no-pr --reason "Documentation update"

    # Force close (bypass validation)
    bd-close multi_agent_beads-xyz --force
"""

import subprocess
import sys

import click

from mab.pr_validation import validate_close


@click.command()
@click.argument("bead_ids", nargs=-1, required=True)
@click.option(
    "--pr",
    "-p",
    "pr_number",
    type=int,
    default=None,
    help="PR number to verify (auto-detected if not specified)",
)
@click.option(
    "--reason",
    "-r",
    default=None,
    help="Reason for closing (passed to bd close)",
)
@click.option(
    "--no-pr",
    is_flag=True,
    help="Mark as non-code bead (no PR required)",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force close, bypass PR validation",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Check validation without actually closing",
)
def main(
    bead_ids: tuple[str, ...],
    pr_number: int | None,
    reason: str | None,
    no_pr: bool,
    force: bool,
    dry_run: bool,
) -> None:
    """Close beads with PR merge validation.

    Ensures that code beads have merged PRs before allowing close.
    Non-code beads (docs, config) can close without PRs using --no-pr.

    \b
    Exit codes:
        0 - Success (bead(s) closed)
        1 - Validation failed (PR not merged)
        2 - bd close command failed
    """
    failed_beads: list[tuple[str, str]] = []
    validated_beads: list[str] = []

    # Validate all beads first
    for bead_id in bead_ids:
        click.echo(f"Validating {bead_id}...")

        result = validate_close(
            bead_id=bead_id,
            pr_number=pr_number,
            force=force,
            no_pr=no_pr,
        )

        if result.allowed:
            click.secho(f"  ✓ {result.reason}", fg="green")
            if result.pr_info:
                click.echo(f"    PR: #{result.pr_info.number} - {result.pr_info.title}")
            validated_beads.append(bead_id)
        else:
            click.secho(f"  ✗ {result.reason}", fg="red")
            if result.suggestions:
                click.echo("  Suggestions:")
                for suggestion in result.suggestions:
                    click.echo(f"    - {suggestion}")
            failed_beads.append((bead_id, result.reason))

    # Report validation summary
    if failed_beads:
        click.echo()
        click.secho(
            f"Validation failed for {len(failed_beads)} bead(s):",
            fg="red",
            bold=True,
        )
        for bead_id, _ in failed_beads:
            click.echo(f"  - {bead_id}")
        click.echo()
        click.echo("Fix the issues above or use --force to bypass validation.")
        sys.exit(1)

    if dry_run:
        click.echo()
        click.secho("Dry run: validation passed, no beads were closed.", fg="yellow")
        sys.exit(0)

    # All validated, proceed with closing
    click.echo()
    click.echo(f"Closing {len(validated_beads)} bead(s)...")

    # Build bd close command
    cmd = ["bd", "close"] + list(validated_beads)
    if reason:
        cmd.extend(["--reason", reason])

    # Execute bd close
    try:
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            click.secho("bd close command failed", fg="red")
            sys.exit(2)
    except FileNotFoundError:
        click.secho("Error: bd command not found", fg="red")
        sys.exit(2)

    click.secho("✓ Bead(s) closed successfully", fg="green")


if __name__ == "__main__":
    main()
