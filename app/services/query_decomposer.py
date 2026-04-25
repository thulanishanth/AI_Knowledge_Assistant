# app/services/query_decomposer.py

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.llm_client import call_llm_with_tool
from app.services.prompt_builder import PromptBuilder

@dataclass
class SubQuery:
    index: int
    question: str
    depends_on: list[int] = field(default_factory=list)  # Dependency graph
    result: Any = None
    sql: str = ""
    executed: bool = False

@dataclass
class DecompositionPlan:
    is_compound: bool
    sub_queries: list[SubQuery]
    merge_strategy: str  # "join", "compare", "append", "single"
    reasoning: str

class QueryDecomposer:
    """
    Detects compound questions and breaks them into executable sub-queries.

    Examples of compound questions:
    - "Compare X and Y" → 2 sub-queries + comparison merge
    - "What is A and also B?" → 2 independent sub-queries
    - "Top 5 X by each Y" → potentially N sub-queries
    - "Revenue this month vs last month" → 2 time-scoped queries
    """

    COMPOUND_SIGNALS = [
        r"\bcompare\b", r"\bvs\b", r"\bversus\b",
        r"\band also\b", r"\bboth\b", r"\beach\b",
        r"\bbreak.*down\b", r"\bsplit.*by\b",
        r"\bfor each\b", r"\bper\b.*\bper\b",
        r"(\d{4}).*(\d{4})",   # Two years mentioned
        r"\bthis.*vs.*last\b", r"\bcurrent.*previous\b"
    ]

    DECOMPOSE_SCHEMA = {
        "name": "submit_decomposition",
        "description": "Submit the query decomposition plan.",
        "parameters": {
            "type": "object",
            "properties": {
                "is_compound": {"type": "boolean"},
                "reasoning": {"type": "string"},
                "merge_strategy": {
                    "type": "string",
                    "enum": ["single", "compare", "join", "append"]
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
                                "items": {"type": "integer"}
                            }
                        },
                        "required": ["index", "question"]
                    }
                }
            },
            "required": ["is_compound", "sub_queries", "merge_strategy"]
        }
    }

    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._pb = prompt_builder

    def is_likely_compound(self, question: str) -> bool:
        """Fast regex pre-filter before calling LLM."""
        q = question.lower()
        return any(re.search(sig, q) for sig in self.COMPOUND_SIGNALS)

    def decompose(
        self,
        question: str,
        schema_block: str,
        dialogue_state: str = "",
    ) -> DecompositionPlan:
        """
        Decompose a potentially compound question into sub-queries.
        Only calls LLM if fast pre-filter detects compound signals.
        """
        if not self.is_likely_compound(question):
            return DecompositionPlan(
                is_compound=False,
                sub_queries=[SubQuery(index=0, question=question)],
                merge_strategy="single",
                reasoning="No compound signals detected"
            )

        prompt = self._build_decompose_prompt(question, schema_block, dialogue_state)

        try:
            parsed = call_llm_with_tool(
                prompt=prompt,
                tool_schema=self.DECOMPOSE_SCHEMA,
                tool_name="submit_decomposition",
                max_tokens=500,
                temperature=0.0
            )

            if not parsed or not parsed.get("is_compound"):
                return DecompositionPlan(
                    is_compound=False,
                    sub_queries=[SubQuery(index=0, question=question)],
                    merge_strategy="single",
                    reasoning="LLM determined not compound"
                )

            sub_queries = [
                SubQuery(
                    index=sq["index"],
                    question=sq["question"],
                    depends_on=sq.get("depends_on", [])
                )
                for sq in parsed.get("sub_queries", [])
            ]

            return DecompositionPlan(
                is_compound=True,
                sub_queries=sub_queries,
                merge_strategy=parsed.get("merge_strategy", "append"),
                reasoning=parsed.get("reasoning", "")
            )

        except Exception:
            return DecompositionPlan(
                is_compound=False,
                sub_queries=[SubQuery(index=0, question=question)],
                merge_strategy="single",
                reasoning="Decomposer failed — treating as single query"
            )

    def _build_decompose_prompt(
        self, question: str, schema_block: str, dialogue_state: str
    ) -> str:
        return f"""
You are a query decomposition specialist for a business database assistant.

Analyze whether this question requires multiple database queries to answer correctly.

COMPOUND QUESTION TYPES:
- Comparison: "X vs Y", "compare A and B", "2018 vs 2019"
- Multi-metric: "revenue AND booking count by room type"
- Time series: "this month vs last month"
- Ranked per group: "top 3 for each room type"
- Dependent: query 2 uses results from query 1

RULES:
1. Only mark as compound if one SQL cannot correctly answer the full question.
2. Keep sub-questions explicit and self-contained (no pronouns).
3. Mark dependencies if sub-query 2 uses results from sub-query 1.
4. merge_strategy:
   - "single": one query handles it
   - "compare": side-by-side comparison (e.g., 2018 vs 2019)
   - "join": combine results by shared dimension
   - "append": independent results shown together

SCHEMA:
{schema_block}

Dialogue state:
{dialogue_state}

User question:
{question}
""".strip()