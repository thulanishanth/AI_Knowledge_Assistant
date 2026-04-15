# app/services/prompt_builder.py
#app/services/prompt_builder.py
from __future__ import annotations

import json

from app.core.settings import settings
from app.infrastructure.repositories.schema_repository import TableSchema


class PromptBuilder:
    """Centralized builder for all LLM prompts."""

    TEACHING_SCHEMA = {
        "canonical_term": "The primary name of the concept.",
        "aliases": ["list", "of", "similar", "words"],
        "sql_condition": "Valid SQL WHERE clause fragment (e.g. column_name = 'Value')",
        "description": "Short human readable explanation."
    }

    REWRITE_SCHEMA = {
        "normalized_question": "Normalized version of the user's message.",
        "standalone_question": "Standalone version with pronouns and context resolved.",
        "is_follow_up": False,
        "follow_up_strategy": "none | refinement | drill_down | new_metric | correction",
        "missing_context": [],
    }

    PLAN_SCHEMA = {
        "route": "database_query | clarify | schema_answer | show_sql | show_source | explain_last_answer | general_answer | diagnose | teach_rule",
        "standalone_question": "Resolved, explicit version of the question.",
        "user_goal": "Short summary of what the user wants.",
        "needs_clarification": False,
        "clarifying_question": "",
        "requires_schema": True,
        "requires_business_mapping": False,
        "follow_up_strategy": "none | refinement | drill_down | new_metric | correction",
        "confidence": 0.95,
        "dimensions": [],
        "filters": {},
        "date_range": None,
        "comparison_target": None,
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
        "reason": "",
        "clarifying_question": "",
    }

    @staticmethod
    def _trim(text: str | None, limit: int = 4000) -> str:
        value = (text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit].rstrip()

    @staticmethod
    def _schema_block(schema: dict[str, object]) -> str:
        return json.dumps(schema, ensure_ascii=True, indent=2)

    def build_teaching_prompt(self, instruction: str, schema: TableSchema) -> str:
        schema_block = schema.to_prompt_block()
        return f"""
You are a database dictionary builder. The user is teaching you a new business rule or definition.
Translate their natural language instruction into a strict SQL WHERE clause condition based on the schema.

Schema:
{schema_block}

User's Instruction:
{instruction}

Return exactly this JSON schema:
{self._schema_block(self.TEACHING_SCHEMA)}
""".strip()

    def build_data_aware_clarification_prompt(self, unresolved_terms: list[str], question: str, schema: TableSchema) -> str:
        schema_block = schema.to_prompt_block()
        terms = ", ".join(unresolved_terms)
        return f"""
You are an expert data analyst assistant.
The user asked a question, but used term(s) that are not defined in our database dictionary: "{terms}".

Your goal is to formulate a highly specific, polite clarifying question to ask the user.
Look at the database schema below. Guess which column(s) or sample values the user might be referring to, and offer them as a suggestion.

Schema:
{schema_block}

User Question:
{question}

Task: Write a 1-to-2 sentence response. Inform the user you don't know how to define "{terms}", and ask them if they mean a specific column or value from the schema.
""".strip()

    def build_rewrite_prompt(
        self,
        user_question: str,
        recent_context: str = "",
        dialogue_state: str = "",
    ) -> str:
        context = self._trim(recent_context, 2000) or "(none)"
        state = self._trim(dialogue_state, 1000) or "(none)"

        return f"""
You are Stage 1 and Stage 2 of a planning pipeline for a database-grounded assistant.

Your job:
1. Normalize the user's wording.
2. Resolve follow-up context such as pronouns, "same as before", "only canceled ones", or "why?".
3. Produce a standalone question for downstream routing.

Return ONLY valid JSON. No markdown. No explanations.

Rules:
1. Resolve pronouns and follow-up references using the recent context and dialogue state.
2. CRITICAL: If the `Dialogue State` shows a `pending_clarification`, and the user's message is answering it, you MUST substitute their answer into the standalone question (e.g., if they say "cost is 0", replace "free stay" with "price is 0").
3. If the request depends on missing context, list that in "missing_context".
4. Keep "standalone_question" faithful to the user. Do not invent metrics or filters.

Recent conversation window:
{context}

Current Dialogue State (Last metrics, filters, pending clarifications):
{state}

User question:
{user_question}

Return exactly this JSON schema:
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
        state = self._trim(dialogue_state, 1000) or "(none)"
        rewrite_block = self._trim(rewrite_result, 1200) or "(none)"

        return f"""
You are Stage 3 of a planning pipeline for a database-grounded business assistant.

Your job is to choose the next safe action for the user's request.

