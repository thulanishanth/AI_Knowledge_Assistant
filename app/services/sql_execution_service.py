#app/services/sql_execution_service.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from mysql.connector import Error

from app.core.logging import get_logger
from app.core.settings import settings
from app.infrastructure.mysql_pool import close_connection, create_db_connection

logger = get_logger(__name__)


@dataclass(slots=True)
class QueryExecutionResult:
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    execution_ms: float
    executed_sql: str
    selected_columns: list[str] = field(default_factory=list)
    error_message: str | None = None


class SQLExecutionService:
    """Execute one validated SQL query with timeout and bounded preview size."""

    def execute(self, sql_query: str) -> QueryExecutionResult:
        connection = None
        cursor = None
        started = time.perf_counter()
        bounded_sql = self._ensure_limit(sql_query)

        try:
            connection = create_db_connection()
            if connection is None:
                raise RuntimeError("Database connection is unavailable.")

            if hasattr(connection, "is_connected") and not connection.is_connected():
                raise RuntimeError("Acquired connection is not connected.")

            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                f"SET SESSION MAX_EXECUTION_TIME={int(settings.db_query_timeout_ms)}"
            )
            cursor.execute(bounded_sql)

            fetch_limit = settings.max_query_results + 1
            rows = cursor.fetchmany(fetch_limit)

            truncated = len(rows) > settings.max_query_results
            if truncated:
                rows = rows[: settings.max_query_results]

            selected_columns: list[str] = []
            if getattr(cursor, "column_names", None):
                selected_columns = [str(name) for name in cursor.column_names if name]

            if not selected_columns and rows:
                selected_columns = list(rows[0].keys())

            return QueryExecutionResult(
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                execution_ms=(time.perf_counter() - started) * 1000,
                executed_sql=bounded_sql,
                selected_columns=selected_columns,
            )

        except Error as exc:
            logger.error("SQL execution failed: %s", exc)
            return QueryExecutionResult(
                rows=[],
                row_count=0,
                truncated=False,
                execution_ms=(time.perf_counter() - started) * 1000,
                executed_sql=bounded_sql,
                selected_columns=[],
                error_message=self._sanitize_error(str(exc)),
            )
        except Exception as exc:
            logger.error("Unexpected SQL execution failure: %s", exc)
            return QueryExecutionResult(
                rows=[],
                row_count=0,
                truncated=False,
                execution_ms=(time.perf_counter() - started) * 1000,
                executed_sql=bounded_sql,
                selected_columns=[],
                error_message=self._sanitize_error(str(exc)),
            )
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    logger.debug("Cursor close failed", exc_info=True)
            close_connection(connection)

    @staticmethod
    def _sanitize_error(message: str) -> str:
        clean = " ".join(str(message or "").split()).strip()
        return clean[:500] if clean else "Unknown database execution error."

    @staticmethod
    def _has_limit(sql_query: str) -> bool:
        return bool(re.search(r"\blimit\s+\d+\b", sql_query, re.IGNORECASE))

    def _ensure_limit(self, sql_query: str) -> str:
        sql = str(sql_query or "").strip().rstrip(";")
        if not sql:
            return "SELECT 1 LIMIT 1;"

        if self._has_limit(sql):
            return f"{sql};"

        return f"{sql} LIMIT {settings.max_query_results};"