#app/core/cache.py
"""Small in-memory TTL cache used for schema and query result reuse."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    """Thread-safe TTL cache with explicit invalidation."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._entries: dict[str, CacheEntry[T]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.time():
                self._entries.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: T, ttl_seconds: int | None = None) -> T:
        ttl = max(1, ttl_seconds or self._ttl_seconds)
        with self._lock:
            self._entries[key] = CacheEntry(
                value=value,
                expires_at=time.time() + ttl,
            )
        return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def purge_expired(self) -> int:
        removed = 0
        now = time.time()
        with self._lock:
            for key in list(self._entries):
                if self._entries[key].expires_at <= now:
                    self._entries.pop(key, None)
                    removed += 1
        return removed
