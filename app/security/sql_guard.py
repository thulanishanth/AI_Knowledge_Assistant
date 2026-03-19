"""Strict read-only SQL validation for the configured table."""

from __future__ import annotations

from importlib import import_module
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

sqlglot: Any | None = None
exp: Any | None = None
ParseError: type[BaseException] = Exception

try:  # pragma: no cover - optional dependency
    sqlglot = import_module("sqlglot")
    exp = getattr(sqlglot, "exp", None)
    sqlglot_errors = import_module("sqlglot.errors")
    resolved_parse_error = getattr(sqlglot_errors, "ParseError", None)
    if isinstance(resolved_parse_error, type) and issubclass(
        resolved_parse_error,
        BaseException,
    ):
        ParseError = resolved_parse_error
except ImportError:  # pragma: no cover
    sqlglot = None
    exp = None

_FORBIDDEN_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "replace",
    "grant",
    "revoke",
    "commit",
    "rollback",
    "call",
    "execute",
    "handler",
    "load_file",
    "outfile",
    "dumpfile",
    "benchmark",
    "sleep",
}
_COMMENT_PATTERN = re.compile(r"(--|/\*|\*/|#)")
_TABLE_PATTERN = re.compile(
    r"\b(?:from|join)\s+[`\"]?([a-zA-Z_][a-zA-Z0-9_]*)[`\"]?",
    re.IGNORECASE,
)


@dataclass(slots=True)
class SqlValidationResult:
    is_valid: bool
    normalized_sql: str = ""
    errors: list[str] = field(default_factory=list)


class SqlGuard:
    """Validate that model-generated SQL is read-only and table-scoped."""

    def __init__(self, allowed_table: str | None = None) -> None:
        self._allowed_table = (allowed_table or settings.db_table).lower()

    def validate(self, sql_query: str) -> SqlValidationResult:
        sql = str(sql_query or "").strip()
        if not sql:
            return SqlValidationResult(is_valid=False, errors=["Empty SQL query."])

        sql = self._normalize(sql)
        keyword_errors = self._validate_keywords(sql)
        if keyword_errors:
            return SqlValidationResult(is_valid=False, normalized_sql=sql, errors=keyword_errors)

        if sqlglot is not None:
            return self._validate_with_sqlglot(sql)
        return self._validate_with_regex(sql)

    @staticmethod
    def _normalize(sql_query: str) -> str:
        sql = sql_query.replace("```sql", "").replace("```", "").strip()
        if not sql.endswith(";"):
            sql = f"{sql};"
        return " ".join(sql.split())

    def _validate_keywords(self, sql_query: str) -> list[str]:
        lowered = sql_query.lower()
        errors: list[str] = []
        if _COMMENT_PATTERN.search(lowered):
            errors.append("SQL comments are not allowed.")
        if lowered.count(";") > 1:
            errors.append("Multiple SQL statements are not allowed.")
        for keyword in _FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                errors.append(f"Forbidden SQL keyword detected: {keyword}.")
        if not re.match(r"^(select|with)\b", lowered):
            errors.append("Only SELECT queries are allowed.")
        return errors

    def _validate_with_sqlglot(self, sql_query: str) -> SqlValidationResult:
        try:
            statements = [stmt for stmt in sqlglot.parse(sql_query, read="mysql") if stmt is not None]
        except ParseError as exc:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=[f"Failed to parse SQL: {exc}"],
            )
        if len(statements) != 1:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=["Multiple SQL statements are not allowed."],
            )

        statement = statements[0]
        if exp is None:
            return self._validate_with_regex(sql_query)

        forbidden_nodes = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create)
        for forbidden_node in forbidden_nodes:
            if list(statement.find_all(forbidden_node)):
                return SqlValidationResult(
                    is_valid=False,
                    normalized_sql=sql_query,
                    errors=["Write operations are not allowed."],
                )

        tables = {
            table.name.lower()
            for table in statement.find_all(exp.Table)
            if getattr(table, "name", None)
        }
        if not tables:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=["No table reference detected."],
            )
        if tables != {self._allowed_table}:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=[
                    f"Query can reference only `{self._allowed_table}`. Found: {', '.join(sorted(tables))}.",
                ],
            )

        return SqlValidationResult(is_valid=True, normalized_sql=sql_query)

    def _validate_with_regex(self, sql_query: str) -> SqlValidationResult:
        tables = {match.group(1).lower() for match in _TABLE_PATTERN.finditer(sql_query)}
        if not tables:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=["No table reference detected."],
            )
        if tables != {self._allowed_table}:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=[
                    f"Query can reference only `{self._allowed_table}`. Found: {', '.join(sorted(tables))}.",
                ],
            )
        return SqlValidationResult(is_valid=True, normalized_sql=sql_query)


_guard = SqlGuard()


def validate_sql(sql_query: str) -> bool:
    """Backward-compatible validation helper."""
    result = _guard.validate(sql_query)
    if not result.is_valid:
        logger.warning("Rejected SQL candidate: %s", "; ".join(result.errors))
    return result.is_valid
