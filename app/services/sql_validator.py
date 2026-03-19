# AI_Knowledge_Assistant/app/services/sql_validator.py
"""Compatibility wrapper for SQL validation."""

from app.security.sql_guard import SqlGuard, validate_sql

sql_guard = SqlGuard()

__all__ = ["validate_sql", "sql_guard"]
