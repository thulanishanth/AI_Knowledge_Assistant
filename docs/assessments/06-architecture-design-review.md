# Architecture & Design Review

**Project:** AI Knowledge Assistant (SQL Query Chatbot)
**Date:** 2026-02-20

---

## Executive Summary

The current codebase implements a RAG (Retrieval-Augmented Generation) architecture for answering Java interview questions from a PDF. The MVP requires a fundamentally different architecture: a Text-to-SQL agent. This document evaluates the current design, proposes the target architecture for the MVP, and identifies key design decisions that need to be made.

---

## 1. Current Architecture

```
┌─────────────┐    ┌──────────┐    ┌────────────┐    ┌─────────┐
│  PDF File   │───>│  Loader  │───>│  Splitter  │───>│ Chunks  │
└─────────────┘    └──────────┘    └────────────┘    └────┬────┘
                                                          │
┌─────────────┐    ┌──────────────┐    ┌──────────┐      │
│  Embeddings │<───│ Vector Store │<───│  FAISS   │<─────┘
│  (HF API)   │    │              │    │  Index   │
└──────┬──────┘    └──────┬───────┘    └──────────┘
       │                  │
       │           ┌──────┴───────┐
       │           │  Retriever   │
       │           └──────┬───────┘
       │                  │
┌──────┴──────┐    ┌──────┴───────┐    ┌──────────┐    ┌──────┐
│  User       │───>│  RAG Chain   │───>│   LLM    │───>│Answer│
│  Question   │    │              │    │ (HF API) │    │      │
└─────────────┘    └──────────────┘    └──────────┘    └──────┘
```

**Pattern:** RAG (Retrieval-Augmented Generation)
**Data Source:** Static PDF file
**Retrieval:** Vector similarity search (FAISS)
**Interface:** CLI (terminal input loop)

### Current Architecture Problems (for MVP goal)

1. **Wrong pattern** — RAG retrieves from documents. MVP needs to query a database.
2. **Wrong data source** — PDF vs. live database.
3. **Wrong retrieval** — Vector similarity vs. SQL execution.
4. **Wrong prompt** — Java instructor vs. SQL expert.
5. **No execution** — Current system only generates text answers. MVP must execute SQL and return real data.
6. **No safety** — Executing code (SQL) on a live system requires safety layers that don't exist.

---

## 2. Target MVP Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                      │
│  POST /api/query    GET /api/health    GET /api/schema       │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                    Application Layer                          │
│                                                              │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │   Config   │  │  Session /   │  │   Error Handler      │ │
│  │  Manager   │  │   Memory     │  │                      │ │
│  └────────────┘  └──────────────┘  └──────────────────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SQL Agent Pipeline                       │   │
│  │                                                      │   │
│  │  Question ──> Schema    ──> LLM        ──> SQL       │   │
│  │               Injection     (Generate      Validator  │   │
│  │                             SQL)                      │   │
│  │                                                      │   │
│  │  SQL     ──> Executor  ──> Result      ──> Response  │   │
│  │  (valid)     (DB query)     Formatter       to User   │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                    External Services                         │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Database    │  │  LLM API     │  │  Monitoring      │  │
│  │  (PostgreSQL)│  │  (HuggingFace│  │  (LangSmith/     │  │
│  │              │  │   / OpenAI)  │  │   Sentry)        │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Key Architecture Decisions

### Decision 1: SQL Chain vs. SQL Agent

| Approach | How It Works | Pros | Cons |
|----------|-------------|------|------|
| **SQL Chain** | Fixed pipeline: question → SQL → execute → format | Predictable, easier to control, faster | Can't self-correct, limited flexibility |
| **SQL Agent** | LLM uses tools (generate SQL, execute, inspect schema) in a loop | Can self-correct bad SQL, can explore schema, handles complex queries | Slower (multiple LLM calls), harder to control, more expensive |

**Recommendation:** Start with SQL Chain for MVP. It is simpler, cheaper, and more predictable. Move to SQL Agent later when you need self-correction and complex query handling.

### Decision 2: Monolith vs. Microservices

| Approach | Description | Pros | Cons |
|----------|------------|------|------|
| **Monolith** | Single FastAPI app with all logic | Simple to deploy, debug, and maintain | Harder to scale individual components |
| **Microservices** | Separate services for API, SQL generation, execution | Independent scaling, fault isolation | Massive over-engineering for MVP |

