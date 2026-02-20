# Dependency Health Assessment

**Project:** AI Knowledge Assistant (SQL Query Chatbot)
**Date:** 2026-02-20
**Total Dependencies:** 81 pinned packages in requirements.txt

---

## Executive Summary

The project has 81 pinned dependencies, many of which are unnecessary for the actual MVP (SQL chatbot). The dependency list appears to be a `pip freeze` dump rather than a curated list of direct dependencies. This inflates the attack surface, increases Docker image size, and makes maintenance harder. Key actions: remove unnecessary dependencies, separate direct from transitive dependencies, audit for vulnerabilities, and check license compatibility.

---

## 1. Dependency Inventory Analysis

### Direct Dependencies (packages the code actually imports)

| Package | Used In | Needed for SQL MVP? | Notes |
|---------|---------|---------------------|-------|
| `langchain==1.2.8` | rag_chain.py | YES | Core framework |
| `langchain-core==1.2.9` | prompt.py, rag_chain.py | YES | Core abstractions |
| `langchain-community==0.4.1` | loader.py, vector_store.py | YES | SQL tools are here |
| `langchain-huggingface==1.2.0` | llm.py, embeddings.py | YES (if keeping HF) | LLM integration |
| `langchain-text-splitters==1.1.0` | splitter.py | NO | No text splitting in SQL chatbot |
| `python-dotenv==1.2.1` | llm.py, embeddings.py | YES | Env var loading |
| `pypdf==6.6.2` | (via PyPDFLoader) | NO | No PDF loading in SQL chatbot |
| `faiss-cpu==1.13.2` | vector_store.py | NO | No vector store in SQL chatbot |
| `SQLAlchemy==2.0.46` | (listed but unused) | YES | Database connection |
| `pydantic==2.12.5` | (transitive) | YES | Request validation |
| `pydantic-settings==2.12.0` | (transitive) | YES | Config management |

### Heavy Dependencies That Should Be REMOVED

| Package | Size (installed) | Why Not Needed |
|---------|-----------------|---------------|
| `torch==2.10.0` | ~2-5 GB | Only for local model inference. MVP uses API-based LLM. |
| `transformers==4.57.6` | ~500 MB | Same — only for local inference |
| `sentence-transformers==5.2.2` | ~200 MB | Only for embeddings. No embeddings in SQL chatbot. |
| `scikit-learn==1.8.0` | ~150 MB | Transitive dep of sentence-transformers |
| `scipy==1.17.0` | ~100 MB | Transitive dep of scikit-learn |
| `faiss-cpu==1.13.2` | ~50 MB | Vector store, not needed |
| `numpy==2.4.2` | ~50 MB | Transitive dep, may still be needed by SQLAlchemy |
| `safetensors==0.7.0` | ~10 MB | Model file format, not needed |
| `tokenizers==0.22.2` | ~30 MB | Transitive dep of transformers |
| `sympy==1.14.0` | ~40 MB | Transitive dep of torch |
| `networkx==3.6.1` | ~10 MB | Transitive dep of torch |
| `pypdf==6.6.2` | ~5 MB | PDF loading, not needed |

**Total estimated savings: 3-6 GB in Docker image size, 1-3 GB in runtime memory.**

### Packages That Should Be ADDED

| Package | Purpose | Why |
|---------|---------|-----|
| `fastapi` | Web framework | API layer |
| `uvicorn` | ASGI server | Running FastAPI |
| `psycopg2-binary` or `asyncpg` | PostgreSQL driver | Database connectivity |
| `sqlparse` | SQL parsing | SQL validation/safety |
| `langchain-experimental` | SQL chains | create_sql_query_chain |
| `pytest` | Testing | Test framework |
| `pytest-cov` | Coverage | Test coverage |
| `pytest-asyncio` | Async tests | Testing async endpoints |
| `ruff` | Linting | Code quality |
| `mypy` | Type checking | Type safety |
| `pre-commit` | Git hooks | Code quality enforcement |
| `pip-audit` | Vulnerability scanning | Security |
| `httpx` | Already present | Test client for FastAPI |

### Packages of Uncertain Necessity

