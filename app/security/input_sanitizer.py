# app/security/input_sanitizer.py
"""User-input sanitization helpers for chat requests."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.settings import settings

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FENCE_PATTERN = re.compile(r"```(?:\w+)?|```")


@dataclass(frozen=True, slots=True)
class SanitizedQuestion:
    raw: str
    normalized: str


def _strip_code_fences(text: str) -> str:
    return _FENCE_PATTERN.sub(" ", text or "")


def sanitize_question(text: str, max_chars: int | None = None) -> SanitizedQuestion:
    """Normalize whitespace, strip fences, and bound user input."""
    limit = max_chars or settings.max_question_chars
    raw = str(text or "")

    cleaned = _strip_code_fences(raw)
    cleaned = _CONTROL_CHARS.sub(" ", cleaned)
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    cleaned = " ".join(cleaned.split()).strip()

    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()

    return SanitizedQuestion(raw=raw, normalized=cleaned)


def normalize_role(role: str) -> str:
    """Normalize message role names for API and frontend consistency."""
    lowered = (role or "").strip().lower()
    if lowered in {"assistant", "bot", "ai", "system"}:
        return "assistant"
    return "user"