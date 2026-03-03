import requests
from huggingface_hub import InferenceClient

from app.config import HF_API_KEY, HF_MODEL
from app.utils.logger import get_logger

logger = get_logger(__name__)

HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
client = InferenceClient(model=HF_MODEL, token=HF_API_KEY)


def _call_llm_router(prompt: str, max_tokens: int) -> str:
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

    raise RuntimeError(f"Legacy HF API returned unexpected response type: {type(result).__name__}")


def call_llm(prompt: str, max_tokens: int = 200) -> str:
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    try:
        return _call_llm_router(prompt, max_tokens)
    except (requests.RequestException, RuntimeError, ValueError, TypeError, KeyError) as router_error:
        logger.exception("HF router call failed, trying legacy API fallback")
        try:
            return _call_llm_legacy(prompt, max_tokens)
        except (RuntimeError, ValueError, TypeError, AttributeError) as legacy_error:
            logger.exception("Legacy HF fallback failed")
            router_message = str(router_error).strip() or router_error.__class__.__name__
            legacy_message = str(legacy_error).strip() or legacy_error.__class__.__name__
            raise RuntimeError(
                f"LLM request failed: router error ({router_message}); legacy error ({legacy_message})"
            ) from legacy_error
