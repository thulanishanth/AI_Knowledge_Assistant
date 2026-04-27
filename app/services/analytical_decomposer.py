# app/services/analytical_decomposer.py
"""
Analytical Chain-of-Thought Decomposer.

When no business rule directly answers a question, this service:
1. Asks the LLM to think step-by-step about what sub-facts are needed.
2. Generates a SQL query for each atomic step.
3. Executes all steps and collects intermediate results.
4. Synthesizes a final grounded answer from all the pieces.

Example:
  Q: "How many bookings have more weekend nights than week nights?
      (Exclude any bookings that have 0 weekend nights)"

  Step 1 → COUNT where weekend > weeknight (all)         → 4775
  Step 2 → COUNT where weekend_nights = 0                → 1522
  Step 3 → Subtract: 4775 - 1522 = 3253  (or direct SQL)→ 3253
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.services.llm_client import call_llm, call_llm_with_tool

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReasoningStep:
    index: int
    description: str          # human-readable explanation of this step
    sql: str = ""             # SQL to execute for this step
    result: Any = None        # raw rows returned
    scalar: Any = None        # extracted single value if applicable
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


# ─────────────────────────────────────────────────────────────────────────────
# Signals that indicate a question likely needs multi-step reasoning
# ─────────────────────────────────────────────────────────────────────────────

_MULTI_STEP_SIGNALS = [
    r"\bexclude\b",
    r"\bexcluding\b",
    r"\bexcept\b",
    r"\bminus\b",
    r"\bsubtract\b",
    r"\bremove\b.*\bfrom\b",
    r"\bnot\s+including\b",
    r"\bapart\s+from\b",
    r"\bbut\s+not\b",
    r"\bwithout\b",
    r"\bonly\s+those\b",
    r"\bonly\s+where\b",
    r"\bafter\s+(removing|excluding|filtering)\b",
    r"\bwhat\s+(percentage|percent|fraction|ratio|share)\b.*\bof\b",
    r"\bcompare\b.*\band\b",
    r"\bdifference\s+between\b",
    r"\bhow\s+much\s+more\b",
    r"\bhow\s+much\s+less\b",
    r"\bbreak.*down\b",
    r"\bfor\s+each\b.*\band\s+also\b",
]

_MULTI_STEP_PATTERN = re.compile(
    "|".join(_MULTI_STEP_SIGNALS), re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool schema for step planning
# ─────────────────────────────────────────────────────────────────────────────

_PLAN_TOOL = {
    "name": "submit_reasoning_plan",
    "description": "Submit the step-by-step reasoning plan to answer the question.",
    "parameters": {
        "type": "object",
        "properties": {
            "needs_decomposition": {
                "type": "boolean",
                "description": "True if this question requires multiple SQL steps to answer correctly.",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of why decomposition is or is not needed.",
            },
            "steps": {
                "type": "array",
                "description": "Ordered list of atomic reasoning steps.",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "description": {
                            "type": "string",
                            "description": "What this step calculates in plain English.",
                        },
                        "sql": {
                            "type": "string",
                            "description": (
                                "A single complete SELECT SQL statement for this step. "
                                "Use results from previous steps by substituting their scalar value "
                                "as a literal in the WHERE clause where needed."
                            ),
                        },
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
    "description": "Submit the final synthesized answer from all step results.",
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": (
                    "A concise, professional business answer. "
                    "State the key number(s) first. "
                    "2-4 sentences max. No SQL, no technical terms."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "0.0–1.0 confidence that the answer is correct.",
            },
        },
        "required": ["answer", "confidence"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Main service
# ─────────────────────────────────────────────────────────────────────────────

class AnalyticalDecomposer:
    """
    Breaks complex analytical questions into atomic SQL steps and
    synthesizes a final grounded answer.
    """

    def is_candidate(self, question: str) -> bool:
        """Fast pre-filter: does this question show multi-step signals?"""
        return bool(_MULTI_STEP_PATTERN.search(question))

    async def plan(
        self,
        question: str,
        schema_block: str,
        business_rules: str = "",
        model: str = "Qwen/Qwen2.5-7B-Instruct",
    ) -> AnalyticalPlan:
        """
        Ask the LLM to produce a step-by-step reasoning plan.
        Returns an AnalyticalPlan with steps (SQL not yet executed).
        """
        prompt = self._build_plan_prompt(question, schema_block, business_rules)

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
        except Exception as e:
            logger.error("AnalyticalDecomposer planning failed: %s", e)
            return AnalyticalPlan(question=question, needs_decomposition=False)

        if not parsed:
            return AnalyticalPlan(question=question, needs_decomposition=False)

        steps = [
            ReasoningStep(
                index=s.get("index", i),
                description=s.get("description", f"Step {i+1}"),
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
        """
        Given all executed steps and their results, synthesize the final answer.
        Returns (answer_text, confidence).
        """
        prompt = self._build_synthesis_prompt(question, steps)

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
        except Exception as e:
            logger.error("AnalyticalDecomposer synthesis failed: %s", e)
            return self._fallback_answer(steps), 0.7

        if not parsed:
            return self._fallback_answer(steps), 0.7

        answer = parsed.get("answer", "").strip()
        confidence = float(parsed.get("confidence", 0.8))

        if not answer:
            return self._fallback_answer(steps), 0.7

        return answer, confidence

    # ──────────────────────────────────────────────────────────────────────
    # Prompt builders
    # ──────────────────────────────────────────────────────────────────────

    def _build_plan_prompt(
        self, question: str, schema_block: str, business_rules: str
    ) -> str:
        rules_block = business_rules.strip() or "(none)"
        return f"""
