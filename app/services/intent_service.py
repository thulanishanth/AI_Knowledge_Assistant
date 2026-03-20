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

_SCHEMA_TERMS = {
    "column",
    "columns",
    "coloumn",
    "coloumns",
    "field",
    "fields",
    "schema",
    "table structure",
    "database structure",
    "table schema",
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
    "latest",
    "last",
    "first",
    "top",
    "group by",
    "order by",
}

_DOMAIN_TERMS = {
    "booking",
    "bookings",
    "reservation",
    "reservations",
    "hotel",
    "room",
    "rooms",
    "guest",
    "guests",
    "meal",
    "parking",
    "arrival",
    "price",
    "status",
    "children",
    "adults",
    "lead time",
    "special requests",
}

_FOLLOW_UP_TERMS = {
    "it",
    "that",
    "those",
    "them",
    "same",
    "previous",
    "above",
    "again",
    "last",
    "these",
}

_SQL_PATTERN = re.compile(
    r"\bselect\b|\bfrom\b|\bwhere\b|\bgroup by\b|\border by\b|\bhaving\b|\blimit\b",
    re.IGNORECASE,
)

_AGGREGATE_PATTERN = re.compile(
    r"\bhow many\b|\bcount\b|\btotal\b|\baverage\b|\bavg\b|\bsum\b|\bminimum\b|\bmaximum\b|\bmin\b|\bmax\b",
    re.IGNORECASE,
)

_ROW_QUERY_PATTERN = re.compile(
    r"\bshow\b|\blist\b|\bdisplay\b|\bfind\b|\bget\b|\bretrieve\b|\bfirst\b|\blatest\b|\blast\b|\btop\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class IntentDecision:
    kind: str
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


class IntentService:
    """Classify questions as greeting, database_query, or unsupported."""

    def classify(
        self,
        question: str,
        schema: TableSchema | None = None,
        has_session_context: bool = False,
    ) -> IntentDecision:
        normalized = self._normalize(question)
        if not normalized:
            return IntentDecision("unsupported", 0.0, ("empty",))

        if normalized in _GREETING_TERMS:
            return IntentDecision("greeting", 1.0, ("greeting",))

        reasons: list[str] = []
        score = 0.0

        # 1. Direct SQL terms are a very strong signal.
        if _SQL_PATTERN.search(normalized):
            score += 0.9
            reasons.append("sql_terms")

        # 2. Schema requests should be treated as DB questions.
        if any(term in normalized for term in _SCHEMA_TERMS):
            score += 0.7
            reasons.append("schema_request")

        # 3. Generic DB action wording.
        action_hits = sum(1 for term in _DB_ACTION_TERMS if term in normalized)
        if action_hits:
            score += min(0.5, 0.17 * action_hits)
            reasons.append("db_action")

        # 4. Domain-specific wording.
        domain_hits = sum(1 for term in _DOMAIN_TERMS if term in normalized)
        if domain_hits:
            score += min(0.45, 0.14 * domain_hits)
            reasons.append("domain_terms")

        # 5. Aggregate and row-listing patterns.
        if _AGGREGATE_PATTERN.search(normalized):
            score += 0.3
            reasons.append("aggregate_query")

        if _ROW_QUERY_PATTERN.search(normalized):
            score += 0.22
            reasons.append("row_query")

        # 6. Numbers often indicate limits / filtering requests.
        if re.search(r"\b\d+\b", normalized):
            score += 0.08
            reasons.append("numeric_request")

        # 7. Schema-aware matching from live table metadata.
        if schema is not None:
            column_hits, sample_hits = self._score_schema_matches(normalized, schema)

            if column_hits:
                score += min(0.55, 0.15 * column_hits)
                reasons.append("schema_columns")

            if sample_hits:
                score += min(0.45, 0.12 * sample_hits)
                reasons.append("sample_values")

        # 8. Short follow-up questions can still be valid DB requests.
        if has_session_context and self._looks_like_follow_up(normalized):
            score += 0.2
            reasons.append("follow_up_context")

        final_reasons = tuple(dict.fromkeys(reasons))

        if score >= 0.45:
            return IntentDecision(
                kind="database_query",
                confidence=round(min(score, 1.0), 2),
                reasons=final_reasons,
            )

        return IntentDecision(
            kind="unsupported",
            confidence=round(max(score, 0.1), 2),
            reasons=final_reasons,
        )

    @staticmethod
    def classify_legacy(user_question: str) -> str:
        """
        Legacy helper for places that still expect 'sql' or 'general'.
        Use this temporarily during refactor, then remove it later.
        """
        decision = IntentService().classify(user_question)
        return "sql" if decision.kind == "database_query" else "general"

    @staticmethod
    def _normalize(question: str) -> str:
        return " ".join((question or "").strip().lower().split())

    @staticmethod
    def _looks_like_follow_up(normalized: str) -> bool:
        tokens = set(re.findall(r"[a-zA-Z_]+", normalized))
        if len(normalized.split()) <= 4:
            return True
        return bool(tokens.intersection(_FOLLOW_UP_TERMS))

    @staticmethod
    def _score_schema_matches(normalized: str, schema: TableSchema) -> tuple[int, int]:
        column_hits = 0
        sample_hits = 0

        for column in schema.columns:
            raw_name = column.name.lower()
            readable_name = raw_name.replace("_", " ")

            if raw_name in normalized or readable_name in normalized:
                column_hits += 1
                continue

            column_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", readable_name)
                if len(token) >= 3
            }
            if column_tokens and all(token in normalized for token in column_tokens):
                column_hits += 1

            for sample in column.sample_values:
                sample_text = sample.lower().strip()
                if sample_text and sample_text in normalized:
                    sample_hits += 1

        return column_hits, sample_hits