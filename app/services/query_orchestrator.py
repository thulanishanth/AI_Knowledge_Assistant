# app/services/query_orchestrator.py
from __future__ import annotations

import asyncio
import json
import os
import re
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
from app.services.intent_service import IntentService
from app.services.prompt_builder import PromptBuilder
from app.services.conversation_state_store import (
    ConversationStateStore,
    LastQueryState,
    DialogueState,
)
from app.services.rag_retriever import retrieve_dynamic_rag_context
from app.observability.query_observer import QueryObserver

from app.services.query_decomposer import QueryDecomposer
from app.services.result_merger import ResultMerger
from app.services.ontology_resolver import OntologyResolver
from app.services.analytical_decomposer import AnalyticalDecomposer, ReasoningStep
from app.services.terminology_resolver import TerminologyResolver

from app.security.row_level_security import RLSFilter
from app.security.auth_context import build_security_context

from app.services.grounded_synthesizer import GroundedSynthesizer
from app.memory.entity_tracker import EntityTracker

logger = get_logger(__name__)

# Columns injected by business rules — never pollute the entity tracker
_RULE_INJECTED_COLUMNS = {
    "booking_status",
    "tenant_id",
    "table_schema",
    "table_name",
}


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


def _extract_dialogue_fields(sql: str, question: str) -> dict:
    sql_lower = sql.lower()
    q_lower = question.lower()

    metric = None
    for candidate in ["revenue", "price", "count", "sum", "avg", "total", "sales"]:
        if candidate in q_lower:
            metric = candidate
            break

    date_range = None
    date_match = re.search(r"\b(20\d{2})\b", sql)
    if date_match:
        date_range = date_match.group(1)
    month_match = re.search(r"month\s*=\s*['\"]?(\w+)['\"]?", sql_lower)
    if month_match:
        date_range = (date_range or "") + f" {month_match.group(1)}"

    filters: dict[str, str] = {}
    for m in re.finditer(r"(\w+)\s*=\s*'([^']+)'", sql):
        col, val = m.group(1), m.group(2)
        col_lower = col.lower()
        if col_lower in _RULE_INJECTED_COLUMNS:
            continue
        if val.lower() in q_lower or col_lower in q_lower:
            filters[col] = val

    return {
        "metric": metric,
        "date_range": date_range.strip() if date_range else None,
        "filters": filters,
    }


