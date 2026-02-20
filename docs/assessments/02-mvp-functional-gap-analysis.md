# MVP Functional Gap Analysis

**Project:** AI Knowledge Assistant (SQL Query Chatbot)
**Date:** 2026-02-20
**MVP Readiness:** ~10-15%

---

## Executive Summary

The MVP goal is a chatbot where users ask natural-language questions, the system generates SQL queries, executes them on a real database, and returns human-readable results. The current codebase is a PDF-based Java Q&A RAG pipeline. Only the LangChain scaffolding and LLM integration pattern are reusable. Nearly every core feature of the actual product must be built from scratch.

---

## MVP Definition

**Core User Flow:**
1. User types a natural-language question (e.g., "Show me all orders from last week")
2. System understands the database schema
3. System generates a valid SQL query
4. System executes the SQL on a real database
5. System returns the results in a human-readable format
6. User can ask follow-up questions

---

## What Exists Today

| Component | File | What It Does |
|-----------|------|-------------|
| PDF Loader | `loader.py` | Loads a Java interview PDF |
| Text Splitter | `splitter.py` | Splits PDF text into chunks |
| Embeddings | `embeddings.py` | HuggingFace sentence embeddings |
| Vector Store | `vector_store.py` | FAISS in-memory vector store |
| Retriever | `retriever.py` | Similarity search over vectors |
| LLM | `llm.py` | Qwen2.5-7B via HuggingFace API |
| Prompt | `prompt.py` | "You are an expert Java instructor" |
| RAG Chain | `rag_chain.py` | question → retrieve chunks → LLM → text answer |
| CLI Interface | `main.py` | `while True: input()` loop |

**Current pipeline:**
```
User Question → Embed → Search PDF chunks → Inject as context → LLM → Text Answer
```

**Required pipeline:**
```
User Question → Inject DB Schema → LLM generates SQL → Validate SQL → Execute on DB → Format Results → Answer
```

These are fundamentally different architectures. The current pipeline is RAG (Retrieval-Augmented Generation). The MVP needs a SQL Agent pattern.

---

## Component-by-Component Analysis

### Reusable Components

#### 1. LLM Integration (`llm.py`) — Reuse Level: HIGH
- HuggingFace Endpoint setup is directly reusable
- May need a stronger model — Qwen2.5-7B-Instruct can handle simple SQL but may struggle with complex multi-table JOINs, subqueries, or window functions
- Consider: CodeLlama, SQLCoder, or GPT-4-class models for better SQL accuracy
- Temperature of 0.1 is appropriate for SQL (deterministic output wanted)
- `max_new_tokens=250` may be too low for complex queries — should be configurable

#### 2. LangChain Framework (`rag_chain.py`) — Reuse Level: MEDIUM
- Chain composition pattern (pipe operator, RunnablePassthrough) is reusable knowledge
- The actual chain logic needs complete replacement
- LangChain provides purpose-built SQL tools that should be used instead

#### 3. Dependencies (`requirements.txt`) — Reuse Level: MEDIUM
- **Keep:** langchain, langchain-core, langchain-community, langchain-huggingface, SQLAlchemy (already listed!), python-dotenv, pydantic, pydantic-settings
- **Remove:** faiss-cpu, pypdf, sentence-transformers, torch, transformers, scikit-learn, scipy (massive packages not needed for SQL chatbot if using API-based LLM)
- **Add:** fastapi, uvicorn, langchain-experimental (for SQL chains), psycopg2/pymysql (DB drivers), sqlparse

#### 4. Environment Pattern (`dotenv`) — Reuse Level: HIGH
- Same pattern, just more env vars (DATABASE_URL, etc.)

### Components to Remove

