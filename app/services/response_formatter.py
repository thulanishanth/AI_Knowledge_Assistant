#app/services/response_formatter.py
"""Result-aware answer formatting for chat and UI rendering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.settings import settings


@dataclass(slots=True)
class FormattedAnswer:
    answer: str
    presentation: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def format_answer(answer: str) -> str:
    return answer.strip() if str(answer or "").strip() else "No answer generated."


def _display_label(column_name: str) -> str:
    return column_name.replace("_", " ").strip().title()


def _stringify(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _stable_choice(seed: str, options: list[str]) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(options)
    return options[index]


class ResponseFormatter:
    """Create polished text plus a structured presentation payload."""

    def format_rows(
        self,
        *,
        question: str,
        session_id: str,
        rows: list[dict[str, Any]],
        truncated: bool,
        cached: bool,
    ) -> FormattedAnswer:
        if not rows:
            return FormattedAnswer(
                answer=_stable_choice(
                    f"{session_id}:{question}:empty",
                    [
                        "No matching rows were found for that question.",
                        "The database did not return any matching rows for that request.",
                        "I checked the table and there were no matching records.",
                    ],
                ),
                presentation={
                    "kind": "notice",
                    "title": "No matching rows",
                    "message": "The configured table returned no records for this query.",
                },
                meta={"cached": cached},
            )

        if len(rows) == 1 and len(rows[0]) == 1:
            column_name, value = next(iter(rows[0].items()))
            intro = _stable_choice(
                f"{session_id}:{question}:metric",
                [
                    "Here is the result.",
                    "This is the value from the database.",
                    "The database result is below.",
                ],
            )
            rendered_value = _stringify(value)
            return FormattedAnswer(
                answer=f"{intro}\n\n{_display_label(column_name)}: {rendered_value}",
                presentation={
                    "kind": "metric",
                    "title": _display_label(column_name),
                    "value": rendered_value,
                },
                meta={"cached": cached},
            )

        if len(rows) == 1:
            row = rows[0]
            intro = _stable_choice(
                f"{session_id}:{question}:record",
                [
                    "I found one matching record.",
                    "There is one matching row in the database.",
                    "The database returned a single matching record.",
                ],
            )
            details = "\n".join(
                f"- {_display_label(key)}: {_stringify(value)}"
                for key, value in row.items()
            )
            return FormattedAnswer(
                answer=f"{intro}\n\n{details}",
                presentation={
                    "kind": "record",
                    "title": "Matching record",
                    "fields": [
                        {"label": _display_label(key), "value": _stringify(value)}
                        for key, value in row.items()
                    ],
                },
                meta={"cached": cached},
            )

        columns = list(rows[0].keys())
        preview_rows = rows[: settings.max_preview_rows]
        intro = _stable_choice(
            f"{session_id}:{question}:rows",
            [
                "Here are the matching rows.",
                "These are the rows returned from the database.",
                "The database returned the following records.",
            ],
        )
        bullet_rows = []
        for index, row in enumerate(preview_rows, start=1):
            cell_text = " | ".join(
                f"{_display_label(column)}: {_stringify(row.get(column))}"
                for column in columns
            )
            bullet_rows.append(f"{index}. {cell_text}")

        answer = f"{intro}\n\n" + "\n".join(bullet_rows)
        if truncated:
            answer += f"\n\nShowing the first {settings.max_query_results} rows."

        return FormattedAnswer(
            answer=answer,
            presentation={
                "kind": "rows",
                "title": "Query results",
                "columns": [_display_label(column) for column in columns],
                "rows": [
                    [_stringify(row.get(column)) for column in columns]
                    for row in preview_rows
                ],
                "truncated": truncated,
                "layout": "cards" if len(columns) > 5 else "table",
            },
            meta={"cached": cached},
        )


_response_formatter = ResponseFormatter()


def format_sql_results(results: list[dict[str, Any]], user_question: str) -> str:
    return _response_formatter.format_rows(
        question=user_question,
        session_id="legacy",
        rows=results,
        truncated=False,
        cached=False,
    ).answer
