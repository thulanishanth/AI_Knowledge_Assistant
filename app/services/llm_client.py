# app/services/llm_client.py
"""LLM client wrapper wired to Hugging Face Inference API (Qwen 2.5 7B)."""

import time
import os
import json
from typing import Any
from openai import OpenAI

from app.core.settings import settings
from app.core.logging import get_logger
from app.observability.query_observer import extract_token_usage

logger = get_logger(__name__)

# Global cache for the client
_client: OpenAI | None = None

def _get_client() -> OpenAI:
    """Lazily initialize the OpenAI-compatible client pointing to Hugging Face."""
    global _client
    if _client is None:
        # Use HF_API_KEY from your settings
        api_key = settings.hf_api_key
        
        if not api_key:
            raise RuntimeError("HF_API_KEY is not configured in your .env file.")
            
        # Redirecting to Hugging Face Inference API (OpenAI compatible)
        _client = OpenAI(
            base_url="https://router.huggingface.co/v1",
            api_key=api_key,
        )
    return _client

def call_llm(
    prompt: str,
    max_tokens: int = 1000,
    max_retries: int | None = None,
    temperature: float | None = None,
) -> str:
    """Generate LLM output using Qwen-7B on Hugging Face."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
        
    retries = max_retries if max_retries is not None else settings.llm_max_retries
    temp = settings.llm_temperature_sql if temperature is None else temperature

    client = _get_client()
    # Recommended: Qwen2.5-7B-Instruct is highly optimized for SQL and reasoning
    model_name = settings.hf_model or "Qwen/Qwen2.5-7B-Instruct"

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temp,
            )
            
            # --- OBSERVABILITY: Extract and log token usage ---
            usage = extract_token_usage(response, model_name=model_name)
            logger.info("LLM_TOKENS | model=%s | prompt=%s | completion=%s | total=%s", 
                usage.model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
            
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
                
            raise RuntimeError("Hugging Face returned an empty response.")
            
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: HF API call failed ({e}).")
            
            # Check for common HF/OpenAI errors
            error_text = str(e).lower()
            if "401" in error_text or "unauthorized" in error_text:
                raise RuntimeError("Your Hugging Face API key is invalid.") from e
            
            # 503 is common for HF serverless if the model is loading
            if "503" in error_text or "loading" in error_text:
                logger.info("Model is currently loading on HF. Waiting longer...")
                time.sleep(15) 
            
            if attempt < retries - 1:
                sleep_time = 2 ** attempt
                time.sleep(sleep_time)
            else:
                logger.error("All Hugging Face generation attempts exhausted.")
                raise RuntimeError("HF Inference API is currently unreachable or the model is overloaded.") from e

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
    Calls Qwen-7B and forces it to return data matching a specific Tool schema.
    Qwen 2.5 is natively trained to handle Tool Calling.
    """
    retries = max_retries if max_retries is not None else 3
    temp = temperature if temperature is not None else 0.0
    client = _get_client() 
    model_name = settings.hf_model or "Qwen/Qwen2.5-7B-Instruct"

    logger.info(f"\n{'='*70}\n🛠️ [HF TOOL CALL: {tool_name} | MODEL: {model_name}] 🛠️\n{'-'*70}\n{prompt.strip()[:200]}...\n{'='*70}\n")

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                tools=[{"type": "function", "function": tool_schema}],
                # Note: Serverless HF API tool support can be strict. 
                # If "tool_choice" fails, we fallback to auto.
                tool_choice={"type": "function", "function": {"name": tool_name}},
                temperature=temp,
                max_tokens=max_tokens
            )

            # --- OBSERVABILITY: Extract and log token usage ---
            usage = extract_token_usage(response, model_name=model_name)
            logger.info("LLM_TOOL_TOKENS | model=%s | prompt=%s | completion=%s | total=%s", 
                usage.model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)

            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                arguments_str = tool_calls[0].function.arguments
                return json.loads(arguments_str)

            # Fallback: If no tool call, check if the model just wrote JSON in content
            content = response.choices[0].message.content
            if content and "{" in content:
                # Basic extraction logic for non-strict outputs
                start = content.find("{")
                end = content.rfind("}") + 1
                return json.loads(content[start:end])

            return {}

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: HF tool call failed ({e}).")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                return {}

    return {}