#app/api/chat.py
"""Chat API routes backed by the database-grounded query orchestrator."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.api.models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    SessionHistoryResponse,
    SessionSummary,
)
from app.core.dependency_injection import container
from app.core.logging import get_logger
from app.observability.metrics import metrics

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


def _fetch_session_history_sync(
    session_id: str,
) -> list[dict[str, str | float | None]]:
    return [
        {
            "role": message.role,
            "content": message.content,
            "confidence": message.confidence,
        }
        for message in _history_repository().get_session_messages(session_id)
    ]


@router.post("/", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """Handle one user question against the configured database table."""
    user_id = (request.user_id or "").strip() or "anonymous"

    try:
        result = await container.query_orchestrator.handle_query(
            user_question=request.question,
            user_id=user_id,
            session_id=request.session_id,
        )

        save_chat_message(
            user_id=user_id,
            session_id=result.session_id,
            role="user",
            message=request.question,
        )
        save_chat_message(
            user_id=user_id,
            session_id=result.session_id,
            role="assistant",
            message=result.answer,
            confidence=result.confidence,
        )

        metrics.increment_requests("/api/chat")

        return ChatResponse(
            answer=result.answer,
            confidence=result.confidence,
            session_id=result.session_id,
            presentation=result.presentation,
            meta=result.meta,
        )

    except ValueError as exc:
        logger.warning("Validation error in chat endpoint: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled error in chat endpoint")
        metrics.increment_errors("chat_endpoint")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing chat request.",
        ) from exc


@router.get("/history/{user_id}", response_model=SessionHistoryResponse)
async def get_user_history(user_id: str) -> SessionHistoryResponse:
    """Return the list of sessions for the given user."""
    try:
        sessions = await asyncio.to_thread(_fetch_user_history_sync, user_id)
        return SessionHistoryResponse(
            user_id=user_id,
            sessions=[SessionSummary(**item) for item in sessions],
        )
    except Exception as exc:
        logger.exception("Failed to fetch user history")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch user history.",
        ) from exc


@router.get("/session/{session_id}", response_model=list[ChatMessage])
async def get_session_history(session_id: str) -> list[ChatMessage]:
    """Return the full message history for one session."""
    try:
        messages = await asyncio.to_thread(_fetch_session_history_sync, session_id)
        return [ChatMessage(**message) for message in messages]
    except Exception as exc:
        logger.exception("Failed to fetch session history")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch session history.",
        ) from exc