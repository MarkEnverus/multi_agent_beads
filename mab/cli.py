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
from mab.dashboard_manager import (
    DashboardAlreadyRunningError,
    DashboardManager,
    DashboardStartError,
)
from mab.rpc import DaemonNotRunningError as RPCDaemonNotRunningError
from mab.rpc import get_default_client
from mab.templates import get_template
from mab.towns import (
    PortConflictError,
    Town,
    TownError,
    TownExistsError,
    TownManager,
    TownNotFoundError,
    TownStatus,
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
@click.option(
    "--template",
    "-t",
    type=click.Choice(["solo", "pair", "full"]),
    default=None,
    help="Create town with template and start (quick start)",
)
@click.pass_context
def start(
    ctx: click.Context,
    daemon: bool,
    workers: int,
    role: str,
    template: str | None,
) -> None:
    """Start agent workers.

    Spawns worker agents that process beads from the queue. Can run as a
    foreground process or as a background daemon.

    \b
    Quick Start with Templates:
      mab start --template=solo   # Single dev
      mab start --template=pair   # Dev + QA (default)
      mab start --template=full   # All roles

    Using --template auto-creates a town for the current project with the
    specified template configuration.
    """
    daemon_instance: Daemon = ctx.obj["daemon"]

    # Quick start with template: create town and start
    if template:
        town_path: Path = ctx.obj["town_path"]
        town_name = town_path.name

        # Create town manager
        manager = TownManager(MAB_HOME)

        # Check if town already exists
        try:
            existing = manager.get(town_name)
            click.echo(f"Town '{town_name}' already exists (template: {existing.template})")
        except TownNotFoundError:
            # Create new town with template
            template_config = get_template(template)
            if template_config is None:
                click.secho(f"Error: Invalid template '{template}'", fg="red", err=True)
                raise SystemExit(1)

            try:
                new_town = manager.create(
                    name=town_name,
                    project_path=str(town_path),
                    template=template,
                    description=f"Auto-created with template '{template}'",
                )
                click.secho(f"Created town '{town_name}' with template '{template}'", fg="green")
                click.echo(f"  Workers: {_format_worker_counts(new_town.worker_counts)}")
                click.echo(f"  Port: {new_town.port}")
            except TownError as e:
                click.secho(f"Error creating town: {e}", fg="red", err=True)
                raise SystemExit(1)

        click.echo("")

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
    Fails if daemon is not currently running.
    """
    daemon_instance: Daemon = ctx.obj["daemon"]

    # Check if daemon is running first
    if not daemon_instance.is_running():
        click.echo("Error: Daemon is not running. Use 'mab start' to start it.", err=True)
        raise SystemExit(1)

    click.echo("Restarting MAB daemon...")

    try:
        # Stop the running daemon
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
    except DaemonNotRunningError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


# Valid worker roles
VALID_ROLES = ("dev", "qa", "tech-lead", "manager", "reviewer")


def _normalize_role_name(role: str) -> str:
    """Normalize role name for comparison with templates.

    CLI uses hyphen (tech-lead) but templates use underscore (tech_lead).
    """
    return role.replace("-", "_")


def _get_town_for_project(project_path: str) -> Town | None:
    """Find the town associated with a project path.

    Args:
        project_path: Path to the project directory.

    Returns:
        Town if found, None otherwise.
    """
    manager = TownManager(MAB_HOME)

    # First try to find by exact project path
    towns = manager.list_towns(project_path=project_path)
    if towns:
        return towns[0]

    # Fallback: try to find by directory name
    project_name = Path(project_path).name
    try:
        return manager.get(project_name)
    except TownNotFoundError:
        return None


def _validate_role_for_town(role: str, town: Town) -> tuple[bool, str]:
    """Validate that a role is allowed by the town's template.

    Args:
        role: The role to validate (e.g., "dev", "tech-lead").
        town: The town to validate against.

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    normalized_role = _normalize_role_name(role)
    effective_roles = town.get_effective_roles()

    if normalized_role in effective_roles:
        return True, ""

    # Build helpful error message
    allowed_roles = list(effective_roles.keys())
    # Convert back to CLI format for display (underscore to hyphen)
    allowed_roles_display = [r.replace("_", "-") for r in allowed_roles]

    return False, (
        f"Role '{role}' is not allowed for town '{town.name}' "
        f"(template: {town.template}). "
        f"Allowed roles: {', '.join(allowed_roles_display)}"
    )


@cli.command()
@click.option(
    "--role",
    "-r",
    type=click.Choice(VALID_ROLES),
    required=True,
    help="Agent role to spawn",
)
@click.option(
    "--count",
    "-c",
    type=int,
    default=1,
    help="Number of workers to spawn",
)
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default=None,
    help="Project directory (defaults to current directory)",
)
@click.pass_context
def spawn(ctx: click.Context, role: str, count: int, project: str | None) -> None:
    """Spawn worker agents.

    Creates new worker agents of the specified role. Workers run in the
    background managed by the daemon.

    \b
    Examples:
      mab spawn --role dev          # Spawn a dev worker
      mab spawn --role qa -c 2      # Spawn 2 QA workers
    """
    project_path = project or str(ctx.obj["town_path"])

    # Validate role against town template
    town = _get_town_for_project(project_path)
    if town is not None:
        is_valid, error_msg = _validate_role_for_town(role, town)
        if not is_valid:
            click.secho(f"Error: {error_msg}", fg="red", err=True)
            raise SystemExit(1)

    try:
        client = get_default_client()

        for i in range(count):
            result = client.call(
                "worker.spawn",
                {"role": role, "project_path": project_path},
            )
            click.echo(f"Spawned {role} worker: {result['worker_id']} (PID {result['pid']})")

    except RPCDaemonNotRunningError:
        click.echo("Error: Daemon is not running. Start it with 'mab start -d'", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


# Worker command group for horizontal scaling
@cli.group()
@click.pass_context
def worker(ctx: click.Context) -> None:
    """Manage worker agents.

    Commands for scaling workers horizontally within a town.
    """
    pass


@worker.command("add")
@click.argument("role", type=click.Choice(VALID_ROLES))
@click.option(
    "--count",
    "-c",
    type=int,
    default=1,
    help="Number of workers to add",
)
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default=None,
    help="Project directory (defaults to current directory)",
)
@click.pass_context
def worker_add(ctx: click.Context, role: str, count: int, project: str | None) -> None:
    """Add worker(s) of a specific role.

    ROLE is the agent role to add (dev, qa, tech-lead, manager, reviewer).

    This command scales workers horizontally by adding more agents of the
    specified role to process work in parallel.

    \b
    Examples:
      mab worker add dev          # Add 1 dev worker
      mab worker add qa -c 2      # Add 2 QA workers
      mab worker add dev -c 3     # Add 3 dev workers for faster processing
    """
    project_path = project or str(ctx.obj["town_path"])

    # Validate role against town template
    town = _get_town_for_project(project_path)
    if town is not None:
        is_valid, error_msg = _validate_role_for_town(role, town)
        if not is_valid:
            click.secho(f"Error: {error_msg}", fg="red", err=True)
            raise SystemExit(1)

    try:
        client = get_default_client()

        spawned = []
        for i in range(count):
            result = client.call(
                "worker.spawn",
                {"role": role, "project_path": project_path},
            )
            spawned.append(result)
            click.echo(f"Added {role} worker: {result['worker_id']} (PID {result['pid']})")

        if count > 1:
            click.secho(f"\nAdded {count} {role} worker(s)", fg="green")

    except RPCDaemonNotRunningError:
        click.echo("Error: Daemon is not running. Start it with 'mab start -d'", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@cli.command("list")
@click.option(
    "--role",
    "-r",
    type=click.Choice(VALID_ROLES),
    default=None,
    help="Filter by role",
)
@click.option(
    "--status",
    "-s",
    type=click.Choice(["running", "stopped", "crashed", "all"]),
    default="all",
    help="Filter by status",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output as JSON",
)
@click.pass_context
def list_workers(
    ctx: click.Context,
    role: str | None,
    status: str,
    json_output: bool,
) -> None:
    """List active workers.

    Shows running workers with their status, role, and current task.
    """
    import json

    try:
        client = get_default_client()

        params: dict[str, str] = {}
        if role:
            params["role"] = role
        if status != "all":
            params["status"] = status

        result = client.call("worker.list", params)
        workers = result.get("workers", [])

        if json_output:
            click.echo(json.dumps(workers, indent=2))
            return

        if not workers:
            click.echo("No workers running.")
            return

        click.echo(f"{'ID':<20} {'ROLE':<12} {'STATUS':<10} {'PID':<8} {'PROJECT'}")
        click.echo("-" * 70)

        for w in workers:
            worker_id = w.get("id", "")[:18]
            worker_role = w.get("role", "")
            worker_status = w.get("status", "")
            worker_pid = str(w.get("pid", ""))
            worker_project = w.get("project_path", "-")
            if len(worker_project) > 20:
                worker_project = "..." + worker_project[-17:]

            status_color = "green" if worker_status == "running" else "red"
            status_str = click.style(worker_status, fg=status_color)

            click.echo(
                f"{worker_id:<20} {worker_role:<12} {status_str:<20} {worker_pid:<8} {worker_project}"
            )

    except RPCDaemonNotRunningError:
        click.echo("No workers running (daemon not running).")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@cli.group()
@click.pass_context
def config(ctx: click.Context) -> None:
    """Manage MAB configuration.

    View and modify MAB configuration for the current project or globally.
    """
    pass


@config.command("show")
@click.option(
    "--global",
    "show_global",
    is_flag=True,
    help="Show global configuration",
)
@click.pass_context
def config_show(ctx: click.Context, show_global: bool) -> None:
    """Show current configuration.

    Displays the configuration file contents for the current project
    or global settings.
    """
    if show_global:
        config_path = MAB_HOME / "config.yaml"
    else:
        town_path: Path = ctx.obj["town_path"]
        config_path = town_path / ".mab" / "config.yaml"

    if not config_path.exists():
        location = "globally" if show_global else f"in {ctx.obj['town_path']}"
        click.echo(f"Error: MAB not initialized {location}", err=True)
        click.echo("Run 'mab init' to create configuration.")
        raise SystemExit(1)

    click.echo(f"# Config: {config_path}")
    click.echo(config_path.read_text())


@config.command("get")
@click.argument("key")
@click.pass_context
def config_get(ctx: click.Context, key: str) -> None:
    """Get a configuration value.

    KEY is the configuration key in dot notation (e.g., workers.max_workers).
    """
    import yaml

    town_path: Path = ctx.obj["town_path"]
    config_path = town_path / ".mab" / "config.yaml"

    if not config_path.exists():
        click.echo("Error: MAB not initialized in this directory", err=True)
        raise SystemExit(1)

    try:
        config_data = yaml.safe_load(config_path.read_text())

        # Navigate dot notation
        value = config_data
        for part in key.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break

        if value is None:
            click.echo(f"Key '{key}' not found")
            raise SystemExit(1)

        click.echo(value)

    except yaml.YAMLError as e:
        click.echo(f"Error parsing config: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.option(
    "--follow",
    "-f",
    is_flag=True,
    help="Follow log output",
)
@click.option(
    "--lines",
    "-n",
    type=int,
    default=50,
    help="Number of lines to show",
)
@click.option(
    "--worker",
    "-w",
    default=None,
    help="Filter by worker ID",
)
@click.pass_context
def logs(ctx: click.Context, follow: bool, lines: int, worker: str | None) -> None:
    """View worker logs.

    Shows logs from running workers. Use --follow to stream logs in real-time.

    \b
    Examples:
      mab logs              # Show recent logs
      mab logs -f           # Follow log output
      mab logs -n 100       # Show last 100 lines
    """
    import subprocess

    # Determine log file location
    town_path: Path = ctx.obj["town_path"]
    town_logs_dir = town_path / ".mab" / "logs"
    daemon_log = MAB_HOME / "daemon.log"

    # Check for logs
    log_files = []

    if daemon_log.exists():
        log_files.append(daemon_log)

    if town_logs_dir.exists():
        log_files.extend(town_logs_dir.glob("*.log"))

    if not log_files:
        click.echo("No logs found.")
        click.echo("Start workers with 'mab start' to generate logs.")
        return

    if follow:
        # Use tail -f for following logs
        try:
            files_to_tail = [str(daemon_log)] if daemon_log.exists() else []
            if town_logs_dir.exists():
                worker_logs = list(town_logs_dir.glob("*.log"))
                files_to_tail.extend(str(f) for f in worker_logs)

            if not files_to_tail:
                click.echo("No log files to follow.")
                return

            click.echo("Following logs (Ctrl+C to stop)...")
            subprocess.run(["tail", "-f"] + files_to_tail)

        except KeyboardInterrupt:
            pass
    else:
        # Show recent logs from daemon log
        if daemon_log.exists():
            log_lines = daemon_log.read_text().splitlines()

            # Filter by worker if specified
            if worker:
                log_lines = [line for line in log_lines if worker in line]

            # Show last N lines
            for line in log_lines[-lines:]:
                click.echo(line)
        else:
            click.echo("No daemon logs found.")


@cli.command()
@click.option(
    "--port",
    "-p",
    type=int,
    default=None,
    help="Dashboard port (auto-assigned if not specified)",
)
@click.option(
    "--stop",
    is_flag=True,
    help="Stop the dashboard for current project",
)
@click.option(
    "--status",
    "show_status",
    is_flag=True,
    help="Show status of running dashboards",
)
@click.pass_context
def dashboard(
    ctx: click.Context,
    port: int | None,
    stop: bool,
    show_status: bool,
) -> None:
    """Start or manage the dashboard for the current project.

    Starts a web dashboard for monitoring beads, workers, and agents.
    Each project gets its own dashboard instance on a unique port.

    \b
    Examples:
      mab dashboard              # Start dashboard for current project
      mab dashboard --port 8001  # Start on specific port
      mab dashboard --stop       # Stop dashboard for current project
      mab dashboard --status     # Show all running dashboards
    """
    manager = DashboardManager()
    project_path = ctx.obj["town_path"]

    if show_status:
        # Show status of all running dashboards
        dashboards = manager.list_dashboards()
        if not dashboards:
            click.echo("No dashboards running.")
            return

        click.echo(f"{'PROJECT':<30} {'PORT':<8} {'PID':<10} {'URL'}")
        click.echo("-" * 70)
        for db in dashboards:
            project_name = Path(db.project_path).name
            if len(project_name) > 28:
                project_name = project_name[:25] + "..."
            url = f"http://127.0.0.1:{db.port}"
            click.echo(f"{project_name:<30} {db.port:<8} {db.pid or '-':<10} {url}")
        return

    if stop:
        # Stop dashboard for current project
        if manager.stop(project_path):
            click.secho(f"✓ Stopped dashboard for {project_path.name}", fg="green")
        else:
            click.echo("Dashboard is not running for this project.")
        return

    # Start dashboard for current project
    try:
        info = manager.start(project_path, port=port)
        click.secho(f"✓ Dashboard started on port {info.port}", fg="green")
        click.echo(f"  URL: http://127.0.0.1:{info.port}")
        click.echo(f"  PID: {info.pid}")
        click.echo(f"  Log: {info.log_file}")
        click.echo("\nStop with: mab dashboard --stop")

    except DashboardAlreadyRunningError as e:
        # Get existing dashboard info
        existing = manager.get_dashboard(project_path)
        if existing:
            click.secho(f"Dashboard already running on port {existing.port}", fg="yellow")
            click.echo(f"  URL: http://127.0.0.1:{existing.port}")
            click.echo("\nTo restart, stop first: mab dashboard --stop")
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    except DashboardStartError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        raise SystemExit(1)


@cli.group()
@click.pass_context
def town(ctx: click.Context) -> None:
    """Manage orchestration towns.

    Towns are isolated orchestration contexts, each with its own:
    - Dashboard on a unique port
    - Worker pool
    - Configuration

    Multiple towns can run simultaneously for different projects or environments.
    """
    ctx.ensure_object(dict)
    ctx.obj["town_manager"] = TownManager(MAB_HOME)


@town.command("create")
@click.argument("name")
@click.option(
    "--port",
    "-p",
    type=int,
    default=None,
    help="Dashboard port (auto-allocated if not specified)",
)
@click.option(
    "--project",
    "-P",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default=None,
    help="Project directory path",
)
@click.option(
    "--template",
    "-t",
    type=click.Choice(["solo", "pair", "full"]),
    default="pair",
    help="Team template (solo: 1 dev, pair: dev+qa, full: all roles)",
)
@click.option(
    "--max-workers",
    "-w",
    type=int,
    default=None,
    help="Maximum concurrent workers (defaults to template total)",
)
@click.option(
    "--roles",
    "-r",
    multiple=True,
    default=None,
    help="Override default roles (can be specified multiple times)",
)
@click.option(
    "--description",
    "-d",
    default="",
    help="Human-readable description",
)
@click.pass_context
def town_create(
    ctx: click.Context,
    name: str,
    port: int | None,
    project: str | None,
    template: str,
    max_workers: int | None,
    roles: tuple[str, ...] | None,
    description: str,
) -> None:
    """Create a new orchestration town.

    NAME is the unique identifier for the town (alphanumeric with underscores).

    \b
    Templates:
      solo   Single developer, human merges PRs (1 worker)
      pair   Developer + QA, human merges PRs (2 workers)
      full   Complete team with all roles (5 workers)

    Examples:

        mab town create staging --template=pair

        mab town create myproject --template=solo --project /path/to/project
    """
    manager: TownManager = ctx.obj["town_manager"]

    # Get template config for defaults
    template_config = get_template(template)
    if template_config is None:
        click.secho(f"Error: Invalid template '{template}'", fg="red", err=True)
        raise SystemExit(1)

    # Use template defaults if not overridden
    effective_max_workers = max_workers or template_config.get_total_workers()
    effective_roles = list(roles) if roles else None

    try:
        new_town = manager.create(
            name=name,
            port=port,
            project_path=project,
            max_workers=effective_max_workers,
            default_roles=effective_roles,
            description=description,
            template=template,
        )

        click.secho(f"Created town '{name}' on port {new_town.port}", fg="green")
        click.echo(f"  Template: {new_town.template}")
        click.echo(f"  Workers: {_format_worker_counts(new_town.worker_counts)}")
        if new_town.project_path:
            click.echo(f"  Project: {new_town.project_path}")

        click.echo("\nNext steps:")
        click.echo(f"  1. Start town dashboard: mab town start {name}")
        click.echo(f"  2. Open dashboard: http://127.0.0.1:{new_town.port}")

    except TownExistsError:
        click.secho(f"Error: Town '{name}' already exists", fg="red", err=True)
        raise SystemExit(1)
    except PortConflictError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        raise SystemExit(1)
    except TownError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        raise SystemExit(1)


def _format_worker_counts(counts: dict[str, int]) -> str:
    """Format worker counts as a readable string."""
    if not counts:
        return "-"
    return ", ".join(f"{role}: {count}" for role, count in counts.items())


@town.command("list")
@click.option(
    "--status",
    "-s",
    type=click.Choice(["running", "stopped", "all"]),
    default="all",
    help="Filter by status",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output as JSON",
)
@click.pass_context
def town_list(
    ctx: click.Context,
    status: str,
    json_output: bool,
) -> None:
    """List all towns.

    Shows town name, port, status, and worker count.
    """
    import json

    manager: TownManager = ctx.obj["town_manager"]

    status_filter = None
    if status == "running":
        status_filter = TownStatus.RUNNING
    elif status == "stopped":
        status_filter = TownStatus.STOPPED

    towns = manager.list_towns(status=status_filter)

    if json_output:
        output = [t.to_dict() for t in towns]
        click.echo(json.dumps(output, indent=2))
        return

    if not towns:
        click.echo("No towns found.")
        click.echo("\nCreate one with: mab town create <name>")
        return

    click.echo(f"{'NAME':<15} {'PORT':<8} {'STATUS':<10} {'WORKERS':<10} {'PROJECT'}")
    click.echo("-" * 70)

    for t in towns:
        status_color = "green" if t.status == TownStatus.RUNNING else "red"
        status_str = click.style(t.status.value, fg=status_color)
        project = t.project_path or "-"
        if len(project) > 25:
            project = "..." + project[-22:]

        # Get worker count for this town (would need RPC call in real impl)
        workers = "-"

        click.echo(f"{t.name:<15} {t.port:<8} {status_str:<20} {workers:<10} {project}")


@town.command("delete")
@click.argument("name")
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force delete even if running",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def town_delete(
    ctx: click.Context,
    name: str,
    force: bool,
    yes: bool,
) -> None:
    """Delete a town.

    NAME is the town to delete. Running towns must be stopped first
    unless --force is used.
    """
    manager: TownManager = ctx.obj["town_manager"]

    try:
        existing_town = manager.get(name)
    except TownNotFoundError:
        click.secho(f"Error: Town '{name}' not found", fg="red", err=True)
        raise SystemExit(1)

    if not yes:
        msg = f"Delete town '{name}'"
        if existing_town.status == TownStatus.RUNNING:
            msg += " (RUNNING)"
        msg += "?"
        if not click.confirm(msg):
            click.echo("Aborted.")
            return

    try:
        manager.delete(name, force=force)
        click.secho(f"Deleted town '{name}'", fg="green")
    except TownError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        raise SystemExit(1)


