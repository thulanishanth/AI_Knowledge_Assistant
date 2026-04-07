#app/services/rag_retriever.py
"""Dynamic RAG schema/context retrieval from live MySQL metadata and local business context."""

from __future__ import annotations

import json
from pathlib import Path

from mysql.connector import Error

from app.core.logging import get_logger
from app.core.settings import settings
from app.infrastructure.mysql_pool import close_connection, create_db_connection

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CONTEXT_FILES = (
    _DATA_DIR / "business_context.json",
    _DATA_DIR / "business_metadata.json",
)

_TEXT_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum"}


def _load_local_business_context() -> dict:
    for path in _CONTEXT_FILES:
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read business context from %s: %s", path, exc)
    return {}


def _fetch_live_schema_block() -> str:
    connection = None
    cursor = None

    try:
        connection = create_db_connection()
        if connection is None:
            return "Live schema unavailable."

        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                CASE WHEN k.column_name IS NULL THEN 0 ELSE 1 END AS is_primary_key
            FROM information_schema.columns AS c
            LEFT JOIN information_schema.key_column_usage AS k
                ON c.table_schema = k.table_schema
                AND c.table_name = k.table_name
                AND c.column_name = k.column_name
                AND k.constraint_name = 'PRIMARY'
            WHERE c.table_schema = %s AND c.table_name = %s
            ORDER BY c.ordinal_position
            """,
            (settings.db_name, settings.db_table),
        )
        rows = cursor.fetchall()

        if not rows:
            return f"Database: {settings.db_name}\nTable: {settings.db_table}\nColumns:\n- No live schema rows found."

        lines = [
            f"Database: {settings.db_name}",
            f"Table: {settings.db_table}",
            "Columns:",
        ]

        for row in rows:
            column_name = str(row.get("column_name", "")).strip()
            data_type = str(row.get("data_type", "")).strip().lower()
            nullable = str(row.get("is_nullable", "YES")).strip().upper() == "YES"
            is_primary = bool(row.get("is_primary_key", 0))

            if not column_name or not data_type:
                logger.warning("Skipping malformed schema row in RAG: %r", row)
                continue

            suffix = []
            suffix.append("nullable" if nullable else "required")
            if is_primary:
                suffix.append("primary-key")

            lines.append(f"- {column_name} ({data_type}, {', '.join(suffix)})")

            if data_type in _TEXT_TYPES:
                sample_values = _fetch_sample_values(connection, column_name)
                if sample_values:
                    joined = ", ".join(sample_values)
                    lines.append(f"  Sample values: {joined}")

        return "\n".join(lines)

    except Error as exc:
        logger.error("Failed to build live schema RAG block: %s", exc)
        return "Live schema unavailable due to database error."
    except Exception as exc:
        logger.error("Unexpected schema RAG failure: %s", exc)
        return "Live schema unavailable due to unexpected error."
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                logger.debug("Cursor close failed", exc_info=True)
        close_connection(connection)


def _fetch_sample_values(connection, column_name: str, limit: int = 4) -> list[str]:
    cursor = None
    try:
        cursor = connection.cursor()
        query = (
            f"SELECT DISTINCT `{column_name}` "
            f"FROM `{settings.db_table}` "
            f"WHERE `{column_name}` IS NOT NULL "
            f"LIMIT {int(limit)}"
        )
        cursor.execute(query)
        rows = cursor.fetchall()
        samples: list[str] = []
        for row in rows:
            if not row:
                continue
            value = row[0]
            text = str(value).strip()
            if text:
                samples.append(repr(text))
        return samples
    except Exception:
        return []
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                logger.debug("Cursor close failed while reading sample values", exc_info=True)


def _build_local_context_block(context: dict) -> str:
    if not context:
        return ""

    lines: list[str] = []

    dataset_name = str(context.get("dataset_name", "")).strip()
    if dataset_name:
        lines.append(f"Dataset Name: {dataset_name}")

    semantic_layer = [
        str(item).strip()
        for item in context.get("semantic_layer", [])
        if str(item).strip() and "TODO:" not in str(item)
    ]
    constraints = [
        str(item).strip()
        for item in context.get("constraints", [])
        if str(item).strip() and "TODO:" not in str(item)
    ]
    examples = [
        str(item).strip()
        for item in context.get("examples", [])
        if str(item).strip()
    ]

    if semantic_layer:
        lines.append("Semantic Layer:")
        lines.extend(f"- {item}" for item in semantic_layer[:20])

    if constraints:
        lines.append("Business Constraints:")
        lines.extend(f"- {item}" for item in constraints[:20])

    if examples:
        lines.append("Examples:")
        lines.extend(f"- {item}" for item in examples[:6])

    return "\n".join(lines).strip()


def retrieve_dynamic_rag_context() -> str:
    """Build one grounding block for prompts using live schema + local business context."""
    live_schema = _fetch_live_schema_block()
    local_context = _build_local_context_block(_load_local_business_context())

    blocks = [block for block in [live_schema, local_context] if block and block.strip()]
    return "\n\n".join(blocks).strip()