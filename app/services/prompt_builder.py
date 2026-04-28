#app/services/prompt_builder.py
"""
Prompt builder — fully dynamic, schema-first.

No hardcoded domain terms, no hardcoded calculation rules, no hardcoded column names.
The LLM reads the schema (with sample values + statistics) and figures out the
domain, calculations, and mappings itself.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.settings import settings


class PromptBuilder:

    @staticmethod
    def _trim(text: str | None, limit: int = 4000) -> str:
        return (text or "").strip()[:limit].rstrip()

    def _schema_block(self, schema: Any) -> str:
        """
        Render the richest possible schema representation.
        Uses ColumnProfile.to_prompt_line() — includes allowed values,
        numeric ranges, and null info so the LLM understands the domain.
        """
        if hasattr(schema, "to_prompt_block"):
            return schema.to_prompt_block()
        if hasattr(schema, "schema_dict"):
            lines = [
                f"Database: {getattr(schema, 'dataset_name', 'unknown')}",
                f"Dialect: {getattr(schema, 'dialect', 'sql').upper()}",
                "Schema:",
            ]
            for table, cols in schema.schema_dict.items():
                lines.append(f"\nTable: `{table}`")
                for col in cols:
                    lines.append(f"  {col}")
            return "\n".join(lines)
        return str(schema)

    # Keep _prune_schema as alias for backward compatibility
    def _prune_schema(self, schema: Any) -> str:
        return self._schema_block(schema)

    # ─────────────────────────────────────────────
    # SQL GENERATION — schema-first, no hardcoding
    # ─────────────────────────────────────────────

    def build_sql_prompt(
        self,
        question: str,
        schema: Any,
        session_context: str = "",
        examples_context: str = "",
        ontology_context: str = "",   # kept for signature compat, merged into context
    ) -> str:
        schema_block = self._schema_block(schema)
        context_block = self._trim(session_context, 2000) or "(none)"
        rules_block = self._trim(examples_context, 1200) or "(none — answer using schema alone)"

        return f"""You are an expert SQL analyst. Convert the user's question into ONE correct SQL query.

━━━ DATABASE SCHEMA ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(The [Values] list for each column shows what data exists — use this to map
 user phrases to the correct column values. Do NOT guess column names.)
{schema_block}

━━━ CONVERSATION CONTEXT (resolve follow-up references) ━━━━━━━━━━━━━━━━━
{context_block}

━━━ BUSINESS RULES (backup enrichment — apply only when directly relevant) ━
{rules_block}

━━━ SQL RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Use ONLY columns and tables from the schema above.
2. Map user terms → column values using the [Values] hints:
     e.g. if user says "platform X" and [Values] shows 'Online', use 'Online'.
3. One SELECT or WITH…SELECT statement only.
4. Aggregate queries (COUNT/SUM/AVG/MIN/MAX without GROUP BY on full table): no LIMIT.
   Row-returning queries: LIMIT {settings.max_query_results}.
5. Protect every division: NULLIF(denominator, 0).
6. Percentage: (part / NULLIF(whole, 0)) * 100.
7. If question CANNOT be answered from this schema: output exactly INSUFFICIENT_CONTEXT.
8. Output ONLY the SQL — no explanation, no markdown fences, no comments.

━━━ QUESTION ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{question}

SQL:""".strip()

    def build_sql_repair_prompt(
        self,
        question: str,
        schema: Any,
        session_context: str,
        invalid_sql: str,
        errors: list[str],
        ontology_context: str = "",
    ) -> str:
        schema_block = self._schema_block(schema)
        context_block = self._trim(session_context, 1200) or "(none)"
        error_block = "\n".join(f"  - {e}" for e in errors) or "  - Invalid SQL"

        return f"""Repair the broken SQL query below. Fix ONLY the listed errors.

SCHEMA:
{schema_block}

CONTEXT:
{context_block}

QUESTION: {question}

BROKEN SQL:
{invalid_sql}

ERRORS TO FIX:
{error_block}

Output ONLY the corrected SQL. No explanation, no fences.

SQL:""".strip()

    # ─────────────────────────────────────────────
    # INTENT: REWRITE / FOLLOW-UP DETECTION
    # ─────────────────────────────────────────────

    def build_rewrite_prompt(
        self, user_question: str, chat_history: str = "", dialogue_state: str = ""
    ) -> str:
        history_block = self._trim(chat_history, 1500) or "(no prior conversation)"
        state_block = self._trim(dialogue_state, 400) or "(none)"

        return f"""Determine whether the user's message is a follow-up to the conversation.

CONVERSATION HISTORY (most recent last):
{history_block}

PREVIOUS STATE:
{state_block}

USER MESSAGE: "{user_question}"

TASK:
1. Does the message reference anything from prior conversation
   (e.g., "that", "those", "same", "it", "the previous result")?
2. If YES — rewrite as a fully self-contained standalone question with all
   referenced context spelled out explicitly.
3. If NO — standalone_question equals the original.

follow_up_strategy: "none" | "refinement" | "drill_down" | "new_metric" | "correction"

Respond using the submit_rewrite tool.
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
        schema_summary: str = "",
    ) -> str:
        context_block = self._trim(recent_context, 1000) or "(none)"
        state_block = self._trim(dialogue_state, 400) or "(none)"
        schema_hint = self._trim(schema_summary, 500) or "(not provided)"

        return f"""You are a query router for a database assistant.

ROUTES:
  database_query     — User wants data from the database (use this by default).
  general_answer     — Pure concept explanation needing no data.
  clarify            — Question is genuinely too ambiguous to query.
  schema_answer      — User asks about table/column structure.
  show_sql           — User wants to see the SQL from the previous answer.
  explain_last_answer — User wants more detail about the previous answer.

SCHEMA SUMMARY:
{schema_hint}

STATE: {state_block}
CONTEXT: {context_block}
REWRITE: {rewrite_result}

ORIGINAL: "{user_question}"
STANDALONE: "{rewritten_question}"

RULES:
- Default to database_query for numbers, counts, lists, comparisons, dates.
- Use general_answer ONLY for pure definitions needing zero database data.
- Use clarify only when there are truly multiple interpretations.

Respond using the submit_execution_plan tool.
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
        return f"""Review this routing plan. Be conservative — only override if clearly wrong.

