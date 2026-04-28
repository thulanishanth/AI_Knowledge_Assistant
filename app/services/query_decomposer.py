#app/services/query_decomposer.py
"""
Query decomposer — LLM-only, no regex compound signals.

The LLM reads the question and schema and decides whether multiple
SQL queries are needed. No hardcoded patterns.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.services.llm_client import call_llm_with_tool
from app.services.prompt_builder import PromptBuilder


@dataclass
class SubQuery:
    index: int
    question: str
    depends_on: list[int] = field(default_factory=list)
    result: Any = None
    sql: str = ""
    executed: bool = False


@dataclass
class DecompositionPlan:
    is_compound: bool
    sub_queries: list[SubQuery]
    merge_strategy: str  # "single" | "compare" | "join" | "append"
    reasoning: str


_DECOMPOSE_TOOL = {
    "name": "submit_decomposition",
    "description": "Submit the query decomposition decision.",
    "parameters": {
        "type": "object",
        "properties": {
            "is_compound": {"type": "boolean"},
            "reasoning": {"type": "string"},
            "merge_strategy": {
                "type": "string",
                "enum": ["single", "compare", "join", "append"],
            },
            "sub_queries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "question": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": ["index", "question"],
                },
            },
        },
        "required": ["is_compound", "sub_queries", "merge_strategy"],
    },
}


class QueryDecomposer:
    """
    Decides whether a question needs multiple SQL queries.
    Pure LLM decision — no regex pre-filter.
    """

    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._pb = prompt_builder

    def is_likely_compound(self, question: str) -> bool:
        """
        Always returns True — let the LLM decide.
        The LLM is fast with a schema-aware prompt and more accurate
        than any regex heuristic.
        """
        return True

    def decompose(
        self,
        question: str,
        schema_block: str,
        dialogue_state: str = "",
    ) -> DecompositionPlan:
        """
        Ask the LLM whether this question needs multiple sub-queries.
        Returns a DecompositionPlan with is_compound=False if single SQL suffices.
        """
        prompt = self._pb.build_decompose_prompt(question, schema_block, dialogue_state)

        try:
            parsed = call_llm_with_tool(
                prompt=prompt,
                tool_schema=_DECOMPOSE_TOOL,
                tool_name="submit_decomposition",
                max_tokens=500,
                temperature=0.0,
            )

            if not parsed or not parsed.get("is_compound"):
                return DecompositionPlan(
                    is_compound=False,
                    sub_queries=[SubQuery(index=0, question=question)],
                    merge_strategy="single",
                    reasoning=parsed.get("reasoning", "Single SQL is sufficient.") if parsed else "Single SQL.",
                )

            sub_queries = [
                SubQuery(
                    index=sq["index"],
                    question=sq["question"],
                    depends_on=sq.get("depends_on", []),
                )
                for sq in parsed.get("sub_queries", [])
            ]

            if not sub_queries:
                return DecompositionPlan(
                    is_compound=False,
                    sub_queries=[SubQuery(index=0, question=question)],
                    merge_strategy="single",
                    reasoning="No sub-queries returned — treating as single.",
                )

            return DecompositionPlan(
                is_compound=True,
                sub_queries=sub_queries,
                merge_strategy=parsed.get("merge_strategy", "append"),
                reasoning=parsed.get("reasoning", ""),
            )

        except Exception:
            return DecompositionPlan(
                is_compound=False,
                sub_queries=[SubQuery(index=0, question=question)],
                merge_strategy="single",
                reasoning="Decomposer error — treating as single query.",
            )