# app/services/sql_generation_service.py
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.infrastructure.repositories.schema_repository import TableSchema
from app.security.sql_guard import SqlGuard, SqlValidationResult
from app.services.cloud_llm import call_cloud_llm
from app.services.llm_client import call_llm
from app.services.prompt_builder import PromptBuilder

logger = get_logger(__name__)


@dataclass(slots=True)
class SqlGenerationResult:
    sql: str = ""
    validation: SqlValidationResult = field(
        default_factory=lambda: SqlValidationResult(
            is_valid=False,
            errors=["No SQL generated."],
        )
    )
    strategy: str = "none"
    notice: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.validation.is_valid


class SQLGenerationService:
    """Generate and repair SQL with deterministic prompting plus strict validation."""

    def __init__(self, sql_guard: SqlGuard, prompt_builder: PromptBuilder) -> None:
        self._sql_guard = sql_guard
        self._prompt_builder = prompt_builder

    async def _robust_generate(
        self,
        prompt: str,
        model: str,
        is_cloud: bool,
        max_tokens: int = 300,
    ) -> tuple[str, str | None]:
        notice = None

        if is_cloud:
            notice = "*Using higher model for complex query.*"
            try:
                result = await call_cloud_llm(
                    prompt=prompt,
                    model_name=model,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                return result, notice
            except Exception as cloud_err:
                err_str = str(cloud_err).lower()
                if "configured" in err_str or "api_key" in err_str:
                    notice = "*Missing API key for higher model. Continuing with standard client LLM.*"
                elif "429" in err_str or "rate limit" in err_str or "quota" in err_str:
                    notice = "*Limit reached for higher model. Continuing with default model.*"
                else:
                    notice = "*Higher model unavailable. Continuing with default model.*"
                logger.warning("Cloud LLM fallback triggered: %s", cloud_err)

        try:
            result = await asyncio.to_thread(
                call_llm,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            return result, notice
        except Exception as local_err:
            logger.error("Total AI failure: %s", local_err)
            raise RuntimeError(
                "All AI models are currently unreachable. Please try again in a moment."
            ) from local_err

    async def generate_sql(
        self,
        question: str,
        schema: TableSchema,
        session_context: str = "",
        examples_context: str = "",
        model: str = "local-llm",
        is_cloud: bool = False,
    ) -> SqlGenerationResult:
        prompt = self._prompt_builder.build_sql_prompt(
            question=question,
            schema=schema,
            session_context=session_context,
            examples_context=examples_context,
        )

        try:
            candidate, notice = await self._robust_generate(
                prompt=prompt,
                model=model,
                is_cloud=is_cloud,
                max_tokens=300,
            )
        except Exception as exc:
            return SqlGenerationResult(
                validation=SqlValidationResult(is_valid=False, errors=[str(exc)])
            )

        sql_candidate = self._extract_sql(candidate)

        if not sql_candidate:
            return SqlGenerationResult(
                validation=SqlValidationResult(
                    is_valid=False,
                    errors=["Model could not ground the query in the provided schema/context."],
                ),
                notice=notice,
            )

        validation = self._sql_guard.validate(sql_candidate)
        if validation.is_valid:
            return SqlGenerationResult(
                sql=validation.normalized_sql,
                validation=validation,
                strategy="llm_primary",
                notice=notice,
            )

        repair_prompt = self._prompt_builder.build_sql_repair_prompt(
            question=question,
            schema=schema,
            session_context=session_context,
            invalid_sql=sql_candidate,
            errors=validation.errors,
            examples_context=examples_context,
        )

        try:
            repaired, repair_notice = await self._robust_generate(
                prompt=repair_prompt,
                model=model,
                is_cloud=is_cloud,
                max_tokens=300,
            )
        except Exception as exc:
            return SqlGenerationResult(
                validation=SqlValidationResult(is_valid=False, errors=[str(exc)]),
                notice=notice,
            )

        repaired_sql = self._extract_sql(repaired)
        if not repaired_sql:
            return SqlGenerationResult(
                validation=SqlValidationResult(
                    is_valid=False,
                    errors=["Model could not repair the SQL using the provided schema/context."],
                ),
                strategy="llm_repair",
                notice=repair_notice or notice,
            )

        repaired_validation = self._sql_guard.validate(repaired_sql)

        return SqlGenerationResult(
            sql=repaired_validation.normalized_sql
            if repaired_validation.is_valid
            else repaired_sql,
            validation=repaired_validation,
            strategy="llm_repair",
            notice=repair_notice or notice,
        )

    @staticmethod
    def _extract_sql(text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""

        if value.strip().upper() == "INSUFFICIENT_CONTEXT":
            return ""

        fenced = re.search(r"`{3}(?:sql)?(.*?)`{3}", value, re.IGNORECASE | re.DOTALL)
        if fenced:
            value = fenced.group(1).strip()

        if value.strip().upper() == "INSUFFICIENT_CONTEXT":
            return ""

        match = re.search(r"\b(with|select)\b.*", value, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""

        sql = match.group(0).strip()
        if ";" in sql:
            sql = sql.split(";", 1)[0].strip()
        return f"{sql};"