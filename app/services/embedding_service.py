# AI_Knowledge_Assistant/app/services/embedding_service.py
"""Embedding service abstraction with pluggable providers."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from sentence_transformers import (  # type: ignore
        SentenceTransformer as SENTENCE_TRANSFORMER_CLS,
    )
except ImportError:  # pragma: no cover
    SENTENCE_TRANSFORMER_CLS = None

try:
    from openai import OpenAI as OPENAI_CLIENT_CLS  # type: ignore
except ImportError:  # pragma: no cover
    OPENAI_CLIENT_CLS = None


class EmbeddingService:
    """Generates embeddings through OpenAI, HF, or local models."""

    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        """Initialize embedding provider and model settings."""
        self._provider = (provider or settings.embedding_provider).strip().lower()
        self._model = model or settings.embedding_model
        self._local_model: Any | None = None
        self._openai_client: Any | None = None
        
        # Concurrency lock to prevent multiple simultaneous model loads
        self._model_lock = asyncio.Lock()

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

        raise ValueError(f"Unsupported embedding provider: {self._provider}")

    async def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via OpenAI embeddings API."""
        if OPENAI_CLIENT_CLS is None:
            raise RuntimeError("openai package is missing. Cannot use OpenAI embeddings.")
        if not settings.cloud_api_key:
            raise ValueError("CLOUD_API_KEY is not set for OpenAI provider.")

        if self._openai_client is None:
            self._openai_client = OPENAI_CLIENT_CLS(api_key=settings.cloud_api_key)

        def _call() -> list[list[float]]:
            response = self._openai_client.embeddings.create(model=self._model, input=texts)
            return [item.embedding for item in response.data]

        try:
            return await asyncio.to_thread(_call)
        except Exception as e:
            logger.exception("OpenAI embedding failed")
            raise RuntimeError(f"Failed to generate embeddings via OpenAI: {str(e)}") from e

    async def _embed_hf_inference(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via Hugging Face inference API using async httpx."""
        if not settings.hf_api_key:
            raise ValueError("HF_API_KEY is not set for Hugging Face provider.")

        payload = {"inputs": texts}
        headers = {
            "Authorization": f"Bearer {settings.hf_api_key}",
            "Content-Type": "application/json"
        }
        url = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{self._model}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=45.0)
                response.raise_for_status()
                data = response.json()
                
            if isinstance(data, list) and data and isinstance(data[0], list):
                return [self._normalize_vector(vector) for vector in data]
                
            raise ValueError("Hugging Face API returned an unexpected data structure.")
            
        except httpx.HTTPError as e:
            logger.exception("Hugging Face API network error")
            raise RuntimeError(f"HF embedding failed due to network error: {str(e)}") from e
        except Exception as e:
            logger.exception("Hugging Face embedding processing failed")
            raise RuntimeError(f"HF embedding failed: {str(e)}") from e

    async def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via local sentence-transformers model with concurrency safety."""
        if SENTENCE_TRANSFORMER_CLS is None:
            raise RuntimeError("sentence-transformers is not installed. Cannot use local models.")

        # Double-checked locking pattern to safely initialize the model once
        if self._local_model is None:
            async with self._model_lock:
                if self._local_model is None:
                    logger.info("Loading local embedding model: %s", self._model)
                    self._local_model = await asyncio.to_thread(SENTENCE_TRANSFORMER_CLS, self._model)

        try:
            encoded = await asyncio.to_thread(
                self._local_model.encode,
                texts,
                normalize_embeddings=True,
            )
            return [list(map(float, vector)) for vector in encoded]
        except Exception as e:
            logger.exception("Local embedding generation failed")
            raise RuntimeError(f"Local embedding failed: {str(e)}") from e

    @staticmethod
    def _normalize_vector(vector: list[float]) -> list[float]:
        """L2-normalize a vector and return float values."""
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [float(value / norm) for value in vector]
