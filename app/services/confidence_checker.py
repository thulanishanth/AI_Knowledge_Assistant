# app/services/confidence_checker.py
"""
Neuro-Symbolic Confidence Checker.

Replaces brittle regex with deterministic mathematical verification.
1. Uses the LLM to extract numerical claims from its own answer.
2. Uses standard Python to verify if those numbers exist in the database rows.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.services.llm_client import call_llm_with_tool

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tool Schema for the LLM Claim Extractor
# ─────────────────────────────────────────────────────────────────────────────

_CLAIM_EXTRACTION_TOOL = {
    "name": "extract_numerical_claims",
    "description": "Extract all specific numbers, metrics, and dates claimed in the text.",
    "parameters": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "description": "List of every distinct number mentioned in the text.",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric_name": {
                            "type": "string",
                            "description": "What the number represents (e.g., 'total bookings', 'average price')."
                        },
                        "exact_value": {
                            "type": "number",
                            "description": "The exact numerical value (e.g., 6667, 87.41)."
                        }
                    },
                    "required": ["metric_name", "exact_value"]
                }
            }
        },
        "required": ["claims"]
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Main Service
# ─────────────────────────────────────────────────────────────────────────────

class ConfidenceChecker:
    """Evaluates LLM generation grounding using Neuro-Symbolic verification."""
    
    async def check_confidence(
        self, 
        answer: str, 
        context_rows: list[dict[str, Any]],
        row_count: int = 0
    ) -> float:
        """
        Check if the numbers in the answer mathematically exist in the database rows.
        """
        # 1. Immediate failure states (Fast-path exits)
        if not answer:
            return 0.0
            
        answer_lower = answer.lower()
        if answer_lower.startswith("error") or "system error" in answer_lower:
            return 0.0
            
        uncertainty_phrases = [
            "i don't know", "i am not sure", "cannot find", 
            "not mentioned", "no information", "i apologize",
            "not present in the context", "unable to provide"
        ]
        if any(phrase in answer_lower for phrase in uncertainty_phrases):
            logger.debug("Confidence lowered: Explicit uncertainty phrase detected.")
            return 0.15

        # 2. Ask the LLM to list the exact numbers it used in its answer
        prompt = f"""
        Extract every numerical claim made in this text. Include totals, averages, percentages, and counts.
        If a number is written as a word (e.g., 'five'), extract it as a digit (5).
        
        TEXT:
        "{answer}"
        """
        
        try:
            parsed = await asyncio.to_thread(
                call_llm_with_tool,
                prompt=prompt,
                tool_schema=_CLAIM_EXTRACTION_TOOL,
                tool_name="extract_numerical_claims",
                max_tokens=300,
                temperature=0.0  # Zero temperature for strict factual extraction
            )
            
            claims = parsed.get("claims", []) if parsed else []
            
        except Exception as e:
            logger.error("Claim extraction LLM call failed: %s", e)
            return 0.5  # Fallback if the LLM fails to extract

        # If the LLM didn't claim any hard numbers, it's a safe conceptual answer
        if not claims:
            return 1.0

        # 3. Extract all VALID numbers from the database payload using Python
        valid_numbers = set()
        
        # Always allow 0, the total row count, and the length of the data array
        valid_numbers.add(0.0)
        valid_numbers.add(float(row_count))
        valid_numbers.add(float(len(context_rows)))

        # Flatten all numbers from the actual database columns
        for row in context_rows:
            for value in row.values():
                if isinstance(value, (int, float)):
                    valid_numbers.add(float(value))
                elif isinstance(value, str) and value.replace(".", "", 1).isdigit():
                    try:
                        valid_numbers.add(float(value))
                    except ValueError:
                        pass

        # 4. Deterministic Verification (Symbolic Check)
        passed_claims = 0
        for claim in claims:
            claimed_value = float(claim.get("exact_value", 0))
            metric_name = claim.get("metric_name", "unknown")
            
            # Mathematical check: Does the claimed value exist in the valid DB numbers?
            # We use `abs(a - b) < 0.1` to safely handle minor floating point rounding (e.g., 87.41 vs 87.4)
            if any(abs(claimed_value - valid_num) < 0.1 for valid_num in valid_numbers):
                passed_claims += 1
            else:
                logger.warning("HALLUCINATION CAUGHT: Claimed value '%s' for '%s' is not in the database results.", claimed_value, metric_name)

        # 5. Calculate Final Score
        overlap_ratio = passed_claims / len(claims)
        
        # If any claim was completely fabricated, cap the score low to trigger the strict regenerator
        if overlap_ratio < 1.0:
            return min(overlap_ratio, 0.4)
            
        return 1.0

# Export singleton
confidence_checker = ConfidenceChecker()