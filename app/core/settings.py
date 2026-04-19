##app/core/settings.py
"""Application settings and environment parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = _get_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = _get_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get_str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _get_list(name: str, default: str) -> list[str]:
    raw = _get_str(name, default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or [value.strip() for value in default.split(",") if value.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable application configuration."""

    db_host: str = _get_str("DB_HOST", "localhost")
    db_port: int = _get_int("DB_PORT", 3306)
    db_user: str = _get_str("DB_USER", "root")
    db_password: str = _get_str("DB_PASSWORD", "")
    db_name: str = _get_str("DB_NAME", "")
    db_table: str = _get_str("DB_TABLE", "")
    db_pool_size: int = _get_int("DB_POOL_SIZE", 10)
    db_query_timeout_ms: int = _get_int("DB_QUERY_TIMEOUT_MS", 5000)
    
    # Metadata Catalog Tables (Lean Setup)
    meta_table_tenants: str = _get_str("META_TABLE_TENANTS", "meta_tenants")
    meta_table_rules: str = _get_str("META_TABLE_RULES", "meta_business_rules")
    
    max_query_results: int = _get_int("MAX_QUERY_RESULTS", 10)
    max_preview_rows: int = _get_int("MAX_PREVIEW_ROWS", 10)
    max_question_chars: int = _get_int("MAX_QUESTION_CHARS", 800)

    hf_api_key: str = _get_str("HF_API_KEY")
    hf_model: str = _get_str("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    cloud_api_key: str = _get_str("CLOUD_API_KEY")
    llm_timeout_seconds: float = _get_float("LLM_TIMEOUT_SECONDS", 30.0)
    llm_max_retries: int = _get_int("LLM_MAX_RETRIES", 2)
    llm_temperature_sql: float = _get_float("LLM_TEMPERATURE_SQL", 0.0)

    log_level: str = _get_str("LOG_LEVEL", "INFO").upper()
    log_file: str = _get_str("LOG_FILE")
    structured_log_json: bool = _get_bool("STRUCTURED_LOG_JSON", False)

    cors_allowed_origins: tuple[str, ...] = ()

    chroma_server_host: str = _get_str("CHROMA_SERVER_HOST", "localhost")
    chroma_server_port: int = _get_int("CHROMA_SERVER_PORT", 8000)
    chroma_use_ssl: bool = _get_bool("CHROMA_USE_SSL", False)
    chroma_persist_path: str = _get_str("CHROMA_PERSIST_PATH", "./.chroma")
    vector_collection_user: str = _get_str(
        "VECTOR_COLLECTION_USER",
        "user_memory_collection",
    )
    vector_collection_knowledge: str = _get_str(
        "VECTOR_COLLECTION_KNOWLEDGE",
        "knowledge_memory_collection",
    )
    window_memory_size: int = _get_int("WINDOW_MEMORY_SIZE", 10)
    top_k_retrieval: int = _get_int("TOP_K_RETRIEVAL", 8)
    rerank_top_k: int = _get_int("RERANK_TOP_K", 5)
    embedding_model: str = _get_str(
        "EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    embedding_provider: str = _get_str("EMBEDDING_PROVIDER", "local")
    memory_ttl_days: int = _get_int("MEMORY_TTL_DAYS", 30)
    memory_importance_threshold: float = _get_float(
        "MEMORY_IMPORTANCE_THRESHOLD",
        0.5,
    )
    enable_reranking: bool = _get_bool("ENABLE_RERANKING", True)

    enable_metrics: bool = _get_bool("ENABLE_METRICS", True)
    enable_tracing: bool = _get_bool("ENABLE_TRACING", False)
    otel_service_name: str = _get_str(
        "OTEL_SERVICE_NAME",
        "ai-knowledge-assistant",
    )

    schema_cache_ttl_seconds: int = _get_int("SCHEMA_CACHE_TTL_SECONDS", 300)
    query_cache_ttl_seconds: int = _get_int("QUERY_CACHE_TTL_SECONDS", 45)
    session_history_limit: int = _get_int("SESSION_HISTORY_LIMIT", 15)

    frontend_dir: str = _get_str("FRONTEND_DIR", str(BASE_DIR / "frontend"))
    environment: str = _get_str("APP_ENV", "development").lower()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cors_allowed_origins",
            tuple(
                _get_list(
                    "CORS_ALLOWED_ORIGINS",
                    "http://localhost:8000,http://127.0.0.1:8000",
                )
            ),
        )

    @property
    def frontend_path(self) -> Path:
        return Path(self.frontend_dir).resolve()


settings = Settings()
