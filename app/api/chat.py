# AI_Knowledge_Assistant/app/api/chat.py
"""Chat API routes and request/response contracts."""

import asyncio
from fastapi import APIRouter, HTTPException
from mysql.connector import Error as MySQLError
from pydantic import BaseModel, Field

from app.observability.metrics import metrics
from app.services.query_service import handle_query
from app.utils.logger import get_logger
from app.db.mysql import create_db_connection, close_connection

router = APIRouter()
logger = get_logger(__name__)

# ==========================================
# 1. Request and Response Models
# ==========================================

class ChatRequest(BaseModel):
    """Incoming payload for conversational chat requests."""
    question: str = Field(..., min_length=1)
    user_id: str | None = None
    session_id: str | None = None

class ChatResponse(BaseModel):
    """Normalized API response returned by chat endpoint."""
    answer: str
    confidence: float = 1.0
    session_id: str | None = None

class ChatMessage(BaseModel):
    """Represents a single message in a session history."""
    role: str = Field(..., description="Must be 'user' or 'bot'")
    content: str = Field(..., description="The text content of the message")
    confidence: float | None = Field(default=None, description="AI confidence score")

class SessionHistoryResponse(BaseModel):
    """The response model for the session history endpoint."""
    session_id: str
    messages: list[ChatMessage]


# ==========================================
# 2. Database Helper Functions (Synchronous)
# ==========================================

def _fetch_user_history_sync(user_id: str) -> list[dict]:
    """Synchronously fetch user history from MySQL."""
    query = """
        SELECT session_id, message AS title
        FROM chat_messages
        WHERE user_id = %s AND role = 'user'
        ORDER BY created_at ASC
    """
    conn = None
    try:
        conn = create_db_connection()
        if not conn or not conn.is_connected():
            return []
        # Passing dictionary=True gives us the same behavior as aiomysql.DictCursor
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()
        cursor.close()
        return rows
    except (MySQLError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.error("Error fetching history from MySQL: %s", exc)
        return []
    finally:
        close_connection(conn)

def _fetch_session_history_sync(session_id: str) -> list[dict]:
    """Synchronously fetch a specific session's messages from MySQL."""
    query = """
        SELECT role, message, confidence
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY created_at ASC
    """
    conn = None
    try:
        conn = create_db_connection()
        if not conn or not conn.is_connected():
            raise RuntimeError("Database connection failed")
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, (session_id,))
        rows = cursor.fetchall()
        cursor.close()
        return rows
    except (MySQLError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.error("Database error while fetching session %s: %s", session_id, exc)
        raise RuntimeError(f"Database error: {exc}") from exc
    finally:
        close_connection(conn)

def save_chat_message(
    user_id: str,
    session_id: str,
    role: str,
    message: str,
    confidence: float | None = None,
) -> None:
    """
    Save a chat message to MySQL.
    Args:
        user_id: Unique identifier for the user
        session_id: Chat session identifier
        role: 'user' or 'assistant'
        message: Text content of the message
        confidence: Optional AI confidence score
    """
    query = """
        INSERT INTO chat_messages (user_id, session_id, role, message, confidence)
        VALUES (%s, %s, %s, %s, %s)
    """
    conn = None
    try:
        conn = create_db_connection()
        if not conn or not conn.is_connected():
            logger.error("Database connection failed while saving chat message")
            return
        cursor = conn.cursor()
        cursor.execute(query, (user_id, session_id, role, message, confidence))
        conn.commit()
        cursor.close()

        logger.info(
            "Chat message saved user_id=%s session_id=%s role=%s",
            user_id,
            session_id,
            role,
        )

    except (MySQLError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.error("Error saving chat message: %s", exc)

    finally:
        close_connection(conn)

# ==========================================
# 3. Chat API Endpoints
# ==========================================

@router.post("/", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """Handle a chat request through the full query pipeline."""
    logger.info("Chat request received")

    user_id = request.user_id or "anonymous"

    try:
        answer, confidence, resolved_session_id = await handle_query(
            request.question,
            user_id=user_id,
            session_id=request.session_id,
        )

        # Save user message
        await asyncio.to_thread(
            save_chat_message,
            user_id,
            resolved_session_id,
            "user",
            request.question,
            None,
        )

        # Save assistant response
        await asyncio.to_thread(
            save_chat_message,
            user_id,
            resolved_session_id,
            "assistant",
            answer,
            confidence,
        )

        logger.info("Chat request completed with confidence %.2f", confidence)

        return ChatResponse(
            answer=answer,
            confidence=confidence,
            session_id=resolved_session_id,
        )

    except (RuntimeError, ValueError, TypeError, KeyError) as e:
        logger.exception("Unhandled error in chat endpoint")
        metrics.increment_errors("chat_endpoint")

        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no error message."
        raise HTTPException(status_code=500, detail=error_message) from e

@router.get("/history/{user_id}")
async def get_user_history(user_id: str):
    """Fetch past sessions from MySQL for the frontend sidebar."""
    logger.info("Fetching sidebar history from MySQL for user_id=%s", user_id)

    # Offload the synchronous database call to a background thread
    rows = await asyncio.to_thread(_fetch_user_history_sync, user_id)

    # Deduplicate to get only the first question of each session for the title
    seen_sessions = set()
    history_items = []

    for row in rows:
        sid = row["session_id"]
        if sid not in seen_sessions:
            seen_sessions.add(sid)
            history_items.append(
                {
                    "id": sid,  # Safe fallback for ID
                    "title": row["title"],
                    "session_id": sid,
                }
            )

    # Reverse the list so the newest sessions appear at the top
    history_items.reverse()

    # Return the top 15 most recent sessions
    return {"history": history_items[:15]}

@router.get("/session/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(session_id: str):
    """Retrieve the exact chronological conversation history from MySQL."""
    logger.info("Fetching full chat history for session_id=%s", session_id)

    try:
        # Offload the synchronous database call to a background thread
        raw_messages = await asyncio.to_thread(_fetch_session_history_sync, session_id)

        if not raw_messages:
            raise HTTPException(status_code=404, detail="Session not found")

        formatted_messages = []
        for row in raw_messages:
            formatted_messages.append(
                ChatMessage(
                    role=row["role"],
                    content=row["message"],
                    confidence=row.get("confidence"),
                )
            )

        return SessionHistoryResponse(
            session_id=session_id,
            messages=formatted_messages,
        )

    except RuntimeError as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
