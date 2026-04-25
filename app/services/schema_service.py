# app/services/schema_service.py
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

import pymysql

from app.core.cache import TTLCache
from app.core.settings import settings
from app.core.logging import get_logger
from app.services.hybrid_rule_retriever import HybridRuleRetriever

logger = get_logger(__name__)

@dataclass
class UniversalSchema:
    dataset_name: str
    dialect: str
    schema_dict: dict[str, list[str]]
    fingerprint: str

    @property
    def table_name(self) -> str:
        if self.schema_dict:
            return list(self.schema_dict.keys())[0]
        return self.dataset_name

    def to_prompt_block(self) -> str:
        lines = [f"Target Execution Dialect: {self.dialect.upper()}", f"Dataset Name: {self.dataset_name}", "Schema Structure:"]
        for table, cols in self.schema_dict.items():
            lines.append(f"\nTable: {table}")
            for col in cols: lines.append(f"  {col}")
        return "\n".join(lines)

class SchemaService:
    def __init__(self, repository: Any = None) -> None:
        self._cache: TTLCache[UniversalSchema] = TTLCache(settings.schema_cache_ttl_seconds)
        self._rules_cache: TTLCache[list[str]] = TTLCache(120)

        # Initialize Phase 5 Hybrid RAG
        self._hybrid_retriever = HybridRuleRetriever()

    def _get_db_connection(self):
        return pymysql.connect(
            host=settings.db_host, port=settings.db_port, user=settings.db_user,
            password=settings.db_password, database=settings.db_name,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=5,
        )

    def _fetch_schema_sync(self, tenant_id: str) -> UniversalSchema:
        schema_dict: dict[str, list[str]] = {}
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = %s AND TABLE_NAME NOT LIKE 'meta_%%'
                        ORDER BY TABLE_NAME, ORDINAL_POSITION;
                    """, (settings.db_name,))
                    for row in cursor.fetchall():
                        t, c, d = row["TABLE_NAME"], row["COLUMN_NAME"], row["DATA_TYPE"]
                        pk = " [PRIMARY KEY]" if row["COLUMN_KEY"] == "PRI" else ""
                        schema_dict.setdefault(t, [])
                        schema_dict[t].append(f"- {c} ({d}){pk}")
        except Exception as exc:
            logger.error("Schema introspection failed: %s", exc)
            return UniversalSchema("error", "error", {}, "error_fingerprint")

        fingerprint = hashlib.md5(str(schema_dict).encode()).hexdigest()
        return UniversalSchema(dataset_name=settings.db_name, dialect="mysql", schema_dict=schema_dict, fingerprint=fingerprint)

    async def get_schema(self, tenant_id: str = "default", force_refresh: bool = False) -> UniversalSchema:
        cache_key = f"live_schema_{settings.db_name}"
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None: return cached
        schema_obj = await asyncio.to_thread(self._fetch_schema_sync, tenant_id)
        if schema_obj.dataset_name != "error":
            self._cache.set(cache_key, schema_obj)
        return schema_obj

    def _fetch_rules_sync(self, tenant_id: str) -> list[str]:
        # CRITICAL RAG FIX: Default to settings.db_name if tenant_id is "default"
        active_tenant = settings.db_name if tenant_id == "default" else tenant_id

        cache_key = f"rules_{active_tenant}_{settings.db_name}"
        cached = self._rules_cache.get(cache_key)
        if cached is not None: return cached

        rules: list[str] = []
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT rule_definition FROM {settings.meta_table_rules} "
                        f"WHERE tenant_id = %s OR tenant_id = 'default' ORDER BY is_critical DESC", (active_tenant,)
                    )
                    for row in cursor.fetchall():
                        defn = str(row.get("rule_definition", "")).strip()
                        if defn:
                            if re.match(r"^CRITICAL\s*:\s*CRITICAL\s*:", defn, re.IGNORECASE):
                                defn = re.sub(r"^CRITICAL\s*:\s*CRITICAL\s*:\s*", "CRITICAL: ", defn, flags=re.IGNORECASE)
                            rules.append(defn)
        except Exception as exc:
            logger.warning("Business rules fetch failed: %s", exc)

        self._rules_cache.set(cache_key, rules)
        return rules

    async def select_examples(self, question: str, tenant_id: str = "default", limit: int = 5) -> str:
        """
        [PHASE 5] Dynamically selects the best rules using Semantic + Keyword Hybrid Search.
        """
        t_start = time.perf_counter()
        rules = self._fetch_rules_sync(tenant_id)
        if not rules:
            return ""

        # Use the powerful Hybrid Retriever
        top_rules = await self._hybrid_retriever.retrieve(question, rules, top_k=limit)

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info("Hybrid RAG selected %d rules for tenant='%s' in %.1f ms", len(top_rules), tenant_id, elapsed_ms)

        for idx, rule in enumerate(top_rules):
            logger.debug(f"HybridRank[{idx}] (RRF: {rule.rrf_score:.4f}): {rule.rule_text[:80]}...")

        return "\n\n".join([r.rule_text for r in top_rules])

    def get_all_rules_raw(self, tenant_id: str = "default") -> list[str]:
        return self._fetch_rules_sync(tenant_id)

    def invalidate_rules_cache(self, tenant_id: str = "default") -> None:
        self._rules_cache.delete(f"rules_{tenant_id}_{settings.db_name}")