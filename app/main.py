from fastapi import FastAPI, HTTPException
from app.schemas import QueryRequest, QueryResponse
from app.sql.validator import validate_sql
from app.sql.executor import execute_safe_query
import logging

# Set up logging to monitor our API and security alerts
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the FastAPI application
app = FastAPI(title="Hotel AI Assistant API", version="1.0.0")

@app.get("/health")
def health_check():
    """Simple endpoint to verify the API is running."""
    return {"status": "healthy", "service": "online"}

@app.post("/query", response_model=QueryResponse)
def process_query(request: QueryRequest):
    """
    Main endpoint for query processing.
    Currently configured to accept raw SQL to test the validation firewall.
    """
    # NOTE: Since the LLM is paused, we are treating the user's 'question' 
    # input directly as the SQL query to test the pipeline.
    proposed_sql = request.question 
    
    # 1. Security Firewall: Validate the SQL
    is_valid, validation_msg = validate_sql(proposed_sql)
    
    if not is_valid:
        logger.warning(f"SECURITY ALERT: Blocked query -> {validation_msg}")
        # Return a 400 Bad Request if the SQL violates our security policies
        raise HTTPException(status_code=400, detail=validation_msg)
        
    # 2. Execution: Run the validated query against MySQL
    try:
        results = execute_safe_query(proposed_sql)
        return QueryResponse(
            status="success",
            sql_query=proposed_sql,
            results=results,
            message="Query validated and executed securely."
        )
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        raise HTTPException(status_code=500, detail="Database execution failed.")