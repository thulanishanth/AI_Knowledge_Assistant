#app/main.py
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
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.core.dependency_injection import container
from app.core.logging import clear_request_id, get_logger, set_request_id, setup_logging
from app.core.settings import settings

setup_logging()
logger = get_logger(__name__)


async def _run_window_cleanup_forever(interval_seconds: int = 1800) -> None:
    """Background cleanup for stale window-memory sessions."""
    while True:
        try:
            await container.window_memory.cleanup_stale_sessions(max_idle_seconds=3600)
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Window memory cleanup task gracefully shutting down.")
            break
        except Exception:
            logger.exception("Window memory cleanup task failed.")
            await asyncio.sleep(60)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    await container.initialize()

    memory_cleanup_task = asyncio.create_task(
        container.memory_manager.run_cleanup_forever()
    )
    window_cleanup_task = asyncio.create_task(
        _run_window_cleanup_forever()
    )

    app.state.memory_cleanup_task = memory_cleanup_task
    app.state.window_cleanup_task = window_cleanup_task

    logger.info("Application startup completed")
    yield

    for task_name in ("memory_cleanup_task", "window_cleanup_task"):
        task = getattr(app.state, task_name, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    logger.info("Application shutdown completed")


app = FastAPI(
    title="AI Knowledge Assistant",
    description="An AI-powered assistant for querying business data with natural language.",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.memory_cleanup_task = None
app.state.window_cleanup_task = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allowed_origins),
    allow_credentials=list(settings.cors_allowed_origins) != ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID"],
)

app.include_router(chat_router, prefix="/api/chat", tags=["Chat"])


@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Any],
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
            "Frontend directory exists, but index.html was not found."
        )
    return "AI Knowledge Assistant API is running."


@app.get("/health")
async def health_check() -> JSONResponse:
    vector_ok = False
    try:
        vector_ok = await container.vector_memory.health_check()
    except Exception:
        logger.exception("Vector health check failed")

    payload = {
        "status": "ok",
        "app": "ai-knowledge-assistant",
        "environment": settings.environment,
        "vector_store_healthy": vector_ok,
        "frontend_dir": str(settings.frontend_path),
    }
    return JSONResponse(payload)


frontend_dir = settings.frontend_path
frontend_index = frontend_dir / "index.html"

if frontend_dir.exists() and frontend_index.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(frontend_dir), html=True),
        name="frontend",
    )
    logger.info("Frontend directory mounted successfully from %s", frontend_dir)
else:
    @app.get("/")
    async def root_status() -> JSONResponse:
        return JSONResponse(
            {
                "message": _api_status_message(frontend_dir, frontend_index),
                "frontend_dir": str(frontend_dir),
            }
        )