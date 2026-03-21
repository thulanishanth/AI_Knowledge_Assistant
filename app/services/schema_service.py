#app/services/schema_service.py
"""Cached live-schema access and prompt enrichment."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.core.cache import TTLCache
from app.core.settings import settings
from app.infrastructure.repositories.schema_repository import SchemaRepository, TableSchema

_KEYWORD_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")


class SchemaService:
    """Expose cached schema metadata plus relevant example snippets."""

    def __init__(self, repository: SchemaRepository) -> None:
        self._repository = repository
        self._cache: TTLCache[TableSchema] = TTLCache(settings.schema_cache_ttl_seconds)
        self._examples_file = Path(__file__).resolve().parents[1] / "data" / "examples.txt"

    async def get_schema(self, force_refresh: bool = False) -> TableSchema:
        cache_key = f"{settings.db_name}:{settings.db_table}"
        if not force_refresh and (cached := self._cache.get(cache_key)) is not None:
            return cached
        schema = await asyncio.to_thread(self._repository.get_active_schema)
        self._cache.set(cache_key, schema)
        return schema

    def select_examples(self, question: str, schema: TableSchema, limit: int = 3) -> str:
        """Return only examples that overlap with the current question/schema."""
        try:
            raw_examples = self._examples_file.read_text(encoding="utf-8")
        except OSError:
            return ""

        blocks = [block.strip() for block in re.split(r"\n\s*\n", raw_examples) if block.strip()]
        question_tokens = {token.lower() for token in _KEYWORD_PATTERN.findall(question)}
        schema_tokens = {token.lower() for token in schema.column_names}
        scored: list[tuple[int, str]] = []
        for block in blocks:
            lowered = block.lower()
            hits = sum(token in lowered for token in question_tokens)
            hits += sum(token in lowered for token in schema_tokens)
            if hits >= 3:
                scored.append((hits, block))

        scored.sort(key=lambda item: item[0], reverse=True)
        return "\n\n".join(block for _, block in scored[:limit])

    @staticmethod
    def question_mentions_schema(question: str, schema: TableSchema) -> bool:
        lowered = question.lower()
        column_terms = {
            column.name.lower().replace("_", " ")
            for column in schema.columns
        }
        sample_terms = {
            sample.lower()
            for column in schema.columns
            for sample in column.sample_values
        }
        return any(term in lowered for term in column_terms.union(sample_terms))
