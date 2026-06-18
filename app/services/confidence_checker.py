#app/services/confidence_checker.py
"""
Heuristic-based confidence scoring for LLM generation results.
Includes lexical context-grounding to detect hallucinations.
"""

import re
from typing import Set

from app.core.logging import get_logger

logger = get_logger(__name__)

def _extract_keywords(text: str) -> Set[str]:
    """Extract meaningful words (4+ characters) from a text string."""
    if not text:
        return set()
    # \b matches word boundaries, ensuring we only grab whole words
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    return set(words)

def check_confidence(answer: str, context: str) -> float:
    """
    Check the confidence of an answer by evaluating uncertainty phrases
    and measuring lexical overlap with the provided RAG context.

    Args:
        answer (str): The generated answer from the LLM.
        context (str): The aggregated memory/RAG context used to prompt the LLM.

    Returns:
        float: Confidence score between 0.0 and 1.0
    """
    if not answer:
        return 0.0
        
    answer_lower = answer.lower()
    
    # 1. Immediate failure states
    if answer_lower.startswith("error") or "system error" in answer_lower:
        return 0.0

    # 2. Detect explicit uncertainty (LLM admits it doesn't know)
    uncertainty_phrases = [
        "i don't know", "i am not sure", "cannot find", 
        "not mentioned", "no information", "i apologize",
        "not present in the context", "unable to provide"
    ]
    if any(phrase in answer_lower for phrase in uncertainty_phrases):
        logger.debug("Confidence lowered: Uncertainty phrase detected.")
        return 0.15

    # 3. Base confidence for a generated response
    base_confidence = 0.8

    # 4. Context Grounding (Hallucination check)
    # If the LLM is giving a factual answer, its vocabulary should overlap 
    # with the vector memory and schema context we provided it.
    if context and context.strip():
        ans_keywords = _extract_keywords(answer)
        ctx_keywords = _extract_keywords(context)
        
        if ans_keywords:
            # Calculate what percentage of the answer's words actually came from the context
            overlap = len(ans_keywords.intersection(ctx_keywords))
            overlap_ratio = overlap / len(ans_keywords)
            
            if overlap_ratio > 0.35:
                # Highly grounded in context
                base_confidence += 0.25
            elif overlap_ratio > 0.15:
                # Moderately grounded
                base_confidence += 0.15
            elif overlap_ratio < 0.05:
                # Potential hallucination: uses almost no words from the context
                logger.warning("Potential hallucination detected: Low context overlap.")
                base_confidence -= 0.30
        else:
            # Very short answers (e.g., "Yes.", "None.") get a slight bump
            base_confidence += 0.10

    # 5. SQL Keyword Detection (Safe Regex)
    sql_keywords = [r"\bselect\b", r"\bfrom\b", r"\bwhere\b", r"\bjoin\b"]
    if any(re.search(kw, answer_lower) for kw in sql_keywords):
        base_confidence += 0.30

    # Cap the final score between 0.0 and 1.0
    final_score = max(0.0, min(1.0, base_confidence))
    return round(final_score, 2)