#app/services/prompt_builder.py
"""
PRODUCTION GRADE — Zero hardcoding.
- GLOBAL_METRIC_GLOSSARY is the ONLY place formulas exist.
  The DB rules override/extend it at runtime.
- All domain-specific data (hotel columns, status values) comes from
  the DB schema (introspected live) and meta_business_rules (DB table).
- Nothing about "hotel", "Not_Canceled", or any column name is hardcoded here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.settings import settings


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL METRIC GLOSSARY
# Domain-agnostic formulas expressed as templates.
# The actual column names come from the DB schema at runtime.
# ═══════════════════════════════════════════════════════════════════════════════

GLOBAL_METRIC_GLOSSARY: dict[str, dict] = {
    "revenue": {
        "aliases": [
            "total revenue", "earnings", "income", "booking revenue",
            "sales revenue", "turnover", "sales amount", "overall revenue",
        ],
        "formula_template": "SUM(price_column * quantity_column)",
        "sql_note": "Use approved formula from business rules if one exists for this domain.",
        "unit": "currency",
        "aggregation": "SUM",
        "warnings": [
            "never confuse with profit",
            "row-level multiply first then SUM — never SUM(a)*SUM(b)",
            "use NULLIF for zero-safety on divisions",
        ],
    },
    "profit": {
        "aliases": ["profit", "net profit", "gross profit", "earnings after cost"],
        "formula_template": "SUM(revenue_field) - SUM(cost_field)",
        "unit": "currency",
        "warnings": [
            "never treat profit as total revenue",
            "must have cost column in schema — return INSUFFICIENT_CONTEXT if missing",
        ],
    },
    "profit_margin": {
        "aliases": ["profit margin", "margin %", "net margin", "profit percentage", "margin"],
        "formula_template": "(profit / NULLIF(revenue, 0)) * 100",
        "unit": "percentage",
        "warnings": [
            "denominator is revenue not cost",
            "do not confuse with markup — markup uses cost as denominator",
        ],
    },
    "markup": {
        "aliases": ["markup", "markup %", "markup percentage"],
        "formula_template": "(profit / NULLIF(cost, 0)) * 100",
        "unit": "percentage",
        "warnings": ["denominator is cost not revenue"],
    },
    "occupancy_rate": {
        "aliases": [
            "occupancy", "occupancy rate", "room occupancy", "occupancy %",
            "filled room percentage", "fill rate", "room fill", "room usage",
        ],
        "formula_template": "(occupied_count / NULLIF(total_available, 0)) * 100",
        "unit": "percentage",
        "warnings": [
            "denominator is available capacity in same time scope",
            "do not confuse occupancy COUNT with occupancy RATE",
        ],
    },
    "vacancy_rate": {
        "aliases": ["vacancy", "vacancy rate", "vacant rooms rate"],
        "formula_template": "((total - occupied) / NULLIF(total, 0)) * 100",
        "unit": "percentage",
    },
    "adr": {
        "aliases": ["adr", "average daily rate", "average room rate", "daily rate"],
        "formula_template": "SUM(room_revenue) / NULLIF(COUNT(rooms_sold), 0)",
        "unit": "currency",
        "warnings": [
            "denominator is rooms SOLD not total rooms available",
            "do not confuse ADR with RevPAR",
        ],
    },
    "revpar": {
        "aliases": ["revpar", "rev par", "revenue per available room"],
        "formula_template": "room_revenue / NULLIF(available_rooms, 0)",
        "alt_formula": "ADR * occupancy_rate_decimal",
        "unit": "currency",
        "warnings": [
            "divide by AVAILABLE rooms not occupied rooms",
            "do not confuse with ADR",
        ],
    },
    "average_booking_value": {
        "aliases": [
            "average booking value", "average booking amount", "average order value",
            "average transaction value", "aov", "average bill", "average ticket size",
        ],
        "formula_template": "SUM(booking_revenue) / NULLIF(COUNT(*), 0)",
        "unit": "currency",
        "warnings": [
            "denominator is booking COUNT not unique customer count",
            "zero-safe division required",
        ],
    },
    "cancellation_rate": {
        "aliases": [
            "cancellation rate", "cancel rate", "cancellation %", "cancel %",
            "booking cancellation percentage", "booking cancel rate",
        ],
        "formula_template": "(COUNT(cancelled) / NULLIF(COUNT(*), 0)) * 100",
        "unit": "percentage",
        "warnings": [
            "denominator is TOTAL bookings in same filter scope — not just active bookings",
            "use NULLIF for zero-safety",
            "scope must be identical for numerator and denominator",
        ],
    },
    "average_stay_length": {
        "aliases": [
            "average stay", "average stay length", "average nights", "avg stay",
            "average length of stay", "alos",
        ],
        "formula_template": "AVG(nights_column)",
        "unit": "nights",
        "warnings": ["define whether cancelled stays are included or excluded"],
    },
    "lead_time": {
        "aliases": [
            "lead time", "average lead time", "booking lead time", "advance booking",
        ],
        "formula_template": "AVG(lead_time_column)",
        "unit": "days",
    },
    "repeat_customer_rate": {
        "aliases": [
            "repeat customer rate", "repeat guest rate", "returning guest rate",
            "loyalty rate", "repeat booking rate",
        ],
        "formula_template": "(COUNT(repeat_customers) / NULLIF(COUNT(*), 0)) * 100",
        "unit": "percentage",
        "warnings": ["denominator is total bookings not total unique customers"],
    },
    "growth_rate": {
        "aliases": [
            "growth", "growth rate", "growth %", "increase percentage",
            "month over month growth", "year over year growth", "mom", "yoy",
            "decline percentage", "change percentage",
        ],
        "formula_template": "((current_value - previous_value) / NULLIF(previous_value, 0)) * 100",
        "unit": "percentage",
        "warnings": [
            "old value is the denominator",
            "compare the correct time periods",
            "protect against zero previous value with NULLIF",
        ],
    },
    "conversion_rate": {
        "aliases": [
            "conversion rate", "booking conversion", "lead conversion", "success rate",
        ],
        "formula_template": "(COUNT(conversions) / NULLIF(COUNT(total), 0)) * 100",
        "unit": "percentage",
        "warnings": [
            "define what counts as a conversion",
            "denominator must match same funnel stage",
        ],
    },
    "share_of_total": {
        "aliases": [
            "share of total", "contribution percentage", "share of total sales",
            "booking share by segment", "revenue contribution",
        ],
        "formula_template": "(group_value / NULLIF(grand_total, 0)) * 100",
        "unit": "percentage",
        "warnings": ["compute group total and grand total in same scope"],
    },
    "average_rating": {
        "aliases": ["average rating", "avg rating", "rating", "mean rating"],
        "formula_template": "AVG(rating_column)",
        "unit": "score",
        "warnings": [
            "use numeric field only",
            "do not average text labels",
        ],
    },
    "booking_count": {
        "aliases": [
            "booking count", "number of bookings", "total bookings",
            "how many bookings", "count of bookings",
        ],
        "formula_template": "COUNT(*)",
        "unit": "count",
        "warnings": ["simple row count unless glossary defines special eligibility"],
    },
    "unique_customer_count": {
        "aliases": [
            "unique customers", "distinct customers", "different customers",
            "number of unique guests",
        ],
        "formula_template": "COUNT(DISTINCT customer_id_column)",
        "unit": "count",
        "warnings": ["use COUNT(DISTINCT ...) — do not confuse with total transactions"],
    },
    "roi": {
        "aliases": ["roi", "return on investment", "return %"],
        "formula_template": "((gain - cost) / NULLIF(cost, 0)) * 100",
        "unit": "percentage",
        "warnings": [
            "denominator is cost not revenue",
            "do not confuse with profit margin",
        ],
    },
    "arpu": {
        "aliases": ["arpu", "average revenue per user", "revenue per user"],
        "formula_template": "SUM(revenue) / NULLIF(COUNT(DISTINCT user_id), 0)",
        "unit": "currency",
        "warnings": ["denominator is unique users not sessions or transactions"],
    },
    "clv": {
        "aliases": ["clv", "ltv", "customer lifetime value", "lifetime value"],
        "formula_template": "average_order_value * purchase_frequency * customer_lifespan",
        "unit": "currency",
        "warnings": [
            "ONLY use if approved formula exists in business rules",
            "never invent CLV formula — return INSUFFICIENT_CONTEXT if not defined",
        ],
    },
    "discount_rate": {
        "aliases": ["discount rate", "discount %", "discount percentage"],
        "formula_template": "(discount_amount / NULLIF(original_amount, 0)) * 100",
        "unit": "percentage",
        "warnings": ["denominator is original amount not discounted amount"],
    },
    "burn_rate": {
        "aliases": ["burn rate", "monthly burn", "spend rate"],
        "formula_template": "SUM(spend) / time_period",
        "unit": "currency",
        "warnings": ["time period must be clearly defined"],
    },
}


def _build_glossary_block(question: str) -> str:
    """
    Build a compact, question-relevant metric glossary snippet.
    Only includes metrics whose aliases overlap with the question.
    Max 4 metrics returned to stay within token budget.
    Formulas are domain-agnostic templates — column names come from DB schema.
    """
    q_lower = question.lower()
    matched: list[tuple[int, str, dict]] = []

    for key, defn in GLOBAL_METRIC_GLOSSARY.items():
        aliases = defn.get("aliases", [])
        hits = sum(1 for alias in aliases if alias in q_lower)
        if key.replace("_", " ") in q_lower:
            hits += 3
        if hits > 0:
            matched.append((hits, key, defn))

    matched.sort(key=lambda x: x[0], reverse=True)

    if not matched:
        return "(none matched — use schema columns and business rules directly)"

    lines = []
    for _, key, defn in matched[:4]:
        formula = defn.get("formula_template", defn.get("formula", ""))
        sql_note = defn.get("sql_note", "")
        unit = defn.get("unit", "")
        warns = defn.get("warnings", [])
        warn_str = " | WARNING: " + "; ".join(warns[:2]) if warns else ""
        note_str = f" | NOTE: {sql_note}" if sql_note else ""
        lines.append(f"  {key.upper()}: {formula} [{unit}]{note_str}{warn_str}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSAL CALCULATION RULES BLOCK
# Domain-agnostic — no column names, no status values, no domain terms.
# ═══════════════════════════════════════════════════════════════════════════════

CALCULATION_RULES_BLOCK = """
CALCULATION RULES (apply to every query):

