# AI_Knowledge_Assistant/app/services/sql_executor.py
"""Compatibility wrapper for SQL execution."""

from __future__ import annotations

from mysql.connector import Error

from app.core.settings import settings
from app.db.mysql import create_db_connection
from app.services.sql_execution_service import QueryExecutionResult, SQLExecutionService
from app.utils.logger import get_logger

logger = get_logger(__name__)
sql_execution_service = SQLExecutionService()


def execute_safe_query(sql_query: str) -> list[dict] | str:
    """Legacy helper kept for tests and older imports."""
    connection = None
    cursor = None
    try:
        connection = create_db_connection()
        if not connection:
            return "Error executing SQL query: Connection pool exhausted."
        if hasattr(connection, "is_connected") and not connection.is_connected():
            return "Acquired connection is not connected"
        if not hasattr(connection, "cursor"):
            return "Acquired connection is not connected"

        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={settings.db_query_timeout_ms}")
        cursor.execute(sql_query)

        if hasattr(cursor, "fetchmany"):
            rows = cursor.fetchmany(settings.max_query_results + 1)
        elif hasattr(cursor, "fetchall"):
            rows = cursor.fetchall()
        else:
            raise RuntimeError("Cursor does not support fetchmany or fetchall")

        if len(rows) > settings.max_query_results:
            return rows[: settings.max_query_results]
        return rows
    except Error as exc:
        return f"Error executing SQL query: {exc}"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Legacy SQL execution failed")
        return str(exc)
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:  # pragma: no cover
                logger.debug("Cursor close failed", exc_info=True)
        if connection is not None:
            try:
                connection.close()
            except Exception:  # pragma: no cover
                logger.debug("Connection close failed", exc_info=True)


__all__ = ["QueryExecutionResult", "SQLExecutionService", "execute_safe_query", "sql_execution_service"]
