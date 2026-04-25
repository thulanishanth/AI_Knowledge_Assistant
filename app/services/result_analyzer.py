#app/services/result_analyzer.py
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
    # Add chart detection
    is_time_series: bool = False
    is_categorical: bool = False

class ResultAnalyzer:
    """Infer result shape generically from returned rows to power the UI."""

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

        # Detect if this should be a chart!
        # If we have 2 columns, and one is numeric and one is text/date, it's a perfect chart.
        is_categorical = False
        is_time_series = False

        if len(columns) == 2 and len(rows) > 1 and len(rows) <= 20:
            col1_val = first[columns[0]]
            col2_val = first[columns[1]]

            col1_is_num = isinstance(col1_val, (int, float))
            col2_is_num = isinstance(col2_val, (int, float))

            # E.g., [Year, Revenue]
            if (isinstance(col1_val, str) and "year" in columns[0].lower()) or \
               (isinstance(col1_val, str) and "month" in columns[0].lower()):
                is_time_series = True
            elif isinstance(col1_val, str) and col2_is_num:
                is_categorical = True

        return ResultShape(
            kind="chart" if (is_time_series or is_categorical) else "rows",
            row_count=len(rows),
            columns=columns,
            is_time_series=is_time_series,
            is_categorical=is_categorical
        )