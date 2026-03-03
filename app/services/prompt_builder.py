from app.config import DB_TABLE
from app.utils.logger import get_logger

logger = get_logger(__name__)


def build_prompt(user_question: str, context: str, intent: str) -> str:
    """
    Build a prompt for the LLM by combining context and user question.

    Args:
        user_question (str): The refined user query.
        context (str): Relevant context retrieved from RAG.

    Returns:
        str: Full prompt string for LLM.
    """
    if intent == "sql":
        logger.debug("Building SQL prompt")
        prompt = f"""
You are an assistant that converts user questions into safe MySQL SELECT queries.

Context:
{context}

Question:
{user_question}

Instructions:
- Return only one valid MySQL SELECT query.
- Use only tables/columns from the provided context/schema.
- Use only this table: {DB_TABLE}.
- Never use INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE.
- Do not include explanations, markdown, or code fences.

SQL:
"""
        return prompt.strip()

    logger.debug("Building general prompt")
    prompt = f"""
You are an AI assistant that helps answer user questions accurately.

Context:
{context}

Question:
{user_question}

Instructions:
- Provide concise and clear answers.
- If the answer is unknown, respond with 'I don't know'.

Answer:
"""
    return prompt.strip()
