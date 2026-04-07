# app/services/conversation_state_store.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class LastQueryState:
    user_id: str
    session_id: str
    original_question: str
    corrected_question: str
    generated_sql: str
    executed_sql: str
    execution_status: str
    answer: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    selected_columns: list[str] = field(default_factory=list)
    source_table: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ConversationStateStore:
    """Thread-safe in-memory store for the latest successful query per session."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], LastQueryState] = {}
        self._lock = threading.RLock()

    async def save_last_query_state(
        self,
        user_id: str,
        session_id: str,
        state: LastQueryState,
    ) -> None:
        with self._lock:
            self._store[(user_id, session_id)] = state

    async def get_last_query_state(
        self,
        user_id: str,
        session_id: str,
    ) -> LastQueryState | None:
        with self._lock:
            return self._store.get((user_id, session_id))

    async def clear_last_query_state(
        self,
        user_id: str,
        session_id: str,
    ) -> None:
        with self._lock:
            self._store.pop((user_id, session_id), None)