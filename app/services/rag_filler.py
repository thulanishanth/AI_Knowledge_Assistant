# app/services/rag_filler.py
"""Advanced Dynamic RAG Enricher and Formatter."""

from typing import Any, List
from app.core.logging import get_logger

logger = get_logger(__name__)

class DynamicRagFiller:
    """
    Takes raw vector database results, filters out chat history,
    and cleanly formats Business Logic, Constraints, and Examples for the LLM.
    """

    @staticmethod
    def build_business_context(vector_results: List[dict[str, Any]]) -> str:
        """Filters and formats ChromaDB results into a strict business rules block."""
        if not vector_results:
            return "(None retrieved)"

        # 1. STRICT FILTERING: Keep ONLY knowledge from the JSON (ignore user chat history)
        knowledge_only = []
        for item in vector_results:
            is_knowledge = (
                item.get("source") == "knowledge_memory" or 
                item.get("metadata", {}).get("memory_type") == "knowledge" or
                item.get("metadata", {}).get("source") == "db_context_script"
            )
            if is_knowledge:
                knowledge_only.append(item)

        if not knowledge_only:
            return "(None retrieved)"

        # 2. CATEGORIZATION: Group rules by their type for better LLM reasoning
        semantics = []
        constraints = []
        examples = []

        for item in knowledge_only:
            text = str(item.get("text", "")).strip()
            
            if text.startswith("SEMANTIC DEFINITION:"):
                semantics.append(text.replace("SEMANTIC DEFINITION:\n", "- "))
            elif text.startswith("BUSINESS CONSTRAINT:"):
                constraints.append(text.replace("BUSINESS CONSTRAINT:\n", "- "))
            elif text.startswith("SQL EXAMPLE:"):
                examples.append(text)
            else:
                constraints.append(f"- {text}")

        # 3. FORMATTING: Build the highly-readable block for the LLM
        sections = []
        
        if semantics:
            sections.append("SEMANTIC DEFINITIONS:\n" + "\n".join(semantics))
        if constraints:
            sections.append("BUSINESS CONSTRAINTS:\n" + "\n".join(constraints))
        if examples:
            sections.append("SQL EXAMPLES:\n" + "\n\n".join(examples))

        final_context = "\n\n".join(sections)
        
        logger.info(f"Advanced RAG Filler applied: {len(semantics)} Semantics, {len(constraints)} Constraints, {len(examples)} Examples.")
        return final_context

# Initialize a singleton for easy importing
rag_filler = DynamicRagFiller()