**Recommendation:** Monolith. Absolutely. A startup MVP with < 50 users does not need microservices. The added complexity of service discovery, inter-service communication, and distributed debugging would slow development 3-5x for zero benefit at this scale.

### Decision 3: Synchronous vs. Asynchronous

| Approach | Description | Pros | Cons |
|----------|------------|------|------|
| **Sync** | Each request blocks a worker thread | Simpler code | Workers blocked during LLM calls (3-10s) |
| **Async** | Non-blocking I/O, event loop | Better throughput, workers not blocked | Slightly more complex code |

**Recommendation:** Async. FastAPI is async-native, LangChain supports `ainvoke()`, and since LLM calls take seconds, blocking a worker is wasteful. The code complexity difference is minimal:

```python
# Sync
@app.post("/query")
def query(request: QueryRequest):
    result = chain.invoke(request.question)
    return result

# Async (recommended)
@app.post("/query")
async def query(request: QueryRequest):
    result = await chain.ainvoke(request.question)
    return result
```

### Decision 4: LLM Provider Strategy

| Approach | Description | Pros | Cons |
|----------|------------|------|------|
| **Single provider** | HuggingFace only | Simple | Single point of failure, vendor lock-in |
| **Abstracted provider** | Interface that supports multiple LLMs | Can switch providers, fallback capability | More code to maintain |

**Recommendation:** Use LangChain's LLM abstraction (which the code already uses). This gives you provider switching for free. Define a factory function:

```python
def get_llm(provider: str = "huggingface"):
    if provider == "huggingface":
        return ChatHuggingFace(...)
    elif provider == "openai":
        return ChatOpenAI(...)
```

### Decision 5: State Management

| Approach | Description | Good For |
|----------|------------|---------|
| **Stateless** | No memory between requests | Simple queries, API-first |
| **Session-based** | Memory per session (in Redis/DB) | Follow-up questions, chat UX |

**Recommendation:** Session-based. The chatbot UX requires follow-up questions ("Now filter by region"). Store session in Redis or database, keyed by session ID.

---

## 4. Module Design

### Proposed Module Structure

```
ai_knowledge_assistant/
├── app.py                  # FastAPI application entry point
├── config.py               # Centralized configuration (pydantic-settings)
├── api/
│   ├── __init__.py
│   ├── routes.py           # API route definitions
│   ├── schemas.py          # Pydantic request/response models
│   └── middleware.py       # Auth, rate limiting, error handling
├── core/
│   ├── __init__.py
│   ├── llm.py              # LLM provider factory
│   ├── database.py         # SQLDatabase wrapper + connection management
│   ├── sql_chain.py        # SQL generation chain
│   ├── safety.py           # SQL validation + safety checks
│   ├── formatter.py        # Result formatting
│   └── memory.py           # Conversation memory management
├── tests/
│   ├── __init__.py
│   ├── conftest.py         # Shared fixtures
│   ├── test_sql_chain.py
│   ├── test_safety.py
│   ├── test_formatter.py
│   └── test_api.py
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

### Module Responsibilities

**`config.py`** — Single source of truth for all configuration:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    huggingface_api_token: str
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 500
    query_timeout_seconds: int = 30
    max_result_rows: int = 1000
    allowed_tables: list[str] = []  # Empty = all tables
    
    class Config:
        env_file = ".env"
```

**`database.py`** — Database connection with schema introspection:
- Wraps LangChain's `SQLDatabase`
- Connection pooling via SQLAlchemy
- Schema caching
- Table allowlisting

**`sql_chain.py`** — The core pipeline:
- Takes user question
- Injects schema
- Calls LLM to generate SQL
- Validates SQL
- Executes query
- Formats results

**`safety.py`** — Defense in depth:
- SQL parsing (only SELECT)
- Keyword blocklist
- Table/column allowlist validation
- Row limit enforcement
- Query timeout enforcement

**`formatter.py`** — Result presentation:
- Convert SQL results to natural language (via LLM)
- Convert SQL results to table format
- Handle empty results
- Handle errors

**`memory.py`** — Conversation state:
- Session creation and lookup
- Message history storage
- Context window management

---

## 5. Data Flow (Detailed)

