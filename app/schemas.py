#app/schemas.py
"""Schemas for API request and response validation."""
from typing import Any
from pydantic import BaseModel

class QueryRequest(BaseModel):
    # For testing Phase 1, we will pass raw SQL here.
    # Later, this will just be the user's natural language question.
    """Data model for an incoming user query."""
    question: str

class QueryResponse(BaseModel):
    """Data model for the API response."""
    status: str
    sql_query: str | None = None
    results: list[dict[str, Any]] | None = None
    message: str | None = None
