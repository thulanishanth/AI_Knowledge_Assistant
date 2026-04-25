# app/services/hybrid_rule_retriever.py
from __future__ import annotations
import math
import asyncio
from dataclasses import dataclass
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

@dataclass
class ScoredRule:
    rule_text: str
    is_critical: bool
    rrf_score: float = 0.0

class HybridRuleRetriever:
    """Combines BM25 keyword matching with Semantic Dense embeddings."""

    RRF_K = 60  # Standard Reciprocal Rank Fusion constant
    _model_instance = None # Class-level singleton to avoid reloading model

    def __init__(self):
        self._rule_embeddings: dict[str, list[float]] = {}

        # Load the model from your .env file as a singleton
        if HybridRuleRetriever._model_instance is None:
            model_name = settings.embedding_model or 'sentence-transformers/all-MiniLM-L6-v2'
            logger.info(f"Loading embedding model for Hybrid RAG: {model_name}")
            HybridRuleRetriever._model_instance = SentenceTransformer(model_name)

        self._model = HybridRuleRetriever._model_instance

    async def retrieve(self, question: str, rules: list[str], top_k: int = 5) -> list[ScoredRule]:
        if not rules:
            return []

        # 1. Sparse Retrieval (BM25 Keyword Matching)
        sparse_ranked = self._sparse_retrieve(question, rules)

        # 2. Dense Retrieval (Semantic Similarity)
        dense_ranked = await self._dense_retrieve(question, rules)

        # 3. Reciprocal Rank Fusion (Merge Both Worlds)
        rrf_scores: dict[int, float] = {}

        for rank, (idx, _) in enumerate(dense_ranked, 1):
            rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (self.RRF_K + rank)

        for rank, (idx, _) in enumerate(sparse_ranked, 1):
            rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (self.RRF_K + rank)

        # Build final scored objects
        fused = []
        for idx, score in rrf_scores.items():
            rule_text = rules[idx]
            is_critical = rule_text.strip().lower().startswith("critical:")

            # CRITICAL rules get a slight RRF floor boost so they aren't ignored
            final_score = max(score, 0.03) if is_critical else score

            fused.append(ScoredRule(rule_text=rule_text, is_critical=is_critical, rrf_score=final_score))

        # Sort by best combined score and return Top K
        fused.sort(key=lambda r: r.rrf_score, reverse=True)
        return fused[:top_k]

    def _sparse_retrieve(self, question: str, rules: list[str]) -> list[tuple[int, float]]:
        tokenized_corpus = [r.lower().split() for r in rules]
        bm25 = BM25Okapi(tokenized_corpus)
        tokenized_query = question.lower().split()

        scores = bm25.get_scores(tokenized_query)
        scored = [(i, score) for i, score in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    async def _embed_text(self, text: str) -> list[float]:
        """Convert text to a semantic vector asynchronously."""
        if not text or not text.strip():
            return []
        vector = await asyncio.to_thread(self._model.encode, text)
        return vector.tolist()

    async def _dense_retrieve(self, question: str, rules: list[str]) -> list[tuple[int, float]]:
        q_emb = await self._embed_text(question)
        if not q_emb:
            return [(i, 0.0) for i in range(len(rules))]

        scored = []
        for i, rule in enumerate(rules):
            if rule not in self._rule_embeddings:
                self._rule_embeddings[rule] = await self._embed_text(rule)

            rule_emb = self._rule_embeddings[rule]
            sim = self._cosine_similarity(q_emb, rule_emb)
            scored.append((i, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b): return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(y * y for y in b))
        if mag_a == 0 or mag_b == 0: return 0.0
        return dot / (mag_a * mag_b)