### Happy Path: User asks a question

```
1. User sends POST /api/query
   Body: { "question": "How many orders last week?", "session_id": "abc-123" }

2. API Layer:
   - Validate request (Pydantic)
   - Check authentication
   - Check rate limit
   - Load session memory

3. SQL Generation:
   - Fetch database schema (cached)
   - Build prompt: system instructions + schema + conversation history + question
   - Call LLM → receives SQL string
   - Example: "SELECT COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL '7 days'"

4. SQL Validation:
   - Parse with sqlparse → confirm it's SELECT
   - Check referenced tables against allowlist
   - Append LIMIT if missing

5. SQL Execution:
   - Execute on database with timeout
   - Receive results: [(42,)]

6. Result Formatting:
   - Pass SQL + results to LLM (or format as table)
   - "There were 42 orders placed in the last week."

7. Response:
   - Return to user: { "answer": "...", "sql": "...", "data": [...] }
   - Update session memory with Q&A pair
```

### Error Path: Invalid SQL generated

```
3. SQL Generation:
   - LLM generates: "SELECT * FROM nonexistent_table"

4. SQL Validation:
   - Table "nonexistent_table" not found in schema
   - Reject query

5. Error Response:
   - Return: { "error": "Unable to generate a valid query. Please rephrase." }
   - Log: full error details server-side
   - Do NOT execute invalid SQL
```

---

## 6. Technology Stack Recommendation

| Layer | Technology | Reason |
|-------|-----------|--------|
| Web Framework | FastAPI | Async-native, auto-generated OpenAPI docs, Pydantic integration |
| LLM Framework | LangChain | Already in use, has SQL-specific tools, provider abstraction |
| Database ORM | SQLAlchemy (via LangChain SQLDatabase) | Already in dependencies, industry standard |
| Database | PostgreSQL | Most popular for production, excellent SQL support |
| Configuration | pydantic-settings | Already in dependencies, type-safe config from env vars |
| Testing | pytest + pytest-asyncio | Standard Python testing |
| Linting | ruff | Fast, comprehensive, replaces flake8+isort+pyupgrade |
| Type Checking | mypy | Standard Python type checker |
| Containerization | Docker | Industry standard |
| CI/CD | GitHub Actions | Free for public repos, well-integrated |

---

## 7. Extensibility Considerations

Design decisions that will make future features easier:

### Multi-Database Support
- Abstract database connection behind an interface
- Store per-database configurations
- Future: user connects their own database

### Multi-LLM Support
- Already abstracted via LangChain
- Add model routing: simple questions → cheap model, complex → expensive model

### Plugin Architecture (Future)
- Visualization generation (charts from query results)
- Scheduled queries / alerts
- Query history and favorites
- Shared queries between users
- Natural language data exploration (drill-down suggestions)

### Multi-Tenant (Future)
- Each customer has their own database connection
- Isolated sessions and memory
- Per-tenant rate limits and billing

---

## 8. Anti-Patterns to Avoid

1. **Don't use global state** — The current `main.py` uses module-level variables. Use dependency injection (FastAPI's `Depends()`).

2. **Don't hardcode configuration** — Current code hardcodes model names, chunk sizes, temperatures. Use `config.py`.

3. **Don't use print() for logging** — Use Python's `logging` module.

4. **Don't rebuild state on every request** — Cache schema, reuse connections.

5. **Don't trust LLM output** — Always validate generated SQL before execution.

6. **Don't expose internal errors** — Catch all exceptions at the API boundary.

7. **Don't over-engineer** — No microservices, no Kubernetes, no event sourcing for an MVP.

---

## Action Items

| Priority | Item | Effort |
|----------|------|--------|
| Critical | Design and implement new module structure | 4-6 hours |
| Critical | Implement SQL chain pipeline | 6-8 hours |
| Critical | Implement safety layer | 4-6 hours |
| High | Set up FastAPI with proper dependency injection | 4-6 hours |
| High | Implement centralized config with pydantic-settings | 2-3 hours |
| High | Design API request/response schemas | 2-3 hours |
| Medium | Design session/memory architecture | 3-4 hours |
| Medium | Document architecture decisions (ADRs) | 2-3 hours |
| Low | Plan extensibility hooks for future features | 2-3 hours |
