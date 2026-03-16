# AI_Knowledge_Assistant/app/services/cloud_llm.py
"""
Service for interacting with the Hugging Face Cloud LLM API.
Provides utility functions to generate text using hosted inference models via async HTTP.
"""
import httpx
from app.config import HF_API_KEY, HF_MODEL
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Fallback to Qwen if not defined in your central app.config
_MODEL = HF_MODEL or "Qwen/Qwen2.5-7B-Instruct"

async def call_cloud_llm(prompt: str, max_tokens: int = 200) -> str:
    """
    Asynchronously call Hugging Face API via the OpenAI-compatible router to generate text.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    # 1. Lazy evaluation: build URL and headers inside the function
    api_url = f"https://api-inference.huggingface.co/models/{_MODEL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2  # Lower temperature is better for SQL generation and facts
    }

    try:
        logger.info("Calling cloud LLM endpoint for model: %s", _MODEL)
        
        # 2. Async HTTP Call: This allows FastAPI to serve other users while waiting
        async with httpx.AsyncClient() as client:
            response = await client.post(
                api_url, 
                headers=headers, 
                json=payload, 
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

        # 3. Safe extraction
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM API returned an empty or malformed response (no choices).")

        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("LLM API returned empty text content.")

        return content

    # 4. Fail loudly so the query_service can catch the error and fallback
    except httpx.HTTPStatusError as e:
        logger.error("Cloud LLM HTTP Error %s: %s", e.response.status_code, e.response.text)
        raise RuntimeError(f"Cloud LLM API failed with status {e.response.status_code}") from e
        
    except httpx.RequestError as e:
        logger.error("Network error while connecting to LLM API: %s", e)
        raise RuntimeError("Cloud LLM API is currently unreachable.") from e
        
    except Exception as e:
        logger.exception("Unexpected error in Cloud LLM call")
        raise RuntimeError(f"An unexpected error occurred: {str(e)}") from e