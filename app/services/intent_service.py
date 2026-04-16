# app/services/intent_service.py
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.observability.structured_logger import log_event
from app.services.llm_client import call_llm
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
    """Multi-stage planner with Rewrite (Follow-up), Planning, and Critic Review."""

    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._prompt_builder = prompt_builder

    def analyze(
        self,
        user_question: str,
        memory_context: str = "",
        dialogue_state: str = "",
    ) -> dict[str, Any]:
        question = " ".join((user_question or "").split()).strip()

        # Step 1: Rewrite & Follow-up Detection
        rewrite = self._rewrite_question(question, memory_context, dialogue_state)
        
        # Step 2: Build the Plan (CoT)
        plan = self._build_execution_plan(question, rewrite, memory_context, dialogue_state)

        # Step 3: The Plan Critic
        # We only run the critic if confidence is low, or if the bot decided to clarify (to prevent looping)
        if plan.confidence < 0.85 or plan.route == "clarify" or rewrite.is_follow_up:
            reviewed_plan = self._review_plan(question, rewrite, plan, memory_context, dialogue_state)
            if reviewed_plan:
                plan = reviewed_plan

        # Ensure we have a clarifying question if the route demands it
        if plan.route == "clarify" and not plan.clarifying_question:
            plan.needs_clarification = True
            plan.clarifying_question = "Could you provide a bit more detail so I can query the right data?"

        log_event("info", "intent_analysis_complete", route=plan.route, follow_up=rewrite.is_follow_up)
        
        # Merge rewrite data and plan data to pass back to the orchestrator
        result = asdict(plan)
        result["is_follow_up"] = rewrite.is_follow_up
        result["standalone_question"] = rewrite.standalone_question or question
        return result

    def review_sql_candidate(self, question: str, sql: str, schema: Any, session_context: str = "") -> dict[str, Any]:
        """The SQL Critic: Evaluates generated SQL before execution."""
        prompt = self._prompt_builder.build_sql_critic_prompt(
            question=question,
            sql=sql,
            schema=schema,
            session_context=session_context,
        )
        tool_schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["approve", "retry", "clarify"]},
                "reason": {"type": "string"},
                "clarifying_question": {"type": "string"}
            },
            "required": ["verdict", "reason"]
        }
            
        try:
            output = call_llm(prompt=prompt, max_tokens=200, temperature=0.0)
            parsed = self._extract_json(output)
            return parsed or {"verdict": "approve", "reason": "Failed to parse critic JSON"}
        except Exception as e:
            logger.warning(f"SQL Critic failed: {e}")
            return {"verdict": "approve", "reason": "Critic offline."}

    def answer_general_question(self, user_question: str, memory_context: str = "") -> str:
        prompt = self._prompt_builder.build_general_answer_prompt(user_question, memory_context)
        try:
            return call_llm(prompt=prompt, max_tokens=200, temperature=0.3).strip()
        except Exception:
            return "I can help with general questions and with your data queries."

    # --- INTERNAL HELPERS ---
    def _rewrite_question(self, question: str, context: str, state: str) -> RewriteResult:
        prompt = self._prompt_builder.build_rewrite_prompt(question, context, state)
        try:
            output = call_llm(prompt=prompt, max_tokens=300, temperature=0.0)
            parsed = self._extract_json(output)
            return RewriteResult(**parsed) if parsed else RewriteResult(standalone_question=question)
        except Exception:
            return RewriteResult(standalone_question=question)

    def _build_execution_plan(self, question: str, rewrite: RewriteResult, context: str, state: str) -> ExecutionPlan:
        prompt = self._prompt_builder.build_plan_prompt(
            user_question=question,
            rewritten_question=rewrite.standalone_question,
            recent_context=context,
            dialogue_state=state,
            rewrite_result=json.dumps(asdict(rewrite))
        )
        try:
            output = call_llm(prompt=prompt, max_tokens=400, temperature=0.0)
            parsed = self._extract_json(output)
            # Safely merge defaults for missing keys
            if parsed:
                return ExecutionPlan(**{k: v for k, v in parsed.items() if k in ExecutionPlan.__dataclass_fields__})
        except Exception as e:
            logger.error(f"Planner failed: {e}")
        return ExecutionPlan(route="database_query", standalone_question=rewrite.standalone_question or question)

    def _review_plan(self, question: str, rewrite: RewriteResult, plan: ExecutionPlan, context: str, state: str) -> ExecutionPlan | None:
        prompt = self._prompt_builder.build_plan_critic_prompt(
            user_question=question,
            rewritten_question=rewrite.standalone_question,
            plan_json=json.dumps(asdict(plan)),
            recent_context=context,
            dialogue_state=state
        )
        try:
            output = call_llm(prompt=prompt, max_tokens=200, temperature=0.0)
            parsed = self._extract_json(output)
            if not parsed: return None
            
            review = CriticReview(**{k: v for k, v in parsed.items() if k in CriticReview.__dataclass_fields__})
            
            if review.verdict == "approve":
                return plan
            elif review.verdict == "clarify":
                plan.route = "clarify"
                plan.needs_clarification = True
                plan.clarifying_question = review.clarifying_question
                return plan
            # If replan, we fallback to a safe query
            return ExecutionPlan(route="database_query", standalone_question=rewrite.standalone_question)
        except Exception:
            return None

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        try:
            cleaned = text.strip("`").strip()
            if cleaned.lower().startswith("json"): cleaned = cleaned[4:].strip()
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start != -1 and end != -1:
                return json.loads(cleaned[start:end+1])
        except Exception:
            pass
        return None