| Package | In requirements.txt | Analysis |
|---------|-------------------|----------|
| `langgraph==1.0.7` | Yes | Not imported anywhere. Remove unless planning to use LangGraph. |
| `langgraph-checkpoint==4.0.0` | Yes | Not imported. Remove. |
| `langgraph-prebuilt==1.0.7` | Yes | Not imported. Remove. |
| `langgraph-sdk==0.3.3` | Yes | Not imported. Remove. |
| `langsmith==0.6.8` | Yes | Useful for monitoring. Keep if using LangSmith for tracing. |
| `typer-slim==0.21.1` | Yes | CLI framework. Not imported. Remove. |
| `langchain-classic==1.0.1` | Yes | Legacy compatibility. Likely not needed. Remove. |

---

## 2. requirements.txt Structure Problems

### Problem: `pip freeze` Dump

The current `requirements.txt` contains 81 packages. This is a `pip freeze` output, which includes every transitive dependency. This causes:

1. **Maintenance burden** — Must manually update 81 version pins
2. **Confusion** — Can't tell which packages are direct dependencies vs. transitive
3. **Conflicts** — Pinning transitive dependencies can cause version conflicts on updates
4. **Review difficulty** — 81 packages to audit for security/licensing

### Solution: Separate Direct and Transitive Dependencies

**Option A: `pyproject.toml` with loose pins (recommended)**
```toml
[project]
dependencies = [
    "langchain>=1.2,<2.0",
    "langchain-core>=1.2,<2.0",
    "langchain-community>=0.4,<1.0",
    "langchain-huggingface>=1.2,<2.0",
    "fastapi>=0.115,<1.0",
    "uvicorn>=0.32,<1.0",
    "sqlalchemy>=2.0,<3.0",
    "psycopg2-binary>=2.9,<3.0",
    "sqlparse>=0.5,<1.0",
    "python-dotenv>=1.0,<2.0",
    "pydantic-settings>=2.0,<3.0",
]
```

**Option B: Keep `requirements.txt` but split into files**
```
requirements/
├── base.txt          # Direct production dependencies (10-15 packages)
├── dev.txt           # Development tools (pytest, ruff, mypy, pre-commit)
├── constraints.txt   # Pinned versions from pip freeze (for reproducibility)
```

---

## 3. Vulnerability Assessment

### Known Risk Areas

These packages have historically had security vulnerabilities and should be monitored closely:

| Package | Risk Area | Historical Issues |
|---------|-----------|------------------|
| `SQLAlchemy` | SQL injection | Rare, but misuse can enable injection |
| `Jinja2` | Server-Side Template Injection (SSTI) | CVE history, but mostly via unsafe usage |
| `PyYAML` | Deserialization attacks | `yaml.load()` without SafeLoader is dangerous |
| `requests` | SSRF, certificate validation | Generally well-maintained |
| `urllib3` | SSRF, TLS issues | Generally well-maintained |
| `torch` | Supply chain risk | Very large package, large attack surface |
| `httpx` | SSRF | Generally well-maintained |

### Required: Automated Vulnerability Scanning

```bash
# One-time scan
pip install pip-audit
pip-audit -r requirements.txt

# Also scan with safety
pip install safety
safety check -r requirements.txt
```

**Add to CI/CD pipeline:**
```yaml
- name: Security audit
  run: |
    pip install pip-audit
    pip-audit -r requirements.txt --fail-on-severity high
```

**Add to pre-commit:**
```yaml
- repo: https://github.com/pypa/pip-audit
  hooks:
    - id: pip-audit
```

### Required: Dependabot or Renovate

Set up automated dependency update PRs:

`.github/dependabot.yml`:
```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 10
```

---

## 4. License Audit

### Why This Matters
For a commercial startup product, every dependency's license must be compatible with your distribution model. Some licenses (AGPL, GPL) have copyleft requirements that could force you to open-source your entire product.

### License Analysis of Key Dependencies

| Package | License | Commercial Safe? | Notes |
|---------|---------|-----------------|-------|
| langchain | MIT | YES | Permissive |
| langchain-core | MIT | YES | Permissive |
| langchain-community | MIT | YES | Permissive |
| SQLAlchemy | MIT | YES | Permissive |
| FastAPI | MIT | YES | Permissive |
| pydantic | MIT | YES | Permissive |
| python-dotenv | BSD-3 | YES | Permissive |
| torch | BSD-3 | YES | Permissive (but remove it anyway) |
| transformers | Apache-2.0 | YES | Permissive |
| requests | Apache-2.0 | YES | Permissive |
| psycopg2 | LGPL | CAUTION | LGPL allows use but not modification without sharing |
| faiss-cpu | MIT | YES | Permissive (but remove it) |

### Action Items for License Compliance

1. **Run license audit tool:**
   ```bash
   pip install pip-licenses
   pip-licenses --format=table --with-urls
   ```

