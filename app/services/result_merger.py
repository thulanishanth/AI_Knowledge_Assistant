# app/services/result_merger.py

from __future__ import annotations
from typing import Any
import json

from app.services.llm_client import call_llm

class ResultMerger:
    """
    Merges results from multiple sub-queries into a coherent answer.
    Uses the LLM for semantic merging, not just concatenation.
    """

    def merge(
        self,
        original_question: str,
        sub_results: list[dict[str, Any]],
        merge_strategy: str,
    ) -> str:
        """
        sub_results: [
            {"question": "Revenue 2018?", "sql": "...", "data": [...], "rows": 3},
            {"question": "Revenue 2019?", "sql": "...", "data": [...], "rows": 3},
        ]
        """
        if len(sub_results) == 1:
            # Not actually compound — return as-is
            return sub_results[0].get("synthesis", "")

        # Build context for synthesis
        results_context = []
        for i, result in enumerate(sub_results):
            results_context.append(
                f"Sub-question {i+1}: {result['question']}\n"
                f"Data: {json.dumps(result.get('data', [])[:5], default=str)}\n"
                f"Rows: {result.get('rows', 0)}"
            )

        prompt = self._build_merge_prompt(
            original_question, results_context, merge_strategy
        )

        try:
            return call_llm(prompt=prompt, max_tokens=300, temperature=0.3)
        except Exception:
            # Fallback: concatenate individual syntheses
            return "\n\n".join(
                r.get("synthesis", "") for r in sub_results if r.get("synthesis")
            )

    def _build_merge_prompt(
        self,
        original_question: str,
        results_context: list[str],
        strategy: str
    ) -> str:
        strategy_guide = {
            "compare": "Present results side-by-side. Highlight differences and percentage changes.",
            "join": "Combine the datasets by their shared dimension. Show combined view.",
            "append": "Present each result clearly, then summarize overall findings.",
        }.get(strategy, "Summarize all results coherently.")

        return f"""
You are a senior business analyst answering a complex multi-part question.

You have results from multiple database queries. Combine them into one coherent answer.

STRATEGY: {strategy_guide}

RULES:
- Lead with the most important finding
- Use business language, not technical terms
- Never mention SQL, queries, or databases
- Include specific numbers from the data
- 3-6 sentences maximum
- If one result is empty but others have data, note the absence specifically

ORIGINAL QUESTION: {original_question}

QUERY RESULTS:
{chr(10).join(results_context)}

Write your combined business analyst answer:
""".strip()