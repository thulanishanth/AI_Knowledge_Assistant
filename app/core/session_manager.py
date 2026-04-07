#app/core/session_manager.py
"""Session and user identity helpers for multi-tenant requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SessionContext:
    """Resolved identity context for a single request."""

    user_id: str
    session_id: str
    request_ts: datetime