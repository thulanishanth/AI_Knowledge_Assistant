# app/scripts/ingest_db_context.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.core.dependency_injection import container
from app.core.logging import get_logger
from app.ingestion.knowledge_ingestion import KnowledgeIngestionService

logger = get_logger(__name__)


def _resolve_context_file() -> Path:
    data_dir = Path(__file__).resolve().parent.parent / "app" / "data"

    candidates = [
        data_dir / "business_context.json",
        data_dir / "business_context1.json",
    ]

    for path in candidates:
        if path.exists():
            return path

    raise FileNotFoundError(
        "No active business_context file found. Expected one of: "
        "business_context.json, business_context1.json"
    )


async def ingest_database_context() -> None:
    """Extract semantics, constraints, and examples and feed them to ChromaDB."""

    await container.initialize()
    ingestion_service = KnowledgeIngestionService(container.vector_memory)

    context_file = _resolve_context_file()
    logger.info("Loading business context from %s", context_file)

    with open(context_file, "r", encoding="utf-8") as f:
        business_context = json.load(f)

    semantic_layer = business_context.get("semantic_layer", []) or []
    constraints = business_context.get("constraints", []) or []
    examples = business_context.get("examples", []) or []

    logger.info(
        "Context counts semantic=%s constraints=%s examples=%s",
        len(semantic_layer),
        len(constraints),
        len(examples),
    )

    documents_to_embed: list[str] = []

    for item in semantic_layer:
        text = str(item).strip()
        if text and "TODO:" not in text:
            documents_to_embed.append(f"SEMANTIC DEFINITION:\n{text}")

    for item in constraints:
        text = str(item).strip()
        if text and "TODO:" not in text:
            documents_to_embed.append(f"BUSINESS CONSTRAINT:\n{text}")

    for item in examples:
        text = str(item).strip()
        if text:
            documents_to_embed.append(f"SQL EXAMPLE:\n{text}")

    if not documents_to_embed:
        raise RuntimeError(
            f"No knowledge chunks found in {context_file}. "
            "Make sure the active file contains non-empty top-level keys: "
            "semantic_layer, constraints, and examples."
        )

    logger.info(
        "Embedding %s knowledge chunks into Vector Memory...",
        len(documents_to_embed),
    )

    record_ids = await ingestion_service.ingest_documents(
        documents=documents_to_embed,
        source="db_context_script",
    )

    logger.info(
        "Successfully ingested %s context rules into the AI Knowledge base!",
        len(record_ids),
    )


if __name__ == "__main__":
    asyncio.run(ingest_database_context())