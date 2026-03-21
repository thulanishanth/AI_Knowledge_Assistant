# app/services/cloud_llm.py
"""Async cloud LLM client used by router-based model integrations."""

from __future__ import annotations

import httpx

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MODEL = settings.hf_model or "Qwen/Qwen2.5-7B-Instruct"


async def call_cloud_llm(
    prompt: str,
    max_tokens: int = 200,
    temperature: float | None = None,
) -> str:
    """Call the Hugging Face chat-completions endpoint asynchronously."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
    if not settings.hf_api_key:
        raise RuntimeError("HF_API_KEY is not configured.")

    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt.strip()}],
        "max_tokens": max_tokens,
        "temperature": settings.llm_temperature_sql if temperature is None else temperature,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"https://api-inference.huggingface.co/models/{_MODEL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.hf_api_key}",
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
