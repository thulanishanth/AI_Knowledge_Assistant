"""Compatibility wrapper for intent classification."""

from app.services.intent_service import IntentService

_intent_service = IntentService()


def classify_intent(user_question: str) -> str:
    """Return `sql` for DB questions, otherwise `general`."""
    normalized = " ".join((user_question or "").strip().lower().split())
    if any(term in normalized for term in {"how many", "count", "show", "list", "database", "table"}):
        return "sql"
    decision = _intent_service.classify(user_question)
    return "sql" if decision.kind == "database_query" else "general"
