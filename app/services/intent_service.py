"""Intent classification for a database-only assistant."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.infrastructure.repositories.schema_repository import TableSchema

_GREETING_TERMS = {
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
}
_DB_ACTION_TERMS = {
    "show",
    "list",
    "count",
    "how many",
    "total",
    "average",
    "avg",
    "sum",
    "minimum",
    "maximum",
    "min",
    "max",
    "find",
    "get",
    "retrieve",
    "display",
    "rows",
    "records",
    "bookings",
    "reservations",
}
_SCHEMA_TERMS = {
    "column",
    "columns",
    "coloumn",
    "coloumns",
    "field",
    "fields",
    "schema",
}


@dataclass(frozen=True, slots=True)
class IntentDecision:
    kind: str
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


class IntentService:
    """Classify questions as greeting, database query, or unsupported."""

    def classify(
        self,
        question: str,
        schema: TableSchema | None = None,
        has_session_context: bool = False,
    ) -> IntentDecision:
        normalized = " ".join((question or "").strip().lower().split())
        if not normalized:
            return IntentDecision("unsupported", 0.0, ("empty",))

        if normalized in _GREETING_TERMS:
            return IntentDecision("greeting", 1.0, ("greeting",))

        reasons: list[str] = []
        score = 0.0

        if re.search(r"\bselect\b|\bfrom\b|\bwhere\b", normalized):
            score += 0.8
            reasons.append("sql_terms")

        if any(term in normalized for term in _DB_ACTION_TERMS):
            score += 0.35
            reasons.append("db_action")

        if any(term in normalized for term in _SCHEMA_TERMS):
            score += 0.5
            reasons.append("schema_request")

        if schema is not None:
            column_hits = 0
            sample_hits = 0
            for column in schema.columns:
                readable_name = column.name.lower().replace("_", " ")
                if column.name.lower() in normalized or readable_name in normalized:
                    column_hits += 1
                for sample in column.sample_values:
                    if sample.lower() in normalized:
                        sample_hits += 1
            if column_hits:
                score += min(0.4, 0.15 * column_hits)
                reasons.append("schema_columns")
            if sample_hits:
                score += min(0.35, 0.1 * sample_hits)
                reasons.append("sample_values")

        if has_session_context and len(normalized.split()) <= 8:
            score += 0.2
            reasons.append("follow_up_context")

        if score >= 0.45:
            return IntentDecision("database_query", min(score, 1.0), tuple(reasons))

        return IntentDecision("unsupported", max(score, 0.1), tuple(reasons))
