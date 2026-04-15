#app/observability/metrics.py
"""Prometheus-compatible metrics with no-op fallback."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from prometheus_client import Counter as PROM_COUNTER_CLS  # type: ignore
    from prometheus_client import Histogram as PROM_HISTOGRAM_CLS  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    PROM_COUNTER_CLS = None
    PROM_HISTOGRAM_CLS = None


class Metrics:
    """Wrapper around Prometheus metrics with graceful no-op behavior."""

    def __init__(self) -> None:
        self._enabled = (
            settings.enable_metrics
            and PROM_COUNTER_CLS is not None
            and PROM_HISTOGRAM_CLS is not None
        )

        if self._enabled:
            self._request_counter = PROM_COUNTER_CLS(
                "assistant_requests_total", "Total assistant requests", ["route"]
            )
            self._error_counter = PROM_COUNTER_CLS(
                "assistant_errors_total", "Total assistant errors", ["component"]
            )
            self._signal_counter = PROM_COUNTER_CLS(
                "assistant_pipeline_events_total",
                "Pipeline event counts",
                ["signal"],
            )
            self._latency_histogram = PROM_HISTOGRAM_CLS(
                "assistant_latency_seconds",
                "Operation latency in seconds",
                ["operation"],
            )
        else:
            self._request_counter = None
            self._error_counter = None
            self._signal_counter = None
            self._latency_histogram = None
            if settings.enable_metrics:
                logger.warning("prometheus_client unavailable; metrics are disabled.")

    def increment_requests(self, route: str) -> None:
        """Increment per-route request counter."""
        if self._request_counter is not None:
            self._request_counter.labels(route=route).inc()

    def increment_errors(self, component: str) -> None:
        """Increment per-component error counter."""
        if self._error_counter is not None:
            self._error_counter.labels(component=component).inc()

    def increment_signal(self, signal: str) -> None:
        """Increment a generic pipeline event counter."""
        if self._signal_counter is not None:
            self._signal_counter.labels(signal=signal).inc()

    @contextmanager
    def timer(self, operation: str) -> Iterator[None]:
        """Measure elapsed time for one operation and record histogram value."""
        start = time.perf_counter()
        try:
            yield
        finally:
            if self._latency_histogram is not None:
                duration = time.perf_counter() - start
                self._latency_histogram.labels(operation=operation).observe(duration)


metrics = Metrics()