@town.command("show")
@click.argument("name")
@click.pass_context
def town_show(ctx: click.Context, name: str) -> None:
    """Show details of a town.

    NAME is the town to show. Displays template, worker counts, and configuration.
    """
    manager: TownManager = ctx.obj["town_manager"]

    try:
        t = manager.get(name)
    except TownNotFoundError:
        click.secho(f"Error: Town '{name}' not found", fg="red", err=True)
        raise SystemExit(1)

    status_color = "green" if t.status == TownStatus.RUNNING else "red"

    click.echo(f"Town: {click.style(t.name, bold=True)}")
    click.echo(f"  Status: {click.style(t.status.value, fg=status_color)}")
    click.echo(f"  Port: {t.port}")

    # Template information
    click.echo(f"  Template: {t.template}")

    # Worker counts (from template or custom)
    effective_roles = t.get_effective_roles()
    click.echo(f"  Workers: {_format_worker_counts(effective_roles)}")
    click.echo(f"  Max Workers: {t.max_workers}")

    # Workflow
    if t.workflow:
        click.echo(f"  Workflow: {' -> '.join(t.workflow)}")

    if t.project_path:
        click.echo(f"  Project: {t.project_path}")
    if t.description:
        click.echo(f"  Description: {t.description}")
    if t.pid:
        click.echo(f"  PID: {t.pid}")
    if t.started_at:
        click.echo(f"  Started: {t.started_at}")

    click.echo(f"  Created: {t.created_at}")


