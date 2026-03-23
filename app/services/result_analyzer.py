from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class ResultShape:
    kind: str
    row_count: int
    columns: list[str]
    scalar_value: Any = None
    scalar_label: str | None = None

class ResultAnalyzer:
    """Infer result shape generically from returned rows."""

    def analyze(self, rows: list[dict[str, Any]]) -> ResultShape:
        if not rows:
            return ResultShape(kind="empty", row_count=0, columns=[])

        first = rows[0]
        columns = list(first.keys())

        if len(rows) == 1 and len(columns) == 1:
            label = columns[0]
            return ResultShape(
                kind="scalar",
                row_count=1,
                columns=columns,
                scalar_label=label,
                scalar_value=first.get(label),
            )

        if len(rows) == 1:
            return ResultShape(kind="single_record", row_count=1, columns=columns)

        return ResultShape(kind="rows", row_count=len(rows), columns=columns)