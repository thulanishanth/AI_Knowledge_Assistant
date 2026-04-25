#app/services/prompt_builder.py
from __future__ import annotations
import json
import re
from typing import Any
from app.core.settings import settings

CALCULATION_RULES_BLOCK = """
CALCULATION RULES (apply to every query):
OPERATION MAPPING:
  total / sum / overall / aggregate   → SUM(field)
  average / mean                      → AVG(field) — never compute as SUM/COUNT manually
  how many / count / number of        → COUNT(*) for all rows; COUNT(DISTINCT col) for unique
  percentage / percent / rate         → (part / NULLIF(whole, 0)) * 100
  growth / MoM / YoY / increase %     → ((current - previous) / NULLIF(previous, 0)) * 100
ROW-LEVEL vs AGGREGATE (critical rule):
  NEVER: SUM(a) * SUM(b) for a row-level product. CORRECT: SUM(a * b)
FILTER-BEFORE-AGGREGATE: Always apply WHERE before GROUP BY and aggregate functions.
ZERO SAFETY: Protect every division with NULLIF(denominator, 0).
SCOPE CONSISTENCY: In any ratio or percentage, numerator and denominator must use IDENTICAL WHERE filters.
""".strip()

class PromptBuilder:
    TEACHING_SCHEMA = { "canonical_term": "name", "aliases": ["words"], "sql_condition": "WHERE clause", "description": "desc" }
    REWRITE_SCHEMA = { "normalized_question": "str", "standalone_question": "str", "is_follow_up": False, "follow_up_strategy": "none", "missing_context": [] }
    PLAN_SCHEMA = { "thought_process": "str", "route": "database_query", "standalone_question": "str", "user_goal": "str", "needs_clarification": False, "clarifying_question": "", "requires_schema": True, "requires_business_mapping": False, "follow_up_strategy": "none", "confidence": 0.95, "filters": {}, "date_range": None }
    CRITIC_SCHEMA = { "verdict": "approve", "reason": "", "needs_clarification": False, "clarifying_question": "", "confidence": 0.9 }
    SQL_CRITIC_SCHEMA = { "verdict": "approve", "reason": "", "clarifying_question": "" }

    @staticmethod
    def _trim(text: str | None, limit: int = 4000) -> str:
        return (text or "").strip()[:limit].rstrip()

    @staticmethod
    def _schema_block(schema: dict) -> str:
        return json.dumps(schema, ensure_ascii=True, indent=2)

    def _prune_schema(self, schema: Any) -> str:
        if hasattr(schema, "schema_dict"):
            lines = [f"Dialect: {getattr(schema, 'dialect', 'SQL').upper()}", f"Database: {getattr(schema, 'dataset_name', 'default')}", "Tables:"]
            for tname, cols in schema.schema_dict.items():
                if tname.lower() in {"chat_messages", "alembic_version"}: continue
                lines.append(f"\n  {tname}")
                for col in cols: lines.append(f"    {col}")
            return "\n".join(lines)
        return str(schema)

    def build_sql_prompt(
        self, question: str, schema: Any, ontology_context: str, session_context: str = "", examples_context: str = ""
    ) -> str:
        schema_block = self._prune_schema(schema)

        # CRITICAL RAG FIX:
        # In Phase 5, Orchestrator already handles all RAG selection mathematically (via Hybrid Search).
        # We must NOT filter it again here. We just clean up the "Vector Memory:" header formatting.
        clean_context = session_context.replace("Vector Memory:", "").strip() if session_context else ""
        relevant_rules = clean_context if clean_context else "(No specific business rules required)"

        examples = self._trim(examples_context, 1200) or "(none)"

        return f"""
You are a production-grade SQL analyst for a business database.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK: Convert the user's business question into ONE correct SQL query.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1 — CHAIN-OF-THOUGHT (write as SQL comments at the top of your output):
  a) What is the user EXPLICITLY asking for?
  b) Which business metric does this map to? Check METRIC GLOSSARY.
  c) Which table and columns satisfy this metric?
  d) Which calculation operation applies?
  e) Which business rules from the list below are DIRECTLY relevant?
  f) Are any two rules in conflict? If yes, which wins? (Explicit request > CRITICAL rules > Defaults)
  g) What filters, grouping, and ordering does this need?

STEP 2 — SQL QUERY
Write the single correct SQL after the comments.

HARD RULES:
  1. Use ONLY columns from the schema below. Never invent columns.
  2. ONE statement only (SELECT or WITH...SELECT).
  3. LIMIT {settings.max_query_results} on row queries. No LIMIT on aggregates.
  4. If unanswerable from schema: output exactly INSUFFICIENT_CONTEXT

SCHEMA:
{schema_block}

METRIC GLOSSARY (Live Ontology Matches):
{ontology_context}

{CALCULATION_RULES_BLOCK}

BUSINESS RULES FOR THIS QUESTION:
{relevant_rules}

SQL EXAMPLES:
{examples}

USER QUESTION:
{question}

OUTPUT FORMAT:
-- a) User asks for: [exact intent]
-- b) Metric: [metric name]
-- c) Columns: [which columns]
-- d) Operation: [formula type]
-- e) Rules applied: [rule names]
-- f) Conflict: [resolution]
-- g) Filters/grouping: [description]
[SQL query here]
""".strip()

    def build_sql_repair_prompt(self, question: str, schema: Any, ontology_context: str, session_context: str, invalid_sql: str, errors: list[str]) -> str:
        clean_context = session_context.replace("Vector Memory:", "").strip() if session_context else ""
        relevant_rules = clean_context if clean_context else "(No specific business rules required)"
        error_block = "\n".join(f"  - {e}" for e in errors) if errors else "  - Invalid SQL"

        return f"""
You are repairing a failed SQL query.
QUESTION: {question}
SCHEMA:
{self._prune_schema(schema)}

METRIC GLOSSARY:
{ontology_context}

RELEVANT BUSINESS RULES:
{relevant_rules}

PREVIOUS BROKEN SQL:
{invalid_sql}

ERRORS TO FIX:
{error_block}

OUTPUT:
-- Fix applied: [what changed and why]
[Corrected SQL]
""".strip()

    def build_rewrite_prompt(self, user_question: str, chat_history: str = "", dialogue_state: str = "") -> str: return ""
    def build_plan_prompt(self, user_question: str, rewritten_question: str, recent_context: str = "", dialogue_state: str = "", rewrite_result: str = "") -> str: return ""
    def build_plan_critic_prompt(self, *, user_question: str, rewritten_question: str, plan_json: str, recent_context: str = "", dialogue_state: str = "") -> str: return ""
    def build_json_repair_prompt(self, *, schema_name: str, schema: dict, previous_output: str, validation_errors: list[str]) -> str: return ""
    def build_sql_critic_prompt(self, *, question: str, sql: str, schema: Any, session_context: str = "", examples_context: str = "") -> str: return ""

    def build_synthesis_prompt(self, question: str, data_preview: str, row_count: int, executed_sql: str, ontology_context: str = "") -> str:
        preview = self._trim(data_preview, 3000)
        return f"""
You are a senior business analyst answering a manager's question.
ANSWER RULES:
  1. Lead with the key number.
  2. Use business language.
  3. Never mention SQL, databases, queries.
  4. Length: 2–4 sentences maximum.

METRIC CONTEXT:
{ontology_context}

USER QUESTION: {question}
DATA:
{preview}

Write your business analyst answer:
""".strip()