@town.command("update")
@click.argument("name")
@click.option(
    "--port",
    "-p",
    type=int,
    default=None,
    help="New dashboard port",
)
@click.option(
    "--max-workers",
    "-w",
    type=int,
    default=None,
    help="New maximum concurrent workers",
)
@click.option(
    "--description",
    "-d",
    default=None,
    help="New description",
)
@click.pass_context
def town_update(
    ctx: click.Context,
    name: str,
    port: int | None,
    max_workers: int | None,
    description: str | None,
) -> None:
    """Update town configuration.

    NAME is the town to update.
    """
    manager: TownManager = ctx.obj["town_manager"]

    try:
        updated = manager.update(
            name=name,
            port=port,
            max_workers=max_workers,
            description=description,
        )
        click.secho(f"Updated town '{name}'", fg="green")
        click.echo(f"  Port: {updated.port}")
        click.echo(f"  Max Workers: {updated.max_workers}")

    except TownNotFoundError:
        click.secho(f"Error: Town '{name}' not found", fg="red", err=True)
        raise SystemExit(1)
    except PortConflictError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        raise SystemExit(1)
    except TownError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        raise SystemExit(1)


@town.command("workflow")
@click.option(
    "--current",
    "-c",
    type=str,
    required=True,
    help="Current role in the workflow (e.g., dev, qa)",
)
@click.option(
    "--next",
    "show_next",
    is_flag=True,
    default=False,
    help="Show only the next handoff target",
)
@click.option(
    "--town-name",
    "-t",
    type=str,
    default=None,
    help="Town name (auto-detected from current directory if not specified)",
)
@click.pass_context
def town_workflow(
    ctx: click.Context,
    current: str,
    show_next: bool,
    town_name: str | None,
) -> None:
    """Query workflow handoff information for a town.

    Returns the next step in the workflow for a given role. This is used by
    agents to know where to hand off work after completing their tasks.

    \b
    Examples:
      mab town workflow --current=dev --next      # Returns next handoff for dev
      mab town workflow --current=qa --next       # Returns next handoff for qa
      mab town workflow --current=dev -t mytown   # Query specific town
    """
    from mab.towns import get_next_handoff

    manager: TownManager = ctx.obj["town_manager"]

    # Determine which town to query
    if town_name:
        try:
            target_town = manager.get(town_name)
        except TownNotFoundError:
            click.secho(f"Error: Town '{town_name}' not found", fg="red", err=True)
            raise SystemExit(1)
    else:
        # Try to find town by current project path
        project_path = str(ctx.obj.get("town_path", Path.cwd()))
        towns = manager.list_towns(project_path=project_path)
        if not towns:
            # Fallback: try to find by directory name
            town_name_guess = Path(project_path).name
            try:
                target_town = manager.get(town_name_guess)
            except TownNotFoundError:
                click.secho(
                    "Error: No town found for current project. Use --town-name to specify a town.",
                    fg="red",
                    err=True,
                )
                raise SystemExit(1)
        else:
            target_town = towns[0]

    # Get workflow info
    if not target_town.workflow:
        click.secho(f"Error: Town '{target_town.name}' has no workflow defined", fg="red", err=True)
        raise SystemExit(1)

    next_handoff = get_next_handoff(current, target_town.workflow)

    if show_next:
        # Simple output for scripting: just the next step
        if next_handoff:
            click.echo(next_handoff)
        else:
            # Empty output if role not in workflow or at the end
            click.echo("")
    else:
        # Detailed output
        click.echo(f"Town: {target_town.name}")
        click.echo(f"Template: {target_town.template}")
        click.echo(f"Workflow: {' -> '.join(target_town.workflow)}")
        click.echo(f"Current role: {current}")
        if next_handoff:
            click.echo(f"Next handoff: {click.style(next_handoff, fg='green', bold=True)}")
        else:
            if current not in target_town.workflow:
                click.secho(f"Note: Role '{current}' is not in this workflow", fg="yellow")
            else:
                click.secho("Note: This is the final step in the workflow", fg="yellow")


