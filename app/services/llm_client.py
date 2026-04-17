#app/services/llm_client.py
"""LLM client wrapper wired to the OpenAI API."""

import time
import os
import json
from typing import Any
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
        api_key = os.getenv("OPENAI_API_KEY")
        
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured in your .env file.")
            
        # Initialize without a custom base_url to default to official OpenAI endpoints
        _client = OpenAI(
            api_key=api_key,
        )
    return _client

def call_llm(
    prompt: str,
    max_tokens: int = 1000,
    max_retries: int | None = None,
    temperature: float | None = None,
) -> str:
    """Generate LLM output using the OpenAI endpoint with retries."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
        
    retries = max_retries if max_retries is not None else settings.llm_max_retries
    temp = settings.llm_temperature_sql if temperature is None else temperature

    client = _get_client()
    # Updated default to an official OpenAI model
    model_name = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

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
            
            # Handle OpenAI's specific Rate Limit errors (429)
            if "429" in error_text or "too many requests" in error_text:
                logger.warning("OpenAI rate limit hit. Waiting a bit longer...")
                time.sleep(5) # Wait an extra 5 seconds if we hit the limit
            
            if attempt < retries - 1:
                sleep_time = 2 ** attempt  # 1s, 2s, 4s...
                logger.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                logger.error("All LLM generation attempts exhausted.")
                raise RuntimeError("LLM API is currently unreachable, timing out, or out of credits.") from e

    raise RuntimeError("All LLM generation attempts exhausted.")

def call_llm_with_tool(
    prompt: str,
    tool_schema: dict[str, Any],
    tool_name: str,
    max_tokens: int = 500,
    max_retries: int | None = None,
    temperature: float | None = None
) -> dict[str, Any]:
    """
    Natively calls the LLM and strictly forces it to return data matching
    a specific Tool (Function) schema. 
    """
    # Fallback to defaults if not provided
    retries = max_retries if max_retries is not None else 3
    temp = temperature if temperature is not None else 0.0
    client = _get_client() 

    logger.info(f"\n{'='*70}\n🛠️ [SENDING TOOL CALL TO LLM: {tool_name}] 🛠️\n{'-'*70}\n{prompt.strip()[:200]}...\n{'='*70}\n")

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo", # Explicitly set to GPT-3.5-Turbo as requested
                messages=[{"role": "user", "content": prompt}],
                tools=[{"type": "function", "function": tool_schema}],
                # Force the model to use the exact tool we provided
                tool_choice={"type": "function", "function": {"name": tool_name}},
                temperature=temp,
                max_tokens=max_tokens
            )

            # Extract the arguments the LLM generated for the tool
            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                arguments_str = tool_calls[0].function.arguments
                return json.loads(arguments_str)

            return {}

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse tool arguments as JSON on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                return {}
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: LLM tool call failed ({e}).")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.error(f"Native Tool Call failed entirely after {retries} attempts.")
                return {}

    return {}