OPERATION MAPPING — map user words to SQL operations:
  total / sum / overall / aggregate   → SUM(field)
  average / mean                      → AVG(field) — never compute as SUM/COUNT manually
  how many / count / number of        → COUNT(*) for all rows; COUNT(DISTINCT col) for unique
  unique / distinct / different       → COUNT(DISTINCT col)
  lowest / minimum / smallest / earliest / cheapest → MIN(field)
  highest / maximum / largest / latest / costliest  → MAX(field)
  percentage / percent / rate         → (part / NULLIF(whole, 0)) * 100
  ratio                               → division WITHOUT * 100; keep as decimal
  difference / gap / remaining        → subtraction — identify order carefully
  growth / MoM / YoY / increase %    → ((current - previous) / NULLIF(previous, 0)) * 100
  share / contribution %              → (group_value / NULLIF(grand_total, 0)) * 100
  weighted average                    → SUM(value * weight) / NULLIF(SUM(weight), 0)

ROW-LEVEL vs AGGREGATE (critical rule):
  NEVER: SUM(a) * SUM(b) for a row-level product
  CORRECT: SUM(a * b) — multiply at row level FIRST, then SUM
  WHY: SUM(price) * SUM(qty) = wrong; SUM(price * qty) = correct

FILTER-BEFORE-AGGREGATE: Always apply WHERE before GROUP BY and aggregate functions.

