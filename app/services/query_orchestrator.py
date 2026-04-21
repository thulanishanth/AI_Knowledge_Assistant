#app/services/query_orchestrator.py
from __future__ import annotations

import os
import asyncio
import json
import re
from pathlib import Path
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
from app.services.llm_client import call_llm
from app.services.prompt_builder import PromptBuilder
from app.services.conversation_state_store import ConversationStateStore, LastQueryState, DialogueState
from app.services.rag_retriever import retrieve_dynamic_rag_context
from app.observability.query_observer import QueryObserver

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


def _extract_dialogue_fields(sql: str, question: str) -> dict:
    """Pull metric/filter signals from a successful SQL for the dialogue state."""
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

    filters = {}
    for m in re.finditer(r"(\w+)\s*=\s*'([^']+)'", sql):
        col, val = m.group(1), m.group(2)
        if col.lower() not in {"table_schema", "table_name"}:
            filters[col] = val

    return {"metric": metric, "date_range": date_range.strip() if date_range else None, "filters": filters}


class QueryOrchestrator:
    """Intelligent database query pipeline with intent routing and dynamic synthesis."""

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
        self._query_cache: TTLCache[QueryExecutionResult] = TTLCache(settings.query_cache_ttl_seconds)

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

        session_ctx = self._session_manager.resolve(user_id=user_id, session_id=session_id)

        # ── OBSERVABILITY INIT ──────────────────────────────────────────────────
        obs = QueryObserver(
            session_id=session_ctx.session_id, user_id=session_ctx.user_id,
            tenant_id=tenant_id, question=sanitized.normalized,
        )
        obs.start()

        with tracing.span("query.handle"), metrics.timer("query_total"):
            schema = await self._schema_service.get_schema(tenant_id)
            user_profile = await self._memory_manager._vector_memory.get_user_profile(session_ctx.user_id)
            profile_context = f"USER PROFILE & PREFERENCES:\n{user_profile}\n\n" if user_profile else ""

            session_context = ""
            debug_vector, debug_window, debug_summary = [], [], ""

            try:
                try: rag_context_str = await asyncio.to_thread(retrieve_dynamic_rag_context, tenant_id)
                except Exception: rag_context_str = ""

                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                    user_query=sanitized.normalized, tenant_id=tenant_id, rag_context=rag_context_str,
                )
                session_context = str(memory_context.get("aggregated_context", ""))
                debug_vector = memory_context.get("vector_results", [])
                debug_window = memory_context.get("window_messages", [])
                debug_summary = memory_context.get("summary", "")
            except Exception: pass

            # ── RECORD RAG HEALTH ──
            all_rules_raw = self._schema_service.get_all_rules_raw(tenant_id)
            selected_rules_text = self._schema_service.select_examples(question=sanitized.normalized, tenant_id=tenant_id, limit=5)
            rules_injected = len([r for r in selected_rules_text.split("\n\n") if r.strip()])
            double_prefix_count = sum(1 for r in all_rules_raw if "CRITICAL: CRITICAL:" in r.upper())
            contradiction_fired = any(s in sanitized.normalized.lower() for s in ["how many", "total count", "all bookings", "every booking"])

            obs.record_rag(rules_loaded=len(all_rules_raw), rules_injected=rules_injected, contradiction_resolved=contradiction_fired, double_prefix_fixed=double_prefix_count, vector_results_count=len(debug_vector), cache_hit=False)

            dialogue_state_obj = await self._conversation_state_store.get_dialogue_state(session_ctx.user_id, session_ctx.session_id)
            formatted_history = "\n".join([f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}" for msg in debug_window[-4:]])
            planner_context = f"{profile_context}{self._prompt_builder._prune_schema(schema)}\n\nConversation Context:\n{session_context}"

            analysis = await asyncio.to_thread(
                self._intent_service.analyze, user_question=sanitized.normalized,
                memory_context=planner_context, dialogue_state=json.dumps(dialogue_state_obj.to_prompt_payload()), chat_history=formatted_history
            )
            intent = analysis.get("route", "database_query")

            # ── RECORD INTENT HEALTH ──
            obs.record_intent(route=intent, confidence=analysis.get("confidence", 0.0), is_follow_up=analysis.get("is_follow_up", False), follow_up_strategy=analysis.get("follow_up_strategy", "none"), standalone_question=analysis.get("standalone_question", sanitized.normalized), rewrite_called=True, plan_called=True, critic_called=analysis.get("confidence", 1.0) < 0.85, critic_verdict="reviewed" if analysis.get("confidence", 1.0) < 0.85 else "n/a")

            def build_debug_meta(strategy: str, sql: str = "", ms: float = 0.0, rows: int = 0, cached: bool = False, sql_prompt: str = None, synth_prompt: str = None):
                return {
                    "strategy": strategy, "cached": cached, "row_count": rows, "execution_ms": round(ms, 2), "sql": sql,
                    "memory_architecture": {
                        "1_rag_schema": selected_rules_text,
                        "2_recent_window": debug_window,
                        "3_rolling_summary": debug_summary,
                        "4_chromadb_vector_matches": debug_vector,
                        "5_final_aggregated_context": session_context
                    },
                    "intent_analysis": analysis,
                    "llm_prompts": {
                        "sql_generation": sql_prompt,
                        "synthesis": synth_prompt
                    }
                }

            async def persist_interaction_dump(
                *,
                question: str,
                answer: str,
                full_prompt: str = "",
                rag_context: str = "",
                generated_sql: str = "",
                execution_status: str = "",
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
                    logger.error(f"Memory pipeline failed: {e}")

            if intent == "clarify" or analysis.get("needs_clarification") is True:
                clarifying_msg = analysis.get("clarifying_question", "Could you provide a little more detail?")
                dialogue_state_obj.pending_clarification = clarifying_msg
                await self._conversation_state_store.save_dialogue_state(session_ctx.user_id, session_ctx.session_id, dialogue_state_obj)
                await persist_interaction_dump(
                    question=sanitized.normalized,
                    answer=clarifying_msg,
                    execution_status="Clarification requested",
                )
                obs.finish()
                return QueryResponse(answer=clarifying_msg, confidence=1.0, session_id=session_ctx.session_id, presentation={"kind": "notice", "title": "Clarification Needed", "message": clarifying_msg}, meta=build_debug_meta("intent_clarify"))

            if intent in ["general_answer", "explain_last_answer", "diagnose"]:
                chat_answer = await asyncio.to_thread(self._intent_service.answer_general_question, user_question=sanitized.normalized, memory_context=session_context)
                await persist_interaction_dump(
                    question=sanitized.normalized,
                    answer=chat_answer,
                    rag_context=session_context,
                    execution_status=f"Intent route: {intent}",
                )
                obs.finish()
                return QueryResponse(answer=chat_answer, confidence=1.0, session_id=session_ctx.session_id, presentation={"kind": "text", "message": chat_answer}, meta=build_debug_meta(f"intent_{intent}"))

            final_query = analysis.get("standalone_question", sanitized.normalized)
            knowledge_chunks = [profile_context] if user_profile else []
            if selected_rules_text: knowledge_chunks.append("CRITICAL BUSINESS RULES:\n" + selected_rules_text)
            clean_business_rules = "\n\n".join(chunk for chunk in knowledge_chunks if chunk)

            target_model, is_cloud = os.getenv("HF_MODEL", "local-llm"), False
            max_attempts, attempt = 3, 1
            db_error_message, execution, sql_result, cached = None, None, None, False
            previous_sql, debug_sql_prompt = None, ""

            while attempt <= max_attempts:
                current_context = clean_business_rules
                if attempt > 1 and db_error_message and previous_sql:
                    logger.warning(f"🔄 [SELF-HEALING] Attempt {attempt}/{max_attempts} triggered.")
                    current_context += (
                        f"\n\nCRITICAL ERROR REFLECTION:\n"
                        f"Your previous SQL query:\n```sql\n{previous_sql}\n```\n"
                        f"Failed with the following error:\n{db_error_message}\n\n"
                        f"Please analyze the schema carefully and write a corrected SQL query that fixes this exact error."
                    )

                if attempt == 1:
                    cached_sql = await self._memory_manager._vector_memory.get_semantic_sql(query=final_query, schema_fingerprint=schema.fingerprint)
                    if cached_sql:
                        from app.services.sql_generation_service import SqlGenerationResult
                        from app.security.sql_guard import SqlValidationResult
                        sql_result = SqlGenerationResult(sql=cached_sql, is_valid=True, strategy="semantic_cache_hit", notice=None, validation=SqlValidationResult(is_valid=True, normalized_sql=cached_sql))
                        cached = True

                if not cached:
                    debug_sql_prompt = self._prompt_builder.build_sql_prompt(question=final_query, schema=schema, session_context=current_context)
                    sql_result = await self._sql_generation_service.generate_sql(question=final_query, schema=schema, session_context=current_context, model=target_model, is_cloud=is_cloud)
                    # Capture token estimation specifically for this large LLM call
                    obs.record_tokens_from_prompt(prompt_text=debug_sql_prompt, completion_text=sql_result.sql, model=target_model)

                if not sql_result.is_valid:
                    safe_answer = f"Blocked: {'; '.join(sql_result.validation.errors)}"
                    obs.record_sql(sql=sql_result.sql, strategy="security_blocked")
                    await persist_interaction_dump(
                        question=final_query,
                        answer=safe_answer,
                        full_prompt=debug_sql_prompt,
                        rag_context=clean_business_rules,
                        generated_sql=sql_result.sql,
                        execution_status="Security blocked",
                    )
                    obs.finish()
                    return QueryResponse(answer=safe_answer, confidence=1.0, session_id=session_ctx.session_id, presentation={"kind": "notice", "title": "Blocked", "message": safe_answer}, meta=build_debug_meta("security_blocked", sql=sql_result.sql, sql_prompt=debug_sql_prompt))

                cache_key = f"{schema.fingerprint}|{sql_result.sql}"
                execution = self._query_cache.get(cache_key)

                if execution is None:
                    try:
                        source_uri = f"mysql+pymysql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
                        execution = self._sql_execution_service.execute(sql_query=sql_result.sql, source_uri=source_uri, category="relational_db")
                        if hasattr(execution, "error_message") and execution.error_message:
                            raise ValueError(execution.error_message)
                        self._query_cache.set(cache_key, execution)
                        if not cached:
                            asyncio.create_task(self._memory_manager._vector_memory.save_semantic_sql(query=final_query, sql=sql_result.sql, schema_fingerprint=schema.fingerprint))
                        break
                    except Exception as e:
                        db_error_message = str(e)
                        previous_sql = sql_result.sql
                        attempt += 1
                        continue
                else:
                    break

            if execution is None or (hasattr(execution, "error_message") and execution.error_message):
                failed_msg = "I encountered a technical error querying the database."
                obs.record_sql(sql=sql_result.sql if sql_result else "", strategy="failed", execution_error=db_error_message)

                try:
                    await self._memory_manager.update_memory_pipeline(
                        user_id=session_ctx.user_id, session_id=session_ctx.session_id, question=final_query, answer=failed_msg,
                        full_prompt=debug_sql_prompt, rag_context=clean_business_rules, generated_sql=sql_result.sql if sql_result else "None", execution_status=f"Failed after {max_attempts} attempts. Last Error: {db_error_message}"
                    )
                except Exception: pass

                obs.finish()
                return QueryResponse(answer=failed_msg, confidence=0.0, session_id=session_ctx.session_id, presentation={"kind": "error", "message": db_error_message}, meta=build_debug_meta("execution_failed", sql=sql_result.sql if sql_result else "", sql_prompt=debug_sql_prompt))

            # ── RECORD SQL HEALTH ──
            obs.record_sql(sql=execution.executed_sql, strategy=sql_result.strategy, repair_attempts=attempt-1, execution_ms=execution.execution_ms, rows_returned=execution.row_count, truncated=execution.truncated)
            if cached: obs.audit.rag_health.cache_hit = True

            explanation = "Here are the results I found for your query:"
            debug_synthesis_prompt = None
            if execution.rows:
                try:
                    data_preview = json.dumps(execution.rows[:5], default=str)
                    debug_synthesis_prompt = self._prompt_builder.build_synthesis_prompt(question=final_query, data_preview=data_preview, row_count=execution.row_count, executed_sql=execution.executed_sql)
                    explanation = await asyncio.to_thread(call_llm, prompt=debug_synthesis_prompt, max_tokens=150, temperature=0.3)
                except Exception:
                    pass

            obs.record_answer(explanation, confidence=0.95 if execution.row_count > 0 else 0.85)
            table_text, presentation = self._response_formatter.format_database_result(question=final_query, rows=execution.rows, truncated=execution.truncated)

            try:
                dfields = _extract_dialogue_fields(execution.executed_sql, final_query)
                last_state = LastQueryState(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id, original_question=user_question, corrected_question=final_query, generated_sql=sql_result.sql, executed_sql=execution.executed_sql, execution_status=f"Success ({execution.row_count} rows)", answer=explanation, rows=execution.rows, row_count=execution.row_count, truncated=execution.truncated,
                    dialogue_state=DialogueState(last_sql=execution.executed_sql, last_standalone_question=final_query, last_answer_summary=explanation, pending_clarification=None, last_metric=dfields["metric"], last_date_range=dfields["date_range"], last_filters=dfields["filters"], last_route="database_query")
                )
                await self._conversation_state_store.save_last_query_state(session_ctx.user_id, session_ctx.session_id, last_state)
            except Exception as e:
                logger.error(f"Failed to save conversation state: {e}")

            # Use raw LLM output in memory dump to preserve SQL Comments
            try:
                await self._memory_manager.update_memory_pipeline(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id, question=final_query, answer=explanation,
                    full_prompt=debug_sql_prompt, rag_context=clean_business_rules, generated_sql=getattr(sql_result, 'raw_llm_output', sql_result.sql), execution_status=f"Success ({execution.row_count} rows)"
                )
            except Exception as e:
                logger.error(f"Memory pipeline failed: {e}")

            obs.finish()

            return QueryResponse(
                answer=explanation,
                confidence=0.95 if execution.row_count > 0 else 0.85,
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
                )
            )
