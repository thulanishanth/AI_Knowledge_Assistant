# AI_Knowledge_Assistant/app/services/llm_client.py
"""LLM client wrapper with router-first, legacy fallback, and retry strategy."""

import time
import requests
from huggingface_hub import InferenceClient

from app.config import HF_API_KEY, HF_MODEL
from app.utils.logger import get_logger

logger = get_logger(__name__)

HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
client = InferenceClient(model=HF_MODEL, token=HF_API_KEY)


def _call_llm_router(prompt: str, max_tokens: int) -> str:
    """Call HF router endpoint and return assistant text content."""
    payload = {
        "model": HF_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}

    response = requests.post(HF_ROUTER_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()

    choices = data.get("choices", []) if isinstance(data, dict) else []
    if choices:
        message = choices[0].get("message", {})
        content = (message.get("content") or "").strip()
        if content:
            return content

    raise RuntimeError("HF router returned an empty or unexpected response.")


def _call_llm_legacy(prompt: str, max_tokens: int) -> str:
    """Call legacy HF text-generation endpoint as fallback."""
    result = client.text_generation(
        prompt,
        max_new_tokens=max_tokens,
        temperature=0.2,
    )

    if isinstance(result, str):
        cleaned = result.strip()
        if cleaned:
            return cleaned
        raise RuntimeError("Legacy HF API returned an empty response.")

    raise RuntimeError(
        "Legacy HF API returned unexpected response type: "
        f"{type(result).__name__}"
    )


def call_llm(prompt: str, max_tokens: int = 200, max_retries: int = 3) -> str:
    """Generate LLM output with router call, legacy fallback, and exponential backoff retries."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1.")

    for attempt in range(max_retries):
        try:
            # 1. Try the primary router
            return _call_llm_router(prompt, max_tokens)

        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
        ) as router_error:
            logger.warning(
                "Attempt %s: HF router call failed (%s). Trying legacy fallback.",
                attempt + 1,
                router_error,
            )

            try:
                # 2. Try the legacy fallback
                return _call_llm_legacy(prompt, max_tokens)

            except (RuntimeError, ValueError, TypeError, AttributeError) as legacy_error:
                logger.warning(
                    "Attempt %s: Legacy HF fallback failed (%s).",
                    attempt + 1,
                    legacy_error,
                )

                # 3. If both fail, evaluate if we should retry
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt  # Wait 1s, then 2s...
                    logger.info("Rate limit or timeout hit. Retrying in %s seconds...", sleep_time)
                    time.sleep(sleep_time)
                else:
                    logger.error("All LLM generation attempts exhausted.")
                    # Let the error bubble up so the Query Service can catch it
                    raise RuntimeError(
                        "External AI provider is currently unreachable."
                    ) from legacy_error
    raise RuntimeError("All LLM generation attempts exhausted.")
