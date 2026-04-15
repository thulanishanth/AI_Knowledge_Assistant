from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.observability.metrics import metrics
from app.observability.structured_logger import log_event
from app.services.llm_client import call_llm
from app.services.prompt_builder import PromptBuilder

logger = get_logger(__name__)

_ALLOWED_ROUTES = {
    "clarify",
    "database_query",
    "schema_answer",
    "show_sql",
    "show_source",
    "explain_last_answer",
    "general_answer",
    "diagnose",
    "teach_rule",
}
_ALLOWED_FOLLOW_UP_STRATEGIES = {
    "none",
    "refinement",
    "drill_down",
    "new_metric",
    "correction",
}
_ALLOWED_CRITIC_VERDICTS = {"approve", "clarify", "replan"}
_ALLOWED_SQL_CRITIC_VERDICTS = {"approve", "retry", "clarify"}


@dataclass(slots=True)
class RewriteResult:
    normalized_question: str = ""
    standalone_question: str = ""
    is_follow_up: bool = False
    follow_up_strategy: str = "none"
    missing_context: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionPlan:
    route: str = "database_query"
    standalone_question: str = ""
    user_goal: str = "list_rows"
    needs_clarification: bool = False
    clarifying_question: str = ""
    requires_schema: bool = True
    requires_business_mapping: bool = False
    follow_up_strategy: str = "none"
    confidence: float = 0.5
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, str] = field(default_factory=dict)
    date_range: str | None = None
    comparison_target: str | None = None


@dataclass(slots=True)
class CriticReview:
    verdict: str = "approve"
    reason: str = ""
    needs_clarification: bool = False
    clarifying_question: str = ""
    confidence: float = 0.8


@dataclass(slots=True)
class SqlCriticReview:
    verdict: str = "approve"
    reason: str = ""
    clarifying_question: str = ""


