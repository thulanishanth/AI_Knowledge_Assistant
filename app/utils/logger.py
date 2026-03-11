"""
Logging utility for standardized application-wide tracking and request tracing.
"""
import contextvars
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_REQUEST_ID_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_LOGGER_STATE = {"configured": False}


class RequestIdFilter(logging.Filter):
    """
    Logging filter to inject a unique request_id into every log record.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        """Add the request_id to the log record."""
        record.request_id = self.get_request_id()
        return True

    def get_request_id(self) -> str:
        """Retrieve the current request ID from the context variable."""
        return _REQUEST_ID_CONTEXT.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in the current context for log tracing."""
    _REQUEST_ID_CONTEXT.set(request_id or "-")


def clear_request_id() -> None:
    """Clear the request ID from the current context."""
    _REQUEST_ID_CONTEXT.set("-")


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure application-wide logging with stream and rotating file handlers."""
    if _LOGGER_STATE["configured"]:
        return

    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_path = Path(log_file) if log_file else logs_dir / "app.log"

    level_name = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s"
    )

    request_filter = RequestIdFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_filter)

    file_handler = RotatingFileHandler(
        filename=file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_filter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    _LOGGER_STATE["configured"] = True


def get_logger(name: str) -> logging.Logger:
    """Retrieve a configured logger instance by name."""
    if not _LOGGER_STATE["configured"]:
        setup_logging()
    return logging.getLogger(name)
