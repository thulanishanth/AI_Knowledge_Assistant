# AI_Knowledge_Assistant/app/services/llm_client.py
"""LLM client wrapper with router-first, legacy fallback, and retry strategy."""

import time
from huggingface_hub import InferenceClient

from app.config import HF_API_KEY, HF_MODEL
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Global cache for the client to ensure lazy loading
_hf_client: InferenceClient | None = None

def _get_client() -> InferenceClient:
    """Lazily initialize the HF client to ensure environment variables are loaded."""
    global _hf_client
    if _hf_client is None:
        _hf_client = InferenceClient(model=HF_MODEL, token=HF_API_KEY)
    return _hf_client


def _call_llm_router(prompt: str, max_tokens: int) -> str:
    """Call HF chat completions API natively using the InferenceClient."""
    client = _get_client()
    messages = [{"role": "user", "content": prompt}]
    
    # Natively routes to the /v1/chat/completions endpoint
    response = client.chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.2,
    )
    
    content = (response.choices[0].message.content or "").strip()
    if content:
        return content

    raise RuntimeError("HF chat completion returned an empty response.")


def _call_llm_legacy(prompt: str, max_tokens: int) -> str:
    """Call legacy HF text-generation endpoint as fallback."""
    client = _get_client()
    result = client.text_generation(
        prompt,
        max_new_tokens=max_tokens,
        temperature=0.2,
        return_full_text=False,  # CRITICAL: Prevents prompt echoing
    )

    if isinstance(result, str):
        cleaned = result.strip()
        if cleaned:
            return cleaned
        raise RuntimeError("Legacy HF API returned an empty response.")

    raise RuntimeError(
        f"Legacy HF API returned unexpected response type: {type(result).__name__}"
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

        except Exception as router_error:
            # Catching Exception here is safe because we *want* the fallback 
            # to trigger regardless of whether it was a network timeout or a 503 from HF.
            logger.warning(
                "Attempt %s: HF router call failed (%s). Trying legacy fallback.",
                attempt + 1,
                router_error,
            )

            try:
                # 2. Try the legacy fallback
                return _call_llm_legacy(prompt, max_tokens)

            except Exception as legacy_error:
                logger.warning(
                    "Attempt %s: Legacy HF fallback failed (%s).",
                    attempt + 1,
                    legacy_error,
                )

                # 3. If both fail, evaluate if we should retry
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s...
                    logger.info("Rate limit or timeout hit. Retrying in %s seconds...", sleep_time)
                    time.sleep(sleep_time)
                else:
                    logger.error("All LLM generation attempts exhausted.")
                    # Let the error bubble up so the Query Service can catch it
                    raise RuntimeError(
                        "External AI provider is currently unreachable."
                    ) from legacy_error

    raise RuntimeError("All LLM generation attempts exhausted.")