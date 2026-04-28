# app/services/terminology_resolver.py
"""
Terminology Resolution — LLM-only, schema-grounded.

No hardcoded patterns, no domain-specific lists, no regex pre-filters.
The LLM reads the schema's [Values] lists and maps any user phrase
to the correct column/value. Works for any domain automatically.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.services.llm_client import call_llm_with_tool

logger = get_logger(__name__)

_RESOLVE_TOOL = {
    "name": "submit_term_resolution",
    "description": "Submit the resolution of domain terms found in the question.",
    "parameters": {
        "type": "object",
        "properties": {
            "needs_resolution": {
                "type": "boolean",
                "description": "True if the question has terms that must be mapped to column values.",
            },
            "terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw_term": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "category_value", "time_expression",
                                "computed_condition", "business_phrase",
                                "custom", "unknown",
                            ],
                        },
                        "meaning": {"type": "string"},
                        "sql_condition": {
                            "type": "string",
                            "description": (
                                "Exact SQL WHERE fragment using only schema columns. "
                                "E.g. \"status = 'Delivered'\" or "
                                "\"order_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)\". "
                                "Write UNKNOWN if genuinely unmappable."
                            ),
                        },
                        "confidence": {"type": "number"},
                        "needs_clarification": {"type": "boolean"},
                        "clarification_question": {"type": "string"},
                    },
                    "required": [
                        "raw_term", "category", "meaning", "sql_condition", "confidence",
                    ],
                },
            },
            "enriched_question": {
                "type": "string",
                "description": (
                    "Rewrite the original question replacing each vague term with its "
                    "explicit database meaning so SQL generation is unambiguous."
                ),
            },
        },
        "required": ["needs_resolution", "terms", "enriched_question"],
    },
}


@dataclass
class ResolvedTerm:
    raw_term: str
    category: str
    meaning: str
    sql_condition: str
    confidence: float = 0.8
    needs_clarification: bool = False
    clarification_question: str = ""


@dataclass
class TerminologyResolution:
    original_question: str
    resolved_terms: list[ResolvedTerm] = field(default_factory=list)
    enriched_question: str = ""
    sql_context_block: str = ""
    had_unknown_terms: bool = False
    needs_clarification: bool = False
    clarification_question: str = ""


class TerminologyResolver:
    """
    Schema-grounded term resolver.
    Asks the LLM to map user phrases to column values using the schema's [Values] lists.
    No hardcoded domain knowledge required.
    """

    async def resolve(
        self,
        question: str,
        schema_block: str,
        business_rules: str = "",
        model: str = "Qwen/Qwen2.5-7B-Instruct",
    ) -> TerminologyResolution:
        from app.services.prompt_builder import PromptBuilder
        prompt = PromptBuilder().build_term_resolution_prompt(
            question, schema_block, business_rules
        )

        try:
            parsed = await asyncio.to_thread(
                call_llm_with_tool,
                prompt,
                _RESOLVE_TOOL,
                "submit_term_resolution",
                600,
                2,
                0.0,
            )
        except Exception as exc:
            logger.error("TerminologyResolver LLM failed: %s", exc)
            return TerminologyResolution(
                original_question=question, enriched_question=question
            )

        if not parsed or not parsed.get("needs_resolution"):
            return TerminologyResolution(
                original_question=question,
                enriched_question=question,
                had_unknown_terms=False,
            )

        resolved_terms: list[ResolvedTerm] = []
        needs_clarification = False
        clarification_question = ""

        for t in parsed.get("terms", []):
            term = ResolvedTerm(
                raw_term=t.get("raw_term", ""),
                category=t.get("category", "custom"),
                meaning=t.get("meaning", ""),
                sql_condition=t.get("sql_condition", "UNKNOWN"),
                confidence=float(t.get("confidence", 0.5)),
                needs_clarification=bool(t.get("needs_clarification", False)),
                clarification_question=t.get("clarification_question", ""),
            )
            resolved_terms.append(term)
            if term.needs_clarification and not needs_clarification:
                needs_clarification = True
                clarification_question = term.clarification_question

        enriched_question = parsed.get("enriched_question", "").strip() or question
        sql_context_block = self._build_context_block(resolved_terms)

        logger.info(
            "TerminologyResolver: %d terms resolved, needs_clarification=%s",
            len(resolved_terms),
            needs_clarification,
        )

        return TerminologyResolution(
            original_question=question,
            resolved_terms=resolved_terms,
            enriched_question=enriched_question,
            sql_context_block=sql_context_block,
            had_unknown_terms=bool(resolved_terms),
            needs_clarification=needs_clarification,
            clarification_question=clarification_question,
        )

    @staticmethod
    def _build_context_block(resolved_terms: list[ResolvedTerm]) -> str:
        if not resolved_terms:
            return ""
        lines = ["TERM MAPPINGS (use these exact SQL conditions):"]
        for term in resolved_terms:
            if term.sql_condition and term.sql_condition.upper() != "UNKNOWN":
                conf_tag = f" [{term.confidence:.0%}]" if term.confidence < 0.8 else ""
                lines.append(
                    f'  "{term.raw_term}" → {term.meaning}'
                    f"\n    SQL condition: {term.sql_condition}{conf_tag}"
                )
        return "\n".join(lines) if len(lines) > 1 else ""

    def has_unknown_terms(self, question: str) -> bool:
        """
        Let the LLM decide — no regex pre-filter.
        Always attempt resolution; the LLM returns needs_resolution=false quickly
        when nothing needs mapping.
        """
        return True