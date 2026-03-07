# AI_Knowledge_Assistant/app/observability/structured_logger.py
"""Structured logging helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def log_event(level: str, event: str, **fields: Any) -> None:
    """Emit a structured log entry with event metadata."""
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    if settings.structured_log_json:
        message = json.dumps(payload, default=str, ensure_ascii=True)
    else:
        suffix = " ".join(f"{key}={value}" for key, value in fields.items())
        message = f"{event} {suffix}".strip()

    level_name = (level or "info").lower()
    if level_name == "debug":
        logger.debug(message)
    elif level_name == "warning":
        logger.warning(message)
    elif level_name == "error":
        logger.error(message)
    else:
        logger.info(message)
