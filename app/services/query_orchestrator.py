# app/services/query_orchestrator.py
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.settings import settings
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.observability.tracing import tracing
from app.security.input_sanitizer import sanitize_question
from app.services.confidence_checker import check_confidence
from app.services.conversation_state_store import (
    ConversationStateStore,
    LastQueryState,
)
from app.services.intent_service import IntentService
from app.services.llm_client import call_llm
from app.services.prompt_builder import PromptBuilder
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
    """High-accuracy database query pipeline with business-term resolution."""

    def __init__(
        self,
        *,
        session_manager,
        memory_manager,
        schema_service: SchemaService,
        intent_service: IntentService,
        business_term_detector,
        business_logic_search_service,
        business_logic_resolver,
        business_logic_injector,
        sql_generation_service: SQLGenerationService,
        sql_execution_service: SQLExecutionService,
        response_formatter: ResponseFormatter,
        conversation_state_store: ConversationStateStore,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._schema_service = schema_service
        self._intent_service = intent_service
        self._business_term_detector = business_term_detector
        self._business_logic_search_service = business_logic_search_service
        self._business_logic_resolver = business_logic_resolver
        self._business_logic_injector = business_logic_injector
        self._sql_generation_service = sql_generation_service
        self._sql_execution_service = sql_execution_service
        self._response_formatter = response_formatter
        self._conversation_state_store = conversation_state_store
        self._prompt_builder = prompt_builder
        self._query_cache: TTLCache[QueryExecutionResult] = TTLCache(
            settings.query_cache_ttl_seconds
        )

    async def handle_query(
        self,
        user_question: str,
        user_id: str = "anonymous",
        session_id: str | None = None,
    ) -> QueryResponse:
        sanitized = sanitize_question(user_question)
        if not sanitized.normalized:
            raise ValueError("Question cannot be empty.")

        session_ctx = self._session_manager.resolve(
            user_id=user_id,
            session_id=session_id,
        )

        with tracing.span("query.handle"), metrics.timer("query_total"):
            schema = await self._schema_service.get_schema()

            session_context = ""
            debug_rag = ""
            debug_summary = ""
            debug_window: list[dict[str, Any]] = []
            debug_vector: list[dict[str, Any]] = []

            try:
                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    user_query=sanitized.normalized,
                    include_vector=True,
                    rag_context=None,
                )
                session_context = str(memory_context.get("aggregated_context", ""))
                debug_rag = str(memory_context.get("rag_context", ""))
                debug_summary = str(memory_context.get("summary", ""))
                debug_window = list(memory_context.get("window_messages", []))
                debug_vector = list(memory_context.get("vector_results", []))
            except Exception:
                logger.exception("Memory context load failed")

            analysis = await asyncio.to_thread(
                self._intent_service.analyze,
                user_question=sanitized.normalized,
                memory_context=session_context,
            )
            intent = analysis.get("intent", "database_query")

            last_state = await self._conversation_state_store.get_last_query_state(
                session_ctx.user_id,
                session_ctx.session_id,
            )

            business_debug: dict[str, Any] = {
                "detected_terms": [],
                "search_results": {},
                "resolved_terms": [],
                "unresolved_terms": [],
                "injected_block": "",
            }

            def build_debug_meta(
                strategy: str,
                sql: str = "",
                ms: float = 0.0,
                rows: int = 0,
                cached: bool = False,
                sql_prompt: str | None = None,
                synth_prompt: str | None = None,
            ) -> dict[str, Any]:
                return {
                    "strategy": strategy,
                    "cached": cached,
                    "row_count": rows,
                    "execution_ms": round(ms, 2),
                    "sql": sql,
                    "memory_architecture": {
                        "1_rag_schema": debug_rag,
                        "2_recent_window": debug_window,
                        "3_rolling_summary": debug_summary,
                        "4_chromadb_vector_matches": debug_vector,
                        "5_final_aggregated_context": session_context,
                    },
                    "business_term_resolution": business_debug,
                    "intent_analysis": analysis,
                    "llm_prompts": {
                        "sql_generation": sql_prompt,
                        "synthesis": synth_prompt,
                    },
                }

            if intent == "schema_inquiry":
                schema_answer = schema.to_prompt_block()
                return QueryResponse(
                    answer=schema_answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": f"Schema for {schema.table_name}",
                        "message": schema_answer,
                    },
                    meta=build_debug_meta("schema_inquiry"),
                )

            if intent in {"greeting", "general_chitchat"}:
                chat_answer = analysis.get(
                    "direct_response",
                    "Hello! How can I help you with your data?",
                )
                try:
                    await self._memory_manager.update_memory_pipeline(
                        user_id=session_ctx.user_id,
                        session_id=session_ctx.session_id,
                        question=sanitized.normalized,
                        answer=chat_answer,
                    )
                except Exception:
                    logger.debug("Memory update skipped for greeting/chitchat", exc_info=True)

                return QueryResponse(
                    answer=chat_answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "text", "message": chat_answer},
                    meta=build_debug_meta("intent_chitchat"),
                )

            if intent == "ask_for_sql":
                if last_state is None:
                    answer = "I do not have a previous SQL query in this session yet."
                else:
                    answer = (
                        f"Here is the SQL from the last successful answer:\n\n"
                        f"```sql\n{last_state.executed_sql or last_state.generated_sql}\n```"
                    )
                return QueryResponse(
                    answer=answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "text", "message": answer},
                    meta=build_debug_meta(
                        "ask_for_sql",
                        sql=last_state.executed_sql if last_state else "",
                    ),
                )

            if intent == "ask_for_source":
                answer = self._build_source_answer(last_state, schema.table_name)
                return QueryResponse(
                    answer=answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "text", "message": answer},
                    meta=build_debug_meta(
                        "ask_for_source",
                        sql=last_state.executed_sql if last_state else "",
                    ),
                )

            if intent == "explain_last_answer":
                answer = self._build_explanation_answer(last_state)
                return QueryResponse(
                    answer=answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "text", "message": answer},
                    meta=build_debug_meta(
                        "explain_last_answer",
                        sql=last_state.executed_sql if last_state else "",
                    ),
                )

            final_query = self._resolve_final_question(
                analysis=analysis,
                sanitized_question=sanitized.normalized,
                last_state=last_state,
            )

            if intent == "refine_last_query" and last_state is None:
                answer = "There is no previous successful query in this session to refine yet."
                return QueryResponse(
                    answer=answer,
                    confidence=0.7,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "message": answer},
                    meta=build_debug_meta("refine_last_query_missing_state"),
                )

            difficulty_score = int(analysis.get("difficulty_score", 50))
            target_model, is_cloud = "local-llm", False

            logger.info(
                "Model router selected model=%s is_cloud=%s difficulty=%s",
                target_model,
                is_cloud,
                difficulty_score,
            )

            vector_rule_block = self._extract_knowledge_rules(debug_vector)
            local_rule_block = self._schema_service.get_local_rule_block()
            clean_business_rules = self._merge_rule_blocks(local_rule_block, vector_rule_block)
            examples_context = self._schema_service.get_relevant_examples(
                question=final_query,
                schema=schema,
            )

            # ---------------------------------------------------------
            # BUSINESS TERM DETECTION -> SEARCH -> RESOLUTION -> INJECT
            # ---------------------------------------------------------
            active_context = self._schema_service.get_business_context()
            domain_hint = self._infer_domain_hint(schema.table_name, active_context)

            detected_terms = self._business_term_detector.detect_terms(
                question=final_query,
                known_schema_columns=list(schema.column_names),
                active_context=active_context,
            )
            business_debug["detected_terms"] = detected_terms

            if detected_terms:
                search_results = await self._business_logic_search_service.search_terms(
                    terms=detected_terms,
                    domain_hint=domain_hint,
                    top_k_per_term=5,
                )
                business_debug["search_results"] = {
                    term: [
                        {
                            "id": match.get("id"),
                            "score": match.get("score"),
                            "text": str(match.get("text", ""))[:250],
                            "metadata": match.get("metadata", {}),
                        }
                        for match in matches
                    ]
                    for term, matches in search_results.items()
                }

                resolution = self._business_logic_resolver.resolve(search_results)
                business_debug["resolved_terms"] = [
                    {
                        "user_term": item.user_term,
                        "canonical_term": item.canonical_term,
                        "sql_condition": item.sql_condition,
                        "score": item.score,
                        "source": item.source,
                    }
                    for item in resolution.resolved_terms
                ]
                business_debug["unresolved_terms"] = resolution.unresolved_terms

                if resolution.unresolved_terms:
                    answer = self._business_logic_injector.build_insufficient_context_message(
                        resolution.unresolved_terms
                    )
                    return QueryResponse(
                        answer=answer,
                        confidence=0.15,
                        session_id=session_ctx.session_id,
                        presentation={"kind": "notice", "message": answer},
                        meta=build_debug_meta("business_logic_insufficient"),
                    )

                injected_block = self._business_logic_injector.build_injection_block(resolution)
                business_debug["injected_block"] = injected_block
                clean_business_rules = self._merge_rule_blocks(clean_business_rules, injected_block)

            max_attempts = 3
            attempt = 1
            db_error_message: str | None = None
            previous_sql: str | None = None
            execution: QueryExecutionResult | None = None
            sql_result = None
            debug_sql_prompt = ""
            cached = False

            while attempt <= max_attempts:
                current_context = clean_business_rules
                if attempt > 1 and db_error_message and previous_sql:
                    current_context = self._merge_rule_blocks(
                        clean_business_rules,
                        (
                            "Retry guidance:\n"
                            f"- Previous SQL failed: {previous_sql}\n"
                            f"- Database error: {db_error_message}"
                        ),
                    )

                debug_sql_prompt = self._prompt_builder.build_sql_prompt(
                    question=final_query,
                    schema=schema,
                    session_context=current_context,
                    examples_context=examples_context,
                )

                sql_result = await self._sql_generation_service.generate_sql(
                    question=final_query,
                    schema=schema,
                    session_context=current_context,
                    examples_context=examples_context,
                    model=target_model,
                    is_cloud=is_cloud,
                )

                logger.info("Generated SQL attempt=%s sql=%s", attempt, sql_result.sql)

                if not sql_result.is_valid:
                    answer = self._build_invalid_sql_answer(sql_result.validation.errors)
                    if sql_result.notice:
                        answer = f"{sql_result.notice}\n\n{answer}"

                    try:
                        await self._memory_manager.update_memory_pipeline(
                            user_id=session_ctx.user_id,
                            session_id=session_ctx.session_id,
                            question=final_query,
                            answer=answer,
                            full_prompt=debug_sql_prompt,
                            rag_context=current_context,
                            generated_sql=sql_result.sql,
                            execution_status="Failed (Validation Blocked)",
                        )
                    except Exception:
                        logger.debug("Memory pipeline failed during SQL validation block", exc_info=True)

                    return QueryResponse(
                        answer=answer,
                        confidence=0.2,
                        session_id=session_ctx.session_id,
                        presentation={
                            "kind": "notice",
                            "title": "Query Blocked",
                            "message": answer,
                        },
                        meta=build_debug_meta(
                            "validation_blocked",
                            sql=sql_result.sql,
                            sql_prompt=debug_sql_prompt,
                        ),
                    )

                cache_key = f"{schema.fingerprint}|{sql_result.sql}"
                execution = self._query_cache.get(cache_key)
                cached = execution is not None

                if execution is None:
                    execution = self._sql_execution_service.execute(sql_result.sql)
                    if execution.error_message:
                        db_error_message = execution.error_message
                        previous_sql = sql_result.sql
                        attempt += 1
                        continue
                    self._query_cache.set(cache_key, execution)

                break

            if execution is None or execution.error_message:
                failed_msg = (
                    f"I could not produce a reliable answer from the current schema and rules. "
                    f"Last database error: {db_error_message or 'unknown error'}"
                )

                try:
                    await self._memory_manager.update_memory_pipeline(
                        user_id=session_ctx.user_id,
                        session_id=session_ctx.session_id,
                        question=final_query,
                        answer=failed_msg,
                        full_prompt=debug_sql_prompt,
                        rag_context=clean_business_rules,
                        generated_sql=sql_result.sql if sql_result else "",
                        execution_status=f"Failed after {max_attempts} attempts",
                    )
                except Exception:
                    logger.debug("Memory pipeline failed during DB failure block", exc_info=True)

                return QueryResponse(
                    answer=failed_msg,
                    confidence=0.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "error", "message": db_error_message},
                    meta=build_debug_meta(
                        "execution_failed",
                        sql=sql_result.sql if sql_result else "",
                        sql_prompt=debug_sql_prompt,
                    ),
                )

            explanation = "Here are the results I found."
            debug_synthesis_prompt = None

            if execution.rows:
                try:
                    data_preview = json.dumps(execution.rows[:5], default=str)
                    debug_synthesis_prompt = self._prompt_builder.build_synthesis_prompt(
                        question=final_query,
                        data_preview=data_preview,
                        row_count=execution.row_count,
                        executed_sql=execution.executed_sql,
                    )
                    explanation = await asyncio.to_thread(
                        call_llm,
                        prompt=debug_synthesis_prompt,
                        max_tokens=180,
                        temperature=0.0,
                    )
                except Exception as exc:
                    logger.error("Synthesis failed: %s", exc)
                    explanation = self._fallback_answer_from_rows(
                        final_query,
                        execution.rows,
                        execution.row_count,
                    )
            else:
                explanation = "I ran the query, but I could not find any data matching your request."

            if sql_result.notice:
                explanation = f"{sql_result.notice}\n\n{explanation}"

            _, presentation = self._response_formatter.format_database_result(
                question=final_query,
                rows=execution.rows,
                truncated=execution.truncated,
            )

            grounding_context = "\n\n".join(
                block
                for block in [
                    schema.to_prompt_block(),
                    clean_business_rules,
                    examples_context,
                    json.dumps(execution.rows[:5], default=str) if execution.rows else "",
                ]
                if block
            )
            confidence = check_confidence(explanation, grounding_context)
            if execution.rows and confidence < 0.8:
                confidence = 0.8
            if not execution.rows:
                confidence = min(confidence, 0.85)

            exec_status = (
                f"Success ({execution.row_count} rows)"
                if execution.row_count > 0
                else "Success (0 rows)"
            )

            await self._conversation_state_store.save_last_query_state(
                session_ctx.user_id,
                session_ctx.session_id,
                LastQueryState(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    original_question=sanitized.normalized,
                    corrected_question=final_query,
                    generated_sql=sql_result.sql,
                    executed_sql=execution.executed_sql,
                    execution_status=exec_status,
                    answer=explanation,
                    rows=execution.rows[:10],
                    row_count=execution.row_count,
                    truncated=execution.truncated,
                    selected_columns=execution.selected_columns,
                    source_table=schema.table_name,
                ),
            )

            try:
                await self._memory_manager.update_memory_pipeline(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    question=final_query,
                    answer=explanation,
                    full_prompt=debug_sql_prompt,
                    rag_context=clean_business_rules,
                    generated_sql=sql_result.sql,
                    execution_status=exec_status,
                )
            except Exception:
                logger.debug("Memory pipeline failed during success path", exc_info=True)

            log_event(
                "info",
                "query_completed",
                strategy=sql_result.strategy,
                rows=execution.row_count,
                cached=cached,
            )

            return QueryResponse(
                answer=explanation,
                confidence=round(confidence, 2),
                session_id=session_ctx.session_id,
                presentation=presentation,
                meta=build_debug_meta(
                    strategy=sql_result.strategy,
                    sql=execution.executed_sql,
                    ms=execution.execution_ms,
                    rows=execution.row_count,
                    cached=cached,
                    sql_prompt=debug_sql_prompt,
                    synth_prompt=debug_synthesis_prompt,
                ),
            )

    @staticmethod
    def _merge_rule_blocks(*blocks: str) -> str:
        clean = [str(block).strip() for block in blocks if str(block).strip()]
        return "\n\n".join(clean)

    @staticmethod
    def _extract_knowledge_rules(vector_results: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        seen: set[str] = set()

        for item in vector_results:
            if item.get("source") != "knowledge_memory":
                continue
            text = str(item.get("text", "")).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            lines.append(f"- {text}")

        return "\n".join(lines[:15])

    @staticmethod
    def _resolve_final_question(
        analysis: dict[str, Any],
        sanitized_question: str,
        last_state: LastQueryState | None,
    ) -> str:
        corrected = str(analysis.get("corrected_query", sanitized_question)).strip()
        intent = str(analysis.get("intent", "database_query")).strip()
        is_follow_up = bool(analysis.get("is_follow_up", False))

        if intent == "refine_last_query" and last_state is not None:
            return (
                f"{last_state.corrected_question}. "
                f"Apply this refinement: {sanitized_question}"
            )

        if is_follow_up and last_state is not None and len(corrected.split()) < 6:
            return (
                f"{last_state.corrected_question}. "
                f"Follow-up request: {sanitized_question}"
            )

        return corrected or sanitized_question

    @staticmethod
    def _build_invalid_sql_answer(errors: list[str]) -> str:
        lowered = " | ".join(errors).lower()

        if "insufficient context" in lowered or "could not ground" in lowered:
            return (
                "I could not generate a reliable SQL query from the current schema and business rules. "
                "This usually means the requested metric or business logic is not explicitly defined yet."
            )

        if "forbidden" in lowered or "only select" in lowered or "reference only" in lowered:
            return f"Blocked for safety: {'; '.join(errors)}"

        return f"I could not produce a reliable SQL query: {'; '.join(errors)}"

    @staticmethod
    def _build_source_answer(
        last_state: LastQueryState | None,
        default_table: str,
    ) -> str:
        if last_state is None:
            return "I do not have a previous successful answer in this session yet."

        columns = ", ".join(last_state.selected_columns) if last_state.selected_columns else "Unknown"
        table_name = last_state.source_table or default_table

        return (
            f"The last answer was based on table `{table_name}`.\n"
            f"Selected columns: {columns}.\n"
            f"Rows returned in the answer pipeline: {last_state.row_count}.\n"
            f"Executed SQL:\n```sql\n{last_state.executed_sql or last_state.generated_sql}\n```"
        )

    @staticmethod
    def _build_explanation_answer(last_state: LastQueryState | None) -> str:
        if last_state is None:
            return "I do not have a previous successful answer in this session yet."

        columns = ", ".join(last_state.selected_columns) if last_state.selected_columns else "Unknown"

        return (
            f"The last answer came from running SQL against the source table `{last_state.source_table or 'unknown'}`.\n"
            f"It answered this cleaned question: {last_state.corrected_question}\n"
            f"Selected columns: {columns}\n"
            f"Rows returned: {last_state.row_count}\n"
            f"Execution status: {last_state.execution_status}\n"
            f"Executed SQL:\n```sql\n{last_state.executed_sql or last_state.generated_sql}\n```"
        )

    @staticmethod
    def _fallback_answer_from_rows(
        question: str,
        rows: list[dict[str, Any]],
        row_count: int,
    ) -> str:
        if not rows:
            return "I ran the query, but I could not find any matching rows."

        first = rows[0]
        preview = ", ".join(f"{key}={value}" for key, value in list(first.items())[:4])

        if row_count == 1:
            return f"I found 1 matching row for your request. Example: {preview}."
        return f"I found {row_count} rows for your request. First row preview: {preview}."

    @staticmethod
    def _infer_domain_hint(table_name: str, active_context: dict[str, Any]) -> str | None:
        explicit = str(active_context.get("dataset_domain", "")).strip().lower()
        if explicit:
            return explicit

        table = str(table_name or "").lower()
        if "hotel" in table or "booking" in table or "reservation" in table:
            return "hotel"
        if "hospital" in table or "patient" in table or "admission" in table:
            return "hospital"
        if "order" in table or "retail" in table or "sales" in table:
            return "retail"
        if "employee" in table or "payroll" in table or "hr" in table:
            return "hr"
        return None