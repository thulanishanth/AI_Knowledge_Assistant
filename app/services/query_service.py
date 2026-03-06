# app/services/query_service.py
"""
Orchestrates the entire query processing pipeline from intent classification to execution.
"""
import re

from app.services.intent_classifier import classify_intent
from app.services.rule_engine import apply_rules
from app.services.rag_retriever import retrieve_context
from app.services.rag_filler import fill_context
from app.services.prompt_builder import build_prompt
from app.services.llm_client import call_llm  # HF API call
# Remove call_cloud_llm if not using separate cloud fallback
from app.services.confidence_checker import check_confidence
from app.services.sql_validator import validate_sql
from app.services.sql_executor import execute_safe_query
from app.services.formatter import format_answer, format_sql_results
from app.config import DB_TABLE
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _extract_sql(llm_output: str) -> str:
    if not llm_output:
        return ""

    text = llm_output.strip()

    fence_match = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    select_match = re.search(r"(select\b.*?)(?:;|$)", text, flags=re.IGNORECASE | re.DOTALL)
    if select_match:
        sql = select_match.group(1).strip()
        return f"{sql};"

    return text


def _is_greeting(text: str) -> bool:
    """
    Check if the provided text is a standard conversational greeting.
    """
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


def _is_smalltalk(text: str) -> bool:
    """
    Detect simple conversational phrases (including common typos) so they
    do not enter the SQL pipeline.
    """
    normalized = " ".join((text or "").lower().strip().split())
    normalized = re.sub(r"[^a-z0-9\s?]", "", normalized)

    patterns = (
        r"^how\s*are\s*(you|u)\??$",
        r"^how\s*r\s*(you|u)\??$",
        r"^hows\s*it\s*going\??$",
        r"^what\s*is\s*up\??$",
        r"^whats\s*up\??$",
    )
    return any(re.match(pattern, normalized) for pattern in patterns)


def _is_low_information_input(text: str) -> bool:
    """Detect very short/noisy input that should not trigger SQL."""
    normalized = re.sub(r"[^a-z0-9\s]", "", (text or "").lower()).strip()
    if not normalized:
        return True

    tokens = [tok for tok in normalized.split() if tok]
    if len(tokens) == 1 and len(tokens[0]) <= 2:
        return True
    return False


def _is_sensitive_input(text: str) -> bool:
    """Detect harmful/violent terms and avoid processing as data queries."""
    normalized = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    sensitive_terms = {
        "bomb",
        "explosive",
        "weapon",
        "attack",
        "kill",
        "harm",
        "terror",
    }
    return any(term in normalized.split() for term in sensitive_terms)


def _is_likely_db_question(text: str) -> bool:
    """Heuristic gate for whether a query is about the booking database."""
    lowered = (text or "").lower()

    db_terms = {
        DB_TABLE.lower(),
        "booking",
        "bookings",
        "reservation",
        "reservations",
        "customer",
        "customers",
        "guest",
        "guests",
        "room",
        "rooms",
        "cancel",
        "canceled",
        "not_canceled",
        "revenue",
        "occupancy",
        "arrival",
        "lead time",
        "meal plan",
        "database",
        "table",
        "records",
        "rows",
    }
    db_actions = {
        "how many",
        "count",
        "show",
        "list",
        "find",
        "top",
        "latest",
        "average",
        "total",
        "trend",
        "between",
    }

    has_term = any(term in lowered for term in db_terms)
    has_action = any(action in lowered for action in db_actions)
    return has_term or (has_term and has_action) or (has_action and "?" in lowered)


def _build_out_of_scope_response(user_question: str) -> str:
    """Return a polite response for non-database questions."""
    normalized = (user_question or "").strip()
    if normalized:
        return (
            "Sorry, I’m not able to help with that. "
            "I can help with booking-related questions, for example: "
            "'How many confirmed bookings do we have?'"
        )
    return (
        "Sorry, I’m not able to help with that. "
        "I can help with booking-related questions. "
        "Please ask a booking-related question."
    )


def _is_retryable_sql_error(error_text: str) -> bool:
    """Return True when SQL execution errors are likely fixable via a single rewrite."""
    if not error_text:
        return False

    lowered = error_text.lower()
    retryable_patterns = (
        "unknown column",
        "ambiguous column",
        "you have an error in your sql syntax",
        "in 'order clause'",
        "in 'field list'",
        "unknown table",
    )
    return lowered.startswith("error executing sql query:") and any(
        pattern in lowered for pattern in retryable_patterns
    )