GROUPING: Use GROUP BY ONLY when user asks "by [dimension]". Never add extra group columns.

ZERO SAFETY: Protect every division with NULLIF(denominator, 0). Prevent unsafe division errors.

NULL HANDLING: Database ignores NULLs in AVG/SUM by default. Never treat NULL as zero unless a business rule says so.

SCOPE CONSISTENCY: In any ratio or percentage, numerator and denominator must use IDENTICAL WHERE filters.

PERCENTAGE OF TOTAL: Compute group total first, then grand total separately, then divide.

ROUNDING: Keep full precision during calculation. Round only the final output if user requests it.

TOP/BOTTOM: Compute metric first → ORDER BY metric DESC/ASC → LIMIT N. Do not sort by raw input columns.

AVERAGING AVERAGES: Do NOT AVG(group_averages) unless groups have equal sizes. Use weighted average instead.
""".strip()


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class PromptBuilder:
    """
    Production prompt builder — zero hardcoded domain data.
    All column names, status values, and formulas come from:
      1. DB schema (live introspection via SchemaService)
      2. meta_business_rules table (DB-driven, per tenant)
      3. GLOBAL_METRIC_GLOSSARY (domain-agnostic formula templates)
    """

    # ── Tool Calling JSON schemas ──────────────────────────────────────────────

    TEACHING_SCHEMA = {
        "canonical_term": "The primary name of the concept.",
        "aliases": ["list", "of", "similar", "words"],
        "sql_condition": "Valid SQL WHERE clause fragment",
        "description": "Short human readable explanation.",
    }

    REWRITE_SCHEMA = {
        "normalized_question": "Normalized version of the user's message.",
        "standalone_question": "Standalone version with pronouns and context resolved.",
        "is_follow_up": False,
        "follow_up_strategy": "none | refinement | drill_down | new_metric | correction",
        "missing_context": [],
    }

    PLAN_SCHEMA = {
        "thought_process": "1-2 sentences of chain-of-thought before choosing route.",
        "route": "database_query | clarify | schema_answer | show_sql | explain_last_answer | general_answer | diagnose | teach_rule",
        "standalone_question": "Resolved, explicit version of the question.",
        "user_goal": "Short summary of what the user wants.",
        "needs_clarification": False,
        "clarifying_question": "",
        "requires_schema": True,
        "requires_business_mapping": False,
        "follow_up_strategy": "none | refinement | drill_down | new_metric | correction",
        "confidence": 0.95,
        "filters": {},
        "date_range": None,
    }

    CRITIC_SCHEMA = {
        "verdict": "approve | clarify | replan",
        "reason": "",
        "needs_clarification": False,
        "clarifying_question": "",
        "confidence": 0.9,
    }

    SQL_CRITIC_SCHEMA = {
        "verdict": "approve | retry | clarify",
        "reason": "Explain if SQL misinterprets the request or contains logically impossible conditions.",
        "clarifying_question": "",
    }

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _trim(text: str | None, limit: int = 4000) -> str:
        value = (text or "").strip()
        return value if len(value) <= limit else value[:limit].rstrip()

    @staticmethod
    def _schema_block(schema: dict) -> str:
        return json.dumps(schema, ensure_ascii=True, indent=2)

    def _prune_schema(self, schema: Any) -> str:
        """Render schema to a prompt-friendly string. Excludes system/internal tables."""
        excluded = {"chat_messages", "alembic_version"}

        if hasattr(schema, "schema_dict"):
            lines = [
                f"Dialect: {getattr(schema, 'dialect', 'SQL').upper()}",
                f"Database: {getattr(schema, 'dataset_name', 'default')}",
                "Tables:",
            ]
            for tname, cols in schema.schema_dict.items():
                if tname.lower() in excluded:
                    continue
                lines.append(f"\n  {tname}")
                for col in cols:
                    lines.append(f"    {col}")
            return "\n".join(lines)

        if hasattr(schema, "tables"):
            lines = [
                f"Dialect: {getattr(schema, 'dialect', 'SQL').upper()}",
                f"Database: {getattr(schema, 'dataset_name', 'default')}",
                "Tables:",
            ]
            for tname, cols in schema.tables.items():
                if tname.lower() in excluded:
                    continue
                lines.append(f"\n  {tname}")
                for col in cols:
                    pk = " [PK]" if getattr(col, "is_primary_key", False) else ""
                    lines.append(f"    - {col.name} ({col.data_type}){pk}")
                    if getattr(col, "sample_values", None):
                        sv = ", ".join(f"'{v}'" for v in col.sample_values[:3])
                        lines.append(f"      samples: {sv}")
            return "\n".join(lines)

        if hasattr(schema, "to_prompt_block"):
            return schema.to_prompt_block()
        return str(schema)

    @staticmethod
    def _resolve_rule_conflicts(rules_text: str, question: str) -> str:
        """
        Resolve contradictions in business rules BEFORE the LLM sees them.

        Works purely on the question text and the rule text.
        No domain-specific column names or status values are hardcoded here.
        The detection works by looking for patterns in the RULE TEXT itself:
          - A rule that says "ALWAYS add [filter]" conflicts with
          - A rule that says "NEVER add [filter] when user asks [signal]"

        When a contradiction is detected, the blanket "ALWAYS" rule is suppressed
        and replaced with a comment so the LLM understands what happened.

        Also fixes the double-CRITICAL prefix bug in a single pass.
        """
        if not rules_text:
            return ""

        q_lower = question.lower()

        # Signals that mean "user wants all records regardless of default filters"
        ALL_DATA_SIGNALS = [
            "how many", "total count", "all bookings", "every booking",
            "total bookings", "all records", "overall count", "exact count",
            "including cancelled", "including canceled", "regardless",
            "across all", "without filter", "unfiltered",
        ]
        # Signals that mean "user explicitly wants excluded/filtered-out records"
        EXCLUDED_DATA_SIGNALS = [
            "cancel", "cancelled", "canceled", "cancellation",
            "inactive", "draft", "expired", "rejected", "failed",
        ]

        user_wants_all = any(s in q_lower for s in ALL_DATA_SIGNALS)
        user_wants_excluded = any(s in q_lower for s in EXCLUDED_DATA_SIGNALS)

        lines = rules_text.split("\n")
        resolved: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Fix double CRITICAL prefix (DB storage bug)
            if re.match(r"^CRITICAL\s*:\s*CRITICAL\s*:", stripped, re.IGNORECASE):
                # Remove everything up to and including the second "CRITICAL:"
                stripped = re.sub(
                    r"^CRITICAL\s*:\s*CRITICAL\s*:\s*", "CRITICAL: ", stripped, flags=re.IGNORECASE
                )

            lower = stripped.lower()

            # Detect a blanket "always add [filter]" rule
            # Pattern: contains "always" AND contains a filter word AND scoped to "every query" / "all queries"
            is_blanket_filter = (
                "always" in lower
                and any(scope in lower for scope in ["every query", "all quer", "where clause", "each query"])
            )

            if is_blanket_filter and (user_wants_all or user_wants_excluded):
                resolved.append(
                    f"-- DEFAULT FILTER SUPPRESSED: User's question implies they want "
                    f"all records or specifically excluded records. "
                    f"Original rule: '{stripped[:100]}'"
                )
                continue

            resolved.append(stripped)

        return "\n".join(resolved)

    @staticmethod
    def _score_and_select_rules(rules_text: str, question: str, max_rules: int = 5) -> str:
        """
        Score each business rule by keyword overlap with the question.
        Inject only the top N most relevant rules.

        Scoring:
          CRITICAL rules  → minimum floor score of 3 (boosted but NOT unconditional)
          Optional rules  → must have ≥1 keyword match or they are excluded
          Duplicate rules → deduped

        This prevents the "inject ALL rules blindly" problem that causes wrong answers.
        """
        if not rules_text or rules_text.strip() in ("(none)", ""):
            return "(none)"

        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "have",
            "has", "had", "do", "does", "did", "will", "would", "could", "should",
            "for", "of", "in", "on", "at", "to", "from", "with", "by", "or",
            "and", "but", "not", "how", "what", "when", "where", "which", "who",
            "many", "much", "more", "most", "some", "any", "all", "show", "get",
            "give", "tell", "find", "list", "me", "my", "their", "its",
        }

        q_tokens = {
            t.lower()
            for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", question)
            if t.lower() not in stop_words and len(t) > 2
        }

        raw_lines = [l.strip() for l in rules_text.split("\n") if l.strip()]

        # Strip section-header lines
        header_starts = (
            "critical business rules:", "optional business guidelines:",
            "business rules:", "rag schema context:", "retrieved rules:",
            "relevant rules:", "rules applied:",
        )
        raw_lines = [
            l for l in raw_lines
            if not l.lower().startswith(header_starts)
        ]

        scored: list[tuple[int, str]] = []
        seen: set[str] = set()

        for rule in raw_lines:
            # Fix double prefix on the fly
            if re.match(r"^CRITICAL\s*:\s*CRITICAL\s*:", rule, re.IGNORECASE):
                rule = re.sub(
                    r"^CRITICAL\s*:\s*CRITICAL\s*:\s*", "CRITICAL: ", rule, flags=re.IGNORECASE
                )

            if rule in seen:
                continue
            seen.add(rule)

            rule_lower = rule.lower()
            rule_tokens = {
                t.lower()
                for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", rule)
                if t.lower() not in stop_words and len(t) > 2
            }

            overlap = len(q_tokens & rule_tokens)

            if re.match(r"^critical\s*:", rule_lower):
                score = max(overlap, 3)   # floor=3: boosted but not unconditional
            else:
                score = overlap           # optional: must have ≥1 match

            if score > 0:
                scored.append((score, rule))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [rule for _, rule in scored[:max_rules]]
        return "\n".join(selected) if selected else "(none)"

    # ── Intent / Planning prompts ──────────────────────────────────────────────

    def build_rewrite_prompt(
        self,
        user_question: str,
        chat_history: str = "",
        dialogue_state: str = "",
    ) -> str:
        history = self._trim(chat_history, 2000) or "(no recent history)"
        state = self._trim(dialogue_state, 800) or "(none)"

        return f"""
