# app/security/sql_guard.py
"""Strict read-only query validation for dynamic dialects and schemas."""

from __future__ import annotations

from importlib import import_module
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

sqlglot: Any | None = None
exp: Any | None = None
ParseError: type[BaseException] = Exception

try:
    sqlglot = import_module("sqlglot")
    exp = getattr(sqlglot, "exp", None)
    sqlglot_errors = import_module("sqlglot.errors")
    resolved_parse_error = getattr(sqlglot_errors, "ParseError", None)
    if isinstance(resolved_parse_error, type) and issubclass(resolved_parse_error, BaseException):
        ParseError = resolved_parse_error
except ImportError:
    sqlglot = None
    exp = None

# SQL specific blocks
_FORBIDDEN_SQL_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create",
    "truncate", "replace", "grant", "revoke", "commit",
    "rollback", "call", "execute", "handler", "load_file",
    "outfile", "dumpfile", "benchmark", "sleep",
}

# NoSQL / MongoDB specific blocks
_FORBIDDEN_NOSQL_KEYWORDS = {
    "insert", "insertone", "insertmany", "update", "updateone",
    "updatemany", "delete", "deleteone", "deletemany", "remove",
    "drop", "dropdatabase", "replaceone",
}

# Matches single-line (--) and multi-line (/* */) SQL comments
_SINGLE_LINE_COMMENT = re.compile(r"--[^\n]*")
_MULTI_LINE_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Block dangerous inline comment tokens that can't appear in valid SQL values
_DANGEROUS_COMMENT = re.compile(r"(#)")

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
    """Validate that model-generated queries are read-only and safely scoped."""

    def validate(
        self,
        query: str,
        dialect: str = "mysql",
        allowed_tables: set[str] | None = None,
    ) -> SqlValidationResult:
        raw_query = str(query or "").strip()
        if not raw_query:
            return SqlValidationResult(is_valid=False, errors=["Empty query."])

        # Strip markdown fences and normalize
        normalized = self._normalize(raw_query, dialect)
        safe_dialect = dialect.lower()

        # ── NoSQL routing ──
        if "mongo" in safe_dialect or "nosql" in safe_dialect:
            return self._validate_nosql(normalized)

        # ── Strip SQL comments before validation ──
        # The LLM is instructed to output chain-of-thought as SQL comments.
        # We remove them cleanly rather than rejecting the whole query.
        stripped = self._strip_comments(normalized)

        if not stripped.strip():
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=normalized,
                errors=["Query is empty after stripping comments."],
            )

        # Check for dangerous inline comment tokens (#) that remain
        if _DANGEROUS_COMMENT.search(stripped):
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=stripped,
                errors=["Dangerous comment token (#) detected in query."],
            )

        # Multiple statement check
        if stripped.count(";") > 1:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=stripped,
                errors=["Multiple SQL statements are not allowed."],
            )

        # Forbidden keyword check
        keyword_errors = self._validate_sql_keywords(stripped)
        if keyword_errors:
            return SqlValidationResult(
                is_valid=False, normalized_sql=stripped, errors=keyword_errors
            )

        if sqlglot is not None:
            return self._validate_with_sqlglot(stripped, safe_dialect, allowed_tables)

        return self._validate_with_regex(stripped, allowed_tables)

    @staticmethod
    def _strip_comments(sql: str) -> str:
        """Remove SQL single-line and multi-line comments, then re-normalize whitespace."""
        result = _MULTI_LINE_COMMENT.sub(" ", sql)
        result = _SINGLE_LINE_COMMENT.sub(" ", result)
        # Re-collapse whitespace and ensure trailing semicolon
        result = " ".join(result.split()).strip()
        if result and not result.endswith(";"):
            result = result + ";"
        return result

    @staticmethod
    def _normalize(query: str, dialect: str) -> str:
        """Strip markdown fences and normalize spacing."""
        q = (
            query.replace("```sql", "")
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )
        # Don't add semicolon for NoSQL — will be handled separately
        if "mongo" not in dialect.lower() and not q.endswith(";"):
            q = f"{q};"
        return " ".join(q.split())

    def _validate_sql_keywords(self, sql_query: str) -> list[str]:
        """Check for forbidden DML/DDL keywords."""
        lowered = sql_query.lower()
        errors: list[str] = []

        for keyword in _FORBIDDEN_SQL_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                errors.append(f"Forbidden SQL keyword detected: {keyword}.")

        if not re.match(r"^(select|with)\b", lowered):
            errors.append("Only SELECT or WITH queries are allowed.")

        return errors

    def _validate_nosql(self, query: str) -> SqlValidationResult:
        """Basic string-based guardrails for NoSQL/MongoDB payloads."""
        lowered = query.lower()
        errors: list[str] = []

        for keyword in _FORBIDDEN_NOSQL_KEYWORDS:
            if keyword in lowered:
                errors.append(f"Forbidden NoSQL operation detected: {keyword}.")

        if errors:
            return SqlValidationResult(is_valid=False, normalized_sql=query, errors=errors)

        return SqlValidationResult(is_valid=True, normalized_sql=query)

    def _validate_with_sqlglot(
        self,
        sql_query: str,
        dialect: str,
        allowed_tables: set[str] | None,
    ) -> SqlValidationResult:
        """AST-based deep validation using sqlglot."""
        sqlglot_dialect = dialect
        if dialect == "postgresql":
            sqlglot_dialect = "postgres"

        try:
            statements = [
                stmt
                for stmt in sqlglot.parse(sql_query, read=sqlglot_dialect)
                if stmt is not None
            ]
        except ParseError as exc:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=[f"Failed to parse {dialect.upper()} SQL: {exc}"],
            )

        if len(statements) != 1:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=["Multiple SQL statements are not allowed."],
            )

        statement = statements[0]
        if exp is None:
            return self._validate_with_regex(sql_query, allowed_tables)

        forbidden_nodes = (
            exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create
        )
        for forbidden_node in forbidden_nodes:
            if list(statement.find_all(forbidden_node)):
                return SqlValidationResult(
                    is_valid=False,
                    normalized_sql=sql_query,
                    errors=["Write operations are not allowed."],
                )

        if allowed_tables:
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

            invalid_tables = tables - {t.lower() for t in allowed_tables}
            if invalid_tables:
                return SqlValidationResult(
                    is_valid=False,
                    normalized_sql=sql_query,
                    errors=[
                        f"Query references unauthorized tables: {', '.join(sorted(invalid_tables))}."
                    ],
                )

        return SqlValidationResult(is_valid=True, normalized_sql=sql_query)

    def _validate_with_regex(
        self, sql_query: str, allowed_tables: set[str] | None
    ) -> SqlValidationResult:
        """Regex fallback if sqlglot AST fails or is missing."""
        if not allowed_tables:
            return SqlValidationResult(is_valid=True, normalized_sql=sql_query)

        tables = {
            match.group(1).lower() for match in _TABLE_PATTERN.finditer(sql_query)
        }
        if not tables:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=["No table reference detected."],
            )

        invalid_tables = tables - {t.lower() for t in allowed_tables}
        if invalid_tables:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=[
                    f"Query references unauthorized tables: {', '.join(sorted(invalid_tables))}."
                ],
            )

        return SqlValidationResult(is_valid=True, normalized_sql=sql_query)


_guard = SqlGuard()


def validate_sql(
    sql_query: str,
    dialect: str = "mysql",
    allowed_tables: set[str] | None = None,
) -> bool:
    """Backward-compatible validation helper."""
    result = _guard.validate(sql_query, dialect, allowed_tables)
    if not result.is_valid:
        logger.warning("Rejected SQL candidate: %s", "; ".join(result.errors))
    return result.is_valid