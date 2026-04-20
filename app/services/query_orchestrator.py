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
from app.observability.file_dumper import dump_conversation

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

        with tracing.span("query.handle"), metrics.timer("query_total"):
            schema = await self._schema_service.get_schema(tenant_id)
            
            # ==========================================
            # FEATURE 3: INJECT THE LIVING PROFILE
            # ==========================================
            user_profile = await self._memory_manager._vector_memory.get_user_profile(session_ctx.user_id)
            profile_context = f"USER PROFILE & PREFERENCES:\n{user_profile}\n\n" if user_profile else ""
            
            # Quick intent check for Metadata Pre-Filtering (Feature 4)
            fast_topic_guess = "finance" if any(w in sanitized.normalized.lower() for w in ["revenue", "price", "cost", "money"]) else None

            # Fetch memory
            session_context = ""
            debug_vector = []
            debug_window = []
            debug_rag = ""
            debug_summary = ""
            
            try:
                # Fetch RAG context for this tenant
                try:
                    rag_context_str = await asyncio.to_thread(retrieve_dynamic_rag_context, tenant_id)
                except Exception:
                    rag_context_str = ""

                # FEATURE 4 applied: Fast topic filtering
                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id, 
                    session_id=session_ctx.session_id, 
                    user_query=sanitized.normalized,
                    tenant_id=tenant_id,
                    rag_context=rag_context_str,   # <-- now actually passed
                )
                session_context = str(memory_context.get("aggregated_context", ""))
                debug_vector = memory_context.get("vector_results", [])
                debug_window = memory_context.get("window_messages", [])
                debug_rag = memory_context.get("rag_context", "")
                debug_summary = memory_context.get("summary", "")
            except Exception: 
                pass

            dialogue_state_obj = await self._conversation_state_store.get_dialogue_state(session_ctx.user_id, session_ctx.session_id)
            formatted_history = "\n".join([f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}" for msg in debug_window[-4:]])
            
            # Send pruned schema & profile to the planner so it can route correctly
            planner_context = f"{profile_context}{self._prompt_builder._prune_schema(schema)}\n\nConversation Context:\n{session_context}"
            
            analysis = await asyncio.to_thread(
                self._intent_service.analyze, 
                user_question=sanitized.normalized, 
                memory_context=planner_context,
                dialogue_state=json.dumps(dialogue_state_obj.to_prompt_payload()), 
                chat_history=formatted_history
            )
            intent = analysis.get("route", "database_query")
            
            def build_debug_meta(strategy: str, sql: str = "", ms: float = 0.0, rows: int = 0, cached: bool = False, sql_prompt: str = None, synth_prompt: str = None):
                return {
                    "strategy": strategy, "cached": cached, "row_count": rows, "execution_ms": round(ms, 2), "sql": sql,
                    "memory_architecture": {
                        "1_rag_schema": debug_rag,
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

            # --- 3. The Clarification Router ---
            if intent == "clarify" or analysis.get("needs_clarification") is True:
                clarifying_msg = analysis.get("clarifying_question", "Could you provide a little more detail about the data you want to see?")
                
                dialogue_state_obj.pending_clarification = clarifying_msg
                await self._conversation_state_store.save_dialogue_state(
                    session_ctx.user_id, session_ctx.session_id, dialogue_state_obj
                )
                
                await self._memory_manager.update_memory_pipeline(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                    question=sanitized.normalized, answer=clarifying_msg,
                )
                
                return QueryResponse(
                    answer=clarifying_msg, 
                    confidence=1.0, 
                    session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Clarification Needed", "message": clarifying_msg},
                    meta=build_debug_meta("intent_clarify")
                )

            # --- 4. Schema Inquiry ---
            if intent == "schema_answer":
                schema_answer = schema.to_prompt_block()
                return QueryResponse(
                    answer=schema_answer, confidence=1.0, session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": f"Schema for {schema.table_name}", "message": schema_answer},
                    meta=build_debug_meta("llm_intent_schema"),
                )
            
            # --- 5. Greeting, Chitchat, or Explanation ---
            if intent in ["general_answer", "explain_last_answer", "diagnose"]:
                chat_answer = await asyncio.to_thread(
                    self._intent_service.answer_general_question,
                    user_question=sanitized.normalized,
                    memory_context=session_context
                )
                
                try:
                    await self._memory_manager.update_memory_pipeline(
                        user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                        question=sanitized.normalized, answer=chat_answer,
                    )
                except Exception:
                    pass
                    
                return QueryResponse(
                    answer=chat_answer, confidence=1.0, session_id=session_ctx.session_id,
                    presentation={"kind": "text", "message": chat_answer},
                    meta=build_debug_meta(f"intent_{intent}"),
                )

            # --- 6. Database Query Flow ---
            final_query = analysis.get("standalone_question", sanitized.normalized)
            
            target_model, is_cloud = os.getenv("HF_MODEL", "unknown-local-model"), False  

            # ==========================================
            # SURGICAL RULE EXTRACTION
            # ==========================================
            knowledge_chunks = [profile_context] if user_profile else []
            
            # Fetch strictly the MySQL business rules needed for this question
            surgical_rules = self._schema_service.select_examples(question=final_query, tenant_id=tenant_id, limit=5)
            if surgical_rules: 
                knowledge_chunks.append("CRITICAL BUSINESS RULES:\n" + surgical_rules)
            
            # Vector memory matches
            if debug_vector:
                knowledge_only = [
                    item for item in debug_vector
                    if item.get("source") == "knowledge_memory"
                ]
                knowledge_chunks.extend(
                    f"- {item.get('text', '').strip()}"
                    for item in knowledge_only
                    if item.get("text")
                )

            clean_business_rules = "\n\n".join(chunk for chunk in knowledge_chunks if chunk)

            # ==========================================
            # SELF-HEALING SQL EXECUTION & CRITIC LOOP
            # ==========================================
            max_attempts, attempt = 3, 1
            db_error_message, execution, sql_result, cached = None, None, None, False
            previous_sql = None
            debug_sql_prompt = ""

            while attempt <= max_attempts:
                current_context = clean_business_rules
                
                # Inject Error Reflection if this is a retry attempt
                if attempt > 1 and db_error_message and previous_sql:
                    logger.warning(f"🔄 [SELF-HEALING] Attempt {attempt}/{max_attempts} triggered.")
                    current_context += (
                        f"\n\nCRITICAL ERROR REFLECTION:\n"
                        f"Your previous SQL query:\n```sql\n{previous_sql}\n```\n"
                        f"Failed with the following error:\n{db_error_message}\n\n"
                        f"Please analyze the schema carefully and write a corrected SQL query that fixes this exact error."
                    )

                # ==========================================
                # FEATURE 1: SEMANTIC SQL CACHING INTERCEPT
                # ==========================================
                cached_sql = None
                if attempt == 1:
                    cached_sql = await self._memory_manager._vector_memory.get_semantic_sql(
                        query=final_query, schema_fingerprint=schema.fingerprint
                    )

                if cached_sql:
                    from app.services.sql_generation_service import SqlGenerationResult
                    from app.security.sql_guard import SqlValidationResult
                    
                    sql_result = SqlGenerationResult(
                        sql=cached_sql, 
                        is_valid=True, 
                        strategy="semantic_cache_hit", 
                        notice=None, 
                        validation=SqlValidationResult(is_valid=True, normalized_sql=cached_sql)
                    )
                    cached = True
                else:
                    debug_sql_prompt = self._prompt_builder.build_sql_prompt(question=final_query, schema=schema, session_context=current_context)
                    sql_result = await self._sql_generation_service.generate_sql(
                        question=final_query, schema=schema, session_context=current_context, model=target_model, is_cloud=is_cloud
                    )
                    logger.info(f"🚀 [LLM SQL] (Attempt {attempt}): {sql_result.sql}")
                
                # Check security blocks
                if not sql_result.is_valid:
                    error_details = "; ".join(sql_result.validation.errors)
                    safe_answer = "Security Alert: Prohibited operation." if "Write operations" in error_details else f"Blocked: {error_details}"
                    if sql_result.notice: 
                        safe_answer = f"{sql_result.notice}\n\n{safe_answer}"

                    try:
                        await self._memory_manager.update_memory_pipeline(
                            user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                            question=final_query, answer=safe_answer, full_prompt=debug_sql_prompt,
                            rag_context=current_context, generated_sql=sql_result.sql, 
                            execution_status="Failed (Security Blocked)", 
                        )
                    except Exception as e:
                        logger.error(f"Memory pipeline failed during security block: {e}")

                    return QueryResponse(
                        answer=safe_answer, confidence=1.0, session_id=session_ctx.session_id,
                        presentation={"kind": "notice", "title": "Action Blocked", "message": safe_answer},
                        meta=build_debug_meta("security_blocked", sql=sql_result.sql, sql_prompt=debug_sql_prompt),
                    )

                # --- THE SQL CRITIC (Result Critic) ---
                # Bypass critic if it's a semantic cache hit since it was already proven successful previously
                if sql_result.strategy != "semantic_cache_hit":
                    sql_review = await asyncio.to_thread(
                        self._intent_service.review_sql_candidate,
                        question=final_query,
                        sql=sql_result.sql,
                        schema=schema,
                        session_context=current_context
                    )
                    
                    if sql_review.get("verdict") == "retry" and attempt < max_attempts:
                        logger.warning(f"⚖️ [SQL CRITIC REJECTED]: {sql_review.get('reason')}")
                        db_error_message = f"SQL Critic rejected the query: {sql_review.get('reason')}"
                        previous_sql = sql_result.sql
                        attempt += 1
                        continue
                        
                    elif sql_review.get("verdict") == "clarify":
                        clarifying_msg = sql_review.get("clarifying_question", "I need a bit more detail to write this query safely.")
                        return QueryResponse(
                            answer=clarifying_msg, confidence=1.0, session_id=session_ctx.session_id,
                            presentation={"kind": "notice", "title": "Clarification Needed", "message": clarifying_msg},
                            meta=build_debug_meta("critic_clarify", sql=sql_result.sql)
                        )

                # Execute Database Query
                cache_key = f"{schema.fingerprint}|{sql_result.sql}"
                execution = self._query_cache.get(cache_key)
                
                if execution is None:
                    try:
                        # 100% DYNAMIC CONNECTION: Driven entirely by your .env file
                        # No JSON context scripts needed anymore!
                        source_uri = f"mysql+pymysql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
                        category = "relational_db"

                        execution = self._sql_execution_service.execute(
                            sql_query=sql_result.sql,
                            source_uri=source_uri,
                            category=category
                        )
                        
                        if hasattr(execution, "error_message") and execution.error_message:
                            raise ValueError(execution.error_message)
                            
                        self._query_cache.set(cache_key, execution)

                        # ==========================================
                        # FEATURE 1: SAVE SUCCESSFUL SQL TO CACHE
                        # ==========================================
                        if sql_result.strategy != "semantic_cache_hit":
                            asyncio.create_task(
                                self._memory_manager._vector_memory.save_semantic_sql(
                                    query=final_query, sql=sql_result.sql, schema_fingerprint=schema.fingerprint
                                )
                            )
                        break
                    except Exception as e:
                        db_error_message = str(e)
                        previous_sql = sql_result.sql
                        logger.error(f"❌ SQL Execution Failed: {db_error_message}")
                        attempt += 1
                        continue 
                else:
                    break

            # ULTIMATE FAILURE FALLBACK
            if execution is None or (hasattr(execution, "error_message") and execution.error_message):
                failed_msg = "I encountered a technical error while querying the database and could not resolve it. Please try rephrasing your question."
                try:
                    await self._memory_manager.update_memory_pipeline(
                        user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                        question=final_query, answer=failed_msg, full_prompt=debug_sql_prompt,
                        rag_context=clean_business_rules, generated_sql=sql_result.sql if sql_result else "None", 
                        execution_status=f"Failed after {max_attempts} attempts. Last Error: {db_error_message}", 
                    )
                except Exception as e:
                    logger.error(f"Memory pipeline failed during DB error block: {e}")
                    
                return QueryResponse(
                    answer=failed_msg, confidence=0.0, session_id=session_ctx.session_id,
                    presentation={"kind": "error", "message": db_error_message},
                    meta=build_debug_meta("execution_failed", sql=sql_result.sql if sql_result else "", sql_prompt=debug_sql_prompt),
                )

            # DYNAMIC SYNTHESIS
            explanation = "Here are the results I found for your query:"
            debug_synthesis_prompt = None
            
            if execution.rows:
                try:
                    data_preview = json.dumps(execution.rows[:5], default=str)
                    debug_synthesis_prompt = self._prompt_builder.build_synthesis_prompt(
                        question=final_query,
                        data_preview=data_preview,
                        row_count=execution.row_count,       
                        executed_sql=execution.executed_sql  
                    )
                    explanation = await asyncio.to_thread(
                        call_llm, prompt=debug_synthesis_prompt, max_tokens=150, temperature=0.3
                    )
                except Exception as e:
                    logger.error(f"Synthesis failed: {e}")
            else:
                explanation = "I ran the query, but I couldn't find any data matching your request."

            if sql_result.notice:
                explanation = f"{sql_result.notice}\n\n{explanation}"

            table_text, presentation = self._response_formatter.format_database_result(
                question=final_query, rows=execution.rows, truncated=execution.truncated,
            )

            confidence = 0.95 if execution.row_count > 0 else 0.85
            exec_status = f"Success ({execution.row_count} rows)" if execution.row_count > 0 else "Success (0 rows)"

            # UPDATE MEMORY PIPELINE 
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
            except Exception as e:
                logger.error(f"CRITICAL: Memory pipeline failed: {e}")

            # ==========================================
            # SAVE THE DIALOGUE STATE FOR FOLLOW-UPS
            # ==========================================
            try:
                dfields = _extract_dialogue_fields(execution.executed_sql, final_query)
                last_state = LastQueryState(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    original_question=user_question,
                    corrected_question=final_query,
                    generated_sql=sql_result.sql,
                    executed_sql=execution.executed_sql,
                    execution_status=exec_status,
                    answer=explanation,
                    rows=execution.rows,
                    row_count=execution.row_count,
                    truncated=execution.truncated,
                    dialogue_state=DialogueState(
                        last_sql=execution.executed_sql,
                        last_standalone_question=final_query,
                        last_answer_summary=explanation,
                        pending_clarification=None,
                        last_metric=dfields["metric"],
                        last_date_range=dfields["date_range"],
                        last_filters=dfields["filters"],
                        last_route="database_query",
                    )
                )
                await self._conversation_state_store.save_last_query_state(
                    session_ctx.user_id, session_ctx.session_id, last_state
                )
            except Exception as e:
                logger.error(f"Failed to save conversation state: {e}")

            # ==========================================
            # DYNAMIC LLM METRICS LOGGING (The Fix)
            # ==========================================
            try:
                # 1. Define the Context Map for your supported models
                CONTEXT_WINDOW_MAP = {
                    "local-llm": 8192,
                    "llama-3-8b": 8192,
                    "mistral-v0.3": 32768,
                    "qwen-2-7b": 32768,
                    "gpt-4o": 128000,
                    "gpt-4-turbo": 128000,
                    "gpt-3.5-turbo": 16384
                }
                
                # 2. Dynamically fetch the window size, defaulting to 8192 if unknown
                actual_max_window = CONTEXT_WINDOW_MAP.get(target_model, 8192)
                
                # 3. Calculate estimated tokens safely
                estimated_tokens = int(len(debug_sql_prompt.split()) * 1.3) if debug_sql_prompt else 0
                
                # 4. DIRECT CALL to the file dumper (bypassing the memory manager)
                # This guarantees the arguments reach the file dumper perfectly.
                await dump_conversation(
                    user_query=final_query,
                    ai_response=explanation,
                    full_prompt=debug_sql_prompt if debug_sql_prompt else "",
                    rag_context=clean_business_rules,
                    generated_sql=sql_result.sql if sql_result else "None",
                    execution_status=exec_status,
                    human_readable_prompt=debug_sql_prompt if debug_sql_prompt else "None",
                    llm_model_name=target_model,             
                    max_context_window=actual_max_window,   
                    estimated_tokens=estimated_tokens
                )
            except Exception as e:
                logger.error(f"Metrics Dumper failed: {e}")

            # ==========================================
            # RETURN FINAL RESPONSE
            # ==========================================
            log_event("info", "query_completed", strategy=sql_result.strategy, rows=execution.row_count)

            return QueryResponse(
                answer=explanation, 
                confidence=confidence, 
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