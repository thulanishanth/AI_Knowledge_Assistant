#app/services/reranker_service.py
"""Re-ranking service to improve retrieval relevance using Cross-Encoders."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Safely handle optional dependencies
try:
    from sentence_transformers import CrossEncoder  # type: ignore
except ImportError:
    CrossEncoder = None


class RerankerService:
    """Ranks retrieval candidates using a semantic Cross-Encoder model."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        """Initialize the reranker with a highly optimized sentence-transformers model."""
        self._model_name = model_name
        self._model: Any | None = None
        
        # Concurrency lock to prevent multiple simultaneous model loads into RAM
        self._model_lock = asyncio.Lock()

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Return top candidates by passing query-document pairs through a Cross-Encoder."""
        if not candidates:
            return []

        # 1. Fallback gracefully if the library is missing
        if CrossEncoder is None:
            logger.warning("sentence-transformers not installed. Skipping semantic reranking.")
            return self._fallback_rank(candidates, top_k)

        # 2. Thread-safe Lazy Loading of the ML Model
        if self._model is None:
            async with self._model_lock:
                if self._model is None:
                    logger.info("Loading Cross-Encoder model: %s", self._model_name)
                    try:
                        self._model = await asyncio.to_thread(CrossEncoder, self._model_name)
                    except Exception as e:
                        logger.error("Failed to load Cross-Encoder: %s. Using fallback.", e)
                        return self._fallback_rank(candidates, top_k)

        # 3. Format inputs for the Cross-Encoder: [(query, doc1), (query, doc2), ...]
        sentence_pairs = [
            [query, str(candidate.get("text", ""))] 
            for candidate in candidates
        ]

        try:
            # 4. Run inference in a separate thread so we don't block the FastAPI event loop!
            scores = await asyncio.to_thread(self._model.predict, sentence_pairs)
        except Exception as e:
            logger.exception("Cross-Encoder prediction failed. Using fallback.")
            return self._fallback_rank(candidates, top_k)

        # 5. Zip the new scores back onto safely copied candidate dictionaries
        scored_candidates = []
        for candidate, score in zip(candidates, scores):
            safe_candidate = dict(candidate)
            # Convert numpy float32 to standard Python float for JSON serialization
            safe_candidate["rerank_score"] = float(score)
            scored_candidates.append(safe_candidate)

        # 6. Sort by the new, highly-accurate semantic score
        scored_candidates.sort(key=lambda item: item["rerank_score"], reverse=True)
        return scored_candidates[:top_k]

    @staticmethod
    def _fallback_rank(candidates: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        """Fallback method that simply relies on the original vector database similarity score."""
        sorted_candidates = sorted(
            candidates, 
            key=lambda item: float(item.get("score", 0.0)), 
            reverse=True
        )
        return sorted_candidates[:top_k]