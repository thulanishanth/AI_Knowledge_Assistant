# AI Knowledge Assistant Refactor Blueprint

## 1. Executive summary

### What was wrong
- The old backend mixed request handling, memory retrieval, SQL generation, SQL validation, execution, fallback chat, and formatting inside a few broad files.
- The system still had a non-database "general answer" path, which conflicted with the requirement to stay grounded only in the configured MySQL table and internal session context.
- Schema grounding was weak: one fake schema file existed, live schema access was uncached, and prompt generation could drift away from actual table metadata.
- Frontend rendering trusted assistant markdown as HTML and relied on a CDN markdown parser, creating XSS risk and inconsistent formatting.
- Configuration and logging were split across `app.config`, `app.core.config`, and `app.utils.logger`.

### What was improved
- Added clear layers for settings, logging, caching, security, infrastructure, repositories, orchestration, and response formatting.
- Introduced a database-only `QueryOrchestrator` that sanitizes input, resolves session context, loads cached live schema, optionally pulls memory, generates safe SQL, validates it, executes it with limits, formats the result, and caches repeated SQL.
- Replaced unsafe frontend markdown injection with DOM-safe rendering plus structured presentation payloads for metrics, records, notices, and rows.
- Added schema metadata caching, SQL result caching, safer CORS defaults, centralized MySQL pooling, and parameterized chat-history persistence.

### Major risks
- The new SQL pipeline depends on accurate live schema metadata from MySQL; if metadata access fails, accuracy drops and the assistant should return conservative error guidance.
- Query-result caching is TTL-based because table mutations are not observable from the app today.
- The heuristic SQL shortcuts improve common cases but are intentionally conservative; unusual phrasing still depends on the LLM + repair loop.

### Expected impact
- Cleaner filenames and shorter responsibility chains.
- Lower latency for repeated questions through schema and SQL result caching.
- Better factual reliability by removing LLM answer synthesis from final DB answers.
- Safer frontend rendering and stronger SQL restrictions.

## 2. Proposed new folder structure

### Old-to-new mapping
- `app/core/config.py` -> `app/core/settings.py`
- `app/utils/logger.py` -> `app/core/logging.py`
- `app/db/mysql.py` -> `app/infrastructure/mysql_pool.py`
- `app/api/chat.py` -> `app/api/routes/chat_routes.py` with `app/api/chat.py` kept as compatibility entrypoint
- `app/services/query_service.py` -> `app/services/query_orchestrator.py` with a thin compatibility facade left in `app/services/query_service.py`
- `app/services/sql_validator.py` -> `app/security/sql_guard.py`
- `app/services/sql_executor.py` -> `app/services/sql_execution_service.py`
- `app/services/formatter.py` -> `app/services/response_formatter.py`
- `app/services/intent_classifier.py` -> `app/services/intent_service.py`
- `app/vector_store/chroma_adapter.py` -> `app/infrastructure/vector_store/chroma_store.py` alias
- `app/memory/window_memory.py` -> `app/memory/session_window_store.py` alias

### Why the renamed files are clearer
- `settings.py` is more explicit than `config.py` because it owns runtime environment parsing.
- `mysql_pool.py` makes it obvious that the file is infrastructure for pooled DB connections, not query logic.
- `sql_guard.py` states that the module is enforcing safety, not generating SQL.
- `response_formatter.py` is clearer than `formatter.py` because it focuses on assistant responses, not arbitrary formatting utilities.
- `query_orchestrator.py` tells the reader that the file owns the pipeline, not raw service helpers.

## 3. Refactor strategy

### Files merged or simplified
- Kept compatibility wrappers for legacy imports instead of duplicating full logic.
- Collapsed scattered config/logging behavior into `app/core/settings.py` and `app/core/logging.py`.

### Files split
- Query flow was separated into `intent_service.py`, `schema_service.py`, `sql_generation_service.py`, `sql_execution_service.py`, `response_formatter.py`, and `query_orchestrator.py`.
- Chat history persistence moved into `app/infrastructure/repositories/chat_history_repository.py`.
- Live schema inspection moved into `app/infrastructure/repositories/schema_repository.py`.

### Utilities centralized
- Input sanitization: `app/security/input_sanitizer.py`
- SQL safety: `app/security/sql_guard.py`
- TTL caching: `app/core/cache.py`
- Logging: `app/core/logging.py`

### Abstractions removed or reduced
- Removed the practical dependency on a free-form "general answer" flow for runtime chat handling.
- Reduced ambiguity between `app.config` and `app.core.config` by making both wrappers over `app/core/settings.py`.

