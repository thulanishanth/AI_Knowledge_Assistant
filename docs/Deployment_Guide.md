# Deployment Guidance

## Environment

Set these variables in production:

- `CHROMA_SERVER_HOST`
- `CHROMA_SERVER_PORT`
- `VECTOR_COLLECTION_USER`
- `VECTOR_COLLECTION_KNOWLEDGE`
- `WINDOW_MEMORY_SIZE`
- `TOP_K_RETRIEVAL`
- `RERANK_TOP_K`
- `EMBEDDING_PROVIDER`
- `EMBEDDING_MODEL`
- `MEMORY_TTL_DAYS`
- `ENABLE_RERANKING`
- `ENABLE_TRACING`
- `ENABLE_METRICS`

## Runtime Topology

Recommended split:

1. API service (FastAPI/Uvicorn workers)
2. Chroma service (remote or persistent deployment)
3. MySQL service (business data + schema)
4. Metrics/trace backend (Prometheus + OTEL collector)

## Horizontal Scaling

- Run multiple API replicas behind a load balancer.
- Use shared remote Chroma for vector consistency.
- Keep session identity client-driven (`session_id`) or gateway-injected.
- Window/Summary memory is currently process-local; move to Redis for strict cross-replica session continuity.

## Reliability

- Keep Chroma health checks active.
- Enable request timeouts for LLM and DB paths.
- Monitor: vector latency, llm latency, memory retrieval latency, error rates.
- Use graceful fallback when vector retrieval fails.

## Security

- Rotate API keys and DB credentials via secrets manager.
- Never commit live tokens in `.env`.
- Restrict DB user to read-only for query assistant workloads.

## Production Checklist

1. Install dependencies from `requirements.txt`.
2. Run schema/read permissions validation.
3. Smoke test `/api/chat/` with and without `session_id`.
4. Verify memory update + retrieval across multiple turns.
5. Verify TTL cleanup task behavior in logs.
