# AI_Knowledge_Assistant/app/db/mysql.py
"""Backward-compatible MySQL helpers."""

from app.infrastructure.mysql_pool import close_connection, create_db_connection, get_connection

__all__ = ["create_db_connection", "get_connection", "close_connection"]
