# AI_Knowledge_Assistant/app/api/chat.py
"""Chat API routes backed by the database-grounded query orchestrator."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.api.models import ChatMessage, ChatRequest, ChatResponse, SessionHistoryResponse
from app.core.dependency_injection import container
from app.observability.metrics import metrics
from app.services.query_service import handle_query
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _history_repository():
    return container.chat_history_repository


def save_chat_message(
    user_id: str,
    session_id: str,
    role: str,
    message: str,
    confidence: float | None = None,
) -> None:
    _history_repository().save_message(
        user_id=user_id,
        session_id=session_id,
        role=role,
        message=message,
        confidence=confidence,
    )


def _fetch_user_history_sync(user_id: str) -> list[dict[str, str]]:
    return _history_repository().list_user_sessions(user_id)


def _fetch_session_history_sync(session_id: str) -> list[dict[str, str | float | None]]:
    return [
        {
            "role": message.role,
            "message": message.content,
            "confidence": message.confidence,
        }
        for message in _history_repository().get_session_messages(session_id)
    ]


@router.post("/", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """Handle one user question against the configured database table."""
    user_id = request.user_id or "anonymous"
    try:
        result = await handle_query(
            request.question,
            user_id=user_id,
            session_id=request.session_id,
        )

        answer = getattr(result, "answer", None)
        confidence = getattr(result, "confidence", None)
        session_id = getattr(result, "session_id", None)
        presentation = getattr(result, "presentation", None)
        meta = getattr(result, "meta", {})

        if answer is None or confidence is None or session_id is None:
            answer, confidence, session_id = result
            presentation = None
            meta = {}

        await asyncio.to_thread(
            save_chat_message,
            user_id,
            session_id,
            "user",
            request.question,
            None,
        )
        await asyncio.to_thread(
            save_chat_message,
            user_id,
            session_id,
            "assistant",
            answer,
            confidence,
        )

        return ChatResponse(
            answer=answer,
            confidence=confidence,
            session_id=session_id,
            presentation=presentation,
            meta=meta or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Chat request failed")
        metrics.increment_errors("chat_endpoint")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected error in chat endpoint")
        metrics.increment_errors("chat_endpoint")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/history/{user_id}")
async def get_user_history(user_id: str) -> dict[str, list[dict[str, str]]]:
    rows = await asyncio.to_thread(_fetch_user_history_sync, user_id)
    return {"history": rows}


@router.get("/session/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(session_id: str) -> SessionHistoryResponse:
    rows = await asyncio.to_thread(_fetch_session_history_sync, session_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionHistoryResponse(
        session_id=session_id,
        messages=[
            ChatMessage(
                role=str(row["role"]),
                content=str(row["message"]),
                confidence=row.get("confidence"),
            )
            for row in rows
        ],
    )
