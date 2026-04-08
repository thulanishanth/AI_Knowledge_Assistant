from __future__ import annotations

from app.services.business_logic_resolver import BusinessResolutionResult


class BusinessLogicInjector:
    """
    Build compact prompt-ready business logic context from resolved business terms.
    """

    def build_injection_block(self, resolution: BusinessResolutionResult) -> str:
        if not resolution.resolved_terms:
            return ""

        lines = ["Resolved Business Logic:"]
        for item in resolution.resolved_terms:
            lines.append(
                f"- {item.user_term} => {item.sql_condition} "
                f"(matched approved term: {item.canonical_term}, score={item.score})"
            )

        return "\n".join(lines)

    def build_insufficient_context_message(
        self,
        unresolved_terms: list[str],
    ) -> str:
        term_text = ", ".join(unresolved_terms)
        return (
            "Sorry, there is insufficient context in the approved business knowledge base "
            f"for these business terms: {term_text}. Please try again later after the business logic is added."
        )