class PlannerService:
    """
    Multi-stage planner with strict JSON validation, repair, and critic review.

    Stages:
    1. normalize and rewrite the user message
    2. resolve conversational context
    3. choose a safe execution route
    """

    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._prompt_builder = prompt_builder

    def analyze(
        self,
        user_question: str,
        memory_context: str = "",
        dialogue_state: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        question = " ".join((user_question or "").split()).strip()
        if not question:
            return asdict(
                ExecutionPlan(
                    route="clarify",
                    standalone_question="",
                    needs_clarification=True,
                    clarifying_question="Please ask a question.",
                    requires_schema=False,
                    confidence=1.0,
                )
            )

        dialogue_state_text = self._render_dialogue_state(dialogue_state)

        rewrite = self._rewrite_question(
            question=question,
            memory_context=memory_context,
            dialogue_state=dialogue_state_text,
        )
        plan = self._build_execution_plan(
            question=question,
            rewrite=rewrite,
            memory_context=memory_context,
            dialogue_state=dialogue_state_text,
        )

        if self._should_review_plan(plan=plan, rewrite=rewrite):
            reviewed = self._review_plan(
                question=question,
                rewrite=rewrite,
                plan=plan,
                memory_context=memory_context,
                dialogue_state=dialogue_state_text,
            )
            if reviewed is not None:
                plan = reviewed

        if plan.route == "clarify" and not plan.clarifying_question:
            plan.needs_clarification = True
            plan.clarifying_question = self._fallback_clarification(question, rewrite)

        if plan.follow_up_strategy == "correction":
            metrics.increment_signal("user_correction_detected")

        log_event(
            "info",
            "planner_completed",
            route=plan.route,
            confidence=plan.confidence,
            follow_up_strategy=plan.follow_up_strategy,
            requires_business_mapping=plan.requires_business_mapping,
        )
        return asdict(plan)

    def answer_general_question(
        self,
        *,
        user_question: str,
        memory_context: str = "",
    ) -> str:
        prompt = self._prompt_builder.build_general_answer_prompt(
            user_question=user_question,
            recent_context=memory_context,
        )
        try:
            answer = call_llm(prompt=prompt, max_tokens=180, temperature=0.2).strip()
            return answer or "I can help with general questions and with your data queries."
        except Exception as exc:
            logger.debug("General answer generation failed: %s", exc)
            return "I can help with general questions and with your data queries."

    def review_sql_candidate(
        self,
        *,
        question: str,
        sql: str,
        schema,
        session_context: str = "",
        examples_context: str = "",
    ) -> dict[str, Any]:
        prompt = self._prompt_builder.build_sql_critic_prompt(
            question=question,
            sql=sql,
            schema=schema,
            session_context=session_context,
            examples_context=examples_context,
        )
        review_data = self._collect_structured_output(
            prompt=prompt,
            schema_name="sql_critic",
            schema=self._prompt_builder.SQL_CRITIC_SCHEMA,
            validator=self._validate_sql_critic_payload,
            max_tokens=180,
        )
        if review_data is not None:
            return review_data
        return asdict(self._heuristic_sql_review(question=question, sql=sql))

    def _rewrite_question(
        self,
        *,
        question: str,
        memory_context: str,
        dialogue_state: str,
    ) -> RewriteResult:
        prompt = self._prompt_builder.build_rewrite_prompt(
            user_question=question,
            recent_context=memory_context,
            dialogue_state=dialogue_state,
        )
        rewrite_data = self._collect_structured_output(
            prompt=prompt,
            schema_name="rewrite_result",
            schema=self._prompt_builder.REWRITE_SCHEMA,
            validator=self._validate_rewrite_payload,
            max_tokens=240,
        )
        if rewrite_data is None:
            return RewriteResult(
                normalized_question=question,
                standalone_question=question,
            )
        return RewriteResult(**rewrite_data)

    def _build_execution_plan(
        self,
        *,
        question: str,
        rewrite: RewriteResult,
        memory_context: str,
        dialogue_state: str,
    ) -> ExecutionPlan:
        prompt = self._prompt_builder.build_plan_prompt(
            user_question=question,
            rewritten_question=rewrite.standalone_question or question,
            recent_context=memory_context,
            dialogue_state=dialogue_state,
            rewrite_result=json.dumps(asdict(rewrite), ensure_ascii=True),
        )
        plan_data = self._collect_structured_output(
            prompt=prompt,
            schema_name="execution_plan",
            schema=self._prompt_builder.PLAN_SCHEMA,
            validator=lambda payload: self._validate_plan_payload(
                payload,
                fallback_question=rewrite.standalone_question or question,
                rewrite=rewrite,
            ),
            max_tokens=320,
        )
        if plan_data is None:
            metrics.increment_signal("planner_fallback")
            log_event("warning", "planner_fallback_used", question=question)
            return self._minimal_fallback(question, rewrite)
        return ExecutionPlan(**plan_data)

    def _review_plan(
        self,
        *,
        question: str,
        rewrite: RewriteResult,
        plan: ExecutionPlan,
        memory_context: str,
        dialogue_state: str,
    ) -> ExecutionPlan | None:
        prompt = self._prompt_builder.build_plan_critic_prompt(
            user_question=question,
            rewritten_question=rewrite.standalone_question or question,
            plan_json=json.dumps(asdict(plan), ensure_ascii=True),
            recent_context=memory_context,
            dialogue_state=dialogue_state,
        )
        review_data = self._collect_structured_output(
            prompt=prompt,
            schema_name="plan_critic",
            schema=self._prompt_builder.CRITIC_SCHEMA,
            validator=self._validate_critic_payload,
            max_tokens=180,
        )
        if review_data is None:
            return None

        review = CriticReview(**review_data)
        log_event(
            "info",
            "plan_critic_completed",
            verdict=review.verdict,
            confidence=review.confidence,
        )

        if review.verdict == "approve":
            return plan

        if review.verdict == "clarify":
            return ExecutionPlan(
                route="clarify",
                standalone_question=plan.standalone_question,
                user_goal=plan.user_goal,
                needs_clarification=True,
                clarifying_question=review.clarifying_question or self._fallback_clarification(question, rewrite),
                requires_schema=False,
                requires_business_mapping=plan.requires_business_mapping,
                follow_up_strategy=plan.follow_up_strategy,
                confidence=min(plan.confidence, review.confidence),
                dimensions=plan.dimensions,
                filters=plan.filters,
                date_range=plan.date_range,
                comparison_target=plan.comparison_target,
            )

        return self._build_execution_plan(
            question=question,
            rewrite=rewrite,
            memory_context=memory_context,
            dialogue_state=dialogue_state,
        )

    def _collect_structured_output(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        validator,
        max_tokens: int,
        max_attempts: int = 3,
    ) -> dict[str, Any] | None:
        effective_prompt = prompt
        previous_output = ""
        errors = ["Model did not return valid JSON."]

        for attempt in range(max_attempts):
            try:
                previous_output = call_llm(
                    prompt=effective_prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
            except Exception as exc:
                previous_output = ""
                errors = [str(exc)]

            parsed, parse_error = self._parse_json_object(previous_output)
            if parsed is None:
                metrics.increment_signal("planner_json_failure")
                errors = [parse_error or "Model response was not valid JSON."]
            else:
                normalized, validation_errors = validator(parsed)
                if normalized is not None:
                    return normalized
                metrics.increment_signal("planner_json_failure")
                errors = validation_errors or ["Structured output failed schema validation."]

            log_event(
                "warning",
                "structured_output_invalid",
                schema_name=schema_name,
                attempt=attempt + 1,
                errors=errors,
            )
            effective_prompt = self._prompt_builder.build_json_repair_prompt(
                schema_name=schema_name,
                schema=schema,
                previous_output=previous_output,
                validation_errors=errors,
            )

        return None

    def _validate_rewrite_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        errors: list[str] = []
        normalized_question = self._safe_str(payload.get("normalized_question"), "")
        standalone_question = self._safe_str(payload.get("standalone_question"), "")
        follow_up_strategy = self._safe_enum(
            payload.get("follow_up_strategy"),
            _ALLOWED_FOLLOW_UP_STRATEGIES,
            "none",
        )

        if not normalized_question:
            errors.append("normalized_question must be a non-empty string.")
        if not standalone_question:
            errors.append("standalone_question must be a non-empty string.")
        if errors:
            return None, errors

        return {
            "normalized_question": normalized_question,
            "standalone_question": standalone_question,
            "is_follow_up": self._safe_bool(payload.get("is_follow_up"), False),
            "follow_up_strategy": follow_up_strategy,
            "missing_context": self._safe_list_of_strings(payload.get("missing_context")),
        }, []

    def _validate_plan_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback_question: str,
        rewrite: RewriteResult,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        errors: list[str] = []
        route = self._safe_enum(payload.get("route"), _ALLOWED_ROUTES, "")
        standalone_question = self._safe_str(
            payload.get("standalone_question"),
            fallback_question,
        )
        confidence = self._safe_nullable_float(payload.get("confidence"), 0.0, 1.0)

        if not route:
            errors.append("route must be one of the allowed values.")
        if not standalone_question:
            errors.append("standalone_question must be a non-empty string.")
        if confidence is None:
            errors.append("confidence must be a number between 0 and 1.")
        if errors:
            return None, errors

        follow_up_strategy = self._safe_enum(
            payload.get("follow_up_strategy"),
            _ALLOWED_FOLLOW_UP_STRATEGIES,
            rewrite.follow_up_strategy,
        )
        needs_clarification = self._safe_bool(
            payload.get("needs_clarification"),
            False,
        )
        clarifying_question = self._safe_str(payload.get("clarifying_question"), "")

        if rewrite.missing_context and route == "database_query" and confidence < 0.65:
            route = "clarify"
            needs_clarification = True
            clarifying_question = clarifying_question or self._fallback_clarification(
                standalone_question,
                rewrite,
            )

        if needs_clarification and route != "clarify":
            route = "clarify"

        return {
            "route": route,
            "standalone_question": standalone_question,
            "user_goal": self._safe_str(payload.get("user_goal"), "query data"),
            "needs_clarification": needs_clarification,
            "clarifying_question": clarifying_question,
            "requires_schema": self._safe_bool(payload.get("requires_schema"), True),
            "requires_business_mapping": self._safe_bool(
                payload.get("requires_business_mapping"),
                False,
            ),
            "follow_up_strategy": follow_up_strategy,
            "confidence": confidence,
            "dimensions": self._safe_list_of_strings(payload.get("dimensions")),
            "filters": self._safe_dict_of_strings(payload.get("filters")),
            "date_range": self._safe_nullable_str(payload.get("date_range")),
            "comparison_target": self._safe_nullable_str(payload.get("comparison_target")),
        }, []

    def _validate_critic_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        verdict = self._safe_enum(payload.get("verdict"), _ALLOWED_CRITIC_VERDICTS, "")
        confidence = self._safe_nullable_float(payload.get("confidence"), 0.0, 1.0)
        errors: list[str] = []

        if not verdict:
            errors.append("verdict must be approve, clarify, or replan.")
        if confidence is None:
            errors.append("confidence must be a number between 0 and 1.")
        if errors:
            return None, errors

        return {
            "verdict": verdict,
            "reason": self._safe_str(payload.get("reason"), ""),
            "needs_clarification": self._safe_bool(
                payload.get("needs_clarification"),
                verdict == "clarify",
            ),
            "clarifying_question": self._safe_str(payload.get("clarifying_question"), ""),
            "confidence": confidence,
        }, []

    def _validate_sql_critic_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        verdict = self._safe_enum(
            payload.get("verdict"),
            _ALLOWED_SQL_CRITIC_VERDICTS,
            "",
        )
        if not verdict:
            return None, ["verdict must be approve, retry, or clarify."]

        return {
            "verdict": verdict,
            "reason": self._safe_str(payload.get("reason"), ""),
            "clarifying_question": self._safe_str(payload.get("clarifying_question"), ""),
        }, []

    def _should_review_plan(
        self,
        *,
        plan: ExecutionPlan,
        rewrite: RewriteResult,
    ) -> bool:
        return any(
            [
                plan.needs_clarification,
                plan.confidence < 0.7,
                rewrite.is_follow_up,
                bool(rewrite.missing_context),
                plan.requires_business_mapping,
            ]
        )

    @staticmethod
    def _minimal_fallback(
        question: str,
        rewrite: RewriteResult | None = None,
    ) -> ExecutionPlan:
        lowered = question.lower()
        standalone = rewrite.standalone_question if rewrite else question

        if any(token in lowered for token in ("show me the sql", "show sql", "what sql", "query used")):
            return ExecutionPlan(
                route="show_sql",
                standalone_question=standalone,
                user_goal="show previous sql",
                requires_schema=False,
                confidence=0.35,
            )

        if any(token in lowered for token in ("where did this come from", "what source", "which table", "what columns")):
            return ExecutionPlan(
                route="show_source",
                standalone_question=standalone,
                user_goal="show answer source",
                requires_schema=False,
                confidence=0.35,
            )

        if any(token in lowered for token in ("why did you say", "explain the last answer", "how was that calculated")):
            return ExecutionPlan(
                route="explain_last_answer",
                standalone_question=standalone,
                user_goal="explain previous answer",
                requires_schema=False,
                confidence=0.35,
            )

        if lowered in {"hi", "hello", "hey", "thanks", "thank you", "bye"}:
            return ExecutionPlan(
                route="general_answer",
                standalone_question=standalone,
                user_goal="general chat",
                requires_schema=False,
                confidence=0.4,
            )

        if len(standalone.split()) <= 3:
            return ExecutionPlan(
                route="clarify",
                standalone_question=standalone,
                user_goal="clarify request",
                needs_clarification=True,
                clarifying_question=PlannerService._fallback_clarification(question, rewrite),
                requires_schema=False,
                confidence=0.15,
            )

        return ExecutionPlan(
            route="database_query",
            standalone_question=standalone,
            user_goal="query data",
            follow_up_strategy=rewrite.follow_up_strategy if rewrite else "none",
            confidence=0.3,
        )

    @staticmethod
    def _heuristic_sql_review(
        *,
        question: str,
        sql: str,
    ) -> SqlCriticReview:
        lowered_question = question.lower()
        lowered_sql = sql.lower()

        if any(token in lowered_question for token in ("compare", "versus", "vs")) and "group by" not in lowered_sql:
            return SqlCriticReview(
                verdict="retry",
                reason="The SQL may be missing a grouping needed for comparison.",
            )

        if any(token in lowered_question for token in ("top", "highest", "lowest", "best", "worst")) and "order by" not in lowered_sql:
            return SqlCriticReview(
                verdict="retry",
                reason="The SQL may be missing ordering for a ranking request.",
            )

        if len(question.split()) <= 3:
            return SqlCriticReview(
                verdict="clarify",
                reason="The question is very short and may be ambiguous.",
                clarifying_question="Could you provide a little more detail about the data you want?",
            )

        return SqlCriticReview(verdict="approve", reason="No obvious SQL issues detected.")

    @staticmethod
    def _fallback_clarification(
        question: str,
        rewrite: RewriteResult | None = None,
    ) -> str:
        if rewrite and rewrite.missing_context:
            joined = ", ".join(rewrite.missing_context[:3])
            return f"I need a bit more detail to answer this safely. Could you clarify: {joined}?"

        if len(question.split()) <= 3:
            return "Could you provide a bit more detail about the data you want?"

        return "Could you clarify the exact metric, filter, or time range you want?"

    @staticmethod
    def _render_dialogue_state(dialogue_state: dict[str, Any] | str | None) -> str:
        if dialogue_state is None:
            return "{}"
        if isinstance(dialogue_state, str):
            return dialogue_state.strip() or "{}"
        try:
            return json.dumps(dialogue_state, ensure_ascii=True)
        except Exception:
            return "{}"

    @staticmethod
    def _parse_json_object(raw: str) -> tuple[dict[str, Any] | None, str | None]:
        text = (raw or "").strip()
        if not text:
            return None, "Response was empty."

        cleaned = text.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed, None
            return None, "Response JSON root must be an object."
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None, "Response did not contain a valid JSON object."

            candidate = cleaned[start : end + 1]
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed, None
                return None, "Extracted JSON root must be an object."
            except Exception as exc:
                return None, f"JSON parsing failed: {exc}"

    @staticmethod
    def _safe_str(value: Any, default: str) -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    @staticmethod
    def _safe_nullable_str(value: Any) -> str | None:
        if value in (None, "", "null"):
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _safe_enum(value: Any, allowed: set[str], default: str) -> str:
        text = str(value).strip().lower() if value is not None else ""
        return text if text in allowed else default

    @staticmethod
    def _safe_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1"}:
                return True
            if lowered in {"false", "no", "0"}:
                return False
        return default

    @staticmethod
    def _safe_nullable_float(value: Any, min_value: float, max_value: float) -> float | None:
        if value in (None, "", "null"):
            return None
        try:
            number = float(value)
        except Exception:
            return None
        if number < min_value:
            return min_value
        if number > max_value:
            return max_value
        return round(number, 4)

    @staticmethod
    def _safe_list_of_strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    @staticmethod
    def _safe_dict_of_strings(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, str] = {}
        for key, item in value.items():
            clean_key = str(key).strip()
            clean_value = str(item).strip() if item is not None else ""
            if clean_key and clean_value:
                result[clean_key] = clean_value
        return result


class IntentService(PlannerService):
    """Backward-compatible alias for older imports."""
    pass