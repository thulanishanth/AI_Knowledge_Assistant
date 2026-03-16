# AI_Knowledge_Assistant/app/memory/memory_manager.py
"""Memory orchestrator that composes vector, window, summary and RAG contexts."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.memory.context_aggregator import ContextAggregator
from app.memory.summary_memory import SummaryMemory
from app.memory.vector_memory import VectorMemory
from app.memory.window_memory import WindowMemory
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.observability.tracing import tracing
from app.services.rag_retriever import retrieve_context
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MemoryManager:
    """High-level coordinator for conversational memory lifecycle."""

    def __init__(
        self,
        vector_memory: VectorMemory,
        window_memory: WindowMemory,
        summary_memory: SummaryMemory,
        context_aggregator: ContextAggregator,
    ) -> None:
        self._vector_memory = vector_memory
        self._window_memory = window_memory
        self._summary_memory = summary_memory
        self._context_aggregator = context_aggregator

    async def initialize_memory_manager(self) -> None:
        """Initialize memory subsystems at application startup."""
        await self._vector_memory.initialize_vector_store()
        log_event("info", "memory_manager_initialized")

    async def fetch_relevant_context(
        self,
        user_id: str,
        session_id: str,
        user_query: str,
    ) -> list[dict[str, Any]]:
        """Retrieve hybrid vector context with resilient fallback behavior."""
        with tracing.span("memory.fetch_relevant_context"), metrics.timer("memory_retrieval"):
            try:
                return await self._vector_memory.retrieve_hybrid_context(
                    user_id=user_id,
                    session_id=session_id,
                    query=user_query,
                )
            except Exception as exc:
                logger.exception("Vector retrieval failed; using window+summary fallback.")
                metrics.increment_errors("vector_retrieval")
                log_event(
                    "warning",
                    "vector_retrieval_failed",
                    error=str(exc).strip() or type(exc).__name__,
                )
                return []

    async def get_context_for_llm(
        self,
        user_id: str,
        session_id: str,
        user_query: str,
        rag_context: str | None = None,
    ) -> dict[str, Any]:
        """Assemble vector, window, summary, and RAG context with fault-tolerant concurrency."""
        with tracing.span("memory.get_context_for_llm"), metrics.timer("memory_context_assembly"):
            
            # 1. Define all retrieval tasks
            tasks = [
                self.fetch_relevant_context(user_id, session_id, user_query),
                self._window_memory.get_window(user_id, session_id),
                self._summary_memory.get_summary(user_id, session_id),
            ]
            
            if rag_context is None:
                tasks.append(asyncio.to_thread(retrieve_context, user_query))

            # 2. Execute concurrently with return_exceptions=True to prevent a single failure from crashing everything
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 3. Safely unpack and validate results
            vector_results = results[0] if not isinstance(results[0], Exception) else []
            window_messages = results[1] if not isinstance(results[1], Exception) else []
            summary = results[2] if not isinstance(results[2], Exception) else ""
            
            if rag_context is None:
                rag = results[3] if not isinstance(results[3], Exception) else ""
            else:
                rag = rag_context

            # Log component failures if any occurred
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error("Context retrieval component %s failed: %s", i, result)

            # 4. Extract safe strings
            vector_texts = [
                str(item.get("text", "")).strip()
                for item in vector_results
                if isinstance(item, dict) and item.get("text")
            ]
            window_texts = [
                f"{message.get('role', 'unknown')}: {message.get('content', '')}".strip()
                for message in window_messages
                if isinstance(message, dict) and message.get("content")
            ]

            aggregated = self._context_aggregator.aggregate(
                vector_context=vector_texts,
                window_context=window_texts,
                summary_context=summary,
                rag_context=rag or "",
            )
            
            return {
                "vector_results": vector_results,
                "window_messages": window_messages,
                "summary": summary,
                "rag_context": rag or "",
                "aggregated_context": aggregated,
            }

    async def update_memory_pipeline(
        self,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
    ) -> None:
        """Update session window chronologically, then execute heavy memory tasks concurrently."""
        with tracing.span("memory.update_pipeline"), metrics.timer("memory_update"):
            importance = self.detect_memory_importance(question, answer)
            
            # 1. Update WindowMemory sequentially to guarantee chronological order
            await self._window_memory.add_message(user_id, session_id, "user", question)
            await self._window_memory.add_message(user_id, session_id, "assistant", answer)

            # 2. Execute heavy NLP/Vector I/O operations concurrently
            background_tasks = [
                self._summary_memory.update_summary(user_id, session_id, question, answer)
            ]

            if importance >= settings.memory_importance_threshold:
                background_tasks.append(
                    self._vector_memory.store_user_memory(
                        user_id=user_id,
                        session_id=session_id,
                        question=question,
                        answer=answer,
                        importance_score=importance,
                    )
                )

            # Fire and wait for background updates, ignoring exceptions so we don't crash post-response
            await asyncio.gather(*background_tasks, return_exceptions=True)

            log_event(
                "info",
                "memory_pipeline_updated",
                user_id=user_id,
                session_id=session_id,
                importance_score=round(importance, 3),
            )

    def detect_memory_importance(self, question: str, answer: str) -> float:
        """Estimate memory importance from lexical cues and response quality."""
        text = f"{question} {answer}".lower()
        score = 0.2
        if any(
            token in text
            for token in ["always", "preference", "remember", "important", "never"]
        ):
            score += 0.35
        if any(
            token in text
            for token in ["id", "email", "phone", "date", "booking", "reservation"]
        ):
            score += 0.25
        if len(text) > 320:
            score += 0.15
        if "i don't know" in text:
            score -= 0.2
        return max(0.0, min(1.0, score))

    async def run_cleanup_forever(self, interval_seconds: int = 3600) -> None:
        """Background cleanup task for TTL-based vector memory expiration."""
        while True:
            try:
                await self._vector_memory.cleanup_old_memory()
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                # Expected behavior when the FastAPI server shuts down
                logger.info("Memory cleanup task gracefully shutting down.")
                break
            except Exception:
                logger.exception("Background memory cleanup failed")
                metrics.increment_errors("memory_cleanup")
                # Sleep a shorter amount on failure to prevent rapid retry loops
                await asyncio.sleep(60)