You are Stage 1 of a multi-agent planning pipeline for a business database assistant.

ROLE: Question rewriter and follow-up detector.
TASK: Produce a standalone question that resolves all pronouns and follow-up references.

Return ONLY valid JSON. No markdown. No explanation text.

RULES:
1. Drill-down: If prev="Revenue in Jan?" and new="What about Feb?" →
   REPLACE the old filter: "What was revenue in Feb?" — do not concatenate contradicting words.
2. Scope change: "Just for Room Type 1" → preserve all other filters, narrow the scope.
3. Set is_follow_up=true if this message references or modifies the previous query.
4. List genuinely missing context in missing_context (do not over-fill this).

Recent conversation:
{history}

Dialogue state:
{state}

User question: {user_question}

Return exactly this JSON:
{self._schema_block(self.REWRITE_SCHEMA)}
""".strip()

    def build_plan_prompt(
        self,
        user_question: str,
        rewritten_question: str,
        recent_context: str = "",
        dialogue_state: str = "",
        rewrite_result: str = "",
    ) -> str:
        context = self._trim(recent_context, 2000) or "(none)"
        state = self._trim(dialogue_state, 800) or "(none)"
        rewrite_block = self._trim(rewrite_result, 1000) or "(none)"

        return f"""
You are Stage 2 of a multi-agent planning pipeline for a business database assistant.