@cli.command("fix-worktrees")
@click.option(
    "--project",
    "-p",
    "project_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Project path (default: current directory)",
)
@click.pass_context
def fix_worktrees(ctx: click.Context, project_path: Path) -> None:
    """Fix .beads symlinks in existing worktrees.

    This command repairs worktrees that were created before the symlink code
    was added, or where symlink creation failed. It replaces stale .beads
    directories with symlinks to the main project's .beads directory.

    This ensures workers can see the live beads database instead of a
    stale snapshot from when the worktree was created.
    """
    from mab.spawner import fix_worktree_beads_symlinks, get_git_root

    project = project_path.resolve()
    git_root = get_git_root(project)

    if git_root is None:
        click.secho(f"Error: {project} is not a git repository", fg="red", err=True)
        raise SystemExit(1)

    main_beads = git_root / ".beads"
    if not main_beads.exists():
        click.secho(f"No .beads directory found in {git_root}", fg="yellow")
        raise SystemExit(0)

    click.echo(f"Fixing .beads symlinks in worktrees for {git_root}...")

    fixed, errors = fix_worktree_beads_symlinks(project)

    if fixed > 0:
        click.secho(f"Fixed {fixed} worktree(s)", fg="green")
    elif errors == 0:
        click.echo("All worktrees already have correct symlinks")

    if errors > 0:
        click.secho(f"Failed to fix {errors} worktree(s)", fg="red", err=True)
        raise SystemExit(1)


