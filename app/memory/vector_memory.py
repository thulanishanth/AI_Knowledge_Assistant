#app/memory/vector_memory.py
"""Vector memory orchestration for user, knowledge, and business-logic retrieval."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from uuid import uuid4

from app.core.logging import get_logger
from app.core.settings import settings
from app.services.embedding_service import EmbeddingService
from app.services.reranker_service import RerankerService
from app.vector_store.vector_store_interface import VectorRecord, VectorStoreInterface

logger = get_logger(__name__)


class VectorMemory:
    """Store and retrieve vectors through vector store providers."""

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
        self._business_logic_collection = getattr(
            settings,
            "vector_collection_business_logic",
            "business_logic_collection",
        )

    async def initialize_vector_store(self) -> None:
        await self._vector_store.initialize()

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

        now_epoch = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()
        record_id = f"usr_{uuid4().hex}"

        payload = {
            "user_id": user_id,
            "session_id": session_id,
            "question": question.strip(),
            "answer": answer.strip(),
            "timestamp": now_iso,
            "timestamp_epoch": now_epoch,
            "importance_score": float(importance_score),
            "source": "chatbot",
            "memory_type": "conversation",
            "version": "v1",
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
        embedding = await self.generate_embedding(content)
        now_epoch = time.time()
        record_id = f"knw_{uuid4().hex}"

        payload = {
            "timestamp_epoch": now_epoch,
            "source": "knowledge_ingestion",
            "memory_type": "knowledge",
            "version": "v1",
        }
        if metadata:
            payload.update(metadata)

        await self._vector_store.upsert_records(
            self._knowledge_collection,
            [VectorRecord(id=record_id, text=content.strip(), embedding=embedding, metadata=payload)],
        )
        return record_id

    async def store_business_logic(
        self,
        content: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        embedding = await self.generate_embedding(content)
        now_epoch = time.time()
        record_id = f"biz_{uuid4().hex}"

        payload = {
            "timestamp_epoch": now_epoch,
            "source": "business_logic_ingestion",
            "memory_type": "business_logic",
            "version": "v1",
        }
        if metadata:
            payload.update(metadata)

        await self._vector_store.upsert_records(
            self._business_logic_collection,
            [VectorRecord(id=record_id, text=content.strip(), embedding=embedding, metadata=payload)],
        )
        return record_id

    async def retrieve_user_context(
        self,
        user_id: str,
        query: str,
        top_k: int | None = None,
        session_id: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, object]]:
        embedding = query_embedding or await self.generate_embedding(query)

        filters: dict[str, str] = {"user_id": user_id}
        if session_id:
            filters["session_id"] = session_id

        records = await self._vector_store.query_records(
            self._user_collection,
            query_embedding=embedding,
            top_k=top_k or settings.top_k_retrieval,
            filters=filters,
        )

        return [
            {
                "id": record.id,
                "text": record.text,
                "score": record.score,
                "metadata": record.metadata,
                "source": "user_memory",
            }
            for record in records
        ]

    async def retrieve_global_context(
        self,
        query: str,
        top_k: int | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, object]]:
        embedding = query_embedding or await self.generate_embedding(query)

        records = await self._vector_store.query_records(
            self._knowledge_collection,
            query_embedding=embedding,
            top_k=top_k or settings.top_k_retrieval,
            filters=None,
        )

        return [
            {
                "id": record.id,
                "text": record.text,
                "score": record.score,
                "metadata": record.metadata,
                "source": "knowledge_memory",
            }
            for record in records
        ]

    async def retrieve_business_logic_context(
        self,
        query: str,
        domain: str | None = None,
        top_k: int | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, object]]:
        embedding = query_embedding or await self.generate_embedding(query)

        filters = {"memory_type": "business_logic"}
        if domain:
            filters["domain"] = domain

        records = await self._vector_store.query_records(
            self._business_logic_collection,
            query_embedding=embedding,
            top_k=top_k or settings.rerank_top_k,
            filters=filters,
        )

        return [
            {
                "id": record.id,
                "text": record.text,
                "score": record.score,
                "metadata": record.metadata,
                "source": "business_logic_memory",
            }
            for record in records
        ]

    async def retrieve_hybrid_context(
        self,
        user_id: str,
        query: str,
        session_id: str | None = None,
    ) -> list[dict[str, object]]:
        try:
            embedding = await self.generate_embedding(query)
        except Exception as e:
            logger.error("Failed to generate embedding for hybrid retrieval: %s", e)
            return []

        user_task = asyncio.create_task(
            self.retrieve_user_context(
                user_id=user_id,
                query=query,
                top_k=settings.top_k_retrieval,
                session_id=session_id,
                query_embedding=embedding,
            )
        )
        global_task = asyncio.create_task(
            self.retrieve_global_context(
                query=query,
                top_k=settings.top_k_retrieval,
                query_embedding=embedding,
            )
        )

        results = await asyncio.gather(user_task, global_task, return_exceptions=True)

        combined = []
        user_results, global_results = results

        if isinstance(user_results, list):
            combined.extend(user_results)
        else:
            logger.error("User context retrieval failed during hybrid search: %s", user_results)

        if isinstance(global_results, list):
            combined.extend(global_results)
        else:
            logger.error("Global context retrieval failed during hybrid search: %s", global_results)

        return await self.rank_results(query, combined, settings.rerank_top_k)

    async def rank_results(
        self,
        query: str,
        results: list[dict[str, object]],
        top_k: int | None = None,
    ) -> list[dict[str, object]]:
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
        cutoff_epoch = time.time() - (ttl * 86400)

        user_task = asyncio.create_task(
            self._vector_store.cleanup_records_older_than(self._user_collection, cutoff_epoch)
        )
        global_task = asyncio.create_task(
            self._vector_store.cleanup_records_older_than(self._knowledge_collection, cutoff_epoch)
        )
        business_task = asyncio.create_task(
            self._vector_store.cleanup_records_older_than(self._business_logic_collection, cutoff_epoch)
        )

        results = await asyncio.gather(user_task, global_task, business_task, return_exceptions=True)

        deleted_user = results[0] if isinstance(results[0], int) else 0
        deleted_knowledge = results[1] if isinstance(results[1], int) else 0
        deleted_business = results[2] if isinstance(results[2], int) else 0

        if isinstance(results[0], Exception):
            logger.error("User memory cleanup failed: %s", results[0])
        if isinstance(results[1], Exception):
            logger.error("Knowledge memory cleanup failed: %s", results[1])
        if isinstance(results[2], Exception):
            logger.error("Business logic cleanup failed: %s", results[2])

        logger.info(
            "Memory cleanup completed user_deleted=%s knowledge_deleted=%s business_logic_deleted=%s",
            deleted_user,
            deleted_knowledge,
            deleted_business,
        )
        return {
            "user_memory_deleted": deleted_user,
            "knowledge_memory_deleted": deleted_knowledge,
            "business_logic_deleted": deleted_business,
        }