ROLE: Execution planner with chain-of-thought routing.
TASK: Choose the safest and most accurate route for the user's request.

Return ONLY valid JSON. No markdown.

ROUTES:
  database_query     → User wants data, metrics, counts, comparisons, trends, or lists.
  clarify            → Question is fundamentally unanswerable without a critical missing parameter.
  schema_answer      → User asks about available columns, tables, or data fields.
  show_sql           → User asks to see the SQL that was used.
  explain_last_answer→ User asks how the last answer was calculated.
  diagnose           → User says the last answer was wrong or looks incorrect.
  teach_rule         → User says "remember that X means Y" or corrects a formula.
  general_answer     → Greetings, thanks, or off-topic conversation.

ROUTING RULES:
1. CHAIN-OF-THOUGHT: Write 1-2 sentences in thought_process before choosing route.
2. BRAVE ROUTING: Any business metric question (revenue, rate, count, average, growth, etc.)
   → route to database_query. Map the term via the schema and business rules provided.
3. CLARIFY ONLY IF: The question cannot be answered at all without a specific parameter
   the user must provide. Do NOT clarify for vocabulary differences — map terms yourself.
4. LOOP GUARD: If pending_clarification in dialogue_state already holds this question,
   force route to database_query — never repeat the same clarification.
5. FOLLOW-UP: If is_follow_up=true, preserve all previous filters in standalone_question.

