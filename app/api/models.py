#app/api/models.py
"""API request and response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=800)
    user_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    tenant_id: str = Field(default="default", description="Identifies the target dataset/client")


class ChatResponse(BaseModel):
    answer: str
    confidence: float = 1.0
    session_id: str | None = None
    presentation: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: str
    confidence: float | None = None


class SessionSummary(BaseModel):
    id: str
    session_id: str
    title: str


class SessionHistoryResponse(BaseModel):
    user_id: str
    sessions: list[SessionSummary]