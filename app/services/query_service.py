"""Production-grade end-to-end query pipeline."""

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
    "The AI service is currently busy. Please try again in a moment."
)

# --------------------------------------------------
# SQL Extraction
# --------------------------------------------------

def _extract_sql(llm_output: str) -> str:
    """Extract SQL safely from LLM output."""

    if not llm_output:
        return ""

    text = llm_output.strip()

    # remove markdown
    fence = re.search(r"```(?:sql)?(.*?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1)

    # extract SELECT query
    match = re.search(r"\bselect\b.*?;", text, re.I | re.S)

    if match:
        return match.group(0).strip()

    match = re.search(r"\bselect\b.*", text, re.I | re.S)

    if match:
        return match.group(0).strip() + ";"

    return ""


# --------------------------------------------------
# Greeting detection
# --------------------------------------------------

def _is_greeting(text: str) -> bool:

    normalized = " ".join(text.lower().strip().split())

    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
    }

    return normalized in greetings


# --------------------------------------------------
# SQL Safety Override
# --------------------------------------------------

DB_DOMAIN_TERMS = [
    "booking",
    "reservation",
    "room",
    "guest",
]


def _force_sql_if_db_related(question: str, intent: str) -> str:
    """
    Safety override to prevent SQL misclassification.
    """

    q = question.lower()

    if intent == "general":
        if any(term in q for term in DB_DOMAIN_TERMS):
            logger.warning("SQL override triggered based on domain keywords")
            return "sql"

    return intent


# --------------------------------------------------
# General prompt
# --------------------------------------------------

def _build_general_prompt(refined_question, memory_context):

    return container.prompt_builder.build_memory_prompt(
        user_query=refined_question,
        system_instructions=(
            "You are an enterprise AI assistant. "
            "Be factual and do not invent database facts."
        ),
        conversation_history=[
            f"{m.get('role')}: {m.get('content')}"
            for m in memory_context.get("window_messages", [])
        ],
        relevant_memories=[
            str(x.get("text", ""))
            for x in memory_context.get("vector_results", [])
        ],
        summary_context=str(memory_context.get("summary", "")),
        rag_context=str(memory_context.get("rag_context", "")),
    )


# --------------------------------------------------
# Main Pipeline
# --------------------------------------------------

async def handle_query(
    user_question: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
):

    if not user_question or not user_question.strip():
        raise ValueError("Question cannot be empty")

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
        return "Hi! How can I help you today?", 1.0, session_ctx.session_id

    with tracing.span("query.handle"):

        # ---------------------------
        # Intent detection
        # ---------------------------

        intent = classify_intent(user_question)
        intent = _force_sql_if_db_related(user_question, intent)

        logger.info("Intent detected: %s", intent)

        refined_question = apply_rules(user_question, intent)

        # ---------------------------
        # Memory retrieval
        # ---------------------------

        try:

            memory_context = await container.memory_manager.get_context_for_llm(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                user_query=refined_question,
            )

        except Exception:

            logger.exception("Memory retrieval failed")

            memory_context = {
                "vector_results": [],
                "window_messages": [],
                "summary": "",
                "rag_context": "",
                "aggregated_context": "",
            }

        context = str(memory_context.get("aggregated_context", ""))

        # ---------------------------
        # SQL generation
        # ---------------------------

        sql_query = ""
        sql_is_valid = False

        if intent == "sql":

            try:

                sql_prompt = build_prompt(refined_question, context, "sql")

                sql_llm_output = await asyncio.to_thread(call_llm, sql_prompt)

                sql_query = _extract_sql(sql_llm_output)

                logger.info("SQL candidate: %s", sql_query)

                sql_is_valid = validate_sql(sql_query)

                # retry once if invalid
                if not sql_is_valid:

                    retry_prompt = f"""
Generate a valid MySQL SELECT query.

Rules:
- Use table: {DB_TABLE}
- Do not invent columns
- Return ONLY SQL
"""

                    retry_output = await asyncio.to_thread(call_llm, retry_prompt)

                    sql_query = _extract_sql(retry_output)

                    sql_is_valid = validate_sql(sql_query)

            except Exception:

                logger.exception("SQL generation failed")

        # ---------------------------
        # SQL execution
        # ---------------------------

        if sql_is_valid:

            logger.info("SQL validated successfully")

            results = await asyncio.to_thread(execute_safe_query, sql_query)

            if not results:

                answer = "No data found for your query."
                confidence = 0.85

            else:

                answer = await asyncio.to_thread(
                    synthesize_natural_response,
                    user_question,
                    results,
                )

                confidence = 0.95

        else:

            logger.warning("Falling back to general pipeline")

            general_prompt = _build_general_prompt(
                refined_question,
                memory_context,
            )

            try:

                llm_response = await asyncio.to_thread(call_llm, general_prompt)

                answer = llm_response

                confidence = check_confidence(llm_response, context)

            except Exception:

                answer = LLM_BACKPRESSURE_MESSAGE
                confidence = 0.0

        answer = format_answer(answer)

        # ---------------------------
        # Memory update
        # ---------------------------

        try:

            await container.memory_manager.update_memory_pipeline(
                user_id=session_ctx.user_id,
                session_id=session_ctx.session_id,
                question=user_question,
                answer=answer,
            )

        except Exception:

            logger.exception("Memory update failed")

        log_event(
            "info",
            "query_pipeline_completed",
            user_id=session_ctx.user_id,
            session_id=session_ctx.session_id,
            confidence=confidence,
        )

        logger.info("Query pipeline completed")

        return answer, confidence, session_ctx.session_id


# --------------------------------------------------
# SQL answer synthesis
# --------------------------------------------------

def synthesize_natural_response(user_question, db_result):

    prompt = f"""
Answer the question using ONLY the provided database result.

Question:
{user_question}

Database Result:
{db_result}

Write a natural answer.
"""

    try:

        return call_llm(prompt, max_tokens=80).strip()

    except Exception:

        return format_sql_results(db_result, user_question)