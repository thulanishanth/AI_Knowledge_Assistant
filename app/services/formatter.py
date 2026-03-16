# AI_Knowledge_Assistant/app/services/formatter.py
"""Formatting helpers for user-facing answer text."""

from datetime import date, datetime
from decimal import Decimal

def format_answer(answer: str) -> str:
    """Normalize a general answer string."""
    if not answer:
        return "No answer generated."
    return answer.strip()

def _safe_format_value(value: object) -> str:
    """Safely format native SQL data types for human-readable output."""
    if value is None:
        return "NULL"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        # Clean trailing zeros for cleaner display
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, Decimal):
        return str(value)
    
    # Stringify and truncate massive text blobs to prevent breaking the UI
    text_val = str(value)
    if len(text_val) > 150:
        return text_val[:147] + "..."
    
    # Escape pipe characters so they don't break the Markdown table rendering
    return text_val.replace("|", "\\|")

def format_sql_results(results: list[dict[str, object]], _user_question: str) -> str:
    """Convert SQL rows into a compact, human-readable Markdown table."""
    if not results:
        return "No data found for your query."

    # Case 1: Single result (e.g., COUNT, SUM, or a single specific record)
    if len(results) == 1:
        row = results[0]
        response_parts = [
            f"**{key.replace('_', ' ').title()}**: {_safe_format_value(value)}"
            for key, value in row.items()
        ]
        return "Here is the result:\n\n" + "\n".join(response_parts)

    # Case 2: Multiple rows (Render as a Markdown Table)
    limit = 20
    displayed_results = results[:limit]
    
    # Dynamically extract headers from the keys of the first row
    headers = list(displayed_results[0].keys())
    
    # Build Markdown table header
    header_row = "| " + " | ".join(h.replace('_', ' ').title() for h in headers) + " |"
    separator_row = "| " + " | ".join("---" for _ in headers) + " |"
    
    table_lines = [header_row, separator_row]
    
    # Build Markdown table rows
    for row in displayed_results:
        row_str = "| " + " | ".join(_safe_format_value(row.get(h)) for h in headers) + " |"
        table_lines.append(row_str)

    # Efficiently join the list into a single string
    response = "Here are the results:\n\n" + "\n".join(table_lines)

    if len(results) > limit:
        response += f"\n\n*Showing first {limit} of {len(results)} records.*"

    return response