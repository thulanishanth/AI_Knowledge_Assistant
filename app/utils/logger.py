# AI_Knowledge_Assistant/app/utils/logger.py
"""Backward-compatible logging exports."""

from app.core.logging import clear_request_id, get_logger, set_request_id, setup_logging

__all__ = ["clear_request_id", "get_logger", "set_request_id", "setup_logging"]