You are an expert data analyst planning how to answer a business question step-by-step.

Your goal: break the question into the MINIMUM number of atomic SQL steps (2–4 max)
that together build the complete answer. Each step must be a single, executable SELECT query.

WHEN TO DECOMPOSE:
- Question uses exclusion words: "exclude", "except", "without", "but not", "removing"
- Question asks for a ratio/percentage involving two different subsets
- Question compares two independent groups
- One calculation depends on the result of another

WHEN NOT TO DECOMPOSE (set needs_decomposition = false):
- A single WHERE clause can express the full filter
- It is a straightforward COUNT, SUM, or AVG with no dependencies

STEP RULES:
1. Each step SQL must be a complete, valid SELECT statement.
2. Steps run IN ORDER. If step 2 uses a value from step 1, embed that as a
   placeholder comment: -- USES_STEP_1_RESULT -- and include the logic directly
   in the WHERE clause (not as a subquery referencing a variable).
3. Keep each SQL focused on ONE thing.
4. Final step may do arithmetic combining previous results if needed.
5. LIMIT clause: only on row-returning queries, not aggregates.

SCHEMA:
{schema_block}

BUSINESS RULES:
{rules_block}

QUESTION: "{question}"

Think carefully: does this question TRULY need multiple SQL steps, or can one SQL
with the right WHERE clause answer it directly?

Respond using the submit_reasoning_plan tool.
""".strip()

    def _build_synthesis_prompt(
        self, question: str, steps: list[ReasoningStep]
    ) -> str:
        steps_block = "\n\n".join(
            f"Step {s.index + 1}: {s.description}\n"
            f"  SQL: {s.sql}\n"
            f"  Result: {json.dumps(s.result, default=str) if s.result else ('ERROR: ' + s.error)}"
            for s in steps
        )

        return f"""
You are a senior business analyst. Based on the step-by-step query results below,
write a clear, direct answer to the user's question.

RULES:
1. State the final number first.
2. Briefly explain how it was derived (1 sentence).
3. Use business language only — no SQL, no technical terms.
4. 2–3 sentences maximum.
5. If any step failed, say what could not be determined and why.
6. Format numbers with commas (e.g., 3,253 not 3253).

ORIGINAL QUESTION: "{question}"

REASONING STEPS AND RESULTS:
{steps_block}

Respond using the submit_final_answer tool.
""".strip()

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_scalar(rows: list[dict]) -> Any:
        """Pull a single numeric value from a one-row, one-column result."""
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
        """Build a plain fallback answer when synthesis LLM fails."""
        parts = []
        for s in steps:
            if s.result and not s.error:
                parts.append(f"{s.description}: {json.dumps(s.result, default=str)}")
        if parts:
            return "Here are the intermediate results:\n" + "\n".join(parts)
        return "I was unable to compute a complete answer for this question."