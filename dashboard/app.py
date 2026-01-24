"""Multi-Agent Dashboard - FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.config import CORS_ORIGINS, STATIC_DIR, TEMPLATES_DIR

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


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"})


if __name__ == "__main__":
    import uvicorn

    from dashboard.config import HOST, PORT

    uvicorn.run("dashboard.app:app", host=HOST, port=PORT, reload=True)
