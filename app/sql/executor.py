from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database.connection import SessionLocal
import logging

logger = logging.getLogger(__name__)

def execute_safe_query(sql_query: str) -> list[dict]:
    """
    Executes a pre-validated SQL query against the database.
    Returns the results as a list of dictionaries.
    """
    db = SessionLocal()
    try:
        # Wrap the raw string in SQLAlchemy's text() construct for safe execution
        result = db.execute(text(sql_query))
        
        # Fetch all rows and convert them to a list of dicts for easy JSON serialization
        rows = result.mappings().fetchall()
        return [dict(row) for row in rows]
        
    except SQLAlchemyError as e:
        # Log the actual database error server-side, but don't expose DB internals to the user
        logger.error(f"Database execution error: {e}")
        raise ValueError("An error occurred while executing the query on the database.")
        
    finally:
        db.close()