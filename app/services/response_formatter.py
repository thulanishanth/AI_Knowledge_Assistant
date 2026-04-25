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

        # Handle formatting for Charts vs Tables
        preview = rows[: settings.max_preview_rows]
        columns = list(preview[0].keys())

        formatted_rows = [
            [_stringify(row.get(column)) for column in columns]
            for row in preview
        ]

        lines = ["Here are the matching results:\n"]
        headers = [_titleize(column) for column in columns]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")

        for row in formatted_rows:
            lines.append("| " + " | ".join(row) + " |")

        if truncated:
            lines.append("\n*Showing a limited preview of the results.*")

        # If the analyzer detected a chart pattern, return a chart payload!
        presentation_payload = {
            "kind": "rows",
            "layout": "grid",
            "columns": headers,
            "rows": formatted_rows,
            "truncated": truncated,
        }

        if shape.kind == "chart":
            labels = [str(r.get(columns[0])) for r in preview]
            # Convert values to float for Chart.js
            try:
                values = [float(r.get(columns[1])) for r in preview]
                presentation_payload = {
                    "kind": "bar_chart" if shape.is_categorical else "line_chart",
                    "title": f"{headers[1]} by {headers[0]}",
                    "labels": labels,
                    "datasets": [{"label": headers[1], "data": values}]
                }
            except Exception:
                pass # Fallback to table if math conversion fails

        return (
            "\n".join(lines),
            presentation_payload,
        )