#app/services/confidence_checker.py
"""
Heuristic confidence scoring for grounded answers.

This module estimates whether the final assistant answer is well-grounded
in the schema / retrieved context / execution result preview.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.core.logging import get_logger

logger = get_logger(__name__)

_WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_]{2,}\b")
_SQL_RE = re.compile(
    r"\b(select|from|where|group|order|limit|count|sum|avg|min|max)\b",
    re.IGNORECASE,
)


def _extract_keywords(text: str) -> set[str]:
    if not text:
        return set()

    words = {match.group(0).lower() for match in _WORD_RE.finditer(text)}
    stopwords = {
        "the", "and", "for", "with", "that", "this", "from", "into", "your",
        "have", "has", "had", "were", "was", "been", "are", "about", "using",
        "used", "will", "would", "could", "should", "there", "their", "them",
        "they", "than", "then", "what", "when", "which", "while", "where",
        "who", "how", "why", "does", "did", "done", "not", "found", "result",
        "results", "matching", "query", "table", "database", "data", "rows",
        "row", "value", "values", "record", "records",
    }
    return {word for word in words if word not in stopwords}


def _contains_uncertainty(answer_lower: str) -> bool:
    uncertainty_phrases = [
        "i don't know",
        "i am not sure",
        "i'm not sure",
        "cannot determine",
        "can't determine",
        "not enough information",
        "insufficient context",
        "unable to provide",
        "not present in the context",
        "could not find",
        "no information",
    ]
    return any(phrase in answer_lower for phrase in uncertainty_phrases)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _keyword_overlap(answer: str, context: str) -> float:
    answer_keywords = _extract_keywords(answer)
    context_keywords = _extract_keywords(context)

    if not answer_keywords:
        return 0.0

    overlap = len(answer_keywords.intersection(context_keywords))
    return _safe_ratio(overlap, len(answer_keywords))


def _has_structured_signal(texts: Iterable[str]) -> bool:
    merged = " ".join(texts).lower()
    return bool(_SQL_RE.search(merged))


def check_confidence(answer: str, context: str) -> float:
    """
    Return a confidence score from 0.0 to 1.0.

    High score:
    - answer is non-empty
    - no explicit uncertainty
    - answer vocabulary overlaps with grounding context
    - structured SQL/data terminology appears when appropriate
    """
    if not answer or not answer.strip():
        return 0.0

    answer = answer.strip()
    context = (context or "").strip()
    answer_lower = answer.lower()

    if answer_lower.startswith("error") or "internal server error" in answer_lower:
        return 0.0

    if _contains_uncertainty(answer_lower):
        logger.debug("Confidence lowered due to uncertainty phrase.")
        return 0.15

    score = 0.60

    overlap_ratio = _keyword_overlap(answer, context)
    if overlap_ratio >= 0.45:
        score += 0.28
    elif overlap_ratio >= 0.25:
        score += 0.18
    elif overlap_ratio >= 0.10:
        score += 0.08
    else:
        logger.warning("Low grounding overlap detected for generated answer.")
        score -= 0.18

    if _has_structured_signal([answer, context]):
        score += 0.08

    if len(answer.split()) <= 5:
        score -= 0.05

    final_score = max(0.0, min(1.0, score))
    return round(final_score, 2)