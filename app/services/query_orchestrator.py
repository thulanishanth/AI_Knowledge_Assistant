#app/services/query_orchestrator.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.settings import settings
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.observability.tracing import tracing
from app.security.input_sanitizer import sanitize_question
from app.services.response_formatter import ResponseFormatter
from app.services.schema_service import SchemaService
from app.services.sql_execution_service import QueryExecutionResult, SQLExecutionService
from app.services.sql_generation_service import SQLGenerationService

logger = get_logger(__name__)


@dataclass(slots=True)
class QueryResponse:
    answer: str
    confidence: float
    session_id: str
    presentation: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __iter__(self):
        yield self.answer
        yield self.confidence
        yield self.session_id


class QueryOrchestrator:
    """Generic database query pipeline driven purely by LLM reasoning."""

    def __init__(
        self,
        *,
        session_manager,
        memory_manager,
        schema_service: SchemaService,
        intent_service: Any = None, # Left for backward compatibility in DI container
        sql_generation_service: SQLGenerationService,
        sql_execution_service: SQLExecutionService,
        response_formatter: ResponseFormatter,
    ) -> None:
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._schema_service = schema_service
        self._sql_generation_service = sql_generation_service
        self._sql_execution_service = sql_execution_service
        self._response_formatter = response_formatter
        self._query_cache: TTLCache[QueryExecutionResult] = TTLCache(settings.query_cache_ttl_seconds)

    async def handle_query(
        self,
        user_question: str,
        user_id: str = "anonymous",
        session_id: str | None = None,
    ) -> QueryResponse:
        sanitized = sanitize_question(user_question)
        if not sanitized.normalized:
            raise ValueError("Question cannot be empty.")

        session_ctx = self._session_manager.resolve(user_id=user_id, session_id=session_id)

        with tracing.span("query.handle"), metrics.timer("query_total"):
            schema = await self._schema_service.get_schema()
            
            session_context = ""
            try:
                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    user_query=sanitized.normalized,
                    include_vector=False,
                )
                session_context = str(memory_context.get("aggregated_context", ""))
            except Exception:
                logger.exception("Memory context load failed")

            # Rely natively on the LLM to understand intent and write the SQL
            sql_result = await self._sql_generation_service.generate_sql(
                question=sanitized.normalized,
                schema=schema,
                session_context=session_context,
            )

            if not sql_result.is_valid:
                raise ValueError("Could not generate a safe database query for this request.")

            cache_key = f"{schema.fingerprint}|{sql_result.sql}"
            execution = self._query_cache.get(cache_key)
            cached = execution is not None

            if execution is None:
                execution = self._sql_execution_service.execute(sql_result.sql)
                self._query_cache.set(cache_key, execution)

            answer, presentation = self._response_formatter.format_database_result(
                question=sanitized.normalized,
                rows=execution.rows,
                truncated=execution.truncated,
            )

            confidence = 0.95 if execution.row_count > 0 else 0.85

            try:
                await self._memory_manager.update_memory_pipeline(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    question=sanitized.normalized,
                    answer=answer,
                )
            except Exception:
                logger.exception("Memory update failed")

            log_event(
                "info",
                "query_completed",
                strategy=sql_result.strategy,
                rows=execution.row_count,
            )

            return QueryResponse(
                answer=answer,
                confidence=confidence,
                session_id=session_ctx.session_id,
                presentation=presentation,
                meta={
                    "strategy": sql_result.strategy,
                    "cached": cached,
                    "row_count": execution.row_count,
                    "execution_ms": round(execution.execution_ms, 2),
                    "sql": execution.executed_sql,
                },
            )