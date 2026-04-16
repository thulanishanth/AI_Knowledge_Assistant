# app/services/schema_service.py
"""Cached live-schema access and prompt enrichment from Universal Context."""

from __future__ import annotations

import asyncio
import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.cache import TTLCache
from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_KEYWORD_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")
_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@dataclass
class UniversalSchema:
    """Replaces the old MySQL TableSchema with a dialect-aware generic schema."""
    dataset_name: str
    dialect: str
    schema_dict: dict[str, list[str]]
    fingerprint: str

    @property
    def table_name(self) -> str:
        # Fallback to the first table key if multiple exist
        if self.schema_dict:
            return list(self.schema_dict.keys())[0]
        return self.dataset_name

    @property
    def columns(self) -> list[str]:
        all_cols = []
        for cols in self.schema_dict.values():
            all_cols.extend(cols)
        return all_cols
    
    def to_prompt_block(self) -> str:
        """Used when the user explicitly asks 'what is the schema?'"""
        lines = [
            f"Target Execution Dialect: {self.dialect.upper()}",
            f"Dataset Name: {self.dataset_name}",
            "Schema Structure:"
        ]
        for table, cols in self.schema_dict.items():
            lines.append(f"\nTable: {table}")
            for col in cols:
                lines.append(f"  {col}")
        return "\n".join(lines)


class SchemaService:
    """Expose cached schema metadata and examples from the dynamic JSON."""

    def __init__(self, repository: Any = None) -> None:
        self._cache: TTLCache[UniversalSchema] = TTLCache(settings.schema_cache_ttl_seconds)

    async def get_schema(self, tenant_id: str = "default", force_refresh: bool = False) -> UniversalSchema:
        """Loads the schema universally from business_context.json."""
        cache_key = f"universal_schema_state_{tenant_id}"
        
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        # STRICTLY use business_context.json
        context_path = _DATA_DIR / "business_context.json"

        if not context_path.exists():
            logger.warning("business_context.json not found. Returning empty schema.")
            return UniversalSchema("unknown", "unknown", {}, "empty_fingerprint")

        try:
            with open(context_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            
            # SMART FALLBACK: If the JSON has the tenant_id as a top-level key, use that block. 
            # Otherwise, assume the whole file is the context.
            data = raw_data.get(tenant_id, raw_data)
            
            dataset = data.get("dataset", tenant_id)
            dialect = data.get("target_dialect", "unknown")
            schema_dict = data.get("schema", {})
            
            # Create a unique hash of the schema to use as a Cache Key for identical queries
            schema_str = json.dumps(schema_dict, sort_keys=True)
            fingerprint = hashlib.md5(schema_str.encode()).hexdigest()

            schema_obj = UniversalSchema(
                dataset_name=dataset,
                dialect=dialect,
                schema_dict=schema_dict,
                fingerprint=fingerprint
            )
            self._cache.set(cache_key, schema_obj)
            return schema_obj

        except Exception as e:
            logger.error(f"Failed to load universal schema: {e}")
            return UniversalSchema("error", "error", {}, "error_fingerprint")

    def select_examples(self, question: str, tenant_id: str = "default", limit: int = 3) -> str:
        """Return only examples that overlap with the current question."""
        # STRICTLY use business_context.json
        context_path = _DATA_DIR / "business_context.json"
        
        if not context_path.exists():
            return ""
            
        try:
            with open(context_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            
            # Smart fallback for nested vs flat JSON
            data = raw_data.get(tenant_id, raw_data)
            
            examples = data.get("examples", [])
            if not examples:
                return ""

            question_tokens = {token.lower() for token in _KEYWORD_PATTERN.findall(question)}
            
            scored = []
            for example in examples:
                lowered = str(example).lower()
                # Score the example based on keyword overlap
                hits = sum(token in lowered for token in question_tokens)
                scored.append((hits, example))

            # Sort by highest relevance
            scored.sort(key=lambda item: item[0], reverse=True)
            return "\n\n".join(block for _, block in scored[:limit])

        except Exception as e:
            logger.error(f"Failed to select dynamic examples: {e}")
            return ""