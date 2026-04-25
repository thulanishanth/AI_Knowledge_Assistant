# app/services/ontology_resolver.py
from __future__ import annotations
import json
from dataclasses import dataclass

import pymysql
from app.core.settings import settings
from app.core.logging import get_logger
from app.core.cache import TTLCache

logger = get_logger(__name__)

@dataclass
class ResolvedTerm:
    canonical_term: str
    sql_template: str
    unit: str
    warnings: list[str]
    matched_phrase: str
    confidence: float

class OntologyResolver:
    """Dynamically resolves user business phrases to canonical SQL templates from the DB."""

    def __init__(self) -> None:
        # Cache the ontology for 5 minutes so we don't hit the DB on every single message
        self._cache: TTLCache[list[dict]] = TTLCache(300)

    def _get_db_connection(self):
        return pymysql.connect(
            host=settings.db_host, port=settings.db_port, user=settings.db_user,
            password=settings.db_password, database=settings.db_name,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=5,
        )

    def _fetch_ontology(self, tenant_id: str) -> list[dict]:
        cache_key = f"ontology_{tenant_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        ontology = []
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT canonical_term, user_phrases, sql_template, unit, warnings "
                        "FROM meta_ontology WHERE tenant_id = %s OR tenant_id = 'default'",
                        (tenant_id,)
                    )
                    for row in cursor.fetchall():
                        entry = dict(row)
                        entry["user_phrases"] = json.loads(entry.get("user_phrases", "[]"))
                        entry["warnings"] = json.loads(entry.get("warnings", "[]")) if entry.get("warnings") else []
                        ontology.append(entry)
            self._cache.set(cache_key, ontology)
        except Exception as e:
            logger.error(f"Failed to fetch ontology: {e}")

        return ontology

    def resolve(self, question: str, tenant_id: str, schema_columns: set[str] = None) -> list[ResolvedTerm]:
        """Finds business terms in the question and returns matched term objects."""
        ontology = self._fetch_ontology(tenant_id)
        if not ontology:
            return []

        q_lower = question.lower()
        matched: list[tuple[int, ResolvedTerm]] = []

        for entry in ontology:
            phrases = entry.get("user_phrases", [])
            canonical = entry["canonical_term"]

            hits = sum(1 for phrase in phrases if phrase.lower() in q_lower)
            if canonical.replace("_", " ") in q_lower:
                hits += 3

            if hits > 0:
                # Find the exact phrase they used for better prompting
                matched_phrase = canonical
                for p in phrases:
                    if p.lower() in q_lower:
                        matched_phrase = p
                        break

                term = ResolvedTerm(
                    canonical_term=canonical,
                    sql_template=entry["sql_template"],
                    unit=entry.get("unit", ""),
                    warnings=entry.get("warnings", []),
                    matched_phrase=matched_phrase,
                    confidence=1.0 if canonical.replace("_"," ") in q_lower else 0.8
                )
                matched.append((hits, term))

        matched.sort(key=lambda x: x[0], reverse=True)
        return [term for _, term in matched[:4]]

    def build_ontology_prompt_block(self, resolved: list[ResolvedTerm]) -> str:
        """Formats the list of resolved terms into a string block for the LLM."""
        if not resolved:
            return "(none matched — use schema columns and business rules directly)"

        lines = ["APPROVED BUSINESS TERM FORMULAS (use these exact SQL expressions):"]
        for term in top_matches:
            warn_str = f" | WARNING: {'; '.join(term.warnings[:2])}" if term.warnings else ""
            unit_str = f" [{term.unit}]" if term.unit else ""

            lines.append(
                f"  {term.canonical_term.upper()} (User said '{term.matched_phrase}'): "
                f"{term.sql_template}{unit_str}{warn_str}"
            )

        return "\n".join(lines)