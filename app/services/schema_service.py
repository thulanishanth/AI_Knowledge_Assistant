# app/services/schema_service.py
"""
Schema service with rich data profiling.

The schema + sample values + statistics IS the domain knowledge.
No hardcoded domain terms — the LLM reads the data and understands it.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import pymysql

from app.core.cache import TTLCache
from app.core.settings import settings
from app.core.logging import get_logger
from app.services.hybrid_rule_retriever import HybridRuleRetriever

logger = get_logger(__name__)

_NUMERIC_TYPES = frozenset({
    "int", "bigint", "decimal", "float", "double",
    "numeric", "tinyint", "smallint", "mediumint", "real",
})
_TEXT_TYPES = frozenset({
    "varchar", "text", "char", "tinytext",
    "mediumtext", "longtext", "enum",
})
_MAX_CATEGORICAL = 30  # columns with ≤ this many distinct values get value enumeration


@dataclass
class ColumnProfile:
    name: str
    data_type: str
    is_primary_key: bool = False
    allowed_values: list[str] = field(default_factory=list)
    min_val: Any = None
    max_val: Any = None
    avg_val: Any = None
    null_pct: float = 0.0
    distinct_count: int = 0

    def to_prompt_line(self) -> str:
        parts = [f"  - {self.name} ({self.data_type})"]
        if self.is_primary_key:
            parts.append(" [PRIMARY KEY]")
        if self.allowed_values:
            quoted = ", ".join(repr(v) for v in self.allowed_values[:20])
            parts.append(f" [Values: {quoted}]")
        elif self.min_val is not None:
            avg_str = (
                f", Avg: {self.avg_val:.2f}" if self.avg_val is not None else ""
            )
            parts.append(f" [Range: {self.min_val} → {self.max_val}{avg_str}]")
        if self.null_pct > 5.0:
            parts.append(f" [~{self.null_pct:.0f}% null]")
        return "".join(parts)


@dataclass
class UniversalSchema:
    dataset_name: str
    dialect: str
    columns: dict[str, list[ColumnProfile]] = field(default_factory=dict)
    schema_dict: dict[str, list[str]] = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def table_name(self) -> str:
        if self.columns:
            return list(self.columns.keys())[0]
        return self.dataset_name

    def to_prompt_block(self) -> str:
        """Rich schema block — LLM reads this to understand the full data domain."""
        lines = [
            f"Database: {self.dataset_name}",
            f"Dialect: {self.dialect.upper()}",
            "",
            "SCHEMA  (column [Values] and [Range] show actual data — use them to map "
            "user terms to the correct column values):",
        ]
        for table_name, cols in self.columns.items():
            lines.append(f"\nTable: `{table_name}`")
            for col in cols:
                lines.append(col.to_prompt_line())
        return "\n".join(lines)


class SchemaService:
    def __init__(self, repository: Any = None) -> None:
        self._cache: TTLCache[UniversalSchema] = TTLCache(settings.schema_cache_ttl_seconds)
        self._rules_cache: TTLCache[list[str]] = TTLCache(120)
        self._hybrid_retriever = HybridRuleRetriever()

    def _get_db_connection(self):
        return pymysql.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
        )

    # ─────────────────────────────────────────────
    # RICH SCHEMA FETCHING
    # ─────────────────────────────────────────────

    def _fetch_schema_sync(self, tenant_id: str) -> UniversalSchema:
        columns_by_table: dict[str, list[ColumnProfile]] = {}
        schema_dict: dict[str, list[str]] = {}

        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE,
                               IS_NULLABLE, COLUMN_KEY
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = %s
                          AND TABLE_NAME NOT LIKE 'meta_%%'
                        ORDER BY TABLE_NAME, ORDINAL_POSITION
                        """,
                        (settings.db_name,),
                    )
                    rows = cursor.fetchall()

                    tables: dict[str, list[dict]] = {}
                    for row in rows:
                        tables.setdefault(row["TABLE_NAME"], []).append(row)

                    for table, col_rows in tables.items():
                        profiles: list[ColumnProfile] = []

                        for row in col_rows:
                            col = row["COLUMN_NAME"]
                            dtype = row["DATA_TYPE"].lower()
                            is_pk = row["COLUMN_KEY"] == "PRI"

                            profile = ColumnProfile(
                                name=col,
                                data_type=dtype,
                                is_primary_key=is_pk,
                            )

                            if is_pk:
                                profiles.append(profile)
                                continue

                            try:
                                # Null percentage
                                cursor.execute(
                                    f"SELECT COUNT(*) as total, "
                                    f"SUM(CASE WHEN `{col}` IS NULL THEN 1 ELSE 0 END) as nulls "
                                    f"FROM `{table}`"
                                )
                                stat = cursor.fetchone()
                                total = max(stat["total"] or 1, 1)
                                profile.null_pct = (stat["nulls"] or 0) / total * 100

                                if dtype in _TEXT_TYPES:
                                    cursor.execute(
                                        f"SELECT COUNT(DISTINCT `{col}`) as d FROM `{table}`"
                                    )
                                    d_count = cursor.fetchone()["d"] or 0
                                    profile.distinct_count = d_count

                                    if 0 < d_count <= _MAX_CATEGORICAL:
                                        cursor.execute(
                                            f"SELECT DISTINCT `{col}` as v FROM `{table}` "
                                            f"WHERE `{col}` IS NOT NULL "
                                            f"ORDER BY `{col}` LIMIT {_MAX_CATEGORICAL}"
                                        )
                                        profile.allowed_values = [
                                            str(r["v"])
                                            for r in cursor.fetchall()
                                            if r["v"] is not None
                                        ]

                                elif dtype in _NUMERIC_TYPES:
                                    cursor.execute(
                                        f"SELECT MIN(`{col}`) as mn, MAX(`{col}`) as mx, "
                                        f"AVG(`{col}`) as av FROM `{table}`"
                                    )
                                    stat = cursor.fetchone()
                                    profile.min_val = stat["mn"]
                                    profile.max_val = stat["mx"]
                                    if stat["av"] is not None:
                                        try:
                                            profile.avg_val = float(stat["av"])
                                        except (TypeError, ValueError):
                                            pass

                            except Exception as prof_err:
                                logger.debug(
                                    "Column profile error %s.%s: %s", table, col, prof_err
                                )

                            profiles.append(profile)

                        columns_by_table[table] = profiles
                        schema_dict[table] = [p.to_prompt_line() for p in profiles]

        except Exception as exc:
            logger.error("Schema introspection failed: %s", exc)
            return UniversalSchema("error", "mysql", {}, {}, "error_fingerprint")

        fingerprint = hashlib.md5(str(schema_dict).encode()).hexdigest()
        return UniversalSchema(
            dataset_name=settings.db_name,
            dialect="mysql",
            columns=columns_by_table,
            schema_dict=schema_dict,
            fingerprint=fingerprint,
        )

    async def get_schema(
        self, tenant_id: str = "default", force_refresh: bool = False
    ) -> UniversalSchema:
        cache_key = f"live_schema_{settings.db_name}"
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        schema_obj = await asyncio.to_thread(self._fetch_schema_sync, tenant_id)
        if schema_obj.dataset_name != "error":
            self._cache.set(cache_key, schema_obj)
        return schema_obj

    # ─────────────────────────────────────────────
    # BUSINESS RULES (backup enrichment only)
    # ─────────────────────────────────────────────

    def _fetch_rules_sync(self, tenant_id: str) -> list[str]:
        active_tenant = settings.db_name if tenant_id == "default" else tenant_id
        cache_key = f"rules_{active_tenant}"
        cached = self._rules_cache.get(cache_key)
        if cached is not None:
            return cached

        rules: list[str] = []
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT rule_definition FROM {settings.meta_table_rules} "
                        f"WHERE tenant_id = %s OR tenant_id = 'default' "
                        f"ORDER BY is_critical DESC",
                        (active_tenant,),
                    )
                    for row in cursor.fetchall():
                        defn = str(row.get("rule_definition", "")).strip()
                        if defn:
                            rules.append(defn)
        except Exception as exc:
            logger.warning("Business rules fetch skipped (non-fatal): %s", exc)

        self._rules_cache.set(cache_key, rules)
        return rules

    async def select_examples(
        self, question: str, tenant_id: str = "default", limit: int = 5
    ) -> str:
        """Top-k rules most relevant to this question (backup enrichment)."""
        rules = self._fetch_rules_sync(tenant_id)
        if not rules:
            return ""
        top_rules = await self._hybrid_retriever.retrieve(question, rules, top_k=limit)
        logger.info(
            "Hybrid RAG: %d rules selected for question (backup enrichment)", len(top_rules)
        )
        return "\n\n".join(r.rule_text for r in top_rules)

    def get_all_rules_raw(self, tenant_id: str = "default") -> list[str]:
        return self._fetch_rules_sync(tenant_id)

    def invalidate_schema_cache(self) -> None:
        self._cache.clear()

    def invalidate_rules_cache(self, tenant_id: str = "default") -> None:
        self._rules_cache.delete(f"rules_{tenant_id}")