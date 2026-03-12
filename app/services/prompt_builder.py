# AI_Knowledge_Assistant/app/services/prompt_builder.py
"""Prompt templates for SQL mode and memory-augmented general answers."""

from __future__ import annotations

from typing import Iterable


from app.config import DB_TABLE
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _truncate(text: str, max_chars: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3].rstrip() + "..."


def _join_lines(lines: Iterable[str]) -> str:
    return "\n".join([line for line in lines if line.strip()])


def build_prompt(user_question: str, context: str, intent: str) -> str:
    """Backward compatible prompt builder for SQL/general flow."""
    if intent == "sql":
        logger.debug("Building SQL prompt")
        return _join_lines(
            [
                "You are an assistant that converts user questions into safe MySQL SELECT queries.",
                "",
                "Context:",
                _truncate(context, 7000),
                "",
                "Question:",
                user_question,
                "",
                "Instructions:",
                "- Return only one valid MySQL SELECT query.",
                "- Use only tables/columns from the provided context/schema.",
                f"- Use only this table: {DB_TABLE}.",
                "- Never use INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE.",
                "- Do not include explanations, markdown, or code fences.",
                "",
                "SQL:",
            ]
        )

    logger.debug("Building general prompt")
    return _join_lines(
        [
            "You are an AI assistant that helps answer user questions accurately.",
            "",
            "Context:",
            _truncate(context, 7000),
            "",
            "Question:",
            user_question,
            "",
            "Instructions:",
            "- Provide concise and clear answers.",
            "- If the answer is unknown, respond with 'I don't know'.",
            "",
            "Answer:",
        ]
    )


class PromptBuilder:
    """Token-aware prompt builder for hybrid memory context."""

    # pylint: disable=too-few-public-methods
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def build_memory_prompt(
        self,
        user_query: str,
        system_instructions: str,
        conversation_history: list[str],
        relevant_memories: list[str],
        summary_context: str,
        rag_context: str,
    ) -> str:
        """Build the final memory-aware prompt block for general QA fallback."""
        history_block = "\n".join(f"- {item}" for item in conversation_history[-8:])
        memory_block = "\n".join(f"- {item}" for item in relevant_memories[:10])

        return _join_lines(
            [
                "System Instructions:",
                _truncate(system_instructions, 3000),
                "",
                "Recent Conversation Window:",
                _truncate(history_block, 2500) or "- None",
                "",
                "Relevant Retrieved Memories:",
                _truncate(memory_block, 3000) or "- None",
                "",
                "Summary Memory:",
                _truncate(summary_context, 1800) or "None",
                "",
                "RAG Schema Context:",
                _truncate(rag_context, 2200) or "None",
                "",
                "User Query:",
                user_query,
                "",
                "Assistant Response:",
            ]
        )
