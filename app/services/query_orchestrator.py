#app/services/query_orchestrator.py
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

# --- IMPORT FOR DYNAMIC SYNTHESIS ---
from app.services.llm_client import call_llm

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
    ) -> None:
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._schema_service = schema_service
        self._intent_service = intent_service
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
            
            # --- 1. Fetch Memory Context Early ---
            # We must do this first so the Intent LLM can read the history!
            session_context = ""
            try:
                memory_context = await self._memory_manager.get_context_for_llm(
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    user_query=sanitized.normalized,
                    include_vector=True, # Changed to True if you want cross-session memory!
                )
                session_context = str(memory_context.get("aggregated_context", ""))
            except Exception:
                logger.exception("Memory context load failed")

            # --- 2. Let the LLM figure out exactly what the user wants! ---
            analysis = await asyncio.to_thread(
                self._intent_service.analyze, 
                user_question=sanitized.normalized, 
                memory_context=session_context
            )
            
            intent = analysis.get("intent", "database_query")
            
            # --- 3. If the LLM realized they are asking about the structure/columns:
            if intent == "schema_inquiry":
                schema_answer = schema.to_prompt_block()
                return QueryResponse(
                    answer=schema_answer, confidence=1.0, session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": f"Schema for {schema.table_name}", "message": schema_answer},
                    meta={"strategy": "llm_intent_schema"},
                )

            # --- 4. Handle Chitchat / Greetings ---
            if intent in ["greeting", "general_chitchat"]:
                chat_answer = analysis.get("direct_response", "Hello! I am your AI Database Assistant. How can I help you with your data today?")
                
                # Save greeting to memory so the bot remembers saying hello
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
                    meta={"strategy": "intent_chitchat"},
                )

            # --- 5. Otherwise, it's a database_query, so proceed with SQL generation! ---
            final_query = analysis.get("corrected_query", sanitized.normalized)
            difficulty_score = analysis.get("difficulty_score", 50) # Default to 50 if missing
            
            is_cloud = False
            
            # --- MODEL CASCADING ROUTER ---
            if difficulty_score <= 50:
                target_model = "local-llm"
                is_cloud = False  
            elif difficulty_score <= 60:
                target_model = "gpt-4o-mini" # Cheap, extremely fast cloud reasoning
                is_cloud = True   
            elif difficulty_score <= 85:
                target_model = "gpt-4-turbo" # Heavy reasoning
                is_cloud = True
            else:
                target_model = "gpt-4o"      # Flagship Enterprise model
                is_cloud = True

            # Log the routing decision so you can see it in the terminal!
            logger.info(f"🔀 [MODEL ROUTER] Difficulty: {difficulty_score}/100 | Selected Model: {target_model}")

            sql_result = await self._sql_generation_service.generate_sql(
                question=final_query,
                schema=schema,
                session_context=session_context,
                model=target_model,
                is_cloud=is_cloud
            )

            # --- LOG LINE: TERMINAL OUTPUT FOR SQL ---
            logger.info(f"🚀 [LLM SQL]: {sql_result.sql}")

            # --- Security Check ---
            if not sql_result.is_valid:
                error_details = "; ".join(sql_result.validation.errors)
                if "Write operations are not allowed" in error_details or "Forbidden SQL keyword" in error_details:
                    safe_answer = "Security Alert: I am strictly prohibited from modifying or deleting data."
                else:
                    safe_answer = f"I could not safely process that request: {error_details}"

                if sql_result.notice:
                    safe_answer = f"{sql_result.notice}\n\n{safe_answer}"

                return QueryResponse(
                    answer=safe_answer, confidence=1.0, session_id=session_ctx.session_id,
                    presentation={"kind": "notice", "title": "Action Blocked", "message": safe_answer},
                    meta={"strategy": "security_blocked"},
                )

            # --- Execution ---
            cache_key = f"{schema.fingerprint}|{sql_result.sql}"
            execution = self._query_cache.get(cache_key)
            cached = execution is not None

            if execution is None:
                execution = self._sql_execution_service.execute(sql_result.sql)
                self._query_cache.set(cache_key, execution)

            # ==========================================
            # DYNAMIC SYNTHESIS
            # ==========================================
            explanation = "Here are the results I found for your query:"
            
            if execution.rows:
                try:
                    # Grab a small sample to show the LLM so we don't blow up the token limit
                    data_preview = json.dumps(execution.rows[:5], default=str)
                    
                    synthesis_prompt = (
                        f"You are a helpful, professional Hotel Data Assistant.\n"
                        f"The user asked: '{final_query}'\n"
                        f"The database returned this raw data: {data_preview}\n\n"
                        f"Write a friendly 1 to 2 sentence human explanation of this data answering the user's question. "
                        f"Do NOT write markdown tables. Do NOT mention JSON or raw SQL. Just speak naturally."
                    )
                    
                    # Call the Groq LLM to write the friendly human explanation
                    explanation = await asyncio.to_thread(
                        call_llm, 
                        prompt=synthesis_prompt, 
                        max_tokens=150, 
                        temperature=0.3
                    )
                except Exception as e:
                    logger.error(f"Synthesis failed: {e}")
            else:
                explanation = "I ran the query, but I couldn't find any data matching your request."
            # ==========================================

            if sql_result.notice:
                explanation = f"{sql_result.notice}\n\n{explanation}"

            # Format the Markdown Table using your existing formatter
            table_text, presentation = self._response_formatter.format_database_result(
                question=final_query,
                rows=execution.rows,
                truncated=execution.truncated,
            )

            # Glue the human explanation to the top of the markdown table!
            final_answer = explanation

            confidence = 0.95 if execution.row_count > 0 else 0.85

            try:
                await self._memory_manager.update_memory_pipeline(
                    user_id=session_ctx.user_id, session_id=session_ctx.session_id,
                    question=final_query, answer=final_answer,
                )
            except Exception:
                pass

            log_event("info", "query_completed", strategy=sql_result.strategy, rows=execution.row_count)

            return QueryResponse(
                answer=final_answer, 
                confidence=confidence, 
                session_id=session_ctx.session_id,
                presentation=presentation,
                meta={
                    "strategy": sql_result.strategy, "cached": cached,
                    "row_count": execution.row_count, "execution_ms": round(execution.execution_ms, 2),
                    "sql": execution.executed_sql,
                },
            )