def _repair_sql_query(
    user_question: str,
    context: str,
    failed_sql: str,
    sql_error: str,
) -> str:
    """Ask LLM to repair a failed SQL query using DB error feedback."""
    repair_prompt = f"""
You are fixing a MySQL SELECT query that failed at runtime.

Context:
{context}

User Question:
{user_question}

Failed SQL:
{failed_sql}

Database Error:
{sql_error}

Instructions:
- Return only one corrected MySQL SELECT query.
- Use only this table: {DB_TABLE}.
- Use only columns present in context.
- Do not use INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE.
- Do not include explanations, markdown, or code fences.

SQL:
"""
    return _extract_sql(call_llm(repair_prompt))

def handle_query(user_question: str):
    """Process the user question through the pipeline to generate an answer."""
    if not user_question or not user_question.strip():
        raise ValueError("Question cannot be empty.")

    logger.info("Starting query pipeline")

    if _is_greeting(user_question):
        logger.info("Greeting detected, returning conversational response")
        return "Hi! How can I help you today?", 1.0
    if _is_smalltalk(user_question):
        logger.info("Small-talk detected, returning conversational response")
        return "I'm doing well. How can I help you with your booking data?", 1.0
    if _is_sensitive_input(user_question):
        logger.info("Sensitive input detected, returning safe response")
        return (
            "I can only help with booking database questions.",
            0.1,
        )
    if _is_low_information_input(user_question):
        logger.info("Low-information input detected, asking for clarification")
        return (
            "Please ask a full question about booking data, for example: "
            "'How many confirmed bookings do we have?'",
            0.2,
        )

    # Step 1: Intent (kept for logging/rules)
    intent = classify_intent(user_question)
    logger.info("Intent detected: %s", intent)
    refined_question = apply_rules(user_question, intent)
    if intent != "sql" and not _is_likely_db_question(refined_question):
        logger.info("Out-of-scope question detected, skipping SQL pipeline")
        return _build_out_of_scope_response(user_question), 0.2

    # Step 2: Context / RAG
    context = retrieve_context(refined_question)
    context = fill_context(refined_question, context)
    logger.info("Context retrieved")

    # Step 3: SQL-first mode (always try DB execution first)
    sql_prompt = build_prompt(refined_question, context, "sql")
    sql_llm_response = call_llm(sql_prompt)
    sql_query = _extract_sql(sql_llm_response)
    logger.info("SQL candidate generated")

    if not validate_sql(sql_query):
        logger.warning("First SQL candidate failed validation; retrying SQL generation once")
        retry_prompt = (
            f"{sql_prompt}\n"
            "Important:\n"
            f"- Use only one table: {DB_TABLE}\n"
            "- Return exactly one valid SELECT query and nothing else.\n"
        )
        retry_sql_response = call_llm(retry_prompt)
        sql_query = _extract_sql(retry_sql_response)
        logger.info("Retry SQL candidate generated")

    if validate_sql(sql_query):
        logger.info("SQL validated successfully")
        results = execute_safe_query(sql_query)
        if isinstance(results, str):
            logger.warning("SQL execution failed: %s", results)
            if _is_retryable_sql_error(results):
                logger.info("Attempting one SQL repair pass using runtime error feedback")
                repaired_sql = _repair_sql_query(
                    user_question=refined_question,
                    context=context,
                    failed_sql=sql_query,
                    sql_error=results,
                )
                if validate_sql(repaired_sql):
                    repaired_results = execute_safe_query(repaired_sql)
                    if not isinstance(repaired_results, str):
                        answer = format_sql_results(repaired_results, user_question)
                        confidence = 1.0
                    else:
                        answer = repaired_results
                        confidence = 0.0
                else:
                    answer = results
                    confidence = 0.0
            else:
                # DB/runtime error text from executor
                answer = results
                confidence = 0.0
        else:
            answer = format_sql_results(results, user_question)
            confidence = 1.0
    else:
        # Step 4: Fallback to general answer if SQL generation fails
        logger.warning("SQL generation/validation failed; falling back to general answer.")
        general_prompt = build_prompt(refined_question, context, "general")
        llm_response = call_llm(general_prompt)
        confidence = check_confidence(llm_response, context)
        answer = llm_response

    # Step 5: Format final answer
    answer = format_answer(answer)

    if not answer:
        raise RuntimeError("No answer generated by the pipeline.")

    logger.info("Query pipeline completed")
    return answer, confidence
