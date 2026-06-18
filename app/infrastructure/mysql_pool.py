#app/infrastructure/vector_store/mysql_pool.py
"""MySQL pooled connection management."""

from __future__ import annotations

from functools import lru_cache

import mysql.connector
from mysql.connector import Error, pooling
from mysql.connector.connection import MySQLConnection
from mysql.connector.pooling import PooledMySQLConnection

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

Connection = PooledMySQLConnection | MySQLConnection


@lru_cache(maxsize=1)
def _get_pool() -> pooling.MySQLConnectionPool:
    logger.info(
        "Initializing MySQL pool host=%s port=%s database=%s pool_size=%s",
        settings.db_host,
        settings.db_port,
        settings.db_name,
        settings.db_pool_size,
    )
    return mysql.connector.pooling.MySQLConnectionPool(
        pool_name="ai_knowledge_assistant",
        pool_size=settings.db_pool_size,
        pool_reset_session=True,
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        autocommit=False,
    )


def create_db_connection() -> Connection | None:
    """Return one pooled connection or None when unavailable."""
    try:
        return _get_pool().get_connection()
    except Error as exc:
        logger.exception("Failed to get MySQL connection from pool: %s", exc)
        return None


def get_connection() -> Connection | None:
    return create_db_connection()


def close_connection(connection: Connection | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Failed to close MySQL connection cleanly: %s", exc)