class QueryOrchestrator:
    """Intelligent database query pipeline with full intent routing,
    terminology resolution, and analytical chain-of-thought decomposition."""

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
        prompt_builder: PromptBuilder,
        conversation_state_store: ConversationStateStore,
    ) -> None:
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._schema_service = schema_service
        self._intent_service = intent_service
        self._sql_generation_service = sql_generation_service
        self._sql_execution_service = sql_execution_service
        self._response_formatter = response_formatter
        self._prompt_builder = prompt_builder
        self._conversation_state_store = conversation_state_store
        self._query_cache: TTLCache[QueryExecutionResult] = TTLCache(
            settings.query_cache_ttl_seconds
        )
        self._query_decomposer = QueryDecomposer(prompt_builder)
        self._result_merger = ResultMerger()
        self._ontology_resolver = OntologyResolver()
        self._analytical_decomposer = AnalyticalDecomposer()
        self._terminology_resolver = TerminologyResolver()
        self._rls_filter = RLSFilter()
        self._grounded_synthesizer = GroundedSynthesizer()

    # ─────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────

    async def handle_query(
        self,
        user_question: str,
        user_id: str = "anonymous",
        session_id: str | None = None,
        tenant_id: str = "default",
    ) -> QueryResponse:
        sanitized = sanitize_question(user_question)
        if not sanitized.normalized:
            raise ValueError("Question cannot be empty.")

        session_ctx = self._session_manager.resolve(
            user_id=user_id, session_id=session_id
        )
        security_ctx = build_security_context(
            user_id=session_ctx.user_id,
            tenant_id=tenant_id,
            user_roles={"role": "admin"},
        )
        obs = QueryObserver(
            session_id=session_ctx.session_id,
            user_id=session_ctx.user_id,
            tenant_id=tenant_id,
            question=sanitized.normalized,
        )
        obs.start()

        with tracing.span("query.handle"), metrics.timer("query_total"):
            schema = await self._schema_service.get_schema(tenant_id)
            user_profile = await self._memory_manager._vector_memory.get_user_profile(
                session_ctx.user_id
            )
            profile_context = (
                f"USER PROFILE & PREFERENCES:\n{user_profile}\n\n" if user_profile else ""
            )

            session_context = ""
            debug_vector, debug_window, debug_summary = [], [], ""

            try:
                try:
                    rag_context_str = await retrieve_dynamic_rag_context(tenant_id)
                except Exception:
                    rag_context_str = ""

                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    user_query=sanitized.normalized,
                    tenant_id=tenant_id,
                    rag_context=rag_context_str,
                )
                session_context = str(memory_context.get("aggregated_context", ""))
                debug_vector = memory_context.get("vector_results", [])
                debug_window = memory_context.get("window_messages", [])
                debug_summary = memory_context.get("summary", "")
            except Exception:
                pass

            all_rules_raw = self._schema_service.get_all_rules_raw(tenant_id)
            selected_rules_text = await self._schema_service.select_examples(
                question=sanitized.normalized, tenant_id=tenant_id, limit=5
            )
            rules_injected = len(
                [r for r in selected_rules_text.split("\n\n") if r.strip()]
            )
            double_prefix_count = sum(
                1 for r in all_rules_raw if "CRITICAL: CRITICAL:" in r.upper()
            )
            contradiction_fired = any(
                s in sanitized.normalized.lower()
                for s in ["how many", "total count", "all bookings", "every booking"]
            )
            obs.record_rag(
                rules_loaded=len(all_rules_raw),
                rules_injected=rules_injected,
                contradiction_resolved=contradiction_fired,
                double_prefix_fixed=double_prefix_count,
                vector_results_count=len(debug_vector),
                cache_hit=False,
            )

            dialogue_state_obj = await self._conversation_state_store.get_dialogue_state(
                session_ctx.user_id, session_ctx.session_id
            )
            entity_tracker = EntityTracker()
            if (
                hasattr(dialogue_state_obj, "last_filters")
                and dialogue_state_obj.last_filters
            ):
                entity_tracker.load_from_state(dialogue_state_obj.last_filters)
            if (
                hasattr(dialogue_state_obj, "last_date_range")
                and dialogue_state_obj.last_date_range
            ):
                entity_tracker.load_from_state(
                    {"date_period": dialogue_state_obj.last_date_range}
                )
            entity_context = entity_tracker.to_context_block()

            formatted_history = "\n".join(
                f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
                for msg in debug_window[-4:]
            )
            planner_context = (
                f"{profile_context}{self._prompt_builder._prune_schema(schema)}\n\n"
                f"Conversation Context:\n{session_context}\n\n{entity_context}"
            )

            analysis = await asyncio.to_thread(
                self._intent_service.analyze,
                user_question=sanitized.normalized,
                memory_context=planner_context,
                dialogue_state=json.dumps(dialogue_state_obj.to_prompt_payload()),
                chat_history=formatted_history,
            )
            intent = analysis.get("route", "database_query")

            obs.record_intent(
                route=intent,
                confidence=analysis.get("confidence", 0.0),
                is_follow_up=analysis.get("is_follow_up", False),
                follow_up_strategy=analysis.get("follow_up_strategy", "none"),
                standalone_question=analysis.get(
                    "standalone_question", sanitized.normalized
                ),
                rewrite_called=True,
                plan_called=True,
                critic_called=analysis.get("confidence", 1.0) < 0.85,
                critic_verdict=(
                    "reviewed" if analysis.get("confidence", 1.0) < 0.85 else "n/a"
                ),
            )

            final_query = analysis.get("standalone_question") or sanitized.normalized

            # ── ROUTE DISPATCH ──────────────────────────────────────────

            if intent == "clarify":
                clarifying_q = (
                    analysis.get("clarifying_question") or "Could you provide more detail?"
                )
                obs.record_answer(clarifying_q, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=clarifying_q,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Clarification needed",
                        "message": clarifying_q,
                    },
                    meta={"route": "clarify", "intent_analysis": analysis},
                )

            if intent == "general_answer":
                answer = await asyncio.to_thread(
                    self._intent_service.answer_general_question,
                    user_question=final_query,
                    memory_context=session_context,
                )
                await self._update_memory(
                    session_ctx, final_query, answer, "", "", "general_answer",
                    full_prompt="General Knowledge Question"
                )
                obs.record_answer(answer, 0.9)
                obs.finish()
                return QueryResponse(
                    answer=answer,
                    confidence=0.9,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Answer", "message": answer},
                    meta={"route": "general_answer", "intent_analysis": analysis},
                )

            if intent == "schema_answer":
                schema_text = self._prompt_builder._prune_schema(schema)
                answer = f"Here is the current database schema:\n\n```\n{schema_text}\n```"
                obs.record_answer(answer, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Schema", "message": answer},
                    meta={"route": "schema_answer"},
                )

            if intent == "show_sql":
                last_state = await self._conversation_state_store.get_last_query_state(
                    session_ctx.user_id, session_ctx.session_id
                )
                if last_state and last_state.executed_sql:
                    answer = f"Here is the SQL from the previous result:\n\n```sql\n{last_state.executed_sql}\n```"
                else:
                    answer = "I don't have a previous SQL query to show."
                obs.record_answer(answer, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=answer,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Previous SQL",
                        "message": answer,
                    },
                    meta={"route": "show_sql"},
                )

            if intent == "explain_last_answer":
                last_state = await self._conversation_state_store.get_last_query_state(
                    session_ctx.user_id, session_ctx.session_id
                )
                if last_state:
                    context_for_explain = (
                        f"Previous question: {last_state.original_question}\n"
                        f"Previous answer: {last_state.answer}\n"
                        f"SQL used: {last_state.executed_sql}"
                    )
                    answer = await asyncio.to_thread(
                        self._intent_service.answer_general_question,
                        user_question=f"Please explain in more detail: {final_query}",
                        memory_context=context_for_explain,
                    )
                else:
                    answer = "I don't have a previous answer to explain."
                obs.record_answer(answer, 0.9)
                obs.finish()
                return QueryResponse(
                    answer=answer,
                    confidence=0.9,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Explanation",
                        "message": answer,
                    },
                    meta={"route": "explain_last_answer"},
                )

            # Default → database_query
            return await self._handle_database_query(
                sanitized_question=sanitized.normalized,
                final_query=final_query,
                session_ctx=session_ctx,
                security_ctx=security_ctx,
                schema=schema,
                selected_rules_text=selected_rules_text,
                analysis=analysis,
                entity_context=entity_context,
                entity_tracker=entity_tracker,
                profile_context=profile_context,
                user_profile=user_profile,
                debug_vector=debug_vector,
                debug_window=debug_window,
                debug_summary=debug_summary,
                tenant_id=tenant_id,
                obs=obs,
            )

    # ─────────────────────────────────────────────────────────────────────
    # DATABASE QUERY PIPELINE — top-level dispatcher
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_database_query(
        self,
        *,
        sanitized_question: str,
        final_query: str,
        session_ctx,
        security_ctx,
        schema,
        selected_rules_text: str,
        analysis: dict,
        entity_context: str,
        entity_tracker: EntityTracker,
        profile_context: str,
        user_profile: str,
        debug_vector: list,
        debug_window: list,
        debug_summary: str,
        tenant_id: str,
        obs: QueryObserver,
    ) -> QueryResponse:

        knowledge_chunks = [profile_context] if user_profile else []
        if selected_rules_text:
            knowledge_chunks.append("CRITICAL BUSINESS RULES:\n" + selected_rules_text)
        if analysis.get("is_follow_up") and entity_context:
            knowledge_chunks.append(entity_context)
        clean_business_rules = "\n\n".join(chunk for chunk in knowledge_chunks if chunk)

        target_model = os.getenv(
            "HF_MODEL", settings.hf_model or "Qwen/Qwen2.5-7B-Instruct"
        )
        is_cloud = False
        schema_block = self._prompt_builder._prune_schema(schema)

        # ══════════════════════════════════════════════════════════════
        # STAGE 1 — TERMINOLOGY RESOLUTION
        # ══════════════════════════════════════════════════════════════
        terminology_resolution = None
        terminology_debug: dict = {}

        if self._terminology_resolver.has_unknown_terms(final_query):
            logger.info(
                "Terminology resolution triggered for: %s", final_query
            )
            terminology_resolution = await self._terminology_resolver.resolve(
                question=final_query,
                schema_block=schema_block,
                business_rules=clean_business_rules,
                model=target_model,
            )

            terminology_debug = {
                "triggered": True,
                "original_question": final_query,
                "enriched_question": terminology_resolution.enriched_question,
                "resolved_terms": [
                    {
                        "term": t.raw_term,
                        "category": t.category,
                        "meaning": t.meaning,
                        "sql_condition": t.sql_condition,
                        "confidence": t.confidence,
                    }
                    for t in terminology_resolution.resolved_terms
                ],
            }

            if terminology_resolution.needs_clarification:
                obs.record_answer(terminology_resolution.clarification_question, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=terminology_resolution.clarification_question,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Clarification needed",
                        "message": terminology_resolution.clarification_question,
                    },
                    meta={
                        "route": "terminology_clarify",
                        "terminology": terminology_debug,
                    },
                )

            if terminology_resolution.had_unknown_terms:
                final_query = terminology_resolution.enriched_question

                if terminology_resolution.sql_context_block:
                    clean_business_rules = (
                        terminology_resolution.sql_context_block
                        + "\n\n"
                        + clean_business_rules
                    ).strip()

                logger.info(
                    "Terminology enriched question: %s", final_query
                )
        else:
            terminology_debug = {"triggered": False}

        # ══════════════════════════════════════════════════════════════
        # STAGE 2 — ANALYTICAL CHAIN-OF-THOUGHT
        # ══════════════════════════════════════════════════════════════
        if self._analytical_decomposer.is_candidate(final_query):
            logger.info("Analytical decomposition candidate: %s", final_query)
            analytical_response = await self._run_analytical_pipeline(
                question=final_query,
                sanitized_question=sanitized_question,
                schema=schema,
                schema_block=schema_block,
                business_rules=clean_business_rules,
                session_ctx=session_ctx,
                security_ctx=security_ctx,
                target_model=target_model,
                selected_rules_text=selected_rules_text,
                debug_vector=debug_vector,
                debug_window=debug_window,
                debug_summary=debug_summary,
                entity_tracker=entity_tracker,
                clean_business_rules=clean_business_rules,
                analysis=analysis,
                terminology_debug=terminology_debug,
                obs=obs,
            )
            if analytical_response is not None:
                return analytical_response
            logger.info("Analytical decomposer: single-SQL is sufficient.")

        # ══════════════════════════════════════════════════════════════
        # STAGE 3 — STANDARD SINGLE-SQL PIPELINE
        # ══════════════════════════════════════════════════════════════
        return await self._run_standard_pipeline(
            sanitized_question=sanitized_question,
            final_query=final_query,
            schema=schema,
            clean_business_rules=clean_business_rules,
            session_ctx=session_ctx,
            security_ctx=security_ctx,
            target_model=target_model,
            is_cloud=is_cloud,
            tenant_id=tenant_id,
            selected_rules_text=selected_rules_text,
            debug_vector=debug_vector,
            debug_window=debug_window,
            debug_summary=debug_summary,
            entity_tracker=entity_tracker,
            analysis=analysis,
            terminology_debug=terminology_debug,
            obs=obs,
        )

    # ─────────────────────────────────────────────────────────────────────
    # ANALYTICAL CHAIN-OF-THOUGHT PIPELINE
    # ─────────────────────────────────────────────────────────────────────

    async def _run_analytical_pipeline(
        self,
        *,
        question: str,
        sanitized_question: str,
        schema,
        schema_block: str,
        business_rules: str,
        session_ctx,
        security_ctx,
        target_model: str,
        selected_rules_text: str,
        debug_vector: list,
        debug_window: list,
        debug_summary: str,
        entity_tracker: EntityTracker,
        clean_business_rules: str,
        analysis: dict,
        terminology_debug: dict,
        obs: QueryObserver,
    ) -> QueryResponse | None:
        plan = await self._analytical_decomposer.plan(
            question=question,
            schema_block=schema_block,
            business_rules=business_rules,
            model=target_model,
        )

        if not plan.needs_decomposition or len(plan.steps) < 2:
            return None

        logger.info(
            "Analytical pipeline: %d steps. Reasoning: %s",
            len(plan.steps),
            plan.reasoning,
        )

        source_uri = (
            f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        )

        executed_sqls: list[str] = []
        total_rows = 0
        total_ms = 0.0

        for step in plan.steps:
            if not step.sql:
                step.error = "No SQL generated for this step."
                continue

            from app.security.sql_guard import SqlGuard
            guard = SqlGuard()
            allowed = (
                set(schema.schema_dict.keys())
                if hasattr(schema, "schema_dict")
                else set()
            )
            validation = guard.validate(
                step.sql, dialect=schema.dialect, allowed_tables=allowed
            )

            if not validation.is_valid:
                step.error = f"Validation failed: {'; '.join(validation.errors)}"
                logger.warning("Step %d SQL failed: %s", step.index, step.error)
                continue

            secured_sql = self._rls_filter.apply(
                validation.normalized_sql, security_ctx
            )
            logger.info(
                "Analytical Step %d: %s | SQL: %s",
                step.index, step.description, secured_sql,
            )

            try:
                result = self._sql_execution_service.execute(
                    sql_query=secured_sql,
                    source_uri=source_uri,
                    category="relational_db",
                )
                if result.error_message:
                    step.error = result.error_message
                    logger.warning("Step %d error: %s", step.index, step.error)
                    continue

                step.result = result.rows
                step.scalar = self._analytical_decomposer._extract_scalar(result.rows)
                step.executed = True
                executed_sqls.append(secured_sql)
                total_rows += result.row_count
                total_ms += result.execution_ms
                logger.info(
                    "Step %d: %s rows, scalar=%s",
                    step.index, result.row_count, step.scalar,
                )
            except Exception as e:
                step.error = str(e)
                logger.error("Step %d exception: %s", step.index, e)

        successful_steps = [s for s in plan.steps if s.executed]
        if not successful_steps:
            logger.warning("All analytical steps failed — falling back to standard.")
            return None

        final_answer, confidence = (
            await self._analytical_decomposer.synthesize_final_answer(
                question=question,
                steps=plan.steps,
                model=target_model,
            )
        )

        final_combined_sql = "\n\n-- STEP SEPARATOR --\n\n".join(executed_sqls)
        obs.record_sql(
            sql=final_combined_sql,
            strategy="analytical_cot",
            execution_ms=total_ms,
            rows_returned=total_rows,
        )

        last_data = successful_steps[-1].result or []
        _table_text, presentation = self._response_formatter.format_database_result(
            question=question, rows=last_data, truncated=False
        )
        exec_status = f"Analytical CoT: {len(plan.steps)} steps, {total_rows} total rows"

        try:
            dfields = _extract_dialogue_fields(final_combined_sql, question)
            last_state = LastQueryState(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                original_question=sanitized_question,
                corrected_question=question,
                generated_sql=final_combined_sql,
                executed_sql=final_combined_sql,
                execution_status=exec_status,
                answer=final_answer,
                rows=last_data,
                row_count=total_rows,
                truncated=False,
                dialogue_state=DialogueState(
                    last_sql=final_combined_sql,
                    last_standalone_question=question,
                    last_answer_summary=final_answer,
                    pending_clarification=None,
                    last_metric=dfields["metric"],
                    last_date_range=dfields["date_range"],
                    last_filters=dfields["filters"],
                    last_route="analytical_cot",
                ),
            )
            await self._conversation_state_store.save_last_query_state(
                session_ctx.user_id, session_ctx.session_id, last_state
            )
        except Exception as e:
            logger.error("Failed to save analytical state: %s", e)

        steps_dump = "\n".join(
            f"Step {s.index}: {s.description}\n  SQL: {s.sql}\n  Result: {s.result}"
            for s in plan.steps
        )
        actual_full_prompt = f"Analytical CoT Plan Reasoning:\n{plan.reasoning}\n\n{steps_dump}"

        await self._update_memory(
            session_ctx, question, final_answer,
            clean_business_rules, final_combined_sql, exec_status,
            full_prompt=actual_full_prompt
        )

        try:
            from app.observability.file_dumper import dump_conversation
            await dump_conversation(
                user_query=question,
                ai_response=final_answer,
                full_prompt=actual_full_prompt,
                rag_context=clean_business_rules,
                generated_sql=final_combined_sql,
                execution_status=exec_status,
                human_readable_prompt=actual_full_prompt,
                llm_model_name=target_model,
                max_context_window=8000,
                estimated_tokens=obs.audit.token_usage.total_tokens,
            )
        except Exception as e:
            logger.error("File dumper failed: %s", e)

        obs.record_answer(final_answer, confidence)
        obs.finish()

        return QueryResponse(
            answer=final_answer,
            confidence=confidence,
            session_id=session_ctx.session_id,
            presentation=presentation,
            meta={
                "strategy": "analytical_cot",
                "reasoning": plan.reasoning,
                "steps": [
                    {
                        "index": s.index,
                        "description": s.description,
                        "sql": s.sql,
                        "result": s.result,
                        "scalar": s.scalar,
                        "error": s.error,
                        "executed": s.executed,
                    }
                    for s in plan.steps
                ],
                "terminology": terminology_debug,
                "cached": False,
                "row_count": total_rows,
                "execution_ms": round(total_ms, 2),
                "sql": final_combined_sql,
                "security_role": security_ctx.role,
                "memory_architecture": {
                    "1_rag_schema": selected_rules_text,
                    "2_recent_window": debug_window,
                    "3_rolling_summary": debug_summary,
                    "4_chromadb_vector_matches": debug_vector,
                    "5_tracked_entities": entity_tracker.entities
                    if hasattr(entity_tracker, "entities")
                    else [],
                    "6_final_aggregated_context": clean_business_rules,
                },
                "intent_analysis": analysis,
            },
        )

    # ─────────────────────────────────────────────────────────────────────
    # STANDARD SINGLE-SQL PIPELINE
    # ─────────────────────────────────────────────────────────────────────

    async def _run_standard_pipeline(
        self,
        *,
        sanitized_question: str,
        final_query: str,
        schema,
        clean_business_rules: str,
        session_ctx,
        security_ctx,
        target_model: str,
        is_cloud: bool,
        tenant_id: str,
        selected_rules_text: str,
        debug_vector: list,
        debug_window: list,
        debug_summary: str,
        entity_tracker: EntityTracker,
        analysis: dict,
        terminology_debug: dict,
        obs: QueryObserver,
    ) -> QueryResponse:

        schema_cols: set[str] = set()
        if hasattr(schema, "schema_dict") and isinstance(schema.schema_dict, dict):
            for col_strings in schema.schema_dict.values():
                for col_str in col_strings:
                    match = re.match(r"-\s*([a-zA-Z0-9_]+)", col_str)
                    if match:
                        schema_cols.add(match.group(1))

        decomp_plan = await asyncio.to_thread(
            self._query_decomposer.decompose,
            question=final_query,
            schema_block=self._prompt_builder._prune_schema(schema),
            dialogue_state=json.dumps({}),
        )

        all_sub_results: list[dict] = []
        combined_sql_executed: list[str] = []
        all_sql_prompts: list[str] = []
        total_execution_ms = 0.0
        total_rows_returned = 0
        sql_result = None
        cached = False

        for sq in decomp_plan.sub_queries:
            logger.info("Standard Sub-Query %s: %s", sq.index, sq.question)

            resolved_terms = await asyncio.to_thread(
                self._ontology_resolver.resolve,
                question=sq.question,
                tenant_id=tenant_id,
                schema_columns=schema_cols,
            )
            ontology_context_str = self._ontology_resolver.build_ontology_prompt_block(
                resolved_terms
            )

            max_attempts = 3
            attempt = 1
            db_error_message = None
            execution = None
            previous_sql = ""
            secured_sql = ""

            while attempt <= max_attempts:
                current_context = clean_business_rules
                if attempt > 1 and db_error_message and previous_sql:
                    logger.warning("🔄 [SELF-HEALING] Attempt %s/%s", attempt, max_attempts)
                    current_context += (
                        f"\n\nCRITICAL ERROR REFLECTION:\n"
                        f"Your previous SQL:\n```sql\n{previous_sql}\n```\n"
                        f"Failed with: {db_error_message}\n\n"
                        f"Write a corrected SQL that fixes this exact error."
                    )

                # ALWAYS build and record the prompt for logging/observability BEFORE checking cache
                debug_sql_prompt = self._prompt_builder.build_sql_prompt(
                    question=sq.question,
                    schema=schema,
                    ontology_context=ontology_context_str,
                    session_context=current_context,
                )
                all_sql_prompts.append(debug_sql_prompt)

                has_active_entities = bool(hasattr(entity_tracker, "entities") and entity_tracker.entities)

                if attempt == 1 and not has_active_entities:
                    cached_sql = (
                        await self._memory_manager._vector_memory.get_semantic_sql(
                            query=sq.question,
                            schema_fingerprint=schema.fingerprint,
                        )
                    )
                    if cached_sql:
                        from app.services.sql_generation_service import SqlGenerationResult
                        from app.security.sql_guard import SqlValidationResult
                        sql_result = SqlGenerationResult(
                            sql=cached_sql,
                            strategy="semantic_cache_hit",
                            validation=SqlValidationResult(
                                is_valid=True,
                                normalized_sql=cached_sql,
                                errors=[],
                            ),
                        )
                        cached = True

                if not cached:
                    sql_result = await self._sql_generation_service.generate_sql(
                        question=sq.question,
                        schema=schema,
                        ontology_context=ontology_context_str,
                        session_context=current_context,
                        model=target_model,
                        is_cloud=is_cloud,
                    )
                    obs.record_tokens_from_prompt(
                        prompt_text=debug_sql_prompt,
                        completion_text=sql_result.sql,
                        model=target_model,
                    )

                if not sql_result or not sql_result.is_valid:
                    errors = (
                        sql_result.validation.errors
                        if sql_result
                        else ["Generation failed"]
                    )
                    safe_answer = (
                        f"I was unable to generate a valid query. "
                        f"Reason: {'; '.join(errors)}"
                    )
                    obs.record_sql(
                        sql=sql_result.sql if sql_result else "",
                        strategy="security_blocked",
                    )
                    obs.finish()
                    return QueryResponse(
                        answer=safe_answer,
                        confidence=1.0,
                        session_id=session_ctx.session_id,
                        presentation={
                            "kind": "notice",
                            "title": "Blocked",
                            "message": safe_answer,
                        },
                    )

                secured_sql = self._rls_filter.apply(sql_result.sql, security_ctx)
                cache_key = f"{schema.fingerprint}|{security_ctx.role}|{secured_sql}"
                execution = self._query_cache.get(cache_key)

                if execution is None:
                    try:
                        source_uri = (
                            f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
                            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
                        )
                        execution = self._sql_execution_service.execute(
                            sql_query=secured_sql,
                            source_uri=source_uri,
                            category="relational_db",
                        )
                        if hasattr(execution, "error_message") and execution.error_message:
                            raise ValueError(execution.error_message)
                        if execution.rows:
                            execution.rows = self._rls_filter.mask_pii(
                                execution.rows, security_ctx
                            )
                        self._query_cache.set(cache_key, execution)
                        if not cached:
                            asyncio.create_task(
                                self._memory_manager._vector_memory.save_semantic_sql(
                                    query=sq.question,
                                    sql=sql_result.sql,
                                    schema_fingerprint=schema.fingerprint,
                                )
                            )
                        break
                    except Exception as e:
                        db_error_message = str(e)
                        previous_sql = sql_result.sql
                        cached = False
                        attempt += 1
                        continue
                else:
                    break

            if execution is None or (
                hasattr(execution, "error_message") and execution.error_message
            ):
                all_sub_results.append(
                    {
                        "question": sq.question,
                        "sql": sql_result.sql if sql_result else "Failed",
                        "data": [],
                        "rows": 0,
                        "synthesis": (
                            f"Failed after {max_attempts} attempts. "
                            f"Last error: {db_error_message}"
                        ),
                    }
                )
            else:
                combined_sql_executed.append(execution.executed_sql)
                total_execution_ms += execution.execution_ms
                total_rows_returned += execution.row_count

                sq_synthesis = "Data retrieved successfully."
                if execution.rows:
                    try:
                        sq_synthesis, synth_conf = await self._grounded_synthesizer.synthesize(
                            question=sq.question,
                            rows=execution.rows,
                            row_count=execution.row_count,
                            executed_sql=execution.executed_sql,
                            metric_context=ontology_context_str,
                        )
                        logger.info("Sub-Query Grounding: %.2f", synth_conf)
                    except Exception as e:
                        logger.error("Grounded synthesis failed: %s", e)

                all_sub_results.append(
                    {
                        "question": sq.question,
                        "sql": execution.executed_sql,
                        "data": execution.rows,
                        "rows": execution.row_count,
                        "synthesis": sq_synthesis,
                    }
                )

        if decomp_plan.merge_strategy == "single" and all_sub_results:
            explanation = all_sub_results[0].get("synthesis", "Data retrieved.")
        else:
            explanation = await asyncio.to_thread(
                self._result_merger.merge,
                original_question=final_query,
                sub_results=all_sub_results,
                merge_strategy=decomp_plan.merge_strategy,
            )

        final_combined_sql = "\n\n".join(combined_sql_executed)
        obs.record_sql(
            sql=final_combined_sql,
            strategy="compound_execution" if decomp_plan.is_compound else "standard",
            execution_ms=total_execution_ms,
            rows_returned=total_rows_returned,
        )

        presentation_rows = all_sub_results[0].get("data", []) if all_sub_results else []
        _table_text, presentation = self._response_formatter.format_database_result(
            question=final_query, rows=presentation_rows, truncated=False
        )
        exec_status = (
            f"Success ({total_rows_returned} rows across {len(all_sub_results)} queries)"
        )
        
        actual_full_prompt = "\n\n=== NEXT QUERY PROMPT ===\n\n".join(all_sql_prompts) if all_sql_prompts else (
            f"Decomposition: {decomp_plan.merge_strategy}\n"
            + "\n".join(sq.question for sq in decomp_plan.sub_queries)
        )

        try:
            dfields = _extract_dialogue_fields(final_combined_sql, final_query)
            last_state = LastQueryState(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                original_question=sanitized_question,
                corrected_question=final_query,
                generated_sql=final_combined_sql,
                executed_sql=final_combined_sql,
                execution_status=exec_status,
                answer=explanation,
                rows=presentation_rows,
                row_count=total_rows_returned,
                truncated=False,
                dialogue_state=DialogueState(
                    last_sql=final_combined_sql,
                    last_standalone_question=final_query,
                    last_answer_summary=explanation,
                    pending_clarification=None,
                    last_metric=dfields["metric"],
                    last_date_range=dfields["date_range"],
                    last_filters=dfields["filters"],
                    last_route="database_query",
                ),
            )
            await self._conversation_state_store.save_last_query_state(
                session_ctx.user_id, session_ctx.session_id, last_state
            )
        except Exception as e:
            logger.error("Failed to save state: %s", e)

        await self._update_memory(
            session_ctx, final_query, explanation,
            clean_business_rules,
            sql_result.raw_llm_output
            if sql_result and hasattr(sql_result, "raw_llm_output")
            else final_combined_sql,
            exec_status,
            full_prompt=actual_full_prompt
        )

        try:
            from app.observability.file_dumper import dump_conversation
            await dump_conversation(
                user_query=final_query,
                ai_response=explanation,
                full_prompt=actual_full_prompt,
                rag_context=clean_business_rules,
                generated_sql=final_combined_sql,
                execution_status=exec_status,
                human_readable_prompt=actual_full_prompt,
                llm_model_name=target_model,
                max_context_window=8000,
                estimated_tokens=obs.audit.token_usage.total_tokens,
            )
        except Exception as e:
            logger.error("File dumper failed: %s", e)

        obs.record_answer(explanation, 0.95 if total_rows_returned > 0 else 0.85)
        obs.finish()

        return QueryResponse(
            answer=explanation,
            confidence=0.95 if total_rows_returned > 0 else 0.85,
            session_id=session_ctx.session_id,
            presentation=presentation,
            meta={
                "strategy": "compound_execution"
                if decomp_plan.is_compound
                else "standard",
                "terminology": terminology_debug,
                "cached": cached,
                "row_count": total_rows_returned,
                "execution_ms": round(total_execution_ms, 2),
                "sql": final_combined_sql,
                "security_role": security_ctx.role,
                "memory_architecture": {
                    "1_rag_schema": selected_rules_text,
                    "2_recent_window": debug_window,
                    "3_rolling_summary": debug_summary,
                    "4_chromadb_vector_matches": debug_vector,
                    "5_tracked_entities": entity_tracker.entities
                    if hasattr(entity_tracker, "entities")
                    else [],
                    "6_final_aggregated_context": clean_business_rules,
                },
                "intent_analysis": analysis,
                "llm_prompts": {
                    "sql_generation": actual_full_prompt
                    if not decomp_plan.is_compound
                    else "Compound queries executed separately.",
                },
            },
        )

    # ─────────────────────────────────────────────────────────────────────
    # SHARED HELPERS
    # ─────────────────────────────────────────────────────────────────────

    async def _update_memory(
        self,
        session_ctx,
        question: str,
        answer: str,
        rag_context: str,
        generated_sql: str,
        execution_status: str,
        full_prompt: str = "",
    ) -> None:
        try:
            await self._memory_manager.update_memory_pipeline(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                question=question,
                answer=answer,
                full_prompt=full_prompt,
                rag_context=rag_context,
                generated_sql=generated_sql,
                execution_status=execution_status,
            )
        except Exception as e:
            logger.error("Memory pipeline failed: %s", e)