# app/services/query_orchestrator.py
"""
Query orchestrator — schema-first, LLM-driven pipeline.

All domain understanding comes from the schema (with sample values + statistics).
No hardcoded domain terms, no regex routing, no hardcoded column names.

Pipeline:
  1. Schema fetch (with rich data profiling)
  2. Memory + RAG context assembly
  3. Intent analysis (LLM)
  4. Term resolution (LLM + schema)
  5. Analytical decomposition if needed (LLM decides)
  6. Standard SQL generation → validate → execute → synthesize
"""
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
from app.services.analytical_decomposer import AnalyticalDecomposer
from app.services.terminology_resolver import TerminologyResolver
from app.security.row_level_security import RLSFilter
from app.security.auth_context import build_security_context
from app.services.grounded_synthesizer import GroundedSynthesizer
from app.memory.entity_tracker import EntityTracker

logger = get_logger(__name__)

# Columns that come from rule injection, not user queries — exclude from entity tracking
_RULE_INJECTED_COLUMNS = {"booking_status", "tenant_id", "table_schema", "table_name"}


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
    """Extract metric / date / filter hints for dialogue state (SQL parsing only)."""
    sql_lower = sql.lower()
    q_lower = question.lower()

    metric = None
    for candidate in ["revenue", "price", "count", "sum", "avg", "total", "sales", "amount"]:
        if candidate in q_lower:
            metric = candidate
            break

    date_range = None
    date_match = re.search(r"\b(20\d{2})\b", sql)
    if date_match:
        date_range = date_match.group(1)

    filters: dict[str, str] = {}
    for m in re.finditer(r"(\w+)\s*=\s*'([^']+)'", sql):
        col, val = m.group(1), m.group(2)
        if col.lower() in _RULE_INJECTED_COLUMNS:
            continue
        if val.lower() in q_lower or col.lower() in q_lower:
            filters[col] = val

    return {
        "metric": metric,
        "date_range": date_range,
        "filters": filters,
    }


