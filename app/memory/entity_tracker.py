# app/memory/entity_tracker.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class TrackedEntity:
    """An entity that has been mentioned and resolved in the conversation."""
    name: str              # e.g., "2018", "Offline"
    entity_type: str       # e.g., "arrival_year", "market_segment_type"
    sql_filter: str        # e.g., "arrival_year = '2018'"

class EntityTracker:
    """
    Tracks entities mentioned in the conversation to resolve coreferences.
    Prevents the AI from forgetting active filters during follow-up questions.
    """
    def __init__(self) -> None:
        self.entities: list[TrackedEntity] = []

    def load_from_state(self, filters_dict: dict[str, str] | None) -> None:
        """Loads entities from the previous query's extracted SQL filters."""
        if not filters_dict:
            return

        for col, val in filters_dict.items():
            # Ignore structural/internal filters that aren't user-driven
            if col.lower() in ["tenant_id", "table_schema", "table_name"]:
                continue

            self.entities.append(TrackedEntity(
                name=str(val),
                entity_type=col,
                sql_filter=f"{col} = '{val}'"
            ))

    def to_context_block(self) -> str:
        """Render active entities as a context block for the LLM Planner/Rewriter."""
        if not self.entities:
            return ""

        lines = [
            "ACTIVE CONVERSATION ENTITIES (Crucial for follow-up questions).",
            "Use these to resolve pronouns like 'it', 'that year', 'that segment':"
        ]

        for e in self.entities:
            clean_type = e.entity_type.replace('_', ' ').title()
            lines.append(f"  - {clean_type}: '{e.name}' (Context Filter: {e.sql_filter})")

        return "\n".join(lines)