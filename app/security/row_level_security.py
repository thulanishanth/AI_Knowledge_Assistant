# app/security/row_level_security.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SecurityContext:
    """Carries the user's security identity through the pipeline."""

    user_id: str
    tenant_id: str
    role: str  # "admin", "analyst", "viewer"
    allowed_segments: list[str] | None = None
    allowed_properties: list[int] | None = None
    max_rows: int = 1000
    can_see_pii: bool = False


class RLSFilter:
    """Injects security filters into SQL queries AFTER generation but BEFORE execution."""

    # Detect aggregate-only queries that should NOT have a LIMIT appended
    _AGGREGATE_PATTERN = re.compile(
        r"\b(count|sum|avg|min|max)\s*\(",
        re.IGNORECASE,
    )
    _GROUP_BY_PATTERN = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)

    def apply(self, sql: str, ctx: SecurityContext) -> str:
        if not sql or sql.strip().upper() == "INSUFFICIENT_CONTEXT":
            return sql

        # Normalise: strip trailing semicolon and any existing LIMIT
        clean_sql = re.sub(r"\s*LIMIT\s+\d+\s*;?\s*$", "", sql.strip(), flags=re.IGNORECASE)
        clean_sql = clean_sql.rstrip(";").strip()

        # Determine whether this is a pure-aggregate query (no GROUP BY = single row)
        has_group_by = bool(self._GROUP_BY_PATTERN.search(clean_sql))
        has_aggregate = bool(self._AGGREGATE_PATTERN.search(clean_sql))
        is_scalar_aggregate = has_aggregate and not has_group_by

        effective_limit = min(ctx.max_rows, settings.max_query_results)

        # Build additional WHERE filters
        filters: list[str] = []

        if ctx.allowed_segments:
            segs = ", ".join(f"'{s}'" for s in ctx.allowed_segments)
            filters.append(f"market_segment_type IN ({segs})")

        if ctx.allowed_properties:
            props = ", ".join(str(p) for p in ctx.allowed_properties)
            filters.append(f"property_id IN ({props})")

        # ── No additional security filters needed ──
        if not filters:
            if is_scalar_aggregate:
                # Single-value aggregates don't need LIMIT
                return f"{clean_sql};"
            return f"{clean_sql} LIMIT {effective_limit};"

        filter_clause = " AND ".join(filters)

        if is_scalar_aggregate:
            secured_sql = (
                f"WITH rls_base AS (\n"
                f"  {clean_sql}\n"
                f"),\n"
                f"rls_secured AS (\n"
                f"  SELECT * FROM rls_base\n"
                f"  WHERE {filter_clause}\n"
                f")\n"
                f"SELECT * FROM rls_secured;"
            )
        else:
            secured_sql = (
                f"WITH rls_base AS (\n"
                f"  {clean_sql}\n"
                f"),\n"
                f"rls_secured AS (\n"
                f"  SELECT * FROM rls_base\n"
                f"  WHERE {filter_clause}\n"
                f")\n"
                f"SELECT * FROM rls_secured LIMIT {effective_limit};"
            )

        logger.info(
            "RLS applied: tenant=%s role=%s segments=%s properties=%s",
            ctx.tenant_id,
            ctx.role,
            ctx.allowed_segments,
            ctx.allowed_properties,
        )
        return secured_sql

    def mask_pii(self, rows: list[dict[str, Any]], ctx: SecurityContext) -> list[dict[str, Any]]:
        """Mask PII fields in results if the user lacks PII access."""
        if ctx.can_see_pii:
            return rows

        PII_COLUMNS = {
            "guest_name", "customer_name", "email", "phone",
            "guest_email", "guest_phone", "credit_card",
            "passport_number", "address",
        }

        masked = []
        for row in rows:
            masked_row = {}
            for col, val in row.items():
                if col.lower() in PII_COLUMNS and val:
                    val_str = str(val)
                    masked_row[col] = val_str[:3] + "***" if len(val_str) > 3 else "***"
                else:
                    masked_row[col] = val
            masked.append(masked_row)

        return masked