"""In-memory repository for chat history and session metadata."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.settings import settings
from app.security.input_sanitizer import normalize_role


@dataclass(slots=True)
class SessionMessage:
    role: str
    content: str
    confidence: float | None = None


@dataclass(slots=True)
class _StoredMessage:
    role: str
    content: str
    confidence: float | None
    created_at: datetime


class ChatHistoryRepository:
    """Read and write chat history in process memory."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages_by_session: dict[str, list[_StoredMessage]] = {}
        self._session_index: dict[str, dict[str, object]] = {}

    def save_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        message: str,
        confidence: float | None = None,
    ) -> None:
        normalized_role = normalize_role(role)
        content = str(message or "").strip()
        now = datetime.now(timezone.utc)

        with self._lock:
            self._messages_by_session.setdefault(session_id, []).append(
                _StoredMessage(
                    role=normalized_role,
                    content=content,
                    confidence=confidence,
                    created_at=now,
                )
            )

            entry = self._session_index.setdefault(
                session_id,
                {
                    "user_id": user_id,
                    "title": "Untitled session",
                    "updated_at": now,
                },
            )
            entry["user_id"] = user_id
            entry["updated_at"] = now

            if normalized_role == "user" and content and entry["title"] == "Untitled session":
                entry["title"] = content

    def list_user_sessions(self, user_id: str) -> list[dict[str, str]]:
        with self._lock:
            sessions = [
                {
                    "id": session_id,
                    "session_id": session_id,
                    "title": str(meta.get("title") or "Untitled session"),
                    "updated_at": meta.get("updated_at"),
                }
                for session_id, meta in self._session_index.items()
                if meta.get("user_id") == user_id
            ]

        sessions.sort(
            key=lambda item: item.get("updated_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        return [
            {
                "id": str(item["id"]),
                "session_id": str(item["session_id"]),
                "title": str(item["title"]),
            }
            for item in sessions[: settings.session_history_limit]
        ]

    def get_session_messages(self, session_id: str) -> list[SessionMessage]:
        with self._lock:
            stored_messages = list(self._messages_by_session.get(session_id, []))

        return [
            SessionMessage(
                role=message.role,
                content=message.content,
                confidence=message.confidence,
            )
            for message in stored_messages
        ]
