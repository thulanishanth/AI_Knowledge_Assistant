# app/services/rag_retriever.py
"""Local RAG context retrieval from project schema and semantic assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_BUSINESS_CONTEXT_PATH = _DATA_DIR / "business_context.json"


def _load_business_context() -> dict[str, Any]:
    if not _BUSINESS_CONTEXT_PATH.exists():
        logger.warning(f"Context file not found at {_BUSINESS_CONTEXT_PATH}")
        return {}

    try:
        return json.loads(_BUSINESS_CONTEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load local business context: %s", exc)
        return {}


def retrieve_dynamic_rag_context() -> str:
    """Build schema-aware context from local schema and rich semantic JSON assets."""
    business_context = _load_business_context()
    
    if not business_context:
        return "Warning: No business context found. Operating with default assumptions."

    # 1. Core Metadata
    dataset = str(business_context.get("dataset", settings.db_table)).strip()
    source_type = str(business_context.get("source_type", "relational_db")).strip()
    dialect = str(business_context.get("target_dialect", "unknown")).strip()

    sections = [
        "=============================\n"
        "DATASET CONTEXT\n"
        "=============================\n"
        f"- Target Dataset: {dataset}\n"
        f"- Source Type: {source_type}\n"
        f"- Required SQL Dialect: {dialect.upper()}"
    ]

    # 2. Dynamic Schema
    schema_dict = business_context.get("schema", {})
    if schema_dict:
        schema_lines = []
        for table, cols in schema_dict.items():
            schema_lines.append(f"Table: {table}")
            schema_lines.extend(f"  {col}" for col in cols)
        sections.append("DATABASE SCHEMA:\n" + "\n".join(schema_lines))

    # 3. Semantic Layer
    semantic_layer = [
        str(item).strip() for item in business_context.get("semantic_layer", []) if str(item).strip()
    ]
    if semantic_layer:
        sections.append("SEMANTIC LAYER (Data Meanings):\n" + "\n".join(f"- {item}" for item in semantic_layer))

    # 4. Ontology (Synonyms mapping)
    ontology = business_context.get("ontology", {})
    if ontology:
        ont_lines = [
            f"- '{col}' is also referred to as: {', '.join(synonyms)}" 
            for col, synonyms in ontology.items() if synonyms
        ]
        if ont_lines:
            sections.append("ONTOLOGY & SYNONYMS:\n" + "\n".join(ont_lines))

    # 5. Business Rules
    business_rules = [
        str(item).strip() for item in business_context.get("business_rules", []) if str(item).strip()
    ]
    if business_rules:
        sections.append("BUSINESS RULES:\n" + "\n".join(f"- {item}" for item in business_rules))

    # 6. Hard Constraints
    constraints = [
        str(item).strip() for item in business_context.get("constraints", []) if str(item).strip()
    ]
    if constraints:
        sections.append("SYSTEM CONSTRAINTS:\n" + "\n".join(f"- {item}" for item in constraints))

    return "\n\n".join(section for section in sections if section.strip())