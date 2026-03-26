#app/services/intent_service.py
import json
from app.services.llm_client import call_llm
from app.core.logging import get_logger

logger = get_logger(__name__)

class IntentService:
    """The intelligence layer that categorizes and cleans user inputs."""

    def analyze(self, user_question: str, memory_context: str = "") -> dict:
        """Analyzes the user's prompt to determine intent and fix grammar."""
        
        prompt = f"""You are the intelligent routing brain of a Hotel Database Assistant.
Your job is to read the user's prompt, figure out what they want, fix their grammar, and return a STRICT JSON response.

Here is the recent conversation memory (use this to understand context if they ask "what about the other one?"):
{memory_context}

User's New Prompt: "{user_question}"

Task:
1. Categorize the intent into one of three categories: "greeting", "general_chitchat", or "database_query".
2. If it is a database_query, rewrite their prompt into perfect, professional English. Fix all spelling (e.g., "cloums" -> "columns").
3. If it is a greeting or chitchat, write a friendly, helpful direct response.

You MUST return ONLY a valid JSON object. No markdown formatting, no extra text.
Format:
{{
    "intent": "database_query | greeting | general_chitchat",
    "corrected_query": "The grammatically perfect version of their question (only if database_query)",
    "direct_response": "A friendly reply (only if greeting or chitchat, otherwise empty)",
    "is_follow_up": true/false
}}
"""
        try:
            # We call the LLM just to route and clean the text!
            response_text = call_llm(prompt, temperature=0.1)
            
            # Clean up the response in case the LLM wrapped it in markdown
            if response_text.startswith("```json"):
                response_text = response_text.replace("```json", "").replace("```", "").strip()
            elif response_text.startswith("```"):
                response_text = response_text.replace("```", "").strip()
                
            return json.loads(response_text)
            
        except Exception as e:
            logger.error(f"Intent analysis failed: {e}. Falling back to default.")
            # Fallback just in case the LLM fails to return valid JSON
            return {
                "intent": "database_query",
                "corrected_query": user_question,
                "direct_response": "",
                "is_follow_up": False
            }