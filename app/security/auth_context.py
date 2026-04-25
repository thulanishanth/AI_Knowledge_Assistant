# app/security/auth_context.py

from __future__ import annotations
from app.security.row_level_security import SecurityContext

# In your chat endpoint, build SecurityContext from the authenticated session:
def build_security_context(
    user_id: str,
    tenant_id: str,
    user_roles: dict,          # From JWT token or session store
) -> SecurityContext:
    """
    Build security context from the user's authenticated session.
    In production, this comes from your auth service (JWT, OAuth, etc.)
    """
    role = user_roles.get("role", "viewer")

    return SecurityContext(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        allowed_segments=user_roles.get("allowed_segments"),
        allowed_properties=user_roles.get("allowed_properties"),
        max_rows={"admin": 10000, "analyst": 1000, "viewer": 100}.get(role, 100),
        can_see_pii=role == "admin"
    )