Context (schema + memory + rules):
{context}

Dialogue state:
{state}

Rewrite result:
{rewrite_block}

Original question: {user_question}
Standalone question: {rewritten_question}

Return exactly this JSON:
{self._schema_block(self.PLAN_SCHEMA)}
""".strip()

    def build_plan_critic_prompt(
        self,
        *,
        user_question: str,
        rewritten_question: str,
        plan_json: str,
        recent_context: str = "",
        dialogue_state: str = "",
    ) -> str:
        context = self._trim(recent_context, 1200) or "(none)"
        state = self._trim(dialogue_state, 800) or "(none)"
        plan_block = self._trim(plan_json, 1000) or "(none)"

        return f"""
You are a planning critic for a business database assistant.

ROLE: Review the proposed plan for correctness and safety.
Return ONLY valid JSON. No markdown.

VERDICTS:
  approve → Plan is correct, route is appropriate, standalone question is accurate.
  clarify → Request genuinely needs one specific missing parameter to proceed.
  replan  → Route is wrong or standalone question is materially flawed.

LOOP GUARD (mandatory): If the plan routes to "clarify" for a question that is already
in pending_clarification in the dialogue state, you MUST return "replan" or force
"approve" with database_query. Never let the user get stuck in a clarification loop.

Context: {context}
Dialogue state: {state}
User question: {user_question}
Standalone question: {rewritten_question}
Proposed plan: {plan_block}

Return exactly this JSON:
{self._schema_block(self.CRITIC_SCHEMA)}
""".strip()

    def build_json_repair_prompt(
        self,
        *,
        schema_name: str,
        schema: dict,
        previous_output: str,
        validation_errors: list[str],
    ) -> str:
        errors = "\n".join(f"  - {e}" for e in validation_errors) or "  - Unknown error"
        output = self._trim(previous_output, 1500) or "(empty)"

        return f"""
You previously returned invalid JSON for `{schema_name}`.
Return ONLY valid JSON. No markdown. No explanation. No code fences.

Validation errors:
{errors}

Previous output:
{output}

Return exactly this JSON schema:
{self._schema_block(schema)}
""".strip()

    # ── SQL Prompts ────────────────────────────────────────────────────────────

    def build_sql_prompt(
        self,
        question: str,
        schema: Any,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        schema_block = self._prune_schema(schema)

        # Resolve contradictions BEFORE LLM sees the rules
        conflict_resolved = self._resolve_rule_conflicts(session_context, question)

        # Select only relevant rules (max 5, scored by keyword overlap)
        relevant_rules = self._score_and_select_rules(conflict_resolved, question, max_rules=5)

        # Build relevant metric glossary (domain-agnostic templates)
        metric_glossary = _build_glossary_block(question)

        examples = self._trim(examples_context, 1200) or "(none)"

        return f"""
