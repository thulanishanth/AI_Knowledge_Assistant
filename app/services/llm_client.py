#app/services/llm_client.py
"""LLM client wrapper wired to Hugging Face Inference API."""

import time
from huggingface_hub import InferenceClient

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Global cache for the client to ensure lazy loading
_hf_client: InferenceClient | None = None

def _get_client() -> InferenceClient:
    """Lazily initialize the HF client to ensure environment variables are loaded."""
    global _hf_client
    if _hf_client is None:
        if not settings.hf_api_key:
            raise RuntimeError("HF_API_KEY is not configured in your .env file.")
        
        # Fallback to Qwen 7B if it's missing from settings
        model_id = settings.hf_model if settings.hf_model else "Qwen/Qwen2.5-7B-Instruct"
        
        _hf_client = InferenceClient(model=model_id, token=settings.hf_api_key)
    return _hf_client


def call_llm(
    prompt: str,
    max_tokens: int = 400,
    max_retries: int | None = None,
    temperature: float | None = None,
) -> str:
    """Generate LLM output using Hugging Face with exponential backoff retries."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
        
    retries = max_retries if max_retries is not None else settings.llm_max_retries
    temp = settings.llm_temperature_sql if temperature is None else temperature

    client = _get_client()

    for attempt in range(retries):
        try:
            # Use the modern chat_completion endpoint for Qwen models
            response = client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temp,
            )
            
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
                
            raise RuntimeError("Hugging Face API returned an empty response.")
            
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: HF API call failed ({e}).")
            
            # Check for authentication/credit errors
            error_text = str(e).lower()
            if "401" in error_text or "unauthorized" in error_text:
                raise RuntimeError("Your Hugging Face API key is invalid. Please check your .env file.") from e
            
            if attempt < retries - 1:
                sleep_time = 2 ** attempt  # 1s, 2s, 4s...
                logger.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                logger.error("All Hugging Face generation attempts exhausted.")
                raise RuntimeError("Hugging Face API is currently unreachable or timing out.") from e

    raise RuntimeError("All LLM generation attempts exhausted.")