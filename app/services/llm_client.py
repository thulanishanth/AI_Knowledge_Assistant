#app/services/llm_client.py
"""LLM client wrapper wired to the OpenAI-compatible API (Groq)."""

import time
import os
from openai import OpenAI

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Global cache for the client to ensure lazy loading
_client: OpenAI | None = None

def _get_client() -> OpenAI:
    """Lazily initialize the OpenAI client using environment variables."""
    global _client
    if _client is None:
        # Fetch directly from os.environ
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
        api_key = os.getenv("OPENAI_API_KEY")
        
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured in your .env file.")
            
        _client = OpenAI(
            base_url=base_url,
            api_key=api_key,
        )
    return _client


def call_llm(
    prompt: str,
    max_tokens: int = 1000,
    max_retries: int | None = None,
    temperature: float | None = None,
) -> str:
    """Generate LLM output using an OpenAI-compatible endpoint (Groq) with retries."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
        
    retries = max_retries if max_retries is not None else settings.llm_max_retries
    temp = settings.llm_temperature_sql if temperature is None else temperature

    client = _get_client()
    model_name = os.getenv("OPENAI_MODEL", "llama-3.3-70b-versatile")

    for attempt in range(retries):
        try:
            # Universal chat completions endpoint
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temp,
            )
            
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
                
            raise RuntimeError("API returned an empty response.")
            
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: LLM API call failed ({e}).")
            
            # Check for authentication errors
            error_text = str(e).lower()
            if "401" in error_text or "unauthorized" in error_text or "invalid_api_key" in error_text:
                raise RuntimeError("Your API key is invalid. Please check your .env file.") from e
            
            # Handle Groq's specific Rate Limit errors (429)
            if "429" in error_text or "too many requests" in error_text:
                logger.warning("Groq rate limit hit. Waiting a bit longer...")
                time.sleep(5) # Wait an extra 5 seconds if we hit the limit
            
            if attempt < retries - 1:
                sleep_time = 2 ** attempt  # 1s, 2s, 4s...
                logger.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                logger.error("All LLM generation attempts exhausted.")
                raise RuntimeError("LLM API is currently unreachable, timing out, or out of credits.") from e

    raise RuntimeError("All LLM generation attempts exhausted.")