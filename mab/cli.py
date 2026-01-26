"""MAB CLI - Multi-Agent Beads command-line interface.

A tool for orchestrating concurrent agent workflows in software development.

The CLI connects to the global daemon at ~/.mab/ regardless of current directory.
Per-project configuration can be stored in <project>/.mab/config.yaml.
"""

from pathlib import Path

import click

from mab.daemon import (
    MAB_HOME,
    Daemon,
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    DaemonState,
    status_to_json,
)
from mab.version import __version__


@click.group()
@click.version_option(version=__version__, prog_name="mab")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Multi-Agent Beads - Orchestrate concurrent agent workflows.

    MAB coordinates Developer, QA, Tech Lead, Manager, and Code Reviewer
    agents working concurrently on shared codebases with proper task handoffs.

    The daemon runs globally at ~/.mab/ and manages workers across all projects.
    Per-project configuration can be stored in <project>/.mab/config.yaml.
    """
    ctx.ensure_object(dict)
    # Always use global daemon at ~/.mab/
    # Optionally detect current project for per-project features
    town_path = Path.cwd()
    ctx.obj["daemon"] = Daemon(mab_dir=MAB_HOME, town_path=town_path)
    ctx.obj["town_path"] = town_path


def _is_git_repo(directory: Path) -> bool:
    """Check if directory is inside a git repository."""
    check_dir = directory.resolve()
    while check_dir != check_dir.parent:
        if (check_dir / ".git").exists():
            return True
        check_dir = check_dir.parent
    return False


def _has_beads_setup(directory: Path) -> bool:
    """Check if directory has an existing beads setup."""
    return (directory / ".beads").exists()


def _get_config_template(template: str, has_beads: bool) -> str:
    """Generate config.yaml content based on template."""
    base_config = """# MAB Configuration File
# Multi-Agent Beads orchestration settings for this project
# See https://github.com/multi_agent_beads for documentation

# Project identification
project:
  name: ""  # Auto-detected from directory name if empty
  description: ""

# Worker settings
workers:
  # Maximum concurrent workers for this project
  max_workers: 3
  # Default roles to spawn on 'mab start'
  default_roles:
    - dev
    - qa
"""

    minimal_config = """# MAB Configuration File (Minimal)
# See 'mab init --template full' for all options

project:
  name: ""

workers:
  max_workers: 2
"""

    full_config = """# MAB Configuration File (Full)
# Multi-Agent Beads orchestration settings for this project
# See https://github.com/multi_agent_beads for documentation

# Project identification
project:
  name: ""  # Auto-detected from directory name if empty
  description: ""
  # Project-specific issue prefix (overrides beads prefix if set)
  issue_prefix: ""

# Worker settings
workers:
  # Maximum concurrent workers for this project
  max_workers: 5
  # Default roles to spawn on 'mab start'
  default_roles:
    - dev
    - qa
    - reviewer
  # Worker restart policy
  restart_policy: always  # always, on-failure, never
  # Heartbeat interval in seconds
  heartbeat_interval: 30
  # Maximum consecutive failures before giving up
  max_failures: 3

# Role-specific configuration
roles:
  dev:
    # Labels this role can work on
    labels:
      - dev
      - feature
      - bug
    # Priority threshold (0=P0 only, 4=all)
    max_priority: 3

  qa:
    labels:
      - qa
      - test
    max_priority: 2

  reviewer:
    labels:
      - review
    max_priority: 2

# Integration with beads
beads:
  # Auto-detected, but can be overridden
  enabled: true
  # Path to beads directory (relative to project root)
  path: ".beads"

# Logging
logging:
  # Log level: debug, info, warning, error
  level: info
  # Keep logs for N days
  retention_days: 7

# Hooks (scripts to run at various points)
hooks:
  # Before claiming a bead
  pre_claim: ""
  # After completing work
  post_complete: ""
  # On worker error
  on_error: ""
