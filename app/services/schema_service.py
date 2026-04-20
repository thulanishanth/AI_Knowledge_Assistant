# app/services/schema_service.py
"""Cached live-schema introspection directly from the connected MySQL database."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any

import pymysql

from app.core.cache import TTLCache
from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_KEYWORD_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")

@dataclass
class UniversalSchema:
    """Represents the live schema introspected from the database."""
    dataset_name: str
    dialect: str
    schema_dict: dict[str, list[str]]
    fingerprint: str

    @property
    def table_name(self) -> str:
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
        """Used to inject the live database schema into the LLM prompt."""
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
    """Introspects the live database to get tables and columns automatically."""

    def __init__(self, repository: Any = None) -> None:
        self._cache: TTLCache[UniversalSchema] = TTLCache(settings.schema_cache_ttl_seconds)

    def _get_db_connection(self):
        """Helper to get a fresh database connection."""
        return pymysql.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            cursorclass=pymysql.cursors.DictCursor
        )

    def _fetch_schema_sync(self, tenant_id: str) -> UniversalSchema:
        """Synchronous method to introspect the live MySQL schema."""
        schema_dict = {}
        dataset = settings.db_name
        dialect = "mysql"

        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # MAGIC HAPPENS HERE: We query MySQL's internal system tables!
                    # We exclude any tables starting with 'meta_' so the AI doesn't see its own brain.
                    cursor.execute("""
                        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = %s
                          AND TABLE_NAME NOT LIKE 'meta_%%'
                        ORDER BY TABLE_NAME, ORDINAL_POSITION;
                    """, (settings.db_name,))
                    rows = cursor.fetchall()

                    for row in rows:
                        t_name = row["TABLE_NAME"]
                        c_name = row["COLUMN_NAME"]
                        dtype  = row["DATA_TYPE"]
                        pk     = " [PRIMARY KEY]" if row["COLUMN_KEY"] == "PRI" else ""
                        
                        if t_name not in schema_dict:
                            schema_dict[t_name] = []
                        
                        schema_dict[t_name].append(f"- {c_name} ({dtype}){pk}")

        except Exception as e:
            logger.error(f"Failed to introspect database schema: {e}")
            return UniversalSchema("error", "error", {}, "error_fingerprint")

        # Create a hash of the schema to use as a Cache Key
        schema_str = str(schema_dict)
        fingerprint = hashlib.md5(schema_str.encode()).hexdigest()

        return UniversalSchema(
            dataset_name=dataset,
            dialect=dialect,
            schema_dict=schema_dict,
            fingerprint=fingerprint
        )

    async def get_schema(self, tenant_id: str = "default", force_refresh: bool = False) -> UniversalSchema:
        """Loads the schema dynamically directly from MySQL."""
        cache_key = f"live_schema_state_{settings.db_name}"

        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        # Run the blocking MySQL query in a thread to prevent freezing FastAPI
        schema_obj = await asyncio.to_thread(self._fetch_schema_sync, tenant_id)

        if schema_obj.dataset_name != "error":
            self._cache.set(cache_key, schema_obj)

        return schema_obj

    def _fetch_rules_sync(self, tenant_id: str) -> list[str]:
        """Synchronous helper to fetch human business rules from the database."""
        rules = []
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # We still fetch manual business rules because MySQL can't automate human logic!
                    cursor.execute(
                        f"SELECT rule_definition, is_critical FROM {settings.meta_table_rules}"
                    )
                    rows = cursor.fetchall()
                    for row in rows:
                        prefix = "CRITICAL: " if row.get("is_critical") else ""
                        rules.append(f"{prefix}{row['rule_definition']}")
        except Exception as e:
            logger.warning(f"No business rules found or meta_business_rules table missing: {e}")
        return rules

    def select_examples(self, question: str, tenant_id: str = "default", limit: int = 5) -> str:
        """Dynamically fetches and scores business rules directly from MySQL."""

        rules = self._fetch_rules_sync(tenant_id)
        if not rules:
            return ""

        question_tokens = {token.lower() for token in _KEYWORD_PATTERN.findall(question)}

        scored = []
        seen_rules = set()

        for rule in rules:
            clean_rule = str(rule).strip()
            if clean_rule in seen_rules:
                continue
            seen_rules.add(clean_rule)

            # Force critical rules to always be included with an artificially high score
            if clean_rule.startswith("CRITICAL:"):
                scored.append((999, clean_rule))
                continue

            lowered = clean_rule.lower()
            # Score the rule based on keyword overlap
            hits = sum(token in lowered for token in question_tokens)
            if hits > 0:
                scored.append((hits, clean_rule))

        # Sort by highest relevance
        scored.sort(key=lambda item: item[0], reverse=True)
        return "\n\n".join(block for _, block in scored[:limit])