# app/memory/vector_memory.py
"""Vector memory orchestration for user and global knowledge retrieval."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from uuid import uuid4

from app.core.settings import settings
from app.services.embedding_service import EmbeddingService
from app.services.reranker_service import RerankerService
from app.core.logging import get_logger
from app.vector_store.vector_store_interface import VectorRecord, VectorStoreInterface

logger = get_logger(__name__)


class VectorMemory:
    """Store and retrieve user/global vectors through vector store providers."""

    def __init__(
        self,
        vector_store: VectorStoreInterface,
        embedding_service: EmbeddingService,
        reranker_service: RerankerService,
    ) -> None:
        self._vector_store = vector_store
        self._embedding_service = embedding_service
        self._reranker = reranker_service
        self._user_collection = settings.vector_collection_user
        self._knowledge_collection = settings.vector_collection_knowledge
        self._user_profile_collection = getattr(
            settings, "vector_collection_user_profile", "user_profile_collection"
        )
        self._sql_cache_collection = getattr(
            settings, "vector_collection_sql_cache", "sql_cache_collection"
        )

    async def initialize_vector_store(self) -> None:
        await self._vector_store.initialize()

    # ──────────────────────────────────────────────────────
    # LIVING USER PROFILE
    # ──────────────────────────────────────────────────────

    async def get_user_profile(self, user_id: str) -> str:
        """Fetch the user's living profile summary."""
        record_id = f"profile_{user_id}"
        try:
            dummy_embedding = await self.generate_embedding("profile search")
            records = await self._vector_store.query_records(
                self._user_profile_collection,
                query_embedding=dummy_embedding,
                top_k=1,
                filters={"user_id": user_id},
            )
            if records and records[0].id == record_id:
                return records[0].text
            return ""
        except Exception as e:
            logger.debug("Profile fetch failed or empty for %s: %s", user_id, e)
            return ""

    async def save_user_profile(self, user_id: str, profile_text: str) -> None:
        """Overwrite the user's living profile summary."""
        if not profile_text.strip():
            return
        record_id = f"profile_{user_id}"
        embedding = await self.generate_embedding(profile_text)
        await self._vector_store.upsert_records(
            self._user_profile_collection,
            [
                VectorRecord(
                    id=record_id,
                    text=profile_text.strip(),
                    embedding=embedding,
                    metadata={"user_id": user_id, "type": "living_profile"},
                )
            ],
        )

    # ──────────────────────────────────────────────────────
    # SEMANTIC SQL CACHE
    # ──────────────────────────────────────────────────────

    async def get_semantic_sql(
        self, query: str, schema_fingerprint: str, similarity_threshold: float = 0.95
    ) -> str | None:
        """Search for a highly similar cached SQL to avoid LLM calls."""
        try:
            embedding = await self.generate_embedding(query)
            records = await self._vector_store.query_records(
                collection_name=self._sql_cache_collection,
                query_embedding=embedding,
                top_k=1,
                filters={"schema_fingerprint": schema_fingerprint},
            )
            if records and records[0].score >= similarity_threshold:
                logger.info(
                    "Semantic Cache HIT (Score: %.2f) — bypassing LLM.", records[0].score
                )
                return str(records[0].metadata.get("sql", ""))
            return None
        except Exception as e:
            logger.error("Semantic SQL Cache retrieval failed: %s", e)
            return None

    async def save_semantic_sql(
        self, query: str, sql: str, schema_fingerprint: str
    ) -> None:
        """Save a proven SQL query into the Vector Database."""
        try:
            embedding = await self.generate_embedding(query)
            record_id = f"sql_{uuid4().hex}"
            await self._vector_store.upsert_records(
                collection_name=self._sql_cache_collection,
                records=[
                    VectorRecord(
                        id=record_id,
                        text=query,
                        embedding=embedding,
                        metadata={
                            "sql": sql,
                            "schema_fingerprint": schema_fingerprint,
                            "type": "semantic_sql_cache",
                        },
                    )
                ],
            )
        except Exception as e:
            logger.error("Failed to save to Semantic SQL Cache: %s", e)

    # ──────────────────────────────────────────────────────
    # USER MEMORY
    # ──────────────────────────────────────────────────────

    async def store_user_memory(
        self,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
        importance_score: float = 0.5,
        metadata: dict[str, str] | None = None,
    ) -> str:
        text = f"Question: {question.strip()}\nAnswer: {answer.strip()}"
        embedding = await self.generate_embedding(text)
        record_id = f"usr_{uuid4().hex}"

        payload: dict = {
            "user_id": user_id,
            "session_id": session_id,
            "timestamp_epoch": time.time(),
            "importance_score": float(importance_score),
            "memory_type": "conversation",
        }
        if metadata:
            payload.update(metadata)

        await self._vector_store.upsert_records(
            self._user_collection,
            [VectorRecord(id=record_id, text=text, embedding=embedding, metadata=payload)],
        )
        return record_id

    async def store_knowledge_memory(
        self,
        content: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Store a knowledge document in the global knowledge collection."""
        text = content.strip()
        if not text:
            return ""
        embedding = await self.generate_embedding(text)
        record_id = f"know_{uuid4().hex}"

        payload: dict = {
            "source": "manual_ingestion",
            "memory_type": "knowledge",
            "timestamp_epoch": time.time(),
        }
        if metadata:
            payload.update(metadata)

        await self._vector_store.upsert_records(
            self._knowledge_collection,
            [VectorRecord(id=record_id, text=text, embedding=embedding, metadata=payload)],
        )
        return record_id

    # ──────────────────────────────────────────────────────
    # RETRIEVAL
    # ──────────────────────────────────────────────────────

    async def retrieve_user_context(
        self,
        user_id: str,
        query: str,
        top_k: int | None = None,
        session_id: str | None = None,
        query_embedding: list[float] | None = None,
        topic_filter: str | None = None,
    ) -> list[dict]:
        embedding = query_embedding or await self.generate_embedding(query)
        filters: dict[str, str] = {"user_id": user_id}
        if session_id:
            filters["session_id"] = session_id
        if topic_filter:
            filters["topic"] = topic_filter

        records = await self._vector_store.query_records(
            self._user_collection,
            query_embedding=embedding,
            top_k=top_k or settings.top_k_retrieval,
            filters=filters,
        )
        return [
            {"id": r.id, "text": r.text, "score": r.score, "metadata": r.metadata, "source": "user_memory"}
            for r in records
        ]

    async def retrieve_global_context(
        self,
        query: str,
        top_k: int | None = None,
        query_embedding: list[float] | None = None,
        topic_filter: str | None = None,
    ) -> list[dict]:
        embedding = query_embedding or await self.generate_embedding(query)
        filters = {"topic": topic_filter} if topic_filter else None

        records = await self._vector_store.query_records(
            self._knowledge_collection,
            query_embedding=embedding,
            top_k=top_k or settings.top_k_retrieval,
            filters=filters,
        )
        return [
            {"id": r.id, "text": r.text, "score": r.score, "metadata": r.metadata, "source": "knowledge_memory"}
            for r in records
        ]

    async def retrieve_hybrid_context(
        self,
        user_id: str,
        query: str,
        session_id: str | None = None,
        topic_filter: str | None = None,
    ) -> list[dict]:
        try:
            embedding = await self.generate_embedding(query)
        except Exception:
            return []

        user_task = asyncio.create_task(
            self.retrieve_user_context(
                user_id=user_id,
                query=query,
                session_id=session_id,
                query_embedding=embedding,
                topic_filter=topic_filter,
            )
        )
        global_task = asyncio.create_task(
            self.retrieve_global_context(
                query=query,
                query_embedding=embedding,
                topic_filter=topic_filter,
            )
        )
        results = await asyncio.gather(user_task, global_task, return_exceptions=True)

        combined: list[dict] = []
        if isinstance(results[0], list):
            combined.extend(results[0])
        if isinstance(results[1], list):
            combined.extend(results[1])

        return await self.rank_results(query, combined, settings.rerank_top_k)

    async def rank_results(
        self, query: str, results: list[dict], top_k: int | None = None
    ) -> list[dict]:
        if not results:
            return []
        limit = top_k or settings.rerank_top_k
        if settings.enable_reranking:
            return await self._reranker.rerank(query=query, candidates=results, top_k=limit)
        return sorted(results, key=lambda item: float(item.get("score", 0.0)), reverse=True)[:limit]

    async def generate_embedding(self, text: str) -> list[float]:
        return await self._embedding_service.embed_text(text)

    async def health_check(self) -> bool:
        return await self._vector_store.health_check()

    async def cleanup_old_memory(self, ttl_days: int | None = None) -> dict[str, int]:
        ttl = ttl_days or settings.memory_ttl_days
        cutoff = time.time() - (ttl * 86400)
        u_task = asyncio.create_task(
            self._vector_store.cleanup_records_older_than(self._user_collection, cutoff)
        )
        g_task = asyncio.create_task(
            self._vector_store.cleanup_records_older_than(self._knowledge_collection, cutoff)
        )
        res = await asyncio.gather(u_task, g_task, return_exceptions=True)
        return {
            "user_deleted": res[0] if isinstance(res[0], int) else 0,
            "knowledge_deleted": res[1] if isinstance(res[1], int) else 0,
        }