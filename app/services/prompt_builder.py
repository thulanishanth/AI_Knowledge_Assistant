# app/services/prompt_builder.py

from __future__ import annotations

import json
from typing import Any

from app.core.settings import settings


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
        "thought_process": "Write 1-2 sentences explaining your logical reasoning before choosing the route.",
        "route": "database_query | clarify | schema_answer | show_sql | show_source | explain_last_answer | general_answer | diagnose | teach_rule",
        "standalone_question": "Resolved, explicit version of the question.",
        "user_goal": "Short summary of what the user wants.",
        "needs_clarification": False,
        "clarifying_question": "If routing to clarify, state exactly what is confusing or contradictory.",
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
        "reason": "Explain if the SQL misinterprets the request or contains logically impossible WHERE/JOIN conditions.",
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

    def _prune_schema(self, schema: Any) -> str:
        excluded = {"chat_messages", "alembic_version"}
        
        # UniversalSchema (live introspection — primary path)
        if hasattr(schema, "schema_dict"):
            lines = [
                f"Target Execution Dialect: {getattr(schema, 'dialect', 'MYSQL').upper()}",
                f"Dataset Name: {getattr(schema, 'dataset_name', 'default')}",
                "Schema Structure:"
            ]
            for table_name, columns in schema.schema_dict.items():
                if table_name.lower() in excluded:
                    continue
                lines.append(f"\nTable: {table_name}")
                for col in columns:
                    lines.append(f"  {col}")
            return "\n".join(lines)
        
        # TableSchema (legacy JSON path)
        if hasattr(schema, "tables"):
            lines = [
                f"Target Execution Dialect: {getattr(schema, 'dialect', 'MYSQL').upper()}",
                f"Dataset Name: {getattr(schema, 'dataset_name', 'default')}",
                "Schema Structure:"
            ]
            for table_name, columns in schema.tables.items():
                if table_name.lower() in excluded:
                    continue
                lines.append(f"\nTable: {table_name}")
                for col in columns:
                    pk = " [PRIMARY KEY]" if getattr(col, "is_primary_key", False) else ""
                    lines.append(f"  - {col.name} ({col.data_type}){pk}")
                    if getattr(col, "sample_values", None):
                        joined = ", ".join(f"'{v}'" for v in col.sample_values)
                        lines.append(f"    Sample values: {joined}")
            return "\n".join(lines)
        
        # Last resort
        if hasattr(schema, "to_prompt_block"):
            return schema.to_prompt_block()
        return str(schema)

    def build_teaching_prompt(self, instruction: str, schema: Any) -> str:
        schema_block = self._prune_schema(schema)
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

    def build_data_aware_clarification_prompt(self, unresolved_terms: list[str], question: str, schema: Any) -> str:
        schema_block = self._prune_schema(schema)
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
        chat_history: str = "",
        dialogue_state: str = "",
    ) -> str:
        history = self._trim(chat_history, 2000) or "(No recent history.)"
        state = self._trim(dialogue_state, 1000) or "(none)"

        return f"""
You are Stage 1 of a planning pipeline for a database assistant.

Your job:
1. Detect if the user's new message is a follow-up to the previous conversation.
2. Produce a standalone question that resolves pronouns and context.

Return ONLY valid JSON. No markdown. No explanations.

Rules:
1. If the user asks a drill-down question (e.g., Previous: "Revenue in 2018?", New: "What about October?"), you must REPLACE the old filter to form a logical new question (e.g., "What was the revenue in October 2018?"). Do not just append contradictory words.
2. If the request depends on missing context, list that in "missing_context".
3. Set "is_follow_up" to true if the user's message refers to or modifies the previous query.

Recent conversation window:
{history}

Current Dialogue State (Last metrics, filters):
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

Your job is to evaluate the user's request against our Database Schema and Semantic Ontology, then choose the safest next action.

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

Rules for Routing & Ontology:
1. ONTOLOGY CHECK: Read the "DATABASE SCHEMA" and "ONTOLOGY & SYNONYMS" in the context below. If the user asks for a specific metric (e.g. "revenue") and our ontology maps that to a column, route to "database_query". 
2. THE CLARIFICATION ROUTER: Only route to 'clarify' if the question is fundamentally impossible to answer, completely missing context, or logically contradictory. If the user asks for a metric like 'revenue' or 'sales', use your best judgment to map it to the closest financial column (like 'price' or 'avg_price_per_room'). Be brave. Do not ask for clarification for minor vocabulary differences.
3. Use the "thought_process" key to explicitly write out your logic before selecting a route.
4. Confidence must be between 0 and 1.

Context (Schema, Ontology, and Recent Conversation):
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
        schema: Any,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        business_rules = self._trim(session_context, 2500) or "(none)"
        examples = self._trim(examples_context, 1500) or "(none)"

        return f"""
You are a lightweight SQL critic.

Review whether the SQL is a safe, logically sound, and relevant match for the user's question.

Return ONLY valid JSON. No markdown. No explanations.

Decision rules:
- Use "approve" if the SQL is aligned to the question and schema.
- Use "retry" if the SQL misinterprets the request, contains mutually exclusive WHERE clauses (e.g., requiring a column to be two different values simultaneously), or contains impossible JOINs.
- Use "clarify" if the question is too ambiguous to answer safely.

User question:
{question}

SQL candidate:
{sql}

Schema:
{self._prune_schema(schema)}

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
        schema: Any,
        session_context: str = "",
        examples_context: str = "",
    ) -> str:
        business_rules = self._trim(session_context, 8000) or "(none)"
        examples = self._trim(examples_context, 2500) or "(none)"
        schema_block = self._prune_schema(schema)

        return f"""
        You are an expert MySQL database architect.

        Your job:
        Convert the user's business question into ONE correct SQL query.

        Hard rules:
        1. Use ONLY tables and columns that appear in the schema below.
        2. Output ONLY SQL. No markdown formatting. No conversational text.
        3. CHAIN OF THOUGHT: You MUST write 1-2 sentences of reasoning at the very top of your output using SQL comments (`--`). Explain which business rules apply and how you are handling filters.
        4. Only one statement (SELECT or WITH ... SELECT).
        5. Do not invent metrics.
        6. If the question cannot be answered, return exactly: INSUFFICIENT_CONTEXT

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
        schema: Any,
        session_context: str,
        invalid_sql: str,
        errors: list[str],
        examples_context: str = "",
    ) -> str:
        business_rules = self._trim(session_context, 8000) or "(none)"
        examples = self._trim(examples_context, 2500) or "(none)"
        error_block = "\n".join(f"- {item}" for item in errors) if errors else "- Invalid SQL"

        return f"""
You are repairing a failed SQL query.

Return ONLY corrected SQL.
No markdown. No explanation. No comments.

Question:
{question}

Schema:
{self._prune_schema(schema)}

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
- Use only tables and columns from the schema
- Keep the result bounded with LIMIT {settings.max_query_results} UNLESS using aggregate functions.
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