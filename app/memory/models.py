# AI_Knowledge_Assistant/app/memory/models.py
"""Shared models for memory components."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class RetrievedMemory(BaseModel):
    """Normalized, immutable memory record returned from retrieval components."""

    # 1. Enforce Immutability: Prevent accidental data mutation downstream
    # 2. Strict Types: Ignore stray kwargs passed by rogue DB adapters
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(
        ..., 
        description="Unique identifier for the vector record."
    )
    
    text: str = Field(
        ..., 
        min_length=1, 
        description="The actual text content of the retrieved memory."
    )
    
    score: float = Field(
        ..., 
        description="Similarity or relevance score from the vector database."
    )
    
    metadata: dict[str, Any] = Field(
        default_factory=dict, 
        description="Optional key-value context (e.g., timestamps, user IDs)."
    )