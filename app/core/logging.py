#app/core/logging.py
"""Central logging configuration and request correlation helpers."""

from __future__ import annotations

import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.core.settings import settings

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)
_configured = False


class RequestIdFilter(logging.Filter):
    """Attach the current request id to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id or "-")


def clear_request_id() -> None:
    _request_id_var.set("-")


def setup_logging(log_level: str | None = None, log_file: str | None = None) -> None:
    """Configure root logging once."""
    global _configured
    if _configured:
        return

    formatter: logging.Formatter
    if settings.structured_log_json:
        formatter = logging.Formatter("%(message)s")
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s",
        )

    root = logging.getLogger()
    effective_level = (log_level or settings.log_level).upper()
    root.setLevel(getattr(logging, effective_level, logging.INFO))
    root.handlers.clear()

    request_filter = RequestIdFilter()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_filter)
    root.addHandler(stream_handler)

    effective_file = log_file or settings.log_file
    if effective_file:
        file_path = Path(effective_file)
    else:
        file_path = Path(__file__).resolve().parents[1] / "logs" / "app.log"

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_filter)
    root.addHandler(file_handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)


def log_structured(
    logger: logging.Logger,
    level: str,
    event: str,
    **fields: Any,
) -> None:
    """Emit a structured event without leaking secrets."""
    payload = {"event": event, **fields}
    if settings.structured_log_json:
        message = json.dumps(payload, default=str, ensure_ascii=True)
    else:
        parts = [event]
        parts.extend(f"{key}={value}" for key, value in fields.items())
        message = " ".join(parts)

    method = getattr(logger, level.lower(), logger.info)
    method(message)
