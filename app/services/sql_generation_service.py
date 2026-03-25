#app/services/sql_generation_service.py
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from app.infrastructure.repositories.schema_repository import TableSchema
from app.security.sql_guard import SqlGuard, SqlValidationResult
from app.services.llm_client import call_llm
from app.services.schema_service import SchemaService

@dataclass(slots=True)
class SqlGenerationResult:
    sql: str = ""
    validation: SqlValidationResult = field(
        default_factory=lambda: SqlValidationResult(is_valid=False, errors=["No SQL generated."])
    )
    strategy: str = "none"

    @property
    def is_valid(self) -> bool:
        return self.validation.is_valid

class SQLGenerationService:
    """100% LLM-driven SQL generation for maximum flexibility and accuracy."""

    def __init__(self, schema_service: SchemaService, sql_guard: SqlGuard) -> None:
        self._schema_service = schema_service
        self._sql_guard = sql_guard

    async def generate_sql(
        self,
        question: str,
        schema: TableSchema,
        session_context: str = "",
        intent: Any | None = None,
    ) -> SqlGenerationResult:
        
        prompt = self._build_sql_prompt(question, schema, session_context)
        candidate = await asyncio.to_thread(call_llm, prompt, 300)
        sql_candidate = self._extract_sql(candidate)
        validation = self._sql_guard.validate(sql_candidate)

        if validation.is_valid:
            return SqlGenerationResult(
                sql=validation.normalized_sql,
                validation=validation,
                strategy="llm_primary",
            )

        repair_prompt = self._build_repair_prompt(
            question=question,
            schema=schema,
            session_context=session_context,
            invalid_sql=sql_candidate,
            errors=validation.errors,
        )
        repaired = await asyncio.to_thread(call_llm, repair_prompt, 300)
        repaired_sql = self._extract_sql(repaired)
        repaired_validation = self._sql_guard.validate(repaired_sql)

        return SqlGenerationResult(
            sql=repaired_validation.normalized_sql if repaired_validation.is_valid else repaired_sql,
            validation=repaired_validation,
            strategy="llm_repair",
        )

    @staticmethod
    def _extract_sql(text: str) -> str:
        if not text:
            return ""
        value = text.strip()
        fenced = re.search(r"`{3}(?:sql)?(.*?)`{3}", value, re.IGNORECASE | re.DOTALL)
        if fenced:
            value = fenced.group(1).strip()

        match = re.search(r"\bselect\b.*", value, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""

        sql = match.group(0).strip()
        if ";" in sql:
            sql = sql.split(";", 1)[0].strip()
        return f"{sql};"

    @staticmethod
    def _build_sql_prompt(question: str, schema: TableSchema, session_context: str) -> str:
        return "\n".join(
            [
                "You are an expert MySQL Data Analyst. Your only job is to generate ONE valid, read-only SELECT query.",
                "Rules:",
                f"- You MUST strictly use the table `{schema.table_name}` and its columns.",
                "- Only use columns that explicitly exist in the schema below.",
                "- If the user asks a conversational question or something unrelated to the database, generate exactly: SELECT 'I can only answer questions about the database.' AS message;",
                "- For complex questions (rates, percentages, ratios), use SQL math. Example: `SUM(CASE WHEN status='Canceled' THEN 1 ELSE 0 END) / COUNT(*)`.",
                "- Use GROUP BY and aggregate functions (COUNT, AVG, SUM, MIN, MAX) when the question implies aggregation.",
                # --- THIS IS THE RULE WE CHANGED ---
                "- If the user asks for a specific number of results (e.g., 'top 5', 'show me 10'), you MUST use the LIMIT keyword.",
                "- Return ONLY the raw SQL code inside a ```sql block. No explanations.",
                "",
                "Schema:",
                schema.to_prompt_block(),
                "",
                "Session context:",
                session_context or "(none)",
                "",
                f"Question: {question}",
                "Think step-by-step, then write the SQL.",
                "SQL:"
            ]
        )

    @staticmethod
    def _build_repair_prompt(
        question: str,
        schema: TableSchema,
        session_context: str,
        invalid_sql: str,
        errors: list[str],
    ) -> str:
        return "\n".join(
            [
                "Repair the invalid SQL and return one valid MySQL SELECT query only.",
                "Use only the provided schema.",
                "",
                "Schema:",
                schema.to_prompt_block(),
                "",
                f"Question: {question}",
                f"Session context: {session_context or '(none)'}",
                f"Invalid SQL: {invalid_sql}",
                f"Validation errors: {'; '.join(errors)}",
                "Corrected SQL:"
            ]
        )