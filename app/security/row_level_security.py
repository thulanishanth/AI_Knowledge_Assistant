#app/security/row_level_security.py
"""
Row-level security and PII masking.

PII column detection is schema-driven — no hardcoded field names.
Columns are identified as PII by name heuristics that work across domains.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# PII heuristic: column names containing these substrings are treated as PII.
# These are generic enough to work across any domain (hotel, e-commerce, etc.)
_PII_NAME_FRAGMENTS = frozenset({
    "name", "email", "phone", "mobile", "address", "passport",
    "credit", "card", "ssn", "dob", "birth", "gender", "ip_addr",
    "national_id", "tax_id", "license",
})


def _is_pii_column(col_name: str) -> bool:
    """Heuristic: is this column likely to contain PII?"""
    lower = col_name.lower()
    return any(fragment in lower for fragment in _PII_NAME_FRAGMENTS)


@dataclass
class SecurityContext:
    user_id: str
    tenant_id: str
    role: str  # "admin" | "analyst" | "viewer"
    allowed_segments: list[str] | None = None
    allowed_properties: list[int] | None = None
    max_rows: int = 1000
    can_see_pii: bool = False


class RLSFilter:
    """
    Injects security filters into SQL queries AFTER generation but BEFORE execution.
    PII masking is schema-driven — no hardcoded column names.
    """

    _AGGREGATE_PATTERN = re.compile(
        r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE
    )
    _GROUP_BY_PATTERN = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)

    def apply(self, sql: str, ctx: SecurityContext) -> str:
        if not sql or sql.strip().upper() == "INSUFFICIENT_CONTEXT":
            return sql

        clean_sql = re.sub(
            r"\s*LIMIT\s+\d+\s*;?\s*$", "", sql.strip(), flags=re.IGNORECASE
        )
        clean_sql = clean_sql.rstrip(";").strip()

        has_group_by = bool(self._GROUP_BY_PATTERN.search(clean_sql))
        has_aggregate = bool(self._AGGREGATE_PATTERN.search(clean_sql))
        is_scalar_aggregate = has_aggregate and not has_group_by

        effective_limit = min(ctx.max_rows, settings.max_query_results)

        filters: list[str] = []
        if ctx.allowed_segments:
            segs = ", ".join(f"'{s}'" for s in ctx.allowed_segments)
            filters.append(f"market_segment_type IN ({segs})")
        if ctx.allowed_properties:
            props = ", ".join(str(p) for p in ctx.allowed_properties)
            filters.append(f"property_id IN ({props})")

        if not filters:
            return f"{clean_sql};" if is_scalar_aggregate else f"{clean_sql} LIMIT {effective_limit};"

        filter_clause = " AND ".join(filters)
        if is_scalar_aggregate:
            secured = (
                f"WITH rls_base AS (\n  {clean_sql}\n),\n"
                f"rls_secured AS (\n  SELECT * FROM rls_base WHERE {filter_clause}\n)\n"
                f"SELECT * FROM rls_secured;"
            )
        else:
            secured = (
                f"WITH rls_base AS (\n  {clean_sql}\n),\n"
                f"rls_secured AS (\n  SELECT * FROM rls_base WHERE {filter_clause}\n)\n"
                f"SELECT * FROM rls_secured LIMIT {effective_limit};"
            )

        logger.info(
            "RLS applied: tenant=%s role=%s", ctx.tenant_id, ctx.role
        )
        return secured

    def mask_pii(
        self, rows: list[dict[str, Any]], ctx: SecurityContext
    ) -> list[dict[str, Any]]:
        """
        Mask PII fields. Detection is schema-driven via column name heuristics —
        no hardcoded column lists.
        """
        if ctx.can_see_pii or not rows:
            return rows

        # Discover PII columns from the actual result set
        pii_cols = {col for col in rows[0].keys() if _is_pii_column(col)}
        if not pii_cols:
            return rows

        masked = []
        for row in rows:
            masked_row = {}
            for col, val in row.items():
                if col in pii_cols and val:
                    val_str = str(val)
                    masked_row[col] = val_str[:3] + "***" if len(val_str) > 3 else "***"
                else:
                    masked_row[col] = val
            masked.append(masked_row)

        return masked