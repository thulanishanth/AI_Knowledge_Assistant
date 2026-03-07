# AI_Knowledge_Assistant/app/memory/context_aggregator.py
"""Context aggregation for final LLM prompt assembly."""

from __future__ import annotations


class ContextAggregator:  # pylint: disable=too-few-public-methods
    """Merges contexts in strict priority order for LLM usage."""


    def aggregate(
        self,
        vector_context: list[str],
        window_context: list[str],
        summary_context: str,
        rag_context: str,
    ) -> str:
        """Build a single prompt context string from memory and RAG sources."""
        sections: list[str] = []

        if vector_context:
            sections.append("Vector Memory:\n" + "\n".join(f"- {item}" for item in vector_context))
        if window_context:
            sections.append("Session Window:\n" + "\n".join(f"- {item}" for item in window_context))
        if summary_context.strip():
            sections.append("Summary Memory:\n" + summary_context.strip())
        if rag_context.strip():
            sections.append("RAG Schema Context:\n" + rag_context.strip())

        return "\n\n".join(sections).strip()
