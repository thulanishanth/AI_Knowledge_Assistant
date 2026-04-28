#app/services/intent_service.py
"""
Intent service — pure LLM routing, no hardcoded domain logic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.observability.structured_logger import log_event
from app.services.llm_client import call_llm, call_llm_with_tool
from app.services.prompt_builder import PromptBuilder

logger = get_logger(__name__)


@dataclass(slots=True)
class RewriteResult:
    normalized_question: str = ""
    standalone_question: str = ""
    is_follow_up: bool = False
    follow_up_strategy: str = "none"
    missing_context: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionPlan:
    thought_process: str = ""
    route: str = "database_query"
    standalone_question: str = ""
    user_goal: str = "query data"
    needs_clarification: bool = False
    clarifying_question: str = ""
    requires_schema: bool = True
    requires_business_mapping: bool = False
    follow_up_strategy: str = "none"
    confidence: float = 0.5


@dataclass(slots=True)
class CriticReview:
    verdict: str = "approve"
    reason: str = ""
    needs_clarification: bool = False
    clarifying_question: str = ""
    confidence: float = 0.8


class IntentService:
    """Multi-stage planner: rewrite → plan → (optional) critic."""

    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._pb = prompt_builder

    def analyze(
        self,
        user_question: str,
        memory_context: str = "",
        dialogue_state: str = "",
        chat_history: str = "",
        schema_summary: str = "",
    ) -> dict[str, Any]:
        question = " ".join((user_question or "").split()).strip()

        rewrite = self._rewrite_question(question, chat_history, dialogue_state)
        plan = self._build_execution_plan(
            question, rewrite, memory_context, dialogue_state, schema_summary
        )

        if plan.confidence < 0.85 or plan.route == "clarify" or rewrite.is_follow_up:
            reviewed = self._review_plan(question, rewrite, plan, memory_context, dialogue_state)
            if reviewed:
                plan = reviewed

        if plan.route == "clarify" and not plan.clarifying_question:
            plan.needs_clarification = True
            plan.clarifying_question = "Could you provide a bit more detail?"

        log_event("info", "intent_analysis_complete", route=plan.route, follow_up=rewrite.is_follow_up)

        result = asdict(plan)
        result["is_follow_up"] = rewrite.is_follow_up
        result["standalone_question"] = rewrite.standalone_question or question
        return result

    def review_sql_candidate(
        self,
        question: str,
        sql: str,
        schema: Any,
        session_context: str = "",
    ) -> dict[str, Any]:
        prompt = self._pb.build_sql_critic_prompt(
            question=question,
            sql=sql,
            schema=schema,
            session_context=session_context,
        )
        tool = {
            "name": "submit_sql_review",
            "description": "Submit the official review of the SQL candidate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["approve", "retry", "clarify"]},
                    "reason": {"type": "string"},
                    "clarifying_question": {"type": "string"},
                },
                "required": ["verdict", "reason"],
            },
        }
        try:
            parsed = call_llm_with_tool(
                prompt=prompt, tool_schema=tool,
                tool_name="submit_sql_review", temperature=0.0,
            )
            return parsed if parsed else {"verdict": "approve", "reason": "fallback"}
        except Exception as exc:
            logger.warning("SQL Critic failed: %s", exc)
            return {"verdict": "approve", "reason": "Critic offline."}

    def answer_general_question(
        self, user_question: str, memory_context: str = ""
    ) -> str:
        prompt = self._pb.build_general_answer_prompt(user_question, memory_context)
        try:
            return call_llm(prompt=prompt, max_tokens=300, temperature=0.3).strip()
        except Exception as exc:
            logger.error("General answer failed: %s", exc)
            return "I can help with general questions and data queries."

    # ──────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────────────

    def _rewrite_question(
        self, question: str, chat_history: str, state: str
    ) -> RewriteResult:
        prompt = self._pb.build_rewrite_prompt(question, chat_history, state)
        if not prompt:
            return RewriteResult(standalone_question=question)

        tool = {
            "name": "submit_rewrite",
            "description": "Submit the follow-up analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "normalized_question": {"type": "string"},
                    "standalone_question": {"type": "string"},
                    "is_follow_up": {"type": "boolean"},
                    "follow_up_strategy": {
                        "type": "string",
                        "enum": ["none", "refinement", "drill_down", "new_metric", "correction"],
                    },
                    "missing_context": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["standalone_question", "is_follow_up"],
            },
        }
        try:
            parsed = call_llm_with_tool(prompt, tool, "submit_rewrite", max_tokens=300, temperature=0.0)
            if parsed:
                valid_fields = {k: v for k, v in parsed.items() if k in RewriteResult.__dataclass_fields__}
                return RewriteResult(**valid_fields)
        except Exception as exc:
            logger.warning("Rewrite failed: %s", exc)
        return RewriteResult(standalone_question=question)

    def _build_execution_plan(
        self,
        question: str,
        rewrite: RewriteResult,
        context: str,
        state: str,
        schema_summary: str = "",
    ) -> ExecutionPlan:
        prompt = self._pb.build_plan_prompt(
            user_question=question,
            rewritten_question=rewrite.standalone_question,
            recent_context=context,
            dialogue_state=state,
            rewrite_result=str(asdict(rewrite)),
            schema_summary=schema_summary,
        )
        if not prompt:
            return ExecutionPlan(
                route="database_query",
                standalone_question=rewrite.standalone_question or question,
            )

        tool = {
            "name": "submit_execution_plan",
            "description": "Submit the routing and execution plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought_process": {"type": "string"},
                    "route": {
                        "type": "string",
                        "enum": [
                            "database_query", "clarify", "schema_answer",
                            "show_sql", "explain_last_answer", "general_answer",
                        ],
                    },
                    "standalone_question": {"type": "string"},
                    "user_goal": {"type": "string"},
                    "needs_clarification": {"type": "boolean"},
                    "clarifying_question": {"type": "string"},
                    "requires_schema": {"type": "boolean"},
                    "requires_business_mapping": {"type": "boolean"},
                    "follow_up_strategy": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["thought_process", "route", "standalone_question", "confidence"],
            },
        }
        try:
            parsed = call_llm_with_tool(prompt, tool, "submit_execution_plan", max_tokens=400, temperature=0.0)
            if parsed:
                valid_fields = {k: v for k, v in parsed.items() if k in ExecutionPlan.__dataclass_fields__}
                return ExecutionPlan(**valid_fields)
        except Exception as exc:
            logger.error("Planner failed: %s", exc)

        return ExecutionPlan(
            route="database_query",
            standalone_question=rewrite.standalone_question or question,
        )

    def _review_plan(
        self,
        question: str,
        rewrite: RewriteResult,
        plan: ExecutionPlan,
        context: str,
        state: str,
    ) -> ExecutionPlan | None:
        prompt = self._pb.build_plan_critic_prompt(
            user_question=question,
            rewritten_question=rewrite.standalone_question,
            plan_json=str(asdict(plan)),
            recent_context=context,
            dialogue_state=state,
        )
        if not prompt:
            return None

        tool = {
            "name": "submit_plan_review",
            "description": "Submit the plan review verdict.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["approve", "clarify", "replan"]},
                    "reason": {"type": "string"},
                    "needs_clarification": {"type": "boolean"},
                    "clarifying_question": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["verdict", "reason"],
            },
        }
        try:
            parsed = call_llm_with_tool(prompt, tool, "submit_plan_review", max_tokens=200, temperature=0.0)
            if not parsed:
                return None

            verdict = parsed.get("verdict", "approve")
            if verdict == "approve":
                return plan
            if verdict == "clarify":
                plan.route = "clarify"
                plan.needs_clarification = True
                plan.clarifying_question = (
                    parsed.get("clarifying_question") or "Could you clarify your question?"
                )
                return plan
            return ExecutionPlan(
                route="database_query",
                standalone_question=rewrite.standalone_question,
            )
        except Exception as exc:
            logger.warning("Plan critic failed: %s", exc)
            return None