"""Backward-compatible import for centralized settings."""

from app.core.settings import Settings, settings

__all__ = ["Settings", "settings"]
