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


if __name__ == "__main__":
    import uvicorn

    from dashboard.config import HOST, PORT

    uvicorn.run("dashboard.app:app", host=HOST, port=PORT, reload=True)
