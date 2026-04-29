# app/services/grounded_synthesizer.py
from __future__ import annotations
import json
from typing import Any
import asyncio
from app.services.llm_client import call_llm
from app.services.confidence_checker import confidence_checker
from app.core.logging import get_logger

logger = get_logger(__name__)

class GroundedSynthesizer:
    """
    Produces answers strictly grounded in the database rows.
    Validates the answer against the data using Neuro-Symbolic verification.
    Uses a two-pass approach: generate → verify → fallback if hallucinated.
    """

    async def synthesize(
        self,
        question: str,
        rows: list[dict[str, Any]],
        row_count: int,
        executed_sql: str,
        metric_context: str = "",
    ) -> tuple[str, float]:
        if not rows:
            return "No matching records were found for your query.", 0.95

        data_preview = json.dumps(rows[:5], default=str)
        
        # Pass 1: Generate the standard business answer
        answer = await asyncio.to_thread(
            self._generate, question, data_preview, row_count, executed_sql, metric_context
        )

        # Pass 2: Verify answer mathematically using Neuro-Symbolic logic
        confidence = await confidence_checker.check_confidence(
            answer=answer, 
            context_rows=rows, 
            row_count=row_count
        )

        # Pass 3: If low confidence (hallucination detected), regenerate with a strict prompt
        if confidence < 0.6:
            logger.warning("Low grounding confidence (%.2f) — AI hallucinated. Regenerating strictly.", confidence)
            answer = await asyncio.to_thread(
                self._generate_strict, question, data_preview, row_count
            )

            # Re-verify the strict answer
            confidence = await confidence_checker.check_confidence(
                answer=answer, 
                context_rows=rows,
                row_count=row_count
            )

        return answer, confidence

    def _generate(
        self,
        question: str,
        data_preview: str,
        row_count: int,
        executed_sql: str,
        metric_context: str,
    ) -> str:
        prompt = f"""
You are a senior business analyst answering a manager's question.
Write a direct, professional, conversational answer using ONLY the data below.

STRICT RULES:
1. ONLY state numbers that can be derived directly from the data block below.
2. Format numbers nicely (e.g., $15,000 instead of 15000.0).
3. Do not use hyperbolic words like "significantly", "dramatically", "notably".
4. 2-3 sentences maximum. Write a natural language paragraph.
5. DO NOT output markdown tables under any circumstances.
6. Never mention SQL, databases, or technical terms.

METRIC CONTEXT: {metric_context}
QUESTION: {question}
SQL: {executed_sql}
ROWS: {row_count}
DATA: {data_preview}

Answer:""".strip()

        try:
            return call_llm(prompt=prompt, max_tokens=200, temperature=0.1)
        except Exception as e:
            logger.error("Synthesis failed: %s", e)
            return "I retrieved the data but encountered an error generating a summary. The raw result is shown below."

    def _generate_strict(
        self,
        question: str,
        data_preview: str,
        row_count: int,
    ) -> str:
        """Stricter fallback prompt for when the first generation hallucinates."""
        prompt = f"""
Extract and state the key number(s) from the data that answer the question.

Format: "The [metric] is [number]."
Write exactly 1 to 2 human-readable sentences.
DO NOT output markdown tables.
Absolutely nothing else. No adjectives.

QUESTION: {question}
DATA: {data_preview}

Answer:""".strip()

        try:
            return call_llm(prompt=prompt, max_tokens=100, temperature=0.0)
        except Exception:
            return f"The query returned {row_count} result(s). Please see the data below."