def _parse_duration(duration_str: str) -> int:
    """Parse a duration string like '7d', '24h', '1w' into seconds.

    Args:
        duration_str: Duration string (e.g., '7d', '24h', '2w', '30m').

    Returns:
        Duration in seconds.

    Raises:
        ValueError: If duration format is invalid.
    """
    import re

    match = re.match(r"^(\d+)([smhdw])$", duration_str.lower())
    if not match:
        raise ValueError(
            f"Invalid duration format: {duration_str}. "
            "Use format like '7d', '24h', '2w', '30m'."
        )

    value = int(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }

    return value * multipliers[unit]


@cli.command()
@click.option(
    "--all",
    "-a",
    "cleanup_all",
    is_flag=True,
    default=False,
    help="Remove all non-running workers (stopped, crashed, failed)",
)
@click.option(
    "--older-than",
    "-o",
    type=str,
    default=None,
    help="Remove workers older than specified duration (e.g., '7d', '24h', '2w')",
)
@click.option(
    "--status",
    "-s",
    type=click.Choice(["stopped", "crashed", "failed"]),
    default=None,
    help="Remove workers with specific status only",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show what would be removed without actually removing",
)
@click.option(
    "--project",
    "-p",
    "project_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project path (defaults to current directory)",
)
@click.pass_context
def cleanup(
    ctx: click.Context,
    cleanup_all: bool,
    older_than: str | None,
    status: str | None,
    dry_run: bool,
    project_path: Path | None,
) -> None:
    """Clean up old workers from the database.

    Removes stopped, crashed, or failed workers from the worker database.
    Running workers are never removed.

    \b
    Examples:
      mab cleanup --all              # Remove all non-running workers
      mab cleanup --older-than=7d    # Remove workers older than 7 days
      mab cleanup --status=crashed   # Remove only crashed workers
      mab cleanup --all --dry-run    # Show what would be removed
    """
    from datetime import datetime, timedelta

    from mab.workers import WorkerDatabase, WorkerStatus

    # Validate options
    if not cleanup_all and not older_than and not status:
        click.secho(
            "Error: Must specify --all, --older-than, or --status",
            fg="red",
            err=True,
        )
        click.echo("Run 'mab cleanup --help' for usage information.")
        raise SystemExit(1)

    # Parse older-than duration
    max_age_seconds: int | None = None
    if older_than:
        try:
            max_age_seconds = _parse_duration(older_than)
        except ValueError as e:
            click.secho(f"Error: {e}", fg="red", err=True)
            raise SystemExit(1)

    # Determine project path
    project = project_path or ctx.obj.get("town_path", Path.cwd())
    project = Path(project).resolve()

    # Find database locations
    databases_to_check: list[Path] = []

    # Check project-specific database
    project_db = project / ".mab" / "workers.db"
    if project_db.exists():
        databases_to_check.append(project_db)

    # Check global database
    global_db = MAB_HOME / "workers.db"
    if global_db.exists():
        databases_to_check.append(global_db)

    if not databases_to_check:
        click.echo("No worker databases found.")
        raise SystemExit(0)

    # Determine which statuses to clean up
    if status:
        target_statuses = [WorkerStatus(status)]
    elif cleanup_all:
        target_statuses = [
            WorkerStatus.STOPPED,
            WorkerStatus.CRASHED,
            WorkerStatus.FAILED,
        ]
    else:
        # If only --older-than specified, default to all non-running statuses
        target_statuses = [
            WorkerStatus.STOPPED,
            WorkerStatus.CRASHED,
            WorkerStatus.FAILED,
        ]

    # Collect workers to remove
    workers_to_remove: list[tuple[Path, str, str, str, str | None]] = []
    cutoff_time = (
        datetime.now() - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    )

    for db_path in databases_to_check:
        try:
            db = WorkerDatabase(db_path)
            for target_status in target_statuses:
                workers = db.list_workers(status=target_status)
                for worker in workers:
                    # Skip running workers (should never happen but be safe)
                    if worker.status == WorkerStatus.RUNNING:
                        continue

                    # Check age if --older-than specified
                    if cutoff_time:
                        # Use stopped_at if available, otherwise created_at
                        timestamp_str = worker.stopped_at or worker.created_at
                        try:
                            # Parse ISO format timestamp
                            worker_time = datetime.fromisoformat(
                                timestamp_str.replace("Z", "+00:00")
                            )
                            # Make naive for comparison
                            if worker_time.tzinfo:
                                worker_time = worker_time.replace(tzinfo=None)
                            if worker_time > cutoff_time:
                                continue  # Worker is too recent
                        except (ValueError, TypeError):
                            continue  # Can't parse timestamp, skip

                    workers_to_remove.append(
                        (
                            db_path,
                            worker.id,
                            worker.role,
                            worker.status.value,
                            worker.stopped_at or worker.created_at,
                        )
                    )
        except Exception as e:
            click.secho(f"Warning: Could not read database {db_path}: {e}", fg="yellow")
            continue

    if not workers_to_remove:
        click.echo("No workers found matching criteria.")
        raise SystemExit(0)

    # Show what will be removed
    click.echo(f"Found {len(workers_to_remove)} worker(s) to remove:")
    for db_path, worker_id, role, worker_status, timestamp in workers_to_remove:
        db_name = "project" if "/.mab/" in str(db_path) else "global"
        click.echo(f"  - {worker_id} ({role}) [{worker_status}] {timestamp} ({db_name})")

    if dry_run:
        click.echo("\nDry run - no changes made")
        raise SystemExit(0)

    # Actually remove workers
    removed_count = 0
    errors = 0

    for db_path, worker_id, role, worker_status, _ in workers_to_remove:
        try:
            db = WorkerDatabase(db_path)
            if db.delete_worker(worker_id):
                removed_count += 1
            else:
                errors += 1
        except Exception as e:
            click.secho(f"Error removing {worker_id}: {e}", fg="red", err=True)
            errors += 1

    if removed_count > 0:
        click.secho(f"Removed {removed_count} worker(s)", fg="green")

    if errors > 0:
        click.secho(f"Failed to remove {errors} worker(s)", fg="red", err=True)
        raise SystemExit(1)


