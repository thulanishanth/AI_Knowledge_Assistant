import contextvars
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_REQUEST_ID_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_LOGGER_STATE = {"configured": False}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID_CONTEXT.get()
        return True


def set_request_id(request_id: str) -> None:
    _REQUEST_ID_CONTEXT.set(request_id or "-")


def clear_request_id() -> None:
    _REQUEST_ID_CONTEXT.set("-")


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    if _LOGGER_STATE["configured"]:
        return

    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_path = Path(log_file) if log_file else logs_dir / "app.log"

    level_name = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s"
    )

    request_filter = RequestIdFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_filter)

    file_handler = RotatingFileHandler(
        filename=file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_filter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    _LOGGER_STATE["configured"] = True


def get_logger(name: str) -> logging.Logger:
    if not _LOGGER_STATE["configured"]:
        setup_logging()
    return logging.getLogger(name)
