# app/services/prompt_builder.py
from __future__ import annotations

from app.core.settings import settings
from app.infrastructure.repositories.schema_repository import TableSchema


class PromptBuilder:
    """Centralized builder for all LLM prompts."""

    @staticmethod
    def _trim(text: str | None, limit: int = 4000) -> str:
        value = (text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit].rstrip()

    def build_intent_prompt(self, user_question: str, memory_context: str = "") -> str:
        recent_context = self._trim(memory_context, 2500) or "(none)"

        return f"""
You are the routing and rewrite engine for a business data assistant.

Return ONLY valid JSON.
Do not add markdown.
Do not add explanations.

Allowed intents:
- "schema_inquiry": user asks about columns, schema, fields, structure
- "database_query": user asks for data, counts, aggregations, rows, trends
- "refine_last_query": user modifies the previous result/query
- "explain_last_answer": user asks how the last answer was derived
- "ask_for_sql": user asks to see the SQL/query used
- "ask_for_source": user asks which table/columns/source were used
- "greeting": hello/thanks/bye
- "general_chitchat": non-data chat

Rules:
1. If the user is asking for data, return a grammatically clean standalone question in "corrected_query".
2. If the user is following up on a previous question, use the recent context to rewrite a full standalone question.
3. Do not invent business metrics that were never mentioned.
4. Keep "direct_response" empty unless intent is greeting or general_chitchat.
5. "difficulty_score" must be 0 to 100.

Recent conversation context:
{recent_context}

User question:
{user_question}

Return exactly this JSON shape:
{{
  "intent": "database_query",
  "corrected_query": "clean standalone question",
  "direct_response": "",
  "difficulty_score": 45,
  "is_follow_up": false
}}
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
- Unless the user explicitly asks for more rows, keep the result bounded with LIMIT {settings.max_query_results}
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