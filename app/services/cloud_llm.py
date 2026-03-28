# app/services/cloud_llm.py
"""Async cloud LLM client used by router-based model integrations."""

from __future__ import annotations
import os

import httpx

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def call_cloud_llm(
    prompt: str,
    model_name: str,
    max_tokens: int = 200,
    temperature: float | None = None,
) -> str:
    """Call the Hugging Face chat-completions endpoint asynchronously."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
    
    # Dynamically fetch the key from the environment!
    cloud_api_key = os.getenv("OPENAI_API_KEY_CLOUD")
    if not cloud_api_key:
        raise RuntimeError("CLOUD_API_KEY is not configured.")

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt.strip()}],
        "max_tokens": max_tokens,
        "temperature": settings.llm_temperature_sql if temperature is None else temperature,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cloud_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Cloud LLM HTTP error status=%s", exc.response.status_code)
        raise RuntimeError(f"Cloud LLM API failed with status {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        logger.error("Cloud LLM request failed: %s", exc)
        raise RuntimeError("Cloud LLM API is currently unreachable.") from exc

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Cloud LLM returned no choices.")
    content = (choices[0].get("message", {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("Cloud LLM returned empty content.")
    return content
