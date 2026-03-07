# AI_Knowledge_Assistant/app/services/query_service.py
"""End-to-end query pipeline orchestration for chat requests."""

from __future__ import annotations

import asyncio
import re

from app.config import DB_TABLE
from app.core.dependency_injection import container
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.observability.tracing import tracing
from app.services.confidence_checker import check_confidence
from app.services.formatter import format_answer, format_sql_results
from app.services.intent_classifier import classify_intent
from app.services.llm_client import call_llm
from app.services.prompt_builder import build_prompt
from app.services.rule_engine import apply_rules
from app.services.sql_executor import execute_safe_query
from app.services.sql_validator import validate_sql
from app.utils.logger import get_logger

logger = get_logger(__name__)
LLM_BACKPRESSURE_MESSAGE = (
    "I apologize, but my external AI language brain is currently experiencing "
    "high traffic or rate limits. Please wait a moment and try again!"
)

def _extract_sql(llm_output: str) -> str:
    """Extract a single SQL statement from model output."""
    if not llm_output:
        return ""

    text = llm_output.strip()

    fence_match = re.search(
        r"```(?:sql)?\s*(.*?)```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    select_match = re.search(
        r"(select\b.*?)(?:;|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if select_match:
        sql = select_match.group(1).strip()
        return f"{sql};"

    return text


def _is_greeting(text: str) -> bool:
    """Return `True` when input is a simple greeting."""
    normalized = " ".join((text or "").lower().strip().split())
    greetings = {
        "hi",
        "hii",
        "hiii",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
    }
    return normalized in greetings


def _build_general_prompt(
    refined_question: str,
    memory_context: dict[str, object],
) -> str:
    """Build general-answer prompt from memory manager context payload."""
    return container.prompt_builder.build_memory_prompt(
        user_query=refined_question,
        system_instructions=(
            "You are an enterprise AI assistant. "
            "Be concise, factual, and safe."
        ),
        conversation_history=[
            f"{message.get('role')}: {message.get('content')}"
            for message in memory_context.get("window_messages", [])
            if isinstance(message, dict)
        ],
        relevant_memories=[
            str(item.get("text", ""))
            for item in memory_context.get("vector_results", [])
            if isinstance(item, dict)
        ],
        summary_context=str(memory_context.get("summary", "")),
        rag_context=str(memory_context.get("rag_context", "")),
    )


def _empty_memory_context() -> dict[str, object]:
    """Return the default memory context shape used by the query pipeline."""
    return {
        "vector_results": [],
        "window_messages": [],
        "summary": "",
        "rag_context": "",
        "aggregated_context": "",
    }

# pylint: disable=too-many-locals,too-many-branches,too-many-statements
async def handle_query(
    user_question: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
) -> tuple[str, float, str]:
    """Handle one user question and return answer, confidence, and session ID."""
    if not user_question or not user_question.strip():
        raise ValueError("Question cannot be empty.")

    session_ctx = container.session_manager.resolve(
        user_id=user_id,
        session_id=session_id,
    )
    metrics.increment_requests("/api/chat")
    logger.info(
        "Starting query pipeline user_id=%s session_id=%s",
        session_ctx.user_id,
        session_ctx.session_id,
    )

    if _is_greeting(user_question):
        logger.info("Greeting detected, returning conversational response")
        return "Hi! How can I help you today?", 1.0, session_ctx.session_id

    with tracing.span("query.handle"):
        intent = classify_intent(user_question)
        logger.info("Intent detected: %s", intent)
        refined_question = apply_rules(user_question, intent)

        try:
            memory_context = await container.memory_manager.get_context_for_llm(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                user_query=refined_question,
            )
            logger.info("Hybrid memory context retrieved")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Hybrid memory context retrieval failed; continuing with empty context"
            )
            metrics.increment_errors("memory_context")
            log_event(
                "warning",
                "memory_context_unavailable",
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                error=str(exc).strip() or type(exc).__name__,
            )
            memory_context = _empty_memory_context()

        context = str(memory_context.get("aggregated_context", "") or "")

        sql_query = ""
        sql_is_valid = False
        if intent == "sql":
            try:
                sql_prompt = build_prompt(refined_question, context, "sql")
                with metrics.timer("llm_latency"):
                    sql_llm_response = await asyncio.to_thread(call_llm, sql_prompt)
                sql_query = _extract_sql(sql_llm_response)
                logger.info("SQL candidate generated: %s", sql_query)

                sql_is_valid = validate_sql(sql_query)
                if not sql_is_valid:
                    logger.warning(
                        "First SQL candidate failed validation; retrying SQL generation once"
                    )
                    retry_prompt = (
                        f"{sql_prompt}\n"
                        "Important:\n"
                        f"- Use only one table: {DB_TABLE}\n"
                        "- Return exactly one valid SELECT query and nothing else.\n"
                    )
                    with metrics.timer("llm_latency"):
                        retry_sql_response = await asyncio.to_thread(call_llm, retry_prompt)
                    sql_query = _extract_sql(retry_sql_response)
                    logger.info("Retry SQL candidate generated")
                    sql_is_valid = validate_sql(sql_query)
            except RuntimeError as exc:
                logger.error("SQL generation pipeline failed: %s", exc)
                metrics.increment_errors("sql_generation")
                log_event(
                    "warning",
                    "sql_generation_failed",
                    user_id=session_ctx.user_id,
                    session_id=session_ctx.session_id,
                    error=str(exc).strip() or type(exc).__name__,
                )
        else:
            logger.info("Intent=%s; skipping SQL generation stage", intent)

        if sql_is_valid:
            logger.info("SQL validated successfully")
            with metrics.timer("sql_execution_latency"):
                results = await asyncio.to_thread(execute_safe_query, sql_query)
            if isinstance(results, str):
                answer = results
                confidence = 0.0
            else:
                answer = format_sql_results(results, user_question)
                confidence = 1.0
        else:
            if intent == "sql":
                logger.warning(
                    "SQL generation/validation failed; falling back to general answer."
                )
            else:
                logger.info("Using general-answer stage for non-SQL intent")
            general_prompt = _build_general_prompt(refined_question, memory_context)
            try:
                with metrics.timer("llm_latency"):
                    llm_response = await asyncio.to_thread(call_llm, general_prompt)
                confidence = check_confidence(llm_response, context)
                answer = llm_response
            except RuntimeError as e:
                # Intercept the LLM failure here!
                logger.error("LLM Pipeline failed: %s", e)
                answer = LLM_BACKPRESSURE_MESSAGE
                confidence = 0.0

        answer = format_answer(answer)
        if not answer:
            raise RuntimeError("No answer generated by the pipeline.")

        try:
            await container.memory_manager.update_memory_pipeline(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                question=user_question,
                answer=answer,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Memory pipeline update failed; returning response without memory persistence"
            )
            metrics.increment_errors("memory_update")
            log_event(
                "warning",
                "memory_pipeline_update_failed",
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                error=str(exc).strip() or type(exc).__name__,
            )
        log_event(
            "info",
            "query_pipeline_completed",
            user_id=session_ctx.user_id,
            session_id=session_ctx.session_id,
            confidence=round(float(confidence), 3),
        )
        logger.info("Query pipeline completed")
        return answer, confidence, session_ctx.session_id
