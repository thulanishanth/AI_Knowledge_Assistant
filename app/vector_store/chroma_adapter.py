#app/vector_store/chroma_adapter.py
"""Chroma vector store adapter with a seamless in-memory fallback."""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import settings
from app.core.logging import get_logger
from app.vector_store.vector_store_interface import (
    VectorRecord,
    VectorSearchResult,
    VectorStoreInterface,
)

logger = get_logger(__name__)

# Optional dependency handling for chromadb
try:
    import chromadb  # type: ignore
except ImportError:
    chromadb = None


@dataclass
class ChromaConfig:
    """Holds configuration settings for the Chroma connection."""
    host: str
    port: int
    use_ssl: bool
    persist_path: str


@dataclass
class ChromaState:
    """Holds the runtime state to keep instance attributes low."""
    client: Any | None = None
    initialized: bool = False
    fallback_mode: bool = False
    in_memory_store: dict[str, list[VectorRecord]] = field(
        default_factory=lambda: defaultdict(list)
    )


class ChromaAdapter(VectorStoreInterface):
    """Vector store implementation that connects to ChromaDB."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        use_ssl: bool | None = None,
        persist_path: str | None = None,
    ) -> None:
        """Initialize the adapter with connection settings."""
        self._config = ChromaConfig(
            host=host or settings.chroma_server_host,
            port=port or settings.chroma_server_port,
            use_ssl=settings.chroma_use_ssl if use_ssl is None else use_ssl,
            persist_path=persist_path or settings.chroma_persist_path,
        )
        self._state = ChromaState()
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Safely initialize the Chroma client.
        Uses a lock to ensure this only happens once across concurrent requests.
        """
        if self._state.initialized:
            return

        async with self._lock:
            if self._state.initialized:
                return

            if chromadb is None:
                logger.warning("chromadb package missing. Using in-memory fallback.")
                self._state.fallback_mode = True
                self._state.initialized = True
                return

            self._state.client = await self._build_client()
            self._state.initialized = True
            logger.info("Chroma adapter initialized. Fallback mode: %s", self._state.fallback_mode)

    async def _build_client(self) -> Any | None:
        """Attempt to connect to the Chroma HTTP server, falling back to local storage."""
        try:
            client = chromadb.HttpClient(
                host=self._config.host,
                port=self._config.port,
                ssl=self._config.use_ssl,
            )
            await asyncio.to_thread(client.heartbeat)
            logger.info("Connected to Chroma HTTP server %s:%s",
                        self._config.host,
                        self._config.port)
            return client

        except (ConnectionError, RuntimeError, OSError, ValueError) as server_error:
            logger.warning("Chroma HTTP unavailable: %s", str(server_error).strip())
            return await self._build_persistent_client()

    async def _build_persistent_client(self) -> Any | None:
        """Attempt to build a persistent local Chroma client as a backup."""
        try:
            client = chromadb.PersistentClient(path=self._config.persist_path)
            await asyncio.to_thread(client.heartbeat)
            logger.info("Connected to Chroma persistent DB at %s", self._config.persist_path)
            return client

        except (ConnectionError, RuntimeError, OSError, ValueError) as persistent_err:
            logger.warning("Chroma persistent client unavailable: %s", str(persistent_err).strip())
            self._state.fallback_mode = True
            return None

    @staticmethod
    def _format_chroma_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
        """Safely formats metadata filters for ChromaDB requirements."""
        if not filters:
            return None

        if len(filters) == 1:
            return filters

        and_conditions = []
        for key, value in filters.items():
            and_conditions.append({key: value})

        return {"$and": and_conditions}

    async def upsert_records(self, collection_name: str, records: list[VectorRecord]) -> None:
        """Insert or update records in the database."""
        await self.initialize()
        if not records:
            return

        if self._state.fallback_mode or self._state.client is None:
            self._upsert_in_memory(collection_name, records)
            return

        collection = await asyncio.to_thread(
            self._state.client.get_or_create_collection, collection_name
        )

        ids = [record.id for record in records]
        embeddings = [record.embedding for record in records]
        documents = [record.text for record in records]
        metadatas = [record.metadata for record in records]

        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    async def query_records(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Search for the most similar records based on a vector embedding."""
        await self.initialize()
        if top_k <= 0:
            return []

        if self._state.fallback_mode or self._state.client is None:
            return self._query_in_memory(collection_name, query_embedding, top_k, filters)

        collection = await asyncio.to_thread(
            self._state.client.get_or_create_collection, collection_name
        )
        formatted_filters = self._format_chroma_filters(filters)

        result = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=formatted_filters,
            include=["distances", "documents", "metadatas"],
        )

        return self._parse_query_results(result)

    @staticmethod
    def _parse_query_results(result: dict[str, Any]) -> list[VectorSearchResult]:
        """Convert raw ChromaDB output into standard VectorSearchResult objects."""
        distances = (result.get("distances") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]

        responses: list[VectorSearchResult] = []

        for idx, record_id in enumerate(ids):
            distance = float(distances[idx]) if idx < len(distances) else 1.0
            score = 1.0 - max(0.0, distance)

            text = str(documents[idx]) if idx < len(documents) else ""
            metadata = metadatas[idx] if (idx < len(metadatas) and metadatas[idx]) else {}

            responses.append(
                VectorSearchResult(
                    id=str(record_id),
                    text=text,
                    score=score,
                    metadata=metadata,
                )
            )

        return responses

    async def delete_records(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> None:
        """Delete specific records by ID or by matching metadata filters."""
        await self.initialize()

        if self._state.fallback_mode or self._state.client is None:
            self._delete_in_memory(collection_name, ids, filters)
            return

        collection = await asyncio.to_thread(
            self._state.client.get_or_create_collection, collection_name
        )

        kwargs: dict[str, Any] = {}
        if ids:
            kwargs["ids"] = ids
        if filters:
            kwargs["where"] = self._format_chroma_filters(filters)

        if kwargs:
            await asyncio.to_thread(collection.delete, **kwargs)

    async def cleanup_records_older_than(self, collection_name: str, cutoff_epoch: float) -> int:
        """Remove records that are older than the given timestamp."""
        await self.initialize()

        if self._state.fallback_mode or self._state.client is None:
            return self._cleanup_in_memory(collection_name, cutoff_epoch)

        collection = await asyncio.to_thread(
            self._state.client.get_or_create_collection, collection_name
        )
        existing = await asyncio.to_thread(collection.get, include=["metadatas"])

        ids = existing.get("ids", [])
        metadatas = existing.get("metadatas", [])

        stale_ids = []
        for record_id, metadata in zip(ids, metadatas):
            safe_meta = metadata or {}
            record_time = float(safe_meta.get("timestamp_epoch", cutoff_epoch + 1))

            if record_time < cutoff_epoch:
                stale_ids.append(record_id)

        if stale_ids:
            await asyncio.to_thread(collection.delete, ids=stale_ids)

        return len(stale_ids)

    async def health_check(self) -> bool:
        """Check if the connection to the vector database is healthy."""
        await self.initialize()
        if self._state.fallback_mode:
            return True

        if self._state.client is None:
            return False

        try:
            await asyncio.to_thread(self._state.client.heartbeat)
            return True
        except (ConnectionError, RuntimeError, OSError, ValueError):
            return False

    # ----------------------------------------------------------------------
    # In-Memory Fallback Methods (Used when ChromaDB is unreachable)
    # ----------------------------------------------------------------------

    def _upsert_in_memory(self, collection_name: str, records: list[VectorRecord]) -> None:
        """Insert or update records in the memory store."""
        existing_records = {r.id: r for r in self._state.in_memory_store[collection_name]}
        for record in records:
            existing_records[record.id] = record
        self._state.in_memory_store[collection_name] = list(existing_records.values())

    def _query_in_memory(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[VectorSearchResult]:
        """Query records directly from the memory store."""
        records = self._state.in_memory_store.get(collection_name, [])
        matched: list[VectorSearchResult] = []

        for record in records:
            if filters and not self._metadata_matches(record.metadata, filters):
                continue

            similarity = self._cosine_similarity(query_embedding, record.embedding)
            matched.append(
                VectorSearchResult(
                    id=record.id,
                    text=record.text,
                    score=similarity,
                    metadata=record.metadata,
                )
            )

        matched.sort(key=lambda item: item.score, reverse=True)
        return matched[:top_k]

    def _delete_in_memory(
        self,
        collection_name: str,
        ids: list[str] | None,
        filters: dict[str, Any] | None,
    ) -> None:
        """Delete records directly from the memory store."""
        records = self._state.in_memory_store.get(collection_name, [])
        to_keep: list[VectorRecord] = []
        id_set = set(ids or [])

        for record in records:
            id_matches = bool(id_set) and record.id in id_set
            filter_matches = bool(filters) and self._metadata_matches(record.metadata, filters)

            if id_matches or filter_matches:
                continue

            to_keep.append(record)

        self._state.in_memory_store[collection_name] = to_keep

    def _cleanup_in_memory(self, collection_name: str, cutoff_epoch: float) -> int:
        """Cleanup stale records directly from the memory store."""
        records = self._state.in_memory_store.get(collection_name, [])
        starting_count = len(records)
        valid_records = []

        for record in records:
            record_time = float(record.metadata.get("timestamp_epoch", cutoff_epoch + 1))
            if record_time >= cutoff_epoch:
                valid_records.append(record)

        self._state.in_memory_store[collection_name] = valid_records
        return starting_count - len(valid_records)

    # ----------------------------------------------------------------------
    # Helper Math and Validation Methods
    # ----------------------------------------------------------------------

    @staticmethod
    def _metadata_matches(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Check if dictionary metadata matches all filter values."""
        for key, expected_value in filters.items():
            if metadata.get(key) != expected_value:
                return False
        return True

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        """Calculates how mathematically similar two vector arrays are."""
        if not left or not right or len(left) != len(right):
            return 0.0

        numerator = sum(x * y for x, y in zip(left, right))
        left_norm = math.sqrt(sum(x * x for x in left))
        right_norm = math.sqrt(sum(y * y for y in right))

        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0

        return numerator / (left_norm * right_norm)