You are a production-grade SQL analyst for a business database.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK: Convert the user's business question into ONE correct SQL query.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1 — CHAIN-OF-THOUGHT (write as SQL comments at the top of your output):
Answer ALL of these before writing the SQL:
  a) What is the user EXPLICITLY asking for? (exact words matter)
  b) Which business metric does this map to? Check METRIC GLOSSARY.
  c) Which table and columns in the schema satisfy this metric?
  d) Which calculation operation applies? (SUM / AVG / COUNT / % / ratio / growth?)
  e) Which business rules from the list below are DIRECTLY relevant? Name them.
  f) Are any two rules in conflict? If yes, which one wins and why?
     Priority: 1st = User's explicit request | 2nd = CRITICAL rules | 3rd = Defaults
  g) What filters, grouping, and ordering does this need?

STEP 2 — SQL QUERY
Write the single correct SQL after the comments.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RULES:
  1. Use ONLY columns from the schema below. Never invent columns.
  2. ONE statement only (SELECT or WITH...SELECT).
  3. LIMIT {settings.max_query_results} on row queries. No LIMIT on aggregates.
  4. If unanswerable from schema: output exactly INSUFFICIENT_CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEMA (live from database — trust this):
{schema_block}

METRIC GLOSSARY (domain-agnostic formula templates — adapt column names from schema):
{metric_glossary}

{CALCULATION_RULES_BLOCK}

BUSINESS RULES FOR THIS QUESTION (scored by relevance — most relevant first):
{relevant_rules}

SQL EXAMPLES:
{examples}

USER QUESTION:
{question}

OUTPUT FORMAT:
-- a) User asks for: [exact intent]
-- b) Metric: [metric name or "direct column query"]
-- c) Columns: [which columns and table]
-- d) Operation: [SUM / AVG / COUNT / % formula / etc]
-- e) Rules applied: [rule names or "none relevant"]
-- f) Conflict: [yes — [winning rule] wins because [reason] | no]
-- g) Filters/grouping: [description]
[SQL query here]
""".strip()

    def build_sql_repair_prompt(
        self,
        question: str,
        schema: Any,
        session_context: str,
        invalid_sql: str,
        errors: list[str],
        examples_context: str = "",
    ) -> str:
        conflict_resolved = self._resolve_rule_conflicts(session_context, question)
        relevant_rules = self._score_and_select_rules(conflict_resolved, question, max_rules=5)
        examples = self._trim(examples_context, 800) or "(none)"
        error_block = "\n".join(f"  - {e}" for e in errors) if errors else "  - Invalid SQL"

        return f"""
You are repairing a failed SQL query.

TASK: Fix the SQL so it correctly answers the question without breaking it further.

QUESTION: {question}

SCHEMA:
{self._prune_schema(schema)}

METRIC GLOSSARY:
{_build_glossary_block(question)}

RELEVANT BUSINESS RULES:
{relevant_rules}

PREVIOUS BROKEN SQL:
{invalid_sql or "(empty)"}

ERRORS TO FIX:
{error_block}

REPAIR RULES:
  - Fix ONLY what the errors describe
  - Use ONLY columns from the schema
  - ONE SELECT statement
  - LIMIT {settings.max_query_results} on row queries, no LIMIT on aggregates
  - Conflict priority: explicit request > CRITICAL rules > defaults
  - If still unanswerable: INSUFFICIENT_CONTEXT

OUTPUT:
-- Fix applied: [what changed and why]
[Corrected SQL]
""".strip()

    # ── SQL Critic ─────────────────────────────────────────────────────────────

    def build_sql_critic_prompt(
        self,
        *,
        question: str,
        sql: str,
        schema: Any,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        # Critic receives ONLY CRITICAL rules — not optional guidelines
        # This prevents optional rules from rejecting correct SQL
        lines = (session_context or "").split("\n")
        critical_only = "\n".join(
            l.strip() for l in lines
            if re.match(r"^critical\s*:", l.strip(), re.IGNORECASE)
            and not re.match(r"^critical\s*:\s*critical\s*:", l.strip(), re.IGNORECASE)
        ) or "(none)"

        return f"""
You are a SQL correctness critic for a business database assistant.

ROLE: Verify the SQL correctly answers the question.
Return ONLY valid JSON. No markdown.

VERDICT RULES:
  approve → SQL correctly answers the question, respects CRITICAL rules, is logically sound.
  retry   → SQL contradicts the user's EXPLICIT request, uses wrong columns/formula,
            has impossible WHERE conditions (e.g. col='A' AND col='B' simultaneously),
            violates a CRITICAL rule, or uses wrong denominator for a ratio/percentage.
  clarify → The user's QUESTION ITSELF is too ambiguous to answer safely.

KEY PRINCIPLE — the SQL must answer WHAT THE USER ASKED:
  - User asked for ALL records but SQL adds a default status filter → retry
  - User asked for a RATE but SQL returns a COUNT → retry
  - User asked for a RATE and SQL uses wrong denominator → retry
  - SQL is logically correct and answers the question → approve

