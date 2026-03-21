#app/services/rag_retriever.py
"""Dynamic RAG schema-context retrieval from live MySQL metadata."""

from __future__ import annotations

from mysql.connector import Error

from app.core.logging import get_logger
from app.core.settings import settings
from app.infrastructure.mysql_pool import create_db_connection

logger = get_logger(__name__)

_TEXT_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext"}


def _build_schema_fallback() -> str:
    """Return a clean minimal fallback context."""
    return "\n".join(
        [
            f"Active Database: {settings.db_name}",
            f"Target Table: {settings.db_table}",
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
        (settings.db_name, settings.db_table),
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
            columns.append((str(col_name), str(data_type).lower()))

    return columns


def _fetch_sample_values(cursor, columns: list[tuple[str, str]]) -> list[str]:
    """Fetch a few representative values from text-like columns."""
    text_columns = [name for name, data_type in columns if data_type in _TEXT_TYPES]
    if not text_columns:
        return []

    chosen_columns = text_columns[:3]
    select_parts = []
    for column in chosen_columns:
        select_parts.append(
            f"NULLIF(TRIM(CAST(`{column}` AS CHAR)), '') AS `{column}`"
        )

    query = f"""
        SELECT {", ".join(select_parts)}
        FROM `{settings.db_table}`
        LIMIT 5
    """
    cursor.execute(query)
    rows = cursor.fetchall()

    samples: list[str] = []
    for row in rows:
        values = []
        if isinstance(row, dict):
            for column in chosen_columns:
                value = row.get(column)
                if value:
                    values.append(f"{column}={value}")
        elif isinstance(row, (tuple, list)):
            for column, value in zip(chosen_columns, row):
                if value:
                    values.append(f"{column}={value}")

        if values:
            samples.append(", ".join(values))

    return samples


def retrieve_dynamic_rag_context() -> str:
    """Build schema-aware database context dynamically from live metadata."""
    connection = None
    cursor = None

    try:
        connection = create_db_connection()
        if connection is None:
            logger.warning("Could not create DB connection for RAG context.")
            return _build_schema_fallback()

        cursor = connection.cursor(dictionary=True)

        columns = _fetch_columns(cursor)
        if not columns:
            logger.warning("No schema columns found for configured table.")
            return _build_schema_fallback()

        schema_lines = [
            f"- {column_name} ({data_type})"
            for column_name, data_type in columns
        ]

        sample_values = _fetch_sample_values(cursor, columns)

        sections = [
            f"Active Database: {settings.db_name}",
            f"Target Table: {settings.db_table}",
            "Schema:",
            *schema_lines,
        ]

        if sample_values:
            sections.extend(
                [
                    "",
                    "Sample Values:",
                    *[f"- {sample}" for sample in sample_values],
                ]
            )

        context = "\n".join(sections)
        logger.info(
            "Dynamic RAG context built successfully with %d columns.", len(columns)
        )
        return context

    except Error as exc:
        logger.warning("Failed to retrieve dynamic RAG context: %s", exc)
        return _build_schema_fallback()
    except Exception as exc:
        logger.exception("Unexpected error while building dynamic RAG context: %s", exc)
        return _build_schema_fallback()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()