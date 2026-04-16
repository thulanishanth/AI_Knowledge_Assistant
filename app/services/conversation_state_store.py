# app/services/conversation_state_store.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class DialogueState:
    """Tracks semantic conversational context for the planner and follow-ups."""

    last_metric: str | None = None
    last_dimensions: list[str] = field(default_factory=list)
    last_filters: dict[str, str] = field(default_factory=dict)
    last_date_range: str | None = None
    comparison_target: str | None = None
    last_answer_summary: str | None = None
    last_sql: str | None = None
    pending_clarification: str | None = None
    last_route: str | None = None
    last_standalone_question: str | None = None

    def to_prompt_payload(self) -> dict[str, Any]:
        """Return a compact JSON-serializable view for planning prompts."""
        return {
            "last_metric": self.last_metric,
            "last_dimensions": self.last_dimensions,
            "last_filters": self.last_filters,
            "last_date_range": self.last_date_range,
            "comparison_target": self.comparison_target,
            "last_answer_summary": self.last_answer_summary,
            "last_sql": self.last_sql,
            "pending_clarification": self.pending_clarification,
            "last_route": self.last_route,
            "last_standalone_question": self.last_standalone_question,
        }


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
    dialogue_state: DialogueState = field(default_factory=DialogueState)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ConversationStateStore:
    """Thread-safe in-memory store for latest successful query and dialogue state."""

    def __init__(self) -> None:
        self._query_store: dict[tuple[str, str], LastQueryState] = {}
        self._dialogue_store: dict[tuple[str, str], DialogueState] = {}
        self._lock = threading.RLock()

    async def save_last_query_state(
        self,
        user_id: str,
        session_id: str,
        state: LastQueryState,
    ) -> None:
        with self._lock:
            key = (user_id, session_id)
            self._query_store[key] = state
            self._dialogue_store[key] = state.dialogue_state

    async def get_last_query_state(
        self,
        user_id: str,
        session_id: str,
    ) -> LastQueryState | None:
        with self._lock:
            return self._query_store.get((user_id, session_id))

    async def save_dialogue_state(
        self,
        user_id: str,
        session_id: str,
        state: DialogueState,
    ) -> None:
        with self._lock:
            key = (user_id, session_id)
            self._dialogue_store[key] = state
            last_query = self._query_store.get(key)
            if last_query is not None:
                last_query.dialogue_state = state

    async def get_dialogue_state(
        self,
        user_id: str,
        session_id: str,
    ) -> DialogueState:
        with self._lock:
            return self._dialogue_store.get((user_id, session_id), DialogueState())

    async def clear_last_query_state(
        self,
        user_id: str,
        session_id: str,
    ) -> None:
        with self._lock:
            key = (user_id, session_id)
            self._query_store.pop(key, None)
            self._dialogue_store.pop(key, None)