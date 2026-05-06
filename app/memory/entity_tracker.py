# app/memory/entity_tracker.py
from __future__ import annotations
from typing import Any

class EntityTracker:
    """
    Tracks entities mentioned in the conversation to resolve coreferences.
    Prevents the AI from forgetting active filters during follow-up questions.
    """
    def __init__(self) -> None:
        # Using a dictionary directly allows us to seamlessly store lists (IN clauses), 
        # strings (EQ), and operator strings (>, <, BETWEEN).
        self.entities: dict[str, Any] = {}

    def load_from_state(self, filters_dict: dict[str, Any] | None) -> None:
        """Loads entities from the previous query's extracted SQL filters."""
        if not filters_dict:
            return

        for col, val in filters_dict.items():
            # Ignore structural/internal filters that aren't user-driven
            if col.lower() in ["tenant_id", "table_schema", "table_name"]:
                continue

            # Directly map the column to its value payload (string, list, or operator string)
            self.entities[col] = val
            
    def to_context_block(self) -> str:
        """Converts active entities into a deterministic string for the LLM prompt."""
        if not hasattr(self, "entities") or not self.entities:
            return ""
            
        lines = []
        for col, val in self.entities.items():
            
            # 1. Special handling for the date block
            if col == "date_period":
                lines.append(f"- DATE RANGE: {val}")
                continue
                
            # 2. Handle IN clauses safely (Python lists -> SQL IN syntax)
            if isinstance(val, list):
                formatted_vals = ", ".join(f"'{v}'" for v in val)
                lines.append(f"- {col} IN ({formatted_vals})")
                
            # 3. Handle Inequalities and BETWEEN seamlessly
            elif isinstance(val, str) and val.startswith((">", "<", ">=", "<=", "BETWEEN")):
                lines.append(f"- {col} {val}")
                
            # 4. Standard equality fallback
            else:
                lines.append(f"- {col} = '{val}'")
                
        return "CURRENT ACTIVE FILTERS:\n" + "\n".join(lines)