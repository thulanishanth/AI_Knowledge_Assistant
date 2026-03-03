from app.utils.logger import get_logger

logger = get_logger(__name__)


def apply_rules(user_question: str, intent: str) -> str:
    """
    Apply rules to refine the user question based on its intent.

    Args:
        user_question (str): Original user input.
        intent (str): Detected intent ('sql' or 'general').

    Returns:
        str: Refined question.
    """

    refined_question = user_question.strip()  # Remove leading/trailing whitespace

    # Simple rule: if SQL, ensure semicolon at the end
    if intent == "sql":
        if not refined_question.endswith(";"):
            refined_question += ";"
            logger.debug("Semicolon appended for SQL intent")

    # Simple rule: if general, remove redundant spaces
    elif intent == "general":
        refined_question = " ".join(refined_question.split())
        logger.debug("Whitespace normalized for general intent")

    return refined_question
