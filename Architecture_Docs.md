# Production Memory + Vector Architecture

## 1. Target Architecture (Implemented)

The assistant now follows this strict runtime flow:

1. User Question
2. Session Manager (`app/core/session_manager.py`) resolves `user_id` + `session_id`
3. Memory Manager (`app/memory/memory_manager.py`) orchestrates context retrieval
4. Vector Retrieval (`app/memory/vector_memory.py`)
5. Re-ranking (`app/services/reranker_service.py`) Top 20 -> Top 5
6. Window Memory (`app/memory/window_memory.py`)
7. Summary Memory (`app/memory/summary_memory.py`)
8. RAG Schema Context (`app/services/rag_retriever.py`)
9. Context Aggregation (`app/memory/context_aggregator.py`)
10. Prompt Build (`app/services/prompt_builder.py`)
11. LLM execution (`app/services/llm_client.py`)
12. Response
13. Memory Update Pipeline (window + summary + vector write)
14. Background TTL cleanup

## 2. Clean Architecture Boundaries

- Core: config, DI, session identity
- Memory domain: orchestration and context lifecycle
- Vector store adapters: provider abstraction + Chroma implementation
- Services: embedding, reranking, prompt construction, LLM
- Observability: metrics, tracing, structured events
- API: request/response orchestration only

`MemoryManager` depends on abstractions and domain services, not direct Chroma calls.

## 3. Module-by-Module Implementation Order

1. `app/core/config.py`
- Central env-driven settings for DB, memory, vector, observability.

2. `app/core/session_manager.py`
- Multi-tenant session resolution with deterministic defaults.

3. `app/vector_store/vector_store_interface.py`
- Abstract vector API (`initialize`, `upsert`, `query`, `delete`, `health_check`, `cleanup`).

4. `app/vector_store/chroma_adapter.py`
- Chroma HTTP + persistent fallback + in-memory fallback.

5. `app/services/embedding_service.py`
- Pluggable embedding providers (OpenAI/HF/local/deterministic fallback).

6. `app/services/reranker_service.py`
- Retrieval re-ranking service (Top-N refinement).

7. `app/memory/vector_memory.py`
- Required vector memory functions:
  - `initialize_vector_store`
  - `store_user_memory`
  - `store_knowledge_memory`
  - `retrieve_user_context`
  - `retrieve_global_context`
  - `retrieve_hybrid_context`
  - `rank_results`
  - `generate_embedding`
  - `health_check`
  - `cleanup_old_memory`

8. `app/memory/window_memory.py`
- Session-scoped, async-safe recent-message cache.

9. `app/memory/summary_memory.py`
- Token-efficient session compression.

10. `app/memory/context_aggregator.py`
- Strict priority merge:
  - Vector memory
  - Session window
  - Summary memory
  - RAG schema context

11. `app/memory/memory_manager.py`
- Required orchestrator functions:
  - `initialize_memory_manager`
  - `get_context_for_llm`
  - `update_memory_pipeline`
  - `fetch_relevant_context`
  - `detect_memory_importance`

12. `app/observability/*.py`
- `metrics.py`, `tracing.py`, `structured_logger.py` with no-op safe fallback.

13. Integration updates
- `app/services/query_service.py`: hybrid memory retrieval + memory update.
- `app/api/chat.py`: async request with optional `user_id`/`session_id`.
- `app/main.py`: startup initialization + background cleanup task.

## 4. Multi-User Memory Data Contract

User memory metadata includes:
- `user_id`
- `session_id`
- `question`
- `answer`
- `timestamp`
- `timestamp_epoch`
- `importance_score`
- `source`
- `memory_type`
- `version`

Collections:
- `VECTOR_COLLECTION_USER` (default `user_memory_collection`)
- `VECTOR_COLLECTION_KNOWLEDGE` (default `knowledge_memory_collection`)

## 5. Resilience and Fallback

- If vector DB fails, system continues with window + summary + RAG.
- If Chroma is unavailable, adapter falls back to in-memory vectors.
- Optional observability dependencies degrade gracefully.

## 6. Scalability Readiness

- Async-first memory retrieval/update
- Provider abstraction for future Pinecone/Weaviate/Milvus adapters
- Background cleanup for TTL memory lifecycle
- Token-aware prompt construction to control context growth
