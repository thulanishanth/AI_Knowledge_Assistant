# AI_Knowledge_Assistant/app/ingestion/knowledge_ingestion.py
"""Knowledge ingestion pipeline for global memory collection."""

from __future__ import annotations

from app.memory.vector_memory import VectorMemory
from app.utils.logger import get_logger

logger = get_logger(__name__)


class KnowledgeIngestionService:  # pylint: disable=too-few-public-methods
    """Ingests curated documents into long-term knowledge memory."""


    def __init__(self, vector_memory: VectorMemory) -> None:
        self._vector_memory = vector_memory

    async def ingest_documents(self, documents: list[str], source: str = "manual") -> list[str]:
        """Store non-empty documents in vector memory and return created IDs."""
        ids: list[str] = []
        for document in documents:
            text = document.strip()
            if not text:
                continue
            record_id = await self._vector_memory.store_knowledge_memory(
                content=text,
                metadata={"source": source},
            )
            ids.append(record_id)
        logger.info("Knowledge ingestion completed count=%s source=%s", len(ids), source)
        return ids
