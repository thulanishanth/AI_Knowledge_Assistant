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

Here is the recent conversation memory:
{memory_context}

User's New Prompt: "{user_question}"

Task:
1. Categorize the intent into one of FOUR categories: 
   - "schema_inquiry": If the user asks about the structure of the table (e.g., "what columns are there", "how many fields", "show the schema").
   - "database_query": If the user is asking for actual data/rows (e.g., "how many bookings", "average price").
   - "greeting": For hello/goodbye.
   - "general_chitchat": For off-topic questions.
2. If it is a database_query, rewrite their prompt into perfect, professional English. 
3. If it is a greeting or chitchat, write a friendly, helpful direct response.
4. Evaluate the user's prompt and assign a "difficulty_score" from 0 to 100 based on the cognitive load required to answer it.
   - 0 to 50: Simple factual retrieval, basic counting, or single-table queries.
   - 51 to 70: Multi-step reasoning, complex SQL math, or data grouping.
   - 71 to 100: Heavy algorithmic generation or highly ambiguous logic.

You MUST return ONLY a valid JSON object. No markdown formatting, no extra text.
Format:
{{
    "intent": "database_query | schema_inquiry | greeting | general_chitchat",
    "corrected_query": "The grammatically perfect version of their question (only if database_query)",
    "direct_response": "A friendly reply (only if greeting or chitchat, otherwise empty)",
    "difficulty_score": 45,
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
                "difficulty_score": 50,
                "is_follow_up": False
            }