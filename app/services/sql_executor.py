from mysql.connector import Error

from app.db.mysql import close_connection, create_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


def execute_safe_query(sql_query: str):
    """
    Execute a validated SQL query safely and return results.

    Args:
        sql_query (str): A validated SQL SELECT query.

    Returns:
        list[dict]: Query results as a list of dictionaries.
    """
    try:
        logger.info("Executing validated SQL query")
        connection = create_db_connection()

        if connection.is_connected():
            cursor = connection.cursor(dictionary=True)
            cursor.execute(sql_query)
            results = cursor.fetchall()
            cursor.close()
            close_connection(connection)
            logger.info("SQL query executed successfully with %s rows", len(results))
            return results

        return "Error executing SQL query: Unable to establish database connection."

    except Error as e:
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no message."
        logger.exception("MySQL error while executing query")
        return f"Error executing SQL query: {error_message}"
