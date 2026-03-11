"""
API Router for chat-related endpoints.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.query_service import handle_query
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

class ChatRequest(BaseModel):
    """Schema for the user's chat request."""
    question: str = Field(..., min_length=1)

class ChatResponse(BaseModel):
    """
    Schema for the successful chat response, including the generated answer 
    and the system's confidence level.
    """
    answer: str
    confidence: float = 1.0

@router.post("/", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    """
    Handles incoming chat requests by passing them through the query pipeline.
    """
    logger.info("Chat request received")
    try:
        answer, confidence = handle_query(request.question)
        logger.info("Chat request completed with confidence %.2f", confidence)
        return ChatResponse(answer=answer, confidence=confidence)
    except (RuntimeError, ValueError, TypeError, KeyError) as e:
        logger.exception("Unhandled error in chat endpoint")
        error_message = str(e).strip() or f"{e.__class__.__name__} occurred with no error message."
        raise HTTPException(status_code=500, detail=error_message) from e
