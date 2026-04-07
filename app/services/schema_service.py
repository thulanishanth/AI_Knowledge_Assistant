#app/services/schema_service.py
from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.settings import settings
from app.infrastructure.repositories.schema_repository import SchemaRepository, TableSchema

logger = get_logger(__name__)

_KEYWORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


class SchemaService:
    """Cached schema access plus optional local business-context helpers."""

    def __init__(self, schema_repository: SchemaRepository) -> None:
        self._schema_repository = schema_repository
        self._cache: TTLCache[TableSchema] = TTLCache(settings.schema_cache_ttl_seconds)
        self._data_dir = Path(__file__).resolve().parent.parent / "data"
        self._context_files = [
            self._data_dir / "business_context.json",
            self._data_dir / "business_metadata.json",
        ]

    async def get_schema(self) -> TableSchema:
        cache_key = f"{settings.db_name}.{settings.db_table}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        schema = self._schema_repository.get_active_schema()
        self._cache.set(cache_key, schema)
        return schema

    def get_business_context(self) -> dict:
        for path in self._context_files:
            if not path.exists():
                continue
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to read business context from %s: %s", path, exc)
        return {}

    def get_local_rule_block(self) -> str:
        data = self.get_business_context()
        lines: list[str] = []

        for item in data.get("semantic_layer", []):
            text = str(item).strip()
            if text and "TODO:" not in text:
                lines.append(f"- {text}")

        for item in data.get("constraints", []):
            text = str(item).strip()
            if text and "TODO:" not in text:
                lines.append(f"- {text}")

        return "\n".join(lines[:20])

    def get_relevant_examples(
        self,
        question: str,
        schema: TableSchema,
        limit: int = 3,
    ) -> str:
        data = self.get_business_context()
        raw_examples = data.get("examples", [])
        if not raw_examples:
            return ""

        question_tokens = {token.lower() for token in _KEYWORD_PATTERN.findall(question or "")}
        schema_tokens = {token.lower() for token in schema.column_names}

        scored: list[tuple[int, str]] = []
        for item in raw_examples:
            block = str(item).strip()
            if not block:
                continue

            lowered = block.lower()
            hits = sum(token in lowered for token in question_tokens)
            hits += sum(token in lowered for token in schema_tokens)
            if hits > 0:
                scored.append((hits, block))

        if not scored:
            return "\n\n".join(str(item).strip() for item in raw_examples[:limit] if str(item).strip())

        scored.sort(key=lambda item: item[0], reverse=True)
        return "\n\n".join(block for _, block in scored[:limit])

    @staticmethod
    def question_mentions_schema(question: str, schema: TableSchema) -> bool:
        lowered = (question or "").lower()
        column_terms = {column.name.lower().replace("_", " ") for column in schema.columns}
        sample_terms = {
            sample.lower()
            for column in schema.columns
            for sample in column.sample_values
        }
        return any(term in lowered for term in column_terms.union(sample_terms))