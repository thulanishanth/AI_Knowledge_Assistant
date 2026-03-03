def check_confidence(answer: str, _context: str) -> float:
    """
    Check the confidence of an answer.

    Args:
        answer (str): The generated answer.
        context (str): The retrieved context.

    Returns:
        float: Confidence score between 0.0 and 1.0
    """
    if not answer or answer.lower().startswith("error"):
        return 0.0

    # Simple heuristic:
    # If answer contains SQL keywords, high confidence
    sql_keywords = ["select", "from", "where", "join", "count", "sum"]
    if any(keyword in answer.lower() for keyword in sql_keywords):
        return 1.0

    # Otherwise, moderate confidence for general answers
    return 0.8