2. **Flag any GPL/AGPL/SSPL dependencies** — These may require open-sourcing your code

3. **Consider `psycopg2-binary` vs `psycopg2`** — Both LGPL. Using as a library (not modifying) is generally fine under LGPL, but consult legal counsel.

4. **Alternative:** Use `asyncpg` (Apache-2.0) instead of `psycopg2` for fully permissive license

5. **Document all licenses** in a `THIRD_PARTY_LICENSES.md` or `NOTICES` file

---

## 5. Maintenance Status of Key Dependencies

| Package | Last Release | Active? | Bus Factor Risk |
|---------|-------------|---------|----------------|
| langchain | Actively maintained | YES | Low (large team) |
| FastAPI | Actively maintained | YES | Medium (Tiangolo is primary) |
| SQLAlchemy | Actively maintained | YES | Low (established project) |
| pydantic | Actively maintained | YES | Low (well-funded) |
| torch | Actively maintained | YES | Low (Meta-backed) |
| python-dotenv | Actively maintained | YES | Medium |
| pypdf | Actively maintained | YES | Medium |

### LangChain Version Considerations

LangChain has a very fast release cycle and frequently introduces breaking changes. The codebase uses:
- `langchain==1.2.8`
- `langchain-core==1.2.9`
- `langchain-community==0.4.1`

**Risks:**
- API changes between minor versions
- Deprecation of features
- Documentation may not match pinned version

**Mitigation:**
- Pin versions in requirements (already done)
- Test after every dependency update
- Follow LangChain migration guides for major updates

---

## 6. Docker Image Impact

### Current (with all 81 dependencies)

| Component | Estimated Size |
|-----------|---------------|
| Python 3.11-slim base | ~150 MB |
| torch + transformers | ~3-5 GB |
| Other ML packages | ~500 MB |
| LangChain + utilities | ~100 MB |
| **Total image size** | **~4-6 GB** |

### After Cleanup (MVP dependencies only)

| Component | Estimated Size |
|-----------|---------------|
| Python 3.11-slim base | ~150 MB |
| LangChain + SQLAlchemy | ~100 MB |
| FastAPI + uvicorn | ~20 MB |
| PostgreSQL driver | ~5 MB |
| Other utilities | ~50 MB |
| **Total image size** | **~350-500 MB** |

**Savings: ~4-5 GB (80-90% reduction)**

This directly impacts:
- Docker build time (minutes vs. seconds)
- Container registry storage costs
- Deployment time (pull speed)
- Server disk usage
- Container startup time

---

## 7. Recommended Cleaned-Up Dependencies

### Production Dependencies (`requirements.txt` or `pyproject.toml`)

```
langchain>=1.2,<2.0
langchain-core>=1.2,<2.0
langchain-community>=0.4,<1.0
langchain-huggingface>=1.2,<2.0
langchain-experimental>=0.3,<1.0
fastapi>=0.115,<1.0
uvicorn>=0.32,<1.0
sqlalchemy>=2.0,<3.0
psycopg2-binary>=2.9,<3.0
sqlparse>=0.5,<1.0
python-dotenv>=1.0,<2.0
pydantic>=2.0,<3.0
pydantic-settings>=2.0,<3.0
langsmith>=0.6,<1.0
```

**Count: ~14 direct dependencies (down from 81)**

### Development Dependencies

```
pytest>=8.0
pytest-cov>=5.0
pytest-asyncio>=0.24
ruff>=0.8
mypy>=1.13
pre-commit>=4.0
pip-audit>=2.7
httpx>=0.28  # FastAPI test client
```

---

## Action Items

| Priority | Item | Effort |
|----------|------|--------|
| Critical | Remove torch, transformers, sentence-transformers, faiss-cpu | 1 hour |
| Critical | Remove unused packages (langgraph, typer, pypdf, langchain-classic) | 30 min |
| Critical | Add missing MVP dependencies (fastapi, uvicorn, psycopg2, sqlparse) | 30 min |
| High | Run pip-audit for vulnerability scan | 30 min |
| High | Separate direct from transitive dependencies | 2 hours |
| High | Run license audit (pip-licenses) | 1 hour |
| High | Set up Dependabot for automated updates | 30 min |
| Medium | Add pip-audit to CI pipeline | 30 min |
| Medium | Create THIRD_PARTY_LICENSES.md | 1-2 hours |
| Low | Evaluate asyncpg as psycopg2 alternative (license) | 1 hour |
