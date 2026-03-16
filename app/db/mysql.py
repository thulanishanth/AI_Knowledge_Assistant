# AI_Knowledge_Assistant/app/db/mysql.py
"""MySQL connection management with lazy singleton pooling."""

from __future__ import annotations

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
            pool_size=10,
            pool_reset_session=True,
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        logger.info("MySQL Connection Pool established successfully.")
        return pool
    except Error as exc:
        error_message = str(exc).strip() or f"{exc.__class__.__name__} occurred with no message."
        logger.exception("Failed to create MySQL Connection Pool: %s", error_message)
        raise


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
    except Error as exc:
        error_message = str(exc).strip() or f"{exc.__class__.__name__} occurred with no message."
        logger.exception("Error fetching connection from pool: %s", error_message)
        return None


def get_connection() -> PooledMySQLConnection | MySQLConnection | None:
    """
    Alias for backward compatibility.
    """
    return create_db_connection()


def close_connection(connection: PooledMySQLConnection | MySQLConnection | None) -> None:
    """
    Safely return a connection to the pool.

    Important:
    Do NOT call `is_connected()` here. With mysql-connector, that check can raise
    `InternalError: Unread result found` if a cursor still has pending rows.
    Calling `.close()` on a pooled connection safely returns it to the pool.

    Args:
        connection: MySQL connection object or pooled connection.
    """
    if connection is None:
        return

    try:
        connection.close()
        logger.debug("MySQL connection returned to pool")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Failed to return MySQL connection to pool: %s", exc)