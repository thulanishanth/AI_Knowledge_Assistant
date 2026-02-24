from pydantic import BaseModel
from typing import Any

class QueryRequest(BaseModel):
    # For testing Phase 1, we will pass raw SQL here. 
    # Later, this will just be the user's natural language question.
    question: str

class QueryResponse(BaseModel):
    status: str
    sql_query: str | None = None
    results: list[dict[str, Any]] | None = None
    message: str | None = None