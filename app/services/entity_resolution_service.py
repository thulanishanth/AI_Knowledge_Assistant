# app/services/entity_resolution_service.py
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


class EntityResolutionService:
    """
    Backward-compatible neutral resolver.

    Intentionally returns no injected hints.
    This keeps old imports from breaking while removing hotel-specific hardcoding.
    """

    async def resolve_entities(self, user_question: str, sql_execution_service) -> str:
        logger.debug("EntityResolutionService is disabled in generic mode.")
        return ""


entity_resolver = EntityResolutionService()