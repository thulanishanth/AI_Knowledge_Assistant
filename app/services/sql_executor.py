# AI_Knowledge_Assistant/app/services/sql_executor.py
"""Safe SQL execution utilities for validated read-only queries via Connection Pool."""

from __future__ import annotations

from mysql.connector import Error

from app.config import MAX_QUERY_RESULTS
from app.db.mysql import create_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_EXECUTION_ROW_LIMIT = MAX_QUERY_RESULTS or 100


def execute_safe_query(sql_query: str) -> list[dict] | str:
    """
    Execute a validated SQL query safely using a pooled connection and return results.

    Returns:
        list[dict]: successful rows
        str: error message
    """
    connection = None
    cursor = None
    results: list[dict] = []

    try:
        logger.info("Executing validated SQL query")

        connection = create_db_connection()
        if not connection:
            logger.warning("Connection pool exhausted")
            return "Error executing SQL query: Connection pool exhausted."

        if hasattr(connection, "is_connected") and not connection.is_connected():
            logger.error("Acquired connection is not connected")
            return "Acquired connection is not connected"

        if not hasattr(connection, "cursor"):
            logger.error("Acquired connection does not support cursor")
            return "Acquired connection is not connected"

        cursor = connection.cursor(dictionary=True)

        # Apply per-session timeout
        cursor.execute("SET SESSION MAX_EXECUTION_TIME=5000")

        # Run the validated read-only SQL
        cursor.execute(sql_query)

        # Read one extra row so we can detect truncation
        if hasattr(cursor, "fetchmany"):
            fetched = cursor.fetchmany(_EXECUTION_ROW_LIMIT + 1)
        elif hasattr(cursor, "fetchall"):
            fetched = cursor.fetchall()
        else:
            raise RuntimeError("Cursor does not support fetchmany or fetchall")

        if len(fetched) > _EXECUTION_ROW_LIMIT:
            logger.warning(
                "Query results truncated. Exceeded maximum limit of %s rows.",
                _EXECUTION_ROW_LIMIT,
            )
            results = fetched[:_EXECUTION_ROW_LIMIT]

            # CRITICAL: drain the unread remainder so cleanup does not crash
            try:
                if hasattr(cursor, "fetchall"):
                    cursor.fetchall()
            except Exception as drain_exc:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to fully drain unread rows: %s", drain_exc)
        else:
            results = fetched

        logger.info("SQL query executed successfully with %s rows", len(results))
        return results

    except Error as exc:
        if getattr(exc, "errno", None) == 3024:
            logger.error("Query timeout")
            return "Error: Query exceeded maximum execution time."

        logger.exception("MySQL error")
        return f"Error executing SQL query: {str(exc)}"

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected system error")
        return f"System error during execution: {str(exc)}"

    finally:
        # Close cursor first
        if cursor is not None:
            try:
                cursor.close()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to close cursor cleanly: %s", exc)

        # Then return pooled connection without calling is_connected()
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to return pooled connection cleanly: %s", exc)