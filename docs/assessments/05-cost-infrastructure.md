# Cost & Infrastructure Planning

**Project:** AI Knowledge Assistant (SQL Query Chatbot)
**Date:** 2026-02-20

---

## Executive Summary

The largest cost driver for this application is the LLM API. Every user query requires at least one LLM inference call. Infrastructure costs (compute, database, hosting) are secondary. This document breaks down expected costs per query, monthly projections at various user levels, and recommends an infrastructure strategy that balances cost with reliability.

---

## 1. Cost Per Query Breakdown

### Current Architecture (HuggingFace API-based LLM)

| Step | Cost Driver | Estimated Cost Per Query |
|------|------------|------------------------|
| User input processing | CPU (negligible) | ~$0.00 |
| Schema injection | CPU (negligible) | ~$0.00 |
| LLM: Generate SQL | HuggingFace Inference API | $0.001 - $0.01 |
| SQL validation | CPU (negligible) | ~$0.00 |
| DB query execution | Database I/O | ~$0.0001 |
| LLM: Format results | HuggingFace Inference API | $0.001 - $0.01 |
| **Total per query** | | **$0.002 - $0.02** |

### Notes
- With 2 LLM calls per query (generate SQL + format results), the cost is ~2x
- With 1 LLM call per query (generate SQL only, format results as table), the cost is ~1x
- HuggingFace Inference API pricing varies by model and tier
- Costs are heavily dependent on input/output token count

---

## 2. LLM Cost Comparison

### Option A: HuggingFace Inference API (Current)

| Model | Cost | SQL Accuracy | Latency |
|-------|------|-------------|---------|
| Qwen2.5-7B-Instruct (current) | Free tier available, Pro: ~$9/month | Moderate | 2-8s |
| CodeLlama-34B | Pro tier required | Good | 3-10s |
| SQLCoder-7B | May need dedicated endpoint | Very Good for SQL | 2-8s |

- **Free tier:** Rate-limited, suitable for development only
- **Pro tier ($9/month):** Higher rate limits, suitable for early MVP
- **Dedicated endpoint ($~0.06/hour+):** No rate limits, required for production

### Option B: OpenAI API

| Model | Cost per 1K tokens | SQL Accuracy | Latency |
|-------|-------------------|-------------|---------|
| GPT-4o | ~$0.0025 input, ~$0.01 output | Excellent | 1-5s |
| GPT-4o-mini | ~$0.00015 input, ~$0.0006 output | Very Good | 0.5-3s |
| GPT-3.5-turbo | ~$0.0005 input, ~$0.0015 output | Good | 0.5-2s |

### Option C: Self-Hosted LLM

| Item | Cost | Notes |
|------|------|-------|
| GPU server (A10G) | $0.75 - $1.50/hour | AWS/GCP spot pricing |
| GPU server (T4) | $0.35 - $0.70/hour | Budget option |
| Monthly (A10G, 24/7) | $540 - $1,080/month | Only viable at scale |
| Monthly (T4, 24/7) | $250 - $500/month | Budget option |

- Self-hosting makes sense only at 1000+ queries/day
- Eliminates per-query cost but adds infrastructure complexity
- Full control over data privacy (no data sent to third party)

### Recommendation for MVP

**Start with Option A (HuggingFace Pro) or Option B (GPT-4o-mini).**
- Cost: $9-50/month
- Sufficient for up to ~1000 queries/day
- No infrastructure to manage
- Switch to self-hosted only if costs exceed $500/month or data privacy requires it

---

## 3. Monthly Cost Projections

### Scenario: HuggingFace API + Cloud Hosting

| Users/Day | Queries/Day | LLM Cost/Month | Infra Cost/Month | Total/Month |
|-----------|------------|----------------|-------------------|-------------|
| 5 | 20 | $9 (Pro tier) | $20-30 | $30-40 |
| 20 | 100 | $9-30 | $30-50 | $40-80 |
| 50 | 500 | $30-100 | $50-100 | $80-200 |
| 200 | 2,000 | $100-400 | $100-200 | $200-600 |
| 1,000 | 10,000 | $400-2,000 | $200-500 | $600-2,500 |

### Scenario: OpenAI GPT-4o-mini + Cloud Hosting

| Users/Day | Queries/Day | LLM Cost/Month | Infra Cost/Month | Total/Month |
|-----------|------------|----------------|-------------------|-------------|
| 5 | 20 | < $1 | $20-30 | $20-30 |
| 20 | 100 | $2-5 | $30-50 | $32-55 |
| 50 | 500 | $10-25 | $50-100 | $60-125 |
| 200 | 2,000 | $40-100 | $100-200 | $140-300 |
| 1,000 | 10,000 | $200-500 | $200-500 | $400-1,000 |

---

## 4. Infrastructure Components & Costs

### 4.1 Application Server

| Option | Specs | Monthly Cost | Good For |
|--------|-------|-------------|----------|
| Railway/Render (free tier) | 512MB RAM, shared CPU | Free | Development |
| Railway/Render (paid) | 1GB RAM, dedicated | $5-20 | Early MVP |
| AWS EC2 t3.small | 2GB RAM, 2 vCPU | $15-20 | MVP |
| AWS EC2 t3.medium | 4GB RAM, 2 vCPU | $30-40 | Growth |
| AWS ECS Fargate | Pay per use | $20-100 | Production |
| DigitalOcean Droplet | 2GB RAM, 1 vCPU | $12 | Budget MVP |

**Recommendation for MVP:** DigitalOcean Droplet ($12/month) or Railway ($5-20/month)

### 4.2 Database

