"""Multi-Agent Dashboard - FastAPI application."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.config import (
    CORS_ORIGINS,
    HOST,
    LOG_LEVEL_STR,
    PORT,
    PROJECT_ROOT,
    STATIC_DIR,
    TEMPLATES_DIR,
    TOWN_NAME,
    setup_logging,
)
from dashboard.exceptions import (
    BeadError,
    BeadNotFoundError,
    DashboardError,
    LogFileError,
)
from dashboard.routes.agents import _get_active_agents
from dashboard.routes.agents import router as agents_router
from dashboard.routes.beads import router as beads_router
from dashboard.routes.logs import router as logs_router
from dashboard.routes.towns import router as towns_router
from dashboard.routes.workers import router as workers_router
from dashboard.routes.ws import router as ws_router
from dashboard.services import BeadService

# Configure logging using centralized setup
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager for startup and shutdown logging."""
    # Startup
    logger.info(
        "Dashboard starting: host=%s, port=%d, log_level=%s, town=%s",
        HOST,
        PORT,
        LOG_LEVEL_STR,
        TOWN_NAME,
    )
    logger.info(
        "Routers registered: /api/agents, /api/beads, /api/logs, "
        "/api/towns, /api/workers, /ws"
    )
    yield
    # Shutdown
    logger.info("Dashboard shutting down (town=%s)", TOWN_NAME)


# Create FastAPI application
app = FastAPI(
    title="Multi-Agent Dashboard",
    description="Real-time dashboard for multi-agent SDLC orchestration",
    version="0.1.0",
    lifespan=lifespan,
)


# Global exception handlers
@app.exception_handler(DashboardError)
async def dashboard_error_handler(request: Request, exc: DashboardError) -> JSONResponse:
    """Handle all DashboardError exceptions with consistent JSON responses."""
    logger.error(
        "Dashboard error: %s - %s (status=%d)",
        exc.__class__.__name__,
        exc.message,
        exc.status_code,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions with a generic error response."""
    logger.exception("Unexpected error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred. Please try again later.",
        },
    )


# Configure CORS middleware (localhost only)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Configure Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Include API routers
app.include_router(agents_router)
app.include_router(beads_router)
app.include_router(logs_router)
app.include_router(towns_router)
app.include_router(workers_router)
app.include_router(ws_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the main Kanban board dashboard."""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    """Render the system administration page.

    Provides worker management, daemon status, and health monitoring.
    """
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "project_path": str(PROJECT_ROOT),
            "town_name": TOWN_NAME,
            "port": PORT,
        },
    )


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request) -> HTMLResponse:
    """Render the dedicated agents monitoring page.

    Provides detailed agent list, status indicators, per-agent logs,
    and role-based filtering. Shows agent sessions detected from claude.log.
    """
    return templates.TemplateResponse(
        "agents.html",
        {"request": request},
    )


@app.get("/beads")
async def beads_redirect() -> RedirectResponse:
    """Redirect /beads to main dashboard where kanban board with beads is shown."""
    return RedirectResponse(url="/", status_code=302)


@app.get("/logs")
async def logs_redirect() -> RedirectResponse:
    """Redirect /logs to admin page where worker logs are accessible."""
    return RedirectResponse(url="/admin", status_code=302)


@app.get("/partials/kanban", response_class=HTMLResponse)
async def kanban_partial(
    request: Request,
    refresh: bool = False,
) -> HTMLResponse:
    """Render the Kanban board partial with current bead data.

    Uses batch fetching for performance - reduces 4 subprocess calls to 2,
    with 5-second TTL caching for repeated requests.

    Args:
        request: FastAPI request object.
        refresh: If True, bypass cache and fetch fresh data.
    """
    ready_beads: list[dict[str, Any]] = []
    in_progress_beads: list[dict[str, Any]] = []
    done_beads: list[dict[str, Any]] = []
    total_count = 0
    error_message: str | None = None

    try:
        # Run blocking subprocess call in thread pool to avoid blocking event loop
        kanban_data = await asyncio.to_thread(
            BeadService.get_kanban_data, done_limit=20, use_cache=not refresh
        )
        ready_beads = kanban_data["ready_beads"]
        in_progress_beads = kanban_data["in_progress_beads"]
        done_beads = kanban_data["done_beads"]
        total_count = kanban_data["total_count"]

    except BeadError as e:
        logger.warning("Failed to fetch beads for kanban: %s", e.message)
        error_message = e.message

    return templates.TemplateResponse(
        "partials/kanban.html",
        {
            "request": request,
            "ready_beads": ready_beads,
            "in_progress_beads": in_progress_beads,
            "done_beads": done_beads,
            "total_count": total_count,
            "error_message": error_message,
        },
    )


@app.get("/partials/agents", response_class=HTMLResponse)
async def agents_partial(request: Request) -> HTMLResponse:
    """Render the agent sidebar partial with current agent data."""
    agents: list[dict[str, Any]] = []
    error_message: str | None = None

    try:
        # Run blocking file I/O in thread pool to avoid blocking event loop
        agents = await asyncio.to_thread(_get_active_agents)
    except LogFileError as e:
        logger.warning("Failed to get active agents: %s", e.message)
        error_message = e.message
    except Exception as e:
        logger.exception("Unexpected error getting agents: %s", e)
        error_message = "Failed to load agent status"

    return templates.TemplateResponse(
        "partials/agent_sidebar.html",
        {
            "request": request,
            "agents": agents,
            "error_message": error_message,
        },
    )


