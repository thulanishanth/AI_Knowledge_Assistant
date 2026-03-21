#app/services/query_service.py
from __future__ import annotations

import asyncio
import re

from app.core.dependency_injection import container
from app.services.confidence_checker import check_confidence
from app.services.intent_service import IntentService
from app.services.llm_client import call_llm
from app.services.prompt_builder import build_prompt
from app.services.response_formatter import format_answer, format_sql_results
from app.services.rule_engine import apply_rules
from app.services.sql_execution_service import execute_safe_query
from app.security.sql_guard import validate_sql
from app.core.logging import get_logger

logger = get_logger(__name__)

LLM_BACKPRESSURE_MESSAGE = (
    "The AI service is currently busy. Please try again in a moment."
)


def _extract_sql(llm_output: str) -> str:
    if not llm_output:
        return ""
    text = llm_output.strip()
    fence = re.search(r"```(?:sql)?(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1)
    match = re.search(r"\bselect\b.*", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    sql = match.group(0).strip()
    if ";" in sql:
        sql = sql.split(";", 1)[0].strip()
    return f"{sql};"


def _is_greeting(text: str) -> bool:
    normalized = " ".join((text or "").strip().lower().split())
    return normalized in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}


def _build_general_prompt(refined_question: str, memory_context: dict[str, object]) -> str:
    return container.prompt_builder.build_memory_prompt(
        user_query=refined_question,
        system_instructions=(
            "You are a database assistant. Answer only with facts grounded in the provided context."
        ),
        conversation_history=[
            f"{message.get('role')}: {message.get('content')}"
            for message in memory_context.get("window_messages", [])
        ],
        relevant_memories=[
            str(item.get("text", ""))
            for item in memory_context.get("vector_results", [])
        ],
        summary_context=str(memory_context.get("summary", "")),
        rag_context=str(memory_context.get("rag_context", "")),
    )


async def handle_query(
    user_question: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
):
    """Use the new orchestrator when available, otherwise run the legacy-compatible flow."""
    if hasattr(container, "query_orchestrator"):
        return await container.query_orchestrator.handle_query(
            user_question=user_question,
            user_id=user_id,
            session_id=session_id,
        )

    if not user_question or not user_question.strip():
        raise ValueError("Question cannot be empty")

    session_ctx = container.session_manager.resolve(user_id=user_id, session_id=session_id)

    if _is_greeting(user_question):
        return "Hi! How can I help you today?", 1.0, session_ctx.session_id

    intent = IntentService.classify_legacy(user_question)
    refined_question = apply_rules(user_question, intent)
    memory_context = await container.memory_manager.get_context_for_llm(
        user_id=session_ctx.user_id,
        session_id=session_ctx.session_id,
        user_query=refined_question,
    )
    context = str(memory_context.get("aggregated_context", ""))

    if intent == "sql":
        sql_prompt = build_prompt(refined_question, context, "sql")
        sql_candidate = _extract_sql(await asyncio.to_thread(call_llm, sql_prompt))
        if validate_sql(sql_candidate):
            results = await asyncio.to_thread(execute_safe_query, sql_candidate)
            if isinstance(results, list) and results:
                answer = format_sql_results(results, user_question)
                confidence = 1.0
            else:
                answer = "No data found for your query."
                confidence = 0.85
        else:
            general_prompt = _build_general_prompt(refined_question, memory_context)
            try:
                llm_response = await asyncio.to_thread(call_llm, general_prompt)
                answer = llm_response
                confidence = check_confidence(llm_response, context)
            except Exception:
                answer = LLM_BACKPRESSURE_MESSAGE
                confidence = 0.0
        return format_answer(answer), confidence, session_ctx.session_id

    general_prompt = _build_general_prompt(refined_question, memory_context)
    try:
        llm_response = await asyncio.to_thread(call_llm, general_prompt)
        answer = llm_response
        confidence = check_confidence(llm_response, context)
    except Exception:
        answer = LLM_BACKPRESSURE_MESSAGE
        confidence = 0.0
    return format_answer(answer), confidence, session_ctx.session_id