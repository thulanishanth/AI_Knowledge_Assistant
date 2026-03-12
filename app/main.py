# AI_Knowledge_Assistant/app/main.py
"""FastAPI application bootstrap and lifecycle wiring."""

from __future__ import annotations

import asyncio                                         
import contextlib
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api import chat
from app.config import LOG_FILE, LOG_LEVEL
from app.core.dependency_injection import container
from app.utils.logger import clear_request_id, get_logger, set_request_id, setup_logging

setup_logging(LOG_LEVEL, LOG_FILE or None)
logger = get_logger(__name__)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    # Startup
    await container.initialize()
    app.state.cleanup_task = asyncio.create_task(
        container.memory_manager.run_cleanup_forever()
    )
    logger.info("Application startup completed")
    yield
    # Shutdown
    cleanup_task = app.state.cleanup_task
    if cleanup_task is not None:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
    logger.info("Application shutdown completed")

app = FastAPI(
    title="AI Knowledge Assistant",
    description="An AI-powered assistant for querying databases with natural language.",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.cleanup_task = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development. In production, use specific domains.
    allow_credentials=True,
    allow_methods=["*"],  # Allows POST, GET, etc.
    allow_headers=["*"],  # Allows custom headers like Content-Type
)
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])

@app.middleware("http")
async def request_logging_middleware(
    request: Request, call_next: Callable[[Request], Any]
) -> Response:
    """Attach request IDs and log per-request latency."""
    request_id = str(uuid.uuid4())[:8]
    set_request_id(request_id)
    start = time.perf_counter()
    logger.info("Incoming request %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Request completed status=%s duration_ms=%.2f",
            response.status_code,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_request_id()

def _api_status_message(frontend_dir: Path, frontend_index: Path) -> str:
    """Return a clear API root status message when static frontend is unavailable."""
    if frontend_dir.exists() and not frontend_index.exists():
        return (
            "AI Knowledge Assistant API is running. "
            "Frontend index.html not found."
        )
    return "AI Knowledge Assistant API is running. Frontend directory not found."


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"

if FRONTEND_DIR.exists() and FRONTEND_INDEX.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIR), html=True, check_dir=True),
        name="frontend",
    )
    logger.info("Frontend directory mounted successfully from %s", FRONTEND_DIR)
else:
    if FRONTEND_DIR.exists():
        logger.warning(
            "Frontend directory found at %s, but index.html is missing. "
            "Root will return API status.",
            FRONTEND_DIR,
        )
    else:
        logger.warning(
            "Frontend directory not found at %s. Root will return API status.",
            FRONTEND_DIR,
        )

    @app.get("/")
    def root() -> dict[str, str]:
        """Return API status when the static frontend is unavailable."""
        return {"message": _api_status_message(FRONTEND_DIR, FRONTEND_INDEX)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
