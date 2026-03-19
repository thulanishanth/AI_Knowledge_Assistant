"""End-to-end database-grounded chat query orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.cache import TTLCache
from app.core.settings import settings
from app.infrastructure.repositories.schema_repository import TableSchema
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.observability.tracing import tracing
from app.security.input_sanitizer import SanitizedQuestion, sanitize_question
from app.services.intent_service import IntentDecision, IntentService
from app.services.response_formatter import ResponseFormatter
from app.services.schema_service import SchemaService
from app.services.sql_execution_service import QueryExecutionResult, SQLExecutionService
from app.services.sql_generation_service import SQLGenerationService, SqlGenerationResult
from app.utils.logger import get_logger

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
    """Own the request lifecycle for one chat question."""

    def __init__(
        self,
        *,
        session_manager,
        memory_manager,
        schema_service: SchemaService,
        intent_service: IntentService,
        sql_generation_service: SQLGenerationService,
        sql_execution_service: SQLExecutionService,
        response_formatter: ResponseFormatter,
    ) -> None:
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._schema_service = schema_service
        self._intent_service = intent_service
        self._sql_generation_service = sql_generation_service
        self._sql_execution_service = sql_execution_service
        self._response_formatter = response_formatter
        self._query_cache: TTLCache[QueryExecutionResult] = TTLCache(
            settings.query_cache_ttl_seconds,
        )

    async def handle_query(
        self,
        user_question: str,
        user_id: str = "anonymous",
        session_id: str | None = None,
    ) -> QueryResponse:
        sanitized = sanitize_question(user_question)
        if not sanitized.normalized:
            raise ValueError("Question cannot be empty")

        session_ctx = self._session_manager.resolve(user_id=user_id, session_id=session_id)
        metrics.increment_requests("/api/chat")

        with tracing.span("query.handle"), metrics.timer("query_total"):
            pre_intent = self._intent_service.classify(sanitized.normalized)
            if pre_intent.kind == "greeting":
                response = QueryResponse(
                    answer="Hello. Ask about the configured database table and I will answer from that data only.",
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Database assistant",
                        "message": "This assistant answers only from the configured table and session context.",
                    },
                )
                await self._update_memory(session_ctx.user_id, session_ctx.session_id, sanitized.normalized, response.answer)
                return response

            schema = await self._schema_service.get_schema()
            schema_response = self._build_schema_response(
                question=sanitized.normalized,
                schema=schema,
                session_id=session_ctx.session_id,
            )
            if schema_response is not None:
                await self._update_memory(
                    session_ctx.user_id,
                    session_ctx.session_id,
                    sanitized.normalized,
                    schema_response.answer,
                )
                return schema_response

            memory_context = await self._fetch_memory_context(
                session_ctx.user_id,
                session_ctx.session_id,
                sanitized,
                schema,
            )
            intent = self._intent_service.classify(
                sanitized.normalized,
                schema=schema,
                has_session_context=bool(memory_context["window_messages"]),
            )
            logger.info(
                "Intent classified kind=%s confidence=%.2f reasons=%s",
                intent.kind,
                intent.confidence,
                ",".join(intent.reasons),
            )

            if intent.kind != "database_query":
                answer = (
                    "I can answer only database questions about the configured MySQL table. "
                    "Ask about rows, counts, filters, aggregates, schema columns, or specific fields."
                )
                response = QueryResponse(
                    answer=answer,
                    confidence=0.3,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Database-only mode",
                        "message": answer,
                    },
                )
                await self._update_memory(session_ctx.user_id, session_ctx.session_id, sanitized.normalized, response.answer)
                return response

            try:
                sql_result = await self._sql_generation_service.generate_sql(
                    sanitized.normalized,
                    schema,
                    session_context=str(memory_context.get("aggregated_context", "")),
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("SQL generation failed")
                answer, title = self._build_generation_failure_response(str(exc))
                response = QueryResponse(
                    answer=answer,
                    confidence=0.1,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": title,
                        "message": answer,
                    },
                    meta={"error": str(exc)},
                )
                await self._update_memory(session_ctx.user_id, session_ctx.session_id, sanitized.normalized, response.answer)
                return response

            if not sql_result.is_valid:
                answer = self._build_sql_failure_message(sql_result)
                response = QueryResponse(
                    answer=answer,
                    confidence=0.25,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Need a clearer database question",
                        "message": answer,
                    },
                    meta={"validation_errors": sql_result.validation.errors},
                )
                await self._update_memory(session_ctx.user_id, session_ctx.session_id, sanitized.normalized, response.answer)
                return response

            try:
                execution_result, cached = await self._execute_with_cache(sql_result.sql)
            except RuntimeError as exc:
                answer = (
                    "I generated a safe read-only query, but the database could not complete it. "
                    f"{exc}"
                )
                response = QueryResponse(
                    answer=answer,
                    confidence=0.15,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Database execution failed",
                        "message": answer,
                    },
                    meta={"strategy": sql_result.strategy},
                )
                await self._update_memory(session_ctx.user_id, session_ctx.session_id, sanitized.normalized, response.answer)
                return response

            formatted = self._response_formatter.format_rows(
                question=sanitized.normalized,
                session_id=session_ctx.session_id,
                rows=execution_result.rows,
                truncated=execution_result.truncated,
                cached=cached,
            )
            confidence = self._score_confidence(sql_result, execution_result, intent)

            await self._update_memory(
                session_ctx.user_id,
                session_ctx.session_id,
                sanitized.normalized,
                formatted.answer,
            )

            response = QueryResponse(
                answer=formatted.answer,
                confidence=confidence,
                session_id=session_ctx.session_id,
                presentation=formatted.presentation,
                meta={
                    **formatted.meta,
                    "cached": cached,
                    "strategy": sql_result.strategy,
                    "execution_ms": round(execution_result.execution_ms, 2),
                },
            )
            log_event(
                "info",
                "query_completed",
                session_id=session_ctx.session_id,
                confidence=confidence,
                cached=cached,
                strategy=sql_result.strategy,
            )
            return response

    async def _fetch_memory_context(
        self,
        user_id: str,
        session_id: str,
        sanitized: SanitizedQuestion,
        schema: TableSchema,
    ) -> dict[str, Any]:
        rag_context = schema.to_prompt_block()
        return await self._memory_manager.get_context_for_llm(
            user_id=user_id,
            session_id=session_id,
            user_query=sanitized.normalized,
            rag_context=rag_context,
            include_vector=sanitized.is_follow_up,
        )

    async def _execute_with_cache(self, sql_query: str) -> tuple[QueryExecutionResult, bool]:
        cache_key = sql_query.strip().lower()
        cached = self._query_cache.get(cache_key)
        if cached is not None:
            return cached, True
        result = await asyncio.to_thread(self._sql_execution_service.execute, sql_query)
        self._query_cache.set(cache_key, result)
        return result, False

    async def _update_memory(
        self,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
    ) -> None:
        try:
            await self._memory_manager.update_memory_pipeline(
                user_id=user_id,
                session_id=session_id,
                question=question,
                answer=answer,
            )
        except Exception:  # pragma: no cover
            logger.exception("Memory update failed")

    @staticmethod
    def _build_sql_failure_message(sql_result: SqlGenerationResult) -> str:
        details = "; ".join(sql_result.validation.errors[:2]) or "The request could not be mapped safely."
        return (
            "I could not produce a safe read-only query for that request. "
            f"{details} Please mention the target columns or filters more explicitly."
        )

    @staticmethod
    def _build_generation_failure_response(error_message: str) -> tuple[str, str]:
        lowered = error_message.lower()
        if "credits" in lowered or "payment required" in lowered:
            return (
                "The configured Hugging Face inference account has exhausted its credits. "
                "Simple schema and rule-based questions can still work, but this request needs the configured model. "
                "Add credits or switch the configured model/provider.",
                "LLM provider unavailable",
            )
        if "credentials" in lowered or "unauthorized" in lowered:
            return (
                "The configured Hugging Face API credentials are invalid. "
                "Update the API key or switch the configured provider.",
                "LLM provider unavailable",
            )
        return (
            "I could not generate a safe database query for that request right now. "
            "Please try rephrasing the question with specific columns or filters.",
            "Query generation failed",
        )

    @staticmethod
    def _build_schema_response(
        question: str,
        schema: TableSchema,
        session_id: str,
    ) -> QueryResponse | None:
        lowered = question.lower()
        schema_terms = ("column", "columns", "coloumn", "coloumns", "field", "fields", "schema")
        if not any(term in lowered for term in schema_terms):
            return None

        if any(term in lowered for term in ("how many", "count", "number of", "total")):
            count = len(schema.columns)
            answer = (
                f"The configured table `{schema.table_name}` has {count} columns."
            )
            return QueryResponse(
                answer=answer,
                confidence=0.99,
                session_id=session_id,
                presentation={
                    "kind": "metric",
                    "title": "Column count",
                    "value": str(count),
                },
                meta={"strategy": "schema_metadata"},
            )

        if any(term in lowered for term in ("show", "list", "what", "which")):
            rows = [[column.name, column.data_type] for column in schema.columns]
            answer_lines = [
                f"The configured table `{schema.table_name}` has these columns:",
                "",
            ]
            answer_lines.extend(
                f"{index}. {column.name} ({column.data_type})"
                for index, column in enumerate(schema.columns, start=1)
            )
            return QueryResponse(
                answer="\n".join(answer_lines),
                confidence=0.99,
                session_id=session_id,
                presentation={
                    "kind": "rows",
                    "title": "Schema columns",
                    "columns": ["Column", "Type"],
                    "rows": rows,
                    "truncated": False,
                    "layout": "table",
                },
                meta={"strategy": "schema_metadata"},
            )

        return None

    @staticmethod
    def _score_confidence(
        sql_result: SqlGenerationResult,
        execution_result: QueryExecutionResult,
        intent: IntentDecision,
    ) -> float:
        score = 0.65 + min(intent.confidence, 0.2)
        if sql_result.strategy == "rule_based":
            score += 0.15
        if sql_result.strategy == "llm_repair":
            score -= 0.05
        if execution_result.rows:
            score += 0.1
        if execution_result.truncated:
            score -= 0.03
        return round(max(0.0, min(0.99, score)), 2)
