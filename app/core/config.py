# AI_Knowledge_Assistant/app/core/config.py

"""Centralized application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Environment-backed immutable settings used across the app."""

    # pylint: disable=too-many-instance-attributes
    # Database
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = _get_int("DB_PORT", 3306)
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_name: str = os.getenv("DB_NAME", "hotel_db")
    db_table: str = os.getenv("DB_TABLE", "hotel_reservations")

    # LLM
    hf_api_key: str = os.getenv("HF_API_KEY", "")
    hf_model: str = os.getenv("HF_MODEL", "katanemo/Arch-Router-1.5B")
    cloud_api_key: str = os.getenv("CLOUD_API_KEY", "")
    max_query_results: int = _get_int("MAX_QUERY_RESULTS", 10)

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "")
    structured_log_json: bool = _get_bool("STRUCTURED_LOG_JSON", False)

    # Memory / vector
    chroma_server_host: str = os.getenv("CHROMA_SERVER_HOST", "localhost")
    chroma_server_port: int = _get_int("CHROMA_SERVER_PORT", 8000)
    chroma_use_ssl: bool = _get_bool("CHROMA_USE_SSL", False)
    chroma_persist_path: str = os.getenv("CHROMA_PERSIST_PATH", "./.chroma")
    vector_collection_user: str = os.getenv("VECTOR_COLLECTION_USER", "user_memory_collection")
    vector_collection_knowledge: str = os.getenv(
        "VECTOR_COLLECTION_KNOWLEDGE", "knowledge_memory_collection"
    )
    window_memory_size: int = _get_int("WINDOW_MEMORY_SIZE", 10)
    top_k_retrieval: int = _get_int("TOP_K_RETRIEVAL", 20)
    rerank_top_k: int = _get_int("RERANK_TOP_K", 5)
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "deterministic")
    memory_ttl_days: int = _get_int("MEMORY_TTL_DAYS", 30)
    memory_importance_threshold: float = _get_float("MEMORY_IMPORTANCE_THRESHOLD", 0.5)
    enable_reranking: bool = _get_bool("ENABLE_RERANKING", True)

    # Observability
    enable_metrics: bool = _get_bool("ENABLE_METRICS", True)
    enable_tracing: bool = _get_bool("ENABLE_TRACING", False)
    otel_service_name: str = os.getenv("OTEL_SERVICE_NAME", "ai-knowledge-assistant")


settings = Settings()
