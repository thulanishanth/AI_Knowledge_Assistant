# app/security/input_sanitizer.py
"""User-input sanitization helpers for chat requests."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.settings import settings

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

@dataclass(frozen=True, slots=True)
class SanitizedQuestion:
    raw: str
    normalized: str
    # Removed 'is_follow_up' because the LLM IntentService handles it now!

def sanitize_question(text: str, max_chars: int | None = None) -> SanitizedQuestion:
    """Normalize whitespace, strip fences, and bound user input."""
    limit = max_chars or settings.max_question_chars
    raw = str(text or "")
    cleaned = raw.replace("```sql", " ").replace("```", " ").replace("`", " ")
    cleaned = _CONTROL_CHARS.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()

    # The AI will handle follow-up detection later, so we just return the clean text
    return SanitizedQuestion(raw=raw, normalized=cleaned)

def normalize_role(role: str) -> str:
    """Normalize message role names for API and frontend consistency."""
    lowered = (role or "").strip().lower()
    if lowered in {"bot", "assistant"}:
        return "assistant"
    return "user"