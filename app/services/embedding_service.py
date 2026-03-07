# AI_Knowledge_Assistant/app/services/embedding_service.py
"""Embedding service abstraction with pluggable providers."""

from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Any

import requests

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from sentence_transformers import (  # type: ignore
        SentenceTransformer as SENTENCE_TRANSFORMER_CLS,
    )
except ImportError:  # pragma: no cover - optional dependency
    SENTENCE_TRANSFORMER_CLS = None

try:
    from openai import OpenAI as OPENAI_CLIENT_CLS  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    OPENAI_CLIENT_CLS = None


class EmbeddingService:
    """Generates embeddings through OpenAI, HF, local model, or deterministic fallback."""

    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        """Initialize embedding provider and model settings."""
        self._provider = (provider or settings.embedding_provider).strip().lower()
        self._model = model or settings.embedding_model
        self._local_model: Any | None = None
        self._openai_client: Any | None = None

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        vectors = await self.embed_texts([text])
        return vectors[0] if vectors else []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts using configured provider."""
        normalized = [text.strip() for text in texts if text and text.strip()]
        if not normalized:
            return []

        if self._provider == "openai":
            return await self._embed_openai(normalized)
        if self._provider in {"huggingface", "hf"}:
            return await self._embed_hf_inference(normalized)
        if self._provider in {"local", "sentence_transformers"}:
            return await self._embed_local(normalized)

        return [self._deterministic_embedding(text) for text in normalized]

    async def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via OpenAI embeddings API with deterministic fallback."""
        if OPENAI_CLIENT_CLS is None:
            logger.warning("openai package missing, using deterministic embeddings.")
            return [self._deterministic_embedding(text) for text in texts]
        if not settings.cloud_api_key:
            logger.warning("CLOUD_API_KEY not set, using deterministic embeddings.")
            return [self._deterministic_embedding(text) for text in texts]

        if self._openai_client is None:
            self._openai_client = OPENAI_CLIENT_CLS(api_key=settings.cloud_api_key)

        def _call() -> list[list[float]]:
            response = self._openai_client.embeddings.create(model=self._model, input=texts)
            return [item.embedding for item in response.data]

        try:
            return await asyncio.to_thread(_call)
        except (RuntimeError, TypeError, ValueError, AttributeError):
            logger.exception("OpenAI embedding failed, using deterministic fallback")
            return [self._deterministic_embedding(text) for text in texts]

    async def _embed_hf_inference(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via Hugging Face inference API with fallback."""
        if not settings.hf_api_key:
            logger.warning("HF_API_KEY not set, using deterministic embeddings.")
            return [self._deterministic_embedding(text) for text in texts]

        payload = {"inputs": texts}
        headers = {"Authorization": f"Bearer {settings.hf_api_key}"}
        url = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{self._model}"

        try:
            response = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                json=payload,
                timeout=45,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                return [self._normalize_vector(vector) for vector in data]
        except (requests.RequestException, ValueError, TypeError, RuntimeError):
            logger.exception("HF embedding failed, using deterministic fallback")

        return [self._deterministic_embedding(text) for text in texts]

    async def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via local sentence-transformers model."""
        if SENTENCE_TRANSFORMER_CLS is None:
            logger.warning("sentence-transformers not installed, using deterministic embeddings.")
            return [self._deterministic_embedding(text) for text in texts]

        if self._local_model is None:
            self._local_model = await asyncio.to_thread(SENTENCE_TRANSFORMER_CLS, self._model)

        try:
            encoded = await asyncio.to_thread(
                self._local_model.encode,
                texts,
                normalize_embeddings=True,
            )
            return [list(map(float, vector)) for vector in encoded]
        except (RuntimeError, TypeError, ValueError, AttributeError):
            logger.exception("Local embedding failed, using deterministic fallback")
            return [self._deterministic_embedding(text) for text in texts]

    @staticmethod
    def _deterministic_embedding(text: str, dimensions: int = 128) -> list[float]:
        """Build a repeatable pseudo-embedding from text hash."""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        repeated = (digest * ((dimensions // len(digest)) + 1))[:dimensions]
        raw = [((byte / 255.0) * 2.0) - 1.0 for byte in repeated]
        return EmbeddingService._normalize_vector(raw)

    @staticmethod
    def _normalize_vector(vector: list[float]) -> list[float]:
        """L2-normalize a vector and return float values."""
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [float(value / norm) for value in vector]