Return ONLY valid JSON. No markdown. No explanations.

Allowed routes:
- "clarify": The request is too vague, ambiguous, or lacks required parameters.
- "database_query": The user is asking for data, metrics, trends, comparisons, or rows.
- "schema_answer": The user asks about columns, fields, table structure, or available data.
- "show_sql": The user asks to see the SQL query used.
- "show_source": The user asks where the answer came from or which table/columns were used.
- "explain_last_answer": The user asks how the last answer was derived or calculated.
- "diagnose": The user says the previous answer or data is incorrect, wrong, or doesn't make sense.
- "teach_rule": The user is explicitly correcting a rule, defining a term, or saying "remember that X means Y".
- "general_answer": Greetings, thanks, or non-database chat.

Rules:
1. Prefer "clarify" over risky guessing when critical context is missing.
2. CRITICAL: If the Dialogue State has a `pending_clarification` and the user just provided the missing info, route to "database_query". DO NOT repeat the exact same clarifying question.
3. Use "requires_business_mapping" only when aliases, acronyms, vendors, or business shorthand need approved mapping.
4. For "general_answer", do not route database questions away from data unless they are truly general chat.
5. Populate dimensions, filters, date_range, and comparison_target only when grounded in the request.
6. Confidence must be between 0 and 1.

Recent conversation window:
{context}

Current Dialogue State:
{state}

Rewrite stage output:
{rewrite_block}

Original user question:
{user_question}

Standalone question candidate:
{rewritten_question}

Return exactly this JSON schema:
{self._schema_block(self.PLAN_SCHEMA)}
""".strip()

    def build_json_repair_prompt(
        self,
        *,
        schema_name: str,
        schema: dict[str, object],
        previous_output: str,
        validation_errors: list[str],
    ) -> str:
        errors = "\n".join(f"- {item}" for item in validation_errors) or "- Unknown error"
        output = self._trim(previous_output, 1800) or "(empty)"

        return f"""
You previously attempted to return JSON for `{schema_name}`, but the output was invalid.

Return ONLY valid JSON.
Do not add markdown.
Do not add explanation text.
Do not wrap the result in code fences.

Validation errors:
{errors}

Previous invalid output:
{output}

