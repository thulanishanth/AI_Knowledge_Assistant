# DevOps & Deployment Readiness

**Project:** AI Knowledge Assistant (SQL Query Chatbot)
**Date:** 2026-02-20
**Current State:** Not deployable (CLI-only, no containerization, no deployment config)

---

## Executive Summary

The application currently has zero deployment infrastructure. There is no Dockerfile, no CI/CD pipeline, no monitoring, no health checks, and no deployment strategy. This document defines everything needed to go from "runs on my machine" to "running in production reliably."

---

## 1. Containerization

### Current State: Nothing exists

No `Dockerfile`, no `docker-compose.yml`, no `.dockerignore`.

### Required: Dockerfile

```dockerfile
# Multi-stage build for smaller production image
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

**Key considerations:**
- Use multi-stage build to reduce image size
- Use `python:3.11-slim` (not `python:3.11` — saves ~600MB)
- Remove torch/transformers from requirements (saves 3-5GB in image)
- Set non-root user for security
- Pin base image digest for reproducibility

### Required: docker-compose.yml (development)

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    environment:
      POSTGRES_DB: knowledge_assistant
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: devpassword
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

### Required: .dockerignore

```
.git
.env
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
venv/
data/
docs/
tests/
*.md
```

---

## 2. CI/CD Pipeline

### Current State: Nothing exists

No GitHub Actions, no Jenkinsfile, no automation of any kind.

### Required: GitHub Actions Workflows

#### Workflow 1: CI (on every push and PR)

File: `.github/workflows/ci.yml`

**Stages:**
1. **Lint** — Run ruff for code style and formatting
2. **Type Check** — Run mypy for type errors
3. **Test** — Run pytest with coverage report
4. **Security Scan** — Run pip-audit for dependency vulnerabilities
5. **Docker Build** — Verify the Docker image builds successfully

**Expected run time:** 3-5 minutes

#### Workflow 2: Deploy (on merge to main)

File: `.github/workflows/deploy.yml`

**Stages:**
1. **Build** — Build Docker image
2. **Push** — Push to container registry (Docker Hub, GHCR, or ECR)
3. **Deploy to Staging** — Deploy to staging environment
4. **Smoke Test** — Hit health endpoint to verify deployment
5. **Deploy to Production** — Manual approval gate, then deploy

---

## 3. Environment Strategy

### Environments Needed

| Environment | Purpose | Infrastructure | Database |
|-------------|---------|---------------|----------|
| Local | Development | docker-compose | Local PostgreSQL |
| CI | Automated testing | GitHub Actions | SQLite or test PostgreSQL |
| Staging | Pre-production testing | Same as prod (smaller) | Staging PostgreSQL |
| Production | Live users | Production infra | Production PostgreSQL |

### Environment Configuration

Each environment needs its own:
- Database connection string
- LLM API key / configuration
- Log level (DEBUG for local, INFO for staging, WARNING for production)
- Rate limit settings
- Feature flags

**Implementation:** Use `.env` files for local, environment variables for CI/staging/production.

```
# .env.example
DATABASE_URL=postgresql://user:password@localhost:5432/knowledge_assistant
HUGGINGFACEHUB_API_TOKEN=hf_xxxxxxxxxxxxx
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=500
LOG_LEVEL=INFO
QUERY_TIMEOUT_SECONDS=30
MAX_RESULT_ROWS=1000
RATE_LIMIT_PER_MINUTE=10
CORS_ALLOWED_ORIGINS=http://localhost:3000
```

---

## 4. Health Checks & Readiness

### Current State: Nothing exists

### Required Endpoints

**`GET /health`** — Basic liveness check
```json
{
  "status": "healthy",
  "timestamp": "2026-02-20T10:30:00Z"
}
```

**`GET /health/ready`** — Full readiness check (verify all dependencies)
```json
{
  "status": "ready",
  "checks": {
    "database": { "status": "connected", "latency_ms": 5 },
    "llm_api": { "status": "reachable", "latency_ms": 200 },
    "memory": { "used_mb": 180, "total_mb": 2048 }
  }
}
```

**Usage:**
- Load balancer health check → `/health` (fast, no external calls)
- Kubernetes readiness probe → `/health/ready` (checks dependencies)
- Monitoring dashboard → `/health/ready` (full status)

---

## 5. Logging Strategy

### Current State: `print()` statements only

### Required: Structured Logging

```python
import logging
import json

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

