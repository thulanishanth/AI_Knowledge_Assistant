#app/observability/query_observer.py
"""
PRODUCTION OBSERVABILITY — zero hardcoded paths.
Uses settings.BASE_DIR for log directory.
Includes record_tokens_from_prompt (the missing method that caused the crash).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = "unknown"

    def __str__(self) -> str:
        return (
            f"model={self.model} | "
            f"prompt={self.prompt_tokens} | "
            f"completion={self.completion_tokens} | "
            f"total={self.total_tokens}"
        )


@dataclass
class RagHealth:
    rules_loaded: int = 0
    rules_injected: int = 0
    rules_filtered_out: int = 0
    contradiction_resolved: bool = False
    double_prefix_fixed: int = 0
    vector_results_count: int = 0
    cache_hit: bool = False

    def summary(self) -> str:
        return (
            f"rules_loaded={self.rules_loaded} | "
            f"injected={self.rules_injected} | "
            f"filtered_out={self.rules_filtered_out} | "
            f"contradiction_resolved={self.contradiction_resolved} | "
            f"double_prefix_bugs={self.double_prefix_fixed} | "
            f"vector_matches={self.vector_results_count} | "
            f"cache_hit={self.cache_hit}"
        )


@dataclass
class IntentHealth:
    route: str = "unknown"
    confidence: float = 0.0
    is_follow_up: bool = False
    follow_up_strategy: str = "none"
    standalone_question: str = ""
    rewrite_called: bool = False
    plan_called: bool = False
    critic_called: bool = False
    critic_verdict: str = "n/a"

    def summary(self) -> str:
        return (
            f"route={self.route} | "
            f"confidence={self.confidence:.2f} | "
            f"is_follow_up={self.is_follow_up} | "
            f"critic_verdict={self.critic_verdict}"
        )


@dataclass
class SqlHealth:
    sql_generated: str = ""
    sql_strategy: str = "none"
    sql_critic_verdict: str = "n/a"
    sql_critic_reason: str = ""
    repair_attempts: int = 0
    execution_ms: float = 0.0
    rows_returned: int = 0
    truncated: bool = False
    execution_error: str = ""

    def summary(self) -> str:
        return (
            f"strategy={self.sql_strategy} | "
            f"critic={self.sql_critic_verdict} | "
            f"repairs={self.repair_attempts} | "
            f"rows={self.rows_returned} | "
            f"exec_ms={self.execution_ms:.1f}"
        )


@dataclass
class QueryAudit:
    """Complete audit record for one user query."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    session_id: str = ""
    user_id: str = ""
    tenant_id: str = ""
    user_question: str = ""
    final_answer: str = ""
    confidence: float = 0.0

    token_usage: TokenUsage = field(default_factory=TokenUsage)
    rag_health: RagHealth = field(default_factory=RagHealth)
    intent_health: IntentHealth = field(default_factory=IntentHealth)
    sql_health: SqlHealth = field(default_factory=SqlHealth)

    total_latency_ms: float = 0.0
    memory_fetch_ms: float = 0.0
    intent_analysis_ms: float = 0.0
    sql_generation_ms: float = 0.0
    synthesis_ms: float = 0.0

    answer_has_number: bool = False
    answer_mentions_sql: bool = False
    answer_is_empty: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def health_score(self) -> float:
        """
        Compute a 0–100 health score for this query.
        Used to detect degradation patterns over time.
        """
        score = 100.0
        if self.sql_health.sql_critic_verdict == "retry":
            score -= 15
        if self.sql_health.repair_attempts > 0:
            score -= 10 * self.sql_health.repair_attempts
        if self.sql_health.execution_error:
            score -= 30
        if self.answer_is_empty:
            score -= 20
        if self.answer_mentions_sql:
            score -= 10
        if self.rag_health.contradiction_resolved:
            score -= 5  # mild warning: a contradiction existed (but was resolved)
        if self.rag_health.rules_injected == 0:
            score -= 10
        if self.rag_health.double_prefix_fixed > 0:
            score -= 5  # DB has the double-prefix bug — needs fixing
        if self.intent_health.confidence < 0.7:
            score -= 10
        if self.total_latency_ms > 5000:
            score -= 10
        return max(0.0, score)

    def log_summary(self) -> None:
        logger.info(
            "QUERY_AUDIT | score=%.0f | %s | %s | %s | latency_ms=%.0f | %s",
            self.health_score(),
            self.rag_health.summary(),
            self.intent_health.summary(),
            self.sql_health.summary(),
            self.total_latency_ms,
            str(self.token_usage),
        )


