# AI_Knowledge_Assistant/app/services/rag_retriever.py
"""Dynamic RAG schema-context retrieval from live MySQL metadata."""

from __future__ import annotations

from mysql.connector import Error

from app.config import DB_NAME, DB_TABLE
from app.db.mysql import create_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TEXT_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext"}


def _build_schema_fallback() -> str:
    """Return a clean minimal fallback context."""
    return "\n".join(
        [
            f"Active Database: {DB_NAME}",
            f"Target Table: {DB_TABLE}",
            "Schema Status: unavailable",
        ]
    )


def _fetch_columns(cursor) -> list[tuple[str, str]]:
    """Fetch ordered column names and data types for the configured table."""
    cursor.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (DB_NAME, DB_TABLE),
    )
    rows = cursor.fetchall()
    columns: list[tuple[str, str]] = []

    for row in rows:
        if isinstance(row, (tuple, list)) and len(row) >= 2:
            col_name, data_type = row[0], row[1]
        elif isinstance(row, dict):
            col_name = row.get("column_name") or row.get("COLUMN_NAME")
            data_type = row.get("data_type") or row.get("DATA_TYPE")
        else:
            continue

        if col_name and data_type:
            columns.append((str(col_name).strip(), str(data_type).strip().lower()))

    return columns


def _count_distinct_values(cursor, column_name: str) -> int | None:
    """Return COUNT(DISTINCT column) for one column."""
    try:
        query = f"SELECT COUNT(DISTINCT `{column_name}`) AS distinct_count FROM `{DB_TABLE}`"
        cursor.execute(query)
        row = cursor.fetchone()

        if isinstance(row, dict):
            return int(row.get("distinct_count", 0))
        if isinstance(row, (tuple, list)) and row:
            return int(row[0])
        return None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Failed distinct count for column %s: %s", column_name, exc)
        return None


def _fetch_distinct_values(
    cursor,
    column_name: str,
    max_values: int = 12,
) -> list[str]:
    """Fetch a small ordered list of distinct non-null values for one column."""
    try:
        query = f"""
            SELECT DISTINCT `{column_name}`
            FROM `{DB_TABLE}`
            WHERE `{column_name}` IS NOT NULL
              AND TRIM(`{column_name}`) <> ''
            ORDER BY `{column_name}`
            LIMIT {max_values}
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        values: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                value = row.get(column_name)
                if value is None and row:
                    value = next(iter(row.values()))
            elif isinstance(row, (tuple, list)) and row:
                value = row[0]
            else:
                value = None

            if value is not None:
                values.append(str(value).strip())

        return values
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Failed distinct value fetch for column %s: %s", column_name, exc)
        return []


def _fetch_live_schema_context() -> str:
    """
    Build prompt-ready schema context dynamically from MySQL.

    Includes:
    - column names
    - data types
    - distinct values for low-cardinality text columns
    """
    connection = None
    cursor = None

    try:
        connection = create_db_connection()
        if not connection:
            return _build_schema_fallback()

        cursor = connection.cursor(dictionary=True)

        columns = _fetch_columns(cursor)
        if not columns:
            return _build_schema_fallback()

        lines = [
            f"Active Database: {DB_NAME}",
            f"Target Table: {DB_TABLE}",
            "Available Columns:",
        ]

        for column_name, data_type in columns:
            lines.append(f"- {DB_TABLE}.{column_name} ({data_type})")

            # Only enrich low-cardinality text columns dynamically
            if data_type in _TEXT_TYPES:
                distinct_count = _count_distinct_values(cursor, column_name)

                # Treat small-cardinality text fields like enums
                if distinct_count is not None and 0 < distinct_count <= 20:
                    distinct_values = _fetch_distinct_values(cursor, column_name)
                    if distinct_values:
                        joined = ", ".join(distinct_values)
                        lines.append(f"  Allowed Values: {joined}")

        return "\n".join(lines)

    except Error:
        logger.exception("Failed to fetch live schema context from MySQL")
        return _build_schema_fallback()

    except Exception:
        logger.exception("Unexpected error while fetching schema context")
        return _build_schema_fallback()

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                logger.debug("Failed to close schema cursor cleanly", exc_info=True)

        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("Failed to close schema connection cleanly", exc_info=True)


def retrieve_context(_user_question: str) -> str:
    """Retrieve live schema context dynamically from MySQL."""
    return _fetch_live_schema_context()