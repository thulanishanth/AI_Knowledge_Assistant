# AI_Knowledge_Assistant/app/services/reranker_service.py
"""Re-ranking service to improve retrieval relevance."""

from __future__ import annotations

import re
from typing import Any


class RerankerService:  # pylint: disable=too-few-public-methods
    """Ranks retrieval candidates using lexical overlap and base score."""

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Return top candidates after combining base score and token overlap."""
        if not candidates:
            return []

        query_tokens = self._tokenize(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for candidate in candidates:
            text = str(candidate.get("text", ""))
            base_score = float(candidate.get("score", 0.0))
            token_overlap = self._jaccard_similarity(query_tokens, self._tokenize(text))
            score = (0.65 * base_score) + (0.35 * token_overlap)
            candidate["rerank_score"] = score
            scored.append((score, candidate))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [candidate for _, candidate in scored[:top_k]]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(token) > 1}

    @staticmethod
    def _jaccard_similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)
