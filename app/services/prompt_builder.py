# app/services/prompt_builder.py
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
    TEACHING_SCHEMA = {"canonical_term": "name", "aliases": ["words"], "sql_condition": "WHERE clause", "description": "desc"}
    REWRITE_SCHEMA = {"normalized_question": "str", "standalone_question": "str", "is_follow_up": False, "follow_up_strategy": "none", "missing_context": []}
    PLAN_SCHEMA = {"thought_process": "str", "route": "database_query", "standalone_question": "str", "user_goal": "str", "needs_clarification": False, "clarifying_question": "", "requires_schema": True, "requires_business_mapping": False, "follow_up_strategy": "none", "confidence": 0.95, "filters": {}, "date_range": None}
    CRITIC_SCHEMA = {"verdict": "approve", "reason": "", "needs_clarification": False, "clarifying_question": "", "confidence": 0.9}
    SQL_CRITIC_SCHEMA = {"verdict": "approve", "reason": "", "clarifying_question": ""}

    @staticmethod
    def _trim(text: str | None, limit: int = 4000) -> str:
        return (text or "").strip()[:limit].rstrip()

    @staticmethod
    def _schema_block(schema: dict) -> str:
        return json.dumps(schema, ensure_ascii=True, indent=2)

    def _prune_schema(self, schema: Any) -> str:
        if hasattr(schema, "schema_dict"):
            lines = [
                f"Dialect: {getattr(schema, 'dialect', 'SQL').upper()}",
                f"Database: {getattr(schema, 'dataset_name', 'default')}",
                "Tables:",
            ]
            for tname, cols in schema.schema_dict.items():
                if tname.lower() in {"chat_messages", "alembic_version"}:
                    continue
                lines.append(f"\n  {tname}")
                for col in cols:
                    lines.append(f"    {col}")
            return "\n".join(lines)
        # Fallback for TableSchema with .tables attribute
        if hasattr(schema, "tables"):
            lines = [
                f"Dialect: {getattr(schema, 'dialect', 'SQL').upper()}",
                f"Database: {getattr(schema, 'dataset_name', 'default')}",
                "Tables:",
            ]
            for tname, cols in schema.tables.items():
                lines.append(f"\n  {tname}")
                for col in cols:
                    col_str = f"    - {col.name} ({col.data_type})"
                    if col.is_primary_key:
                        col_str += " [PRIMARY KEY]"
                    lines.append(col_str)
            return "\n".join(lines)
        return str(schema)

    # ─────────────────────────────────────────────
    # SQL GENERATION
    # ─────────────────────────────────────────────

    def build_sql_prompt(
        self, question: str, schema: Any, ontology_context: str, session_context: str = "", examples_context: str = ""
    ) -> str:
        schema_block = self._prune_schema(schema)
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

    def build_sql_repair_prompt(
        self, question: str, schema: Any, ontology_context: str, session_context: str, invalid_sql: str, errors: list[str]
    ) -> str:
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

    # ─────────────────────────────────────────────
    # INTENT: REWRITE / FOLLOW-UP DETECTION
    # ─────────────────────────────────────────────

    def build_rewrite_prompt(
        self, user_question: str, chat_history: str = "", dialogue_state: str = ""
    ) -> str:
        history_block = self._trim(chat_history, 1500) or "(no prior conversation)"
        state_block = self._trim(dialogue_state, 500) or "(no prior state)"

        return f"""
You are an expert conversation analyst. Your job is to determine if the user's message is a follow-up to the conversation history, and if so, rewrite it as a fully self-contained standalone question.

CONVERSATION HISTORY (most recent last):
{history_block}

PREVIOUS DIALOGUE STATE:
{state_block}

USER'S CURRENT MESSAGE:
"{user_question}"

INSTRUCTIONS:
1. Check if the message references anything from the conversation (e.g., "that", "those", "same", "it", "them", "last year", "the previous result").
2. If it IS a follow-up: rewrite it as a complete, standalone question that includes all referenced context.
3. If it is NOT a follow-up: normalized_question and standalone_question are the same as the input.
4. follow_up_strategy options: "none" | "refinement" (filter change) | "drill_down" (more detail) | "new_metric" (different metric same scope) | "correction" (user corrects something).
5. missing_context: list anything needed to answer but not present.

RESPOND using the submit_rewrite tool.
""".strip()

    # ─────────────────────────────────────────────
    # INTENT: EXECUTION PLAN
    # ─────────────────────────────────────────────

    def build_plan_prompt(
        self,
        user_question: str,
        rewritten_question: str,
        recent_context: str = "",
        dialogue_state: str = "",
        rewrite_result: str = "",
    ) -> str:
        context_block = self._trim(recent_context, 1500) or "(none)"
        state_block = self._trim(dialogue_state, 500) or "(no prior state)"

        return f"""
You are a senior query router for a business database assistant. Decide the best route for this user request.

AVAILABLE ROUTES:
  database_query   — User wants data from the database (most common).
  general_answer   — General question not needing database data (e.g., "what does ADR mean?").
  clarify          — The question is too ambiguous to query safely; ask one clarifying question.
  schema_answer    — User is asking about the structure of the data (tables, columns).
  show_sql         — User wants to see the SQL query that was run.
  explain_last_answer — User wants more explanation of the previous answer.
  diagnose         — User reports a data quality issue or inconsistency.

DIALOGUE STATE (previous turn context):
{state_block}

RECENT MEMORY CONTEXT:
{context_block}

REWRITE ANALYSIS:
{rewrite_result}

ORIGINAL QUESTION: "{user_question}"
REWRITTEN STANDALONE QUESTION: "{rewritten_question}"

ROUTING RULES:
- Default to database_query when the question involves numbers, counts, totals, lists, comparisons, or dates.
- Use general_answer ONLY for pure definitions, how-to questions, or explanations that need NO data.
- Use clarify only if the question is genuinely ambiguous (e.g., "show me sales" — which date range? which product?).
- confidence: 0.0–1.0 reflecting your certainty in the chosen route.

RESPOND using the submit_execution_plan tool.
""".strip()

    # ─────────────────────────────────────────────
    # INTENT: PLAN CRITIC
    # ─────────────────────────────────────────────

    def build_plan_critic_prompt(
        self,
        *,
        user_question: str,
        rewritten_question: str,
        plan_json: str,
        recent_context: str = "",
        dialogue_state: str = "",
    ) -> str:
        return f"""
You are a critical reviewer checking a routing plan for a database assistant.

ORIGINAL QUESTION: "{user_question}"
REWRITTEN QUESTION: "{rewritten_question}"
PROPOSED PLAN: {plan_json}

DIALOGUE STATE: {self._trim(dialogue_state, 400) or "(none)"}
CONTEXT: {self._trim(recent_context, 800) or "(none)"}

YOUR TASK:
- If the plan is correct and the route makes sense: verdict = "approve".
- If the question is genuinely ambiguous: verdict = "clarify" and provide a clarifying_question.
- If the route is clearly wrong (e.g., database_query for a definition question): verdict = "replan".

Be conservative — only override if clearly wrong. When in doubt, approve.

RESPOND using the submit_plan_review tool.
""".strip()

    # ─────────────────────────────────────────────
    # SQL CRITIC
    # ─────────────────────────────────────────────

    def build_sql_critic_prompt(
        self,
        *,
        question: str,
        sql: str,
        schema: Any,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        schema_block = self._prune_schema(schema)
        rules_block = self._trim(session_context, 1000) or "(none)"

        return f"""
You are a SQL quality reviewer. Evaluate whether the SQL query correctly and safely answers the user's question.

QUESTION: "{question}"
SCHEMA:
{schema_block}

BUSINESS RULES:
{rules_block}

GENERATED SQL:
{sql}

REVIEW CRITERIA:
1. Does the SQL answer the exact question asked?
2. Are the correct columns and tables used?
3. Is any aggregation (SUM, AVG, COUNT) correct for the question?
4. Are there any obvious logic errors (wrong filters, missing GROUP BY, etc.)?
5. Could this query cause issues (e.g., no LIMIT on a potentially large table)?

VERDICT OPTIONS:
  "approve"  — SQL is correct and safe to execute.
  "retry"    — SQL has a fixable problem; provide the reason.
  "clarify"  — The question needs clarification before a correct SQL can be written.

RESPOND using the submit_sql_review tool.
""".strip()

    # ─────────────────────────────────────────────
    # JSON REPAIR
    # ─────────────────────────────────────────────

    def build_json_repair_prompt(
        self,
        *,
        schema_name: str,
        schema: dict,
        previous_output: str,
        validation_errors: list[str],
    ) -> str:
        error_block = "\n".join(f"  - {e}" for e in validation_errors)
        return f"""
The previous LLM output was supposed to match the schema "{schema_name}" but failed validation.

EXPECTED SCHEMA:
{json.dumps(schema, indent=2)}

PREVIOUS (BROKEN) OUTPUT:
{self._trim(previous_output, 1500)}

VALIDATION ERRORS:
{error_block}

OUTPUT ONLY valid JSON matching the schema. No explanation, no markdown fences.
""".strip()

    # ─────────────────────────────────────────────
    # SYNTHESIS
    # ─────────────────────────────────────────────

    def build_synthesis_prompt(
        self, question: str, data_preview: str, row_count: int, executed_sql: str, ontology_context: str = ""
    ) -> str:
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

    # ─────────────────────────────────────────────
    # GENERAL ANSWER
    # ─────────────────────────────────────────────

    def build_general_answer_prompt(self, user_question: str, memory_context: str = "") -> str:
        context_block = self._trim(memory_context, 1000) or "(none)"
        return f"""
You are a helpful business assistant. Answer the user's question conversationally and accurately.
You do NOT have access to the database for this answer — use your general knowledge.

CONVERSATION CONTEXT:
{context_block}

USER QUESTION: {user_question}

RULES:
- Be concise (2–4 sentences).
- If the question is about a business term (e.g., "what is ADR?"), explain it clearly.
- Do not mention SQL, databases, or internal systems.
- If you genuinely don't know, say so honestly.

Answer:
""".strip()