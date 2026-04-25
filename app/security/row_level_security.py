#app/security/row_level_security.py

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class SecurityContext:
    """Carries the user's security identity through the pipeline."""
    user_id: str
    tenant_id: str
    role: str           # "admin", "analyst", "viewer"
    allowed_segments: list[str] = None    # e.g., ["online", "direct"]
    allowed_properties: list[int] = None  # e.g., [property_id_1, property_id_2]
    max_rows: int = 1000
    can_see_pii: bool = False             # Guest names, emails, phones

class RLSFilter:
    """
    Injects security filters into SQL queries AFTER generation but BEFORE execution.
    """

    def apply(self, sql: str, ctx: SecurityContext) -> str:
        if not sql or sql.strip().upper() == "INSUFFICIENT_CONTEXT":
            return sql

        # Extract the base SQL (remove existing LIMIT for re-wrapping)
        clean_sql = re.sub(r"\s*LIMIT\s+\d+\s*;?\s*$", "", sql.strip(), flags=re.IGNORECASE)
        # CRITICAL FIX: Strip trailing semicolons so it can be safely embedded inside the CTE
        clean_sql = clean_sql.rstrip(";")

        filters: list[str] = []

        # TEMPORARY FIX: Commented out tenant_id injection because hotel_reservations
        # table does not actually have a tenant_id column!
        # filters.append(f"tenant_id = '{ctx.tenant_id}'")

        # 2. Segment filter (if user is restricted to certain segments)
        if ctx.allowed_segments:
            segs = ", ".join(f"'{s}'" for s in ctx.allowed_segments)
            filters.append(f"market_segment_type IN ({segs})")

        # 3. Property filter (for multi-property hotel groups)
        if ctx.allowed_properties:
            props = ", ".join(str(p) for p in ctx.allowed_properties)
            filters.append(f"property_id IN ({props})")

        # If there are no filters to apply, just return the cleaned SQL with the limit
        if not filters:
            effective_limit = min(ctx.max_rows, settings.max_query_results)
            return f"{clean_sql} LIMIT {effective_limit};"

        filter_clause = " AND ".join(filters)
        effective_limit = min(ctx.max_rows, settings.max_query_results)

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
            ctx.tenant_id, ctx.role, ctx.allowed_segments, ctx.allowed_properties
        )

        return secured_sql

    def mask_pii(self, rows: list[dict[str, Any]], ctx: SecurityContext) -> list[dict[str, Any]]:
        """
        Mask PII fields in results if user doesn't have PII access.
        """
        if ctx.can_see_pii:
            return rows

        PII_COLUMNS = {
            "guest_name", "customer_name", "email", "phone", "guest_email",
            "guest_phone", "credit_card", "passport_number", "address"
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