class QueryOrchestrator:
    """Schema-first query pipeline. Domain knowledge comes from the database itself."""

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
            # ── Step 1: Rich schema fetch ────────────────────────────────
            schema = await self._schema_service.get_schema(tenant_id)
            schema_block = schema.to_prompt_block()

            # ── Step 2: Memory + backup rules ────────────────────────────
            user_profile = await self._memory_manager._vector_memory.get_user_profile(
                session_ctx.user_id
            )
            profile_context = (
                f"USER PROFILE:\n{user_profile}\n\n" if user_profile else ""
            )

            session_context = ""
            debug_vector, debug_window, debug_summary = [], [], ""

            try:
                rag_context_str = await retrieve_dynamic_rag_context(tenant_id)
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

            # Backup business rules (optional enrichment)
            all_rules_raw = self._schema_service.get_all_rules_raw(tenant_id)
            selected_rules_text = await self._schema_service.select_examples(
                question=sanitized.normalized, tenant_id=tenant_id, limit=5
            )
            rules_injected = len([r for r in selected_rules_text.split("\n\n") if r.strip()])

            obs.record_rag(
                rules_loaded=len(all_rules_raw),
                rules_injected=rules_injected,
                contradiction_resolved=False,
                double_prefix_fixed=0,
                vector_results_count=len(debug_vector),
                cache_hit=False,
            )

            # ── Step 3: Dialogue state + entity tracking ─────────────────
            dialogue_state_obj = await self._conversation_state_store.get_dialogue_state(
                session_ctx.user_id, session_ctx.session_id
            )
            entity_tracker = EntityTracker()
            if getattr(dialogue_state_obj, "last_filters", None):
                entity_tracker.load_from_state(dialogue_state_obj.last_filters)
            if getattr(dialogue_state_obj, "last_date_range", None):
                entity_tracker.load_from_state({"date_period": dialogue_state_obj.last_date_range})
            entity_context = entity_tracker.to_context_block()

            formatted_history = "\n".join(
                f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
                for msg in debug_window[-4:]
            )

            # Build planner context with schema summary so routing is schema-aware
            schema_summary = "\n".join(
                f"  Table: {t} ({len(cols)} columns)"
                for t, cols in schema.columns.items()
            )
            planner_context = (
                f"{profile_context}Conversation Context:\n{session_context}\n\n{entity_context}"
            )

            # ── Step 4: Intent analysis ───────────────────────────────────
            analysis = await asyncio.to_thread(
                self._intent_service.analyze,
                user_question=sanitized.normalized,
                memory_context=planner_context,
                dialogue_state=json.dumps(dialogue_state_obj.to_prompt_payload()),
                chat_history=formatted_history,
                schema_summary=schema_summary,
            )
            intent = analysis.get("route", "database_query")
            final_query = analysis.get("standalone_question") or sanitized.normalized

            obs.record_intent(
                route=intent,
                confidence=analysis.get("confidence", 0.0),
                is_follow_up=analysis.get("is_follow_up", False),
                follow_up_strategy=analysis.get("follow_up_strategy", "none"),
                standalone_question=final_query,
                rewrite_called=True,
                plan_called=True,
                critic_called=analysis.get("confidence", 1.0) < 0.85,
                critic_verdict="reviewed" if analysis.get("confidence", 1.0) < 0.85 else "n/a",
            )

            # ── Step 5: Route dispatch ────────────────────────────────────
            if intent == "clarify":
                clarifying_q = analysis.get("clarifying_question") or "Could you provide more detail?"
                obs.record_answer(clarifying_q, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=clarifying_q, confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Clarification needed", "message": clarifying_q},
                    meta={"route": "clarify"},
                )

            if intent == "general_answer":
                answer = await asyncio.to_thread(
                    self._intent_service.answer_general_question,
                    user_question=final_query,
                    memory_context=session_context,
                )
                await self._update_memory(session_ctx, final_query, answer, "", "", "general_answer")
                obs.record_answer(answer, 0.9)
                obs.finish()
                return QueryResponse(
                    answer=answer, confidence=0.9,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Answer", "message": answer},
                    meta={"route": "general_answer"},
                )

            if intent == "schema_answer":
                answer = f"Here is the current database schema:\n\n```\n{schema_block}\n```"
                obs.record_answer(answer, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=answer, confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Schema", "message": answer},
                    meta={"route": "schema_answer"},
                )

            if intent == "show_sql":
                last_state = await self._conversation_state_store.get_last_query_state(
                    session_ctx.user_id, session_ctx.session_id
                )
                answer = (
                    f"Here is the SQL from the previous result:\n\n```sql\n{last_state.executed_sql}\n```"
                    if last_state and last_state.executed_sql
                    else "I don't have a previous SQL query to show."
                )
                obs.record_answer(answer, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=answer, confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Previous SQL", "message": answer},
                    meta={"route": "show_sql"},
                )

            if intent == "explain_last_answer":
                last_state = await self._conversation_state_store.get_last_query_state(
                    session_ctx.user_id, session_ctx.session_id
                )
                if last_state:
                    ctx_str = (
                        f"Previous question: {last_state.original_question}\n"
                        f"Previous answer: {last_state.answer}\nSQL used: {last_state.executed_sql}"
                    )
                    answer = await asyncio.to_thread(
                        self._intent_service.answer_general_question,
                        user_question=f"Explain: {final_query}",
                        memory_context=ctx_str,
                    )
                else:
                    answer = "I don't have a previous answer to explain."
                obs.record_answer(answer, 0.9)
                obs.finish()
                return QueryResponse(
                    answer=answer, confidence=0.9,
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Explanation", "message": answer},
                    meta={"route": "explain_last_answer"},
                )

            # Default → database_query
            return await self._handle_database_query(
                sanitized_question=sanitized.normalized,
                final_query=final_query,
                session_ctx=session_ctx,
                security_ctx=security_ctx,
                schema=schema,
                schema_block=schema_block,
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
    # DATABASE QUERY PIPELINE
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_database_query(
        self,
        *,
        sanitized_question: str,
        final_query: str,
        session_ctx,
        security_ctx,
        schema,
        schema_block: str,
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

        # Build business context (rules are backup only)
        knowledge_chunks: list[str] = []
        if user_profile:
            knowledge_chunks.append(f"USER PROFILE:\n{user_profile}")
        if selected_rules_text:
            knowledge_chunks.append("BUSINESS RULES (backup):\n" + selected_rules_text)
        if analysis.get("is_follow_up") and entity_context:
            knowledge_chunks.append(entity_context)
        clean_business_rules = "\n\n".join(c for c in knowledge_chunks if c)

        target_model = os.getenv("HF_MODEL", settings.hf_model or "Qwen/Qwen2.5-7B-Instruct")

        # ── STAGE 1: Term resolution (LLM + schema) ──────────────────────
        # Ask LLM if any terms in the question need schema-value mapping
        terminology_debug: dict = {"triggered": False}
        term_resolution = await self._terminology_resolver.resolve(
            question=final_query,
            schema_block=schema_block,
            business_rules=clean_business_rules,
            model=target_model,
        )

        if term_resolution.had_unknown_terms:
            terminology_debug = {
                "triggered": True,
                "original": final_query,
                "enriched": term_resolution.enriched_question,
                "terms": [
                    {
                        "term": t.raw_term,
                        "meaning": t.meaning,
                        "sql_condition": t.sql_condition,
                        "confidence": t.confidence,
                    }
                    for t in term_resolution.resolved_terms
                ],
            }

            if term_resolution.needs_clarification:
                obs.record_answer(term_resolution.clarification_question, 1.0)
                obs.finish()
                return QueryResponse(
                    answer=term_resolution.clarification_question,
                    confidence=1.0,
                    session_id=session_ctx.session_id,
                    presentation={
                        "kind": "notice",
                        "title": "Clarification needed",
                        "message": term_resolution.clarification_question,
                    },
                    meta={"route": "terminology_clarify", "terminology": terminology_debug},
                )

            if term_resolution.enriched_question:
                final_query = term_resolution.enriched_question

            if term_resolution.sql_context_block:
                clean_business_rules = (
                    term_resolution.sql_context_block + "\n\n" + clean_business_rules
                ).strip()

        # ── STAGE 2: Analytical decomposition (LLM decides) ──────────────
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
            analysis=analysis,
            terminology_debug=terminology_debug,
            obs=obs,
        )
        if analytical_response is not None:
            return analytical_response

        # ── STAGE 3: Standard single/compound SQL pipeline ────────────────
        return await self._run_standard_pipeline(
            sanitized_question=sanitized_question,
            final_query=final_query,
            schema=schema,
            schema_block=schema_block,
            clean_business_rules=clean_business_rules,
            session_ctx=session_ctx,
            security_ctx=security_ctx,
            target_model=target_model,
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
        analysis: dict,
        terminology_debug: dict,
        obs: QueryObserver,
    ) -> QueryResponse | None:
        """Run multi-step analytical pipeline. Returns None if single SQL is sufficient."""
        plan = await self._analytical_decomposer.plan(
            question=question,
            schema_block=schema_block,
            business_rules=business_rules,
            model=target_model,
        )

        if not plan.needs_decomposition or len(plan.steps) < 2:
            return None

        logger.info("Analytical pipeline: %d steps. %s", len(plan.steps), plan.reasoning)

        source_uri = self._build_source_uri()
        executed_sqls: list[str] = []
        total_rows, total_ms = 0, 0.0

        for step in plan.steps:
            if not step.sql:
                step.error = "No SQL generated."
                continue

            from app.security.sql_guard import SqlGuard
            guard = SqlGuard()
            allowed = set(schema.schema_dict.keys()) if hasattr(schema, "schema_dict") else set()
            validation = guard.validate(step.sql, dialect=schema.dialect, allowed_tables=allowed)

            if not validation.is_valid:
                step.error = f"Validation: {'; '.join(validation.errors)}"
                logger.warning("Step %d SQL invalid: %s", step.index, step.error)
                continue

            secured_sql = self._rls_filter.apply(validation.normalized_sql, security_ctx)

            try:
                result = self._sql_execution_service.execute(
                    sql_query=secured_sql, source_uri=source_uri, category="relational_db"
                )
                if result.error_message:
                    step.error = result.error_message
                    continue
                step.result = result.rows
                step.scalar = self._analytical_decomposer._extract_scalar(result.rows)
                step.executed = True
                executed_sqls.append(secured_sql)
                total_rows += result.row_count
                total_ms += result.execution_ms
            except Exception as exc:
                step.error = str(exc)

        successful_steps = [s for s in plan.steps if s.executed]
        if not successful_steps:
            logger.warning("All analytical steps failed — falling back to standard pipeline.")
            return None

        final_answer, confidence = await self._analytical_decomposer.synthesize_final_answer(
            question=question, steps=plan.steps, model=target_model
        )

        combined_sql = "\n\n-- STEP --\n\n".join(executed_sqls)
        obs.record_sql(sql=combined_sql, strategy="analytical_cot",
                       execution_ms=total_ms, rows_returned=total_rows)

        last_data = successful_steps[-1].result or []
        _, presentation = self._response_formatter.format_database_result(
            question=question, rows=last_data, truncated=False
        )
        exec_status = f"Analytical CoT: {len(plan.steps)} steps, {total_rows} total rows"

        await self._save_state(
            session_ctx=session_ctx,
            sanitized_question=sanitized_question,
            final_query=question,
            combined_sql=combined_sql,
            exec_status=exec_status,
            answer=final_answer,
            rows=last_data,
            row_count=total_rows,
            route="analytical_cot",
        )
        await self._update_memory(
            session_ctx, question, final_answer, business_rules, combined_sql, exec_status
        )
        await self._dump_conversation(
            question, final_answer,
            f"Analytical CoT:\n{plan.reasoning}",
            business_rules, combined_sql, exec_status, target_model,
            obs.audit.token_usage.total_tokens,
        )
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
                    {"index": s.index, "description": s.description,
                     "sql": s.sql, "result": s.result, "scalar": s.scalar,
                     "error": s.error, "executed": s.executed}
                    for s in plan.steps
                ],
                "terminology": terminology_debug,
                "row_count": total_rows,
                "execution_ms": round(total_ms, 2),
                "sql": combined_sql,
                "memory_architecture": self._memory_debug(
                    selected_rules_text, debug_window, debug_summary,
                    debug_vector, entity_tracker, business_rules
                ),
                "intent_analysis": analysis,
            },
        )

    # ─────────────────────────────────────────────────────────────────────
    # STANDARD PIPELINE
    # ─────────────────────────────────────────────────────────────────────

    async def _run_standard_pipeline(
        self,
        *,
        sanitized_question: str,
        final_query: str,
        schema,
        schema_block: str,
        clean_business_rules: str,
        session_ctx,
        security_ctx,
        target_model: str,
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

        # Ontology resolution (dynamic — reads from DB)
        resolved_terms = await asyncio.to_thread(
            self._ontology_resolver.resolve,
            question=final_query,
            tenant_id=tenant_id,
        )
        ontology_context_str = self._ontology_resolver.build_ontology_prompt_block(resolved_terms)

        # Decompose (LLM decides — no regex)
        decomp_plan = await asyncio.to_thread(
            self._query_decomposer.decompose,
            question=final_query,
            schema_block=schema_block,
            dialogue_state=json.dumps({}),
        )

        all_sub_results: list[dict] = []
        combined_sql_executed: list[str] = []
        total_ms, total_rows = 0.0, 0
        debug_sql_prompt = None
        sql_result = None
        cached = False

        for sq in decomp_plan.sub_queries:
            logger.info("Sub-query %d: %s", sq.index, sq.question)

            max_attempts = 3
            attempt = 1
            db_error = None
            execution = None
            previous_sql = ""
            secured_sql = ""

            while attempt <= max_attempts:
                current_context = clean_business_rules
                if attempt > 1 and db_error and previous_sql:
                    current_context += (
                        f"\n\nCRITICAL: Your previous SQL failed:\n```sql\n{previous_sql}\n```\n"
                        f"Error: {db_error}\nFix this exact error."
                    )

                # Semantic SQL cache (only on first attempt with no active entity filters)
                has_entities = bool(
                    getattr(entity_tracker, "entities", None) and entity_tracker.entities
                )
                if attempt == 1 and not has_entities:
                    cached_sql = await self._memory_manager._vector_memory.get_semantic_sql(
                        query=sq.question, schema_fingerprint=schema.fingerprint
                    )
                    if cached_sql:
                        from app.services.sql_generation_service import SqlGenerationResult
                        from app.security.sql_guard import SqlValidationResult
                        sql_result = SqlGenerationResult(
                            sql=cached_sql,
                            strategy="semantic_cache_hit",
                            validation=SqlValidationResult(is_valid=True, normalized_sql=cached_sql),
                        )
                        cached = True

                if not cached:
                    debug_sql_prompt = self._prompt_builder.build_sql_prompt(
                        question=sq.question,
                        schema=schema,
                        session_context=current_context,
                        examples_context=ontology_context_str,
                    )
                    sql_result = await self._sql_generation_service.generate_sql(
                        question=sq.question,
                        schema=schema,
                        ontology_context=ontology_context_str,
                        session_context=current_context,
                        model=target_model,
                        is_cloud=False,
                    )
                    obs.record_tokens_from_prompt(
                        prompt_text=debug_sql_prompt,
                        completion_text=sql_result.sql,
                        model=target_model,
                    )

                if not sql_result or not sql_result.is_valid:
                    errors = sql_result.validation.errors if sql_result else ["Generation failed"]
                    safe_answer = f"Unable to generate a valid query. Reason: {'; '.join(errors)}"
                    obs.record_sql(sql=sql_result.sql if sql_result else "", strategy="security_blocked")
                    obs.finish()
                    return QueryResponse(
                        answer=safe_answer, confidence=1.0,
                        session_id=session_ctx.session_id,
                        presentation={"kind": "notice", "title": "Blocked", "message": safe_answer},
                    )

                secured_sql = self._rls_filter.apply(sql_result.sql, security_ctx)
                cache_key = f"{schema.fingerprint}|{security_ctx.role}|{secured_sql}"
                execution = self._query_cache.get(cache_key)

                if execution is None:
                    try:
                        execution = self._sql_execution_service.execute(
                            sql_query=secured_sql,
                            source_uri=self._build_source_uri(),
                            category="relational_db",
                        )
                        if getattr(execution, "error_message", None):
                            raise ValueError(execution.error_message)
                        if execution.rows:
                            execution.rows = self._rls_filter.mask_pii(execution.rows, security_ctx)
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
                    except Exception as exc:
                        db_error = str(exc)
                        previous_sql = sql_result.sql
                        cached = False
                        attempt += 1
                        continue
                else:
                    break

            if execution is None or getattr(execution, "error_message", None):
                all_sub_results.append({
                    "question": sq.question,
                    "sql": sql_result.sql if sql_result else "Failed",
                    "data": [],
                    "rows": 0,
                    "synthesis": f"Failed after {max_attempts} attempts. Error: {db_error}",
                })
            else:
                combined_sql_executed.append(execution.executed_sql)
                total_ms += execution.execution_ms
                total_rows += execution.row_count

                synthesis = "Data retrieved."
                if execution.rows:
                    try:
                        synthesis, _ = await asyncio.to_thread(
                            self._grounded_synthesizer.synthesize,
                            question=sq.question,
                            rows=execution.rows,
                            row_count=execution.row_count,
                            executed_sql=execution.executed_sql,
                            metric_context=ontology_context_str,
                        )
                    except Exception as exc:
                        logger.error("Grounded synthesis failed: %s", exc)

                all_sub_results.append({
                    "question": sq.question,
                    "sql": execution.executed_sql,
                    "data": execution.rows,
                    "rows": execution.row_count,
                    "synthesis": synthesis,
                })

        # Merge results
        if decomp_plan.merge_strategy == "single" and all_sub_results:
            explanation = all_sub_results[0].get("synthesis", "Data retrieved.")
        else:
            explanation = await asyncio.to_thread(
                self._result_merger.merge,
                original_question=final_query,
                sub_results=all_sub_results,
                merge_strategy=decomp_plan.merge_strategy,
            )

        combined_sql = "\n\n".join(combined_sql_executed)
        obs.record_sql(
            sql=combined_sql,
            strategy="compound" if decomp_plan.is_compound else "standard",
            execution_ms=total_ms,
            rows_returned=total_rows,
        )

        presentation_rows = all_sub_results[0].get("data", []) if all_sub_results else []
        _, presentation = self._response_formatter.format_database_result(
            question=final_query, rows=presentation_rows, truncated=False
        )
        exec_status = f"Success ({total_rows} rows)"

        await self._save_state(
            session_ctx=session_ctx,
            sanitized_question=sanitized_question,
            final_query=final_query,
            combined_sql=combined_sql,
            exec_status=exec_status,
            answer=explanation,
            rows=presentation_rows,
            row_count=total_rows,
            route="database_query",
        )
        await self._update_memory(
            session_ctx, final_query, explanation,
            clean_business_rules,
            sql_result.raw_llm_output if sql_result and hasattr(sql_result, "raw_llm_output") else combined_sql,
            exec_status,
        )
        await self._dump_conversation(
            final_query, explanation,
            f"Strategy: {decomp_plan.merge_strategy}",
            clean_business_rules, combined_sql, exec_status,
            settings.hf_model, obs.audit.token_usage.total_tokens,
        )

        confidence = 0.95 if total_rows > 0 else 0.85
        obs.record_answer(explanation, confidence)
        obs.finish()

        return QueryResponse(
            answer=explanation,
            confidence=confidence,
            session_id=session_ctx.session_id,
            presentation=presentation,
            meta={
                "strategy": "compound" if decomp_plan.is_compound else "standard",
                "terminology": terminology_debug,
                "cached": cached,
                "row_count": total_rows,
                "execution_ms": round(total_ms, 2),
                "sql": combined_sql,
                "security_role": security_ctx.role,
                "memory_architecture": self._memory_debug(
                    selected_rules_text, debug_window, debug_summary,
                    debug_vector, entity_tracker, clean_business_rules
                ),
                "intent_analysis": analysis,
                "llm_prompts": {"sql_generation": debug_sql_prompt},
            },
        )

    # ─────────────────────────────────────────────────────────────────────
    # SHARED HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def _build_source_uri(self) -> str:
        return (
            f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        )

    async def _save_state(
        self,
        *,
        session_ctx,
        sanitized_question: str,
        final_query: str,
        combined_sql: str,
        exec_status: str,
        answer: str,
        rows: list,
        row_count: int,
        route: str,
    ) -> None:
        try:
            dfields = _extract_dialogue_fields(combined_sql, final_query)
            last_state = LastQueryState(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                original_question=sanitized_question,
                corrected_question=final_query,
                generated_sql=combined_sql,
                executed_sql=combined_sql,
                execution_status=exec_status,
                answer=answer,
                rows=rows,
                row_count=row_count,
                truncated=False,
                dialogue_state=DialogueState(
                    last_sql=combined_sql,
                    last_standalone_question=final_query,
                    last_answer_summary=answer,
                    last_metric=dfields["metric"],
                    last_date_range=dfields["date_range"],
                    last_filters=dfields["filters"],
                    last_route=route,
                ),
            )
            await self._conversation_state_store.save_last_query_state(
                session_ctx.user_id, session_ctx.session_id, last_state
            )
        except Exception as exc:
            logger.error("Failed to save state: %s", exc)

    async def _update_memory(
        self,
        session_ctx,
        question: str,
        answer: str,
        rag_context: str,
        generated_sql: str,
        execution_status: str,
    ) -> None:
        try:
            await self._memory_manager.update_memory_pipeline(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                question=question,
                answer=answer,
                full_prompt="",
                rag_context=rag_context,
                generated_sql=generated_sql,
                execution_status=execution_status,
            )
        except Exception as exc:
            logger.error("Memory pipeline failed: %s", exc)

    async def _dump_conversation(
        self,
        question: str,
        answer: str,
        full_prompt: str,
        rag_context: str,
        sql: str,
        exec_status: str,
        model: str,
        estimated_tokens: int,
    ) -> None:
        try:
            from app.observability.file_dumper import dump_conversation
            await dump_conversation(
                user_query=question,
                ai_response=answer,
                full_prompt=full_prompt,
                rag_context=rag_context,
                generated_sql=sql,
                execution_status=exec_status,
                human_readable_prompt=full_prompt,
                llm_model_name=model,
                max_context_window=8000,
                estimated_tokens=estimated_tokens,
            )
        except Exception as exc:
            logger.error("File dumper failed: %s", exc)

    @staticmethod
    def _memory_debug(
        selected_rules: str,
        debug_window: list,
        debug_summary: str,
        debug_vector: list,
        entity_tracker: EntityTracker,
        aggregated_context: str,
    ) -> dict:
        return {
            "1_backup_rules": selected_rules,
            "2_recent_window": debug_window,
            "3_rolling_summary": debug_summary,
            "4_vector_matches": debug_vector,
            "5_tracked_entities": getattr(entity_tracker, "entities", []),
            "6_aggregated_context": aggregated_context,
        }