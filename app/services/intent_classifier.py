from app.utils.logger import get_logger

logger = get_logger(__name__)


def classify_intent(user_question: str) -> str:
    """
    Classify the user's intent as either 'sql' or 'general'.

    SQL intent covers both explicit SQL and natural-language questions
    that likely require database lookup.
    """
    question_lower = user_question.lower()

    sql_keywords = [
        "select", "insert", "update", "delete", "from", "where",
        "join", "count", "sum", "group by", "order by"
    ]
    db_entities = [
        "database", "db", "table", "record", "row", "rows", "column", "data",
        "user", "users", "order", "orders", "product", "quantity", "email"
    ]
    db_actions = ["how many", "total", "list", "show", "find", "latest", "first", "count"]

    if any(keyword in question_lower for keyword in sql_keywords):
        logger.debug("Intent classified as sql by keyword match")
        return "sql"

    has_entity = any(term in question_lower for term in db_entities)
    has_action = any(term in question_lower for term in db_actions)

    if has_entity and has_action:
        logger.debug("Intent classified as sql by entity+action match")
        return "sql"

    if has_entity and "?" in question_lower:
        logger.debug("Intent classified as sql by entity+question match")
        return "sql"

    logger.debug("Intent classified as general")
    return "general"
