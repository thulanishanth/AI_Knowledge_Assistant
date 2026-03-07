# AI_Knowledge_Assistant/app/observability/tracing.py
"""OpenTelemetry tracing wrapper with safe fallbacks."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from opentelemetry import trace as OTEL_TRACE_API  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    OTEL_TRACE_API = None


class Tracing:
    """OpenTelemetry tracing facade with automatic no-op fallback."""

    # pylint: disable=too-few-public-methods
    def __init__(self) -> None:
        self._enabled = settings.enable_tracing and OTEL_TRACE_API is not None
        if self._enabled:
            self._tracer = OTEL_TRACE_API.get_tracer(settings.otel_service_name)
        else:
            self._tracer = None
            if settings.enable_tracing:
                logger.warning("opentelemetry not installed; tracing disabled.")

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        """Create a tracing span when tracing is enabled."""
        if self._tracer is None:
            yield
            return
        with self._tracer.start_as_current_span(name):
            yield


tracing = Tracing()
