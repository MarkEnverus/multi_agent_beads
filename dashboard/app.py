"""Multi-Agent Dashboard - FastAPI application."""

import json
import subprocess
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.config import CORS_ORIGINS, STATIC_DIR, TEMPLATES_DIR
from dashboard.routes.agents import _get_active_agents
from dashboard.routes.agents import router as agents_router
from dashboard.routes.beads import router as beads_router
from dashboard.routes.logs import router as logs_router

# Create FastAPI application
app = FastAPI(
    title="Multi-Agent Dashboard",
    description="Real-time dashboard for multi-agent SDLC orchestration",
    version="0.1.0",
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


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"})


def _run_bd_command(args: list[str]) -> tuple[bool, str]:
    """Run a bd command and return (success, output)."""
    try:
        result = subprocess.run(
            ["bd", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, "bd command not found"
    except Exception as e:
        return False, str(e)


def _parse_beads_json(output: str) -> list[dict[str, Any]]:
    """Parse JSON output from bd command."""
    try:
        result = json.loads(output)
        if isinstance(result, list):
            return result  # type: ignore[return-value]
        return []
    except json.JSONDecodeError:
        return []


def _sort_by_priority(beads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort beads by priority (P0 first)."""
    return sorted(beads, key=lambda b: b.get("priority", 4))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the main Kanban board dashboard."""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request},
    )


@app.get("/partials/kanban", response_class=HTMLResponse)
async def kanban_partial(request: Request) -> HTMLResponse:
    """Render the Kanban board partial with current bead data."""
    # Fetch ready beads (open, no blockers)
    success, output = _run_bd_command(["ready", "--json"])
    ready_beads = _sort_by_priority(_parse_beads_json(output)) if success else []

    # Fetch in-progress beads
    success, output = _run_bd_command(["list", "--json", "--status", "in_progress", "--limit", "0"])
    in_progress_beads = _sort_by_priority(_parse_beads_json(output)) if success else []

    # Fetch closed beads (last 20)
    success, output = _run_bd_command(["list", "--json", "--status", "closed", "--limit", "20"])
    done_beads = _parse_beads_json(output) if success else []

    # Calculate total count
    success, output = _run_bd_command(["list", "--json", "--limit", "0"])
    all_beads = _parse_beads_json(output) if success else []
    total_count = len(all_beads)

    return templates.TemplateResponse(
        "partials/kanban.html",
        {
            "request": request,
            "ready_beads": ready_beads,
            "in_progress_beads": in_progress_beads,
            "done_beads": done_beads,
            "total_count": total_count,
        },
    )


@app.get("/partials/agents", response_class=HTMLResponse)
async def agents_partial(request: Request) -> HTMLResponse:
    """Render the agent sidebar partial with current agent data."""
    agents = _get_active_agents()

    return templates.TemplateResponse(
        "partials/agent_sidebar.html",
        {
            "request": request,
            "agents": agents,
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
    # Fetch all beads with blocked_by info
    success, output = _run_bd_command(["blocked", "--json"])
    blocked_beads = _parse_beads_json(output) if success else []

    # Also get all beads to have complete status info
    success, output = _run_bd_command(["list", "--json", "--limit", "0"])
    all_beads = _parse_beads_json(output) if success else []

    # Merge blocked_by info into all_beads
    blocked_map = {b["id"]: b.get("blocked_by", []) for b in blocked_beads}
    for bead in all_beads:
        bead["blocked_by"] = blocked_map.get(bead["id"], [])

    mermaid_code, node_count, edge_count = _generate_mermaid_graph(all_beads)

    return templates.TemplateResponse(
        "partials/depgraph.html",
        {
            "request": request,
            "mermaid_code": mermaid_code,
            "node_count": node_count,
            "edge_count": edge_count,
        },
    )


@app.get("/partials/beads/{bead_id}", response_class=HTMLResponse)
async def bead_detail_partial(request: Request, bead_id: str) -> HTMLResponse:
    """Render the bead detail modal partial."""
    success, output = _run_bd_command(["show", bead_id, "--json"])

    if not success:
        return HTMLResponse(
            content="""
            <div class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/80">
                <div class="bg-gray-800 rounded-lg p-6 text-center">
                    <p class="text-red-400 mb-4">Failed to load bead details</p>
                    <button onclick="document.getElementById('bead-detail-modal').innerHTML = ''"
                            class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">
                        Close
                    </button>
                </div>
            </div>
            """,
            status_code=200,
        )

    beads = _parse_beads_json(output)
    if not beads:
        return HTMLResponse(
            content="""
            <div class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/80">
                <div class="bg-gray-800 rounded-lg p-6 text-center">
                    <p class="text-gray-400 mb-4">Bead not found</p>
                    <button onclick="document.getElementById('bead-detail-modal').innerHTML = ''"
                            class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">
                        Close
                    </button>
                </div>
            </div>
            """,
            status_code=200,
        )

    return templates.TemplateResponse(
        "partials/bead_modal.html",
        {
            "request": request,
            "bead": beads[0],
        },
    )


if __name__ == "__main__":
    import uvicorn

    from dashboard.config import HOST, PORT

    uvicorn.run("dashboard.app:app", host=HOST, port=PORT, reload=True)
