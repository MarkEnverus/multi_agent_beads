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
from mab.towns import (
    PortConflictError,
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
    "--max-workers",
    "-w",
    type=int,
    default=3,
    help="Maximum concurrent workers",
)
@click.option(
    "--roles",
    "-r",
    multiple=True,
    default=["dev", "qa"],
    help="Default roles to spawn (can be specified multiple times)",
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
    max_workers: int,
    roles: tuple[str, ...],
    description: str,
) -> None:
    """Create a new orchestration town.

    NAME is the unique identifier for the town (alphanumeric with underscores).

    Examples:

        mab town create staging --port 8001

        mab town create dev --project /path/to/project --roles dev --roles qa
    """
    manager: TownManager = ctx.obj["town_manager"]

    try:
        new_town = manager.create(
            name=name,
            port=port,
            project_path=project,
            max_workers=max_workers,
            default_roles=list(roles),
            description=description,
        )

        click.secho(f"Created town '{name}' on port {new_town.port}", fg="green")
        click.echo(f"  Max workers: {new_town.max_workers}")
        click.echo(f"  Default roles: {', '.join(new_town.default_roles)}")
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

    NAME is the town to show.
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
    click.echo(f"  Max Workers: {t.max_workers}")
    click.echo(f"  Default Roles: {', '.join(t.default_roles)}")

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
