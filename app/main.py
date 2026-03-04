"""
Main entry point for the AI Knowledge Assistant FastAPI application.
Configures middleware, routes, and static file mounting.
"""
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from app.api import chat
from app.config import LOG_FILE, LOG_LEVEL
from app.utils.logger import clear_request_id, get_logger, set_request_id, setup_logging

setup_logging(LOG_LEVEL, LOG_FILE or None)
logger = get_logger(__name__)

app = FastAPI(
    title="AI Knowledge Assistant",
    description="An AI-powered assistant for querying databases with natural language.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development. In production, use specific domains.
    allow_credentials=True,
    allow_methods=["*"],  # Allows POST, GET, etc.
    allow_headers=["*"],  # Allows custom headers like Content-Type
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """
    Middleware to assign a unique request ID and log performance metrics.
    """
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

# Include API routes
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])

# 5. Serve Frontend Files with Absolute Pathing
# This logic finds the 'frontend' folder relative to the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_path = os.path.join(BASE_DIR, "frontend")

if os.path.exists(frontend_path):
    # Mount the 'frontend' folder to the root URL path
    app.mount("/", StaticFiles(directory=frontend_path, html=True, check_dir=True), name="frontend")
    logger.info("Frontend directory mounted successfully from %s:", frontend_path)
else:
    logger.warning("Frontend directory NOT FOUND at %s. Falling back to root.", frontend_path)

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        """Fallback root endpoint when frontend is missing."""
        return f"""
        <html>
            <body style="font-family: 'Inter', sans-serif; text-align: center; padding-top: 100px; background: #0f1115; color: #e1e4e8;">
                <h1 style="color: #ff6b6b;">Frontend Folder Not Found</h1>
                <p>The server is looking for the folder here: <br><code>{frontend_path}</code></p>
                <hr style="width: 50%; border: 0.5px solid #30363d; margin: 20px auto;">
                <p>Status: <span style="color: #2fd3b6;">Backend API is Running</span></p>
                <p>Visit documentation: <a href="/docs" style="color: #45a7ff;">/docs</a></p>
            </body>
        </html>
        """
if __name__ == "__main__":
    import uvicorn
    # Using the path where you usually run the app
    uvicorn.run("app.utils.main:app", host="0.0.0.0", port=8000, reload=True)
