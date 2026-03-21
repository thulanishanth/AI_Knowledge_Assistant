#app/services/rag_filler.py
"""Adds supplemental context to RAG results from local reference files."""
from pathlib import Path
import re
from typing import List, Set

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SCHEMA_FILE = _DATA_DIR / "schema.txt"
_EXAMPLES_FILE = _DATA_DIR / "examples.txt"


def _read_text(path: Path) -> str:
    """Read UTF-8 text from disk; return empty string if unavailable."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("Unable to read filler file: %s", path)
        return ""


def _extract_keywords(question: str) -> List[str]:
    """Extract lightweight keywords from a user question for example filtering."""
    tokens = re.findall(r"[a-zA-Z_]{3,}", (question or "").lower())
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "show",
        "list",
        "give",
        "what",
        "when",
        "where",
        "which",
        "how",
        "many",
        "into",
        "about",
    }
    return [token for token in tokens if token not in stop_words]


def _extract_context_terms(base_context: str) -> Set[str]:
    """Extract DB/table/column-like terms from live context."""
    terms: Set[str] = set()
    terms.add(settings.db_table.lower())
    for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", (base_context or "").lower()):
        terms.add(token)
    return terms


def _is_schema_relevant(schema_text: str, base_context: str) -> bool:
    """Use reference schema only if it appears related to active table/live context."""
    if not schema_text:
        return False

    lowered = schema_text.lower()
    table_name = settings.db_table.lower()
    if re.search(rf"\bcreate\s+table\s+`?{re.escape(table_name)}`?\b", lowered):
        return True

    context_terms = _extract_context_terms(base_context)
    overlap = sum(1 for term in context_terms if term in lowered)
    return overlap >= 4


def _select_relevant_examples(
    question: str,
    raw_examples: str,
    base_context: str,
    limit: int = 3,
) -> str:
    """
    Keep only examples with strong overlap to question + live context.
    Do not include generic fallback examples to avoid contamination.
    """
    if not raw_examples:
        return ""

    blocks = [block.strip() for block in re.split(r"\n\s*\n", raw_examples) if block.strip()]
    if not blocks:
        return ""

    question_keywords = set(_extract_keywords(question))
    context_terms = _extract_context_terms(base_context)
    if not question_keywords:
        return ""

    scored = []
    for block in blocks:
        lowered = block.lower()
        question_hits = sum(1 for keyword in question_keywords if keyword in lowered)
        context_hits = sum(1 for term in context_terms if term in lowered)
        score = (2 * question_hits) + context_hits
        if question_hits >= 1 and context_hits >= 1 and score >= 4:
            scored.append((score, block))

    if not scored:
        return ""

    scored.sort(key=lambda item: item[0], reverse=True)
    return "\n\n".join(block for _, block in scored[:limit])


def fill_context(user_question: str, base_context: str) -> str:
    """
    Enrich base RAG context with local schema/examples references.
    This gives the LLM a fallback when live schema retrieval is partial.
    """
    schema_text = _read_text(_SCHEMA_FILE)
    examples_text = _read_text(_EXAMPLES_FILE)
    filtered_examples = _select_relevant_examples(user_question, examples_text, base_context)
    include_schema = _is_schema_relevant(schema_text, base_context)

    sections = []
    if base_context and base_context.strip():
        sections.append(f"Live Context:\n{base_context.strip()}")
    if include_schema:
        sections.append(f"Reference Schema:\n{schema_text}")
    if filtered_examples:
        sections.append(f"Reference Examples:\n{filtered_examples}")

    if not sections:
        return base_context or ""

    enriched = "\n\n".join(sections).strip()
    logger.info(
        "RAG filler applied (schema_included=%s, examples_included=%s)",
        include_schema,
        bool(filtered_examples),
    )
    return enriched
