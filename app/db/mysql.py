"""MySQL connection management with lazy singleton pooling."""

from functools import lru_cache
import mysql.connector
from mysql.connector import Error, pooling
from mysql.connector.connection import MySQLConnection
from mysql.connector.pooling import PooledMySQLConnection

from app.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
from app.utils.logger import get_logger

logger = get_logger(__name__)

@lru_cache(maxsize=1)
def _get_pool() -> pooling.MySQLConnectionPool:
    """
    Initialize and return the connection pool.
    This function is memoized to create the pool exactly once.
    """
    try:
        logger.info("Initializing MySQL Connection Pool...")
        pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name="ai_assistant_pool",
            pool_size=10,             # Keep 10 connections warm and ready
            pool_reset_session=True,  # Wipe temporary variables when returned to pool
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        logger.info("MySQL Connection Pool established successfully.")
        return pool
    except Error as e:
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no message."
        logger.exception("Failed to create MySQL Connection Pool: %s", error_message)
        raise  # If we can't connect to the DB on startup, we should fail loudly

def create_db_connection() -> PooledMySQLConnection | MySQLConnection | None:
    """
    Fetch a connection from the pre-warmed pool instead of opening a new TCP socket.
    Kept the same function name so you don't have to rewrite the rest of your app.
    """
    try:
        pool = _get_pool()
        connection = pool.get_connection()
        logger.debug("Fetched connection from MySQL pool")
        return connection
    except Error as e:
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no message."
        logger.exception("Error fetching connection from pool: %s", error_message)
        return None

def get_connection():
    """
    Alias for backward compatibility.
    """
    return create_db_connection()

def close_connection(connection):
    """
    Safely return a connection to the pool.

    Args:
        connection: MySQL connection object.
    """
    if connection and connection.is_connected():
        # Magic: Because this is a PooledMySQLConnection, calling .close()
        # does NOT sever the TCP link. It simply hands it back to the pool!
        connection.close()
        logger.debug("MySQL connection returned to pool")
