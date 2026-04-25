# app/services/query_orchestrator.py
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

# --- AGENTIC & ONTOLOGY COMPONENTS (Phases 2 & 3) ---
from app.services.query_decomposer import QueryDecomposer
from app.services.result_merger import ResultMerger
from app.services.ontology_resolver import OntologyResolver

# --- SECURITY COMPONENTS (Phase 4) ---
from app.security.row_level_security import RLSFilter
from app.security.auth_context import build_security_context

# --- GROUNDING COMPONENTS (Phase 6) ---
from app.services.grounded_synthesizer import GroundedSynthesizer

# --- MEMORY COMPONENTS (Phase 7) ---
from app.memory.entity_tracker import EntityTracker

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
    """Intelligent database query pipeline with Agentic Decomposition, Dynamic Synthesis, RLS, Hybrid RAG, Grounding, and Entity Tracking."""

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

        # Initialize Phase 2-7 Components
        self._query_decomposer = QueryDecomposer(prompt_builder)
        self._result_merger = ResultMerger()
        self._ontology_resolver = OntologyResolver()
        self._rls_filter = RLSFilter()
        self._grounded_synthesizer = GroundedSynthesizer()

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

        # ── RLS SECURITY INIT ──
        security_ctx = build_security_context(
            user_id=session_ctx.user_id,
            tenant_id=tenant_id,
            user_roles={"role": "admin"}
        )

        # ── OBSERVABILITY INIT ──
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
                try:
                    rag_context_str = await retrieve_dynamic_rag_context(tenant_id)
                except Exception:
                    rag_context_str = ""

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
            selected_rules_text = await self._schema_service.select_examples(question=sanitized.normalized, tenant_id=tenant_id, limit=5)

            rules_injected = len([r for r in selected_rules_text.split("\n\n") if r.strip()])
            double_prefix_count = sum(1 for r in all_rules_raw if "CRITICAL: CRITICAL:" in r.upper())
            contradiction_fired = any(s in sanitized.normalized.lower() for s in ["how many", "total count", "all bookings", "every booking"])

            obs.record_rag(rules_loaded=len(all_rules_raw), rules_injected=rules_injected, contradiction_resolved=contradiction_fired, double_prefix_fixed=double_prefix_count, vector_results_count=len(debug_vector), cache_hit=False)

            dialogue_state_obj = await self._conversation_state_store.get_dialogue_state(session_ctx.user_id, session_ctx.session_id)

            # --- PHASE 7: ENTITY TRACKING ---
            entity_tracker = EntityTracker()
            if hasattr(dialogue_state_obj, 'last_filters') and dialogue_state_obj.last_filters:
                entity_tracker.load_from_state(dialogue_state_obj.last_filters)
            if hasattr(dialogue_state_obj, 'last_date_range') and dialogue_state_obj.last_date_range:
                entity_tracker.load_from_state({"date_period": dialogue_state_obj.last_date_range})

            entity_context = entity_tracker.to_context_block()

            formatted_history = "\n".join([f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}" for msg in debug_window[-4:]])

            # Inject entity_context into the planner context so it can resolve coreferences perfectly
            planner_context = f"{profile_context}{self._prompt_builder._prune_schema(schema)}\n\nConversation Context:\n{session_context}\n\n{entity_context}"

            # --- INTENT ROUTING ---
            analysis = await asyncio.to_thread(
                self._intent_service.analyze, user_question=sanitized.normalized,
                memory_context=planner_context, dialogue_state=json.dumps(dialogue_state_obj.to_prompt_payload()), chat_history=formatted_history
            )
            intent = analysis.get("route", "database_query")

            obs.record_intent(route=intent, confidence=analysis.get("confidence", 0.0), is_follow_up=analysis.get("is_follow_up", False), follow_up_strategy=analysis.get("follow_up_strategy", "none"), standalone_question=analysis.get("standalone_question", sanitized.normalized), rewrite_called=True, plan_called=True, critic_called=analysis.get("confidence", 1.0) < 0.85, critic_verdict="reviewed" if analysis.get("confidence", 1.0) < 0.85 else "n/a")

            # --- AGENTIC QUERY DECOMPOSITION ---
            final_query = analysis.get("standalone_question", sanitized.normalized)
            knowledge_chunks = [profile_context] if user_profile else []
            if selected_rules_text: knowledge_chunks.append("CRITICAL BUSINESS RULES:\n" + selected_rules_text)

            if analysis.get("is_follow_up") and entity_context:
                knowledge_chunks.append(entity_context)

            clean_business_rules = "\n\n".join(chunk for chunk in knowledge_chunks if chunk)

            target_model, is_cloud = os.getenv("HF_MODEL", "local-llm"), False

            decomp_plan = await asyncio.to_thread(
                self._query_decomposer.decompose,
                question=final_query,
                schema_block=self._prompt_builder._prune_schema(schema),
                dialogue_state=json.dumps(dialogue_state_obj.to_prompt_payload())
            )

            all_sub_results = []
            combined_sql_executed = []
            total_execution_ms = 0.0
            total_rows_returned = 0

            # Safe Schema Column Extraction
            schema_cols = set()
            if hasattr(schema, "schema_dict") and isinstance(schema.schema_dict, dict):
                for table_name, col_strings in schema.schema_dict.items():
                    for col_str in col_strings:
                        match = re.match(r"-\s*([a-zA-Z0-9_]+)", col_str)
                        if match:
                            schema_cols.add(match.group(1))

            debug_sql_prompt = None

            for sq in decomp_plan.sub_queries:
                logger.info(f"Executing Sub-Query {sq.index}: {sq.question}")

                # --- ONTOLOGY RESOLUTION ---
                resolved_terms = await asyncio.to_thread(
                    self._ontology_resolver.resolve,
                    question=sq.question,
                    tenant_id=tenant_id,
                    schema_columns=schema_cols
                )
                ontology_context_str = self._ontology_resolver.build_ontology_prompt_block(resolved_terms)

                max_attempts, attempt = 3, 1
                db_error_message, execution, sql_result, cached = None, None, None, False
                previous_sql = ""
                secured_sql = ""

                while attempt <= max_attempts:
                    current_context = clean_business_rules
                    if attempt > 1 and db_error_message and previous_sql:
                        logger.warning(f"🔄 [SELF-HEALING] Attempt {attempt}/{max_attempts} triggered.")
                        current_context += (
                            f"\n\nCRITICAL ERROR REFLECTION:\n"
                            f"Your previous SQL query:\n```sql\n{previous_sql}\n```\n"
                            f"Failed with the following error:\n{db_error_message}\n\n"
                            f"Write a corrected SQL query that fixes this exact error."
                        )

                    if attempt == 1:
                        cached_sql = await self._memory_manager._vector_memory.get_semantic_sql(query=sq.question, schema_fingerprint=schema.fingerprint)
                        if cached_sql:
                            from app.services.sql_generation_service import SqlGenerationResult
                            from app.security.sql_guard import SqlValidationResult
                            sql_result = SqlGenerationResult(sql=cached_sql, strategy="semantic_cache_hit", validation=SqlValidationResult(is_valid=True, normalized_sql=cached_sql, errors=[]))
                            cached = True

                    if not cached:
                        debug_sql_prompt = self._prompt_builder.build_sql_prompt(
                            question=sq.question,
                            schema=schema,
                            ontology_context=ontology_context_str,
                            session_context=current_context
                        )
                        sql_result = await self._sql_generation_service.generate_sql(
                            question=sq.question,
                            schema=schema,
                            ontology_context=ontology_context_str,
                            session_context=current_context,
                            model=target_model,
                            is_cloud=is_cloud
                        )
                        obs.record_tokens_from_prompt(prompt_text=debug_sql_prompt, completion_text=sql_result.sql, model=target_model)

                    if not sql_result.is_valid:
                        safe_answer = f"Blocked: {'; '.join(sql_result.validation.errors)}"
                        obs.record_sql(sql=sql_result.sql, strategy="security_blocked")
                        obs.finish()
                        return QueryResponse(answer=safe_answer, confidence=1.0, session_id=session_ctx.session_id, presentation={"kind": "notice", "title": "Blocked", "message": safe_answer})

                    # ── APPLY ROW LEVEL SECURITY (RLS) ──
                    secured_sql = self._rls_filter.apply(sql_result.sql, security_ctx)

                    cache_key = f"{schema.fingerprint}|{security_ctx.role}|{secured_sql}"
                    execution = self._query_cache.get(cache_key)

                    if execution is None:
                        try:
                            source_uri = f"mysql+pymysql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

                            execution = self._sql_execution_service.execute(sql_query=secured_sql, source_uri=source_uri, category="relational_db")
                            if hasattr(execution, "error_message") and execution.error_message:
                                raise ValueError(execution.error_message)

                            # ── MASK PII POST-EXECUTION ──
                            if execution.rows:
                                execution.rows = self._rls_filter.mask_pii(execution.rows, security_ctx)

                            self._query_cache.set(cache_key, execution)
                            if not cached:
                                asyncio.create_task(self._memory_manager._vector_memory.save_semantic_sql(query=sq.question, sql=sql_result.sql, schema_fingerprint=schema.fingerprint))
                            break
                        except Exception as e:
                            db_error_message = str(e)
                            previous_sql = sql_result.sql
                            attempt += 1
                            continue
                    else:
                        break

                if execution is None or (hasattr(execution, "error_message") and execution.error_message):
                    all_sub_results.append({
                        "question": sq.question, "sql": sql_result.sql if sql_result else "Failed",
                        "data": [], "rows": 0, "synthesis": f"Failed to execute sub-query: {db_error_message}"
                    })
                else:
                    combined_sql_executed.append(execution.executed_sql)
                    total_execution_ms += execution.execution_ms
                    total_rows_returned += execution.row_count

                    # --- ZERO-HALLUCINATION SYNTHESIS (FIXED) ---
                    # Always try to synthesize an answer if we got rows!
                    sq_synthesis = "Data retrieved."
                    if execution.rows:
                        try:
                            sq_synthesis, synth_conf = await asyncio.to_thread(
                                self._grounded_synthesizer.synthesize,
                                question=sq.question,
                                rows=execution.rows,
                                row_count=execution.row_count,
                                executed_sql=execution.executed_sql,
                                metric_context=ontology_context_str
                            )
                            logger.info(f"Sub-Query Grounding Confidence: {synth_conf:.2f}")
                        except Exception as e:
                            logger.error(f"Grounded synthesis failed: {e}")

                    all_sub_results.append({
                        "question": sq.question, "sql": execution.executed_sql,
                        "data": execution.rows, "rows": execution.row_count, "synthesis": sq_synthesis
                    })

            # --- UNIFIED RESULT MERGER (FIXED) ---
            # We bypass the merger and use the single synthesized paragraph directly!
            if decomp_plan.merge_strategy == "single" and all_sub_results:
                explanation = all_sub_results[0].get("synthesis", "Data retrieved.")
            else:
                explanation = await asyncio.to_thread(
                    self._result_merger.merge,
                    original_question=final_query,
                    sub_results=all_sub_results,
                    merge_strategy=decomp_plan.merge_strategy
                )

            final_combined_sql = "\n\n".join(combined_sql_executed)
            obs.record_sql(sql=final_combined_sql, strategy="compound_execution" if decomp_plan.is_compound else "standard", execution_ms=total_execution_ms, rows_returned=total_rows_returned)

            presentation_rows = all_sub_results[0].get("data", []) if all_sub_results else []
            table_text, presentation = self._response_formatter.format_database_result(
                question=final_query, rows=presentation_rows, truncated=False
            )
            exec_status = f"Success ({total_rows_returned} rows across {len(all_sub_results)} queries)"

            # UPDATE MEMORY & STATE
            try:
                dfields = _extract_dialogue_fields(final_combined_sql, final_query)
                last_state = LastQueryState(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id, original_question=user_question, corrected_question=final_query, generated_sql=final_combined_sql, executed_sql=final_combined_sql, execution_status=exec_status, answer=explanation, rows=presentation_rows, row_count=total_rows_returned, truncated=False,
                    dialogue_state=DialogueState(last_sql=final_combined_sql, last_standalone_question=final_query, last_answer_summary=explanation, pending_clarification=None, last_metric=dfields["metric"], last_date_range=dfields["date_range"], last_filters=dfields["filters"], last_route="database_query")
                )
                await self._conversation_state_store.save_last_query_state(session_ctx.user_id, session_ctx.session_id, last_state)
            except Exception as e:
                logger.error(f"Failed to save conversation state: {e}")

            try:
                sql_to_save = getattr(sql_result, 'raw_llm_output', final_combined_sql) if sql_result else final_combined_sql
                await self._memory_manager.update_memory_pipeline(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id, question=final_query, answer=explanation,
                    full_prompt="Compound Query Executed", rag_context=clean_business_rules, generated_sql=sql_to_save, execution_status=exec_status
                )
            except Exception as e:
                logger.error(f"Memory pipeline failed: {e}")

            def build_debug_meta(strategy: str, sql: str = "", ms: float = 0.0, rows: int = 0, cached: bool = False, sql_prompt: str = None, synth_prompt: str = None):
                return {
                    "strategy": strategy, "cached": cached, "row_count": rows, "execution_ms": round(ms, 2), "sql": sql,
                    "security_role": security_ctx.role,
                    "memory_architecture": {
                        "1_rag_schema": selected_rules_text,
                        "2_recent_window": debug_window,
                        "3_rolling_summary": debug_summary,
                        "4_chromadb_vector_matches": debug_vector,
                        "5_tracked_entities": entity_tracker.entities if hasattr(entity_tracker, 'entities') else [],
                        "6_final_aggregated_context": clean_business_rules
                    },
                    "intent_analysis": analysis,
                    "llm_prompts": {
                        "sql_generation": sql_prompt,
                        "synthesis": synth_prompt
                    }
                }

            # ── VISUAL MARKDOWN TRACE DUMPER ──
            try:
                from app.observability.file_dumper import dump_conversation
                await dump_conversation(
                    user_query=final_query,
                    ai_response=explanation,
                    full_prompt=f"Decomposition Strategy: {decomp_plan.merge_strategy}\n\nSub-Queries:\n" + "\n".join([sq.question for sq in decomp_plan.sub_queries]),
                    rag_context=clean_business_rules,
                    generated_sql=final_combined_sql,
                    execution_status=exec_status,
                    human_readable_prompt="Compound Context",
                    llm_model_name=target_model,
                    max_context_window=8000,
                    estimated_tokens=obs.audit.token_usage.total_tokens
                )
            except Exception as e:
                logger.error(f"Markdown file dumper failed: {e}")

            obs.finish()

            return QueryResponse(
                answer=explanation,
                confidence=0.95 if total_rows_returned > 0 else 0.85,
                session_id=session_ctx.session_id,
                presentation=presentation,
                meta=build_debug_meta(
                    strategy="compound_execution" if decomp_plan.is_compound else "standard",
                    sql=final_combined_sql,
                    ms=total_execution_ms,
                    rows=total_rows_returned,
                    cached=cached,
                    sql_prompt=debug_sql_prompt if not decomp_plan.is_compound else "Compound queries executed separately.",
                    synth_prompt="Compound Synthesis",
                )
            )