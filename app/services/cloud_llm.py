# app/services/cloud_llm.py
import requests
from app.config import HF_API_KEY
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Same reliable model
HF_MODEL = "Qwen2.5-7B-Instruct"

# Direct model-specific OpenAI-compatible endpoint
API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {HF_API_KEY}"}

def call_cloud_llm(prompt: str, max_tokens: int = 200) -> str:
    """
    Call Hugging Face API via the OpenAI-compatible router to generate text.
    """
    payload = {
        "model": HF_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7
    }

    try:
        logger.info("Calling cloud LLM endpoint")
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"].strip()

        return str(data)

    except requests.exceptions.HTTPError as err:
        error_details = response.text if 'response' in locals() else str(err)
        logger.exception("Cloud LLM HTTP error")
        return f"HTTP Error: {err}. Details: {error_details}"
    except (ValueError, TypeError, KeyError, RuntimeError) as e:
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no message."
        logger.exception("Cloud LLM unexpected error")
        return f"Unexpected error: {error_message}"
