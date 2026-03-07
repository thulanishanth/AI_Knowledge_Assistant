# AI_Knowledge_Assistant/app/services/sql_executor.py
"""Safe SQL execution utilities for validated read-only queries via Connection Pool."""

from mysql.connector import Error

from app.db.mysql import close_connection, create_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


def execute_safe_query(sql_query: str):
    """
    Execute a validated SQL query safely using a pooled connection and return results.

    Args:
        sql_query (str): A validated SQL SELECT query.

    Returns:
        list[dict] | str: Query results as a list of dictionaries, or an error string.
    """
    connection = None
    cursor = None
    try:
        logger.info("Executing validated SQL query")
        # 1. Grab connection instantly from the pre-warmed pool
        connection = create_db_connection()

        # 2. Safety Circuit Breaker: Handle pool exhaustion gracefully
        if not connection:
            logger.warning("SQL Executor could not acquire a DB connection from the pool.")
            return "Error executing SQL query: Connection pool exhausted or database unreachable."

        if connection.is_connected():
            cursor = connection.cursor(dictionary=True)
            cursor.execute(sql_query)
            results = cursor.fetchall()
            logger.info("SQL query executed successfully with %s rows", len(results))
            return results

        return "Error executing SQL query: Acquired connection is not connected."

    except Error as e:
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no message."
        logger.exception("MySQL error while executing query")
        return f"Error executing SQL query: {error_message}"
    finally:
        # 3. GUARANTEED CLEANUP: This runs even if an error occurs above!
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            # Returns the connection to the pool, preventing silent freezes
            close_connection(connection)
