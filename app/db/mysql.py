import mysql.connector
from mysql.connector import Error
from mysql.connector.connection import MySQLConnection

from app.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
from app.utils.logger import get_logger

logger = get_logger(__name__)


def create_db_connection() -> MySQLConnection:
    connection = mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )
    logger.info("MySQL connection established")
    return connection


def get_connection():
    """
    Create and return a new MySQL database connection.

    Returns:
        mysql.connector.connection.MySQLConnection: MySQL connection object.
    """
    try:
        return create_db_connection()
    except Error as e:
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no message."
        logger.exception("Error connecting to MySQL: %s", error_message)
        return None


def close_connection(connection):
    """
    Safely close a MySQL connection.

    Args:
        connection: MySQL connection object.
    """
    if connection and connection.is_connected():
        connection.close()
        logger.info("MySQL connection closed")
