from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.core.dependency_injection import container
from app.core.logging import get_logger

logger = get_logger(__name__)


def _resolve_context_file() -> Path:
    data_dir = Path(__file__).resolve().parent.parent / "app" / "data"

    candidates = [
        data_dir / "business_context.active.json",
        data_dir / "business_context.json",
        data_dir / "business_context.compiled.json",
        data_dir / "business_context1.json",
    ]

    for path in candidates:
        if path.exists():
            return path

    raise FileNotFoundError(
        "No active business context file found in app/data. "
        "Expected one of: business_context.active.json, business_context.json, "
        "business_context.compiled.json, business_context1.json"
    )


def _normalize_term_mapping(item) -> dict | None:
    if isinstance(item, dict):
        term = str(item.get("term", "")).strip()
        sql_condition = str(item.get("sql_condition", "")).strip()
        aliases = item.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.split("|") if part.strip()]
        elif not isinstance(aliases, list):
            aliases = []

        if not term or not sql_condition:
            return None

        return {
            "canonical_term": term,
            "aliases": aliases,
            "sql_condition": sql_condition,
            "description": str(item.get("description", "")).strip(),
            "rule_type": str(item.get("type", "term_mapping")).strip(),
        }

    if isinstance(item, str):
        # Example: "Term Mapping 'gds': market_segment_type IN ('Corporate', 'Aviation')"
        text = item.strip()
        if not text:
            return None

        term = ""
        sql_condition = ""

        if "Term Mapping" in text and ":" in text:
            left, right = text.split(":", 1)
            sql_condition = right.strip()
            if "'" in left:
                parts = left.split("'")
                if len(parts) >= 2:
                    term = parts[1].strip()

        if not term or not sql_condition:
            return None

        return {
            "canonical_term": term,
            "aliases": [],
            "sql_condition": sql_condition,
            "description": text,
            "rule_type": "term_mapping",
        }

    return None


async def ingest_business_logic() -> None:
    await container.initialize()
    context_file = _resolve_context_file()
    logger.info("Loading business logic from %s", context_file)

    with open(context_file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    domain = str(payload.get("dataset_domain", "")).strip().lower()
    semantic_layer = payload.get("semantic_layer", []) or []
    term_mappings = payload.get("term_mappings", []) or []
    status_mappings = payload.get("status_mappings", []) or []
    metric_definitions = payload.get("metric_definitions", []) or []

    records_to_store: list[tuple[str, dict[str, str]]] = []

    for item in term_mappings:
        normalized = _normalize_term_mapping(item)
        if not normalized:
            continue

        canonical_term = normalized["canonical_term"]
        aliases = normalized["aliases"]
        sql_condition = normalized["sql_condition"]
        description = normalized["description"] or f"Approved mapping for term {canonical_term}"

        content = (
            f"TERM: {canonical_term}\n"
            f"DOMAIN: {domain or 'generic'}\n"
            f"ALIASES: {', '.join(aliases) if aliases else canonical_term}\n"
            f"TYPE: {normalized['rule_type']}\n"
            f"SQL_CONDITION: {sql_condition}\n"
            f"DESCRIPTION: {description}\n"
            f"SOURCE: approved_business_context"
        )
        metadata = {
            "domain": domain or "generic",
            "canonical_term": canonical_term,
            "aliases": "|".join(aliases) if aliases else canonical_term,
            "sql_condition": sql_condition,
            "description": description,
            "rule_type": normalized["rule_type"],
            "source": "approved_business_context",
        }
        records_to_store.append((content, metadata))

    # Also index semantic business meaning rules if they look explicit
    for item in semantic_layer:
        text = str(item).strip()
        if not text or "Business Meaning" not in text:
            continue

        lowered = text.lower()
        if "interpreted as" not in lowered and "means" not in lowered and "use " not in lowered:
            continue

        content = (
            f"TERM: semantic_rule\n"
            f"DOMAIN: {domain or 'generic'}\n"
            f"ALIASES: semantic_rule\n"
            f"TYPE: semantic_definition\n"
            f"SQL_CONDITION: \n"
            f"DESCRIPTION: {text}\n"
            f"SOURCE: approved_business_context"
        )
        metadata = {
            "domain": domain or "generic",
            "canonical_term": "semantic_rule",
            "aliases": "semantic_rule",
            "sql_condition": "",
            "description": text,
            "rule_type": "semantic_definition",
            "source": "approved_business_context",
        }
        records_to_store.append((content, metadata))

    # Status mappings
    for item in status_mappings:
        text = str(item).strip()
        if not text:
            continue

        content = (
            f"TERM: status_mapping\n"
            f"DOMAIN: {domain or 'generic'}\n"
            f"ALIASES: status_mapping\n"
            f"TYPE: status_mapping\n"
            f"SQL_CONDITION: \n"
            f"DESCRIPTION: {text}\n"
            f"SOURCE: approved_business_context"
        )
        metadata = {
            "domain": domain or "generic",
            "canonical_term": "status_mapping",
            "aliases": "status_mapping",
            "sql_condition": "",
            "description": text,
            "rule_type": "status_mapping",
            "source": "approved_business_context",
        }
        records_to_store.append((content, metadata))

    # Metric definitions
    for item in metric_definitions:
        text = str(item).strip()
        if not text:
            continue

        content = (
            f"TERM: metric_definition\n"
            f"DOMAIN: {domain or 'generic'}\n"
            f"ALIASES: metric_definition\n"
            f"TYPE: metric_definition\n"
            f"SQL_CONDITION: \n"
            f"DESCRIPTION: {text}\n"
            f"SOURCE: approved_business_context"
        )
        metadata = {
            "domain": domain or "generic",
            "canonical_term": "metric_definition",
            "aliases": "metric_definition",
            "sql_condition": "",
            "description": text,
            "rule_type": "metric_definition",
            "source": "approved_business_context",
        }
        records_to_store.append((content, metadata))

    if not records_to_store:
        raise RuntimeError(
            "No business-logic records were prepared. "
            "Make sure your active context file contains term_mappings or explicit business meanings."
        )

    stored = 0
    for content, metadata in records_to_store:
        await container.vector_memory.store_business_logic(content=content, metadata=metadata)
        stored += 1

    logger.info("Successfully ingested %s business-logic records into Chroma.", stored)


if __name__ == "__main__":
    asyncio.run(ingest_business_logic())