"""

    if template == "minimal":
        config = minimal_config
    elif template == "full":
        config = full_config
    else:
        config = base_config

    # Add beads integration note if detected
    if has_beads:
        config += "\n# Note: Existing beads setup detected at .beads/\n"
        config += "# MAB will integrate with beads for issue tracking.\n"

    return config


@cli.command()
@click.option(
    "--template",
    "-t",
    type=click.Choice(["default", "minimal", "full"]),
    default="default",
    help="Project template to use",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Overwrite existing configuration",
)
@click.argument("directory", default=".", required=False)
def init(template: str, force: bool, directory: str) -> None:
    """Initialize a new MAB project.

    Sets up configuration files and directory structure for multi-agent
    orchestration in the specified DIRECTORY (defaults to current directory).

    \b
    Templates:
      default  Standard configuration with common options
      minimal  Bare minimum settings only
      full     All available options with documentation
    """
    target_dir = Path(directory).resolve()

    # Check if directory exists
    if not target_dir.exists():
        click.echo(f"Creating directory: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)

    # Check for git repo
    if not _is_git_repo(target_dir):
        click.secho(
            "Warning: Not a git repository. MAB works best with version control.",
            fg="yellow",
        )

    # Check for existing beads setup
    has_beads = _has_beads_setup(target_dir)
    if has_beads:
        click.echo("Detected existing beads setup at .beads/")

    # Create .mab directory
    mab_dir = target_dir / ".mab"
    config_file = mab_dir / "config.yaml"
    logs_dir = mab_dir / "logs"
    heartbeat_dir = mab_dir / "heartbeat"

    # Check if already initialized
    if mab_dir.exists() and not force:
        if config_file.exists():
            click.secho(
                f"Project already initialized at {mab_dir}",
                fg="yellow",
            )
            click.echo("Use --force to reinitialize and overwrite configuration.")
            raise SystemExit(1)

    # Create directory structure
    mab_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    heartbeat_dir.mkdir(exist_ok=True)

    # Generate and write config
    config_content = _get_config_template(template, has_beads)

    # Set project name from directory if not specified
    project_name = target_dir.name
    config_content = config_content.replace(
        'name: ""  # Auto-detected',
        f'name: "{project_name}"  # Auto-detected',
        1,
    )

    config_file.write_text(config_content)

    # Create .gitignore for .mab directory
    gitignore_file = mab_dir / ".gitignore"
    gitignore_content = """# MAB local files (not tracked in git)
# Config is tracked so team shares settings
!config.yaml

# Logs are local
logs/
*.log

