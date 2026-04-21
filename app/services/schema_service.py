# app/services/schema_service.py
"""
PRODUCTION GRADE schema service.
Changes from previous version:
  - select_examples: CRITICAL rules get floor=3, not unconditional 999
  - _fetch_rules_sync: fixes double-CRITICAL prefix at read time
  - _rules_cache: 2-minute TTL, invalidated on teach_rule
  - get_all_rules_raw: used by orchestrator for observability
  - invalidate_rules_cache: called when user teaches a new rule
"""

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

logger = get_logger(__name__)

_KEYWORD_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "have", "has",
    "had", "do", "does", "did", "will", "would", "could", "should", "for", "of",
    "in", "on", "at", "to", "from", "with", "by", "or", "and", "but", "not",
    "how", "what", "when", "where", "which", "who", "many", "much", "more",
    "most", "some", "any", "all", "show", "get", "give", "tell", "find", "list",
    "me", "my", "their", "its", "this", "that", "these", "those",
}


@dataclass
class UniversalSchema:
    """Live schema introspected from the database."""
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
        all_cols: list[str] = []
        for cols in self.schema_dict.values():
            all_cols.extend(cols)
        return all_cols

    def to_prompt_block(self) -> str:
        lines = [
            f"Target Execution Dialect: {self.dialect.upper()}",
            f"Dataset Name: {self.dataset_name}",
            "Schema Structure:",
        ]
        for table, cols in self.schema_dict.items():
            lines.append(f"\nTable: {table}")
            for col in cols:
                lines.append(f"  {col}")
        return "\n".join(lines)


class SchemaService:
    """
    Live schema introspection + business rules loader.
    Fully DB-driven — no hardcoded table names, column names, or status values.
    """

    def __init__(self, repository: Any = None) -> None:
        self._cache: TTLCache[UniversalSchema] = TTLCache(settings.schema_cache_ttl_seconds)
        self._rules_cache: TTLCache[list[str]] = TTLCache(120)  # 2-minute rules cache

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

    def _fetch_schema_sync(self, tenant_id: str) -> UniversalSchema:
        schema_dict: dict[str, list[str]] = {}
        dataset = settings.db_name
        dialect = "mysql"

        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = %s
                          AND TABLE_NAME NOT LIKE 'meta_%%'
                        ORDER BY TABLE_NAME, ORDINAL_POSITION;
                        """,
                        (settings.db_name,),
                    )
                    for row in cursor.fetchall():
                        t = row["TABLE_NAME"]
                        c = row["COLUMN_NAME"]
                        d = row["DATA_TYPE"]
                        pk = " [PRIMARY KEY]" if row["COLUMN_KEY"] == "PRI" else ""
                        schema_dict.setdefault(t, [])
                        schema_dict[t].append(f"- {c} ({d}){pk}")
        except Exception as exc:
            logger.error("Schema introspection failed: %s", exc)
            return UniversalSchema("error", "error", {}, "error_fingerprint")

        fingerprint = hashlib.md5(str(schema_dict).encode()).hexdigest()
        return UniversalSchema(
            dataset_name=dataset,
            dialect=dialect,
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

    def _fetch_rules_sync(self, tenant_id: str) -> list[str]:
        """
        Fetch business rules from DB with 2-minute caching.
        Cleans double-CRITICAL prefix at read time so all downstream code
        receives clean rules regardless of what's stored in the DB.
        """
        cache_key = f"rules_{tenant_id}_{settings.db_name}"
        cached = self._rules_cache.get(cache_key)
        if cached is not None:
            return cached

        rules: list[str] = []
        double_prefix_count = 0

        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT rule_definition, is_critical "
                        f"FROM {settings.meta_table_rules} "
                        f"WHERE tenant_id = %s OR tenant_id = 'default' "
                        f"ORDER BY is_critical DESC, rule_id",
                        (tenant_id,),
                    )
                    for row in cursor.fetchall():
                        defn = str(row.get("rule_definition", "")).strip()
                        if not defn:
                            continue
                        # Fix double CRITICAL prefix (DB storage bug)
                        if re.match(r"^CRITICAL\s*:\s*CRITICAL\s*:", defn, re.IGNORECASE):
                            defn = re.sub(
                                r"^CRITICAL\s*:\s*CRITICAL\s*:\s*",
                                "CRITICAL: ",
                                defn,
                                flags=re.IGNORECASE,
                            )
                            double_prefix_count += 1
                        rules.append(defn)
        except Exception as exc:
            logger.warning("Business rules fetch failed: %s", exc)

        if double_prefix_count > 0:
            logger.warning(
                "Fixed %d double-CRITICAL prefix rules at read time for tenant=%s. "
                "Run business_rules_final.sql to fix them in the database permanently.",
                double_prefix_count,
                tenant_id,
            )

        self._rules_cache.set(cache_key, rules)
        logger.info(
            "Loaded %d business rules for tenant=%s (double_prefix_fixed=%d)",
            len(rules),
            tenant_id,
            double_prefix_count,
        )
        return rules

    def select_examples(
        self,
        question: str,
        tenant_id: str = "default",
        limit: int = 5,
    ) -> str:
        """
        Select business rules by RELEVANCE to the question.

        FIX vs old code: CRITICAL rules previously got score=999 unconditionally.
        That injected ALL critical rules regardless of relevance, causing:
          - Two contradicting critical rules both being injected
          - LLM getting confused and picking the wrong one

        Now: CRITICAL rules get a minimum floor score of 3 (boosted but not unconditional).
        A CRITICAL rule with zero keyword overlap still gets score=3 — enough to be
        included if fewer than 5 rules are selected. But it won't dominate when
        more-relevant rules are present.
        """
        t_start = time.perf_counter()
        rules = self._fetch_rules_sync(tenant_id)
        if not rules:
            return ""

        question_tokens = {
            t.lower()
            for t in _KEYWORD_PATTERN.findall(question)
            if t.lower() not in _STOP_WORDS and len(t) > 2
        }

        scored: list[tuple[int, str]] = []
        seen: set[str] = set()

        for rule in rules:
            clean = rule.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)

            lower = clean.lower()
            rule_tokens = {
                t.lower()
                for t in _KEYWORD_PATTERN.findall(clean)
                if t.lower() not in _STOP_WORDS and len(t) > 2
            }
            overlap = len(question_tokens & rule_tokens)

            if re.match(r"^critical\s*:", lower):
                score = max(overlap, 3)   # floor=3: boosted but not unconditional
            else:
                score = overlap           # optional: need ≥1 keyword match

            if score > 0:
                scored.append((score, clean))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [rule for _, rule in scored[:limit]]

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "Rule selection for tenant=%s: question_tokens=%d | candidates=%d | selected=%d | elapsed_ms=%.1f",
            tenant_id,
            len(question_tokens),
            len(scored),
            len(selected),
            elapsed_ms,
        )
        for idx, (score, rule) in enumerate(scored[:limit]):
            logger.debug("Rule[%d] score=%d: %.80s", idx, score, rule)

        return "\n\n".join(selected)

    def get_all_rules_raw(self, tenant_id: str = "default") -> list[str]:
        """Return all rules unscored. Used by QueryObserver for RAG health tracking."""
        return self._fetch_rules_sync(tenant_id)

    def invalidate_rules_cache(self, tenant_id: str = "default") -> None:
        """Force reload from DB on next call. Called after user teaches a new rule."""
        self._rules_cache.delete(f"rules_{tenant_id}_{settings.db_name}")
        logger.info("Rules cache invalidated for tenant=%s", tenant_id)