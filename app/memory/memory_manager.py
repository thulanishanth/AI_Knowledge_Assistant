# app/memory/memory_manager.py
"""Memory orchestrator that composes vector, window, summary and RAG contexts."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.core.settings import settings
from app.memory.context_aggregator import ContextAggregator
from app.memory.summary_memory import SummaryMemory
from app.memory.vector_memory import VectorMemory
from app.memory.window_memory import WindowMemory
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.observability.tracing import tracing
from app.observability.file_dumper import dump_conversation
from app.services.rag_retriever import retrieve_dynamic_rag_context

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
                logger.exception("Vector retrieval failed; using empty fallback.")
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
        include_vector: bool = True,
        rag_context: str | None = None,
        tenant_id: str = "hotel", 
    ) -> dict[str, str | list[dict[str, object]]]:
        # 1. Local RAG context fallback
        if not rag_context:
            # Pass the tenant_id to the retriever!
            rag_context = retrieve_dynamic_rag_context(tenant_id) 
        """Assemble vector, window, summary, and RAG context."""
        with tracing.span("memory.get_context_for_llm"), metrics.timer("memory_context_assembly"):
            vector_results: list[dict[str, Any]] = []
            tasks: list[Any] = []

            if include_vector:
                tasks.append(self.fetch_relevant_context(user_id, session_id, user_query))

            tasks.extend(
                [
                    self._window_memory.get_window(user_id, session_id),
                    self._summary_memory.get_summary(user_id, session_id),
                ]
            )

            if rag_context is None:
                tasks.append(asyncio.to_thread(retrieve_dynamic_rag_context))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            result_index = 0

            if include_vector:
                vector_results = (
                    results[result_index]
                    if not isinstance(results[result_index], Exception)
                    else []
                )
                result_index += 1

            window_messages = (
                results[result_index]
                if not isinstance(results[result_index], Exception)
                else []
            )
            result_index += 1

            summary = (
                results[result_index]
                if not isinstance(results[result_index], Exception)
                else ""
            )
            result_index += 1

            rag = (
                results[result_index]
                if rag_context is None and not isinstance(results[result_index], Exception)
                else rag_context or ""
            )

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error("Context retrieval component %s failed: %s", i, result)

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
                rag_context=rag,
            )

            return {
                "vector_results": vector_results,
                "window_messages": window_messages,
                "summary": summary,
                "rag_context": rag,
                "aggregated_context": aggregated,
            }

    async def update_memory_pipeline(
        self,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
        full_prompt: str = "",
        rag_context: str = "", 
        generated_sql: str = "",
        execution_status: str = "",
    ) -> None:
        """Update session memory after a completed interaction."""
        with tracing.span("memory.update_pipeline"), metrics.timer("memory_update"):
            importance = self.detect_memory_importance(question, answer)

            # --- CRITICAL FIX: HIDDEN MEMORY STATE ---
            # We save the SQL into the bot's memory so it can explain itself later!
            memory_answer = answer
            if generated_sql:
                memory_answer = f"{answer}\n(System Note - I used this SQL to get the data: {generated_sql})"

            await self._window_memory.add_message(user_id, session_id, "user", question)
            await self._window_memory.add_message(user_id, session_id, "assistant", memory_answer) # <--- Save the hidden state

            background_tasks = [
                self._summary_memory.update_summary(user_id, session_id, question, answer),
                dump_conversation(question, answer, full_prompt, rag_context, generated_sql, execution_status)
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
                logger.info("Memory cleanup task gracefully shutting down.")
                break
            except Exception:
                logger.exception("Background memory cleanup failed")
                metrics.increment_errors("memory_cleanup")
                await asyncio.sleep(60)