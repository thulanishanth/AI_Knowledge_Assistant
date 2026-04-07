#app/memory/window_memory.py
"""Session window memory for recent conversational turns."""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)


@dataclass
class SessionState:
    """Wrapper to hold a session's messages and its last active timestamp."""

    messages: deque[dict[str, str]]
    last_accessed: float = field(default_factory=time.time)


class WindowMemory:
    """Bounded, TTL-aware window memory using an LRU eviction policy."""

    def __init__(self, window_size: int | None = None, max_sessions: int = 1000) -> None:
        self._window_size = window_size or settings.window_memory_size
        self._max_sessions = max_sessions
        self._store: OrderedDict[tuple[str, str], SessionState] = OrderedDict()

    async def add_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        """Append a message and update the session's LRU position and timestamp."""
        key = (user_id, session_id)
        payload = {
            "role": str(role or "").strip() or "unknown",
            "content": str(content or "").strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if key not in self._store:
            self._store[key] = SessionState(messages=deque(maxlen=self._window_size))

        session = self._store[key]
        session.messages.append(payload)
        session.last_accessed = time.time()
        self._store.move_to_end(key)

        if len(self._store) > self._max_sessions:
            self._store.popitem(last=False)

    async def get_window(self, user_id: str, session_id: str) -> list[dict[str, str]]:
        """Return recent session messages, updating its LRU position."""
        key = (user_id, session_id)
        if key not in self._store:
            return []

        session = self._store[key]
        session.last_accessed = time.time()
        self._store.move_to_end(key)
        return list(session.messages)

    async def clear_session(self, user_id: str, session_id: str) -> None:
        """Clear all windowed messages for one session."""
        self._store.pop((user_id, session_id), None)

    async def cleanup_stale_sessions(self, max_idle_seconds: int = 3600) -> int:
        """
        Remove sessions that have not been accessed recently.
        Call this periodically from a background task.
        """
        now = time.time()
        stale_keys = [
            key
            for key, session in self._store.items()
            if (now - session.last_accessed) > max_idle_seconds
        ]

        for key in stale_keys:
            self._store.pop(key, None)

        if stale_keys:
            logger.info("Evicted %s stale sessions from WindowMemory", len(stale_keys))

        return len(stale_keys)