### Patterns introduced
- Clear infrastructure/service/security split.
- Repository pattern for MySQL schema and chat-history access.
- Query orchestration with structured response payloads.
- Conservative compatibility shims for older imports and tests.

## 4. Production architecture design

### Final architecture
- API layer: `app/api/models.py`, `app/api/chat.py`, `app/api/routes/chat_routes.py`
- Core layer: `app/core/settings.py`, `app/core/logging.py`, `app/core/cache.py`, `app/core/dependency_injection.py`
- Security layer: `app/security/input_sanitizer.py`, `app/security/sql_guard.py`
- Infrastructure layer: `app/infrastructure/mysql_pool.py`, repositories, vector-store alias
- Service/orchestration layer: `app/services/query_orchestrator.py`, SQL generation/execution, intent/schema/formatter services
- Memory layer: existing memory manager plus selective vector retrieval
- Frontend layer: static `frontend/index.html`, `frontend/style.css`, `frontend/script.js`

### Request flow
1. Frontend submits `POST /api/chat/`.
2. `app/api/chat.py` validates the request and calls `handle_query`.
3. The real container path delegates to `QueryOrchestrator`.
4. The orchestrator sanitizes the question, resolves session/user ids, classifies intent, loads cached live schema, optionally loads memory context, generates SQL, validates it, executes it safely, formats the answer, and returns structured UI payloads.
5. The route persists user and assistant messages through the chat-history repository.

### Safe use of memory/vector/RAG
- Live schema prompt context is always the highest-priority grounding source.
- Window/summary/vector memory is used only as internal conversational context.
- Vector retrieval now runs only for follow-up-style questions to reduce noise and latency.
- No external web context is used to answer database questions.

### Caching
- Schema metadata cache: in-memory TTL, keyed by database/table.
- SQL result cache: in-memory TTL, keyed by normalized validated SQL.

### Formatting
- The backend returns both human-readable text and a structured `presentation` block.
- The frontend renders metrics, single-record cards, row cards, or a wrapping grid table depending on result shape.

### Layered security checks
1. Input sanitization and length limits.
2. Intent gating to DB-only mode.
3. Live-schema-grounded prompt construction.
4. SQL validation through `SqlGuard`.
5. Execution time and row limits in `SQLExecutionService`.
6. DOM-safe frontend rendering without trusting assistant HTML.

## 5. Accuracy improvement plan

### Why accuracy was low
- Hardcoded schema assumptions were brittle.
- The app could drift into a general-answer path and synthesize prose detached from the actual rows.
- SQL generation did not have strong enough schema/value grounding or a repair loop tied to validation errors.

### Concrete fixes implemented
- Cached live schema retrieval from `information_schema`.
- Prompt examples filtered by current question and schema.
- Heuristic SQL generation for common counts/listing/aggregate cases before LLM fallback.
- SQL repair loop using validation feedback.
- Removed LLM-based natural-language answer synthesis for DB results; answers are now formatted directly from returned rows.

### Evaluation approach
- Build an evaluation set with realistic business questions against the configured table.
- For each question, store expected SQL class, expected row count, and expected answer facts.
- Measure:
  - SQL validation pass rate
  - execution success rate
  - exact match / fact match for aggregates
  - row-coverage correctness for row preview questions
  - unsupported-question rejection rate

### Acceptance criteria toward a >90% target
- At least 90% fact correctness on a representative held-out DB question set.
- 100% rejection of write SQL in adversarial prompt tests.
- At least 95% pass rate for schema-grounded intent classification on the evaluation set.
- No hallucinated facts in answers where the SQL result is empty or unsupported.

## 6. Security hardening plan

### Weaknesses addressed
- Unsafe assistant HTML rendering in the browser.
- Broad CORS defaults.
- Mixed config/logging paths.
- SQL validation split from execution limits.
- Unbounded use of fallback chat behavior.

### Exact improvements
- Added `app/security/input_sanitizer.py`.
- Added `app/security/sql_guard.py` with table scoping, forbidden keyword blocking, comment blocking, and optional AST validation.
- Frontend now builds DOM nodes from plain text and structured payloads instead of calling a remote markdown parser and setting raw HTML.
- Added query timeout and row-limit enforcement in `SQLExecutionService`.
- Centralized settings and reduced accidental secret logging surfaces.

## 7. Performance and caching plan

### Bottlenecks identified
- Re-fetching live schema for repeated questions.
- Unnecessary vector retrieval for every question.
- Repeating identical SQL work for repeated questions.