| Component | File | Why Not Needed |
|-----------|------|---------------|
| PDF Loader | `loader.py` | MVP reads from a database, not PDFs |
| Text Splitter | `splitter.py` | No documents to chunk |
| Embeddings | `embeddings.py` | No vector similarity search needed |
| Vector Store | `vector_store.py` | No embeddings to store |
| Retriever | `retriever.py` | Retrieval is done via SQL, not vectors |
| Java Prompt | `prompt.py` | Wrong domain entirely |
| Sample PDF | `data/*.pdf` | Not relevant to SQL chatbot |

### Components to Build (MISSING)

---

#### Feature 1: Database Connection Layer
**Status:** Does not exist
**Priority:** Critical
**Estimated Effort:** 4-6 hours

**What it needs to do:**
- Connect to a real database (PostgreSQL, MySQL, or SQLite for dev)
- Manage connection lifecycle and pooling
- Support multiple database engines via connection string
- Load connection string from environment variable

**How to build it:**
LangChain provides `SQLDatabase` wrapper that handles all of this:
```python
from langchain_community.utilities import SQLDatabase

db = SQLDatabase.from_uri(os.getenv("DATABASE_URL"))
```

**Key decisions:**
- Which database engine? PostgreSQL recommended for production
- Connection pooling strategy (SQLAlchemy handles this)
- Read-only connection for safety (use a read-only DB user)

---

#### Feature 2: Database Schema Introspection
**Status:** Does not exist
**Priority:** Critical
**Estimated Effort:** 2-3 hours

**What it needs to do:**
- Automatically extract table names, column names, data types, and relationships
- Format schema information for injection into LLM prompt
- Optionally limit which tables the chatbot can access
- Cache schema to avoid repeated introspection

**How to build it:**
```python
# LangChain's SQLDatabase does this automatically
schema_info = db.get_table_info()  # Returns CREATE TABLE statements
table_names = db.get_usable_table_names()
```

**Key decisions:**
- Expose all tables or whitelist specific ones?
- Include sample data rows in schema context? (Helps LLM accuracy)
- How often to refresh schema cache?

---

#### Feature 3: Natural Language to SQL Prompt
**Status:** Wrong domain (currently Java Q&A)
**Priority:** Critical
**Estimated Effort:** 4-8 hours (including prompt engineering/testing)

**What it needs to do:**
- System prompt that instructs LLM to convert questions to SQL
- Inject database schema dynamically
- Include SQL dialect specification (PostgreSQL vs MySQL syntax differs)
- Include safety rules (SELECT only, no destructive queries)
- Handle ambiguous questions gracefully

**Example prompt structure:**
```
You are a SQL expert. Given the database schema below and the user's question,
generate a syntactically correct {dialect} query.

IMPORTANT RULES:
- Only generate SELECT queries
- Never generate INSERT, UPDATE, DELETE, DROP, ALTER, or TRUNCATE
- Only use tables and columns that exist in the schema
- If the question cannot be answered with the given schema, say so
- Return ONLY the SQL query, no explanations

Database Schema:
{schema}

User Question: {question}
SQL Query:
```

**Key decisions:**
- Include few-shot examples in prompt? (Improves accuracy significantly)
- How to handle ambiguous column names across tables?
- Include sample data rows for context?

---

#### Feature 4: SQL Generation Chain
**Status:** Does not exist (current chain is RAG, not SQL)
**Priority:** Critical
**Estimated Effort:** 4-6 hours

**What it needs to do:**
- Take user question + schema → generate SQL via LLM
- Validate the generated SQL before execution
- Retry on invalid SQL (with error feedback to LLM)
- Support conversation context for follow-up queries

**How to build it:**
LangChain provides two approaches:

**Option A: SQL Chain (simpler)**
```python
from langchain.chains import create_sql_query_chain
chain = create_sql_query_chain(llm, db)
sql_query = chain.invoke({"question": "How many users signed up last week?"})
```

**Option B: SQL Agent (more powerful)**
```python
from langchain_community.agent_toolkits import create_sql_agent
agent = create_sql_agent(llm, db=db, agent_type="openai-tools")
result = agent.invoke({"input": "How many users signed up last week?"})
```

