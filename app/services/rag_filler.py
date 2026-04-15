#app/services/rag_filler.py
"""Dynamic RAG formatter for knowledge memory + business logic memory."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class DynamicRagFiller:
    """
    Formats retrieved Chroma results into a clean prompt block for SQL generation.

    It supports:
    - general knowledge memory
    - approved business-logic memory
    - resolved business-term mappings
    """

    @staticmethod
    def build_prompt_context(
        vector_results: list[dict[str, Any]] | None = None,
        business_search_results: dict[str, list[dict[str, Any]]] | None = None,
        resolution_result: Any | None = None,
    ) -> str:
        vector_results = vector_results or []
        business_search_results = business_search_results or {}

        knowledge_block = DynamicRagFiller._build_knowledge_context(vector_results)
        resolved_business_block = DynamicRagFiller._build_resolved_business_block(
            resolution_result
        )
        retrieved_business_block = DynamicRagFiller._build_retrieved_business_block(
            business_search_results,
            resolution_result,
        )

        sections = [
            block
            for block in (
                knowledge_block,
                resolved_business_block,
                retrieved_business_block,
            )
            if block and block.strip() and block.strip() != "(None retrieved)"
        ]

        final_context = "\n\n".join(sections).strip()
        return final_context or "(None retrieved)"

    @staticmethod
    def _build_knowledge_context(vector_results: list[dict[str, Any]]) -> str:
        if not vector_results:
            return ""

        knowledge_only: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in vector_results:
            metadata = item.get("metadata", {}) or {}
            text = str(item.get("text", "")).strip()
            if not text or text in seen:
                continue

            is_knowledge = (
                item.get("source") == "knowledge_memory"
                or metadata.get("memory_type") == "knowledge"
                or metadata.get("source") == "db_context_script"
            )
            if is_knowledge:
                seen.add(text)
                knowledge_only.append(item)

        if not knowledge_only:
            return ""

        semantics: list[str] = []
        constraints: list[str] = []
        examples: list[str] = []
        other: list[str] = []

        for item in knowledge_only:
            text = str(item.get("text", "")).strip()

            if text.startswith("SEMANTIC DEFINITION:"):
                semantics.append(text.replace("SEMANTIC DEFINITION:\n", "- ", 1))
            elif text.startswith("BUSINESS CONSTRAINT:"):
                constraints.append(text.replace("BUSINESS CONSTRAINT:\n", "- ", 1))
            elif text.startswith("SQL EXAMPLE:"):
                examples.append(text.replace("SQL EXAMPLE:\n", "", 1).strip())
            else:
                other.append(f"- {text}")

        sections: list[str] = []

        if semantics:
            sections.append("SEMANTIC DEFINITIONS:\n" + "\n".join(semantics[:15]))
        if constraints:
            sections.append("BUSINESS CONSTRAINTS:\n" + "\n".join(constraints[:12]))
        if examples:
            sections.append("SQL EXAMPLES:\n" + "\n\n".join(examples[:6]))
        if other:
            sections.append("ADDITIONAL KNOWLEDGE:\n" + "\n".join(other[:8]))

        logger.info(
            "RAG filler knowledge formatting applied: semantics=%s constraints=%s examples=%s other=%s",
            len(semantics),
            len(constraints),
            len(examples),
            len(other),
        )
        return "\n\n".join(sections).strip()

    @staticmethod
    def _build_resolved_business_block(resolution_result: Any | None) -> str:
        if resolution_result is None:
            return ""

        resolved_terms = getattr(resolution_result, "resolved_terms", []) or []
        if not resolved_terms:
            return ""

        lines = ["RESOLVED BUSINESS LOGIC:"]
        seen: set[str] = set()

        for item in resolved_terms:
            canonical_term = str(getattr(item, "canonical_term", "") or "").strip()
            user_term = str(getattr(item, "user_term", "") or "").strip()
            sql_condition = str(getattr(item, "sql_condition", "") or "").strip()
            description = str(getattr(item, "description", "") or "").strip()

            if not sql_condition:
                continue

            key = f"{canonical_term}|{sql_condition}"
            if key in seen:
                continue
            seen.add(key)

            if description:
                lines.append(
                    f"- {user_term} => {sql_condition} "
                    f"(approved term: {canonical_term}; description: {description})"
                )
            else:
                lines.append(
                    f"- {user_term} => {sql_condition} "
                    f"(approved term: {canonical_term})"
                )

        return "\n".join(lines).strip() if len(lines) > 1 else ""

    @staticmethod
    def _build_retrieved_business_block(
        business_search_results: dict[str, list[dict[str, Any]]],
        resolution_result: Any | None,
    ) -> str:
        if not business_search_results:
            return ""

        resolved_conditions = set()
        if resolution_result is not None:
            for item in getattr(resolution_result, "resolved_terms", []) or []:
                resolved_conditions.add(
                    str(getattr(item, "sql_condition", "") or "").strip()
                )

        lines = ["RETRIEVED APPROVED BUSINESS MATCHES:"]
        seen: set[str] = set()

        for user_term, matches in business_search_results.items():
            for match in matches[:3]:
                text = str(match.get("text", "")).strip()
                metadata = match.get("metadata", {}) or {}
                score = match.get("score", 0.0)

                sql_condition = str(metadata.get("sql_condition", "")).strip()
                canonical_term = str(
                    metadata.get("canonical_term")
                    or metadata.get("term")
                    or user_term
                ).strip()

                if not sql_condition:
                    continue

                if resolved_conditions and sql_condition not in resolved_conditions:
                    continue

                key = f"{canonical_term}|{sql_condition}"
                if key in seen:
                    continue
                seen.add(key)

                lines.append(
                    f"- query term={user_term}; matched term={canonical_term}; "
                    f"sql_condition={sql_condition}; score={score}"
                )

                if text:
                    lines.append(f"  evidence: {text[:250]}")

        return "\n".join(lines).strip() if len(lines) > 1 else ""


rag_filler = DynamicRagFiller()

