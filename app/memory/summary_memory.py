# AI_Knowledge_Assistant/app/memory/summary_memory.py
"""Summary memory to compress long-running conversations safely."""

from __future__ import annotations

from collections import OrderedDict

from app.utils.logger import get_logger

logger = get_logger(__name__)


class SummaryMemory:
    """Maintains compact, LRU-bounded session summaries for token-efficient prompting."""

    def __init__(self, max_sessions: int = 1000) -> None:
        self._max_sessions = max_sessions
        # OrderedDict allows us to efficiently track insertion/access order for LRU eviction
        self._summaries: OrderedDict[tuple[str, str], str] = OrderedDict()

    async def get_summary(self, user_id: str, session_id: str) -> str:
        """Return the current summary for a session and update its LRU position."""
        key = (user_id, session_id)
        if key in self._summaries:
            # Mark as recently used
            self._summaries.move_to_end(key)
            return self._summaries[key]
        return ""

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
        
        current = self._summaries.get(key, "")
        merged = self._merge_summary(current, new_fact)
        
        # Update the summary and mark as recently used
        self._summaries[key] = merged
        self._summaries.move_to_end(key)

        # Enforce memory boundary: drop the oldest inactive session
        if len(self._summaries) > self._max_sessions:
            evicted = self._summaries.popitem(last=False)
            logger.debug("Evicted oldest summary memory to stay under limit.")

        return merged

    @staticmethod
    def _compress_turn(question: str, answer: str) -> str:
        """Compress one conversation turn into a compact statement."""
        # Clean up whitespace and limit raw character counts per turn
        question_short = " ".join(question.strip().split())[:240]
        answer_short = " ".join(answer.strip().split())[:320]
        return f"User asked: {question_short} | Assistant answered: {answer_short}"

    @staticmethod
    def _merge_summary(previous: str, new_fact: str, max_chars: int = 1800) -> str:
        """
        Merge a new fact into summary while keeping bounded length.
        Safely drops the oldest complete lines to avoid fragmenting words.
        """
        if not previous:
            return new_fact
            
        combined = f"{previous}\n{new_fact}"
        
        # If it fits perfectly, return it
        if len(combined) <= max_chars:
            return combined

        # Otherwise, split into individual conversation turns (lines)
        lines = combined.split("\n")
        
        # Keep dropping the oldest line (at index 0) until we are under the limit
        while len("\n".join(lines)) > max_chars and len(lines) > 1:
            lines.pop(0)
            
        return "\n".join(lines)