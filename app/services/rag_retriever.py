# AI_Knowledge_Assistant/app/services/rag_retriever.py
"""RAG schema-context retrieval from live MySQL metadata via Connection Pool."""

from mysql.connector import Error

from app.config import DB_TABLE, DB_NAME
from app.db.mysql import close_connection, create_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _fetch_live_schema_context() -> str:  # pylint: disable=too-many-branches
    """
    Build schema context from the active DB configured in .env.
    Uses the MySQL Connection Pool to prevent TCP exhaustion.
    """
    connection = None
    cursor = None
    try:
        # 1. Fetch from the pre-warmed pool instantly
        connection = create_db_connection()
        # 2. Safety Check: Ensure the pool didn't return None
        if not connection:
            logger.warning("RAG Retriever could not acquire a DB connection from the pool.")
            return (
                f"Active Database: {DB_NAME}\n"
                f"Target Table: {DB_TABLE}\n"
                "Schema unavailable: Connection pool exhausted."
            )

        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                table_name,
                column_name,
                data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (DB_NAME, DB_TABLE),
        )
        rows = cursor.fetchall()

        if not rows:
            return (
                f"Active Database: {DB_NAME}\n"
                f"Target Table: {DB_TABLE}\n"
                "Table not found or has no columns."
            )

        columns = []
        for row in rows:
            # row shape: (table_name, column_name, data_type)
            if isinstance(row, (tuple, list)) and len(row) >= 3:
                column_name = row[1]
                data_type = row[2]
            elif isinstance(row, dict):
                column_name = row.get("column_name") or row.get("COLUMN_NAME")
                data_type = row.get("data_type") or row.get("DATA_TYPE")
            else:
                continue

            if column_name and data_type:
                columns.append(f"{column_name} ({data_type})")

        if not columns:
            return (
                f"Active Database: {DB_NAME}\n"
                f"Target Table: {DB_TABLE}\n"
                "Schema unavailable: unable to read table columns."
            )

        lines = [
            f"Active Database: {DB_NAME}",
            f"Target Table: {DB_TABLE}",
            f"- {DB_TABLE}: {', '.join(columns)}",
        ]
        return "\n".join(lines)
    except Error as e:
        error_message = str(e).strip() or e.__class__.__name__
        logger.exception("Failed to fetch live schema context from MySQL")
        return f"Active Database: {DB_NAME}\nSchema unavailable: {error_message}"
    except (RuntimeError, TypeError, ValueError, KeyError, AttributeError) as e:
        error_message = str(e).strip() or e.__class__.__name__
        logger.exception("Unexpected error while fetching schema context")
        return f"Active Database: {DB_NAME}\nSchema unavailable: {error_message}"
    finally:
        # 3. Clean up cursor
        if cursor:
            cursor.close()
        # 4. Hand the connection back to the pool
        if connection and connection.is_connected():
            close_connection(connection)


def retrieve_context(_user_question: str) -> str:
    """
    Retrieve context for a user question from live database schema.
    """
    return _fetch_live_schema_context()
