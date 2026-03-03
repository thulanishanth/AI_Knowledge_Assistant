import re

def clean_text(text: str) -> str:
    """
    Remove extra spaces, newlines, and unwanted characters from text.

    Args:
        text (str): Input string.

    Returns:
        str: Cleaned text.
    """
    if not text:
        return ""

    # Remove extra spaces and newlines
    text = " ".join(text.split())

    # Optionally remove special characters except punctuation
    text = re.sub(r"[^\w\s.,!?;:]", "", text)

    return text

def safe_string(input_str: str) -> str:
    """
    Escape dangerous characters to prevent injection attacks.

    Args:
        input_str (str): Input string.

    Returns:
        str: Escaped string.
    """
    if not input_str:
        return ""

    # Replace single quotes with escaped version
    return input_str.replace("'", "''")
