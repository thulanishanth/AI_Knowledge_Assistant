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

# SQL specific blocks
_FORBIDDEN_SQL_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", 
    "truncate", "replace", "grant", "revoke", "commit", 
    "rollback", "call", "execute", "handler", "load_file", 
    "outfile", "dumpfile", "benchmark", "sleep"
}

# NoSQL / MongoDB specific blocks
_FORBIDDEN_NOSQL_KEYWORDS = {
    "insert", "insertone", "insertmany", "update", "updateone", 
    "updatemany", "delete", "deleteone", "deletemany", "remove", 
    "drop", "dropdatabase", "replaceone"
}

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

    def validate(self, query: str, dialect: str = "mysql", allowed_tables: set[str] | None = None) -> SqlValidationResult:
        """
        Universally validate a query.
        - dialect: e.g., 'mysql', 'duckdb', 'postgresql', 'mongodb_json'
        - allowed_tables: A set of valid table names from the dynamic schema.
        """
        raw_query = str(query or "").strip()
        if not raw_query:
            return SqlValidationResult(is_valid=False, errors=["Empty query."])

        normalized = self._normalize(raw_query, dialect)
        safe_dialect = dialect.lower()

        # ==========================================
        # 1. NoSQL / MongoDB Validation Routing
        # ==========================================
        if "mongo" in safe_dialect or "nosql" in safe_dialect:
            return self._validate_nosql(normalized)

        # ==========================================
        # 2. Relational / SQL Validation Routing
        # ==========================================
        keyword_errors = self._validate_sql_keywords(normalized)
        if keyword_errors:
            return SqlValidationResult(is_valid=False, normalized_sql=normalized, errors=keyword_errors)

        if sqlglot is not None:
            return self._validate_with_sqlglot(normalized, safe_dialect, allowed_tables)
            
        return self._validate_with_regex(normalized, allowed_tables)

    @staticmethod
    def _normalize(query: str, dialect: str) -> str:
        """Strip markdown, comments, and normalize spacing."""
        q = query.replace("```sql", "").replace("```json", "").replace("```", "").strip()
        
        # Cleanly strip any comments that bypassed extraction
        q = re.sub(r"--.*?(\n|$)", "\n", q)
        q = re.sub(r"/\*.*?\*/", "", q, flags=re.DOTALL)
        q = re.sub(r"#.*?(\n|$)", "\n", q)
        
        q = q.strip()
        if "mongo" not in dialect.lower() and not q.endswith(";"):
            q = f"{q};"
        return " ".join(q.split())

    def _validate_sql_keywords(self, sql_query: str) -> list[str]:
        """Basic regex guardrails for standard SQL."""
        lowered = sql_query.lower()
        errors: list[str] = []
        
        if lowered.count(";") > 1:
            errors.append("Multiple SQL statements are not allowed.")
            
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

    def _validate_with_sqlglot(self, sql_query: str, dialect: str, allowed_tables: set[str] | None) -> SqlValidationResult:
        """AST-based deep validation using sqlglot."""
        # Map generic dialects to sqlglot supported dialects
        sqlglot_dialect = dialect
        if dialect == "postgresql": sqlglot_dialect = "postgres"
        
        try:
            statements = [stmt for stmt in sqlglot.parse(sql_query, read=sqlglot_dialect) if stmt is not None]
        except ParseError as exc:
            return SqlValidationResult(
                is_valid=False, normalized_sql=sql_query, errors=[f"Failed to parse {dialect.upper()} SQL: {exc}"]
            )
            
        if len(statements) != 1:
            return SqlValidationResult(
                is_valid=False, normalized_sql=sql_query, errors=["Multiple SQL statements are not allowed."]
            )

        statement = statements[0]
        if exp is None:
            return self._validate_with_regex(sql_query, allowed_tables)

        forbidden_nodes = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create)
        for forbidden_node in forbidden_nodes:
            if list(statement.find_all(forbidden_node)):
                return SqlValidationResult(
                    is_valid=False, normalized_sql=sql_query, errors=["Write operations are not allowed."]
                )

        # Skip strict table validation if we don't have the dynamic schema yet
        if allowed_tables:
            tables = {
                table.name.lower()
                for table in statement.find_all(exp.Table)
                if getattr(table, "name", None)
            }
            if not tables:
                return SqlValidationResult(
                    is_valid=False, normalized_sql=sql_query, errors=["No table reference detected."]
                )
                
            invalid_tables = tables - {t.lower() for t in allowed_tables}
            if invalid_tables:
                return SqlValidationResult(
                    is_valid=False,
                    normalized_sql=sql_query,
                    errors=[f"Query references unauthorized tables: {', '.join(sorted(invalid_tables))}."],
                )

        return SqlValidationResult(is_valid=True, normalized_sql=sql_query)

    def _validate_with_regex(self, sql_query: str, allowed_tables: set[str] | None) -> SqlValidationResult:
        """Regex fallback if sqlglot AST fails or is missing."""
        if not allowed_tables:
            return SqlValidationResult(is_valid=True, normalized_sql=sql_query)
            
        tables = {match.group(1).lower() for match in _TABLE_PATTERN.finditer(sql_query)}
        if not tables:
            return SqlValidationResult(
                is_valid=False, normalized_sql=sql_query, errors=["No table reference detected."]
            )
            
        invalid_tables = tables - {t.lower() for t in allowed_tables}
        if invalid_tables:
            return SqlValidationResult(
                is_valid=False,
                normalized_sql=sql_query,
                errors=[f"Query references unauthorized tables: {', '.join(sorted(invalid_tables))}."],
            )
            
        return SqlValidationResult(is_valid=True, normalized_sql=sql_query)

_guard = SqlGuard()

def validate_sql(sql_query: str, dialect: str = "mysql", allowed_tables: set[str] | None = None) -> bool:
    """Backward-compatible validation helper with new dynamic support."""
    result = _guard.validate(sql_query, dialect, allowed_tables)
    if not result.is_valid:
        logger.warning("Rejected SQL candidate: %s", "; ".join(result.errors))
    return result.is_valid