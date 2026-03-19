#AI_Knowledge_Assistant/app/config.py
"""Backward-compatible config exports.

New code should import `app.core.settings.settings`.
"""

from app.core.settings import settings

DB_HOST = settings.db_host
DB_PORT = settings.db_port
DB_USER = settings.db_user
DB_PASSWORD = settings.db_password
DB_NAME = settings.db_name
DB_TABLE = settings.db_table

HF_API_KEY = settings.hf_api_key
HF_MODEL = settings.hf_model
CLOUD_API_KEY = settings.cloud_api_key
MAX_QUERY_RESULTS = settings.max_query_results
LOG_LEVEL = settings.log_level
LOG_FILE = settings.log_file

CHROMA_SERVER_HOST = settings.chroma_server_host
CHROMA_SERVER_PORT = settings.chroma_server_port
VECTOR_COLLECTION_USER = settings.vector_collection_user
VECTOR_COLLECTION_KNOWLEDGE = settings.vector_collection_knowledge
WINDOW_MEMORY_SIZE = settings.window_memory_size
TOP_K_RETRIEVAL = settings.top_k_retrieval
EMBEDDING_MODEL = settings.embedding_model
ENABLE_TRACING = settings.enable_tracing
ENABLE_METRICS = settings.enable_metrics
MEMORY_TTL_DAYS = settings.memory_ttl_days
ENABLE_RERANKING = settings.enable_reranking
