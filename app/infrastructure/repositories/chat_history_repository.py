"""Repository for persisted chat history and session metadata."""

from __future__ import annotations

from dataclasses import dataclass

from mysql.connector import Error

from app.core.settings import settings
from app.infrastructure.mysql_pool import close_connection, create_db_connection
from app.security.input_sanitizer import normalize_role
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class SessionMessage:
    role: str
    content: str
    confidence: float | None = None


class ChatHistoryRepository:
    """Read and write chat history using parameterized SQL."""

    def save_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        message: str,
        confidence: float | None = None,
    ) -> None:
        query = """
            INSERT INTO chat_messages (user_id, session_id, role, message, confidence)
            VALUES (%s, %s, %s, %s, %s)
        """
        connection = None
        cursor = None
        try:
            connection = create_db_connection()
            if connection is None:
                return
            cursor = connection.cursor()
            cursor.execute(
                query,
                (user_id, session_id, normalize_role(role), message, confidence),
            )
            connection.commit()
        except Error as exc:
            logger.error("Failed to persist chat history: %s", exc)
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:  # pragma: no cover
                    logger.debug("Rollback failed", exc_info=True)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    logger.debug("Cursor close failed", exc_info=True)
            close_connection(connection)

    def list_user_sessions(self, user_id: str) -> list[dict[str, str]]:
        query = """
            SELECT session_id, message AS title
            FROM chat_messages
            WHERE user_id = %s AND role = 'user'
            ORDER BY created_at DESC
        """
        connection = None
        cursor = None
        try:
            connection = create_db_connection()
            if connection is None:
                return []
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, (user_id,))
            rows = cursor.fetchall()
            seen: set[str] = set()
            sessions: list[dict[str, str]] = []
            for row in rows:
                session_id = str(row.get("session_id", "")).strip()
                if not session_id or session_id in seen:
                    continue
                seen.add(session_id)
                sessions.append(
                    {
                        "id": session_id,
                        "session_id": session_id,
                        "title": str(row.get("title", "")).strip() or "Untitled session",
                    }
                )
            return sessions[: settings.session_history_limit]
        except Error as exc:
            logger.error("Failed to load user sessions: %s", exc)
            return []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    logger.debug("Cursor close failed", exc_info=True)
            close_connection(connection)

    def get_session_messages(self, session_id: str) -> list[SessionMessage]:
        query = """
            SELECT role, message, confidence
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at ASC
        """
        connection = None
        cursor = None
        try:
            connection = create_db_connection()
            if connection is None:
                return []
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, (session_id,))
            rows = cursor.fetchall()
            return [
                SessionMessage(
                    role=normalize_role(str(row.get("role", ""))),
                    content=str(row.get("message", "")),
                    confidence=row.get("confidence"),
                )
                for row in rows
            ]
        except Error as exc:
            logger.error("Failed to load session messages: %s", exc)
            return []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    logger.debug("Cursor close failed", exc_info=True)
            close_connection(connection)
