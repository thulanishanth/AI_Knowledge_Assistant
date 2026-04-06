# app/services/intent_service.py
import json
from app.services.llm_client import call_llm
from app.core.logging import get_logger
from app.services.prompt_builder import PromptBuilder

logger = get_logger(__name__)

class IntentService:
    """The intelligence layer that categorizes and cleans user inputs."""

    def __init__(self, prompt_builder: PromptBuilder):
        self._prompt_builder = prompt_builder

    def analyze(self, user_question: str, memory_context: str = "") -> dict:
        """Analyzes the user's prompt to determine intent and fix grammar."""
        
        # Get the clean prompt from the builder!
        prompt = self._prompt_builder.build_intent_prompt(user_question, memory_context)
        
        try:
            response_text = call_llm(prompt, temperature=0.1)
            if response_text.startswith("```json"):
                response_text = response_text.replace("```json", "").replace("```", "").strip()
            elif response_text.startswith("```"):
                response_text = response_text.replace("```", "").strip()
                
            return json.loads(response_text)
            
        except Exception as e:
            logger.error(f"Intent analysis failed: {e}. Falling back to default.")
            return {
                "intent": "database_query",
                "corrected_query": user_question,
                "direct_response": "",
                "difficulty_score": 50,
                "is_follow_up": False
            }