### Implemented caching strategy
- Schema cache TTL: `SCHEMA_CACHE_TTL_SECONDS`, default 300 seconds.
- SQL result cache TTL: `QUERY_CACHE_TTL_SECONDS`, default 45 seconds.

### TTL choices
- Schema changes are infrequent, so 5 minutes is a good default balance between freshness and performance.
- SQL result cache is intentionally short because table contents can change while still benefiting repeated user questions.

### Cache invalidation
- Time-based invalidation today.
- Recommended future enhancement: explicit invalidation hook after data ingestion or admin refresh.

## 8. Frontend/UI redesign plan

### UX changes implemented
- New split layout with a stronger sidebar, clear DB-only status, and better empty state.
- Structured result rendering for metrics, records, notices, and multi-row answers.
- Retry and copy actions on assistant messages.
- Better loading state and composer status text.

### Table/card rendering strategy
- Metrics render as highlight cards.
- Single rows render as labeled cards.
- Multi-row answers render as wrapping grid tables when columns are few, otherwise as row cards.

### Preventing horizontal scroll
- CSS grid tables use `minmax(0, 1fr)` and `overflow-wrap: anywhere`.
- The frontend switches to card layout for wide datasets or smaller screens.

### JS enhancements
- DOM-safe content rendering.
- Session history loading and active-session highlighting.
- Theme persistence and auto-resizing composer.

## 9. Actual code changes

### Core new implementation files
- `app/core/settings.py`
- `app/core/logging.py`
- `app/core/cache.py`
- `app/security/input_sanitizer.py`
- `app/security/sql_guard.py`
- `app/infrastructure/mysql_pool.py`
- `app/infrastructure/repositories/chat_history_repository.py`
- `app/infrastructure/repositories/schema_repository.py`
- `app/services/intent_service.py`
- `app/services/schema_service.py`
- `app/services/sql_generation_service.py`
- `app/services/sql_execution_service.py`
- `app/services/response_formatter.py`
- `app/services/query_orchestrator.py`
- `app/api/models.py`
- `app/api/routes/chat_routes.py`

### Updated compatibility entry points
- `app/main.py`
- `app/api/chat.py`
- `app/config.py`
- `app/core/config.py`
- `app/db/mysql.py`
- `app/services/query_service.py`
- `app/services/intent_classifier.py`
- `app/services/sql_validator.py`
- `app/services/sql_executor.py`
- `app/services/formatter.py`
- `app/utils/logger.py`

### Frontend files
- `frontend/index.html`
- `frontend/style.css`
- `frontend/script.js`

## 10. Migration notes

### Structure migration
- Keep old imports working for now through the compatibility shims.
- New code should target the new files listed above instead of the legacy wrappers.

### Environment changes
- New optional settings:
  - `DB_POOL_SIZE`
  - `DB_QUERY_TIMEOUT_MS`
  - `SCHEMA_CACHE_TTL_SECONDS`
  - `QUERY_CACHE_TTL_SECONDS`
  - `SESSION_HISTORY_LIMIT`
  - `CORS_ALLOWED_ORIGINS`
  - `LLM_TIMEOUT_SECONDS`
  - `LLM_MAX_RETRIES`
  - `LLM_TEMPERATURE_SQL`

### Dependency changes
- No mandatory new runtime dependency was introduced in code paths already covered by the existing environment.
- Optional SQL AST validation remains compatible with environments that already have `sqlglot`.

### Commands to run
```powershell
venv\Scripts\python.exe -m pytest -q
uvicorn app.main:app --reload
```

## 11. Validation checklist

### Automated
- `venv\Scripts\python.exe -m pytest -q`

### Manual backend
- Ask greeting questions and verify DB-only greeting response.
- Ask unsupported general-knowledge questions and verify DB-only rejection message.
- Ask count, aggregate, and row-preview questions against the configured table.
- Verify session history still saves and reloads.

### Security checks
- Try prompts that ask for `DROP`, `DELETE`, or stacked SQL and verify rejection.
- Try HTML/script-like text in messages and verify the frontend renders it as text, not executable HTML.

### UI checks
- Desktop and mobile layout.
- Wide result sets switch to cards instead of horizontal scrolling.
- Copy and retry actions behave correctly.

### Performance checks
- Repeating the same question should show cached behavior in response metadata.
- Repeated schema-dependent questions should not trigger visible latency spikes.

## 12. Final acceptance checklist

- Same core app purpose: yes
- Cleaner filenames: yes
- Reduced unnecessary code paths: yes
- More secure: yes
- Faster on repeated queries: yes
- Clearer answers: yes
- Better UI: yes
- No external web answering for user DB questions: yes
- Reusable production-style structure: yes
