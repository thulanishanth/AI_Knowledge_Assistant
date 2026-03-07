"""Session window memory for recent conversational turns."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone

from app.core.config import settings

class WindowMemory:
    """Thread-safe, bounded window memory using an LRU eviction policy."""

    def __init__(self, window_size: int | None = None, max_sessions: int = 1000) -> None:
        self._window_size = window_size or settings.window_memory_size
        self._max_sessions = max_sessions

        # OrderedDict allows us to efficiently track insertion/access order
        # to evict the oldest, inactive sessions (LRU Cache pattern).
        self._store: OrderedDict[tuple[str, str], deque[dict[str, str]]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def add_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        """Append a message, updating its LRU position and evicting old sessions if necessary."""
        key = (user_id, session_id)
        payload = {
            "role": role,
            "content": content.strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        async with self._lock:
            # Step 1: Initialize the deque if this is a brand new session
            if key not in self._store:
                self._store[key] = deque(maxlen=self._window_size)

            # Step 2: Append the new message
            self._store[key].append(payload)

            # Step 3: Mark this session as the "Most Recently Used" by moving it to the end
            self._store.move_to_end(key)

            # Step 4: Enforce the memory boundary
            # If we exceed max_sessions, remove the Least Recently Used session (at the front)
            if len(self._store) > self._max_sessions:
                self._store.popitem(last=False)

    async def get_window(self, user_id: str, session_id: str) -> list[dict[str, str]]:
        """Return recent session messages, updating its LRU position."""
        key = (user_id, session_id)
        async with self._lock:
            if key in self._store:
                # Accessing the memory means it is active; keep it alive by moving to the end
                self._store.move_to_end(key)
                return list(self._store[key])
            return []

    async def clear_session(self, user_id: str, session_id: str) -> None:
        """Clear all windowed messages for one session."""
        key = (user_id, session_id)
        async with self._lock:
            self._store.pop(key, None)
