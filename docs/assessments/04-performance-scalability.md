# Performance & Scalability Assessment

**Project:** AI Knowledge Assistant (SQL Query Chatbot)
**Date:** 2026-02-20
**Current Scalability:** Single user, single thread, no concurrency

---

## Executive Summary

The current application can only serve one user at a time via a terminal input loop. It has no concurrency, no caching, no connection pooling, and rebuilds its entire state on every startup. For an MVP, the immediate concerns are LLM latency, database query performance, and supporting at least 10-50 concurrent users. This document identifies every performance bottleneck and scalability limitation.

---

## 1. Current Architecture Bottlenecks

### 1.1 Single-Threaded CLI Loop
```python
while True:
    question = input("\nAsk your question:")  # Blocks entire process
    answer = rag_chain.invoke(question)        # Synchronous call
    print(answer)
```
- Only one user can interact at a time
- The process blocks on `input()` and on the LLM call
- No concurrency whatsoever

**Fix:** Replace with async FastAPI server using `uvicorn` with multiple workers.

### 1.2 State Rebuilt on Every Startup
Every time `main.py` runs, it:
1. Loads the PDF from disk
2. Splits into chunks
3. Creates embeddings (API call to HuggingFace)
4. Builds FAISS index in memory

For the SQL chatbot MVP, this is less of a concern (no embeddings needed), but the pattern of "build everything at startup with no caching" is problematic.

**Fix:** Use persistent storage for any expensive computed state. For SQL chatbot: cache database schema.

### 1.3 No Connection Pooling
- The current code has no database connections (MVP needs to add them)
- When database connections are added, each request must not open a new connection
- Must use SQLAlchemy's built-in connection pool

**Fix (for MVP):**
```python
from sqlalchemy import create_engine
engine = create_engine(
    DATABASE_URL,
    pool_size=10,          # Max persistent connections
    max_overflow=20,       # Extra connections under load
    pool_timeout=30,       # Seconds to wait for a connection
    pool_recycle=1800,     # Recycle connections every 30 min
)
```

---

## 2. LLM Latency — Primary Performance Concern

### The Problem
LLM inference is the slowest step in the entire pipeline. Every user query requires at least one LLM call (SQL generation), potentially two (SQL generation + result formatting).

### Expected Latency Profile

| Step | Expected Latency | Notes |
|------|-----------------|-------|
| Input validation | < 1ms | Negligible |
| Schema injection | < 10ms | String formatting |
| LLM: Generate SQL | 2-10 seconds | HuggingFace API call |
| SQL validation | < 10ms | Local parsing |
| Database query execution | 50ms - 5s | Depends on query complexity |
| LLM: Format results | 2-10 seconds | Second API call (if used) |
| **Total per query** | **4-25 seconds** | **Dominated by LLM calls** |

### Mitigation Strategies

1. **Streaming responses**
   - Stream LLM output token-by-token to the user
   - User sees the answer forming in real time (much better UX)
   - Use Server-Sent Events (SSE) or WebSocket

2. **Reduce LLM calls per query**
   - Option: Skip the "explain results" LLM call for simple queries
   - Option: Format results as a table instead of natural language (no second LLM call)
   - Decision: One LLM call (generate SQL only) vs. two (generate + explain)

3. **LLM response caching**
   - Cache SQL for repeated/similar questions
   - Hash the question + schema → check cache before calling LLM
   - Caveat: Cache invalidation when data changes

4. **Model selection tradeoffs**
   - Qwen2.5-7B-Instruct: Moderate speed, moderate accuracy
   - Smaller models (1-3B): Faster but less accurate SQL
   - Larger models / GPT-4: Slower but much more accurate SQL
   - Self-hosted: More control over latency, but requires GPU infrastructure

5. **Async LLM calls**
   - Use `ainvoke()` instead of `invoke()` for non-blocking execution
   - Allows serving other requests while waiting for LLM response

---

## 3. Database Query Performance

### Concerns

1. **No query timeout enforcement**
   - A poorly generated SQL query could run for minutes
   - Must set statement_timeout at database level and application level
   ```sql
   SET statement_timeout = '30s';
   ```

2. **No row count limits**
   - `SELECT * FROM large_table` could return millions of rows
   - Must append `LIMIT` to all generated queries
   - LLM should be instructed to include LIMIT, but also enforce programmatically

3. **No query complexity limits**
   - LLM could generate expensive JOINs across many tables
   - Consider using `EXPLAIN` to estimate query cost before execution
   - Reject queries with estimated cost above a threshold

4. **No indexing guidance**
   - The chatbot has no knowledge of which columns are indexed
   - Could generate queries that cause full table scans
   - Consider: Include index information in schema context for LLM

### Performance Safeguards to Implement

```python
QUERY_TIMEOUT_SECONDS = 30
MAX_RESULT_ROWS = 1000

def execute_query_safely(db, sql: str):
    if "LIMIT" not in sql.upper():
        sql = f"{sql.rstrip(';')} LIMIT {MAX_RESULT_ROWS}"
    
    with db.engine.connect() as conn:
        conn.execute(text(f"SET statement_timeout = '{QUERY_TIMEOUT_SECONDS}s'"))
        result = conn.execute(text(sql))
        return result.fetchall()
```

---

## 4. Memory Usage

### Current State
The `requirements.txt` includes `torch==2.10.0`, `transformers==4.57.6`, and `sentence-transformers==5.2.2`. These packages consume significant memory:

