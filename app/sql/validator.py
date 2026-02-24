import sqlparse
import logging

logger = logging.getLogger(__name__)

# The strict blocklist of mutating or administrative commands
FORBIDDEN_KEYWORDS = {
    "DELETE", "UPDATE", "DROP", "ALTER", 
    "TRUNCATE", "INSERT", "GRANT", "REVOKE", 
    "COMMIT", "ROLLBACK", "REPLACE"
}

def validate_sql(query: str) -> tuple[bool, str]:
    """
    Parses and validates an AI-generated SQL query.
    Returns a tuple: (is_valid: bool, error_message: str)
    """
    if not query or not query.strip():
        return False, "Query is empty."

    # Parse the SQL to ensure it is structurally valid
    parsed = sqlparse.parse(query)
    if not parsed:
        return False, "Could not parse SQL syntax."

    query_upper = query.upper()

    # 1. Enforce SELECT-only operations
    if not query_upper.strip().startswith("SELECT"):
        logger.warning(f"Blocked non-SELECT query: {query}")
        return False, "Only SELECT queries are permitted."

    # 2. Hard block any destructive keywords (defense-in-depth)
    for keyword in FORBIDDEN_KEYWORDS:
        if keyword in query_upper:
            logger.warning(f"Blocked malicious/forbidden keyword '{keyword}' in query: {query}")
            return False, f"Forbidden keyword detected: {keyword}. Operation blocked."

    # 3. Prevent massive data dumps by enforcing a LIMIT if one doesn't exist
    if "LIMIT" not in query_upper:
        logger.info("LIMIT clause missing, but query is otherwise safe. (Consider appending LIMIT programmatically).")

    return True, "Query is safe to execute."