| Option | Specs | Monthly Cost | Good For |
|--------|-------|-------------|----------|
| SQLite (local file) | No server needed | $0 | Development/demo |
| Supabase (free tier) | PostgreSQL, 500MB | Free | Early MVP |
| Railway PostgreSQL | 1GB, managed | $5-10 | MVP |
| AWS RDS db.t3.micro | PostgreSQL, 1GB | $15-20 | MVP |
| AWS RDS db.t3.small | PostgreSQL, 2GB | $30-40 | Growth |
| PlanetScale (free tier) | MySQL, 5GB | Free | MVP alternative |

**Recommendation for MVP:** Supabase free tier or Railway PostgreSQL ($5-10/month)

### 4.3 Caching (Redis)

| Option | Specs | Monthly Cost | Good For |
|--------|-------|-------------|----------|
| In-memory (no Redis) | Per-process only | $0 | MVP |
| Upstash Redis (free tier) | 10K commands/day | Free | Early MVP |
| Railway Redis | 256MB | $5 | Growth |
| AWS ElastiCache | t3.micro | $12-15 | Production |

**Recommendation for MVP:** Skip Redis initially, use in-memory caching. Add Upstash when needed.

### 4.4 Monitoring & Logging

| Option | Monthly Cost | Good For |
|--------|-------------|----------|
| LangSmith (free tier) | Free | LLM tracing |
| Sentry (free tier) | Free | Error tracking |
| Better Stack (free tier) | Free | Log aggregation |
| Grafana Cloud (free tier) | Free | Metrics dashboards |

**Recommendation for MVP:** All free tiers above = $0/month

---

## 5. MVP Infrastructure Recommendation

### Minimum Viable Infrastructure

```
Total Monthly Cost: $17-50

┌─────────────────────────────────────────────┐
│  Application Layer                           │
│  DigitalOcean Droplet ($12/mo)              │
│  - FastAPI + Uvicorn (2 workers)            │
│  - 2GB RAM, 1 vCPU                          │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│  Database Layer                              │
│  Supabase Free Tier ($0) or Railway ($5/mo) │
│  - PostgreSQL                                │
│  - 500MB - 1GB storage                       │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│  LLM Layer                                   │
│  HuggingFace Pro ($9/mo) or OpenAI Pay-as-go│
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│  Monitoring (Free Tiers)                     │
│  LangSmith + Sentry + Better Stack           │
└─────────────────────────────────────────────┘
```

---

## 6. Cost Optimization Strategies

### Immediate (MVP)

1. **Remove unnecessary dependencies** — Removing torch, transformers, sentence-transformers reduces Docker image size by 3-5GB, which reduces build time and storage costs

2. **Single LLM call per query** — Format results as a table instead of calling LLM a second time (halves LLM cost)

3. **Schema caching** — Don't re-fetch schema on every query

4. **Use free tiers aggressively** — Supabase, Upstash, LangSmith, Sentry all have generous free tiers

### Growth Phase

5. **Response caching** — Cache LLM responses for repeated questions (can save 20-40% of LLM calls)

6. **Model downsizing** — Use smaller/cheaper model for simple queries, larger model for complex ones (query routing)

7. **Batch processing** — If applicable, batch similar queries to reduce per-query overhead

### Scale Phase

8. **Self-hosted LLM** — When LLM API costs exceed $500/month, self-hosting becomes viable

9. **Read replicas** — Add database read replicas for query distribution

10. **Auto-scaling** — Scale workers up/down based on demand (don't pay for idle capacity)

---

## 7. Cost Monitoring & Budget Alerts

### Set Up From Day One

1. **HuggingFace/OpenAI usage dashboard** — Monitor daily API spend
2. **Cloud provider billing alerts** — Alert at 50%, 80%, 100% of monthly budget
3. **Per-query cost tracking** — Log token counts per request, calculate running cost
4. **Monthly cost review** — Compare projected vs actual, adjust strategy

### Budget Guardrails

```python
MAX_DAILY_LLM_CALLS = 500
MAX_MONTHLY_BUDGET = 100  # USD

# Track in application
daily_call_count = get_today_call_count()
if daily_call_count >= MAX_DAILY_LLM_CALLS:
    raise RateLimitError("Daily query limit reached. Try again tomorrow.")
```

---

## 8. Infrastructure Decision Matrix

| Decision | MVP Choice | Reason | Revisit When |
|----------|-----------|--------|-------------|
| LLM hosting | API (HuggingFace/OpenAI) | No infra to manage, low upfront cost | LLM costs > $500/month |
| App hosting | DigitalOcean / Railway | Cheap, simple, sufficient | > 200 concurrent users |
| Database | Supabase / Railway PostgreSQL | Managed, free/cheap tier | > 5GB data or need replicas |
| Caching | In-memory | No additional infra | Cache hit rate data available |
| Container orchestration | None (single server) | Premature at this scale | > 3 servers needed |
| CDN | None | No static assets to serve | Frontend is added |
| CI/CD | GitHub Actions (free) | Free for public repos, 2000 min/month private | Need self-hosted runners |

---

## Action Items

| Priority | Item | Effort |
|----------|------|--------|
| Critical | Choose LLM provider and tier | 1 hour (decision) |
| Critical | Set up hosting environment | 2-4 hours |
| Critical | Set up managed database | 1-2 hours |
| High | Remove torch/transformers to shrink footprint | 1 hour |
| High | Set up billing alerts on all services | 1 hour |
| High | Implement daily query limit budget guardrail | 2 hours |
| Medium | Set up free-tier monitoring (LangSmith, Sentry) | 2-3 hours |
| Medium | Document infrastructure decisions in README | 1 hour |
| Low | Evaluate self-hosted LLM cost-benefit | 4 hours (research) |
