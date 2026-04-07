# app/services/intent_service.py
from __future__ import annotations

import json

from app.core.logging import get_logger
from app.services.llm_client import call_llm
from app.services.prompt_builder import PromptBuilder

logger = get_logger(__name__)


class IntentService:
    """Categorizes user requests and rewrites them into clean standalone queries."""

    _ALLOWED_INTENTS = {
        "schema_inquiry",
        "database_query",
        "refine_last_query",
        "explain_last_answer",
        "ask_for_sql",
        "ask_for_source",
        "greeting",
        "general_chitchat",
    }

    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._prompt_builder = prompt_builder

    def analyze(self, user_question: str, memory_context: str = "") -> dict:
        prompt = self._prompt_builder.build_intent_prompt(
            user_question=user_question,
            memory_context=memory_context,
        )

        try:
            response_text = call_llm(prompt=prompt, max_tokens=300, temperature=0.0)
            cleaned = self._strip_code_fences(response_text)
            data = json.loads(cleaned)

            intent = str(data.get("intent", "database_query")).strip()
            if intent not in self._ALLOWED_INTENTS:
                intent = "database_query"

            corrected_query = str(data.get("corrected_query", user_question)).strip()
            direct_response = str(data.get("direct_response", "")).strip()

            try:
                difficulty_score = int(data.get("difficulty_score", 50))
            except Exception:
                difficulty_score = 50

            is_follow_up = bool(data.get("is_follow_up", False))

            return {
                "intent": intent,
                "corrected_query": corrected_query or user_question,
                "direct_response": direct_response,
                "difficulty_score": max(0, min(100, difficulty_score)),
                "is_follow_up": is_follow_up,
            }
        except Exception as exc:
            logger.error("Intent analysis failed: %s", exc)
            return self._heuristic_fallback(user_question)

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        value = (text or "").strip()
        if value.startswith("```json"):
            return value.replace("```json", "", 1).rsplit("```", 1)[0].strip()
        if value.startswith("```"):
            return value.replace("```", "", 1).rsplit("```", 1)[0].strip()
        return value

    def _heuristic_fallback(self, user_question: str) -> dict:
        lowered = (user_question or "").strip().lower()

        explain_markers = [
            "how did you get",
            "how was this derived",
            "why this answer",
            "explain this result",
            "explain that result",
            "derived from",
        ]
        sql_markers = [
            "show me the sql",
            "show the sql",
            "what sql",
            "what query did you use",
            "show query",
            "show the query",
        ]
        source_markers = [
            "what is this based on",
            "which table",
            "which columns",
            "what columns",
            "where did this come from",
            "source of this",
        ]
        refine_markers = [
            "now ",
            "only ",
            "filter ",
            "sort ",
            "exclude ",
            "include ",
            "instead ",
            "for last ",
            "for this ",
            "for only ",
        ]

        if any(marker in lowered for marker in sql_markers):
            intent = "ask_for_sql"
        elif any(marker in lowered for marker in explain_markers):
            intent = "explain_last_answer"
        elif any(marker in lowered for marker in source_markers):
            intent = "ask_for_source"
        elif any(lowered.startswith(marker) for marker in refine_markers):
            intent = "refine_last_query"
        elif any(word in lowered for word in ["schema", "column", "columns", "table structure", "fields"]):
            intent = "schema_inquiry"
        elif lowered in {"hi", "hello", "hey", "thanks", "thank you", "bye"}:
            intent = "greeting"
        else:
            intent = "database_query"

        return {
            "intent": intent,
            "corrected_query": user_question,
            "direct_response": "Hello! How can I help you with your data?"
            if intent == "greeting"
            else "",
            "difficulty_score": 50,
            "is_follow_up": intent in {
                "refine_last_query",
                "explain_last_answer",
                "ask_for_sql",
                "ask_for_source",
            },
        }