def _generate_mermaid_graph(beads: list[dict[str, Any]]) -> tuple[str, int, int]:
    """Generate Mermaid flowchart syntax from beads with dependencies.

    Returns (mermaid_code, node_count, edge_count).
    """
    if not beads:
        return "", 0, 0

    # Build a map of bead id -> bead for quick lookup
    bead_map = {b["id"]: b for b in beads}

    # Collect all edges and nodes involved in dependencies
    edges: list[tuple[str, str]] = []
    nodes_with_deps: set[str] = set()

    for bead in beads:
        bead_id = bead["id"]
        # Check if this bead has dependencies (blocked_by)
        blocked_by = bead.get("blocked_by", [])
        if blocked_by:
            nodes_with_deps.add(bead_id)
            for dep_id in blocked_by:
                nodes_with_deps.add(dep_id)
                # Edge: dependency -> dependent (blocker -> blocked)
                edges.append((dep_id, bead_id))

    if not edges:
        return "", 0, 0

    # Build Mermaid code
    lines = ["graph TD"]

    # Add node definitions with labels (truncated title)
    for node_id in nodes_with_deps:
        bead = bead_map.get(node_id, {})
        title = bead.get("title", node_id)
        # Truncate title for readability
        if len(title) > 30:
            title = title[:27] + "..."
        # Escape special characters for Mermaid
        title = title.replace('"', "'").replace("[", "(").replace("]", ")")
        # Use short ID for node name
        short_id = node_id.split("-")[-1] if "-" in node_id else node_id
        lines.append(f'    {short_id}["{title}"]')

    # Add edges
    for from_id, to_id in edges:
        from_short = from_id.split("-")[-1] if "-" in from_id else from_id
        to_short = to_id.split("-")[-1] if "-" in to_id else to_id
        lines.append(f"    {from_short} --> {to_short}")

    # Add styles based on status
    status_colors = {
        "open": "#3b82f6",
        "in_progress": "#eab308",
        "closed": "#22c55e",
    }

    for node_id in nodes_with_deps:
        bead = bead_map.get(node_id, {})
        status = bead.get("status", "open")
        # Check if blocked
        blocked_by = bead.get("blocked_by", [])
        has_open_blockers = any(
            bead_map.get(b, {}).get("status") not in ("closed",)
            for b in blocked_by
        )
        if status == "open" and has_open_blockers:
            color = "#ef4444"  # Red for blocked
        else:
            color = status_colors.get(status, "#3b82f6")

        short_id = node_id.split("-")[-1] if "-" in node_id else node_id
        lines.append(f"    style {short_id} fill:{color}")

    # Add click handlers
    for node_id in nodes_with_deps:
        short_id = node_id.split("-")[-1] if "-" in node_id else node_id
        lines.append(f'    click {short_id} call handleNodeClick("{node_id}")')

    return "\n".join(lines), len(nodes_with_deps), len(edges)


@app.get("/partials/depgraph", response_class=HTMLResponse)
async def depgraph_partial(request: Request) -> HTMLResponse:
    """Render the dependency graph partial."""
    mermaid_code = ""
    node_count = 0
    edge_count = 0
    error_message: str | None = None

    try:
        # Run blocking subprocess calls in thread pool to avoid blocking event loop
        blocked_beads = await asyncio.to_thread(BeadService.list_blocked)

        # Also get all beads to have complete status info
        all_beads = await asyncio.to_thread(BeadService.list_beads)

        # Merge blocked_by info into all_beads
        blocked_map = {b["id"]: b.get("blocked_by", []) for b in blocked_beads}
        for bead in all_beads:
            bead["blocked_by"] = blocked_map.get(bead["id"], [])

        mermaid_code, node_count, edge_count = _generate_mermaid_graph(all_beads)

    except BeadError as e:
        logger.warning("Failed to fetch beads for dependency graph: %s", e.message)
        error_message = e.message

    return templates.TemplateResponse(
        "partials/depgraph.html",
        {
            "request": request,
            "mermaid_code": mermaid_code,
            "node_count": node_count,
            "edge_count": edge_count,
            "error_message": error_message,
        },
    )


def _render_error_modal(message: str, title: str = "Error") -> str:
    """Render an error modal HTML snippet."""
    escaped_message = message.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/80">
        <div class="bg-gray-800 rounded-lg p-6 text-center max-w-md">
            <h3 class="text-lg font-semibold text-red-400 mb-2">{title}</h3>
            <p class="text-gray-300 mb-4">{escaped_message}</p>
            <button onclick="document.getElementById('bead-detail-modal').innerHTML = ''"
                    class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">
                Close
            </button>
        </div>
    </div>
    """


@app.get("/partials/beads/{bead_id}", response_class=HTMLResponse)
async def bead_detail_partial(request: Request, bead_id: str) -> HTMLResponse:
    """Render the bead detail modal partial."""
    try:
        # Run blocking subprocess call in thread pool to avoid blocking event loop
        bead = await asyncio.to_thread(BeadService.get_bead, bead_id)
        return templates.TemplateResponse(
            "partials/bead_modal.html",
            {
                "request": request,
                "bead": bead,
            },
        )

    except BeadNotFoundError:
        logger.info("Bead not found: %s", bead_id)
        return HTMLResponse(
            content=_render_error_modal(
                f"Bead '{bead_id}' was not found. It may have been deleted.",
                title="Bead Not Found",
            ),
            status_code=200,
        )

    except BeadError as e:
        logger.warning("Failed to load bead %s: %s", bead_id, e.message)
        return HTMLResponse(
            content=_render_error_modal(
                f"Failed to load bead details: {e.message}",
                title="Load Error",
            ),
            status_code=200,
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("dashboard.app:app", host=HOST, port=PORT, reload=True)