USER QUESTION: {question}

SQL CANDIDATE:
{sql}

SCHEMA:
{self._prune_schema(schema)}

CRITICAL RULES (must not be violated — checked here):
{critical_only}

Return exactly this JSON:
{self._schema_block(self.SQL_CRITIC_SCHEMA)}
""".strip()

    # ── Synthesis ──────────────────────────────────────────────────────────────

    def build_synthesis_prompt(
        self,
        question: str,
        data_preview: str,
        row_count: int,
        executed_sql: str,
    ) -> str:
        preview = self._trim(data_preview, 3000)
        metric_context = _build_glossary_block(question)

        return f"""
You are a senior business analyst answering a manager's question.

ROLE: Translate a database result into a clear business insight.
TASK: Write a direct, professional answer based ONLY on the data below.

ANSWER RULES:
  1. Lead with the key number or fact the user asked for — put it first.
  2. Use business language — percentages, currency, counts, trends — not "rows" or "records".
  3. Add one sentence of business interpretation if the data clearly supports it.
  4. Mention the filter scope if it matters (e.g., "in 2018", "for Room Type 1").
  5. If result is empty → say no matching records were found.
  6. NEVER mention SQL, databases, queries, tables, or any technical terms.
  7. NEVER say "I ran a query" or "based on the data".
  8. NEVER say you are an AI or a system.
  9. NEVER invent numbers not visible in the result.
  10. Length: 2–4 sentences maximum.

METRIC CONTEXT (use these definitions for framing):
{metric_context}

USER QUESTION: {question}
SQL USED: {executed_sql}
ROWS RETURNED: {row_count}
DATA:
{preview}

Write your business analyst answer:
""".strip()

    # ── Supporting prompts ─────────────────────────────────────────────────────

    def build_teaching_prompt(self, instruction: str, schema: Any) -> str:
        return f"""
You are a business dictionary builder for a database assistant.

TASK: Translate the user's natural language instruction into a SQL WHERE clause condition.
The WHERE clause must use only columns from the schema below.

SCHEMA:
{self._prune_schema(schema)}

USER INSTRUCTION: {instruction}

Return exactly this JSON:
{self._schema_block(self.TEACHING_SCHEMA)}
""".strip()

    def build_data_aware_clarification_prompt(
        self, unresolved_terms: list[str], question: str, schema: Any
    ) -> str:
        terms = ", ".join(unresolved_terms)
        return f"""
You are a helpful business data assistant.

TASK: The user used term(s) not in the approved business glossary: "{terms}".
Write 1–2 polite sentences asking for clarification.
Look at the schema and suggest which column(s) or sample values the user likely means.

SCHEMA:
{self._prune_schema(schema)}

USER QUESTION: {question}
""".strip()

    def build_general_answer_prompt(
        self,
        user_question: str,
        recent_context: str = "",
    ) -> str:
        context = self._trim(recent_context, 1000) or "(none)"
        return f"""
You are a friendly assistant in a business analytics application.

TASK: Answer the user's general or conversational message naturally and briefly.

RULES:
  - Be concise and helpful
  - Do not invent database results
  - If the message is off-topic, answer briefly and offer to help with data questions

Recent conversation: {context}
User message: {user_question}

Write a short natural response.
""".strip()

    def build_custom_explanation_prompt(
        self,
        user_instruction: str,
        last_question: str,
        executed_sql: str,
        row_count: int,
        data_preview: str,
    ) -> str:
        preview = self._trim(data_preview, 2000)
        return f"""
You are a senior business analyst explaining a previous result to a manager.

TASK: Explain the result following the manager's specific instructions exactly.

RULES:
  - Follow the manager's instructions about what to show or not show
  - Use plain business language only
  - Do not mention SQL or technical terms unless the manager explicitly asked for them

Manager's instruction: {user_instruction}
Original question: {last_question}
SQL executed: {executed_sql}
Rows returned: {row_count}
Data: {preview}

Write your explanation:
""".strip()

    def build_diagnostic_prompt(
        self,
        user_complaint: str,
        last_question: str,
        last_sql: str,
    ) -> str:
        return f"""
You are a business analyst debugging an answer with a manager.

TASK: Explain what filters were applied and ask which was wrong.

RULES:
  - Be polite and specific (2–4 sentences)
  - Explain the filters in plain English — not SQL syntax
  - Ask which specific filter or assumption was incorrect
  - Tell them: "You can correct me by saying: Remember that [term] means [correct rule]"

Manager's complaint: {user_complaint}
Original question: {last_question}
SQL that was run: {last_sql}
""".strip()