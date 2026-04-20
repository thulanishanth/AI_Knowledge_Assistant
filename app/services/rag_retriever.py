# app/services/rag_retriever.py
"""Continuous Sync Engine: Database Rules to Vector Memory."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4
from typing import Any

import sqlalchemy as sa

from app.core.logging import get_logger
from app.core.settings import settings
from app.infrastructure.database import db_manager
from app.memory.vector_memory import VectorMemory
from app.vector_store.vector_store_interface import VectorRecord

logger = get_logger(__name__)


async def sync_database_rules_to_vector_store(vector_memory: VectorMemory, tenant_id: str = "default") -> int:
    """
    Pulls business rules, semantics, and constraints from the live database
    and syncs them into the ChromaDB knowledge collection.
    """
    logger.info(f"Starting database-to-vector knowledge sync for tenant: {tenant_id}")
    
    # 1. Fetch live rules directly from MySQL
    rules = []
    try:
        with db_manager.engine.connect() as conn:
            query = sa.text(f"SELECT rule_id, rule_definition, is_critical FROM {settings.meta_table_rules}")
            result = conn.execute(query)
            rules = [dict(row._mapping) for row in result]
    except Exception as e:
        logger.error(f"Failed to fetch rules from {settings.meta_table_rules}: {e}")
        return 0

    if not rules:
        logger.info("No business rules found in the database to sync.")
        return 0

    # 2. Parse, Categorize, and Embed
    records_to_upsert = []
    for rule in rules:
        rule_id = str(rule.get("rule_id", uuid4().hex))
        definition = str(rule.get("rule_definition", "")).strip()
        is_critical = bool(rule.get("is_critical", False))
        
        if not definition:
            continue

        # Smart Categorization for Vector Metadata
        rule_type = "business_rule"
        lower_def = definition.lower()
        if "semantic" in lower_def or "means" in lower_def:
            rule_type = "semantic"
        elif "ontology" in lower_def or "synonym" in lower_def:
            rule_type = "ontology"
        elif "constraint" in lower_def or "must" in lower_def or "never" in lower_def:
            rule_type = "constraint"

        text_payload = definition
        if is_critical and not text_payload.startswith("CRITICAL:"):
            text_payload = f"CRITICAL: {text_payload}"

        try:
            embedding = await vector_memory.generate_embedding(text_payload)
        except Exception as e:
            logger.warning(f"Failed to generate embedding for rule {rule_id}: {e}")
            continue

        records_to_upsert.append(
            VectorRecord(
                id=f"rule_{tenant_id}_{rule_id}",
                text=text_payload,
                embedding=embedding,
                metadata={
                    "source": "live_db_sync",
                    "memory_type": "knowledge",
                    "tenant_id": tenant_id,
                    "rule_type": rule_type,
                    "is_critical": is_critical,
                    "timestamp_epoch": time.time()
                }
            )
        )

    # 3. Upsert into ChromaDB Knowledge Collection
    if records_to_upsert:
        try:
            await vector_memory._vector_store.upsert_records(
                collection_name=settings.vector_collection_knowledge,
                records=records_to_upsert
            )
            logger.info(f"Successfully synced {len(records_to_upsert)} rules into vector memory.")
        except Exception as e:
            logger.error(f"Failed to upsert rules into ChromaDB: {e}")
            return 0
            
    return len(records_to_upsert)

async def retrieve_dynamic_rag_context(tenant_id: str = "default") -> str:
    """
    Legacy stub. In the advanced architecture, schema is handled by SchemaService 
    and RAG rules are dynamically injected by VectorMemory via the sync engine. 
    Returns an empty string to gracefully sever the legacy file-based pipeline.
    """
    return ""