Return exactly this JSON schema:
{self._schema_block(schema)}
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
        context = self._trim(recent_context, 1500) or "(none)"
        state = self._trim(dialogue_state, 1000) or "(none)"
        plan_block = self._trim(plan_json, 1200) or "(none)"

        return f"""
You are a lightweight planning critic for a database-grounded assistant.

Review the proposed plan and decide whether it is safe and appropriate.

Return ONLY valid JSON. No markdown. No explanations.

Decision rules:
- Use "approve" if the plan is safe and route selection looks correct.
- Use "clarify" if the request is ambiguous or missing critical context.
- Use "replan" if the route is likely wrong or the standalone question is materially flawed.
- CRITICAL: If the proposed plan is routing to "clarify" with the exact same question that is already in the Dialogue State's `pending_clarification`, you MUST reject it with "replan" or force "approve" on a database route. Do not trap the user in a loop.

Recent context:
{context}

Dialogue state:
{state}

User question:
{user_question}

Standalone question:
{rewritten_question}

Proposed plan:
{plan_block}

Return exactly this JSON schema:
{self._schema_block(self.CRITIC_SCHEMA)}
""".strip()

    def build_sql_critic_prompt(
        self,
        *,
        question: str,
        sql: str,
        schema: TableSchema,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        business_rules = self._trim(session_context, 2500) or "(none)"
        examples = self._trim(examples_context, 1500) or "(none)"

        return f"""
You are a lightweight SQL critic.

Review whether the SQL is a safe and relevant match for the user's question.

Return ONLY valid JSON. No markdown. No explanations.

Decision rules:
- Use "approve" if the SQL is aligned to the question and schema.
- Use "retry" if the SQL likely misinterprets the request but the request is still answerable.
- Use "clarify" if the question is too ambiguous to answer safely.

User question:
{question}

SQL candidate:
{sql}

Schema:
{schema.to_prompt_block()}

Business rules and retrieved knowledge:
{business_rules}

Relevant examples:
{examples}

Return exactly this JSON schema:
{self._schema_block(self.SQL_CRITIC_SCHEMA)}
""".strip()

    def build_general_answer_prompt(
        self,
        user_question: str,
        recent_context: str = "",
    ) -> str:
        context = self._trim(recent_context, 1200) or "(none)"

        return f"""
You are a helpful assistant in a business data application.

Answer the user's general or conversational message naturally.
Keep it concise, useful, and friendly.
Do not invent database results.
If the user asks something unrelated to the database, answer briefly and offer to help with data questions too.

Recent conversation:
{context}

User message:
{user_question}

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
        preview = self._trim(data_preview, 2500)
        return f"""
You are a grounded data assistant. The user is asking you to explain the previous database result, and they have provided specific instructions on HOW to explain it.

User's current instruction:
{user_instruction}

Context of the last query:
- Original question: {last_question}
- Executed SQL: {executed_sql}
- Rows returned: {row_count}
- Data preview: {preview}

Provide a clear explanation that STRICTLY honors the user's instruction (e.g., if they asked not to show the query, do not show it).
""".strip()

    def build_diagnostic_prompt(
        self,
        user_complaint: str,
        last_question: str,
        last_sql: str,
    ) -> str:
        return f"""
You are a data assistant debugging a query with the user.
The user indicated that your last answer was incorrect or looks wrong.

User's complaint: {user_complaint}
Original question you answered: {last_question}
SQL Executed: {last_sql}

Write a polite, conversational response (2-4 sentences).
Explain to the user exactly how you calculated the data (mention the specific SQL filters you used in plain English).
Ask them which rule or filter was incorrect. Tell them they can correct you by replying: "Remember that [term] means [correct rule]".
""".strip()

    def build_sql_prompt(
        self,
        question: str,
        schema: TableSchema,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        business_rules = self._trim(session_context, 3500) or "(none)"
        examples = self._trim(examples_context, 2500) or "(none)"
        schema_block = schema.to_prompt_block()

        return f"""
You are a deterministic MySQL SQL generator.

Your job:
Convert the user's business question into ONE correct SQL query.

Target table:
{schema.table_name}

Hard rules:
- Use ONLY this table: {schema.table_name}
- Use ONLY columns that appear in the schema below
- Output ONLY SQL
- No markdown
- No explanation
- No comments
- Only one statement
- Only SELECT or WITH ... SELECT
- Do not invent columns, tables, joins, or metrics
- If the question cannot be answered from the provided schema and business rules, return exactly:
INSUFFICIENT_CONTEXT
- Keep the result bounded with LIMIT {settings.max_query_results} UNLESS using aggregate functions (COUNT, AVG, SUM, MIN, MAX) without a GROUP BY.
- Prefer simple, correct SQL over clever SQL
- When filtering text categories, use exact values only if supported by provided business context
- If a metric formula is not explicitly defined in the business rules, do not invent it

Schema:
{schema_block}

Business rules and retrieved knowledge:
{business_rules}

Relevant examples:
{examples}

User question:
{question}

Output:
SQL only
""".strip()

    def build_sql_repair_prompt(
        self,
        question: str,
        schema: TableSchema,
        session_context: str,
        invalid_sql: str,
        errors: list[str],
        examples_context: str = "",
    ) -> str:
        business_rules = self._trim(session_context, 3500) or "(none)"
        examples = self._trim(examples_context, 2500) or "(none)"
        error_block = "\n".join(f"- {item}" for item in errors) if errors else "- Invalid SQL"

        return f"""
You are repairing a failed SQL query.

Return ONLY corrected SQL.
No markdown.
No explanation.
No comments.

Target table:
{schema.table_name}

Question:
{question}

Schema:
{schema.to_prompt_block()}

Business rules:
{business_rules}

Relevant examples:
{examples}

Previous invalid SQL:
{invalid_sql or "(empty)"}

Validation / execution issues:
{error_block}

Repair rules:
- Keep only one statement
- Use only SELECT or WITH ... SELECT
- Use only table {schema.table_name}
- Use only columns from the schema
- Keep the result bounded with LIMIT {settings.max_query_results} UNLESS using aggregate functions (COUNT, AVG, SUM, MIN, MAX) without a GROUP BY.
- If the question still cannot be answered safely, return exactly:
INSUFFICIENT_CONTEXT

Output:
SQL only
""".strip()

    def build_synthesis_prompt(
        self,
        question: str,
        data_preview: str,
        row_count: int,
        executed_sql: str,
    ) -> str:
        preview = self._trim(data_preview, 3500)

        return f"""
You are a grounded data explainer.

Write a short business answer using ONLY the result preview below.
Do not invent facts.
Do not mention data not visible in the result.
Do not mention assumptions as facts.
If the preview is empty, say that no matching rows were found.
Do not mention that you are an AI.

User question:
{question}

Executed SQL:
{executed_sql}

Returned row count:
{row_count}

Result preview:
{preview}

Write 2 to 5 concise sentences.
""".strip()