logger = logging.getLogger(__name__)
```

**What to log:**

| Event | Log Level | Content |
|-------|----------|---------|
| Request received | INFO | Method, path, user_id, session_id |
| SQL generated | INFO | Question (truncated), SQL query, generation time |
| SQL executed | INFO | Query time, row count, success/failure |
| SQL validation failed | WARNING | Question, generated SQL, reason |
| LLM error | ERROR | Error type, message, retry count |
| Database error | ERROR | Error type, message (NO credentials!) |
| Unhandled exception | CRITICAL | Full traceback (server-side only) |
| Request completed | INFO | Status code, total duration |

**What NOT to log:**
- Database passwords or connection strings
- API keys or tokens
- Full user queries (may contain sensitive business info — truncate or hash)
- Full query results (may contain PII)

### Log Aggregation

| Option | Cost | Good For |
|--------|------|---------|
| stdout/stderr (Docker logs) | Free | Local, simple deployments |
| Better Stack (free tier) | Free | MVP |
| AWS CloudWatch | $0.50/GB | AWS deployments |
| Datadog | $15/host/month | Growth stage |
| ELK Stack (self-hosted) | Infrastructure cost | Scale stage |

**Recommendation:** Start with stdout + Better Stack free tier.

---

## 6. Monitoring & Alerting

### Current State: Nothing exists

### Required Monitoring Stack

#### Application Metrics (Prometheus-compatible)
- Request count (by endpoint, status code)
- Request latency (p50, p95, p99)
- LLM call count and latency
- Database query count and latency
- Error rate
- Active sessions
- Memory usage

#### LLM-Specific Monitoring (LangSmith)
- Token usage per request
- LLM response quality (manual review)
- Chain execution traces
- Cost tracking

#### Infrastructure Monitoring
- CPU and memory usage
- Disk usage
- Network I/O
- Container health

### Alerting Rules

| Alert | Condition | Severity | Action |
|-------|-----------|----------|--------|
| High error rate | > 10% of requests failing | Critical | Page on-call |
| LLM API down | Health check fails 3x | Critical | Page on-call |
| Database unreachable | Connection fails | Critical | Page on-call |
| High latency | p95 > 30 seconds | High | Investigate |
| Memory leak | Memory growing > 10%/hour | High | Investigate |
| Rate limit exhaustion | LLM daily quota > 80% | Medium | Notify team |
| Disk space low | < 20% free | Medium | Notify team |

---

## 7. Backup & Recovery

### Database Backups
- **Automated daily backups** — Most managed databases (Supabase, RDS) do this automatically
- **Point-in-time recovery** — Enable WAL archiving for PostgreSQL
- **Backup testing** — Restore from backup monthly to verify integrity
- **Retention** — Keep 7 daily, 4 weekly, 12 monthly backups

### Application Recovery
- **Stateless application** — If the app crashes, just restart it (no data loss)
- **Session persistence** — If using Redis for sessions, ensure Redis is persistent
- **Configuration backup** — All config in version control (except secrets)

### Disaster Recovery Plan
1. Database is lost → Restore from latest backup
2. Application server is lost → Redeploy from Docker image
3. LLM API is down → Return graceful error, queue requests for retry
4. DNS/networking failure → Failover to backup region (post-MVP)

---

## 8. Deployment Strategy

### MVP: Simple Deployment

```
Developer pushes to main
    │
    ▼
GitHub Actions CI
    │ (lint, test, build)
    ▼
Docker image pushed to registry
    │
    ▼
SSH to server + docker pull + docker restart
    │
    ▼
Health check passes → Done
```

### Growth: Blue-Green Deployment

```
New version deployed alongside old version
    │
    ▼
Health check on new version
    │
    ▼
Switch traffic to new version
    │
    ▼
Keep old version for 1 hour (rollback window)
    │
    ▼
Remove old version
```

### Rollback Plan
1. Revert git commit on main
2. CI rebuilds previous version
3. Redeploy
4. Alternative: Keep last 3 Docker image tags, `docker pull` the previous tag

---

## 9. SSL/TLS & Domain

### Requirements
- Custom domain (e.g., `api.yourstartup.com`)
- SSL certificate (Let's Encrypt — free, auto-renewable)
- HTTPS enforced (redirect HTTP → HTTPS)

### Options
| Approach | Effort | Cost |
|----------|--------|------|
| Cloudflare (free plan) | Low | Free |
| Let's Encrypt + Nginx | Medium | Free |
| AWS Certificate Manager | Low | Free (with AWS infra) |
| Platform built-in (Railway, Render) | Zero | Included |

**Recommendation for MVP:** Use platform built-in SSL (Railway/Render) or Cloudflare free plan.

---

## 10. Secrets Management in Production

### Development
- `.env` file (fine for local development)

### Production Options

| Option | Cost | Complexity |
|--------|------|-----------|
| Platform env vars (Railway, Render) | Free | Very Low |
| AWS Secrets Manager | $0.40/secret/month | Low |
| HashiCorp Vault | Infrastructure cost | High |
| Doppler | Free tier available | Low |

**Recommendation for MVP:** Platform environment variables (Railway/Render/DigitalOcean console).

**Rules:**
- Never commit secrets to git
- Never log secrets
- Rotate API keys quarterly
- Use separate keys for staging and production
- Document all required secrets in `.env.example`

---

## Deployment Readiness Checklist

| Item | Status | Priority |
|------|--------|----------|
| Dockerfile | Not created | Critical |
| docker-compose.yml | Not created | Critical |
| .dockerignore | Not created | High |
| CI pipeline (GitHub Actions) | Not created | Critical |
| CD pipeline | Not created | High |
| Health check endpoints | Not created | Critical |
| Structured logging | Not implemented | High |
| Environment variable management | Partial | High |
| .env.example | Not created | High |
| SSL/TLS setup | Not done | High |
| Domain setup | Not done | Medium |
| Monitoring setup | Not done | High |
| Alerting rules | Not defined | Medium |
| Backup strategy | Not defined | Medium |
| Rollback plan | Not defined | Medium |
| Secrets management (production) | Not configured | High |

---

## Action Items

| Priority | Item | Effort |
|----------|------|--------|
| Critical | Create Dockerfile (multi-stage) | 2-3 hours |
| Critical | Create docker-compose.yml (app + db) | 1-2 hours |
| Critical | Set up GitHub Actions CI workflow | 3-4 hours |
| Critical | Implement health check endpoints | 1-2 hours |
| High | Replace print() with structured logging | 2-3 hours |
| High | Create .env.example with all variables | 30 minutes |
| High | Set up deployment to Railway/Render/DO | 2-4 hours |
| High | Configure SSL and custom domain | 1-2 hours |
| High | Set up monitoring (LangSmith + Sentry free) | 2-3 hours |
| Medium | Create CD pipeline with staging | 3-4 hours |
| Medium | Define alerting rules | 1-2 hours |
| Medium | Document backup and recovery plan | 1-2 hours |
| Low | Set up blue-green deployment | 4-6 hours |
