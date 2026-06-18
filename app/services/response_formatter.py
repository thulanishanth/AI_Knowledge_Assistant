#app/services/response_formatter.py
from __future__ import annotations

from typing import Any

from app.core.settings import settings
from app.services.result_analyzer import ResultAnalyzer

def _titleize(value: str) -> str:
    return str(value or "").replace("_", " ").strip().title()

def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)

class ResponseFormatter:
    """Format output dynamically from result shape."""

    def __init__(self) -> None:
        self._analyzer = ResultAnalyzer()

    def format_database_result(
        self,
        question: str,
        rows: list[dict[str, Any]],
        truncated: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        shape = self._analyzer.analyze(rows)

        if shape.kind == "empty":
            return (
                "No matching rows were found in the database.",
                {
                    "kind": "notice",
                    "title": "No results",
                    "message": "No matching rows were found in the database.",
                },
            )

        if shape.kind == "scalar":
            label = _titleize(shape.scalar_label or "Result")
            value = _stringify(shape.scalar_value)
            
            # Catch LLM conversational fallbacks
            if str(shape.scalar_label).strip().lower() == "message":
                return (
                    value,
                    {
                        "kind": "notice",
                        "title": "Assistant Notice",
                        "message": value,
                    },
                )

            return (
                f"**{label}:** {value}",
                {
                    "kind": "metric",
                    "title": label,
                    "value": value,
                },
            )

        if shape.kind == "single_record":
            row = rows[0]
            fields = [
                {"label": _titleize(key), "value": _stringify(value)}
                for key, value in row.items()
            ]
            text = "Here is the matching record.\n\n" + "\n".join(
                f"**{field['label']}:** {field['value']}" for field in fields
            )
            return text, {"kind": "record", "fields": fields}

        # --- UPDATED: PROPER MARKDOWN TABLE GENERATION ---
        preview = rows[: settings.max_preview_rows]
        columns = list(preview[0].keys())
        
        formatted_rows = [
            [_stringify(row.get(column)) for column in columns]
            for row in preview
        ]

        # Build Markdown Table String
        lines = ["Here are the matching results:\n"]
        
        headers = [_titleize(column) for column in columns]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")
        
        for row in formatted_rows:
            lines.append("| " + " | ".join(row) + " |")

        if truncated:
            lines.append("\n*Showing a limited preview of the results.*")

        return (
            "\n".join(lines),
            {
                "kind": "rows",
                "layout": "grid",
                "columns": headers,
                "rows": formatted_rows,
                "truncated": truncated,
            },
        )