# AI_Knowledge_Assistant/app/services/rule_engine.py
"""Rule-based preprocessing for user questions."""

from __future__ import annotations

import re

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_whitespace(text: str) -> str:
    """Collapse repeated whitespace into single spaces."""
    return " ".join(text.split())


def _remove_control_characters(text: str) -> str:
    """
    Remove non-printable control characters that may break prompts
    while preserving normal punctuation and spacing.
    """
    return re.sub(r"[\x00-\x1F\x7F]", " ", text)


def _strip_markdown_fences(text: str) -> str:
    """Remove simple markdown code fences/backticks from pasted input."""
    text = text.replace("```sql", " ")
    text = text.replace("```", " ")
    text = text.replace("`", " ")
    return text


def _clean_sql_intent_question(text: str) -> str:
    """
    Clean a natural-language question intended for SQL generation.

    Important:
    Do NOT append semicolons here.
    This function prepares the user's question for the LLM prompt,
    not the final SQL query itself.
    """
    text = _strip_markdown_fences(text)
    text = _remove_control_characters(text)
    text = _normalize_whitespace(text)

    # Remove trailing semicolons from natural-language questions.
    # Example: "show latest bookings;;;" -> "show latest bookings"
    text = re.sub(r";+\s*$", "", text).strip()

    return text


def _clean_general_question(text: str) -> str:
    """Clean a general user question while preserving meaning."""
    text = _strip_markdown_fences(text)
    text = _remove_control_characters(text)
    text = _normalize_whitespace(text)
    return text.strip()


def apply_rules(user_question: str, intent: str) -> str:
    """
    Apply rule-based cleanup to improve prompt quality.

    Args:
        user_question: Original user input.
        intent: Detected intent ('sql' or 'general').

    Returns:
        Cleaned question string ready for downstream prompt building.
    """
    if not isinstance(user_question, str):
        logger.warning(
            "apply_rules received non-string user_question of type %s",
            type(user_question).__name__,
        )
        return ""

    if not user_question.strip():
        return ""

    normalized_intent = (intent or "").strip().lower()
    raw_question = user_question.strip()

    if normalized_intent == "sql":
        refined_question = _clean_sql_intent_question(raw_question)
        logger.debug("Applied SQL-intent preprocessing rules")
        return refined_question

    if normalized_intent == "general":
        refined_question = _clean_general_question(raw_question)
        logger.debug("Applied general-intent preprocessing rules")
        return refined_question

    # Safe fallback for unknown intent labels
    logger.warning("Unknown intent '%s'. Falling back to general cleanup.", intent)
    return _clean_general_question(raw_question)