# Runtime files
heartbeat/
*.pid
*.lock
*.sock
"""
    gitignore_file.write_text(gitignore_content)

    # Success message
    click.secho(f"✓ Initialized MAB project in {mab_dir}", fg="green")
    click.echo(f"  Config: {config_file}")
    click.echo(f"  Logs:   {logs_dir}")

    if has_beads:
        click.echo("\nBeads integration enabled. Use 'bd ready' to find work.")

    click.echo("\nNext steps:")
    click.echo("  1. Edit .mab/config.yaml to customize settings")
    click.echo("  2. Run 'mab start' to begin agent orchestration")


@cli.command()
@click.option(
    "--daemon",
    "-d",
    is_flag=True,
    help="Run as background daemon",
)
@click.option(
    "--workers",
    "-w",
    type=int,
    default=1,
    help="Number of worker agents to spawn",
)
@click.option(
    "--role",
    "-r",
    type=click.Choice(["dev", "qa", "tech-lead", "manager", "reviewer", "all"]),
    default="all",
    help="Agent role to start",
)
@click.pass_context
def start(ctx: click.Context, daemon: bool, workers: int, role: str) -> None:
    """Start agent workers.

    Spawns worker agents that process beads from the queue. Can run as a
    foreground process or as a background daemon.
    """
    daemon_instance: Daemon = ctx.obj["daemon"]

    try:
        if daemon:
            click.echo(f"Starting MAB daemon with {workers} {role} worker(s)...")
            daemon_instance.start(foreground=False)
            click.echo("Daemon started successfully.")
        else:
            click.echo(f"Starting {workers} {role} worker(s) in foreground mode...")
            click.echo("Press Ctrl+C to stop.")
            daemon_instance.start(foreground=True)
    except DaemonAlreadyRunningError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.option(
    "--all",
    "-a",
    "stop_all",
    is_flag=True,
    help="Stop all running workers",
)
@click.option(
    "--graceful/--force",
    "-g/-f",
    default=True,
    help="Wait for current work to complete before stopping (default: graceful)",
)
@click.option(
    "--timeout",
    "-t",
    type=float,
    default=60.0,
    help="Timeout in seconds for graceful shutdown",
)
@click.argument("worker_id", required=False)
@click.pass_context
def stop(
    ctx: click.Context,
    stop_all: bool,
    graceful: bool,
    timeout: float,
    worker_id: str | None,
) -> None:
    """Stop agent workers.

    Stops running workers by ID or all workers with --all flag.
    By default, waits for current work to complete (graceful shutdown).
    """
    daemon_instance: Daemon = ctx.obj["daemon"]

    if stop_all:
        # Stop the daemon (which stops all workers)
        mode = "gracefully" if graceful else "forcefully"
        click.echo(f"Stopping daemon {mode}...")

        try:
            daemon_instance.stop(graceful=graceful, timeout=timeout)
            click.echo("Daemon stopped successfully.")
        except DaemonNotRunningError:
            click.echo("Daemon is not running.", err=True)
            raise SystemExit(1)

    elif worker_id:
        # Stop specific worker (future: RPC to daemon)
        click.echo(f"Stopping worker {worker_id}...")
        click.echo("Note: Individual worker stop not yet implemented")

    else:
        click.echo("Error: Specify worker ID or use --all flag", err=True)
        raise SystemExit(1)


@cli.command()
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    help="Continuously update status display",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output status as JSON",
)
@click.pass_context
def status(ctx: click.Context, watch: bool, json_output: bool) -> None:
    """Show status of agent workers.

    Displays current worker status, active beads, and queue information.
    """
    import time

    daemon_instance: Daemon = ctx.obj["daemon"]

    def display_status() -> None:
        daemon_status = daemon_instance.get_status()

        if json_output:
            click.echo(status_to_json(daemon_status))
        else:
            click.echo("MAB Status")
            click.echo("=" * 40)

            # Daemon state with color
            state = daemon_status.state
            if state == DaemonState.RUNNING:
                state_str = click.style("RUNNING", fg="green", bold=True)
            elif state == DaemonState.STOPPED:
                state_str = click.style("STOPPED", fg="red")
            elif state == DaemonState.STARTING:
                state_str = click.style("STARTING", fg="yellow")
            else:
                state_str = click.style("STOPPING", fg="yellow")

            click.echo(f"Daemon: {state_str}")

            if daemon_status.pid:
                click.echo(f"PID: {daemon_status.pid}")

            if daemon_status.uptime_seconds is not None:
                uptime = int(daemon_status.uptime_seconds)
                hours, remainder = divmod(uptime, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    uptime_str = f"{hours}h {minutes}m {seconds}s"
                elif minutes > 0:
                    uptime_str = f"{minutes}m {seconds}s"
                else:
                    uptime_str = f"{seconds}s"
                click.echo(f"Uptime: {uptime_str}")

            if daemon_status.started_at:
                click.echo(f"Started: {daemon_status.started_at}")

            click.echo(f"Workers: {daemon_status.workers_count} running")

    if watch:
        try:
            while True:
                click.clear()
                display_status()
                click.echo("\n(Press Ctrl+C to exit)")
                time.sleep(2)
        except KeyboardInterrupt:
            pass
    else:
        display_status()


@cli.command()
@click.option(
    "--daemon",
    "-d",
    is_flag=True,
    default=True,
    help="Run as background daemon after restart",
)
@click.pass_context
def restart(ctx: click.Context, daemon: bool) -> None:
    """Restart the MAB daemon.

    Stops the daemon if running, then starts it again.
    """
    daemon_instance: Daemon = ctx.obj["daemon"]

    click.echo("Restarting MAB daemon...")

    try:
        # Stop if running
        if daemon_instance.is_running():
            click.echo("Stopping current daemon...")
            daemon_instance.stop(graceful=True)
            click.echo("Daemon stopped.")

        # Start again
        click.echo("Starting daemon...")
        daemon_instance.start(foreground=not daemon)
        if daemon:
            click.echo("Daemon restarted successfully.")
    except DaemonAlreadyRunningError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


def main() -> None:
    """Entry point for the mab CLI."""
    cli()


if __name__ == "__main__":
    main()
