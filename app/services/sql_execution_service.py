#app/services/sql_execution_service.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from mysql.connector import Error

from app.core.settings import settings
from app.infrastructure.mysql_pool import close_connection, create_db_connection
from app.core.logging import get_logger

logger = get_logger(__name__)

@dataclass(slots=True)
class QueryExecutionResult:
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    execution_ms: float
    executed_sql: str

class SQLExecutionService:
    """Execute one validated SQL query with timeout, row limits, and smart counts."""

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
                raise RuntimeError("Acquired connection is not connected")

            cursor = connection.cursor(dictionary=True)
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={settings.db_query_timeout_ms}")
            cursor.execute(bounded_sql)

            fetch_limit = settings.max_query_results + 1
            if hasattr(cursor, "fetchmany"):
                rows = cursor.fetchmany(fetch_limit)
            else:
                rows = cursor.fetchall()
                
            truncated = len(rows) > settings.max_query_results
            
            # --- NEW SMART LOGIC: If returning a massive raw list, show the total count instead ---
            # We skip this for queries that are ALREADY aggregated (like AVG price by month)
            is_aggregated = bool(
                re.search(r"\bgroup by\b|\b(count|sum|avg|max|min)\s*\(", sql_query, re.IGNORECASE)
            )
            
            if truncated and not is_aggregated:
                # Strip any limits and run a COUNT query
                clean_sql = re.sub(r"\blimit\s+\d+\b", "", sql_query, flags=re.IGNORECASE).strip().rstrip(";")
                
                # FORMAT() adds the beautiful commas (e.g. "11,885")
                count_sql = f"SELECT FORMAT(COUNT(*), 0) AS `Total Matching Bookings` FROM ({clean_sql}) AS subq;"
                
                cursor.execute(count_sql)
                count_row = cursor.fetchone()
                
                return QueryExecutionResult(
                    rows=[count_row] if count_row else [{"Total Matching Bookings": "0"}],
                    row_count=1,
                    truncated=False,
                    execution_ms=(time.perf_counter() - started) * 1000,
                    executed_sql=count_sql,
                )
            # --------------------------------------------------------------------------------------

            limited_rows = rows[: settings.max_query_results]
            if truncated:
                try:
                    cursor.fetchall()
                except Exception:
                    pass

            return QueryExecutionResult(
                rows=limited_rows,
                row_count=len(limited_rows),
                truncated=truncated,
                execution_ms=(time.perf_counter() - started) * 1000,
                executed_sql=bounded_sql,
            )
        except Error as exc:
            if getattr(exc, "errno", None) == 3024:
                raise RuntimeError("The database query timed out.") from exc
            raise RuntimeError(f"Database error while executing query: {exc}") from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            close_connection(connection)

    @staticmethod
    def _ensure_limit(sql_query: str) -> str:
        sql = (sql_query or "").strip().rstrip(";")
        if re.search(r"\blimit\b", sql, re.IGNORECASE):
            return f"{sql};"
        return f"{sql} LIMIT {settings.max_query_results + 1};"

_execution_service = SQLExecutionService()

def execute_safe_query(sql_query: str) -> list[dict[str, Any]] | str:
    """Backward-compatible execution helper."""
    try:
        return _execution_service.execute(sql_query).rows
    except Exception as exc: 
        return str(exc)