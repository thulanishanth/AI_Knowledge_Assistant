import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _get_env_int(name: str, default: int) -> int:
    value: Optional[str] = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = _get_env_int("DB_PORT", 3306)
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "hotel_db")
DB_TABLE = os.getenv("DB_TABLE", "hotel_reservations")

HF_API_KEY = os.getenv("HF_API_KEY", "")
HF_MODEL = os.getenv("HF_MODEL", "katanemo/Arch-Router-1.5B")
CLOUD_API_KEY = os.getenv("CLOUD_API_KEY", "")
MAX_QUERY_RESULTS = _get_env_int("MAX_QUERY_RESULTS", 10)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "")
