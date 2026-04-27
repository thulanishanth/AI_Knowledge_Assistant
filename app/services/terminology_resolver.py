# app/services/terminology_resolver.py
"""
Terminology Resolution Pipeline.

When a user mentions a business term, brand name, platform name, or
domain-specific phrase that the LLM may not directly map to a schema column,
this service:

1. Detects unknown / ambiguous terms in the question.
2. For each term, asks the LLM what it likely means in the context of the
   business domain AND which schema column/value it maps to.
3. Builds a resolved context block that is injected into the SQL prompt so
   the SQL generator knows exactly what filter to apply.

Examples:
  "booking.com"        → market_segment_type = 'Online' or column 'source'
  "walk-in"            → market_segment_type = 'Walk-In' (or similar)
  "last 1000 days"     → arrival_year/month/date computed from today
  "VIP guests"         → no_of_special_requests > 2 (or repeated_guest = 1)
  "long stays"         → (no_of_weekend_nights + no_of_week_nights) > 7
  "high season"        → arrival_month IN (6,7,8,12)
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.core.logging import get_logger
from app.services.llm_client import call_llm_with_tool

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ResolvedTerm:
    """One resolved business / brand term."""
    raw_term: str           # exactly as the user wrote it
    category: str           # "platform", "segment", "time_expression",
                            # "guest_type", "booking_type", "custom"
    meaning: str            # plain-English explanation
    sql_condition: str      # the WHERE clause fragment, e.g. "market_segment_type = 'Online'"
    confidence: float = 0.8
    needs_clarification: bool = False
    clarification_question: str = ""


@dataclass
class TerminologyResolution:
    """Full resolution result for one question."""
    original_question: str
    resolved_terms: list[ResolvedTerm] = field(default_factory=list)
    enriched_question: str = ""   # question rewritten with explicit conditions
    sql_context_block: str = ""   # ready-to-inject context for the SQL prompt
    had_unknown_terms: bool = False
    needs_clarification: bool = False
    clarification_question: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Regex signals — terms that suggest unknown business language
# ─────────────────────────────────────────────────────────────────────────────

# Known booking / travel platform names
_PLATFORM_PATTERN = re.compile(
    r"\b(booking\.com|expedia|airbnb|agoda|makemytrip|trivago|tripadvisor|"
    r"hotels\.com|goibibo|cleartrip|yatra|ola|uber|vrbo|hostelworld|"
    r"direct\s*booking|walk[\s-]?in|corporate|travel\s*agent|ota)\b",
    re.IGNORECASE,
)

# Relative time expressions that need date math
_TIME_EXPR_PATTERN = re.compile(
    r"\b(last\s+\d+\s+(days?|weeks?|months?|years?)|"
    r"past\s+\d+\s+(days?|weeks?|months?|years?)|"
    r"previous\s+\d+\s+(days?|weeks?|months?|years?)|"
    r"this\s+(week|month|quarter|year)|"
    r"last\s+(week|month|quarter|year)|"
    r"year[\s-]?to[\s-]?date|ytd|mtd|qtd)\b",
    re.IGNORECASE,
)

# Guest / customer type phrases
_GUEST_TYPE_PATTERN = re.compile(
    r"\b(vip|loyalty|premium|regular|first[\s-]?time|new|returning|repeat|"
    r"corporate|leisure|business\s+travel(?:l?er)?|family|solo|couple|group)\b",
    re.IGNORECASE,
)

# Stay / booking type phrases
_BOOKING_TYPE_PATTERN = re.compile(
    r"\b(long[\s-]?stay|short[\s-]?stay|extended\s+stay|day[\s-]?use|"
    r"overnight|weekend\s+stay|weekday\s+stay|high\s+season|low\s+season|"
    r"peak|off[\s-]?peak|blackout|no[\s-]?show|early\s+check[\s-]?in|"
    r"late\s+check[\s-]?out)\b",
    re.IGNORECASE,
)

_ALL_SIGNALS = [
    _PLATFORM_PATTERN,
    _TIME_EXPR_PATTERN,
    _GUEST_TYPE_PATTERN,
    _BOOKING_TYPE_PATTERN,
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas
# ─────────────────────────────────────────────────────────────────────────────

_RESOLVE_TOOL = {
    "name": "submit_term_resolution",
    "description": "Submit the resolution of all unknown business terms found in the question.",
    "parameters": {
        "type": "object",
        "properties": {
            "terms": {
                "type": "array",
                "description": "List of resolved terms.",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw_term": {
                            "type": "string",
                            "description": "The exact phrase from the user's question.",
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "platform",
                                "segment",
                                "time_expression",
                                "guest_type",
                                "booking_type",
                                "meal_plan",
                                "room_type",
                                "custom",
                                "unknown",
                            ],
                        },
                        "meaning": {
                            "type": "string",
                            "description": "Plain-English meaning of this term in a hotel context.",
                        },
                        "sql_condition": {
                            "type": "string",
                            "description": (
                                "The exact SQL WHERE clause fragment that represents this term. "
                                "Use only columns from the schema provided. "
                                "Example: \"market_segment_type = 'Online'\" "
                                "or \"(no_of_weekend_nights + no_of_week_nights) > 7\" "
                                "or \"arrival_year = 2022 AND arrival_month >= 6\". "
                                "If you cannot map it, write UNKNOWN."
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "description": "0.0–1.0 confidence in this mapping.",
                        },
                        "needs_clarification": {
                            "type": "boolean",
                            "description": "True if the term is too ambiguous to map without asking the user.",
                        },
                        "clarification_question": {
                            "type": "string",
                            "description": "If needs_clarification is true, what to ask the user.",
                        },
                    },
                    "required": [
                        "raw_term", "category", "meaning",
                        "sql_condition", "confidence",
                    ],
                },
            },
            "enriched_question": {
                "type": "string",
                "description": (
                    "Rewrite the original question replacing each unknown term with its "
                    "explicit database condition. This will be used as the final question "
                    "for SQL generation."
                ),
            },
        },
        "required": ["terms", "enriched_question"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Main service
# ─────────────────────────────────────────────────────────────────────────────

class TerminologyResolver:
    """
    Detects and resolves unknown business / domain terms before SQL generation.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def has_unknown_terms(self, question: str) -> bool:
        """Fast pre-filter: does the question contain signals of unknown terms?"""
        return any(p.search(question) for p in _ALL_SIGNALS)

    async def resolve(
        self,
        question: str,
        schema_block: str,
        business_rules: str = "",
        model: str = "Qwen/Qwen2.5-7B-Instruct",
    ) -> TerminologyResolution:
        """
        Resolve all unknown terms in the question.
        Returns a TerminologyResolution with:
          - resolved_terms: what each term maps to
          - enriched_question: rewritten question for SQL generation
          - sql_context_block: ready-to-inject context block
        """
        detected = self._detect_terms(question)
        if not detected:
            return TerminologyResolution(
                original_question=question,
                enriched_question=question,
                had_unknown_terms=False,
            )

        logger.info(
            "TerminologyResolver: detected %d unknown term(s) in: %s",
            len(detected),
            question,
        )

        # Handle relative time expressions locally without LLM (faster + more accurate)
        time_resolved = self._resolve_time_expressions_locally(question)

        prompt = self._build_prompt(question, detected, schema_block, business_rules, time_resolved)

        try:
            parsed = await asyncio.to_thread(
                call_llm_with_tool,
                prompt,
                _RESOLVE_TOOL,
                "submit_term_resolution",
                600,
                2,
                0.0,
            )
        except Exception as e:
            logger.error("TerminologyResolver LLM call failed: %s", e)
            return TerminologyResolution(
                original_question=question,
                enriched_question=question,
                had_unknown_terms=True,
            )

        if not parsed:
            return TerminologyResolution(
                original_question=question,
                enriched_question=question,
                had_unknown_terms=True,
            )

        resolved_terms: list[ResolvedTerm] = []
        needs_clarification = False
        clarification_question = ""

        for t in parsed.get("terms", []):
            term = ResolvedTerm(
                raw_term=t.get("raw_term", ""),
                category=t.get("category", "custom"),
                meaning=t.get("meaning", ""),
                sql_condition=t.get("sql_condition", "UNKNOWN"),
                confidence=float(t.get("confidence", 0.5)),
                needs_clarification=bool(t.get("needs_clarification", False)),
                clarification_question=t.get("clarification_question", ""),
            )
            resolved_terms.append(term)

            if term.needs_clarification and not needs_clarification:
                needs_clarification = True
                clarification_question = term.clarification_question

        # Merge local time resolutions (they override LLM for accuracy)
        for raw_term, sql_cond in time_resolved.items():
            for rt in resolved_terms:
                if raw_term.lower() in rt.raw_term.lower():
                    rt.sql_condition = sql_cond
                    rt.confidence = 1.0  # deterministic

        enriched_question = parsed.get("enriched_question", "").strip() or question
        sql_context_block = self._build_sql_context_block(resolved_terms)

        logger.info("TerminologyResolver result:\n%s", sql_context_block)

        return TerminologyResolution(
            original_question=question,
            resolved_terms=resolved_terms,
            enriched_question=enriched_question,
            sql_context_block=sql_context_block,
            had_unknown_terms=True,
            needs_clarification=needs_clarification,
            clarification_question=clarification_question,
        )

    # ── Detection ─────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_terms(question: str) -> list[str]:
        """Return unique matched terms from all signal patterns."""
        found: list[str] = []
        seen: set[str] = set()
        for pattern in _ALL_SIGNALS:
            for m in pattern.finditer(question):
                term = m.group(0).strip()
                if term.lower() not in seen:
                    seen.add(term.lower())
                    found.append(term)
        return found

    # ── Local time expression resolver (no LLM needed) ───────────────────────

    @staticmethod
    def _resolve_time_expressions_locally(question: str) -> dict[str, str]:
        """
        Handle relative time phrases deterministically using today's date.
        Returns {raw_phrase: sql_condition}.
        """
        today = date.today()
        results: dict[str, str] = {}

        # Pattern: "last N days/weeks/months/years"
        for m in re.finditer(
            r"\b(last|past|previous)\s+(\d+)\s+(days?|weeks?|months?|years?)\b",
            question,
            re.IGNORECASE,
        ):
            raw = m.group(0)
            n = int(m.group(2))
            unit = m.group(3).lower().rstrip("s")

            if unit == "day":
                start = today - timedelta(days=n)
            elif unit == "week":
                start = today - timedelta(weeks=n)
            elif unit == "month":
                # approximate: 30 days per month
                start = today - timedelta(days=n * 30)
            else:  # year
                start = today - timedelta(days=n * 365)

            # Convert to year/month/date components used in the schema
            cond = (
                f"(arrival_year > {start.year} OR "
                f"(arrival_year = {start.year} AND arrival_month > {start.month}) OR "
                f"(arrival_year = {start.year} AND arrival_month = {start.month} "
                f"AND arrival_date >= {start.day}))"
            )
            results[raw] = cond

        # Pattern: "this year / last year"
        for m in re.finditer(r"\b(this|last)\s+year\b", question, re.IGNORECASE):
            raw = m.group(0)
            yr = today.year if m.group(1).lower() == "this" else today.year - 1
            results[raw] = f"arrival_year = {yr}"

        # Pattern: "this month / last month"
        for m in re.finditer(r"\b(this|last)\s+month\b", question, re.IGNORECASE):
            raw = m.group(0)
            if m.group(1).lower() == "this":
                yr, mo = today.year, today.month
            else:
                if today.month == 1:
                    yr, mo = today.year - 1, 12
                else:
                    yr, mo = today.year, today.month - 1
            results[raw] = f"arrival_year = {yr} AND arrival_month = {mo}"

        # YTD
        if re.search(r"\bytd\b|year[\s-]?to[\s-]?date\b", question, re.IGNORECASE):
            results["YTD"] = (
                f"arrival_year = {today.year} AND arrival_month <= {today.month}"
            )

        return results

    # ── Prompt builder ────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(
        question: str,
        detected_terms: list[str],
        schema_block: str,
        business_rules: str,
        time_resolved: dict[str, str],
    ) -> str:
        terms_list = "\n".join(f"  - \"{t}\"" for t in detected_terms)
        rules_block = business_rules.strip() or "(none)"
        time_block = (
            "\n".join(f"  {k!r} → already resolved to: {v}" for k, v in time_resolved.items())
            if time_resolved
            else "  (none)"
        )

        return f"""
You are a hotel business intelligence expert. Your job is to map unfamiliar business
terms, platform names, and domain phrases to the correct SQL conditions for this
hotel reservation database.

TODAY'S DATE: {date.today().isoformat()}

SCHEMA:
{schema_block}

BUSINESS RULES (already known mappings — do NOT re-resolve these):
{rules_block}

TIME EXPRESSIONS ALREADY RESOLVED (do NOT change these):
{time_block}

USER QUESTION:
"{question}"

TERMS TO RESOLVE:
{terms_list}

YOUR TASK FOR EACH TERM:
1. What does this term mean in a hotel booking context?
2. Which schema column and value does it map to?
3. If it's a booking platform (e.g., Booking.com, Expedia, Agoda):
   - It almost certainly maps to a market_segment_type or similar column.
   - Look at the schema. If there's a column like market_segment_type or
     booking_source, check whether "Online" or "Corporate" or similar values
     would represent it.
   - Booking.com, Expedia, Agoda, MakeMyTrip = Online Travel Agents (OTA)
     → most likely maps to: market_segment_type = 'Online'
   - Direct booking / walk-in → market_segment_type = 'Direct' or 'Walk-In'
   - Corporate → market_segment_type = 'Corporate'
4. If the schema does NOT have a column that can represent this term,
   set sql_condition = UNKNOWN and needs_clarification = true.
5. Rewrite the enriched_question by replacing each vague term with its
   explicit condition so the SQL generator knows exactly what to query.

IMPORTANT:
- Only use columns that exist in the schema above.
- Do not invent column names.
- Be specific: "market_segment_type = 'Online'" not just "online bookings".
- For time expressions already resolved above, copy their sql_condition exactly.

Respond using the submit_term_resolution tool.
""".strip()

    # ── SQL context block builder ─────────────────────────────────────────────

    @staticmethod
    def _build_sql_context_block(resolved_terms: list[ResolvedTerm]) -> str:
        """
        Build a formatted context block to inject into the SQL generation prompt.
        """
        if not resolved_terms:
            return ""

        lines = [
            "TERMINOLOGY RESOLUTIONS (apply these mappings exactly in your SQL):",
        ]

        for term in resolved_terms:
            if term.sql_condition and term.sql_condition.upper() != "UNKNOWN":
                conf_tag = f" [confidence: {term.confidence:.0%}]" if term.confidence < 0.8 else ""
                lines.append(
                    f'  "{term.raw_term}" → {term.meaning}'
                    f"\n    SQL condition: {term.sql_condition}{conf_tag}"
                )
            else:
                lines.append(
                    f'  "{term.raw_term}" → COULD NOT RESOLVE '
                    f"(do your best based on schema)"
                )

        return "\n".join(lines)