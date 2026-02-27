"""Entry point for the Hotel AI Assistant FastAPI application."""
import logging
from fastapi import FastAPI, HTTPException
from app.schemas import QueryRequest, QueryResponse
from app.sql.validator import validate_sql
from app.sql.executor import execute_safe_query
from app.services.hf_client import generate_sql

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Hotel AI Assistant API", version="1.0.0")

@app.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest):
    """Converts natural language to SQL and executes it securely."""
    try:
        # 1. AI Generation
        logger.info("Generating SQL for question: %s", request.question)
        proposed_sql = generate_sql(request.question)
        logger.info("AI Proposed SQL: %s", proposed_sql)
        
        # 2. Security Firewall
        is_valid, validation_msg = validate_sql(proposed_sql)
        if not is_valid:
            logger.warning("SECURITY ALERT: Blocked AI-generated query -> %s", validation_msg)
            raise HTTPException(status_code=400, detail=validation_msg)
            
        # 3. Execution
        results = execute_safe_query(proposed_sql)
        
        return QueryResponse(
            status="success",
            sql_query=proposed_sql,
            results=results,
            message="Query processed successfully."
        )

    except ValueError as ve:
        # This catches the 503/Loading errors from hf_client.py
        logger.error("AI Client Error: %s", ve)
        raise HTTPException(status_code=502, detail=f"AI Service Error: {str(ve)}") from ve
    
    except Exception as e:
        # General catch-all for DB or unexpected logic errors
        logger.error("System Failure: %s", e)
        raise HTTPException(status_code=500, detail="An internal server error occurred.") from e