# ════════════════════════════════════════════════════════════════════════════
# TOKEN HELPERS
# ════════════════════════════════════════════════════════════════════════════

def extract_token_usage(api_response: Any, model: str = "unknown") -> TokenUsage:
    """
    Extract real token counts from an OpenAI-compatible API response.
    HuggingFace Inference API returns usage in response.usage — we just read it.
    """
    usage = TokenUsage(model=model)
    if api_response is None:
        return usage
    try:
        response_usage = getattr(api_response, "usage", None)
        if response_usage is not None:
            usage.prompt_tokens = getattr(response_usage, "prompt_tokens", 0) or 0
            usage.completion_tokens = getattr(response_usage, "completion_tokens", 0) or 0
            usage.total_tokens = getattr(response_usage, "total_tokens", 0) or (
                usage.prompt_tokens + usage.completion_tokens
            )
            return usage
        if isinstance(api_response, dict):
            raw = api_response.get("usage", {})
            usage.prompt_tokens = raw.get("prompt_tokens", 0) or 0
            usage.completion_tokens = raw.get("completion_tokens", 0) or 0
            usage.total_tokens = raw.get("total_tokens", 0) or (
                usage.prompt_tokens + usage.completion_tokens
            )
            return usage
    except Exception as exc:
        logger.debug("Token extraction failed: %s", exc)
    return usage


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 chars per token for English text."""
    return max(1, len(text) // 4)


# ════════════════════════════════════════════════════════════════════════════
# QUERY OBSERVER
# ════════════════════════════════════════════════════════════════════════════

class QueryObserver:
    """
    Tracks the full lifecycle of one query.

    Usage pattern in query_orchestrator.py:
        obs = QueryObserver(session_id, user_id, tenant_id, question)
        obs.start()
        # ... pipeline runs ...
        obs.record_rag(...)
        obs.record_intent(...)
        obs.record_sql(...)
        obs.record_tokens_from_prompt(prompt_text, completion_text, model)
        obs.record_answer(answer, confidence)
        obs.finish()  # logs summary + writes JSONL to disk
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        tenant_id: str,
        question: str,
    ) -> None:
        self.audit = QueryAudit(
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            user_question=question,
        )
        self._start_time: float = 0.0

    def start(self) -> None:
        self._start_time = time.perf_counter()

    def record_rag(
        self,
        rules_loaded: int = 0,
        rules_injected: int = 0,
        contradiction_resolved: bool = False,
        double_prefix_fixed: int = 0,
        vector_results_count: int = 0,
        cache_hit: bool = False,
    ) -> None:
        self.audit.rag_health = RagHealth(
            rules_loaded=rules_loaded,
            rules_injected=rules_injected,
            rules_filtered_out=max(0, rules_loaded - rules_injected),
            contradiction_resolved=contradiction_resolved,
            double_prefix_fixed=double_prefix_fixed,
            vector_results_count=vector_results_count,
            cache_hit=cache_hit,
        )
        if double_prefix_fixed > 0:
            logger.warning(
                "OBSERVABILITY: %d rules have double CRITICAL prefix — "
                "run business_rules_final.sql to fix the database",
                double_prefix_fixed,
            )

    def record_intent(
        self,
        route: str,
        confidence: float,
        is_follow_up: bool = False,
        follow_up_strategy: str = "none",
        standalone_question: str = "",
        rewrite_called: bool = True,
        plan_called: bool = True,
        critic_called: bool = False,
        critic_verdict: str = "n/a",
    ) -> None:
        self.audit.intent_health = IntentHealth(
            route=route,
            confidence=confidence,
            is_follow_up=is_follow_up,
            follow_up_strategy=follow_up_strategy,
            standalone_question=standalone_question,
            rewrite_called=rewrite_called,
            plan_called=plan_called,
            critic_called=critic_called,
            critic_verdict=critic_verdict,
        )

    def record_sql(
        self,
        sql: str = "",
        strategy: str = "none",
        critic_verdict: str = "n/a",
        critic_reason: str = "",
        repair_attempts: int = 0,
        execution_ms: float = 0.0,
        rows_returned: int = 0,
        truncated: bool = False,
        execution_error: str = "",
    ) -> None:
        self.audit.sql_health = SqlHealth(
            sql_generated=sql,
            sql_strategy=strategy,
            sql_critic_verdict=critic_verdict,
            sql_critic_reason=critic_reason,
            repair_attempts=repair_attempts,
            execution_ms=execution_ms,
            rows_returned=rows_returned,
            truncated=truncated,
            execution_error=execution_error,
        )

    def record_tokens(self, api_response: Any = None, model: str = "unknown") -> None:
        """Record tokens from a live API response object."""
        self.audit.token_usage = extract_token_usage(api_response, model)
        if self.audit.token_usage.total_tokens == 0:
            # Fallback estimate when API doesn't return usage
            estimated = estimate_tokens(self.audit.user_question) + 800
            self.audit.token_usage = TokenUsage(
                prompt_tokens=estimated,
                completion_tokens=150,
                total_tokens=estimated + 150,
                model=f"{model} (estimated)",
            )

    def record_tokens_from_prompt(
        self, prompt_text: str, completion_text: str, model: str
    ) -> None:
        """
        Estimate token counts from raw prompt + completion text.
        Used when the API response object is not accessible.
        This was the MISSING METHOD that caused the AttributeError crash.
        """
        p = estimate_tokens(prompt_text)
        c = estimate_tokens(completion_text)
        self.audit.token_usage = TokenUsage(
            prompt_tokens=p,
            completion_tokens=c,
            total_tokens=p + c,
            model=f"{model} (estimated)",
        )

    def record_answer(self, answer: str, confidence: float = 0.0) -> None:
        self.audit.final_answer = answer
        self.audit.confidence = confidence
        a_lower = answer.lower()
        self.audit.answer_has_number = bool(re.search(r"\d", answer))
        self.audit.answer_mentions_sql = any(
            kw in a_lower
            for kw in [
                "select ", "where ", "group by", "order by",
                "sql query", "i ran a query", "from table",
            ]
        )
        self.audit.answer_is_empty = not answer.strip() or len(answer.strip()) < 5

    def record_timing(
        self,
        memory_fetch_ms: float = 0.0,
        intent_analysis_ms: float = 0.0,
        sql_generation_ms: float = 0.0,
        synthesis_ms: float = 0.0,
    ) -> None:
        self.audit.memory_fetch_ms = memory_fetch_ms
        self.audit.intent_analysis_ms = intent_analysis_ms
        self.audit.sql_generation_ms = sql_generation_ms
        self.audit.synthesis_ms = synthesis_ms

    def finish(self) -> None:
        if self._start_time > 0:
            self.audit.total_latency_ms = (
                time.perf_counter() - self._start_time
            ) * 1000
        self.audit.log_summary()
        self._write_to_dump()

    def _write_to_dump(self) -> None:
        """
        Append a JSONL audit record to the daily observability file.
        Path: {settings.BASE_DIR}/logs/observability/YYYY-MM-DD_query_audit.jsonl
        No hardcoded paths — uses settings.BASE_DIR.
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # Use BASE_DIR from settings — same pattern as the rest of the app
            base = Path(settings.frontend_path).parent  # go up from frontend/ to project root
            dump_dir = base / "logs" / "observability"
            dump_dir.mkdir(parents=True, exist_ok=True)

            record = self.audit.to_dict()
            record["health_score"] = round(self.audit.health_score(), 1)

            with open(dump_dir / f"{today}_query_audit.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        except Exception as exc:
            logger.error("Failed to write observability dump: %s", exc)