**Key decisions:**
- Chain vs Agent? Agent is more flexible (can self-correct, explore schema) but harder to control
- Single-step (generate + execute) or multi-step (generate → validate → execute → explain)?

---

#### Feature 5: SQL Execution Engine
**Status:** Does not exist
**Priority:** Critical
**Estimated Effort:** 3-4 hours

**What it needs to do:**
- Execute the generated SQL query against the database
- Return results as structured data
- Handle execution errors (syntax errors, timeouts, permission denied)
- Enforce query timeout limits
- Enforce row count limits (prevent `SELECT * FROM huge_table`)

**How to build it:**
```python
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
execute_tool = QuerySQLDataBaseTool(db=db)
result = execute_tool.invoke(sql_query)
```

**Key decisions:**
- Query timeout (recommend: 30 seconds max)
- Row limit (recommend: 1000 rows max, configurable)
- How to handle large result sets (pagination?)

---

#### Feature 6: SQL Safety / Validation Layer
**Status:** Does not exist
**Priority:** Critical (NON-NEGOTIABLE for any product executing SQL)
**Estimated Effort:** 4-6 hours

**What it needs to do:**
- Parse and validate SQL before execution
- Block all non-SELECT statements (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, GRANT, etc.)
- Detect SQL injection patterns
- Enforce query complexity limits
- Log all executed queries for audit trail

**Implementation layers (defense in depth):**

1. **Database-level:** Use a read-only database user (most important single defense)
2. **SQL parsing:** Use `sqlparse` to parse and validate query type
3. **Regex blocklist:** Block dangerous keywords as a secondary check
4. **LLM prompt:** Instruct LLM to only generate SELECT (weakest defense, not sufficient alone)
5. **Query allowlist:** Optionally restrict to specific tables/columns

```python
import sqlparse

def validate_sql(query: str) -> bool:
    parsed = sqlparse.parse(query)
    for statement in parsed:
        if statement.get_type() != "SELECT":
            raise ValueError(f"Only SELECT queries allowed, got: {statement.get_type()}")
    return True
```

**Key decisions:**
- How strict? (SELECT-only vs. also allow CTEs, EXPLAIN, etc.)
- What to do on violation? (Return error message, log alert, block user?)
- Audit logging requirements?

---

#### Feature 7: Result Formatting
**Status:** Does not exist
**Priority:** High
**Estimated Effort:** 3-4 hours

**What it needs to do:**
- Convert raw SQL results (rows/tuples) into human-readable answers
- Support both natural language summaries and tabular display
- Handle empty results gracefully
- Handle very large result sets (summarize rather than dump)

**Two approaches:**

**Approach A: LLM-based (recommended for chatbot UX)**
Pass results back to LLM for natural language summarization:
```
Given the SQL query and its results, provide a clear natural language answer.

SQL: SELECT COUNT(*) FROM users WHERE created_at > '2026-02-13'
Results: [(42,)]
Answer: 42 users signed up in the last week.
```

**Approach B: Structured formatting**
Return data as formatted table (better for data analysis use cases).

**Key decisions:**
- Natural language or structured table or both?
- How to handle result sets with 100+ rows?
- Include the SQL query in the response to the user?

---

#### Feature 8: Conversation Memory
**Status:** Does not exist
**Priority:** High
**Estimated Effort:** 3-4 hours

**What it needs to do:**
- Remember previous questions and answers in a session
- Support follow-up questions ("Now filter that by region")
- Maintain memory per user/session (not global)
- Limit memory window to prevent context overflow

**How to build it:**
```python
from langchain.memory import ConversationBufferWindowMemory
memory = ConversationBufferWindowMemory(k=10)  # Last 10 exchanges
```

**Key decisions:**
- Memory storage: in-memory (lost on restart) vs. Redis/DB-backed (persistent)
- Memory window size (LLM context length is limited)
- Per-session or per-user?
- Include previous SQL queries in memory or just Q&A?

