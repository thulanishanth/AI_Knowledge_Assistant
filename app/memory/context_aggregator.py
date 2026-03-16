# AI_Knowledge_Assistant/app/memory/context_aggregator.py
"""Context aggregation for final LLM prompt assembly."""

from __future__ import annotations
from typing import Sequence


class ContextAggregator:
    """Merges contexts in strict priority order for optimal LLM attention."""

    def aggregate(
        self,
        vector_context: Sequence[str] | None,
        window_context: Sequence[str] | None,
        summary_context: str | None,
        rag_context: str | None,
    ) -> str:
        """Build a single, clean prompt context string from memory and RAG sources."""
        sections: list[str] = []

        # 1. RAG Schema (Highest Priority Grounding: placed first)
        if rag_context and (clean_rag := rag_context.strip()):
            sections.append(f"RAG Schema Context:\n{clean_rag}")

        # 2. Summary Memory (Distant context)
        if summary_context and (clean_summary := summary_context.strip()):
            sections.append(f"Summary Memory:\n{clean_summary}")

        # 3. Vector Memory (Semantically retrieved related context)
        if vector_context:
            # Filter out empty strings or None values gracefully
            clean_vectors = [
                item.strip() for item in vector_context 
                if item and item.strip()
            ]
            if clean_vectors:
                sections.append(
                    "Vector Memory:\n" + "\n".join(f"- {item}" for item in clean_vectors)
                )

        # 4. Session Window (Immediate context: placed last so it sits right above the user prompt)
        if window_context:
            clean_window = [
                item.strip() for item in window_context 
                if item and item.strip()
            ]
            if clean_window:
                sections.append(
                    "Session Window:\n" + "\n".join(f"- {item}" for item in clean_window)
                )

        return "\n\n".join(sections)  