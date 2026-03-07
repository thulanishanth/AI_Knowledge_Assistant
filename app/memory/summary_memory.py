# AI_Knowledge_Assistant/app/memory/summary_memory.py
"""Summary memory to compress long-running conversations."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class SummaryMemory:
    """Maintains compact session summaries for token-efficient prompting."""

    def __init__(self) -> None:
        self._summaries: dict[tuple[str, str], str] = defaultdict(str)
        self._lock = asyncio.Lock()

    async def get_summary(self, user_id: str, session_id: str) -> str:
        """Return the current summary for a session."""
        async with self._lock:
            return self._summaries.get((user_id, session_id), "")

    async def update_summary(
        self,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
    ) -> str:
        """Update and return the compressed rolling summary for a session."""
        key = (user_id, session_id)
        new_fact = self._compress_turn(question, answer)
        async with self._lock:
            current = self._summaries.get(key, "")
            merged = self._merge_summary(current, new_fact)
            self._summaries[key] = merged
            return merged

    @staticmethod
    def _compress_turn(question: str, answer: str) -> str:
        """Compress one conversation turn into a compact statement."""
        question_short = " ".join(question.strip().split())[:240]
        answer_short = " ".join(answer.strip().split())[:320]
        return f"User asked: {question_short} | Assistant answered: {answer_short}"

    @staticmethod
    def _merge_summary(previous: str, new_fact: str, max_chars: int = 1800) -> str:
        """Merge a new fact into summary while keeping bounded length."""
        if not previous:
            return new_fact
        combined = f"{previous}\n{new_fact}"
        if len(combined) <= max_chars:
            return combined
        return combined[-max_chars:]
