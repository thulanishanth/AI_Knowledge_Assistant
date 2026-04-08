from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ResolvedBusinessTerm:
    user_term: str
    canonical_term: str
    sql_condition: str
    description: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BusinessResolutionResult:
    resolved_terms: list[ResolvedBusinessTerm] = field(default_factory=list)
    unresolved_terms: list[str] = field(default_factory=list)

    @property
    def has_unresolved_terms(self) -> bool:
        return bool(self.unresolved_terms)

    @property
    def has_resolved_terms(self) -> bool:
        return bool(self.resolved_terms)


_SQL_CONDITION_PATTERNS = [
    re.compile(r"SQL_CONDITION:\s*(.+)", re.IGNORECASE),
    re.compile(r"=>\s*(.+)"),
    re.compile(r"sql_condition['\"]?\s*[:=]\s*['\"]?(.+?)['\"]?$", re.IGNORECASE),
]


class BusinessLogicResolver:
    """
    Turn retrieved Chroma candidates into trusted, prompt-ready business mappings.
    """

    def __init__(self) -> None:
        self._exact_match_bonus = 0.25
        self._alias_match_bonus = 0.20
        self._text_contains_bonus = 0.10
        self._min_accept_score = 0.62
        self._min_semantic_only_score = 0.78

    def resolve(
        self,
        search_results: dict[str, list[dict[str, object]]],
    ) -> BusinessResolutionResult:
        resolved: list[ResolvedBusinessTerm] = []
        unresolved: list[str] = []

        for user_term, candidates in search_results.items():
            best = self._pick_best_candidate(user_term, candidates)
            if best is None:
                unresolved.append(user_term)
            else:
                resolved.append(best)

        return BusinessResolutionResult(
            resolved_terms=resolved,
            unresolved_terms=unresolved,
        )

    def _pick_best_candidate(
        self,
        user_term: str,
        candidates: list[dict[str, object]],
    ) -> ResolvedBusinessTerm | None:
        if not candidates:
            return None

        best_payload: ResolvedBusinessTerm | None = None
        best_score = -1.0
        user_term_lower = user_term.lower().strip()

        for candidate in candidates:
            text = str(candidate.get("text", "")).strip()
            metadata = candidate.get("metadata", {}) or {}
            vector_score = float(candidate.get("score", 0.0) or 0.0)

            canonical_term = str(
                metadata.get("canonical_term")
                or metadata.get("term")
                or metadata.get("matched_term")
                or user_term
            ).strip()

            aliases = self._extract_aliases(metadata, text)
            sql_condition = self._extract_sql_condition(metadata, text)
            description = str(metadata.get("description") or text).strip()
            source = str(metadata.get("source") or "business_logic_collection").strip()

            if not sql_condition:
                continue

            total_score = vector_score
            exact_or_alias = False

            if canonical_term.lower() == user_term_lower:
                total_score += self._exact_match_bonus
                exact_or_alias = True

            if user_term_lower in aliases:
                total_score += self._alias_match_bonus
                exact_or_alias = True

            if user_term_lower in text.lower():
                total_score += self._text_contains_bonus

            if exact_or_alias:
                accepted = total_score >= self._min_accept_score
            else:
                accepted = total_score >= self._min_semantic_only_score

            if not accepted:
                continue

            if total_score > best_score:
                best_score = total_score
                best_payload = ResolvedBusinessTerm(
                    user_term=user_term,
                    canonical_term=canonical_term or user_term,
                    sql_condition=sql_condition,
                    description=description,
                    source=source,
                    score=round(total_score, 4),
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )

        return best_payload

    @staticmethod
    def _extract_aliases(metadata: dict[str, Any], text: str) -> set[str]:
        aliases: set[str] = set()

        raw_aliases = metadata.get("aliases")
        if isinstance(raw_aliases, str):
            parts = re.split(r"[|,;]", raw_aliases)
            aliases.update(part.strip().lower() for part in parts if part.strip())

        canonical = metadata.get("canonical_term") or metadata.get("term")
        if canonical:
            aliases.add(str(canonical).strip().lower())

        text_lower = text.lower()
        alias_match = re.search(r"ALIASES:\s*(.+)", text, re.IGNORECASE)
        if alias_match:
            parts = re.split(r"[|,;]", alias_match.group(1))
            aliases.update(part.strip().lower() for part in parts if part.strip())

        for token in re.findall(r"\b[a-zA-Z0-9.\- ]{2,}\b", text_lower):
            token = token.strip()
            if len(token) >= 2:
                aliases.add(token)

        return aliases

    @staticmethod
    def _extract_sql_condition(metadata: dict[str, Any], text: str) -> str:
        raw = metadata.get("sql_condition")
        if raw:
            return str(raw).strip()

        for pattern in _SQL_CONDITION_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1).strip().rstrip(".")

        return ""