from __future__ import annotations

from app.core.logging import get_logger
from app.memory.vector_memory import VectorMemory

logger = get_logger(__name__)


class BusinessLogicSearchService:
    """
    Search only the approved business-logic collection in Chroma.
    """

    def __init__(self, vector_memory: VectorMemory) -> None:
        self._vector_memory = vector_memory

    async def search_terms(
        self,
        terms: list[str],
        domain_hint: str | None = None,
        top_k_per_term: int = 5,
    ) -> dict[str, list[dict[str, object]]]:
        results: dict[str, list[dict[str, object]]] = {}

        for term in terms:
            try:
                matches = await self._vector_memory.retrieve_business_logic_context(
                    query=term,
                    domain=domain_hint,
                    top_k=top_k_per_term,
                )
                results[term] = matches
            except Exception as exc:
                logger.error("Business logic search failed for term=%s error=%s", term, exc)
                results[term] = []

        return results