---

#### Feature 9: Web API Layer
**Status:** Does not exist (only CLI `while True` loop)
**Priority:** High
**Estimated Effort:** 6-8 hours

**What it needs to do:**
- REST API for the chatbot (FastAPI recommended)
- Endpoints:
  - `POST /api/query` — accept question, return answer + SQL + results
  - `GET /api/health` — health check
  - `GET /api/schema` — return available tables (optional)
  - `POST /api/sessions` — create a new conversation session
- Request/response validation with Pydantic models
- CORS configuration for frontend access
- Error response formatting

**Key decisions:**
- Sync or async? (async recommended for I/O-heavy workload)
- Authentication required for MVP? (At minimum: API key)
- Rate limiting? (Recommended to prevent abuse)
- WebSocket for streaming responses? (Nice-to-have, not MVP)

---

#### Feature 10: User Interface (Optional for MVP)
**Status:** Does not exist
**Priority:** Medium (needed for demos/investors, not for API-first MVP)
**Estimated Effort:** 4-8 hours

**Options:**
- **Streamlit** — Fastest to build, good for demos, limited customization
- **Gradio** — Similar to Streamlit, ML-focused
- **React/Next.js** — Most professional, most effort, best for production

**Minimum UI features:**
- Chat interface with message history
- Display both natural language answer and SQL query
- Display results in a table
- New conversation button
- Loading indicators during query processing

---

## Functional Readiness Scorecard

| Feature | Completeness | Priority |
|---------|-------------|----------|
| Database Connection | 0% | Critical |
| Schema Introspection | 0% | Critical |
| NL-to-SQL Prompt | 0% | Critical |
| SQL Generation Chain | 0% | Critical |
| SQL Execution | 0% | Critical |
| SQL Safety Layer | 0% | Critical |
| Result Formatting | 0% | High |
| Conversation Memory | 0% | High |
| Web API | 0% | High |
| LLM Integration | 60% | Done (adapt) |
| LangChain Scaffolding | 30% | Done (adapt) |
| User Interface | 0% | Medium |

---

## File-Level Change Plan

| Action | File | Description |
|--------|------|-------------|
| KEEP + MODIFY | `llm.py` | Keep HuggingFace setup, make model configurable |
| REWRITE | `prompt.py` | Replace Java prompt with SQL generation prompt |
| REWRITE | `rag_chain.py` → `sql_chain.py` | Replace RAG chain with SQL agent/chain |
| REWRITE | `main.py` | Replace CLI with FastAPI app bootstrap |
| DELETE | `loader.py` | Not needed for SQL chatbot |
| DELETE | `splitter.py` | Not needed for SQL chatbot |
| DELETE | `embeddings.py` | Not needed for SQL chatbot |
| DELETE | `vector_store.py` | Not needed for SQL chatbot |
| DELETE | `retriever.py` | Not needed for SQL chatbot |
| DELETE | `data/*.pdf` | Not relevant |
| CREATE | `database.py` | SQLDatabase wrapper + connection management |
| CREATE | `schema.py` | Schema introspection + caching |
| CREATE | `safety.py` | SQL validation + safety checks |
| CREATE | `formatter.py` | Result formatting |
| CREATE | `memory.py` | Conversation memory management |
| CREATE | `api.py` | FastAPI application |
| CREATE | `config.py` | Centralized configuration |
| MODIFY | `requirements.txt` | Add FastAPI, DB drivers; remove FAISS, torch, etc. |

---

## Estimated Build Timeline

| Phase | Scope | Effort |
|-------|-------|--------|
| Phase 1 | Core SQL Pipeline (Features 1-6) | 3-5 days |
| Phase 2 | API Layer (Feature 9) | 1-2 days |
| Phase 3 | Memory + Polish (Features 7-8) | 1-2 days |
| Phase 4 | UI (Feature 10, optional) | 1-3 days |
| **Total** | **Deployable MVP** | **6-12 days** |
