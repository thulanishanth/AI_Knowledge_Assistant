# AI_Knowledge_Assistant/app/vector_store/vector_store_interface.py
"""Vector store abstraction for pluggable providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VectorRecord:
    """A vectorized text record ready for persistence in vector storage."""

    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VectorSearchResult:
    """A vector query result with similarity score and metadata."""

    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStoreInterface(ABC):
    """Provider-agnostic vector store interface."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize provider clients and collections."""

    @abstractmethod
    async def upsert_records(self, collection_name: str, records: list[VectorRecord]) -> None:
        """Insert or update records."""

    @abstractmethod
    async def query_records(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Run similarity search."""

    @abstractmethod
    async def delete_records(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> None:
        """Delete records by IDs and/or metadata filters."""

    @abstractmethod
    async def cleanup_records_older_than(
        self,
        collection_name: str,
        cutoff_epoch: float,
    ) -> int:
        """Delete records older than cutoff and return deleted count."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Validate provider availability."""
