#app/services/grounded_synthesizer.py
from __future__ import annotations
import json
import re
from typing import Any

from app.services.llm_client import call_llm
from app.core.logging import get_logger

logger = get_logger(__name__)

class GroundedSynthesizer:
    """
    Produces answers strictly grounded in the database rows.
    Validates the answer against the data before returning it.
    Uses a two-pass approach: generate → verify → fallback if hallucinated.
    """

    def synthesize(
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
        answer = self._generate(question, data_preview, row_count, executed_sql, metric_context)

        # Pass 2: Verify answer is grounded mathematically
        confidence = self._verify_grounding(answer, rows, data_preview)

        # Pass 3: If low confidence (hallucination detected), regenerate with a strict prompt
        if confidence < 0.6:
            logger.warning("Low grounding confidence (%.2f) — AI hallucinated. Regenerating strictly.", confidence)
            answer = self._generate_strict(question, data_preview, row_count, executed_sql)
            confidence = self._verify_grounding(answer, rows, data_preview)

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
        executed_sql: str,
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

    def _verify_grounding(
        self,
        answer: str,
        rows: list[dict[str, Any]],
        data_preview: str,
    ) -> float:
        """
        Check if the numbers in the answer actually appear in the data.
        Returns 0.0-1.0 confidence score.
        """
        def extract_floats(text: str) -> set[float]:
            # Remove commas and dollar signs used as formatting
            clean_text = text.replace(",", "").replace("$", "").replace("%", "")
            matches = re.findall(r'\b\d+(?:\.\d+)?\b', clean_text)

            floats = set()
            for m in matches:
                try:
                    floats.add(float(m))
                except ValueError:
                    pass
            return floats

        answer_numbers = extract_floats(answer)
        data_numbers = extract_floats(data_preview)

        # Remove small numbers (like years) from consideration as they skew the hallucination ratio
        answer_numbers = {n for n in answer_numbers if n > 2100 or n < 1900}
        data_numbers = {n for n in data_numbers if n > 2100 or n < 1900}

        if not answer_numbers:
            return 0.9

        # Check overlap mathematically
        matched = set()
        for a_num in answer_numbers:
            for d_num in data_numbers:
                # Allow for minor rounding differences (e.g. 15000 vs 15000.0)
                if abs(a_num - d_num) < 0.1:
                    matched.add(a_num)
                    break

        overlap_ratio = len(matched) / len(answer_numbers) if answer_numbers else 1.0

        hallucination_phrases = [
            "significantly higher", "dramatically lower", "strong growth",
            "substantial increase", "trend shows", "pattern indicates",
            "projected", "forecast", "expected to"
        ]
        has_hallucination_signal = any(p in answer.lower() for p in hallucination_phrases)

        score = overlap_ratio
        if has_hallucination_signal:
            score -= 0.3

        return max(0.0, min(1.0, score))