QUESTION: "{user_question}"
STANDALONE: "{rewritten_question}"
PLAN: {plan_json}
CONTEXT: {self._trim(recent_context, 500) or "(none)"}
STATE: {self._trim(dialogue_state, 300) or "(none)"}

- Correct → verdict = "approve"
- Genuinely ambiguous → verdict = "clarify" + clarifying_question
- Clearly wrong route → verdict = "replan"

Respond using the submit_plan_review tool.
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
        schema_block = self._schema_block(schema)
        rules_block = self._trim(session_context, 800) or "(none)"

        return f"""Review whether this SQL correctly answers the question.

QUESTION: "{question}"
SCHEMA:
{schema_block}
CONTEXT: {rules_block}
SQL: {sql}

Check: (1) answers the exact question? (2) valid columns/tables? (3) correct aggregation?
(4) logic errors? (5) dangerous unbounded query?

VERDICT: "approve" | "retry" (provide reason) | "clarify"

Respond using the submit_sql_review tool.
""".strip()

    # ─────────────────────────────────────────────
    # ANALYTICAL DECOMPOSITION
    # ─────────────────────────────────────────────

    def build_analytical_plan_prompt(
        self,
        question: str,
        schema_block: str,
        business_rules: str = "",
    ) -> str:
        rules_block = business_rules.strip() or "(none)"
        return f"""You are a data analyst planning a step-by-step answer to a business question.

SCHEMA:
{schema_block}

BUSINESS RULES (backup):
{rules_block}

QUESTION: "{question}"

Does this TRULY need multiple SQL queries, or can one SQL (with CTEs/subqueries) answer it?

MULTI-STEP NEEDED when:
- Exclusion logic where the excluded set must be computed first
- One result depends numerically on a previous result
- Comparing two fully independent subsets incorrectly handled by one SQL

NOT NEEDED when:
- A CTE or subquery handles the full logic
- A WHERE clause with AND/OR covers it

If multi-step: max 4 steps, each a complete SELECT, focused on ONE thing.

Respond using the submit_reasoning_plan tool.
""".strip()

    def build_analytical_synthesis_prompt(
        self, question: str, steps_block: str
    ) -> str:
        return f"""You are a senior business analyst. Synthesize an answer from these step results.

QUESTION: "{question}"

STEP RESULTS:
{steps_block}

RULES:
1. State the key number(s) first.
2. One sentence on how it was derived.
3. Business language only — no SQL, no technical terms.
4. 2–3 sentences max. Format numbers with commas (3,253 not 3253).
5. If a step failed, note what could not be determined.

Respond using the submit_final_answer tool.
""".strip()

    # ─────────────────────────────────────────────
    # COMPOUND DECOMPOSITION
    # ─────────────────────────────────────────────

    def build_decompose_prompt(
        self, question: str, schema_block: str, dialogue_state: str = ""
    ) -> str:
        return f"""Decide whether this question needs multiple SQL queries.

SCHEMA:
{schema_block}

STATE: {dialogue_state or "(none)"}

QUESTION: "{question}"

Only mark compound if ONE SQL truly cannot answer it.
Compound examples: "X vs Y", "this month vs last month", dependent sub-calculations.

merge_strategy: "single" | "compare" | "join" | "append"

Respond using the submit_decomposition tool.
""".strip()

    # ─────────────────────────────────────────────
    # TERM RESOLUTION (no regex — pure LLM + schema)
    # ─────────────────────────────────────────────

    def build_term_resolution_prompt(
        self,
        question: str,
        schema_block: str,
        business_rules: str = "",
    ) -> str:
        rules_block = business_rules.strip() or "(none)"
        return f"""A user asked a question that may contain domain terms, brand names, or
business phrases that need mapping to database column values.

SCHEMA (use the [Values] lists to make the mapping):
{schema_block}

BUSINESS RULES (backup):
{rules_block}

QUESTION: "{question}"

TASK: For each ambiguous term/phrase in the question:
1. What does it mean in this business context?
2. Which column and value in the schema represents it?
3. Write the exact SQL WHERE fragment.

Examples of what to resolve:
- A booking platform name → the matching value in a category column
- A guest type ("VIP", "loyal") → a column = 'value' condition
- A time phrase ("last 90 days", "this year") → a date range condition
- A business phrase ("high season", "long stay") → a column IN (...) condition

Use ONLY columns and values visible in the schema above.
Set needs_clarification = true if genuinely unmappable.
Set needs_resolution = false if the question has no ambiguous terms.

Respond using the submit_term_resolution tool.
""".strip()

    # ─────────────────────────────────────────────
    # GENERAL ANSWER
    # ─────────────────────────────────────────────

    def build_general_answer_prompt(
        self, user_question: str, memory_context: str = ""
    ) -> str:
        context_block = self._trim(memory_context, 800) or "(none)"
        return f"""You are a helpful business assistant. Answer conversationally and accurately.
You do NOT have access to the database for this answer — use general knowledge.

CONTEXT: {context_block}
QUESTION: {user_question}

Rules: 2–4 sentences. No SQL, no technical jargon. If uncertain, say so.

Answer:""".strip()