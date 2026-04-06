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
from app.services.response_formatter import ResponseFormatter
from app.services.schema_service import SchemaService
from app.services.sql_execution_service import QueryExecutionResult, SQLExecutionService
from app.services.sql_generation_service import SQLGenerationService
from app.services.intent_service import IntentService
from app.services.llm_client import call_llm
from app.services.prompt_builder import PromptBuilder

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
    ) -> None:
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._schema_service = schema_service
        self._intent_service = intent_service
        self._sql_generation_service = sql_generation_service
        self._sql_execution_service = sql_execution_service
        self._response_formatter = response_formatter
        self._prompt_builder = prompt_builder
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
            
            # ==========================================
            # 1. FETCH & UNPACK ALL MEMORY COMPONENTS
            # ==========================================
            session_context = ""
            debug_rag = ""
            debug_summary = ""
            debug_window = []
            debug_vector = []
            
            try:
                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    user_query=sanitized.normalized,
                    include_vector=True, 
                    rag_context=None # <--- CRITICAL: Must be None to trigger live DB schema fetch!
                )
                
                session_context = str(memory_context.get("aggregated_context", ""))
                debug_rag = memory_context.get("rag_context", "")
                debug_summary = memory_context.get("summary", "")
                debug_window = memory_context.get("window_messages", [])
                debug_vector = memory_context.get("vector_results", []) 
                
            except Exception:
                logger.exception("Memory context load failed")

            # --- 2. Let the LLM figure out exactly what the user wants! ---
            analysis = await asyncio.to_thread(
                self._intent_service.analyze, 
                user_question=sanitized.normalized, 
                memory_context=session_context
            )
            
            intent = analysis.get("intent", "database_query")
            
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

            # --- 3. Schema Inquiry ---
            if intent == "schema_inquiry":
                schema_answer = schema.to_prompt_block()
                return QueryResponse(
                    answer=schema_answer, confidence=1.0, session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": f"Schema for {schema.table_name}", "message": schema_answer},
                    meta=build_debug_meta("llm_intent_schema"),
                )
            
            # --- 4. Greeting, Chitchat, or Explanation ---
            if intent in ["greeting", "general_chitchat", "explanation"]:
                chat_answer = analysis.get("direct_response", "I'm sorry, I couldn't generate an explanation.")
                
                try:
                    await self._memory_manager.update_memory_pipeline(
                        user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                        question=sanitized.normalized, answer=chat_answer,
                    )
                except Exception:
                    pass
                    
                strategy_name = "intent_explanation" if intent == "explanation" else "intent_chitchat"
                
                return QueryResponse(
                    answer=chat_answer, confidence=1.0, session_id=session_ctx.session_id,
                    presentation={"kind": "text", "message": chat_answer},
                    meta=build_debug_meta(strategy_name),
                )

            # --- 5. Database Query Flow ---
            final_query = analysis.get("corrected_query", sanitized.normalized)
            difficulty_score = analysis.get("difficulty_score", 50) 
            
            if difficulty_score <= 50:
                target_model, is_cloud = "local-llm", False  
            elif difficulty_score <= 60:
                target_model, is_cloud = "gpt-4o-mini", True   
            elif difficulty_score <= 85:
                target_model, is_cloud = "gpt-4-turbo", True
            else:
                target_model, is_cloud = "gpt-4o", True 

            logger.info(f"🔀 [MODEL ROUTER] Difficulty: {difficulty_score}/100 | Selected Model: {target_model}")

            # ==========================================
            # FILTER CONTEXT: STRICTLY RAG KNOWLEDGE ONLY
            # ==========================================
            clean_business_rules = ""
            if debug_vector:
                knowledge_only = [
                    item for item in debug_vector 
                    if item.get("source") == "knowledge_memory"
                ]
                clean_business_rules = "\n".join([f"- {item.get('text', '')}" for item in knowledge_only])

            # ==========================================
            # SELF-HEALING SQL EXECUTION LOOP
            # ==========================================
            max_attempts = 3
            attempt = 1
            db_error_message = None
            previous_sql = None
            execution = None
            sql_result = None
            debug_sql_prompt = ""
            cached = False

            while attempt <= max_attempts:
                # 1. Inject Error Reflection if this is a retry attempt
                current_context = clean_business_rules
                if attempt > 1 and db_error_message and previous_sql:
                    logger.warning(f"🔄 [SELF-HEALING] Attempt {attempt}/{max_attempts} triggered to fix SQL error.")
                    reflection_msg = (
                        f"\n\nCRITICAL ERROR REFLECTION:\n"
                        f"Your previous SQL query:\n```sql\n{previous_sql}\n```\n"
                        f"Failed with the following database error:\n{db_error_message}\n\n"
                        f"Please analyze the schema carefully and write a corrected SQL query that fixes this exact error."
                    )
                    current_context += reflection_msg

                # 2. Build Prompt and Generate SQL
                debug_sql_prompt = self._prompt_builder.build_sql_prompt(
                question=final_query, schema=schema, session_context=clean_business_rules
            )

                sql_result = await self._sql_generation_service.generate_sql(
                question=final_query, schema=schema, session_context=clean_business_rules,
                model=target_model, is_cloud=is_cloud
            )
                logger.info(f"🚀 [LLM SQL] (Attempt {attempt}): {sql_result.sql}")

                # 3. Security Check (Break immediately if malicious, do NOT retry)
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

                # 4. Attempt to Execute the SQL
                cache_key = f"{schema.fingerprint}|{sql_result.sql}"
                execution = self._query_cache.get(cache_key)
                cached = execution is not None

                if execution is None:
                    try:
                        execution = self._sql_execution_service.execute(sql_result.sql)
                        if hasattr(execution, "error_message") and execution.error_message:
                            raise ValueError(execution.error_message)
                        # Success! Break the loop
                        break 
                    except Exception as e:
                        db_error_message = str(e)
                        previous_sql = sql_result.sql
                        logger.error(f"❌ SQL Execution Failed: {db_error_message}")
                        attempt += 1
                        continue 
                else:
                    break

            # ==========================================
            # ULTIMATE FAILURE FALLBACK
            # ==========================================
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

            # ==========================================
            # DYNAMIC SYNTHESIS
            # ==========================================
            explanation = "Here are the results I found for your query:"
            debug_synthesis_prompt = None
            
            if execution.rows:
                try:
                    data_preview = json.dumps(execution.rows[:5], default=str)
                    debug_synthesis_prompt = self._prompt_builder.build_synthesis_prompt(
                        question=final_query, data_preview=data_preview
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

            # --- UPDATE MEMORY PIPELINE FOR SUCCESSFUL QUERIES ---
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
                logger.error(f"CRITICAL: Memory pipeline failed to save to text files: {e}")

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