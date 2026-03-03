# app/services/formatter.py

def format_answer(answer: str) -> str:
    """
    Basic formatting for general answers.
    """
    if not answer:
        return "No answer generated."
    return answer.strip()


def format_sql_results(results, _user_question: str) -> str:
    """
    Converts SQL query results into a natural language response.
    Supports single row and multiple row outputs.
    """

    if not results:
        return "No data found for your query."

    # Case 1: Single result (like COUNT)
    if len(results) == 1:
        row = results[0]
        response_parts = []

        for key, value in row.items():
            response_parts.append(f"{key.replace('_', ' ').title()}: {value}")

        return "Here is the result:\n" + ", ".join(response_parts)

    # Case 2: Multiple rows
    response = "Here are the results:\n\n"

    for i, row in enumerate(results[:20], start=1):  # limit output
        row_text = ", ".join(
            f"{key.replace('_', ' ').title()}: {value}"
            for key, value in row.items()
        )
        response += f"{i}. {row_text}\n"

    if len(results) > 20:
        response += f"\nShowing first 20 of {len(results)} records."

    return response