| Package | Estimated Memory | Needed for SQL MVP? |
|---------|-----------------|-------------------|
| torch | 500MB - 2GB+ | NO (using API-based LLM) |
| transformers | 200MB - 500MB | NO (using API-based LLM) |
| sentence-transformers | 100MB - 300MB | NO (no embeddings needed) |
| faiss-cpu | 50MB - 100MB | NO (no vector store needed) |
| FAISS index (runtime) | Varies | NO |

### Fix
Removing torch, transformers, sentence-transformers, and faiss-cpu from dependencies will reduce the application's memory footprint by 1-3 GB. Since the MVP uses HuggingFace's hosted API (not local inference), these packages are completely unnecessary.

### Expected MVP Memory Profile
| Component | Estimated Memory |
|-----------|-----------------|
| Python runtime | 50MB |
| FastAPI + uvicorn | 30MB |
| LangChain + SQLAlchemy | 50MB |
| DB connection pool | 10-20MB |
| Per-request overhead | 5-10MB |
| **Total per worker** | **~150-200MB** |

---

## 5. Concurrency & Throughput

### Current: Zero concurrency
The CLI loop blocks on a single user's input.

### MVP Target
- 10-50 concurrent users
- Throughput: 5-20 queries per minute (LLM is the bottleneck)

### Recommended Architecture

```
                    ┌─────────────────┐
                    │   Load Balancer  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
        │  Worker 1  │ │  Worker 2  │ │  Worker 3  │
        │  (uvicorn) │ │  (uvicorn) │ │  (uvicorn) │
        └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                    ┌────────┴────────┐
                    │  Connection Pool │
                    │   (SQLAlchemy)   │
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │    Database      │
                    └─────────────────┘
```

**Configuration:**
```bash
uvicorn app:app --workers 4 --host 0.0.0.0 --port 8000
```

- 4 workers = 4 concurrent LLM requests
- Each worker has its own connection pool
- Async handlers allow I/O-bound tasks to not block the event loop

### Scaling Strategy

| Users | Infrastructure | Estimated Cost |
|-------|---------------|---------------|
| 1-10 | Single server, 2 workers | $20-50/month |
| 10-50 | Single server, 4 workers | $50-100/month |
| 50-200 | 2-3 servers behind load balancer | $200-500/month |
| 200+ | Auto-scaling cluster (Kubernetes) | $500+/month |

---

## 6. Caching Strategy

### What Should Be Cached

1. **Database schema** (changes rarely)
   - Cache TTL: 1 hour or until explicitly invalidated
   - Storage: In-memory (per worker)

2. **Frequent query results** (if acceptable staleness)
   - Cache TTL: 5-15 minutes
   - Storage: Redis (shared across workers)
   - Key: Hash of SQL query

3. **LLM responses for identical questions** (same question + same schema = same SQL)
   - Cache TTL: 1 hour
   - Storage: Redis
   - Key: Hash of question + schema version

### What Should NOT Be Cached
- Queries involving time-sensitive data ("orders from today")
- Queries after known data updates
- Results from write operations (not applicable if SELECT-only)

---

## 7. Monitoring & Observability for Performance

### Metrics to Track

| Metric | Target | Alert Threshold |
|--------|--------|----------------|
| LLM response time (p50) | < 5s | > 10s |
| LLM response time (p99) | < 15s | > 30s |
| DB query execution time (p50) | < 500ms | > 2s |
| DB query execution time (p99) | < 5s | > 10s |
| End-to-end latency (p50) | < 8s | > 15s |
| End-to-end latency (p99) | < 20s | > 40s |
| Error rate | < 5% | > 10% |
| Active connections | < pool size | > 80% pool |
| Memory usage per worker | < 300MB | > 500MB |
| Request queue depth | < 10 | > 50 |

### Tools
- **Application metrics:** Prometheus + Grafana
- **Request tracing:** OpenTelemetry
- **LangChain tracing:** LangSmith (already in dependencies)
- **Database monitoring:** pg_stat_statements (PostgreSQL)

---

## 8. Load Testing Plan

Before launching, run load tests to validate:

1. **Single user latency baseline** — What is the end-to-end time for one query?
2. **Concurrent user test** — 10 users, 50 users sending queries simultaneously
3. **Sustained load test** — Constant 5 queries/minute for 1 hour
4. **Spike test** — Sudden burst of 20 queries in 10 seconds
5. **LLM failure test** — What happens when HuggingFace API is slow or down?
6. **Database failure test** — What happens when DB connection is lost?

**Tools:** `locust`, `k6`, or `wrk` for HTTP load testing.

---

## Action Items Summary

| Priority | Item | Effort |
|----------|------|--------|
| Critical | Replace CLI with async FastAPI server | 6-8 hours |
| Critical | Remove torch/transformers from deps (save 1-3GB RAM) | 1 hour |
| Critical | Add DB connection pooling | 2 hours |
| Critical | Add query timeout enforcement | 1 hour |
| Critical | Add row count limits | 1 hour |
| High | Implement schema caching | 2-3 hours |
| High | Use async LLM calls (ainvoke) | 2-3 hours |
| High | Add streaming responses (SSE) | 4-6 hours |
| High | Set up performance monitoring | 4-6 hours |
| Medium | Implement response caching (Redis) | 4-6 hours |
| Medium | Run load testing before launch | 4-8 hours |
| Medium | Add EXPLAIN-based query cost estimation | 3-4 hours |
| Low | Optimize LLM prompt for shorter output | 2-3 hours |
