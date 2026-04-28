#app/services/analytical_decomposer.py
"""
Analytical Chain-of-Thought Decomposer.

No regex pre-filter — the LLM decides whether multi-step is needed.
For any complex analytical question, the LLM plans the steps based on
the schema and question semantics.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.services.llm_client import call_llm_with_tool

logger = get_logger(__name__)


@dataclass
class ReasoningStep:
    index: int
    description: str
    sql: str = ""
    result: Any = None
    scalar: Any = None
    error: str = ""
    executed: bool = False


@dataclass
class AnalyticalPlan:
    question: str
    steps: list[ReasoningStep] = field(default_factory=list)
    needs_decomposition: bool = False
    reasoning: str = ""
    final_answer: str = ""
    confidence: float = 0.0


_PLAN_TOOL = {
    "name": "submit_reasoning_plan",
    "description": "Submit the step-by-step reasoning plan.",
    "parameters": {
        "type": "object",
        "properties": {
            "needs_decomposition": {
                "type": "boolean",
                "description": "True only if multiple SQL steps are genuinely required.",
            },
            "reasoning": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "description": {"type": "string"},
                        "sql": {"type": "string"},
                    },
                    "required": ["index", "description", "sql"],
                },
            },
        },
        "required": ["needs_decomposition", "steps"],
    },
}

_SYNTHESIS_TOOL = {
    "name": "submit_final_answer",
    "description": "Submit the synthesized answer from all step results.",
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer", "confidence"],
    },
}


class AnalyticalDecomposer:
    """
    Breaks complex questions into SQL steps and synthesizes a final answer.
    The LLM decides whether decomposition is needed — no regex gate.
    """

    def is_candidate(self, question: str) -> bool:
        """
        Always returns True — let the LLM decide.
        The LLM is faster and more accurate than regex at determining
        whether multi-step reasoning is needed.
        """
        return True

    async def plan(
        self,
        question: str,
        schema_block: str,
        business_rules: str = "",
        model: str = "Qwen/Qwen2.5-7B-Instruct",
    ) -> AnalyticalPlan:
        from app.services.prompt_builder import PromptBuilder
        prompt = PromptBuilder().build_analytical_plan_prompt(
            question, schema_block, business_rules
        )

        try:
            parsed = await asyncio.to_thread(
                call_llm_with_tool,
                prompt,
                _PLAN_TOOL,
                "submit_reasoning_plan",
                500,
                2,
                0.0,
            )
        except Exception as exc:
            logger.error("AnalyticalDecomposer planning failed: %s", exc)
            return AnalyticalPlan(question=question, needs_decomposition=False)

        if not parsed:
            return AnalyticalPlan(question=question, needs_decomposition=False)

        steps = [
            ReasoningStep(
                index=s.get("index", i),
                description=s.get("description", f"Step {i + 1}"),
                sql=s.get("sql", "").strip(),
            )
            for i, s in enumerate(parsed.get("steps", []))
            if s.get("sql", "").strip()
        ]

        return AnalyticalPlan(
            question=question,
            needs_decomposition=bool(parsed.get("needs_decomposition", False)),
            reasoning=parsed.get("reasoning", ""),
            steps=steps,
        )

    async def synthesize_final_answer(
        self,
        question: str,
        steps: list[ReasoningStep],
        model: str = "Qwen/Qwen2.5-7B-Instruct",
    ) -> tuple[str, float]:
        steps_block = "\n\n".join(
            f"Step {s.index + 1}: {s.description}\n"
            f"  SQL: {s.sql}\n"
            f"  Result: {json.dumps(s.result, default=str) if s.result else ('ERROR: ' + s.error)}"
            for s in steps
        )

        from app.services.prompt_builder import PromptBuilder
        prompt = PromptBuilder().build_analytical_synthesis_prompt(question, steps_block)

        try:
            parsed = await asyncio.to_thread(
                call_llm_with_tool,
                prompt,
                _SYNTHESIS_TOOL,
                "submit_final_answer",
                300,
                2,
                0.1,
            )
        except Exception as exc:
            logger.error("AnalyticalDecomposer synthesis failed: %s", exc)
            return self._fallback_answer(steps), 0.7

        if not parsed:
            return self._fallback_answer(steps), 0.7

        answer = parsed.get("answer", "").strip()
        confidence = float(parsed.get("confidence", 0.8))
        return answer or self._fallback_answer(steps), confidence

    @staticmethod
    def _extract_scalar(rows: list[dict]) -> Any:
        if not rows or len(rows) != 1:
            return None
        row = rows[0]
        if len(row) == 1:
            val = next(iter(row.values()))
            try:
                return float(val) if "." in str(val) else int(val)
            except (TypeError, ValueError):
                return val
        return None

    @staticmethod
    def _fallback_answer(steps: list[ReasoningStep]) -> str:
        parts = []
        for s in steps:
            if s.result and not s.error:
                parts.append(f"{s.description}: {json.dumps(s.result, default=str)}")
        if parts:
            return "Intermediate results:\n" + "\n".join(parts)
        return "Unable to compute a complete answer for this question."