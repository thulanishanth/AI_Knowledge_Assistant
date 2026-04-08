from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.services.llm_client import call_llm

logger = get_logger(__name__)

_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
_BRAND_RE = re.compile(r"\b[a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)+\b")
_QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_\-/.]{2,}\b")
_SINGLE_QUOTED_TERM_RE = re.compile(r"'([^']+)'")
_DOUBLE_QUOTED_TERM_RE = re.compile(r"\"([^\"]+)\"")


class BusinessTermDetector:
    """
    Dynamic detector for strict business aliases / vendor names / channel names.

    It learns what NOT to treat as business terms from:
    - active schema columns
    - active business context JSON
    """

    def __init__(self) -> None:
        self._generic_stop_terms = {
            "average", "count", "total", "sum", "minimum", "maximum", "top", "bottom",
            "show", "list", "find", "get", "rows", "records", "data", "table", "column",
            "columns", "schema", "query", "price", "room", "rooms", "booking", "bookings",
            "patient", "patients", "customer", "customers", "employee", "employees",
            "what", "which", "when", "where", "why", "how", "who", "there", "their",
            "this", "that", "from", "into", "with", "without", "only", "all", "for",
            "and", "or", "but", "via", "like", "than", "then", "between", "using",
            "booked", "asked", "asking", "trying", "achieve",
            "highest", "lowest", "largest", "smallest", "most", "least",
        }

    def detect_terms(
        self,
        question: str,
        known_schema_columns: list[str] | None = None,
        active_context: dict[str, Any] | None = None,
    ) -> list[str]:
        clean_question = (question or "").strip()
        if not clean_question:
            return []

        schema_columns = {self._norm(col) for col in (known_schema_columns or []) if str(col).strip()}
        safe_terms = self._build_safe_terms(schema_columns=schema_columns, active_context=active_context or {})

        heuristic_terms = self._detect_terms_heuristically(clean_question, schema_columns, safe_terms)
        llm_terms = self._detect_terms_with_llm(clean_question, schema_columns, safe_terms)

        combined: list[str] = []
        seen: set[str] = set()

        for term in heuristic_terms + llm_terms:
            normalized = self._normalize_surface_term(term)
            if not normalized:
                continue

            lowered = self._norm(normalized)
            if lowered in seen:
                continue
            if self._should_ignore_term(lowered, schema_columns, safe_terms):
                continue

            seen.add(lowered)
            combined.append(normalized)

        return combined

    def _detect_terms_heuristically(
        self,
        question: str,
        schema_columns: set[str],
        safe_terms: set[str],
    ) -> list[str]:
        candidates: list[str] = []

        for match in _ACRONYM_RE.findall(question):
            lowered = self._norm(match)
            if not self._should_ignore_term(lowered, schema_columns, safe_terms):
                candidates.append(match)

        for match in _BRAND_RE.findall(question):
            lowered = self._norm(match)
            if not self._should_ignore_term(lowered, schema_columns, safe_terms):
                candidates.append(match)

        for left, right in _QUOTED_RE.findall(question):
            value = left or right
            lowered = self._norm(value)
            if value and not self._should_ignore_term(lowered, schema_columns, safe_terms):
                candidates.append(value)

        for token in _WORD_RE.findall(question):
            lowered = self._norm(token)
            if self._should_ignore_term(lowered, schema_columns, safe_terms):
                continue

            # Only strong signals:
            # - acronym
            # - dotted brand
            # - likely vendor/channel shorthand
            # - capitalized brand-like word
            if (
                token.isupper()
                or "." in token
                or self._looks_like_brand_or_alias(token)
            ):
                candidates.append(token)

        return candidates

    def _detect_terms_with_llm(
        self,
        question: str,
        schema_columns: set[str],
        safe_terms: set[str],
    ) -> list[str]:
        safe_term_preview = sorted(list(safe_terms))[:120]

        prompt = f"""
You extract ONLY strict business alias terms from user questions.

Return ONLY valid JSON as:
{{"terms": ["term1", "term2"]}}

Extract only:
- business abbreviations like GDS, OTA, OPD, IPD, ADR, LOS
- vendor names, brand names, partner names, channel names
- domain shorthand that usually requires explicit business mapping

Do NOT extract:
- schema columns
- metric phrases
- status phrases
- ordinary English filter phrases
- terms already covered by schema or active business context

Schema columns to ignore:
{sorted(schema_columns)}

Safe terms already understood by schema or active business context:
{safe_term_preview}

User question:
{question}
""".strip()

        try:
            raw = call_llm(prompt=prompt, max_tokens=100, temperature=0.0)
            clean = raw.strip().strip("`")
            if clean.lower().startswith("json"):
                clean = clean[4:].strip()

            data = json.loads(clean)
            terms = data.get("terms", [])
            if not isinstance(terms, list):
                return []

            final_terms = []
            for term in terms:
                normalized = self._normalize_surface_term(str(term))
                lowered = self._norm(normalized)
                if not normalized:
                    continue
                if self._should_ignore_term(lowered, schema_columns, safe_terms):
                    continue
                final_terms.append(normalized)

            return final_terms
        except Exception as exc:
            logger.debug("Business term LLM extraction fallback used due to: %s", exc)
            return []

    def _build_safe_terms(
        self,
        schema_columns: set[str],
        active_context: dict[str, Any],
    ) -> set[str]:
        safe_terms: set[str] = set()
        safe_terms.update(schema_columns)

        # Add schema column variants
        for col in list(schema_columns):
            safe_terms.add(col.replace("_", " "))
            safe_terms.add(col.replace(" ", "_"))

        # Parse known structured sections dynamically
        for item in active_context.get("term_mappings", []) or []:
            self._ingest_mapping_like_item(item, safe_terms)

        for item in active_context.get("status_mappings", []) or []:
            self._ingest_mapping_like_item(item, safe_terms)

        for item in active_context.get("metric_definitions", []) or []:
            self._ingest_mapping_like_item(item, safe_terms)

        # Parse semantic layer dynamically
        for item in active_context.get("semantic_layer", []) or []:
            text = str(item).strip()
            if not text:
                continue

            # Extract quoted terms
            for term in self._extract_quoted_terms(text):
                safe_terms.add(self._norm(term))

            # Also add category values like Not_Canceled, Canceled, Online, Offline
            for value in self._extract_quoted_terms(text):
                normalized = self._norm(value)
                safe_terms.add(normalized)
                safe_terms.add(normalized.replace("_", " "))
                safe_terms.add(normalized.replace("-", " "))

        # Parse examples to protect common safe phrases already represented in business context
        for item in active_context.get("examples", []) or []:
            text = str(item).strip()
            if not text:
                continue
            for term in self._extract_quoted_terms(text):
                safe_terms.add(self._norm(term))

        # Add a few dynamic phrase variants for status/category values
        expanded = set()
        for term in safe_terms:
            expanded.add(term)
            expanded.add(term.replace("_", " "))
            expanded.add(term.replace("-", " "))
        safe_terms.update(expanded)

        return safe_terms

    def _ingest_mapping_like_item(self, item: Any, safe_terms: set[str]) -> None:
        if isinstance(item, dict):
            for key in ("term", "canonical_term", "metric_name"):
                value = str(item.get(key, "")).strip()
                if value:
                    safe_terms.add(self._norm(value))

            aliases = item.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [part.strip() for part in re.split(r"[|,;]", aliases) if part.strip()]
            elif not isinstance(aliases, list):
                aliases = []

            for alias in aliases:
                safe_terms.add(self._norm(alias))

        else:
            text = str(item).strip()
            if not text:
                return
            for term in self._extract_quoted_terms(text):
                safe_terms.add(self._norm(term))

    def _should_ignore_term(
        self,
        lowered: str,
        schema_columns: set[str],
        safe_terms: set[str],
    ) -> bool:
        if not lowered:
            return True
        if lowered in self._generic_stop_terms:
            return True
        if lowered in schema_columns:
            return True
        if lowered.replace(" ", "_") in schema_columns:
            return True
        if lowered in safe_terms:
            return True
        return False

    @staticmethod
    def _extract_quoted_terms(text: str) -> list[str]:
        values = []
        values.extend(_SINGLE_QUOTED_TERM_RE.findall(text))
        values.extend(_DOUBLE_QUOTED_TERM_RE.findall(text))
        return [value.strip() for value in values if value.strip()]

    @staticmethod
    def _looks_like_brand_or_alias(token: str) -> bool:
        if not token:
            return False

        lowered = token.lower()
        if "." in token:
            return True
        if token.isupper():
            return True

        # Capitalized single token, likely vendor/brand
        if token[0].isupper() and len(token) >= 4 and "_" not in token:
            return True

        # Short uppercase-like business abbreviation written lowercase
        if lowered in {"gds", "ota", "opd", "ipd", "aov", "arpu", "los", "adr", "crm", "erp", "pms", "pos"}:
            return True

        return False

    @staticmethod
    def _normalize_surface_term(term: str) -> str:
        value = " ".join(str(term or "").split()).strip(" ,.:;")
        if len(value) < 2:
            return ""
        return value

    @staticmethod
    def _norm(term: str) -> str:
        value = str(term or "").strip().lower()
        value = value.replace("_", " ").replace("-", " ")
        value = " ".join(value.split())
        return value