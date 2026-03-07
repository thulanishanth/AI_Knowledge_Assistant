# AI_Knowledge_Assistant/app/memory/models.py
"""Shared models for memory components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RetrievedMemory:
    """Normalized memory record returned from retrieval components."""

    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