@cli.command("cleanup-worktrees")
@click.option(
    "--project",
    "-p",
    "project_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Project path (default: current directory)",
)
@click.option(
    "--all",
    "-a",
    "cleanup_all",
    is_flag=True,
    default=False,
    help="Remove ALL worktrees, including those for active workers",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show what would be removed without actually removing",
)
@click.pass_context
def cleanup_worktrees(
    ctx: click.Context, project_path: Path, cleanup_all: bool, dry_run: bool
) -> None:
    """Clean up orphaned git worktrees from stopped/killed workers.

    This command removes worktrees in .worktrees/ that are not associated
    with any active (RUNNING status) workers. Use this to reclaim disk space
    after workers are stopped or if the daemon crashes without proper cleanup.

    By default, only cleans up worktrees for workers that are no longer running.
    Use --all to remove all worktrees (will break any running workers!).

    Examples:

      \b
      mab cleanup-worktrees            # Clean orphaned worktrees
      mab cleanup-worktrees -n         # Dry run - show what would be removed
      mab cleanup-worktrees --all      # Remove ALL worktrees (dangerous)
    """
    from mab.spawner import (
        WORKTREES_DIR,
        cleanup_stale_worktrees,
        get_git_root,
        list_worktrees,
    )
    from mab.workers import WorkerDatabase, WorkerStatus

    project = project_path.resolve()
    git_root = get_git_root(project)

    if git_root is None:
        click.secho(f"Error: {project} is not a git repository", fg="red", err=True)
        raise SystemExit(1)

    worktrees_dir = git_root / WORKTREES_DIR
    if not worktrees_dir.exists():
        click.echo("No worktrees directory found - nothing to clean up")
        raise SystemExit(0)

    # List existing worktrees
    worktrees = list_worktrees(project)
    # Filter to just those in .worktrees/
    worker_worktrees = [w for w in worktrees if WORKTREES_DIR in w.get("path", "")]

    if not worker_worktrees:
        click.echo("No worker worktrees found - nothing to clean up")
        raise SystemExit(0)

    # Get active worker IDs if not cleaning all
    active_ids: set[str] = set()
    if not cleanup_all:
        # Check project-specific database
        project_db = project / ".mab" / "workers.db"
        if project_db.exists():
            try:
                db = WorkerDatabase(project_db)
                for worker in db.list_workers(status=WorkerStatus.RUNNING):
                    active_ids.add(worker.id)
            except Exception as e:
                click.secho(f"Warning: Could not read worker database: {e}", fg="yellow")

        # Also check global database
        global_db = MAB_HOME / "workers.db"
        if global_db.exists():
            try:
                db = WorkerDatabase(global_db)
                for worker in db.list_workers(
                    status=WorkerStatus.RUNNING, project_path=str(project)
                ):
                    active_ids.add(worker.id)
            except Exception:
                pass

    # Determine which worktrees to remove
    to_remove = []
    for wt in worker_worktrees:
        wt_path = Path(wt["path"])
        worker_id = wt_path.name
        if cleanup_all or worker_id not in active_ids:
            to_remove.append((worker_id, wt_path, wt.get("branch", "unknown")))

    if not to_remove:
        click.echo("No orphaned worktrees found - all worktrees belong to active workers")
        raise SystemExit(0)

    # Show what will be removed
    click.echo(f"Found {len(to_remove)} worktree(s) to remove:")
    for worker_id, wt_path, branch in to_remove:
        status = "active" if worker_id in active_ids else "orphaned"
        click.echo(f"  - {worker_id} ({branch}) [{status}]")

    if dry_run:
        click.echo("\nDry run - no changes made")
        raise SystemExit(0)

    # Actually remove them
    if cleanup_all:
        removed = cleanup_stale_worktrees(project, active_worker_ids=None)
    else:
        removed = cleanup_stale_worktrees(project, active_worker_ids=active_ids)

    if removed > 0:
        click.secho(f"Removed {removed} worktree(s)", fg="green")
    else:
        click.secho("No worktrees were removed", fg="yellow")


def main() -> None:
    """Entry point for the mab CLI."""
    cli()


if __name__ == "__main__":
    main()
